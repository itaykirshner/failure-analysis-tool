"""Analysis modules for log processing and insights."""
from src.analysis.connectivity import detect_connectivity_issues
from src.analysis.log_analyzer import analyze_logs
from src.analysis.log_clustering import (
    cluster_logs_by_similarity,
    extract_log_templates,
    summarize_clusters,
)
from src.analysis.noise_reducer import (
    collapse_repeated_errors,
    deduplicate_logs,
    filter_irrelevant_warnings,
)
from src.analysis.pod_health import assess_pod_health
from src.analysis.root_cause import identify_root_causes
from src.analysis.temporal_correlation import (
    correlate_events_by_time,
    detect_anomalous_spikes,
)

__all__ = [
    "analyze_logs",
    "deduplicate_logs",
    "filter_irrelevant_warnings",
    "collapse_repeated_errors",
    "assess_pod_health",
    "detect_connectivity_issues",
    "identify_root_causes",
    "extract_log_templates",
    "cluster_logs_by_similarity",
    "summarize_clusters",
    "correlate_events_by_time",
    "detect_anomalous_spikes",
]

