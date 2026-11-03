The interface is titled **Explainable Property Underwriting**, with a small
Version 2 label and an About this MVP scope note (Fire and Security only).
Technical run diagnostics are retained internally and no longer appear as UI
expanders. Read-only Decision loading reuses one HTTP session, shares the
validated current snapshot with the view, and fetches PDFs on demand; no query
results are cached between checks and no GPT requests or writes are enabled.

For Streamlit Cloud, deploy `cloud_app.py` from `feature/v2-reliability`.
In the app's Secrets settings, supply TOML with quoted values:

```toml
TURSO_DATABASE_URL = "libsql://YOUR-DATABASE.turso.io"
TURSO_AUTH_TOKEN = "YOUR-TOKEN"
```

Cloud reads these secrets directly and ignores the optional local
`services.credential_env_path`. No `.env` upload or GPT key is needed.
Missing credentials are reported by setting name, never by value.
The shared database remains SELECT-only; generation and writes stay disabled.
Locally, the explicitly configured existing `.env` still supplies database
credentials when they were not supplied through secrets/environment.

Current local configuration uses `services.mode: turso_readonly`: it reads the
existing Turso database through the explicitly configured `credential_env_path`.
Only database credential names are used from that file; GPT credentials are not
loaded. No credentials are copied, and neither the credential file nor V1 is
modified. Saved results load on button clicks. Missing/stale results are reported,
never regenerated; writes, schema changes and paid calls are blocked server-side.
Switch to `offline` to return to isolated local development. Legacy V1 content
identities remain readable without migrating or rewriting stored records.

# Explainable Property Underwriting MVP

**MVP Version 2 — Reliability upgrade** — a local research prototype with Submission, Assessment,
Decision and System overview workspaces. The supplied Northbridge case and demonstration rules are
synthetic research assumptions, not Protector rules, validated loss probabilities
or insurance acceptance criteria.


## Version 2 isolation and reliability

Version 1 remains in `C:\Weihua\project\explainable-property-underwriting-mvp`,
on `main` at `c9caf217bf323bd18c58e42d08f840fce55c9314`. This worktree uses
`feature/v2-reliability` in the separate `explainable-property-underwriting-mvp-v2`
folder. Nothing here changes the existing cloud deployment. Run each version
from its own environment; V2 defaults to port **8502**.

V2 starts with **offline SQLite** (`services.mode: offline`). Its database is
`.runtime/v2.sqlite`; pending responses, generated files and caches are also
local to this worktree. It neither copies production records nor falls back to
V1 credentials. The application initially has no saved analyses. Offline mode
can view locally saved results and perform local numerical/review operations;
live GPT generation is blocked. The recorded fixtures are test data, not
fabricated company results seeded into the application.

Non-secret settings remain in `config/settings.yaml`; the unchanged numerical
model remains in `config/decision_model.yaml`. The Decision system guardrail is
now referenced from YAML with the unchanged approved wording; complete system
and user messages are included in its prompt hash. Restart after YAML changes.
Only an explicitly confirmed **separate test** service may be configured using
`services.mode: test_turso`, `test_scope_confirmed: true`, an explicit
`allowed_test_database_hosts` list and V2-only `.env` credential names:
`V2_OPENAI_API_KEY`, `V2_TURSO_DATABASE_URL`, `V2_TURSO_AUTH_TOKEN`.
The host allowlist is an owner assertion of test scope, not independent proof
that a database is non-production. Never put a production host there.
`public_readonly` remains enforced in services and repository write boundaries.
No live database or GPT request was used to validate this upgrade.

Saved results are checked before generation. Material evidence and confirmed
belief hashes exclude bookkeeping run IDs; actual content, source, prompt and
relevant configuration still determine compatibility. Failed database reads
remain errors and never become paid-generation cache misses. Missing/stale
prerequisites stop a stage with a reminder; earlier stages never run implicitly.
The same screening resolver supplies Submission, Decision and reports.

