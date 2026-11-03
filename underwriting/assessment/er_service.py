"""Explicit deterministic aggregation using current human-confirmed inputs only."""
import uuid

from underwriting.assessment.belief_service import current_input, content_hash
from underwriting.configuration.decision_model import get_model, effective_model_hash, effective_er_model_hash
from underwriting.configuration.access import require_owner
from underwriting.configuration.settings import get_config
from underwriting.intake.service import now
from underwriting.models.er_adapter import aggregate_configured_group
from underwriting.models.er import ENGINE_VERSION, UPSTREAM
from underwriting.models.brb_adapter import prepare_brb_inputs


def required_members(model):
    attributes = {attribute["id"] for attribute in model["attributes"]}
    return {member for group in model["er"]["groups"].values() for member in group["members"] if member in attributes}


def input_belief_hash(digest, reviewed_hash, belief_run_id, values):
    return content_hash({"source_hash": digest, "reviewed_hash": reviewed_hash,
                         "identity_version": get_config()["reliability"]["identity_version"]+":confirmed-beliefs", "confirmed_beliefs": values})


def compatible_input_hashes(run):
    """Recognise recorded V1 identity only after checking its actual input content."""
    legacy=content_hash({'source_hash':run['source_hash'],'reviewed_hash':run['reviewed_hash'],
                         'belief_run_id':run['belief_run_id'],'confirmed_beliefs':run['confirmed_beliefs']})
    modern=input_belief_hash(run['source_hash'],run['reviewed_hash'],run['belief_run_id'],run['confirmed_beliefs'])
    return {legacy,modern} if run['input_belief_hash'] in {legacy,modern} else set()


def current_confirmed(case, store, model):
    digest, evidence, _, reviewed_hash, _ = current_input(case, store)
    proposal = store.latest_beliefs(case, digest, reviewed_hash)
    if not proposal:
        raise ValueError("No successful belief proposals match the current reviewed evidence. Load/propose and confirm beliefs first.")
    from underwriting.reliability.prerequisites import confirmed_values
    values, confirmation_ids = confirmed_values(proposal,store.belief_reviews(proposal['run_id']),required_members(model),digest,reviewed_hash)
    return digest, reviewed_hash, proposal["run_id"], values, confirmation_ids, evidence["run_id"]


def calculate_er(settings, case, store, model_config=None):
    model = model_config if model_config is not None else get_model()
    digest, reviewed, proposal_id, values, events, evidence_id = current_confirmed(case, store, model)
    input_hash = input_belief_hash(digest, reviewed, proposal_id, values)
    metadata = {"version": model["model_version"], "sha256": effective_model_hash(model)}
    try:
        require_owner(settings)
        owner = True
    except PermissionError:
        owner = False
    saved = store.latest_er_compatible(case, digest, input_hash, effective_er_model_hash(model), ENGINE_VERSION)
    if saved:
        from underwriting.reliability.diagnostics import loaded
        return loaded(saved,"ER")
    if not owner:
        if not saved:
            raise PermissionError("Read-only access: no saved ER result matches the current inputs/model. Only a local owner may calculate and save.")
        return saved
    from underwriting.reliability.prerequisites import assessment_entry
    assessment_entry(case,store,digest)
    groups = {identifier: aggregate_configured_group(identifier, values, model) for identifier in model["er"]["groups"]}
    # Recheck input versions before save; never persist a calculation as current after a concurrent edit.
    after = current_confirmed(case, store, model)
    if input_belief_hash(after[0], after[1], after[2], after[3]) != input_hash:
        raise ValueError("Confirmed beliefs changed during calculation. Calculate again; no result was saved.")
    run = {"run_id": uuid.uuid4().hex, "case_id": case["case_id"], "source_version": case["submission_version"],
           "source_hash": digest, "reviewed_hash": reviewed, "belief_run_id": proposal_id, "evidence_run_id": evidence_id,
           "input_belief_hash": input_hash, "confirmed_beliefs": values, "confirmation_event_ids": events,
           "decision_model": metadata, "decision_model_snapshot": model, "engine_version": ENGINE_VERSION,
           "er_model_hash": effective_er_model_hash(model),
           "upstream": dict(UPSTREAM), "finished_at": now(), "groups": groups,
           "brb_inputs": prepare_brb_inputs(groups, model), "status": "completed"}
    store.initialise_er()
    from underwriting.reliability.diagnostics import attach
    attach(run,"ER",source="numerical_calculation")
    store.save_er(run)
    return run
