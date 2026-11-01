"""Explicit, saved-first proposals from the complete reviewed evidence set."""
import copy
import hashlib
import json
import re
import threading
import uuid
from decimal import Decimal

from underwriting.configuration.settings import ROOT, get_config, configuration_metadata
from underwriting.configuration.access import require_owner
from underwriting.intake.service import now, structured_request, request_failure
from underwriting.assessment.evidence_service import document_hash
from underwriting.assessment.evidence_schema import attribute_definitions, attribute_hash
from underwriting.assessment.belief_schema import BeliefOutput, belief_output_schema, SCHEMA_VERSION, validate_beliefs, validate_values
from underwriting.configuration.decision_model import model_metadata

_guard = threading.Lock()
_locks = {}


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def reviewed_set(evidence, events):
    if not evidence or evidence["status"] != "completed":
        raise ValueError("Load valid attribute evidence before proposing beliefs.")
    latest = {event["attribute_id"]: event for event in events}
    result = copy.deepcopy(evidence["result"])
    versions = {}
    missing = []
    for attribute in result["attributes"]:
        identifier = attribute["attribute_id"]
        event = latest.get(identifier)
        if not event or not event["confirmed"] or event["run_id"] != evidence["run_id"] or event["source_hash"] != evidence["source_hash"]:
            missing.append(identifier)
            continue
        if set(event["corrected_facts"]) != {item["evidence_id"] for item in attribute["evidence_items"]}:
            raise ValueError("Human evidence corrections do not match the evidence IDs.")
        for item in attribute["evidence_items"]:
            item["human_interpretation"] = event["corrected_facts"][item["evidence_id"]]
        attribute["human_notes"] = event["notes"]
        attribute["human_reviewed"] = True
        versions[identifier] = event["event_id"]
    if missing:
        raise ValueError("Reviewed evidence required for all five attributes. Pending: " + ", ".join(missing))
    result["attributes"].sort(key=lambda item: item["attribute_id"])
    result["evidence_run_id"] = evidence["run_id"]
    return result, content_hash(result), versions


def current_input(case, store):
    digest = document_hash(case)
    evidence = store.latest_evidence(case, digest)
    if not evidence:
        raise ValueError("No saved attribute evidence exists for this company and current source.")
    reviewed, digest_reviewed, versions = reviewed_set(evidence, store.reviews(evidence["run_id"]))
    return digest, evidence, reviewed, digest_reviewed, versions


def belief_prompt(case, reviewed):
    definition = get_config()["prompts"]["attribute_beliefs"]
    values = {"belief_increment": str(get_config()["belief_assessment"]["increment"]),
              "attribute_definitions_from_yaml": json.dumps(attribute_definitions(), ensure_ascii=False, indent=2),
              "case_id_and_source_version": f"Case ID: {case['case_id']}\nSource version: {case['submission_version']}",
              "reviewed_attribute_evidence_json": json.dumps(reviewed, ensure_ascii=False, indent=2)}
    text = re.sub(r"{{(\w+)}}", lambda match: values[match[1]], definition["template"])
    return text, {"version": definition["version"], "sha256": hashlib.sha256(definition["template"].encode()).hexdigest(),
                  "effective_sha256": hashlib.sha256(text.encode()).hexdigest()}


def historical_definitions(run):
    """Older completed runs predate declarative belief-aspect IDs; preserve them."""
    definitions = copy.deepcopy(run.get("attribute_configuration", []))
    if not definitions:
        definitions = [{"id": attribute["attribute_id"]} for attribute in run["reviewed_evidence"]["attributes"]]
    proposals = {item["attribute_id"]: item for item in run["result"]["assessments"]}
    for definition in definitions:
        if "belief_aspects" not in definition:
            definition["belief_aspects"] = [row["aspect"] for row in proposals[definition["id"]]["protection_aspect_findings"]]
    return definitions