Received responses are durably retained before final saving. Save retry never
repeats GPT. After interruption, a recorded response can be revalidated and saved
once the configured request timeout plus a 60-second grace has elapsed and no
local lock is held. A response never received or durably recorded cannot be
recovered: remote completion may be ambiguous, and a new request requires an
explicit owner action. Claims interrupted before a pending record exists fail
closed for owner inspection; they are not automatically released. SDK retries
remain disabled. Recovery is bounded to one explicit attempt per action.

Collapsed run diagnostics use a shared allowlist (stage, run ID, status, source,
duration, generic error and recovery action), never exception bodies or source
text. `reliability.input_limits` defines conservative per-model page, character
and complete UTF-8 request limits. These are application safety envelopes, not
provider token-capacity claims. Oversized inputs stop without truncation; an
owner must review the envelope or supply a smaller complete source.

Run offline validation:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/smoke_test.py
.\.venv\Scripts\python.exe scripts/evaluate_offline.py
```

The synthetic evaluation checks software contracts only. No expert approvals or
real-world underwriting accuracy are claimed. See `evals/README.md` and
`docs/V2_RELIABILITY_PLAN.md`. No new providers, agents or numerical algorithms
were introduced. The reference sections below describe the inherited workflow;
V2 service isolation above takes precedence over shared-hosting instructions.

## Run locally

Use Python 3.12. From the project root, create an environment and install the
dependencies if you have not already done so:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Use this worktree’s separate project-root `.env` (loaded with override=False).
Offline mode needs no credentials. Do not copy the Version 1 environment.
Never commit credential values.

```powershell
.\scripts\run.ps1
```

Open [http://127.0.0.1:8502](http://127.0.0.1:8502). Keep the terminal open; Ctrl+C
stops the app. If that port is occupied, run `scripts/run.ps1 -Port 8502`.
The equivalent direct command remains:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

The app is intended to run from this checkout; no editable package installation,
deployment, database migration command, or model training is required.

## Share the app online

Use [the Streamlit Community Cloud guide](docs/community-cloud.md) to deploy
`cloud_app.py` from GitHub. This entry point enforces saved-results viewing only;
provide Turso credentials in Cloud Secrets and do not add an OpenAI key.
Your local `app.py` owner workflow stays unchanged.

## Workflow

- **Submission:** company selection, original PDF review/download, eight information
  categories, six screening checks, underwriter notes and confirmation/review requests.
  The explicit extraction button checks Turso first and loads matching saved results
  without GPT. A missing field marked Not provided is a valid extraction result.
- **Assessment:** Load attribute evidence → human evidence review → Load proposed
  beliefs → human belief confirmation → Calculate Fire / Security ER → Calculate
  overall BRB assessment. Each stage shows its own view, with explanations from
  actual inputs and activated rules. The four top buttons select the active stage
  (red); review/completion status is shown separately. Returning to an already
  loaded current stage reuses its session results. Beliefs use selector/detail
  columns, and ER/BRB summaries appear above their input tables. Selections do
  not trigger paid requests.
- **Decision:** explicitly load/generate a saved-input review, inspect evidence-linked
  concerns and information requests, record a human workflow action, and download a
  concise one/two-page A4 PDF from saved records. No action is preselected. Opening or rerunning the
  tab never queries review results or calls GPT. See [Decision workflow](docs/decision-workflow.md).

**Reset workspace**, the first company-dropdown option, clears only the active
tab's displayed data and drafts. Other tabs and saved records remain unchanged.
Loading again retrieves the latest matching successful run. Regeneration is an
explicit, confirmed paid action; failed attempts do not replace earlier success.
Received responses that fail to save can be retried without another GPT request.

## Project structure

```text
app.py                     Stable Streamlit launch entry point
underwriting/
  configuration/          YAML settings, model validation, credentials/access, cases
  documents/              Original PDF reading and rendering
  intake/                 Extraction, screening, recovery and human review services
  assessment/             Evidence, belief and ER/BRB orchestration
  decision/               Saved snapshots, review validation, human decisions and PDFs
  models/                 Shared analytical ER, BRB inference and adapters
  persistence/            Append-only Turso repositories
  ui/                     Tab renderers, controls, state and presentation helpers
