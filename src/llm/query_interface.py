"""Query interface for natural language queries about analysis results."""
import os
from typing import Optional

from src.llm.llm_service_client import LLMServiceClient
from src.llm.model_loader import LLMModel
from src.models.analysis import AnalysisResult, LogEntry


def answer_query(
    query: str,
    analysis: AnalysisResult,
    model: Optional[LLMModel] = None,
    max_context_logs: int = 50,
    llm_service: Optional[LLMServiceClient] = None,
) -> str:
    """
    Answer a natural language query about the analysis results.
    
    Uses RAG-style retrieval to find relevant context, then generates
    an answer using the LLM.
    
    Args:
        query: Natural language query
        analysis: AnalysisResult to query against
        model: Loaded LLMModel
        max_context_logs: Maximum logs to include in context
        
    Returns:
        Answer string
    """
    # Retrieve relevant context
    relevant_logs = _retrieve_relevant_logs(query, analysis, max_context_logs)
    relevant_issues = _retrieve_relevant_issues(query, analysis)
    relevant_root_causes = _retrieve_relevant_root_causes(query, analysis)
    
    # Build prompt
    prompt = _build_query_prompt(
        query,
        analysis,
        relevant_logs,
        relevant_issues,
        relevant_root_causes,
    )
    
    # Generate answer - use service if available, otherwise local model
    # Note: This function is synchronous, but llm_service.generate is async
    # The caller (FastAPI) should handle async properly
    if llm_service:
        # For async context, this should be awaited by the caller
        # For sync context, we'll need to handle it differently
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        answer = loop.run_until_complete(
            llm_service.generate(
                prompt=prompt,
                max_tokens=512,
                temperature=0.5,
                stop=["<|end|>", "\n\n"],
            )
        )
    elif model:
        answer = model.generate(
            prompt=prompt,
            max_tokens=512,
            temperature=0.5,
            stop=["<|end|>", "\n\n"],
        )
    else:
        raise RuntimeError("No LLM model or service available")
    
    return answer.strip()


def _retrieve_relevant_logs(
    query: str,
    analysis: AnalysisResult,
    max_logs: int,
) -> list[LogEntry]:
    """Retrieve logs relevant to the query."""
    query_lower = query.lower()
    
    # Collect all logs from issues
    all_logs: list[LogEntry] = []
    for issue in analysis.issues:
        all_logs.extend(issue.related_logs)
    
    # Simple keyword matching for now
    # In a production system, you'd use embeddings/semantic search
    relevant = []
    
    for log in all_logs:
        message_lower = log.message.lower()
        pod_lower = log.pod_name.lower()
        
        # Check if query keywords match
        query_words = set(query_lower.split())
        message_words = set(message_lower.split())
        pod_words = set(pod_lower.split())
        
        if query_words.intersection(message_words) or query_words.intersection(pod_words):
            relevant.append(log)
    
    # Prioritize errors
    errors = [log for log in relevant if log.level.value in ["ERROR", "FATAL"]]
    others = [log for log in relevant if log.level.value not in ["ERROR", "FATAL"]]
    
    return (errors + others)[:max_logs]


def _retrieve_relevant_issues(query: str, analysis: AnalysisResult) -> list:
    """Retrieve issues relevant to the query."""
    query_lower = query.lower()
    relevant = []
    
    for issue in analysis.issues:
        if (
            query_lower in issue.description.lower()
            or query_lower in issue.pod_name.lower()
            or any(word in issue.description.lower() for word in query_lower.split())
        ):
            relevant.append(issue)
    
    return relevant[:10]  # Limit to 10


def _retrieve_relevant_root_causes(query: str, analysis: AnalysisResult) -> list:
    """Retrieve root causes relevant to the query."""
    query_lower = query.lower()
    relevant = []
    
    for rc in analysis.root_causes:
        if (
            query_lower in rc.description.lower()
            or any(pod in query_lower for pod in rc.affected_pods)
        ):
            relevant.append(rc)
    
    return relevant[:5]  # Limit to 5


def _build_query_prompt(
    query: str,
    analysis: AnalysisResult,
    relevant_logs: list[LogEntry],
    relevant_issues: list,
    relevant_root_causes: list,
) -> str:
    """Build a prompt for answering the query."""
    prompt = f"""<extra_id_0>System
You are an expert Kubernetes log analyst. Answer the user's question based on the analysis results provided.

<extra_id_1>User
{query}

Context from analysis:
- Total pods: {analysis.summary.get('total_pods', 0)}
- Unhealthy pods: {analysis.summary.get('unhealthy_pods', 0)}
- Total issues: {analysis.summary.get('total_issues', 0)}
- Root causes: {analysis.summary.get('root_causes', 0)}

Relevant Root Causes:
"""
    
    for rc in relevant_root_causes:
        prompt += f"- {rc.description}\n"
        prompt += f"  Affected pods: {', '.join(rc.affected_pods)}\n"
    
    prompt += "\nRelevant Issues:\n"
    for issue in relevant_issues[:5]:
        prompt += f"- {issue.description} (Pod: {issue.pod_name})\n"
    
    if relevant_logs:
        prompt += "\nRelevant Log Entries:\n"
        for log in relevant_logs[:20]:
            timestamp_str = log.timestamp.strftime("%H:%M:%S") if log.timestamp else "N/A"
            prompt += f"[{timestamp_str}] {log.pod_name}: {log.message[:150]}\n"
    
    prompt += "\n<extra_id_1>Assistant\n"
    
    return prompt

