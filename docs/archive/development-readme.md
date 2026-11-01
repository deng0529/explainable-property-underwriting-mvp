# Explainable Property Underwriting MVP

**Current BRB checkpoint:** explicit ER aggregates confirmed attribute beliefs into
Fire/Security; **Calculate overall BRB assessment** combines their full distributions
using the configured nine-rule `1.0-demo` model and the same shared ER core, without
GPT. Results, calculation traces and versioned history are saved through Turso.
See [docs/brb.md](docs/brb.md), [docs/er-aggregation.md](docs/er-aggregation.md) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for equations, supported weights,
independent verification and the pinned MIT upstream source.

Decision-model scaffolding is now in [config/decision_model.yaml](config/decision_model.yaml).
Application/model-selection/prompts remain in settings.yaml, which references that file.
See [docs/decision-model.md](docs/decision-model.md) for attributes, ER relative weights,
nine-rule BRB configuration, validation and deterministic model hashing.
Analytical ER and BRB inference are enabled only through explicit actions.

The compact **Submission | Assessment | Decision** UI is preserved. Submission has
explicit basic-information extraction and screening. Assessment supports explicit
attribute evidence load/find, per-attribute human review, initial belief proposals
and separate human accept/edit/confirm records. Decision recording remains
disabled. The complete evidence and belief prompts are stored directly
in YAML; see [docs/attribute-evidence.md](docs/attribute-evidence.md).
Belief workflow and staleness are described in
[docs/attribute-beliefs.md](docs/attribute-beliefs.md).
Assessment actions share one row: Load attribute evidence, Load proposed beliefs,
Calculate Fire / Security ER, Calculate overall BRB assessment. Load indicators distinguish GPT from saved Turso
results. Saved evidence and beliefs remain editable/reviewable without re-extraction.
No paid GPT calls are made during development tests.

## Run locally (PowerShell)

