"""Explicit BRB action, distributions, activated-rule trace and numerical explanation."""
import streamlit as st

from underwriting.ui.navigation import activate_stage
from underwriting.models.brb import ENGINE_VERSION
from underwriting.models.brb_adapter import prepare_brb_inputs
from underwriting.assessment.brb_service import calculate_brb, er_result_hash, rule_configuration_hash
from underwriting.persistence.brb import BRBStore
from underwriting.configuration.decision_model import get_model, effective_er_model_hash
from underwriting.models.er import ENGINE_VERSION as ER_ENGINE_VERSION
from underwriting.ui.intake import settings_for_ui
from underwriting.persistence.intake import StoreError
from underwriting.ui.brb_presentation import input_support_summary, strongest_rule_summary, activation_summary


def brb_controls(case, prefix, er_view, button_slot):
    model, settings = get_model(), settings_for_ui()
    er_run = er_view.get("run")
    digest = er_run["source_hash"] if er_run else "not_loaded"
    key = f"{prefix}:{case['submission_version']}:{digest}:brb"
    view = st.session_state.setdefault(key, {"run": None, "stale": False})
    blocker = ""
    if not model["brb"]["enabled"] or not model["brb"]["configured"]:
        blocker = "BRB rule configuration is not enabled."
    elif not er_run or er_view.get("stale"):
        blocker = "Calculate current Fire / Security ER results first."
    else:
        try:
            if (er_run.get("case_id"), er_run.get("source_version"), er_run.get("engine_version")) != (case["case_id"], case["submission_version"], ER_ENGINE_VERSION) or effective_er_model_hash(er_run["decision_model_snapshot"]) != effective_er_model_hash(model):
                raise ValueError("ER results are not current for this company/configuration. Calculate Fire / Security ER first.")
            prepare_brb_inputs(er_run["groups"], model)
        except (ValueError, KeyError, TypeError) as error:
            blocker = str(error)
    if settings.missing(paid=False):
        blocker = (blocker + " " if blocker else "") + "Missing configuration: " + ", ".join(settings.missing(paid=False))
    if view["run"]:
        old = view["run"]
        view["stale"] = view.get("invalidated", False) or bool(er_view.get("stale")) or not er_run or old["er_result_hash"] != er_result_hash(er_run) or old["rule_configuration"]["sha256"] != rule_configuration_hash(model) or old["engine_version"] != ENGINE_VERSION
    if button_slot.button("Calculate overall BRB assessment", key=f"{prefix}:calculate_brb", width="stretch",
                          disabled=bool(blocker), help=blocker or "Load a matching saved BRB result or calculate and save numerically. No GPT call."):
        try:
            with st.status("BRB · checking current ER results and saved assessments", expanded=True) as status:
                result, reused = calculate_brb(settings, case, er_run, BRBStore(settings), model)
                view.update(run=result, stale=False, invalidated=False, origin="Turso database · no recalculation" if reused else "Numerical calculation · saved to Turso")
                activate_stage(st.session_state, prefix, "BRB results")
                status.update(label="Saved BRB assessment loaded" if reused else "BRB calculation saved", state="complete", expanded=False)
        except (ValueError, ArithmeticError, OSError, PermissionError, StoreError) as error:
            if isinstance(error, ValueError) and view.get("run"):
                view.update(stale=True, invalidated=True)
            st.error(str(error))
    if blocker:
        button_slot.caption(blocker)
    return view


def rule_rows(rules, input_ids, states):
    rows = []
    for rule in rules:
        percent = rule["activation_weight"] * 100
        row = {"Rule ID": rule["rule_id"]}
        row.update({identifier.title() + " antecedent": rule["antecedents"][identifier].title() for identifier in input_ids})
        row.update({identifier.title() + " matching degree": f"{rule['matching_degrees'][identifier]:.2f}" for identifier in input_ids})
        row["Activation percentage"] = "<0.01%" if 0 < percent < .005 else f"{percent:.2f}%"
        row.update({"Conclusion " + state.title(): f"{rule['conclusion_beliefs'][state]:.2f}" for state in states})
        rows.append(row)
    return rows


