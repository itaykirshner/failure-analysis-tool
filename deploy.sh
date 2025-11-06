#!/bin/bash

# Failure Analysis Tool - Complete Deployment Script
# This script builds the app image, pushes to ECR, and deploys all Kubernetes resources

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
AWS_REGION=${AWS_REGION:-"us-east-2"}
AWS_ACCOUNT_ID=${AWS_ACCOUNT_ID:-""}
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
NAMESPACE=${1:-failure-analysis-tool}
TAG=${2:-latest}

# Image names
APP_IMAGE="failure-analysis-tool/app"

echo -e "${BLUE}🚀 Starting Failure Analysis Tool Deployment${NC}"
echo -e "${BLUE}=============================================${NC}"
echo -e "Registry: ${ECR_REGISTRY}"
echo -e "Namespace: ${NAMESPACE}"
echo -e "Tag: ${TAG}"
echo ""
echo -e "${YELLOW}Usage: $0 [NAMESPACE] [TAG]${NC}"
echo -e "${YELLOW}  NAMESPACE: Target namespace (default: failure-analysis-tool)${NC}"
echo -e "${YELLOW}  TAG: Image tag (default: latest)${NC}"
echo ""

# Function to print status
print_status() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check prerequisites
echo -e "${BLUE}🔍 Checking prerequisites...${NC}"

if ! command_exists docker; then
    print_error "Docker is not installed or not in PATH"
    exit 1
fi

if ! command_exists kubectl; then
    print_error "kubectl is not installed or not in PATH"
    exit 1
fi

if ! command_exists aws; then
    print_error "AWS CLI is not installed or not in PATH"
    exit 1
fi

if [ -z "$AWS_ACCOUNT_ID" ]; then
    print_error "AWS_ACCOUNT_ID environment variable is not set"
    exit 1
fi

print_status "All prerequisites found"

# Check AWS authentication
echo -e "${BLUE}🔐 Checking AWS authentication...${NC}"
if ! aws sts get-caller-identity >/dev/null 2>&1; then
    print_error "AWS authentication failed. Please run 'aws configure' or set up credentials"
    exit 1
fi
print_status "AWS authentication successful"

# Login to ECR
echo -e "${BLUE}🔑 Logging into ECR...${NC}"
aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${ECR_REGISTRY}
if [ $? -ne 0 ]; then
    print_error "ECR login failed"
    exit 1
fi
print_status "ECR login successful"

# Create ECR repository if it doesn't exist
echo -e "${BLUE}📦 Creating ECR repository...${NC}"
if ! aws ecr describe-repositories --repository-names ${APP_IMAGE} --region ${AWS_REGION} >/dev/null 2>&1; then
    echo "Creating repository: ${APP_IMAGE}"
    aws ecr create-repository --repository-name ${APP_IMAGE} --region ${AWS_REGION}
    if [ $? -eq 0 ]; then
        print_status "Created repository: ${APP_IMAGE}"
    else
        print_error "Failed to create repository: ${APP_IMAGE}"
        exit 1
    fi
else
    print_status "Repository already exists: ${APP_IMAGE}"
fi

# Build and push app image
echo -e "${BLUE}🏗️  Building and pushing app image...${NC}"

echo -e "${YELLOW}Building app image...${NC}"
docker build -t ${ECR_REGISTRY}/${APP_IMAGE}:${TAG} -f Dockerfile .
if [ $? -ne 0 ]; then
    print_error "Docker build failed"
    exit 1
fi
print_status "App image built successfully"

echo -e "${YELLOW}Pushing app image...${NC}"
docker push ${ECR_REGISTRY}/${APP_IMAGE}:${TAG}
if [ $? -ne 0 ]; then
    print_error "Docker push failed"
    exit 1
fi
print_status "App image pushed successfully"

# Create namespace if it doesn't exist
echo -e "${BLUE}📋 Creating Kubernetes namespace...${NC}"
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
print_status "Namespace ${NAMESPACE} ready"

# Apply Kubernetes resources
echo -e "${BLUE}🚀 Deploying Kubernetes resources...${NC}"

# Deploy LLM service first (if using production deployment)
echo -e "${YELLOW}Deploying LLM service...${NC}"
if [ -f k8s/llm-deployment-production.yaml ]; then
    sed "s/namespace: failure-analysis-tool/namespace: ${NAMESPACE}/g" k8s/llm-deployment-production.yaml | \
    kubectl apply -f -
    print_status "LLM service deployed (production config)"
else
    sed "s/namespace: failure-analysis-tool/namespace: ${NAMESPACE}/g" k8s/llm-deployment.yaml | \
    kubectl apply -f -
    print_status "LLM service deployed (development config)"
fi

# Deploy application
echo -e "${YELLOW}Deploying application...${NC}"
sed "s/namespace: failure-analysis-tool/namespace: ${NAMESPACE}/g" k8s/app-deployment.yaml | \
sed "s|PLACEHOLDER_ECR_REGISTRY|${ECR_REGISTRY}|g" | \
sed "s|:latest|:${TAG}|g" | \
kubectl apply -f -
print_status "Application deployed"

# Wait for deployments to be ready
echo -e "${YELLOW}Waiting for deployments to be ready...${NC}"

# Wait for LLM service
echo -e "${YELLOW}Waiting for LLM service...${NC}"
kubectl wait --for=condition=available --timeout=600s deployment/nemotron-mini-4b-gguf-deployment -n ${NAMESPACE} 2>/dev/null || \
print_warning "LLM service not ready yet (this is OK if model is still downloading)"

# Wait for application
echo -e "${YELLOW}Waiting for application...${NC}"
kubectl wait --for=condition=available --timeout=300s deployment/log-analysis-aiops -n ${NAMESPACE}
if [ $? -eq 0 ]; then
    print_status "Application is ready"
else
    print_warning "Application deployment may still be in progress"
fi

# Display deployment status
echo -e "${BLUE}📊 Deployment Status${NC}"
echo -e "${BLUE}===================${NC}"
kubectl get pods -n ${NAMESPACE}
echo ""

kubectl get services -n ${NAMESPACE}
echo ""

# Display useful commands
echo -e "${BLUE}🔧 Useful Commands${NC}"
echo -e "${BLUE}=================${NC}"
echo "View application logs:"
echo "  kubectl logs -f deployment/log-analysis-aiops -n ${NAMESPACE}"
echo ""
echo "View LLM service logs:"
echo "  kubectl logs -f deployment/nemotron-mini-4b-gguf-deployment -n ${NAMESPACE}"
echo ""
echo "Check application health:"
echo "  kubectl exec -it deployment/log-analysis-aiops -n ${NAMESPACE} -- curl http://localhost:8000/health"
echo ""
echo "Port-forward to access application:"
echo "  kubectl port-forward service/log-analysis-aiops-service 8000:8000 -n ${NAMESPACE}"
echo "  Open http://localhost:8000"
echo ""
echo "Check pod status:"
echo "  kubectl get pods -n ${NAMESPACE}"
echo "  kubectl describe pod <pod-name> -n ${NAMESPACE}"
echo ""
echo "Check service endpoints:"
echo "  kubectl get endpoints -n ${NAMESPACE}"
echo ""

print_status "Failure Analysis Tool deployment completed successfully! 🎉"
echo -e "${GREEN}Your application is now running in the ${NAMESPACE} namespace.${NC}"
echo -e "${GREEN}✅ Application: Accessible via port-forward or service${NC}"
echo -e "${GREEN}✅ LLM Service: Running and ready for queries${NC}"