```powershell
cd C:\Weihua\project\explainable-property-underwriting-mvp
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

Open http://localhost:8501. Keep the terminal open; Ctrl+C stops the server.
If this app already occupies the port, use the existing URL. Restart Streamlit after
editing YAML for this initial version; configuration is validated and cached per process.

## Non-secret settings: config/settings.yaml

This is the main application configuration, read by `underwriting/configuration/settings.py` with safe YAML
loading and duplicate-key/type/identifier validation. The previous field/check JSON
files have been migrated into it, without changing their identifiers. It contains:

- `llm.model`, supported generation parameters, request timeout and retries (fixed at 0).
- Approved prompt file paths and versions; prompt text stays in `prompts/*.txt`.
- `intake.field_groups`, stable field IDs, prompt aliases and eight display categories.
- Screening checks, selector labels, scope, policy version and permitted outcomes.
- A reference to `config/decision_model.yaml` for attribute definitions, grades,
  ER importance weights and the configured demonstration BRB rules.
- Application defaults.

The initial model is **`gpt-5-mini`**, selected in `llm.model`.
`OPENAI_MODEL` is neither required nor read, and there is no hidden model fallback.
Generation parameters remain empty, so no unsupported sampling parameters are sent.

To change the model, edit `llm.model`. Generation settings start empty; only explicitly
configured parameters listed in `llm.supported_generation_parameters` are sent. Check
the chosen model's documented support before adding any. No parameters are inferred
from the model name. The loader rejects invalid values rather than substituting defaults.

Edit attributes, grade order, ER membership/importance weights and rule configuration
only in `decision_model.yaml`. ER and the nine-rule BRB model are enabled. Conclusions
are user-supplied illustrative research assumptions; input weights remain equal.
The loader validates references, weights, distributions and complete-rule coverage.
BRB inputs map lower-level unassigned mass to explicit Unknown, while output Unknown
and residual unassigned remain separate. No scalar/winner-only transformation is used.

Screening labels/questions and scope live under `screening`. The approved prompt also
states the policy. Policy changes must be reviewed alongside that prompt, increment
`policy_version`, and update `approved_policy_hash` after review. The hash is the SHA-256
of canonical JSON for `screening` excluding `selector_labels` and `approved_policy_hash`
(`sort_keys=True`, `ensure_ascii=False`). This prevents an accidental YAML edit from
silently disagreeing with the approved prompt. Python continues to derive the outcome.

## Credentials: .env or deployed secrets

The loader explicitly reads the project-root file named **`.env`** with
`python-dotenv` and `override=False`. No additional credentials file is required.
Existing environment variables are preserved. Supported setting names and aliases:

- `OPENAI_API_KEY` / `OPENAI_KEY` / `OPENAI_API_TOKEN`
- `TURSO_DATABASE_URL` / `TURSO_URL` / `LIBSQL_URL` / `DATABASE_URL`
- `TURSO_AUTH_TOKEN` / `TURSO_TOKEN` / `LIBSQL_AUTH_TOKEN` / `DATABASE_AUTH_TOKEN`

Credentials can also be provided by deployed secrets. Only missing setting names
are displayed. The contents of `.env` are never displayed, copied into YAML,
logged or committed. `.env.example` is only a credential-name reference, not a
required file. `.env`, deployed secrets, logs and pending output are ignored.

### Access modes

`access.mode: local_owner` permits extraction and save actions without a password,
only when Streamlit is bound to loopback (`127.0.0.1`). The launch command above
sets that address. There is no `INTAKE_ADMIN_PASSWORD` requirement.

`access.mode: public_readonly` permits saved-result viewing but blocks all paid
calls, database writes and automatic screening saves server-side. This remains the
mode to use for future shared hosting. Do not expose local_owner through a proxy.

## Extraction, versions and recovery

The extraction button first queries Turso by company and document hash. Complete saved
extraction and screening load automatically, including legitimately missing fields
marked Not provided. They are reused even if prompts/model settings have changed.
A database outage is an error, never an assumption of empty history or permission
to call GPT. Source changes are marked stale.

If only extraction is saved, Python reuses its sourced fields/observations to recover
screening conservatively. Missing essential support produces Unknown/review issues,
not another model request. Local-owner recovery creates an append-only screening run;
public_readonly does not write. Previous results and raw responses are preserved.

Only an explicit authorised **Extract information and screen** click makes one GPT
request. Ordinary navigation/refresh never does. Regeneration requires confirmation.
The progress panel reports database, source, GPT, validation and save stages with
elapsed times; the last progress trace remains available after completion. Failed
responses expose validation details and can be explicitly revalidated without GPT.
Missing loss currency and non-ISO loss dates are preserved and flagged for review;
they are never silently inferred or corrected, and unresolved issues prevent Proceed.
The typed response is validated, Python derives the screening outcome, and the whole
run is stored atomically in append-only Turso intake tables. No earlier successful run
is overwritten by a failed attempt. No real database migration/write is performed by tests.

New runs record the effective non-secret configuration snapshot/version/hash, prompt
hashes/versions, schema, source, policy, model, usage and timestamps. Historical results
keep their original outcome and versions when YAML/model settings change; the UI
indicates configuration differences without re-extracting or changing saved data.
Changed source versions/hashes are stale and do not populate the current case.

A response that cannot be saved is retained locally. Refresh and use **Save retained
result · no GPT call** to retry saving it. This does not require an OpenAI key.
See [docs/submission-intake.md](docs/submission-intake.md) for validation, normalisation,
field aliases, permissions, atomic storage and interrupted-request recovery details.

## Focused checks

Submission supports separate human review: notes for basic information and each
screening check, followed by **Confirm submission review** or **Save review request**.
Confirmation requires acknowledging both sections and shows a reminder to open
Assessment. It records review, not insurance acceptance or a screening override;
existing pause/out-of-scope outcomes and validation issues remain unchanged.
Assessment navigation is not locked in this MVP. A request records Broker, Senior
underwriter or Other department as its intended recipient and requires a reason;
it sends no message. Local-owner saves append to `submission_human_reviews_v1`,
linked to the original intake run and source hash. Previous reviews remain intact.
Use **Load saved submission reviews** to retrieve history explicitly. Resetting
Submission clears displayed notes and controls without deleting saved reviews.

In Assessment, first **Load attribute evidence**, review and confirm each attribute's
evidence, then **Load proposed beliefs**, confirm each attribute's beliefs, and click
**Calculate Fire / Security ER**. Each step checks that its prerequisites are complete.
Clicking a step too early shows a reminder
without starting processing; buttons remain clickable when configuration is available.
These readiness checks use session state and do not trigger database reads or GPT calls.

Assessment displays one stage at a time. Evidence shows the attribute selector and
evidence details without belief columns or ER panels. Loading beliefs opens the
belief distributions and human confirmation controls, without evidence detail.
The action row contains four buttons in workflow order: Load attribute evidence,
Load proposed beliefs, Calculate Fire / Security ER, and Calculate overall BRB assessment.
The fourth button enables after current valid ER results are available. It loads a
matching saved assessment or calculates/saves numerically, without rerunning GPT or ER.
Calculating opens the two ER results. A compact view selector allows returning to
already loaded stages without GPT calls, database queries or recalculation.
Successful load/calculate actions automatically select and highlight their stage.
Evidence review status updates from the saved review events and shows reviewer
names and latest review times. Names are self-reported in local-owner mode, not
authenticated identities; older unnamed reviews are labelled accordingly.
Only the latest successful evidence/proposal set matching the current source and
reviewed evidence is loaded. Failed regenerations do not replace it. Earlier runs
remain in audit history; the MVP has one current result and no version chooser.

Choose **Reset workspace**, the first option in the company dropdown, to clear only
the active tab. Submission reset clears displayed intake and document controls;
Assessment reset clears evidence, beliefs, ER results and review controls. Other tabs
and the selected company are preserved.
Saved database history and retained response files are preserved. Load buttons can
retrieve saved records again; resetting does not force a new paid extraction.
The two result panels show Low, High and Unknown (unassigned) for Fire and Security,
with effective weights and saved calculation provenance. This action uses no GPT
and saves a new ER run without overwriting previous runs. BRB is a separate explicit action.
Each group also shows its largest-component label and belief degree: Low concern,
High concern, Insufficient information, or Mixed assessment for ties within
`config/decision_model.yaml` → `tolerances.assessment_tie`. These are descriptive
labels only. Full unrounded distributions, including Unknown, are retained for BRB.
Both group aggregation and activated-rule aggregation use the one
`underwriting.models.er.combine_er` function. `underwriting.models.brb.infer_brb` calculates max-normalised antecedent
weights, matching products and normalised rule activations. The result retains all
output states and a separate residual, plus the complete activated-rule trace.

BRB view shows the overall distribution first, followed by the two ER input
distributions, input-support and most-active-rule explanations, and activated rules.
All nine rules can legitimately activate when both inputs have nonzero beliefs in
all three states. Technical calculation/model-settings panels are hidden; settings
still come from decision_model.yaml and full traces remain saved. The conventional
matching/activation method has been checked against the pinned upstream code; see
docs/brb.md and THIRD_PARTY_NOTICES.md for the explicit interpolation/epsilon differences.

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe smoke_test.py
```

All GPT responses and database access in tests are mocked/local. Tests cover YAML,
missing/invalid configuration, stable IDs, historic results, no exposed credentials,
field/check mapping, screening outcomes, quotes, save retry, owner permissions,
case isolation and zero GPT calls during navigation.

Current interaction: startup and ordinary navigation do not load analysis from Turso
or call GPT. The button checks Turso first; saved results load without GPT. Only if
no valid saved result exists may that explicit action extract, screen and save.
Results loaded by the button remain visible in the current browser session.
