"""The world's LLM paths (planner, narrator, director) against a fake OpenAI-compatible
endpoint: the request shape, the use of the model's answer, and the fallback to rules."""
import json
import json as json_module

import httpx
import pytest

import app.config as config
import app.llm as llm
from app.world import director, narrator, planner
from app.world.manager import WorldManager
from conftest import control_setup


class FakeModel:
    """Answers chat/completions by looking at the system prompt."""

    def __init__(self):
        self.requests = []
        self.fail = False
        self.status = 200
        self.voice_line = None

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.requests.append({"url": url, "json": json, "headers": headers})
        if self.fail:
            raise httpx.ConnectError("offline")
        if self.status != 200:
            request = httpx.Request("POST", url)
            return httpx.Response(self.status, json={"error": "nope"}, request=request)
        system = json["messages"][0]["content"]
        if "PLANNER" in system:
            content = '<thought>hmm</thought>{"action": "talk", "person": "rex", "say": "Rex, about the pier...", "intent": "wants an ally"}'
        elif "lived experience" in system:
            content = '```json\n{"text": "I said hello to Rex by the fountain. He nodded."}\n```'
        elif "exact words ONE island resident" in system:
            self.lines = getattr(self, "lines", 0) + 1
            line = self.voice_line or f"Line {self.lines}: the pier again, of all things."
            content = json_module.dumps({"line": line})
        elif "DIRECTOR" in system:
            content = '{"title": "Whale sighting", "text": "A whale surfaced off the dock.", "place": "dock", "minutes": 90, "target": null}'
        else:
            content = '{"reply": "Fine."}'
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]}, request=request)


@pytest.fixture
def fake(monkeypatch):
    model = FakeModel()
    settings = lambda: {"api_key": "test-key", "base_url": "https://llm.example/v1/", "model": "fake-1"}
    monkeypatch.setattr(config, "llm_settings", settings)
    monkeypatch.setattr(llm, "llm_settings", settings)
    monkeypatch.setattr(llm.httpx, "post", model)
    llm._auth_blocked_until.clear()
    yield model
    llm._auth_blocked_until.clear()


def test_request_shape(fake):
    assert llm.available()
    llm.complete_json("system text", "user text")
    req = fake.requests[0]
    assert req["url"] == "https://llm.example/v1/chat/completions"
    assert req["headers"]["Authorization"] == "Bearer test-key"
    assert req["json"]["model"] == "fake-1"
    assert [m["role"] for m in req["json"]["messages"]] == ["system", "user"]


def test_llm_world_uses_the_model_and_falls_back(fake, tmp_path):
    manager = WorldManager(tmp_path / "worlds")
    try:
        world = manager.create(control_setup(world_brain="llm"), background=False)
        ctx = world._context(world.residents["aya"])
        action = planner.plan_llm(ctx, stats=world.stats)
        assert action == {"type": "talk", "person": "rex", "say": "Rex, about the pier...",
                          "intent": "wants an ally", "source": "llm"}

        text, source = narrator.narrate([{"k": "outcome", "text": "I sat by the fountain."},
                                         {"k": "whisper", "text": "Then the bells rang."}],
                                        {"name": "Aya", "time_text": "Day 1, 08:00", "weather_text": "clear skies",
                                         "place_name": "Fountain Plaza"}, use_llm=True)
        assert source == "llm" and text == "I said hello to Rex by the fountain. He nodded. Then the bells rang."

        event = director.llm_event(world._director_context())
        assert event["title"] == "Whale sighting" and event["place"] == "dock"

        world.run_beat()
        rows = [json.loads(line) for line in (world.dir / "experiences.jsonl").read_text().splitlines()]
        assert all(r["narration"] == "llm" for r in rows)
        assert world.residents["aya"].plan_source == "llm"
        assert world.stats.snapshot()["calls"] > 0

        fake.fail = True                       # the model goes away mid-run
        world.run_beat()
        assert world.residents["aya"].plan_source == "rules"
        rows = [json.loads(line) for line in (world.dir / "experiences.jsonl").read_text().splitlines()]
        assert all(r["narration"] == "rules" for r in rows[-3:])
        assert world.stats.snapshot()["failures"] > 0
    finally:
        manager.shutdown()


def test_invalid_planner_choice_raises_for_fallback(fake, monkeypatch, tmp_path):
    manager = WorldManager(tmp_path / "worlds")
    try:
        world = manager.create(control_setup(), background=False)
        ctx = world._context(world.residents["aya"])
        ctx["people"] = []                     # rex is not a valid option any more
        with pytest.raises(ValueError):
            planner.plan_llm(ctx)
    finally:
        manager.shutdown()


def test_rejected_key_is_not_hammered(fake):
    fake.status = 401
    stats = llm.Stats()
    with pytest.raises(httpx.HTTPStatusError):
        llm.complete_json("s", "u", stats=stats)
    assert len(fake.requests) == 1                 # no retry on 401
    for _ in range(5):
        with pytest.raises(llm.LLMUnavailable):
            llm.complete_json("s", "u", stats=stats)
    assert len(fake.requests) == 1                 # cooled down: no more HTTP calls
    snap = stats.snapshot()
    assert snap["failures"] == 6 and "rejected" in snap["last_error"]


def test_llm_voice_writes_the_lines_and_the_guard_still_holds(fake, tmp_path):
    manager = WorldManager(tmp_path / "worlds")
    try:
        world = manager.create(control_setup(world_brain="llm"), background=False)
        for _ in range(3):
            world.run_beat()
        rows = [json.loads(line) for line in (world.dir / "experiences.jsonl").read_text().splitlines()]
        assert any(r.get("voice") == "llm" for r in rows)
        voice_prompts = [r for r in fake.requests if "exact words ONE island resident" in r["json"]["messages"][0]["content"]]
        assert voice_prompts and "VOICE:" in voice_prompts[0]["json"]["messages"][1]["content"]

        fake.voice_line = "Same thing every time."          # a model stuck on one line: the guard refuses it
        for _ in range(3):
            world.run_beat()
        said = [i["text"] for i in world.feed if i["kind"] in ("say", "reaction") and i["text"] == "Same thing every time."]
        assert len(said) <= 1
    finally:
        manager.shutdown()
