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
