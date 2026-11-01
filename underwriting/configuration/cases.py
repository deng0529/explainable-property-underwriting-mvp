"""Read the local case catalogue without writing case records."""
import json
from underwriting.configuration.settings import get_config, resolve_path



def load_cases():
    cases = json.loads(resolve_path(get_config()["app"]["case_catalogue_path"]).read_text(encoding="utf-8"))["cases"]
    if not cases or len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("Provide at least one case and unique case IDs.")
    for case in cases:
        for field in ("case_id", "company_name", "submission_version", "status", "document_path", "prepared_summary", "summary_reference"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                raise ValueError(f"Missing or invalid catalogue field: {field}")
    return {case["case_id"]: case for case in cases}
