"""Rebuild REPORT.md / PLAYGROUND.md from a console log.

For when a run completed but the session died before the files were saved
(or were never copied off Colab). The log lines carry every number we need.

  1. Save the console output to a text file, e.g. evals/run_round2.log
  2. python evals/parse_log.py evals/run_round2.log --tag round2

Writes REPORT_<tag>.md and PLAYGROUND_<tag>.md.

Caveat, stated honestly: the console log does NOT contain the per-defect
quotes/codes from the drill suite (a printer bug meant they were never
echoed). Drill scores, verdicts and latencies ARE recoverable. If you need
the defect texts, they only exist in drillbench_report.json.
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RE_RETR = re.compile(r"retrieval\[(\w+)\]:\s*hit@\d+=([\d.]+)\s+latency=([\d.]+)s")
RE_ROUTER = re.compile(r"router accuracy:\s*([\d.]+)")
RE_SUMMARY = re.compile(
    r"^(\S+:\S+)\s+refusal=([\d.]+)\s+grounding=([\d.]+)"
    r"(?:\s+\(pooled\s+([\d.]+),\s*(\d+)/(\d+)\s+claims\))?", re.M)
RE_BENCH = re.compile(
    r"\[\s*\d+\]\s+(\S+)\s+(\w+)\s+(\w+)\s+fetch (PASS|MISS) . answer (PASS|MISS) . "
    r"sanity ([\d.]+) . ([\d.]+)s")
RE_DRILL = re.compile(
    r"\[\s*\d+\]\s+(\S+)\s+(d_\w+)\s+avg ([\d.]+|None)/5 . (\d+) \w+\(?\w*\)?s? . (\S+) . ([\d.]+)s")
RE_SUITE = re.compile(r"^(ASK|GUARD|DRILL) suite", re.M)


def parse(text: str) -> dict:
    out: dict = {"retrieval": {}, "router": None, "answers": {},
                 "ask": [], "guard": [], "drill": []}

    for m in RE_RETR.finditer(text):
        out["retrieval"][m.group(1)] = {"hit": float(m.group(2)),
                                        "latency": float(m.group(3))}
    m = RE_ROUTER.search(text)
    if m:
        out["router"] = float(m.group(1))

    for m in RE_SUMMARY.finditer(text):
        out["answers"][m.group(1)] = {
            "refusal": float(m.group(2)), "grounding": float(m.group(3)),
            "pooled": float(m.group(4)) if m.group(4) else None,
            "supported": int(m.group(5)) if m.group(5) else None,
            "claims": int(m.group(6)) if m.group(6) else None}

    # Which suite is each bench row in? Track section boundaries.
    sections = [(m.start(), m.group(1)) for m in RE_SUITE.finditer(text)]

    def section_of(pos: int) -> str:
        cur = "ASK"
        for start, name in sections:
            if start <= pos:
                cur = name
            else:
                break
        return cur

    for m in RE_BENCH.finditer(text):
        row = {"model": m.group(1), "q_id": m.group(2), "kind": m.group(3),
               "fetch": m.group(4) == "PASS", "answer": m.group(5) == "PASS",
               "sanity": float(m.group(6)), "latency": float(m.group(7))}
        sec = section_of(m.start())
        out["guard" if sec == "GUARD" else "ask"].append(row)

    for m in RE_DRILL.finditer(text):
        avg = m.group(3)
        out["drill"].append({
            "model": m.group(1), "q_id": m.group(2),
            "avg": None if avg == "None" else float(avg),
            "defects": int(m.group(4)), "verdict": m.group(5),
            "latency": float(m.group(6))})
    return out


def md_report(d: dict, tag: str) -> str:
    L = [f"# Eval report ({tag})", "",
         "Reconstructed from the run console log. Judge: gpt-5.5 "
         "(outside the tested set for all local models).", "",
         "## Retrieval (model-independent — no LLM involved)", "",
         "| mode | hit@6 | avg latency |", "|---|---|---|"]
    for mode, v in d["retrieval"].items():
        L.append(f"| {mode} | {v['hit']} | {v['latency']}s |")
    L += ["", "*Ceiling effect: the 16-question golden set is too easy to "
              "discriminate retrieval modes. Harder questions needed before "
              "these numbers can adjudicate anything.*", "",
          f"## Router accuracy: {d['router']}", "",
          "## Answers — refusal & grounding", "",
          "| model | refusal | grounding (mean) | grounding (pooled) | claims supported |",
          "|---|---|---|---|---|"]
    for m, v in d["answers"].items():
        L.append(f"| {m} | **{v['refusal']}** | **{v['grounding']}** | "
                 f"{v['pooled'] if v['pooled'] is not None else '—'} | "
                 f"{v['supported']}/{v['claims']} |"
                 if v["claims"] else
                 f"| {m} | **{v['refusal']}** | **{v['grounding']}** | — | — |")
    L += ["", "*Grounding is a claim-level supported fraction, not a binary "
              "check. A binary metric collapses to 0 under a strict judge — it "
              "measures the judge, not the model.*", ""]
    return "\n".join(L)


def agg_bench(rows: list[dict], group_by_guard: bool = False) -> dict:
    tot: dict = defaultdict(lambda: {"f": 0, "a": 0, "n": 0, "s": [], "lat": [],
                                     "kind": defaultdict(lambda: {"ok": 0, "n": 0})})
    for r in rows:
        key = r["model"]
        t = tot[key]
        t["f"] += int(r["fetch"]); t["a"] += int(r["answer"]); t["n"] += 1
        t["s"].append(r["sanity"]); t["lat"].append(r["latency"])
        k = t["kind"][r["kind"]]
        k["ok"] += int(r["fetch"] and r["answer"]); k["n"] += 1
    return tot


def md_bench(tot: dict, title: str) -> list[str]:
    L = [f"## {title}", "",
         "| model | fetch | answer | sanity /5 | avg latency |", "|---|---|---|---|---|"]
    for m, v in tot.items():
        L.append(f"| {m} | {round(100*v['f']/v['n'])}% | {round(100*v['a']/v['n'])}% | "
                 f"{round(statistics.mean(v['s']),2)} | {round(statistics.mean(v['lat']),1)}s |")
    L.append("")
    L += ["**Pass rate by question kind**", "",
          "| model | " + " | ".join(sorted({k for v in tot.values() for k in v["kind"]}))
          + " |"]
    kinds = sorted({k for v in tot.values() for k in v["kind"]})
    L.append("|---" * (len(kinds) + 1) + "|")
    for m, v in tot.items():
        cells = []
        for k in kinds:
            d = v["kind"].get(k)
            cells.append(f"{round(100*d['ok']/d['n'])}%" if d and d["n"] else "—")
        L.append(f"| {m} | " + " | ".join(cells) + " |")
    L.append("")
    return L


def md_drill(rows: list[dict]) -> list[str]:
    tot: dict = defaultdict(lambda: {"scores": [], "verdicts": defaultdict(int),
                                     "failed": 0, "lat": []})
    for r in rows:
        t = tot[r["model"]]
        t["lat"].append(r["latency"])
        if r["avg"] is None or r["avg"] == 0 or r["verdict"] == "?":
            t["failed"] += 1           # judge failed / unscorable output
        else:
            t["scores"].append(r["avg"])
            t["verdicts"][r["verdict"]] += 1
    L = ["## Drill suite", "",
         "| model | overall /5 | scored | failed | verdicts | avg latency |",
         "|---|---|---|---|---|---|"]
    for m, v in sorted(tot.items(),
                       key=lambda kv: statistics.mean(kv[1]["scores"]) if kv[1]["scores"] else 0):
        overall = round(statistics.mean(v["scores"]), 2) if v["scores"] else "—"
        L.append(f"| {m} | {overall} | {len(v['scores'])} | {v['failed']} | "
                 + ", ".join(f"{k}:{n}" for k, n in v["verdicts"].items())
                 + f" | {round(statistics.mean(v['lat']))}s |")
    L += ["", "*`failed` = the model produced no scorable structured spec, or the "
              "judge could not score it. That is a capability failure, not a low "
              "score, and it is excluded from the average.*",
          "", "*Defect counts and quotes are NOT in this reconstruction — a printer "
              "bug meant they were never echoed to the console. They exist only in "
              "`drillbench_report.json`.*", ""]
    return L


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild reports from a console log")
    ap.add_argument("log", type=Path)
    ap.add_argument("--tag", default="recovered")
    args = ap.parse_args()

    text = args.log.read_text(encoding="utf-8", errors="replace")
    d = parse(text)

    if d["answers"] or d["retrieval"]:
        p = EVALS_DIR / f"REPORT_{args.tag}.md"
        p.write_text(md_report(d, args.tag), encoding="utf-8")
        print(f"REPORT  -> {p}  ({len(d['answers'])} models)")

    md = [f"# Playground report ({args.tag})", "",
          "Reconstructed from the run console log. Judge: gpt-5.5.", ""]
    if d["ask"]:
        md += md_bench(agg_bench(d["ask"]), "Ask suite (guard=l1)")
    if d["guard"]:
        md += md_bench(agg_bench(d["guard"]), "Guard suite (levels off/l1/l2 pooled)")
    if d["drill"]:
        md += md_drill(d["drill"])
    if len(md) > 4:
        p = EVALS_DIR / f"PLAYGROUND_{args.tag}.md"
        p.write_text("\n".join(md), encoding="utf-8")
        print(f"PLAYGROUND -> {p}  "
              f"(ask {len(d['ask'])} rows, guard {len(d['guard'])}, drill {len(d['drill'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
