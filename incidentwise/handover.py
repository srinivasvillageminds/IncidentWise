"""Pre-handover vertical: one incident-informed check across ALL open permits.

At shift handover, the incoming shift inherits every open job at once - and
job interactions are exactly what a per-permit checklist misses. This looks
at all OPEN permits in the jobs log, groups equipment by plant-model
proximity, retrieves related incident wisdom, and produces a handover brief:
per-area checks split by job class (hot work / cold work / confined space),
plus interaction watch-outs. Deterministic evidence rides along.
"""
from __future__ import annotations

import json

import retrieval
import simops
from permits import load_plant, neighbors
from rag import ollama_json

HANDOVER_PROMPT = """You prepare the incoming shift in-charge for handover at an Indian plant. Below are ALL open permits, plant-model proximity facts, and excerpts from real incident reports (cite as [n]).

OPEN PERMITS:
{jobs}

PROXIMITY (authoritative; distances in metres):
{proximity}

REAL INCIDENT EXCERPTS:
{seeds}

Produce a pre-handover brief. Every check must be specific and verifiable on a walkdown (tags, valves, blinds, barricades, gas tests) - never "ensure safety". Use SOP names only if a permit references one; never invent SOP contents. Return JSON:
{{"areas": [{{"tags": ["equipment in this cluster"],
   "open_permits": ["permit numbers"],
   "checks": {{"hot_work": ["..."], "cold_work": ["..."], "confined_space": ["..."]}},
   "interaction_watchouts": ["hazards from these jobs coexisting, with [n] where applicable"]}}],
 "general": ["3-5 plant-wide handover checks for this permit mix"]}}
Omit empty job-class lists. Max 5 checks per list."""


async def pre_handover(radius_m: float = 30.0, days: int = 30,
                       model: str | None = None,
                       backend: str | None = None) -> dict:
    jobs = [j for j in simops.list_jobs(days)
            if (j.get("status") or "open").lower() == "open"]
    if not jobs:
        return {"jobs": 0, "review": {"areas": [], "general": [
            "No open permits in the jobs log - log permits (or run "
            "seed_permits.py for demo data) first."]}, "sources": []}

    plant = load_plant()
    prox_lines, seen = [], set()
    for j in jobs:
        tag = j.get("equipment_tag", "")
        for p in neighbors(plant, tag, radius_m):
            key = (tag, p["tag"])
            if key in seen:
                continue
            seen.add(key)
            prox_lines.append(f"- {p['tag']} ({p['type']}) at {p['distance_m']} m of "
                              f"{tag}; hazards: {', '.join(p['hazards']) or 'none listed'}")

    jobs_txt = "\n".join(
        f"- {j.get('permit_no','?')} | {j.get('permit_type','?')} | "
        f"{j.get('equipment_tag','?')} | {j.get('job_description','')[:90]}"
        + (f" | SOP: {j['sop_ref']}" if j.get("sop_ref") else "")
        for j in jobs)

    types = " ".join(sorted({j.get("permit_type", "") for j in jobs}))
    result = await retrieval.retrieve(
        f"shift handover simultaneous {types} incident causes", mode="good", k=5)
    seeds = result["hits"]
    seeds_txt = "\n\n".join(
        f"[{h['n']}] {h['category']} - \"{h['title']}\", p.{h['page']}\n{h['text'][:500]}"
        for h in seeds) or "(none)"

    review = await ollama_json(
        HANDOVER_PROMPT.format(jobs=jobs_txt[:2500],
                               proximity="\n".join(prox_lines)[:2000] or "(no plant model)",
                               seeds=seeds_txt[:4500]),
        read_timeout=300.0, temperature=0.3, model=model, backend=backend)

    return {"jobs": len(jobs), "review": review,
            "sources": [{"n": h["n"], "title": h["title"], "category": h["category"],
                         "doc_type": h.get("doc_type", ""), "page": h["page"],
                         "pages_total": h["pages_total"], "file": h["file"],
                         "doc_url": h["doc_url"], "source_url": h["source_url"],
                         "distance": h["distance"], "snippet": h["text"][:280]}
                        for h in seeds],
            "disclaimer": "Decision support only - the outgoing and incoming "
                          "shift in-charge remain responsible for the physical walkdown."}
