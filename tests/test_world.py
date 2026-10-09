"""World engine: the island, the planner, conversations, the clock, injections, persistence."""
import json
import random

import pytest

from app import llm
from app.world import planner
from app.world.engine import World, hour_of
from app.world.manager import SetupError, WorldManager, validate_setup
from app.world.places import ACTIVITIES, JOBS, LOCATIONS, available_activities, doing_text, home_slot, public_map
from conftest import control_setup


@pytest.fixture
def manager(tmp_path):
    m = WorldManager(tmp_path / "worlds")
    yield m
    m.shutdown()


def make_world(manager, **overrides):
    world = manager.create(control_setup(**overrides), background=False)
    assert world.status == "ready", world.error
    world.running = False
    return world


# --- the island ----------------------------------------------------------------------
def test_island_is_consistent():
    for act_id, act in ACTIVITIES.items():
        assert act["place"] == "home" or act["place"] in LOCATIONS, act_id
        assert act["outcomes"], act_id
        for weight, quality, text, cond in act["outcomes"]:
            assert weight > 0 and quality in "+-0" and cond in (None, "bad", "fair")
            assert text[0].isupper() and text.rstrip("'\"").endswith("."), text
        assert doing_text(act_id)
    for job_id, job in JOBS.items():
        if job["activity"]:
            assert ACTIVITIES[job["activity"]]["job"] == job_id
            assert ACTIVITIES[job["activity"]]["place"] == job["place"]
    slots = {home_slot(i) for i in range(10)}
    assert len(slots) == 10
    data = public_map()
    assert {loc["id"] for loc in data["locations"]} == set(LOCATIONS)


def test_outcomes_never_name_feelings():
    banned = (" felt ", "happy", "sad ", "lonely", "proud", "ashamed", "angry", "excited", "nervous")
    for act in ACTIVITIES.values():
        for _, _, text, _ in act["outcomes"]:
            assert not any(word in f" {text.lower()} " for word in banned), text


def test_job_activities_are_gated():
    assert "cafe.work" in available_activities("cafe", "baker", 9)
    assert "cafe.work" not in available_activities("cafe", "fisher", 9)
    assert "beach.bonfire" not in available_activities("beach", None, 12)
    assert "beach.bonfire" in available_activities("beach", None, 20)


# --- llm parsing -------------------------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    ('{"a": 1}', {"a": 1}),
    ('<thought>{"no": 1}</thought>\n{"a": "x}"}', {"a": "x}"}),
    ('Sure!\n```json\n{"a": {"b": [1, 2]}}\n```', {"a": {"b": [1, 2]}}),
    ('prefix {"a": "say \\"hi\\" {there}"} suffix', {"a": 'say "hi" {there}'}),
])
def test_parse_json_object(raw, expected):
    assert llm.parse_json_object(raw) == expected


def test_parse_json_object_rejects_garbage():
    with pytest.raises(ValueError):
        llm.parse_json_object("no json here")


def test_no_key_means_rules():
    assert not llm.available()
    with pytest.raises(llm.LLMUnavailable):
        llm.complete_json("s", "u")


# --- planner --------------------------------------------------------------------------
def test_rules_planner_returns_valid_actions(manager):
    world = make_world(manager)
    contexts = {r.id: world._context(r) for r in world.residents.values()}
    for ctx in contexts.values():
        for seed in range(20):
            action = planner.plan_rules(ctx, random.Random(seed))
            assert planner._valid({**action, "action": action["type"]}, ctx) is not None, action
            assert action["source"] == "rules"


def test_llm_planner_output_is_validated(manager):
    world = make_world(manager)
    ctx = world._context(world.residents["aya"])
    assert planner._valid({"action": "do", "activity": "cafe.breakfast"}, ctx)["type"] == "do"
    assert planner._valid({"action": "do", "activity": "nowhere.dance"}, ctx) is None
    assert planner._valid({"action": "talk", "person": "nobody"}, ctx) is None
    assert planner._valid({"action": "talk", "person": "rex", "say": "Hi"}, ctx)["say"] == "Hi"
    text = planner.describe_context(ctx)
    assert "INNER STATE" in text and "Aya" in text and "OPTIONS" in text


# --- the beat -------------------------------------------------------------------------
def test_beats_advance_the_clock_and_log_experiences(manager):
    world = make_world(manager)
    start = world.clock
    for _ in range(8):
        assert world.run_beat()
    assert world.clock == start + 8 * world.beat_minutes
    rows = [json.loads(line) for line in (world.dir / "experiences.jsonl").read_text().splitlines()]
    assert len(rows) == 8 * len(world.residents)
    first = [r for r in rows if r["beat"] == 0]
    assert all(r["experience"].startswith("I woke up") for r in first)
    assert all(r["experience"] for r in rows)


