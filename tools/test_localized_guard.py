from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.localized_guard import (
    CONTROL_PATHS,
    ContractError,
    create_handoff,
    digest,
    persist_files,
    validate_changes,
    validate_export,
    validate_handoff,
    validate_plan,
    validate_revision,
    validate_workflow,
)


DATE = "2026-08-28"
PROJECT = Path(__file__).resolve().parents[1]


class LocalizedGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.run_git("init", "-q")
        self.run_git("config", "user.name", "Fixture")
        self.run_git("config", "user.email", "fixture@example.invalid")
        for name in (*CONTROL_PATHS, ".github/workflows/batch.yml", "payload/runtime.enc"):
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture original\n", encoding="utf-8")
        self.run_git("add", ".")
        self.run_git("commit", "-qm", "fixture base")
        self.base = self.run_git("rev-parse", "HEAD")
        self.export = self.root / "export"
        self.export.mkdir()
        self.next_runtime = self.root / "next"
        self.next_runtime.mkdir()
        self.create_export()
        self.ciphertext = self.root / "localized.next.enc"
        self.ciphertext.write_bytes(b"BATCHENV2" + b"new encrypted fixture" * 4)
        self.handoff = self.root / "handoff"
        self.outputs = self.root / "outputs.txt"

    def run_git(self, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, check=True)
        return result.stdout.decode().strip()

    def create_export(self) -> None:
        with zipfile.ZipFile(self.export / f"{DATE}.docx", "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<document/>")
        (self.export / f"{DATE}.pdf").write_bytes(b"%PDF-1.4\nfixture")
        self.identity = {"publication_date": DATE, "year": 2026, "annual_issue": 31, "total_issue": 127}
        self.manifest = {"status": "validated", "issue": self.identity,
                         "pipeline": {"mode": "chinese", "language": "zh-CN"}}
        self.write_manifest()
        state = self.next_runtime / "state/issue.json"
        state.parent.mkdir()
        state.write_text(json.dumps({"schema_version": 1, "last_published": self.identity}))
        index = self.next_runtime / "reports/index.json"
        index.parent.mkdir()
        index.write_text(json.dumps({"schema_version": 1, "publications": [{
            **self.identity, "status": "private-exported", "delivery": {
                "type": "job-export", "manifest": "manifest.json",
                "manifest_sha256": digest(self.export / "manifest.json"),
            },
        }]}))
        for name in (".runtime-ready", "pyproject.toml", "requirements.txt", "tools/__init__.py",
                     "tools/chinese_batch.py", "tools/prepare_system.sh"):
            path = self.next_runtime / name
            path.parent.mkdir(exist_ok=True)
            path.write_text("fixture\n")

    def write_manifest(self) -> None:
        (self.export / "manifest.json").write_text(json.dumps(self.manifest))
        (self.export / "checksums.sha256").write_text("".join(
            f"{digest(self.export / name)}  {name}\n"
            for name in (f"{DATE}.docx", f"{DATE}.pdf", "manifest.json")
        ))

    def handoff_args(self) -> argparse.Namespace:
        return argparse.Namespace(repo=self.repo, export=self.export, next_runtime=self.next_runtime,
                                  ciphertext=self.ciphertext, handoff=self.handoff, github_output=self.outputs,
                                  output_date=DATE, run_id="12345", run_attempt="2")

    def ready_handoff(self) -> argparse.Namespace:
        args = self.handoff_args()
        create_handoff(args)
        args.base_sha = self.base
        args.handoff_digest = digest(self.handoff / "handoff.json")
        return args

    def test_current_workflow_contract(self) -> None:
        validate_workflow(PROJECT / ".github/workflows/localized.yml")

    def test_workflow_rejects_destination_secret(self) -> None:
        path = self.root / "bad.yml"
        path.write_text((PROJECT / ".github/workflows/localized.yml").read_text()
                        + "\n# DELIVERY_BEARER_TOKEN\n")
        with self.assertRaises(ContractError):
            validate_workflow(path)

    def test_workflow_rejects_secret_in_write_job(self) -> None:
        path = self.root / "bad.yml"
        path.write_text((PROJECT / ".github/workflows/localized.yml").read_text()
                        + "\n    env:\n      KEY: ${{ secrets.BUNDLE_KEY }}\n")
        with self.assertRaisesRegex(ContractError, "secret-free"):
            validate_workflow(path)

    def test_workflows_share_non_cancelling_publication_queue(self) -> None:
        for name in ("batch.yml", "localized.yml"):
            workflow = (PROJECT / ".github/workflows" / name).read_text()
            self.assertIn("group: scheduled-batch-main\n  queue: max", workflow)
            self.assertNotIn("cancel-in-progress:", workflow)
        path = self.root / "bad.yml"
        path.write_text((PROJECT / ".github/workflows/localized.yml").read_text()
                        .replace("group: scheduled-batch-main", "group: localized-batch-main"))
        with self.assertRaisesRegex(ContractError, "Incomplete execution boundary"):
            validate_workflow(path)

    def test_workflow_rejects_additional_job(self) -> None:
        path = self.root / "bad.yml"
        path.write_text((PROJECT / ".github/workflows/localized.yml").read_text()
                        .replace("jobs:\n", "jobs:\n  unexpected:\n    runs-on: ubuntu-24.04\n"))
        with self.assertRaisesRegex(ContractError, "Unexpected workflow job set"):
            validate_workflow(path)

    def test_complete_handoff_and_fixed_commit_preserve_source_files(self) -> None:
        args = self.ready_handoff()
        source_before = digest(self.repo / "payload/runtime.enc")
        persist_files(args)
        self.run_git("add", "payload/localized.enc", f"output/zh/{DATE}.docx", f"output/zh/{DATE}.pdf")
        validate_changes(args)
        self.run_git("commit", "-qm", "fixture localized publication")
        validate_changes(args, committed=True)
        self.assertEqual(digest(self.repo / "payload/runtime.enc"), source_before)
        self.assertEqual(self.run_git("diff", self.base, "--", ".github/workflows/batch.yml"), "")

    def test_handoff_rejects_extra_file(self) -> None:
        args = self.ready_handoff()
        (self.handoff / "extra.txt").write_text("unexpected")
        with self.assertRaisesRegex(ContractError, "Unexpected handoff paths"):
            validate_handoff(args)

    def test_handoff_rejects_symlink(self) -> None:
        args = self.ready_handoff()
        target = self.handoff / f"{DATE}.pdf"
        target.unlink()
        target.symlink_to(self.export / f"{DATE}.pdf")
        with self.assertRaisesRegex(ContractError, "Invalid handoff files"):
            validate_handoff(args)

    def test_handoff_rejects_another_attempt(self) -> None:
        args = self.ready_handoff()
        args.run_attempt = "3"
        with self.assertRaisesRegex(ContractError, "Handoff context mismatch"):
            validate_handoff(args)

    def test_handoff_rejects_modified_file(self) -> None:
        args = self.ready_handoff()
        with (self.handoff / f"{DATE}.pdf").open("ab") as handle:
            handle.write(b"changed")
        with self.assertRaisesRegex(ContractError, "Handoff digest mismatch"):
            validate_handoff(args)

    def test_handoff_rejects_modified_metadata(self) -> None:
        args = self.ready_handoff()
        path = self.handoff / "handoff.json"
        path.write_text(path.read_text() + " ")
        with self.assertRaisesRegex(ContractError, "metadata digest mismatch"):
            validate_handoff(args)

    def test_export_rejects_other_pipeline(self) -> None:
        self.manifest["pipeline"]["mode"] = "source-digest"
        self.write_manifest()
        with self.assertRaisesRegex(ContractError, "Unexpected localized pipeline"):
            validate_export(self.export, self.next_runtime, DATE)

    def test_export_rejects_mismatched_persisted_state(self) -> None:
        path = self.next_runtime / "state/issue.json"
        data = json.loads(path.read_text())
        data["last_published"]["total_issue"] += 1
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ContractError, "Persisted state mismatch"):
            validate_export(self.export, self.next_runtime, DATE)

    def test_unrelated_source_commit_is_accepted(self) -> None:
        args = self.ready_handoff()
        (self.repo / "payload/runtime.enc").write_text("new source state")
        self.run_git("add", "payload/runtime.enc")
        self.run_git("commit", "-qm", "fixture source publication")
        validate_revision(self.repo, self.base, "HEAD", DATE)
        persist_files(args)
        self.assertEqual((self.repo / "payload/runtime.enc").read_text(), "new source state")

    def test_post_commit_rebase_preserves_unrelated_source_update(self) -> None:
        args = self.ready_handoff()
        persist_files(args)
        self.run_git("add", "payload/localized.enc", f"output/zh/{DATE}.docx", f"output/zh/{DATE}.pdf")
        self.run_git("commit", "-qm", "fixture localized publication")
        self.run_git("branch", "localized-fixture")
        self.run_git("checkout", "-qb", "source-fixture", self.base)
        (self.repo / "payload/runtime.enc").write_text("independent source update")
        self.run_git("add", "payload/runtime.enc")
        self.run_git("commit", "-qm", "fixture source update")
        self.run_git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.run_git("checkout", "-q", "localized-fixture")
        validate_revision(self.repo, self.base, "origin/main", DATE)
        self.run_git("rebase", "origin/main")
        validate_changes(args, committed=True)
        self.assertEqual((self.repo / "payload/runtime.enc").read_text(), "independent source update")

    def test_localized_state_change_is_rejected(self) -> None:
        args = self.ready_handoff()
        (self.repo / "payload/localized.enc").write_text("other localized state")
        self.run_git("add", "payload/localized.enc")
        self.run_git("commit", "-qm", "fixture conflicting state")
        with self.assertRaisesRegex(ContractError, "Localized control plane or state changed"):
            persist_files(args)

    def test_existing_pair_is_never_replaced(self) -> None:
        args = self.ready_handoff()
        output = self.repo / "output/zh"
        output.mkdir(parents=True)
        (output / f"{DATE}.docx").write_text("existing")
        (output / f"{DATE}.pdf").write_text("existing")
        self.run_git("add", "output/zh")
        self.run_git("commit", "-qm", "fixture existing pair")
        with self.assertRaisesRegex(ContractError, "Scheduled output already exists"):
            persist_files(args)
        self.assertEqual((output / f"{DATE}.docx").read_text(), "existing")

    def test_extra_staged_english_change_is_rejected(self) -> None:
        args = self.ready_handoff()
        persist_files(args)
        (self.repo / "payload/runtime.enc").write_text("must not be staged")
        self.run_git("add", ".")
        with self.assertRaisesRegex(ContractError, "Unexpected persisted change set"):
            validate_changes(args)

    def plan_args(self, *, published: bool = False, operation: str = "preview") -> argparse.Namespace:
        plan = self.root / "plan.json"
        plan.write_text(json.dumps({"status": "ok", "mode": "plan", "publication_date": DATE,
                                    "already_published": published}))
        state = self.root / "state.json"
        state.write_text(json.dumps({"schema_version": 1,
                                     "last_published": {"publication_date": "2026-08-21"}}))
        return argparse.Namespace(plan=plan, state=state, repo=self.repo, github_output=self.outputs,
                                  event="workflow_dispatch", operation=operation, requested_date=DATE)

    def test_preview_never_requests_persistence(self) -> None:
        validate_plan(self.plan_args())
        self.assertIn("persist_requested=false\n", self.outputs.read_text())
        self.assertIn("should_persist=false\n", self.outputs.read_text())
        self.assertIn("run_batch=true\n", self.outputs.read_text())

    def test_recovery_accepts_next_friday(self) -> None:
        validate_plan(self.plan_args(operation="recover-missed-schedule"))
        self.assertIn("should_persist=true\n", self.outputs.read_text())

    def test_recovery_requires_explicit_date(self) -> None:
        args = self.plan_args(operation="recover-missed-schedule")
        args.requested_date = ""
        with self.assertRaisesRegex(ContractError, "explicit recovery date"):
            validate_plan(args)

    def test_recovery_rejects_skipped_state(self) -> None:
        args = self.plan_args(operation="recover-missed-schedule")
        args.state.write_text(json.dumps({"schema_version": 1,
                                         "last_published": {"publication_date": "2026-08-14"}}))
        with self.assertRaisesRegex(ContractError, "current or next Friday"):
            validate_plan(args)

    def test_published_pair_is_a_no_op(self) -> None:
        args = self.plan_args(published=True, operation="recover-missed-schedule")
        args.state.write_text(json.dumps({"schema_version": 1, "last_published": {"publication_date": DATE}}))
        output = self.repo / "output/zh"
        output.mkdir(parents=True)
        (output / f"{DATE}.docx").write_text("existing")
        (output / f"{DATE}.pdf").write_text("existing")
        self.run_git("add", "output/zh")
        validate_plan(args)
        self.assertIn("should_persist=false\n", self.outputs.read_text())
        self.assertIn("run_batch=false\n", self.outputs.read_text())


if __name__ == "__main__":
    unittest.main()
