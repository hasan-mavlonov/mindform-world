from fastapi.testclient import TestClient
from app.main import app
from app.simulation import World


def test_world_moves_and_records_events():
    w = World()
    initial = [(a.x, a.z) for a in w.agents]
    state = w.step(30)
    assert state["tick"] == 30
    assert len(state["agents"]) == 5
    assert any((a.x, a.z) != p for a, p in zip(w.agents, initial))
    assert len(state["events"]) > 1
    assert any(a.memories for a in w.agents)


def test_reset_is_deterministic():
    w = World()
    a = w.step(60)
    w.reset()
    b = w.step(60)
    assert a == b


def test_api():
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200
    assert client.get("/").status_code == 200
    assert client.post("/api/step?n=0").status_code == 400
    assert client.post("/api/reset").status_code == 200
    assert client.post("/api/step?n=1").json()["tick"] == 1
