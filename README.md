# Explainable Property Underwriting MVP

Current checkpoint: **Submission | Assessment | Decision**. The original seven-screen
layout has been replaced. Only company/attribute selection and document review work.
Future extraction, screening, beliefs, confirmation, ER/BRB, decision recording and
export are visibly disabled with blank outputs.

## Run locally (PowerShell)

From `C:\Weihua\project\explainable-property-underwriting-mvp`:

```powershell
.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

Open http://localhost:8501. Keep the terminal open; Ctrl+C stops the server.
If port 8501 is in use by this app, open the existing URL. For a separate instance,
change `--server.port` to `8502` and open http://localhost:8502.

For a fresh environment, run `python -m venv .venv`, then
`.venv\Scripts\python.exe -m pip install -r requirements.txt` first.
Direct dependencies remain Streamlit and PyMuPDF. No external services or database.

## Layout and case catalogue

- Submission: prepared summary (not AI extracted), paged company PDF viewer and
  unchanged original download; basic-information and screening placeholders.
- Assessment: grouped selectable attribute rows, definitions and blank evidence/belief
  fields; three empty result panels. Model settings, weights and future formulation
  notes are in an expander. Top-level Unknown treatment remains unspecified.
- Decision: blank concern/information fields and disabled decision controls.

`case_catalogue.json` holds company name, case ID, version, status, document path,
prepared summary/reference and expected page count. Paths may be relative to the
project folder. Only Northbridge is supplied. Future catalogue entries need unique
case IDs. There is no Add Company interface. PDF-page and attribute selections are
namespaced by case ID; placeholder widget keys also include the case ID. No review
status is advanced by navigation.

`source_document.py` reuses cached page rendering. The supplied PDF is never rewritten.
Missing source/preview errors are visible; download still works if rendering fails.

## Focused smoke check

```powershell
.venv\Scripts\python.exe smoke_test.py
```

Checks all three tab contents, all five attribute selections, company selection and
case isolation (using an in-memory test entry only), four-page navigation, exact
download bytes, blank disabled future controls and missing source/preview handling.
