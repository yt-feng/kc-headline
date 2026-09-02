# Architecture

## Purpose

This repository is a generic control plane for two scheduled batch jobs. The
public files describe only the execution boundary. Application logic,
configuration and state are stored as encrypted data. Selected scheduled
deliverables are intentionally published under date-only names.

## Components

1. **Scheduler** — a GitHub Actions workflow starts the job on a weekly cadence
   or through an explicit manual dispatch.
2. **Authenticated encrypted payloads** — `payload/runtime.enc` contains the
   source-digest runtime and `payload/localized.enc` contains the localized
   runtime. Their publication states are independent. No decrypted copy is
   committed to Git history.
3. **Ephemeral workspace** — the payload is decrypted only inside the runner's
   temporary directory and removed with the hosted runner.
4. **Secret boundary** — decryption material and service credentials are read
   only from GitHub Actions Secrets. Local copies remain outside this repository.
5. **Encrypted run record** — the complete result and diagnostic data are
   encrypted before they are uploaded as workflow artifacts.
6. **Published output** — a successful scheduled execution contributes one
   validated document pair using `YYYY-MM-DD` filenames. The source digest
   remains in `output/`; the localized edition uses `output/zh/`.
7. **Write-isolated persistence** — the secret-bearing execution job has
   read-only repository access. A separate job receives authenticated state and
   a validated output pair, then persists only those fixed paths.
8. **Post-persistence delivery** — only the source-digest workflow has a separate
   read-only job that validates the committed pair and performs one idempotent
   downstream handoff. The localized workflow has no delivery job or destination
   credential and does not invoke the existing downstream integration.

## Parallel Output Contract

The established `.github/workflows/batch.yml`, its runtime payload, output paths,
and delivery behavior are unchanged. `.github/workflows/localized.yml` restores
the localized production path as a separate job at 11:43 UTC each Friday. Its
encrypted entrypoint implements the same plan, preview, and scheduled modes,
with its own issue state. It can recover an earlier missed issue without moving
the source digest's publication state.

Both workflows share the existing `scheduled-batch-main` concurrency group with
`queue: max` and no cancellation. Runs serialize so the localized writer cannot
invalidate the source workflow's exact-base-revision persistence check. This
shared scheduling lock does not share their state or change source behavior.

The public `tools/localized_guard.py` validates planning, the private export,
state/index agreement, document containers, and the write boundary using only
the Python standard library. The handoff contains exactly the next
`localized.enc`, one date-named document pair, and a small metadata file. The
metadata is bound to the workflow run, attempt, execution base revision, exact
paths, and SHA-256 digests, and its own digest is passed as a job output.

The localized write job never decrypts or executes a private runtime. It writes
only `payload/localized.enc` and the two planned `output/zh/` files, validating
both staged and committed bytes. An unrelated main-branch update can be rebased
only if all localized control files, its encrypted state, and the planned output
paths are unchanged since execution began. A changed localized state or existing
output stops the write. Git failures stop the job; no alternate transport or
network configuration is attempted.

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
source digest only: validate committed pair and perform idempotent delivery
```

## Public Information Policy

- Documentation and workflow labels remain generic and English-only.
- No credentials, domain inputs, or private configuration are stored in
  plaintext.
- Only the intended date-named files in `output/` and `output/zh/` are published
  as generated content.
- Workflow logs expose only generic lifecycle messages.
- Diagnostic artifacts and complete run records are encrypted before upload.
- Scheduled handoffs contain only authenticated ciphertext, the two plaintext
  files intended for publication, and fixed-path validation metadata where
  applicable. Handoff artifacts are tied to the current run and attempt and
  retained for one day.
- Manual dispatch defaults to preview. The explicit recovery operation is
  limited to the current published Friday or the immediately following Friday.
- Both scheduled workflows use standard hosted runners and do not run on push.

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
