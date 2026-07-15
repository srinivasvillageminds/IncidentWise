"""FastAPI backend for incidentwise.

Endpoints:
  POST /api/chat    - SSE stream: sources event, then token events, then done
  GET  /api/health  - Ollama + index status
  GET  /api/stats   - corpus/index numbers for the UI
  GET  /            - Claude-inspired chat UI (static/index.html)

Run:  uvicorn app:app --port 8000
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import analytics
import drills
import guardrails
import handover
import history
import permits
import rag
import retrieval
import scenario as scenario_mod
import simops
import verticals
import testbench
from config import (CHAT_BACKEND, GATE_DISTANCE, GUARD_LEVEL, INGEST_REPORT,
                    OLLAMA_MODEL, OPENAI_API_KEY, OPENAI_CHAT_MODEL, PROVIDERS,
                    TOP_K)

OUT_OF_SCOPE_MESSAGE = (
    "This assistant answers questions about **Indian process safety** — "
    "incidents and investigations (OISD, PNGRB, NDMA, DISH), standards and "
    "guidelines, audits and certifications, permits and plant operations.\n\n"
    "Your question doesn't appear to be in that scope, and nothing relevant "
    "was found in the document corpus, so I won't improvise an answer.\n\n"
    "If this *is* a process-safety question, rephrase it with plant, incident, "
    "or regulatory terms — or pin a document category in Settings."
)

app = FastAPI(title="incidentwise", version="0.1.0")

STATIC_DIR = Path(__file__).resolve().parent / "static"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[dict] = Field(default_factory=list)
    category: str | None = None
    k: int = TOP_K
    mode: str = "good"      # medium | good | best
    vertical: str = "auto"  # auto | incidents | regulatory
    backend: str | None = None  # None = CHAT_BACKEND env; or "ollama"/"openai"
    model: str | None = None    # specific model name for the chosen backend
    guard: str | None = None    # off | l1 | l2 | l3 (None = GUARD_LEVEL env)


class ScenarioRequest(BaseModel):
    theme: str = Field(min_length=2, max_length=200)
    unit_type: str = Field(default="process plant", max_length=100)
    difficulty: str = "intermediate"  # basic | intermediate | advanced
    category: str | None = None


class DrillCreateRequest(ScenarioRequest):
    refine: bool = True
    save: bool = True
    model: str | None = None
    backend: str | None = None


class DrillRefineRequest(BaseModel):
    spec: dict
    instruction: str = Field(min_length=2, max_length=600)
    model: str | None = None
    backend: str | None = None


class HandoverRequest(BaseModel):
    radius_m: float = 30.0
    model: str | None = None
    backend: str | None = None


class HistorySaveRequest(BaseModel):
    id: str | None = None
    kind: str  # ask | drill
    title: str
    payload: dict


class TitleRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    answer: str = ""


class BenchRequest(BaseModel):
    models: list[str] = Field(min_length=1, max_length=6)  # "backend:model" ids
    judge: str | None = None                                # judge model id or None
    suite: str = "ask"                                      # ask | guard | drill
    guard: str = "l1"                                       # for suite=ask
    levels: list[str] = Field(default_factory=lambda: ["off", "l1", "l2"])  # suite=guard
    kinds: list[str] | None = None                           # filter ask suite


class DrillStatusRequest(BaseModel):
    status: str  # draft | validated | rejected
    notes: str = ""


class PermitAnalyzeRequest(BaseModel):
    permit: dict
    model: str | None = None
    log_job: bool = False  # also record into the SIMOPS jobs log


class SimopsScenarioRequest(BaseModel):
    job_ids: list[str] = Field(min_length=2, max_length=2)
    model: str | None = None
    save: bool = True


class DummyPermitRequest(BaseModel):
    permit_type: str = "hot_work"
    unit: str | None = None


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/chat")
async def chat(req: ChatRequest):
    async def generate():
        # Analytics questions get deterministic answers from incidents.json -
        # numbers are counted, never generated. Falls through to RAG otherwise.
        if analytics.available() and analytics.detect(req.message):
            ans = analytics.answer(req.message)
            if ans is not None:
                yield sse("sources", {
                    "weak": False, "mode": req.mode,
                    "pipeline": "structured incident database (deterministic)",
                    "vertical": "analytics", "scoped": [], "web": [],
                    "items": ans["sources"],
                })
                md = ans["markdown"]
                for i in range(0, len(md), 400):
                    yield sse("token", {"t": md[i:i + 400]})
                yield sse("done", {})
                return

        guard = (req.guard or GUARD_LEVEL).lower()
        if guard not in ("off", "l1", "l2", "l3"):
            guard = "l1"

        def refusal(pipeline: str, vertical_tag: str, message: str):
            return [sse("sources", {"weak": True, "mode": req.mode,
                                    "pipeline": pipeline, "vertical": vertical_tag,
                                    "scoped": [], "web": [], "items": []}),
                    sse("token", {"t": message}), sse("done", {})]

        # L3 first: harm screen is independent of topic and of the corpus.
        if guard == "l3":
            g = await guardrails.guard_model_check(req.message)
            if g.get("flagged"):
                cats = f"\n\n(guard category: {g.get('categories', '')})" if g.get("categories") else ""
                for chunk in refusal(f"guard model ({g.get('model', '')})", "blocked",
                                     guardrails.UNSAFE_MESSAGE.format(cats=cats)):
                    yield chunk
                return
            if not g.get("available", True):
                # fail-open, but say so in the log
                print(f"guardrails: L3 unavailable - {g.get('note', '')}")

        vertical = (req.vertical if req.vertical in verticals.VERTICALS else "auto")
        resolved = verticals.classify(req.message) if vertical == "auto" else vertical

        # Follow-ups ("the sequence of events in the first one") cannot be
        # retrieved on their own - condense against the history first. The
        # ANSWER still sees the user's original wording.
        search_q = await rag.condense_query(req.message, req.history,
                                            model=req.model, backend=req.backend)

        try:
            result = await retrieval.retrieve(search_q, mode=req.mode,
                                              k=req.k, category=req.category)
            hits = result["hits"]
        except Exception as exc:  # noqa: BLE001
            yield sse("error", {"message": f"Retrieval failed: {exc}. Did you run ingest.py?"})
            return

        # L1: deterministic scope gate - no domain vocabulary AND no close match
        # in the corpus -> fixed refusal, no LLM call. Uses GATE_DISTANCE (tighter
        # than WEAK_DISTANCE): embeddings retrieve *something* for any query, so
        # gating on `weak` alone meant the gate never actually fired.
        gate_far = (result.get("min_distance") is None
                    or result["min_distance"] > GATE_DISTANCE)
        if (guard in ("l1", "l2", "l3") and gate_far
                and not verticals.in_domain(req.message)):
            for chunk in refusal("scope gate (deterministic refusal)",
                                 "out_of_scope", OUT_OF_SCOPE_MESSAGE):
                yield chunk
            return

        # L2: LLM intent classifier for what vocabulary can't see (fails open).
        if guard in ("l2", "l3"):
            intent = await guardrails.intent_check(req.message,
                                                   model=req.model, backend=req.backend)
            v = intent.get("verdict", "in_scope")
            if v != "in_scope":
                msg = (OUT_OF_SCOPE_MESSAGE if v == "out_of_scope"
                       else guardrails.INJECTION_MESSAGE if v == "injection"
                       else guardrails.UNSAFE_MESSAGE.format(cats=""))
                for chunk in refusal(f"intent classifier ({v}: {intent.get('reason', '')})",
                                     "blocked" if v != "out_of_scope" else "out_of_scope", msg):
                    yield chunk
                return

        web_results: list[dict] = []
        if resolved == "regulatory":
            web_results = await asyncio.to_thread(verticals.web_search, req.message)

        yield sse("sources", {
            "weak": result["weak"],
            "mode": result["mode"],
            "pipeline": result["pipeline"],
            "vertical": resolved,
            "scoped": result.get("scoped", []),
            "rewritten": search_q if search_q != req.message else None,
            "web": web_results,
            "items": [{
                "n": h["n"], "title": h["title"], "category": h["category"],
                "doc_type": h.get("doc_type", ""),
                "page": h["page"], "pages_total": h["pages_total"],
                "file": h["file"], "doc_url": h["doc_url"],
                "source_url": h["source_url"], "distance": h["distance"],
                "snippet": h["text"][:280],
            } for h in hits],
        })

        # Deterministic entity check: if NOTHING retrieved mentions any named
        # thing from the question (plant, unit, date), the model is one step
        # away from narrating a different incident with perfect citations.
        # Refuse rather than substitute - this is the worst failure mode we have.
        if guard != "off" and resolved != "analytics":
            missing = guardrails.entity_mismatch(search_q, hits)
            if missing:
                for chunk in refusal(
                        "entity check (no excerpt mentions the subject)",
                        "no_match",
                        "The corpus doesn't appear to contain the specific incident "
                        f"you asked about (**{', '.join(missing[:4])}**).\n\n"
                        "The excerpts I retrieved describe *other* incidents — and "
                        "narrating those as if they were yours would be worse than "
                        "no answer at all, so I won't.\n\n"
                        "Try naming the plant, unit, or date differently, or ask "
                        "what the corpus *does* cover for this equipment type."):
                    yield chunk
                return

        try:
            messages = rag.build_messages(req.message, hits, req.history,
                                          web_results=web_results)
            answer_acc = []
            async for token in rag.stream_chat(messages, backend=req.backend,
                                               model=req.model):
                answer_acc.append(token)
                yield sse("token", {"t": token})
            full = "".join(answer_acc)
            if guard != "off":
                # The model was told to decline off-topic tails; small models
                # comply anyway. Flag it rather than let it pass as advice.
                if guardrails.offtopic_leak(full):
                    yield sse("token", {"t": guardrails.OFFTOPIC_LEAK_NOTE})
                # Grounded answer with zero [n] citations is suspect.
                warn = guardrails.citation_warning(full, bool(hits), resolved)
                if warn:
                    yield sse("token", {"t": warn})
            yield sse("done", {})
        except Exception as exc:  # noqa: BLE001
            yield sse("error", {
                "message": (f"LLM call failed: {exc}. Check that Ollama is running "
                            f"(`ollama serve`) and the model is pulled "
                            f"(`ollama pull {OLLAMA_MODEL}`).")
            })

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/scenario")
async def make_scenario(req: ScenarioRequest):
    """Generate a corpus-grounded hypothetical incident for safety-meeting drills."""
    async def generate():
        try:
            result = await scenario_mod.get_seeds(req.theme, req.unit_type, req.category)
            seeds = result["hits"]
        except Exception as exc:  # noqa: BLE001
            yield sse("error", {"message": f"Seed retrieval failed: {exc}. Did you run ingest.py?"})
            return

        yield sse("sources", {
            "weak": result["weak"], "pipeline": result["pipeline"],
            "vertical": "drill", "web": [],
            "items": [{
                "n": h["n"], "title": h["title"], "category": h["category"],
                "doc_type": h.get("doc_type", ""),
                "page": h["page"], "pages_total": h["pages_total"],
                "file": h["file"], "doc_url": h["doc_url"],
                "source_url": h["source_url"], "distance": h["distance"],
                "snippet": h["text"][:280],
            } for h in seeds],
        })

        try:
            messages = scenario_mod.build_messages(seeds, req.theme, req.unit_type, req.difficulty)
            async for token in rag.stream_chat(messages, temperature=0.7):
                yield sse("token", {"t": token})
            yield sse("done", {})
        except Exception as exc:  # noqa: BLE001
            yield sse("error", {"message": f"Scenario generation failed: {exc}. "
                                           f"Check Ollama is running with {OLLAMA_MODEL}."})

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/health")
async def health():
    ollama = await rag.ollama_status()
    try:
        chunks = rag.get_collection().count()
    except Exception:  # noqa: BLE001
        chunks = 0
    chat_ready = (ollama["model_available"] if CHAT_BACKEND == "ollama"
                  else bool(PROVIDERS.get(CHAT_BACKEND, {}).get("api_key")))
    chat_model = (ollama["model"] if CHAT_BACKEND == "ollama"
                  else PROVIDERS.get(CHAT_BACKEND, {}).get("default_model", "?"))
    return {"ok": (ollama["reachable"] and ollama["embed_model_available"]
                   and chat_ready and chunks > 0),
            "chat": {"backend": CHAT_BACKEND, "model": chat_model, "ready": chat_ready},
            "ollama": ollama, "index_chunks": chunks}


@app.post("/api/drill/create")
async def drill_create(req: DrillCreateRequest):
    """Drill engine v2: generate -> critique -> refine -> library (SSE progress)."""
    async def generate():
        try:
            yield sse("stage", {"stage": "retrieving + generating"})
            drill = await drills.create_drill(req.theme, req.unit_type,
                                              req.difficulty, refine=req.refine,
                                              category=req.category, model=req.model,
                                              backend=req.backend)
            yield sse("stage", {"stage": "critiqued",
                                "scores": drill["critique"].get("scores", {}),
                                "refined": drill["refined"]})
            drill_id = drills.save_drill(drill) if req.save else None
            yield sse("result", {"id": drill_id, "spec": drill["spec"],
                                 "critique": drill["critique"],
                                 "refined": drill["refined"],
                                 "markdown": drills.render_markdown(drill),
                                 "anchors": drill["anchors"],
                                 "sources": [{
                                     "n": h["n"], "title": h["title"],
                                     "category": h["category"],
                                     "doc_type": h.get("doc_type", ""),
                                     "page": h["page"], "pages_total": h["pages_total"],
                                     "file": h["file"], "doc_url": h["doc_url"],
                                     "source_url": h["source_url"],
                                     "distance": h["distance"],
                                     "snippet": h["text"][:280],
                                 } for h in drill["seeds"]],
                                 "status": "draft" if drill_id else "unsaved"})
            yield sse("done", {})
        except Exception as exc:  # noqa: BLE001
            yield sse("error", {"message": f"Drill creation failed: {exc}"})

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/drill/refine")
async def drill_refine(req: DrillRefineRequest):
    """History-aware follow-up: revise an existing spec per instruction."""
    try:
        spec = await drills.refine_with_instruction(req.spec, req.instruction,
                                                    model=req.model,
                                                    backend=req.backend)
        return {"spec": spec, "markdown": drills.render_markdown({"spec": spec})}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Refine failed: {exc}"}


@app.post("/api/handover")
async def handover_check(req: HandoverRequest):
    """Pre-handover brief across ALL open permits (proximity + incident-informed)."""
    try:
        return await handover.pre_handover(radius_m=req.radius_m,
                                           model=req.model, backend=req.backend)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Handover check failed: {exc}"}


@app.get("/api/history")
async def history_list():
    return {"items": history.list_items()}


@app.get("/api/history/{item_id}")
async def history_get(item_id: str):
    item = history.get_item(item_id)
    return item if item else {"error": "not found"}


@app.post("/api/history")
async def history_save(req: HistorySaveRequest):
    item_id = history.save_item(req.kind, req.title, req.payload, req.id)
    return {"id": item_id}


@app.delete("/api/history/{item_id}")
async def history_delete(item_id: str):
    return {"ok": history.delete_item(item_id)}


@app.get("/api/drills")
async def drills_list(status: str | None = None):
    return {"drills": drills.list_drills(status)}


@app.get("/api/drills/{drill_id}")
async def drill_get(drill_id: str):
    d = drills.get_drill(drill_id)
    return d if d else {"error": "not found"}


@app.post("/api/drills/{drill_id}/status")
async def drill_status(drill_id: str, req: DrillStatusRequest):
    """Expert validation workflow: draft -> validated | rejected."""
    ok = drills.set_status(drill_id, req.status, req.notes)
    return {"ok": ok, "id": drill_id, "status": req.status if ok else None}


@app.post("/api/permit/analyze")
async def permit_analyze(req: PermitAnalyzeRequest):
    """Incident-informed permit review: corpus + facts + plant proximity."""
    try:
        result = await permits.analyze_permit(req.permit, model=req.model)
        if req.log_job:
            result["logged_job"] = simops.log_job(req.permit)
        return result
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Permit analysis failed: {exc}"}


@app.post("/api/jobs/log")
async def jobs_log(req: PermitAnalyzeRequest):
    """Record a permit as a job in the SIMOPS rolling log."""
    return simops.log_job(req.permit)


@app.get("/api/jobs")
async def jobs_list(days: int = 30):
    return {"jobs": simops.list_jobs(days)}


@app.get("/api/simops/combinations")
async def simops_combinations(days: int = 30, radius: float = 40.0):
    """Time-overlapping, spatially-related job pairs worth a what-if look."""
    return {"combinations": simops.find_combinations(days, radius)}


@app.post("/api/simops/scenario")
async def simops_scenario(req: SimopsScenarioRequest):
    """'What could have happened' drill from two logged jobs (SSE)."""
    async def generate():
        try:
            jobs = {j["job_id"]: j for j in simops.list_jobs(365)}
            picked = [jobs.get(i) for i in req.job_ids]
            if not all(picked):
                yield sse("error", {"message": f"job ids not found: {req.job_ids}"})
                return
            from permits import distance_hint, load_plant
            relation, dist = distance_hint(load_plant(),
                                           picked[0].get("equipment_tag", ""),
                                           picked[1].get("equipment_tag", ""))
            combo = {"jobs": picked, "relation": relation, "distance_m": dist,
                     "why_flagged": f"manual selection ({relation}"
                                    + (f", {dist} m)" if dist is not None else ")")}
            yield sse("stage", {"stage": "retrieving + generating", "combo": combo["why_flagged"]})
            drill = await simops.combination_scenario(combo, model=req.model)
            drill_id = drills.save_drill(drill) if req.save else None
            yield sse("result", {"id": drill_id, "spec": drill["spec"],
                                 "markdown": drills.render_markdown(drill),
                                 "meta": drill["meta"], "status": "draft" if drill_id else "unsaved"})
            yield sse("done", {})
        except Exception as exc:  # noqa: BLE001
            yield sse("error", {"message": f"SIMOPS scenario failed: {exc}"})

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/permit/dummy")
async def permit_dummy(req: DummyPermitRequest):
    """Generate a realistic dummy permit for demo/testing the analyzer."""
    try:
        return await permits.generate_dummy_permit(req.permit_type, req.unit)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Dummy permit generation failed: {exc}"}


@app.get("/api/plant")
async def plant():
    p = permits.load_plant()
    return {"plant": p.get("plant", ""), "units": p.get("units", []),
            "equipment_count": len(p.get("equipment", [])),
            "model_file": "plant_model.json" if permits.PLANT_MODEL_PATH.exists()
                          else "plant_model.json.example (sample)"}


@app.post("/api/title")
async def make_title(req: TitleRequest):
    """Professional 3-6 word conversation title (the ChatGPT/Claude way)."""
    try:
        out = await rag.ollama_json(
            "Write a professional 3-6 word title for this conversation, Title "
            "Case, no quotes, no trailing punctuation.\n"
            f"Question: {req.question[:400]}\n"
            f"Answer excerpt: {req.answer[:400]}\n"
            'Return JSON: {"title": "..."}',
            read_timeout=60.0)
        title = str(out.get("title", "")).strip().strip('"')[:60]
        return {"title": title}
    except Exception:  # noqa: BLE001
        return {"title": ""}


@app.post("/api/bench")
async def bench_run(req: BenchRequest):
    """Test playground: ask | guard | drill suites x selected models (SSE rows)."""
    async def generate():
        try:
            if req.suite == "drill":
                gen = testbench.run_drill(req.models, req.judge)
            elif req.suite == "guard":
                gen = testbench.run_guard(req.models, req.judge, req.levels)
            else:
                gen = testbench.run_ask(req.models, req.judge, req.guard, req.kinds)
            async for event, data in gen:
                yield sse(event, data)
            yield sse("done", {})
        except Exception as exc:  # noqa: BLE001
            yield sse("error", {"message": f"Bench failed: {exc}"})
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/bench/report")
async def bench_report(suite: str = "ask"):
    return testbench.last_report(suite)


@app.get("/api/models")
async def models():
    """Answering-model choices for the UI dropdown."""
    out = []
    status = await rag.ollama_status()
    for name in status.get("models_pulled", []):
        if "embed" in name.lower() or "vision" in name.lower() or "-vl" in name.lower():
            continue  # embedding/vision models are not answering models
        out.append({"id": f"ollama:{name}", "label": f"{name} · local"})
    for prov, cfg in PROVIDERS.items():
        if not cfg["api_key"]:
            continue
        for name in dict.fromkeys(cfg["models"]):
            out.append({"id": f"{prov}:{name}", "label": f"{name} · {cfg['label']}"})
    default_model = (PROVIDERS.get(CHAT_BACKEND, {}).get("default_model")
                     if CHAT_BACKEND != "ollama" else OLLAMA_MODEL)
    return {"models": out,
            "default": {"backend": CHAT_BACKEND, "model": default_model or OLLAMA_MODEL}}


@app.get("/api/analytics")
async def analytics_endpoint():
    """Raw aggregates from the structured incident database."""
    if not analytics.available():
        return {"available": False,
                "hint": "Run `python facts.py` after ingest to build incidents.json"}
    return {"available": True, **analytics.aggregates()}


@app.get("/api/stats")
async def stats():
    try:
        chunks = rag.get_collection().count()
    except Exception:  # noqa: BLE001
        chunks = 0
    categories, docs = {}, 0
    if INGEST_REPORT.exists():
        try:
            report = json.loads(INGEST_REPORT.read_text(encoding="utf-8"))
            categories = report.get("categories", {})
            docs = len(report.get("indexed", [])) + len(report.get("skipped_existing", []))
        except Exception:  # noqa: BLE001
            pass
    return {"index_chunks": chunks, "documents": docs,
            "categories": categories,
            "model": OPENAI_CHAT_MODEL if CHAT_BACKEND == "openai" else OLLAMA_MODEL}


# Mounted last so /api/* wins.
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