config/                    settings.yaml, decision_model.yaml, underwriting.configuration.cases.json
data/submissions/           Immutable supplied PDFs, organised by case ID
prompts/                    Approved intake prompt text files
tests/                      Mocked service, numerical, local SQLite and UI checks
scripts/                    Run, test and PDF/UI smoke commands
docs/                       Architecture, methods, configuration and archived notes
```

See [architecture](docs/architecture.md), [documentation index](docs/README.md),
[project background](docs/project-overview.md) and [changelog](CHANGELOG.md).

## Configuration and credentials

Submission uses Basic information and Screening inner tabs, compact escaped HTML
tables and collapsed sources/review controls. Scoped styles are in
`underwriting/ui/styles/submission.css`: main navigation labels are 18px, inner
tabs 16px and Submission tables 15–16px. Other workspaces retain their typography.

| Location | Contents |
| --- | --- |
| `config/settings.yaml` | GPT model (`gpt-5-mini` initially), supported request settings, prompts, basic-information schema, screening policy and application/access defaults |
| `config/decision_model.yaml` | Attribute definitions, grade order, ER groups/importance weights, BRB states, input/rule weights, nine conclusion distributions, versions and numerical tolerances |
| `config/underwriting.configuration.cases.json` | Company metadata, case/submission identifiers and source-document paths |
| Root `.env` | `V2_OPENAI_API_KEY`, `V2_TURSO_DATABASE_URL`, `V2_TURSO_AUTH_TOKEN` only |

The root `.env` is loaded explicitly with python-dotenv and `override=False`.
Recognised credential aliases are supported; `OPENAI_MODEL` and an administrator
password are not required. No secret values belong in YAML or source control.
Approved intake prompts remain in `prompts/`; evidence/belief prompt templates and
versions remain in the existing YAML settings. No prompt text changed in this release.

**Restart Streamlit after editing YAML.** The safe loader validates configuration
and caches it per process. Invalid configuration is reported, not silently replaced.
Screening-policy edits must agree with the approved prompt and policy hash; see
[intake documentation](docs/submission-intake.md).

With `access.mode: local_owner`, loopback-bound local owners may explicitly extract
and save. `public_readonly` permits matching saved-result viewing only; paid calls,
calculation on a cache miss and writes are blocked server-side. Model changes do
not erase or automatically regenerate historical extraction results.

## ER and BRB model

There is one production ER function:
[`underwriting.models.er.combine_er`](underwriting/models/er.py). Group aggregation
passes normalised attribute importance weights; activated BRB conclusions pass
calculated normalised activation weights into that same core.

Fire/Security retain full Low/High/unassigned distributions. Their descriptive
largest-component labels are Low concern, High concern, Insufficient information
or Mixed assessment within the configured tie tolerance. Display rounding never
changes saved numerical values.

The top-level BRB uses the supplied `1.0-demo` nine-rule model. It consumes complete
ER distributions, maps the unassigned remainder to explicit Unknown, matches rule
antecedents, normalises attribute weights by their maximum, and combines activated
conclusions through ER. Output Unknown and residual unassigned belief remain
separate. No scalar/winner-only substitution or acceptance/rejection recommendation
is produced. All nine rules may activate for full distribution inputs.

- [ER equations and validation](docs/er-aggregation.md)
- [BRB method, custom Unknown interface and directional checks](docs/brb.md)
- [Model configuration and extension](docs/decision-model.md)
- [Pinned upstream source and MIT notices](THIRD_PARTY_NOTICES.md)

## Saved records and local artifacts

Successful runs and human reviews append to Turso with source, configuration,
model and engine references. Changed inputs/configuration mark dependent results
stale. Matching saved BRB results are reused without recalculating upstream stages.
Notes/review requests are recorded; no email or departmental dispatch is automated.

The original PDF is now at
[`data/submissions/NB-2026-001/Northbridge_Broker_Submission.pdf`](data/submissions/NB-2026-001/Northbridge_Broker_Submission.pdf).
Its bytes/hash and case identifiers are unchanged, so existing source-linked results
remain valid. Existing `.intake_pending/`, `.evidence_pending/` and `.belief_pending/`
directories stay local for save-retry compatibility. `.runtime/` holds local logs.
Credentials, retained responses, logs, caches and virtual environments are Git-ignored.

## Checks

```powershell
.\scripts\test.ps1 -Smoke
```

Or run directly:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/smoke_test.py
```

