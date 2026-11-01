# ER aggregation checkpoint

Deterministic, explicit ER groups and top-level BRB are enabled. The configured
demonstration BRB uses this same core; see [brb.md](brb.md). Neither calculation needs GPT.
No training, JAX, Pyomo or optimisation backends were installed; the core is stdlib-only.

## Source verification and exact formulation

The actual upstream function and MIT licence were read at commit
`6744efbabeb27fe15dc52c516d86c929030b230e`. See
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) for the source/blob identities,
licence and modifications. Upstream labels this the analytical ER combination
(Chen et al. 2011 Eq. A-15 / Misitano 2020 Eq. 3.20), originating in the analytical
importance-weight ER approach. Its API normally receives normalized BRB rule
activations, but its product formula can combine weighted attribute assessments
when the weights have the corresponding importance-discounting interpretation.

This project explicitly chooses the normalized importance-share convention of the
revised ER mass split, not independent source reliability weights and not the 2013
weight/reliability ER rule. It is not weighted averaging. The source paper background
is Yang and Xu (2002), [DOI 10.1109/TSMCA.2002.802746](https://doi.org/10.1109/TSMCA.2002.802746),
and the upstream README cites the analytical development by Wang/Yang/Xu (2006).
The formula itself was checked directly in the inspected code and independently
derived below; no claim is made that every upstream citation/ER variant was implemented.

For assigned-grade beliefs b[i,j], row total S[i] <= 1 and supplied weights w[i]:

```
C[j] = product_i (w[i]*b[i,j] + 1 - w[i]*S[i])
R    = product_i (1 - w[i]*S[i])
Q    = product_i (1 - w[i])
A    = sum_j C[j] - (N-1)*R
D    = A - Q
combined[j] = (C[j] - R) / D
unassigned  = (R - Q) / D
```

To check applicability, construct each input's singleton masses m[j]=w*b[j],
importance-only mass B=1-w, and incomplete-assessment mass T=w*(1-S).
The total frame mass is H=B+T=1-w*S. In conjunctive combination, a grade survives
when each source supports that grade or H, excluding the all-H product. Thus its
unnormalized mass is C[j]-R. The surviving importance-only mass is Q and surviving
incomplete mass is R-Q. Their total non-conflict mass is A. Conflict normalization
divides all these quantities by A. Revised ER then removes the importance-only
mass Q/A, dividing by 1-Q/A. Cancelling A gives exactly the inspected denominator
D and the displayed equations. This removal does not discard incompleteness: R-Q
remains explicit unassigned belief. This derivation explains why importing BRB
activation-weight calculations or simply averaging attribute values would be wrong.

Assumptions: a common ordered frame of mutually exclusive assigned grades, valid
belief distributions, and configured nonnegative relative attribute importance.
Applying the ER combination is a modelling choice for attribute synthesis, not proof
of independent case facts, calibrated event probabilities or underwriting validity.

## Pure core: combine_er

`underwriting/models/er.py` imports no project modules, UI, Turso, OpenAI or insurance identifiers.
Rows are evidence assessments and columns are assigned grades. Incomplete rows are
allowed; no Unknown column is appended, and no incomplete row is rescaled to one.
Return values are assigned_beliefs, unassigned_belief and diagnostics.

Supported weights are explicit finite nonnegative combination shares in [0,1] whose
sum is one within the configured floating cleanup tolerance. The core rejects raw
relative vectors such as [1,1]. It never normalizes weights. Zero-weight rows have
no algebraic effect but still must be valid assessments. All-zero weights, invalid
totals/dimensions/non-finite values and nonpositive combination denominators raise
explicit errors. One active input returns that input's assigned/incomplete beliefs;
fully Unknown inputs return assigned zero and unassigned one.

The upstream implementation replaces zero product factors by float-tiny and divides
by D+1e-12. Those protections were inspected and deliberately not copied: they bias
otherwise exact pure/complete cases and can mask undefined input. This adaptation
uses Decimal products at precision 80, with true zero factors and explicit denominator
checks. It does not require log products or NumPy for this normalized-weight domain.

Cleanup tolerance defaults to 1e-12 and is capped at 1e-9. Only tiny excursions outside
[0,1] may be brought to the nearest boundary, with every adjustment recorded. A row
total above one by at most that tolerance has just its tiny excess subtracted from
its largest component. Incomplete rows are untouched. Larger errors are rejected.
There is no broad clipping, output renormalization or arbitrary thresholding of
positive weights. Undefined/ill-conditioned arithmetic raises rather than returning
an epsilon-dependent answer. Diagnostics include products, denominator, conflict,
supplied weight sum, row totals, active/zero rows and all cleanup operations.

## YAML adapter

`aggregate_configured_group(group_id, beliefs_by_attribute_id, model_config)` resolves
membership/grade order and uses `importance_weight / group_total` under the configured
`divide_by_group_total` policy. Only this adapter normalizes relative weights. The
core sees the resulting explicit shares. A positive normalized weight that underflows
to float zero is rejected rather than silently dropped.

Each input must provide every configured assigned grade and Unknown/unassigned mass.
The supplied Unknown must match `1-sum(assigned)` within distribution_sum tolerance;
the core derives unassigned mass from the assigned matrix, not a third grade.
Missing/disabled attributes, invalid references/methods and undefined weights fail
visibly. The adapter supports arbitrary configured grade/member counts and acyclic
nested `er:group` references. ER importance weights are never substituted for BRB
antecedent, rule or activation weights. BRB has no implemented inference path here.

## Application and storage

**Calculate Fire / Security ER** explicitly reads current evidence reviews, matching proposals
and human-confirmed belief events from Turso, rejecting incomplete/stale confirmations.
Local owners recompute the configured groups on each click; there is no GPT request.
Read-only mode may load a saved result matching current input/model/engine hashes but
cannot calculate-and-save. Startup and navigation do not query ER results.

Each group displays its human-confirmed attribute inputs and effective weights before
the aggregated distribution. All beliefs and weights are displayed to two decimal
places; calculations and saved records retain full precision. Small rounding
differences in displayed totals are expected.

Deterministic descriptions identify the strongest weighted input support for each
grade and the largest weighted information gap (input belief multiplied by effective
weight). Ties and zero support are explicit. These indicators are not an additive
decomposition of nonlinear ER, a causal attribution, or an explanation of source
facts. They require no GPT calls. Technical provenance and numerical diagnostics
remain in saved records and code/documentation, rather than in the underwriting UI.
The confirmation/input hash and model hash mark loaded results stale when visible
inputs/model change. Before saving, the backend rechecks confirmed inputs to prevent
a concurrent edit being labelled current. External changes are detected on the next
explicit calculation; there is no background database polling.

One atomic append-only row in `er_aggregation_runs_v1` stores all groups, input values,
confirmation event IDs, evidence/proposal IDs, input hash, model snapshot/version/hash,
engine version, timestamp and verified upstream provenance. Prior results are kept.
A deterministic calculation/save can be repeated without any paid request if saving
fails. No old result is silently rewritten to use a different model or input set.

## Shared ER and BRB interfaces

There is one production ER formula: `underwriting.models.er.combine_er`. `er_adapter` supplies
normalised attribute-importance weights for group aggregation.
`underwriting.models.brb_adapter.combine_activated_rule_conclusions` calls that exact same function
with externally calculated, already normalised activation weights and configured
output-state distributions. It does not compute activations or substitute antecedent
attribute weights or relative rule weights. No second ER equation exists in the BRB
adapter. Null conclusions, invalid state mappings, invalid weights and all-zero
activations are rejected rather than guessed or silently normalised.

`prepare_brb_inputs` forwards every assigned group belief, plus the unassigned
remainder as the explicit Unknown input state. It does not round, select winners or
normalise away incompleteness. New ER runs save this complete `brb_inputs` mapping
alongside their original full `groups` distributions. At the BRB output interface,
`state_beliefs.unknown` is separate from `residual_unassigned_belief` returned by ER.
The application now uses the supplied `1.0-demo` rule conclusions. `underwriting.models.brb.infer_brb`
implements matching and activation and calls this adapter for the shared ER step.

## Descriptive group labels

The largest of Low, High and unassigned Unknown is labelled Low concern, High
concern or Insufficient information. Components within the absolute configured
`tolerances.assessment_tie` of the unrounded maximum produce Mixed assessment.
The displayed degree is the maximum, including when several components tie; no
unique winning state is chosen for a tie. Full distributions remain visible and
retained at full precision. These labels are descriptions, not acceptance decisions,
and are never passed into BRB as a replacement for numeric distributions.

The model version/hash includes the new tie tolerance. Recalculate explicitly when
old ER results become stale; no GPT request or automatic regeneration is needed.

## Independent verification

`tests/test_er.py` implements a separate pairwise recursive reference with exact
Fraction arithmetic: singleton masses, importance mass B and incompleteness mass T
are combined with conflict normalization at each step, and only B is removed at the
end. It does not call the analytical core or reuse its product computation.

Tests include pure grades/Unknown, complete and incomplete mixtures, unequal/equal/
zero weights, a single active source, reordered inputs, 50 reproducible randomized
cases with 1–5 grades/1–8 inputs, invalid/non-finite/all-zero cases, cleanup boundaries,
YAML member/grade changes and mocked current-confirmation persistence without GPT.

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p test_er.py -v
```
