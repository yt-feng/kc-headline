from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.workflow_guard import WorkflowContractError, validate_workflow


WORKFLOW = """
- name: Execute private batch
  run: python -m tools.private_batch
- name: Deliver persisted document
  env:
    DELIVERY_BEARER_TOKEN: ${{ secrets.DELIVERY_BEARER_TOKEN }}
  run: |
    python -m pip install --disable-pip-version-check --quiet "$RUNNER_TEMP/delivery-runtime"
    python tools/secret_guard.py --environment DELIVERY_BEARER_TOKEN
"""


class WorkflowGuardTests(unittest.TestCase):
    def _path(self, content: str) -> Path:
        temporary = tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False, encoding="utf-8")
        with temporary:
            temporary.write(content)
        self.addCleanup(Path(temporary.name).unlink, missing_ok=True)
        return Path(temporary.name)

    def test_accepts_source_digest_without_a_model_service_secret(self) -> None:
        validate_workflow(self._path(WORKFLOW))

    def test_rejects_a_reintroduced_model_service_secret(self) -> None:
        with self.assertRaisesRegex(WorkflowContractError, "model-service-secret-forbidden"):
            validate_workflow(self._path(WORKFLOW + "\nenv: SERVICE_TOKEN\n"))

    def test_requires_the_delivery_secret_boundary(self) -> None:
        with self.assertRaisesRegex(WorkflowContractError, "source-digest-workflow-incomplete"):
            validate_workflow(self._path(WORKFLOW.replace("--environment DELIVERY_BEARER_TOKEN", "deliver")))

    def test_requires_delivery_runtime_dependencies(self) -> None:
        with self.assertRaisesRegex(WorkflowContractError, "source-digest-workflow-incomplete"):
            validate_workflow(
                self._path(
                    WORKFLOW.replace(
                        'python -m pip install --disable-pip-version-check --quiet "$RUNNER_TEMP/delivery-runtime"',
                        "python -m kc_headline.delivery",
                    )
                )
            )


if __name__ == "__main__":
    unittest.main()
