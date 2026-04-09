# Building Reproducible ML Environments with Argo and Kubeflow

**KubeCon Europe 2026 — Amsterdam**
Workshop by Nourhan Mohamed

https://www.youtube.com/watch?v=JA1rrdMLmew

---

## Overview

This repository contains the full source code and Kubernetes manifests from the KubeCon Europe 2026 workshop on building reproducible ML workflows on Kubernetes.

The workshop demonstrates a complete ML pipeline that trains an anomaly detection model, validates it against quality gates, packages it into a container image, and deploys it to Kubernetes via GitOps — all orchestrated by Kubeflow Pipelines with full lineage tracking.

### What You'll Find Here

- A **5-step ML pipeline** (train, register, validate, build, deploy) defined in pure Python using KFP v2
- **Kubernetes manifests** for MLflow, MinIO, Gitea, ArgoCD, and supporting infrastructure
- **Deployment scripts** to stand up the entire stack on a single-node Kubernetes cluster
- A **fitness tracking dataset** (20,000 records, 39 features) used for anomaly detection

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                    Kubeflow Pipelines (Argo)                   │
│                                                                │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌───────┐  ┌──────┐  │
│  │  Train  ├─►│ Register ├─►│ Validate ├─►│ Build ├─►│Deploy│  │
│  └────┬────┘  └────┬─────┘  └──────────┘  └───┬───┘  └──┬───┘  │
│       │            │                           │         │     │
└───────┼────────────┼───────────────────────────┼─────────┼─────┘
        │            │                           │         │
   ┌────▼────┐  ┌────▼─────┐               ┌────▼───┐ ┌───▼──┐
   │  MLflow │  │  Model   │               │Registry│ │ Gitea│
   │Tracking │  │ Registry │               │ (OCI)  │ │ (Git)│
   └────┬────┘  └──────────┘               └────────┘ └───┬──┘
        │                                                  │
   ┌────▼────┐                                        ┌────▼───┐
   │  MinIO  │                                        │ ArgoCD │
   │  (S3)   │                                        │(GitOps)│
   └─────────┘                                        └────┬───┘
                                                           │
                                                    ┌──────▼──────┐
                                                    │Model Serving│
                                                    │  (2 pods)   │
                                                    └─────────────┘
