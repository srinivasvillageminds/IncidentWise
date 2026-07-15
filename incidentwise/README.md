# incidentwise

Local RAG chatbot over the corpus collected by `safety-pdf-crawler`
(OISD / PNGRB case studies, incident investigations, guidelines).
Fully offline after first setup: ChromaDB + local `nomic-embed-text`
embeddings (via Ollama) for retrieval, Ollama for generation, FastAPI + a
Claude-inspired web UI.
Every answer cites the exact source document and page, using the
crawler's provenance manifest.

```
raw_pdfs + manifest ──> ingest.py ──> chroma_db/
                                          │ top-k chunks
user ──> static UI ──> FastAPI /api/chat ─┴─> Ollama ──> streamed, cited answer
```

## Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com) installed and running (`ollama serve`, usually auto-starts)
- Chat model pulled: `ollama pull llama3.1:8b`
- Embedding model pulled: `ollama pull nomic-embed-text`
  (embeddings run through Ollama too — no external CDN/HuggingFace downloads)
- The crawler corpus at `../safety-pdf-crawler/data/` (already present)

## Setup (Windows)

```bat
cd D:\safety_gpt_resources\incidentwise
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 1. Build the index

```bat
python ingest.py
```

- Re-runs are incremental (already-indexed PDFs are skipped by content hash).
- `python ingest.py --limit 5` for a quick smoke test; `--rebuild` to start over.
- Scanned/image-only PDFs are skipped by default and listed in `ingest_report.json`.

### OCR for scanned pages (optional)

Two interchangeable backends; only pages with no extractable text get OCR'd:

```bat
:: local VLM - data never leaves the machine, slow on CPU
ollama pull qwen3-vl:8b
python ingest.py --ocr ollama

:: OpenAI vision - fast and accurate, needs a key, public docs only
set OPENAI_API_KEY=sk-...
python ingest.py --ocr openai
```

| | ollama (qwen3-vl:8b) | openai (gpt-4o-mini) |
|---|---|---|
| Data leaves machine | never | yes — public corpus only |
| Speed on CPU | minutes/page | seconds/page |
| Handwriting / Gujarati | decent | strong |
| Cost | free | per page (small, check current pricing) |

Config: `OCR_BACKEND`, `OLLAMA_VLM_MODEL` (try `granite3.2-vision:2b` for a much
faster document-specialized model), `OPENAI_VLM_MODEL`, `OCR_MAX_PAGES` (default
40/doc), `OCR_DPI`. OCR'd chunks carry `ocr` metadata so answers derived from
OCR text remain traceable. Three consecutive OCR failures disable OCR for the
run instead of failing the ingest. Already-indexed documents are untouched;
re-running with `--ocr` picks up only the previously skipped scanned ones.

**PDF forensics (deterministic):** "no extractable text" is measured, not
guessed. `python pdf_forensics.py --empty-only` diagnoses every skipped
document into `image_only_scan` / `fonts_no_unicode` (looks digital in a
viewer, extracts as nothing — broken ToUnicode map) / `vector_outlines` /
`encrypted` / `corrupt`, using page-level counts of text, images, image
coverage, fonts, and vector drawings. Ingest logs the diagnosed verdict per
skipped file. All three non-scan text pathologies are still correctly fixed
by OCR — the VLM reads rendered pixels, which are immune to font-encoding
problems.

**OCR cache:** every OCR result is stored in `ocr_cache/{doc-hash}/pNNNN.txt`
(plus per-page provenance in `meta.json`) and reused on every later run —
including `--rebuild` and runs with `--ocr off` — so the expensive OCR pass
happens exactly once per page, ever. Empty results are cached too (hopeless
scans aren't retried). The files are plain text: open them to judge OCR
quality. `--reocr` regenerates deliberately (e.g. after switching VLM);
`OCR_CACHE_DIR` relocates the cache.

## 2. Run the server

```bat
uvicorn app:app --port 8000
```

Open http://localhost:8000 — the status dot in the header tells you if
Ollama, the model, and the index are all ready.

### Run on a free Colab GPU instead

No local GPU? `../COLAB_DEMO.md` runs the whole stack on a free Colab GPU:
one notebook cell restores your corpus / index / caches from Google Drive,
starts Ollama, and prints a public `*.trycloudflare.com` link you can share
for demos. Everything else in this README — models, ingest, verticals,
guardrails, drills — works identically there.

## Configuration (.env file or env vars)

Copy `.env.example` to `.env` and edit — API keys and model choices live
there. Real environment variables override `.env` values. `.env` is
gitignored; never commit it.

| Variable        | Default                  | Purpose                          |
|-----------------|--------------------------|----------------------------------|
| `OLLAMA_MODEL`  | `llama3.1:8b`            | Any chat model you have pulled   |
| `CHAT_BACKEND`  | `ollama`                 | `openai` routes answer generation (chat, drills, permits, rerank) to `OPENAI_CHAT_MODEL`; embeddings/retrieval stay on Ollama |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text`  | Embedding model (via Ollama)     |
| `OLLAMA_URL`    | `http://localhost:11434` | Ollama endpoint                  |
| `OLLAMA_NUM_CTX`| `8192`                   | Context window                   |
| `TOP_K`         | `6`                      | Chunks retrieved per question    |
| `CHROMA_DIR`    | `./chroma_db`            | Index location                   |
| `SAFETY_CRAWLER_DIR` | `../safety-pdf-crawler` | Corpus location             |

