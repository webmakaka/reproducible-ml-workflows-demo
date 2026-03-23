#!/bin/bash
set -e

echo ""
echo "========================================================"
echo "Deploying Infrastructure: Registry, MinIO, MLflow, KFP"
echo "              + GitOps (Gitea, ArgoCD)"
echo "========================================================"
echo ""

########################################
# Container Registry Setup
########################################

echo "Deploying local container registry..."
kubectl create namespace registry --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f /root/.registry-deployment.yaml

echo "Waiting for registry pod to become Ready..."
kubectl wait --for=condition=Ready pod -l app=registry -n registry --timeout=180s

echo "Registry is ready at: registry-service.registry.svc.cluster.local:5000"
echo ""

# Configure containerd to use insecure registry
echo "Configuring containerd for local registry..."
mkdir -p /etc/containerd/certs.d/registry-service.registry.svc.cluster.local:5000
cat > /etc/containerd/certs.d/registry-service.registry.svc.cluster.local:5000/hosts.toml <<EOF
server = "http://registry-service.registry.svc.cluster.local:5000"

[host."http://registry-service.registry.svc.cluster.local:5000"]
  capabilities = ["pull", "resolve", "push"]
  skip_verify = true
EOF

# Also configure for localhost access
mkdir -p /etc/containerd/certs.d/localhost:30500
cat > /etc/containerd/certs.d/localhost:30500/hosts.toml <<EOF
server = "http://localhost:30500"

[host."http://localhost:30500"]
  capabilities = ["pull", "resolve", "push"]
  skip_verify = true
EOF

echo "Containerd configured for insecure registry."
echo ""

########################################
# MinIO Setup
########################################

echo "Deploying MinIO..."
kubectl apply -f /root/.minio-deployment.yaml

echo "Waiting for MinIO pod to become Ready..."
kubectl wait --for=condition=Ready pod -l app=minio --timeout=180s

MINIO_POD=$(kubectl get pods -l app=minio -o jsonpath='{.items[0].metadata.name}')
echo "MinIO pod is ready: $MINIO_POD"

echo "Creating 'mlartifacts' bucket in MinIO..."
kubectl exec "$MINIO_POD" -- mc alias set local http://127.0.0.1:9000 minioadmin minioadmin >/dev/null 2>&1
kubectl exec "$MINIO_POD" -- sh -c "mc mb local/mlartifacts || true"

echo "Bucket 'mlartifacts' verified/created."
echo ""


########################################
# MLflow Setup
########################################

echo "Deploying MLflow..."
kubectl apply -f /root/.mlflow-deployment.yaml

echo "Waiting for MLflow pod to be created..."
sleep 5   # initial delay to allow pod to appear

