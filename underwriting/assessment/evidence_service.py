"""Explicit evidence actions and review events. No beliefs or model inference."""
import hashlib
import json
import re
import threading
import uuid

from underwriting.configuration.settings import ROOT, get_config, configuration_metadata
from underwriting.configuration.access import require_owner
from underwriting.intake.service import now, source_context, structured_request, request_failure
from underwriting.documents.pdf import read_source
from underwriting.assessment.evidence_schema import EvidenceOutput, evidence_output_schema, validate_evidence, attribute_definitions, attribute_hash, EVIDENCE_SCHEMA_VERSION, TEXT_EXTRACTION_VERSION
from underwriting.configuration.decision_model import model_metadata

_guard = threading.Lock()
_locks = {}


def document_hash(case):
    return hashlib.sha256(read_source(case["document_path"])).hexdigest()


def evidence_prompt(case, pages):
    definition = get_config()["prompts"]["attribute_evidence"]
    values = {"attribute_definitions_from_yaml": json.dumps(attribute_definitions(), ensure_ascii=False, indent=2),
              "case_id_and_source_version": f"Case ID: {case['case_id']}\nSource version: {case['submission_version']}",
              "page_labelled_original_pdf_text": "\n\n".join(f"[PAGE {index}]\n{page}" for index, page in enumerate(pages, 1))}
    template = definition["template"]
    user = re.sub(r"{{(\w+)}}", lambda match: values[match[1]], template)
    metadata = {"version": definition["version"], "sha256": hashlib.sha256(template.encode()).hexdigest()}
    return user, metadata


def source_pages(case, digest, store, may_save):
    source = store.source(digest, TEXT_EXTRACTION_VERSION)
    if source:
        pages = source["pages"]
        if source["source_hash"] != digest or source["extraction_version"] != TEXT_EXTRACTION_VERSION or not pages or any(not isinstance(page, str) for page in pages):
            raise ValueError("Stored PDF text provenance is invalid.")
        if source["text_hash"] != hashlib.sha256(json.dumps(pages, ensure_ascii=False).encode()).hexdigest():
            raise ValueError("Stored PDF text hash is invalid.")
        return pages
    actual, pages = source_context(case)
    if actual != digest:
        raise ValueError("Source changed during extraction. Reload before continuing.")
    if may_save:
        store.initialise_evidence()
        store.save_source({"source_hash": digest, "extraction_version": TEXT_EXTRACTION_VERSION,
                           "pages": pages, "page_labelled_text": "\n\n".join(f"[PAGE {i}]\n{page}" for i, page in enumerate(pages, 1)),
                           "text_hash": hashlib.sha256(json.dumps(pages, ensure_ascii=False).encode()).hexdigest(),
                           "document_path": case["document_path"], "created_at": now()})
    return pages


def retained(case, digest):
    directory = ROOT / ".evidence_pending"
    if not directory.exists():
        return []
    values = [json.loads(path.read_text(encoding="utf-8")) for path in directory.glob("*.json")]
    return [value for value in values if (value["case_id"], value["source_version"], value["source_hash"]) == (case["case_id"], case["submission_version"], digest)]


def retain_evidence(run):
    directory = ROOT / ".evidence_pending"
    directory.mkdir(exist_ok=True)
    path = directory / (run["run_id"] + ".json")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def retry_evidence_save(settings, store, run):
    require_owner(settings)
    if run["status"] not in {"completed", "failed"}:
        raise ValueError("Interrupted evidence request requires owner recovery; it will not be automatically repeated.")
    store.initialise_evidence()
    store.save_evidence(run)
    (ROOT / ".evidence_pending" / (run["run_id"] + ".json")).unlink(missing_ok=True)
    return run