Example: `set OLLAMA_MODEL=qwen2.5:7b-instruct` before `uvicorn`.

## Corpus triage (runs before OCR/chunking — the sieve)

Every new document is tiered *before* any expensive processing:

| Tier | Meaning | Treatment |
|---|---|---|
| 1 core_knowledge | incident/accident case studies, investigations | processed first, full OCR budget |
| 2 guidance | guidelines, standards, alerts | full processing |
| 3 reference | statistics, annual reports, forms, lists | indexed, OCR capped at 10 pages, demoted in retrieval |
| 4 scrap | org charts, directories, tenders/EOIs, awards, press releases | **quarantined — never indexed** |

Mechanics: cheap heuristics (filename, category, page-1 text) first; one small
LLM call only when unsure; scanned documents get a page-1-only OCR (via the
OCR cache) to earn or lose their full OCR pass. The LLM alone can never
quarantine a document — tier 4 requires a heuristic scrap signal, and anything
mentioning an incident is protected. All verdicts cached in
`triage_cache.json`; everything quarantined is listed in `ingest_report.json`.

Controls: `python ingest.py --triage-only` previews tiers with zero compute;
`--include-scrap` indexes tier-4 anyway (tagged + demoted); `--no-llm-triage`
for heuristics only; `triage_overrides.json` (`{"filename substring": tier}`)
overrides any verdict — the human always wins.

## Retrieval quality behaviors

- **Smart titles**: document titles are heading-scored (boilerplate and
  disclaimer lines rejected), with a small LLM fallback for low-confidence
  cases (`--no-llm-titles` to skip).
- **Doc-type awareness**: every chunk is tagged `case_study | investigation |
  guideline | form | statistics | other`. Form/template documents are demoted
  below content documents unless the question is explicitly about forms.
  Disclaimer pages are never indexed.
