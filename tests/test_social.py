"""Voices, encounters, relationships, gossip, secrets, god-mode presets, trait cards, recaps."""
import json
import random

import httpx
import pytest

import app.config as config
import app.llm as llm
from app.world import intrigue, voice
from app.world.engine import World, crossing
from app.world.manager import WorldManager, validate_setup
from conftest import control_setup

DRAMA = [
    {"mode": "manual", "identity": {"name": "Aya", "age": "27"}, "levels": {"O": 4, "C": 3, "E": 4, "A": 5, "N": 4},
     "job": "baker", "goal": "leave the island for good",
     "secret": "has already bought a one-way ferry ticket and hasn't told anyone"},
    {"mode": "manual", "identity": {"name": "Rex", "age": "34"}, "levels": {"O": 2, "C": 4, "E": 2, "A": 1, "N": 2},
     "job": "boatbuilder", "goal": "get off the island on his own terms",
     "secret": "is secretly building a boat to leave the island"},
    {"mode": "manual", "identity": {"name": "Ines", "age": "41"}, "levels": {"O": 3, "C": 5, "E": 2, "A": 4, "N": 3},
     "job": "nurse", "goal": "keep her job at the clinic", "secret": "mixed up two patients' charts and is hiding it"},
    {"mode": "manual", "identity": {"name": "Leo", "age": "22"}, "levels": {"O": 4, "C": 1, "E": 5, "A": 4, "N": 3},
     "job": "none", "goal": "be liked by everyone on the island", "voice": "cheeky, says \"mate\" a lot"},
]


@pytest.fixture
def manager(tmp_path):
    m = WorldManager(tmp_path / "worlds")
    yield m
    m.shutdown()


def drama_world(manager, **overrides):
    world = manager.create(control_setup(characters=DRAMA, **overrides), background=False)
    assert world.status == "ready", world.error
    world.running = False
    return world


def lines_by(world):
    out = {}
    for it in world.feed:
        if it["kind"] in ("say", "reaction") and it.get("actor"):
            out.setdefault(it["actor"], []).append(it["text"])
    return out


# --- voices -----------------------------------------------------------------------------
def test_each_resident_gets_their_own_voice(manager):
    world = drama_world(manager)
    profiles = {r.id: r.voice for r in world.residents.values()}
    assert profiles["leo"]["address"] == ["mate"]                    # from the written voice profile
    leads = [lead for p in profiles.values() for lead in p["leads"]]
    assert len(leads) == len(set(leads))                             # no tic given to two people
    assert profiles["aya"]["tags"] != profiles["rex"]["tags"]


def test_no_shared_templates_and_no_repeats(manager):
    world = drama_world(manager)
    for _ in range(48):
        world.run_beat()
    owners = {}
    for tid, rid in world.voices.claims.items():
        assert owners.setdefault(tid, rid) == rid
    by = lines_by(world)
    assert len(by) == 4 and all(len(v) > 5 for v in by.values())
    for rid, lines in by.items():
        for i, line in enumerate(lines):
            window = lines[max(0, i - voice.RECENT_LINES):i]
            assert not voice.near_duplicate(line, window), (rid, line)
    # no two residents share a phrase of six words or more (names and shared subjects aside)
    from app.world import director
    from app.world.places import ACTIVITIES
    subjects = list(director.TOPICS.values()) + [e.get("topic") for e in world.events]
    subjects += [o[2].split(";")[0].rstrip(".") for a in ACTIVITIES.values() for o in a["outcomes"]]
    subjects += [f["text"] for r in world.residents.values() for f in r.knowledge]
    subjects += [intrigue.first_person_goal(r.goal) for r in world.residents.values()]
    names = {r.name for r in world.residents.values()}
    for rid, lines in by.items():
        others = [line for oid, ls in by.items() if oid != rid for line in ls]
        for line in lines:
            assert not voice.shares_run(line, others, names=names, ignore=subjects), (rid, line)


def test_mind_mode_says_mindforms_words(manager):
    world = drama_world(manager, voices="mind")
    assert world.voice_mode == "mind"
    for _ in range(6):
        world.run_beat()
    rows = [json.loads(x) for x in (world.dir / "experiences.jsonl").read_text().splitlines()]
    spoken = [r for r in rows if r.get("spoken")]
    assert spoken and all(r["spoken"] == r["reply"] for r in spoken)


def test_guard_helpers():
    assert voice.near_duplicate("I'm still carrying it, if I'm honest.", ["I'm still carrying it, if I'm honest!"])
    assert not voice.near_duplicate("The fishing went fine.", ["Nobody looks under the tarp."])
    assert voice.shares_run("and I need room to do this my own way", ["Rex: and I need room to do this my own way."])
    assert voice.gerund_phrase("leave the island for good") == "leaving the island for good"
    assert voice.gerund_phrase("get her son to visit") == "getting her son to visit"
    assert intrigue.first_person_goal("save enough to open her own bakery") == "save enough to open my own bakery"
    assert intrigue.first_person_secret("is secretly building a boat") == "I'm secretly building a boat"


