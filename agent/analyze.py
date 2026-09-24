"""Cluster final.json into the pattern stats that drive the headline of the page -> data/patterns.json."""
import json
from collections import Counter, defaultdict
from pathlib import Path

ACCESS_ORDER = ["self_serve_free", "self_serve_trial", "paid_plan", "approval_required", "partner_or_sales", "no_public_api"]
VERDICT_ORDER = ["ready_now", "ready_with_friction", "needs_outreach", "not_feasible"]
SELF_SERVE = {"self_serve_free", "self_serve_trial"}
GATED = {"paid_plan", "approval_required", "partner_or_sales", "no_public_api"}


def pct(n, d):
    return round(100 * n / d) if d else 0


def analyze(data: Path):
    rows = [r for r in json.loads((data / "final.json").read_text(encoding="utf-8")) if not r.get("missing")]
    n = len(rows)
    cats = list(dict.fromkeys(r["category"] for r in rows))

    primary = Counter(r["primary_auth"] for r in rows)
    any_auth = Counter(m for r in rows for m in set(r["auth_methods"]))
    oauth_and_key = sum(1 for r in rows if "oauth2" in r["auth_methods"] and ({"api_key", "bearer_token"} & set(r["auth_methods"])))
    access = Counter(r["access"] for r in rows)
    verdict = Counter(r["verdict"] for r in rows)
    blockers = Counter(r["blocker"] for r in rows if r["blocker"] != "none")
    mcp = Counter(r["mcp"] for r in rows)
    styles = Counter(s for r in rows for s in set(r["api_styles"]))

    mcp_by_verdict = defaultdict(Counter)
    for r in rows:
        mcp_by_verdict[r["verdict"]][r["mcp"]] += 1

    by_cat = {}
    for c in cats:
        rs = [r for r in rows if r["category"] == c]
        by_cat[c] = {
            "n": len(rs),
            "access": Counter(r["access"] for r in rs),
            "verdict": Counter(r["verdict"] for r in rs),
            "self_serve_pct": pct(sum(r["access"] in SELF_SERVE for r in rs), len(rs)),
            "ready_now_pct": pct(sum(r["verdict"] == "ready_now" for r in rs), len(rs)),
            "oauth_primary_pct": pct(sum(r["primary_auth"] == "oauth2" for r in rs), len(rs)),
            "official_mcp_pct": pct(sum(r["mcp"] == "official" for r in rs), len(rs)),
        }


    def brief(r):
        return {"id": r["id"], "name": r["name"], "category": r["category"], "primary_auth": r["primary_auth"],
                "access": r["access"], "blocker": r["blocker"], "mcp": r["mcp"], "note": r["blocker_notes"] or r["access_notes"]}

    easy = [brief(r) for r in rows if r["verdict"] == "ready_now" and r["mcp"] != "official"]
    easy_with_mcp = [brief(r) for r in rows if r["verdict"] == "ready_now" and r["mcp"] == "official"]
    friction = [brief(r) for r in rows if r["verdict"] == "ready_with_friction"]
    outreach = [brief(r) for r in rows if r["verdict"] == "needs_outreach"]
    infeasible = [brief(r) for r in rows if r["verdict"] == "not_feasible"]

    cat_sorted = sorted(cats, key=lambda c: -by_cat[c]["self_serve_pct"])
    top_blocker = blockers.most_common(1)[0] if blockers else ("none", 0)

    headlines = [
        f"{pct(verdict['ready_now'], n)}% of the {n} apps could be shipped as agent toolkits today; "
        f"{pct(verdict['ready_with_friction'], n)}% more are buildable with friction, and only "
        f"{pct(verdict['needs_outreach'] + verdict['not_feasible'], n)}% need outreach or are not feasible.",
        f"OAuth2 is the primary auth for {pct(primary['oauth2'], n)}% of apps, and {pct(oauth_and_key, n)}% offer both OAuth2 "
        f"and a static key/token - so a toolkit usually needs both an OAuth app and a key-based fallback.",
        (f"Most self-serve: {cat_sorted[0]} ({by_cat[cat_sorted[0]]['self_serve_pct']}%) and {cat_sorted[1]} "
        f"({by_cat[cat_sorted[1]]['self_serve_pct']}%). Most gated: {cat_sorted[-1]} ({by_cat[cat_sorted[-1]]['self_serve_pct']}% self-serve) "
        f"and {cat_sorted[-2]} ({by_cat[cat_sorted[-2]]['self_serve_pct']}%).") if len(cat_sorted) >= 4 else "",
        f"The most common blocker is '{top_blocker[0]}' ({top_blocker[1]} apps), followed by "
        + ", ".join(f"'{b}' ({c})" for b, c in blockers.most_common(3)[1:]) + ".",
        f"{pct(mcp['official'], n)}% already ship an official MCP server and {pct(mcp['community_only'], n)}% have only community ones. "
        f"Official MCP tracks openness: {pct(mcp_by_verdict['ready_now']['official'], verdict['ready_now'])}% of ready-now apps vs "
        f"{pct(mcp_by_verdict['needs_outreach']['official'], verdict['needs_outreach'] or 1)}% of apps that need outreach.",
    ]

    headlines = [h for h in headlines if h]
    out = {
        "n": n,
        "headlines": headlines,
        "primary_auth": primary.most_common(),
        "any_auth": any_auth.most_common(),
        "oauth_and_key": oauth_and_key,
        "access": [(k, access.get(k, 0)) for k in ACCESS_ORDER],
        "verdict": [(k, verdict.get(k, 0)) for k in VERDICT_ORDER],
        "blockers": blockers.most_common(),
        "mcp": mcp.most_common(),
        "api_styles": styles.most_common(),
        "by_category": {c: {**v, "access": dict(v["access"]), "verdict": dict(v["verdict"])} for c, v in by_cat.items()},
        "category_order": cats,
        "mcp_by_verdict": {k: dict(v) for k, v in mcp_by_verdict.items()},
        "lists": {"easy_wins": easy, "easy_with_official_mcp": easy_with_mcp, "friction": friction,
                  "outreach": outreach, "not_feasible": infeasible},
        "self_serve_total": sum(access.get(k, 0) for k in SELF_SERVE),
    }
    (data / "patterns.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print("\n".join(headlines))
