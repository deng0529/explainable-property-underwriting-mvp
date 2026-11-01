# Current checkpoint: Submission intake and central YAML configuration

Latest checkpoint: top-level BRB is now authorised and implemented using the supplied
1.0-demo nine-rule configuration, complete Fire/Security distributions, max-normalised
antecedent weights, explicit activation and the shared combine_er core. It runs only
on explicit action, reuses matching saved results, and preserves history and separate
Unknown/residual outputs. No GPT calls, training, deployment or Decision recording.
See docs/brb.md. Older checkpoint notes below describe their historical scope.

Latest checkpoint: reusable analytical ER group aggregation is authorised, using the
pinned desdeo-brb combination equation with preserved MIT attribution. No BRB inference.
The pure core requires explicit normalized importance shares; the YAML adapter alone
normalizes relative group weights. Calculate only from current human-confirmed beliefs,
save full distributions/input/model hashes and provenance, mark stale outputs and retain
history. No GPT calls or optimisation backends. See docs/er-aggregation.md.

Latest configuration checkpoint: reusable model scaffolding is defined only in
config/decision_model.yaml, referenced by settings.decision_model_path. Attribute
definitions, grade order, relative ER weights and disabled/unconfigured nine-rule
BRB scaffolding are validated and hashed. No inference or GPT calls are authorised
by this configuration task. Existing saved records/prompts remain unchanged.

Latest checkpoint: initial attribute belief proposals and human accept/edit/confirm
events are authorised. Use the complete YAML attribute_beliefs prompt, the full
reviewed five-attribute evidence set and saved-first Turso loading. All five evidence
reviews are required. Preserve original proposals and separate human events; changed
reviewed evidence makes dependent proposals/confirmations stale. No automatic GPT
calls or live tests. See docs/attribute-beliefs.md. ER/BRB remain unimplemented.

Latest checkpoint: attribute evidence identification and human evidence review in
Assessment are now authorised. The complete approved evidence prompt lives in YAML
under prompts.attribute_evidence; do not create a text file for it. Preserve Submission
and Decision. Use explicit saved-first loading, original full PDF text, typed validation,
versioned Turso runs/reviews and save-only recovery. No startup assessment loading or
GPT calls. Beliefs, ER/BRB and deployment remain deferred. See docs/attribute-evidence.md.

Authorised 1 November 2026. Supersedes older layout-only/implementation-not-authorised
instructions. Read PROJECT_OVERVIEW.md and README.md for context.

Implement only the explicit Submission extraction/screening action using the approved
runtime prompt files, typed validation, Python screening policy, owner protection and
append-only Turso persistence. Preserve the compact eight-category/six-check selector
layout and the separate on-demand original PDF viewer/download. Preserve stable field IDs.

Use config/settings.yaml for non-secret application settings and decision_model.yaml
for reusable attribute/ER/BRB configuration. Model selection is
llm.model, initially gpt-5-mini.
No OPENAI_MODEL environment requirement or hidden fallback. Preserve environment values;
credentials load explicitly from .env (override=False), environment or deployed secrets. No secret values in YAML, logs or Git.

No automatic GPT calls: only an explicit authorised click. No SDK retries. Duplicate
requests are blocked. Validate before presenting success; retain raw responses and allow
save-only recovery. Reload saved results without a paid key and preserve their historical
configuration/version/outcome when current YAML differs. Mark changed sources stale.

Use mocked GPT/database tests. Never perform the first live GPT extraction during
implementation. Do not change saved assessments in development. Restart Streamlit, report
configuration paths/missing setting names and the local URL, and stop.

No subagents, deployment, attribute evidence, belief assessment, ER/BRB, decision recording
or unrelated refactoring. Future configuration is explicitly not implemented.

Saved-data-first checkpoint: query Turso before any GPT action; reuse matching complete runs.
Recover missing screening from saved extraction without GPT; block on database outage.
Use access.mode local_owner on 127.0.0.1 without a password. public_readonly blocks
paid calls and writes server-side. .env is ignored and must never be printed.

Current interaction: startup and ordinary navigation do not load analysis from Turso
or call GPT. The button checks Turso first; saved results load without GPT. Only if
no valid saved result exists may that explicit action extract, screen and save.
Results loaded by the button remain visible in the current browser session.
