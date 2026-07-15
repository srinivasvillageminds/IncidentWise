"""Draft golden Q&A items from the indexed corpus, for HUMAN curation.

Samples chunks across categories, asks the local LLM to write one factual
question each chunk answers, and emits golden_draft.jsonl. You then:
  1. Read every draft. Delete bad ones (vague, unanswerable, trivial).
  2. Fix expect_any_terms to words that MUST appear in a correct retrieval.
  3. Append the survivors to golden_qa.jsonl (keep golden_seed.jsonl intact).

An uncurated golden set is worse than none - it launders bad questions
into fake accuracy numbers. Budget 30-60 minutes of honest review.

Usage:  python evals/make_golden.py --n 30
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS_DIR.parent))

import httpx  # noqa: E402

from config import OLLAMA_MODEL, OLLAMA_URL  # noqa: E402
from rag import get_collection  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def draft_qa(chunk: str) -> dict:
    prompt = (
        "You write evaluation questions for a retrieval system over Indian "
        "process-safety documents.\n\nExcerpt:\n" + chunk[:1200] + "\n\n"
        "Write ONE specific, factual question that this excerpt clearly answers "
        "(the kind a safety engineer would actually ask), the short answer, and "
        "3-5 distinctive lowercase key terms from the excerpt that a correct "
        "retrieval must contain. Return JSON: "
        '{"question": "...", "answer": "...", "key_terms": ["...", "..."]}'
    )
    payload = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
               "format": "json", "options": {"temperature": 0.3, "num_ctx": 4096}}
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=180.0)) as client:
        r = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
        r.raise_for_status()
        return json.loads(r.json().get("response", "{}"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30, help="Drafts to generate")
    ap.add_argument("--out", type=Path, default=EVALS_DIR / "golden_draft.jsonl")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    col = get_collection()
    data = col.get(include=["documents", "metadatas"])
    if not data["ids"]:
        print("Index is empty - run ingest.py first.")
        return 1

    by_cat: dict[str, list[int]] = defaultdict(list)
    for i, meta in enumerate(data["metadatas"]):
        if len(data["documents"][i]) > 300:  # skip stub chunks
            by_cat[meta.get("category", "?")].append(i)

    rng = random.Random(args.seed)
    picks: list[int] = []
    cats = sorted(by_cat)
    while len(picks) < args.n and any(by_cat[c] for c in cats):
        for c in cats:  # round-robin across categories for coverage
            if by_cat[c] and len(picks) < args.n:
                picks.append(by_cat[c].pop(rng.randrange(len(by_cat[c]))))

    drafts = []
    for count, i in enumerate(picks, 1):
        meta, chunk = data["metadatas"][i], data["documents"][i]
        try:
            qa = await draft_qa(chunk)
            if not qa.get("question"):
                raise ValueError("no question returned")
            drafts.append({
                "id": f"d{count:02d}",
                "type": "corpus",
                "question": str(qa["question"])[:300],
                "expect_category_any": [meta.get("category", "")],
                "expect_any_terms": [str(t)[:40] for t in qa.get("key_terms", [])][:5],
                "draft_answer": str(qa.get("answer", ""))[:400],
                "source_file": meta.get("file", ""),
                "source_page": meta.get("page", 0),
                "notes": "DRAFT - curate before use",
            })
            print(f"[{count}/{len(picks)}] {meta.get('category','?')}: {qa['question'][:70]}")
        except Exception as exc:  # noqa: BLE001
            print(f"[{count}/{len(picks)}] draft failed: {exc}")

    with args.out.open("w", encoding="utf-8") as f:
        for d in drafts:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"\n{len(drafts)} drafts -> {args.out}")
    print("Now CURATE them (delete/fix), then append keepers to evals/golden_qa.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
