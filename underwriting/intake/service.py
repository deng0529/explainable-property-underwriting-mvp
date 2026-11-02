"""Explicit single-request intake, durable save-only recovery and versioned prompts."""
import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone

import pymupdf
from openai import OpenAI, APITimeoutError, APIConnectionError, APIStatusError

from underwriting.configuration.access import ROOT, require_owner
from underwriting.configuration.settings import get_config, resolve_path, configuration_metadata
from underwriting.configuration.decision_model import model_metadata
from underwriting.intake.schema import IntakeOutput, POLICY_VERSION, SCHEMA_VERSION, schema_hash, validate_output, derive_outcome, fields_definition, UI_TO_PROMPT
from underwriting.documents.pdf import read_source

_locks_guard = threading.Lock()
_locks = {}


def now():
    return datetime.now(timezone.utc).isoformat()


def request_failure(error, timeout):
    """Return non-secret diagnostics, never exception bodies, URLs or headers."""
    if isinstance(error, APITimeoutError):
        return {"kind": "timeout", "message": f"GPT request exceeded the {timeout:g}-second timeout. No automatic retry was made; any new request requires your explicit confirmation."}
    if isinstance(error, APIConnectionError):
        return {"kind": "connection", "message": "Could not connect to OpenAI. Check network/proxy connectivity. No automatic retry was made."}
    if isinstance(error, APIStatusError):
        status = error.status_code
        guidance = {400: "OpenAI rejected the request format, schema or generation settings.",
                    401: "OpenAI authentication failed. Check OPENAI_API_KEY.",
                    403: "OpenAI denied access. Check project/model permissions.",
                    404: "The configured model or API resource is unavailable.",
                    429: "OpenAI quota or rate limit was reached. Check billing and rate limits."}
        return {"kind": "api_status", "http_status": status,
                "message": f"HTTP {status}: " + guidance.get(status, "OpenAI returned a service error.") + " No automatic retry was made."}
    return {"kind": "unexpected", "message": "GPT request failed before a response was returned. No automatic retry was made."}


def source_context(case):
    content = read_source(case["document_path"])
    with pymupdf.open(stream=content, filetype="pdf") as document:
        pages = [page.get_text() for page in document]
    if not pages or not any(page.strip() for page in pages):
        raise ValueError("No selectable PDF text. Supply a readable PDF; OCR is not implemented.")
    return hashlib.sha256(content).hexdigest(), pages


