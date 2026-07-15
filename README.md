# IncidentWise

Local, fully on-prem RAG assistant over Indian process-safety documents
(OISD & PNGRB case studies, incident investigations, guidelines, DISH and
NDMA material). Ollama for generation and embeddings, ChromaDB for
retrieval, FastAPI + React UI. Every answer cites the exact source
document and page via a crawl provenance manifest.

**Status: research prototype.** Numbers from the eval harness are in
`incidentwise/evals/REPORT.md` — if that file is missing, the maintainers
haven't earned your trust yet.

## What's inside

| Folder | What it is |
|---|---|
| `safety-pdf-crawler/` | Polite crawler that rebuilds the public corpus with full provenance (robots.txt-aware) |
| `incidentwise/` | Ingestion (with optional VLM OCR), tiered retrieval (vector / hybrid+RRF / rerank), vertical routing with regulatory web fallback, incident-drill generator, React UI |
| `incidentwise/evals/` | Golden-set eval harness: retrieval hit-rate, router accuracy, refusal & grounding checks |

## Architecture

### 1 — Corpus lifecycle: from government portals to a clean, structured knowledge base

```mermaid
flowchart TD
    SRC["Gov sources<br/>OISD · PNGRB · PESO · NDMA · DISH"]
    SRC -->|"monthly polite re-crawl<br/>robots.txt + delays"| CR["safety-pdf-crawler"]
    CR --> MAN[("Provenance manifest<br/>URL + SHA256 dedup<br/>only NEW/CHANGED files proceed")]
    MAN --> TRI{"Triage — tier before compute<br/>filename + page-1 signals,<br/>LLM only when unsure"}
    TRI -->|"Tier 4: org charts, tenders,<br/>directories, ads"| QUA["QUARANTINE<br/>listed in report for human review<br/>overrides file always wins"]
    TRI -->|"Tier 1 incident · 2 guidance · 3 reference"| FOR{"Text extraction +<br/>PDF forensics (deterministic)"}
    FOR -->|digital text| CH["Chunk ~1100 chars<br/>page-accurate"]
    FOR -->|"scan / broken fonts /<br/>vector text"| OCR["OCR (page-1 gate, tiered budget)<br/>local VLM: qwen3-vl / granite-vision<br/>or OpenAI gpt-4o-mini (public docs only)<br/>content-hash cache: pay once, ever"]
    OCR --> CH
    CH --> DB[("ChromaDB — local<br/>nomic embeddings via Ollama<br/>metadata: tier, doc_type, page,<br/>OCR provenance, source URL")]
    DB --> FX["facts.py — 1 extraction call/doc<br/>nulls when text is silent"]
    FX --> INC[("incidents.json<br/>structured · human-editable<br/>EXPERT SPOT-CHECK REQUIRED")]
```

### 2 — Question lifecycle: every answer type has its own guarantees

```mermaid
flowchart TD
    Q["Question"] --> RT{"Router"}
    RT -->|"count / year / trend"| AN["ANALYTICS<br/>filter + count incidents.json<br/>deterministic — no LLM touches numbers"]
    RT -->|"audit / licence / rule"| RG["REGULATORY<br/>corpus + gov.in-biased web search<br/>web cited as W#, labeled UNVERIFIED"]
    RT -->|"incident / default"| IC["INCIDENT CORPUS"]
    IC --> LV{"RAG level (user-selectable)"}
    LV -->|Medium| V1["vector top-k"]
    LV -->|"Good (default)"| V2["hybrid vector + BM25<br/>RRF fusion"]
    LV -->|Best| V3["multi-query + hybrid<br/>+ LLM listwise rerank"]
    V1 --> PP
    V2 --> PP
    V3 --> PP
    PP["Post-processing<br/>category pinning · form/tier demotion<br/>weak-retrieval flag"]
    PP --> GEN["Ollama chat model<br/>llama3.1:8b default — swappable"]
    RG --> GEN
    GEN --> ANS["Answer + source cards<br/>document · page · original gov URL<br/>voice in/out · dark/light UI"]
    AN --> ANS
```

### 3 — Generation side: drills, permits, and job-combination (SIMOPS) what-ifs

