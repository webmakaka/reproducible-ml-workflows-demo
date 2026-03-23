"""
KubeCon 2026: Reproducible ML Environments Demo
Full Train-to-Deploy Pipeline with GitOps

This pipeline demonstrates:
1. train_model() - Train IsolationForest and log to MLflow
2. register_model() - Register trained model in Model Registry
3. validate_model() - Fetch metrics and validate model performance
4. build_and_push_image() - Build Docker image and push it to registry
5. deploy_model_gitops() - Push manifest to Git, ArgoCD auto-syncs to Kubernetes
"""

from kfp import dsl
from kfp.dsl import Output, Model
from kfp import kubernetes as kfp_k8s


@dsl.component(
    base_image="python:3.12",
    packages_to_install=[
        "mlflow==2.17.2",
        "scikit-learn==1.5.2",
        "pandas==2.2.3",
        "boto3==1.35.0",
        "numpy==2.0.2",
        "joblib==1.4.2",
    ],
)
def train_model(
    mlflow_tracking_uri: str,
    minio_endpoint: str,
    aws_access_key: str,
    aws_secret_key: str,
    experiment_name: str,
    data_path: str,
    n_estimators: int,
    contamination: float,
    random_state: int,
    model: Output[Model],
) -> str:
    """Train IsolationForest model and log to MLflow. Returns run_id."""
    import os
    import joblib
    import mlflow
    import mlflow.sklearn
    import pandas as pd

    print("Starting model training component...")

    mlflow.set_tracking_uri(mlflow_tracking_uri)
    print(f"MLflow tracking URI: {mlflow_tracking_uri}")

    os.environ["MLFLOW_S3_ENDPOINT_URL"] = minio_endpoint
    os.environ["AWS_ACCESS_KEY_ID"] = aws_access_key
    os.environ["AWS_SECRET_ACCESS_KEY"] = aws_secret_key
    print(f"MinIO endpoint configured: {minio_endpoint}")

    mlflow.set_experiment(experiment_name)
    print(f"Using experiment: {experiment_name}")

    with mlflow.start_run() as run:
        print(f"MLflow run started: {run.info.run_id}")

        print(f"Loading data from: {data_path}")
        df = pd.read_csv(data_path)
        print(f"Data shape: {df.shape}")

        num_cols = sorted(df.select_dtypes(include="number").columns)
        X = df[num_cols].dropna()
        print(f"Feature matrix shape: {X.shape}")

        from sklearn.ensemble import IsolationForest

        model_obj = IsolationForest(
            n_estimators=n_estimators,
            contamination=contamination,
            random_state=random_state,
        )

        print(f"Training with n_estimators={n_estimators}, contamination={contamination}")
        model_obj.fit(X)
        print("Training complete!")

        predictions = model_obj.predict(X)
        anomaly_count = int((predictions == -1).sum())
        anomaly_rate = float((predictions == -1).mean())

        print(f"Anomalies detected: {anomaly_count} ({anomaly_rate:.2%})")

        mlflow.log_param("n_estimators", n_estimators)
        mlflow.log_param("contamination", contamination)
        mlflow.log_param("random_state", random_state)
        mlflow.log_param("n_samples", int(len(X)))
        mlflow.log_param("n_features", int(len(X.columns)))

        mlflow.log_metric("anomaly_rate", anomaly_rate)
        mlflow.log_metric("anomaly_count", anomaly_count)
        mlflow.log_metric("training_samples", int(len(X)))

        mlflow.sklearn.log_model(model_obj, "model")
        print("Model logged to MLflow artifact store")

        # Persist model to the KFP model artifact output
        joblib.dump(model_obj, model.path)

        run_id = run.info.run_id
        print(f"Training complete! Run ID: {run_id}")
        return run_id


@dsl.component(
    base_image="python:3.12",
    packages_to_install=["mlflow==2.17.2", "boto3==1.35.0"],
)
def register_model(
    mlflow_tracking_uri: str,
    minio_endpoint: str,
    aws_access_key: str,
    aws_secret_key: str,
    run_id: str,
    model_name: str,
) -> str:
    """Register trained model in MLflow Model Registry. Returns model version."""
    import os
    import mlflow

    print("Starting model registration component...")
    mlflow.set_tracking_uri(mlflow_tracking_uri)

    os.environ["MLFLOW_S3_ENDPOINT_URL"] = minio_endpoint
    os.environ["AWS_ACCESS_KEY_ID"] = aws_access_key
    os.environ["AWS_SECRET_ACCESS_KEY"] = aws_secret_key

    print(f"Registering model from run: {run_id}")
    model_uri = f"runs:/{run_id}/model"
    print(f"Model URI: {model_uri}")

    result = mlflow.register_model(model_uri=model_uri, name=model_name)
    model_version = result.version

    print(f"Model registered: {model_name} version {model_version}")
    return str(model_version)


