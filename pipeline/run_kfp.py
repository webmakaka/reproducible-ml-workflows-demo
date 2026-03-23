#!/usr/bin/env python3
"""
Submit a compiled Kubeflow Pipelines YAML to KFP, create a run, and execute it.

Usage:
  python run_kfp.py full_pipeline.yaml
  python run_kfp.py full_pipeline.yaml --param n_estimators=200 --param contamination=0.1
  python run_kfp.py full_pipeline.yaml -e my-experiment -r "Run with high contamination"
"""

import argparse
import sys
import kfp


def parse_params(param_list):
    """Convert ["key1=val1", "key2=val2"] to {"key1": "val1", "key2": "val2"}."""
    params = {}
    for item in param_list:
        key, sep, value = item.partition("=")
        if sep != "=" or not key:
            raise ValueError(f"Invalid --param '{item}', expected key=value")
        # Try to convert to appropriate type
        try:
            if '.' in value:
                params[key] = float(value)
            else:
                params[key] = int(value)
        except ValueError:
            params[key] = value
    return params


def main(argv=None):
    argv = argv or sys.argv[1:]

    parser = argparse.ArgumentParser(
        description="Run a compiled Kubeflow pipeline YAML on KFP."
    )
    parser.add_argument(
        "pipeline_file",
        help="Path to the compiled pipeline YAML file.",
    )
    parser.add_argument(
        "--host",
        help="Kubeflow Pipelines API endpoint.",
        default="http://127.0.0.1:30502/pipeline",
    )
    parser.add_argument(
        "--experiment-name", "-e",
        help="Name of the experiment.",
        default="kubecon-demo",
    )
    parser.add_argument(
        "--run-name", "-r",
        help="Name of the run.",
        default=None,
    )
    parser.add_argument(
        "--namespace", "-n",
        help="Kubernetes namespace.",
        default="kubeflow",
    )
    parser.add_argument(
        "--param", "-p",
        action="append",
        default=[],
        help="Pipeline parameter (key=value). Can be repeated.",
    )

    args = parser.parse_args(argv)

    try:
        params = parse_params(args.param)
    except ValueError as exc:
        parser.error(str(exc))

    client = kfp.Client(host=args.host, namespace=args.namespace)

    print(f"Submitting: {args.pipeline_file}")
    print(f"Experiment: {args.experiment_name}")
    if args.run_name:
        print(f"Run name:   {args.run_name}")
    if params:
        print(f"Parameters: {params}")

    run_result = client.create_run_from_pipeline_package(
        pipeline_file=args.pipeline_file,
        arguments=params or None,
        run_name=args.run_name,
        experiment_name=args.experiment_name,
        namespace=args.namespace,
    )

    print(f"\nRun submitted! ID: {run_result.run_id}")
    print(f"View at: http://localhost:30502/#/runs/details/{run_result.run_id}")


if __name__ == "__main__":
    main()
