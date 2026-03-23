#!/bin/bash
# Background script to deploy Gitea and ArgoCD infrastructure
# This runs during startup to have GitOps infra ready when user runs deploy_stack.sh

set -e

LOG_FILE="/root/.gitops-deploy.log"
exec > "$LOG_FILE" 2>&1

echo "$(date): Starting GitOps infrastructure deployment..."

########################################
# Gitea Git Server Setup
########################################

echo "$(date): Deploying Gitea Git Server..."
kubectl apply -f /root/.gitea-deployment.yaml

echo "$(date): Waiting for Gitea pod to become Ready..."
kubectl wait --for=condition=Ready pod -l app=gitea --timeout=300s

GITEA_POD=$(kubectl get pods -l app=gitea -o jsonpath='{.items[0].metadata.name}')
echo "$(date): Gitea pod is ready: $GITEA_POD"

# Wait for Gitea to fully initialize
echo "$(date): Waiting for Gitea to initialize..."
sleep 15

# Create mlops user using Gitea CLI
echo "$(date): Creating mlops user in Gitea..."
kubectl exec "$GITEA_POD" -- gitea admin user create \
    --username mlops \
    --password admin123 \
    --email mlops@kodekloud.com \
    --must-change-password=false \
    --admin || echo "User may already exist"

echo "$(date): Gitea is ready"

########################################
# ArgoCD Installation
########################################

# Install ArgoCD
kubectl create namespace argocd
kubectl create -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# Set predefined password (hash for "admin123")
kubectl -n argocd patch secret argocd-secret \
  -p '{"stringData": {
    "admin.password": "$2a$12$/BHc8IhRJelrXucA1JxHTeTprsdi/mvxV74HjiH2JBlL8..PyS9bW",
    "admin.passwordMtime": "'$(date +%FT%T%Z)'"
  }}'

# Expose ArgoCD server via NodePort on port 30504
kubectl patch svc argocd-server -n argocd -p '{"spec":{"type":"NodePort","ports":[{"port":80,"nodePort":30504}]}}'

# Patch Argo to allow insecure access and restart server
kubectl -n argocd patch configmap argocd-cmd-params-cm \
  --type merge -p '{"data":{"server.insecure":"true"}}'
kubectl -n argocd rollout restart deploy argocd-server

# Install argocd CLI
curl -sSL -o /usr/local/bin/argocd https://github.com/argoproj/argo-cd/releases/latest/download/argocd-linux-amd64
chmod +x /usr/local/bin/argocd

echo "$(date): GitOps infrastructure deployment complete!"