# --- encounters ---------------------------------------------------------------------------
def _force(world, actions):
    full = {rid: {"type": "rest", "intent": ""} for rid in world.residents}
    full.update(actions)
    return world._resolve(full)


def test_people_in_the_same_place_start_talking(manager, monkeypatch):
    world = drama_world(manager)
    monkeypatch.setattr(World, "_encounter_chance", lambda self, a, b: 1.0)
    for rid in ("aya", "rex"):
        world.residents[rid].place = "cafe"
    plan = _force(world, {"aya": {"type": "do", "activity": "cafe.coffee"},
                          "rex": {"type": "do", "activity": "cafe.coffee"}})
    talk = plan["talk"]
    assert talk.get("aya") == "rex" or talk.get("rex") == "aya"
    says = [i for i in world.feed if i["kind"] == "say" and i.get("encounter")]
    assert says and says[-1]["text"]


def test_crossing_paths():
    a = [[-10.0, 0.0], [10.0, 0.0]]
    b = [[10.0, 0.3], [-10.0, 0.3]]
    assert crossing(a, b) is not None
    assert crossing(a, [[0.0, 20.0], [0.0, 30.0]]) is None


def test_walkers_who_cross_exchange_a_line_in_passing(manager, monkeypatch):
    world = drama_world(manager)
    monkeypatch.setattr(World, "_encounter_chance", lambda self, a, b: 1.0)
    aya, rex = world.residents["aya"], world.residents["rex"]
    aya.place, rex.place = "library", "greenhouse"
    aya.x, aya.z = world._spot("library", "aya")
    rex.x, rex.z = world._spot("greenhouse", "rex")
    plan = _force(world, {"aya": {"type": "do", "activity": "greenhouse.tend"},
                          "rex": {"type": "do", "activity": "library.read"}})
    assert plan["passing"], plan["talk"]
    speaker = next(iter(plan["passing"]))
    assert any(i.get("passing") for i in plan["items"][speaker] if i["k"] == "said")
    world._live(plan)
    answers = [i for i in world.feed if i["kind"] == "say" and i.get("answer") and i.get("passing")]
    assert answers


# --- relationships, gossip, secrets ----------------------------------------------------------
def test_relationships_have_trust_and_affection(manager):
    world = drama_world(manager)
    for _ in range(30):
        world.run_beat()
    rels = [rel for r in world.residents.values() for rel in r.public(world)["relationships"]]
    assert rels and all({"trust", "affection", "label"} <= set(rel) for rel in rels)
    assert any(rel["talks"] for rel in rels)


def test_walking_off_costs_trust(manager):
    world = drama_world(manager)
    world.residents["rex"].place = world.residents["aya"].place = "plaza"
    plan = _force(world, {"aya": {"type": "talk", "person": "rex", "say": "Rex, wait!"},
                          "rex": {"type": "do", "activity": "dock.fish"}})
    world._live(plan)
    assert world.residents["aya"].relationships["rex"]["trust"] < 0


def test_seeing_someone_work_on_their_secret_is_a_clue(manager):
    world = drama_world(manager)
    rex, leo = world.residents["rex"], world.residents["leo"]
    assert rex.secret["kind"] == "boat" and rex.secret["place"] == "workshop"
    rex.place = leo.place = "workshop"
    plan = _force(world, {"rex": {"type": "secret"}, "leo": {"type": "do", "activity": "workshop.build"}})
    assert any(i["k"] == "secret_work" for i in plan["items"]["rex"])
    assert any(i["k"] == "saw" for i in plan["items"]["leo"])
    assert 0 < intrigue.suspicion_of(leo, "rex") < 1
    # Enough clues and they work it out.
    for _ in range(3):
        _force(world, {"rex": {"type": "secret"}, "leo": {"type": "do", "activity": "workshop.build"}})
        world.beat += 1
    assert "leo" in rex.secret["known_by"]
    assert any(i["kind"] == "secret" and i["other"] == "rex" for i in world.feed)


def test_gossip_passes_a_clue_on(manager):
    world = drama_world(manager)
    aya, leo = world.residents["aya"], world.residents["leo"]
    clue = "Rex came out of the boatyard long after closing, sawdust all over their sleeves."
    intrigue.learn(leo, "rex", clue, t=world.clock, src="saw", clue=True)
    fact = intrigue.gossip_for(leo, aya, world.residents, random.Random(1))
    assert fact and fact["about"] == "rex"
    world._conversation_effects(leo, aya, {"intent": "gossip", "fact": fact, "who": "Rex"}, heard=True)
    assert any(f["text"] == clue for f in aya.knowledge)
    assert intrigue.suspicion_of(aya, "rex") > 0


