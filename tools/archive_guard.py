from __future__ import annotations

import argparse
import tarfile
from pathlib import Path, PurePosixPath


class ArchiveError(RuntimeError):
    pass


def extract_archive(archive_path: Path, destination: Path) -> None:
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise ArchiveError("Archive destination is invalid")
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ArchiveError("Archive destination is not empty")
    resolved_destination = destination.resolve(strict=True)
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            if not members:
                raise ArchiveError("Archive is empty")
            extractable_members = []
            for member in members:
                if member.isdir() and member.name in {".", "./"}:
                    continue
                value = PurePosixPath(member.name)
                if (
                    not value.parts
                    or value.is_absolute()
                    or ".." in value.parts
                    or not (member.isfile() or member.isdir())
                ):
                    raise ArchiveError("Archive contains an unsafe entry")
                target = (resolved_destination / value).resolve()
                if target != resolved_destination and resolved_destination not in target.parents:
                    raise ArchiveError("Archive path escapes the destination")
                extractable_members.append(member)
            if not extractable_members:
                raise ArchiveError("Archive is empty")
            archive.extractall(resolved_destination, members=extractable_members, filter="data")
    except (OSError, tarfile.TarError) as exc:
        raise ArchiveError("Archive extraction failed") from exc


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="archive-guard")
    value.add_argument("--archive", type=Path, required=True)
    value.add_argument("--destination", type=Path, required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        extract_archive(args.archive, args.destination)
    except ArchiveError as exc:
        raise SystemExit(str(exc)) from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
