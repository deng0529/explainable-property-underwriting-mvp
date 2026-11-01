# Top-level BRB assessment · 1.0-demo

This is an illustrative research-prototype rule base supplied by the user, not
Protector rules, validated loss probabilities or insurance acceptance criteria.
The nine conclusions are in the existing `config/decision_model.yaml`. Existing
IDs R01–R09 are preserved; they correspond in order to R1–R9 in the specification.
Both antecedent attribute weights remain 1. The stronger Fire influence comes
only from the deliberately asymmetric conclusion distributions.

## Custom Unknown interface

Fire/Security ER have assigned grades Low and High; Unknown is their unassigned
remainder. `prepare_brb_inputs` copies the entire unrounded distributions and maps
that remainder to an explicit BRB input state `unknown`, meaning insufficient
information. No scalar score, winner-only input, or interpolation is used.
This is a deliberate custom modelling interface, not a third lower-level ER grade.

BRB output `state_beliefs.unknown` is an explicit conclusion state. Any separate
ER remainder is returned as `residual_unassigned`. The two are never silently
merged; neither means Medium severity.

## Reusable numerical engine

The method was checked directly against `input_transform`,
`compute_activation_weights` and `compute_combined_belief_degrees` in
[the pinned upstream source](https://github.com/gialmisi/desdeo-brb/blob/6744efbabeb27fe15dc52c516d86c929030b230e/src/desdeo_brb/inference.py),
blob `e5b2cce81336709f5914d772da9bbcf470fabd8d`. We use conventional BRB
antecedent matching, not the extended distribution-distance matching function.
See THIRD_PARTY_NOTICES.md for the retained MIT licence and precise adaptations.
Unlike upstream scalar interpolation, our inputs already contain full distributions,
so that interpolation is deliberately bypassed. The shared per-input weight vector
is equivalent to using that vector for every upstream rule. Epsilon-free
normalisation, strict all-zero validation and explicit no-match handling are
deliberate differences; the numerical formulation remains unchanged.

`underwriting.models.brb.infer_brb(input_distributions, brb_config)` is independent of Streamlit,
Turso, OpenAI and insurance-specific input IDs. With input weights a[i], it computes:

```
relative_weight[i] = a[i] / max_i a[i]
matching[k,i] = input_distributions[i][rule[k].antecedents[i]]
strength[k] = rule_weight[k] * product_i matching[k,i] ** relative_weight[i]
activation[k] = strength[k] / sum_k strength[k]
```

A zero attribute weight contributes factor 1, including at a zero matching degree.
All-zero attribute weights are rejected. Disabled/zero-weight rules have zero
strength. No matching rules returns `no_matching_rules` with no invented distribution.
Log-scaled strength normalisation avoids falsely reporting no matches when very
small nonzero strengths underflow; any numerical underflow is recorded in the trace.

The existing `combine_activated_rule_conclusions` forwards all conclusion rows and
normalised activations to the SAME `underwriting.models.er.combine_er` used for group aggregation.
There is no duplicate ER formula and no weighted-average substitute. Complete
conclusions are supplied initially; incomplete conclusions remain supported, with
their residual unassigned belief reported separately. Input/output mass, IDs,
state references, finite non-negative weights and full Cartesian coverage are
validated. Configuration changes never trigger GPT or training.

## Explicit action and display

The fourth Assessment action is **Calculate overall BRB assessment**. It is enabled
only after current, valid ER results are loaded/calculated. Clicking checks current
source, reviewed evidence, confirmed beliefs and ER versions through Turso. It loads
an exact matching saved BRB result when available; otherwise a local owner computes
and appends a result. It never calls GPT or recalculates attribute/ER stages.
Ordinary startup/navigation neither calculates BRB nor queries/writes its records.

Successful action automatically selects BRB results. The result shows the full
Low/High/Unknown distribution, residual unassigned belief separately, activated rules
ordered by activation, and deterministic numerical explanations. All positive rules
above `brb.active_rule_display_tolerance` are shown; the complete table is expandable.
The threshold changes display only, and hidden positive rules are explicitly noted.
Activation is not an additive percentage contribution: analytical ER is nonlinear.
No acceptance/rejection recommendation is inferred from the largest state.

The display order is the overall distribution row, separate residual, side-by-side
Fire/Security ER input distributions, deterministic input/rule explanations, then
activated rules. All comparisons use the saved full-precision inputs that actually
produced the displayed result. Weight-adjusted input support is explicitly described
as an input-pattern comparison, not an additive or causal attribution of the final
nonlinear BRB output. The most activated rule and its actual conclusion distribution
connect those patterns to the overall result.

All nine rules may be active: if both inputs have nonzero Low/High/Unknown and all
weights are positive, every Cartesian antecedent pair has positive matching strength.
This is expected for full distribution inputs. Scalar interpolation's two adjacent
states per input does not apply here. Exact zero components deactivate rules that
require them (unless that input weight is zero). Small positive components displayed
as 0.00 after rounding remain in matching, with an explicit display note.

The technical Calculation details and Model settings panels are removed from the
underwriter UI. Full traces, versions and hashes remain saved. Every BRB calculation
still reads the effective model from the central YAML loader: input references and
states, weights, conclusions, method, version, thresholds and tolerances are supplied
from `config/decision_model.yaml`. Restart Streamlit after editing that YAML; no
settings change triggers an automatic GPT call or calculation.

## Persistence and staleness

`brb_service` uses `BRBStore` on the existing Turso transport. On an explicit owner
save only, it creates the additive `brb_assessment_runs_v1` table if necessary and
appends the full result/trace. Nothing is deleted or overwritten. Records include
company/source/submission identifiers, ER run/group hashes and versions, rule
version/hash, model snapshot/hash, both engine versions, output and timestamp.
Public-readonly access can load matching saved results but cannot calculate/save.

Changes to confirmed inputs, ER runs/configuration or BRB rules invalidate downstream
results. Backend checks before and after inference catch concurrent input changes.
BRB-only edits do not invalidate/recalculate ER: `effective_er_model_hash` hashes only
ER dependencies, including historical snapshots. Original full model references
remain stored. External database changes are checked on explicit action, not polled.

## Focused verification

All nine pure-state pairs reproduce their corresponding configured conclusions.
For Fire {Low .3, High .6, Unknown .1} and Security {Low .7, High .2, Unknown .1},
R01–R09 strengths are [.21, .06, .03, .42, .12, .06, .07, .02, .01]. The combined
distribution, independently checked by the exact Fraction recursive ER reference,
is Low 0.2531760782242403, High 0.6843919677956165, Unknown 0.0624319539801432,
with residual unassigned 0. These are example model outputs, not loss probabilities.

A small grid fixes Unknown for each input at 0, .1, .3 or .6 and varies High at the
expense of Low over five fractions of the remaining mass. Both directions were
checked with five fixed fractions of the other input: 640 adjacent comparisons,
zero counterexamples to nondecreasing overall High within 1e-12. This finite check
is not a proof of global monotonicity; future rule/weight edits need the same audit.
Tests report counterexamples rather than silently changing conclusions.

Tests also cover unequal/zero weights, no matching rules, invalid inputs/configuration,
display thresholds, history/reuse/staleness, readonly controls and zero GPT calls.
All persistence tests use local SQLite/mock services; no live database is migrated
or written during verification. No training/optimisation dependencies were installed.

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p test_brb.py -v
```
