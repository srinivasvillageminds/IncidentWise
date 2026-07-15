# Publishing & commercialization structure

Blunt guiding principle: **open-source the corpus plumbing, keep the
workflow intelligence commercial.** The crawler, RAG stack, and drill
generator earn you credibility and testers on GitHub. The permit-checklist
copilot (SAP-adjacent, P&ID-aware, per-site data) is the thing companies
will actually pay for — it never goes public.

## Target repo layout (monorepo, rename on first push)

```
incidentwise/                      # public repo root
├── LICENSE                      # Apache-2.0 (enterprise-friendly, patent grant)
├── README.md                    # demo GIF at the top, 5-minute quickstart
├── crawler/                     # = safety-pdf-crawler (already clean)
│   └── seeds.example.yaml       # ship EXAMPLE seeds, not your full seed list
├── app/                         # = incidentwise (this folder)
│   ├── ingest.py  retrieval.py  rag.py  verticals.py  scenario.py  app.py
│   └── static/
├── evals/                       # REQUIRED before you publicize (see below)
│   ├── golden_qa.jsonl          # 30-50 question/answer/source triples
│   └── run_evals.py             # retrieval hit-rate + citation accuracy
└── docs/
    ├── architecture.md
    └── corpus.md                # how to rebuild the corpus yourself
```

## What must NOT go in the public repo

- **The PDFs themselves.** Government-published documents have murky
  redistribution status (Indian government works are copyrighted;
  s.52(1)(q) Copyright Act exemptions are narrow). Ship the crawler +
  manifest so anyone can rebuild the corpus in 20 minutes. You lose
  nothing and avoid the one legal question that could get the repo taken down.
- `chroma_db/`, `.venv/`, manifests with your crawl timestamps (optional,
  but a fresh `seeds.example.yaml` is cleaner).
- Anything from a future pilot site: P&IDs, permits, near-miss data.
  That data is radioactive — customer-confidential and often
  security-sensitive. Per-customer private deployments only.

## Publishing phases

## Publish gate — mechanical checklist (tools now exist)

1. `python evals/run_evals.py` → fix MISSes → `--answers` run → REPORT.md committed
2. `python evals/make_golden.py --n 30` → curate drafts → grow golden_qa.jsonl to 40+
3. From repo root: `git add -A` → review the tracked file list → commit.
4. Push the `publish` branch.

**Phase P0 — now (1 evening of cleanup).** Push crawler + app + README
with honest labeling: "research prototype, not safety advice." Add the
disclaimer to the README, not just the UI. MIT/Apache-2 license file.
Do not announce anywhere yet.

**Phase P1 — credibility gate (1-2 weekends).** Build `evals/` — 30-50
golden questions written by YOU (you know the corpus), measuring:
retrieval hit-rate@k, citation correctness (does [n] actually support the
claim), and refusal correctness on 10 out-of-corpus questions. Publish the
scores in the README. An AI safety tool with no eval numbers gets laughed
out of the room by exactly the engineers you want as testers. This is
also when Docling/LlamaParse replaces PyMuPDF (better tables) — do it
behind the same `ingest.py` interface.

**Phase P2 — announce.** LinkedIn (EHS community is very active there),
r/ChemicalEngineering, LlamaIndex/Ollama community showcases. The drill
generator is the demo that lands: "your monthly safety meeting, but the
case study is new every month and grounded in real Indian incidents."

**Phase P3 — commercial fork (only after a design partner exists).**
Private repo: permit parsing, near-miss ingestion, role-based checklist
generation, SAP PM / Permit-to-Work connectors. Open-core boundary:
public repo answers "what happened in industry"; private product answers
"what should MY unit check today."

## License choice, bluntly

Apache-2.0. MIT is fine too; GPL will scare off the industrial partners
you need, and "source-available" licenses (BSL etc.) are premature at
zero users. You are not yet defending anything worth defending.

## Naming note

"IncidentWise" is generic and collides with OpenAI's trademark ambitions.
Before the repo goes public, pick something ownable (e.g. anything
referencing barriers/layers-of-protection). Renaming after traction is
far more painful.
