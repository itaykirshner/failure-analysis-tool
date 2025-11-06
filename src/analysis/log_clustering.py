"""Log clustering module using template extraction and clustering."""
from collections import Counter
from typing import Optional

from src.models.analysis import LogEntry, LogLevel


def extract_log_templates(logs: list[LogEntry]) -> dict[str, list[LogEntry]]:
    """
    Extract log templates using a simplified Drain-like algorithm.
    
    Groups logs by template pattern, replacing variable parts with placeholders.
    This is more sophisticated than simple normalization - it identifies
    the structure of log messages.
    
    Args:
        logs: List of log entries to cluster
        
    Returns:
        Dictionary mapping template patterns to lists of log entries
    """
    templates: dict[str, list[LogEntry]] = {}
    
    for entry in logs:
        template = _extract_template(entry.message)
        templates.setdefault(template, []).append(entry)
    
    return templates


def _extract_template(message: str) -> str:
    """
    Extract a template from a log message using heuristics.
    
    Replaces variable parts (numbers, UUIDs, IPs, etc.) with placeholders
    to create a template pattern. This is a simplified version of Drain.
    """
    import re
    
    # Start with the message
    template = message
    
    # Replace common variable patterns with placeholders
    # UUIDs
    template = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "<UUID>",
        template,
        flags=re.IGNORECASE,
    )
    
    # IP addresses
    template = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "<IP>", template)
    
    # Hex addresses
    template = re.sub(r"0x[0-9a-f]+", "<HEX>", template, flags=re.IGNORECASE)
    
    # File paths
    template = re.sub(r"/[^\s]+", "<PATH>", template)
    template = re.sub(r"[A-Z]:\\[^\s]+", "<PATH>", template, flags=re.IGNORECASE)
    
    # Email addresses
    template = re.sub(r"\b[\w.-]+@[\w.-]+\.\w+\b", "<EMAIL>", template)
    
    # Timestamps (various formats)
    template = re.sub(
        r"\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?",
        "<TIMESTAMP>",
        template,
    )
    template = re.sub(r"\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}", "<TIMESTAMP>", template)
    template = re.sub(r"\d{10}(?:\.\d+)?", "<TIMESTAMP>", template)
    
    # Numbers (but be smart about it - preserve structure)
    # Only replace standalone numbers, not numbers that are part of words
    template = re.sub(r"\b\d+\b", "<NUM>", template)
    
    # URLs
    template = re.sub(r"https?://[^\s]+", "<URL>", template)
    
    # Normalize whitespace
    template = re.sub(r"\s+", " ", template)
    
    return template.strip()


def cluster_logs_by_similarity(
    logs: list[LogEntry],
    min_cluster_size: int = 2,
    similarity_threshold: float = 0.7,
) -> dict[str, list[LogEntry]]:
    """
    Cluster logs by semantic similarity using TF-IDF and simple distance.
    
    This groups logs that are similar but not identical, handling variations
    in log messages from different apps or formats.
    
    Args:
        logs: List of log entries to cluster
        min_cluster_size: Minimum size for a cluster to be kept
        similarity_threshold: Minimum similarity (0-1) to group logs
        
    Returns:
        Dictionary mapping cluster IDs to lists of log entries
    """
    if not logs:
        return {}
    
    # First, extract templates
    templates = extract_log_templates(logs)
    
    # For each template, create a cluster
    clusters: dict[str, list[LogEntry]] = {}
    
    for template, template_logs in templates.items():
        if len(template_logs) >= min_cluster_size:
            cluster_id = f"cluster_{hash(template) % 10000}"
            clusters[cluster_id] = template_logs
    
    # Now, try to merge similar templates using simple string similarity
    # (Full TF-IDF + DBSCAN would require scikit-learn, keeping it simple for now)
    merged_clusters = _merge_similar_clusters(clusters, similarity_threshold)
    
    return merged_clusters


def _merge_similar_clusters(
    clusters: dict[str, list[LogEntry]],
    similarity_threshold: float,
) -> dict[str, list[LogEntry]]:
    """
    Merge clusters with similar templates.
    
    Uses simple Jaccard similarity on words to find similar templates.
    """
    if len(clusters) <= 1:
        return clusters
    
    # Get template for each cluster (use first log's template)
    cluster_templates = {}
    for cluster_id, logs in clusters.items():
        if logs:
            template = _extract_template(logs[0].message)
            cluster_templates[cluster_id] = template
    
    # Calculate similarity matrix
    cluster_ids = list(clusters.keys())
    merged = set()
    result = {}
    
    for i, cluster_id_1 in enumerate(cluster_ids):
        if cluster_id_1 in merged:
            continue
        
        template_1 = cluster_templates[cluster_id_1]
        words_1 = set(template_1.lower().split())
        
        # Try to merge with other clusters
        merged_logs = clusters[cluster_id_1].copy()
        
        for cluster_id_2 in cluster_ids[i + 1:]:
            if cluster_id_2 in merged:
                continue
            
            template_2 = cluster_templates[cluster_id_2]
            words_2 = set(template_2.lower().split())
            
            # Calculate Jaccard similarity
            intersection = len(words_1 & words_2)
            union = len(words_1 | words_2)
            
            if union == 0:
                similarity = 0.0
            else:
                similarity = intersection / union
            
            if similarity >= similarity_threshold:
                merged_logs.extend(clusters[cluster_id_2])
                merged.add(cluster_id_2)
        
        result[cluster_id_1] = merged_logs
        merged.add(cluster_id_1)
    
    return result


def summarize_clusters(clusters: dict[str, list[LogEntry]]) -> list[dict]:
    """
    Summarize clusters into actionable event types.
    
    Args:
        clusters: Dictionary of clusters
        
    Returns:
        List of cluster summaries with counts and representative messages
    """
    summaries = []
    
    for cluster_id, logs in clusters.items():
        if not logs:
            continue
        
        # Get representative message (most common or first)
        message_counts = Counter(log.message for log in logs)
        representative = message_counts.most_common(1)[0][0]
        
        # Count by level
        level_counts = Counter(log.level for log in logs)
        
        # Get time range
        timestamps = [log.timestamp for log in logs if log.timestamp]
        first_seen = min(timestamps) if timestamps else None
        last_seen = max(timestamps) if timestamps else None
        
        summaries.append({
            "cluster_id": cluster_id,
            "count": len(logs),
            "representative_message": representative,
            "level_distribution": {level.value: count for level, count in level_counts.items()},
            "first_seen": first_seen,
            "last_seen": last_seen,
            "affected_pods": list(set(log.pod_name for log in logs)),
        })
    
    # Sort by count (most frequent first)
    summaries.sort(key=lambda x: x["count"], reverse=True)
    
    return summaries

