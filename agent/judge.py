"""Loop 2b: evidence judge for fields where the researcher (pass 1) and the verifier (pass 2) disagree.

Measured on the 20-app sample, the verifier fixed 9 answers and broke 9 (net zero): it over-corrects. So instead of
"last model wins", a judge sees both answers with their cited pages (fetched by our crawler) and must pick the one a
quoted vendor sentence supports. Burden of proof is on the change: without a supporting quote, pass 1 stands.
"""
import json

from pydantic import BaseModel, Field

from . import llm
from .research import _read_pages
from .schema import RUBRIC

FIELDS = ["primary_auth", "access", "mcp", "verdict", "blocker", "auth_methods", "api_styles"]
NOTE = {"primary_auth": "auth_notes", "auth_methods": "auth_notes", "access": "access_notes", "mcp": "mcp_notes",
        "verdict": "blocker_notes", "blocker": "blocker_notes", "api_styles": "api_notes"}
CLAIM = {"primary_auth": "auth", "auth_methods": "auth", "access": "access", "mcp": "mcp", "verdict": "verdict",
         "blocker": "verdict", "api_styles": "api"}


class Ruling(BaseModel):
    field: str
    winner: str = Field(description="'A' (first answer), 'B' (second answer) or 'neither'")
    value: str = Field(description="The final value (for list fields, comma-separated enum values)")
    quote: str = Field(description="Verbatim sentence from a vendor page that supports the final value, or '' if none")
    url: str
    reason: str


class Rulings(BaseModel):
    rulings: list[Ruling]


PROMPT = """You are a strict judge. Two research agents disagree about the app "{name}" ({hint}).
For each disputed field below decide which answer the evidence supports.

Rules:
- Base your decision on vendor pages (the excerpts below, or pages you find via search). Quote the exact sentence.
- Burden of proof is on changing answer A: pick B only if a vendor sentence clearly supports B over A.
  If neither answer is clearly supported, keep A.
- Pick 'neither' only if a vendor sentence clearly supports a third value (give it).
- Apply the definitions exactly.
{rubric}
DISPUTED FIELDS:
{disputes}

PAGE EXCERPTS (fetched by our crawler from the URLs both agents cited):
{pages}

Write a ruling per field: field, winner (A/B/neither), final value, the supporting quote + URL, and a one-line reason."""


def disputes_for(r1, r2):
    out = []
    for f in FIELDS:
        a, b = r1[f], r2[f]
        if (sorted(a) if isinstance(a, list) else a) != (sorted(b) if isinstance(b, list) else b):
            out.append(f)
    return out


def run_judge(app, p1, p2):
    r1, r2 = p1["record"], p2["record"]
    fields = disputes_for(r1, r2)
    if not fields:
        return {"app": app, "rulings": [], "meta": {}}
    lines = []
    urls = []
    for f in fields:
        ea = [e for e in r1["evidence"] if e["claim"] == CLAIM[f]][:2]
        eb = [e for e in r2["evidence"] if e["claim"] == CLAIM[f]][:2]
        urls += [e["url"] for e in ea + eb]
        lines.append(
            f"- {f}:\n    A = {r1[f]}  (A's note: {r1[NOTE[f]]}; A's sources: {[e['url'] + ' :: ' + e['quote'] for e in ea]})\n"
            f"    B = {r2[f]}  (B's note: {r2[NOTE[f]]}; B's sources: {[e['url'] + ' :: ' + e['quote'] for e in eb]})"
        )
    pages, read_log = _read_pages(list(dict.fromkeys(urls)), max_pages=8)
    notes, meta = llm.grounded_text(
        llm.VERIFY_MODEL,
        PROMPT.format(name=app["name"], hint=app["hint"], rubric=RUBRIC, disputes="\n".join(lines), pages=pages or "(none readable)"),
    )
    rulings = llm.extract_json(f"Extract one ruling per field from these judge notes. Fields: {fields}\n\n{notes}", Rulings)
    meta["pages_read"] = read_log
    return {"app": app, "fields": fields, "rulings": [r.model_dump() for r in rulings.rulings], "notes": notes, "meta": meta}


def apply_rulings(r1, r2, judged):
    """Final record = verifier record, with every disputed field replaced by the judge's ruling."""
    rec = dict(r2)
    for ru in (judged or {}).get("rulings", []):
        f = ru["field"]
        if f not in r1:
            continue
        if ru["winner"] == "A":
            rec[f] = r1[f]
        elif ru["winner"] == "B":
            rec[f] = r2[f]
        elif ru["value"]:
            rec[f] = [v.strip() for v in ru["value"].split(",")] if isinstance(r1[f], list) else ru["value"].strip()
    return rec
