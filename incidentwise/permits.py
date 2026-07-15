"""Permit vertical - the groundwork for the permit copilot.

What exists now (works with dummy data, no site integration needed):
- A realistic Indian permit-to-work JSON schema + dummy-permit generator,
  so the analyzer can be exercised and demoed without any customer data.
- A plant topology interface (plant_model.json): units, tagged equipment,
  hazards, adjacency with distances. Hand-editable today; the future
  GA-drawing/P&ID extraction pipeline TARGETS THIS FORMAT, so everything
  built on it survives that upgrade.
- The analyzer: permit -> related corpus incidents + structured incident
  anchors + proximity hazards -> role-specific, incident-informed checklist
  questions beyond the static permit checklist. This is the product thesis
  in one function.

What deliberately does NOT exist yet: P&ID parsing (hard, needs real
drawings), SAP/PTW integration (needs a pilot site). The schema boundary
here is where those plug in.
"""
from __future__ import annotations

import json
import random
import re
import time

import retrieval
from analytics import _load as load_incidents
from config import APP_DIR
from rag import ollama_json

PLANT_MODEL_PATH = APP_DIR / "plant_model.json"
PLANT_EXAMPLE_PATH = APP_DIR / "plant_model.json.example"

PERMIT_TYPES = ("hot_work", "confined_space", "work_at_height", "electrical",
                "excavation", "line_breaking", "cold_work")

# The permit schema (documentation-by-example; also used by the dummy generator)
PERMIT_SCHEMA_EXAMPLE = {
    "permit_no": "HW-2026-0412",
    "permit_type": "hot_work",
    "unit": "Unit-1 (CDU)",
    "equipment_tag": "E-104",
    "job_description": "Weld repair of shell nozzle on crude preheat exchanger",
    "contractor": "M/s Example Engineering",
    "validity": {"from": "2026-07-06T08:00", "to": "2026-07-06T18:00"},
    "isolations": ["Spade at inlet flange", "Electrical LOTO on pump P-101"],
    "gas_test": {"required": True, "LEL": "0%", "O2": "20.9%", "H2S": "0 ppm"},
    "ppe": ["fire retardant suit", "face shield"],
    "static_checklist_done": True,
}


# ---------------------------------------------------------------------------
# plant model
# ---------------------------------------------------------------------------

def load_plant() -> dict:
    for path in (PLANT_MODEL_PATH, PLANT_EXAMPLE_PATH):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
    return {"plant": "unknown", "units": [], "equipment": [], "adjacency": []}


def equipment_by_tag(plant: dict, tag: str) -> dict | None:
    tag_l = (tag or "").strip().lower()
    for e in plant.get("equipment", []):
        if e.get("tag", "").lower() == tag_l:
            return e
    return None


def neighbors(plant: dict, tag: str, radius_m: float = 30.0) -> list[dict]:
    """Equipment within radius of a tag, with hazards - the 'proximity' part."""
    tag_l = (tag or "").strip().lower()
    out = []
    for edge in plant.get("adjacency", []):
        a, b = edge.get("a", ""), edge.get("b", "")
        dist = float(edge.get("distance_m", 9999))
        other = b if a.lower() == tag_l else a if b.lower() == tag_l else None
        if other and dist <= radius_m:
            eq = equipment_by_tag(plant, other) or {"tag": other}
            out.append({"tag": eq.get("tag", other), "type": eq.get("type", ""),
                        "service": eq.get("service", ""),
                        "hazards": eq.get("hazards", []), "distance_m": dist})
    return sorted(out, key=lambda x: x["distance_m"])


def distance_hint(plant: dict, tag_a: str, tag_b: str,
                  radius_m: float = 40.0) -> tuple[str, float | None]:
    """Spatial relation between two tags for SIMOPS screening:
    ('same_equipment' | 'adjacent' | 'same_unit' | 'unrelated', distance_m)."""
    ta, tb = (tag_a or "").strip().lower(), (tag_b or "").strip().lower()
    if ta and ta == tb:
        return "same_equipment", 0.0
    for e in plant.get("adjacency", []):
        if {e.get("a", "").lower(), e.get("b", "").lower()} == {ta, tb}:
            d = float(e.get("distance_m", 9999))
            return ("adjacent", d) if d <= radius_m else ("unrelated", d)
    ea, eb = equipment_by_tag(plant, ta), equipment_by_tag(plant, tb)
    if ea and eb and ea.get("unit") and ea.get("unit") == eb.get("unit"):
        return "same_unit", None
    return "unrelated", None


# ---------------------------------------------------------------------------
# dummy permits (for demo/testing the analyzer without site data)
# ---------------------------------------------------------------------------

