"""Connectivity issue detection module."""
import re
from collections import defaultdict
from datetime import datetime
from typing import Optional

from src.models.analysis import ConnectivityIssue, LogEntry, LogLevel, PodHealth


def detect_connectivity_issues(
    logs: list[LogEntry],
    pod_health: list[PodHealth],
) -> list[ConnectivityIssue]:
    """
    Detect connectivity issues between services from logs.
    
    Identifies:
    - Connection refused errors
    - Timeout errors
    - DNS resolution failures
    - Network unreachable errors
    
    Args:
        logs: List of log entries to analyze
        pod_health: List of pod health assessments
        
    Returns:
        List of ConnectivityIssue objects
    """
    connectivity_errors = _extract_connectivity_errors(logs)
    
    # Group by source pod and target service
    issue_groups: dict[tuple[str, str, str], list[LogEntry]] = defaultdict(list)
    
    for entry in connectivity_errors:
        target = _extract_target_service(entry.message)
        if target:
            key = (entry.pod_name, entry.namespace, target)
            issue_groups[key].append(entry)
    
    # Create ConnectivityIssue objects
    issues = []
    
    for (source_pod, source_namespace, target_service), entries in issue_groups.items():
        if not entries:
            continue
        
        # Determine error type
        error_type = _classify_connectivity_error(entries[0].message)
        
        # Extract target namespace if available
        target_namespace = _extract_target_namespace(entries[0].message, target_service)
        
        # Get timestamps
        timestamps = [e.timestamp for e in entries if e.timestamp]
        if not timestamps:
            continue
        
        first_occurrence = min(timestamps)
        last_occurrence = max(timestamps)
        
        # Get representative error message
        error_message = entries[0].message
        
        issues.append(
            ConnectivityIssue(
                source_pod=source_pod,
                source_namespace=source_namespace,
                target_service=target_service,
                target_namespace=target_namespace,
                error_type=error_type,
                error_message=error_message,
                first_occurrence=first_occurrence,
                last_occurrence=last_occurrence,
                occurrence_count=len(entries),
                related_logs=entries,
            )
        )
    
    return issues


def _extract_connectivity_errors(logs: list[LogEntry]) -> list[LogEntry]:
    """Extract log entries that indicate connectivity issues."""
    connectivity_patterns = [
        r"connection.*refused",
        r"connection.*reset",
        r"connection.*timeout",
        r"timeout.*connecting",
        r"dial.*timeout",
        r"no such host",
        r"name resolution",
        r"dns.*error",
        r"network.*unreachable",
        r"connection.*closed",
        r"broken pipe",
        r"connection.*aborted",
        r"failed to connect",
        r"cannot.*connect",
        r"unable.*to.*connect",
        r"service.*unavailable",
        r"503.*service",
        r"502.*bad.*gateway",
        r"504.*gateway.*timeout",
    ]
    
    connectivity_errors = []
    
    for entry in logs:
        if entry.level not in [LogLevel.ERROR, LogLevel.WARN]:
            continue
        
        message_lower = entry.message.lower()
        
        for pattern in connectivity_patterns:
            if re.search(pattern, message_lower):
                connectivity_errors.append(entry)
                break
    
    return connectivity_errors


def _extract_target_service(message: str) -> Optional[str]:
    """
    Extract target service name from error message.
    
    Looks for patterns like:
    - service-name:port
    - service-name.namespace
    - http://service-name
    - service-name.svc.cluster.local
    """
    # Kubernetes service FQDN pattern
    k8s_service_pattern = r"([a-z0-9-]+)\.([a-z0-9-]+)\.svc\.cluster\.local"
    match = re.search(k8s_service_pattern, message, re.IGNORECASE)
    if match:
        return match.group(1)
    
    # service-name:port pattern
    service_port_pattern = r"([a-z0-9-]+):(\d+)"
    match = re.search(service_port_pattern, message, re.IGNORECASE)
    if match:
        return match.group(1)
    
    # service-name.namespace pattern
    service_ns_pattern = r"([a-z0-9-]+)\.([a-z0-9-]+)(?:\s|$|:)"
    match = re.search(service_ns_pattern, message, re.IGNORECASE)
    if match:
        return match.group(1)
    
    # http://service-name or https://service-name
    http_pattern = r"https?://([a-z0-9-]+)"
    match = re.search(http_pattern, message, re.IGNORECASE)
    if match:
        return match.group(1)
    
    # Look for common service names in error context
    common_services = [
        "database",
        "db",
        "redis",
        "postgres",
        "mysql",
        "mongodb",
        "api",
        "backend",
        "frontend",
        "auth",
        "gateway",
    ]
    
    message_lower = message.lower()
    for service in common_services:
        if service in message_lower:
            return service
    
    return None


def _extract_target_namespace(message: str, target_service: str) -> Optional[str]:
    """Extract target namespace from error message."""
    # Kubernetes FQDN: service.namespace.svc.cluster.local
    fqdn_pattern = rf"{re.escape(target_service)}\.([a-z0-9-]+)\.svc\.cluster\.local"
    match = re.search(fqdn_pattern, message, re.IGNORECASE)
    if match:
        return match.group(1)
    
    # service.namespace pattern
    service_ns_pattern = rf"{re.escape(target_service)}\.([a-z0-9-]+)"
    match = re.search(service_ns_pattern, message, re.IGNORECASE)
    if match:
        return match.group(1)
    
    return None


def _classify_connectivity_error(message: str) -> str:
    """Classify the type of connectivity error."""
    message_lower = message.lower()
    
    if re.search(r"connection.*refused|refused.*connection", message_lower):
        return "connection_refused"
    elif re.search(r"timeout|timed.*out", message_lower):
        return "timeout"
    elif re.search(r"dns|name.*resolution|no such host", message_lower):
        return "dns_error"
    elif re.search(r"network.*unreachable|unreachable", message_lower):
        return "network_unreachable"
    elif re.search(r"connection.*reset|reset.*connection", message_lower):
        return "connection_reset"
    elif re.search(r"broken.*pipe|pipe.*broken", message_lower):
        return "broken_pipe"
    elif re.search(r"503|service.*unavailable", message_lower):
        return "service_unavailable"
    elif re.search(r"502|bad.*gateway", message_lower):
        return "bad_gateway"
    elif re.search(r"504|gateway.*timeout", message_lower):
        return "gateway_timeout"
    else:
        return "unknown"