def test_confiding_shares_the_secret_and_trust(manager):
    world = drama_world(manager)
    aya, leo = world.residents["aya"], world.residents["leo"]
    world._conversation_effects(aya, leo, {"intent": "confide"}, heard=True)
    assert "leo" in aya.secret["known_by"] and intrigue.knows_secret(leo, aya)
    assert leo.relationships["aya"]["trust"] > 0
    # The one who was confided in talks about it as an ally, never confronts.
    for seed in range(20):
        topic = intrigue.choose_topic(leo, aya, world, random.Random(seed))
        assert topic["intent"] != "confront"


def test_secrets_shape_the_rules_planner(manager):
    from app.world import planner
    world = drama_world(manager)
    world.clock = 19 * 60                        # boat hours, after work
    ctx = world._context(world.residents["rex"])
    assert ctx["secret"]["place"] == "workshop"
    picks = [planner.plan_rules(ctx, random.Random(s))["type"] for s in range(60)]
    assert "secret" in picks
    assert "SECRET" in planner.describe_context(ctx)


# --- god mode presets -------------------------------------------------------------------------
@pytest.mark.parametrize("preset", ["storm", "stranger", "expose", "fire", "festival", "rumor", "fired"])
def test_presets_hit_everyone_and_force_reactions(manager, preset):
    world = drama_world(manager)
    result = world.preset(preset)
    assert result["event"]["force"]
    world.run_beat()
    rows = [json.loads(x) for x in (world.dir / "experiences.jsonl").read_text().splitlines()]
    event = next(e for e in world.events if e.get("preset") == preset)
    assert set(event["seen_by"]) == set(world.residents)            # everyone was hit
    for rid in world.residents:
        mine = [r for r in rows if r["resident"] == rid]
        assert any(f["k"] == "event" for f in mine[-1]["facts"])
    spoken = {i["actor"] for i in world.feed if i["kind"] == "say" and i["t"] == event["start"]}
    assert len(spoken) >= len(world.residents) - 1                  # (nearly) everyone says something out loud


def test_storm_cancels_the_ferry(manager):
    world = drama_world(manager)
    world.preset("storm")
    assert world.weather == "storm"
    world.weather = "clear"                                         # even if it clears, today's ferry is off
    world._ferry(world.clock)
    assert world.events[-1]["title"] == "No ferry"


def test_letter_preset_exposes_a_secret(manager):
    world = drama_world(manager)
    world.preset("expose", target="ines")
    ines = world.residents["ines"]
    assert ines.secret["exposed"] and set(ines.secret["known_by"]) == {"aya", "rex", "leo"}
    assert any(i["kind"] == "secret" and i.get("mode") == "exposed" for i in world.feed)


def test_job_loss_and_rumor(manager):
    world = drama_world(manager)
    world.preset("fired", target="aya")
    assert world.residents["aya"].job == "none" and world.residents["aya"].former_job == "baker"
    world.preset("rumor", target="rex")
    assert all(any(f["about"] == "rex" for f in r.knowledge) for r in world.residents.values() if r.id != "rex")
    with pytest.raises(ValueError):
        world.preset("fired", target="aya")                         # no job left to lose
    with pytest.raises(ValueError):
        world.preset("volcano")


def test_custom_forced_event_reaches_everyone_once(manager):
    world = drama_world(manager)
    world.inject("event", "Every bell on the island started ringing at once.", title="Bells", force=True)
    world.run_beat()
    world.run_beat()
    rows = [json.loads(x) for x in (world.dir / "experiences.jsonl").read_text().splitlines()]
    for rid in world.residents:
        assert sum("Every bell" in r["experience"] for r in rows if r["resident"] == rid) == 1


# --- trait cards, recap -------------------------------------------------------------------------
def _with_trait(world, r, value):
    state = json.loads(json.dumps(r.state))
    for t in state["traits"]:
        if t["key"] == "N":
            t["value"] = value
    r.state = state


def test_wobbling_traits_dont_flip_flop_cards(manager):
    world = drama_world(manager)
    r = world.residents["aya"]
    base = next(t["value"] for t in r.state["traits"] if t["key"] == "N")
    cards = []
    for beat in range(60):
        world.beat = beat
        _with_trait(world, r, base + (0.09 if beat % 2 else -0.09))       # up, down, up, down...
        world._smooth_traits(r)
        cards += [i for i in world.feed if i["kind"] == "shift" and i["seq"] > (cards[-1]["seq"] if cards else 0)]
    assert not cards
    for beat in range(60, 100):                                           # a real, steady change
        world.beat = beat
        _with_trait(world, r, base + 0.004 * (beat - 59) * 3)
        world._smooth_traits(r)
    shifts = [i for i in world.feed if i["kind"] == "shift"]
    assert shifts and all(s["up"] for s in shifts)


