"""Kubernetes client module for in-cluster configuration."""
from typing import Optional

try:
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException
except ImportError:
    client = None
    config = None
    ApiException = None


def get_kubernetes_client() -> Optional["client.ApiClient"]:
    """
    Get Kubernetes API client using in-cluster configuration.
    
    Tries in-cluster config first, falls back to kubeconfig if not in cluster.
    
    Returns:
        ApiClient instance or None if kubernetes library not available
    """
    if client is None or config is None:
        return None
    
    try:
        # Try in-cluster config first (when running in Kubernetes)
        config.load_incluster_config()
        return client.ApiClient()
    except config.ConfigException:
        try:
            # Fall back to kubeconfig (for local development)
            config.load_kube_config()
            return client.ApiClient()
        except config.ConfigException:
            return None


def get_namespace() -> Optional[str]:
    """
    Get current namespace from in-cluster configuration.
    
    Returns:
        Namespace string or None
    """
    try:
        with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def check_kubernetes_connection() -> bool:
    """
    Check if we can connect to Kubernetes API.
    
    Returns:
        True if connection successful, False otherwise
    """
    api_client = get_kubernetes_client()
    if api_client is None:
        return False
    
    try:
        v1 = client.CoreV1Api(api_client)
        v1.list_namespaces()
        return True
    except Exception:
        return False