@dsl.component(
    base_image="python:3.12",
    packages_to_install=["requests==2.32.3"],
)
def validate_model(
    mlflow_tracking_uri: str,
    run_id: str,
    anomaly_rate_min: float,
    anomaly_rate_max: float,
) -> str:
    """Validate model performance by checking metrics from MLflow. Returns JSON string."""
    import json
    import requests

    print(f"Validating model from run: {run_id}")
    print(f"Acceptable range: {anomaly_rate_min}% - {anomaly_rate_max}%")

    api_url = f"{mlflow_tracking_uri}/api/2.0/mlflow/runs/get"
    response = requests.get(api_url, params={"run_id": run_id}, timeout=30)
    response.raise_for_status()

    run_data = response.json()
    metrics = run_data["run"]["data"]["metrics"]
    metrics_dict = {metric["key"]: metric["value"] for metric in metrics}

    print(f"Retrieved metrics: {metrics_dict}")

    anomaly_rate = metrics_dict.get("anomaly_rate")

    if anomaly_rate is None:
        validation_result = {
            "decision": "FAILED",
            "reason": "anomaly_rate metric not found",
            "metrics": metrics_dict,
        }
    else:
        anomaly_rate_pct = float(anomaly_rate) * 100.0
        if anomaly_rate_min <= anomaly_rate_pct <= anomaly_rate_max:
            decision = "PASSED"
            reason = f"Anomaly rate {anomaly_rate_pct:.2f}% within range"
        else:
            decision = "FAILED"
            reason = (
                f"Anomaly rate {anomaly_rate_pct:.2f}% outside range "
                f"[{anomaly_rate_min}%-{anomaly_rate_max}%]"
            )

        validation_result = {
            "decision": decision,
            "reason": reason,
            "anomaly_rate": anomaly_rate_pct,
            "threshold_min": anomaly_rate_min,
            "threshold_max": anomaly_rate_max,
            "metrics": metrics_dict,
        }

    print(f"Validation: {validation_result['decision']} - {validation_result['reason']}")
    return json.dumps(validation_result)


