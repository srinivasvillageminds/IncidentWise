"""Headless Playground runner - the same three suites the UI runs, from the CLI.

Use this for batch/Colab runs where babysitting a browser is silly. Produces the
same artifacts the UI reads back (testbench_report.json, drillbench_report.json)
plus a human-readable PLAYGROUND.md you can commit next to REPORT.md.

  ask     24 questions of mixed corpus-relevance (corpus / regulatory+web /
          analytics / out-of-scope / MIXED "safety question + restaurants" /
          injection) - scored on FETCH, ANSWER (both deterministic) and SANITY.
  guard   the tricky inputs only, re-run at each guard level: what each rung buys.
  drill   4 permit-class scenarios per model, judged for absurd causality,
          contradictions, role misuse, fabrication.

Examples:
  python evals/run_playground.py --suite ask   --models "ollama:qwen2.5:7b,openai:gpt-4o-mini" --judge openai:gpt-4o-mini
  python evals/run_playground.py --suite guard --models "ollama:llama3.1:8b" --levels off,l1,l2
  python evals/run_playground.py --suite drill --models "ollama:llama3.1:8b,ollama:gemma3:12b,ollama:qwen2.5:14b,openai:gpt-4o-mini" --judge openai:gpt-4o-mini
  python evals/run_playground.py --suite all   --models "..." --judge openai:gpt-4o-mini
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS_DIR.parent))

import testbench  # noqa: E402

MD_PATH = EVALS_DIR / "PLAYGROUND.md"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def _run(gen, label: str, tag: str = "") -> dict:
    print(f"\n{'='*70}\n{label}\n{'='*70}")
    summary = {}
    rows_seen = []
    n = 0
    # Incremental crash-safe log: a 3-hour run must never be lost to a session
    # expiry. Every row is appended to disk the moment it arrives.
    jl_path = EVALS_DIR / f"rows_{tag or 'live'}.jsonl"
    jl = jl_path.open("a", encoding="utf-8")
    async for event, data in gen:
        if event == "row":
            n += 1
            rows_seen.append(data)
            jl.write(json.dumps(data, ensure_ascii=False) + "\n")
            jl.flush()
            if data.get("suite") == "drill":
                defects = data.get("defects") or data.get("absurdities") or []
                print(f"  [{n:>3}] {data['model']:<28} {data['q_id']:<9} "
                      f"avg {data.get('avg')}/5 · {len(defects)} defect(s) · "
                      f"{data.get('verdict','?')} · {data.get('latency_s')}s")
                for d in defects[:3]:
                    if isinstance(d, dict):
                        print(f"        ! [{d.get('code','A?')}] {d.get('quote','')[:110]}")
                    else:
                        print(f"        ! {str(d)[:110]}")
            else:
                f = "PASS" if data["fetch"] else "MISS"
                a = "PASS" if data["answer"] else "MISS"
                print(f"  [{n:>3}] {data['model']:<28} {data['q_id']:<4} "
                      f"{data['kind']:<11} fetch {f} · answer {a} · "
                      f"sanity {data['sanity']} · {data['latency_s']}s")
        elif event == "summary":
            summary = data
    jl.close()
    if rows_seen:
        print(f"  (rows appended live to {jl_path.name} — survives a session crash)")
    return summary


def md_ask(s: dict) -> list[str]:
    out = [f"## {s.get('suite','ask').title()} suite "
           f"(guard={s.get('guard','-')}, judge={s.get('judge')})", "",
           "| model | fetch | answer | sanity /5 |", "|---|---|---|---|"]
    for m, v in (s.get("models") or {}).items():
        out.append(f"| {m} | {v['fetch_pct']}% | {v['answer_pct']}% | {v.get('sanity_avg','-')} |")
    out.append("")
    for m, v in (s.get("models") or {}).items():
        if v.get("by_kind"):
            out.append(f"**{m}** by question kind: "
                       + " · ".join(f"{k} {p}%" for k, p in v["by_kind"].items()))
    out.append("")
    return out


_AXES = ["causality_realism", "internal_consistency", "role_accuracy",
         "physical_plausibility", "no_fabrication", "training_value"]
_AX_SHORT = {"causality_realism": "causality", "internal_consistency": "consistency",
             "role_accuracy": "roles", "physical_plausibility": "physics",
             "no_fabrication": "no-fabrication", "training_value": "training value"}


def md_drill(s: dict) -> list[str]:
    models = s.get("models") or {}
    codes = s.get("codes") or {}
    out = [f"## Drill suite (judge={s.get('judge')})", "",
           "**Headline** — defects per generated scenario, and whether a safety "
           "professional could use it.", "",
           "| model | overall /5 | defects per drill | verdicts | scored | failed |",
           "|---|---|---|---|---|---|"]
    for m, v in sorted(models.items(), key=lambda kv: (kv[1].get("overall") or 0)):
        out.append(
            f"| {m} | {v.get('overall') if v.get('overall') is not None else '—'} | "
            f"{v.get('defects_per_drill') if v.get('defects_per_drill') is not None else '—'} | "
            + ", ".join(f"{k}:{n}" for k, n in (v.get('verdicts') or {}).items())
            + f" | {v.get('drills_scored', 0)} | {v.get('drills_failed', 0)} |")
    out += ["", "*A model with `failed > 0` did not produce a scorable structured "
                "spec — that is a capability failure, not a low score.*", ""]

    # Score matrix: models x axes
    out += ["### Scores by axis (1–5, higher is better)", "",
            "| model | " + " | ".join(_AX_SHORT[a] for a in _AXES) + " |",
            "|---" * (len(_AXES) + 1) + "|"]
    for m, v in models.items():
        sc = v.get("avg_scores") or {}
        if not sc:
            continue
        out.append(f"| {m} | " + " | ".join(str(sc.get(a, "—")) for a in _AXES) + " |")
    out.append("")

    # Defect profile: models x A-codes
    if codes:
        keys = sorted(codes)
        out += ["### Defect profile — what KIND of nonsense each model produces", "",
                "| model | " + " | ".join(f"{k} {codes[k]}" for k in keys) + " | total |",
                "|---" * (len(keys) + 2) + "|"]
        for m, v in models.items():
            bc = v.get("defects_by_code") or {}
            if not v.get("drills_scored"):
                continue
            out.append(f"| {m} | " + " | ".join(str(bc.get(k, 0)) for k in keys)
                       + f" | {v.get('defects_total', 0)} |")
        out.append("")
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description="Headless Playground runner")
    ap.add_argument("--suite", choices=["ask", "guard", "drill", "all"], default="ask")
    ap.add_argument("--models", required=True,
                    help="Comma-separated backend:model ids "
                         "(ollama:qwen2.5:7b,openai:gpt-4o-mini,groq:...)")
    ap.add_argument("--judge", default="", help="Judge model id (use a strong one)")
    ap.add_argument("--levels", default="off,l1,l2",
                    help="Guard levels for --suite guard")
    ap.add_argument("--guard", default="l1", help="Guard level for --suite ask")
    ap.add_argument("--tag", default="",
                    help="Archive tag, e.g. 'round2'. Writes PLAYGROUND_<tag>.md "
                         "so earlier rounds are never overwritten.")
    args = ap.parse_args()

    # Same preflight as run_evals: retrieval needs Ollama for embeddings even when
    # every answering model is an API model. Fail loudly, never report zeros.
    import httpx  # local import; keeps the module import-light
    from config import OLLAMA_URL
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            tags = (await c.get(f"{OLLAMA_URL}/api/tags")).json()
        if not tags.get("models"):
            raise RuntimeError("Ollama running but no models pulled")
        print(f"Ollama OK: {len(tags['models'])} model(s) available")
    except Exception as exc:  # noqa: BLE001
        print(f"PREFLIGHT FAILED: Ollama unreachable at {OLLAMA_URL} ({exc}). "
              f"Start it and re-run; refusing to emit a report of zeros.")
        return 2

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    judge = args.judge.strip() or None
    levels = [l.strip() for l in args.levels.split(",") if l.strip()]
    t0 = time.time()
    md = [f"# Playground report", "",
          f"models: {', '.join(models)} | judge: {judge or '(default)'} | "
          f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}", ""]

    suites = ["ask", "guard", "drill"] if args.suite == "all" else [args.suite]
    for suite in suites:
        tag = f"{args.tag or 'live'}_{suite}"
        if suite == "ask":
            s = await _run(testbench.run_ask(models, judge, args.guard),
                           f"ASK suite · guard={args.guard} · {len(models)} model(s)", tag)
            md += md_ask(s)
        elif suite == "guard":
            s = await _run(testbench.run_guard(models, judge, levels),
                           f"GUARD suite · levels={levels} · {len(models)} model(s)", tag)
            md += md_ask(s)
        else:
            s = await _run(testbench.run_drill(models, judge),
                           f"DRILL suite · 4 scenarios x {len(models)} model(s)", tag)
            md += md_drill(s)
        # write the markdown after EVERY suite, not just at the end
        partial = (EVALS_DIR / f"PLAYGROUND_{args.tag}.md") if args.tag else MD_PATH
        partial.write_text("\n".join(md), encoding="utf-8")
        print(f"  (partial report written to {partial.name})")

    md += ["---", "*Fetch and Answer are deterministic checks. Sanity and drill "
           "scores come from an LLM judge - use a strong judge model, and treat "
           "these as screening, not ground truth: a competent safety professional "
           "remains the validator.*", ""]
    out_path = (EVALS_DIR / f"PLAYGROUND_{args.tag}.md") if args.tag else MD_PATH
    out_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\nDone in {time.time()-t0:.0f}s → {out_path}")
    print("Raw rows: testbench_report.json / drillbench_report.json")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
