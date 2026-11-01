# Submission intake checkpoint

Only Submission extraction and screening are implemented. Attribute evidence,
belief elicitation/confirmation, ER/BRB and decisions remain disabled. No live GPT
call is made by tests. No deployment or unrelated database tables are changed.

## Configuration and owner access

The project-root `.env` is loaded explicitly with `override=False`; No additional credentials file is required. Existing environment variables are preserved. OpenAI key,
Turso URL and Turso token accept the aliases documented in README. Missing settings
are reported by name only. Credentials never enter YAML, run records, logs or Git.

The model is `llm.model` in YAML (initially `gpt-5-mini`). `OPENAI_MODEL` is ignored.
The generation parameter map starts empty. SDK retries are disabled, and there is
no fallback model or automatic repair request.

`access.mode: local_owner` enables actions without a password only on a loopback-bound
Streamlit server. `public_readonly` blocks writes/paid calls server-side even if a
client submits a button event. INTAKE_ADMIN_PASSWORD is neither read nor required.

The extraction button queries saved results by company/document hash. Complete, valid
runs are loaded without GPT, including fields legitimately marked Not provided.
Missing screening is recovered from sourced extraction/observations in Python where
possible. Insufficient support stays Unknown with review issues. Recovery is saved
append-only in local_owner mode only, with the original run linked and preserved.
Database outages block extraction; they never fall back to GPT. The action handler
rechecks saved data immediately before a possible paid request.

## Explicit action and persistence

1. Select a company; the configured local-owner mode requires no password.
2. Click **Extract information and screen**. No startup, refresh, tab, category,
   check or company-selection action calls GPT.
3. The application reads selectable source text, labels each page, loads the exact
   supplied system/user prompt files at runtime, and substitutes template variables
   once. Source text is not processed as a second template. Only NB-2026-001 gets
   `is_synthetic_demo=true`; no identifiers are filled by this exception.
4. A process lock and database unique generation claim prevent concurrent/delayed
   duplicate purchases. Each subsequent run requires an explicit confirmation tied
   to the prior attempt. A stale browser action is rejected rather than regenerated.
5. One strict structured-output request is made. Raw output is retained in ignored
   `.intake_pending/` before validation. Do not publish that directory.
6. Python validates the typed response and computes the screening outcome. Valid
   output with unsupported quotations/inconsistencies is `needs_review`, not a
   successful screen. Malformed output is a failed attempt with its original response
   retained for audit; it never replaces an earlier completed result.
7. The entire run is saved using one atomic SQL INSERT. Intake uses two additive,
   namespaced tables: `submission_intake_runs_v1` and `submission_intake_claims_v1`.
   They are created only by an explicit authorised action. Ordinary loading performs
   SELECTs only, and an absent table means no saved extraction.

Runs contain case/run IDs, source version/hash, synthetic flag, model, schema hash/
version, policy version, prompt hashes/versions, configuration snapshot/version/hash, timestamps/status, full response,
available usage, original extraction/checks, review flags, issues and Python outcome.
Prior runs are append-only. A `needs_review` run is clearly labelled and cannot
silently produce Proceed when unresolved validation issues affect the screen.

On an explicit button click, matching results load from Turso even without
an OpenAI key or owner login. Source/version mismatches are marked stale and are
not populated into the current submission. Credentials are never part of run data.

If saving fails after GPT returned, refresh and use **Save retained result · no GPT
call**. That action needs owner/database access but not an OpenAI key. Retained raw
responses interrupted before validation can be validated against the unchanged
source and saved by the same recovery action. An interrupted request with no returned
response remains locked for owner recovery; the application cannot know whether the
provider completed it, so it never automatically resubmits it. Process/machine loss
between a provider response and local retention is not an exactly-once guarantee.

## Typed output and validation

`underwriting/intake/schema.py` creates a separate typed variant for each approved prompt field
ID. Scalar text, numeric fields, lists of cover sections, loss records, and
completeness objects are constrained types, not unconstrained JSON `any`. All
objects forbid extra keys. Missing/conflicting values must be null, numeric values
must be finite/nonnegative, currency/area/period units are checked, and partial ISO
dates preserve year/month precision. Loss amounts can remain null. Completeness
items have `description`, `origin` (`explicit_in_source` or `unfilled_required_field`)
and `evidence`. Loss records retain their own evidence in addition to field evidence.

Every field and SC01–SC06 must occur exactly once. Existing UI IDs stay unchanged;
explicit aliases map `number_of_employees` → `employee_count`, `country_territory` →
`country_or_territory`, `number_of_sites` → `site_count`, `requested_amount` →
`requested_bi_amount`, `basis_of_cover` → `bi_basis`, and
`reported_cancellation_avoidance_or_refusal_history` →
`cancellation_avoidance_or_refusal_history`. The runtime checks the prompt field
list against the typed schema before any paid request.

Quote matching applies Unicode NFKC, removes soft hyphens and collapses whitespace.
The normalised quotation must be a substring of the cited page's normalised text.
Case, punctuation and ordinary hyphens are not fuzzily repaired. The original
quotation is preserved. Page bounds are checked. Pass/fail requires evidence;
conflicts require competing sources and a note. Essential missing/conflicting
fields cannot silently support a pass, and SC06 cannot pass with unknown essential
checks. This checks provenance and structural consistency; it does not independently
verify source statements or prove the correctness of every model interpretation.

Confirmed SC02–SC05 failures take scope precedence. Otherwise, unknown checks,
SC01/SC06 failures or unresolved validation issues pause the workflow. All-pass
valid screening permits assessment; it is not acceptance of insurance.

## Validation commands

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe smoke_test.py
```

Tests use mock-only company data, in-memory SQLite running the production SQL, and
mock GPT responses. They cover mapping, all screening outcomes, invalid/missing/
conflicting data, evidence normalisation, append-only persistence, company/source
isolation, owner permissions, duplicates, save-only retry, UI reload without an API
key and zero GPT calls on navigation. The production Turso transport uses its
[documented HTTP pipeline](https://docs.turso.tech/sdk/http/reference); structured
output follows [OpenAI documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

Non-secret field/check definitions, display grouping, aliases, prompts, generation settings,
policy/scope and future attribute/rule settings are centralised in YAML. Restart Streamlit
after editing it. Earlier field/check JSON configuration files have been migrated, not
retained as competing sources. Saved outcomes are displayed as recorded; a different YAML
hash is disclosed without overwriting or re-interpreting the saved run.

Current interaction: startup and ordinary navigation do not load analysis from Turso
or call GPT. The button checks Turso first; saved results load without GPT. Only if
no valid saved result exists may that explicit action extract, screen and save.
Results loaded by the button remain visible in the current browser session.
# Human submission review

Underwriter notes cover basic information as a whole and individual screening checks.
`underwriting/intake/reviews.py` validates and appends confirmation or clarification requests
to `submission_human_reviews_v1` only on an explicit local-owner save. Each event
references the original intake run, source version/hash, original screening outcome
and validation issues. Confirmation acknowledges both sections without changing the
AI extraction, overriding screening, accepting insurance, or locking Assessment.
Requests require a recipient and reason and are recorded as not sent. No external
dispatch is implemented. `underwriting/ui/underwriting.intake.reviews.py` keeps drafts across check selection;
history loads only through its explicit button, and tab reset clears session drafts.
