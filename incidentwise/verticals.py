"""Query verticals: incident knowledge (corpus) vs regulatory/policy (corpus + web).

Design decisions, stated bluntly:
- The classifier is a deterministic keyword heuristic, not an LLM call.
  At this stage an LLM router adds latency and nondeterminism for ~zero
  accuracy gain; revisit only when real query logs show misroutes.
- Web search is a FALLBACK for regulatory questions (audit frequency,
  certification, license renewals, statutory requirements) that a static
  incident corpus will never answer well. Results are clearly labeled
  UNVERIFIED and cited as [W#]; the model is instructed to tell the user
  to confirm on the official regulator page.
- Search uses DuckDuckGo (no API key). If the network blocks it, the
  request degrades gracefully to corpus-only with a note - never an error.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

VERTICALS = ("auto", "incidents", "regulatory")

# Signals that a question is about rules/compliance rather than incident history.
_REGULATORY_TERMS = re.compile(
    r"\b(how\s+often|how\s+frequently|audit\w*|certif\w*|licen[cs]\w*|renew\w*|"
    r"statut\w*|complian\w*|"
    r"regulat\w*|rule|rules|act|amendment|notification|gazette|mandat\w*|"
    r"frequency|interval|periodic\w*|validity|approval|noc|permission|"
    r"schedule\s*m|factories\s*act|psv\s*testing\s*frequency|calibration\s+frequency|"
    r"peso|ccoe|smpv|dish\b|dgfasli|moefcc|cpcb|spcb|form\s+\d+)\b",
    re.IGNORECASE,
)
# Signals for incident/history questions (kept for tie-breaking clarity).
_INCIDENT_TERMS = re.compile(
    r"\b(incident|accident|blast|explosion|fire|leak\w*|spill\w*|fatalit\w*|"
    r"injur\w*|near\s*miss|root\s*cause|investigation|case\s*stud\w*|lesson\w*)\b",
    re.IGNORECASE,
)


# Domain vocabulary: presence of ANY of these keeps a weak-retrieval query
# inside scope (proceeds to the LLM with honesty rules) instead of being
# deterministically refused by the scope gate.
_DOMAIN_TERMS = re.compile(
    r"\b(safety|incident|accident|hazard\w*|risk|fire|explosion|blast|leak\w*|"
    r"spill\w*|toxic|flammab\w*|h2s|hydrocarbon|lpg|lng|cng|png|gas|pipeline|"
    r"refinery|plant|unit|process|permit|ptw|blind\w*|isolat\w*|confined|"
    r"scaffold\w*|excavat\w*|hot\s*work|cold\s*work|welding|loto|lockout|"
    r"exchanger|pump|compressor|column|vessel|tank|valve|flange|drill|near\s*miss|"
    r"oisd|pngrb|peso|ndma|dish|dgfasli|hse|ehs|psm|hazop|lopa|sop|mock\s*drill|"
    r"emergency|evacuat\w*|ppe|audit\w*|inspect\w*|standard|guideline|regulat\w*)\b",
    re.IGNORECASE,
)


def in_domain(question: str) -> bool:
    """Deterministic scope signal for the input gate."""
    return bool(_DOMAIN_TERMS.search(question))


def classify(question: str) -> str:
    """'regulatory' or 'incidents'. Deterministic, instant, explainable."""
    reg = len(_REGULATORY_TERMS.findall(question))
    inc = len(_INCIDENT_TERMS.findall(question))
    return "regulatory" if reg > inc else "incidents"


def web_search(question: str, max_results: int = 5) -> list[dict]:
    """Regulator-biased web search. Returns [] on any failure (graceful)."""
    try:
        try:
            from ddgs import DDGS  # current package name
        except ImportError:
            from duckduckgo_search import DDGS  # legacy package name

        query = f"{question} India process safety regulation"
        out: list[dict] = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, region="in-en", max_results=max_results * 2):
                url = r.get("href") or r.get("url") or ""
                title = r.get("title") or ""
                snippet = (r.get("body") or "")[:400]
                if not url or not title:
                    continue
                out.append({"title": title[:160], "url": url, "snippet": snippet,
                            "official": ".gov.in" in url or ".nic.in" in url})
                if len(out) >= max_results * 2:
                    break
        # Official government sources first, then the rest.
        out.sort(key=lambda r: not r["official"])
        return out[:max_results]
    except Exception as exc:  # noqa: BLE001
        logger.warning("web search unavailable: %s", exc)
        return []
