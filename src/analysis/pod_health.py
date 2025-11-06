"""Pod health assessment module."""
from datetime import datetime
from typing import Optional

from src.models.analysis import LogEntry, LogLevel, PodHealth, PodState
from src.models.bundle import PodStatus


def assess_pod_health(pod_status: PodStatus, logs: list[LogEntry]) -> PodHealth:
    """
    Assess the health of a pod based on its status and logs.
    
    Args:
        pod_status: PodStatus from bundle parsing
        logs: List of LogEntry objects for this pod
        
    Returns:
        PodHealth assessment
    """
    # Determine state based on pod phase and conditions
    state = _determine_pod_state(pod_status)
    
    # Count errors and warnings
    error_count = sum(1 for log in logs if log.level in [LogLevel.ERROR, LogLevel.FATAL])
    warning_count = sum(1 for log in logs if log.level == LogLevel.WARN)
    
    # Find last error
    last_error = None
    for log in sorted(logs, key=lambda l: l.timestamp or datetime.min, reverse=True):
        if log.level in [LogLevel.ERROR, LogLevel.FATAL]:
            last_error = log
            break
    
    # Extract crash reason
    crash_reason = _extract_crash_reason(pod_status, logs)
    
    # Determine if healthy
    is_healthy = (
        state == PodState.HEALTHY
        and error_count == 0
        and pod_status.restart_count == 0
    )
    
    return PodHealth(
        pod_name=pod_status.pod_name,
        namespace=pod_status.namespace,
        state=state,
        is_healthy=is_healthy,
        crash_reason=crash_reason,
        restart_count=pod_status.restart_count,
        error_count=error_count,
        warning_count=warning_count,
        last_error=last_error,
        assessed_at=datetime.now(),
    )


def _determine_pod_state(pod_status: PodStatus) -> PodState:
    """Determine pod state from status information."""
    phase = pod_status.phase.lower()
    
    if phase == "failed":
        return PodState.CRASHED
    elif phase == "pending":
        return PodState.PENDING
    elif phase == "running":
        # Check if actually healthy
        if not pod_status.ready:
            return PodState.DEGRADED
        if pod_status.restart_count > 0:
            # Check container statuses
            for container_status in pod_status.container_statuses:
                if container_status.state != "running" or not container_status.ready:
                    return PodState.DEGRADED
            # Has restarts but currently running - degraded
            return PodState.DEGRADED
        return PodState.HEALTHY
    elif phase == "succeeded":
        # Job completed successfully
        return PodState.HEALTHY
    else:
        return PodState.UNKNOWN


def _extract_crash_reason(pod_status: PodStatus, logs: list[LogEntry]) -> Optional[str]:
    """Extract crash reason from pod status and logs."""
    # Check container statuses for termination reasons
    for container_status in pod_status.container_statuses:
        if container_status.last_state == "terminated":
            # Would need to access termination details from original status
            # For now, check logs for crash indicators
            pass
    
    # Look for crash indicators in logs
    crash_keywords = [
        "panic",
        "fatal",
        "crash",
        "segmentation fault",
        "out of memory",
        "oom",
        "killed",
        "exit code",
        "exit status",
    ]
    
    for log in sorted(logs, key=lambda l: l.timestamp or datetime.min, reverse=True):
        message_lower = log.message.lower()
        for keyword in crash_keywords:
            if keyword in message_lower:
                return f"Crash detected: {log.message[:200]}"
    
    # Check pod conditions
    for condition in pod_status.conditions:
        if condition.type == "Ready" and condition.status != "True":
            if condition.reason:
                return f"Pod not ready: {condition.reason}"
            if condition.message:
                return f"Pod not ready: {condition.message[:200]}"
    
    return None