@dsl.container_component
def build_and_push_image(
    registry_push_url: str,
    registry_pull_url: str,
    image_name: str,
    model_name: str,
    model_version: str,
    mlflow_tracking_uri: str,
    minio_endpoint: str,
    image_uri: dsl.OutputPath(str),
) -> dsl.ContainerSpec:
    """
    Build and push model serving image using Kaniko (no Docker daemon).
    Outputs the full image URI to image_uri output path.
    """
    script = r"""
set -euo pipefail

# Positional args from `sh -c`: $0 is dummy, so start at $1
REGISTRY_PUSH_URL="$1"
REGISTRY_PULL_URL="$2"
IMAGE_NAME="$3"
MODEL_NAME="$4"
MODEL_VERSION="$5"
MLFLOW_TRACKING_URI="$6"
MLFLOW_S3_ENDPOINT_URL="$7"
IMAGE_URI_PATH="$8"

TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TAG="v${MODEL_VERSION}"

DEST_PUSH="${REGISTRY_PUSH_URL}/${IMAGE_NAME}:${TAG}"
DEST_PULL="${REGISTRY_PULL_URL}/${IMAGE_NAME}:${TAG}"

mkdir -p /workspace

cat > /workspace/Dockerfile <<EOF
FROM python:3.12-slim

LABEL org.opencontainers.image.created="${TS}"
LABEL org.opencontainers.image.description="Model serving image for ${MODEL_NAME}"
LABEL model.name="${MODEL_NAME}"
LABEL model.version="${MODEL_VERSION}"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    mlflow==2.17.2 \
    scikit-learn==1.5.2 \
    pandas==2.2.3 \
    boto3==1.35.0

ENV MODEL_VERSION=${MODEL_VERSION}
ENV MODEL_NAME=${MODEL_NAME}
ENV MLFLOW_TRACKING_URI=${MLFLOW_TRACKING_URI}
ENV MLFLOW_S3_ENDPOINT_URL=${MLFLOW_S3_ENDPOINT_URL}
ENV AWS_ACCESS_KEY_ID=minioadmin
ENV AWS_SECRET_ACCESS_KEY=minioadmin

EXPOSE 5001

HEALTHCHECK --interval=30s --timeout=3s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:5001/health || exit 1

CMD ["mlflow","models","serve","-m","models:/${MODEL_NAME}/${MODEL_VERSION}","-h","0.0.0.0","-p","5001","--env-manager=local"]

EOF

echo "Building and pushing image to: ${DEST_PUSH}"

/kaniko/executor \
  --dockerfile=/workspace/Dockerfile \
  --context=dir:///workspace \
  --destination="${DEST_PUSH}" \
  --insecure \
  --skip-tls-verify \
  --cache=true \
  --cache-repo="${REGISTRY_PUSH_URL}/${IMAGE_NAME}/cache" \
  --verbosity=info

echo -n "${DEST_PULL}" > "${IMAGE_URI_PATH}"
"""

    return dsl.ContainerSpec(
        image="gcr.io/kaniko-project/executor:v1.23.0-debug",
        command=["/busybox/sh", "-c"],
        # For `sh -c`, args[0] is the script, args[1] is $0 (dummy), then $1.. are our values
        args=[
            script,
            "sh",
            registry_push_url,
            registry_pull_url,
            image_name,
            model_name,
            model_version,
            mlflow_tracking_uri,
            minio_endpoint,
            image_uri,
        ],
    )


@dsl.component(
    base_image="python:3.12",
    packages_to_install=["gitpython==3.1.43", "pyyaml==6.0.2"],
)
def deploy_model_gitops(
    image_uri: str,
    model_version: str,
    gitea_url: str,
    gitea_user: str,
    gitea_password: str,
    gitops_repo_name: str,
    deployment_manifest_path: str,
) -> str:
    """Push deployment changes to GitOps repository. ArgoCD auto-syncs to Kubernetes."""
    import os
    import tempfile
    from datetime import datetime

    import git
    import yaml

    print(f"GitOps deployment: {image_uri}")
    print(f"Gitea URL: {gitea_url}")
    print(f"Repository: {gitops_repo_name}")

    # Construct authenticated repo URL
    repo_url = f"http://{gitea_user}:{gitea_password}@{gitea_url}/{gitea_user}/{gitops_repo_name}.git"

    with tempfile.TemporaryDirectory() as tmpdir:
        # Clone the GitOps repository
        print(f"Cloning repository...")
        repo = git.Repo.clone_from(repo_url, tmpdir)

        # Configure git user
        repo.config_writer().set_value("user", "email", "pipeline@kubeflow.local").release()
        repo.config_writer().set_value("user", "name", "Kubeflow Pipeline").release()

        # Read and update deployment manifest
        manifest_path = os.path.join(tmpdir, deployment_manifest_path)
        print(f"Updating manifest: {deployment_manifest_path}")

        with open(manifest_path, "r") as f:
            docs = list(yaml.safe_load_all(f))

        # Update the Deployment resource
        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        for doc in docs:
            if doc and doc.get("kind") == "Deployment":
                # Update container image
                containers = doc["spec"]["template"]["spec"]["containers"]
                for container in containers:
                    if container["name"] == "model-server":
                        container["image"] = image_uri
                        print(f"Updated image to: {image_uri}")

                # Add/update annotations for traceability
                if "annotations" not in doc["spec"]["template"]["metadata"]:
                    doc["spec"]["template"]["metadata"]["annotations"] = {}
                annotations = doc["spec"]["template"]["metadata"]["annotations"]
                annotations["mlops/model-version"] = model_version
                annotations["mlops/deployed-at"] = timestamp
                annotations["mlops/deployed-by"] = "kubeflow-pipeline"

        # Write updated manifest
        with open(manifest_path, "w") as f:
            yaml.dump_all(docs, f, default_flow_style=False)

        # Commit and push changes
        repo.index.add([deployment_manifest_path])

        commit_message = f"Deploy model version {model_version}\n\nImage: {image_uri}\nTimestamp: {timestamp}"
        repo.index.commit(commit_message)

        print("Pushing changes to GitOps repository...")
        origin = repo.remote("origin")
        origin.set_url(repo_url)
        origin.push()

        print("GitOps deployment complete - ArgoCD will sync the changes")

    return f"Pushed {image_uri} to GitOps repo. ArgoCD will auto-sync."


