# Reusable decision-model configuration

This document describes the reusable declarative configuration. The subsequent ER
checkpoint now enables the verified analytical importance-weight engine and explicit
calculation action; see er-aggregation.md. BRB is now enabled with the user-supplied
`1.0-demo` nine-rule base; see brb.md. Configuration changes
do not call GPT or rewrite historical results.

## Two configuration files

`config/settings.yaml` retains application/access defaults, model selection, generation
settings, approved GPT prompts, intake fields/screening and belief-proposal increment/
tolerance. It references `decision_model_path: config/decision_model.yaml`.
Credentials remain in the Git-ignored root `.env` or deployed secrets, never YAML.

`config/decision_model.yaml` is the single source for:

- Ordered assigned assessment grades and their definitions; unassigned belief is
  separate, displayed as Unknown and never a third ER grade.
- Attribute IDs, labels, existing definitions, enabled flags and optional evidence/
  belief aspect lists.
- ER group labels/members, relative importance weights, method identifier and an
  explicit within-group normalisation policy.
- BRB input output-references, ordered reference states and state meanings, adapter,
  input weights, output-state labels, rule antecedents/weights/conclusion beliefs,
  configured/enabled flags, method and model version/numerical tolerances.

The old competing `assessment` and `overall_rules` blocks have been removed from
settings. Attribute consumers now use `underwriting/configuration/decision_model.py`, through the existing safe
YAML loader. Restart Streamlit after configuration changes.

## Initial scaffold

Assigned grades are `low`, `high`. All five existing attributes/definitions are preserved.
Fire contains F1/F2/F3 with relative weights 1 each; Security contains S1/S2 with 1 each.
`divide_by_group_total` declares that each future local weight equals its relative
importance divided by its own group's positive total. The loader validates weights without executing calculations. The explicit ER adapter
now applies this policy when the user requests group aggregation.

ER is enabled with method `analytical_er_importance_normalized_v1`. BRB is enabled
with method `rimer_activation_analytical_er`. BRB references `er:fire` and `er:security`.
Each has `low`, `high`, `unknown` reference states and antecedent attribute weight 1.
The explicit adapter is `unassigned_to_unknown_state`: unassigned ER mass would be
represented as an insufficient-information state at this custom modelling interface.

All nine antecedent combinations exist as R01–R09 with relative rule weight 1.
Conclusions are the user-supplied illustrative research distributions in the same
YAML file; no conclusions were inferred or invented by the implementation.

`brb.output.explicit_unknown.field` is `state_beliefs.unknown`, labelled **Unknown
state (insufficient information)**. `brb.output.residual_unassigned.field` is
`residual_unassigned`, labelled **Residual unassigned output belief**.
These are distinct accounting fields; an explicit Unknown-state conclusion is not
the same as residual unassigned conclusion mass. Inference retains both separately.

## Add, remove or disable an attribute

1. Edit `attributes` in decision_model.yaml, using a unique stable ID, label,
   definition and explicit `enabled` flag. Do not duplicate the definition in settings.
2. Update relevant `er.groups.*.members` and their exact `importance_weights` keys.
   Members reference attribute IDs (or an explicit `er:group_id` dependency for future
   hierarchical scaffolding). Group references must form an acyclic graph.
3. Set optional `aspects` for grouped evidence presentation, and `belief_aspects` for
   the corresponding belief justification fields. No attribute ID is hard-coded in
   the UI, output-ID validation or extraction/assessment JSON-schema ID enums.
4. Restart Streamlit. Disabled attributes are omitted from new UI/request schemas.
   Old runs retain their original attribute snapshots and remain historical.

UI grouping is derived from ER membership; enabled attributes outside a group remain
visible under Other attributes. An enabled ER configuration cannot reference disabled
attributes. Adding/removing an attribute does not automatically regenerate evidence.
The current approved prompt text explicitly discusses five attributes; review those
YAML prompts when deliberately changing that task's scope. No Python ID-list edits
are needed, and this configuration task does not alter the approved prompts.

## Change weights or configure a rule

Change relative weights in `er.groups.<id>.importance_weights`, BRB antecedent
weights in `brb.input_attribute_weights`, and rule weights in each rule's
`rule_weight`. Weights must be finite/nonnegative; group and input totals must be
positive. There is no case-specific reliability weighting in this model.

For a rule, keep its stable ID and specify one valid state for every configured
BRB input. Each antecedent combination must be unique. Populate `conclusion_beliefs`
only with independently reviewed model-design conclusions, keyed by the configured
output states. Do not put residual unassigned mass into the explicit Unknown field.
All belief components must be finite/nonnegative and at most one; totals may be
incomplete but cannot exceed one within the configured tolerance. No automatic
normalisation or generated conclusion values occur.

Null conclusions require a disabled, unconfigured BRB block. To validate an enabled
complete rule base, select explicit method IDs, enable its ER sources, provide
conclusions and mark the BRB configured. Active rules must cover the full Cartesian
product of the inputs' states. An implemented engine must explicitly support the selected method. The ER engine
supports the configured analytical method; the BRB engine supports
`rimer_activation_analytical_er` as described in brb.md.

## Hashes and historical data

`model_metadata()` returns the model version and deterministic SHA-256 of the effective
validated model, using canonical sorted-key JSON without NaN. Mapping-key order and
YAML formatting do not affect the hash; ordered grade/reference lists are meaningful.
The effective application hash also includes the referenced decision-model hash.

New intake/evidence/belief runs record the decision-model version/hash. Existing
database rows and configuration snapshots are untouched. Historical evidence and
belief validation use saved definition/aspect snapshots. Model changes show a
configuration difference and never purchase GPT regeneration or mutate assessments.

## Focused checks

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p test_decision_model.py -v
```

Tests cover initial nine-rule scaffolding, invalid/non-finite/zero weights, duplicate
IDs/members/rules, invalid references/states, cycles, grade order, output accounting,
Cartesian coverage, conclusion totals, deterministic hashing, safe malformed-YAML
handling and dynamic attribute add/disable/remove behaviour. Test distributions are
fixtures only and never enter the real model file. No numerical inference or GPT calls.
