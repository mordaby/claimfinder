"""Semantic validator for the public library seed (spec-by-example for Claude Code).

Checks what JSON Schema can't: every fact/slot/clause/table/ref a rule mentions must exist,
every finding-producing cover cites at least one existing clause, formula placeholders match
named values, scenario facts use legal vocab values, and so on.

Exit code 1 on errors. Warnings (e.g. a draft policy with missing clause texts) don't fail.
Usage: python validate_seed.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "seed"

OPS = {"var", "slot", "holding", "ref", "set", "fracture_pct", "not", "and", "or", "if", "eq", "ne", "gt", "gte",
       "lt", "lte", "in", "any_in", "nonempty", "days_between", "add", "sub", "mul", "div", "min", "max", "clamp"}
ARITY = {"not": 1, "if": 3, "eq": 2, "ne": 2, "gt": 2, "gte": 2, "lt": 2, "lte": 2, "in": 2, "any_in": 2,
         "nonempty": 1, "days_between": 2, "sub": 2, "div": 2, "clamp": 3}

errors, warnings = [], []


def err(where, msg):
    errors.append(f"ERROR   {where}: {msg}")


def warn(where, msg):
    warnings.append(f"warning {where}: {msg}")


def load(p):
    return json.loads((ROOT / p).read_text(encoding="utf-8"))


vocab = load("vocab.json")
keys = {k["key"]: k for k in vocab["keys"]}
sets = {k: v for k, v in vocab["sets"].items() if not k.startswith("_")}
site_default = {k: v for k, v in vocab["site_map_default"].items() if not k.startswith("_")}
slots = {s["key"]: s for s in load("slots.json")["slots"]}
holding_fields = {h["key"] for h in load("slots.json")["holding_fields"]}
cover_types = {c["id"] for c in load("cover_types.json")["cover_types"]}
catalog = load("catalog.json")
insurer_ids = {i["id"] for i in catalog["insurers"]}
products = {p["id"]: p for p in catalog["products"]}
for pid_, prod in products.items():
    if prod.get("edition_rule") not in ("start_date", "last_renewal", "event_date"):
        errors.append(f"ERROR   catalog product {pid_}: edition_rule must be start_date|last_renewal|event_date")
    if prod.get("insurer_id") is not None and prod["insurer_id"] not in insurer_ids:
        errors.append(f"ERROR   catalog product {pid_}: unknown insurer_id {prod['insurer_id']}")
    al = prod.get("aliases_he")
    if not isinstance(al, list) or not al or not all(isinstance(a, str) and a.strip() for a in al):
        errors.append(f"ERROR   catalog product {pid_}: aliases_he must be a non-empty list of strings")
    if not isinstance(prod.get("renewal_updates_edition"), bool):
        errors.append(f"ERROR   catalog product {pid_}: renewal_updates_edition must be true/false")

# ---- data classes / privacy profiles (DESIGN §3.8) -------------------------
dc = load("data_classes.json")
classes = set(dc["classes"])
for f, c in dc["fields"].items():
    if c not in classes:
        err(f"data_classes.fields.{f}", f"unknown class '{c}'")
for h in holding_fields:
    if f"holding.{h}" not in dc["fields"]:
        err("data_classes.fields", f"holding field '{h}' has no data class")
active = [n for n, pr in dc["profiles"].items() if pr["status"] == "active"]
if active != ["device_only"]:
    err("data_classes.profiles", f"exactly one active profile expected (device_only), got {active}")
for n, pr in dc["profiles"].items():
    for k in ("server_may_store", "device_may_store", "llm_may_receive"):
        for c in pr[k]:
            if c not in classes and c != "ciphertext_only":
                err(f"data_classes.profiles.{n}.{k}", f"unknown class '{c}'")
    if "identity" in pr["llm_may_receive"]:
        err(f"data_classes.profiles.{n}", "identity may never be sent to the LLM")
if dc["profiles"]["device_only"]["identity_fields_enabled"] is not False:
    err("data_classes.profiles.device_only", "identity fields must be disabled")

# ---- vocab ---------------------------------------------------------------
for k, d in keys.items():
    if d["type"] in ("enum", "enum_list"):
        vals = [v["v"] for v in d["values"]]
        if len(vals) != len(set(vals)):
            err(f"vocab.{k}", "duplicate enum values")
        for v in d["values"]:
            for r in v.get("refine_to", []):
                if r not in vals:
                    err(f"vocab.{k}.{v['v']}", f"refine_to '{r}' is not a value of {k}")
            if v.get("refine_to") and not v.get("refine_he"):
                err(f"vocab.{k}.{v['v']}", "refine_to without refine_he question")
site_values = {v["v"] for v in keys["fracture_sites"]["values"]}
for s, members in sets.items():
    bad = set(members) - site_values
    if bad:
        err(f"vocab.sets.{s}", f"not fracture_sites values: {sorted(bad)}")
missing_default = site_values - set(site_default)
if missing_default:
    err("vocab.site_map_default", f"no default category for {sorted(missing_default)}")


# ---- expressions ---------------------------------------------------------
def walk(e, where, ctx):
    """ctx: dict(policy, cover_slots(set|None), named(set), clauses(set))"""
    if isinstance(e, (bool, int, float, str)) or e is None:
        return
    if isinstance(e, list):
        for x in e:
            walk(x, where, ctx)
        return
    if not isinstance(e, dict) or len(e) != 1:
        err(where, f"expression must be a single-key object, got {json.dumps(e, ensure_ascii=False)[:80]}")
        return
    (op, a), = e.items()
    if op not in OPS:
        err(where, f"unknown op '{op}'")
        return
    if op == "var":
        if a not in keys:
            err(where, f"unknown fact key '{a}' (add it to vocab.json first)")
        return
    if op == "slot":
        if a not in slots:
            err(where, f"unknown slot '{a}'")
        elif ctx["cover_slots"] is not None and a not in ctx["cover_slots"]:
            err(where, f"slot '{a}' used but not listed in cover.slots")
        return
    if op == "holding":
        if a not in holding_fields:
            err(where, f"unknown holding field '{a}'")
        return
    if op == "ref":
        if a not in ctx["named"]:
            err(where, f"ref '{a}' is not defined earlier in rule.named")
        return
    if op == "set":
        if a not in sets:
            err(where, f"unknown set '{a}'")
        return
    if op == "fracture_pct":
        if a.get("table") not in ctx["policy"].get("tables", {}):
            err(where, f"fracture_pct table '{a.get('table')}' not in policy.tables")
        walk(a.get("sites"), where, ctx)
        return
    args = a if isinstance(a, list) else [a]
    if op in ARITY and len(args) != ARITY[op]:
        err(where, f"op '{op}' expects {ARITY[op]} args, got {len(args)}")
    for x in args:
        walk(x, where, ctx)


def check_clauses(refs, where, known):
    if not refs:
        err(where, "must cite at least one clause")
    for c in refs or []:
        if c not in known:
            err(where, f"clause '{c}' not found in policy.clauses")


# ---- policies ------------------------------------------------------------
policies = {}
for path in sorted((ROOT / "policies").glob("*.json")):
    p = json.loads(path.read_text(encoding="utf-8"))
    pid = p["policy"]["policy_id"]
    policies[pid] = p
    W = f"policy {pid}"
    if path.stem != pid:
        err(W, f"file name {path.name} != policy_id")
    status = p["policy"].get("review", {}).get("status")
    if status not in ("draft", "reviewed_demo", "expert_approved"):
        err(W, f"review.status '{status}' invalid")
    if status == "draft":
        warn(W, f"DRAFT – {len(p['policy']['review'].get('flags', []))} open review flags; findings must be labeled 'טיוטה'")
    nos = [c["clause_no"] for c in p["clauses"]]
    if len(nos) != len(set(nos)):
        err(W, "duplicate clause_no")
    known = set(nos) | {"תנאים כלליים"}
    no_text = [c["clause_no"] for c in p["clauses"] if not c.get("text")]
    if no_text:
        (warn if status == "draft" else err)(W, f"{len(no_text)} clauses without text: {no_text}")
    # ---- provenance: every clause must be traceable to a page of an archived copy of the insurer's PDF
    clause_text = {c["clause_no"]: c.get("text") or "" for c in p["clauses"]}
    src = p["policy"].get("source") or {}
    if not src.get("url"):
        err(W, "policy.source.url is required (where the wording was published)")
    strict = status == "expert_approved"
    if not src.get("sha256"):
        (err if strict else warn)(W, "source.sha256 / archived copy missing – run provenance backfill")
    unverified = [c["clause_no"] for c in p["clauses"] if c.get("text_source") not in ("pdf_text", "pdf_vision")]
    if unverified:
        (err if strict else warn)(W, f"{len(unverified)} clause texts not verified against the PDF: {unverified}")
    no_page = [c["clause_no"] for c in p["clauses"] if c.get("text") and not c.get("page_from")]
    if no_page:
        (err if strict else warn)(W, f"{len(no_page)} clauses without a page reference")
    partial = [c["clause_no"] for c in p["clauses"] if c.get("text") and not c.get("text_complete", False)]
    if partial:
        (err if strict else warn)(W, f"{len(partial)} clauses stored as excerpts, not full text: {partial}")

    def check_quote(where, clause_no, quote):
        """A quote is proof only if it is literally inside the clause text (normalized whitespace)."""
        if clause_no not in clause_text:
            err(where, f"evidence cites unknown clause '{clause_no}'")
            return
        if not quote or not quote.strip():
            err(where, "evidence quote is empty")
            return
        t = clause_text[clause_no]
        if not t:
            warn(where, f"cannot check quote: clause {clause_no} has no text yet")
            return
        norm = lambda x: " ".join(x.split())
        if norm(quote) not in norm(t):
            err(where, f"quote not found in clause {clause_no}: {quote[:50]}")

    for tname, t in p.get("tables", {}).items():
        check_clauses(t.get("clauses"), f"{W} tables.{tname}", known)
        rq = t.get("row_quotes")
        if not rq:
            (err if strict else warn)(f"{W} tables.{tname}", "row_quotes missing: each percentage needs a quote from the table in the PDF")
        else:
            for row in t["rows"]:
                if row not in rq:
                    (err if strict else warn)(f"{W} tables.{tname}", f"row '{row}' has no quote")
                else:
                    check_quote(f"{W} tables.{tname}.{row}", rq[row].get("clause"), rq[row].get("quote"))
        rows = set(t["rows"])
        for site, cats in site_default.items():
            for c in cats:
                if c not in rows and not (p.get("site_map", {}).get(site)):
                    warn(f"{W} tables.{tname}", f"default category '{c}' (for {site}) has no row -> counts as 0%")
        for site, m in p.get("site_map", {}).items():
            if site not in site_values:
                err(f"{W} site_map.{site}", "not a fracture_sites value")
            for c in m["to"]:
                if c not in rows:
                    err(f"{W} site_map.{site}", f"category '{c}' not in table rows")
            if m.get("quote"):
                check_quote(f"{W} site_map.{site}", (m.get("clauses") or [None])[0], m["quote"])
            check_clauses(m.get("clauses"), f"{W} site_map.{site}", known)

    if p.get("scope"):
        walk(p["scope"]["expr"], f"{W} scope", {"policy": p, "cover_slots": None, "named": set(), "clauses": known})
        check_clauses(p["scope"].get("clauses"), f"{W} scope", known)
    else:
        err(W, "policy.scope is required (e.g. event_cause == accident for personal_accident)")
    for fld in ("insurer_id", "product_id"):
        if not p["policy"].get(fld):
            err(W, f"policy.{fld} is required")
    if p["policy"].get("product_id") not in products:
        err(W, f"product_id '{p['policy'].get('product_id')}' not in catalog.json")
    if p["policy"].get("insurer_id") not in insurer_ids:
        err(W, f"insurer_id '{p['policy'].get('insurer_id')}' not in catalog.json")
    plan_ids = set()
    for pl in p.get("plans", []):
        PW = f"{W} plan {pl.get('plan_id')}"
        if pl.get("plan_id") in plan_ids:
            err(PW, "duplicate plan_id")
        plan_ids.add(pl.get("plan_id"))
        for k, v in pl.get("slots", {}).items():
            if k not in slots:
                err(PW, f"unknown slot '{k}'")
            if not isinstance(v, (int, float)):
                err(PW, f"slot '{k}' must be a number")
        check_clauses(pl.get("clauses"), PW, known)
    seen_types = set()
    for c in p["covers"]:
        CW = f"{W} cover {c['cover_id']}"
        if not c["cover_id"].startswith(pid + ":"):
            err(CW, "cover_id must be '<policy_id>:<cover_type>'")
        if c["cover_type"] not in cover_types:
            err(CW, f"unknown cover_type '{c['cover_type']}'")
        if c["cover_type"] in seen_types:
            err(CW, "duplicate cover_type in policy")
        seen_types.add(c["cover_type"])
        check_clauses(c.get("clauses"), CW, known)
        evid = c.get("evidence")
        if evid is None:
            err(CW, "evidence[] is required (may be empty only in draft editions)")
        elif not evid and status != "draft":
            err(CW, "non-draft covers need at least one evidence quote")
        for i, e in enumerate(evid or []):
            if not e.get("claim_he"):
                err(f"{CW} evidence[{i}]", "claim_he is required")
            check_quote(f"{CW} evidence[{i}]", e.get("clause"), e.get("quote"))
        r = c["rule"]
        ctx = {"policy": p, "cover_slots": set(c.get("slots", [])), "named": set(), "clauses": known}
        for part in ("gate", "pending_when", "ask_when"):
            if part in r:
                walk(r[part], f"{CW} rule.{part}", ctx)
        if "gate" not in r:
            err(CW, "rule.gate is required")
        for i, cond in enumerate(r.get("conditions", [])):
            walk(cond["expr"], f"{CW} condition[{i}]", ctx)
            check_clauses(cond.get("clauses"), f"{CW} condition[{i}]", known)
            if cond.get("on_unknown", "verify") not in ("verify", "ask"):
                err(f"{CW} condition[{i}]", "on_unknown must be verify|ask")
            if cond.get("on_unknown", "verify") == "verify" and not cond.get("verify_he"):
                err(f"{CW} condition[{i}]", "verify conditions need verify_he")
        for name, expr in r.get("named", {}).items():
            walk(expr, f"{CW} named.{name}", ctx)
            ctx["named"].add(name)
        if r.get("amount") is None:
            if not r.get("manual_he"):
                err(CW, "amount is null -> manual_he is required")
        else:
            walk(r["amount"], f"{CW} amount", ctx)
            placeholders = set(re.findall(r"\{(\w+)\}", r.get("formula_he", "")))
            if placeholders - ctx["named"]:
                err(CW, f"formula_he placeholders not in named: {sorted(placeholders - ctx['named'])}")
            if not r.get("formula_he"):
                err(CW, "formula_he is required when amount is set")

    for i, x in enumerate(p.get("exclusions", [])):
        XW = f"{W} exclusion[{i}] {x.get('fact') or x.get('id')}"
        if not x.get("fact") and not x.get("expr"):
            err(XW, "needs 'fact' or 'expr'")
        if x.get("fact") and x["fact"] not in keys:
            err(XW, f"unknown fact '{x['fact']}'")
        if x.get("expr") and not (x.get("id") and x.get("label_he")):
            err(XW, "expr exclusions need 'id' and 'label_he'")
        ctx = {"policy": p, "cover_slots": None, "named": set(), "clauses": known}
        for part in ("expr", "ask_when"):
            if part in x:
                walk(x[part], f"{XW} {part}", ctx)
        if x.get("ambiguous") and not x.get("note_he"):
            err(XW, "ambiguous exclusions need note_he")
        check_clauses(x.get("clauses"), XW, known)

# ---- scenarios -----------------------------------------------------------
g = load("scenarios/golden.json")


def check_fact(k, v, where):
    if k not in keys:
        err(where, f"unknown fact key '{k}'")
        return
    d = keys[k]
    vals = {x["v"] for x in d.get("values", [])}
    if d["type"] == "enum" and v not in vals:
        err(where, f"{k}={v!r} not in enum")
    if d["type"] == "enum_list" and (not isinstance(v, list) or set(v) - vals):
        err(where, f"{k}={v!r} has values outside enum")
    if d["type"] == "bool" and not isinstance(v, bool):
        err(where, f"{k} must be bool")
    if d["type"] == "number" and not isinstance(v, (int, float)):
        err(where, f"{k} must be a number")


for pfid, pf in g["portfolios"].items():
    for h in pf["holdings"]:
        HW = f"portfolio {pfid}/{h['holding_id']}"
        pol = policies.get(h["policy_id"])
        if not pol:
            err(HW, f"unknown policy {h['policy_id']}")
            continue
        types = {c["cover_type"] for c in pol["covers"]}
        if set(h["covers_bought"]) - types:
            err(HW, f"covers_bought not in policy: {sorted(set(h['covers_bought']) - types)}")
        for s in h["slots"]:
            if s not in slots:
                err(HW, f"unknown slot {s}")
for s in g["scenarios"]:
    for k, v in s.get("expected_facts", {}).items():
        check_fact(k, v, f"scenario {s['id']}")
    for t in s.get("turns", []):
        for k, v in t["answers"].items():
            check_fact(k, v, f"scenario {s['id']} turn")
    if s.get("portfolio") and s["portfolio"] not in g["portfolios"]:
        err(f"scenario {s['id']}", "unknown portfolio")

print("\n".join(warnings + errors) or "clean")
print(f"\n{len(policies)} policies, {sum(len(p['covers']) for p in policies.values())} covers, "
      f"{sum(len(p['clauses']) for p in policies.values())} clauses, {len(keys)} fact keys, {len(slots)} slots "
      f"-> {len(errors)} errors, {len(warnings)} warnings")
sys.exit(1 if errors else 0)
