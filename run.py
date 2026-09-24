"""Composio toolkit-research pipeline.

    python run.py research  [--ids 1,2,3] [--workers 6] [--force]   # pass 1: discover (2.5 Flash) -> validate URLs -> crawl -> research (3 Flash + Search) -> extract
    python run.py evidence  --pass 1                                 # loop 1: fetch every cited URL, check quotes, lint
    python run.py verify    [--ids ...]                              # loop 2: Gemini 2.5 Pro adversarial verifier
    python run.py evidence  --pass 2                                 # re-check citations of the verified records
    python run.py judge                                              # optional, experimental: evidence judge where pass1 and pass2 disagree (not in `all`)
    python run.py merge                                              # pass2 + sample corrections -> data/final.json
    python run.py review                                             # build review/review.html for the human sample
    python run.py disputes                                           # blind Claude check vs Gemini -> review/disputes.html
    python run.py resolve                                            # agreed values + human dispute picks -> review/human_labels.json
    python run.py score                                              # accuracy of pass1/pass2 vs those labels
    python run.py analyze                                            # cluster -> data/patterns.json
    python run.py page                                               # build site/index.html
    python run.py all                                                # research -> evidence -> verify -> evidence -> merge -> analyze -> page

Every step caches per-app JSON under data/, so reruns only redo missing apps (use --force to redo).
"""
import argparse
import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
APPS = json.loads((DATA / "apps.json").read_text(encoding="utf-8"))
BY_ID = {a["id"]: a for a in APPS}


def pdir(name: str) -> Path:
    d = DATA / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def load(name: str, app_id: int):
    f = DATA / name / f"{app_id:03d}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def save(name: str, app_id: int, obj):
    (pdir(name) / f"{app_id:03d}.json").write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")


def select(ids: str | None):
    if not ids:
        return APPS
    wanted = {int(x) for x in ids.split(",")}
    return [a for a in APPS if a["id"] in wanted]


def fan_out(apps, fn, workers, label):
    t0 = time.time()
    done = fail = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fn, a): a for a in apps}
        for f in as_completed(futs):
            a = futs[f]
            try:
                f.result()
                done += 1
                print(f"[{label}] ok   {a['id']:3d} {a['name']}  ({done + fail}/{len(apps)}, {time.time() - t0:.0f}s)", flush=True)
            except Exception as e:
                fail += 1
                print(f"[{label}] FAIL {a['id']:3d} {a['name']}: {e}", flush=True)
                (pdir("errors") / f"{label}_{a['id']:03d}.txt").write_text(traceback.format_exc(), encoding="utf-8")
    print(f"[{label}] done={done} fail={fail} in {time.time() - t0:.0f}s")


def cmd_research(args):
    from agent.research import run_pass1

    todo = [a for a in select(args.ids) if args.force or not load("pass1", a["id"])]

    def job(a):
        t = time.time()
        out = run_pass1(a)
        out["meta"]["seconds"] = round(time.time() - t, 1)
        save("pass1", a["id"], out)

    fan_out(todo, job, args.workers, "pass1")


def cmd_evidence(args):
    from agent.evidence import check

    src, dst = f"pass{args.pass_}", f"evidence{args.pass_}"
    todo = [a for a in select(args.ids) if load(src, a["id"]) and (args.force or not load(dst, a["id"]))]
    fan_out(todo, lambda a: save(dst, a["id"], check(a, load(src, a["id"])["record"])), args.workers, dst)


def cmd_verify(args):
    from agent.research import run_pass2

    todo = [a for a in select(args.ids)
            if load("pass1", a["id"]) and load("evidence1", a["id"]) and (args.force or not load("pass2", a["id"]))]

    def job(a):
        t = time.time()
        out = run_pass2(a, load("pass1", a["id"]), load("evidence1", a["id"]))
        out["meta"]["seconds"] = round(time.time() - t, 1)
        save("pass2", a["id"], out)

    fan_out(todo, job, args.workers, "pass2")


def cmd_judge(args):
    from agent.judge import run_judge

    todo = [a for a in select(args.ids)
            if load("pass1", a["id"]) and load("pass2", a["id"]) and (args.force or not load("judge", a["id"]))]

    def job(a):
        t = time.time()
        out = run_judge(a, load("pass1", a["id"]), load("pass2", a["id"]))
        out.setdefault("meta", {})["seconds"] = round(time.time() - t, 1)
        save("judge", a["id"], out)

    fan_out(todo, job, args.workers, "judge")


def cmd_merge(args):
    from agent.merge import merge
    merge(DATA, APPS, load)


def cmd_review(args):
    from agent.review import build_review
    build_review(ROOT, APPS, load, n_per_category=args.per_category, seed=args.seed)


def cmd_disputes(args):
    from agent.crosscheck import build_disputes
    build_disputes(ROOT, load)


def cmd_resolve(args):
    from agent.crosscheck import resolve
    resolve(ROOT, load)


def cmd_score(args):
    from agent.review import score
    score(ROOT, load)


def cmd_analyze(args):
    from agent.analyze import analyze
    analyze(DATA)


def cmd_page(args):
    from agent.page import build_page
    build_page(ROOT)


def cmd_all(args):
    for step in (cmd_research, cmd_evidence_1, cmd_verify, cmd_evidence_2, cmd_merge, cmd_analyze, cmd_page):
        step(args)


def cmd_evidence_1(args):
    args.pass_ = 1
    cmd_evidence(args)


def cmd_evidence_2(args):
    args.pass_ = 2
    cmd_evidence(args)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["research", "evidence", "verify", "judge", "merge", "review", "disputes", "resolve", "score", "analyze", "page", "all"])
    ap.add_argument("--ids", help="comma-separated app ids (default: all 100)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--pass", dest="pass_", type=int, default=1, choices=[1, 2])
    ap.add_argument("--per-category", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    globals()[f"cmd_{args.cmd}"](args)


if __name__ == "__main__":
    sys.exit(main())
