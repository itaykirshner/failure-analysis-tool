"""FastAPI backend for web UI."""
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.k8s.client import check_kubernetes_connection, get_namespace

from src.analysis import (
    analyze_logs,
    assess_pod_health,
    collapse_repeated_errors,
    deduplicate_logs,
    detect_connectivity_issues,
    filter_irrelevant_warnings,
    identify_root_causes,
)
from src.api.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    ClusterGraphResponse,
    PodStatusResponse,
    QueryRequest,
    QueryResponse,
    TimelineResponse,
)
from src.bundle import extract_bundle, parse_configmaps, parse_pod_logs, parse_pod_statuses
from src.cli.main import _create_issues_from_health
from src.llm import answer_query, load_gguf_model
from src.llm.llm_service_client import LLMServiceClient
from src.llm.model_loader import LLMModel
from src.models.analysis import AnalysisResult, LogEntry

app = FastAPI(title="Kubernetes Log Analysis AIOps")

# In-memory storage for analysis results
# In production, use a database or cache
analysis_store: dict[str, AnalysisResult] = {}
model_store: Optional[LLMModel] = None
llm_service: Optional[LLMServiceClient] = None


@app.on_event("startup")
async def startup_event():
    """Initialize on startup."""
    # Check Kubernetes connection
    namespace = get_namespace()
    if namespace:
        print(f"Running in Kubernetes namespace: {namespace}")
        if check_kubernetes_connection():
            print("Kubernetes API connection: OK")
        else:
            print("Kubernetes API connection: Failed (continuing anyway)")
    
    # In Kubernetes, LLM is a separate service
    # Don't load model locally - use LLM service URL from env
    llm_service_url = os.getenv("LLM_SERVICE_URL", "http://llm-service:8000")
    print(f"LLM service URL: {llm_service_url}")
    
    # Initialize LLM service client if in Kubernetes
    if namespace:
        global llm_service
        llm_service = LLMServiceClient(llm_service_url)
        print("Initialized LLM service client")
    
    # For local development, try to load model directly
    if not namespace:
        model_path = Path.cwd() / "models" / "Nemotron-Mini-4B-Instruct-Q4_K_M.gguf"
        if model_path.exists():
            try:
                global model_store
                model_store = load_gguf_model(model_path)
                print("Loaded local LLM model")
            except Exception:
                pass  # Model loading is optional


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze_bundle(
    file: UploadFile = File(...),
    min_warning_frequency: int = 3,
) -> AnalyzeResponse:
    """
    Upload and analyze a log bundle.
    
    Args:
        file: The .tgz bundle file
        min_warning_frequency: Minimum frequency for warnings
        
    Returns:
        Analysis response with analysis_id
    """
    if not file.filename.endswith((".tgz", ".tar.gz")):
        raise HTTPException(status_code=400, detail="File must be a .tgz bundle")
    
    # Save uploaded file temporarily
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tgz") as tmp_file:
        tmp_path = Path(tmp_file.name)
        content = await file.read()
        tmp_path.write_bytes(content)
    
    try:
        # Extract bundle
        extract_dir = Path(tempfile.mkdtemp())
        bundle = extract_bundle(tmp_path, extract_dir)
        
        # Parse bundle
        pod_logs = parse_pod_logs(bundle.extract_path)
        pod_statuses = parse_pod_statuses(bundle.extract_path)
        configmaps = parse_configmaps(bundle.extract_path)
        
        # Analyze logs
        all_log_entries: list[LogEntry] = []
        for pod_log in pod_logs:
            from src.analysis.log_analyzer import _parse_log_file
            entries = _parse_log_file(pod_log)
            all_log_entries.extend(entries)
            pod_log.log_entries = entries
        
        # Apply noise reduction
        deduplicated = deduplicate_logs(all_log_entries)
        filtered = filter_irrelevant_warnings(deduplicated, min_warning_frequency)
        collapsed = collapse_repeated_errors(filtered)
        
        # Assess pod health
        pod_health_list = []
        pod_status_map = {
            f"{ps.namespace}/{ps.pod_name}": ps for ps in pod_statuses
        }
        
        logs_by_pod: dict[str, list[LogEntry]] = {}
        for entry in collapsed:
            key = f"{entry.namespace}/{entry.pod_name}"
            logs_by_pod.setdefault(key, []).append(entry)
        
        for key, pod_status in pod_status_map.items():
            pod_logs_list = logs_by_pod.get(key, [])
            health = assess_pod_health(pod_status, pod_logs_list)
            pod_health_list.append(health)
        
        # Detect connectivity issues
        connectivity_issues = detect_connectivity_issues(collapsed, pod_health_list)
        
        # Create issues
        issues = _create_issues_from_health(pod_health_list, connectivity_issues, collapsed)
        
        # Root cause analysis
        root_causes = identify_root_causes(issues, pod_health_list, connectivity_issues)
        
        # Create analysis result
        summary = {
            "total_pods": len(pod_health_list),
            "healthy_pods": sum(1 for ph in pod_health_list if ph.is_healthy),
            "unhealthy_pods": sum(1 for ph in pod_health_list if not ph.is_healthy),
            "total_issues": len(issues),
            "connectivity_issues": len(connectivity_issues),
            "root_causes": len(root_causes),
        }
        
        analysis_result = AnalysisResult(
            bundle_path=tmp_path,
            pod_health=pod_health_list,
            issues=issues,
            connectivity_issues=connectivity_issues,
            root_causes=root_causes,
            summary=summary,
        )
        
        # Generate analysis ID
        analysis_id = hashlib.md5(str(tmp_path).encode()).hexdigest()[:16]
        analysis_store[analysis_id] = analysis_result
        
        return AnalyzeResponse(
            analysis_id=analysis_id,
            status="completed",
            summary=summary,
            message="Analysis completed successfully",
        )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")
    
    finally:
        # Cleanup
        if tmp_path.exists():
            tmp_path.unlink()


