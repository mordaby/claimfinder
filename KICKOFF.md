# Kickoff prompt — paste this into Claude Code in an empty folder that contains this kit

```
You are implementing the ClaimFinder MVP described in DESIGN.md, in this folder.

1. Read CLAUDE.md, then DESIGN.md end to end. Start with §0.2 and §3.5: client data stays on the agent's device,
   and the engine runs in the browser. Then read reference/engine_ref.py, reference/validate_seed.py,
   seed/vocab.json, seed/policies/*.json and seed/scenarios/golden.json.
2. `git init`, then commit the kit as-is ("kit"). Run `python3 reference/validate_seed.py` and
   `python3 reference/engine_ref.py --write-golden`, then `git status`. The expected files must not change.
   If they change, stop and tell me why.
3. Implement milestones M1 → M7 (DESIGN.md §16) in order. After each milestone: make test (and make e2e from M3),
   then commit with a message "M<n>: …". Log every judgment call as one line in DECISIONS.md.
   The app has no opening-hours logic: I start and stop `make trial` myself.
4. Environment: MongoDB is already installed and running locally on 127.0.0.1:27017. Don't use Docker or deploy anything.
   Put the API keys in .env (ANTHROPIC_API_KEY and/or OPENAI_API_KEY; I will provide them). The CLI providers
   (claude_cli / codex_cli) use my own logged-in plans on this machine. Use LLM_MODE=fake for all tests and the demo video.
5. When M7 is done, give me:
   (a) the commands to run the trial;
   (b) the path of the demo video produced by e2e/demo.spec.ts;
   (c) the extraction eval report from `make eval`;
   (d) the Policy Studio URL of the aig_active_0817 job, waiting for my review (never approve it yourself);
   (e) a short list of the decisions you made.
```

Notes for the human:
- Have ready: an Anthropic and/or OpenAI API key, Node 20+, Python 3.12, and a tunnel tool (`cloudflared` or `ngrok`).
- For plan-token runs (Studio jobs, eval): be logged in to Claude Code (`claude auth status` shows `claude.ai`) and/or Codex CLI on this machine.
- First run on your machine:
  1. `make setup && make seed && make test`
  2. `make invite AGENCY="דמו" USES=3`
  3. `make trial`
