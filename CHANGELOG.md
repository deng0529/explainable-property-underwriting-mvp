Separated Unknown support in comparison details; corrected evidence validation for score interpolation and added explicit retained-response recovery without GPT or history replacement.

Updated the V2-only comparison: independent improved direct-belief and score-plus-Unknown prompts, shared full-document/reviewed-evidence snapshots, method-specific saved-result reuse and recovery, and display-only comparisons. Canonical confirmations and numerical engines are unchanged.

Added a temporary independent concern-score belief comparison with its own YAML prompt, append-only results, save recovery and local-only proposal access. Confirmations and ER/BRB remain based on the original direct method.

Restored product title with small V2 label and scope note, removed technical diagnostic expanders, and reduced repeated read-only Decision loading work.

Fixed V2 Cloud startup: read-only Turso uses Cloud secrets without requiring a local Windows .env file; local explicit-file loading remains supported.

Added explicitly configured existing-Turso saved-results-only mode with transport-level SELECT enforcement and read-only V1 identity compatibility.

## MVP Version 2 — Reliability upgrade

- Isolated worktree, environment, offline SQLite storage and port 8502.
- Shared prerequisite contracts, material-content reuse, bounded save-only
  recovery, safe diagnostics and configurable complete-input size limits.
- Recorded synthetic evaluation with no implied expert approval.
- Five attributes, nine BRB rules, shared ER/BRB engines and familiar UI preserved.

# Changelog

## 0.2.0 — 2026-11-02

- Added a dedicated public-readonly cloud entry point and deployment guide; keep
  local owner configuration separate and block cloud paid calls/writes server-side.
  Public controls clearly load saved results and need only database credentials.

- Simplified hierarchy labels to property, Fire and Security concern assessments;
  explained ER/BRB and labelled contribution weights. Added a clear workflow title
  and removed the storage footer without changing diagram sizes or saved data.

- Centred both overview diagrams, balanced readable sizing and collapsed supporting
  explanations; replaced automatic workflow routing with escaped SVG connectors
  that meet box boundaries, preserving configured dependencies and read-only behavior.

- Reduced hierarchy box size and spacing; changed the workflow to compact snake
  rows with a shared storage legend, preserving all dependencies and saved data.

- Added a read-only System overview tab with a saved-data assessment pyramid and
  explicit MVP workflow, including screening routing, human stages and shared Turso storage.

- Reuse Decision reviews and cached PDFs by substantive content across identical
  reruns, ignoring audit IDs/timestamps and repeated confirmations. Preserve original
  snapshot integrity/history; real source/information/assessment changes still invalidate.


- Verify repeat Decision loads reuse the persisted current review and explicitly
  label database loads/no GPT request in progress and the review display.


- Correct explicit Decision loading to generate one current review when no current
  successful review exists; preserve historical runs without selecting them as current.
- Separate AI review and Human decision tabs, preserve draft edits, and move download
  below the tabs with report-only retry independent of paid review generation.
- Persist versioned PDF BLOBs and integrity/version links in decision_reports; cache
  downloads, rebuild only missing/changed artifacts, and preserve saved reviews on
  PDF failures. Record shared screening-resolution corrections explicitly in snapshots.


- Clean Decision to one AI summary; confine old narrative to dated History and
  prioritize validated current reviews. Remove technical references from the view.
- Resolve PDF inputs directly from Turso on download, with explicit field/mapping
  errors and a current-review-pending export instead of mixing historical advice.


- Restore readable historical Decision summaries, results, concerns and suggestions;
  keep full details in expanders and put the PDF download near the top. Historical
  exports are explicitly marked not current, with no regeneration required.


- Resolve screening once across Submission/Assessment/Decision/reports: six Pass
  checks proceed, scope failures are outside, essential unknown/failures pause
  with reasons; data-quality concerns stay visible without silently pausing Pass.
- Link Decision snapshots to current screening ID/version/resolution hash; display
  conflicting historical reviews as stale and require explicit regeneration.
- Replace detailed PDF export with a readable concise one/two-page review, retaining
  full evidence, attributes, rules, working notes and provenance in the app/database.


- Correct Decision readiness: check saved records directly regardless of opened
  tabs. Paused screening is reviewable with constrained clarification/referral
  actions, while missing/stale upstream records still block GPT before any charge.


- Check Submission screening and all saved Assessment prerequisites before any
  Decision GPT request or write; show specific completion reminders and block
  regeneration while prerequisites remain unresolved.


- Fix Decision review reference failures by constraining structured output to exact
  saved reference IDs; keep failed-attempt errors visible and preserve all prior runs.


- Decision adds explicit saved-first, evidence-linked GPT reviews, immutable human
  workflow decisions and local saved-record A4 PDF reports. Current upstream checks,
  owner restrictions, version history, duplicate prevention and save-only recovery
  preserve the existing Submission and Assessment workflows.


- Assessment uses four readable top-stage actions with one active highlight and
  no duplicate lower navigation. Loaded current stages are reused on return.
- Belief review has two columns; ER/BRB distributions precede compact input tables,
  retaining review controls, complete distributions, rule traces and residual belief.


- Submission-only Basic information/Screening inner tabs, compact escaped tables,
  collapsed sources/review controls and responsive selector/detail layouts.
- Main navigation labels enlarged to 18px; inner tabs 16px and scoped Submission
  table text 15–16px. Assessment/Decision content and underlying logic unchanged.

## 0.1.0 — 2026-11-01

First local research-prototype checkpoint.

- Three workspaces: Submission, Assessment and Decision placeholders.
- Versioned case catalogue, immutable PDF viewer/navigation/download and tab-scoped reset.
- Explicit saved-data-first intake, six demonstration screening checks and human notes/review.
- Attribute evidence, named human reviews, belief proposals and confirmation history.
- Shared analytical ER aggregation for Fire/Security and activated BRB conclusions.
- Configured nine-rule demonstration BRB, full distributions, activated-rule explanations,
  and separate explicit Unknown/residual mass.
- Append-only Turso persistence, source/configuration hashes, stale-result detection,
  local-owner writes and readonly saved-result viewing.
- Organised Python packages, root launch entry point, run/test scripts and architecture guide.
- No deployment, training, automated acceptance decisions or Decision recording.
