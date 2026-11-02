# Architecture · version 0.2.0

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
  decision/                   Saved-input snapshots, review, human decisions and reports
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
- **UI:** `ui.app` composes four tabs, including the read-only System overview. Evidence, Beliefs, ER and BRB are separate
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

## Decision records

`decision.snapshot` reads the existing records without rerunning upstream stages.
Submission ID is the catalogue submission version scoped by company/case ID and
source hash; no competing company/submission tables are introduced. Source records
remain in their original tables. Snapshot references link intake → reviewed evidence
→ belief proposal/confirmation events → compatible ER → matching BRB.

`decision_review_runs` holds the immutable snapshot, hashes, prompt/model metadata,
raw response, usage and versioned review JSON. A running audit may finalise once;
completed/failed records are preserved. `underwriting_decisions` appends human
workflow actions and self-referenced supersession links with a foreign key to the
saved review. It does not duplicate Submission human reviews. Reviewer labels are
user-entered context, not authenticated identity.

Decision writes use an FK-enabled Hrana transaction with rollback; a shared atomic
claim prevents duplicate paid requests across sessions. Local pending responses are
ignored by Git and recovered without another GPT call. No schema writes occur at
startup: the owner action initialises only the necessary Decision tables. Existing
upstream tables are neither migrated nor duplicated.

PDF export is deferred until the download click, rechecks current upstream records,
loads the saved human decision and uses a deterministic PyMuPDF template. Unsaved
browser drafts never enter the saved report. See `docs/decision-workflow.md`.

## Read-only explanatory diagrams

`assessment.overview` resolves partially completed saved stages using the same
`current_input`, `current_confirmed` and `current_er_inputs` compatibility checks as
the existing workflow. `persistence.overview` adds SELECT-only stale-record lookups
against existing tables, scoped by company and submission; no schema is created.
`ui.overview_diagrams` constructs escaped Graphviz labels and explicit ranks from
configuration. `ui.overview` reads only while its hierarchy is open; the generic
workflow needs no database. Connection failures preserve both configured diagrams
with hierarchy values unavailable. No upstream stage service is invoked.

The workflow renderer is now `ui.workflow_svg`: deterministic SVG geometry follows
the configured rows/edges, escapes all labels and contains no executable code. Native
`st.image` displays it. Endpoint tests verify that arrows meet source/target boundaries.
The hierarchy continues to use Graphviz inside a centred, bounded native container.