```

**Data layer:** Training data in a PersistentVolume, model artifacts in MinIO via MLflow.

**Orchestration:** Kubeflow Pipelines v2 on Argo Workflows with ML Metadata for automatic lineage tracking.

**Build:** Kaniko builds container images in-cluster without Docker daemon privileges.

**Deployment:** GitOps via Gitea + ArgoCD. The pipeline commits to Git; ArgoCD syncs to the cluster.

## Repository Structure

```
.
├── pipeline/
│   ├── full_pipeline.py       # KFP v2 pipeline definition (5 components)
│   ├── run_kfp.py             # CLI tool to submit compiled pipelines
│   └── sample_request.json    # Sample prediction request for the served model
├── data/
│   └── fitness_data.csv       # Training dataset (20k records, 39 features)
├── manifests/
│   ├── mlflow-deployment.yaml
│   ├── minio-deployment.yaml
│   ├── registry-deployment.yaml
│   ├── gitea-deployment.yaml
│   ├── model-deployment.yaml
│   ├── argocd-model-serve-app.yaml
│   ├── data-pv.yaml
│   ├── artifacts-pv.yaml
│   ├── kfp-rbac.yaml
│   └── kfp-ui-nodeport-patch.yaml
├── scripts/
│   ├── deploy_stack.sh        # Master script — deploys the full infrastructure
│   ├── deploy-gitops-infra.sh # Deploys Gitea + ArgoCD (called by deploy_stack.sh)
│   └── pull-images.sh         # Pre-pulls container images to avoid network delays
└── README.md
```

## The Pipeline

The pipeline is defined in [`pipeline/full_pipeline.py`](pipeline/full_pipeline.py) using KFP v2's `@dsl.component` decorator. Each step runs in an isolated container with pinned dependency versions.

| Step | What It Does |
|------|-------------|
| **Train** | Loads the fitness dataset, trains an IsolationForest anomaly detector, logs parameters and metrics to MLflow, saves the model artifact to MinIO |
| **Register** | Registers the trained model in MLflow's Model Registry with auto-incremented versioning |
| **Validate** | Fetches metrics from MLflow and checks anomaly rate against thresholds (default: 3%–8%). Halts the pipeline if validation fails |
| **Build** | Uses Kaniko to build a serving container image with pinned dependencies matching the training environment. Pushes to the in-cluster registry |
| **Deploy** | Updates the deployment manifest in a Gitea repository. ArgoCD detects the change and syncs to the cluster automatically |

### Key Design Decisions

- **All package versions are pinned** (`mlflow==2.17.2`, `scikit-learn==1.5.2`, `pandas==2.2.3`) — the training and serving environments are identical
- **Every pipeline parameter** (hyperparameters, thresholds, endpoints) is configurable at submission time without code changes
- **ML Metadata** automatically tracks lineage for all typed artifacts flowing between steps
- **Quality gates** prevent bad models from reaching production — a failed validation stops the pipeline before build or deploy

## Quick Start

### Prerequisites

- A Kubernetes cluster (single-node is sufficient)
- `kubectl` configured and pointing to the cluster
- Python 3.12+ with the KFP SDK (`pip install kfp==2.14.4`)

### 1. Deploy the Infrastructure

```bash
chmod +x scripts/*.sh
./scripts/deploy_stack.sh
```

This deploys MinIO, MLflow, Kubeflow Pipelines, Gitea, ArgoCD, and all supporting resources. It takes a few minutes for all pods to become ready.

### 2. Access the UIs

| Service | URL | Credentials |
|---------|-----|-------------|
| Kubeflow Pipelines | `http://localhost:30502` | — |
| MLflow | `http://localhost:30505` | — |
| Gitea | `http://localhost:30503` | `mlops` / `admin123` |
| ArgoCD | `http://localhost:30504` | `admin` / `admin123` |

### 3. Compile and Run the Pipeline

```bash
cd pipeline

# Compile the pipeline to YAML
python full_pipeline.py

# Submit to the cluster
python run_kfp.py full_pipeline.yaml
```

### 4. Test the Deployed Model

Once the pipeline completes and ArgoCD syncs the deployment:

```bash
curl -X POST http://localhost:30501/invocations \
  -H "Content-Type: application/json" \
  -d @sample_request.json
```

A prediction of `1` means normal; `-1` means anomaly.

### 5. Try Breaking the Validation Gate

Submit again with a contamination rate that exceeds the threshold:

```bash
python run_kfp.py full_pipeline.yaml --param contamination=0.3
```

The validate step will fail (anomaly rate 30% vs. the 3%–8% threshold), and the build and deploy steps will never execute.

## Tools Used

| Tool | Role |
|------|------|
| [Kubeflow Pipelines v2](https://www.kubeflow.org/docs/components/pipelines/) | Pipeline orchestration with ML Metadata lineage |
| [MLflow](https://mlflow.org/) | Experiment tracking and model registry |
| [MinIO](https://min.io/) | S3-compatible artifact storage |
| [Kaniko](https://github.com/GoogleContainerTools/kaniko) | In-cluster container image builds (no Docker daemon) |
| [Gitea](https://gitea.io/) | Self-hosted Git server for deployment manifests |
| [ArgoCD](https://argo-cd.readthedocs.io/) | GitOps-based deployment reconciliation |
| [scikit-learn](https://scikit-learn.org/) | IsolationForest anomaly detection model |

## Dataset

The fitness tracking dataset contains 20,000 records with 39 numeric features across four categories:

- **Biometrics:** BMI, body fat percentage, lean mass
- **Heart rate:** max/avg/resting BPM, % heart rate reserve
- **Exercise:** workout frequency, session duration, calories burned
- **Nutrition:** macronutrient intake, caloric balance

The IsolationForest model learns what a typical fitness record looks like and flags statistically unusual combinations as anomalies. With default parameters (`contamination=0.05`), the model flags 1,000 records (5%) as anomalous.

## License

This project is provided as educational material from the KubeCon Europe 2026 workshop.
