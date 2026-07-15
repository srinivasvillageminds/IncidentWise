"""Drill engine v2 - structured, self-critiqued, expert-validated scenarios.

Upgrade over scenario.py (kept for the simple streaming path):
1. STRUCTURED spec (JSON), not freeform markdown - consistent sections,
   role-targeted questions, machine-checkable grounding references.
2. GROUNDING on two rails: retrieved corpus excerpts (as before) PLUS
   real-incident anchors from incidents.json (year/type/root-cause facts),
   which keeps hypotheticals statistically sane, not Hollywood.
3. GENERATE -> CRITIQUE -> REFINE: the model judges its own draft against
   the training-value rubric and revises once. Cheap, measurable uplift.
4. LIBRARY with validation workflow: every drill lands in drill_library/
   as status=draft; an expert marks it validated/rejected with notes.
   Only validated drills should ever reach a safety meeting.

The critique loop raises the floor; it does not replace the expert. That
stays non-negotiable and is encoded in the status workflow itself.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

import retrieval
from analytics import _load as load_incidents  # same table, read-only
from config import APP_DIR
from rag import ollama_json

DRILL_DIR = APP_DIR / "drill_library"
STATUSES = ("draft", "validated", "rejected")
ROLES = ("operator", "shift_incharge", "engineer")

SPEC_PROMPT = """You design a HYPOTHETICAL process-safety incident drill for a monthly safety meeting at an Indian plant.

Real corpus excerpts (cite as integers in "grounding" arrays):
{seeds}

Real incident anchors from the structured incident database (patterns to stay consistent with - do NOT copy details verbatim):
{anchors}

Task: theme "{theme}", unit type "{unit_type}", audience level "{difficulty}".

Hard rules:
- Fictional plant/unit names; never real company names, dates, or casualty figures.
- Every failure mechanism must trace to an excerpt or anchor via grounding numbers.
- Indian plant practice: permit-to-work, tool-box talk, shift handover, monsoon where relevant.
- Personnel must reflect realistic Indian plant staffing for relatability: night-shift
  operations crews (operators, shift in-charge) are typically all-male; include female
  characters only in roles where they are realistically common (day-shift process
  engineers, HSE officers, lab analysts). Relatability builds training credibility.
- INTERNAL CONSISTENCY: setting and narrative must agree on time of day, season,
  weather, and unit state THROUGHOUT. Re-read your narrative against your setting
  before answering - a "monsoon night" setting with a "hot summer evening" narrative
  makes the drill unusable.
- REALISTIC VIOLATIONS: when a barrier is skipped, give a believable organizational
  reason - schedule pressure, complacency after many uneventful jobs, handover gap,
  manpower shortage. NEVER an illogical excuse (weather does not justify skipping
  gas testing). If you cannot justify a violation believably, pick a different one.
- ROLE ACCURACY: confined space entry happens only under a CSE permit with authorized
  entrants, an attendant, and gas testing - operators do not casually enter vessels
  for "routine checks". Match every task to the role that actually performs it in
  an Indian plant.
- Questions escalate: detection -> immediate response -> barriers -> systemic causes.

Return JSON exactly:
{{"title": "...",
 "setting": {{"unit": "...", "time_of_day": "...", "operating_mode": "...", "conditions": "..."}},
 "narrative": "280-350 words, chronological, ends just after escalation begins",
 "initiating_event": "one line",
 "barriers_failed": [{{"barrier": "...", "how_it_failed": "...", "grounding": [1]}}],
 "escalation_injects": ["twist 1", "twist 2"],
 "questions": [{{"role": "operator|shift_incharge|engineer", "question": "...",
                 "facilitator_note": "...", "grounding": [1]}}],
 "grounding_map": [{{"n": 1, "borrowed": "what was taken from this excerpt"}}],
 "validation_checklist": ["5 specific checks for the reviewing expert"]}}"""

CRITIQUE_PROMPT = """You are a hard-to-please process-safety training reviewer. Judge this drill spec:

{spec}

Against the source excerpts:
{seeds}

Check HARD for internal contradictions: does the narrative match the setting on
time of day, season, weather, unit state? Are skipped barriers justified by a
believable organizational reason (never nonsense like "skipped gas test due to
weather")? Does every task match the role that actually performs it (confined
space entry = authorized entrants + attendant, never casual)?

