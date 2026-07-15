"""Tiered retrieval pipelines for incidentwise.

Three accuracy levels, mirroring how enterprise RAG stacks are built:

  medium  vector search only (fastest; what most basic RAG demos do)
  good    hybrid: vector + BM25 keyword search, fused with Reciprocal Rank
          Fusion (the default in Azure AI Search / Elastic / OpenSearch /
          Weaviate hybrid modes). Catches exact terms embeddings miss:
          standard numbers ("OISD-STD-117"), clause refs, company names.
  best    multi-query expansion (LLM rewrites the question with alternate
          terminology), hybrid retrieval per query, RRF fusion, then a
          listwise LLM rerank of the candidate pool (the local, offline
          equivalent of Cohere Rerank / cross-encoder rerankers).

Everything runs through Ollama + ChromaDB + pure-Python BM25 — no cloud
services, no model downloads beyond what Ollama already has.
All LLM-assisted steps degrade gracefully: on any failure they fall back
to the simpler pipeline rather than erroring the request.
"""
from __future__ import annotations

import json
import re

import httpx
from rank_bm25 import BM25Okapi

from config import OLLAMA_MODEL, OLLAMA_URL, TOP_K, WEAK_DISTANCE
from embeddings import embed_texts
from rag import get_collection

MODES = ("medium", "good", "best")
PIPELINE_LABEL = {
    "medium": "vector search",
    "good": "hybrid (vector + BM25, RRF fusion)",
    "best": "multi-query + hybrid + LLM rerank",
}
RRF_K = 60           # standard RRF constant
POOL_GOOD = 14       # candidates per retriever before fusion (good)
POOL_BEST = 8        # candidates per retriever per query (best)
RERANK_POOL = 18     # candidates fed to the LLM reranker
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ORG_TOKENS = {"oisd", "pngrb", "ndma", "dish", "gujarat", "dgfasli", "peso"}
# Words that appear in category NAMES but are far too generic to scope on.
# Bug fixed: "tell me about an incident at X" matched "OISD Incident Guidelines"
# and "PNGRB Incident Analysis", scoping the search AWAY from OISD Case Studies -
# which is exactly where incidents live.
_GENERIC_CAT_TOKENS = {"incident", "incidents", "case", "cases", "study", "studies",
                       "analysis", "guidelines", "guideline", "report", "reports",
                       "compendium", "statistics", "chemical", "annual"}
_FORM_INTENT = re.compile(
    r"\b(form|format|formats|proforma|pro\s*forma|template|checklist|annexure|"
    r"fir|fields?|report(ing)?\s+(form|format))\b", re.IGNORECASE)

# ---- in-memory chunk store + BM25 index (built lazily from Chroma) ----------
_bm25: BM25Okapi | None = None
_bm25_ids: list[str] = []
_chunks: dict[str, dict] = {}   # id -> {"text": str, "meta": dict}


def reset_cache() -> None:
    """Call after re-ingesting (or just restart the server)."""
    global _bm25, _bm25_ids, _chunks
    _bm25, _bm25_ids, _chunks = None, [], {}


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _ensure_bm25() -> None:
    global _bm25, _bm25_ids
    if _bm25 is not None or _bm25_ids:
        return
    col = get_collection()
    data = col.get(include=["documents", "metadatas"])
    _bm25_ids = list(data["ids"])
    for cid, doc, meta in zip(data["ids"], data["documents"], data["metadatas"]):
        _chunks[cid] = {"text": doc, "meta": meta or {}}
    corpus = [_tokenize(_chunks[cid]["text"]) for cid in _bm25_ids]
    if corpus:
        _bm25 = BM25Okapi(corpus)


# ---- category pinning ---------------------------------------------------------

def _where(cats: list[str] | None):
    if not cats:
        return None
    return {"category": cats[0]} if len(cats) == 1 else {"category": {"$in": cats}}


