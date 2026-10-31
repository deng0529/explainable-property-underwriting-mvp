# Explainable Property Underwriting MVP

Status: revised three-tab UI framework and company document review authorised on 31 October 2026. The current UI uses Submission | Assessment | Decision, superseding the earlier seven-screen layout. Only document review and UI navigation are functional; AI, human confirmation, ER/BRB inference and decision recording remain deferred.

## Purpose

Demonstrate LLM-assisted evidence identification and initial belief elicitation, human review, hierarchical ER aggregation and a top-level belief rule model. Assess selected Fire and Security concerns, not a complete commercial property risk or validated loss probability. All case details are fictional. No Protector internal methods or approval are claimed.

## Source artifact

Northbridge_Broker_Submission.pdf, version 1.0, case NB-2026-001. Four-page text-based PDF containing a broker covering note, company profile, requested cover and sums insured, construction, continuity, other exposures, operational observations, maintenance extracts, entry arrangements and losses. It deliberately includes material beyond the five model attributes. Source excerpts are contained inside the pack; separate full certificates are not supplied.

This is an original composite broker-style submission, not a reproduction of a particular insurer form or a legally complete real application. Broad document categories were informed by Allianz's publicly available Commercial Select proposal:
https://www.allianz.co.uk/content/dam/onemarketing/azuk/allianzcouk/broker/docs/products/commercial-select/general-documents/commercial-select-proposal_acom162_12.pdf
This reference supplies context only, not the fictional case facts, scoring rules or model parameters.

## Working sequence

1. Supplied source PDF is preserved unchanged.
2. CURRENT CHECKPOINT: company selection from a JSON case catalogue; Submission with prepared summary, paged PDF viewer/download and screening placeholders; Assessment with selectable grouped attributes, evidence/belief placeholders and blank result panels; Decision with disabled next-action, notes, save and export controls. Scope session state by case ID. Model settings belong in an expander. No Add Company interface. Codex must not recreate or alter the submission silently.
3. Design three prompts in this conversation and review them before implementation.
4. Transfer approved prompts and small task specifications to Codex as Markdown.
5. Implement and inspect one task at a time in Streamlit.
6. Add human review, group ER, top-level rule inference, decision recording and audit export incrementally.

The user may change the source, prompts, attributes or model after review. Preserve versions and invalidate dependent outputs accordingly. The user-requested review checkpoints determine when to advance; this is not a request to implement the whole project at once.

## Model hierarchy

| Group | Basic attributes |
|---|---|
| Fire | F1 Combustible storage; F2 Ignition controls; F3 Fire protection |
| Security | S1 Access protection; S2 Intrusion detection |

F3 combines compartmentation, detection and suppression for compact scope. Preserve their individual findings within the evidence card and explain conflicting indications.

Basic assessments use Low and High concern with unassigned belief displayed as Unknown. L + H + U = 1. LLM proposes fractional distributions directly from supplied evidence and guidance. Human reviewer confirms or edits them. Initial equal local ER weights: 1/3 for Fire attributes, 1/2 for Security attributes. ER calculates full group distributions; no scalar conversion before the next layer.

## Three LLM tasks

### Prompt 1 Submission structuring and screening assistance

Python reads selectable PDF text with page references; the LLM extracts structured company/site/cover details and proposes basic screening against an explicit demonstration policy. Preserve provenance and missing fields. No attribute beliefs in this task.

The synthetic address and absent real registration identifier are intentional. For this preloaded demo, accept the synthetic case ID and declared English location; display fictional identifiers transparently. Do not block the demonstration solely because no real postcode or registration number exists. This exemption is a demo configuration, not a real insurer rule. Unknown activity or unidentifiable site in a different case should still pause screening.

### Prompt 2 Attribute evidence identification

Read the original source, not just Prompt 1's summary. For each attribute, return exact quotations, document/page/section references, extracted facts, favourable/adverse evidence, gaps and contradictions. No numerical belief proposal. Not mentioned does not mean absent. Do not count duplicate passages as independent evidence.

### Prompt 3 Initial belief proposal

Use reviewed attribute evidence, definitions and Low/High assessment guidance. Return low/high/unknown, evidence IDs, concise rationale and review issues for each attribute. These are subjective assessment beliefs, not event probabilities or model self-confidence. Exact decimal values are not uniquely determined by the text.

Prompt text and JSON schemas remain to be drafted here. Do not treat the descriptions in this overview as final production prompts.

## Future Codex implementation checkpoints

| Checkpoint | Deliverable |
|---|---|
| PDF review UI | Display supplied PDF, version, case ID and download control; optional prepared summary clearly labelled |
| Text and Prompt 1 | Show page text, structured extraction and screening proposal |
| Prompt 2 | Show five evidence cards with source links or references |
| Prompt 3 | Show proposed distributions and explanations |
| Human review | Accept/edit facts and beliefs; keep original and revised assessments |
| Fire ER | Three confirmed inputs and complete group output |
| Security ER | Two confirmed inputs and complete group output |
| Rule design | Display and review nine proposed antecedent combinations and conclusions |
| Overall inference | Show matching strengths, activations and aggregate output |
| Decision and audit | Record human next action and export provenance/history |

## ER and BRB mathematics checkpoint

Choose and verify one ER formulation against its original source before calculation coding. Do not mix the 2002 ER algorithm and the 2013 weight/reliability rule or reuse unverified earlier numbers.

Desired top-level interface: two inputs, each displaying Low/High/Unknown; nine combinations. However, lower ER unassigned belief is not automatically an ordinary BRB grade. Specify a deliberate adapter to an explicit insufficient-information state and its semantics, or use a published incomplete-belief BRB formulation and revise the interface with the user. Keep explicit state belief separate from any residual unassigned conclusion belief. Do not label a weighted-average rule mixture as standard BRB inference.

Rule conclusions, rule weights and attribute weights are model-design parameters, reviewed and versioned independently of individual submissions. They can be initially proposed by AI but are not regenerated for every case. Define the full inference equations before implementation. Known adverse findings and missing-information flags remain visible independently of the aggregate.

## Engineering and validation

Use Python and Streamlit. Separate UI, document reading, prompt calls, schemas, validation, ER, top-level inference and storage. Start with JSON persistence. Keep credentials in secrets/environment, outside case records. Never hard-code fabricated live AI results; clearly label cached demonstrations.

Validate exact quotations, identifiers, required fields and finite belief values in [0,1]. Check totals within a declared tolerance; reject invalid AI outputs visibly rather than silently renormalising. Source instructions are data, not directions to the application. Record source hash/version, prompt/model settings, response, reviewer changes, rules and calculation version.

Changes to source invalidate extraction; evidence changes invalidate belief proposals and confirmations; confirmed belief changes invalidate group and overall results. Keep prior versions for audit. Do not silently overwrite human-confirmed values with new AI responses.

Numerical checks must include pure Low/High/Unknown, mixed and incomplete ER cases with independent reference calculations; all nine top-level pure-state combinations; repeatability; valid output accounting; sensitivity to changed inputs. One fictional case demonstrates behaviour, not underwriting superiority.

## Deferred scope

Scanned-document OCR, external retrieval, live company lookups, multiple assessors, pricing, quotation, binding and policy issuance are deferred. This overview preserves the longer project direction without requiring these features now.
