"""SIMOPS layer - hazards from COMBINATIONS of jobs, not single permits.

The sharpest incidents rarely come from one badly-run job; they come from
two individually-fine jobs interacting: hot work at E-104 while the steam
trap 6 metres away is weeping VR, a confined-space entry downwind of a
line break. This module:

1. Keeps a rolling JOBS LOG (permits become job records - opt-in when a
   permit is analyzed, or logged directly).
2. Scans a time window for combinations: jobs whose validity overlaps AND
   whose equipment is identical, same-unit, or within a proximity radius
   in plant_model.json.
3. For a risky combination, generates a plant-specific "what could have
   happened" hypothetical - real tags, real (dummy for now) jobs, corpus-
   grounded mechanisms - and files it in the drill library as status=draft
   for expert validation, same workflow as every other drill.

Honesty note: with dummy permits this is a demonstration of the mechanism.
Its real value switches on when a site's actual permit history flows in -
which is exactly the integration boundary the permit copilot targets.
"""
from __future__ import annotations

import json
import re
import time
import uuid

import drills
import retrieval
from config import APP_DIR
from permits import distance_hint, load_plant
from rag import ollama_json

JOBS_PATH = APP_DIR / "jobs_log.json"

SIMOPS_PROMPT = """You design a HYPOTHETICAL "what could have happened" scenario for an Indian plant safety meeting, based on TWO REAL JOBS that ran in overlapping windows near each other. The point: individually safe jobs, dangerous in combination.

JOB A: {job_a}
JOB B: {job_b}
SPATIAL RELATION: {relation}
NEARBY HAZARDS (plant model, authoritative): {hazards}

Real corpus excerpts (cite as integers in "grounding"):
{seeds}

Real incident anchors (patterns to stay consistent with):
{anchors}

Hard rules:
- Use the ACTUAL equipment tags and job descriptions given; invent nothing spatial.
- The failure mechanism must be an INTERACTION between the two jobs, traceable to excerpts/anchors.
- If jobs reference an SOP by name, cite that SOP name for procedural steps -
  NEVER invent SOP clause numbers or contents beyond the name.
- Personnel must reflect realistic Indian plant staffing: night-shift operations
  crews are typically all-male; female characters only in realistically common
  roles (day-shift engineers, HSE, lab).
- Fictional escalation only; no real company names, dates, casualty figures.
- End the narrative just as the interaction becomes dangerous - the discussion completes it.

Return JSON exactly:
{{"title": "...",
 "setting": {{"unit": "...", "time_of_day": "...", "operating_mode": "...", "conditions": "..."}},
 "narrative": "250-350 words, both jobs visible, chronological",
 "initiating_event": "the interaction moment, one line",
 "barriers_failed": [{{"barrier": "...", "how_it_failed": "...", "grounding": [1]}}],
 "escalation_injects": ["twist 1", "twist 2"],
 "questions": [{{"role": "operator|shift_incharge|engineer", "question": "...",
                 "facilitator_note": "...", "grounding": [1]}}],
 "grounding_map": [{{"n": 1, "borrowed": "..."}}],
 "validation_checklist": ["5 checks incl.: does the permit office actually allow this combination today?"]}}"""


# ---------------------------------------------------------------------------
# jobs log
# ---------------------------------------------------------------------------

def _load_jobs() -> list[dict]:
    try:
        return json.loads(JOBS_PATH.read_text(encoding="utf-8")) if JOBS_PATH.exists() else []
    except Exception:  # noqa: BLE001
        return []


def _save_jobs(jobs: list[dict]) -> None:
    JOBS_PATH.write_text(json.dumps(jobs, indent=1, ensure_ascii=False), encoding="utf-8")


def log_job(permit: dict) -> dict:
    jobs = _load_jobs()
    job = {
        "job_id": "J-" + uuid.uuid4().hex[:8],
        "logged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "permit_no": permit.get("permit_no", ""),
        "permit_type": permit.get("permit_type", ""),
        "unit": permit.get("unit", ""),
        "equipment_tag": permit.get("equipment_tag", ""),
        "job_description": permit.get("job_description", ""),
        "validity": permit.get("validity", {}),
        "status": (permit.get("status") or "open").lower(),   # open | closed
        "sop_ref": permit.get("sop_ref", ""),                 # SOP name only
        "job_kind": permit.get("job_kind", ""),               # e.g. blinding, probe replacement
    }
    jobs.append(job)
    _save_jobs(jobs[-500:])  # rolling window
    return job


