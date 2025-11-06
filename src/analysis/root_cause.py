"""Root cause analysis module."""
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Optional

from src.models.analysis import (
    ConnectivityIssue,
    Issue,
    PodHealth,
    RootCause,
)


def identify_root_causes(
    issues: list[Issue],
    pod_health: list[PodHealth],
    connectivity_issues: list[ConnectivityIssue],
) -> list[RootCause]:
    """
    Identify root causes from issues, pod health, and connectivity problems.
    
    Builds a dependency graph and performs temporal analysis to identify
    which issues are root causes vs symptoms.
    
    Args:
        issues: List of general issues
        pod_health: List of pod health assessments
        connectivity_issues: List of connectivity issues
        
    Returns:
        List of identified root causes
    """
    # Build dependency graph
    dependency_graph = _build_dependency_graph(issues, pod_health, connectivity_issues)
    
    # Identify unhealthy pods
    unhealthy_pods = [ph for ph in pod_health if not ph.is_healthy]
    
    # Map issues to pods
    pod_issues: dict[str, list[Issue]] = defaultdict(list)
    for issue in issues:
        pod_issues[issue.pod_name].append(issue)
    
    # Map connectivity issues to pods
    pod_connectivity: dict[str, list[ConnectivityIssue]] = defaultdict(list)
    for conn_issue in connectivity_issues:
        pod_connectivity[conn_issue.source_pod].append(conn_issue)
    
    root_causes = []
    
    # Analyze each unhealthy pod
    for pod_health_item in unhealthy_pods:
        pod_name = pod_health_item.pod_name
        
        # Check if this pod's issues are symptoms of another pod's failure
        is_symptom = _is_symptom_pod(pod_name, dependency_graph, unhealthy_pods)
        
        if is_symptom:
            continue
        
        # This pod's issues might be a root cause
        pod_issues_list = pod_issues.get(pod_name, [])
        pod_conn_issues = pod_connectivity.get(pod_name, [])
        
        if not pod_issues_list and not pod_conn_issues and pod_health_item.state.value != "crashed":
            continue
        
        # Determine primary issue
        primary_issue = _get_primary_issue(pod_issues_list, pod_conn_issues, pod_health_item)
        
        if not primary_issue:
            continue
        
        # Find symptom issues (issues in dependent pods)
        symptom_issue_ids = _find_symptom_issues(pod_name, dependency_graph, issues)
        
        # Collect affected pods
        affected_pods = [pod_name]
        affected_namespaces = [pod_health_item.namespace]
        
        for symptom_id in symptom_issue_ids:
            symptom_issue = next((i for i in issues if i.issue_id == symptom_id), None)
            if symptom_issue:
                affected_pods.append(symptom_issue.pod_name)
                if symptom_issue.namespace not in affected_namespaces:
                    affected_namespaces.append(symptom_issue.namespace)
        
        # Calculate confidence
        confidence = _calculate_confidence(
            pod_health_item,
            pod_issues_list,
            pod_conn_issues,
            symptom_issue_ids,
        )
        
        # Generate description
        description = _generate_root_cause_description(
            pod_health_item,
            primary_issue,
            len(symptom_issue_ids),
        )
        
        # Collect evidence
        evidence = _collect_evidence(pod_health_item, pod_issues_list, pod_conn_issues)
        
        root_cause = RootCause(
            root_cause_id=str(uuid.uuid4()),
            primary_issue_id=primary_issue.issue_id if isinstance(primary_issue, Issue) else pod_name,
            description=description,
            affected_pods=affected_pods,
            affected_namespaces=affected_namespaces,
            symptom_issues=symptom_issue_ids,
            confidence=confidence,
            evidence=evidence,
            identified_at=datetime.now(),
        )
        
        root_causes.append(root_cause)
    
    return root_causes


def _build_dependency_graph(
    issues: list[Issue],
    pod_health: list[PodHealth],
    connectivity_issues: list[ConnectivityIssue],
) -> dict[str, list[str]]:
    """
    Build a dependency graph from issues and connectivity problems.
    
    Returns a dict mapping pod names to lists of dependent pod names.
    """
    graph: dict[str, list[str]] = defaultdict(list)
    
    # Add edges from connectivity issues
    for conn_issue in connectivity_issues:
        source = conn_issue.source_pod
        target = conn_issue.target_service
        
        # Try to find target pod
        target_pod = _find_pod_by_service(target, pod_health)
        if target_pod:
            graph[target_pod].append(source)  # target depends on source (reverse)
    
    # Add edges from issue messages (look for service references)
    import re
    
    for issue in issues:
        message_lower = issue.description.lower()
        
        # Look for service names in error messages
        service_pattern = r"([a-z0-9-]+)\.([a-z0-9-]+)\.svc"
        matches = re.findall(service_pattern, message_lower)
        
        for service_name, namespace in matches:
            target_pod = _find_pod_by_service(service_name, pod_health, namespace)
            if target_pod and target_pod != issue.pod_name:
                graph[target_pod].append(issue.pod_name)
    
    return dict(graph)


def _find_pod_by_service(
    service_name: str,
    pod_health: list[PodHealth],
    namespace: Optional[str] = None,
) -> Optional[str]:
    """Find a pod name by service name."""
    for ph in pod_health:
        # Simple heuristic: service name often matches pod name prefix
        if service_name in ph.pod_name.lower():
            if namespace is None or ph.namespace == namespace:
                return ph.pod_name
    
    return None


