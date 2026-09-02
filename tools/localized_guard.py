"""Validate the independent localized batch without loading its private runtime."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path


class ContractError(RuntimeError):
    pass


STATE_PATH = "payload/localized.enc"
CONTROL_PATHS = (
    ".github/workflows/localized.yml",
    "tools/localized_guard.py",
    "tools/envelope.py",
    "tools/archive_guard.py",
    "tools/secret_guard.py",
    STATE_PATH,
)
ENGLISH_PATHS = (".github/workflows/batch.yml", "tools/workflow_guard.py", "payload/runtime.enc")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def digest(path: Path) -> str:
    require(path.is_file() and not path.is_symlink(), "Invalid regular file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict:
    require(path.is_file() and not path.is_symlink(), "Invalid metadata file")
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), "Invalid metadata object")
    return value


def date_value(value: str) -> dt.date:
    require(isinstance(value, str), "Invalid publication date")
    parsed = dt.date.fromisoformat(value)
    require(parsed.isoformat() == value and parsed.weekday() == 4, "Invalid publication date")
    return parsed


def fixed_files(root: Path, names: set[str]) -> None:
    require(root.is_dir() and not root.is_symlink(), "Invalid handoff directory")
    members = list(root.iterdir())
    require({path.name for path in members} == names, "Unexpected handoff paths")
    require(all(path.is_file() and not path.is_symlink() and path.stat().st_size for path in members),
            "Invalid handoff files")


def output_paths(value: str) -> tuple[str, str]:
    date_value(value)
    return (f"output/zh/{value}.docx", f"output/zh/{value}.pdf")


def validate_documents(document: Path, pdf: Path) -> None:
    require(zipfile.is_zipfile(document), "Invalid document container")
    with zipfile.ZipFile(document) as archive:
        require({"[Content_Types].xml", "word/document.xml"}.issubset(archive.namelist()),
                "Incomplete document container")
        require(archive.testzip() is None, "Corrupt document container")
    with pdf.open("rb") as handle:
        require(handle.read(5) == b"%PDF-", "Invalid portable document")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    require(result.returncode == 0, "Repository validation failed")
    return result.stdout.decode("utf-8").strip()


def git_bytes(repo: Path, spec: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), "show", spec], capture_output=True, check=False)
    require(result.returncode == 0, "Repository file validation failed")
    return result.stdout


def emit_output(path: Path, **values: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            require(re.fullmatch(r"[A-Za-z0-9_.-]+", value) is not None, "Invalid job output")
            handle.write(f"{key}={value}\n")


def validate_workflow(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    require(not re.search(r"[\u3400-\u9fff]", content), "Workflow labels must remain generic")
    require(content.count("\njobs:\n") == 1, "Unexpected workflow structure")
    header, jobs = content.split("\njobs:\n", 1)
    require("secrets." not in header, "Global secrets are unavailable")
    require(re.findall(r"^  ([a-z_]+):$", jobs, re.MULTILINE) == ["execute", "persist"],
            "Unexpected workflow job set")
    require("\n  persist:\n" in content, "Missing persistence job")
    execute, persist = content.split("\n  persist:\n", 1)
    require(not any(value in content for value in (
        "DELIVERY_", "kc_headline.delivery", "tools.private_batch", "payload/runtime.enc", "GateX",
    )), "Downstream source contract must remain isolated")
    require(set(re.findall(r"secrets\.([A-Z_]+)", execute)) == {"BUNDLE_KEY", "SERVICE_TOKEN"},
            "Unexpected execution secret set")
    require("secrets." not in persist, "Persistence job must be secret-free")
    require(all(value in execute for value in (
        "permissions: {}", "group: scheduled-batch-main", "queue: max", "contents: read", "persist-credentials: false",
        "default: preview", "recover-missed-schedule", "python -m tools.chinese_batch",
        "--input payload/localized.enc", "--environment SERVICE_TOKEN", "localized-result-",
    )), "Incomplete execution boundary")
    require("cancel-in-progress:" not in content, "Batch runs must not cancel another publication")
    require(not any(value in execute for value in (
        "contents: write", "persist-credentials: true", "git push", "git commit",
    )), "Execution job must not write to the repository")
    require(all(value in persist for value in (
        "contents: write", "actions: read", "localized-handoff-", "HANDOFF_DIGEST:",
        "python tools/localized_guard.py persist", "python tools/localized_guard.py staged",
        "python tools/localized_guard.py rebase-check", "python tools/localized_guard.py committed",
        'git add payload/localized.enc "output/zh/$OUTPUT_DATE.docx" "output/zh/$OUTPUT_DATE.pdf"',
        "git rebase origin/main", "git push origin HEAD:main",
    )), "Incomplete persistence boundary")


def validate_plan(args: argparse.Namespace) -> None:
    data = read_json(args.plan)
    state = read_json(args.state)
    require(set(data) == {"status", "mode", "publication_date", "already_published"},
            "Unexpected plan schema")
    require(data["status"] == "ok" and data["mode"] == "plan", "Invalid plan status")
    value, published = data["publication_date"], data["already_published"]
    parsed = date_value(value)
    require(isinstance(published, bool), "Invalid plan values")
    require(not args.requested_date or args.requested_date == value, "Plan date mismatch")
    require(args.event in {"schedule", "workflow_dispatch"}, "Unsupported workflow event")
    require(args.operation in {"preview", "recover-missed-schedule"}, "Unknown manual operation")
    persist = args.event == "schedule" or args.operation == "recover-missed-schedule"
    if args.event == "schedule":
        require(not args.requested_date, "Scheduled date overrides are unavailable")
    elif args.operation == "recover-missed-schedule":
        require(bool(args.requested_date), "An explicit recovery date is required")
        require(state.get("schema_version") == 1 and isinstance(state.get("last_published"), dict),
                "Invalid persisted state")
        last = date_value(state["last_published"].get("publication_date"))
        require(parsed in {last, last + dt.timedelta(days=7)}, "Recovery must use current or next Friday")
        require(parsed <= dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date(),
                "Recovery date is in the future")
    document_path, pdf_path = (args.repo / path for path in output_paths(value))
    exists = []
    for path in (document_path, pdf_path):
        present = path.exists() or path.is_symlink()
        require(not present or (path.is_file() and not path.is_symlink()), "Invalid planned output path")
        exists.append(present)
    should_persist, run_batch = persist, True
    if persist:
        require(exists[0] == exists[1], "The scheduled output pair is incomplete")
        require(published == all(exists), "Persisted state and scheduled output differ")
        if all(exists):
            git(args.repo, "ls-files", "--error-unmatch", *output_paths(value))
            should_persist, run_batch = False, False
    emit_output(args.github_output, output_date=value, persist_requested=str(persist).lower(),
                should_persist=str(should_persist).lower(), run_batch=str(run_batch).lower())


def validate_export(root: Path, next_runtime: Path, value: str) -> None:
    parsed = date_value(value)
    names = {f"{value}.docx", f"{value}.pdf", "manifest.json", "checksums.sha256"}
    fixed_files(root, names)
    manifest = read_json(root / "manifest.json")
    require(manifest.get("status") == "validated", "Export is not validated")
    pipeline = manifest.get("pipeline")
    require(isinstance(pipeline, dict) and pipeline.get("mode") == "chinese"
            and pipeline.get("language") == "zh-CN", "Unexpected localized pipeline")
    issue = manifest.get("issue")
    require(isinstance(issue, dict), "Missing issue metadata")
    identity = {"publication_date": value, "year": parsed.year,
                "annual_issue": issue.get("annual_issue"), "total_issue": issue.get("total_issue")}
    require(all(isinstance(identity[key], int) and not isinstance(identity[key], bool)
                and identity[key] > 0 for key in ("annual_issue", "total_issue")), "Invalid issue identity")
    require(all(issue.get(key) == expected for key, expected in identity.items()), "Issue identity mismatch")
    checksums = {}
    lines = (root / "checksums.sha256").read_text(encoding="utf-8").splitlines()
    require(len(lines) == 3, "Invalid checksum entry count")
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\]+)", line)
        require(match is not None and match[2] not in checksums, "Invalid checksum entry")
        checksums[match[2]] = match[1]
    require(set(checksums) == names - {"checksums.sha256"}, "Checksum path set mismatch")
    require(all(digest(root / name) == expected for name, expected in checksums.items()), "Checksum mismatch")
    state = read_json(next_runtime / "state/issue.json")
    last = state.get("last_published")
    require(state.get("schema_version") == 1 and isinstance(last, dict)
            and all(last.get(key) == expected for key, expected in identity.items()), "Persisted state mismatch")
    index = read_json(next_runtime / "reports/index.json")
    publications = index.get("publications")
    require(index.get("schema_version") == 1 and isinstance(publications, list) and bool(publications),
            "Invalid persisted index")
    matching = [row for row in publications if isinstance(row, dict) and row.get("publication_date") == value]
    require(len(matching) == 1 and publications[-1] is matching[0], "Persisted index date mismatch")
    row = matching[0]
    require(all(row.get(key) == expected for key, expected in identity.items())
            and row.get("status") == "private-exported", "Persisted index identity mismatch")
    delivery = row.get("delivery")
    require(isinstance(delivery, dict) and delivery.get("type") == "job-export"
            and delivery.get("manifest") == "manifest.json"
            and delivery.get("manifest_sha256") == checksums["manifest.json"], "Persisted index manifest mismatch")
    for name in (".runtime-ready", "pyproject.toml", "requirements.txt", "tools/__init__.py",
                 "tools/chinese_batch.py", "tools/prepare_system.sh"):
        path = next_runtime / name
        require(path.is_file() and not path.is_symlink(), "Incomplete next runtime")
    validate_documents(root / f"{value}.docx", root / f"{value}.pdf")


def validate_context(base: str, value: str, run_id: str, attempt: str) -> None:
    require(re.fullmatch(r"[0-9a-f]{40}", base) is not None, "Invalid handoff base revision")
    date_value(value)
    require(re.fullmatch(r"[1-9][0-9]*", run_id) is not None
            and re.fullmatch(r"[1-9][0-9]*", attempt) is not None, "Invalid workflow run identity")


def create_handoff(args: argparse.Namespace) -> None:
    base = git(args.repo, "rev-parse", "HEAD")
    validate_context(base, args.output_date, args.run_id, args.run_attempt)
    validate_export(args.export, args.next_runtime, args.output_date)
    require(not args.handoff.is_symlink(), "Invalid handoff directory")
    args.handoff.mkdir(parents=True, exist_ok=True)
    require(not any(args.handoff.iterdir()), "Handoff directory is not empty")
    require(args.ciphertext.read_bytes().startswith(b"BATCHENV2"), "Invalid next runtime envelope")
    shutil.copyfile(args.ciphertext, args.handoff / "localized.enc")
    for extension in ("docx", "pdf"):
        name = f"{args.output_date}.{extension}"
        shutil.copyfile(args.export / name, args.handoff / name)
    metadata = {"schema_version": 1, "workflow": "localized.yml", "base_sha": base,
                "run_id": args.run_id, "run_attempt": args.run_attempt,
                "publication_date": args.output_date,
                "files": {path.name: digest(path) for path in args.handoff.iterdir()}}
    manifest = args.handoff / "handoff.json"
    manifest.write_text(json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")
    emit_output(args.github_output, base_sha=base, handoff_digest=digest(manifest))


def validate_handoff(args: argparse.Namespace) -> dict[str, str]:
    validate_context(args.base_sha, args.output_date, args.run_id, args.run_attempt)
    require(re.fullmatch(r"[0-9a-f]{64}", args.handoff_digest) is not None, "Invalid handoff digest")
    names = {"localized.enc", f"{args.output_date}.docx", f"{args.output_date}.pdf"}
    fixed_files(args.handoff, names | {"handoff.json"})
    require(digest(args.handoff / "handoff.json") == args.handoff_digest, "Handoff metadata digest mismatch")
    metadata = read_json(args.handoff / "handoff.json")
    expected = {"schema_version": 1, "workflow": "localized.yml", "base_sha": args.base_sha,
                "run_id": args.run_id, "run_attempt": args.run_attempt, "publication_date": args.output_date}
    require(set(metadata) == set(expected) | {"files"}
            and all(metadata.get(key) == value for key, value in expected.items()), "Handoff context mismatch")
    files = metadata["files"]
    require(isinstance(files, dict) and set(files) == names, "Handoff file set mismatch")
    require(all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                and digest(args.handoff / name) == value for name, value in files.items()), "Handoff digest mismatch")
    require((args.handoff / "localized.enc").read_bytes().startswith(b"BATCHENV2"), "Invalid state envelope")
    validate_documents(args.handoff / f"{args.output_date}.docx", args.handoff / f"{args.output_date}.pdf")
    return {STATE_PATH if name == "localized.enc" else f"output/zh/{name}": value for name, value in files.items()}


def validate_revision(repo: Path, base: str, target: str, value: str) -> None:
    require(re.fullmatch(r"[0-9a-f]{40}", base) is not None, "Invalid base revision")
    require(target in {"HEAD", "origin/main"}, "Invalid target revision")
    git(repo, "merge-base", "--is-ancestor", base, target)
    require(not git(repo, "diff", "--name-only", base, target, "--", *CONTROL_PATHS),
            "Localized control plane or state changed during execution")
    for path in output_paths(value):
        require(not git(repo, "ls-tree", "--name-only", target, "--", path), "Scheduled output already exists")


def persist_files(args: argparse.Namespace) -> None:
    expected = validate_handoff(args)
    require(not git(args.repo, "status", "--porcelain=v1", "--untracked-files=all"), "Repository is not clean")
    validate_revision(args.repo, args.base_sha, "HEAD", args.output_date)
    for parent in (args.repo / "payload", args.repo / "output", args.repo / "output/zh"):
        require(not parent.is_symlink() and (not parent.exists() or parent.is_dir()), "Invalid output directory")
    (args.repo / "output/zh").mkdir(parents=True, exist_ok=True)
    for path in output_paths(args.output_date):
        destination = args.repo / path
        require(not destination.exists() and not destination.is_symlink(), "Scheduled output already exists")
    require(not (args.repo / STATE_PATH).is_symlink(), "Invalid state path")
    require(digest(args.repo / STATE_PATH) != expected[STATE_PATH], "Opaque state did not change")
    for path, value in expected.items():
        destination = args.repo / path
        shutil.copyfile(args.handoff / destination.name, destination)
        require(digest(destination) == value, "Persisted file digest mismatch")
    status = git(args.repo, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
    # git().strip() removes the first status line's leading whitespace.
    require(sorted(line.lstrip() for line in status) == sorted([
        f"M {STATE_PATH}", *(f"?? {path}" for path in output_paths(args.output_date)),
    ]), "Unexpected control-plane change")


def validate_changes(args: argparse.Namespace, committed: bool = False) -> None:
    expected = validate_handoff(args)
    revision_args = ["HEAD^", "HEAD"] if committed else ["--cached"]
    actual = git(args.repo, "diff", *revision_args, "--name-status", "--no-renames").splitlines()
    wanted = [f"M\t{STATE_PATH}", *(f"A\t{path}" for path in output_paths(args.output_date))]
    require(sorted(actual) == sorted(wanted), "Unexpected persisted change set")
    for path, value in expected.items():
        prefix = "HEAD:" if committed else ":"
        require(hashlib.sha256(git_bytes(args.repo, prefix + path)).hexdigest() == value,
                "Persisted blob digest mismatch")
    if committed:
        require(not git(args.repo, "status", "--porcelain=v1", "--untracked-files=all"), "Repository is not clean")


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="localized-guard")
    commands = value.add_subparsers(dest="command", required=True)
    workflow = commands.add_parser("workflow")
    workflow.add_argument("--workflow", type=Path, required=True)
    isolation = commands.add_parser("isolation")
    isolation.add_argument("--repo", type=Path, required=True)
    isolation.add_argument("--base-sha", required=True)
    plan = commands.add_parser("plan")
    for name in ("plan", "state", "repo", "github-output"):
        plan.add_argument(f"--{name}", type=Path, required=True)
    for name in ("event", "operation", "requested-date"):
        plan.add_argument(f"--{name}", required=True)
    handoff = commands.add_parser("handoff")
    for name in ("repo", "export", "next-runtime", "ciphertext", "handoff", "github-output"):
        handoff.add_argument(f"--{name}", type=Path, required=True)
    for name in ("output-date", "run-id", "run-attempt"):
        handoff.add_argument(f"--{name}", required=True)
    for command in ("persist", "staged", "committed"):
        stage = commands.add_parser(command)
        for name in ("repo", "handoff"):
            stage.add_argument(f"--{name}", type=Path, required=True)
        for name in ("base-sha", "output-date", "run-id", "run-attempt", "handoff-digest"):
            stage.add_argument(f"--{name}", required=True)
    revision = commands.add_parser("rebase-check")
    revision.add_argument("--repo", type=Path, required=True)
    for name in ("base-sha", "target", "output-date"):
        revision.add_argument(f"--{name}", required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "workflow":
            validate_workflow(args.workflow)
        elif args.command == "isolation":
            require(re.fullmatch(r"[0-9a-f]{40}", args.base_sha) is not None, "Invalid baseline revision")
            require(not git(args.repo, "diff", args.base_sha, "--", *ENGLISH_PATHS),
                    "English source files changed")
        elif args.command == "plan":
            validate_plan(args)
        elif args.command == "handoff":
            create_handoff(args)
        elif args.command == "persist":
            persist_files(args)
        elif args.command in {"staged", "committed"}:
            validate_changes(args, committed=args.command == "committed")
        else:
            validate_revision(args.repo, args.base_sha, args.target, args.output_date)
    except (ContractError, OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile) as exc:
        # Do not print decrypted JSON, exception details, paths, or subprocess output.
        message = str(exc) if isinstance(exc, ContractError) else "Localized contract validation failed"
        raise SystemExit(message) from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
