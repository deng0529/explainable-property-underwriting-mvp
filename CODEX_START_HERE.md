# Current checkpoint: three-tab UI and company document review

Authorised 31 October 2026. This revised layout supersedes the earlier seven-screen
framework. Read PROJECT_OVERVIEW.md for background; implement only this checkpoint.

- Company dropdown above the workspace, initially Northbridge Furniture Storage Ltd.
  Show case NB-2026-001, submission v1.0 and current status. Store metadata and
  document paths in case_catalogue.json. Scope case state by case ID. No Add Company UI.
- Three tabs: Submission | Assessment | Decision.
- Submission: compact “Prepared summary — not AI extracted”; wider PDF viewer with
  page navigation/download on the left, basic-information and screening placeholders
  on the right. Preserve Northbridge_Broker_Submission.pdf unchanged; reuse cached previews.
- Assessment: disabled Find evidence, Propose beliefs, Calculate assessment. Grouped
  selectable F1/F2/F3 Fire and S1/S2 Security rows with blank Low/High/Unknown and
  review status. Selected attribute definition, evidence/facts/gaps/explanation and
  proposed/confirmed belief placeholders. Editing/confirmation disabled. Three blank
  result panels: Fire ER, Security ER, overall BRB. Settings in an expander; Unknown
  treatment and inference formulation remain to be specified.
- Decision: blank concerns/gaps; disabled next-action selection, notes, Save Decision
  and Export. Actions: Request information; Refer for specialist review; Continue
  underwriting review.
- Clean compact layout, one short synthetic-case notice, no fabricated outcomes.

Keep dependencies minimal. No subagents, LLM/API calls, OCR, ER/BRB calculations,
database, authentication, deployment, later stages or broad refactoring.
Update Markdown, run one focused smoke test for all tabs, attribute/company
selection and PDF access, fix actual errors, start Streamlit and provide its local
URL. Stop at this checkpoint.