```mermaid
flowchart TD
    CO[("Corpus excerpts")] --> DG
    IA[("incidents.json anchors<br/>real years/types/causes")] --> DG
    TH["Theme"] --> DG["Drill engine v2<br/>structured JSON spec"]
    DG --> CQ["LLM self-critique<br/>6-axis training rubric"] --> RF["refine pass"] --> LIB[("drill_library/<br/>status: draft")]
    LIB --> EV{"EXPERT VALIDATION<br/>non-negotiable gate"}
    EV -->|validated| USE["Safety-meeting use"]
    EV -->|"rejected + notes"| LIB

    PM[("plant_model.json<br/>tags · hazards · distances<br/>target format for future<br/>GA-drawing / P&ID extraction")] --> PA
    PR["Permit"] --> PA["Permit analyzer<br/>corpus + anchors + proximity"]
    PA --> PO["Role-specific questions<br/>stop-work triggers · often-missed checks<br/>evidence shown for every question"]
    PR -->|"opt-in log"| JL[("jobs log<br/>rolling permit history")]
    JL --> SS["SIMOPS scan<br/>overlapping windows ×<br/>same tag / adjacent / same unit"]
    PM --> SS
    SS --> WS["What-could-have-happened<br/>combination scenarios<br/>REAL tags, REAL jobs"] --> LIB
```

### Guardrail map — where each safeguard lives and what it protects

| Stage | Guardrail | Protects against |
|---|---|---|
| Crawl | robots.txt + delays + manifest provenance | legal exposure; unverifiable corpus |
| Triage | conservative quarantine — LLM alone can never condemn; human overrides file | losing one real incident report (costs more than indexing ten org charts) |
| Forensics | measured verdicts, not "scanned?" guesses | silent data loss from mislabeled PDFs |
| OCR | page-1 gate, tiered budgets, circuit breaker, content-hash cache | runaway compute/cost; repeated spend |
| Facts | extract-only-what's-stated, nulls, confidence, human-editable table | fabricated dates/casualties in analytics |
| Analytics | zero LLM in the counting path; coverage + date-source disclosure | hallucinated numbers with confident tone |
| Retrieval | weak-retrieval flag, form/tier demotion, category pinning | confidently answering from the wrong documents |
| Generation (chat) | context-only prompt, mandatory [n] citations, refuse when absent, never invent clause numbers | plausible-but-wrong safety advice |
| Regulatory web | UNVERIFIED labels, W# citations, "confirm on official page" closer | mistaking search snippets for law |
| Drills / SIMOPS | grounding numbers per mechanism, validation checklist, draft→expert-validated workflow | fiction masquerading as training truth |
| Permits | deterministic evidence returned beside every LLM output; issuer-responsibility disclaimer | automation complacency |
| Evals | golden set, refusal + grounding judges, publish gate | shipping unmeasured accuracy claims |

## Quickstart

Run it one of two ways:

- **Local, fully offline** — Ollama + two model pulls + `python ingest.py` + `uvicorn`. Full steps in `incidentwise/README.md`.
- **Free Colab GPU (shareable public link)** — one notebook cell restores state from your Google Drive, starts Ollama, and opens a `*.trycloudflare.com` URL for demos. Full steps in `COLAB_DEMO.md`.

1. Crawl (or bring your own PDFs): see `safety-pdf-crawler/`
2. Run the app — locally (`incidentwise/README.md`) or on a Colab GPU (`COLAB_DEMO.md`)
3. Run the evals: `python evals/run_evals.py` from `incidentwise/`

## Disclaimer — read this

This software provides **informational assistance only**. It is not a
substitute for the original standards, statutory requirements, a
competent safety professional, or your site's management-of-change and
permit-to-work systems. Generated drill scenarios are drafts that
**require validation by a qualified expert** before any use. Answers can
be wrong; citations exist precisely so you can check them. Do not make
safety-critical decisions on the basis of this tool's output.

The corpus PDFs are not redistributed in this repository; the crawler
fetches them from the original public government sources.

## License

Apache-2.0 — see [LICENSE](LICENSE).
