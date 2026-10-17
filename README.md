# ClaimFinder MVP kit

A design and spec bundle that Claude Code can implement in one pass.

Architecture in one line: the public policy library lives on our server, and everything about a client lives only in the agent's browser. The engine runs there too; the server only extracts facts from scrubbed text and stores nothing about clients.

| Path | What it is |
|---|---|
| `DESIGN.md` | The full design: data model and relations, private intake and edition resolution, rule language, engine semantics, chat flow, LLM calls, Policy Studio (adding policies), API, UI, privacy, tests, trial ops, milestones |
| `CLAUDE.md` | Working rules for Claude Code in this repo |
| `KICKOFF.md` | The prompt to paste into Claude Code |
| `docs/data-map.html` | Diagram of the tables and relations: device store, public library, server admin, and the four public keys that connect them |
| `seed/data_classes.json` | Data classes for every field and the privacy profiles (active: device_only; reserved: encrypted_sync, server_managed) |
| `seed/catalog.json` | Insurers and product lines: edition rule (start date / last renewal / event date), name aliases for matching, renewal assumption |
| `seed/vocab.json` | Event-fact vocabulary: 29 keys, value sets, body-site → fracture-category defaults |
| `seed/slots.json` | Private per-client slots (schedule sums) and holding fields |
| `seed/cover_types.json` | Cross-insurer cover catalog with ranking weights |
| `seed/policies/aig_active_0613.json` | AIG ACTIVE 06/13: 29 clauses, 5 covers, 10 exclusions (`reviewed_demo`) |
| `seed/policies/clal_haganat_atzmit_2018.json` | Clal Medicall Haganat Atzmit 2018: 6 covers, 10 exclusions (`draft`, clause texts pending) |
| `seed/scenarios/golden.json` | 2 demo portfolios, 8 engine scenarios (S1–S7), 15 extraction-eval texts (X01–X15) |
| `reference/engine_ref.py` | Executable reference engine (`--write-golden`, `--json`) |
| `reference/validate_seed.py` | Semantic validator for the library, including proof checks: every evidence quote must be inside its clause text; page references and archived-PDF hashes are required for expert-approved editions |
| `reference/expected_output.txt` / `expected_results.json` | Golden outputs (generated) |

Quick check (Python 3.10+, no dependencies):
```
python3 reference/validate_seed.py
python3 reference/engine_ref.py S1 && python3 reference/engine_ref.py S1 --turn 1
```