def detect_categories(question: str) -> list[str] | None:
    """Pin retrieval to categories the question names explicitly.

    'root causes across the OISD case studies' should never be answered
    from PNGRB or NDMA documents. Deterministic token matching: an exact
    pin needs 2+ category tokens in the question; naming just the org
    (e.g. 'OISD') pins to all of that org's categories.
    """
    _ensure_bm25()  # populates _chunks with every category present
    cats = sorted({c["meta"].get("category", "") for c in _chunks.values()} - {""})
    if not cats:
        return None
    q_tokens = set(_TOKEN_RE.findall(question.lower()))
    q_tokens |= {t.rstrip("s") for t in q_tokens}  # plural-insensitive
    scored: list[tuple[str, int, bool]] = []
    for cat in cats:
        toks = [t.lower() for t in cat.split()]
        matched = [t for t in toks if (t in q_tokens or t.rstrip("s") in q_tokens)
                   and t not in _GENERIC_CAT_TOKENS]   # generic words never scope
        if matched:
            org_hit = any(t in _ORG_TOKENS for t in matched)
            scored.append((cat, len(matched), org_hit))
    if not scored:
        return None
    # A strong pin needs 2+ SPECIFIC tokens, or an organisation name.
    strong = [c for c, n, _ in scored if n >= 2]
    if strong:
        return strong[:4]
    org_only = [c for c, _, org in scored if org]
    return org_only[:4] or None


# ---- primitive retrievers ----------------------------------------------------

def _vector_search(queries: list[str], n: int, cats: list[str] | None):
    """Ranked id-lists per query + cosine distances for the FIRST query."""
    col = get_collection()
    vecs = embed_texts(queries, kind="query")
    res = col.query(
        query_embeddings=vecs,
        n_results=max(1, n),
        where=_where(cats),
        include=["documents", "metadatas", "distances"],
    )
    lists: list[list[str]] = []
    dist_map: dict[str, float] = {}
    for qi in range(len(queries)):
        ids = res["ids"][qi]
        lists.append(list(ids))
        for cid, doc, meta, dist in zip(ids, res["documents"][qi],
                                        res["metadatas"][qi], res["distances"][qi]):
            _chunks.setdefault(cid, {"text": doc, "meta": meta or {}})
            if qi == 0:
                d = float(dist)
                dist_map[cid] = min(d, dist_map.get(cid, d))
    return lists, dist_map


def _bm25_search(query: str, n: int, cats: list[str] | None) -> list[str]:
    _ensure_bm25()
    if _bm25 is None:
        return []
    cat_set = set(cats) if cats else None
    scores = _bm25.get_scores(_tokenize(query))
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    out: list[str] = []
    for i in order:
        if scores[i] <= 0 or len(out) >= n:
            break
        cid = _bm25_ids[i]
        if cat_set and _chunks[cid]["meta"].get("category") not in cat_set:
            continue
        out.append(cid)
    return out


def _rrf(ranked_lists: list[list[str]]) -> list[str]:
    scores: dict[str, float] = {}
    for lst in ranked_lists:
        for rank, cid in enumerate(lst):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(scores, key=lambda c: scores[c], reverse=True)


# ---- LLM-assisted steps (Ollama, JSON mode, graceful fallback) ---------------

async def _ollama_json(prompt: str, read_timeout: float = 90.0) -> dict:
    payload = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
               "format": "json", "options": {"temperature": 0.0, "num_ctx": 8192}}
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=read_timeout)) as client:
        r = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
        r.raise_for_status()
        return json.loads(r.json().get("response", "{}"))


async def _expand_query(question: str) -> list[str]:
    """Two alternative phrasings with different technical terminology."""
    try:
        data = await _ollama_json(
            "You write alternative search queries for retrieval over Indian "
            "process-safety documents (OISD/PNGRB/NDMA standards, incident "
            "investigation reports, case studies).\n"
            f"Question: {question}\n\n"
            "Produce 2 alternative phrasings that use different technical "
            'terminology or synonyms. Return JSON: {"queries": ["q1", "q2"]}'
        )
        qs = [str(q).strip()[:250] for q in data.get("queries", [])
              if isinstance(q, str) and str(q).strip()]
        return qs[:2]
    except Exception:  # noqa: BLE001
        return []