MLFLOW_POD=""
until [[ -n "$MLFLOW_POD" ]]; do
    MLFLOW_POD=$(kubectl get pods -l app=mlflow -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
    [[ -n "$MLFLOW_POD" ]] || { echo "   ...waiting for MLflow pod to be created"; sleep 3; }
done

echo "Waiting for MLflow pod to become Ready..."
kubectl wait --for=condition=Ready pod/"$MLFLOW_POD" --timeout=180s

echo "MLflow pod is ready: $MLFLOW_POD"
echo ""


########################################
# Kubeflow Pipelines Setup
########################################

echo "Installing Kubeflow Pipelines..."
export PIPELINE_VERSION=2.14.4

echo "Applying cluster-scoped resources..."
kubectl apply -k "github.com/kubeflow/pipelines/manifests/kustomize/cluster-scoped-resources?ref=$PIPELINE_VERSION"

echo "Waiting for Applications CRD to become established..."
kubectl wait --for condition=established --timeout=60s crd/applications.app.k8s.io

echo "Applying Kubeflow Pipelines components..."
kubectl apply -k "github.com/kubeflow/pipelines/manifests/kustomize/env/platform-agnostic?ref=$PIPELINE_VERSION"

echo "Waiting for Kubeflow Pipeline pods to become Ready (this may take several minutes)..."
kubectl wait pods -l application-crd-id=kubeflow-pipelines -n kubeflow --for condition=Ready --timeout=600s

echo "Applying UI NodePort patch..."
kubectl apply -f /root/kfp-ui-nodeport-patch.yaml

echo ""


########################################
# PVCs for Pipeline
########################################

echo "Creating PersistentVolumes and PersistentVolumeClaims..."
kubectl apply -f /root/data-pv.yaml
kubectl apply -f /root/artifacts-pv.yaml

echo ""


########################################
# RBAC for Pipeline Deployments
########################################

echo "Applying RBAC for pipeline service account..."
kubectl apply -f /root/kfp-rbac.yaml

echo ""


########################################
# Wait for GitOps Infrastructure
########################################

# Verify Gitea is ready
echo "Verifying Gitea is ready..."
kubectl wait --for=condition=Ready pod -l app=gitea --timeout=60s
echo "Gitea is ready at: http://localhost:30503"

# Verify ArgoCD is ready
echo "Verifying ArgoCD is ready..."
kubectl wait --for=condition=Ready pods -l app.kubernetes.io/name=argocd-server -n argocd --timeout=60s
echo "ArgoCD is ready at: http://localhost:30504"

echo ""


########################################
# GitOps Repository Setup
########################################

echo "Setting up GitOps repository in Gitea..."

# Create a temporary directory for repo setup
GITOPS_TEMP="/tmp/mlops-gitops"
rm -rf "$GITOPS_TEMP"
mkdir -p "$GITOPS_TEMP"

# Initialize git repo
cd "$GITOPS_TEMP"
git init
git config user.email "mlops@kodekloud.com"
git config user.name "MLOps"

# Create directory structure
mkdir -p apps/model-serve

# Copy model deployment manifest
cp /root/.model-deployment.yaml apps/model-serve/deployment.yaml

# Create a README
cat > README.md <<'EOF'
# MLOps GitOps Repository

This repository contains Kubernetes manifests managed by ArgoCD.

## Structure

- `apps/model-serve/` - Model serving deployment manifests

## Managed by

- ArgoCD auto-syncs changes from this repository
- Kubeflow Pipeline pushes image updates here
EOF

# Commit initial manifests
git add .
git commit -m "Initial commit: model-serve deployment"
git branch -M main

# Create repository in Gitea via API
echo "Creating repository in Gitea..."
GITEA_POD=$(kubectl get pods -l app=gitea -o jsonpath='{.items[0].metadata.name}')

# Create the repository using Gitea API
kubectl exec "$GITEA_POD" -- curl -s -X POST \
    "http://localhost:3000/api/v1/user/repos" \
    -H "Content-Type: application/json" \
    -u "mlops:admin123" \
    -d '{"name":"mlops-gitops","description":"GitOps repository for MLOps","private":false,"auto_init":false}'

# Push to Gitea via git (using kubectl port-forward temporarily)
echo "Pushing initial manifests to Gitea..."

# Use kubectl exec to push from inside the cluster
kubectl exec "$GITEA_POD" -- rm -rf /tmp/mlops-gitops 2>/dev/null

# Create repo in Gitea pod and push
kubectl cp "$GITOPS_TEMP" "$GITEA_POD":/tmp/mlops-gitops

kubectl exec "$GITEA_POD" -- sh -c "cd /tmp/mlops-gitops && \
    git config user.email 'mlops@kodekloud.com' && \
    git config user.name 'MLOps' && \
    git remote add origin http://mlops:admin123@localhost:3000/mlops/mlops-gitops.git 2>/dev/null || git remote set-url origin http://mlops:admin123@localhost:3000/mlops/mlops-gitops.git && \
    git push -u origin main --force"

# Cleanup
rm -rf "$GITOPS_TEMP"
cd /root

echo "GitOps repository created and initialized."
echo ""


########################################
# ArgoCD Application Registration
########################################

echo "Registering model-serve application with ArgoCD..."

# Add Gitea repository to ArgoCD
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: gitea-repo
  namespace: argocd
  labels:
    argocd.argoproj.io/secret-type: repository
stringData:
  type: git
  url: http://gitea-service.default.svc.cluster.local:3000/mlops/mlops-gitops.git
  username: mlops
  password: admin123
EOF

# Create ArgoCD Application
kubectl apply -f /root/.argocd-model-serve-app.yaml

echo "ArgoCD Application registered. Model serving is now managed by GitOps."
echo ""

echo ""
echo "========================================================"
echo "Infrastructure Deployment Complete!"
echo "========================================================"
echo ""
echo "Services Available:"
echo "  - Kubeflow Pipelines UI: http://localhost:30502"
echo "  - MLflow UI:             http://localhost:30505"
echo "  - MinIO Console:         http://localhost:30901"
echo "  - Container Registry:    localhost:30500"
echo "  - Gitea (Git Server):    http://localhost:30503  (mlops / admin123)"
echo "  - ArgoCD Dashboard:      http://localhost:30504  (admin / admin123)"
echo "  - Model Service:         http://localhost:30501  (managed by ArgoCD)"
echo ""
echo "Next Steps:"
echo "  1. Compile pipeline:  python3 /root/code/full_pipeline.py"
echo "  2. Submit pipeline:   Through KFP UI, upload the compiled pipeline /root/code/full_pipeline.yaml"
echo "  3. Observe pipeline in KFP UI:    http://localhost:30502"
echo "  4. Watch GitOps sync in ArgoCD:   http://localhost:30504"
echo ""
