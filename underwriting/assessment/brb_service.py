"""Explicit saved-first BRB calculation; never reruns upstream stages or GPT."""
import copy
import uuid

from underwriting.assessment.belief_service import content_hash
from underwriting.models.brb import infer_brb, ENGINE_VERSION
from underwriting.models.brb_adapter import prepare_brb_inputs
from underwriting.configuration.decision_model import get_model, validate_model, effective_model_hash, effective_er_model_hash
from underwriting.models.er import ENGINE_VERSION as ER_ENGINE_VERSION
from underwriting.assessment.er_service import current_confirmed, input_belief_hash
from underwriting.configuration.access import require_owner
from underwriting.intake.service import now


def rule_configuration_hash(model):
    return content_hash({"brb": model["brb"], "numerical_tolerances": {
        name: model["tolerances"][name] for name in ("distribution_sum", "er_floating_cleanup")}})


def er_result_hash(run):
    return content_hash({name: run[name] for name in ("run_id", "source_hash", "source_version", "engine_version", "input_belief_hash", "groups")})


def current_er_inputs(case, er_run, store, model):
    validate_model(model)
    if not er_run or er_run.get("status") != "completed":
        raise ValueError("Calculate current Fire and Security ER results first.")
    digest, reviewed, proposal, values, _, _ = current_confirmed(case, store, model)
    if (er_run["case_id"], er_run["source_version"], er_run["source_hash"]) != (case["case_id"], case["submission_version"], digest):
        raise ValueError("ER results belong to another company, source or submission version.")
    if er_run["engine_version"] != ER_ENGINE_VERSION or effective_er_model_hash(er_run["decision_model_snapshot"]) != effective_er_model_hash(model):
        raise ValueError("ER configuration or engine changed. Calculate Fire / Security ER again.")
    if input_belief_hash(digest, reviewed, proposal, values) != er_run["input_belief_hash"]:
        raise ValueError("Confirmed attribute beliefs changed. Calculate Fire / Security ER again.")
    latest = store.latest_er_compatible(case, digest, er_run["input_belief_hash"], effective_er_model_hash(model), ER_ENGINE_VERSION)
    if not latest or latest["run_id"] != er_run["run_id"]:
        raise ValueError("A different ER result is current. Load/calculate the current Fire / Security ER results first.")
    return prepare_brb_inputs(er_run["groups"], model)


def calculate_brb(settings, case, er_run, store, model_config=None):
    model = model_config if model_config is not None else get_model()
    if not model["brb"]["enabled"] or not model["brb"]["configured"]:
        raise ValueError("BRB is not enabled/configured.")
    inputs = current_er_inputs(case, er_run, store, model)
    er_hash, rule_hash = er_result_hash(er_run), rule_configuration_hash(model)
    saved = store.latest_brb(case, er_run["source_hash"], er_run["run_id"], er_hash, rule_hash, ENGINE_VERSION)
    if saved:
        return saved, True
    try:
        require_owner(settings)  # Read-only access cannot infer or save on a cache miss.
    except PermissionError:
        raise PermissionError("Read-only access: no saved BRB result matches the current ER results and rules. A local owner must calculate and save it first.") from None
    result = infer_brb(inputs, model["brb"], distribution_tolerance=model["tolerances"]["distribution_sum"],
                       er_tolerance=model["tolerances"]["er_floating_cleanup"])
    if current_er_inputs(case, er_run, store, model) != inputs:
        raise ValueError("ER inputs changed during BRB calculation; no result was saved.")
    run = {"run_id": uuid.uuid4().hex, "case_id": case["case_id"], "source_version": case["submission_version"],
           "source_hash": er_run["source_hash"], "er_run_id": er_run["run_id"], "er_result_hash": er_hash,
           "er_input_versions": {identifier: {"er_run_id": er_run["run_id"], "group_hash": content_hash(er_run["groups"][definition["reference"][3:]]),
                                             "engine_version": ER_ENGINE_VERSION, "er_model_hash": effective_er_model_hash(model),
                                             "decision_model": copy.deepcopy(er_run["decision_model"])}
                                 for identifier, definition in model["brb"]["inputs"].items()},
           "rule_configuration": {"version": model["brb"]["version"], "sha256": rule_hash},
           "decision_model": {"version": model["model_version"], "sha256": effective_model_hash(model)},
           "decision_model_snapshot": copy.deepcopy(model), "engine_version": ENGINE_VERSION,
           "finished_at": now(), "status": result["status"], "result": result}
    store.initialise_brb()
    store.save_brb(run)
    return run, False
