"""Pass 1 (research agent) and Pass 2 (adversarial verifier).

Pass 1, per app:
  1. discover  - Gemini + Google Search (text mode) lists official doc URLs; we also resolve the
                 search-grounding redirect links to real URLs.
  2. validate  - we HTTP-check every candidate URL ourselves and drop dead ones (the model does hallucinate paths).
  3. research  - Gemini + Search + URL context reads the live pages and writes cited notes.
  4. extract   - tool-less call turns the notes into the strict AppResearch schema.
Pass 2 repeats research/extract with a different, stronger model acting as an adversarial fact-checker,
fed with the citation checker's findings.
"""
import json
from concurrent.futures import ThreadPoolExecutor

import requests

from . import llm
from .evidence import UA, fetch, is_official
from .schema import RUBRIC, AppResearch, DocLinks, VerifiedApp

DISCOVER_PROMPT = """Use Google Search to find the official developer documentation for the app below.
App: {name}   (hint: {hint}; category: {category})
Search for, and list the exact URLs of:
- the authentication / API keys / OAuth page
- the getting-started / quickstart page
- the pricing page, or the page stating which plans include API access, or the partner/developer program page
- the API reference index
- any MCP server (search "{name} MCP server"), official or community
Only list URLs that appeared in your search results."""

RESEARCH_PROMPT = """You are a product-ops researcher at Composio, which turns SaaS apps into tools AI agents can call.
Research the app below for toolkit buildability.

App: {name}
Hint: {hint}
Assigned category: {category}

These official pages were found by search and confirmed live - read them:
{urls}
Then use Google Search for anything they do not answer (free tier? which plan includes API? app review? partner program? MCP server?).

Write research notes covering: what the product does; every auth method of the public API and which one a multi-tenant
integration would use; exactly how a developer gets credentials and what it costs (free tier / trial / paid plan /
approval / partner / sales); API style (REST/GraphQL/...), breadth, OpenAPI spec, webhooks, sandbox; MCP servers
(official vs community); and your buildability verdict with the main blocker.
For every claim give the source URL and a short verbatim quote from that page.
{rubric}"""

EXTRACT_PROMPT = """Convert these research notes about "{name}" into the JSON schema. Use only what the notes say;
copy URLs and quotes exactly. Apply the definitions below for the enum fields.
{rubric}
NOTES:
{notes}"""

VERIFY_PROMPT = """You are an adversarial fact-checker. A first-pass research agent produced the record below for the app
"{name}" (hint: {hint}). Your job is to find its mistakes. Assume nothing is correct.

For EVERY field: independently re-derive it from the vendor's official docs. Read the cited pages below and search for
more. Pay special attention to:
- access tier (is there really a free tier / trial / dev account that includes API access? is app review or partner signup required?)
- whether the MCP server is really official (vendor-maintained) vs community-built,
- whether every auth method listed actually exists for the public API, and none is missing,
- whether the evidence URLs exist and actually say what the quote claims.

Cited pages to read:
{urls}

An automated citation checker (it fetched every cited URL itself) already ran on the first pass. Its findings:
{flags}

First-pass record:
{record}
{rubric}
Write verification notes: for each field say CONFIRMED or WRONG, the correct value, and the source URL + verbatim quote.
End with a list of fields that remain uncertain (conflicting sources, login-walled docs) - those go to a human."""

VERIFY_EXTRACT_PROMPT = """Below are a first-pass record and a fact-checker's verification notes for "{name}".
Produce the corrected full record: take the checker's value wherever it says WRONG, keep the first-pass value where
CONFIRMED. Fill `changes` with every field that differs from the first pass (old, new, reason, url). Use evidence
URLs/quotes from the notes (prefer them over first-pass evidence the checker disputed). Set needs_human=true if the
checker listed uncertain fields or sources conflicted.
{rubric}
FIRST-PASS RECORD:
{record}
VERIFICATION NOTES:
{notes}"""


def _probe(url: str) -> tuple[str, int]:
    """(final_url, status). status 0 = network error; 403/429 usually = bot wall, not a bad URL."""
    try:
        r = requests.get(url, headers=UA, timeout=(6, 8), allow_redirects=True, stream=True)
        r.close()
        return r.url, r.status_code
    except Exception:
        return url, 0


KEYWORDS = ["oauth", "api key", "api-key", "token", "authenticat", "authoriz", "bearer", "basic auth", "pricing",
            "free", "trial", "plan", "partner", "enterprise", "contact sales", "approval", "review", "access level",
            "developer token", "mcp", "model context protocol", "graphql", "rest api", "webhook", "sandbox", "rate limit"]


