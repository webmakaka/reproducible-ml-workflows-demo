#!/bin/bash

# Kubeflow Pipelines v2.14.4 images (from ghcr.io/kubeflow)
crictl pull ghcr.io/kubeflow/kfp-frontend:2.14.4
crictl pull ghcr.io/kubeflow/kfp-api-server:2.14.4
crictl pull ghcr.io/kubeflow/kfp-persistence-agent:2.14.4
crictl pull ghcr.io/kubeflow/kfp-scheduled-workflow-controller:2.14.4
crictl pull ghcr.io/kubeflow/kfp-metadata-envoy:2.14.4
crictl pull ghcr.io/kubeflow/kfp-metadata-writer:2.14.4
crictl pull ghcr.io/kubeflow/kfp-viewer-crd-controller:2.14.4
crictl pull ghcr.io/kubeflow/kfp-cache-server:2.14.4
crictl pull ghcr.io/kubeflow/kfp-cache-deployer:2.14.4
crictl pull ghcr.io/kubeflow/kfp-visualization-server:2.14.4
crictl pull quay.io/argoproj/workflow-controller:v3.6.7
crictl pull minio/minio:RELEASE.2019-08-14T20-37-41Z
crictl pull mysql:8.4
crictl pull gcr.io/tfx-oss-public/ml_metadata_store_server:1.14.0

# Gitea Git Server
crictl pull gitea/gitea:1.22-rootless

# ArgoCD v2.13.0 images
crictl pull quay.io/argoproj/argocd:v2.13.0
crictl pull ghcr.io/dexidp/dex:v2.41.1
crictl pull redis:7.0.15-alpine
