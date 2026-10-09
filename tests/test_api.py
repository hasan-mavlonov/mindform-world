"""HTTP API, end to end on the control mind."""
import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.world.manager import WorldManager
from conftest import control_setup


@pytest.fixture
def client(tmp_path, monkeypatch):
    manager = WorldManager(tmp_path / "worlds")
    monkeypatch.setattr(main, "manager", manager)
    with TestClient(main.app) as c:
        yield c
    manager.shutdown()


def wait_ready(client, wid, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f"/api/worlds/{wid}/state").json()
        if state["status"] in ("ready", "error"):
            return state
        time.sleep(0.05)
    raise AssertionError("world never became ready")


def wait_beat(client, wid, beat, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f"/api/worlds/{wid}/state").json()
        if state["beat"] >= beat and not state["busy"]:
            return state
        time.sleep(0.05)
    raise AssertionError(f"beat {beat} never came")


def test_pages_and_status(client):
    assert client.get("/").status_code == 200
    assert client.get("/api/health").json()["ok"]
    status = client.get("/api/status").json()
    ids = {v["id"] for v in status["versions"]}
    assert {"mindform_v0", "control"} <= ids
    control = next(v for v in status["versions"] if v["id"] == "control")
    assert control["available"] and control["schema"]["trait_questions"]
    assert status["map"]["locations"] and status["llm"]["available"] is False


def test_world_lifecycle(client):
    assert client.post("/api/worlds", json=control_setup(characters=[])).status_code == 400
    wid = client.post("/api/worlds", json=control_setup()).json()["id"]
    state = wait_ready(client, wid)
    assert state["status"] == "ready" and len(state["residents"]) == 3
    assert any(i["kind"] == "born" for i in state["feed"])
    seq = state["feed_seq"]

    assert client.post(f"/api/worlds/{wid}/step").status_code == 200
    state = wait_beat(client, wid, 1)
    assert state["time_text"] == "Day 1, 07:15"
    new = client.get(f"/api/worlds/{wid}/state", params={"since": seq}).json()["feed"]
    assert new and all(i["seq"] > seq for i in new)

    r = client.post(f"/api/worlds/{wid}/inject", json={"kind": "whisper", "target": "aya", "text": "You found a key."})
    assert r.status_code == 200
    assert client.post(f"/api/worlds/{wid}/inject", json={"kind": "whisper", "target": "zed", "text": "x"}).status_code == 400

    detail = client.get(f"/api/worlds/{wid}/residents/aya").json()
    assert detail["name"] == "Aya" and detail["mind"]["control"] is True
    assert client.get(f"/api/worlds/{wid}/residents/zed").status_code == 404

    assert client.post(f"/api/worlds/{wid}/speed", json={"speed": 4}).json()["speed"] == 4
    assert client.post(f"/api/worlds/{wid}/speed", json={"speed": 3}).status_code == 400
    assert client.post(f"/api/worlds/{wid}/run", json={"running": True}).json()["running"] is True
    wait_beat(client, wid, 3)
    assert client.post(f"/api/worlds/{wid}/run", json={"running": False}).json()["running"] is False

    exps = client.get(f"/api/worlds/{wid}/experiences", params={"resident": "aya"}).json()["experiences"]
    assert exps and all(e["resident"] == "aya" for e in exps)

    listed = client.get("/api/worlds").json()["worlds"]
    assert listed[0]["id"] == wid

    zipped = client.get(f"/api/worlds/{wid}/export")
    names = zipfile.ZipFile(io.BytesIO(zipped.content)).namelist()
    assert f"{wid}/setup.json" in names and f"{wid}/experiences.jsonl" in names

    clone = client.post(f"/api/worlds/{wid}/clone", json={"seed": 3}).json()["id"]
    assert wait_ready(client, clone)["setup"]["seed"] == 3


def test_unknown_world(client):
    assert client.get("/api/worlds/nope/state").status_code == 404
    assert client.get("/api/worlds/..%2F..%2Fetc/state").status_code == 404
    assert client.post("/api/worlds/nope/clone", json={}).status_code == 404


def test_old_cached_page_is_told_to_reload(client):
    """A browser can keep the pre-0.2 page cached and only ever call /api/state + /api/step."""
    r = client.get("/api/state")
    assert r.status_code == 200 and r.headers["clear-site-data"] == '"cache"'
    data = r.json()
    assert "reload" in data["events"][0]["message"].lower()
    assert "new version" in data["mode"]                  # the old page shows this in its top bar
    # ...and it sends itself to the new start screen: the old client renders the resident it
    # selects at start ("aya") into its inspector as HTML.
    aya = next(a for a in data["agents"] if a["id"] == "aya")
    assert "location.replace('/?fresh='" in aya["personality"]
    for key in ("name", "color", "role", "mood", "traits", "x", "z", "memories", "completed_visits"):
        assert key in aya
    step = client.post("/api/step?n=1")                  # an old tab that was already open
    assert step.status_code == 200 and step.headers["clear-site-data"] == '"cache"'
    assert any(a["id"] == "aya" for a in step.json()["agents"])
    assert client.post("/api/reset").status_code == 200


def test_frontend_is_never_cached_and_versioned(client):
    for path in ("/", "/static/js/main.js", "/static/js/scene.js"):
        assert "no-store" in client.get(path).headers["cache-control"]
    assert client.get("/favicon.ico").status_code == 200
    version = client.get("/api/status").json()["app_version"]
    wid = client.post("/api/worlds", json=control_setup()).json()["id"]
    assert wait_ready(client, wid)["app_version"] == version   # open pages reload when this changes