Tests use mocked GPT/services and local SQLite. They cover saved-data-first loading,
save retries, access controls, history/staleness, review workflows, UI navigation,
PDF access, shared ER and BRB equations. The directional grid reports counterexamples
rather than silently changing rules. No paid call or live database write is part of
verification. Training, optimisation backends, authentication and deployment are
outside this version.

Screening workflow status is resolved centrally from the actual six checks and any
explicit reasoned human override. Six Pass checks mean Proceed; non-blocking extraction
validation concerns remain visible separately. Decision snapshots include the selected
screening record/version/hash; older conflicting reviews are historical until explicitly
regenerated. The concise PDF omits working notes, attribute/rule details and appendices;
full saved evidence and numerical/history records remain unchanged.

Decision now separates the current review from dated historical content. One AI review
summary is shown; historical recommendations stay in History. Download review report
loads its inputs directly from Turso, even after restart and without opening other
tabs. If only a stale AI review exists, the explicit Load / Generate action creates a new
current review from complete saved upstream inputs; the old review is never substituted.
Absent saved field mappings/query errors are never labelled Not provided.

Decision uses AI review summary / Human decision inner tabs. Its explicit Load /
Generate button loads a matching current review, or generates once from complete
saved current inputs. PDF artifacts are versioned BLOBs in Turso `decision_reports`,
keyed by review/snapshot/template/human decision. Download retrieves saved bytes;
missing PDF and report-only failures recover from saved AI content without GPT.
Changing a saved human decision or report template creates only a new PDF version.

A repeat Decision load reuses the successful `decision_review_runs` row for the same
saved upstream snapshot; the UI explicitly says Loaded from database — no GPT call.
New ER/BRB run IDs and timestamps alone do not require a new Decision review.
A content check reuses the review and PDF when the source, substantive information
and actual assessment content are unchanged, even after rerunning all stages.

Decision reuse keeps two identities: the original full snapshot hash for audit integrity,
and a content hash for paid-call reuse. Content identity excludes run/event IDs,
timestamps and repeated confirmations, but retains source bytes, case/submission,
provided facts, source evidence, substantive human corrections/notes, screening,
confirmed beliefs, model settings and actual ER/BRB distributions. Old saved reviews
are compared on read without rewriting their snapshots. No approximate paraphrase
matching or rounded-number comparison is used. Explicit confirmed regeneration is
still a deliberate override; ordinary Load uses the saved content.

## System overview diagrams

The fourth main tab contains an Assessment hierarchy and MVP workflow. The hierarchy
reads saved current human confirmations and compatible ER/BRB results directly, even
in a fresh session. AI-only proposals are explicitly unconfirmed; missing, stale and
unavailable values are labelled, and stale numbers are withheld. Opening either view
never calls GPT, calculates results, confirms records or writes to Turso.

Model labels, attributes, memberships and weights come from `config/decision_model.yaml`.
Workflow labels, links and the storage legend are presentation configuration in
`config/system_overview.yaml`, loaded by the existing safe YAML reader. Hierarchy construction
is separate in `underwriting/ui/overview_diagrams.py`; `graphviz` is the only added
dependency. Streamlit renders DOT in the browser; no Graphviz system executable is needed.
Percentages and weights are rounded only for display. The original saved data and
complete distributions remain unchanged. Use the chart fullscreen control on small screens.

The overview uses a compact hierarchy and a row-by-row snake workflow. Edit
`workflow.layout_rows` in `config/system_overview.yaml` to change diagram placement;
these positions never change execution order or the saved analysis. Diagram rendering
is lightweight and uses saved results directly; diagram images are not duplicated in
Turso. No GPT request or numerical recalculation is needed to redraw them.

Both overview drawings are centred and width-limited for laptop viewing. Detailed
explanations remain in collapsed sections. The workflow uses escaped SVG in the native
Streamlit image viewer (`underwriting/ui/workflow_svg.py`) so arrow endpoints touch
box boundaries precisely; its rows and logical edges still come from the same YAML.
The hierarchy retains Graphviz. No scripts, new dependency or persistence change is involved.
