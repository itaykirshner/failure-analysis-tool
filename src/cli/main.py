"""CLI entry point for log bundle analysis."""
import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from src.analysis import (
    analyze_logs,
    assess_pod_health,
    collapse_repeated_errors,
    deduplicate_logs,
    detect_connectivity_issues,
    filter_irrelevant_warnings,
    identify_root_causes,
)
from src.bundle import extract_bundle, parse_configmaps, parse_pod_logs, parse_pod_statuses
from src.models.analysis import AnalysisResult, Issue, LogEntry


def main() -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze Kubernetes log bundles from troubleshoot.sh",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    parser.add_argument(
        "bundle_path",
        type=Path,
        help="Path to the .tgz log bundle file",
    )
    
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output directory for extracted bundle and results (default: ./output)",
        default=Path("./output"),
    )
    
    parser.add_argument(
        "-j",
        "--json",
        type=Path,
        help="Output JSON report to file (default: analysis_result.json)",
        default=Path("analysis_result.json"),
    )
    
    parser.add_argument(
        "--no-extract",
        action="store_true",
        help="Skip extraction if bundle already extracted",
    )
    
    parser.add_argument(
        "--min-warning-frequency",
        type=int,
        default=3,
        help="Minimum frequency for warnings to be kept (default: 3)",
    )
    
    args = parser.parse_args()
    
    if not args.bundle_path.exists():
        print(f"Error: Bundle file not found: {args.bundle_path}", file=sys.stderr)
        return 1
    
    try:
        # Extract bundle
        if args.no_extract:
            extract_path = args.output / args.bundle_path.stem.replace(".tar", "")
            if not extract_path.exists():
                print(f"Error: Extract path does not exist: {extract_path}", file=sys.stderr)
                return 1
            bundle = None
        else:
            print(f"Extracting bundle: {args.bundle_path}")
            bundle = extract_bundle(args.bundle_path, args.output)
            extract_path = bundle.extract_path
            print(f"Extracted to: {extract_path}")
        
        # Parse bundle contents
        print("Parsing pod logs...")
        pod_logs = parse_pod_logs(extract_path)
        print(f"Found {len(pod_logs)} pod log files")
        
        print("Parsing pod statuses...")
        pod_statuses = parse_pod_statuses(extract_path)
        print(f"Found {len(pod_statuses)} pod statuses")
        
        print("Parsing configmaps...")
        configmaps = parse_configmaps(extract_path)
        print(f"Found {len(configmaps)} configmaps")
        
        # Analyze logs
        print("Analyzing logs...")
        all_log_entries: list[LogEntry] = []
        
        for pod_log in pod_logs:
            from src.analysis.log_analyzer import _parse_log_file
            entries = _parse_log_file(pod_log)
            all_log_entries.extend(entries)
            # Update pod_log with parsed entries
            pod_log.log_entries = entries
        
        print(f"Parsed {len(all_log_entries)} log entries")
        
        # Apply noise reduction
        print("Reducing noise...")
        deduplicated = deduplicate_logs(all_log_entries)
        print(f"Deduplicated: {len(deduplicated)} entries (from {len(all_log_entries)})")
        
        filtered = filter_irrelevant_warnings(deduplicated, args.min_warning_frequency)
        print(f"Filtered warnings: {len(filtered)} entries (from {len(deduplicated)})")
        
        collapsed = collapse_repeated_errors(filtered)
        print(f"Collapsed repeated errors: {len(collapsed)} entries (from {len(filtered)})")
        
        # Analyze logs
        log_analysis = analyze_logs(pod_logs)
        
        # Assess pod health
        print("Assessing pod health...")
        pod_health_list = []
        
        # Create a map of pod statuses by name
        pod_status_map = {
            f"{ps.namespace}/{ps.pod_name}": ps
            for ps in pod_statuses
        }
        
        # Group logs by pod
        logs_by_pod: dict[str, list[LogEntry]] = {}
        for entry in collapsed:
            key = f"{entry.namespace}/{entry.pod_name}"
            logs_by_pod.setdefault(key, []).append(entry)
        
        for key, pod_status in pod_status_map.items():
            pod_logs_list = logs_by_pod.get(key, [])
            health = assess_pod_health(pod_status, pod_logs_list)
            pod_health_list.append(health)
        
        # For pods with logs but no status, create health from logs only
        for key, pod_logs_list in logs_by_pod.items():
            if key not in pod_status_map:
                # Extract namespace and pod name
                namespace, pod_name = key.split("/", 1)
                
                # Create minimal pod status
                from src.models.bundle import PodStatus
                minimal_status = PodStatus(
                    pod_name=pod_name,
                    namespace=namespace,
                    phase="Unknown",
                    ready=False,
                    restart_count=0,
                )
                
                health = assess_pod_health(minimal_status, pod_logs_list)
                pod_health_list.append(health)
        
        print(f"Assessed {len(pod_health_list)} pods")
        
        # Detect connectivity issues
        print("Detecting connectivity issues...")
        connectivity_issues = detect_connectivity_issues(collapsed, pod_health_list)
        print(f"Found {len(connectivity_issues)} connectivity issues")
        
        # Create issues from pod health and connectivity
        issues = _create_issues_from_health(pod_health_list, connectivity_issues, collapsed)
        
        # Root cause analysis
        print("Identifying root causes...")
        root_causes = identify_root_causes(issues, pod_health_list, connectivity_issues)
        print(f"Identified {len(root_causes)} root causes")
        
        # Create analysis result
        summary = {
            "total_pods": len(pod_health_list),
            "healthy_pods": sum(1 for ph in pod_health_list if ph.is_healthy),
            "unhealthy_pods": sum(1 for ph in pod_health_list if not ph.is_healthy),
            "total_issues": len(issues),
            "connectivity_issues": len(connectivity_issues),
            "root_causes": len(root_causes),
            "total_log_entries": len(all_log_entries),
            "filtered_log_entries": len(collapsed),
        }
        
        analysis_result = AnalysisResult(
            bundle_path=args.bundle_path,
            pod_health=pod_health_list,
            issues=issues,
            connectivity_issues=connectivity_issues,
            root_causes=root_causes,
            summary=summary,
        )
        
        # Output JSON
        print(f"Writing JSON report to: {args.json}")
        with open(args.json, "w") as f:
            json.dump(analysis_result.model_dump(mode="json"), f, indent=2, default=str)
        
        # Print summary
        _print_summary(analysis_result)
        
        return 0
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


