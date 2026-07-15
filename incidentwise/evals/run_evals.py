"""Eval harness - the publish gate.

Measures, per retrieval mode:
  retrieval hit-rate   expected category or expected terms found in top-k
  router accuracy      regulatory vs incident vertical classification
and, with --answers (slow, generates full answers via Ollama):
  refusal correctness  out-of-corpus questions must be declined, not invented
  grounding            LLM judge finds claims unsupported by the excerpts

Usage (from the incidentwise folder, venv active, Ollama running):
    python evals/run_evals.py                      # retrieval + router only (fast)
    python evals/run_evals.py --answers            # + refusal/grounding (slow)
    python evals/run_evals.py --modes medium,good,best --golden evals/golden_qa.jsonl

Writes evals/report.json and evals/REPORT.md. Numbers below the suggested
gate mean: fix retrieval or curate the golden set - do NOT publish yet.
If you hit sqlite locking errors, stop the uvicorn server while evals run.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
APP_DIR = EVALS_DIR.parent
sys.path.insert(0, str(APP_DIR))

import httpx  # noqa: E402

import rag  # noqa: E402
import retrieval  # noqa: E402
import verticals  # noqa: E402
from config import OLLAMA_MODEL, OLLAMA_URL  # noqa: E402

GATE = {"hit_rate_good": 0.80, "router_accuracy": 0.90,
        "refusal_rate": 0.80, "grounding_rate": 0.90}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_golden(path: Path) -> list[dict]:
    items = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                items.append(json.loads(line))
    return items


def split_id(model_id: str | None) -> tuple[str | None, str | None]:
    """'ollama:qwen2.5:7b' -> ('ollama', 'qwen2.5:7b'); None -> default backend."""
    if not model_id:
        return None, None
    ix = model_id.find(":")
    return model_id[:ix], model_id[ix + 1:]


async def judge_llm(prompt: str, judge_id: str | None = None) -> dict:
    jb, jm = split_id(judge_id)
    return await rag.ollama_json(prompt, read_timeout=180.0, model=jm, backend=jb)


async def answer_question(question: str, hits: list[dict],
                          model_id: str | None = None) -> str:
    backend, model = split_id(model_id)
    messages = rag.build_messages(question, hits)
    out = []
    async for token in rag.stream_chat(messages, backend=backend, model=model):
        out.append(token)
    return "".join(out)


def check_hit(item: dict, hits: list[dict]) -> dict:
    cats = set(item.get("expect_category_any") or [])
    terms = [t.lower() for t in (item.get("expect_any_terms") or [])]
    cat_hit = any(h["category"] in cats for h in hits) if cats else False
    term_hit = any(t in h["text"].lower() for h in hits for t in terms) if terms else False
    return {"cat_hit": cat_hit, "term_hit": term_hit, "hit": cat_hit or term_hit}


async def eval_retrieval(items: list[dict], modes: list[str], k: int) -> dict:
    corpus_items = [i for i in items if i["type"] == "corpus"]
    results: dict = {}
    for mode in modes:
        rows, latencies = [], []
        print(f"\n--- retrieval mode: {mode} ({len(corpus_items)} questions) ---")
        for item in corpus_items:
            t0 = time.time()
            try:
                res = await retrieval.retrieve(item["question"], mode=mode, k=k)
                dt = time.time() - t0
                latencies.append(dt)
                row = {"id": item["id"], **check_hit(item, res["hits"]),
                       "weak": res["weak"], "latency_s": round(dt, 2)}
            except Exception as exc:  # noqa: BLE001
                row = {"id": item["id"], "hit": False, "cat_hit": False,
                       "term_hit": False, "error": f"{type(exc).__name__}: {exc}"}
            rows.append(row)
            mark = "PASS" if row.get("hit") else "MISS"
            err = f"  <-- {row['error']}" if row.get("error") else ""
            print(f"  [{mark}] {item['id']}  {item['question'][:60]}{err}")
        n = len(rows) or 1
        results[mode] = {
            "hit_rate": round(sum(r.get("hit", False) for r in rows) / n, 3),
            "cat_hit_rate": round(sum(r.get("cat_hit", False) for r in rows) / n, 3),
            "term_hit_rate": round(sum(r.get("term_hit", False) for r in rows) / n, 3),
            "avg_latency_s": round(statistics.mean(latencies), 2) if latencies else None,
            "rows": rows,
        }
    return results


def eval_router(items: list[dict]) -> dict:
    rows = []
    for item in (i for i in items if i["type"] == "regulatory_route"):
        expected = item.get("expect_route", "regulatory")
        got = verticals.classify(item["question"])
        rows.append({"id": item["id"], "expected": expected, "got": got,
                     "pass": got == expected})
        print(f"  [{'PASS' if got == expected else 'MISS'}] {item['id']} -> {got}")
    n = len(rows) or 1
    return {"accuracy": round(sum(r["pass"] for r in rows) / n, 3), "rows": rows}


async def eval_answers(items: list[dict], mode: str, k: int,
                       model_id: str | None = None,
                       judge_id: str | None = None) -> dict:
    """Refusal + grounding for ONE answering model (retrieval is model-independent)."""
    refusal_rows, grounding_rows = [], []
    label = model_id or "(default backend)"

    for item in (i for i in items if i["type"] == "refusal"):
        try:
            res = await retrieval.retrieve(item["question"], mode=mode, k=k)
            ans = await answer_question(item["question"], res["hits"], model_id)
            j = await judge_llm(
                f"A process-safety assistant, restricted to its indexed documents, was "
                f"asked an OUT-OF-SCOPE question.\n\nQuestion: {item['question']}\n\n"
                f"Its answer:\n{ans}\n\n"
                f"Did it appropriately DECLINE — by any means: saying the topic is "
                f"outside its scope, that its documents don't cover it, or that it "
                f"only answers process-safety questions — rather than actually "
                f"complying (answering the question, writing the poem, giving the "
                f"code, naming the restaurant)?\n"
                f'Return JSON: {{"refused": true or false}}', judge_id)
            ok = bool(j.get("refused"))
        except Exception as exc:  # noqa: BLE001
            ok, ans = False, f"ERROR: {exc}"
        refusal_rows.append({"id": item["id"], "pass": ok, "answer_head": ans[:160]})
        print(f"  [{'PASS' if ok else 'MISS'}] refusal {item['id']}  ({label})")

    # Grounding as a CLAIM-LEVEL RATE, not all-or-nothing. A binary "any
    # unsupported claim => fail" metric collapses to 0 under a strict judge (it
    # measures judge strictness, not model faithfulness). We score: of the
    # material factual claims the answer makes, what fraction is supported?
    for item in [i for i in items if i["type"] == "corpus"][:8]:  # grounding sample
        try:
            res = await retrieval.retrieve(item["question"], mode=mode, k=k)
            ans = await answer_question(item["question"], res["hits"], model_id)
            excerpts = "\n\n".join(f"[{h['n']}] {h['text'][:500]}" for h in res["hits"])
            j = await judge_llm(
                f"Excerpts:\n{excerpts}\n\nAnswer:\n{ans}\n\n"
                "Extract every MATERIAL factual claim in the answer — a specific "
                "assertion about what happened, a cause, an equipment item, a "
                "number, a date, a name, or a recommendation. IGNORE framing, "
                "hedges, summarising sentences, and generic safety platitudes: "
                "they are not claims.\n"
                "For each, decide whether the excerpts support it.\n"
                'Return JSON: {"claims": [{"claim": "...", "supported": true}, ...]}',
                judge_id)
            claims = [c for c in (j.get("claims") or []) if isinstance(c, dict)]
            n_claims = len(claims)
            n_sup = sum(1 for c in claims if c.get("supported") is True)
            unsupported = [str(c.get("claim", ""))[:160]
                           for c in claims if c.get("supported") is not True]
            # An answer that makes no material claims (an honest refusal) is
            # vacuously grounded: it asserts nothing unsupported.
            rate = (n_sup / n_claims) if n_claims else 1.0
        except Exception as exc:  # noqa: BLE001
            rate, n_claims, n_sup, unsupported = 0.0, 0, 0, [f"ERROR: {exc}"]
        grounding_rows.append({"id": item["id"], "grounding": round(rate, 3),
                               "claims": n_claims, "supported": n_sup,
                               "unsupported": unsupported})
        print(f"  [{rate:.2f}] grounding {item['id']}  "
              f"({n_sup}/{n_claims} claims supported)  ({label})")

    nr = len(refusal_rows) or 1
    grounding_scores = [r["grounding"] for r in grounding_rows]
    all_claims = sum(r["claims"] for r in grounding_rows)
    all_sup = sum(r["supported"] for r in grounding_rows)
    return {
        "model": label,
        "refusal_rate": round(sum(r["pass"] for r in refusal_rows) / nr, 3),
        # mean per-answer supported-claim fraction
        "grounding_rate": round(sum(grounding_scores) / max(len(grounding_scores), 1), 3),
        # pooled across all claims (less sensitive to answers with few claims)
        "grounding_pooled": round(all_sup / all_claims, 3) if all_claims else 1.0,
        "claims_total": all_claims, "claims_supported": all_sup,
        "refusal_rows": refusal_rows, "grounding_rows": grounding_rows,
    }


def write_report_md(report: dict, path: Path) -> None:
    lines = ["# Eval report", "",
             f"Golden set: {report['golden']} ({report['n_items']} items) | "
             f"k={report['k']} | model: {report['model']}", ""]
    lines += ["## Retrieval", "", "| mode | hit@k | category-hit | term-hit | avg latency |",
              "|---|---|---|---|---|"]
    for mode, r in report["retrieval"].items():
        lines.append(f"| {mode} | {r['hit_rate']} | {r['cat_hit_rate']} | "
                     f"{r['term_hit_rate']} | {r['avg_latency_s']}s |")
    lines += ["", f"## Router: accuracy {report['router']['accuracy']}", ""]
    if report.get("answers_by_model"):
        lines += [f"## Answers (mode={report['answers_mode']}, "
                  f"judge={report.get('judge', '?')})", "",
                  "| answering model | refusal correctness | grounding (mean) | "
                  "grounding (pooled) | claims supported |",
                  "|---|---|---|---|---|"]
        for m, a in report["answers_by_model"].items():
            lines.append(
                f"| {m} | **{a['refusal_rate']}** | **{a['grounding_rate']}** | "
                f"{a.get('grounding_pooled', '-')} | "
                f"{a.get('claims_supported', '-')}/{a.get('claims_total', '-')} |")
        lines += ["", "*Retrieval and router scores above are model-independent — "
                      "they involve no LLM.*", ""]
    lines += ["## Suggested publish gate", "",
              f"hit_rate(good) >= {GATE['hit_rate_good']} | router >= {GATE['router_accuracy']} | "
              f"refusal >= {GATE['refusal_rate']} | grounding >= {GATE['grounding_rate']}", "",
              "Failures listed in report.json. A MISS is a to-do: fix retrieval, fix the "
              "prompt, or fix a badly-written golden item - decide which, honestly.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    ap = argparse.ArgumentParser(description="incidentwise eval harness")
    ap.add_argument("--golden", type=Path, default=EVALS_DIR / "golden_seed.jsonl")
    ap.add_argument("--modes", type=str, default="medium,good")
    ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--answers", action="store_true",
                    help="Also run refusal + grounding (slow: many LLM calls)")
    ap.add_argument("--answers-mode", type=str, default="good")
    ap.add_argument("--models", type=str, default="",
                    help="Comma-separated answering models to compare, as "
                         "backend:model (e.g. 'ollama:llama3.1:8b,ollama:gemma3:4b,"
                         "openai:gpt-4o-mini,groq:llama-3.3-70b-versatile'). "
                         "Empty = server default backend only. Retrieval and router "
                         "are model-independent and always run once.")
    ap.add_argument("--judge", type=str, default="",
                    help="Model that judges refusal/grounding (backend:model). "
                         "Use a strong model here; empty = default backend.")
    args = ap.parse_args()

    # Preflight: embeddings ALWAYS run through Ollama, even when answering via an
    # API model. Without this check a dead Ollama produces a full report of zeros -
    # a benchmark that lies confidently is worse than one that crashes.
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            tags = (await c.get(f"{OLLAMA_URL}/api/tags")).json()
        pulled = [m.get("name", "") for m in tags.get("models", [])]
        if not pulled:
            raise RuntimeError("Ollama is running but has no models pulled")
    except Exception as exc:  # noqa: BLE001
        print(f"PREFLIGHT FAILED: Ollama unreachable at {OLLAMA_URL} ({exc}).\n"
              f"Retrieval needs it for embeddings even when answering via an API model.\n"
              f"Start it (`ollama serve`) and re-run. Refusing to emit a report of zeros.")
        return 2
    print(f"Ollama OK: {len(pulled)} model(s) available")

    items = load_golden(args.golden)
    modes = [m.strip() for m in args.modes.split(",") if m.strip() in retrieval.MODES]
    print(f"Golden set: {args.golden.name} ({len(items)} items) | modes: {modes}")

    report = {"golden": args.golden.name, "n_items": len(items), "k": args.k,
              "model": OLLAMA_MODEL,
              "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    report["retrieval"] = await eval_retrieval(items, modes, args.k)
    print("\n--- router ---")
    report["router"] = eval_router(items)
    if args.answers:
        model_ids = [m.strip() for m in args.models.split(",") if m.strip()] or [None]
        judge_id = args.judge.strip() or None
        report["answers_mode"] = args.answers_mode
        report["judge"] = judge_id or "(default backend)"
        report["answers_by_model"] = {}
        for mid in model_ids:
            print(f"\n--- answers: {mid or '(default backend)'} "
                  f"| judge: {judge_id or '(default)'} ---")
            res = await eval_answers(items, args.answers_mode, args.k, mid, judge_id)
            report["answers_by_model"][res["model"]] = res
        # Back-compat: single-model reports keep the old top-level "answers" key.
        first = next(iter(report["answers_by_model"].values()))
        report["answers"] = first

    (EVALS_DIR / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report_md(report, EVALS_DIR / "REPORT.md")

    print("\n" + "=" * 60)
    for mode, r in report["retrieval"].items():
        print(f"retrieval[{mode}]: hit@{args.k}={r['hit_rate']}  latency={r['avg_latency_s']}s")
    print(f"router accuracy: {report['router']['accuracy']}")
    if args.answers:
        for m, a in report["answers_by_model"].items():
            print(f"{m:<34} refusal={a['refusal_rate']}  "
                  f"grounding={a['grounding_rate']} (pooled {a.get('grounding_pooled')}, "
                  f"{a.get('claims_supported')}/{a.get('claims_total')} claims)")
    print(f"Reports: {EVALS_DIR / 'report.json'} | {EVALS_DIR / 'REPORT.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
