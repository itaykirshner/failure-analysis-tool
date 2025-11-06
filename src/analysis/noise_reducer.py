"""Noise reduction module for filtering and deduplicating logs."""
import hashlib
import re
from collections import Counter
from datetime import datetime
from typing import Optional

from src.models.analysis import LogEntry, LogLevel


# Known benign warning patterns that can be ignored
BENIGN_WARNING_PATTERNS = [
    r"deprecated",
    r"deprecation",
    r"will be removed",
    r"future version",
    r"health check",
    r"liveness probe",
    r"readiness probe",
    r"metrics endpoint",
    r"debug mode",
    r"verbose logging",
    r"trace.*enabled",
    r"cache.*miss",
    r"cache.*hit",
    r"rate limit.*not.*exceeded",
    r"connection.*pool.*available",
    r"retry.*scheduled",
    r"backoff.*delay",
]

# Patterns that should be kept even if they appear frequently
CRITICAL_PATTERNS = [
    r"crash",
    r"panic",
    r"fatal",
    r"out of memory",
    r"oom",
    r"segmentation fault",
    r"connection.*refused",
    r"timeout",
    r"deadline.*exceeded",
    r"context.*deadline",
]


def deduplicate_logs(logs: list[LogEntry]) -> list[LogEntry]:
    """
    Deduplicate logs by collapsing identical entries.
    
    For identical log messages, keeps the first occurrence and adds
    a count to the message. Returns a deduplicated list.
    
    Args:
        logs: List of log entries to deduplicate
        
    Returns:
        Deduplicated list of log entries
    """
    if not logs:
        return []
    
    # Group by hash
    hash_groups: dict[str, list[LogEntry]] = {}
    
    for entry in logs:
        if not entry.hash:
            entry.hash = hashlib.md5(entry.message.encode()).hexdigest()
        
        hash_groups.setdefault(entry.hash, []).append(entry)
    
    # Keep first occurrence, add count if multiple
    deduplicated = []
    
    for entries in hash_groups.values():
        if len(entries) == 1:
            deduplicated.append(entries[0])
        else:
            # Keep first entry, add occurrence count
            first_entry = entries[0]
            count = len(entries)
            
            # Update message to include count
            if count > 1:
                first_entry.message = f"{first_entry.message} [repeated {count} times]"
            
            deduplicated.append(first_entry)
    
    return deduplicated


def filter_irrelevant_warnings(logs: list[LogEntry], min_frequency: int = 3) -> list[LogEntry]:
    """
    Filter out irrelevant warnings that don't contribute to issue detection.
    
    Filters:
    - Known benign warning patterns
    - Warnings that appear less than min_frequency times (likely noise)
    - But keeps warnings that appear near errors (context-aware)
    
    Args:
        logs: List of log entries to filter
        min_frequency: Minimum occurrence count to keep a warning
        
    Returns:
        Filtered list of log entries
    """
    if not logs:
        return []
    
    # Count warning frequencies
    warning_counts = Counter()
    warning_entries: dict[str, list[LogEntry]] = {}
    
    for entry in logs:
        if entry.level == LogLevel.WARN:
            pattern = _normalize_warning_pattern(entry.message)
            warning_counts[pattern] += 1
            warning_entries.setdefault(pattern, []).append(entry)
    
    # Identify errors and their timestamps for context-aware filtering
    error_timestamps = set()
    for entry in logs:
        if entry.level in [LogLevel.ERROR, LogLevel.FATAL] and entry.timestamp:
            error_timestamps.add(entry.timestamp)
    
    filtered = []
    
    for entry in logs:
        if entry.level != LogLevel.WARN:
            # Keep all non-warnings
            filtered.append(entry)
            continue
        
        # Check if it matches benign patterns
        if _matches_benign_pattern(entry.message):
            # Only keep if it appears near an error (context-aware)
            if not _is_near_error(entry, error_timestamps):
                continue
        
        # Check if it matches critical patterns (always keep)
        if _matches_critical_pattern(entry.message):
            filtered.append(entry)
            continue
        
        # Check frequency threshold
        pattern = _normalize_warning_pattern(entry.message)
        count = warning_counts.get(pattern, 0)
        
        if count >= min_frequency:
            filtered.append(entry)
        elif _is_near_error(entry, error_timestamps):
            # Keep low-frequency warnings near errors
            filtered.append(entry)
    
    return filtered


