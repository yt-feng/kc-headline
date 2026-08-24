# Architecture

## Purpose

This repository is a generic control plane for one scheduled batch job. The
public files describe only the execution boundary. Application logic,
configuration, state, and generated content are stored as encrypted data.

## Components

1. **Scheduler** — a GitHub Actions workflow starts the job on a weekly cadence
   or through an explicit manual dispatch.
2. **Encrypted payload** — `payload/runtime.enc` contains the private runtime.
   No decrypted copy is committed to Git history.
3. **Ephemeral workspace** — the payload is decrypted only inside the runner's
   temporary directory and removed with the hosted runner.
4. **Secret boundary** — decryption material and service credentials are read
   only from GitHub Actions Secrets. Local copies remain outside this repository.
5. **Encrypted result** — deliverables and diagnostic data are encrypted before
   they are uploaded as workflow artifacts.
6. **Encrypted state** — successful scheduled executions can replace the
   encrypted payload with its updated state. Plaintext state is never committed.

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
encrypt result + updated runtime state
          |
          v
commit opaque state blob and upload encrypted artifact
```

## Public Information Policy

- Documentation and workflow labels remain generic and English-only.
- No credentials, source lists, prompts, business terminology, or generated
  content are stored in plaintext.
- Workflow logs expose only generic lifecycle messages.
- Artifacts are treated as publicly downloadable and are therefore encrypted
  before upload.
- The scheduled workflow uses a standard hosted runner and does not run on push.

## Recovery

The runtime updates its encrypted state only after successful private
validation. A failed execution leaves the committed blob unchanged. Encrypted
diagnostics can be downloaded and decrypted locally for investigation.