Score 1-5 each and list concrete issues. Return JSON:
{{"scores": {{"technical_feasibility": n, "internal_consistency": n,
  "groundedness": n, "no_fabrication": n,
  "provocation": n, "local_relatability": n, "question_quality": n}},
 "issues": ["EVERY contradiction and implausibility, specifically - empty only if truly none"],
 "worst_aspect": "one line"}}"""

REFINE_PROMPT = """Revise this drill spec to fix the reviewer's issues. Keep the same JSON schema, keep what works, change only what the issues demand. Do not add new failure mechanisms without grounding.

Spec:
{spec}

Reviewer issues:
{issues}

Excerpts (grounding numbers must stay consistent):
{seeds}

Return the full corrected JSON spec."""


# ---------------------------------------------------------------------------
# grounding inputs
# ---------------------------------------------------------------------------

def _seed_block(seeds: list[dict]) -> str:
    return "\n\n".join(
        f"[{h['n']}] {h['category']} - \"{h['title']}\", p.{h['page']}\n{h['text'][:700]}"
        for h in seeds) or "(none)"


def incident_anchors(theme: str, limit: int = 5) -> list[dict]:
    """Keyword-matched real-incident facts to keep the hypothetical sane."""
    records = load_incidents()
    words = {w for w in re.findall(r"[a-z]{4,}", theme.lower())}
    scored = []
    for r in records:
        hay = " ".join(str(r.get(k) or "") for k in
                       ("incident_type", "summary", "root_cause", "equipment",
                        "facility_type")).lower()
        score = sum(1 for w in words if w in hay)
        if score:
            scored.append((score, r))
    scored.sort(key=lambda t: -t[0])
    return [{"year": r.get("year"), "type": r.get("incident_type"),
             "facility": r.get("facility_type"), "root_cause": r.get("root_cause"),
             "summary": r.get("summary")} for _, r in scored[:limit]]


def _anchor_block(anchors: list[dict]) -> str:
    if not anchors:
        return "(no structured anchors available - rely on excerpts only)"
    return "\n".join(f"- {a['year'] or '?'} | {a['type'] or '?'} | "
                     f"{a['facility'] or '?'} | cause: {a['root_cause'] or '?'} | "
                     f"{a['summary'] or ''}" for a in anchors)


# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------

async def create_drill(theme: str, unit_type: str, difficulty: str = "intermediate",
                       refine: bool = True, category: str | None = None,
                       model: str | None = None, backend: str | None = None) -> dict:
    """Full pipeline. Returns {spec, critique, seeds, anchors, meta}."""
    theme = (theme or "process incident").strip()[:200]
    unit_type = (unit_type or "process plant").strip()[:100]

    result = await retrieval.retrieve(f"{theme} incident {unit_type} causes failure",
                                      mode="good", k=5, category=category)
    seeds = result["hits"]
    seeds_txt = _seed_block(seeds)
    anchors = incident_anchors(theme)

    spec = await ollama_json(
        SPEC_PROMPT.format(seeds=seeds_txt, anchors=_anchor_block(anchors),
                           theme=theme, unit_type=unit_type, difficulty=difficulty),
        read_timeout=300.0, temperature=0.7, model=model, backend=backend)
    if not spec.get("title"):
        raise RuntimeError("drill generation returned no usable spec")

    critique = await ollama_json(
        CRITIQUE_PROMPT.format(spec=json.dumps(spec, ensure_ascii=False)[:6000],
                               seeds=seeds_txt[:4000]),
        read_timeout=240.0, model=model, backend=backend)

    refined = False
    issues = [str(i) for i in critique.get("issues", []) if str(i).strip()]
    if refine and issues:
        better = await ollama_json(
            REFINE_PROMPT.format(spec=json.dumps(spec, ensure_ascii=False)[:6000],
                                 issues="\n".join(f"- {i}" for i in issues[:8]),
                                 seeds=seeds_txt[:4000]),
            read_timeout=300.0, temperature=0.4, model=model, backend=backend)
        if better.get("title"):
            spec, refined = better, True

    return {"spec": spec, "critique": critique, "refined": refined,
            "seeds": seeds, "anchors": anchors,
            "meta": {"theme": theme, "unit_type": unit_type,
                     "difficulty": difficulty, "pipeline": result["pipeline"]}}


def render_markdown(drill: dict) -> str:
    """Meeting-ready document from a spec."""
    s = drill["spec"]
    lines = [f"## Scenario: {s.get('title', '')}", ""]
    st = s.get("setting", {})
    lines.append(f"**Setting** — {st.get('unit', '')} · {st.get('time_of_day', '')} · "
                 f"{st.get('operating_mode', '')} · {st.get('conditions', '')}")
    lines += ["", s.get("narrative", ""), "",
              f"**Initiating event:** {s.get('initiating_event', '')}", "",
              "**Barriers that failed**", ""]
    for b in s.get("barriers_failed", []):
        g = "".join(f"[{n}]" for n in b.get("grounding", []))
        lines.append(f"- {b.get('barrier', '')}: {b.get('how_it_failed', '')} {g}")
    lines += ["", "**Escalation injects (facilitator only)**", ""]
    for i, inj in enumerate(s.get("escalation_injects", []), 1):
        lines.append(f"{i}. {inj}")
    lines += ["", "## Discussion questions", ""]
    for i, q in enumerate(s.get("questions", []), 1):
        g = "".join(f"[{n}]" for n in q.get("grounding", []))
        lines.append(f"{i}. **({q.get('role', '')})** {q.get('question', '')} {g}")
        lines.append(f"   *Facilitator: {q.get('facilitator_note', '')}*")
    lines += ["", "## Grounding", ""]
    for g in s.get("grounding_map", []):
        lines.append(f"- [{g.get('n')}] {g.get('borrowed', '')}")
    lines += ["", "## Expert validation checklist", ""]
    for c in s.get("validation_checklist", []):
        lines.append(f"- [ ] {c}")
    lines += ["", "*DRAFT — requires validation by a competent safety professional "
                  "before use in any meeting.*"]
    return "\n".join(lines)


INSTRUCT_REFINE_PROMPT = """Revise this safety-drill spec exactly as the reviewer instructs. Keep the same JSON schema. Keep all grounding numbers and everything not touched by the instruction stable. Do not add failure mechanisms without grounding. Personnel realism rules still apply (night-shift crews typically all-male).

