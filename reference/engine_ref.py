"""Reference coverage engine (spec-by-example for Claude Code).

Pure, deterministic, no LLM, no DB. Reads the seed JSON and evaluates one event
against one client portfolio. The production engine must reproduce this file's
semantics exactly (see DESIGN.md §5) and pass reference/expected_output.txt.

Value model
  - Unknown values are UNK and carry `needs` (which fact/slot/holding keys would decide them).
  - Numbers are closed intervals Num(lo, hi). Exact numbers have lo == hi.
    Intervals come from ambiguity (e.g. a body site that maps to 35% or 100%,
    or an `if` whose condition is still unknown). Such a value is "provisional":
    it is known as a range but still carries needs.
  - Booleans use Kleene three-valued logic.

Usage:  python engine_ref.py [scenario_id] [--turn N] [--json]
        python engine_ref.py --write-golden   # regenerates expected_output.txt + expected_results.json
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "seed"
VOCAB = json.load(open(ROOT / "vocab.json", encoding="utf-8"))
COVER_TYPES = {c["id"]: c for c in json.load(open(ROOT / "cover_types.json", encoding="utf-8"))["cover_types"]}
SLOTS = {s["key"]: s for s in json.load(open(ROOT / "slots.json", encoding="utf-8"))["slots"]}
FACT_KEYS = {k["key"]: k for k in VOCAB["keys"]}


class _Unk:
    def __repr__(self):
        return "UNK"


UNK = _Unk()


@dataclass(frozen=True)
class Num:
    lo: float
    hi: float

    @property
    def exact(self):
        return self.lo == self.hi

    def fmt(self, money=False):
        f = (lambda x: f"{x:,.0f} ₪") if money else (lambda x: f"{x:g}")
        return f(self.lo) if self.exact else f"{f(self.lo)} – {f(self.hi)}"


@dataclass
class Ctx:
    facts: dict
    holding: dict
    policy: dict
    named: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    used: set = field(default_factory=set)        # fact keys whose known values were read (proof chain)


def num(x):
    return Num(float(x), float(x))


def ev(e, ctx: Ctx):
    """Evaluate an expression. Returns (value, needs:set[str])."""
    if isinstance(e, bool) or e is None or isinstance(e, str):
        return e, set()
    if isinstance(e, (int, float)):
        return num(e), set()
    if isinstance(e, list):
        vals, needs = [], set()
        for x in e:
            v, n = ev(x, ctx)
            vals.append(v)
            needs |= n
        return vals, needs
    (op, a), = e.items()

    if op == "var":
        if a not in FACT_KEYS:
            raise KeyError(f"unknown fact key {a}")
        v = ctx.facts.get(a)
        if v is None:
            return UNK, {f"fact:{a}"}
        ctx.used.add(a)
        t = FACT_KEYS[a]["type"]
        if t == "number":
            return num(v), set()
        if t == "date":
            return date.fromisoformat(v), set()
        return v, set()
    if op == "slot":
        if a not in SLOTS:
            raise KeyError(f"unknown slot {a}")
        v = ctx.holding["slots"].get(a)
        return (UNK, {f"slot:{ctx.holding['holding_id']}:{a}"}) if v is None else (num(v), set())
    if op == "holding":
        v = ctx.holding.get(a)
        if v is None:
            return UNK, {f"holding:{ctx.holding['holding_id']}:{a}"}
        return (date.fromisoformat(v) if a.endswith("_date") else v), set()
    if op == "ref":
        return ctx.named[a]
    if op == "set":
        return VOCAB["sets"][a], set()

    if op == "fracture_pct":
        return _fracture_pct(a, ctx)

    pairs = [ev(x, ctx) for x in (a if isinstance(a, list) else [a])]
    args = [p[0] for p in pairs]
    needs = set().union(*(p[1] for p in pairs))

    if op == "not":
        v = args[0]
        return (UNK, needs) if v is UNK else (not v, set())
    if op in ("and", "or"):
        dominant = op == "or"            # True dominates OR, False dominates AND
        if any(v is dominant for v in args):
            return dominant, set()
        if any(v is UNK for v in args):
            return UNK, set().union(*(n for v, n in pairs if v is UNK))
        return (not dominant), set()
    if op == "if":
        (cond, cn), (x, xn), (y, yn) = pairs
        if cond is True:
            return x, xn
        if cond is False:
            return y, yn
        if isinstance(x, Num) and isinstance(y, Num):  # unknown condition -> hull, still needs the condition
            return Num(min(x.lo, y.lo), max(x.hi, y.hi)), cn | xn | yn
        return UNK, needs
    if any(v is UNK for v in args):
        return UNK, needs

    if op in ("eq", "ne"):
        x, y = args
        if isinstance(x, Num) and isinstance(y, Num):
            if not (x.exact and y.exact):
                return UNK, needs
            r = x.lo == y.lo
        else:
            r = x == y
        return (r if op == "eq" else not r), set()
    if op in ("gt", "gte", "lt", "lte"):
        x, y = args
        if isinstance(x, date):
            x, y = num(x.toordinal()), num(y.toordinal())
        sure_true = {"gt": x.lo > y.hi, "gte": x.lo >= y.hi, "lt": x.hi < y.lo, "lte": x.hi <= y.lo}[op]
        sure_false = {"gt": x.hi <= y.lo, "gte": x.hi < y.lo, "lt": x.lo >= y.hi, "lte": x.lo > y.hi}[op]
        if sure_true:
            return True, set()
        if sure_false:
            return False, set()
        return UNK, needs
    if op == "in":
        return args[0] in args[1], set()
    if op == "any_in":
        return bool(set(args[0]) & set(args[1])), set()
    if op == "nonempty":
        return len(args[0]) > 0, set()
    if op == "days_between":
        return num((args[1] - args[0]).days), set()
    if op in ("add", "mul"):
        lo, hi = (0.0, 0.0) if op == "add" else (1.0, 1.0)
        for v in args:  # all operands are non-negative in this domain
            lo, hi = (lo + v.lo, hi + v.hi) if op == "add" else (lo * v.lo, hi * v.hi)
        return Num(lo, hi), needs
    if op == "sub":
        x, y = args
        return Num(x.lo - y.hi, x.hi - y.lo), needs
    if op == "div":
        x, y = args
        return Num(x.lo / y.hi, x.hi / y.lo), needs
    if op == "min":
        return Num(min(v.lo for v in args), min(v.hi for v in args)), needs
    if op == "max":
        return Num(max(v.lo for v in args), max(v.hi for v in args)), needs
    if op == "clamp":
        x, lo_, hi_ = args
        c = lambda z: min(max(z, lo_.lo), hi_.hi)
        return Num(c(x.lo), c(x.hi)), needs
    raise ValueError(f"unknown op {op}")


def _refinable(site):
    return any(v["v"] == site and v.get("refine_to") for v in FACT_KEYS["fracture_sites"]["values"])


def _fracture_pct(spec, ctx: Ctx):
    """Sum of table percentages over distinct reported sites, capped. A site that maps to several
    categories yields a range. If the ambiguity is in the POLICY wording (site_map.ambiguous) it is a
    review note, not a question; if it is in the EVENT (e.g. 'vertebra' without body/arch) we ask."""
    sites, n = ev(spec["sites"], ctx)
    if sites is UNK:
        return UNK, n
    table = ctx.policy["tables"][spec["table"]]
    lo = hi = 0.0
    for s in dict.fromkeys(sites):
        m = ctx.policy.get("site_map", {}).get(s)
        cats = m["to"] if m else VOCAB["site_map_default"][s]
        vals = [table["rows"].get(c, 0) for c in cats]
        lo, hi = lo + min(vals), hi + max(vals)
        if len(set(vals)) > 1:
            why = m.get("note_he") if m else "המיקום לא מפורט מספיק – לברר עם הסוכן"
            opts = " או ".join(f"{c} {table['rows'].get(c, 0)}%" for c in cats)
            ctx.notes.append(f"{s}: {opts} — {why}")
            if not (m and m.get("ambiguous")):
                n = n | {f"refine:fracture_sites={s}" if _refinable(s) else "fact:fracture_sites"}
    cap = table.get("cap_pct", 100)
    return Num(min(lo, cap), min(hi, cap)), n


def ask_keys(needs):
    """Needs that become questions to the agent: unknown facts ('fact:k') or a vague known value
    that can be refined ('refine:k=v'). Slot needs are never chat questions: they go to the slot form."""
    return sorted(x.split(":", 1)[1] for x in needs if x.startswith(("fact:", "refine:")))


def slot_needs(needs):
    return sorted(x for x in needs if x.startswith("slot:"))


def question_text(k):
    if "=" in k:                                   # refinement of a vague enum value
        key, val = k.split("=", 1)
        return next(v["refine_he"] for v in FACT_KEYS[key]["values"] if v["v"] == val)
    return FACT_KEYS[k].get("ask_he", FACT_KEYS[k]["label_he"] + "?")


def label_of(x):
    if x.get("label_he"):
        return x["label_he"]
    return FACT_KEYS[x["fact"]]["label_he"] if x.get("fact") in FACT_KEYS else x.get("id")


# Question priority: tier first (1 = exclusion that could zero a whole policy, 2 = gate/condition that could
# unlock a cover, 3 = detail that only narrows an amount), then money at stake (desc), then key (stable).
TIER_EXCLUSION, TIER_GATE, TIER_AMOUNT = 1, 2, 3


def _add_q(questions, k, tier, stake, why):
    q = questions.setdefault(k, {"tier": tier, "stake": 0.0, "why": []})
    q["tier"] = min(q["tier"], tier)
    q["stake"] += stake
    q["why"].append(why)


def run(portfolio, facts):
    findings, questions, checklist, out_of_scope = [], {}, [], []
    for h in portfolio["holdings"]:
        pol = json.load(open(ROOT / "policies" / f"{h['policy_id']}.json", encoding="utf-8"))
        pname = pol["policy"]["name_he"]
        base = Ctx(facts=facts, holding=h, policy=pol)

        # 0) scope: PA policies pay for accidents only. False -> whole holding out of scope (silent + note).
        #    Unknown -> tier-1 question, and every finding of this holding stays provisional.
        scope_ask = []
        if pol.get("scope"):
            sv, sn = ev(pol["scope"]["expr"], base)
            if sv is False:
                out_of_scope.append({"holding": h["holding_id"], "insurer": pol["policy"]["insurer"],
                                     "label": pol["scope"]["label_he"], "clauses": pol["scope"]["clauses"]})
                continue
            if sv is UNK:
                scope_ask = ask_keys(sn)
                for k in scope_ask:
                    _add_q(questions, k, TIER_EXCLUSION, 0, f"{pname}: תנאי סף {pol['scope']['clauses'][0]}")

        # 1) policy-level exclusions
        blocked, flags = [], []
        for x in pol["exclusions"]:
            v, n = ev(x.get("expr", {"var": x.get("fact")}), base)
            if v is True:
                (flags if x.get("ambiguous") else blocked).append(x)
            elif v is UNK:
                aw = ev(x["ask_when"], base)[0] if "ask_when" in x else UNK
                if aw is True:                       # relevant in this context -> ask, before anything else
                    for k in ask_keys(n):
                        _add_q(questions, k, TIER_EXCLUSION, 0, f"{pname}: חריג {x['clauses'][0]}")
                elif aw is False:                    # context makes it irrelevant (motorcycle, fall at home)
                    continue
                else:                                # generic exclusion -> verify list, never a question
                    checklist.append({"label": label_of(x), "insurer": pol["policy"]["insurer"], "clauses": x["clauses"]})

        # 2) covers the client actually bought
        for c in pol["covers"]:
            ct = c["cover_type"]
            if ct not in h["covers_bought"]:
                continue
            r = c["rule"]
            ctx = Ctx(facts=facts, holding=h, policy=pol)
            gate, gn = ev(r["gate"], ctx)
            pending = ev(r["pending_when"], ctx)[0] if "pending_when" in r else False
            if gate is False and pending is not True:
                continue                                          # silent
            for name, expr in r.get("named", {}).items():
                ctx.named[name] = ev(expr, ctx)
            amount, an = ev(r["amount"], ctx) if r.get("amount") else (UNK, set())
            f = {"holding": h["holding_id"], "policy_id": h["policy_id"], "insurer": pol["policy"]["insurer"],
                 "policy": pname, "cover_id": c["cover_id"], "cover": c["name_he"], "cover_type": ct,
                 "clauses": c["clauses"], "notes": list(ctx.notes), "amount": amount,
                 "formula": r.get("formula_he", ""), "named": {k: v[0] for k, v in ctx.named.items()},
                 "missing_slots": slot_needs(an), "verify": [], "status": None, "ask": [], "ask_tier": TIER_AMOUNT,
                 "evidence": c.get("evidence", []),
                 "slots_used": {k: h["slots"][k] for k in c.get("slots", []) if k in h["slots"]}}

            if pending is True:
                f["status"] = "pending_external"
            elif gate is UNK:
                aw = ev(r["ask_when"], ctx)[0] if "ask_when" in r else False
                if aw is not True:
                    continue                                      # silent: not mentioned, not severe enough
                f["status"] = "question"                          # hidden opportunity worth asking about
                f["ask"] += ask_keys(gn)
                f["ask_tier"] = TIER_GATE
            if blocked:
                f["status"] = "blocked"
                f["blocked_by"] = [f"{label_of(x)} ({', '.join(x['clauses'])})" for x in blocked]
                f["ask"] = []
            elif r.get("amount") is None and f["status"] is None:
                f["status"] = "manual_review"
                f["manual"] = r.get("manual_he")
            if f["status"] != "blocked":
                for cond in r.get("conditions", []):
                    cv, cn = ev(cond["expr"], ctx)
                    if cv is False:
                        f["status"] = "not_eligible"
                        f["blocked_by"] = [f"{cond.get('verify_he', '')} ({', '.join(cond['clauses'])})"]
                        f["ask"] = []
                        break
                    if cv is UNK:
                        if cond.get("on_unknown") == "ask":
                            f["ask"] += ask_keys(cn)
                            f["ask_tier"] = TIER_GATE
                        else:
                            f["verify"].append(f"{cond['verify_he']} ({', '.join(cond['clauses'])})")
            if f["status"] is None:
                f["ask"] += ask_keys(an) + scope_ask
                if f["missing_slots"]:
                    f["status"] = "needs_slots"
                elif f["ask"] or (isinstance(amount, Num) and not amount.exact):
                    f["status"] = "provisional"
                else:
                    f["status"] = "payable"
            for x in flags:
                f["verify"].append(f"חריג לא חד-משמעי: {x.get('note_he')} ({', '.join(x['clauses'])})")

            # money at stake for each question this finding raises
            if isinstance(amount, Num):
                stake = amount.hi if f["ask_tier"] == TIER_GATE else amount.hi - amount.lo
            else:
                stake = COVER_TYPES[ct]["rank_weight"] * 1000     # unknown amount: rank by cover importance
            for k in dict.fromkeys(f["ask"]):
                _add_q(questions, k, f["ask_tier"], stake, f"{pname}: {c['name_he']}")
            f["facts_used"] = sorted(ctx.used)
            findings.append(f)

    # exclusion questions: stake = everything currently on the table in the affected policies
    for k, q in questions.items():
        if q["tier"] == TIER_EXCLUSION:
            q["stake"] = sum(f["amount"].hi for f in findings if isinstance(f["amount"], Num)
                             and any(w.startswith(f["policy"]) for w in q["why"]))
    ordered = sorted(questions.items(), key=lambda kv: (kv[1]["tier"], -kv[1]["stake"], kv[0]))
    return findings, ordered, checklist, out_of_scope


def totals(findings):
    lo = sum(f["amount"].lo for f in findings if f["status"] in ("payable", "provisional") and isinstance(f["amount"], Num))
    hi = sum(f["amount"].hi for f in findings if f["status"] in ("payable", "provisional") and isinstance(f["amount"], Num))
    return Num(lo, hi)


def potential(findings):
    """Upside not yet in the total: known amounts of covers that are still a question (hidden opportunities)."""
    return sum(f["amount"].hi for f in findings if f["status"] == "question" and isinstance(f["amount"], Num))


def group_checklist(checklist):
    g = {}
    for c in checklist:
        g.setdefault(c["label"], []).append(f"{c['insurer']} {', '.join(c['clauses'])}")
    return g


def fmt_formula(f):
    s = f["formula"]
    for k, v in f["named"].items():
        s = s.replace("{" + k + "}", v.fmt() if isinstance(v, Num) else "?")
    return s


def report(findings, questions, checklist, out_of_scope, max_q=3):
    out = []
    for o in out_of_scope:
        out.append(f"⊘ [{o['insurer']}] מחוץ לתחום: נדרש '{o['label']}' ({', '.join(o['clauses'])})")
    for f in findings:
        a = f["amount"]
        amt = a.fmt(money=True) if isinstance(a, Num) else "—"
        out.append(f"• [{f['insurer']}] {f['cover']} ({', '.join(f['clauses'])}) | {f['status']} | {amt}")
        out.append(f"    נוסחה: {fmt_formula(f)}")
        if f["facts_used"]:
            out.append(f"    מהאירוע: {', '.join(f['facts_used'])}")
        for e in f["evidence"]:
            out.append(f"    מהפוליסה [{e['clause']}]: \"{e['quote']}\"")
        for k, v in f["slots_used"].items():
            out.append(f"    מהמפרט: {k} = {v:,}")
        for n in f["notes"]:
            out.append(f"    ⚠ {n}")
        if f.get("blocked_by"):
            out.append(f"    ✗ נחסם/לא זכאי: {', '.join(f['blocked_by'])}")
        if f["missing_slots"]:
            out.append(f"    חסר מהמפרט: {', '.join(s.split(':')[2] for s in f['missing_slots'])}")
        if f.get("manual"):
            out.append(f"    {f['manual']}")
        for v in f["verify"]:
            out.append(f"    ☐ {v}")
    if not findings:
        out.append("(לא נמצאה זכאות בפוליסות שבתיק לאירוע הזה)")
    out.append(f"\nסה\"כ מחושב (payable+provisional): {totals(findings).fmt(money=True)}")
    if potential(findings):
        out.append(f"פוטנציאל נוסף אם יאומתו השאלות הפתוחות: עד {potential(findings):,.0f} ₪ (ללא נכות שסכומה טרם ידוע)")
    out.append(f"\nשאלות לסוכן (נשאלות {max_q} ראשונות; סדר: חריג > שער > סכום, ואז לפי כסף):")
    for i, (k, q) in enumerate(questions):
        mark = "?" if i < max_q else "·"
        out.append(f"  {mark} [T{q['tier']} | {q['stake']:,.0f} ₪] {question_text(k)}   [{k}; משפיע על: {'; '.join(dict.fromkeys(q['why']))}]")
    out.append("\nרשימת אימות (לא נשאל, לבדוק לפני הגשה):")
    for label, where in group_checklist(checklist).items():
        out.append(f"  ☐ {label} — {'; '.join(where)}")
    return "\n".join(out)


def to_json(findings, questions, checklist, out_of_scope):
    def j(v):
        if isinstance(v, Num):
            return {"lo": v.lo, "hi": v.hi}
        return None if v is UNK else v
    return {
        "findings": [{"holding": f["holding"], "cover_id": f["cover_id"], "status": f["status"], "amount": j(f["amount"]),
                      "clauses": f["clauses"], "ask": sorted(set(f["ask"])),
                      "missing_slots": [s.split(":")[2] for s in f["missing_slots"]],
                      "blocked_by": f.get("blocked_by", []), "verify_count": len(f["verify"]),
                      "facts_used": f["facts_used"]} for f in findings],
        "total": j(totals(findings)),
        "potential_hi": potential(findings),
        "out_of_scope": [o["holding"] for o in out_of_scope],
        "questions": [{"key": k, "tier": q["tier"], "stake": q["stake"]} for k, q in questions],
        "checklist": sorted(group_checklist(checklist)),
    }


def load_case(sid, turn):
    g = json.load(open(ROOT / "scenarios" / "golden.json", encoding="utf-8"))
    by_id = {s["id"]: s for s in g["scenarios"]}
    sc = by_id[sid]
    base = sc if "text" in sc else {**by_id[sc["text_ref"]], "portfolio": sc["portfolio"]}
    facts = dict(base["expected_facts"])
    for t in base.get("turns", [])[:turn]:
        facts.update(t["answers"])
    return base["text"], g["portfolios"][sc["portfolio"]], facts


CASES = [("S1", 0), ("S1", 1), ("S1_no_slots", 0), ("S2", 0), ("S3", 0), ("S4", 0), ("S5", 0), ("S6", 0), ("S7", 0)]


def main():
    if "--write-golden" in sys.argv:
        here = Path(__file__).resolve().parent
        txt, js = [], {}
        for sid, turn in CASES:
            text, pf, facts = load_case(sid, turn)
            res = run(pf, facts)
            txt.append(f"=== {sid} (turn {turn}) | {text}\n{report(*res)}\n")
            js[f"{sid}#{turn}"] = to_json(*res)
        (here / "expected_output.txt").write_text("\n".join(txt), encoding="utf-8")
        (here / "expected_results.json").write_text(json.dumps(js, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote {len(CASES)} cases")
        return
    sid = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "S1"
    turn = int(sys.argv[sys.argv.index("--turn") + 1]) if "--turn" in sys.argv else 0
    text, pf, facts = load_case(sid, turn)
    res = run(pf, facts)
    if "--json" in sys.argv:
        print(json.dumps(to_json(*res), ensure_ascii=False, indent=1))
    else:
        print(f"=== {sid} (turn {turn}) | {text}")
        print(report(*res))


if __name__ == "__main__":
    main()
