"""Deterministic analytics over incidents.json.

Count/list/trend questions are answered by FILTERING AND COUNTING the
structured incident table - no LLM anywhere in this path, which is the
only honest way to promise "near perfection" on numbers. The LLM's only
loss is prose polish; the gain is that a number in the answer is a number
in the table, always.

Falls through to normal RAG when the question isn't analytic or the
table doesn't exist yet (run facts.py after ingest).
"""
from __future__ import annotations

import json
import re
from collections import Counter

from config import APP_DIR

INCIDENTS_PATH = APP_DIR / "incidents.json"

_INTENT = re.compile(
    r"\bhow many\b|\bcount\b|\blist (of |all )?incident|\bincidents? (in|during|of|from) "
    r"(19|20)\d{2}|\bby year\b|\bper year\b|\byear.?wise\b|\btrend\b|\bstatistics\b|"
    r"\bbreakdown\b|\btotal (number|fatalit|injur|incident)|\bwhich incidents\b|"
    r"\bhow often\b.*\bincident", re.IGNORECASE)
_YEAR = re.compile(r"(?<!\d)(19[89]\d|20[0-3]\d)(?!\d)")
_TYPE_PATTERNS = [
    (re.compile(r"\bexplosion|blast\b", re.I), "explosion"),
    (re.compile(r"\bfire\b", re.I), "fire"),
    (re.compile(r"\bleak", re.I), "gas leak"),
    (re.compile(r"\btoxic|h2s|hydrogen sulphide|poison", re.I), "toxic release"),
    (re.compile(r"\bnear miss", re.I), "near miss"),
    (re.compile(r"\bfatal|death|died|casualt", re.I), "fatality"),
]


def available() -> bool:
    return INCIDENTS_PATH.exists()