def _excerpt(text: str, budget: int = 3500, window: int = 350) -> str:
    """Keep the text around research-relevant keywords, within a char budget."""
    if len(text) <= budget:
        return text
    spans = []
    for k in KEYWORDS:
        start = 0
        while (i := text.find(k, start)) != -1 and len(spans) < 200:
            spans.append((max(0, i - window), min(len(text), i + window)))
            start = i + len(k)
    spans.sort()
    merged = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    out, used = [], 0
    for s, e in merged:
        chunk = text[s:e]
        if used + len(chunk) > budget:
            break
        out.append(chunk)
        used += len(chunk)
    return (text[:600] + " ... " + " ... ".join(out))[: budget + 600]


def _read_pages(urls: list[str], max_pages: int = 7):
    """Fetch pages ourselves (the URL-context tool is not reliably invoked) and return excerpts + a read log."""
    with ThreadPoolExecutor(8) as ex:
        pages = list(ex.map(fetch, urls[:max_pages]))
    blocks, log = [], []
    for u, p in zip(urls, pages):
        readable = 0 < p["status"] < 400 and len(p["text"]) > 400
        log.append({"url": u, "status": p["status"], "chars": len(p["text"]), "readable": readable})
        if readable:
            blocks.append(f"=== PAGE: {u}\n{_excerpt(p['text'])}")
    return "\n\n".join(blocks), log


def _bullets(urls, limit=12):
    urls = [u for u in dict.fromkeys(urls) if u and u.startswith("http")]
    return "\n".join(f"- {u}" for u in urls[:limit]) or "- (none found - rely on search)"


def run_pass1(app: dict) -> dict:
    # 1. discover
    notes_a, meta_a = llm.grounded_text(llm.LIGHT_MODEL, DISCOVER_PROMPT.format(**app))
    links = llm.extract_json(f"Extract the URLs from these notes about {app['name']}:\n{notes_a}", DocLinks)
    candidates = [links.auth_url, links.getting_started_url, links.pricing_or_access_url,
                  links.api_reference_url, links.mcp_url, *links.other_urls[:3]]
    # search-grounding sources are redirect links; resolve them to the real pages the search returned
    candidates += [s["uri"] for s in meta_a["search_sources"][:8]]
    candidates = [u for u in dict.fromkeys(candidates) if u]

    # 2. validate
    with ThreadPoolExecutor(8) as ex:
        probes = list(ex.map(_probe, candidates))
    live = list(dict.fromkeys(f for f, s in probes if 0 < s < 400))
    live.sort(key=lambda u: not is_official(u, app))  # official pages first
    pages, read_log = _read_pages(live)

    # 3. research
    prompt = RESEARCH_PROMPT.format(rubric=RUBRIC, urls=_bullets(live), **app)
    if pages:
        prompt += "\n\nEXCERPTS OF THOSE PAGES (fetched by our crawler just now):\n" + pages
    notes, meta = llm.grounded_text(llm.RESEARCH_MODEL, prompt, thinking="medium")
    # 4. extract
    rec = llm.extract_json(EXTRACT_PROMPT.format(rubric=RUBRIC, notes=notes, **app), AppResearch)

    meta["discovery"] = {
        "search_queries": meta_a["search_queries"],
        "candidates": len([c for c in candidates if "vertexaisearch" not in c]),
        "live_urls": live,
        "bad_candidates": [{"url": c, "status": s} for c, (_, s) in zip(candidates, probes)
                           if not 0 < s < 400 and "vertexaisearch" not in c],
        "pages_read": read_log,
        "retries": meta_a["retries"],
    }
    return {"app": app, "record": rec.model_dump(), "notes": notes, "meta": meta}


def run_pass2(app: dict, pass1: dict, evidence_report: dict) -> dict:
    record = json.dumps(pass1["record"], indent=1)
    flags = evidence_report.get("summary_for_verifier") or "No issues flagged."
    urls = list(dict.fromkeys(e["url"] for e in pass1["record"]["evidence"]))
    pages, read_log = _read_pages(urls, max_pages=8)
    prompt = VERIFY_PROMPT.format(rubric=RUBRIC, flags=flags, urls=_bullets(urls), record=record, **app)
    if pages:
        prompt += "\n\nEXCERPTS OF THE CITED PAGES (fetched by our crawler just now; pages missing here were unreadable):\n" + pages
    notes, meta = llm.grounded_text(llm.VERIFY_MODEL, prompt)
    meta["pages_read"] = read_log
    rec = llm.extract_json(
        VERIFY_EXTRACT_PROMPT.format(rubric=RUBRIC, record=record, notes=notes, **app), VerifiedApp
    )
    return {"app": app, "record": rec.model_dump(), "notes": notes, "meta": meta}
