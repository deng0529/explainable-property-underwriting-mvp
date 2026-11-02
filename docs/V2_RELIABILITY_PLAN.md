# MVP Version 2 — Reliability upgrade

## Isolation and baseline
- Base: Version 1 main at c9caf217bf323bd18c58e42d08f840fce55c9314.
- Worktree: explainable-property-underwriting-mvp-v2; branch feature/v2-reliability.
- Never edit, switch, stop, restart, deploy or write to Version 1 or its services.
- Own .venv, ignored .env, .runtime/v2.sqlite, pending directories and port 8502.
- Default services.mode=offline: local SQLite only; paid generation disabled.
- External test services require explicit test mode, V2-only credential names,
  confirmed test scope and a configured database-host allowlist. No V1 fallback.

## Checkpoints
1. Add isolated local persistence and configuration; preserve the familiar UI.
2. Reuse a small shared prerequisite module at service/UI boundaries. Check
   current saved screening before new assessment work; never run previous stages.
   Keep the authoritative all-six-Pass resolver and explicit override policy.
3. Separate material input identity from audit run IDs. Keep historical links,
   model/prompt/configuration provenance and explicit regeneration. Database
   failures stay errors, never missing-data generation fallbacks.
4. Add explicit recovery of durably recorded responses, safe diagnostics and
   configurable conservative input envelopes without truncation. Never promise
   recovery of responses not received/persisted or automatically retry ambiguity.
5. Add synthetic offline contract fixtures/runner, separate unapproved expert
   judgement fields, focused fault tests and numerical baseline checks.
6. Validate locally, prove V1 status/HEAD/process unchanged, run V2 on 8502,
   inspect the diff/secrets, commit and push only feature/v2-reliability. No deploy.

## Acceptance
Saved results and save retries make zero GPT calls; outages cannot generate;
missing/stale prerequisites are clear; duplicate actions fail closed; fresh-session
Decision uses persisted prerequisites; screening agrees with PDF; public readonly
blocks paid calls/writes; unchanged numerical sources/config preserve ER/BRB.

## Scope
No model/rule/grade/weight changes, autonomous loop, tool registry, subagents,
production credentials/data, migrations, or new hosting. The offline app can review
local saved fixtures; absent results stay empty until an explicit offline fixture
exercise or separately configured test workflow supplies them.

## Verification completed

- 161 offline regression tests passed, including 15 V2 isolation, fault/recovery,
  content-identity, prerequisite and numerical-source parity tests.
- Focused Submission UI/document smoke check passed (one test).
- Five synthetic recorded software contracts passed; zero expert-reviewed cases,
  no expert accuracy score. Live GPT/database behavior remains untested here.
- Browser inspection confirmed V2 branding, offline mode and on-demand source PDF.
- Version 1 remained clean on main at the recorded baseline with its original
  Python processes intact. Version 2 runs separately on 127.0.0.1:8502.
- Before publishing: stage only this worktree, inspect ignored runtime/secrets,
  commit and push the feature branch; do not merge or deploy.
