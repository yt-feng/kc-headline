# Architecture

## Purpose

This repository is a generic control plane for one scheduled batch job. The
public files describe only the execution boundary. Application logic,
configuration, state, and generated content are stored as encrypted data.

## Components

1. **Scheduler** — a GitHub Actions workflow starts the job on a weekly cadence
   or through an explicit manual dispatch.
2. **Authenticated encrypted payload** — `payload/runtime.enc` contains the
   private runtime. No decrypted copy is committed to Git history.
3. **Ephemeral workspace** — the payload is decrypted only inside the runner's
   temporary directory and removed with the hosted runner.
4. **Secret boundary** — decryption material and service credentials are read
   only from GitHub Actions Secrets. Local copies remain outside this repository.
5. **Encrypted result** — deliverables and diagnostic data are encrypted before
   they are uploaded as workflow artifacts.
6. **Write-isolated state** — the secret-bearing execution job has read-only
   repository access. A separate job receives only authenticated ciphertext and
   may replace the opaque payload after a successful scheduled execution.

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
execute private entrypoint with secret injection
          |
          +---- failure ---> encrypt diagnostics ---> short-lived artifact
          |
          v
validate private result
          |
          +---- preview ---> encrypt result ---> short-lived artifact
          |
          v
authenticate and encrypt result + updated runtime state
          |
          v
upload encrypted artifact and hand opaque state to a write-only job
```

## Public Information Policy

- Documentation and workflow labels remain generic and English-only.
- No credentials, domain inputs, private configuration, or output are stored in
  plaintext.
- Workflow logs expose only generic lifecycle messages.
- Artifacts are treated as publicly downloadable and are therefore encrypted
  before upload.
- Manual dispatch is preview-only and cannot update repository state.
- The scheduled workflow uses a standard hosted runner and does not run on push.

## Recovery

The runtime updates its encrypted state only after successful private
validation, authenticated-encryption round-trip verification, and a base-revision
check in the write-isolated job. A failed execution leaves the committed blob
unchanged. Encrypted diagnostics can be downloaded and decrypted locally for
investigation.
