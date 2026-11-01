"""Conservative Python screening recovery from saved extraction; no GPT calls."""
import copy
import json
import re
import uuid

from underwriting.configuration.settings import get_config, configuration_metadata
from underwriting.configuration.access import require_owner
from underwriting.intake.schema import Check, validate_output, derive_outcome, POLICY_VERSION
from underwriting.intake.service import now, retain, retry_save


def recover_screening(saved, pages, case):
    result = copy.deepcopy(saved["result"])
    existing = result.get("screening_checks") or []
    if len({item["check_id"] for item in existing}) != len(existing):
        raise ValueError("Duplicate saved screening observations.")
    checks = {item["check_id"]: Check.model_validate(item).model_dump() for item in existing}
    fields = {field["field_id"]: field for field in result["basic_information"]}
    def field(name):
        return fields.get(name, {})
    def found(*names):
        return all(field(name).get("status") == "found" and field(name).get("value") is not None for name in names)
    def text(name):
        return str(field(name).get("value") or "").lower()
    def evidence(names):
        items = [item for name in names for item in field(name).get("evidence", [])]
        return list({json.dumps(item, sort_keys=True): item for item in items}.values())
    dependencies = {"SC01": ["company_name", "site_address"], "SC02": ["country_or_territory"],
                    "SC03": ["business_description"], "SC04": ["site_count", "occupancy"],
                    "SC05": ["requested_insurance_sections"]}
    for identifier, names in dependencies.items():
        if identifier in checks:
            continue
        outcome = "unknown"
        reason = "Saved essential information is missing, conflicting or insufficient for a deterministic check; review required."
        if found(*names):
            if identifier == "SC01":
                outcome = "pass"
            elif identifier == "SC02":
                territory = text(names[0]).strip()
                if territory in {"uk", "united kingdom", "england", "scotland", "wales", "northern ireland", "great britain"}:
                    outcome = "pass"
                elif territory in {"france", "germany", "ireland", "republic of ireland", "united states", "usa", "outside the uk", "outside uk"}:
                    outcome = "fail"
            elif identifier == "SC03":
                description = text(names[0])
                quote = " ".join(item["quote"].lower() for item in evidence(names))
                explicit_no = bool(re.search(r"(?:no|without)\s+manufactur(?:e|ing)", quote))
                if "furniture" in description and ("storage" in description or "distribution" in description) and explicit_no:
                    outcome = "pass"
                elif re.search(r"(?:includes?|undertakes?)\s+manufactur", quote):
                    outcome = "fail"
            elif identifier == "SC04":
                occupancy = text("occupancy").strip()
                if field("site_count")["value"] != 1:
                    outcome = "fail"
                elif occupancy in {"occupied", "fully occupied", "owner occupied", "owner-occupied"}:
                    outcome = "pass"
                elif occupancy in {"vacant", "unoccupied", "partly unoccupied", "partly vacant"}:
                    outcome = "fail"
            elif identifier == "SC05":
                cover = text(names[0])
                if "property damage" in cover and "no property damage" not in cover and "property damage not requested" not in cover:
                    outcome = "pass"
                elif "only" in cover and "property damage" not in cover:
                    outcome = "fail"
            if outcome != "unknown":
                reason = "Derived in Python from the saved, sourced extraction under " + POLICY_VERSION + "."
        checks[identifier] = {"check_id": identifier, "result": outcome, "reason": reason,
                              "supporting_field_ids": names, "evidence": evidence(names)}
    if "SC06" not in checks:
        determinate = all(checks[f"SC{i:02d}"]["result"] != "unknown" for i in range(1, 6))
        checks["SC06"] = {"check_id": "SC06", "result": "pass" if determinate and all(page.strip() for page in pages) else "unknown",
                          "reason": "Saved observations allow essential screening." if determinate else "Essential saved observations require clarification.",
                          "supporting_field_ids": [], "evidence": [item for check in checks.values() for item in check["evidence"]]}
    result["screening_checks"] = [checks[f"SC{i:02d}"] for i in range(1, 7)]
    parsed, issues = validate_output(result, pages, case["case_id"])
    unknown = [check["check_id"] for check in parsed["screening_checks"] if check["result"] == "unknown"]
    if unknown:
        issues.append("Saved extraction needs screening review: " + ", ".join(unknown))
    envelope = copy.deepcopy(saved)
    envelope.update(run_id=uuid.uuid5(uuid.NAMESPACE_URL, saved["run_id"] + ":screening:" + configuration_metadata()["sha256"]).hex,
                    derived_from_run_id=saved["run_id"], source_version=case["submission_version"],
                    result=parsed, outcome=derive_outcome(parsed["screening_checks"], issues),
                    validation_issues=issues, status="needs_review" if issues else "completed",
                    started_at=now(), finished_at=now(), policy_version=POLICY_VERSION,
                    screening_recovery_configuration=configuration_metadata())
    return envelope


def save_recovery(settings, store, envelope):
    require_owner(settings)
    retain(envelope)
    return retry_save(settings, None, store, envelope)
