"""Build the single-file case-study page site/index.html (+ site/data/*.json for agents)."""
import json
from collections import Counter
from pathlib import Path


def _load(p: Path, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def pipeline_stats(data: Path):
    s = Counter()
    change_fields = Counter()
    for f in sorted((data / "pass1").glob("*.json")):
        d = _load(f)
        m = d["meta"]
        disc = m.get("discovery", {})
        s["apps"] += 1
        s["search_queries"] += len(m.get("search_queries", [])) + len(disc.get("search_queries", []))
        s["candidate_urls"] += disc.get("candidates", 0)
        bad = disc.get("bad_candidates", [])
        s["bad_404"] += sum(1 for b in bad if b["status"] in (404, 410))
        s["bad_blocked"] += sum(1 for b in bad if b["status"] in (401, 403, 429, 999))
        s["bad_other"] += sum(1 for b in bad if b["status"] not in (404, 410, 401, 403, 429, 999))
        s["pages_read"] += sum(1 for p in disc.get("pages_read", []) if p["readable"])
        s["pages_unreadable"] += sum(1 for p in disc.get("pages_read", []) if not p["readable"])
        s["retries"] += len(m.get("retries", []))
        s["pass1_seconds"] += m.get("seconds", 0)
    for f in sorted((data / "pass2").glob("*.json")):
        d = _load(f)
        s["verified"] += 1
        s["pass2_seconds"] += d["meta"].get("seconds", 0)
        s["needs_human"] += bool(d["record"].get("needs_human"))
        s["apps_changed"] += bool(d["record"].get("changes"))
    for r in _load(data / "final.json", []):
        for fld, src in (r.get("provenance") or {}).items():
            if src.startswith("verifier_fixed"):
                change_fields[fld] += 1
    return dict(s), change_fields.most_common()


def citation_summary(data: Path):
    out = {}
    for tag in ("evidence1", "evidence2"):
        ms = [_load(f)["metrics"] for f in sorted((data / tag).glob("*.json"))]
        if not ms:
            continue
        ev = sum(m["n_evidence"] for m in ms) or 1
        rd = sum(m["readable"] for m in ms)
        qs = [m["quote_supported_rate"] * m["readable"] for m in ms if m["quote_supported_rate"] is not None]
        out[tag] = {
            "apps": len(ms),
            "evidence_urls": ev,
            "alive_pct": round(100 * sum(m["alive_rate"] * m["n_evidence"] for m in ms) / ev),
            "dead_pct": round(100 * sum(m.get("dead_rate", 0) * m["n_evidence"] for m in ms) / ev),
            "official_pct": round(100 * sum(m["official_rate"] * m["n_evidence"] for m in ms) / ev),
            "quote_supported_pct": round(100 * sum(qs) / rd) if rd else None,
            "readable": rd,
            "lint_issues": sum(m["lint_issues"] for m in ms),
            "apps_with_flags": sum(1 for m in ms if m["flag_count"]),
        }
    return out


def build_page(root: Path):
    data = root / "data"
    final = _load(data / "final.json", [])
    patterns = _load(data / "patterns.json", {})
    accuracy = _load(data / "accuracy.json", None)
    stats, change_fields = pipeline_stats(data)
    cites = citation_summary(data)
    bundle = {
        "final": final, "patterns": patterns, "accuracy": accuracy, "stats": stats,
        "change_fields": change_fields, "citations": cites,
    }
    site = root / "site"
    (site / "data").mkdir(parents=True, exist_ok=True)
    (site / "data" / "apps.json").write_text(json.dumps(final, indent=1, ensure_ascii=False), encoding="utf-8")
    (site / "data" / "patterns.json").write_text(json.dumps(patterns, indent=1, ensure_ascii=False), encoding="utf-8")
    if accuracy:
        (site / "data" / "accuracy.json").write_text(json.dumps(accuracy, indent=1, ensure_ascii=False), encoding="utf-8")
    for name in ("dispute_decisions.json", "human_labels.json", "disputes.json"):
        f = root / "review" / name
        if f.exists():
            (site / "data" / name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    tpl = (root / "agent" / "page_template.html").read_text(encoding="utf-8")
    html = tpl.replace("/*__DATA__*/null", json.dumps(bundle, ensure_ascii=False).replace("</", "<\\/"))
    (site / "index.html").write_text(html, encoding="utf-8")
    (site / "llms.txt").write_text(LLMS_TXT, encoding="utf-8")
    print(f"site/index.html written ({len(html)//1024} KB, {len(final)} apps)")


LLMS_TXT = """# Composio toolkit research - 100 apps
Machine-readable outputs of the research agent:
- /data/apps.json      one record per app: auth_methods, primary_auth, access, api_styles, api_breadth, mcp, verdict, blocker, evidence[] (url+quote), provenance per field
- /data/patterns.json  aggregated clusters (auth, access, blockers, MCP, per-category) and headline findings
- /data/accuracy.json  human spot-check: per-field accuracy of first pass vs verified pass
Enum definitions are in the page section "Definitions" and in agent/schema.py (RUBRIC) of the source repo.
"""
