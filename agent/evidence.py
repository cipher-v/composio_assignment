"""Loop 1: deterministic citation checker + rule lint. No LLM involved.

For every evidence URL the agent cited we fetch the page ourselves and check:
  * is the URL alive (HTTP < 400)?
  * is it on the vendor's own domain (official) or a third-party page?
  * does the quoted snippet actually appear on the page (fuzzy shingle match)?
  * do the claimed auth methods appear anywhere in the cited pages (keyword check)?
Then we lint the record for internal contradictions (e.g. verdict ready_now while access is partner-gated).
The findings are handed to the verifier agent and also reported as citation-quality metrics.
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"}

AUTH_KEYWORDS = {
    "oauth2": ["oauth"],
    "api_key": ["api key", "api-key", "apikey", "api_key", "api token", "access key", "secret key"],
    "bearer_token": ["bearer", "access token", "token"],
    "basic": ["basic auth", "basic authentication", "authorization: basic", "username", "password"],
    "jwt": ["jwt", "json web token"],
    "signature_hmac": ["hmac", "signature", "sign"],
}

# Vendor domains that don't contain the app name
EXTRA_OFFICIAL = {
    "LinkedIn Ads": ["microsoft.com", "linkedin.com"],
    "Meta Ads": ["facebook.com", "meta.com"],
    "Threads (Meta)": ["facebook.com", "meta.com", "threads.net", "threads.com"],
    "WhatsApp Business": ["facebook.com", "meta.com", "whatsapp.com"],
    "Amazon Selling Partner": ["amazon.com", "amazonservices.com", "github.com/amzn"],
    "SendGrid": ["twilio.com", "sendgrid.com"],
    "Magento (Adobe Commerce)": ["adobe.com", "magento.com"],
    "Salesforce Commerce Cloud": ["salesforce.com", "commercecloud.salesforce.com"],
    "NotebookLM": ["google.com", "cloud.google.com"],
    "GoHighLevel": ["gohighlevel.com", "highlevel.stoplight.io", "leadconnectorhq.com", "highlevel.com"],
    "Jira": ["atlassian.com", "atlassian.net"],
    "Sherlock": ["github.com/sherlock-project", "sherlockproject.xyz"],
    "Mermaid CLI": ["github.com/mermaid-js", "mermaid.js.org", "mermaid.ai"],
    "YouTube Transcript": ["transcriptapi.com"],
    "Binance": ["binance.com", "binance-docs.github.io", "github.com/binance"],
    "Paygent Connect": ["nmi.com", "paygent"],
    "Twenty": ["twenty.com", "github.com/twentyhq"],
    "Lark (Larksuite)": ["larksuite.com", "feishu.cn", "larkoffice.com"],
    "Otter AI": ["otter.ai"],
    "Devin": ["devin.ai", "cognition.ai"],
    "Waterfall.io": ["waterfall.io"],
    "Supabase": ["supabase.com", "github.com/supabase"],
    "Neo4j": ["neo4j.com", "github.com/neo4j"],
    "MongoDB Atlas": ["mongodb.com", "github.com/mongodb"],
}


def official_tokens(app: dict) -> list[str]:
    toks = set()
    hint_host = app["hint"].split()[0].split("/")[0].lower()
    parts = hint_host.split(".")
    if len(parts) >= 2:
        toks.add(parts[-2])
    for w in re.split(r"[^a-z0-9]+", app["name"].lower()):
        if len(w) >= 4:
            toks.add(w)
    return list(toks)


def is_official(url: str, app: dict) -> bool:
    u = url.lower()
    host = urlparse(u).netloc
    if any(d in u for d in EXTRA_OFFICIAL.get(app["name"], [])):
        return True
    if host.endswith("github.com"):
        return False  # github repos only count via EXTRA_OFFICIAL
    return any(t in host for t in official_tokens(app))


@lru_cache(maxsize=4096)
def fetch(url: str) -> dict:
    try:
        # stream with a total deadline: requests' timeout is per socket read, so a slow-drip server could hang forever
        t0 = time.time()
        r = requests.get(url, headers=UA, timeout=10, allow_redirects=True, stream=True)
        buf = b""
        for chunk in r.iter_content(65536):
            buf += chunk
            if len(buf) > 3_000_000 or time.time() - t0 > 20:
                break
        r.close()
        raw = buf.decode(r.encoding or "utf-8", errors="replace")
        text = ""
        if "html" in r.headers.get("content-type", "") or raw.lstrip().startswith("<"):
            soup = BeautifulSoup(raw, "html.parser")
            for t in soup(["script", "style", "noscript"]):
                t.decompose()
            text = soup.get_text(" ")
        else:
            text = raw
        text = re.sub(r"\s+", " ", text).lower()
        return {"status": r.status_code, "final_url": r.url, "text": text[:400_000]}
    except Exception as e:
        return {"status": 0, "final_url": url, "text": "", "error": type(e).__name__}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower())


def quote_match(quote: str, text: str) -> float:
    """Share of 4-word shingles of the quote found on the page (0-1)."""
    q = _norm(quote).split()
    if len(q) < 4:
        return 1.0 if " ".join(q) in _norm(text) else 0.0
    t = _norm(text)
    shingles = [" ".join(q[i:i + 4]) for i in range(len(q) - 3)]
    return sum(1 for s in shingles if s in t) / len(shingles)


def lint(rec: dict) -> list[str]:
    issues = []
    v, a, b = rec["verdict"], rec["access"], rec["blocker"]
    if v == "ready_now" and b != "none":
        issues.append(f"verdict ready_now but blocker={b}")
    if v != "ready_now" and b == "none":
        issues.append(f"verdict {v} but blocker=none")
    if v == "ready_now" and a in ("paid_plan", "partner_or_sales", "no_public_api", "approval_required"):
        issues.append(f"verdict ready_now but access={a}")
    if a == "partner_or_sales" and v in ("ready_now",):
        issues.append("partner-gated access but verdict ready_now")
    if a == "no_public_api" and v not in ("not_feasible", "needs_outreach") and b != "local_cli_only":
        issues.append(f"access no_public_api but verdict {v}")
    if rec["primary_auth"] not in rec["auth_methods"]:
        issues.append("primary_auth not in auth_methods")
    if "none" in rec["api_styles"] and rec["api_breadth"] != "none":
        issues.append("api_styles contains none but breadth is not none")
    claims = {e["claim"] for e in rec["evidence"]}
    for need in ("auth", "access", "api"):
        if need not in claims:
            issues.append(f"no evidence cited for {need}")
    if rec["mcp"] != "none_found" and "mcp" not in claims:
        issues.append(f"mcp={rec['mcp']} claimed without mcp evidence")
    if rec["confidence"] < 0.6:
        issues.append(f"low self-confidence {rec['confidence']}")
    return issues


def check(app: dict, rec: dict) -> dict:
    ev = rec["evidence"]
    urls = list(dict.fromkeys(e["url"] for e in ev))
    with ThreadPoolExecutor(8) as ex:
        pages = dict(zip(urls, ex.map(fetch, urls)))

    items = []
    for e in ev:
        p = pages[e["url"]]
        alive = 0 < p["status"] < 400
        blocked = p["status"] in (401, 403, 429, 999)  # bot wall / login: page likely exists, we just can't read it
        readable = alive and len(p["text"]) > 500
        items.append({
            "blocked": blocked,
            "claim": e["claim"],
            "url": e["url"],
            "status": p["status"],
            "alive": alive,
            "official": is_official(e["url"], app),
            "readable": readable,
            "quote_match": round(quote_match(e["quote"], p["text"]), 2) if readable else None,
        })

    # auth keyword support, searched across every readable cited page
    corpus = " ".join(pages[u]["text"] for u in urls if len(pages[u]["text"]) > 500)
    auth_support = {}
    for m in rec["auth_methods"]:
        kws = AUTH_KEYWORDS.get(m)
        if not kws:
            continue
        auth_support[m] = None if not corpus else any(k in corpus for k in kws)

    issues = lint(rec)
    dead = [i["url"] for i in items if not i["alive"] and not i["blocked"]]
    blocked = [i["url"] for i in items if i["blocked"]]
    weak = [i["url"] for i in items if i["quote_match"] is not None and i["quote_match"] < 0.5]
    unofficial = [i["url"] for i in items if i["alive"] and not i["official"]]
    unsupported_auth = [m for m, ok in auth_support.items() if ok is False]

    lines = []
    if dead:
        lines.append(f"- Dead / unreachable evidence URLs: {dead}")
    if blocked:
        lines.append(f"- Bot-walled / login pages our crawler could not read (verify via search): {blocked}")
    if weak:
        lines.append(f"- Quotes NOT found on the cited page (possible fabrication or wrong page): {weak}")
    if unofficial:
        lines.append(f"- Non-vendor sources (prefer official docs): {unofficial}")
    if unsupported_auth:
        lines.append(f"- Auth methods not mentioned on any cited page: {unsupported_auth}")
    for i in issues:
        lines.append(f"- Consistency: {i}")

    n = len(items) or 1
    readable_items = [i for i in items if i["quote_match"] is not None]
    return {
        "items": items,
        "auth_support": auth_support,
        "lint": issues,
        "summary_for_verifier": "\n".join(lines) or "No issues flagged.",
        "metrics": {
            "n_evidence": len(items),
            "alive_rate": sum(i["alive"] for i in items) / n,
            "dead_rate": len(dead) / n,
            "blocked_rate": len(blocked) / n,
            "official_rate": sum(i["official"] for i in items) / n,
            "readable": len(readable_items),
            "quote_supported_rate": (sum(i["quote_match"] >= 0.5 for i in readable_items) / len(readable_items)) if readable_items else None,
            "lint_issues": len(issues),
            "flag_count": len(lines),
        },
    }
