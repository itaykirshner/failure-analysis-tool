"""Log analysis module for extracting insights from pod logs."""
import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.models.analysis import LogAnalysis, LogEntry, LogLevel
from src.models.bundle import PodLog


def analyze_logs(pod_logs: list[PodLog]) -> LogAnalysis:
    """
    Analyze pod logs to extract errors, warnings, and patterns.
    
    Args:
        pod_logs: List of PodLog objects to analyze
        
    Returns:
        LogAnalysis with extracted insights
    """
    all_entries = []
    error_count = 0
    warning_count = 0
    error_patterns: dict[str, int] = {}
    
    for pod_log in pod_logs:
        entries = _parse_log_file(pod_log)
        all_entries.extend(entries)
        
        for entry in entries:
            if entry.level == LogLevel.ERROR or entry.level == LogLevel.FATAL:
                error_count += 1
                pattern = _extract_error_pattern(entry.message)
                error_patterns[pattern] = error_patterns.get(pattern, 0) + 1
            elif entry.level == LogLevel.WARN:
                warning_count += 1
    
    # Get unique errors (by message hash)
    unique_errors = _get_unique_errors(all_entries)
    
    return LogAnalysis(
        total_entries=len(all_entries),
        error_count=error_count,
        warning_count=warning_count,
        unique_errors=unique_errors,
        error_patterns=error_patterns,
        analyzed_at=datetime.now(),
    )


def _parse_log_file(pod_log: PodLog) -> list[LogEntry]:
    """
    Parse a log file and extract structured log entries.
    
    Uses flexible parsing that works with diverse log formats:
    - JSON logs (structured)
    - Plain text logs with various timestamp/level formats
    - Multiline stack traces
    - Unstructured logs (content-based level inference)
    """
    entries = []
    
    if not pod_log.log_path.exists():
        return entries
    
    try:
        with open(pod_log.log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception:
        return entries
    
    current_entry_lines = []
    current_timestamp = None
    current_level = LogLevel.UNKNOWN
    
    for line_num, line in enumerate(lines, start=1):
        line = line.rstrip("\n\r")
        
        if not line or not line.strip():
            # Empty line - might be part of multiline entry
            if current_entry_lines:
                current_entry_lines.append("")
            continue
        
        # Try to parse as JSON first
        json_entry = _try_parse_json_log(line)
        if json_entry:
            # Save previous multiline entry if exists
            if current_entry_lines:
                entry = _create_log_entry_from_lines(
                    current_entry_lines,
                    pod_log,
                    current_timestamp,
                    current_level,
                )
                if entry:
                    entries.append(entry)
                current_entry_lines = []
            
            entry = _create_log_entry_from_json(
                json_entry,
                pod_log,
                line_num,
                line,
            )
            if entry:
                entries.append(entry)
            continue
        
        # Try to parse as structured text log
        parsed = _parse_text_log_line(line)
        if parsed:
            # Save previous multiline entry if exists
            if current_entry_lines:
                entry = _create_log_entry_from_lines(
                    current_entry_lines,
                    pod_log,
                    current_timestamp,
                    current_level,
                )
                if entry:
                    entries.append(entry)
                current_entry_lines = []
            
            current_timestamp = parsed["timestamp"]
            current_level = parsed["level"]
            current_entry_lines = [parsed["message"]]
        else:
            # Line doesn't match known patterns - could be:
            # 1. Continuation of previous multiline entry
            # 2. Unstructured log line (no timestamp/level)
            
            if current_entry_lines:
                # Likely continuation of multiline entry
                current_entry_lines.append(line)
            else:
                # New unstructured entry - infer level from content
                level = _infer_log_level_from_content(line)
                entry = _create_log_entry_from_lines(
                    [line],
                    pod_log,
                    None,
                    level,
                )
                if entry:
                    entries.append(entry)
    
    # Handle remaining multiline entry
    if current_entry_lines:
        entry = _create_log_entry_from_lines(
            current_entry_lines,
            pod_log,
            current_timestamp,
            current_level,
        )
        if entry:
            entries.append(entry)
    
    return entries


def _try_parse_json_log(line: str) -> Optional[dict]:
    """Try to parse a line as JSON log entry."""
    try:
        import json
        data = json.loads(line)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _parse_text_log_line(line: str) -> Optional[dict]:
    """
    Parse a text log line using flexible heuristics.
    
    Tries multiple strategies to extract timestamp, level, and message
    without assuming a specific format. Works with diverse log formats.
    """
    if not line or not line.strip():
        return None
    
    # Strategy 1: Try to find timestamp (various formats, various positions)
    timestamp = _extract_timestamp_from_line(line)
    remaining = line
    
    if timestamp:
        # Remove timestamp from line - try to find where it is
        timestamp_patterns = [
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?",
            r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?",
            r"\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}",
            r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}",
            r"^\d{10}(?:\.\d+)?",  # Unix timestamp
        ]
        
        for pattern in timestamp_patterns:
            match = re.search(pattern, line)
            if match:
                # Remove timestamp and surrounding whitespace
                before = line[:match.start()].rstrip()
                after = line[match.end():].lstrip()
                remaining = (before + " " + after).strip()
                break
    
    # Strategy 2: Extract log level from anywhere in the line
    level, level_pos = _extract_log_level_from_line(remaining)
    
    # Strategy 3: Extract message (everything except timestamp and level)
    if level_pos is not None and level_pos >= 0:
        # Remove level indicator
        message_parts = remaining.split()
        if 0 <= level_pos < len(message_parts):
            # Remove level token
            message_parts.pop(level_pos)
        message = " ".join(message_parts)
    else:
        message = remaining
    
    # If we found a level, use it; otherwise infer from content
    if level == LogLevel.UNKNOWN:
        level = _infer_log_level_from_content(message)
    
    # Clean up message
    message = message.strip()
    if not message:
        return None
    
    return {"timestamp": timestamp, "level": level, "message": message}


