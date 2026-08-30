# Batch Sandbox

A small sandbox for repeatable batch tasks.

The public layer is deliberately minimal. It contains generic orchestration,
an encrypted runtime payload, and no domain-specific configuration or inputs.

Successful scheduled runs publish date-named document pairs in `output/` and
hand the validated document to a configured downstream destination. Manual runs
default to previews; the explicit recovery operation can persist only the
current or next unpublished Friday.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the public execution boundary.