def test_night_is_skipped_and_everyone_sleeps_at_home(manager):
    world = make_world(manager)
    world.clock = 22 * 60 + 45
    world.run_beat()                       # 22:45 -> 23:00
    assert hour_of(world.clock) == 23
    world.run_beat()                       # bedtime
    assert world.clock == 1440 + 7 * 60    # Day 2, 07:00
    assert all(r.place == r.home and not r.asleep for r in world.residents.values())
    rows = [json.loads(line) for line in (world.dir / "experiences.jsonl").read_text().splitlines()]
    assert all("went to bed" in r["experience"] for r in rows[-len(world.residents):])
    world.run_beat()
    rows = [json.loads(line) for line in (world.dir / "experiences.jsonl").read_text().splitlines()]
    assert all(r["experience"].startswith("I woke up") for r in rows[-len(world.residents):])


def test_same_seed_same_world(manager):
    a = make_world(manager, seed=5)
    b = make_world(manager, seed=5)
    for _ in range(30):
        a.run_beat()
        b.run_beat()
    rows = lambda w: [(r["resident"], r["experience"]) for r in
                      map(json.loads, (w.dir / "experiences.jsonl").read_text().splitlines())]
    assert rows(a) == rows(b)
    c = make_world(manager, seed=6)
    for _ in range(30):
        c.run_beat()
    assert rows(c) != rows(a)


def _force(world, actions):
    """Resolve hand-made actions (bypassing the planner)."""
    full = {rid: {"type": "rest", "intent": ""} for rid in world.residents}
    full.update(actions)
    return world._resolve(full)


def test_conversation_heard_and_answered(manager):
    world = make_world(manager)
    world.residents["rex"].place = world.residents["aya"].place = "cafe"
    plan = _force(world, {"aya": {"type": "talk", "person": "rex", "say": "Morning, Rex."},
                          "rex": {"type": "do", "activity": "cafe.coffee"}})
    assert {"k": "said", "to": "Rex", "line": "Morning, Rex."} in plan["items"]["aya"]
    heard = [i for i in plan["items"]["rex"] if i["k"] == "heard"]
    assert heard and heard[0]["line"] == "Morning, Rex."
    assert plan["levels"]["aya"] == plan["levels"]["rex"] + 1      # the speaker waits for the answer


def test_walking_off_mid_sentence_is_a_brushoff(manager):
    world = make_world(manager)
    world.residents["rex"].place = world.residents["aya"].place = "plaza"
    plan = _force(world, {"aya": {"type": "talk", "person": "rex", "say": "Rex, wait!"},
                          "rex": {"type": "do", "activity": "dock.fish"}})
    assert "aya" in plan["brushoff"]
    assert any(i["k"] == "brushoff" for i in plan["items"]["aya"])
    assert any(i["k"] == "heard_leaving" for i in plan["items"]["rex"])
    assert world.residents["aya"].place == "plaza" and world.residents["rex"].place == "dock"


def test_mutual_talk_becomes_one_conversation(manager):
    world = make_world(manager)
    world.residents["rex"].place, world.residents["aya"].place = "plaza", "cafe"
    plan = _force(world, {"aya": {"type": "talk", "person": "rex", "say": "Hi Rex"},
                          "rex": {"type": "talk", "person": "aya", "say": "Hi Aya"}})
    assert len(plan["talk"]) == 1
    speaker, listener = next(iter(plan["talk"].items()))
    assert world.residents[speaker].place == world.residents[listener].place


def test_speaker_walks_to_the_listener(manager):
    world = make_world(manager)
    world.residents["aya"].place, world.residents["rex"].place = "cafe", "workshop"
    _force(world, {"aya": {"type": "talk", "person": "rex", "say": "Got a minute?"},
                   "rex": {"type": "do", "activity": "workshop.work"}})
    assert world.residents["aya"].place == "workshop"
    assert len(world.residents["aya"].path) >= 2


# --- god mode -------------------------------------------------------------------------
def test_whisper_reaches_the_mind_verbatim(manager):
    world = make_world(manager)
    world.inject("whisper", "A stranger handed me an envelope with my name on it.", target="mira")
    world.run_beat()
    rows = [json.loads(line) for line in (world.dir / "experiences.jsonl").read_text().splitlines()]
    mira = [r for r in rows if r["resident"] == "mira"][-1]
    assert mira["experience"].endswith("A stranger handed me an envelope with my name on it.")


def test_island_wide_event_is_perceived_once_by_everyone(manager):
    world = make_world(manager)
    world.inject("event", "Every bell on the island started ringing at once.", minutes=60)
    world.run_beat()
    world.run_beat()
    rows = [json.loads(line) for line in (world.dir / "experiences.jsonl").read_text().splitlines()]
    for rid in world.residents:
        mine = [r["experience"] for r in rows if r["resident"] == rid]
        assert sum("Every bell" in e for e in mine) == 1


def test_inject_validation(manager):
    world = make_world(manager)
    with pytest.raises(ValueError):
        world.inject("whisper", "hello", target="nobody")
    with pytest.raises(ValueError):
        world.inject("event", "   ")
    with pytest.raises(ValueError):
        world.inject("event", "x", place="atlantis")


