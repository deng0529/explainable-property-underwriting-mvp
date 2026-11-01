"""Notes and explicit human confirmation/referral recording, without dispatch."""
import streamlit as st

from underwriting.configuration.settings import get_config
from underwriting.ui.intake import authorised
from underwriting.persistence.intake import StoreError
from underwriting.persistence.submission_reviews import SubmissionReviewStore
from underwriting.intake.reviews import save_submission_review, RECIPIENTS


def review_key(case, view):
    run = view.get("saved")
    return f"case:{case['case_id']}:submission_review:{run['run_id'] if run else 'not_loaded'}"


def basic_review_notes(case, view):
    st.text_area("Underwriter notes · basic information", key=review_key(case, view) + ":basic_notes",
                 disabled=not view.get("saved") or not authorised(view["settings"]), max_chars=5000,
                 help="Record corrections, missing information or questions across Company through Completeness. These notes do not change the extracted fields.",
                 placeholder="Corrections, questions for the broker, or information to verify…", persist_state="session")


def screening_review_notes(case, view, check_id):
    st.text_area("Underwriter notes · " + check_id, key=review_key(case, view) + ":check:" + check_id,
                 disabled=not view.get("saved") or not authorised(view["settings"]), max_chars=5000,
                 placeholder="Agreement, disagreement, or clarification needed for this check…", persist_state="session")


def review_controls(case, view):
    run, settings = view.get("saved"), view["settings"]
    key = review_key(case, view)
    owner = authorised(settings)
    enabled = bool(run) and owner
    st.subheader("Underwriter submission review")
    st.caption("Review both sections before continuing. Notes and review decisions are saved separately from the AI results; confirmation does not accept insurance or override screening.")
    if not run:
        st.caption("Load basic information and screening before confirming a review.")
    action = st.radio("Review decision", ["Confirm review", "Request clarification or specialist review"],
                      key=key + ":action", disabled=not enabled)
    basic = st.checkbox("I have reviewed all basic information, from Company through Completeness", key=key + ":basic_checked", disabled=not enabled)
    screening = st.checkbox("I have reviewed all six screening checks, reasons and review flags", key=key + ":screening_checked", disabled=not enabled)
    recipient, reason = None, ""
    requesting = action != "Confirm review"
    if requesting:
        recipient = st.selectbox("Intended review recipient", RECIPIENTS, key=key + ":recipient", disabled=not enabled)
        reason = st.text_area("Information or decision required", key=key + ":reason", disabled=not enabled, max_chars=5000, persist_state="session")
        st.caption("Records a review request only. No message is sent; share the recorded notes with the recipient through your normal process.")
    draft = {"basic_information_notes": st.session_state.get(key + ":basic_notes", ""),
             "screening_notes": {check["id"]: st.session_state.get(key + ":check:" + check["id"], "") for check in get_config()["screening"]["checks"]},
             "action": "request_review" if requesting else "confirm", "recipient": recipient, "reason": reason,
             "basic_information_reviewed": basic, "screening_reviewed": screening}
    label = "Save review request" if requesting else "Confirm submission review"
    if st.button(label, key=key + ":save", type="primary", disabled=not enabled):
        try:
            record = save_submission_review(settings, case, run, draft, SubmissionReviewStore(settings))
            st.session_state[key + ":saved"] = {"record": record, "draft": draft}
        except (ValueError, OSError, PermissionError, StoreError) as error:
            st.error(str(error))
    saved = st.session_state.get(key + ":saved")
    if saved:
        if saved["draft"] != draft:
            st.caption("Unsaved review changes. Save again to record the updated review.")
        elif saved["record"]["status"] == "review_confirmed":
            st.success("Submission review saved. You can now open the Assessment tab.")
            if run.get("validation_issues") or run["outcome"] != "Proceed to assessment":
                st.caption("This confirms your review only. Screening remains " + run["outcome"] + "; unresolved issues still require follow-up.")
        else:
            st.success("Review request saved for " + saved["record"]["recipient"] + ". No message has been sent.")
    with st.expander("Saved submission review history"):
        if st.button("Load saved submission reviews", key=key + ":load_history", disabled=not run or bool(settings.missing(paid=False))):
            try:
                st.session_state[key + ":history"] = SubmissionReviewStore(settings).review_history(case, run)
            except StoreError as error:
                st.error(str(error))
        history = st.session_state.get(key + ":history", [])
        if saved and not any(item["event_id"] == saved["record"]["event_id"] for item in history):
            history = [saved["record"], *history]
        for record in history:
            st.caption(record["created_at"] + " · " + record["status"].replace("_", " "))
            st.write("Basic information notes: " + (record["basic_information_notes"] or "—"))
            for identifier, note in record["screening_notes"].items():
                if note:
                    st.write(identifier + ": " + note)
            if record["recipient"]:
                st.write(record["recipient"] + ": " + record["reason"] + " · Not sent")
