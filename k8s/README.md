# Kubernetes Deployment Guide

## Overview

This directory contains Kubernetes manifests for deploying the log analysis AIOps system.

## Components

1. **App Deployment** (`app-deployment.yaml`): Main application with FastAPI backend
2. **LLM Deployment** (`llm-deployment.yaml`): Nemotron-Mini-4B-Instruct model running on CPU

## Prerequisites

- Kubernetes cluster (EKS, GKE, or local)
- kubectl configured
- PersistentVolume provisioner (for model storage)

## AWS Instance Recommendation

For CPU-only inference of Nemotron-Mini-4B-Instruct (Q4_K_M, ~2.7GB):

**Option 1: m7i.xlarge** (4 vCPUs, 16 GiB RAM) - **Tight but workable**
- **CPU allocation:**
  - System overhead (kubelet, kube-proxy): ~0.5-1 CPU
  - LLM pod request: 2 CPUs (guaranteed)
  - LLM pod limit: 3 CPUs (can burst)
  - App pod: 0.2-1 CPU
  - **Total: ~3-4 CPUs used** (fits, but tight)
- **Memory:** 16GB is sufficient (model ~2.7GB + inference ~3GB = ~6GB)
- **Performance:** Inference will be slower with 2-3 CPUs, but functional
- **Cost:** Most cost-effective

**Option 2: m7i.2xlarge** (8 vCPUs, 32 GiB RAM) - **Recommended for production**
- **CPU allocation:**
  - System overhead: ~0.5-1 CPU
  - LLM pod request: 4 CPUs (guaranteed, better performance)
  - LLM pod limit: 6-7 CPUs (can burst)
  - App pod: 0.2-1 CPU
  - **Total: ~5-8 CPUs used** (comfortable headroom)
- **Memory:** 32GB provides plenty of headroom
- **Performance:** Much faster inference with 4-6 CPUs
- **Cost:** ~2x m7i.xlarge, but better performance and reliability

**Recommendation:**
- **Development/testing:** m7i.xlarge works (with reduced CPU requests)
- **Production:** m7i.2xlarge recommended for better performance and reliability

**Note:** If using m7i.xlarge, you can increase LLM CPU requests to 3-4 in the deployment YAML, but this requires the instance to have minimal other workloads. The current config (2 CPU request, 3 CPU limit) is conservative to ensure scheduling.

## Deployment Steps

### 1. Deploy LLM Service

```bash
kubectl apply -f k8s/llm-deployment.yaml
```

Wait for the model to download and service to be ready:
```bash
kubectl wait --for=condition=ready pod -l app=nemotron-mini-4b-gguf -n log-analysis-aiops --timeout=600s
```

### 2. Deploy Application

```bash
kubectl apply -f k8s/app-deployment.yaml
```

### 3. Verify Deployment

```bash
# Check pods
kubectl get pods -n log-analysis-aiops

# Check services
kubectl get svc -n log-analysis-aiops

# Check logs
kubectl logs -f deployment/log-analysis-aiops -n log-analysis-aiops
```

### 4. Access the Application

Port-forward to access locally:
```bash
kubectl port-forward -n log-analysis-aiops svc/log-analysis-aiops-service 8000:8000
```

Then open http://localhost:8000

Or expose via Ingress/LoadBalancer as needed.

## Configuration

### LLM Service

- Model: Nemotron-Mini-4B-Instruct-Q4_K_M.gguf (~2.7GB)
- Context size: 8K tokens
- CPU threads: 8 (matches m7i.xlarge vCPU count)
- Memory: 6-8GB (model + inference overhead)

### Application

- Minimal Kubernetes permissions (only reads pods/namespaces for health checks)
- Connects to LLM service via service DNS: `llm-service.log-analysis-aiops.svc.cluster.local`
- Health check endpoint: `/health`

## Troubleshooting

### LLM Service Issues

```bash
# Check model download
kubectl logs -n log-analysis-aiops deployment/nemotron-mini-4b-gguf-deployment -c model-downloader

# Check LLM server
kubectl logs -n log-analysis-aiops deployment/nemotron-mini-4b-gguf-deployment -c llama-cpp-server

# Check PVC
kubectl get pvc -n log-analysis-aiops
```

### Application Issues

```bash
# Check application logs
kubectl logs -n log-analysis-aiops deployment/log-analysis-aiops

# Check RBAC
kubectl auth can-i get pods --namespace log-analysis-aiops --as=system:serviceaccount:log-analysis-aiops:log-analysis-aiops
```

## Resource Requirements

### LLM Pod (Development - m7i.xlarge)
- CPU: 2-3 cores (request: 2, limit: 3)
- Memory: 6-8GB (request: 6Gi, limit: 8Gi)
- Storage: 5GB PVC for model

### LLM Pod (Production - m7i.2xlarge)
- CPU: 4-6 cores (request: 4, limit: 6)
- Memory: 6-8GB (request: 6Gi, limit: 8Gi)
- Storage: 5GB PVC for model
- Use: `llm-deployment-production.yaml`

### Application Pod
- CPU: 0.2-1 core (request: 200m, limit: 1000m)
- Memory: 512Mi-2Gi (request: 512Mi, limit: 2Gi)

## Deployment Files

- `llm-deployment.yaml`: For m7i.xlarge (4 vCPUs) - reduced CPU requests
- `llm-deployment-production.yaml`: For m7i.2xlarge (8 vCPUs) - better performance

## Notes

- Model is downloaded on first deployment (init container)
- Model is stored in PVC for persistence across pod restarts
- Both services run in the same namespace for easy communication
- Application uses in-cluster Kubernetes config automatically

