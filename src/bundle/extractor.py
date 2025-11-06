"""Bundle extraction module for troubleshoot.sh support bundles."""
import json
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.models.bundle import ExtractedBundle


def extract_bundle(bundle_path: Path, output_dir: Path) -> ExtractedBundle:
    """
    Extract a troubleshoot.sh support bundle (tgz) to a directory.
    
    Args:
        bundle_path: Path to the .tgz bundle file
        output_dir: Directory to extract to
        
    Returns:
        ExtractedBundle with metadata
        
    Raises:
        FileNotFoundError: If bundle_path doesn't exist
        tarfile.TarError: If bundle is corrupted or invalid
    """
    if not bundle_path.exists():
        raise FileNotFoundError(f"Bundle not found: {bundle_path}")
    
    if not bundle_path.suffixes == [".tgz"] and not bundle_path.suffixes == [".tar", ".gz"]:
        raise ValueError(f"Expected .tgz file, got: {bundle_path}")
    
    output_dir.mkdir(parents=True, exist_ok=True)
    extract_path = output_dir / bundle_path.stem.replace(".tar", "")
    
    if extract_path.exists():
        import shutil
        shutil.rmtree(extract_path)
    
    extract_path.mkdir(parents=True, exist_ok=True)
    
    try:
        with tarfile.open(bundle_path, "r:gz") as tar:
            tar.extractall(path=extract_path)
    except tarfile.TarError as e:
        raise tarfile.TarError(f"Failed to extract bundle: {e}") from e
    
    cluster_name, bundle_timestamp = _extract_metadata(extract_path)
    
    return ExtractedBundle(
        bundle_path=bundle_path,
        extract_path=extract_path,
        extracted_at=datetime.now(),
        cluster_name=cluster_name,
        bundle_timestamp=bundle_timestamp,
    )


def _extract_metadata(extract_path: Path) -> tuple[Optional[str], Optional[datetime]]:
    """
    Extract metadata from the bundle directory.
    
    Troubleshoot.sh bundles typically contain:
    - cluster-info/ directory with cluster metadata
    - cluster-resources/ directory with resource definitions
    - Various timestamped directories
    
    Args:
        extract_path: Path to extracted bundle directory
        
    Returns:
        Tuple of (cluster_name, bundle_timestamp)
    """
    cluster_name = None
    bundle_timestamp = None
    
    # Look for cluster-info directory
    cluster_info_path = extract_path / "cluster-info"
    if cluster_info_path.exists():
        # Try to find cluster name in various files
        for info_file in cluster_info_path.rglob("*.json"):
            try:
                with open(info_file, "r") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        cluster_name = data.get("clusterName") or data.get("name")
                        if cluster_name:
                            break
            except (json.JSONDecodeError, KeyError):
                continue
    
    # Try to extract timestamp from directory structure
    # Troubleshoot.sh often creates timestamped directories
    for item in extract_path.iterdir():
        if item.is_dir():
            # Try to parse timestamp from directory name
            try:
                # Common formats: YYYYMMDD-HHMMSS or timestamp
                if len(item.name) == 15 and item.name.replace("-", "").isdigit():
                    # Format: YYYYMMDD-HHMMSS
                    timestamp_str = item.name.replace("-", "")
                    bundle_timestamp = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")
                    break
            except ValueError:
                continue
    
    return cluster_name, bundle_timestamp