def prompt_context(case, pages):
    definitions = get_config()["prompts"]
    paths = [resolve_path(definitions[name]["path"]) for name in ("system", "user")]
    texts = [path.read_text(encoding="utf-8") for path in paths]
    values = {"case_id": case["case_id"], "source_version": case["submission_version"],
              "is_synthetic_demo": "true" if case["case_id"] == get_config()["screening"]["scope"]["synthetic_exception_case_id"] else "false",
              "page_labelled_pdf_text": "\n\n".join(f"[PAGE {index}]\n{text}" for index, text in enumerate(pages, 1))}
    # Substitute once; do not process template-like text inside the untrusted PDF.
    template = texts[1]
    field_section = template.split("PREDEFINED BASIC FIELDS", 1)[-1].split("SPECIAL FIELD INSTRUCTIONS", 1)[0]
    declared = re.findall(r"^- ([a-z_]+)\s*$", field_section, re.MULTILINE)
    expected = {UI_TO_PROMPT.get(field["id"], field["id"]) for field in fields_definition()}
    if set(declared) != expected or len(declared) != len(expected):
        raise ValueError("The approved prompt field list and typed/UI schema do not match.")
    names = set(re.findall(r"{{(\w+)}}", template))
    if names != set(values):
        raise ValueError("The approved user template is missing required placeholders or contains unknown placeholders.")
    user = re.sub(r"{{(\w+)}}", lambda match: values[match[1]], template)
    return texts[0], user, {path.name: {"version": definitions[name]["version"], "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                            for name, path in zip(("system", "user"), paths)}


def request_once(settings, system, user):
    return structured_request(settings, system, user, IntakeOutput.model_json_schema(), "submission_intake")


def structured_request(settings, system, user, schema, name):
    require_owner(settings)
    # One explicit API request. No SDK retries, tools, fallback model or repair request.
    parameters = dict(settings.generation)
    if "reasoning_effort" in parameters:
        parameters["reasoning"] = {"effort": parameters.pop("reasoning_effort")}
    with OpenAI(api_key=settings.api_key, max_retries=get_config()["llm"]["max_retries"], timeout=settings.timeout_seconds) as client:
        response = client.responses.create(model=settings.model, store=False,
            input=([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}],
            text={"format": {"type": "json_schema", "name": name, "strict": True,
                             "schema": schema}}, **parameters)
        return response.model_dump(mode="json"), response.output_text


def pending_directory():
    directory = ROOT / ".intake_pending"
    directory.mkdir(exist_ok=True)
    return directory


def retain(envelope):
    target = pending_directory() / (envelope["run_id"] + ".json")
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(envelope, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(target)


def pending(case_id, source_version, source_hash):
    directory = ROOT / ".intake_pending"
    if not directory.exists():
        return []
    results = []
    for path in directory.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if (value.get("case_id"), value.get("source_version"), value.get("source_hash")) == (case_id, source_version, source_hash):
                results.append(value)
        except (OSError, ValueError):
            continue
    return sorted(results, key=lambda value: value["started_at"])


def retry_save(settings, proof, store, envelope, case=None):
    require_owner(settings, proof)
    if envelope["status"] == "running" and envelope.get("raw_response") and case:
        digest, pages = source_context(case)
        if (case["case_id"], case["submission_version"], digest) != (envelope["case_id"], envelope["source_version"], envelope["source_hash"]):
            raise ValueError("Cannot recover against a changed source. Restore the original source first.")
        try:
            result, issues = validate_output(json.loads(envelope["response_text"]), pages, case["case_id"])
            envelope.update(result=result, validation_issues=issues,
                            status="needs_review" if issues else "completed",
                            outcome=derive_outcome(result["screening_checks"], issues), finished_at=now())
        except (ValueError, TypeError):
            envelope.update(status="failed", error="Retained response failed validation.", finished_at=now())
        retain(envelope)
    if envelope["status"] not in {"completed", "needs_review", "failed"}:
        raise ValueError("This request did not finish. Its audit is retained; do not repeat it automatically.")
    store.initialise(proof)
    store.save(envelope, proof)
    path = pending_directory() / (envelope["run_id"] + ".json")
    path.unlink(missing_ok=True)
    return envelope


def run_intake(settings, proof, case, store, expected_run=None, confirmed=False, request=None, progress=None):
    report = progress or (lambda stage, detail: None)
    report("Source", "Reading the selected PDF and page-labelled text.")
    require_owner(settings, proof)
    request = request or request_once
    digest, pages = source_context(case)
    system, user, prompts = prompt_context(case, pages)
    key = (case["case_id"], case["submission_version"], digest)
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise ValueError("An intake request for this source is already running.")
    try:
        if pending(*key):
            raise ValueError("A previous response is retained. Retry its save without calling GPT again.")
        # Recheck Turso in the action handler, before any GPT request or schema write.
        # A database outage propagates; it is never treated as an empty history.
        report("Database", "Checking Turso history before any GPT request.")
        latest = store.latest(case["case_id"], case["submission_version"], digest, successful=False)
        from underwriting.intake.screening import current_screening_record
        current = current_screening_record(case,store,digest)
        candidates = [current] if current else []
        if candidates and not confirmed:
            saved = candidates[0]
            from underwriting.intake.recovery import recover_screening, save_recovery
            identifiers = [item.get("check_id") for item in saved["result"].get("screening_checks", [])]
            if len(identifiers) == 6 and set(identifiers) == {f"SC{i:02d}" for i in range(1, 7)} and saved.get("outcome"):
                validate_output(saved["result"], pages, case["case_id"])
                return saved
            return save_recovery(settings, store, recover_screening(saved, pages, case))
        actual = latest["run_id"] if latest else None
        if actual != expected_run:
            raise ValueError("The intake history changed. Refresh and explicitly confirm a new run.")
        if latest and not confirmed:
            raise ValueError("Explicit confirmation is required to regenerate this source.")
        if settings.missing():
            raise ValueError("Missing configuration: " + ", ".join(settings.missing()))
        store.initialise(proof)
        report("Request protection", "Reserving this run and checking for duplicate requests.")
        run_id = uuid.uuid4().hex
        generation_key = hashlib.sha256(json.dumps([*key, expected_run]).encode()).hexdigest()
        if not store.claim(generation_key, run_id, proof):
            raise ValueError("This action was already requested. Refresh or recover the pending request; GPT was not called again.")
        envelope = {"run_id": run_id, "case_id": key[0], "source_version": key[1], "source_hash": digest,
                    "synthetic_exception": key[0] == get_config()["screening"]["scope"]["synthetic_exception_case_id"], "started_at": now(), "finished_at": None,
                    "status": "running", "schema_version": SCHEMA_VERSION, "schema_hash": schema_hash(),
                    "policy_version": POLICY_VERSION, "prompts": prompts, "model": settings.model,
                    "configuration": configuration_metadata(), "generation_settings": settings.generation,
                    "decision_model": model_metadata(),
                    "configuration_snapshot": get_config(), "output_schema": IntakeOutput.model_json_schema(),
                    "timeout_seconds": settings.timeout_seconds,
                    "raw_response": None, "response_text": None, "usage": None, "result": None,
                    "validation_issues": [], "outcome": None, "error": None}
        retain(envelope)
        try:
            report("GPT", f"Requesting {settings.model}; timeout {settings.timeout_seconds:g}s, automatic retries disabled.")
            raw, text = request(settings, system, user)
            envelope.update(raw_response=raw, response_text=text, usage=raw.get("usage"),
                            model=raw.get("model", settings.model))
            # Persist the raw response before validation, so validation/save cannot lose paid output.
            retain(envelope)
            report("Validation", "GPT responded. Checking required fields, six checks, units and source quotations.")
            if raw.get("status", "completed") != "completed" or not text:
                raise ValueError("Model refused or returned incomplete output. Review the retained run; no automatic retry.")
            result, issues = validate_output(json.loads(text), pages, case["case_id"])
            envelope.update(result=result, validation_issues=issues,
                            outcome=derive_outcome(result["screening_checks"], issues),
                            status="needs_review" if issues else "completed")
        except Exception as error:
            # Do not include SDK request/credential details in UI or audit errors.
            diagnostic = request_failure(error, settings.timeout_seconds)
            message = "Response validation failed; review the retained response." if envelope["raw_response"] else diagnostic["message"]
            envelope.update(status="failed", error=message)
            report("Validation failed" if envelope["raw_response"] else "GPT failed", message)
            envelope["error_diagnostics"] = {"kind": "response_validation"} if envelope["raw_response"] else diagnostic
            if envelope["raw_response"]:
                envelope["validation_issues"] = [type(error).__name__ + ": " + str(error)[:1000]] if isinstance(error, (ValueError, TypeError)) else [message]
        envelope["finished_at"] = now()
        retain(envelope)
        report("Save", "Saving the complete run and original response to Turso.")
        return retry_save(settings, proof, store, envelope)
    finally:
        lock.release()


def revalidate_saved(settings, case, store, saved):
    """Explicit recovery: unchanged original response, new audit run, no GPT request."""
    import copy
    require_owner(settings)
    digest, pages = source_context(case)
    if saved["case_id"] != case["case_id"] or saved["source_hash"] != digest:
        raise ValueError("Restore the original source before revalidating this run.")
    result, issues = validate_output(json.loads(saved["response_text"]), pages, case["case_id"])
    recovered = copy.deepcopy(saved)
    recovered.update(run_id=uuid.uuid4().hex, derived_from_run_id=saved["run_id"],
                     started_at=now(), finished_at=now(), status="needs_review" if issues else "completed",
                     result=result, validation_issues=issues, outcome=derive_outcome(result["screening_checks"], issues),
                     error=None, recovery_action="revalidate_unchanged_response", configuration=configuration_metadata())
    retain(recovered)
    return retry_save(settings, None, store, recovered)
