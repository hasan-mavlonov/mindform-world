"""World configuration: paths, the world's own LLM, and where MindForm versions live.

Settings resolve in this order (first non-empty wins):
    1. the real environment
    2. this repo's ``.env``
    3. for the LLM key/base/model only: the MindForm v0 checkout's ``.env`` -- so a key you
       already set for MindForm also powers the world's planner/narrator without copying it.

Everything is read lazily (functions, not module constants) so tests can change the
environment without re-importing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MODEL = "gemini-3.5-flash"


def parse_dotenv(path: Path) -> dict[str, str]:
    """KEY=VALUE lines -> dict (comments and blanks skipped, quotes stripped). Missing file -> {}."""
    out: dict[str, str] = {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return out
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _world_env() -> dict[str, str]:
    return parse_dotenv(ROOT / ".env")


def setting(*names: str, default: str | None = None, extra: dict[str, str] | None = None) -> str | None:
    """First non-empty value among ``names`` in env, then the world .env, then ``extra``."""
    sources = [os.environ, _world_env(), extra or {}]
    for source in sources:
        for name in names:
            value = source.get(name)
            if value:
                return value
    return default


def data_dir() -> Path:
    return Path(setting("MINDFORM_WORLD_DATA", default=str(ROOT / "data")))


def worlds_dir() -> Path:
    return data_dir() / "worlds"


# --- MindForm v0 -------------------------------------------------------------------
_V0_CANDIDATES = ("mindform_v0", "mindform-v0", "MindForm_v0")


def v0_path() -> Path | None:
    """The MindForm v0 checkout: MINDFORM_V0_PATH, else a sibling folder of this repo."""
    explicit = setting("MINDFORM_V0_PATH")
    candidates = [Path(explicit).expanduser()] if explicit else [ROOT.parent / n for n in _V0_CANDIDATES]
    for path in candidates:
        if (path / "console.py").is_file() and (path / "web" / "engine_bridge.py").is_file():
            return path.resolve()
    return None


def v0_python(path: Path) -> str:
    """The interpreter that runs v0: MINDFORM_V0_PYTHON, else v0's own venv, else ours."""
    explicit = setting("MINDFORM_V0_PYTHON")
    if explicit:
        return explicit
    for venv in (".venv", "venv", "env"):
        for exe in ("bin/python", "Scripts/python.exe"):
            candidate = path / venv / exe
            if candidate.exists():
                return str(candidate)
    return sys.executable


def v0_env() -> dict[str, str]:
    path = v0_path()
    return parse_dotenv(path / ".env") if path else {}


# --- The world's own LLM (planner / narrator / director) ----------------------------
def llm_settings() -> dict[str, str | None]:
    v0 = v0_env()
    key = setting("WORLD_LLM_API_KEY", "LLM_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY", extra=v0)
    base = setting("WORLD_LLM_BASE_URL", "LLM_BASE_URL", "DEEPSEEK_BASE_URL", extra=v0,
                   default=GEMINI_OPENAI_BASE)
    model = setting("WORLD_LLM_MODEL", "LLM_MODEL", "DEEPSEEK_MODEL", extra=v0, default=DEFAULT_MODEL)
    return {"api_key": key, "base_url": base, "model": model}