def brb_results(view):
    if view.get("stale"):
        st.warning("BRB result is stale: ER results, confirmed inputs or rules changed. Calculate again before using it as current.")
        return
    run = view.get("run")
    if not run:
        return
    result, config = run["result"], run["decision_model_snapshot"]["brb"]
    st.subheader("Overall BRB assessment")
    st.caption(config["assumptions"])
    st.caption("Result source: " + view.get("origin", "Saved assessment"))
    if result["status"] == "no_matching_rules":
        st.warning(result["message"])
    else:
        for column, (state, belief) in zip(st.columns(len(result["state_beliefs"])), result["state_beliefs"].items()):
            column.metric(state.title(), f"{belief:.2f}")
        st.text(f"Residual unassigned belief: {result['residual_unassigned']:.2f}")
    st.caption("Unknown is an explicit insufficient-information state, not Medium severity. Residual unassigned belief is separate. Display rounding does not change the calculation.")
    st.markdown("**Fire and Security ER inputs**")
    for column, (identifier, values) in zip(st.columns(len(result["input_distributions"])), result["input_distributions"].items()):
        with column, st.container(border=True):
            st.markdown("**" + identifier.title() + " ER**")
            st.table([{state.title(): f"{value:.2f}" for state, value in values.items()}])
    small = [f"{identifier.title()} {state.title()} = {value:.3g}" for identifier, values in result["input_distributions"].items() for state, value in values.items() if 0 < value < .005]
    if small:
        st.caption("Nonzero inputs rounded to 0.00: " + "; ".join(small) + ". Their full values still enter rule matching.")
    if result["status"] == "completed":
        st.markdown("**How the inputs relate to the result**")
        for line in input_support_summary(result, config):
            st.write(line)
        st.write(strongest_rule_summary(result))
        st.caption("Input support compares belief × relative input weight. It is not an additive contribution to the overall result; the configured rule conclusions and nonlinear ER combination determine that result.")
    threshold = config["active_rule_display_tolerance"]
    displayed = sorted([rule for rule in result["rules"] if rule["activation_weight"] > threshold], key=lambda rule: -rule["activation_weight"])
    hidden = [rule for rule in result["rules"] if 0 < rule["activation_weight"] <= threshold]
    st.markdown("**Activated rules**")
    st.caption(activation_summary(result))
    if displayed:
        st.table(rule_rows(displayed, config["inputs"], config["output"]["states"]))
    else:
        st.caption("No rules above the display threshold.")
    if hidden:
        st.caption(f"{len(hidden)} positive-activation rule(s) hidden only by the display threshold ({threshold:g}); all were included in the calculation.")
    with st.expander("Complete rule table"):
        st.table(rule_rows(result["rules"], config["inputs"], config["output"]["states"]))
    if result["status"] == "completed":
        st.write("The activated rules’ conclusion distributions were combined using analytical ER to produce the displayed overall assessment.")
        st.caption("Activation percentages are not additive percentage contributions to the final result: ER combination is nonlinear. No acceptance or rejection recommendation is produced.")
        with st.expander("Why these rules activated"):
            for rule in displayed:
                matches = ", ".join(f"{state.title()} {identifier.title()} ({rule['matching_degrees'][identifier]:.4g})" for identifier, state in rule["antecedents"].items())
                weights = ", ".join(f"{identifier.title()}={value:.4g}" for identifier, value in rule["relative_attribute_weights"].items())
                st.write(f"{rule['rule_id']} matches {matches}. Relative attribute weights ({weights}) and rule weight {rule['rule_weight']:.4g} produce strength {rule['strength']:.6g} and activation {rule['activation_weight'] * 100:.4g}%.")