Reviewer instruction: {instruction}

Current spec:
{spec}

Return the full revised JSON spec."""


async def refine_with_instruction(spec: dict, instruction: str,
                                  model: str | None = None,
                                  backend: str | None = None) -> dict:
    """History-aware follow-up: revise an existing spec per a user instruction
    instead of regenerating from scratch."""
    out = await ollama_json(
        INSTRUCT_REFINE_PROMPT.format(instruction=instruction[:500],
                                      spec=json.dumps(spec, ensure_ascii=False)[:7000]),
        read_timeout=300.0, temperature=0.4, model=model, backend=backend)
    if not out.get("title"):
        raise RuntimeError("refine returned no usable spec")
    return out


# ---------------------------------------------------------------------------
# library + validation workflow
# ---------------------------------------------------------------------------

def save_drill(drill: dict) -> str:
    DRILL_DIR.mkdir(exist_ok=True)
    drill_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    record = {"id": drill_id, "status": "draft", "reviewer_notes": "",
              "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              **drill, "markdown": render_markdown(drill)}
    (DRILL_DIR / f"{drill_id}.json").write_text(
        json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    return drill_id


def list_drills(status: str | None = None) -> list[dict]:
    out = []
    if DRILL_DIR.exists():
        for p in sorted(DRILL_DIR.glob("*.json"), reverse=True):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if status and d.get("status") != status:
                continue
            out.append({"id": d["id"], "status": d["status"],
                        "title": d.get("spec", {}).get("title", ""),
                        "theme": d.get("meta", {}).get("theme", ""),
                        "created_at": d.get("created_at", ""),
                        "reviewer_notes": d.get("reviewer_notes", "")})
    return out


def get_drill(drill_id: str) -> dict | None:
    p = DRILL_DIR / f"{Path(drill_id).name}.json"   # sanitize path
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def set_status(drill_id: str, status: str, notes: str = "") -> bool:
    if status not in STATUSES:
        return False
    d = get_drill(drill_id)
    if d is None:
        return False
    d["status"] = status
    d["reviewer_notes"] = notes[:2000]
    d["reviewed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (DRILL_DIR / f"{Path(drill_id).name}.json").write_text(
        json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    return True
