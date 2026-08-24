from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path


class GuardError(RuntimeError):
    pass


def _contains_value(path: Path, value: bytes) -> bool:
    overlap = b""
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                combined = overlap + chunk
                if value in combined:
                    return True
                overlap = combined[-max(0, len(value) - 1) :]
    except OSError as exc:
        raise GuardError("Private material could not be inspected") from exc
    return False


def _files(root: Path):
    try:
        root_stat = root.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise GuardError("Private material could not be inspected") from exc
    if stat.S_ISLNK(root_stat.st_mode):
        raise GuardError("Private material has an unsupported entry")
    if stat.S_ISREG(root_stat.st_mode):
        yield root
        return
    if not stat.S_ISDIR(root_stat.st_mode):
        raise GuardError("Private material has an unsupported entry")
    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise GuardError("Private material could not be inspected") from exc
    for entry in entries:
        yield from _files(entry)


def ensure_absent(value: bytes, roots: list[Path]) -> None:
    if len(value) < 16 or any(byte <= 32 or byte == 127 for byte in value):
        raise GuardError("Inspection material is invalid")
    for root in roots:
        for path in _files(root):
            if _contains_value(path, value):
                raise GuardError("Private material failed persistence inspection")


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="secret-guard")
    value.add_argument("--environment", required=True)
    value.add_argument("--path", action="append", type=Path, required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    raw_value = os.environ.get(args.environment, "").encode()
    try:
        ensure_absent(raw_value, args.path)
    except GuardError as exc:
        raise SystemExit(str(exc)) from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