@dsl.pipeline(
    name="Reproducible ML Pipeline",
    description="Complete train-register-validate-build-deploy workflow with GitOps deployment",
)
def reproducible_ml_pipeline(
    # Registry configuration
    registry_push_url: str = "registry-service.registry.svc.cluster.local:5000",
    registry_pull_url: str = "localhost:30500",
    image_name: str = "model-serve",
    # Infrastructure configuration
    mlflow_tracking_uri: str = "http://mlflow-service.default.svc.cluster.local:5000",
    minio_endpoint: str = "http://minio-service.default.svc.cluster.local:9000",
    aws_access_key: str = "minioadmin",
    aws_secret_key: str = "minioadmin",
    # Training configuration
    experiment_name: str = "kubecon-demo",
    data_path: str = "/data/fitness_data.csv",
    n_estimators: int = 100,
    contamination: float = 0.05,
    random_state: int = 42,
    # Model Registry configuration
    model_name: str = "anomaly-detector",
    # Validation configuration
    anomaly_rate_min: float = 3.0,
    anomaly_rate_max: float = 8.0,
    # GitOps deployment configuration
    gitea_url: str = "gitea-service.default.svc.cluster.local:3000",
    gitea_user: str = "mlops",
    gitea_password: str = "admin123",
    gitops_repo_name: str = "mlops-gitops",
    deployment_manifest_path: str = "apps/model-serve/deployment.yaml",
):
    # Step 1: Train model
    train_task = train_model(
        mlflow_tracking_uri=mlflow_tracking_uri,
        minio_endpoint=minio_endpoint,
        aws_access_key=aws_access_key,
        aws_secret_key=aws_secret_key,
        experiment_name=experiment_name,
        data_path=data_path,
        n_estimators=n_estimators,
        contamination=contamination,
        random_state=random_state,
    )
    kfp_k8s.mount_pvc(
        train_task,
        pvc_name="training-data-pvc",
        mount_path="/data",
    )

    # Step 2: Register model
    register_task = register_model(
        mlflow_tracking_uri=mlflow_tracking_uri,
        minio_endpoint=minio_endpoint,
        aws_access_key=aws_access_key,
        aws_secret_key=aws_secret_key,
        run_id=train_task.outputs["Output"],
        model_name=model_name,
    )
    register_task.set_caching_options(False)
    register_task.after(train_task)

    # Step 3: Validate model
    validate_task = validate_model(
        mlflow_tracking_uri=mlflow_tracking_uri,
        run_id=train_task.outputs["Output"],
        anomaly_rate_min=anomaly_rate_min,
        anomaly_rate_max=anomaly_rate_max,
    )
    validate_task.set_caching_options(False)
    validate_task.after(register_task)

    # Step 4: Build & push image (Kaniko)
    build_task = build_and_push_image(
        registry_push_url=registry_push_url,
        registry_pull_url=registry_pull_url,
        image_name=image_name,
        model_name=model_name,
        model_version=register_task.outputs["Output"],
        mlflow_tracking_uri=mlflow_tracking_uri,
        minio_endpoint=minio_endpoint,
    )
    build_task.set_caching_options(False)
    build_task.after(validate_task)

    # Step 5: Deploy via GitOps (push to Git, ArgoCD syncs)
    deploy_task = deploy_model_gitops(
        image_uri=build_task.outputs["image_uri"],
        model_version=register_task.outputs["Output"],
        gitea_url=gitea_url,
        gitea_user=gitea_user,
        gitea_password=gitea_password,
        gitops_repo_name=gitops_repo_name,
        deployment_manifest_path=deployment_manifest_path,
    )
    deploy_task.set_caching_options(False)
    deploy_task.after(build_task)


if __name__ == "__main__":
    from kfp import compiler

    compiler.Compiler().compile(
        pipeline_func=reproducible_ml_pipeline,
        package_path="full_pipeline.yaml",
    )
    print("Pipeline compiled successfully to full_pipeline.yaml")