- **Category pinning**: questions that name a source ("...across the OISD case
  studies") are automatically scoped to those categories; the sources panel
  shows the scope. An explicit category selected in Settings always wins.

These are index-time features — after upgrading, re-ingest with
`python ingest.py --rebuild` (add `--ocr ...` if you use OCR).

## Retrieval accuracy modes

Selectable from the dropdown in the UI header (`mode` in the API). Each
tier is the local, fully-offline equivalent of a standard enterprise RAG
technique:

| Mode | Pipeline | Enterprise equivalent | Cost |
|---|---|---|---|
| Medium | vector search only | basic vector store RAG (Pinecone/pgvector demos) | fastest |
| Good (default) | vector + BM25 keyword search, fused with Reciprocal Rank Fusion | hybrid search in Azure AI Search, Elastic, OpenSearch, Weaviate | +~0s |
| Best | multi-query expansion (LLM) + hybrid per query + RRF + listwise LLM rerank | LangChain MultiQueryRetriever + Cohere Rerank / cross-encoder | +2 LLM calls |

Why it matters for this corpus: embeddings alone often miss exact tokens
like "OISD-STD-117", clause numbers, plant names. BM25 catches those;
RRF merges both rankings. "Best" additionally rewrites the question with
alternate terminology and has the LLM pick the truly relevant excerpts
from a larger candidate pool. All LLM-assisted steps fall back gracefully
to the simpler pipeline on any failure.

Note: the BM25 index is built in memory from ChromaDB on first request —
restart the server after re-running `ingest.py`.

## Incident analytics (deterministic)

After ingest, `python facts.py` extracts a structured record per incident
document (date, location, type, substances, casualties, root cause) into
`incidents.json` — plain JSON you should spot-check and may hand-correct;
the expert is the validator. Questions like *"what were the incidents in
2018?"*, *"how many pipeline fires?"*, *"incidents by year"* are then
answered by **filtering and counting that table — no LLM in the loop** —
with per-incident citations, honest coverage notes (undated records,
publication-date-only records), and a `GET /api/analytics` endpoint for raw
aggregates. Non-analytic questions fall through to normal RAG.

## Input guardrails (selectable ladder — Settings → Guardrails)

| Level | What runs | Cost | Catches |
|---|---|---|---|
| off | nothing | — | — |
| **L1** (default) | deterministic scope gate: weak retrieval + no domain vocabulary → fixed refusal, **no LLM call** | free | "best biryani in Hyderabad" |
| L2 | L1 + LLM intent classifier (in_scope / out_of_scope / injection / unsafe) | +1–3 s local | cleverly-phrased off-topic, prompt-injection attempts |
| L3 | L2 + Llama Guard 3 harm screen (`ollama pull llama-guard3:1b`) | +1–2 s | weapons/self-harm/illegal categories, topic-independent |

Output side (any level except off): a **citation-presence check** — a grounded
answer that cites nothing gets a visible warning appended. All LLM-based
guards **fail open** (a broken guardrail never breaks answering; the
deterministic L1 gate cannot fail). `GUARD_LEVEL` env sets the default;
the UI dropdown overrides per question. Refusal quality is measured by the
eval suite's out-of-corpus items (including the biryani question, verbatim).

## Verticals

Every question is routed (or pinned via Settings → Vertical):

- **incidents** — answered from the corpus only.
- **regulatory** — audit frequency / certification / rules questions also get a
  DuckDuckGo web-search assist biased to gov.in domains. Web results are cited
  as `[W#]`, labeled UNVERIFIED, and the answer always ends by pointing to the
  official regulator page. If the network blocks search, degrades to corpus-only.

## Drill mode (hypothetical incident generator)

The **Drill** tab generates fictional incident scenarios for monthly safety
meetings: corpus-grounded narrative, escalation injects, 5-6 discussion
questions with facilitator notes, `[n]` citations to the real incidents that
inspired each element, and a mandatory expert-validation checklist. Drafts are
explicitly labeled as requiring validation by a competent safety professional.

## UI

- `/` — React UI: dark/light theme, collapsible settings drawer, voice input
  (Web Speech API, en-IN/hi-IN) and read-aloud, Ask + Drill tabs.
  Loads React from cdnjs; if your network blocks CDNs, use the fallback below.
- `/classic.html` — zero-dependency vanilla UI, fully offline.

## API

- `POST /api/chat` — `{message, history[], category?, k?, mode?, vertical?}` → SSE stream
  (`sources` event first — includes `pipeline`, `vertical`, corpus `items` and `web` results — then `token` events, then `done`)
- `POST /api/scenario` — `{theme, unit_type, difficulty, category?}` → SSE stream (same event shape)
- `GET /api/health` — Ollama reachability, model availability, index size
- `GET /api/stats` — document/category counts for the UI

## Test questions

- What are the most common root causes across the OISD case studies?
- Summarize lessons learned from PNGRB incident investigations.
- What recurring lapses appear in CNG / city-gas incidents?

## Troubleshooting

| Symptom | Fix |
|---|---|
| Status: "Ollama not running" | Start Ollama (`ollama serve` or the desktop app) |
| Status: "model missing" | `ollama pull llama3.1:8b` (or set `OLLAMA_MODEL`) |
| Status: "embed model missing" / ingest says backend not ready | `ollama pull nomic-embed-text` |
| Status: "index empty" | Run `python ingest.py` |
| Answers say corpus doesn't cover it | Check the category filter; corpus may genuinely lack it — crawl more seeds |
| Slow first answer | Model loading into memory; subsequent answers are faster |

## Next-version infrastructure (built, awaiting UI + field verification)

**Drill engine v2** (`drills.py`): structured JSON scenario specs grounded in
corpus excerpts *and* real-incident anchors from `incidents.json`; a
generate → self-critique → refine pipeline scored against the training-value
rubric; and a drill library (`drill_library/`) with an expert validation
workflow (draft → validated/rejected + reviewer notes). Endpoints:
`POST /api/drill/create` (SSE), `GET /api/drills`, `GET /api/drills/{id}`,
`POST /api/drills/{id}/status`. Only validated drills belong in a meeting.

**Permit vertical** (`permits.py`): Indian PTW JSON schema; dummy-permit
generator for demos (`POST /api/permit/dummy`); and the incident-informed
analyzer (`POST /api/permit/analyze`) that combines corpus retrieval,
structured incident anchors, and plant-topology proximity into role-specific
checklist questions, stop-work triggers, and often-missed checks — with the
deterministic evidence returned alongside so every question shows its "why".
Plant topology lives in `plant_model.json` (see `.example`; hand-editable
now, target format for future GA-drawing/P&ID extraction). `GET /api/plant`
shows what's loaded. Decision support only — the permit issuer stays
responsible.

Quick smoke test once the server is up:

```bat
curl -X POST localhost:8000/api/permit/dummy -H "Content-Type: application/json" -d "{\"permit_type\":\"hot_work\"}"
:: paste the returned JSON into:
curl -X POST localhost:8000/api/permit/analyze -H "Content-Type: application/json" -d "{\"permit\": <that json>}"
```

## Safety posture

The system prompt restricts answers to retrieved excerpts, requires
`[n]` citations, forbids invented clause numbers/figures, and the UI
flags low-confidence retrieval. This is informational tooling — not a
substitute for the original standards or a competent authority.

## Roadmap

Shipped since the first build: OCR for scanned PDFs, hybrid BM25 + vector
retrieval, LLM reranker, the golden-set eval harness, guardrail ladder,
multi-provider model registry, and the Colab deployment path (all documented
above). Still ahead: Docker packaging, auto-ingest triggered by the crawler,
GA-drawing / P&ID extraction into `plant_model.json`, and a validated public
drill library.
