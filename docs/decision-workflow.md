# Decision review and report workflow

Decision is a human workflow record, not insurance acceptance, binding or pricing.
Submission and Assessment retain their existing buttons, models, prompts and stores.

## Explicit actions

1. Complete and save Submission extraction/screening, attribute evidence reviews,
   human-confirmed beliefs, Fire/Security ER and the overall BRB assessment.
2. Click **Load / Generate decision review**. Python reads current matching records
   from Turso and builds a versioned snapshot. Missing or stale required stages are
   reported; none are silently rerun. A matching successful saved review loads first,
   with zero GPT requests. Database failure stops the workflow.
3. On a cache miss, a local owner may make one GPT request. The configured client
   retains its zero-retry setting. The full prompt is
   `prompts.decision_review.template` in `config/settings.yaml`; its two placeholders
   are validated centrally. Qualitative routing guidance also lives in settings.
   Numerical rules, attributes and weights remain in `config/decision_model.yaml`.
4. Review the AI concerns, information requests and references. Schema validation and
   known reference IDs do not prove factual correctness. Human review remains essential.
5. Choose a human action (initially unselected), enter a reviewer label and notes,
   optionally add secondary actions, then **Save decision**. A different action needs
   a reason. Paused/outside screening cannot be bypassed by choosing continuation.
   Saving appends a version, links supersession and never calls GPT. A reviewer label
   is not authenticated identity; existing local_owner/public_readonly restrictions
   remain authoritative on the server.
6. **Download review report** reads/checks saved records on click and generates PDF
   locally. A report without a saved human decision is explicitly a draft. Saved
   versions identify their decision ID. Unsaved note edits are excluded. Viewing or
   exporting does not write records or call GPT.

## Versions, staleness and recovery

Snapshots include company/submission/source identifiers, extracted fields, screening
and flags, original AI facts and beliefs, operative human facts and confirmed beliefs,
review events, full ER/BRB distributions, effective weights, rule matches/activations,
stage status and historical versions. A deterministic snapshot hash links the review
and every dependent human decision. Output Unknown-state belief remains distinct
from residual unassigned output; display labels never replace distributions.

Changing source, confirmed inputs or saved calculations makes the dependent review
stale. Changes known in this session are flagged immediately. Remote changes are
checked on explicit load, decision save and report download, without background
polling on navigation. Save and download fail closed if upstream records changed.
Prompt/model changes alone preserve and may reuse historical reviews; versions remain
visible. Deliberate regeneration requires the confirmation in its expander.

The existing shared generation-claim table and a process lock prevent duplicate
requests. A failed/interrupted attempt requires save recovery or explicit confirmed
regeneration. Responses in ignored `.decision_pending/` survive a save outage; Retry
save does not call GPT. Failed regeneration preserves earlier successful reviews.
New Decision tables are created only during an explicit owner write action. No live
schema change is needed for development tests; tests use in-memory SQLite.

## Concise report template

`underwriting/decision/report.py` uses existing PyMuPDF with readable A4 text and
wrapped compact tables. A typical review is one page; legacy long content may use
two. It includes company/case/date/human-review status, a brief submission/screening
summary, Fire/Security/Overall results, key concerns, prioritised information and
next actions. Overall explicit Unknown and material residual belief stay separate.
Working notes, individual attributes, activated rules, technical hashes and the
provenance appendix stay in the app/database and are omitted from this concise PDF.
Long legacy text is excerpted with an explicit pointer to full app details; additional
material rows are counted, never silently discarded. No GPT request occurs on export.

## Reference validation failures

The structured-output schema enumerates the snapshot's exact saved reference IDs.
Each supporting-reference entry must be one bare ID, without explanatory prose or
snapshot paths. Reference validation remains enforced after the response. Earlier
failed responses are not silently accepted or rewritten. Their error details remain
visible outside progress panels; Load reopens the saved attempt without another GPT
request. An owner must explicitly confirm regeneration to request a new review.

## Saved-data prerequisites before paid generation

Load checks Turso directly for the selected company, submission version and source
hash. Earlier tabs do not need to have been opened in this session. Missing or stale
Submission data, evidence reviews, confirmed beliefs or ER/BRB results stop the action
before any GPT request or write and identify the stage needing completion.

Paused screening and saved validation issues are existing findings, not missing
records. When the upstream records are available, Decision may review those findings.
For paused screening, the structured output schema permits only information requests
or senior referral (also for secondary actions). Outside-scope screening permits only
the outside-demo-scope route. These constraints apply before GPT; post-response
validation remains enforced. Screening outcomes and original validation issues are
preserved. A Decision review never clears them or authorises continuation.

