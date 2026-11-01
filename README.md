# Explainable Property Underwriting MVP

**Version 0.1.0** — a local research prototype with Submission, Assessment and
Decision workspaces. The supplied Northbridge case and demonstration rules are
synthetic research assumptions, not Protector rules, validated loss probabilities
or insurance acceptance criteria.

## Run locally

Use Python 3.12. From the project root, create an environment and install the
dependencies if you have not already done so:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Keep your existing project-root `.env`. If it does not exist, create it using
`.env.example` and fill in the credentials locally. Never commit credential values.

```powershell
.\scripts\run.ps1
```

Open [http://127.0.0.1:8501](http://127.0.0.1:8501). Keep the terminal open; Ctrl+C
stops the app. If that port is occupied, run `scripts/run.ps1 -Port 8502`.
The equivalent direct command remains:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

The app is intended to run from this checkout; no editable package installation,
deployment, database migration command, or model training is required.

## Workflow

- **Submission:** company selection, original PDF review/download, eight information
  categories, six screening checks, underwriter notes and confirmation/review requests.
  The explicit extraction button checks Turso first and loads matching saved results
  without GPT. A missing field marked Not provided is a valid extraction result.
- **Assessment:** Load attribute evidence → human evidence review → Load proposed
  beliefs → human belief confirmation → Calculate Fire / Security ER → Calculate
  overall BRB assessment. Each stage shows its own view, with explanations from
  actual inputs and activated rules. Selections do not trigger paid requests.
- **Decision:** visible placeholders; decision recording/export remain disabled.

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

| Location | Contents |
| --- | --- |
| `config/settings.yaml` | GPT model (`gpt-5-mini` initially), supported request settings, prompts, basic-information schema, screening policy and application/access defaults |
| `config/decision_model.yaml` | Attribute definitions, grade order, ER groups/importance weights, BRB states, input/rule weights, nine conclusion distributions, versions and numerical tolerances |
| `config/underwriting.configuration.cases.json` | Company metadata, case/submission identifiers and source-document paths |
| Root `.env` | `OPENAI_API_KEY`, `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN` only |

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