def _is_symptom_pod(
    pod_name: str,
    dependency_graph: dict[str, list[str]],
    unhealthy_pods: list[PodHealth],
) -> bool:
    """
    Check if a pod's issues are symptoms of another pod's failure.
    
    A pod is a symptom if:
    - It depends on another unhealthy pod
    - That other pod failed before this pod
    """
    # Check if this pod depends on any unhealthy pod
    for unhealthy_pod in unhealthy_pods:
        if unhealthy_pod.pod_name == pod_name:
            continue
        
        dependents = dependency_graph.get(unhealthy_pod.pod_name, [])
        if pod_name in dependents:
            # This pod depends on an unhealthy pod
            # Check temporal ordering (simplified: if unhealthy pod has more restarts or errors)
            if (
                unhealthy_pod.restart_count > 0
                or unhealthy_pod.error_count > 0
                or unhealthy_pod.state.value == "crashed"
            ):
                return True
    
    return False


def _get_primary_issue(
    pod_issues: list[Issue],
    connectivity_issues: list[ConnectivityIssue],
    pod_health: PodHealth,
) -> Optional[Issue | PodHealth]:
    """Get the primary issue for a pod."""
    # Prioritize crashes
    if pod_health.state.value == "crashed":
        return pod_health
    
    # Prioritize critical issues
    critical_issues = [i for i in pod_issues if i.severity == "critical"]
    if critical_issues:
        return min(critical_issues, key=lambda i: i.first_seen)
    
    # Prioritize high severity issues
    high_issues = [i for i in pod_issues if i.severity == "high"]
    if high_issues:
        return min(high_issues, key=lambda i: i.first_seen)
    
    # Return first issue by time
    if pod_issues:
        return min(pod_issues, key=lambda i: i.first_seen)
    
    # Return pod health if it has errors
    if pod_health.error_count > 0 or pod_health.restart_count > 0:
        return pod_health
    
    return None


def _find_symptom_issues(
    root_pod: str,
    dependency_graph: dict[str, list[str]],
    issues: list[Issue],
) -> list[str]:
    """Find issues that are symptoms of the root pod's failure."""
    symptom_ids = []
    
    # Find pods that depend on root_pod
    dependents = dependency_graph.get(root_pod, [])
    
    for dependent_pod in dependents:
        # Find issues in dependent pods
        for issue in issues:
            if issue.pod_name == dependent_pod:
                symptom_ids.append(issue.issue_id)
    
    return symptom_ids


def _calculate_confidence(
    pod_health: PodHealth,
    pod_issues: list[Issue],
    connectivity_issues: list[ConnectivityIssue],
    symptom_count: int,
) -> float:
    """Calculate confidence score for root cause identification."""
    confidence = 0.5  # Base confidence
    
    # Increase confidence if pod is crashed
    if pod_health.state.value == "crashed":
        confidence += 0.3
    
    # Increase confidence if there are symptoms
    if symptom_count > 0:
        confidence += min(0.2, symptom_count * 0.05)
    
    # Increase confidence if there are many errors
    if pod_health.error_count > 10:
        confidence += 0.1
    
    # Increase confidence if there are connectivity issues pointing to this pod
    if connectivity_issues:
        confidence += 0.1
    
    return min(1.0, confidence)


def _generate_root_cause_description(
    pod_health: PodHealth,
    primary_issue: Issue | PodHealth,
    symptom_count: int,
) -> str:
    """Generate a human-readable root cause description."""
    if pod_health.state.value == "crashed":
        desc = f"Pod {pod_health.pod_name} in namespace {pod_health.namespace} is crashed"
        if pod_health.crash_reason:
            desc += f": {pod_health.crash_reason}"
    elif isinstance(primary_issue, Issue):
        desc = f"Root cause in pod {pod_health.pod_name}: {primary_issue.description}"
    else:
        desc = f"Pod {pod_health.pod_name} is unhealthy with {pod_health.error_count} errors"
    
    if symptom_count > 0:
        desc += f", causing {symptom_count} dependent issue(s)"
    
    return desc


def _collect_evidence(
    pod_health: PodHealth,
    pod_issues: list[Issue],
    connectivity_issues: list[ConnectivityIssue],
) -> list[str]:
    """Collect evidence for root cause."""
    evidence = []
    
    if pod_health.crash_reason:
        evidence.append(f"Crash reason: {pod_health.crash_reason}")
    
    if pod_health.restart_count > 0:
        evidence.append(f"Pod has restarted {pod_health.restart_count} time(s)")
    
    if pod_health.error_count > 0:
        evidence.append(f"Pod has {pod_health.error_count} error(s) in logs")
    
    if pod_health.last_error:
        evidence.append(f"Last error: {pod_health.last_error.message[:200]}")
    
    for issue in pod_issues[:3]:  # Limit to first 3 issues
        evidence.append(f"Issue: {issue.description[:200]}")
    
    for conn_issue in connectivity_issues[:2]:  # Limit to first 2
        evidence.append(
            f"Connectivity issue: {conn_issue.error_type} to {conn_issue.target_service}"
        )
    
    return evidence