async def generate_dummy_permit(permit_type: str = "hot_work",
                                unit: str | None = None) -> dict:
    permit_type = permit_type if permit_type in PERMIT_TYPES else "hot_work"
    plant = load_plant()
    pool = plant.get("equipment", [])
    eq = random.choice(pool) if pool else {"tag": "E-104", "type": "exchanger",
                                           "service": "hydrocarbon"}
    data = await ollama_json(
        "Invent ONE realistic Indian refinery permit-to-work job for testing "
        f"software. Permit type: {permit_type}. Equipment: {eq.get('tag')} "
        f"({eq.get('type')}, service: {eq.get('service')}). "
        'Return JSON: {"job_description": "one line", '
        '"isolations": ["2-3 realistic isolation steps"], '
        '"ppe": ["items"], "special_conditions": "one line or empty"}',
        read_timeout=120.0, temperature=0.8)
    return {
        "permit_no": f"{permit_type[:2].upper()}-{time.strftime('%Y')}-{random.randint(1000, 9999)}",
        "permit_type": permit_type,
        "unit": unit or (plant.get("units") or [{}])[0].get("name", "Unit-1"),
        "equipment_tag": eq.get("tag", ""),
        "job_description": data.get("job_description", f"{permit_type} job on {eq.get('tag')}"),
        "contractor": "M/s Dummy Contractor (generated)",
        "validity": {"from": time.strftime("%Y-%m-%dT08:00"),
                     "to": time.strftime("%Y-%m-%dT18:00")},
        "isolations": data.get("isolations", []),
        "gas_test": {"required": permit_type in ("hot_work", "confined_space",
                                                 "line_breaking")},
        "ppe": data.get("ppe", []),
        "special_conditions": data.get("special_conditions", ""),
        "generated_dummy": True,
    }


# ---------------------------------------------------------------------------
# analyzer - the core value proposition
# ---------------------------------------------------------------------------

ANALYZE_PROMPT = """You are a process-safety reviewer sitting one layer above the permit system at an Indian plant. A permit is about to be issued. Using ONLY the evidence below, produce incident-informed checks that go BEYOND the static checklist.

PERMIT:
{permit}

NEARBY EQUIPMENT AND HAZARDS (from plant model - treat as authoritative):
{proximity}

RELATED REAL INCIDENTS (corpus excerpts - cite as [n]):
{seeds}

STRUCTURED INCIDENT ANCHORS (patterns from the incident database):
{anchors}

Return JSON:
{{"critical_questions": [{{"role": "operator|shift_incharge|engineer",
   "question": "specific, checkable, references actual tags/conditions",
   "because": "which incident pattern motivates it, with [n] where applicable"}}],
 "stop_work_triggers": ["conditions under which this permit must be suspended"],
 "often_missed": ["2-4 checks crews commonly skip for this job type, per the incidents"],
 "proximity_warnings": ["hazard interactions with nearby equipment, using the distances given"]}}

Rules: max 8 critical questions; every question must be verifiable on the spot
(no 'ensure safety'); never invent equipment not in the permit or plant model."""


def _fact_matches(permit: dict, limit: int = 5) -> list[dict]:
    records = load_incidents()
    hay_words = set(re.findall(r"[a-z]{4,}", json.dumps(permit).lower()))
    scored = []
    for r in records:
        rhay = " ".join(str(r.get(k) or "") for k in
                        ("incident_type", "summary", "root_cause", "equipment",
                         "facility_type", "substances")).lower()
        score = sum(1 for w in hay_words if w in rhay)
        if score >= 2:
            scored.append((score, r))
    scored.sort(key=lambda t: -t[0])
    return [{"year": r.get("year"), "type": r.get("incident_type"),
             "root_cause": r.get("root_cause"), "summary": r.get("summary"),
             "title": r.get("title"), "file": r.get("file")}
            for _, r in scored[:limit]]


async def analyze_permit(permit: dict, model: str | None = None) -> dict:
    """Permit dict -> incident-informed review. Deterministic evidence
    (retrieval, facts, proximity) is returned alongside the LLM output so
    the UI can always show WHY a question was asked."""
    plant = load_plant()
    tag = permit.get("equipment_tag", "")
    prox = neighbors(plant, tag) if tag else []

    query = (f"{permit.get('permit_type', '')} {permit.get('job_description', '')} "
             f"{tag} incident causes")
    result = await retrieval.retrieve(query, mode="good", k=6)
    seeds = result["hits"]
    seeds_txt = "\n\n".join(
        f"[{h['n']}] {h['category']} - \"{h['title']}\", p.{h['page']}\n{h['text'][:600]}"
        for h in seeds) or "(none)"
    anchors = _fact_matches(permit)
    anchors_txt = "\n".join(
        f"- {a['year'] or '?'} | {a['type'] or '?'} | cause: {a['root_cause'] or '?'} "
        f"| {a['summary'] or ''}" for a in anchors) or "(none)"
    prox_txt = "\n".join(
        f"- {p['tag']} ({p['type']}, {p['service']}) at {p['distance_m']} m; "
        f"hazards: {', '.join(p['hazards']) or 'none listed'}" for p in prox) \
        or "(no plant model loaded - proximity analysis unavailable)"

    review = await ollama_json(
        ANALYZE_PROMPT.format(
            permit=json.dumps(permit, ensure_ascii=False)[:2000],
            proximity=prox_txt, seeds=seeds_txt, anchors=anchors_txt),
        read_timeout=300.0, temperature=0.3, model=model)

    return {
        "permit": permit,
        "review": review,
        "evidence": {
            "proximity": prox,
            "incident_anchors": anchors,
            "corpus_sources": [{
                "n": h["n"], "title": h["title"], "category": h["category"],
                "doc_type": h.get("doc_type", ""), "page": h["page"],
                "pages_total": h["pages_total"], "file": h["file"],
                "doc_url": h["doc_url"], "source_url": h["source_url"],
                "distance": h["distance"], "snippet": h["text"][:280],
            } for h in seeds],
            "pipeline": result["pipeline"],
        },
        "disclaimer": ("Decision support only. The permit issuer and area in-charge "
                       "remain fully responsible; validate every question against "
                       "site conditions and the site's PTW procedure."),
    }