def find_evidence(settings, case, store, regenerate=False, confirmed=False, expected_run=None, request=None, progress=None):
    report = progress or (lambda stage, detail: None)
    request = request or structured_request
    digest = document_hash(case)
    scope = (case["case_id"], case["submission_version"], digest)
    with _guard:
        lock = _locks.setdefault(scope, threading.Lock())
    if not lock.acquire(blocking=False):
        raise ValueError("Attribute evidence is already being processed for this source.")
    try:
        report("Database", "Checking Turso for saved attribute evidence.")
        saved = store.latest_evidence(case, digest)
        if saved and not regenerate:
            try:
                require_owner(settings)
                may_save_text = True
            except PermissionError:
                may_save_text = False
            pages = source_pages(case, digest, store, may_save_text)
            validate_evidence(saved["result"], pages, definitions=saved["attribute_configuration"])
            return saved
        require_owner(settings)
        if regenerate and not confirmed:
            raise PermissionError("Confirm the paid regeneration before continuing.")
        if settings.missing():
            raise ValueError("Missing configuration: " + ", ".join(settings.missing()))
        previous = store.latest_evidence(case, digest, successful=False)
        actual = previous["run_id"] if previous else None
        if regenerate and expected_run != actual:
            raise ValueError("Evidence history changed. Reload before confirming regeneration.")
        if previous and not regenerate:
            raise ValueError("A previous evidence attempt exists. Load its details or explicitly confirm regeneration.")
        if retained(case, digest):
            raise ValueError("A retained evidence response needs Retry save; GPT was not called.")
        store.initialise_evidence()
        report("Source", "Loading full page-labelled original PDF text, with matching source provenance.")
        pages = source_pages(case, digest, store, True)
        prompt, metadata = evidence_prompt(case, pages)
        generation = hashlib.sha256(json.dumps(["attribute-evidence", *scope, actual]).encode()).hexdigest()
        identifier = uuid.uuid4().hex
        if not store.claim(generation, identifier, None):
            raise ValueError("This evidence request was already reserved. No duplicate GPT request was made.")
        run = {"run_id": identifier, "case_id": scope[0], "source_version": scope[1], "source_hash": digest,
               "text_extraction_version": TEXT_EXTRACTION_VERSION, "schema_version": EVIDENCE_SCHEMA_VERSION,
               "schema_hash": hashlib.sha256(json.dumps(evidence_output_schema(), sort_keys=True).encode()).hexdigest(),
               "decision_model": model_metadata(),
               "prompt": metadata, "attribute_configuration_hash": attribute_hash(),
               "attribute_configuration": attribute_definitions(), "configuration": configuration_metadata(),
               "model": settings.model, "started_at": now(), "finished_at": None, "status": "running",
               "result": None, "raw_response": None, "response_text": None, "usage": None, "validation_errors": []}
        retain_evidence(run)
        try:
            report("GPT", "One structured request for all five attributes; no beliefs or scores.")
            raw, text = request(settings, "", prompt, evidence_output_schema(), "attribute_evidence")
            run.update(raw_response=raw, response_text=text, usage=raw.get("usage"), model=raw.get("model", settings.model))
            retain_evidence(run)
            report("Validation", "Checking all five IDs, references, page ranges and exact source quotations.")
            if raw.get("status", "completed") != "completed" or not text:
                raise ValueError("GPT returned incomplete evidence or a refusal.")
            run["result"] = validate_evidence(json.loads(text), pages)
            run["status"] = "completed"
        except Exception as error:
            if run["raw_response"]:
                run["validation_errors"] = [str(error)[:4000]] if isinstance(error, ValueError) else ["Evidence validation failed."]
            else:
                run["validation_errors"] = [request_failure(error, settings.timeout_seconds)["message"]]
            run["status"] = "failed"
        run["finished_at"] = now()
        retain_evidence(run)
        report("Save", "Saving the complete evidence run and original response to Turso.")
        return retry_evidence_save(settings, store, run)
    finally:
        lock.release()


def review_attribute(settings, store, run, attribute_id, corrected_facts, notes, confirmed, reviewer_name="Local owner"):
    require_owner(settings)
    if not isinstance(reviewer_name, str) or not reviewer_name.strip() or len(reviewer_name.strip()) > 100:
        raise ValueError("Enter a reviewer name of 1–100 characters.")
    if run["status"] != "completed":
        raise ValueError("Only validated evidence can be reviewed.")
    attribute = next((a for a in run["result"]["attributes"] if a["attribute_id"] == attribute_id), None)
    if not attribute:
        raise ValueError("Unknown attribute ID.")
    expected = {item["evidence_id"] for item in attribute["evidence_items"]}
    if set(corrected_facts) != expected or any(not isinstance(value, str) or not value.strip() for value in corrected_facts.values()):
        raise ValueError("Each evidence ID needs an interpreted fact; original source quotations cannot be edited.")
    event = {"event_id": uuid.uuid4().hex, "run_id": run["run_id"], "case_id": run["case_id"],
             "source_hash": run["source_hash"], "attribute_id": attribute_id, "created_at": now(),
             "corrected_facts": corrected_facts, "notes": notes, "confirmed": bool(confirmed),
             "reviewer": "local_owner", "reviewer_name": reviewer_name.strip(), "reviewer_identity_type": "self_reported", "evidence_version": run["run_id"]}
    store.save_review(event)
    return event
