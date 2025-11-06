"""Temporal correlation analysis for root cause detection."""
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from src.models.analysis import Issue, LogEntry


def correlate_events_by_time(
    issues: list[Issue],
    logs: list[LogEntry],
    correlation_window: timedelta = timedelta(minutes=5),
) -> dict[str, list[str]]:
    """
    Correlate events temporally to identify causal relationships.
    
    Uses time-series correlation: events that occur close in time
    and in dependency order are likely related. The first event
    in a correlated group is likely the root cause.
    
    Args:
        issues: List of issues to correlate
        logs: List of log entries for additional context
        correlation_window: Time window for considering events correlated
        
    Returns:
        Dictionary mapping root cause issue IDs to list of symptom issue IDs
    """
    if not issues:
        return {}
    
    # Create time-series of issues
    issue_timeseries = []
    for issue in issues:
        if issue.first_seen:
            issue_timeseries.append((issue.first_seen, issue))
    
    # Sort by time
    issue_timeseries.sort(key=lambda x: x[0])
    
    # Group issues by time windows
    correlated_groups: list[list[Issue]] = []
    current_group: list[Issue] = []
    current_window_start: Optional[datetime] = None
    
    for timestamp, issue in issue_timeseries:
        if current_window_start is None:
            current_group = [issue]
            current_window_start = timestamp
        elif timestamp - current_window_start <= correlation_window:
            current_group.append(issue)
        else:
            # Start new group
            if len(current_group) > 1:
                correlated_groups.append(current_group)
            current_group = [issue]
            current_window_start = timestamp
    
    # Add last group
    if len(current_group) > 1:
        correlated_groups.append(current_group)
    
    # For each group, identify root cause (first event) and symptoms
    root_to_symptoms: dict[str, list[str]] = {}
    
    for group in correlated_groups:
        if not group:
            continue
        
        # Sort by severity and time (critical + earliest = root cause)
        group_sorted = sorted(
            group,
            key=lambda i: (
                0 if i.severity == "critical" else 1 if i.severity == "high" else 2,
                i.first_seen,
            ),
        )
        
        root_issue = group_sorted[0]
        symptom_issues = [i for i in group_sorted[1:] if i.issue_id != root_issue.issue_id]
        
        if symptom_issues:
            root_to_symptoms[root_issue.issue_id] = [
                s.issue_id for s in symptom_issues
            ]
    
    return root_to_symptoms


def detect_anomalous_spikes(
    logs: list[LogEntry],
    baseline_window: Optional[timedelta] = None,
    spike_threshold: float = 3.0,
) -> list[LogEntry]:
    """
    Detect anomalous spikes in log frequency.
    
    Uses statistical methods (like 3-sigma rule) to identify when
    a log pattern appears much more frequently than normal.
    
    Args:
        logs: List of log entries
        baseline_window: Time window to use for baseline (None = use all data)
        spike_threshold: Number of standard deviations for spike detection
        
    Returns:
        List of log entries that are part of anomalous spikes
    """
    if not logs:
        return []
    
    # Group logs by template (similar messages)
    from src.analysis.log_clustering import extract_log_templates
    
    templates = extract_log_templates(logs)
    
    # Calculate frequency per template over time
    anomalous_logs = []
    
    for template, template_logs in templates.items():
        if len(template_logs) < 3:  # Need at least 3 for statistics
            continue
        
        # Create time buckets
        timestamps = [log.timestamp for log in template_logs if log.timestamp]
        if not timestamps or len(timestamps) < 3:
            continue
        
        # Use 1-minute buckets
        bucket_size = timedelta(minutes=1)
        buckets: dict[datetime, int] = defaultdict(int)
        
        for ts in timestamps:
            bucket = ts.replace(second=0, microsecond=0)
            buckets[bucket] += 1
        
        if not buckets:
            continue
        
        # Calculate baseline (mean and std)
        frequencies = list(buckets.values())
        mean_freq = sum(frequencies) / len(frequencies)
        
        if len(frequencies) < 2:
            continue
        
        variance = sum((f - mean_freq) ** 2 for f in frequencies) / (len(frequencies) - 1)
        std_freq = variance ** 0.5
        
        if std_freq == 0:
            continue
        
        # Find buckets that are spikes
        threshold = mean_freq + (spike_threshold * std_freq)
        
        for bucket, count in buckets.items():
            if count >= threshold:
                # This bucket is anomalous - include logs from this bucket
                bucket_start = bucket
                bucket_end = bucket + bucket_size
                
                for log in template_logs:
                    if log.timestamp and bucket_start <= log.timestamp < bucket_end:
                        anomalous_logs.append(log)
    
    return anomalous_logs

