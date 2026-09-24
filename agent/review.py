"""Loop 3: human spot-check.

build_review -> review/review.html : a stratified random sample (n per category) showing, per field, what pass 1 said,
                what the verifier said, and the cited evidence links. The reviewer opens the docs, picks the true value
                for each field, and clicks "Export" -> review/human_labels.json.
score        -> accuracy of pass 1 vs verified (pass 2) against the human labels, per field -> data/accuracy.json.
"""
import json
import random
from pathlib import Path

from .schema import AppResearch

FIELDS = ["primary_auth", "access", "mcp", "verdict"]  # the four fields that drive the patterns


def enum_values(field):
    import typing
    ann = AppResearch.model_fields[field].annotation
    if typing.get_origin(ann) is list:
        ann = typing.get_args(ann)[0]
    return list(typing.get_args(ann))


def sample_ids(apps, n_per_category, seed):
    rng = random.Random(seed)
    by_cat = {}
    for a in apps:
        by_cat.setdefault(a["category"], []).append(a["id"])
    ids = []
    for c, lst in by_cat.items():
        ids += sorted(rng.sample(lst, min(n_per_category, len(lst))))
    return ids


def build_review(root: Path, apps, load, n_per_category=2, seed=7):
    ids = sample_ids(apps, n_per_category, seed)
    items = []
    for i in ids:
        p1, p2 = load("pass1", i), load("pass2", i)
        if not p1:
            continue
        r2 = (p2 or p1)["record"]
        items.append({
            "id": i, "name": p1["app"]["name"], "category": p1["app"]["category"], "hint": p1["app"]["hint"],
            "pass1": {f: p1["record"][f] for f in FIELDS},
            "pass2": {f: r2[f] for f in FIELDS},
            "evidence": [{"claim": e["claim"], "url": e["url"], "quote": e["quote"]}
                         for e in (p1["record"]["evidence"] + r2["evidence"])],
            "notes": {"primary_auth": p1["record"]["auth_notes"], "access": p1["record"]["access_notes"],
                      "mcp": p1["record"]["mcp_notes"], "verdict": p1["record"]["blocker_notes"]},
            "changes": r2.get("changes", []),
            "needs_human_reason": r2.get("needs_human_reason", ""),
        })
    enums = {f: enum_values(f) for f in FIELDS}
    (root / "review").mkdir(exist_ok=True)
    (root / "review" / "sample.json").write_text(json.dumps({"seed": seed, "ids": ids}, indent=1), encoding="utf-8")
    html = (Path(__file__).parent / "review_template.html").read_text(encoding="utf-8").replace("__ITEMS__", json.dumps(items, ensure_ascii=False)).replace("__ENUMS__", json.dumps(enums))
    (root / "review" / "review.html").write_text(html, encoding="utf-8")
    print(f"review/review.html: {len(items)} apps (seed={seed}, {n_per_category}/category). Open it in a browser.")


def _eq(a, b):
    if isinstance(a, list) or isinstance(b, list):
        return sorted(a or []) == sorted(b or [])
    return a == b


def score(root: Path, load):
    f = root / "review" / "human_labels.json"
    if not f.exists():
        raise SystemExit("review/human_labels.json not found - export it from review/review.html first")
    raw = json.loads(f.read_text(encoding="utf-8"))
    labels = raw["labels"]
    per_field = {fld: {"n": 0, "pass1": 0, "pass2": 0} for fld in FIELDS}
    rows = []
    for sid, lab in labels.items():
        i = int(sid)
        p1, p2 = load("pass1", i), load("pass2", i)
        r1 = p1["record"]
        r2 = (p2 or p1)["record"]
        row = {"id": i, "name": p1["app"]["name"], "fields": {}, "note": lab.get("note", ""), "source": lab.get("source", {})}
        for fld in FIELDS:
            truth = lab.get("truth", {}).get(fld)
            if truth in (None, "", []):
                continue
            ok1, ok2 = _eq(r1[fld], truth), _eq(r2[fld], truth)
            per_field[fld]["n"] += 1
            per_field[fld]["pass1"] += ok1
            per_field[fld]["pass2"] += ok2
            row["fields"][fld] = {"truth": truth, "pass1": r1[fld], "pass2": r2[fld], "ok1": ok1, "ok2": ok2}
        rows.append(row)
    tot_n = sum(v["n"] for v in per_field.values())
    out = {
        "n_apps": len(rows),
        "n_fields": tot_n,
        "pass1_acc": round(sum(v["pass1"] for v in per_field.values()) / tot_n, 3) if tot_n else None,
        "pass2_acc": round(sum(v["pass2"] for v in per_field.values()) / tot_n, 3) if tot_n else None,
        "per_field": {k: {**v, "pass1_acc": round(v["pass1"] / v["n"], 3) if v["n"] else None,
                          "pass2_acc": round(v["pass2"] / v["n"], 3) if v["n"] else None} for k, v in per_field.items()},
        "apps_all_correct_pass1": sum(all(x["ok1"] for x in r["fields"].values()) for r in rows),
        "apps_all_correct_pass2": sum(all(x["ok2"] for x in r["fields"].values()) for r in rows),
        "rows": rows,
        "method": raw.get("method", "human review"),
        "truth_sources": raw.get("counts"),
    }
    (root / "data" / "accuracy.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Field accuracy on {out['n_apps']} human-checked apps ({tot_n} fields): pass1 {out['pass1_acc']} -> verified {out['pass2_acc']}")
    for k, v in out["per_field"].items():
        print(f"  {k:14s} n={v['n']:3d}  pass1 {v['pass1_acc']}  pass2 {v['pass2_acc']}")