# --- persistence ------------------------------------------------------------------------
def test_save_and_resume(manager, tmp_path):
    world = make_world(manager)
    for _ in range(5):
        world.run_beat()
    world.save()
    loaded = World.load(world.dir)
    assert loaded.clock == world.clock and loaded.beat == world.beat
    assert list(loaded.residents) == list(world.residents)
    assert loaded.residents["aya"].relationships == world.residents["aya"].relationships
    assert loaded.rng.getstate() == world.rng.getstate()
    # A fresh manager resumes it (mind restarted) and it keeps going.
    other = WorldManager(manager.root)
    try:
        resumed = other.get(world.id)
        assert resumed.run_beat()
        assert resumed.clock == world.clock + world.beat_minutes
    finally:
        other.shutdown()


def test_clone_keeps_the_cast(manager):
    world = make_world(manager)
    clone = manager.clone(world.id, {"seed": 99, "name": "B"}, background=False)
    assert clone.status == "ready"
    assert [r.name for r in clone.residents.values()] == [r.name for r in world.residents.values()]
    assert clone.setup["seed"] == 99 and clone.setup["cloned_from"] == world.id


def test_setup_validation():
    with pytest.raises(SetupError):
        validate_setup(control_setup(characters=[]))
    with pytest.raises(SetupError):
        validate_setup(control_setup(version="mindform_v9"))
    with pytest.raises(SetupError):
        validate_setup(control_setup(characters=[{"mode": "bio", "bio": "  "}]))
    clean = validate_setup(control_setup(experiences_per_hour=12, intensity=0))
    assert clean["experiences_per_hour"] == 6 and clean["intensity"] == 1


def test_follow_ups_wait_for_morning_and_get_announced(manager):
    world = make_world(manager)
    world.clock = 22 * 60
    with world.lock:
        world._add_incident({"title": "Boat overdue", "text": "A boat is late.", "place": "dock",
                             "minutes": 180, "target": None})
    follow = next(e for e in world.events if e["title"] == "Boat overdue -- resolved")
    assert follow["start"] == 1440 + 7 * 60          # moved from 00:30 to Day 2, 07:00
    assert follow["announced"] is False
    world.clock = follow["start"]
    with world.lock:
        world._direct()
    assert follow["announced"] is True
    assert any(i["kind"] == "event" and i.get("title") == "Boat overdue -- resolved" for i in world.feed)


# --- emotions, bonds, pacing ---------------------------------------------------------------
from app.world.emotion import read_emotion  # noqa: E402


@pytest.mark.parametrize("appraisal, key", [
    ({"valence": 0.8, "intensity": 0.7, "social": 0.1}, "joy"),
    ({"valence": 0.8, "intensity": 0.7, "social": 0.8}, "warm"),
    ({"valence": 0.7, "intensity": 0.6, "agency": 0.6}, "pride"),
    ({"valence": 0.6, "intensity": 0.7, "novelty": 0.8}, "excited"),
    ({"valence": -0.7, "intensity": 0.6, "threat_challenge": -0.7}, "fear"),
    ({"valence": -0.6, "intensity": 0.6, "agency": -0.5}, "angry"),
    ({"valence": -0.6, "intensity": 0.6, "agency": 0.6, "social": 0.5}, "embarrassed"),
    ({"valence": -0.5, "intensity": 0.4}, "sad"),
    ({"valence": 0.0, "intensity": 0.6, "novelty": 0.9}, "surprise"),
    ({"valence": 0.05, "intensity": 0.2}, "calm"),
])
def test_emotion_names_mindforms_appraisal(appraisal, key):
    emotion = read_emotion(appraisal)
    assert emotion["key"] == key and emotion["emoji"] and 0 <= emotion["strength"] <= 1


def test_no_appraisal_no_emotion():
    assert read_emotion(None) is None and read_emotion({}) is None


def test_headlines_carry_dwell_and_pace_the_beat(manager):
    world = make_world(manager)
    world.beat_dwell = 0.0
    item = world.emit("say", "Hello there, Rex. How was the boatyard this morning?", actor="aya")
    assert 1.8 <= item["dwell"] <= 3.2
    assert "dwell" not in world.emit("outcome", "I sat by the fountain.", actor="aya")
    world.emit("event", "A storm rolled in.")
    assert world.beat_dwell == pytest.approx(item["dwell"] + 2.6)


def test_relationship_turning_a_corner_is_a_bond_event(manager):
    world = make_world(manager)
    aya = world.residents["aya"]
    aya.relationships["rex"] = {"affinity": 0.19, "talks": 3, "last": ""}

    class Turn:
        reply, formation = "Thanks, Rex!", None
        appraisal = {"valence": 0.9, "intensity": 0.8, "social": 0.9}
        state = aya.state

    plan = {"talk": {"aya": "rex"}, "brushoff": set(), "addressed_by": {"aya": [], "rex": ["aya"]},
            "actions": {"aya": {"type": "talk"}}}
    world._settle("aya", [], "Rex smiled at me.", "rules", Turn(), None, plan)
    bonds = [i for i in world.feed if i["kind"] == "bond"]
    assert bonds and bonds[-1]["feeling"] == "warm" and bonds[-1]["warmer"] is True
    emotions = [i for i in world.feed if i["kind"] == "emotion"]
    assert emotions and emotions[-1]["key"] == "warm" and aya.mood["key"] == "warm"
