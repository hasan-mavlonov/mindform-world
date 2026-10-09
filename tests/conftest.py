import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    """Tests never call a real model: blank every key the world (or v0's .env) could use."""
    for key in ("WORLD_LLM_API_KEY", "LLM_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.setenv(key, "")
    import app.config as config
    real = config.llm_settings

    def no_key():
        cfg = real()
        cfg["api_key"] = None
        return cfg

    monkeypatch.setattr(config, "llm_settings", no_key)
    import app.llm as llm
    monkeypatch.setattr(llm, "llm_settings", no_key)


def control_setup(**overrides):
    setup = {
        "name": "Test isle", "version": "control", "world_brain": "rules", "mind_llm": False, "seed": 11,
        "experiences_per_hour": 4, "intensity": 3,
        "characters": [
            {"mode": "manual", "identity": {"name": "Aya", "age": "27"},
             "levels": {"O": 4, "C": 3, "E": 5, "A": 5, "N": 4}, "job": "baker", "goal": "open a bakery"},
            {"mode": "manual", "identity": {"name": "Rex", "age": "34"},
             "levels": {"O": 2, "C": 4, "E": 2, "A": 1, "N": 2}, "job": "boatbuilder"},
            {"mode": "bio", "bio": "Mira, 29, a shy librarian from Lisbon.", "job": "librarian"},
        ],
    }
    setup.update(overrides)
    return setup


def v0_checkout():
    from app.config import v0_path
    return v0_path()


requires_v0 = pytest.mark.skipif(
    os.environ.get("SKIP_V0") == "1" or v0_checkout() is None,
    reason="MindForm v0 checkout not found (set MINDFORM_V0_PATH)")
