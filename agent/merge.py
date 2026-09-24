"""Merge pass1 -> pass2 (verifier) -> human corrections into data/final.json with per-field provenance."""
import json
from pathlib import Path

KEY_FIELDS = ["primary_auth", "auth_methods", "access", "api_styles", "api_breadth", "mcp", "verdict", "blocker"]


def norm(v):
    return sorted(v) if isinstance(v, list) else v


def load_human(root: Path) -> dict:
    f = root / "review" / "human_labels.json"
    if not f.exists():
        return {}
    raw = json.loads(f.read_text(encoding="utf-8"))
    return {int(k): v for k, v in raw.get("labels", raw).items()}


def merge(data: Path, apps, load):
    human = load_human(data.parent)
    out = []
    for a in apps:
        p1, p2 = load("pass1", a["id"]), load("pass2", a["id"])
        if not p1:
            out.append({"id": a["id"], "name": a["name"], "category": a["category"], "hint": a["hint"], "missing": True})
            continue
        r1 = p1["record"]
        r2 = p2["record"] if p2 else None
        rec = dict(r2 or r1)
        prov = {}
        for f in KEY_FIELDS:
            if r2 is None:
                prov[f] = "pass1"
            elif norm(r1[f]) == norm(r2[f]):
                prov[f] = "confirmed"
            else:
                prov[f] = "verifier_fixed"
        h = human.get(a["id"], {})
        src = h.get("source", {})
        for f, v in h.get("truth", {}).items():
            if src.get(f, "human") not in ("human", "adjudicated") and norm(v) != norm(rec[f]):
                prov[f] += "+checker_disputes"  # blind checker disagrees, no human decision: keep agent value, flag it
                continue
            if f in KEY_FIELDS and v not in (None, "", []):
                if norm(v) != norm(rec[f]):
                    prov[f] = "human_fixed" if src.get(f, "human") == "human" else "checker_fixed"
                    rec[f] = v
                elif not prov[f].endswith("_fixed"):
                    prov[f] += "+human_ok" if src.get(f, "human") == "human" else "+checker_ok"
        e1, e2 = load("evidence1", a["id"]), load("evidence2", a["id"])
        out.append({
            "id": a["id"], "name": a["name"], "category": a["category"], "hint": a["hint"],
            **{k: v for k, v in rec.items() if k not in ("changes",)},
            "pass1": {f: r1[f] for f in KEY_FIELDS},
            "verifier_changes": (r2 or {}).get("changes", []),
            "needs_human": (r2 or {}).get("needs_human", False),
            "needs_human_reason": (r2 or {}).get("needs_human_reason", ""),
            "human_reviewed": bool(h),
            "human_note": h.get("note", ""),
            "provenance": prov,
            "citations_pass1": (e1 or {}).get("metrics"),
            "citations_pass2": (e2 or {}).get("metrics"),
            "models": {"research": p1["meta"]["model"], "verify": p2["meta"]["model"] if p2 else None},
        })
    (data / "final.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"final.json: {len(out)} apps, {sum(1 for r in out if r.get('missing'))} missing, "
          f"{sum(1 for r in out if r.get('human_reviewed'))} in the verification sample")
