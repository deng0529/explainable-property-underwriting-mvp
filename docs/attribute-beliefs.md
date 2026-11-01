# Initial attribute belief proposals

This checkpoint adds provisional Low/High/Unknown beliefs and human confirmation
for F1/F2/F3/S1/S2. No ER/BRB, group result, overall inference or acceptance logic
is implemented. Submission, evidence extraction/review and Decision are preserved.

## Meaning and configuration

These are subjective concern beliefs, not probabilities of fire/theft/loss and not
the model's confidence in its answer. Unknown is unassigned belief, not a third
severity grade. The full approved prompt lives in YAML at
`prompts.attribute_beliefs.template`, version `1.0`. It is not a separate text file.
It uses the existing attribute definitions and `belief_assessment.increment: 0.05`,
`belief_assessment.sum_tolerance: 0.000001`. Restart Streamlit after editing YAML.
Model selection remains in YAML, credentials remain in the root `.env`.

## Explicit workflow

1. Review and save evidence for every attribute. An empty evidence list can be
   confirmed as insufficient information; its gap records remain available.
2. Click **Load proposed beliefs**. The handler checks the current document hash,
   latest successful evidence run and latest saved review for each attribute in
   Turso. A pending/unconfirmed latest evidence review blocks proposals.
3. Hash the complete five-attribute set, retaining facts, exact quotations,
   attribution, gaps, contradictions, human corrections and notes. Evidence run ID
   is part of the input; human review event IDs are saved separately for version
   provenance. Repeating the same review without changing its content does not
   change the content hash.
4. Matching successful proposals load without GPT, even if current model/prompt/
   assessment settings differ. Historical settings and values are preserved and
   configuration differences are labelled. No values are silently rounded.
5. If no matching proposals exist, local-owner access permits one structured GPT
   request for all five attributes. Read-only visitors can load saved proposals but
   cannot request/save/confirm. Database outages stop the action without a GPT fallback.
6. Validate and save all five assessments atomically, then display them separately
   from human confirmations. No component is prelabelled as confirmed.

Startup/company selection/navigation/reruns never load proposals from Turso or call
GPT. Session-loaded results remain visible. Explicit proposal loading also loads its
source evidence into the existing evidence panel without running evidence extraction.
Regeneration is an owner-only expander action with a confirmation tied to the prior
attempt. Process locks and database generation claims block duplicate purchases;
SDK retries remain zero. Original responses and previous successful sets remain intact.

## Numerical and reference validation

Typed output requires five unique attribute IDs, finite numbers in [0,1], totals
within the configured tolerance and exact decimal increment membership. No repair,
rounding or renormalisation occurs. Nonzero Low/High requires evidence references;
Unknown can cite relevant evidence or documented gap IDs. References cannot cross
attributes. Zero components cannot invent supporting references. F3 must contain
compartmentation, detection and suppression findings, without subattribute scores;
other attributes have no protection-aspect findings.

Invalid responses remain failed runs with their original response and validation
errors. Quote checking is performed by evidence extraction/review; belief proposals
refer to those original IDs and retain human-reviewed interpretations alongside them.

## Human confirmation

For the selected attribute choose **Accept AI proposal**, tick confirmation and
click **Confirm beliefs**. The original proposed values are copied into a separate
human-confirmed event. No re-entry is needed, and accepting historical proposals
does not round them to the current increment.

Alternatively choose **Edit Low and High**. Controls use the configured increment;
Unknown is computed as `1 − Low − High`. A reason and explicit confirmation are
required. Invalid totals, negative Unknown or off-increment edits are rejected.

The selected attribute shows AI and confirmed distributions separately; the
redundant summary table is removed. The selector retains per-attribute statuses. Every
confirmation appends an event with values, accept/edit mode, reason, timestamp and
reviewed-evidence hash. Original AI values and earlier confirmations are immutable.

## Staleness and storage

Changing reviewed facts, notes, gaps, contradictions, confirmation status or the
evidence run invalidates the proposal set through its complete-input hash. Loaded
stale beliefs and confirmations are hidden as current and marked Stale; history is
retained. Confirmation checks the current Turso evidence again, preventing a stale
browser session from confirming obsolete beliefs. Changes during a GPT request
also mark the returned set stale rather than silently using it.

Two additive, namespaced tables reuse the existing Turso transport:
`attribute_belief_runs_v1` stores one atomic five-assessment proposal payload, and
`attribute_belief_reviews_v1` stores append-only human events. Per-attribute values
and justifications live inside the structured run payload. No startup migrations.
Runs include source/evidence IDs/hashes, review event versions, reviewed input,
model, prompt version/content/effective hashes, attribute snapshot/hash, schema
version/hash, assessment settings/hash, configuration version/hash, response, usage,
timestamps, status and errors.

If saving fails, `.belief_pending/` (Git-ignored) retains the run. **Retry save ·
beliefs · no GPT call** saves it without purchasing another response. A changed
evidence set keeps that recovered run historical/stale, never current. A failed
regeneration never replaces a previous successful proposal.

## Mock-only checks

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p test_beliefs.py -v
```

Checks cover fractional proposals, invalid totals/increments/non-finite values/IDs,
references, F3 aspects, insufficient information, reviewed-evidence gating, saved
loading, confirmations/edits, evidence staleness, outages, save-only retry and UI
navigation without extra GPT requests. No live GPT tests or live database writes.
