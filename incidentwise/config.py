"""Central configuration for incidentwise (paths + env vars + .env file)."""
from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Real environment variables win
    over .env values, so `set OCR_BACKEND=...` still overrides the file."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(APP_DIR / ".env")

# --- Corpus produced by safety-pdf-crawler (sibling project) -----------------
CRAWLER_DIR = Path(os.environ.get(
    "SAFETY_CRAWLER_DIR", APP_DIR.parent / "safety-pdf-crawler"
))
RAW_PDF_DIR = CRAWLER_DIR / "data" / "raw_pdfs"
MANIFEST_JSONL = CRAWLER_DIR / "data" / "manifests" / "pdf_manifest.jsonl"

# --- Vector store -------------------------------------------------------------
CHROMA_DIR = Path(os.environ.get("CHROMA_DIR", APP_DIR / "chroma_db"))
COLLECTION_NAME = "safety_docs"
INGEST_REPORT = APP_DIR / "ingest_report.json"

# --- LLM (Ollama) -------------------------------------------------------------
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))

# --- OCR fallback for scanned pages (see ocr.py) -------------------------------
OCR_BACKEND = os.environ.get("OCR_BACKEND", "off")          # off | ollama | openai
OLLAMA_VLM_MODEL = os.environ.get("OLLAMA_VLM_MODEL", "qwen3-vl:8b")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_VLM_MODEL = os.environ.get("OPENAI_VLM_MODEL", "gpt-4o-mini")
OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")
XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
XAI_CHAT_MODEL = os.environ.get("XAI_CHAT_MODEL", "grok-3-mini")
# Groq = fast hosted inference for open models (Llama/Gemma/Qwen), NOT xAI's Grok.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_CHAT_MODEL = os.environ.get("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile")


def _csv(name: str, default: str) -> list[str]:
    return [s.strip() for s in os.environ.get(name, default).split(",") if s.strip()]


# OpenAI-compatible API providers. A provider appears in the UI model
# dropdown only when its key is present. Add more by extending this dict
# (any OpenAI-compatible endpoint works).
PROVIDERS = {
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1",
               "api_key": OPENAI_API_KEY, "default_model": OPENAI_CHAT_MODEL,
               "models": _csv("OPENAI_MODELS",
                              f"{OPENAI_CHAT_MODEL},gpt-5.4-mini,gpt-5.5")},
    "xai": {"label": "xAI", "base_url": "https://api.x.ai/v1",
            "api_key": XAI_API_KEY, "default_model": XAI_CHAT_MODEL,
            "models": _csv("XAI_MODELS", XAI_CHAT_MODEL)},
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1",
             "api_key": GROQ_API_KEY, "default_model": GROQ_CHAT_MODEL,
             "models": _csv("GROQ_MODELS",
                            f"{GROQ_CHAT_MODEL},llama-3.1-8b-instant,gemma2-9b-it")},
}

# Answer-generation backend: "ollama" (default, fully local) or any key of
# PROVIDERS. Embeddings and retrieval STILL run through Ollama regardless.
CHAT_BACKEND = os.environ.get("CHAT_BACKEND", "ollama")
OCR_MAX_PAGES = int(os.environ.get("OCR_MAX_PAGES", "40"))  # per document
OCR_DPI = int(os.environ.get("OCR_DPI", "150"))
OCR_CACHE_DIR = Path(os.environ.get("OCR_CACHE_DIR", APP_DIR / "ocr_cache"))

# --- Guardrail ladder --------------------------------------------------------------
# off | l1 (deterministic scope gate) | l2 (+ LLM intent classifier) |
# l3 (+ Llama Guard harm screen). UI dropdown overrides per question.
# Legacy SCOPE_GATE honored: hard->l1, soft/off->off.
_legacy = os.environ.get("SCOPE_GATE", "")
GUARD_LEVEL = os.environ.get(
    "GUARD_LEVEL", "l1" if _legacy in ("", "hard") else "off").lower()
LLAMA_GUARD_MODEL = os.environ.get("LLAMA_GUARD_MODEL", "llama-guard3:1b")

# --- Retrieval ----------------------------------------------------------------
TOP_K = int(os.environ.get("TOP_K", "6"))
# Cosine distance above which retrieval is considered weak (0 = identical).
# Used for the "low confidence" banner shown to the user.
WEAK_DISTANCE = float(os.environ.get("WEAK_DISTANCE", "0.65"))
# Separate, TIGHTER threshold for the deterministic scope gate. Embeddings are
# generous: an off-topic query ("best biryani") still retrieves chunks at
# distances under WEAK_DISTANCE, so gating on `weak` alone meant the gate never
# fired. The gate now requires: no domain vocabulary AND best distance above this.
GATE_DISTANCE = float(os.environ.get("GATE_DISTANCE", "0.45"))


def pretty_category(folder_name: str) -> str:
    """'oisd-case-studies' -> 'OISD Case Studies'."""
    words = folder_name.replace("_", "-").split("-")
    out = []
    for w in words:
        out.append(w.upper() if w.lower() in {"oisd", "pngrb", "ndma", "dgfasli", "pesos", "peso"} else w.capitalize())
    return " ".join(out)