def _create_issues_from_health(
    pod_health_list: list,
    connectivity_issues: list,
    logs: list[LogEntry],
) -> list[Issue]:
    """Create Issue objects from pod health and connectivity issues."""
    import uuid
    from datetime import datetime
    
    issues = []
    
    # Create issues from unhealthy pods
    for ph in pod_health_list:
        if ph.is_healthy:
            continue
        
        issue_type = "error"
        severity = "high"
        
        if ph.state.value == "crashed":
            issue_type = "crash"
            severity = "critical"
        elif ph.state.value == "degraded":
            severity = "medium"
        
        # Get first and last error timestamps
        error_logs = [log for log in logs if log.pod_name == ph.pod_name and log.level.value in ["ERROR", "FATAL"]]
        
        first_seen = datetime.now()
        last_seen = datetime.now()
        occurrence_count = len(error_logs)
        
        if error_logs:
            timestamps = [log.timestamp for log in error_logs if log.timestamp]
            if timestamps:
                first_seen = min(timestamps)
                last_seen = max(timestamps)
        
        description = f"Pod {ph.pod_name} is {ph.state.value}"
        if ph.crash_reason:
            description += f": {ph.crash_reason}"
        elif ph.error_count > 0:
            description += f" with {ph.error_count} error(s)"
        
        issue = Issue(
            issue_id=str(uuid.uuid4()),
            issue_type=issue_type,
            severity=severity,
            pod_name=ph.pod_name,
            namespace=ph.namespace,
            description=description,
            first_seen=first_seen,
            last_seen=last_seen,
            occurrence_count=occurrence_count,
            related_logs=error_logs[:10],  # Limit to first 10
        )
        
        issues.append(issue)
    
    # Create issues from connectivity problems
    for conn_issue in connectivity_issues:
        issue = Issue(
            issue_id=str(uuid.uuid4()),
            issue_type="connectivity",
            severity="high",
            pod_name=conn_issue.source_pod,
            namespace=conn_issue.source_namespace,
            description=f"Connectivity issue: {conn_issue.error_type} to {conn_issue.target_service}",
            first_seen=conn_issue.first_occurrence,
            last_seen=conn_issue.last_occurrence,
            occurrence_count=conn_issue.occurrence_count,
            related_logs=conn_issue.related_logs[:10],
        )
        issues.append(issue)
    
    return issues


def _print_summary(analysis_result: AnalysisResult) -> None:
    """Print a human-readable summary of the analysis."""
    print("\n" + "=" * 80)
    print("ANALYSIS SUMMARY")
    print("=" * 80)
    
    summary = analysis_result.summary
    print(f"\nTotal Pods: {summary['total_pods']}")
    print(f"  Healthy: {summary['healthy_pods']}")
    print(f"  Unhealthy: {summary['unhealthy_pods']}")
    
    print(f"\nIssues: {summary['total_issues']}")
    print(f"  Connectivity Issues: {summary['connectivity_issues']}")
    print(f"  Root Causes: {summary['root_causes']}")
    
    print(f"\nLogs: {summary['total_log_entries']} entries")
    print(f"  After filtering: {summary['filtered_log_entries']} entries")
    
    if analysis_result.root_causes:
        print("\n" + "-" * 80)
        print("ROOT CAUSES")
        print("-" * 80)
        for rc in analysis_result.root_causes:
            print(f"\n{rc.description}")
            print(f"  Confidence: {rc.confidence:.2%}")
            print(f"  Affected Pods: {', '.join(rc.affected_pods)}")
            if rc.symptom_issues:
                print(f"  Symptoms: {len(rc.symptom_issues)} related issue(s)")
    
    if analysis_result.pod_health:
        unhealthy = [ph for ph in analysis_result.pod_health if not ph.is_healthy]
        if unhealthy:
            print("\n" + "-" * 80)
            print("UNHEALTHY PODS")
            print("-" * 80)
            for ph in unhealthy[:10]:  # Show first 10
                print(f"\n{ph.namespace}/{ph.pod_name}: {ph.state.value}")
                if ph.crash_reason:
                    print(f"  Crash: {ph.crash_reason[:100]}")
                if ph.error_count > 0:
                    print(f"  Errors: {ph.error_count}")
                if ph.restart_count > 0:
                    print(f"  Restarts: {ph.restart_count}")


if __name__ == "__main__":
    sys.exit(main())

