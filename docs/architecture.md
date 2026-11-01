# Architecture · version 0.1.0

The project is a local Streamlit application run from its repository checkout.
`app.py` is the stable launch entry point and calls `underwriting.ui.app.main()` on
every Streamlit rerun. No editable package install or new runtime dependency is needed.

```text
app.py                         Streamlit launch entry point
underwriting/
  configuration/              Safe YAML loading, model validation, access and cases
  documents/                  Original PDF reading and page rendering
  intake/                     Extraction/screening schemas, services and human review
  assessment/                 Evidence, beliefs, ER/BRB orchestration and validation
  models/                     Pure shared ER core, BRB inference and configuration adapters
  persistence/                Owner-gated append-only Turso repositories
  ui/                         Workspace composition, controls, state and explanations
  paths.py                    Single project-root anchor
config/                       Settings, decision model and case catalogue
data/submissions/<case-id>/    Immutable supplied source PDFs
prompts/                      Approved intake prompt text files
tests/                        Mocked services, local SQLite and numerical/UI checks
scripts/                      Local run, test and UI/document smoke commands
docs/                         Current technical documentation and archived checkpoints
```

## Responsibilities

- **Configuration:** `settings.py` validates declarative application settings;
  `decision_model.py` validates attributes, weights, rule coverage and distributions.
  `access.py` explicitly loads root `.env` with override=False and enforces server-side
  access. The package's position never changes where credentials/configuration live.
- **Models:** `models.er.combine_er` is the only production ER equation. Attribute
  aggregation passes normalised importance weights; BRB passes calculated activation
  weights. `models.brb.infer_brb` is independent of Streamlit, Turso and OpenAI.
- **Services:** intake/assessment coordinate explicit user actions, structured validation,
  source/configuration versions, saved-result reuse and concurrency checks. They keep
  business logic out of widgets and SQL out of the UI.
- **Persistence:** repositories share the Turso transport and enforce local-owner writes.
  Saved runs and human review events are appended, not overwritten. Public-readonly
  mode cannot start paid calls or calculate/save on a cache miss.
- **UI:** `ui.app` composes three tabs. Evidence, Beliefs, ER and BRB are separate
  Assessment views. Navigation and reset operate on session state; presentation
  helpers explain saved numbers without calling GPT or recalculating assessments.

## Stable data/configuration boundaries

The case catalogue and PDF moved into config/data respectively. Case ID, submission
version and PDF bytes/hash did not change. Existing Turso records, source matching,
human reviews and pending save retries remain valid. Approved prompt files and the
decision-model settings were not changed by the package reorganisation.

For compatibility with existing retained responses, `.intake_pending/`,
`.evidence_pending/` and `.belief_pending/` stay in their original root locations and
remain ignored. `.runtime/` holds ignored local logs/development artifacts. `.env`
also remains at the root, ignored. None of those files is part of the release.

Configuration is cached per process. Restart Streamlit after editing YAML. Model or
source changes must be visible as stale where relevant; they do not automatically
regenerate data. Changes solely to BRB settings do not invalidate unrelated ER inputs.

## Validation

```powershell
.\scripts\test.ps1 -Smoke
```

The tests use mocked GPT/services and in-memory SQLite, plus the supplied synthetic
PDF for document/UI checks. The BRB directional grid checks 640 comparisons and
reports counterexamples without silently changing the configured rules. No live
database migration/write, paid request or deployment is part of this verification.
