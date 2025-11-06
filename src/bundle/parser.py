"""Parser module for extracting structured data from troubleshoot.sh bundles."""
import json
import re
from pathlib import Path
from typing import Optional

from src.models.bundle import ConfigMap, PodLog, PodStatus, ContainerStatus, PodCondition


def parse_pod_logs(log_dir: Path) -> list[PodLog]:
    """
    Parse pod logs from the bundle directory structure.
    
    Troubleshoot.sh bundles typically organize logs as:
    - cluster-resources/pods/{namespace}/{pod-name}/logs/{container-name}.log
    - Or similar variations
    
    Args:
        log_dir: Root directory of extracted bundle
        
    Returns:
        List of PodLog objects
    """
    pod_logs = []
    
    # Common log locations in troubleshoot.sh bundles
    log_paths = [
        log_dir / "cluster-resources" / "pods",
        log_dir / "pods",
        log_dir / "logs",
    ]
    
    for base_path in log_paths:
        if not base_path.exists():
            continue
        
        # Look for namespace directories
        for namespace_dir in base_path.iterdir():
            if not namespace_dir.is_dir():
                continue
            
            namespace = namespace_dir.name
            
            # Look for pod directories
            for pod_dir in namespace_dir.iterdir():
                if not pod_dir.is_dir():
                    continue
                
                pod_name = pod_dir.name
                logs_dir = pod_dir / "logs"
                
                if not logs_dir.exists():
                    continue
                
                # Parse logs for each container
                for log_file in logs_dir.glob("*.log"):
                    container_name = log_file.stem
                    
                    pod_log = PodLog(
                        pod_name=pod_name,
                        namespace=namespace,
                        container_name=container_name,
                        log_path=log_file,
                        log_entries=[],
                    )
                    pod_logs.append(pod_log)
    
    return pod_logs


def parse_pod_statuses(status_dir: Path) -> list[PodStatus]:
    """
    Parse pod status information from the bundle.
    
    Pod statuses are typically in:
    - cluster-resources/pods/{namespace}/{pod-name}/pod.yaml
    - Or in cluster-resources/pods.json
    
    Args:
        status_dir: Root directory of extracted bundle
        
    Returns:
        List of PodStatus objects
    """
    pod_statuses = []
    
    # Common status locations
    status_paths = [
        status_dir / "cluster-resources" / "pods",
        status_dir / "cluster-resources",
        status_dir / "pods",
    ]
    
    for base_path in status_paths:
        if not base_path.exists():
            continue
        
        # Try to find pods.json first
        pods_json = base_path / "pods.json"
        if pods_json.exists():
            pod_statuses.extend(_parse_pods_json(pods_json))
            continue
        
        # Otherwise look for individual pod YAML/JSON files
        for namespace_dir in base_path.iterdir():
            if not namespace_dir.is_dir():
                continue
            
            namespace = namespace_dir.name
            
            for pod_dir in namespace_dir.iterdir():
                if not pod_dir.is_dir():
                    continue
                
                pod_name = pod_dir.name
                
                # Look for pod.yaml or pod.json
                for status_file in [pod_dir / "pod.yaml", pod_dir / "pod.json"]:
                    if status_file.exists():
                        status = _parse_pod_status_file(status_file, namespace, pod_name)
                        if status:
                            pod_statuses.append(status)
                            break
    
    return pod_statuses


def _parse_pods_json(pods_json: Path) -> list[PodStatus]:
    """Parse a pods.json file containing multiple pod statuses."""
    pod_statuses = []
    
    try:
        with open(pods_json, "r") as f:
            data = json.load(f)
        
        if isinstance(data, dict) and "items" in data:
            pods = data["items"]
        elif isinstance(data, list):
            pods = data
        else:
            return pod_statuses
        
        for pod_data in pods:
            status = _parse_pod_dict(pod_data)
            if status:
                pod_statuses.append(status)
    
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    
    return pod_statuses


def _parse_pod_status_file(status_file: Path, namespace: str, pod_name: str) -> Optional[PodStatus]:
    """Parse a single pod status file (YAML or JSON)."""
    try:
        if status_file.suffix == ".json":
            with open(status_file, "r") as f:
                data = json.load(f)
        else:
            import yaml
            with open(status_file, "r") as f:
                data = yaml.safe_load(f)
        
        return _parse_pod_dict(data, namespace, pod_name, status_file)
    
    except Exception:
        return None


