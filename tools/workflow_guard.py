from __future__ import annotations

import argparse
from pathlib import Path


class WorkflowContractError(RuntimeError):
    pass


def validate_workflow(path: Path) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowContractError("workflow-unreadable") from exc

    forbidden = ("SERVICE_TOKEN", "DEEPSEEK_API_KEY")
    if any(value in content for value in forbidden):
        raise WorkflowContractError("model-service-secret-forbidden")
    required = (
        "- name: Execute private batch",
        "python -m tools.private_batch",
        "- name: Deliver persisted document",
        "DELIVERY_BEARER_TOKEN: ${{ secrets.DELIVERY_BEARER_TOKEN }}",
        'python -m pip install --disable-pip-version-check --quiet "$RUNNER_TEMP/delivery-runtime"',
        "--environment DELIVERY_BEARER_TOKEN",
    )
    if any(value not in content for value in required):
        raise WorkflowContractError("source-digest-workflow-incomplete")


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="workflow-guard")
    value.add_argument("--workflow", type=Path, required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        validate_workflow(args.workflow)
    except WorkflowContractError as exc:
        raise SystemExit(str(exc)) from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
