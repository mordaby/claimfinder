# CLAUDE.md — working rules for this repo

Read `DESIGN.md` fully before writing code. Spec precedence:
1. `reference/` (executable spec);
2. `DESIGN.md`;
3. your judgment. Log each judgment call as one line in `DECISIONS.md`.

## Non-negotiables
- **Privacy is configuration, not code paths.** Every field has a class in `seed/data_classes.json`. The store, scrubber, LLM payload builder, telemetry, feedback and export decide by asking that registry and the active profile (DESIGN §3.8). No module except `IndexedDbStore` touches IndexedDB. Only `device_only` is implemented; don't build the reserved profiles.
- **Client data never reaches the server.** Portfolios, slots, holding dates, sessions, runs and exports live only in the browser's IndexedDB. The server stores only the collections in DESIGN §3.5.1, and never logs request or response bodies. Manual mode sends nothing about the case.
- The LLM never computes amounts and never decides eligibility. Only `frontend/src/engine/` does (TS port of `reference/engine_ref.py`, running in the browser). It is pure: no I/O, clock or randomness.
- Every finding cites ≥ 1 clause of its edition and carries the proof chain (DESIGN §5.2). Covers without clauses or evidence must fail validation.
- A quote is proof only if it is literally inside the clause text. Never paraphrase a quote, never "fix" one to make it match, and never set `text_source: "pdf_text"` unless the text was matched against the PDF's text layer. Unverified items stay visibly unverified in the UI.
- No client names, ID numbers, phone numbers or policy numbers anywhere, not even on the device. Run the scrubber in the browser before any text leaves the device, and again on the server.
- Imported files (Har HaBituach .xlsx, M7 schedule PDFs) are parsed in the browser and never uploaded. Only the whitelisted fields are kept (DESIGN §3.6).
- The app has no availability or opening-hours logic. The owner starts and stops `make trial` by hand.
- `reference/expected_results.json` and `reference/expected_output.txt` are generated files. Never hand-edit them. If the reference engine is wrong, fix `reference/engine_ref.py`, run `python reference/engine_ref.py --write-golden`, and explain the change in `DECISIONS.md`.
- Seeds in `seed/` are the library source of truth. Change them only via a validated edit (`python reference/validate_seed.py` must report 0 errors).
- Draft editions (`review.status == "draft"`) are always labeled "טיוטה" in the UI and the export.

## Conventions
- Backend: Python 3.12, FastAPI, pydantic v2, pymongo `AsyncMongoClient`. LLM calls go only through `app/llm/router.py` and its providers: `anthropic` SDK, `openai` SDK (Responses API, strict JSON schema), `claude_cli` (`claude -p`), `codex_cli` (`codex exec`) and `fake` (DESIGN §7.1). No vendor SDK imports outside `app/llm/providers/`.
- CLI providers spend the owner's personal plan. Never route agent-facing tasks (`extract`, `ask`) to them in trial mode; the server must refuse to start if configured that way.
- Frontend: React + Vite + TypeScript, Hebrew RTL; `idb` for IndexedDB, `xlsx` (SheetJS) for Excel import, `pdfjs-dist` for M7, vitest for unit and golden tests. All Hebrew strings go in `frontend/src/i18n/he.ts` and `frontend/src/chat/templates_he.ts`.
- Mongo binds to `127.0.0.1`. The DB name comes from `MONGO_DB` (default `claimfinder`). Tests use `claimfinder_test` and drop it afterwards.
- `LLM_MODE=fake` for tests, E2E and the demo. Live calls only in `make eval`, `make smoke` and real Policy Studio jobs.
- Don't add a deploy target, Docker or cloud services. Local only, exposed by a tunnel.

## Commands
- `make setup` · `make seed` · `make dev` · `make serve` · `make test` · `make e2e` · `make eval` · `make trial`.
- Before every commit, run `make test`. Before finishing a milestone, run `make test && make e2e`.

## Process
- Implement milestones M1 → M7 from `DESIGN.md §16` in order, committing after each with green tests.
- Don't stop to ask questions. Decide, log the decision in `DECISIONS.md`, and continue.
- Never approve a policy edition yourself. Approval in Policy Studio (M4) is a human action. Take the `aig_active_0817` job up to the review screen, then leave it for review.
- If no API key is set, build everything with `LLM_MODE=fake`. List the live-only checks (eval, M4 live job, smoke) for the human to run.