def _extract_timestamp_from_line(line: str) -> Optional[datetime]:
    """
    Extract timestamp from a log line using multiple heuristics.
    
    Looks for timestamps in various positions and formats.
    """
    # Common timestamp patterns
    timestamp_patterns = [
        # ISO 8601 / RFC3339
        (r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?", [
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
        ]),
        # Standard datetime
        (r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?", [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
        ]),
        # US format
        (r"\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}", [
            "%m/%d/%Y %H:%M:%S",
        ]),
        # Unix timestamp (if it's a long number at start)
        (r"^\d{10}(?:\.\d+)?", [
            None,  # Special handling
        ]),
    ]
    
    for pattern, formats in timestamp_patterns:
        match = re.search(pattern, line)
        if match:
            timestamp_str = match.group(0)
            
            # Special handling for Unix timestamp
            if formats[0] is None:
                try:
                    ts = float(timestamp_str)
                    return datetime.fromtimestamp(ts)
                except (ValueError, OSError):
                    continue
            
            for fmt in formats:
                try:
                    return datetime.strptime(timestamp_str, fmt)
                except ValueError:
                    continue
    
    return None


def _extract_log_level_from_line(line: str) -> tuple[LogLevel, Optional[int]]:
    """
    Extract log level from a line using heuristics.
    
    Looks for level indicators in various positions and formats.
    Returns (level, position_in_tokens) or (UNKNOWN, None).
    """
    line_upper = line.upper()
    tokens = line.split()
    
    # Check for explicit level markers at start
    level_keywords = {
        "ERROR": LogLevel.ERROR,
        "ERR": LogLevel.ERROR,
        "WARN": LogLevel.WARN,
        "WARNING": LogLevel.WARN,
        "INFO": LogLevel.INFO,
        "INFORMATION": LogLevel.INFO,
        "DEBUG": LogLevel.DEBUG,
        "DBG": LogLevel.DEBUG,
        "FATAL": LogLevel.FATAL,
        "CRITICAL": LogLevel.FATAL,
        "CRIT": LogLevel.FATAL,
        "TRACE": LogLevel.DEBUG,
    }
    
    # Check first few tokens for level
    for i, token in enumerate(tokens[:5]):
        token_clean = re.sub(r"[\[\]():,]", "", token).upper()
        if token_clean in level_keywords:
            return level_keywords[token_clean], i
    
    # Check for level in brackets: [ERROR], (WARN), etc.
    bracket_match = re.search(r"[\[\(](ERROR|WARN|INFO|DEBUG|FATAL|CRITICAL)[\]\)]", line_upper)
    if bracket_match:
        level_str = bracket_match.group(1)
        if level_str in level_keywords:
            return level_keywords[level_str], None
    
    # Check for single character levels (Kubernetes style: E, W, I, D, F)
    char_levels = {
        "E": LogLevel.ERROR,
        "W": LogLevel.WARN,
        "I": LogLevel.INFO,
        "D": LogLevel.DEBUG,
        "F": LogLevel.FATAL,
    }
    
    # Look for single char after timestamp/stream
    if len(tokens) >= 3:
        char_token = tokens[2] if len(tokens[2]) == 1 else None
        if char_token and char_token.upper() in char_levels:
            return char_levels[char_token.upper()], 2
    
    return LogLevel.UNKNOWN, None


