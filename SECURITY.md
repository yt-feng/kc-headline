# Security Boundary

Do not commit credentials, decrypted payloads, plaintext output, private input
material, or local diagnostic files. Runtime secrets belong in GitHub Actions
Secrets and in an ignored local secrets directory only.

If plaintext is ever committed, rotate the affected credentials and rebuild the
repository history before continuing scheduled execution.

The encrypted runtime and its pinned dependency metadata are trusted inputs.
The write-isolated job receives ciphertext only and is not a sandbox for an
untrusted runtime.
