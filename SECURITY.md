# Security Boundary

Do not commit credentials, decrypted payloads, plaintext output, source data, or
local diagnostic files. Runtime secrets belong in GitHub Actions Secrets and in
an ignored local secrets directory only.

If plaintext is ever committed, rotate the affected credentials and rebuild the
repository history before continuing scheduled execution.