## Authoritative screening state

`underwriting/intake/screening.py` is the shared resolver used by Submission,
Assessment, Decision snapshots and reports. Six actual Pass checks resolve to
Proceed; a known SC02–SC05 scope failure resolves Outside; otherwise missing/unknown
checks or SC01/SC06 failure resolve Pause. Every pause has check-specific reasons and
clarification. General extraction citation/value-format concerns and specialist/remedial
information requests remain review concerns, not hidden overrides of six Pass checks.

The original stored intake outcome is retained as historical provenance. Existing
review confirmations/requests are not screening overrides. An explicit saved override
must identify its case/submission/source, event ID, reason and outcome and is displayed
separately from the derived check outcome. The screening resolution includes its record
ID, policy/resolver version and hash in the Decision snapshot. Old snapshot/narrative
conflicts are historical/stale and require explicit regeneration, never automatic GPT.
Submission and Decision choose the same latest exact case/submission/source record;
an explicit Decision load also refreshes any already-loaded Submission session cache.

## Current and historical display

There is one read-only AI review summary. Current results and report fields come
from a DB-backed snapshot, regardless of which tabs were opened. Historical wording
is shown only in its dated History expander, with a compact not-current status.
If no current AI review matches the saved inputs, the explicit Load / Generate
action creates it once when prerequisites are complete, without recycling older advice. Matching valid current
reviews take priority even if a later inconsistent historical record exists.

Download review report is available beside Load immediately after restart. The
click reads current Submission, resolved screening, confirmed assessments, ER/BRB,
matching AI review and saved human decision directly from Turso. Missing fields marked
not_provided are distinct from unloaded data, absent mapping keys, conflicting fields
and query failures. Mapping/query failures are errors, never Not provided fallbacks.
Historical downloads are separately labelled and use their matching original snapshot.

Technical reference sections, raw snapshots, hashes and secondary AI/limitation blocks
are retained in stored records and excluded from the main Decision view and concise
PDF. Human notes, actions, owner restrictions and saved decision history are preserved.

## Explicit current review and durable report workflow

On Load / Generate, Python queries Turso and checks current saved upstream records.
A successful review for that exact snapshot loads without GPT. When none exists and
prerequisites are complete, this explicit click makes one GPT request, validates and
saves a new current review; an older historical success is never substituted. Failed
or interrupted attempts still require save recovery or explicit confirmed regeneration
rather than an unintentional duplicate purchase. Opening a tab runs no paid request.

AI review summary and Human decision are separate inner tabs. Human draft values
are session-scoped and survive tab switches; writes occur only on Save decision.
Download review report sits below the tabs. Screening details shows all six actual
results, the resolved outcome and any blocking check/override/reason. Contradictory
historical aggregates remain unchanged; the new snapshot stores an explicit correction
object linking original record/outcome, resolved outcome and resolver version.

A successful review triggers local PDF formatting and BLOB storage in
`decision_reports`. Each immutable artifact links review ID, input snapshot hash,
PDF template version, included human-decision ID, report hash and creation date.
The unique artifact key and report lock prevent unnecessary repeated formatting.
Downloads retrieve and verify the saved bytes. If only the PDF is absent, an owner
builds/saves it from the saved review without GPT. New human decisions or template
versions produce new report versions; old draft/final versions remain available.
Public-readonly can retrieve existing artifacts but cannot create missing ones.

Report creation/save failure does not invalidate or regenerate a saved AI review or
human decision. The progress message and Retry report action recover using saved
structured content, without GPT. Received GPT responses still use the existing ignored
pending-response recovery when saving the review itself fails. No temporary local path
is used as durable report storage; `.runtime/` PDF files are development previews only.
The necessary report table is initialised only during an explicit owner action.

## Reuse across identical reruns

`decision.content.review_content_hash` identifies substantive review inputs separately
from immutable `input_snapshot_hash` audit identity. New calculation/review IDs,
timestamps, reviewer labels and repeated confirmations do not cause paid regeneration.
Legacy reviews are compared by content on read; no destructive migration or rewriting
of their snapshots is needed. A matching saved review and its existing PDF remain
current when all substantive inputs are identical, even after all stages are rerun.

The check retains company/submission and source-byte identity, provided/extracted
facts and evidence, effective screening/override, material review notes, operative
beliefs, numerical results and model definitions. Real changes are not hidden.
References and original full snapshots stay attached to the saved review for audit;
they are not silently relabelled to the newest audit run. Paid-request locks/claims
also use content identity to prevent duplicate purchases across identical reruns.
