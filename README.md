# Can it be an agent toolkit? Researching 100 apps with an agent

Composio AI Product Ops take-home. An agent researches 100 apps (auth, self-serve vs gated, API surface, MCP,
buildability verdict, with evidence URLs and quotes). The results are clustered into patterns and verified in three loops.
The output is one self-contained page: `site/index.html`.

## What the pipeline does

```
apps.json ─► 1 discover ─► 2 validate URLs ─► 3 read + research ─► 4 extract ─► pass1/
             (Gemini 2.5 Flash    (HTTP check,      (crawler fetches pages,     (strict JSON
              + Google Search)     drop 404s)        Gemini 3 Flash + Search     schema, enums)
                                                     writes cited notes)
pass1/ ─► loop 1: citation crawler + lint ─► loop 2: adversarial verifier ─► pass2/ ─► crawler again
          (URL alive? vendor domain?          (Gemini 2.5 Pro + Search, re-derives
           quote really on page? contradictions?) every field, gets loop-1 flags)
pass2/ ─► loop 3: human spot-check (review/review.html, stratified random sample) ─► merge ─► analyze ─► page
```

| Stage | Code | Output |
|---|---|---|
| Research (pass 1) | `agent/research.py::run_pass1` | `data/pass1/NNN.json` (record, notes, search queries, pages read) |
| Citation check | `agent/evidence.py` (no LLM) | `data/evidence{1,2}/NNN.json` |
| Verifier (pass 2) | `agent/research.py::run_pass2` | `data/pass2/NNN.json` (corrected record, change log, needs_human) |
| Human review | `agent/review.py` | `review/review.html` → `review/human_labels.json` → `data/accuracy.json` |
| Merge | `agent/merge.py` | `data/final.json` (per-field provenance: confirmed / verifier_fixed / human_fixed) |
| Patterns | `agent/analyze.py` | `data/patterns.json` |
| Page | `agent/page.py` + `agent/page_template.html` | `site/index.html`, `site/data/*.json`, `site/llms.txt` |

The schema and the rubric (definitions of self-serve, gated, verdicts, and so on) are in `agent/schema.py`. The same
rubric is given to the researcher and the verifier.

## Run it

Needs Python 3.12 and a Google Cloud project with the Vertex AI API enabled.

```bash
gcloud auth application-default login
uv venv && uv pip install -r requirements.txt       # or: python -m venv .venv && pip install -r requirements.txt
export COMPOSIO_RESEARCH_GCP_PROJECT=<your-project> # billing/quota is pinned to this project explicitly

python run.py research --ids 4,31        # try two apps first (Attio, Google Ads)
python run.py all                        # research → evidence → verify → evidence → merge → analyze → page
python run.py review --per-category 2    # open review/review.html, fill in, click Export → save as review/human_labels.json
python run.py merge && python run.py score && python run.py analyze && python run.py page
```

Every stage caches per-app JSON, so reruns only process missing apps (`--force` redoes them; `--ids` restricts to
specific apps). Models can be overridden with `RESEARCH_MODEL`, `LIGHT_MODEL` and `VERIFY_MODEL`.

## Results (run of 2026-09-24)

- 100/100 apps researched with cited evidence. 95 were re-checked by the verifier; 5 failed verification and show first-pass values.
- Headlines: 39% ready now, 53% buildable with friction, 8% need outreach. OAuth2 is the primary auth for 64%.
  The most common blockers are app review/approval and paid plans (24 apps each). 74% have an official MCP server.
- Accuracy on a stratified random sample of 20 apps (80 field judgements): **first pass 85%, after verifier 85%**. The
  verifier fixed 9 answers and broke 9. Ground truth came from a blind re-check by a different model family (Claude)
  against live docs. The 25 disagreements were each decided against the vendor's own page, with the quote recorded in
  `review/dispute_decisions.json`. These were model-adjudicated, not decided by a human.
- What measurably helped: validating URLs before research (183 invented doc URLs dropped) and the blind cross-check.
  Tested next step: `run.py judge`, an evidence judge for pass-1 vs verifier disagreements with the burden of proof on
  the change (tried on 3 apps, not yet run on all 100).

## Design decisions (and what went wrong on the way)

- **Research runs in text mode, extraction is a separate call.** With a JSON response schema, Gemini answered from memory
  and skipped Google Search entirely (0 search queries). In text mode it searches.
- **Own crawler instead of the URL-context tool.** URL context made each call about 10x slower (repeated failed fetches). The
  pipeline fetches pages itself and passes keyword-windowed excerpts to the model, so "pages read" is logged and auditable.
- **URL validation before reading.** The discovery step proposes some doc URLs that don't exist (plausible-looking
  paths that return 404). They are dropped before the research step sees them.
- **Different model for verification.** The verifier (Gemini 2.5 Pro) is a different model from the researcher
  (Gemini 3 Flash), with an adversarial prompt and the crawler's findings, so errors are less correlated.
- **Quota.** On a fresh project, Gemini 3 Flash/Pro preview quotas returned 429s under parallel load. Light steps moved
  to `gemini-2.5-flash`, and the retries use jittered backoff.
