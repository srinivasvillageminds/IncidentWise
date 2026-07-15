"""Hypothetical incident generator for safety-meeting drills.

Generates a plausible, corpus-grounded hypothetical incident scenario with
discussion questions and a facilitator key, plus an explicit EXPERT
VALIDATION checklist. The output is a draft for a competent safety
professional to validate - never a finished training artifact. That
human-in-the-loop step is non-negotiable and is baked into the output
format itself.
"""
from __future__ import annotations

import retrieval

SCENARIO_SYSTEM = """You are a process-safety training designer creating a HYPOTHETICAL incident scenario for a monthly plant safety meeting in India.

You are given numbered excerpts from REAL incident reports and guidelines. Ground your hypothetical in the failure modes, equipment, and circumstances they describe - cite them as [1], [2] wherever a scenario element is inspired by a real excerpt.

Hard rules:
1. The scenario must be clearly FICTIONAL: invent a generic plant/unit name; never use real company names, dates, or casualty figures from the excerpts.
2. Every major failure mechanism you use must be traceable to the excerpts via [n] citations. Do not invent physics or exotic failure modes.
3. Keep it locally relatable to Indian plant practice (permit-to-work, tool-box talk, shift handover, monsoon conditions where relevant). Personnel must reflect realistic staffing: night-shift operations crews are typically all-male; female characters only in realistically common roles (day-shift engineers, HSE officers, lab analysts).
4. This is a DRAFT for expert validation, not a finished artifact.

Output in markdown, exactly these sections:
## Scenario: <title>
**Setting** - unit type, time of day, operating condition (2-3 lines)
**Narrative** - 250-350 words, chronological, ending just after the initiating event escalates
**Escalation injects** - 2 optional twists the facilitator can add mid-discussion
## Discussion questions
5-6 numbered questions of increasing depth (detection -> response -> barriers -> systemic causes), each followed by *Facilitator notes:* 1-2 lines of expected answer grounded in the excerpts [n]
## Grounding
One line per excerpt used: [n] what was borrowed from it
## Expert validation checklist
5 checkboxes the reviewing expert must confirm before use (technical feasibility, local relatability, no real-incident identifiability, question quality, severity appropriateness)"""


def build_scenario_request(theme: str, unit_type: str, difficulty: str) -> tuple[str, str]:
    """Returns (retrieval_query, user_prompt)."""
    theme = (theme or "process safety incident").strip()[:200]
    unit_type = (unit_type or "process plant").strip()[:100]
    difficulty = difficulty if difficulty in ("basic", "intermediate", "advanced") else "intermediate"

    retrieval_query = f"{theme} incident {unit_type} causes failure"
    user_prompt = (
        f"Create one hypothetical scenario.\n"
        f"Theme: {theme}\n"
        f"Unit type: {unit_type}\n"
        f"Audience level: {difficulty} (operators and shift in-charges for basic; "
        f"include engineering/systemic depth for advanced)\n"
        f"Ground it in the excerpts and cite [n]."
    )
    return retrieval_query, user_prompt


async def get_seeds(theme: str, unit_type: str, category: str | None = None, k: int = 5) -> dict:
    query, _ = build_scenario_request(theme, unit_type, "intermediate")
    return await retrieval.retrieve(query, mode="good", k=k, category=category)


def build_messages(seeds: list[dict], theme: str, unit_type: str, difficulty: str) -> list[dict]:
    _, user_prompt = build_scenario_request(theme, unit_type, difficulty)
    blocks = []
    for h in seeds:
        blocks.append(f"[{h['n']}] {h['category']} - \"{h['title']}\", p.{h['page']}\n{h['text']}")
    context = "\n\n".join(blocks) if blocks else "(no excerpts available)"
    return [
        {"role": "system", "content": SCENARIO_SYSTEM},
        {"role": "user", "content": f"Real-incident excerpts:\n\n{context}\n\n---\n{user_prompt}"},
    ]
