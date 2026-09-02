# Batch Sandbox

A small sandbox for repeatable batch tasks.

The public layer is deliberately minimal. It contains generic orchestration,
independent encrypted runtime payloads, and no domain-specific configuration or
inputs.

Two weekly outputs run independently:

| Output | Published files | Workflow | Downstream handoff |
| --- | --- | --- | --- |
| Source digest | `output/YYYY-MM-DD.docx` and `.pdf` | `Scheduled Batch` | Existing destination, unchanged |
| Localized edition | `output/zh/YYYY-MM-DD.docx` and `.pdf` | `Localized Batch` | None |

The source digest keeps its existing filenames, encrypted state, schedule, and
delivery integration. The localized edition has a separate encrypted payload
and publication state; neither output replaces the other. Existing historical
files stay at their original paths.

Both workflows run in GitHub Actions, not on a local computer. They share a
non-cancelling publication queue so one cannot interrupt the other's commit.
Manual runs default to `preview`, which uploads only an encrypted result and
does not commit files. To recover a missed localized issue, dispatch
**Localized Batch**, choose
`recover-missed-schedule`, and provide its Friday publication date. Recovery is
limited to the current or next unpublished Friday in that edition's state and
cannot target a future date. Complete existing pairs are never overwritten.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the public execution boundary.
