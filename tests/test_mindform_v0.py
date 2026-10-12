"""Integration with a real MindForm v0 checkout (offline mode). Skipped when v0 isn't found."""
import json

import pytest

from app.minds import make_mind, versions
from app.world.manager import WorldManager
from conftest import control_setup, requires_v0

pytestmark = requires_v0


def test_v0_is_listed_with_its_own_creation_form():
    v0 = next(v for v in versions() if v["id"] == "mindform_v0")
    assert v0["available"]
    keys = [f["key"] for f in v0["schema"]["identity_fields"]]
    assert keys[0] == "name" and len(v0["schema"]["trait_questions"]) == 5


def test_v0_adapter_creates_and_forms(tmp_path):
    mind = make_mind("mindform_v0", tmp_path / "minds", use_llm=False)
    mind.start()
    try:
        created = mind.create({"mode": "manual", "identity": {"name": "Ines", "age": "40"},
                               "levels": {"O": 4, "C": 2, "E": 2, "A": 4, "N": 5}})
        assert created.ref == "Ines" and created.state["traits"]
        turn = mind.experience("Ines", "I sat alone at the cliff edge while the wind picked up.")
        assert turn.reply and turn.appraisal and turn.state["turn"] == 1
        assert (tmp_path / "minds" / "data" / "characters" / "ines.json").exists()   # isolated roster
    finally:
        mind.stop()


def test_world_runs_on_v0(tmp_path):
    manager = WorldManager(tmp_path / "worlds")
    try:
        world = manager.create(control_setup(version="mindform_v0", seed=2), background=False)
        assert world.status == "ready", world.error
        for _ in range(12):
            world.run_beat()
        rows = [json.loads(line) for line in (world.dir / "experiences.jsonl").read_text().splitlines()]
        assert len(rows) == 12 * 3
        assert all(r["reply"] for r in rows) and any(r["formation"] for r in rows)
        assert all(r["state"]["traits"] for r in rows)
    finally:
        manager.shutdown()


def test_v0_ignores_proxy_settings(tmp_path, monkeypatch):
    """A proxy/VPN app on the Mac answered the world's 127.0.0.1 calls with 502 Bad Gateway, so
    v0 'never started'. The local connection must bypass HTTP(S)_PROXY entirely."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class BadGateway(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(502)
            self.end_headers()
        do_POST = do_CONNECT = do_GET

        def log_message(self, *args):
            pass

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), BadGateway)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{proxy.server_address[1]}"
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(var, url)
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)
    mind = make_mind("mindform_v0", tmp_path / "minds", use_llm=False)
    try:
        mind.start()
        created = mind.create({"mode": "manual", "identity": {"name": "Ines"}, "levels": {}})
        assert created.ref == "Ines"
    finally:
        mind.stop()
        proxy.shutdown()


# --- the API key reaches v0; problems are said out loud -----------------------------------
import app.config as config  # noqa: E402
from app.minds import mindform_v0  # noqa: E402
from app.minds.base import MindError  # noqa: E402
from app.world.manager import SetupError, validate_setup  # noqa: E402


def test_the_worlds_key_reaches_v0(tmp_path, monkeypatch):
    """A key in mindform-world/.env (the world's settings) is passed to v0's process."""
    monkeypatch.setattr(config, "llm_settings", lambda: {"api_key": "world-key", "base_url": "https://x/v1/", "model": "m-1"})
    env = mindform_v0._base_env(tmp_path, True)                 # tmp_path: a v0 without its own .env
    assert env["LLM_API_KEY"] == "world-key" and env["LLM_MODEL"] == "m-1"
    offline = mindform_v0._base_env(tmp_path, False)
    assert not any(offline.get(k) for k in mindform_v0._KEY_VARS)


def test_v0s_own_key_wins(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("GEMINI_API_KEY=v0-key\n")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(config, "llm_settings", lambda: {"api_key": "world-key", "base_url": "", "model": ""})
    env = mindform_v0._base_env(tmp_path, True)
    assert env["GEMINI_API_KEY"] == "v0-key" and not env.get("LLM_API_KEY")


def test_llm_mode_refuses_to_start_without_a_key(tmp_path):
    with pytest.raises(MindError, match="GEMINI_API_KEY"):
        mindform_v0.MindFormV0(tmp_path / "minds", use_llm=True).start()
    with pytest.raises(SetupError, match="GEMINI_API_KEY"):
        validate_setup(control_setup(version="mindform_v0", mind_llm=True))


def test_llm_mode_refuses_without_the_openai_package(tmp_path, monkeypatch):
    monkeypatch.setattr(mindform_v0, "health", lambda path=None: {
        "key": True, "missing": ["openai"], "python": "/usr/bin/python3", "llm_ready": False, "memory": False})
    assert "setup.sh" in mindform_v0.llm_problem(tmp_path)


def test_fallback_reasons_come_from_v0s_log(tmp_path):
    mind = mindform_v0.MindFormV0(tmp_path, use_llm=False)
    (tmp_path / "mindform_v0.log").write_text(
        "2026 nodes.llm_appraisal LLM appraisal unavailable (no LLM API key is set (GEMINI_API_KEY)); using offline appraiser\n"
        "2026 web.engine_bridge memory/encoder unavailable (No module named 'sentence_transformers'); skipping recurrence/recall\n"
        "2026 nodes.llm_impact LLM push unavailable (Error code: 400 - API key not valid. Please pass a valid API key.); using heuristic fallback\n")
    diag = mind.diagnose()
    assert diag["llm"] == "the API key was rejected — check GEMINI_API_KEY"
    assert "setup.sh" in diag["memory"]


def test_health_reports_what_v0s_python_has():
    h = mindform_v0.health()
    assert set(h) >= {"python", "key", "missing", "llm_ready", "memory"}
    assert h["memory"] == ("sentence_transformers" not in h["missing"] and "torch" not in h["missing"])


def test_memory_recall_runs_when_installed(tmp_path):
    """With sentence-transformers in v0's Python (./setup.sh), experiences are stored and recalled."""
    if not mindform_v0.health().get("memory"):
        pytest.skip("sentence-transformers not installed in v0's Python (run ./setup.sh)")
    mind = make_mind("mindform_v0", tmp_path / "minds", use_llm=False)
    mind.start()
    try:
        mind.create({"mode": "manual", "identity": {"name": "Ines"}, "levels": {}})
        for text in ("I fixed the fishing boat's hull at the boatyard.", "I repaired another boat hull at the boatyard."):
            mind.experience("Ines", text)
    finally:
        mind.stop()
    log_text = (tmp_path / "minds" / "mindform_v0.log").read_text()
    assert "memory/encoder unavailable" not in log_text
    assert list((tmp_path / "minds" / "data").rglob("*memor*")), "no memory files were written"