async def _llm_rerank(question: str, candidate_ids: list[str], k: int) -> list[str]:
    """Listwise rerank: one LLM call picks/orders the k most relevant."""
    if len(candidate_ids) <= k:
        return candidate_ids[:k]
    lines = []
    for idx, cid in enumerate(candidate_ids, 1):
        c = _chunks[cid]
        m = c["meta"]
        lines.append(f"[{idx}] ({m.get('category', '')}, \"{str(m.get('title', ''))[:60]}\", "
                     f"p.{m.get('page', '?')}) {c['text'][:320]}")
    try:
        data = await _ollama_json(
            f"Question: {question}\n\nExcerpts:\n" + "\n\n".join(lines) +
            f"\n\nSelect the {k} excerpts most relevant to answering the question, "
            'ordered most-relevant first. Return JSON: {"ranking": [excerpt numbers]}',
            read_timeout=150.0,
        )
        order = []
        for v in data.get("ranking", []):
            s = str(v).strip()
            if s.isdigit() and 1 <= int(s) <= len(candidate_ids):
                order.append(int(s))
        picked = [candidate_ids[i - 1] for i in dict.fromkeys(order)]  # dedupe, keep order
        for cid in candidate_ids:                                      # top up from RRF order
            if len(picked) >= k:
                break
            if cid not in picked:
                picked.append(cid)
        return picked[:k]
    except Exception:  # noqa: BLE001
        return candidate_ids[:k]


# ---- public entry point --------------------------------------------------------

def _hit(cid: str, n: int, dist_map: dict[str, float]) -> dict:
    c = _chunks[cid]
    m = c["meta"]
    d = dist_map.get(cid)
    return {
        "n": n, "text": c["text"],
        "distance": round(d, 4) if d is not None else None,
        "title": m.get("title", ""), "category": m.get("category", ""),
        "doc_type": m.get("doc_type", ""), "tier": m.get("tier", 0),
        "file": m.get("file", ""), "page": m.get("page", 0),
        "pages_total": m.get("pages_total", 0),
        "source_name": m.get("source_name", ""),
        "source_url": m.get("source_url", ""),
        "doc_url": m.get("doc_url", ""),
    }


async def retrieve(question: str, mode: str = "good", k: int = TOP_K,
                   category: str | None = None) -> dict:
    """Returns {"hits", "weak", "pipeline", "mode", "queries", "scoped"}.

    category: explicit user filter (always wins). Otherwise categories the
    question names are auto-pinned. Form/template documents are demoted
    below content documents unless the question is actually about forms.
    """
    mode = mode if mode in MODES else "good"
    k = max(1, min(int(k or TOP_K), 12))

    cats = [category] if category else detect_categories(question)

    queries = [question]
    if mode == "best":
        queries += await _expand_query(question)

    if mode == "medium":
        vec_lists, dist_map = _vector_search(queries, max(k * 2, 8), cats)
        ordered = vec_lists[0]
    elif mode == "good":
        vec_lists, dist_map = _vector_search(queries, POOL_GOOD, cats)
        bm25_list = _bm25_search(question, POOL_GOOD, cats)
        ordered = _rrf(vec_lists + [bm25_list])
    else:  # best
        vec_lists, dist_map = _vector_search(queries, POOL_BEST, cats)
        bm25_lists = [_bm25_search(q, POOL_BEST, cats) for q in queries]
        fused = _rrf(vec_lists + bm25_lists)[:RERANK_POOL]
        ordered = await _llm_rerank(question, fused, k)

    # Demote forms and low-tier reference docs below core content unless the
    # user is explicitly asking about forms (stable sort preserves rank inside
    # each band). Tier: 1 core, 2 guidance, 3 reference (see triage.py).
    if ordered and not _FORM_INTENT.search(question):
        def _demote_key(cid: str):
            m = _chunks[cid]["meta"]
            return (m.get("doc_type", "") == "form", int(m.get("tier", 2)) >= 3)
        ordered = sorted(ordered, key=_demote_key)

    # One excerpt per (document, page): adjacent chunks of the same page add
    # no diversity and burn citation slots (seen as duplicate source cards).
    seen_pages: set = set()
    ids = []
    for cid in ordered:
        m = _chunks[cid]["meta"]
        key = (m.get("doc"), m.get("page"))
        if key in seen_pages:
            continue
        seen_pages.add(key)
        ids.append(cid)
        if len(ids) >= k:
            break
    hits = [_hit(cid, n, dist_map) for n, cid in enumerate(ids, 1)]
    min_distance = min(dist_map.values()) if dist_map else None
    weak = (min_distance > WEAK_DISTANCE) if min_distance is not None else True

    pipeline = PIPELINE_LABEL[mode]
    if cats and not category:
        shown = ", ".join(cats[:2]) + ("…" if len(cats) > 2 else "")
        pipeline += f" · scoped: {shown}"

    return {"hits": hits, "weak": weak, "min_distance": min_distance,
            "pipeline": pipeline, "mode": mode,
            "queries": queries, "scoped": cats or []}
