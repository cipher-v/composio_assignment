"""Loop 3 (as run): independent blind check + human dispute resolution.

1. Four Claude checkers researched the 20 sampled apps BLIND (only name + rubric, never Gemini's answers), from live docs
   -> review/claude_check_*.json
2. build_disputes: compare Claude vs Gemini pass 1 and verified pass 2 per field. Where all agree, that value is taken as
   truth. Where they disagree -> review/disputes.html, a short list a human resolves (pick which one the docs support).
3. resolve: truth = agreed value, or the human's pick for disputes (Claude's value if a dispute is left unresolved,
   labelled as such) -> review/human_labels.json, which `run.py score` and `run.py merge` consume.
"""
import json
from pathlib import Path

from .review import FIELDS, enum_values


def load_claude(root: Path) -> dict:
    out = {}
    for f in sorted((root / "review").glob("claude_check_*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for k, v in d["apps"].items():
            out[int(k)] = v
    return out


def _v(x):
    return x.get("value") if isinstance(x, dict) else x


def build_disputes(root: Path, load):
    claude = load_claude(root)
    items, agreed = [], 0
    for i, c in sorted(claude.items()):
        p1, p2 = load("pass1", i), load("pass2", i)
        if not p1:
            continue
        r1 = p1["record"]
        r2 = (p2 or p1)["record"]
        for f in FIELDS:
            cv = _v(c.get(f, {}))
            if cv == r1[f] == r2[f]:
                agreed += 1
                continue
            ev1 = next((e for e in r1["evidence"] if e["claim"] == {"primary_auth": "auth"}.get(f, f)), None)
            items.append({
                "id": i, "name": p1["app"]["name"], "field": f,
                "pass1": r1[f], "pass2": r2[f], "claude": cv,
                "claude_url": c.get(f, {}).get("url", ""), "claude_quote": c.get(f, {}).get("quote") or c.get(f, {}).get("reason", ""),
                "claude_conf": c.get(f, {}).get("confidence", ""),
                "gemini_note": {"primary_auth": r2["auth_notes"], "access": r2["access_notes"], "mcp": r2["mcp_notes"], "verdict": r2["blocker_notes"] or r2["access_notes"]}[f],
                "gemini_url": ev1["url"] if ev1 else "",
            })
    enums = {f: enum_values(f) for f in FIELDS}
    html = (Path(__file__).parent / "disputes_template.html").read_text(encoding="utf-8")
    html = html.replace("__ITEMS__", json.dumps(items, ensure_ascii=False)).replace("__ENUMS__", json.dumps(enums)) \
               .replace("__AGREED__", str(agreed)).replace("__TOTAL__", str(agreed + len(items)))
    (root / "review" / "disputes.html").write_text(html, encoding="utf-8")
    (root / "review" / "disputes.json").write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(claude)} apps blind-checked: {agreed} fields all-agree, {len(items)} disputes -> review/disputes.html")


def resolve(root: Path, load):
    claude = load_claude(root)
    dec_f = root / "review" / "dispute_decisions.json"
    dec = json.loads(dec_f.read_text(encoding="utf-8")) if dec_f.exists() else {}
    decisions = dec.get("decisions", {})
    who = "human" if "Claude" not in dec.get("decided_by", "human") else "adjudicated"
    labels = {}
    counts = {"agreed": 0, "human": 0, "adjudicated": 0, "claude_unresolved": 0}
    for i, c in sorted(claude.items()):
        p1 = load("pass1", i)
        if not p1:
            continue
        truth, source, notes = {}, {}, []
        for f in FIELDS:
            cv = _v(c.get(f, {}))
            key = f"{i}:{f}"
            p2 = load("pass2", i)
            r1, r2 = p1["record"], (p2 or p1)["record"]
            if cv == r1[f] == r2[f]:
                truth[f], source[f] = cv, "agreed"
            elif key in decisions and decisions[key].get("value"):
                truth[f], source[f] = decisions[key]["value"], who
                if decisions[key].get("note"):
                    notes.append(f"{f}: {decisions[key]['note']}")
            else:
                truth[f], source[f] = cv, "claude_unresolved"
            counts[source[f]] += 1
        labels[i] = {"truth": truth, "source": source, "note": "; ".join(notes)}
    method = ("blind independent check (Claude, live docs); disputes " +
              ("decided by a human" if who == "human" else "re-checked against vendor docs and decided by Claude (human delegated)"))
    out = {"method": method, "decided_by": dec.get("decided_by", ""), "counts": counts, "labels": labels}
    (root / "review" / "human_labels.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"human_labels.json: {len(labels)} apps; field truth sources {counts}")