@app.post("/api/query", response_model=QueryResponse)
async def query_analysis(request: QueryRequest) -> QueryResponse:
    """
    Query analysis results using natural language.
    
    Args:
        request: Query request with query string and optional analysis_id
        
    Returns:
        Query response with answer
    """
    if not model_store and not llm_service:
        raise HTTPException(
            status_code=503,
            detail="LLM model or service not available. Please ensure LLM service is running.",
        )
    
    # Get analysis (use latest if no ID provided)
    if request.analysis_id:
        analysis = analysis_store.get(request.analysis_id)
        if not analysis:
            raise HTTPException(status_code=404, detail="Analysis not found")
    else:
        if not analysis_store:
            raise HTTPException(status_code=404, detail="No analysis available")
        analysis = list(analysis_store.values())[-1]  # Use latest
    
    try:
        # answer_query is sync but uses async llm_service, so we need to handle it
        if llm_service:
            # Build prompt using query_interface helpers
            from src.llm.query_interface import (
                _retrieve_relevant_logs,
                _retrieve_relevant_issues,
                _retrieve_relevant_root_causes,
            )
            
            relevant_logs = _retrieve_relevant_logs(request.query, analysis, 50)
            relevant_issues = _retrieve_relevant_issues(request.query, analysis)
            relevant_root_causes = _retrieve_relevant_root_causes(request.query, analysis)
            
            prompt = _build_query_prompt(
                request.query,
                analysis,
                relevant_logs,
                relevant_issues,
                relevant_root_causes,
            )
            
            # Use async version for service
            answer = await llm_service.generate(
                prompt=prompt,
                max_tokens=512,
                temperature=0.5,
                stop=["<|end|>", "\n\n"],
            )
        else:
            answer = answer_query(request.query, analysis, model_store)
        return QueryResponse(answer=answer.strip(), query=request.query)
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")


@app.get("/api/cluster/graph", response_model=ClusterGraphResponse)
async def get_cluster_graph(analysis_id: Optional[str] = None) -> ClusterGraphResponse:
    """Get cluster dependency graph as Mermaid diagram."""
    # Get analysis
    if analysis_id:
        analysis = analysis_store.get(analysis_id)
        if not analysis:
            raise HTTPException(status_code=404, detail="Analysis not found")
    else:
        if not analysis_store:
            raise HTTPException(status_code=404, detail="No analysis available")
        analysis = list(analysis_store.values())[-1]
    
    # Generate Mermaid diagram
    mermaid_diagram = _generate_mermaid_diagram(analysis)
    
    # Extract nodes and edges
    nodes = []
    edges = []
    
    for ph in analysis.pod_health:
        nodes.append({
            "id": ph.pod_name,
            "label": ph.pod_name,
            "namespace": ph.namespace,
            "state": ph.state.value,
            "healthy": ph.is_healthy,
        })
    
    for conn_issue in analysis.connectivity_issues:
        edges.append({
            "from": conn_issue.source_pod,
            "to": conn_issue.target_service,
            "type": conn_issue.error_type,
            "label": conn_issue.error_type,
        })
    
    return ClusterGraphResponse(
        mermaid_diagram=mermaid_diagram,
        nodes=nodes,
        edges=edges,
    )


