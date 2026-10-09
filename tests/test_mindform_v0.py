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