def _parse_pod_dict(
    pod_data: dict,
    namespace: Optional[str] = None,
    pod_name: Optional[str] = None,
    status_path: Optional[Path] = None,
) -> Optional[PodStatus]:
    """Parse pod data from a dictionary (from JSON/YAML)."""
    if not isinstance(pod_data, dict):
        return None
    
    metadata = pod_data.get("metadata", {})
    status_data = pod_data.get("status", {})
    spec = pod_data.get("spec", {})
    
    parsed_namespace = namespace or metadata.get("namespace", "default")
    parsed_pod_name = pod_name or metadata.get("name", "unknown")
    
    phase = status_data.get("phase", "Unknown")
    
    # Determine ready status
    conditions = status_data.get("conditions", [])
    ready = False
    for condition in conditions:
        if condition.get("type") == "Ready":
            ready = condition.get("status") == "True"
            break
    
    # Parse container statuses
    container_statuses = []
    for container_status_data in status_data.get("containerStatuses", []):
        container_name = container_status_data.get("name", "unknown")
        container_ready = container_status_data.get("ready", False)
        restart_count = container_status_data.get("restartCount", 0)
        
        state_data = container_status_data.get("state", {})
        state = "unknown"
        if "running" in state_data:
            state = "running"
        elif "waiting" in state_data:
            state = "waiting"
        elif "terminated" in state_data:
            state = "terminated"
        
        last_state_data = container_status_data.get("lastState", {})
        last_state = None
        if last_state_data:
            if "running" in last_state_data:
                last_state = "running"
            elif "terminated" in last_state_data:
                last_state = "terminated"
        
        container_statuses.append(
            ContainerStatus(
                name=container_name,
                ready=container_ready,
                restart_count=restart_count,
                state=state,
                last_state=last_state,
            )
        )
    
    # Parse pod conditions
    pod_conditions = []
    for condition_data in conditions:
        pod_conditions.append(
            PodCondition(
                type=condition_data.get("type", "Unknown"),
                status=condition_data.get("status", "Unknown"),
                reason=condition_data.get("reason"),
                message=condition_data.get("message"),
            )
        )
    
    # Calculate total restart count
    total_restart_count = sum(cs.restart_count for cs in container_statuses)
    
    return PodStatus(
        pod_name=parsed_pod_name,
        namespace=parsed_namespace,
        phase=phase,
        ready=ready,
        restart_count=total_restart_count,
        container_statuses=container_statuses,
        conditions=pod_conditions,
        status_path=status_path,
    )


def parse_configmaps(config_dir: Path) -> list[ConfigMap]:
    """
    Parse ConfigMap resources from the bundle.
    
    ConfigMaps are typically in:
    - cluster-resources/configmaps/{namespace}/{name}.yaml
    - Or cluster-resources/configmaps.json
    
    Args:
        config_dir: Root directory of extracted bundle
        
    Returns:
        List of ConfigMap objects
    """
    configmaps = []
    
    configmap_paths = [
        config_dir / "cluster-resources" / "configmaps",
        config_dir / "cluster-resources",
        config_dir / "configmaps",
    ]
    
    for base_path in configmap_paths:
        if not base_path.exists():
            continue
        
        # Try configmaps.json first
        configmaps_json = base_path / "configmaps.json"
        if configmaps_json.exists():
            configmaps.extend(_parse_configmaps_json(configmaps_json))
            continue
        
        # Otherwise look for individual files
        for namespace_dir in base_path.iterdir():
            if not namespace_dir.is_dir():
                continue
            
            namespace = namespace_dir.name
            
            for config_file in namespace_dir.glob("*.yaml"):
                configmap = _parse_configmap_file(config_file, namespace)
                if configmap:
                    configmaps.append(configmap)
    
    return configmaps


def _parse_configmaps_json(configmaps_json: Path) -> list[ConfigMap]:
    """Parse a configmaps.json file."""
    configmaps = []
    
    try:
        with open(configmaps_json, "r") as f:
            data = json.load(f)
        
        if isinstance(data, dict) and "items" in data:
            items = data["items"]
        elif isinstance(data, list):
            items = data
        else:
            return configmaps
        
        for item in items:
            metadata = item.get("metadata", {})
            name = metadata.get("name", "unknown")
            namespace = metadata.get("namespace", "default")
            data_dict = item.get("data", {})
            
            configmaps.append(
                ConfigMap(
                    name=name,
                    namespace=namespace,
                    data=data_dict,
                    config_path=None,
                )
            )
    
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    
    return configmaps


def _parse_configmap_file(config_file: Path, namespace: str) -> Optional[ConfigMap]:
    """Parse a single ConfigMap file."""
    try:
        import yaml
        with open(config_file, "r") as f:
            data = yaml.safe_load(f)
        
        metadata = data.get("metadata", {})
        name = metadata.get("name", config_file.stem)
        data_dict = data.get("data", {})
        
        return ConfigMap(
            name=name,
            namespace=namespace,
            data=data_dict,
            config_path=config_file,
        )
    
    except Exception:
        return None

