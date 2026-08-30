# Architecture

## Purpose

This repository is a generic control plane for one scheduled batch job. The
public files describe only the execution boundary. Application logic,
configuration and state are stored as encrypted data. Selected scheduled
deliverables are intentionally published under date-only names.

## Components

1. **Scheduler** — a GitHub Actions workflow starts the job on a weekly cadence
   or through an explicit manual dispatch.
2. **Authenticated encrypted payload** — `payload/runtime.enc` contains the
   private runtime. No decrypted copy is committed to Git history.
3. **Ephemeral workspace** — the payload is decrypted only inside the runner's
   temporary directory and removed with the hosted runner.
4. **Secret boundary** — decryption material and service credentials are read
   only from GitHub Actions Secrets. Local copies remain outside this repository.
5. **Encrypted run record** — the complete result and diagnostic data are
   encrypted before they are uploaded as workflow artifacts.
6. **Published output** — a successful scheduled execution contributes one
   validated document pair to `output/`, using `YYYY-MM-DD` filenames.
7. **Write-isolated persistence** — the secret-bearing execution job has
   read-only repository access. A separate job receives authenticated state and
   a validated output pair, then persists only those fixed paths.
8. **Post-persistence delivery** — a separate read-only job validates the
   committed pair and uses the encrypted runtime to perform one idempotent
   downstream handoff.

## Execution Flow

```text
schedule or manual dispatch
          |
          v
checkout public control plane
          |
          v
decrypt payload in runner temp space
          |
          v
plan the canonical date and inspect the matching repository paths
          |
          +---- complete pair + matching state ---> no-op
          |
          +---- partial or inconsistent pair ----> stop
          |
          v
execute private entrypoint with secret injection
          |
          +---- failure ---> encrypt diagnostics ---> short-lived artifact
          |
          v
validate private result
          |
          +---- preview ---> encrypt result ---> short-lived artifact ---> stop
          |
          v
authenticate updated state and validate scheduled output
          |
          v
bind paths and hashes to this run and its base revision
          |
          v
hand the fixed file set to a secret-free write job
          |
          v
commit updated state and date-named documents to main
          |
          v
validate committed pair and perform idempotent delivery
```

## Public Information Policy

- Documentation and workflow labels remain generic and English-only.
- No credentials, domain inputs, or private configuration are stored in
  plaintext.
- Only the intended date-named files in `output/` are published as generated
  content.
- Workflow logs expose only generic lifecycle messages.
- Diagnostic artifacts and complete run records are encrypted before upload.
- The scheduled handoff separates authenticated ciphertext from the two
  plaintext files intended for publication. Both artifacts are tied to the
  current run and retained for one day.
- Manual dispatch defaults to preview. The explicit recovery operation is
  limited to the current published Friday or the immediately following Friday.
- The scheduled workflow uses a standard hosted runner and does not run on push.

## Recovery

The runtime updates its encrypted state only after successful private
validation, authenticated-encryption round-trip verification, and a base-revision
check in the write-isolated job. The handoff is restricted to the current run,
attempt, exact file sets, date-only paths, and recorded SHA-256 values. Existing
date paths cannot be replaced. A failed or repeated execution leaves the
committed state and output unchanged. Encrypted diagnostics can be downloaded
and decrypted locally for investigation. A delivery can be retried against the
same date without replacing its committed files or creating a second logical
handoff.
