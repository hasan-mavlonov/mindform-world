"""One world: the island clock, its residents, and the beat that moves everything.

A BEAT is one stretch of island time (60 / experiences-per-hour minutes). Each beat:

    1. DIRECT   weather, the ferry, community events, incidents          (director.py)
    2. PLAN     each awake resident picks an action from their MindForm state (planner.py)
    3. RESOLVE  the world decides what actually happened: who went where, what came of it,
                who spoke to whom, who walked off mid-sentence, who else was there
    4. LIVE     per resident: the facts are narrated as a first-person experience
                (narrator.py) and handed to their mind (MindForm), which forms them and
                answers in their own voice
    5. SETTLE   relationships move with how each resident read the encounter; the clock
                advances; everything is logged and saved

Conversations run INSIDE a beat: the speaker's line is heard, the listener's mind answers,
and the speaker's experience includes that answer. Only an opening line comes from the
planner; every line after it is a MindForm reply (the speaker's "inclination" is the reply
they formed after hearing the other, and it is what they say if they keep talking).

At 23:00 everyone walks home and sleeps; the night is skipped to 07:00.
"""
from __future__ import annotations

import json
import logging
import math
import random
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app import llm
from app.minds import Mind, describe_state, make_mind, version_name
from app.world import director, narrator, planner
from app.world.emotion import HEADLINE_THRESHOLD, SHOW_THRESHOLD, read_emotion
from app.world.places import (ACTIVITIES, ISLAND_NAME, JOBS, LOCATIONS, activity_place,
                              available_activities, doing_text, home_id, home_slot, path_between)

log = logging.getLogger("mindform.world")

WAKE_HOUR = 7
BED_HOUR = 23
MAX_RESIDENTS = 10
FEED_KEEP = 800
BASE_BEAT_SECONDS = 7.0          # shortest beat at 1x (real seconds)
WALK_SECONDS = 6.0               # at 1x, each beat opens with ~6 s of walking before anyone speaks
# Watchable pacing: every "headline" (a line said, an event, a strong emotion, a bond) gets this
# many seconds on screen at 1x; the beat waits for its headlines before the next one runs.
DWELL = {"event": 2.6, "letter": 2.6, "weather": 2.0, "inject": 1.8, "bond": 1.8, "emotion": 1.5}
AFFINITY_RATE = 0.2              # how far one encounter's valence (MindForm's reading) moves a relationship
PALETTE = ["#f4a6c8", "#ffb565", "#84cffa", "#acf2a9", "#d7b4ff", "#ffe08a",
           "#7fe0d4", "#ff9b8a", "#b9c7ff", "#f6c6a0"]


def day_of(minutes: int) -> int:
    return minutes // 1440 + 1


def hour_of(minutes: int) -> int:
    return (minutes % 1440) // 60


def time_text(minutes: int) -> str:
    return f"Day {day_of(minutes)}, {hour_of(minutes):02d}:{minutes % 60:02d}"


def daypart(hour: int) -> str:
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    if hour < 21:
        return "evening"
    return "night"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "resident"


_FEELING_ORDER = ["hostile", "cool", "neutral", "warm", "close"]


def _feeling(affinity: float, talks: int) -> str:
    if not talks:
        return "haven't really met"
    if affinity > 0.5:
        return "close"
    if affinity > 0.2:
        return "warm"
    if affinity > -0.2:
        return "neutral"
    if affinity > -0.5:
        return "cool"
    return "hostile"


@dataclass
class Resident:
    id: str
    name: str
    ref: str
    color: str
    identity: dict
    job: str
    goal: str
    home: str
    home_xy: list
    spec: dict                                   # the creation spec (for cloning the cast)
    created_via: str | None = None
    place: str = ""
    x: float = 0.0
    z: float = 0.0
    path: list = field(default_factory=list)
    path_t: int = 0                              # the island time of the beat that set this path
    doing: str = "waking up"
    activity_id: str | None = None
    intent: str = ""
    plan_source: str = ""
    asleep: bool = False
    state: dict = field(default_factory=dict)
    reply: str | None = None
    inclination: dict | None = None              # {"to": id, "text": str, "beat": n}
    talking_with: list = field(default_factory=list)
    conversation_len: int = 0
    last_experience: str = ""
    recent: list = field(default_factory=list)
    relationships: dict = field(default_factory=dict)
    formation_log: list = field(default_factory=list)
    visited_today: list = field(default_factory=list)
    pending: list = field(default_factory=list)  # letters / whispers waiting for the next beat
    turns: int = 0
    last_appraisal: dict | None = None
    mood: dict | None = None                     # MindForm's last reading, named (see emotion.py)
    mind_error: str | None = None

    def public(self, world: "World") -> dict:
        data = asdict(self)
        for private in ("spec", "pending", "inclination"):
            data.pop(private, None)
        data["place_name"] = world.place_name(self.place)
        data["job_title"] = JOBS.get(self.job, JOBS["none"])["title"]
        data["talking_with_names"] = [world.residents[i].name for i in self.talking_with if i in world.residents]
        data["relationships"] = [
            {"id": oid, "name": world.residents[oid].name if oid in world.residents else oid,
             "affinity": round(rel["affinity"], 3), "talks": rel["talks"], "last": rel.get("last", ""),
             "feeling": _feeling(rel["affinity"], rel["talks"])}
            for oid, rel in self.relationships.items()]
        data["recent"] = self.recent[-6:]
        data["formation_log"] = self.formation_log[-12:]
        data["state_text"] = describe_state(self.state)
        return data