@app.get("/api/timeline", response_model=TimelineResponse)
async def get_timeline(analysis_id: Optional[str] = None) -> TimelineResponse:
    """Get error timeline data."""
    # Get analysis
    if analysis_id:
        analysis = analysis_store.get(analysis_id)
        if not analysis:
            raise HTTPException(status_code=404, detail="Analysis not found")
    else:
        if not analysis_store:
            raise HTTPException(status_code=404, detail="No analysis available")
        analysis = list(analysis_store.values())[-1]
    
    # Build timeline
    timeline = []
    error_counts: dict[str, int] = {}
    
    for issue in analysis.issues:
        timeline.append({
            "timestamp": issue.first_seen.isoformat(),
            "type": issue.issue_type,
            "severity": issue.severity,
            "pod": issue.pod_name,
            "description": issue.description,
        })
        
        error_counts[issue.issue_type] = error_counts.get(issue.issue_type, 0) + 1
    
    timeline.sort(key=lambda x: x["timestamp"])
    
    return TimelineResponse(timeline=timeline, error_counts=error_counts)


@app.get("/api/pods/status", response_model=PodStatusResponse)
async def get_pod_status(analysis_id: Optional[str] = None) -> PodStatusResponse:
    """Get pod health statuses."""
    # Get analysis
    if analysis_id:
        analysis = analysis_store.get(analysis_id)
        if not analysis:
            raise HTTPException(status_code=404, detail="Analysis not found")
    else:
        if not analysis_store:
            raise HTTPException(status_code=404, detail="No analysis available")
        analysis = list(analysis_store.values())[-1]
    
    pods = []
    for ph in analysis.pod_health:
        pods.append({
            "name": ph.pod_name,
            "namespace": ph.namespace,
            "state": ph.state.value,
            "healthy": ph.is_healthy,
            "error_count": ph.error_count,
            "warning_count": ph.warning_count,
            "restart_count": ph.restart_count,
            "crash_reason": ph.crash_reason,
        })
    
    summary = analysis.summary
    
    return PodStatusResponse(pods=pods, summary=summary)


def _generate_mermaid_diagram(analysis: AnalysisResult) -> str:
    """Generate Mermaid diagram from analysis."""
    diagram = "graph TD\n"
    
    # Add nodes with styling
    for ph in analysis.pod_health:
        node_id = ph.pod_name.replace("-", "_").replace(".", "_")
        color = "green" if ph.is_healthy else "red" if ph.state.value == "crashed" else "yellow"
        diagram += f'    {node_id}["{ph.pod_name}"]\n'
        diagram += f'    {node_id}:::pod_{color}\n'
    
    # Add edges from connectivity issues
    for conn_issue in analysis.connectivity_issues:
        from_id = conn_issue.source_pod.replace("-", "_").replace(".", "_")
        to_id = conn_issue.target_service.replace("-", "_").replace(".", "_")
        diagram += f'    {from_id} -->|{conn_issue.error_type}| {to_id}\n'
    
    # Add styling
    diagram += "    classDef pod_green fill:#90EE90,stroke:#333,stroke-width:2px\n"
    diagram += "    classDef pod_red fill:#FF6B6B,stroke:#333,stroke-width:2px\n"
    diagram += "    classDef pod_yellow fill:#FFD93D,stroke:#333,stroke-width:2px\n"
    
    return diagram


# Serve static files
frontend_path = Path(__file__).parent.parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=frontend_path / "static"), name="static")


@app.get("/health")
async def health_check():
    """Health check endpoint for Kubernetes probes."""
    return {
        "status": "healthy",
        "namespace": get_namespace() or "not-in-cluster",
        "kubernetes_connected": check_kubernetes_connection(),
    }


@app.get("/", response_class=HTMLResponse)
async def serve_frontend() -> str:
    """Serve the frontend HTML."""
    frontend_file = frontend_path / "index.html"
    if frontend_file.exists():
        return frontend_file.read_text()
    return "<html><body><h1>Frontend not found</h1></body></html>"