def _infer_log_level_from_content(message: str) -> LogLevel:
    """
    Infer log level from message content when explicit level is not found.
    
    Uses heuristics based on error keywords and message structure.
    """
    message_lower = message.lower()
    
    # Error indicators
    error_keywords = [
        "error", "exception", "failed", "failure", "fatal", "critical",
        "panic", "crash", "abort", "unable", "cannot", "can't",
        "timeout", "deadline exceeded", "connection refused",
        "segmentation fault", "out of memory", "oom",
    ]
    
    # Warning indicators
    warning_keywords = [
        "warn", "warning", "deprecated", "deprecation",
        "retry", "backoff", "rate limit",
    ]
    
    # Check for error keywords
    for keyword in error_keywords:
        if keyword in message_lower:
            if "fatal" in message_lower or "panic" in message_lower or "crash" in message_lower:
                return LogLevel.FATAL
            return LogLevel.ERROR
    
    # Check for warning keywords
    for keyword in warning_keywords:
        if keyword in message_lower:
            return LogLevel.WARN
    
    # Check for stack traces (usually errors)
    if "traceback" in message_lower or "stack trace" in message_lower:
        return LogLevel.ERROR
    
    # Check for HTTP error codes
    if re.search(r"\b(4\d{2}|5\d{2})\b", message):
        return LogLevel.ERROR
    
    # Default to INFO if no indicators found
    return LogLevel.INFO


def _parse_timestamp(timestamp_str: str) -> Optional[datetime]:
    """Parse various timestamp formats."""
    formats = [
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(timestamp_str, fmt)
        except ValueError:
            continue
    
    return None


def _parse_log_level(level_str: str) -> LogLevel:
    """Parse log level string to enum."""
    level_upper = level_str.upper()
    
    if level_upper in ["ERROR", "ERR"]:
        return LogLevel.ERROR
    elif level_upper in ["WARN", "WARNING"]:
        return LogLevel.WARN
    elif level_upper in ["INFO", "INFORMATION"]:
        return LogLevel.INFO
    elif level_upper in ["DEBUG", "DBG"]:
        return LogLevel.DEBUG
    elif level_upper in ["FATAL", "CRITICAL", "CRIT"]:
        return LogLevel.FATAL
    else:
        return LogLevel.UNKNOWN


def _level_char_to_enum(level_char: str) -> LogLevel:
    """Convert Kubernetes log level character to enum."""
    char_upper = level_char.upper()
    mapping = {
        "E": LogLevel.ERROR,
        "W": LogLevel.WARN,
        "I": LogLevel.INFO,
        "D": LogLevel.DEBUG,
        "F": LogLevel.FATAL,
    }
    return mapping.get(char_upper, LogLevel.UNKNOWN)


def _create_log_entry_from_json(
    json_data: dict,
    pod_log: PodLog,
    line_num: int,
    raw_line: str,
) -> Optional[LogEntry]:
    """Create LogEntry from JSON log data."""
    message = json_data.get("message") or json_data.get("msg") or str(json_data)
    level_str = json_data.get("level") or json_data.get("severity") or "UNKNOWN"
    timestamp_str = json_data.get("timestamp") or json_data.get("time") or json_data.get("@timestamp")
    
    timestamp = None
    if timestamp_str:
        timestamp = _parse_timestamp(str(timestamp_str))
    
    level = _parse_log_level(str(level_str))
    
    message_hash = hashlib.md5(message.encode()).hexdigest()
    
    return LogEntry(
        timestamp=timestamp,
        level=level,
        message=message,
        pod_name=pod_log.pod_name,
        namespace=pod_log.namespace,
        container_name=pod_log.container_name,
        raw_line=raw_line,
        line_number=line_num,
        log_file=pod_log.log_path,
        hash=message_hash,
    )


def _create_log_entry_from_lines(
    lines: list[str],
    pod_log: PodLog,
    timestamp: Optional[datetime],
    level: LogLevel,
) -> Optional[LogEntry]:
    """Create LogEntry from multiline log entry."""
    message = "\n".join(lines)
    
    if not message.strip():
        return None
    
    message_hash = hashlib.md5(message.encode()).hexdigest()
    
    return LogEntry(
        timestamp=timestamp,
        level=level,
        message=message,
        pod_name=pod_log.pod_name,
        namespace=pod_log.namespace,
        container_name=pod_log.container_name,
        raw_line=lines[0] if lines else "",
        log_file=pod_log.log_path,
        hash=message_hash,
    )


def _extract_error_pattern(message: str) -> str:
    """
    Extract a normalized error pattern from a log message.
    
    Replaces variable parts (numbers, IDs, etc.) with placeholders.
    """
    # Replace UUIDs
    pattern = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "<UUID>",
        message,
        flags=re.IGNORECASE,
    )
    
    # Replace numbers (but keep structure)
    pattern = re.sub(r"\d+", "<NUM>", pattern)
    
    # Replace common variable parts
    pattern = re.sub(r"0x[0-9a-f]+", "<HEX>", pattern, flags=re.IGNORECASE)
    
    return pattern


def _get_unique_errors(entries: list[LogEntry]) -> list[LogEntry]:
    """Get unique error entries based on message hash."""
    seen_hashes = set()
    unique_errors = []
    
    for entry in entries:
        if entry.level in [LogLevel.ERROR, LogLevel.FATAL]:
            if entry.hash and entry.hash not in seen_hashes:
                seen_hashes.add(entry.hash)
                unique_errors.append(entry)
    
    return unique_errors

