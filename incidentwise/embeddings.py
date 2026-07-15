"""Local embeddings via Ollama — no external CDN or HuggingFace downloads.

We compute vectors ourselves and hand them to ChromaDB explicitly
(`embeddings=` / `query_embeddings=`), so Chroma never tries to fetch its
default ONNX model from the internet.
"""
from __future__ import annotations

import httpx

from config import OLLAMA_EMBED_MODEL, OLLAMA_URL

_BATCH = 32
_IS_NOMIC = OLLAMA_EMBED_MODEL.startswith("nomic-embed")


def _prefix(texts: list[str], kind: str) -> list[str]:
    """nomic-embed models want task prefixes for best retrieval quality."""
    if not _IS_NOMIC:
        return list(texts)
    p = "search_document: " if kind == "document" else "search_query: "
    return [p + t for t in texts]


def embed_texts(texts: list[str], kind: str = "document") -> list[list[float]]:
    """Embed texts with Ollama. kind: 'document' (indexing) or 'query'."""
    if not texts:
        return []
    texts = _prefix(texts, kind)
    out: list[list[float]] = []
    # Generous read timeout: on CPU, Ollama swapping between the chat model
    # and the embed model can stall a batch well past 180s (seen in the wild
    # as a one-off "timed out" doc failure on a 300-chunk document).
    with httpx.Client(timeout=httpx.Timeout(10.0, read=420.0)) as client:
        for b in range(0, len(texts), _BATCH):
            batch = texts[b:b + _BATCH]
            r = client.post(f"{OLLAMA_URL}/api/embed",
                            json={"model": OLLAMA_EMBED_MODEL, "input": batch})
            if r.status_code == 404 and b"not found" in r.content.lower():
                raise RuntimeError(
                    f"Embedding model '{OLLAMA_EMBED_MODEL}' is not pulled. "
                    f"Run:  ollama pull {OLLAMA_EMBED_MODEL}"
                )
            if r.status_code == 404:
                # Very old Ollama without /api/embed: fall back to /api/embeddings.
                for t in batch:
                    r2 = client.post(f"{OLLAMA_URL}/api/embeddings",
                                     json={"model": OLLAMA_EMBED_MODEL, "prompt": t})
                    r2.raise_for_status()
                    out.append(r2.json()["embedding"])
                continue
            r.raise_for_status()
            data = r.json()
            if data.get("error"):
                raise RuntimeError(f"Ollama embed error: {data['error']}")
            out.extend(data["embeddings"])
    return out
