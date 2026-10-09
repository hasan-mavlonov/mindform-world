"""The end-of-day recap: "Today on Halcyon Isle" in three lines.

Through the day the world notes its dramatic moments as they happen (a secret worked out,
a confession, a walk-off, two people becoming friends or rivals, a big event, a trait
turning). At bedtime the three strongest become the recap card -- at most two about the same
resident, newest first among equals. With the LLM world brain the model may tighten the
wording; the facts are always these.
"""
from __future__ import annotations

import logging

from app import llm

log = logging.getLogger("mindform.world.recap")

RECAP_SYSTEM = """You write the end-of-day recap card for a short-video series about life on a small island.
Rewrite each HIGHLIGHT as one punchy line (max 12 words), present tense, same facts, same people.
No emojis, no hashtags. Return JSON only: {"bullets": ["...", "...", "..."]}"""


def pick(highlights: list[dict], n: int = 3) -> list[dict]:
    ranked = sorted(enumerate(highlights), key=lambda p: (-p[1]["drama"], -p[0]))
    out, per, seen = [], {}, set()
    for _, h in ranked:
        if h["text"] in seen:
            continue
        actors = h.get("actors") or []
        if any(per.get(a, 0) >= 2 for a in actors):
            continue
        out.append(h)
        seen.add(h["text"])
        for a in actors:
            per[a] = per.get(a, 0) + 1
        if len(out) >= n:
            break
    return out


def bullets(highlights: list[dict], *, use_llm: bool, stats: llm.Stats | None = None) -> list[str]:
    chosen = pick(highlights)
    lines = [h["text"] for h in chosen]
    if not lines:
        return ["A quiet day. Nobody said what they really meant."]
    if use_llm and llm.available():
        try:
            data = llm.complete_json(RECAP_SYSTEM, "HIGHLIGHTS:\n" + "\n".join(f"- {t}" for t in lines),
                                     temperature=0.7, max_tokens=400, stats=stats)
            out = [str(b).strip() for b in data.get("bullets") or [] if str(b).strip()]
            if len(out) == len(lines):
                return out
        except Exception as exc:
            log.info("recap fell back to the plain highlights: %s", exc)
    return lines
