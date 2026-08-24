from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

MAGIC = b"BATCHENV2"
SALT_BYTES = 16
IV_BYTES = 16
TAG_BYTES = 32
CONTEXTS = ("runtime", "result")


class EnvelopeError(RuntimeError):
    pass


def _master_key(key_file: Path) -> bytes:
    try:
        value = key_file.read_bytes()
    except OSError as exc:
        raise EnvelopeError("Envelope key is unavailable") from exc
    if value.endswith(b"\n"):
        value = value[:-1]
    if len(value) != 64 or re.fullmatch(rb"[0-9a-fA-F]{64}", value) is None:
        raise EnvelopeError("Envelope key is invalid")
    return bytes.fromhex(value.decode("ascii"))


def _derive_keys(master_key: bytes, salt: bytes, context: str) -> tuple[bytes, bytes]:
    prk = hmac.new(salt, master_key, hashlib.sha256).digest()
    info = b"batch-envelope:" + context.encode("ascii")
    output = b""
    previous = b""
    counter = 1
    while len(output) < 64:
        previous = hmac.new(prk, previous + info + bytes((counter,)), hashlib.sha256).digest()
        output += previous
        counter += 1
    return output[:32], output[32:64]


def _transform(value: bytes, encryption_key: bytes, iv: bytes, *, decrypting: bool) -> bytes:
    executable = shutil.which("openssl")
    if not executable:
        raise EnvelopeError("Envelope cipher is unavailable")
    command = [
        executable,
        "enc",
        "-aes-256-ctr",
        "-K",
        encryption_key.hex(),
        "-iv",
        iv.hex(),
    ]
    if decrypting:
        command.append("-d")
    completed = subprocess.run(command, input=value, capture_output=True, check=False)
    if completed.returncode != 0:
        raise EnvelopeError("Envelope cipher failed")
    return completed.stdout


def _atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def encrypt(input_path: Path, destination: Path, key_file: Path, context: str) -> None:
    try:
        plaintext = input_path.read_bytes()
    except OSError as exc:
        raise EnvelopeError("Envelope input is unavailable") from exc
    salt = os.urandom(SALT_BYTES)
    iv = os.urandom(IV_BYTES)
    encryption_key, authentication_key = _derive_keys(_master_key(key_file), salt, context)
    ciphertext = _transform(plaintext, encryption_key, iv, decrypting=False)
    authenticated = MAGIC + b"\0" + context.encode("ascii") + salt + iv + ciphertext
    tag = hmac.new(authentication_key, authenticated, hashlib.sha256).digest()
    _atomic_write(destination, MAGIC + salt + iv + ciphertext + tag)


def decrypt(input_path: Path, destination: Path, key_file: Path, context: str) -> None:
    try:
        envelope = input_path.read_bytes()
    except OSError as exc:
        raise EnvelopeError("Encrypted envelope is unavailable") from exc
    header_size = len(MAGIC) + SALT_BYTES + IV_BYTES
    if len(envelope) < header_size + TAG_BYTES or envelope[: len(MAGIC)] != MAGIC:
        raise EnvelopeError("Encrypted envelope is invalid")
    salt_start = len(MAGIC)
    iv_start = salt_start + SALT_BYTES
    ciphertext_start = iv_start + IV_BYTES
    salt = envelope[salt_start:iv_start]
    iv = envelope[iv_start:ciphertext_start]
    ciphertext = envelope[ciphertext_start:-TAG_BYTES]
    tag = envelope[-TAG_BYTES:]
    encryption_key, authentication_key = _derive_keys(_master_key(key_file), salt, context)
    authenticated = MAGIC + b"\0" + context.encode("ascii") + salt + iv + ciphertext
    expected_tag = hmac.new(authentication_key, authenticated, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        raise EnvelopeError("Encrypted envelope failed authentication")
    plaintext = _transform(ciphertext, encryption_key, iv, decrypting=True)
    _atomic_write(destination, plaintext)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="envelope")
    value.add_argument("operation", choices=("encrypt", "decrypt"))
    value.add_argument("--context", choices=CONTEXTS, required=True)
    value.add_argument("--key-file", type=Path, required=True)
    value.add_argument("--input", type=Path, required=True)
    value.add_argument("--output", type=Path, required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        if args.operation == "encrypt":
            encrypt(args.input, args.output, args.key_file, args.context)
        else:
            decrypt(args.input, args.output, args.key_file, args.context)
    except EnvelopeError as exc:
        raise SystemExit(str(exc)) from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
