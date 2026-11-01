"""Append-only human intake reviews, separate from machine extraction and screening."""
import uuid

from underwriting.configuration.settings import get_config
from underwriting.configuration.access import require_owner
from underwriting.intake.service import source_context, now

RECIPIENTS = ("Broker", "Senior underwriter", "Other department")




def save_submission_review(settings, case, run, draft, store):
    require_owner(settings)
    if not run or run.get("status") not in {"completed", "needs_review"}:
        raise ValueError("Load extracted basic information and screening before saving a review.")
    digest, _ = source_context(case)
    if (run["case_id"], run["source_version"], run["source_hash"]) != (case["case_id"], case["submission_version"], digest):
        raise ValueError("The submission source changed. Load the current submission before reviewing it.")
    check_ids = {check["id"] for check in get_config()["screening"]["checks"]}
    if set(draft.get("screening_notes", {})) != check_ids or {check["check_id"] for check in run["result"]["screening_checks"]} != check_ids:
        raise ValueError("A complete set of screening checks is required for this review.")
    text_values = [draft.get("basic_information_notes"), *draft["screening_notes"].values(), draft.get("reason")]
    if any(not isinstance(value, str) or len(value) > 5000 for value in text_values):
        raise ValueError("Review notes must be text of no more than 5,000 characters each.")
    if draft.get("action") == "confirm":
        if draft.get("basic_information_reviewed") is not True or draft.get("screening_reviewed") is not True:
            raise ValueError("Confirm that you have reviewed both basic information and all six screening checks.")
        status = "review_confirmed"
        recipient, reason = None, ""
    elif draft.get("action") == "request_review":
        recipient, reason = draft.get("recipient"), draft["reason"].strip()
        if recipient not in RECIPIENTS or not reason:
            raise ValueError("Choose a review recipient and explain what needs clarification or review.")
        status = "review_requested"
    else:
        raise ValueError("Select confirmation or a request for clarification/review.")
    record = {"schema_version": "submission-review-v1", "event_id": uuid.uuid4().hex,
              "case_id": case["case_id"], "source_version": run["source_version"], "source_hash": digest,
              "intake_run_id": run["run_id"], "created_at": now(), "status": status,
              "basic_information_notes": draft["basic_information_notes"], "screening_notes": dict(draft["screening_notes"]),
              "basic_information_reviewed": draft.get("basic_information_reviewed") is True,
              "screening_reviewed": draft.get("screening_reviewed") is True,
              "recipient": recipient, "reason": reason, "dispatch_status": "not_sent" if recipient else "not_applicable",
              "original_screening_outcome": run["outcome"], "validation_issues": list(run.get("validation_issues", [])),
              "reviewer_context": "local_owner"}
    store.save_review(record)
    return record
