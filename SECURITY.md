# Security Boundary

Do not commit credentials, decrypted payloads, private input material, or local
diagnostic files. Runtime secrets belong in GitHub Actions Secrets and in an
ignored local secrets directory only.

The only generated plaintext permitted in the repository is the validated,
date-named document pair under `output/`. Keep private values out of filenames,
paths, commit messages, workflow labels, and logs.

If plaintext is ever committed, rotate the affected credentials and rebuild the
repository history before continuing scheduled execution.

The encrypted runtime and its pinned dependency metadata are trusted inputs.
The write-isolated job has no runtime secrets. It receives authenticated state
ciphertext and only the two files already intended for public output.
