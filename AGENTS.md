# Working on this repository

Read README.md and docs/architecture.md before making changes. Historical task
instructions live in docs/archive/implementation-checkpoints.md; the user's current
instructions determine scope.

- Run the application through root app.py; the composition root is underwriting/ui/app.py.
- Keep pure ER/BRB numerical code in underwriting/models, services in intake/assessment,
  UI in ui, and database operations in persistence. Use the one shared combine_er.
- Keep non-secret settings in config/settings.yaml and config/decision_model.yaml.
  Keep the root .env ignored; never print, log, commit or copy credential values.
- Source PDFs are immutable. Case IDs/document paths live in config/case_catalogue.json.
- Preserve saved history and the complete Low/High/Unknown distributions. Explicit BRB
  Unknown and residual unassigned output are different fields.
- Startup/navigation must not call GPT, calculate assessments, or write to Turso.
  Respect local_owner/public_readonly on the server, including direct service calls.
- Run `.venv/Scripts/python.exe -m unittest discover -s tests -v` and the focused
  `scripts/smoke_test.py` when relevant. Use mocks/local SQLite; no paid development
  requests, live data changes, training, deployment or extra agents unless requested.
- Update README/CHANGELOG for user-visible changes. Keep dependencies minimal and
  retain upstream MIT attribution in numerical code and THIRD_PARTY_NOTICES.md.

## Version 2 boundaries

- Work only in this V2 worktree/branch. Never edit or restart V1 or deploy V2.
- Default offline SQLite under .runtime; use V2-only environment variable names.
  No production connections, credential fallback or copied production records.
- reliability/prerequisites.py supplies shared prerequisite contracts. Services
  remain the orchestration boundary; UI is explicit interaction, never execution
  on navigation. Decision assembles persistent prerequisites in snapshot.py.
- Saved-first lookups precede provider work. Content hashes exclude run IDs but
  retain material facts, source and relevant configuration/prompt identity.
- reliability/inputs.py guards complete document/request sizes and provider
  eligibility. Never truncate, implicitly retry or bypass readonly enforcement.
- reliability/recovery.py only recovers durably recorded responses after timeout
  and grace; retry persistence without provider calls. Unreceived responses and
  orphan claims require owner inspection, never automatic regeneration.
- reliability/diagnostics.py defines safe allowlisted metadata. Do not log raw
  documents, provider exception bodies or secrets. Preserve original responses
  only in ignored, worktree-local pending storage and configured test database.
- evals is synthetic software evaluation, separate from expert approval. Preserve
  unchanged numerical modules/model and upstream MIT notices. Use offline tests.
