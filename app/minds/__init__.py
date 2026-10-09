"""Mind versions the world can host. Add a version = add an adapter + one registry row."""
from __future__ import annotations

from pathlib import Path

from app.minds import control, mindform_v0
from app.minds.base import Created, Mind, MindError, Turn, describe_state  # noqa: F401

_CONTROL_SCHEMA = {
    "identity_fields": [{"key": "name", "label": "Name"}, {"key": "age", "label": "Age"},
                        {"key": "origin", "label": "Where from"}],
    "trait_questions": mindform_v0._FALLBACK_SCHEMA["trait_questions"],
}


def versions() -> list[dict]:
    """Everything the setup screen shows, availability checked live."""
    v0_ok, v0_note = mindform_v0.available()
    return [
        {
            "id": mindform_v0.VERSION,
            "name": "MindForm v0",
            "tagline": "Personality formation engine -- ten faculties",
            "description": ("Residents are raised by MindForm v0: every experience forms their traits, "
                            "values, morals, beliefs, needs, self-image, voice and behavioral stance."),
            "available": v0_ok,
            "note": v0_note,
            "supports_llm": True,
            "creation_modes": ["bio", "manual"],
            "schema": mindform_v0.creation_schema() if v0_ok else mindform_v0._FALLBACK_SCHEMA,
        },
        {
            "id": control.VERSION,
            "name": "Control (no MindForm)",
            "tagline": "Baseline -- a fixed persona that never forms",
            "description": ("For experiments: the same world and planner, but residents are static "
                            "personas. Compare against a MindForm world to see what formation adds."),
            "available": True,
            "note": "",
            "supports_llm": True,
            "creation_modes": ["bio", "manual"],
            "schema": _CONTROL_SCHEMA,
        },
    ]


def make_mind(version: str, workdir: Path, use_llm: bool) -> Mind:
    if version == mindform_v0.VERSION:
        return mindform_v0.MindFormV0(workdir, use_llm)
    if version == control.VERSION:
        return control.ControlMind(workdir, use_llm)
    raise MindError(f"unknown mind version {version!r}")


_NAMES = {mindform_v0.VERSION: "MindForm v0", control.VERSION: "Control (no MindForm)"}


def version_name(version: str) -> str:
    return _NAMES.get(version, version)
