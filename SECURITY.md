# Security Boundary

Do not commit credentials, decrypted payloads, private input material, or local
diagnostic files. Runtime secrets belong in GitHub Actions Secrets and in an
ignored local secrets directory only.

The only generated plaintext permitted in the repository is the validated,
date-named document pair under `output/` or `output/zh/`. Keep private values out
of filenames, paths, commit messages, workflow labels, and logs.

If plaintext is ever committed, rotate the affected credentials and rebuild the
repository history before continuing scheduled execution.

The encrypted runtime and its pinned dependency metadata are trusted inputs.
The write-isolated job has no runtime secrets. It receives authenticated state
ciphertext and only the two files already intended for public output. The
post-persistence delivery job has read-only repository access and receives only
its dedicated destination credential.

The localized workflow uses the same decryption secret but an independent
`payload/localized.enc`. Its service credential is injected only into the
read-only execution job and checked against runtime, output, next-state, and log
files before persistence. Installation and planning receive no service
credential. Its separate write job receives no secrets, cannot execute the
private runtime, and may change only the localized ciphertext and the two
previously absent localized date paths. There is no localized delivery job or
destination credential. The existing source workflow and its output names stay
unchanged.

Artifacts are selected by the current run and attempt. The localized handoff
validator checks the exact regular-file set, metadata and file digests,
publication date, and base revision. Staged and post-rebase committed paths and
bytes are validated again before a normal, non-forced push. Public logs contain
generic lifecycle messages; private diagnostics are encrypted before upload.