def _load() -> list[dict]:
    try:
        return json.loads(INCIDENTS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


# Lookup markers: the user wants ONE incident's detail, not a count. These beat
# the aggregate heuristic ("2018 incident" + "tell me more" is a lookup, and
# answering it with a year-table is a routing failure, not an answer).
_LOOKUP = re.compile(
    r"\bmore (info|details|about)|\btell me about|\bdetails? (of|on|about)|"
    r"\bsequence of events|\bwhat happened (in|at|during|on)|\belaborate|"
    r"\bexplain the|\broot cause of|\bthis incident|\bthat incident|"
    r"\bfirst (one|incident|run)|\blast (one|incident)|\babove\b", re.IGNORECASE)
# A full date (28.09.2020 / 28-09-2020 / 2020-09-28) means a specific event.
_FULL_DATE = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")


def detect(question: str) -> bool:
    if _LOOKUP.search(question) or _FULL_DATE.search(question):
        return False        # specific-incident lookup -> RAG, not counting
    return bool(_INTENT.search(question)) or bool(
        re.search(r"\bincidents?\b", question, re.I) and _YEAR.search(question))


def _parse_filters(question: str) -> dict:
    years = sorted({int(y) for y in _YEAR.findall(question)})
    if len(years) == 2 and re.search(r"\b(between|from|to|and|-)\b", question, re.I):
        years = list(range(years[0], years[1] + 1))
    types = [label for pat, label in _TYPE_PATTERNS if pat.search(question)]
    return {"years": years, "types": types}


def _match(r: dict, f: dict) -> bool:
    if f["years"] and (r.get("year") not in f["years"]):
        return False
    if f["types"]:
        rtype = (r.get("incident_type") or "").lower()
        fatal_wanted = "fatality" in f["types"]
        type_ok = any(t != "fatality" and t in rtype for t in f["types"])
        fatal_ok = fatal_wanted and ((r.get("fatalities") or 0) > 0 or "fatal" in rtype)
        if not (type_ok or fatal_ok):
            return False
    return True


def _fmt_int(v) -> str:
    return str(int(v)) if isinstance(v, (int, float)) and v is not None else "–"


def answer(question: str, max_rows: int = 25) -> dict | None:
    """Computed markdown answer + doc-level sources, or None to fall back to RAG."""
    records = _load()
    if not records:
        return None
    f = _parse_filters(question)
    matched = [r for r in records if _match(r, f)]
    matched.sort(key=lambda r: (r.get("year") or 0, r.get("incident_date") or ""))

    known_years = sorted({r["year"] for r in records if r.get("year")})
    undated = sum(1 for r in records if not r.get("year"))
    pub_dated = sum(1 for r in matched if r.get("date_source") == "filename_publication")

    lines: list[str] = []
    scope = []
    if f["years"]:
        scope.append("year " + (str(f["years"][0]) if len(f["years"]) == 1
                                else f"{f['years'][0]}–{f['years'][-1]}"))
    if f["types"]:
        scope.append("type: " + ", ".join(f["types"]))
    lines.append(f"**{len(matched)} incident record(s)** in the corpus database"
                 + (f" for {'; '.join(scope)}" if scope else "") + ".")
    lines.append("")

    if matched:
        lines.append("| # | Year | Type | Location | Facility | Fatalities | Injuries | What happened |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for n, r in enumerate(matched[:max_rows], 1):
            lines.append(
                f"| [{n}] | {r.get('year') or '–'} | {r.get('incident_type') or '–'} "
                f"| {r.get('location') or '–'} | {r.get('facility_type') or '–'} "
                f"| {_fmt_int(r.get('fatalities'))} | {_fmt_int(r.get('injuries'))} "
                f"| {(r.get('summary') or '')[:90]} |")
        if len(matched) > max_rows:
            lines.append(f"\n*…and {len(matched) - max_rows} more (see sources).*")
        lines.append("")
        by_year = Counter(r.get("year") or 0 for r in matched)
        by_type = Counter((r.get("incident_type") or "other") for r in matched)
        fat = sum(r.get("fatalities") or 0 for r in matched)
        inj = sum(r.get("injuries") or 0 for r in matched)
        lines.append("**Breakdown** — by year: "
                     + ", ".join(f"{y or 'undated'}: {c}" for y, c in sorted(by_year.items()))
                     + " · by type: "
                     + ", ".join(f"{t}: {c}" for t, c in by_type.most_common())
                     + f" · total fatalities: {fat}, injuries: {inj} (where stated).")
        causes = Counter((r.get("root_cause") or "").lower() for r in matched
                         if r.get("root_cause"))
        if causes:
            lines.append("**Root causes recorded:** "
                         + "; ".join(f"{c} ({n})" for c, n in causes.most_common(8)) + ".")
    lines.append("")
    lines.append(f"*Database coverage: {len(records)} incident records; years "
                 f"{known_years[0]}–{known_years[-1]}" if known_years else
                 f"*Database coverage: {len(records)} incident records; no dated records"
                 )
    lines[-1] += (f"; {undated} undated."
                  + (f" {pub_dated} matched record(s) are dated by report publication, "
                     f"not incident date." if pub_dated else "")
                  + " Computed deterministically from expert-editable incidents.json — "
                    "no language model produced these numbers.*")

    sources = [{
        "n": n, "title": r["title"], "category": r["category"],
        "doc_type": r.get("doc_type", ""), "page": 1,
        "pages_total": r.get("pages_total", 0), "file": r["file"],
        "doc_url": r.get("doc_url", ""), "source_url": r.get("source_url", ""),
        "distance": None, "snippet": (r.get("summary") or "")[:280],
    } for n, r in enumerate(matched[:max_rows], 1)]

    return {"markdown": "\n".join(lines), "sources": sources,
            "matched": len(matched), "filters": f}


def aggregates() -> dict:
    records = _load()
    doc_dated = [r for r in records if r.get("date_source") == "document_text"]
    pub_dated = [r for r in records if r.get("date_source") == "filename_publication"]
    return {
        "records": len(records),
        "by_year": dict(sorted(Counter(r.get("year") or 0 for r in records).items())),
        # Split so the dashboard can never imply a publication year is an incident year.
        "by_year_incident_dated": dict(sorted(
            Counter(r.get("year") or 0 for r in doc_dated).items())),
        "by_year_publication_dated": dict(sorted(
            Counter(r.get("year") or 0 for r in pub_dated).items())),
        "n_incident_dated": len(doc_dated),
        "n_publication_dated": len(pub_dated),
        "n_undated": len(records) - len(doc_dated) - len(pub_dated),
        "by_type": dict(Counter((r.get("incident_type") or "other")
                                for r in records).most_common()),
        "total_fatalities": sum(r.get("fatalities") or 0 for r in records),
        "total_injuries": sum(r.get("injuries") or 0 for r in records),
    }