def test_bedtime_plays_the_recap(manager):
    world = drama_world(manager)
    world.preset("festival")
    world.clock = 23 * 60
    world.run_beat()
    recap = [i for i in world.feed if i["kind"] == "recap"]
    assert recap and 1 <= len(recap[-1]["bullets"]) <= 3 and recap[-1]["dwell"] > 5


def test_outcomes_and_incidents_dont_repeat(manager):
    world = drama_world(manager)
    r = world.residents["rex"]
    texts = [world._outcome_q(r, "dock.fish")[0] for _ in range(4)]
    assert texts[0] != texts[1] and texts[1] != texts[2]
    with world.lock:
        for _ in range(4):
            world._add_incident(None)
    titles = [e["title"] for e in world.events if e["source"] == "deck" and not e["title"].endswith("resolved")]
    assert len(titles) == len(set(titles))


# --- reliability -----------------------------------------------------------------------------------
class Flaky:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls += 1
        status = self.statuses.pop(0) if self.statuses else 200
        request = httpx.Request("POST", url)
        if status == "drop":
            raise httpx.ReadTimeout("slow", request=request)
        if status != 200:
            return httpx.Response(status, headers={"retry-after": "0"}, json={}, request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]}, request=request)


@pytest.fixture
def keyed(monkeypatch):
    settings = lambda: {"api_key": "k", "base_url": "https://llm.example/v1/", "model": "m"}
    monkeypatch.setattr(config, "llm_settings", settings)
    monkeypatch.setattr(llm, "llm_settings", settings)
    llm.reset_circuit()
    yield monkeypatch
    llm.reset_circuit()


def test_transient_failures_are_retried(keyed):
    fake = Flaky([503, "drop"])
    keyed.setattr(llm.httpx, "post", fake)
    assert llm.complete_json("s", "u") == {"ok": True}
    assert fake.calls == 3


def test_circuit_opens_then_recovers(keyed):
    fake = Flaky([503] * 20)
    keyed.setattr(llm.httpx, "post", fake)
    with pytest.raises(Exception):
        llm.complete_json("s", "u")
    with pytest.raises(Exception):
        llm.complete_json("s", "u")
    calls = fake.calls
    assert calls <= llm.CIRCUIT_FAILURES + 1
    with pytest.raises(llm.LLMUnavailable):                    # open: fails fast, no HTTP
        llm.complete_json("s", "u")
    assert fake.calls == calls
    llm._circuit["open_until"] = 0.0                          # cooldown over
    fake.statuses = []
    assert llm.complete_json("s", "u") == {"ok": True}


def test_mind_hiccup_is_retried(manager, monkeypatch):
    world = drama_world(manager)
    real = world.mind.experience
    calls = {"n": 0}

    def flaky(ref, text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("mind process restarting")
        return real(ref, text)

    monkeypatch.setattr(world.mind, "experience", flaky)
    monkeypatch.setattr("app.world.engine.time.sleep", lambda s: None)
    turn, error = world._experience(world.residents["aya"], "I sat by the fountain.")
    assert turn is not None and error is None


def test_a_failed_beat_does_not_stop_the_show(manager, monkeypatch):
    world = drama_world(manager)
    boom = {"n": 0}
    real = world._day_beat

    def once():
        boom["n"] += 1
        if boom["n"] == 1:
            raise RuntimeError("one bad beat")
        return real()

    monkeypatch.setattr(world, "_day_beat", once)
    world.speed = 0
    world.set_running(True)
    import time
    deadline = time.time() + 10
    while time.time() < deadline and world.beat < 2:
        time.sleep(0.05)
    world.running = False
    assert world.beat >= 2 and world.fail_streak == 0


# --- setup & persistence ----------------------------------------------------------------------------
def test_setup_keeps_secrets_voices_and_mode():
    clean = validate_setup(control_setup(characters=DRAMA, voices="mind"))
    assert clean["voices"] == "mind"
    assert clean["characters"][1]["secret"].startswith("is secretly building")
    assert clean["characters"][3]["voice"].startswith("cheeky")
    assert validate_setup(control_setup())["voices"] == "styled"


def test_social_state_survives_a_restart(manager):
    world = drama_world(manager)
    world.preset("rumor", target="rex")
    for _ in range(6):
        world.run_beat()
    world.save()
    loaded = World.load(world.dir)
    for rid, r in world.residents.items():
        twin = loaded.residents[rid]
        assert twin.secret == r.secret and twin.knowledge == r.knowledge and twin.voice == r.voice
        assert twin.relationships == r.relationships
    assert loaded.voices.claims == world.voices.claims