def _normalize_warning_pattern(message: str) -> str:
    """
    Normalize a warning message to a pattern for frequency counting.
    
    Removes variable parts (numbers, UUIDs, timestamps, etc.) to group
    similar messages together regardless of format differences.
    """
    import re
    
    # Remove timestamps (various formats)
    pattern = re.sub(
        r"\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?",
        "<TIMESTAMP>",
        message,
    )
    pattern = re.sub(r"\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}", "<TIMESTAMP>", pattern)
    pattern = re.sub(r"\d{10}(?:\.\d+)?", "<TIMESTAMP>", pattern)  # Unix timestamp
    
    # Remove UUIDs
    pattern = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "<UUID>",
        pattern,
        flags=re.IGNORECASE,
    )
    
    # Remove hex addresses
    pattern = re.sub(r"0x[0-9a-f]+", "<HEX>", pattern, flags=re.IGNORECASE)
    
    # Remove numbers (but preserve structure)
    pattern = re.sub(r"\d+", "<NUM>", pattern)
    
    # Remove file paths (common in logs)
    pattern = re.sub(r"/[^\s]+", "<PATH>", pattern)
    pattern = re.sub(r"[A-Z]:\\[^\s]+", "<PATH>", pattern, flags=re.IGNORECASE)
    
    # Remove IP addresses
    pattern = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "<IP>", pattern)
    
    # Remove email addresses
    pattern = re.sub(r"\b[\w.-]+@[\w.-]+\.\w+\b", "<EMAIL>", pattern)
    
    # Normalize whitespace
    pattern = re.sub(r"\s+", " ", pattern)
    
    return pattern.lower().strip()


def _matches_benign_pattern(message: str) -> bool:
    """
    Check if message matches known benign warning patterns.
    
    Uses flexible matching that works across different log formats
    by normalizing the message first.
    """
    import re
    
    message_lower = message.lower()
    
    # Normalize message for pattern matching
    normalized = _normalize_warning_pattern(message)
    
    for pattern in BENIGN_WARNING_PATTERNS:
        # Try both original and normalized message
        if re.search(pattern, message_lower) or re.search(pattern, normalized):
            return True
    
    return False


def _matches_critical_pattern(message: str) -> bool:
    """
    Check if message matches critical patterns that should always be kept.
    
    Uses flexible matching that works across different log formats.
    """
    import re
    
    message_lower = message.lower()
    
    # Normalize message for pattern matching
    normalized = _normalize_warning_pattern(message)
    
    for pattern in CRITICAL_PATTERNS:
        # Try both original and normalized message
        if re.search(pattern, message_lower) or re.search(pattern, normalized):
            return True
    
    return False


def _is_near_error(entry: LogEntry, error_timestamps: set) -> bool:
    """
    Check if a log entry is temporally near an error.
    
    Considers an entry "near" an error if it's within 60 seconds.
    """
    if not entry.timestamp or not error_timestamps:
        return False
    
    from datetime import timedelta
    
    time_window = timedelta(seconds=60)
    
    for error_time in error_timestamps:
        if abs((entry.timestamp - error_time).total_seconds()) <= time_window.total_seconds():
            return True
    
    return False


def collapse_repeated_errors(logs: list[LogEntry]) -> list[LogEntry]:
    """
    Collapse repeated errors into single entries with counts.
    
    Similar to deduplicate_logs but specifically for errors,
    and groups by error pattern rather than exact match.
    
    Args:
        logs: List of log entries
        
    Returns:
        List with repeated errors collapsed
    """
    if not logs:
        return []
    
    def _extract_error_pattern(message: str) -> str:
        """Extract a normalized error pattern from a log message."""
        pattern = re.sub(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "<UUID>",
            message,
            flags=re.IGNORECASE,
        )
        pattern = re.sub(r"\d+", "<NUM>", pattern)
        pattern = re.sub(r"0x[0-9a-f]+", "<HEX>", pattern, flags=re.IGNORECASE)
        return pattern
    
    # Group errors by pattern
    pattern_groups: dict[str, list[LogEntry]] = {}
    non_errors = []
    
    for entry in logs:
        if entry.level in [LogLevel.ERROR, LogLevel.FATAL]:
            pattern = _extract_error_pattern(entry.message)
            pattern_groups.setdefault(pattern, []).append(entry)
        else:
            non_errors.append(entry)
    
    # Collapse each pattern group
    collapsed = []
    
    for pattern, entries in pattern_groups.items():
        if len(entries) == 1:
            collapsed.append(entries[0])
        else:
            # Sort by timestamp, keep first
            sorted_entries = sorted(entries, key=lambda e: e.timestamp or datetime.min)
            first_entry = sorted_entries[0]
            
            count = len(entries)
            first_entry.message = f"{first_entry.message} [occurred {count} times]"
            
            collapsed.append(first_entry)
    
    return non_errors + collapsed

