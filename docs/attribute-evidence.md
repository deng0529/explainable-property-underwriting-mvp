# Attribute evidence checkpoint

Only attribute evidence identification and human evidence review are enabled in
Assessment. Beliefs, ER, BRB and decision controls remain disabled. Submission and
Decision layout/behaviour are preserved.

## Configuration

The complete approved prompt is stored directly as a YAML literal block at
`prompts.attribute_evidence.template` in `config/settings.yaml`, version `1.0`.
There is no attribute-prompt text file. Attribute definitions have one source:
`attributes` in `config/decision_model.yaml`; F3 has three explicit `aspects` there.
The loader rejects empty templates, incorrect placeholders or invalid attribute IDs.
Restart Streamlit after YAML changes. Changes do not call GPT or overwrite evidence.

The three placeholders are populated once from YAML definitions, case/source
version, and the complete page-labelled original PDF text. Source instructions
remain data. Submission summaries are never substituted for the original PDF.

## Explicit load/find and regeneration

- Startup/company selection/navigation do not load evidence from Turso or call GPT.
  The UI says Not loaded yet until explicitly loaded; session results are scoped
  to case ID, source version and current document hash.
- **Load attribute evidence** first checks Turso. A matching successful run
  loads with no GPT call. Historical prompt/configuration metadata is displayed;
  differences from current YAML are labelled without changing historical output.
- If no valid saved run exists, `local_owner` on loopback can make one structured
  request for all five attributes. `public_readonly` can load stored evidence but
  cannot issue requests, write reviews or save responses/source text.
- A database outage stops the action. It is never interpreted as no evidence.
- An earlier failed attempt requires an explicit new regeneration confirmation.
  **Regenerate evidence with GPT** is in an expander; it creates a new run with
  current YAML, retaining prior runs. Locks, immutable generation claims, history
  checks and run-specific confirmation keys prevent duplicate requests. SDK retries
  remain zero; no model/validation repair requests are made.

PDF text is cached in Turso by document hash plus `pymupdf-page-text-v1` extraction
version. The record contains pages, page-labelled text, text hash, source path and
timestamp. Matching stored text is reused; otherwise the original local PDF is read,
and local-owner actions save text provenance. Read-only loading may use a temporary
local read if no cache is available, without writing it.

## Validation, persistence and recovery

The typed schema forbids extra keys, numerical assessment output and unknown
implication labels. F1/F2/F3/S1/S2 must occur exactly once. Evidence/gap IDs are
globally unique; related references must exist within their attribute. Page ranges
and quotations are checked against full source pages. Normalisation is the same
documented NFKC/soft-hyphen/whitespace normalisation as Submission; original quote
strings are retained. F3 aspects must match that run's configured three aspects.
No findings requires an explicit information gap. Validation failures are visible,
retained in the failed run and never replace prior successful evidence.

Schema validity and quote matching do not establish factual correctness. The
reviewer must inspect relevance, scope, attributions and contradictions.

The existing Turso HTTP repository/owner gate are reused. Additive tables:

- `attribute_source_text_v1`: source text/provenance by hash/extraction version.
- `attribute_evidence_runs_v1`: one atomic append-only row for all five attributes,
  original response, usage, source, prompt/schema/configuration versions/hashes,
  attribute-definition snapshot/hash and validation status/errors.
- `attribute_evidence_reviews_v1`: append-only per-attribute human review events.

The existing request-claims table is reused with an evidence-specific generation key.
No tables are created on startup. Tests make no live schema changes or paid calls.

If GPT returns but saving fails, the unchanged run is retained under ignored
`.evidence_pending/`. **Retry save · attribute evidence · no GPT call** saves that
response without requesting GPT again. Unknown interrupted requests remain locked
for owner recovery rather than being automatically repeated.

## Human review

The selected attribute shows AI facts, quotations, relevance, gaps, contradictions
and summary. F3 findings are grouped by its three aspects. Owner-only forms edit
interpreted facts and notes and confirm an attribute as Reviewed. The AI facts and
original quotations remain immutable; human interpretations are displayed separately.
Every save is a timestamped review event linked to its evidence run/version. A new
evidence run starts Pending review and never inherits confirmation from an old run.

## Focused mock tests

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p test_evidence.py -v
```

Tests use invented, labelled mock text and the production SQL through in-memory
SQLite. They cover YAML/template substitution, five attributes, F3 aspects, gaps/
contradictions, invalid IDs/quotes/pages, cache reuse, company/source isolation,
read-only protection, human-review persistence, database failure, save-only retry,
regeneration history and zero extra GPT calls on UI navigation.
