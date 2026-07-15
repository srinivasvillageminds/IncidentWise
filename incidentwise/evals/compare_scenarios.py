"""Side-by-side drill-scenario comparison: small local models vs OpenAI.

Generates the SAME corpus-grounded scenario with each model and writes one
markdown file for human judging. This is groundwork for the next version
(near-perfect hypothetical incident generation): before optimizing prompts,
know what each model class actually produces.

Usage (Ollama running; models pulled):
  python evals/compare_scenarios.py --theme "LPG leak at pump seal" \
      --unit "Refinery unit" --models llama3.1:8b,qwen2.5:7b
  add --openai gpt-4o-mini            (needs OPENAI_API_KEY in .env)

Output: evals/scenario_comparison.md with a scoring rubric per response.
Judge with the rubric, honestly - the point is to find where small models
break (invented physics, un-cited claims, generic scenarios), not to
declare a winner by vibes.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS_DIR.parent))

import httpx  # noqa: E402

import rag  # noqa: E402
import scenario as scenario_mod  # noqa: E402
from config import OPENAI_API_KEY  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RUBRIC = """
**Judge (1-5 each):**
- Technical feasibility - could this sequence physically happen as told?
- Groundedness - are [n] citations present and do they map to real seed excerpts?
- No fabrication - zero invented standards, company names, casualty figures
- Provocation - would this trigger real discussion in a monthly safety meeting?
- Local relatability - permit-to-work, shift handover, Indian plant practice
- Validation checklist quality - could an expert act on it?

Score: __/30 | Verdict: use / edit / discard
"""


async def gen_ollama(messages: list[dict], model: str) -> str:
    out = []
    async for token in rag.stream_chat(messages, temperature=0.7, model=model):
        out.append(token)
    return "".join(out)


async def gen_openai(messages: list[dict], model: str) -> str:
    if not OPENAI_API_KEY:
        return "(skipped: OPENAI_API_KEY not set)"
    payload = {"model": model, "messages": messages, "temperature": 0.7,
               "max_tokens": 2500}
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, read=180.0)) as client:
        r = await client.post("https://api.openai.com/v1/chat/completions",
                              headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                              json=payload)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", required=True)
    ap.add_argument("--unit", default="Refinery unit")
    ap.add_argument("--difficulty", default="intermediate")
    ap.add_argument("--models", default="llama3.1:8b",
                    help="Comma-separated Ollama models")
    ap.add_argument("--openai", default="", help="OpenAI model, e.g. gpt-4o-mini")
    ap.add_argument("--out", type=Path, default=EVALS_DIR / "scenario_comparison.md")
    args = ap.parse_args()

    result = await scenario_mod.get_seeds(args.theme, args.unit)
    seeds = result["hits"]
    messages = scenario_mod.build_messages(seeds, args.theme, args.unit, args.difficulty)
    print(f"Seeds: {len(seeds)} excerpts ({result['pipeline']})")

    sections = [f"# Scenario comparison — \"{args.theme}\" ({args.unit}, {args.difficulty})",
                "", f"Seeds: " + "; ".join(f"[{h['n']}] {h['title'][:50]} p.{h['page']}"
                                           for h in seeds), ""]

    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        print(f"Generating with {model} ...")
        t0 = time.time()
        try:
            text = await gen_ollama(messages, model)
        except Exception as exc:  # noqa: BLE001
            text = f"(failed: {exc})"
        sections += [f"## {model} ({time.time() - t0:.0f}s)", "", text, "", RUBRIC, "---", ""]

    if args.openai:
        print(f"Generating with OpenAI {args.openai} ...")
        t0 = time.time()
        try:
            text = await gen_openai(messages, args.openai)
        except Exception as exc:  # noqa: BLE001
            text = f"(failed: {exc})"
        sections += [f"## OpenAI {args.openai} ({time.time() - t0:.0f}s)", "", text, "",
                     RUBRIC, "---", ""]

    args.out.write_text("\n".join(sections), encoding="utf-8")
    print(f"Wrote {args.out} - now judge with the rubric.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