def list_jobs(days: int = 30) -> list[dict]:
    cutoff = time.time() - days * 86400
    out = []
    for j in _load_jobs():
        try:
            t = time.mktime(time.strptime(j["logged_at"][:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:  # noqa: BLE001
            t = time.time()
        if t >= cutoff:
            out.append(j)
    return out


# ---------------------------------------------------------------------------
# combination detection
# ---------------------------------------------------------------------------

def _windows_overlap(a: dict, b: dict) -> bool:
    """Validity overlap; jobs with missing windows are assumed concurrent
    (conservative: better a false combination than a missed one)."""
    try:
        a1, a2 = a["validity"]["from"], a["validity"]["to"]
        b1, b2 = b["validity"]["from"], b["validity"]["to"]
        return a1 <= b2 and b1 <= a2
    except Exception:  # noqa: BLE001
        return True


def find_combinations(days: int = 30, radius_m: float = 40.0) -> list[dict]:
    jobs = list_jobs(days)
    plant = load_plant()
    combos = []
    for i in range(len(jobs)):
        for j in range(i + 1, len(jobs)):
            a, b = jobs[i], jobs[j]
            if not _windows_overlap(a, b):
                continue
            relation, dist = distance_hint(plant, a.get("equipment_tag", ""),
                                           b.get("equipment_tag", ""), radius_m)
            if relation == "unrelated":
                continue
            combos.append({
                "jobs": [a, b], "relation": relation, "distance_m": dist,
                "why_flagged": f"overlapping windows + {relation}"
                               + (f" ({dist} m)" if dist is not None else ""),
            })
    combos.sort(key=lambda c: (c["distance_m"] is None, c["distance_m"] or 0))
    return combos


# ---------------------------------------------------------------------------
# scenario generation from a combination
# ---------------------------------------------------------------------------

def _hazards_near(plant: dict, tags: list[str]) -> str:
    from permits import neighbors  # local import to avoid cycle at module load
    seen, lines = set(), []
    for tag in tags:
        for p in neighbors(plant, tag, radius_m=30.0):
            key = p["tag"]
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- {p['tag']} ({p['type']}) at {p['distance_m']} m of {tag}; "
                         f"hazards: {', '.join(p['hazards']) or 'none listed'}")
    return "\n".join(lines) or "(no plant model hazards listed)"


async def combination_scenario(combo: dict, model: str | None = None) -> dict:
    """One SIMOPS what-if drill from a flagged combination -> drill library shape."""
    a, b = combo["jobs"][0], combo["jobs"][1]
    plant = load_plant()
    theme = f"{a.get('permit_type', '')} + {b.get('permit_type', '')} interaction"

    query = (f"{a.get('permit_type', '')} {b.get('permit_type', '')} simultaneous "
             f"{a.get('equipment_tag', '')} {b.get('equipment_tag', '')} incident")
    result = await retrieval.retrieve(query, mode="good", k=5)
    seeds = result["hits"]
    seeds_txt = drills._seed_block(seeds)  # noqa: SLF001 - deliberate reuse
    anchors = drills.incident_anchors(theme + " " + a.get("job_description", "")
                                      + " " + b.get("job_description", ""))

    spec = await ollama_json(
        SIMOPS_PROMPT.format(
            job_a=json.dumps(a, ensure_ascii=False)[:500],
            job_b=json.dumps(b, ensure_ascii=False)[:500],
            relation=combo.get("why_flagged", ""),
            hazards=_hazards_near(plant, [a.get("equipment_tag", ""),
                                          b.get("equipment_tag", "")]),
            seeds=seeds_txt[:4500], anchors=drills._anchor_block(anchors)),  # noqa: SLF001
        read_timeout=300.0, temperature=0.7, model=model)
    if not spec.get("title"):
        raise RuntimeError("SIMOPS generation returned no usable spec")

    return {"spec": spec, "critique": {}, "refined": False,
            "seeds": seeds, "anchors": anchors,
            "meta": {"kind": "simops", "theme": theme,
                     "unit_type": a.get("unit", ""), "difficulty": "intermediate",
                     "jobs": [a.get("job_id"), b.get("job_id")],
                     "relation": combo.get("why_flagged", ""),
                     "pipeline": result["pipeline"]}}
