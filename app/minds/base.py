"""The contract every mind version implements, and the version-agnostic state summary.

The world never reaches inside a mind. It only:
    * creates a resident from a creation spec (a biography, or explicit fields + sliders),
    * hands it one experience as plain text and gets back who they are now (+ what they said),
    * reads its state.

``MindState`` is the small, stable shape the planner, relationships and UI read; each
version maps its own rich snapshot onto it (and the raw snapshot rides along for the
inspector). A future MindForm v1 only has to fill these same fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Turn:
    """What one experience did to a resident."""
    state: dict                      # MindState-shaped summary (see summarize_* helpers)
    reply: str | None = None         # what they said / thought, in their own formed voice
    appraisal: dict | None = None    # how they read it (valence, intensity, social, ...)
    formation: dict | None = None    # the biggest change this experience made, if any
    raw: dict | None = None          # the version's full snapshot (inspector only)


@dataclass
class Created:
    ref: str                         # the mind's own key for this resident (v0: roster name)
    name: str
    identity: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    raw: dict | None = None
    via: str | None = None           # e.g. "llm" / "heuristic" / "manual"


class Mind(Protocol):
    version: str

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def create(self, spec: dict) -> Created: ...
    def experience(self, ref: str, text: str) -> Turn: ...
    def state(self, ref: str) -> dict: ...   # raw snapshot


class MindError(RuntimeError):
    """A mind could not do what was asked (process down, bad spec, ...)."""


def blank_state() -> dict[str, Any]:
    return {
        "traits": [], "dominant": None, "needs": [], "top_need": None,
        "stance": {"mode": "steady", "line": "steady", "tendency": 0.0},
        "esteem": 0.0, "values": [], "top_value": None, "beliefs": [], "habits": [],
        "voice": "", "lens": "", "turn": 0, "sources": {},
    }


def summarize_v0(snap: dict) -> dict:
    """Map a MindForm v0 cockpit snapshot onto the version-agnostic MindState."""
    state = blank_state()
    traits = snap.get("traits") or []
    state["traits"] = [{k: t.get(k) for k in ("key", "name", "value", "base", "low", "high", "glyph")}
                       for t in traits]
    state["dominant"] = snap.get("dominant")
    state["needs"] = [{"key": d.get("key"), "name": d.get("name"),
                       "tension": float(d.get("tension", 0.0)), "weight": float(d.get("weight", 0.0))}
                      for d in (snap.get("drives") or [])]
    state["top_need"] = snap.get("drive")
    behavior = snap.get("behavior") or {}
    stance = behavior.get("set") or {}
    state["stance"] = {"mode": stance.get("mode", "steady"), "line": behavior.get("line", "steady"),
                       "tendency": float(stance.get("tendency", 0.0))}
    self_row = snap.get("self") or {}
    state["esteem"] = float(self_row.get("esteem", 0.0))
    character = snap.get("character") or {}
    values = sorted(character.get("values") or [], key=lambda v: -float(v.get("value", 0.0)))
    state["values"] = [{"key": v.get("key"), "label": v.get("label"), "value": float(v.get("value", 0.0))}
                       for v in values]
    state["top_value"] = character.get("dominant")
    state["beliefs"] = [{"statement": b.get("statement", ""), "confidence": float(b.get("confidence", 0.0))}
                        for b in (character.get("beliefs") or [])[:5]]
    state["habits"] = list(character.get("habits") or [])[:5]
    state["voice"] = (snap.get("expression") or {}).get("line") or ""
    state["lens"] = snap.get("lens") or ""
    state["turn"] = int(snap.get("turn") or 0)
    state["sources"] = {"appraisal": snap.get("appraisal_source"), "push": snap.get("source"),
                        "reply": (snap.get("expression") or {}).get("source")}
    return state


def describe_state(state: dict) -> str:
    """Plain-language inner state, for the planner prompt and the inspector headline."""
    if not state or not state.get("traits"):
        return "No formed inner state (this resident has no MindForm mind)."
    lines = []
    leaning = [f"{t['glyph']} ({t['value']:+.2f})" for t in state["traits"] if abs(t.get("value") or 0) >= 0.12]
    lines.append("Temperament now: " + (", ".join(leaning) if leaning else "fairly balanced on every trait"))
    needs = sorted(state.get("needs") or [], key=lambda n: -n["tension"])
    if needs:
        lines.append("Needs (0 = met, 1 = starved): " + ", ".join(f"{n['name']} {n['tension']:.2f}" for n in needs))
    stance = state.get("stance") or {}
    lines.append(f"Behavioral stance: {stance.get('line', 'steady')}")
    lines.append(f"Self-esteem: {state.get('esteem', 0.0):+.2f}")
    top_values = [v["label"] for v in (state.get("values") or []) if v["value"] > 0.05][:3]
    if top_values:
        lines.append("Cares most about: " + ", ".join(top_values))
    if state.get("beliefs"):
        lines.append("Beliefs they hold: " + "; ".join(b["statement"] for b in state["beliefs"][:3]))
    if state.get("voice"):
        lines.append(f"Manner of speaking: {state['voice']}")
    if state.get("lens"):
        lines.append(f"How they read situations: {state['lens']}")
    return "\n".join(lines)