def retain_beliefs(run):
    directory = ROOT / ".belief_pending"
    directory.mkdir(exist_ok=True)
    path = directory / (run["run_id"] + ".json")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(run, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def pending_beliefs(case, digest):
    directory = ROOT / ".belief_pending"
    if not directory.exists():
        return []
    values = [json.loads(path.read_text(encoding="utf-8")) for path in directory.glob("*.json")]
    return [value for value in values if (value["case_id"], value["source_version"], value["source_hash"]) == (case["case_id"], case["submission_version"], digest)]


def retry_belief_save(settings, store, run):
    require_owner(settings)
    if run["status"] not in {"completed", "failed", "stale"}:
        raise ValueError("Interrupted belief request requires recovery; no automatic paid retry.")
    store.initialise_beliefs()
    store.save_beliefs(run)
    (ROOT / ".belief_pending" / (run["run_id"] + ".json")).unlink(missing_ok=True)
    return run


def propose_beliefs(settings, case, store, regenerate=False, confirmed=False, expected_run=None, request=None, progress=None):
    request = request or structured_request
    report = progress or (lambda stage, detail: None)
    report("Evidence", "Checking current source and the complete reviewed evidence set in Turso.")
    digest, evidence, reviewed, review_hash, versions = current_input(case, store)
    scope = (case["case_id"], case["submission_version"], digest, review_hash)
    with _guard:
        lock = _locks.setdefault(scope, threading.Lock())
    if not lock.acquire(blocking=False):
        raise ValueError("A belief request for this reviewed evidence is already running.")
    try:
        report("Database", "Checking for saved proposals linked to this reviewed evidence hash.")
        saved = store.latest_beliefs(case, digest, review_hash)
        if saved and not regenerate:
            validate_beliefs(saved["result"], saved["reviewed_evidence"], saved["assessment_settings"], definitions=historical_definitions(saved))
            return saved
        require_owner(settings)
        if {attribute["attribute_id"] for attribute in reviewed["attributes"]} != {attribute["id"] for attribute in attribute_definitions()}:
            raise ValueError("Reviewed evidence does not match the currently enabled model attributes. Deliberately update/review evidence before new proposals.")
        if regenerate and not confirmed:
            raise PermissionError("Confirm the paid belief-regeneration request.")
        previous = store.latest_beliefs(case, digest, review_hash, successful=False)
        actual = previous["run_id"] if previous else None
        if regenerate and actual != expected_run:
            raise ValueError("Belief history changed. Reload before confirming regeneration.")
        if previous and not regenerate:
            raise ValueError("A previous attempt exists; explicitly confirm regeneration or retry its save.")
        if settings.missing():
            raise ValueError("Missing configuration: " + ", ".join(settings.missing()))
        if pending_beliefs(case, digest):
            raise ValueError("A belief response is retained. Retry save without another GPT call.")
        prompt, metadata = belief_prompt(case, reviewed)
        identifier = uuid.uuid4().hex
        store.initialise_beliefs()
        generation = content_hash(["attribute-beliefs", *scope, actual])
        if not store.claim(generation, identifier, None):
            raise ValueError("This belief request was already reserved; no duplicate GPT request.")
        assessment_settings = copy.deepcopy(get_config()["belief_assessment"])
        run = {"run_id": identifier, "case_id": scope[0], "source_version": scope[1], "source_hash": digest,
               "reviewed_hash": review_hash, "reviewed_evidence": reviewed, "evidence_run_id": evidence["run_id"],
               "evidence_review_versions": versions, "prompt": metadata, "attribute_configuration_hash": attribute_hash(),
               "attribute_configuration": copy.deepcopy(attribute_definitions()),
               "assessment_settings": assessment_settings, "assessment_settings_hash": content_hash(assessment_settings),
               "schema_version": SCHEMA_VERSION, "schema_hash": content_hash(belief_output_schema()), "decision_model": model_metadata(),
               "configuration": configuration_metadata(), "model": settings.model,
               "started_at": now(), "finished_at": None, "status": "running", "result": None,
               "raw_response": None, "response_text": None, "usage": None, "validation_errors": []}
        retain_beliefs(run)
        try:
            report("GPT", "One provisional-belief request for all five reviewed attributes; no ER/BRB.")
            raw, text = request(settings, "", prompt, belief_output_schema(), "attribute_beliefs")
            run.update(raw_response=raw, response_text=text, usage=raw.get("usage"), model=raw.get("model", settings.model))
            retain_beliefs(run)
            report("Validation", "Checking totals, increments, finite values and evidence/gap references.")
            if raw.get("status", "completed") != "completed" or not text:
                raise ValueError("GPT returned an incomplete response or refusal.")
            run["result"] = validate_beliefs(json.loads(text), reviewed, assessment_settings)
            current = current_input(case, store)
            if current[0] != digest or current[3] != review_hash:
                run.update(status="stale", validation_errors=["Reviewed evidence changed during the request. Proposals are not current."])
            else:
                run["status"] = "completed"
        except Exception as error:
            run["status"] = "failed"
            run["validation_errors"] = [str(error)[:4000]] if run["raw_response"] and isinstance(error, ValueError) else [request_failure(error, settings.timeout_seconds)["message"]]
        run["finished_at"] = now()
        retain_beliefs(run)
        report("Save", "Saving the original proposal response and versioned assessment set.")
        return retry_belief_save(settings, store, run)
    finally:
        lock.release()


def confirm_belief(settings, case, store, run, attribute_id, mode, low=None, high=None, reason=""):
    require_owner(settings)
    if (run["case_id"], run["source_version"]) != (case["case_id"], case["submission_version"]):
        raise ValueError("Belief run belongs to another company or submission version.")
    if run["status"] != "completed":
        raise ValueError("Only validated proposals can be confirmed.")
    digest, _, _, reviewed_hash, _ = current_input(case, store)
    if digest != run["source_hash"] or reviewed_hash != run["reviewed_hash"]:
        raise ValueError("Reviewed evidence changed. These beliefs and confirmations are stale; deliberately reassess first.")
    proposal = next((item for item in run["result"]["assessments"] if item["attribute_id"] == attribute_id), None)
    if not proposal:
        raise ValueError("Unknown attribute ID.")
    if mode == "accept":
        # Preserve valid historic proposal fractions exactly, regardless of current increments.
        values = {name: proposal[name] for name in ("low", "high", "unknown")}
        validate_values(values, run["assessment_settings"], check_increment=False)
    elif mode == "edit":
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("A reason is required for human edits.")
        if any(isinstance(value, bool) or not isinstance(value, (int, float, Decimal)) for value in (low, high)):
            raise ValueError("Human Low and High must be numeric values.")
        values = {"low": low, "high": high, "unknown": float(Decimal(1) - Decimal(str(low)) - Decimal(str(high)))}
        values = validate_values(values)
    else:
        raise ValueError("Choose accept or edit.")
    event = {"event_id": uuid.uuid4().hex, "run_id": run["run_id"], "attribute_id": attribute_id,
             "case_id": case["case_id"], "source_hash": digest, "reviewed_hash": reviewed_hash,
             "mode": mode, "values": values, "reason": reason, "confirmed": True, "created_at": now(),
             "reviewer": "local_owner", "assessment_settings": copy.deepcopy(get_config()["belief_assessment"])}
    store.save_belief_review(event)
    return event
