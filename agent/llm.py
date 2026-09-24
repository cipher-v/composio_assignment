"""Thin Gemini-on-Vertex wrapper: grounded (Google Search + URL context) calls with structured output."""
import os
import random
import threading
import time

import google.auth
from google import genai
from google.genai import types

PROJECT = os.environ.get("COMPOSIO_RESEARCH_GCP_PROJECT", "composio-research")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
RESEARCH_MODEL = os.environ.get("RESEARCH_MODEL", "gemini-3-flash-preview")
LIGHT_MODEL = os.environ.get("LIGHT_MODEL", "gemini-2.5-flash")  # URL discovery + JSON extraction (separate quota)
VERIFY_MODEL = os.environ.get("VERIFY_MODEL", "gemini-2.5-pro")  # 3.1-pro-preview hit 429 quota on the new project

_client = None


_lock = threading.Lock()


def client() -> genai.Client:
    # Locked: concurrent lazy init created several clients; the losers were GC'd and closed mid-request
    # ("Cannot send a request, as the client has been closed").
    global _client
    with _lock:
        if _client is not None:
            return _client
        # Pin billing/quota to the research project explicitly; never rely on gcloud defaults.
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"], quota_project_id=PROJECT
        )
        _client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION, credentials=creds,
                               http_options=types.HttpOptions(timeout=240_000))  # hard per-call deadline (ms)
    return _client


def _retry(fn, retries=8):
    errors = []
    for attempt in range(retries):
        try:
            return fn(), errors
        except Exception as e:  # 429 / 5xx / occasional invalid JSON
            errors.append(str(e)[:120])
            time.sleep(min(60, 4 * 2 ** attempt) * random.uniform(0.6, 1.4))
    raise RuntimeError(f"failed after {retries} attempts: {errors[-1]}")


def _thinking(level):
    return types.ThinkingConfig(thinking_level=level) if level else None


def grounded_text(model: str, prompt: str, thinking: str | None = None):
    """Free-text call with Google Search. (Gemini skips tools in JSON mode, so research runs in text mode.)
    The URL-context tool is NOT used: it made calls ~10x slower (repeated failed fetches); our own crawler
    fetches the pages and passes excerpts in the prompt instead."""
    cfg = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.2,
        thinking_config=_thinking(thinking),
    )

    def call():
        r = client().models.generate_content(model=model, contents=prompt, config=cfg)
        if not r.text:
            raise ValueError("empty response")
        return r

    r, errors = _retry(call)
    meta = _meta(r, model)
    meta["retries"] = errors
    return r.text, meta


def extract_json(prompt: str, schema, model: str | None = None):
    """Tool-less structured extraction from research notes."""
    cfg = types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema, temperature=0,
                                      thinking_config=types.ThinkingConfig(thinking_budget=0))

    def call():
        r = client().models.generate_content(model=model or LIGHT_MODEL, contents=prompt, config=cfg)
        return r.parsed if r.parsed is not None else schema.model_validate_json(r.text)

    parsed, _ = _retry(call)
    return parsed


def grounded_json(model: str, prompt: str, schema, retries: int = 5):
    """Run one grounded call and return (parsed_model, meta).

    meta records which URLs the model actually opened (url_context) and which
    web sources grounded the answer, so we can audit citations later.
    """
    cfg = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch()), types.Tool(url_context=types.UrlContext())],
        response_mime_type="application/json",
        response_schema=schema,
        temperature=0.2,
    )
    last = None
    errors = []
    for attempt in range(retries):
        try:
            r = client().models.generate_content(model=model, contents=prompt, config=cfg)
            parsed = r.parsed if r.parsed is not None else schema.model_validate_json(r.text)
            meta = _meta(r, model)
            meta["retries"] = errors
            return parsed, meta
        except Exception as e:  # 429 / 5xx / occasional invalid JSON
            last = e
            errors.append(str(e)[:120])
            time.sleep(min(60, 5 * 2 ** attempt))
    raise RuntimeError(f"{model} failed after {retries} attempts: {last}")


def _meta(r, model):
    cand = r.candidates[0]
    opened, searched, queries = [], [], []
    ucm = getattr(cand, "url_context_metadata", None)
    for m in (ucm.url_metadata if ucm and ucm.url_metadata else []):
        opened.append({"url": m.retrieved_url, "status": str(m.url_retrieval_status).split(".")[-1]})
    gm = getattr(cand, "grounding_metadata", None)
    if gm:
        queries = list(gm.web_search_queries or [])
        for ch in gm.grounding_chunks or []:
            if ch.web:
                searched.append({"title": ch.web.title, "uri": ch.web.uri})
    usage = r.usage_metadata
    return {
        "model": model,
        "opened_urls": opened,
        "search_queries": queries,
        "search_sources": searched,
        "tokens": getattr(usage, "total_token_count", None),
    }
