"""LLM-based log analyzer for advanced insights."""
from typing import Optional

from src.llm.model_loader import LLMModel
from src.models.analysis import AnalysisResult, LogEntry


class LLMInsights:
    """Structured insights from LLM analysis."""
    
    def __init__(
        self,
        summary: str,
        key_errors: list[str],
        recommendations: list[str],
        root_cause_hypothesis: Optional[str] = None,
    ):
        """Initialize LLM insights."""
        self.summary = summary
        self.key_errors = key_errors
        self.recommendations = recommendations
        self.root_cause_hypothesis = root_cause_hypothesis


def analyze_with_llm(
    logs: list[LogEntry],
    context: AnalysisResult,
    model: LLMModel,
    max_logs: int = 100,
) -> LLMInsights:
    """
    Analyze logs using LLM to extract insights.
    
    Args:
        logs: List of log entries to analyze
        context: AnalysisResult for context
        model: Loaded LLMModel
        max_logs: Maximum number of logs to include in prompt
        
    Returns:
        LLMInsights with extracted insights
    """
    # Select most relevant logs (errors first, then recent)
    error_logs = [log for log in logs if log.level.value in ["ERROR", "FATAL"]]
    other_logs = [log for log in logs if log.level.value not in ["ERROR", "FATAL"]]
    
    selected_logs = error_logs[:max_logs // 2] + other_logs[:max_logs // 2]
    
    # Build prompt
    prompt = _build_analysis_prompt(selected_logs, context)
    
    # Generate analysis
    response = model.generate(
        prompt=prompt,
        max_tokens=1024,
        temperature=0.3,  # Lower temperature for more focused analysis
        stop=["<|end|>", "\n\n\n"],
    )
    
    # Parse response
    insights = _parse_llm_response(response)
    
    return insights


def _build_analysis_prompt(logs: list[LogEntry], context: AnalysisResult) -> str:
    """Build a prompt for LLM log analysis."""
    prompt = """<extra_id_0>System
You are an expert Kubernetes log analyst. Analyze the following log entries and provide insights about system issues.

Context:
- Total pods: {total_pods}
- Unhealthy pods: {unhealthy_pods}
- Root causes identified: {root_causes}

Log Entries:
{log_entries}

Provide a structured analysis with:
1. Summary of key issues
2. List of most critical errors (top 5)
3. Recommendations for resolution
4. Root cause hypothesis if not already identified

Format your response as:
SUMMARY: [brief summary]
KEY_ERRORS:
- [error 1]
- [error 2]
...
RECOMMENDATIONS:
- [recommendation 1]
- [recommendation 2]
...
ROOT_CAUSE: [hypothesis if applicable]
""".format(
        total_pods=context.summary.get("total_pods", 0),
        unhealthy_pods=context.summary.get("unhealthy_pods", 0),
        root_causes=context.summary.get("root_causes", 0),
        log_entries=_format_logs_for_prompt(logs),
    )
    
    return prompt


def _format_logs_for_prompt(logs: list[LogEntry], max_length: int = 50) -> str:
    """Format logs for inclusion in prompt."""
    formatted = []
    
    for i, log in enumerate(logs[:max_length], 1):
        timestamp_str = log.timestamp.strftime("%Y-%m-%d %H:%M:%S") if log.timestamp else "N/A"
        formatted.append(
            f"{i}. [{timestamp_str}] [{log.level.value}] {log.pod_name}: {log.message[:200]}"
        )
    
    return "\n".join(formatted)


def _parse_llm_response(response: str) -> LLMInsights:
    """Parse LLM response into structured insights."""
    summary = ""
    key_errors = []
    recommendations = []
    root_cause_hypothesis = None
    
    lines = response.split("\n")
    current_section = None
    
    for line in lines:
        line = line.strip()
        
        if not line:
            continue
        
        if line.startswith("SUMMARY:"):
            current_section = "summary"
            summary = line.replace("SUMMARY:", "").strip()
            continue
        elif line.startswith("KEY_ERRORS:"):
            current_section = "errors"
            continue
        elif line.startswith("RECOMMENDATIONS:"):
            current_section = "recommendations"
            continue
        elif line.startswith("ROOT_CAUSE:"):
            current_section = "root_cause"
            root_cause_hypothesis = line.replace("ROOT_CAUSE:", "").strip()
            continue
        
        if current_section == "summary" and not summary:
            summary = line
        elif current_section == "errors" and line.startswith("-"):
            error = line[1:].strip()
            if error:
                key_errors.append(error)
        elif current_section == "recommendations" and line.startswith("-"):
            rec = line[1:].strip()
            if rec:
                recommendations.append(rec)
        elif current_section == "root_cause" and not root_cause_hypothesis:
            root_cause_hypothesis = line
    
    # Fallback: if parsing failed, use raw response as summary
    if not summary and not key_errors:
        summary = response[:500]
    
    return LLMInsights(
        summary=summary or "No summary generated",
        key_errors=key_errors[:5],
        recommendations=recommendations[:5],
        root_cause_hypothesis=root_cause_hypothesis,
    )