class World:
    def __init__(self, world_id: str, root: Path, setup: dict):
        self.id = world_id
        self.dir = Path(root)
        self.setup = setup
        self.name = setup.get("name") or world_id
        self.lock = threading.RLock()
        self.seed = int(setup.get("seed", 431))
        self.rng = random.Random(self.seed)
        self.beat_minutes = 60 // max(3, min(6, int(setup.get("experiences_per_hour", 4))))
        self.intensity = max(1, min(3, int(setup.get("intensity", 2))))
        self.brain = "llm" if setup.get("world_brain") == "llm" else "rules"
        self.mind_llm = bool(setup.get("mind_llm"))
        self.clock = WAKE_HOUR * 60
        self.beat = 0
        self.weather = "clear"
        self.weather_changed = False
        self.morning = True
        self.events: list[dict] = []
        self.event_seq = 0
        self.scheduled_day = 0
        self.last_hour_key = -1
        self.residents: dict[str, Resident] = {}
        self.feed: deque = deque(maxlen=FEED_KEEP)
        self.feed_seq = 0
        self.status = "new"
        self.phase = "idle"
        self.error: str | None = None
        self.progress: dict = {}
        self.running = False
        self.speed = 1.0
        self.busy = False
        self.stats = llm.Stats()
        self.mind: Mind | None = None
        self.last_beat_seconds = 0.0
        self.beat_dwell = 0.0                     # sum of this beat's headline dwell (pacing)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._io_lock = threading.Lock()

    # ---- helpers ----------------------------------------------------------------------
    def place_name(self, place: str) -> str:
        if place.startswith("home:"):
            owner = self.residents.get(place[5:])
            return f"{owner.name}'s cottage" if owner else "a cottage"
        return LOCATIONS[place]["name"] if place in LOCATIONS else place

    def _my_place_name(self, r: Resident, place: str) -> str:
        return "my cottage" if place == r.home else self.place_name(place)

    def _spot(self, place: str, rid: str) -> tuple[float, float]:
        """Where a resident stands at a place (spread around it so nobody overlaps)."""
        if place.startswith("home:"):
            owner = self.residents.get(place[5:])
            hx, hz = owner.home_xy if owner else (0.0, 0.0)
            door = (hx * 0.9, hz * 0.9)                # just in front of the door, toward town
            if owner is None or rid == owner.id:
                return door
            side = math.atan2(hz, hx) + math.pi / 2    # a visitor stands beside the owner
            k = 1.1 * (1 + list(self.residents).index(rid) % 2) if rid in self.residents else 1.1
            return round(door[0] + k * math.cos(side), 2), round(door[1] + k * math.sin(side), 2)
        loc = LOCATIONS[place]
        index = list(self.residents).index(rid) if rid in self.residents else 0
        angle = index * 2.399 + sum(map(ord, place)) * 0.37      # golden-angle spread, stable per place
        radius = 2.4 + 0.35 * (index % 3)
        return round(loc["x"] + radius * math.cos(angle), 2), round(loc["z"] + radius * math.sin(angle), 2)

    @staticmethod
    def dwell_for(kind: str, text: str) -> float:
        """Seconds at 1x a headline stays the focus (long lines take longer to read)."""
        if kind == "say":
            return round(min(3.2, max(1.8, 1.3 + 0.014 * len(text))), 2)
        return DWELL.get(kind, 0.0)

    def emit(self, kind: str, text: str = "", dwell: float | None = None, **extra) -> dict:
        with self.lock:
            self.feed_seq += 1
            dwell = self.dwell_for(kind, text) if dwell is None else dwell
            item = {"seq": self.feed_seq, "kind": kind, "t": self.clock, "time": time_text(self.clock),
                    "text": text, **extra}
            if dwell:
                item["dwell"] = dwell
                self.beat_dwell += dwell
            self.feed.append(item)
        self._append_jsonl("feed.jsonl", item)
        return item

    def _append_jsonl(self, name: str, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        try:
            with self._io_lock, open(self.dir / name, "a") as f:
                f.write(line)
        except OSError as exc:
            log.warning("could not write %s: %s", name, exc)

    # ---- creation ---------------------------------------------------------------------
    def create_residents(self, characters: list[dict]) -> None:
        """Birth the cast through the mind (runs in a background thread)."""
        self.status, self.phase = "creating", "creating residents"
        try:
            self.mind = make_mind(self.setup["version"], self.dir / "minds", self.mind_llm)
            self.mind.start()
            for index, spec in enumerate(characters[:MAX_RESIDENTS]):
                label = (spec.get("identity") or {}).get("name") or (spec.get("bio") or "")[:40]
                self.progress = {"done": index, "total": len(characters), "current": label}
                self.add_resident(spec, index)
            self.progress = {"done": len(characters), "total": len(characters), "current": ""}
            self.status, self.phase = "ready", "idle"
            self.emit("system", f"{len(self.residents)} residents arrived on {ISLAND_NAME}. "
                                f"Minds: {version_name(self.setup['version'])} "
                                f"({'LLM' if self.mind_llm else 'offline'}); world brain: {self.brain}.")
            self._schedule_day()
            self.save()
        except Exception as exc:
            log.exception("world creation failed")
            self.status, self.phase, self.error = "error", "idle", str(exc)
            self.emit("system", f"Creation failed: {exc}")

    def add_resident(self, spec: dict, index: int) -> Resident:
        assert self.mind is not None
        mind_spec = {"mode": spec.get("mode", "bio")}
        if mind_spec["mode"] == "manual":
            identity = dict(spec.get("identity") or {})
            if spec.get("bio"):
                identity.setdefault("bio", spec["bio"])
            mind_spec.update(identity=identity, levels=spec.get("levels") or {})
        else:
            bio = (spec.get("bio") or "").strip()
            name = (spec.get("name") or "").strip()
            if name and name.lower() not in bio.lower():
                bio = f"{name}, {bio}"
            mind_spec["bio"] = bio
        created = self.mind.create(mind_spec)
        rid, n = _slug(created.name), 2
        while rid in self.residents:
            rid, n = f"{_slug(created.name)}-{n}", n + 1
        hx, hz = home_slot(index)
        job = spec.get("job") if spec.get("job") in JOBS else "none"
        r = Resident(id=rid, name=created.name, ref=created.ref,
                     color=spec.get("color") or PALETTE[index % len(PALETTE)],
                     identity=created.identity, job=job, goal=(spec.get("goal") or "").strip()[:200],
                     home=home_id(rid), home_xy=[hx, hz], spec=spec, created_via=created.via,
                     state=created.state)
        with self.lock:
            self.residents[rid] = r
            r.place = r.home
            r.x, r.z = self._spot(r.home, rid)
            r.path = [[r.x, r.z]]
            for other in self.residents.values():
                if other.id != rid:
                    other.relationships.setdefault(rid, {"affinity": 0.0, "talks": 0, "last": ""})
                    r.relationships.setdefault(other.id, {"affinity": 0.0, "talks": 0, "last": ""})
        self.emit("born", f"{r.name} was born ({created.via or 'created'}) -- {JOBS[job]['title'].lower()}.",
                  actor=rid)
        return r

    # ---- director ---------------------------------------------------------------------
    def _schedule_day(self) -> None:
        day = day_of(self.clock)
        if self.scheduled_day == day:
            return
        self.scheduled_day = day
        event = director.community_event(day)
        self._add_event(event, source="community", announce=False)

    def _add_event(self, event: dict, *, source: str, announce: bool = True) -> dict:
        self.event_seq += 1
        event = {"id": self.event_seq, "kind": event.get("kind", "incident"), "title": event["title"],
                 "text": event["text"], "place": event.get("place"), "start": event["start"],
                 "end": event["end"], "target": event.get("target"), "seen_by": [], "source": source,
                 "announced": announce}
        self.events.append(event)
        if announce:
            self.emit("event", event["text"], title=event["title"], place=event["place"],
                      place_name=self.place_name(event["place"]) if event["place"] else "the whole island",
                      target=event["target"], source=source)
        return event

    def active_events(self) -> list[dict]:
        return [e for e in self.events if e["start"] <= self.clock < e["end"]]

    def _event_public(self, e: dict) -> dict:
        return {"id": e["id"], "title": e["title"], "text": e["text"], "place": e["place"],
                "place_name": self.place_name(e["place"]) if e["place"] else "the whole island",
                "start": e["start"], "end": e["end"], "kind": e["kind"], "target": e["target"],
                "active": e["start"] <= self.clock < e["end"], "source": e["source"]}

    def _direct(self) -> bool:
        """Start of a beat: weather, ferry, event activation. Under the lock. Returns whether
        an incident is due this hour (created by ``_add_incident`` -- the LLM director, if
        any, is called outside the lock)."""
        self._schedule_day()
        now = self.clock
        hour_key = now // 60
        hour = hour_of(now)
        self.weather_changed = False
        new_hour = hour_key != self.last_hour_key
        self.last_hour_key = hour_key
        if new_hour and self.beat > 0:
            weather = director.next_weather(self.rng, self.weather)
            if weather != self.weather:
                self.weather, self.weather_changed = weather, True
                self.emit("weather", f"The weather turned: {director.WEATHER_TEXT[weather]}.", weather=weather)
        incident = False
        if new_hour and WAKE_HOUR <= hour < BED_HOUR:
            if hour in (10, 17):
                self._ferry(now)
            incident = director.incident_due(self.rng, self.intensity)
        for e in self.events:                  # scheduled events (community, follow-ups) announce on start
            if e["start"] <= now < e["end"] and not e.get("announced", True):
                e["announced"] = True
                self.emit("event", e["text"], title=e["title"], place=e["place"],
                          place_name=self.place_name(e["place"]) if e["place"] else "the whole island",
                          target=e.get("target"), source=e["source"])
        self.events = [e for e in self.events if e["end"] > now - 120]   # keep a little history
        return incident

    def _ferry(self, now: int) -> None:
        if self.weather == "storm":
            self._add_event({"title": "No ferry", "text": "The ferry did not come because of the storm; the pier is empty.",
                             "place": "dock", "start": now, "end": now + 60, "kind": "ferry"}, source="ferry")
            return
        self._add_event({"title": "Ferry arrived", "text": "The mainland ferry docked and unloaded mail sacks, crates and a few passengers.",
                         "place": "dock", "start": now, "end": now + 45, "kind": "ferry"}, source="ferry")
        letter = director.roll_letter(self.rng, self.intensity, sorted(self.residents))
        if letter:
            rid, text = letter
            self.residents[rid].pending.append({"k": "letter", "text": text})
            self.emit("letter", text, actor=rid)

    def _llm_incident(self) -> dict | None:
        """LLM director (outside the lock); None -> the deck is used."""
        if self.brain != "llm" or not llm.available():
            return None
        with self.lock:
            context = self._director_context()
        try:
            return director.llm_event(context, stats=self.stats)
        except Exception as exc:
            log.info("LLM director fell back to the deck: %s", exc)
            return None

    def _add_incident(self, spec: dict | None) -> None:
        """Under the lock: add the LLM's event, or one from the seeded deck."""
        now = self.clock
        if spec:
            event = {"title": spec["title"], "text": spec["text"], "place": spec["place"], "start": now,
                     "end": now + spec["minutes"], "kind": "incident",
                     "target": spec["target"] if spec.get("target") in self.residents else None}
            source = "llm"
        else:
            rng = random.Random(f"{self.seed}:{now}:incident")
            event = director.pick_incident(rng, self.intensity, now, [r.name for r in self.residents.values()])
            source = "deck"
        added = self._add_event(event, source=source)
        follow = director.follow_up(added)
        if follow:
            hour = hour_of(follow["start"])
            if hour >= BED_HOUR or hour < WAKE_HOUR:           # nobody is up: they hear it at breakfast
                length = follow["end"] - follow["start"]
                day = day_of(follow["start"]) + (1 if hour >= BED_HOUR else 0)
                follow["start"] = (day - 1) * 1440 + WAKE_HOUR * 60
                follow["end"] = follow["start"] + length
            self._add_event(follow, source="deck", announce=False)

    def _director_context(self) -> dict:
        return {
            "time": time_text(self.clock), "weather": self.weather,
            "residents": [{"id": r.id, "name": r.name, "job": JOBS[r.job]["title"], "goal": r.goal,
                           "doing": r.doing, "where": self.place_name(r.place)} for r in self.residents.values()],
            "recent_events": [e["title"] for e in self.events[-6:]],
            "recent_happenings": [i["text"] for i in list(self.feed)[-12:] if i["kind"] in ("say", "outcome", "event")],
        }

    # ---- injection (god mode) ---------------------------------------------------------
    def inject(self, kind: str, text: str, *, target: str | None = None, place: str | None = None,
               minutes: int = 120, title: str | None = None) -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("text is required")
        with self.lock:
            if kind == "whisper":
                if target not in self.residents:
                    raise ValueError("pick a resident to whisper to")
                self.residents[target].pending.append({"k": "whisper", "text": text})
                return self.emit("inject", text, actor=target, mode="whisper")
            if place and place not in LOCATIONS:
                raise ValueError("unknown place")
            event = self._add_event({"title": title or "Something happened", "text": text, "place": place,
                                     "start": self.clock, "end": self.clock + max(15, min(720, int(minutes))),
                                     "kind": "incident"}, source="inject")
            return {"event": self._event_public(event)}

    # ---- the beat ---------------------------------------------------------------------
    def run_beat(self) -> bool:
        with self.lock:
            if self.busy or self.status not in ("ready", "running"):
                return False
            self.busy = True
        started = time.monotonic()
        with self.lock:
            self.beat_dwell = 0.0
        try:
            if hour_of(self.clock) >= BED_HOUR or hour_of(self.clock) < WAKE_HOUR:
                self._night()
            else:
                self._day_beat()
            self.save()
        finally:
            with self.lock:
                self.busy = False
                self.phase = "idle"
                self.last_beat_seconds = round(time.monotonic() - started, 2)
        return True

    def _context(self, r: Resident) -> dict:
        hour = hour_of(self.clock)
        people = []
        for o in self.residents.values():
            if o.id == r.id or o.asleep:
                continue
            people.append({"id": o.id, "name": o.name, "place": o.place, "place_name": self.place_name(o.place),
                           "here": o.place == r.place, "doing": o.doing})
        elsewhere = {}
        for pid in LOCATIONS:
            if pid != r.place:
                acts = available_activities(pid, r.job, hour)
                if acts:
                    elsewhere[pid] = acts
        if r.place != r.home:
            elsewhere[r.home] = available_activities(r.home, r.job, hour)
        upcoming = [e for e in self.events if e["start"] > self.clock and e["start"] - self.clock <= 180]
        events = [self._event_public(e) for e in self.active_events()
                  if not e.get("target") or e["target"] == r.id] + [
            {**self._event_public(e), "title": f"{e['title']} (later, from {hour_of(e['start']):02d}:00)"}
            for e in upcoming if not e.get("target")]
        job = JOBS.get(r.job, JOBS["none"])
        inclination = None
        if r.inclination and r.inclination.get("beat") == self.beat - 1 and r.inclination["to"] in self.residents:
            inclination = {**r.inclination, "to_name": self.residents[r.inclination["to"]].name}
        return {
            "id": r.id, "name": r.name, "identity": r.identity, "job": r.job, "job_title": job["title"],
            "job_hours": job["hours"], "goal": r.goal, "state": r.state, "state_text": describe_state(r.state),
            "inclination": inclination, "place": r.place, "place_name": self._my_place_name(r, r.place),
            "activity_id": r.activity_id, "hour": hour, "time_text": time_text(self.clock),
            "daypart": daypart(hour), "beat_minutes": self.beat_minutes, "weather": self.weather,
            "weather_text": director.WEATHER_TEXT[self.weather], "events": events,
            "here": [p for p in people if p["here"]], "people": people,
            "talking_with": [{"id": i, "name": self.residents[i].name} for i in r.talking_with if i in self.residents],
            "conversation_len": r.conversation_len, "last_experience": r.last_experience,
            "recent": r.recent[-5:-1], "visited_today": list(r.visited_today),
            "relationships": [{"id": oid, "name": self.residents[oid].name, "affinity": rel["affinity"],
                               "talks": rel["talks"], "last": rel.get("last", ""),
                               "feeling": _feeling(rel["affinity"], rel["talks"])}
                              for oid, rel in r.relationships.items() if oid in self.residents],
            "options": {"here": available_activities(r.place, r.job, hour), "elsewhere": elsewhere},
        }

    def _plan(self, contexts: dict[str, dict]) -> dict[str, dict]:
        def one(rid: str) -> dict:
            ctx = contexts[rid]
            rng = random.Random(f"{self.seed}:{self.beat}:{rid}:plan")
            if self.brain == "llm" and llm.available():
                try:
                    return planner.plan_llm(ctx, stats=self.stats)
                except Exception as exc:
                    log.info("planner fell back to rules for %s: %s", rid, exc)
            return planner.plan_rules(ctx, rng)

        ids = list(contexts)
        with ThreadPoolExecutor(max_workers=max(1, min(8, len(ids)))) as pool:
            return dict(zip(ids, pool.map(one, ids)))

    def _day_beat(self) -> None:
        with self.lock:
            self.phase = "planning"
            incident = self._direct()
        spec = self._llm_incident() if incident else None
        with self.lock:
            if incident:
                self._add_incident(spec)
            awake = [r for r in self.residents.values() if not r.asleep]
            contexts = {r.id: self._context(r) for r in awake}
        actions = self._plan(contexts)
        with self.lock:
            self.phase = "resolving"
            plan = self._resolve(actions)
            self.phase = "living"
        self._live(plan)
        with self.lock:
            self.morning = False
            self.beat += 1
            self.clock += self.beat_minutes

    def _resolve(self, actions: dict[str, dict]) -> dict:
        """Decide what actually happened this beat. Under the lock. Returns the beat plan."""
        hour = hour_of(self.clock)
        awake = [r for r in self.residents.values() if not r.asleep]
        R = self.residents
        start = {r.id: r.place for r in awake}
        dest: dict[str, str] = {}
        talk: dict[str, str] = {}
        for r in awake:
            a = actions[r.id]
            if a["type"] == "do":
                dest[r.id] = activity_place(a["activity"], r.id)
            elif a["type"] == "go":
                dest[r.id] = a["place"]
            elif a["type"] == "home":
                dest[r.id] = r.home
            elif a["type"] == "talk" and a.get("person") in R and not R[a["person"]].asleep and a["person"] != r.id:
                talk[r.id] = a["person"]
            else:
                dest[r.id] = r.place
                actions[r.id] = {**a, "type": "rest"}

        # Break talk cycles: in A<->B keep the one with something to say (an inclination) as the
        # speaker; the other just listens where they are.
        def inclined(rid: str) -> bool:
            inc = R[rid].inclination
            return bool(inc and inc["to"] == talk.get(rid) and inc.get("beat") == self.beat - 1)

        for rid in sorted(talk):
            chain, node = [], rid
            while node in talk and node not in chain:
                chain.append(node)
                node = talk[node]
            if node in chain:                                   # a cycle
                cycle = sorted(chain[chain.index(node):])
                keep = next((c for c in cycle if inclined(c)), None)
                drop = next(c for c in cycle if c != keep)
                talk.pop(drop, None)
                dest[drop] = R[drop].place
                actions[drop] = {"type": "listen", "intent": actions[drop].get("intent", ""),
                                 "source": actions[drop].get("source", "")}

        brushoff: set[str] = set()

        def final(rid: str, depth: int = 0) -> str:
            if rid in dest:
                return dest[rid]
            target = talk[rid]
            target_dest = final(target, depth + 1) if depth < 12 else R[target].place
            leaving = target_dest != start[target] and talk.get(target) != rid
            if leaving and start[rid] == start[target]:
                brushoff.add(rid)                               # they walked off mid-sentence
                dest[rid] = start[rid]
            else:
                dest[rid] = target_dest
            return dest[rid]

        for rid in list(talk):
            final(rid)

        # Lines: an inclination (MindForm's own reply to the last thing said) beats the planner.
        lines: dict[str, tuple[str, str]] = {}
        for rid, target in talk.items():
            inc = R[rid].inclination
            if inc and inc["to"] == target and inc.get("beat") == self.beat - 1 and inc.get("text"):
                lines[rid] = (inc["text"], "mind")
            elif actions[rid].get("say"):
                lines[rid] = (actions[rid]["say"], actions[rid].get("source", "planner"))
            else:
                rng = random.Random(f"{self.seed}:{self.beat}:{rid}:say")
                if target in R[rid].talking_with:
                    lines[rid] = (planner.continuation(rng), "rules")
                else:
                    ctx = {"state": R[rid].state, "events": [self._event_public(e) for e in self.active_events()]}
                    lines[rid] = (planner._opener(ctx, {"name": R[target].name}, rng), "rules")

        addressed_by: dict[str, list[str]] = {r.id: [] for r in awake}
        for rid, target in talk.items():
            addressed_by[target].append(rid)

        # Conversation clusters (connected by talk), for overhearing and "talking nearby".
        parent = {r.id: r.id for r in awake}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for rid, target in talk.items():
            if rid not in brushoff:
                parent[find(rid)] = find(target)
        clusters: dict[str, list[str]] = {}
        for r in awake:
            clusters.setdefault(find(r.id), []).append(r.id)

        items: dict[str, list[dict]] = {r.id: [] for r in awake}
        doing: dict[str, str] = {}
        outcomes: dict[str, str] = {}
        for r in awake:
            a = actions[r.id]
            if self.morning:
                items[r.id].append({"k": "wake", "time": f"{WAKE_HOUR}:00",
                                    "weather": director.WEATHER_TEXT[self.weather]})
            if self.weather_changed:
                items[r.id].append({"k": "weather", "text": f"The weather turned: {director.WEATHER_TEXT[self.weather]}."})
            for e in self.active_events():                  # island-wide / private news first
                if e["place"] is None and (not e.get("target") or e["target"] == r.id) and r.id not in e["seen_by"]:
                    e["seen_by"].append(r.id)
                    items[r.id].append({"k": "event", "text": e["text"]})
            if dest[r.id] != r.place:
                items[r.id].append({"k": "move", "from": self._my_place_name(r, r.place),
                                    "to": self._my_place_name(r, dest[r.id])})
            if a["type"] == "do":
                text, quality = self._outcome_q(r, a["activity"])
                outcomes[r.id] = (text, quality)
                items[r.id].append({"k": "outcome", "text": text})
                doing[r.id] = doing_text(a["activity"])
            elif a["type"] == "go":
                if dest[r.id] == r.place:
                    items[r.id].append({"k": "stay", "place": self._my_place_name(r, r.place)})
                doing[r.id] = f"looking around {self.place_name(dest[r.id])}"
            elif a["type"] == "home":
                text = self._outcome(r, "home.rest")
                items[r.id].append({"k": "outcome", "text": text})
                doing[r.id] = "resting at home"
            elif a["type"] == "rest":
                if dest[r.id] == r.home:
                    items[r.id].append({"k": "outcome", "text": self._outcome(r, "home.rest")})
                else:
                    items[r.id].append({"k": "stay", "place": self._my_place_name(r, dest[r.id])})
                doing[r.id] = "taking it easy"
            elif a["type"] == "listen":
                doing[r.id] = f"talking with {R[talk.get(r.id) or (addressed_by[r.id] or [r.id])[0]].name}"
            if r.id in talk:
                target = talk[r.id]
                line = lines[r.id][0]
                items[r.id].append({"k": "said", "to": R[target].name, "line": line})
                if r.id in brushoff:
                    items[r.id].append({"k": "brushoff", "who": R[target].name,
                                        "to_place": self.place_name(dest[target])})
                    doing[r.id] = f"watching {R[target].name} walk off"
                else:
                    doing[r.id] = f"talking with {R[target].name}"
            for speaker in addressed_by[r.id]:
                line = lines[speaker][0]
                if speaker in brushoff:
                    items[r.id].append({"k": "heard_leaving", "who": R[speaker].name, "line": line})
                else:
                    items[r.id].append({"k": "heard", "who": R[speaker].name, "line": line,
                                        "approach": start[speaker] != dest[r.id],
                                        "while": doing_text(a["activity"]) if a["type"] == "do" else None})
            # Overheard: other lines inside my conversation cluster.
            cluster = clusters[find(r.id)]
            for speaker, target in talk.items():
                if speaker in brushoff or speaker == r.id or target == r.id:
                    continue
                if speaker in cluster and r.id in cluster:
                    items[r.id].append({"k": "overheard", "who": R[speaker].name, "to": R[target].name,
                                        "line": lines[speaker][0]})

        for r in awake:                                         # who else was there
            place = dest[r.id]
            mine = set(clusters[find(r.id)])
            others = [o for o in awake if o.id != r.id and dest[o.id] == place and o.id not in mine]
            if others and not place.startswith("home:"):
                items[r.id].append({"k": "present", "people": [{"name": o.name, "doing": doing.get(o.id, "nearby")}
                                                                for o in others[:4]]})
            seen_pairs = set()
            for o in others:
                if o.id in talk and talk[o.id] not in mine and dest[talk[o.id]] == place and o.id not in brushoff:
                    pair = tuple(sorted((o.id, talk[o.id])))
                    if pair not in seen_pairs:
                        seen_pairs.add(pair)
                        items[r.id].append({"k": "convo_nearby", "a": o.name, "b": R[talk[o.id]].name})
            for e in self.active_events():                       # what is going on right here
                if e["place"] == place and (not e.get("target") or e["target"] == r.id) and r.id not in e["seen_by"]:
                    e["seen_by"].append(r.id)
                    items[r.id].append({"k": "event", "text": e["text"]})
            items[r.id].extend(r.pending)
            r.pending = []

        # Commit movement + what everyone visibly does (the client animates this right away).
        for r in awake:
            a = actions[r.id]
            new_place = dest[r.id]
            if new_place != r.place:
                frm = (r.x, r.z)
                to = self._spot(new_place, r.id)
                r.path = path_between(frm, r.place, to, new_place)
                r.x, r.z = to
            else:
                r.path = [[r.x, r.z]]
            r.place = new_place
            r.path_t = self.clock
            key = "home" if new_place.startswith("home:") else new_place
            if key not in r.visited_today:
                r.visited_today.append(key)
            r.doing = doing.get(r.id, "around")
            r.activity_id = a.get("activity") if a["type"] == "do" else None
            r.intent = a.get("intent", "")
            r.plan_source = a.get("source", "")
            partners = sorted(set(([talk[r.id]] if r.id in talk and r.id not in brushoff else []) +
                                  [s for s in addressed_by[r.id] if s not in brushoff]))
            r.conversation_len = r.conversation_len + 1 if partners and set(partners) & set(r.talking_with) else (1 if partners else 0)
            r.talking_with = partners
            self.emit("plan", r.intent, actor=r.id, action=a["type"], doing=r.doing,
                      place=new_place, place_name=self.place_name(new_place), source=r.plan_source)
            if r.id in outcomes:
                text, quality = outcomes[r.id]
                # A notable result (went well / went wrong) is a small moment on screen; routine ones
                # only go to the story -- unless they are about to talk, which says more.
                notable = quality != "0" and r.id not in talk and not addressed_by[r.id]
                self.emit("outcome", text, actor=r.id, place=new_place, quality=quality, notable=notable,
                          dwell=1.4 if notable else 0.0)
        for rid, target in talk.items():
            self.emit("say", lines[rid][0], actor=rid, to=target, to_name=R[target].name,
                      source=lines[rid][1], brushoff=rid in brushoff)

        # Turn order: a speaker lives their beat after the one they spoke to has answered.
        level: dict[str, int] = {}

        def lvl(rid: str, depth: int = 0) -> int:
            if rid in level:
                return level[rid]
            if rid not in talk or rid in brushoff or depth > 12:
                level[rid] = 0
            else:
                level[rid] = lvl(talk[rid], depth + 1) + 1
            return level[rid]

        for r in awake:
            lvl(r.id)
        return {"items": items, "talk": talk, "brushoff": brushoff, "addressed_by": addressed_by,
                "levels": level, "dest": dest, "actions": actions}

    def _outcome(self, r: Resident, activity_id: str) -> str:
        return self._outcome_q(r, activity_id)[0]

    def _outcome_q(self, r: Resident, activity_id: str) -> tuple[str, str]:
        """(fact, quality) -- quality "+"/"-"/"0" is world odds, shown on screen, never to the mind."""
        act = ACTIVITIES[activity_id]
        bad = self.weather in director.BAD_WEATHER
        rng = random.Random(f"{self.seed}:{self.beat}:{r.id}:{activity_id}:outcome")
        skilled = JOBS.get(r.job, JOBS["none"])["place"] == act["place"]
        pool = []
        for weight, quality, text, cond in act["outcomes"]:
            if (cond == "bad" and not bad) or (cond == "fair" and bad):
                continue
            w = float(weight)
            if skilled and quality == "+":
                w *= 1.6
            elif skilled and quality == "-":
                w *= 0.7
            pool.append((w, text, quality))
        if not pool:
            return f"I spent the time trying to {act['label']}.", "0"
        _, text, quality = rng.choices(pool, weights=[w for w, _, _ in pool])[0]
        return text, quality

    def _live(self, plan: dict) -> None:
        """Narrate + form, level by level (listeners before the speakers who need their answers)."""
        R = self.residents
        talk, brushoff, addressed_by = plan["talk"], plan["brushoff"], plan["addressed_by"]
        replies: dict[str, str | None] = {}
        by_level: dict[int, list[str]] = {}
        for rid, lv in plan["levels"].items():
            by_level.setdefault(lv, []).append(rid)

        def live_one(rid: str):
            r = R[rid]
            items = list(plan["items"][rid])
            if rid in talk and rid not in brushoff:
                target = talk[rid]
                answer = replies.get(target)
                items.append({"k": "answer", "who": R[target].name, "line": answer} if answer
                             else {"k": "no_answer", "who": R[target].name})
            elif any(s not in brushoff for s in addressed_by.get(rid, [])):
                # The last fact is who is waiting on them, so their mind's reply is addressed to them.
                speaker = next(s for s in addressed_by[rid] if s not in brushoff)
                items.append({"k": "waiting", "who": R[speaker].name})
            with self.lock:
                header = {"name": r.name, "time_text": time_text(self.clock),
                          "weather_text": director.WEATHER_TEXT[self.weather],
                          "place_name": self.place_name(r.place)}
            text, narr_source = narrator.narrate(items, header, use_llm=self.brain == "llm", stats=self.stats)
            try:
                turn = self.mind.experience(r.ref, text)
                error = None
            except Exception as exc:
                log.warning("mind turn failed for %s: %s", rid, exc)
                turn, error = None, str(exc)
            return rid, items, text, narr_source, turn, error

        for lv in sorted(by_level):
            members = by_level[lv]
            with ThreadPoolExecutor(max_workers=max(1, min(8, len(members)))) as pool:
                results = list(pool.map(live_one, members))
            for rid, items, text, narr_source, turn, error in results:
                replies[rid] = turn.reply if turn else None
                self._settle(rid, items, text, narr_source, turn, error, plan)

    def _settle(self, rid, items, text, narr_source, turn, error, plan) -> None:
        R = self.residents
        talk, brushoff, addressed_by = plan["talk"], plan["brushoff"], plan["addressed_by"]
        with self.lock:
            r = R[rid]
            r.last_experience = text
            r.recent = (r.recent + [f"[{time_text(self.clock)}] {text}"])[-12:]
            r.mind_error = error
            if error:
                self.emit("system", f"{r.name}'s mind could not take this experience: {error}", actor=rid)
            appraisal = turn.appraisal if turn else None
            if turn:
                r.turns += 1
                r.state = turn.state or r.state
                r.reply = turn.reply
                r.last_appraisal = appraisal
            heard_from = [s for s in addressed_by.get(rid, []) if s not in brushoff]
            leaving_from = [s for s in addressed_by.get(rid, []) if s in brushoff]
            self.emit("experience", text, actor=rid, source=narr_source,
                      appraisal=appraisal, mind_source=(turn.state.get("sources") if turn else None))
            emotion = read_emotion(appraisal)
            if emotion:
                r.mood = {**emotion, "t": self.clock}
            spoke = False
            if turn and turn.reply:
                if heard_from:
                    self.emit("say", turn.reply, actor=rid, to=heard_from[0], to_name=R[heard_from[0]].name,
                              source="mind", answer=True, emotion=emotion)
                    spoke = True
                else:
                    self.emit("reaction", turn.reply, actor=rid, source="mind", emotion=emotion)
            if emotion:
                headline = emotion["strength"] >= HEADLINE_THRESHOLD
                # Every reading updates their face; strong ones get a moment of their own on screen
                # (none when it plays alongside their spoken line).
                self.emit("emotion", f"{r.name} felt {emotion['label']}", actor=rid, headline=headline,
                          shown=emotion["strength"] >= SHOW_THRESHOLD,
                          dwell=0.0 if (spoke or not headline) else None, **emotion)
            if rid in talk and turn and turn.reply and rid not in brushoff:
                r.inclination = {"to": talk[rid], "text": turn.reply, "beat": self.beat}
            elif heard_from and turn and turn.reply:
                r.inclination = {"to": heard_from[0], "text": turn.reply, "beat": self.beat}
            else:
                r.inclination = None
            if turn and turn.formation:
                note = {"t": self.clock, "time": time_text(self.clock), **turn.formation}
                r.formation_log = (r.formation_log + [note])[-40:]
                self.emit("formation", f"{r.name} {turn.formation.get('note', 'changed')}", actor=rid,
                          key=turn.formation.get("key"), delta=turn.formation.get("delta"))
            # Relationships move with how THIS resident read the encounter (MindForm's valence).
            valence = float((appraisal or {}).get("valence", 0.0) or 0.0)
            involved = {}
            if rid in talk:
                target = talk[rid]
                involved[target] = (f"{R[target].name} walked off while you were talking" if rid in brushoff
                                    else f"talked at {self.place_name(r.place)}")
            for s in heard_from:
                involved[s] = f"talked at {self.place_name(r.place)}"
            for s in leaving_from:
                involved[s] = f"you walked off while {R[s].name} was talking"
            for oid, what in involved.items():
                rel = r.relationships.setdefault(oid, {"affinity": 0.0, "talks": 0, "last": ""})
                before = _feeling(rel["affinity"], rel["talks"]) if rel["talks"] else None
                rel["affinity"] = max(-1.0, min(1.0, rel["affinity"] + AFFINITY_RATE * valence * (1 - abs(rel["affinity"]))))
                rel["talks"] += 1
                rel["last"] = f"{what} ({time_text(self.clock)})"
                after = _feeling(rel["affinity"], rel["talks"])
                if before is not None and after != before:          # a relationship turned a corner
                    warmer = _FEELING_ORDER.index(after) > _FEELING_ORDER.index(before)
                    self.emit("bond", f"{r.name} now feels {after} toward {R[oid].name}", actor=rid,
                              other=oid, other_name=R[oid].name, feeling=after, warmer=warmer,
                              affinity=round(rel["affinity"], 3))
            record = {"world": self.id, "beat": self.beat, "t": self.clock, "time": time_text(self.clock),
                      "resident": rid, "name": r.name, "place": r.place, "action": plan["actions"][rid],
                      "experience": text, "narration": narr_source, "facts": items,
                      "reply": turn.reply if turn else None, "appraisal": appraisal,
                      "formation": turn.formation if turn else None, "emotion": emotion,
                      "state": r.state, "error": error}
        self._append_jsonl("experiences.jsonl", record)

    def _night(self) -> None:
        """23:00: everyone walks home and sleeps; one experience each; skip to 07:00."""
        with self.lock:
            self.phase = "night"
            self._direct()
            awake = [r for r in self.residents.values() if not r.asleep]
            items, actions = {}, {}
            for r in awake:
                its = []
                if r.place != r.home:
                    its.append({"k": "move", "from": self._my_place_name(r, r.place), "to": "my cottage"})
                    frm = (r.x, r.z)
                    to = self._spot(r.home, r.id)
                    r.path = path_between(frm, r.place, to, r.home)
                    r.x, r.z = to
                    r.place = r.home
                else:
                    r.path = [[r.x, r.z]]
                r.path_t = self.clock
                its.append({"k": "bed"})
                its.extend(r.pending)
                r.pending = []
                r.doing, r.intent, r.activity_id, r.talking_with = "asleep", "bedtime", None, []
                r.asleep = True
                items[r.id] = its
                actions[r.id] = {"type": "sleep"}
                self.emit("plan", "bedtime", actor=r.id, action="sleep", doing="asleep",
                          place=r.home, place_name=self.place_name(r.home), source="world")
        plan = {"items": items, "talk": {}, "brushoff": set(), "addressed_by": {rid: [] for rid in items},
                "levels": {rid: 0 for rid in items}, "dest": {}, "actions": actions}
        self._live(plan)
        with self.lock:
            next_day = day_of(self.clock) + (0 if hour_of(self.clock) < WAKE_HOUR else 1)
            self.clock = (next_day - 1) * 1440 + WAKE_HOUR * 60
            self.beat += 1
            self.morning = True
            for r in self.residents.values():
                r.asleep = False
                r.doing = "waking up"
                r.visited_today = []
                r.inclination = None
                r.conversation_len = 0
            self._schedule_day()
            self.emit("system", f"A new day on {ISLAND_NAME}: {time_text(self.clock)}.")

    # ---- running ----------------------------------------------------------------------
    def start_loop(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"world-{self.id}", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self.running or self.status not in ("ready", "running"):
                self._stop.wait(0.3)
                continue
            t0 = time.monotonic()
            try:
                self.run_beat()
            except Exception as exc:
                log.exception("beat failed")
                self.running = False
                self.error = str(exc)
                self.emit("system", f"The simulation paused after an error: {exc}")
            pace = 0.0 if self.speed <= 0 else max(BASE_BEAT_SECONDS, WALK_SECONDS + self.beat_dwell) / self.speed
            remaining = pace - (time.monotonic() - t0)
            if remaining > 0:
                self._stop.wait(remaining)

    def set_running(self, running: bool) -> None:
        self.running = bool(running) and self.status in ("ready", "running")
        if self.running:
            self.error = None
        self.start_loop()

    def step(self) -> bool:
        """One beat while paused (in the background; the client sees it via polling)."""
        if self.busy or self.running or self.status not in ("ready", "running"):
            return False
        threading.Thread(target=self._safe_beat, daemon=True).start()
        return True

    def _safe_beat(self) -> None:
        try:
            self.run_beat()
        except Exception as exc:
            log.exception("beat failed")
            self.error = str(exc)
            self.emit("system", f"The beat failed: {exc}")

    def shutdown(self) -> None:
        self.running = False
        self._stop.set()
        if self.mind:
            try:
                self.mind.stop()
            except Exception:
                pass

    # ---- views & persistence ------------------------------------------------------------
    def public_state(self, since: int = 0) -> dict:
        with self.lock:
            hour = hour_of(self.clock)
            feed = [i for i in self.feed if i["seq"] > since][-300:]
            return {
                "id": self.id, "name": self.name, "island": ISLAND_NAME, "status": self.status,
                "phase": self.phase, "error": self.error, "progress": self.progress,
                "running": self.running, "speed": self.speed, "busy": self.busy,
                "clock": self.clock, "time_text": time_text(self.clock), "day": day_of(self.clock),
                "hour": hour, "minute": self.clock % 60, "daypart": daypart(hour), "beat": self.beat,
                "beat_minutes": self.beat_minutes, "weather": self.weather,
                "weather_text": director.WEATHER_TEXT[self.weather],
                "setup": {k: self.setup.get(k) for k in ("version", "world_brain", "mind_llm", "seed",
                                                         "experiences_per_hour", "intensity")}
                         | {"version_name": version_name(self.setup.get("version", ""))},
                "llm": {"available": llm.available(), "model": llm.model_label(), **self.stats.snapshot()},
                "events": [self._event_public(e) for e in self.events
                           if e["end"] > self.clock and e["start"] - self.clock <= 180 and not e.get("target")],
                "residents": [r.public(self) for r in self.residents.values()],
                "feed": feed, "feed_seq": self.feed_seq, "last_beat_seconds": self.last_beat_seconds,
                "base_beat_seconds": BASE_BEAT_SECONDS, "walk_seconds": WALK_SECONDS,
            }

    def summary(self) -> dict:
        return {"id": self.id, "name": self.name, "time_text": time_text(self.clock), "beat": self.beat,
                "status": self.status, "residents": [r.name for r in self.residents.values()],
                "setup": {k: self.setup.get(k) for k in ("version", "world_brain", "mind_llm", "seed",
                                                         "experiences_per_hour", "intensity")},
                "version_name": version_name(self.setup.get("version", "")), "loaded": True}

    def to_json(self) -> dict:
        with self.lock:
            return {
                "id": self.id, "setup": self.setup, "clock": self.clock, "beat": self.beat,
                "weather": self.weather, "morning": self.morning, "events": self.events,
                "event_seq": self.event_seq, "scheduled_day": self.scheduled_day,
                "last_hour_key": self.last_hour_key, "feed_seq": self.feed_seq,
                "rng": self.rng.getstate(), "status": "ready" if self.status == "running" else self.status,
                "residents": [asdict(r) for r in self.residents.values()],
            }

    def save(self) -> None:
        data = self.to_json()
        tmp = self.dir / "world.json.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        tmp.replace(self.dir / "world.json")

    @classmethod
    def load(cls, root: Path) -> "World":
        data = json.loads((root / "world.json").read_text())
        world = cls(data["id"], root, data["setup"])
        world.clock, world.beat, world.weather = data["clock"], data["beat"], data["weather"]
        world.morning, world.events = data.get("morning", False), data.get("events", [])
        world.event_seq, world.scheduled_day = data.get("event_seq", 0), data.get("scheduled_day", 0)
        world.last_hour_key, world.feed_seq = data.get("last_hour_key", -1), data.get("feed_seq", 0)
        state = data.get("rng")
        if state:
            world.rng.setstate((state[0], tuple(state[1]), state[2]))
        for rd in data.get("residents", []):
            world.residents[rd["id"]] = Resident(**rd)
        world.status = data.get("status", "ready")
        try:                                     # recent feed for the client
            lines = (root / "feed.jsonl").read_text().splitlines()[-300:]
            world.feed.extend(json.loads(line) for line in lines if line.strip())
        except OSError:
            pass
        return world
