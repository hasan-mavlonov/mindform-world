"""One world: the island clock, its residents, and the beat that moves everything.

A BEAT is one stretch of island time (60 / experiences-per-hour minutes). Each beat:

    1. DIRECT   weather, the ferry, community events, incidents          (director.py)
    2. PLAN     each awake resident picks an action from their MindForm state (planner.py)
    3. ARRANGE  the world decides what actually happens: who goes where, who talks to whom
                (planned conversations, plus people who end up at the same place or cross
                paths on the road), and what each conversation is about (intrigue.py)
    4. VOICE    opening lines are written in the speaker's own voice (voice.py)
    5. COMMIT   the facts: who walked where, what came of it, who said what, who walked off
                mid-sentence, who saw what (a secret being worked on), who else was there
    6. LIVE     per resident: the facts are narrated as a first-person experience
                (narrator.py) and handed to their mind (MindForm), which forms them and
                answers; the answer is voiced in their own words
    7. SETTLE   affection moves with how each resident read the encounter (MindForm's
                valence), trust with what visibly happened (a confession, a walk-off, a lie
                found out); secrets spread; traits that really moved get a card; everything
                is logged and saved

Conversations run INSIDE a beat: the speaker's line is heard, the listener's mind answers,
the speaker hears that answer and has the last word (which the listener hears next beat).

At 23:00 everyone walks home and sleeps, the day's recap card plays, and the night is
skipped to 07:00.
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
from app.world import director, intrigue, narrator, planner, presets, recap
from app.world.emotion import HEADLINE_THRESHOLD, SHOW_THRESHOLD, read_emotion
from app.world.places import (ACTIVITIES, ACTIVITY_TOPIC, ISLAND_NAME, JOBS, LOCATIONS, activity_place,
                              available_activities, doing_text, home_id, home_slot, path_between)
from app.world.voice import RECENT_LINES, VoiceBook, llm_line, near_duplicate, shares_run

log = logging.getLogger("mindform.world")

WAKE_HOUR = 7
BED_HOUR = 23
MAX_RESIDENTS = 10
FEED_KEEP = 800
BASE_BEAT_SECONDS = 7.0          # shortest beat at 1x (real seconds)
WALK_SECONDS = 6.0               # at 1x, each beat opens with ~6 s of walking before anyone speaks
WALK_SPEED = 2.4                 # world units per second at 1x (the client walks at the same pace)
# Watchable pacing: every "headline" (a line said, an event, a strong emotion, a bond) gets this
# many seconds on screen at 1x; the beat waits for its headlines before the next one runs.
DWELL = {"event": 2.6, "letter": 2.6, "weather": 2.0, "inject": 1.8, "bond": 1.8, "emotion": 1.5,
         "secret": 3.0, "shift": 2.6, "recap": 7.0}
AFFINITY_RATE = 0.2              # how far one encounter's valence (MindForm's reading) moves affection
TRAIT_SMOOTH = 0.15              # traits on screen follow MindForm through a slow moving average...
SHIFT_STEP = 0.09                # ...and a card plays when the average has moved this far since the last card
SHIFT_COOLDOWN = 16              # beats between two cards for the same resident (about four island hours)
SHIFT_REVERSAL = 2.0             # turning back the other way needs this many times the move...
REVERSAL_COOLDOWN = 32           # ...and this many beats since that trait's last card
PASS_DISTANCE = 1.5              # two walkers this close at the same moment cross paths
LINES_KEEP = 24
PALETTE = ["#f4a6c8", "#ffb565", "#84cffa", "#acf2a9", "#d7b4ff", "#ffe08a",
           "#7fe0d4", "#ff9b8a", "#b9c7ff", "#f6c6a0"]
MECHANICAL = {"gossip", "confide", "confront", "probe"}     # topics whose words the world must write


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


_FEELING_BOUNDS = {"hostile": (-1.0, -0.5), "cool": (-0.5, -0.2), "neutral": (-0.2, 0.2), "warm": (0.2, 0.5), "close": (0.5, 1.0)}
FEELING_MARGIN = 0.06            # a feeling only turns when it is clearly past the line (no flip-flopping)


def _feeling_held(affinity: float, talks: int, before: str | None) -> str:
    now = _feeling(affinity, talks)
    if before in _FEELING_BOUNDS and now != before:
        lo, hi = _FEELING_BOUNDS[before]
        if lo - FEELING_MARGIN <= affinity <= hi + FEELING_MARGIN:
            return before
    return now


def _family(emotion: dict | None) -> str:
    v = (emotion or {}).get("valence") or 0.0
    return "pos" if v >= 0.15 else "neg" if v <= -0.15 else "neutral"


def _walk(path: list, speed: float = WALK_SPEED) -> list[tuple[float, float, float]]:
    """(t, x, z) samples of a walk along ``path`` at the client's pace (eased like the client)."""
    pts = [tuple(p) for p in path]
    if len(pts) < 2:
        return [(0.0, pts[0][0], pts[0][1])] if pts else []
    seg = [math.dist(pts[i - 1], pts[i]) for i in range(1, len(pts))]
    length = sum(seg)
    dur = min(WALK_SECONDS, max(1.6, length / speed))
    out = []
    for k in range(int(dur / 0.1) + 1):
        t = k * 0.1
        p = min(1.0, t / dur)
        target = (p * p * (3 - 2 * p)) * length
        for i, s in enumerate(seg):
            if target <= s or i == len(seg) - 1:
                f = 0.0 if not s else min(1.0, target / s)
                a, b = pts[i], pts[i + 1]
                out.append((t, a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
                break
            target -= s
    return out


def crossing(path_a: list, path_b: list) -> float | None:
    """When (seconds into the walk at 1x) two walkers pass each other, or None."""
    wa, wb = _walk(path_a), _walk(path_b)
    if len(wa) < 2 or len(wb) < 2:
        return None
    n = min(len(wa), len(wb))
    for i in range(3, n - 3):                          # not at the doorstep they both left from
        if math.hypot(wa[i][1] - wb[i][1], wa[i][2] - wb[i][2]) < PASS_DISTANCE:
            return round(wa[i][0], 2)
    return None


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
    inclination: dict | None = None              # (older saves) the reply they formed last beat
    talking_with: list = field(default_factory=list)
    conversation_len: int = 0
    last_experience: str = ""
    recent: list = field(default_factory=list)
    relationships: dict = field(default_factory=dict)
    formation_log: list = field(default_factory=list)
    visited_today: list = field(default_factory=list)
    pending: list = field(default_factory=list)  # letters / whispers / parting words for the next beat
    turns: int = 0
    last_appraisal: dict | None = None
    mood: dict | None = None                     # MindForm's last reading, named (see emotion.py)
    mind_error: str | None = None
    # --- voice, secrets, what they know
    secret: dict | None = None                   # see intrigue.make_secret
    voice: dict = field(default_factory=dict)    # see voice.VoiceBook.make_profile
    said: list = field(default_factory=list)     # their recent lines (the repetition guard)
    said_ids: list = field(default_factory=list)
    knowledge: list = field(default_factory=list)
    suspicion: dict = field(default_factory=dict)
    pair_topics: dict = field(default_factory=dict)
    last_outcome: str = ""
    outcomes_seen: list = field(default_factory=list)
    former_job: str | None = None
    # --- traits as shown (smoothed) and when a change last got a card
    trait_view: dict = field(default_factory=dict)
    trait_anchor: dict = field(default_factory=dict)
    shift_dirs: dict = field(default_factory=dict)
    last_shift_beat: int = -99

    def public(self, world: "World") -> dict:
        data = asdict(self)
        for private in ("spec", "pending", "inclination", "said_ids", "pair_topics", "outcomes_seen", "trait_anchor",
                        "shift_dirs"):
            data.pop(private, None)
        R = world.residents
        data["place_name"] = world.place_name(self.place)
        data["job_title"] = JOBS.get(self.job, JOBS["none"])["title"]
        if self.former_job:
            data["former_job_title"] = JOBS.get(self.former_job, JOBS["none"])["title"]
        data["talking_with_names"] = [R[i].name for i in self.talking_with if i in R]
        rels = []
        for oid, rel in self.relationships.items():
            rel = intrigue.normalize_rel(rel)
            rels.append({"id": oid, "name": R[oid].name if oid in R else oid,
                         "affinity": round(rel["affection"], 3), "affection": round(rel["affection"], 3),
                         "trust": round(rel["trust"], 3), "talks": rel["talks"], "last": rel.get("last", ""),
                         "feeling": _feeling(rel["affection"], rel["talks"]),
                         "label": intrigue.bond_label(rel["affection"], rel["trust"], rel["talks"]),
                         "knows_their_secret": bool(oid in R and intrigue.knows_secret(self, R[oid])),
                         "suspects": round(intrigue.suspicion_of(self, oid), 2)})
        data["relationships"] = rels
        if self.secret:
            data["secret"] = {**self.secret, "known_by_names": [R[k].name for k in self.secret.get("known_by", []) if k in R]}
        data["knowledge"] = [{"about": f["about"], "name": R[f["about"]].name if f["about"] in R else f["about"],
                              "text": f["text"], "clue": f["clue"], "secret": f["secret"]} for f in self.knowledge[-8:]]
        data["said"] = self.said[-4:]
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
        self.voice_mode = "mind" if setup.get("voices") == "mind" else "styled"
        self.voices = VoiceBook(self.seed)
        self.clock = WAKE_HOUR * 60
        self.beat = 0
        self.weather = "clear"
        self.weather_changed = False
        self.weather_hold_until = 0               # god mode's storm keeps the weather this long
        self.ferry_cancelled_day = 0
        self.morning = True
        self.events: list[dict] = []
        self.event_seq = 0
        self.scheduled_day = 0
        self.last_hour_key = -1
        self.recent_incidents: list[str] = []
        self.recent_outcomes: list[str] = []
        self.letters_sent: list[list[str]] = []
        self.highlights: list[dict] = []          # today's dramatic moments (for the recap card)
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
        self.fail_streak = 0
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

    def _path_for(self, r: Resident, dest: str) -> list:
        if dest == r.place:
            return [[r.x, r.z]]
        return path_between((r.x, r.z), r.place, self._spot(dest, r.id), dest)

    @staticmethod
    def dwell_for(kind: str, text: str) -> float:
        """Seconds at 1x a headline stays the focus (long lines take longer to read)."""
        if kind == "say":
            return round(min(3.4, max(1.9, 1.3 + 0.016 * len(text))), 2)
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

    def _highlight(self, drama: int, text: str, actors: list[str]) -> None:
        self.highlights = (self.highlights + [{"drama": drama, "text": text, "actors": actors, "t": self.clock}])[-80:]

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
            self.voices.allocate(list(self.residents.values()))
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
        r.secret = intrigue.make_secret(spec.get("secret") or "", JOBS[job]["place"], r.home)
        r.voice = self.voices.make_profile(rid, created.name, spec, created.state)
        for t in created.state.get("traits") or []:
            r.trait_view[t["key"]] = r.trait_anchor[t["key"]] = float(t.get("value") or 0.0)
        with self.lock:
            self.residents[rid] = r
            r.place = r.home
            r.x, r.z = self._spot(r.home, rid)
            r.path = [[r.x, r.z]]
            for other in self.residents.values():
                if other.id != rid:
                    other.relationships.setdefault(rid, intrigue.new_rel())
                    r.relationships.setdefault(other.id, intrigue.new_rel())
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
                 "announced": announce, "topic": (event.get("topic") or director.TOPICS.get(event["title"])
                                                  or intrigue.topic_np(event["title"])),
                 **{k: event[k] for k in ("force", "personal", "far_text", "pull", "target_name") if event.get(k)}}
        self.events.append(event)
        if announce:
            self._announce(event)
        return event

    def _announce(self, e: dict) -> None:
        self.emit("event", e["text"], title=e["title"], place=e["place"],
                  place_name=self.place_name(e["place"]) if e["place"] else "the whole island",
                  target=e.get("target"), source=e["source"], event_kind=e["kind"],
                  drama=3 if e.get("force") else 1, dwell=3.4 if e.get("force") else None)

    def active_events(self) -> list[dict]:
        return [e for e in self.events if e["start"] <= self.clock < e["end"]]

    def headline_event(self) -> dict | None:
        """The thing everyone would be talking about right now, if anything."""
        act = [e for e in self.active_events() if not e.get("target")]
        forced = [e for e in act if e.get("force")]
        pool = forced or [e for e in act if e["kind"] in ("incident", "community") or e["title"] == "No ferry"]
        return pool[-1] if pool else None

    def secret_pressure(self, r: Resident) -> bool:
        if not r.secret:
            return False
        return bool(r.secret.get("confronted_by")) or any(
            intrigue.suspicion_of(o, r.id) >= 0.5 for o in self.residents.values() if o.id != r.id)

    def _event_public(self, e: dict) -> dict:
        return {"id": e["id"], "title": e["title"], "text": e["text"], "place": e["place"],
                "place_name": self.place_name(e["place"]) if e["place"] else "the whole island",
                "start": e["start"], "end": e["end"], "kind": e["kind"], "target": e["target"],
                "active": e["start"] <= self.clock < e["end"], "source": e["source"], "topic": e.get("topic"),
                "force": bool(e.get("force")), "pull": e.get("pull"), "target_name": e.get("target_name")}

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
        if new_hour and self.beat > 0 and now >= self.weather_hold_until:
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
                self._announce(e)
        self.events = [e for e in self.events if e["end"] > now - 120]   # keep a little history
        return incident

    def _ferry(self, now: int) -> None:
        if self.weather == "storm" or self.ferry_cancelled_day == day_of(now):
            self._add_event({"title": "No ferry", "text": "The ferry did not come because of the storm; the pier is empty.",
                             "place": "dock", "start": now, "end": now + 60, "kind": "ferry"}, source="ferry")
            return
        self._add_event({"title": "Ferry arrived", "text": "The mainland ferry docked and unloaded mail sacks, crates and a few passengers.",
                         "place": "dock", "start": now, "end": now + 45, "kind": "ferry"}, source="ferry")
        letter = director.roll_letter(self.rng, self.intensity, sorted(self.residents), self.letters_sent)
        if letter:
            rid, text = letter
            self.letters_sent = (self.letters_sent + [[rid, text]])[-60:]
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
        if spec and spec["title"] not in self.recent_incidents:
            event = {"title": spec["title"], "text": spec["text"], "place": spec["place"], "start": now,
                     "end": now + spec["minutes"], "kind": "incident", "topic": spec.get("topic"),
                     "target": spec["target"] if spec.get("target") in self.residents else None}
            source = "llm"
        else:
            rng = random.Random(f"{self.seed}:{now}:incident")
            event = director.pick_incident(rng, self.intensity, now, [r.name for r in self.residents.values()],
                                           exclude=set(self.recent_incidents))
            source = "deck"
        self.recent_incidents = (self.recent_incidents + [event["title"]])[-8:]
        added = self._add_event(event, source=source)
        follow = director.follow_up(added)
        if follow:
            self._add_follow(follow)

    def _add_follow(self, follow: dict) -> None:
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
            "recent_events": [e["title"] for e in self.events[-6:]] + self.recent_incidents[-4:],
            "recent_happenings": [i["text"] for i in list(self.feed)[-12:] if i["kind"] in ("say", "outcome", "event")],
        }

    # ---- injection (god mode) ---------------------------------------------------------
    def inject(self, kind: str, text: str, *, target: str | None = None, place: str | None = None,
               minutes: int = 120, title: str | None = None, force: bool = False) -> dict:
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
            spec = presets.custom(text, title, place) if force else {
                "title": title or "Something happened", "text": text, "place": place, "kind": "incident"}
            spec.update(start=self.clock, end=self.clock + max(15, min(720, int(minutes))))
            if force:
                spec["force"] = True
            event = self._add_event(spec, source="inject")
            if force:
                self._highlight(3, title or text[:80], [])
            return {"event": self._event_public(event)}

    def preset(self, name: str, *, target: str | None = None, place: str | None = None) -> dict:
        """One-click drama (god mode): see presets.py. Everyone is hit next beat and reacts."""
        with self.lock:
            if not self.residents:
                raise ValueError("no residents yet")
            if place and place not in LOCATIONS:
                raise ValueError("unknown place")
            if target and target not in self.residents:
                raise ValueError("unknown resident")
            rng = random.Random(f"{self.seed}:{self.clock}:{name}:{self.event_seq}")
            spec, effects = presets.build(self, name, target=target, place=place, rng=rng)
            follow = spec.pop("follow", None)
            rumor = spec.pop("rumor", None)
            spec["recap"] = spec.get("recap")
            spec.update(start=self.clock, end=self.clock + spec.pop("minutes"), force=True)
            if effects.get("weather"):
                if self.weather != effects["weather"]:
                    self.weather, self.weather_changed = effects["weather"], True
                self.weather_hold_until = self.clock + effects.get("weather_hold", 120)
                self.emit("weather", f"The weather turned: {director.WEATHER_TEXT[self.weather]}.", weather=self.weather)
            if effects.get("ferry_cancelled"):
                self.ferry_cancelled_day = day_of(self.clock)
            if effects.get("fire_job"):
                r = self.residents[effects["fire_job"]]
                r.former_job, r.job = r.job, "none"
            event = self._add_event(spec, source="preset")
            event["preset"] = name
            if follow:
                start = self.clock + follow["delay"]
                self._add_follow({"title": follow["title"], "text": follow["text"], "place": spec["place"],
                                  "start": start, "end": start + follow["minutes"], "kind": "incident"})
            if rumor:
                for o in self.residents.values():
                    if o.id != rumor["about"]:
                        intrigue.learn(o, rumor["about"], rumor["text"], t=self.clock, src="rumor")
            if effects.get("expose"):
                holder = self.residents[effects["expose"]]
                holder.secret["exposed"] = True
                for o in self.residents.values():
                    if o.id != holder.id and o.id not in holder.secret["known_by"]:
                        self._learn_secret(o, holder, "letter", announce=False)
                self.emit("secret", f"Everyone now knows: {intrigue.reveal_text(holder.name, holder.secret)}",
                          actor=holder.id, mode="exposed", drama=3, dwell=3.2)
            self._highlight(3, spec.get("recap") or spec["title"], [])
            return {"event": self._event_public(event), "preset": name}

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
        secret = None
        if r.secret:
            lo, hi = intrigue.secret_hours(r.secret)
            secret = {**r.secret, "hours": [lo, hi], "place_name": self.place_name(r.secret["place"]),
                      "known_by_names": [self.residents[k].name for k in r.secret.get("known_by", []) if k in self.residents]}
        rels = []
        for oid, rel in r.relationships.items():
            if oid not in self.residents:
                continue
            rel = intrigue.normalize_rel(rel)
            rels.append({"id": oid, "name": self.residents[oid].name, "affinity": rel["affection"],
                         "affection": rel["affection"], "trust": rel["trust"], "talks": rel["talks"],
                         "last": rel.get("last", ""), "feeling": _feeling(rel["affection"], rel["talks"]),
                         "label": intrigue.bond_label(rel["affection"], rel["trust"], rel["talks"])})
        return {
            "id": r.id, "name": r.name, "identity": r.identity, "job": r.job, "job_title": job["title"],
            "job_hours": job["hours"], "goal": r.goal, "state": r.state, "state_text": describe_state(r.state),
            "inclination": None, "place": r.place, "place_name": self._my_place_name(r, r.place),
            "activity_id": r.activity_id, "hour": hour, "time_text": time_text(self.clock),
            "daypart": daypart(hour), "beat_minutes": self.beat_minutes, "weather": self.weather,
            "weather_text": director.WEATHER_TEXT[self.weather], "events": events,
            "here": [p for p in people if p["here"]], "people": people,
            "talking_with": [{"id": i, "name": self.residents[i].name} for i in r.talking_with if i in self.residents],
            "conversation_len": r.conversation_len, "last_experience": r.last_experience,
            "recent": r.recent[-5:-1], "visited_today": list(r.visited_today),
            "relationships": rels, "secret": secret, "voice": (r.voice or {}).get("profile", ""),
            "knowledge": [{"about": f["about"], "name": self.residents[f["about"]].name, "text": f["text"]}
                          for f in r.knowledge[-6:] if f["about"] in self.residents],
            "former_job": JOBS[r.former_job]["title"] if r.former_job else None,
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
            arrangement = self._arrange(actions)
        self._write_openers(arrangement, use_llm=True)          # outside the lock: may call the model
        with self.lock:
            plan = self._commit(arrangement)
            self.phase = "living"
        self._live(plan)
        with self.lock:
            self.morning = False
            self.beat += 1
            self.clock += self.beat_minutes

    def _resolve(self, actions: dict[str, dict]) -> dict:
        """Arrange, voice and commit one beat's actions in one go (rules voice). Under the lock."""
        arrangement = self._arrange(actions)
        self._write_openers(arrangement, use_llm=False)
        return self._commit(arrangement)

    # ---- arrange: where everyone goes, who talks to whom, about what -----------------------
    def _arrange(self, actions: dict[str, dict]) -> dict:
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
            elif a["type"] == "secret" and r.secret and not r.secret.get("exposed"):
                dest[r.id] = r.secret["place"]
            elif a["type"] == "talk" and a.get("person") in R and not R[a["person"]].asleep and a["person"] != r.id:
                talk[r.id] = a["person"]
            else:
                dest[r.id] = r.place
                actions[r.id] = {**a, "type": "rest"}

        # Break talk cycles: in A<->B keep the more sociable one as the speaker; the other
        # just listens where they are.
        for rid in sorted(talk):
            chain, node = [], rid
            while node in talk and node not in chain:
                chain.append(node)
                node = talk[node]
            if node in chain:                                   # a cycle
                cycle = sorted(chain[chain.index(node):])
                keep = max(cycle, key=lambda c: (self._sociability(R[c]), c))
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

        # People who end up together start talking: at the same place, or crossing on the road.
        rng = random.Random(f"{self.seed}:{self.beat}:encounters")
        paths = {r.id: self._path_for(r, dest[r.id]) for r in awake}
        engaged = set(talk) | set(talk.values())
        encounter: set[str] = set()
        passing: dict[str, float] = {}
        groups: dict[str, list[str]] = {}
        for r in awake:
            if r.id not in engaged and not dest[r.id].startswith("home:"):
                groups.setdefault(dest[r.id], []).append(r.id)
        for place in sorted(groups):
            ids = sorted(groups[place])
            rng.shuffle(ids)
            while len(ids) >= 2:
                a, b = ids.pop(), ids.pop()
                if rng.random() < self._encounter_chance(R[a], R[b]):
                    speaker, listener = self._who_speaks(R[a], R[b], rng)
                    talk[speaker] = listener
                    encounter.add(speaker)
                    engaged |= {a, b}
        movers = sorted(r.id for r in awake if r.id not in engaged and dest[r.id] != start[r.id])
        for i, a in enumerate(movers):
            for b in movers[i + 1:]:
                if a in engaged or b in engaged:
                    continue
                at = crossing(paths[a], paths[b])
                if at is not None and rng.random() < 0.2 + 0.8 * self._encounter_chance(R[a], R[b]):
                    speaker, listener = self._who_speaks(R[a], R[b], rng)
                    talk[speaker] = listener
                    passing[speaker] = at
                    engaged |= {a, b}

        topics = {}
        for rid, target in sorted(talk.items()):
            topic_rng = random.Random(f"{self.seed}:{self.beat}:{rid}:topic")
            if rid in passing:
                topics[rid] = {"intent": "pass"}
            elif self.voice_mode == "mind":                  # MindForm's own words: no world-written topics
                topics[rid] = {"intent": "small"}
            else:
                topics[rid] = intrigue.choose_topic(R[rid], R[target], self, topic_rng)
        return {"actions": actions, "awake": [r.id for r in awake], "start": start, "dest": dest, "talk": talk,
                "brushoff": brushoff, "encounter": encounter, "passing": passing, "paths": paths,
                "topics": topics, "lines": {}}

    def _sociability(self, r: Resident) -> float:
        t = intrigue.trait_dict(r.state)
        lean = float((r.state.get("stance") or {}).get("tendency") or 0.0)
        need = next((n["tension"] for n in r.state.get("needs") or [] if n.get("key") == "relatedness"), 0.3)
        return t["E"] + 0.6 * need + 0.4 * lean

    def _who_speaks(self, a: Resident, b: Resident, rng: random.Random) -> tuple[str, str]:
        sa = self._sociability(a) + rng.random() * 0.4
        sb = self._sociability(b) + rng.random() * 0.4
        return (a.id, b.id) if sa >= sb else (b.id, a.id)

    def _encounter_chance(self, a: Resident, b: Resident) -> float:
        rel_a = intrigue.normalize_rel(a.relationships.setdefault(b.id, intrigue.new_rel()))
        rel_b = intrigue.normalize_rel(b.relationships.setdefault(a.id, intrigue.new_rel()))
        social = (self._sociability(a) + self._sociability(b)) / 2
        warmth = (rel_a["affection"] + rel_b["affection"]) / 2
        knows = 0.25 if (b.secret and intrigue.knows_secret(a, b)) or (a.secret and intrigue.knows_secret(b, a)) else 0.0
        return max(0.12, min(0.85, 0.42 + 0.25 * social + 0.2 * abs(warmth) + knows))

    # ---- voice ------------------------------------------------------------------------------
    def _voice_ctx(self, r: Resident, role: str, *, to: Resident | None = None, emotion: dict | None = None,
                   topic: dict | None = None, heard: str = "") -> dict:
        topic = topic or {}
        need = (r.state.get("top_need") or {}).get("key") if isinstance(r.state.get("top_need"), dict) else None
        stance = (r.state.get("stance") or {}).get("mode")
        rel = intrigue.normalize_rel(r.relationships.setdefault(to.id, intrigue.new_rel())) if to else None
        ctx = {
            "role": role, "to": to.name if to else "", "traits": intrigue.trait_dict(r.state),
            "esteem": float(r.state.get("esteem") or 0.0), "need": need, "stance": stance,
            "mood": emotion, "family": _family(emotion), "goal": intrigue.first_person_goal(r.goal),
            "weather": {"rain": "rain", "storm": "storm", "fog": "fog", "cloudy": "grey sky"}.get(self.weather, ""),
            "names": {x.name for x in self.residents.values()}, "heard": heard, "intent": topic.get("intent"),
            "job": JOBS.get(r.job, JOBS["none"])["title"],
            "secret_text": intrigue.first_person_secret(r.secret["text"]) if r.secret else "",
        }
        event = self.headline_event()
        ctx["topic"] = topic["topic"] if "topic" in topic else (event.get("topic") if event else "")
        if topic.get("intent") == "gossip":
            ctx.update(gossip=topic["fact"]["text"], who=topic["who"], topic=f"{topic['who']}")
        if topic.get("intent") == "outcome":
            ctx["did"] = topic.get("did", "")
        if topic.get("intent") in ("confront", "probe", "ally") and to and to.secret:
            ctx.update(secret_short=to.secret["short"], their_secret=to.secret["text"])
        if rel is not None:
            ctx["relationship"] = intrigue.bond_label(rel["affection"], rel["trust"], rel["talks"])
        return ctx

    def _content_phrases(self, ctx: dict) -> list[str]:
        """What lines are ABOUT (shared subject matter, not phrasing): topics, gossip, outcomes."""
        out = [ctx.get("topic"), ctx.get("gossip"), ctx.get("did"), ctx.get("goal")]
        out += [e.get("topic") for e in self.events]
        return [p for p in out if p]

    def _others_recent(self, rid: str) -> list[str]:
        return [line for o in self.residents.values() if o.id != rid for line in o.said[-8:]]

    def _remember(self, r: Resident, line: str, tid: str | None = None) -> None:
        r.said = (r.said + [line])[-LINES_KEEP:]
        r.said_ids = (r.said_ids + [tid or ""])[-LINES_KEEP:]

    def _speak(self, r: Resident, role_keys: list[str], ctx: dict, *, use_llm: bool,
               raw: str | None = None, raw_is_llm: bool = False, fixed: list[str] | None = None) -> tuple[str | None, str]:
        """The words ``r`` says. ``fixed`` = specific lines to choose from (a confession, a slip),
        claimed like templates. Returns (line, source)."""
        if self.voice_mode == "mind":
            return raw, "mind"
        names = ctx.get("names") or set()
        recent = r.said[-RECENT_LINES:]
        others = self._others_recent(r.id)
        content = self._content_phrases(ctx)
        ctx["content"] = content

        def fresh(line: str | None) -> bool:
            return (bool(line) and not near_duplicate(line, recent) and not near_duplicate(line, others)
                    and not shares_run(line, others, names=names, ignore=content))

        if raw and raw_is_llm and not fixed and fresh(raw):
            self._remember(r, raw)
            return raw, "mind"
        if use_llm and self.brain == "llm" and llm.available():
            try:
                line = llm_line(r, ctx, raw=raw, stats=self.stats, avoid=recent[-8:] + others[-6:])
                if fresh(line):
                    self._remember(r, line)
                    return line, "llm"
            except Exception as exc:
                log.info("voice fell back to rules for %s: %s", r.id, exc)
        rng = random.Random(f"{self.seed}:{self.beat}:{r.id}:{ctx.get('role')}:{len(r.said)}:line")
        if fixed:
            got = self.voices.claim_line(r.id, fixed, rng, recent)
            if got:
                self._remember(r, got)
                return got, "voice"
        got = self.voices.compose(r, role_keys, ctx, rng, recent, others)
        if got:
            line, tid = got
            self._remember(r, line, tid)
            return line, "voice"
        if raw_is_llm and fresh(raw):                    # nothing new in their voice: MindForm's own words
            self._remember(r, raw)
            return raw, "mind"
        return None, "silent"                            # better silent than repeating (v0's offline replies are templates too)

    def _write_openers(self, arr: dict, *, use_llm: bool) -> None:
        """The first line of every conversation, in the speaker's voice."""
        R = self.residents
        talk, topics, actions = arr["talk"], arr["topics"], arr["actions"]

        def one(rid: str):
            target = talk[rid]
            r, to = R[rid], R[target]
            topic = topics[rid]
            intent = topic["intent"]
            say = (actions[rid].get("say") or "").strip() if rid not in arr["encounter"] else ""
            if self.voice_mode == "mind":
                if say:
                    return rid, (say, actions[rid].get("source", "planner"))
                rng = random.Random(f"{self.seed}:{self.beat}:{rid}:say")
                if target in r.talking_with:
                    return rid, (planner.continuation(rng), "rules")
                ctx = {"state": r.state, "events": [self._event_public(e) for e in self.active_events()]}
                return rid, (planner._opener(ctx, {"name": to.name}, rng), "rules")
            if say and intent not in MECHANICAL and not near_duplicate(say, r.said[-RECENT_LINES:]):
                self._remember(r, say)
                return rid, (say, actions[rid].get("source", "planner"))
            ctx = self._voice_ctx(r, "open" if intent != "pass" else "pass", to=to, emotion=r.mood, topic=topic)
            fixed = None
            if intent == "confide" and r.secret:
                rng = random.Random(f"{self.seed}:{self.beat}:{rid}:confide")
                fixed = [intrigue.secret_line(r.secret, "confide", random.Random(i), r.name) for i in range(4)]
                fixed = list(dict.fromkeys(fixed))
                rng.shuffle(fixed)
            roles = {"pass": ["pass:greet"], "event": ["open:event", "open:small"], "gossip": ["open:gossip"],
                     "goal": ["open:goal", "open:small"], "comfort": ["open:comfort"], "outcome": ["open:outcome", "open:small"],
                     "weather": ["open:weather", "open:small"], "warm": ["open:warm", "open:small"],
                     "cool": ["open:cool", "open:small"], "probe": ["open:probe"], "confront": ["open:confront", "open:probe"],
                     "ally": ["open:ally", "open:small"],
                     "small": ["open:small"], "confide": ["open:small"]}.get(intent, ["open:small"])
            if intent != "pass":
                roles = roles + [x for x in ("open:small", "open:warm", "open:weather", "open:event") if x not in roles]
            line, source = self._speak(r, roles, ctx, use_llm=use_llm, fixed=fixed)
            return rid, (line, source)

        ids = sorted(talk)
        if use_llm and self.brain == "llm" and llm.available() and len(ids) > 1:
            with ThreadPoolExecutor(max_workers=min(6, len(ids))) as pool:
                results = list(pool.map(one, ids))
        else:
            results = [one(rid) for rid in ids]
        arr["lines"] = {}
        for rid, (line, source) in results:
            if line:
                arr["lines"][rid] = (line, source)
                continue
            # Nothing fresh to say: they don't start a conversation after all.
            talk.pop(rid, None)
            for key in ("brushoff", "encounter"):
                arr[key].discard(rid)
            arr["passing"].pop(rid, None)
            arr["topics"].pop(rid, None)
            if actions[rid]["type"] == "talk":
                actions[rid] = {"type": "rest", "intent": actions[rid].get("intent", ""), "source": actions[rid].get("source", "")}

    # ---- commit: the facts ---------------------------------------------------------------------
    def _commit(self, arr: dict) -> dict:
        """Turn the arrangement into facts per resident, move everyone, emit what is seen.
        Under the lock. Returns the beat plan for ``_live``."""
        R = self.residents
        actions, dest, talk, start = arr["actions"], arr["dest"], arr["talk"], arr["start"]
        brushoff, passing, lines, topics = arr["brushoff"], arr["passing"], arr["lines"], arr["topics"]
        awake = [R[rid] for rid in arr["awake"]]
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
            if rid not in brushoff and rid not in passing:
                parent[find(rid)] = find(target)
        clusters: dict[str, list[str]] = {}
        for r in awake:
            clusters.setdefault(find(r.id), []).append(r.id)

        items: dict[str, list[dict]] = {r.id: [] for r in awake}
        doing: dict[str, str] = {}
        outcomes: dict[str, tuple[str, str]] = {}
        forced: dict[str, dict] = {}
        salient: dict[str, str] = {}                     # what this beat was "about" for each of them
        for r in awake:
            a = actions[r.id]
            if self.morning:
                items[r.id].append({"k": "wake", "time": f"{WAKE_HOUR}:00",
                                    "weather": director.WEATHER_TEXT[self.weather]})
            if self.weather_changed:
                items[r.id].append({"k": "weather", "text": f"The weather turned: {director.WEATHER_TEXT[self.weather]}."})
            for e in self.active_events():                  # big news reaches everyone, wherever they are
                if e.get("force") and r.id not in e["seen_by"]:
                    e["seen_by"].append(r.id)
                    here = e["place"] is None or dest[r.id] == e["place"]
                    text = (e.get("personal") or {}).get(r.id) or (e["text"] if here else e.get("far_text") or e["text"])
                    items[r.id].append({"k": "event", "text": text})
                    forced[r.id] = e
                    salient.setdefault(r.id, e.get("topic") or "")
            for e in self.active_events():                  # island-wide / private news
                if e.get("force"):
                    continue
                if e["place"] is None and (not e.get("target") or e["target"] == r.id) and r.id not in e["seen_by"]:
                    e["seen_by"].append(r.id)
                    items[r.id].append({"k": "event", "text": e["text"]})
                    salient.setdefault(r.id, e.get("topic") or "")
            if dest[r.id] != r.place:
                items[r.id].append({"k": "move", "from": self._my_place_name(r, r.place),
                                    "to": self._my_place_name(r, dest[r.id])})
            if a["type"] == "do":
                text, quality = self._outcome_q(r, a["activity"])
                outcomes[r.id] = (text, quality)
                items[r.id].append({"k": "outcome", "text": text})
                doing[r.id] = doing_text(a["activity"])
                salient.setdefault(r.id, ACTIVITY_TOPIC.get(a["activity"], ""))
            elif a["type"] == "secret" and r.secret:
                srng = random.Random(f"{self.seed}:{self.beat}:{r.id}:secret")
                items[r.id].append({"k": "secret_work", "text": intrigue.secret_line(r.secret, "work", srng, r.name)})
                doing[r.id] = intrigue.cover_doing(r.secret)
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
                said = {"k": "said", "to": R[target].name, "line": line}
                if r.id in passing:
                    said["passing"] = True
                items[r.id].append(said)
                if r.id in brushoff:
                    items[r.id].append({"k": "brushoff", "who": R[target].name,
                                        "to_place": self.place_name(dest[target])})
                    doing[r.id] = f"watching {R[target].name} walk off"
                elif r.id not in passing:
                    doing[r.id] = f"talking with {R[target].name}"
            for speaker in addressed_by[r.id]:
                line = lines[speaker][0]
                if speaker in brushoff:
                    items[r.id].append({"k": "heard_leaving", "who": R[speaker].name, "line": line})
                elif speaker in passing:
                    items[r.id].append({"k": "heard", "who": R[speaker].name, "line": line, "passing": True})
                else:
                    items[r.id].append({"k": "heard", "who": R[speaker].name, "line": line,
                                        "approach": start[speaker] != dest[r.id],
                                        "while": doing_text(a["activity"]) if a["type"] == "do" else None})
            # Overheard: other lines inside my conversation cluster.
            cluster = clusters[find(r.id)]
            for speaker, target in talk.items():
                if speaker in brushoff or speaker in passing or speaker == r.id or target == r.id:
                    continue
                if speaker in cluster and r.id in cluster:
                    items[r.id].append({"k": "overheard", "who": R[speaker].name, "to": R[target].name,
                                        "line": lines[speaker][0]})

        for r in awake:                                         # who else was there, what they saw
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
                if e.get("force"):
                    continue
                if e["place"] == place and (not e.get("target") or e["target"] == r.id) and r.id not in e["seen_by"]:
                    e["seen_by"].append(r.id)
                    items[r.id].append({"k": "event", "text": e["text"]})
                    salient[r.id] = e.get("topic") or salient.get(r.id, "")
            if any(p["k"] == "letter" for p in r.pending):
                salient[r.id] = "the letter"
            items[r.id].extend(r.pending)
            r.pending = []

        # Somebody working on their secret where others can see it: a clue.
        for holder in awake:
            if actions[holder.id]["type"] != "secret" or not holder.secret:
                continue
            for o in awake:
                if o.id == holder.id or dest[o.id] != dest[holder.id] or intrigue.knows_secret(o, holder):
                    continue
                crng = random.Random(f"{self.seed}:{self.beat}:{o.id}:{holder.id}:clue")
                clue = intrigue.secret_line(holder.secret, "clue", crng, holder.name)
                items[o.id].append({"k": "saw", "text": clue})
                intrigue.learn(o, holder.id, clue, t=self.clock, src="saw", clue=True)
                O = intrigue.trait_dict(o.state)["O"]
                self.emit("clue", clue, actor=o.id, other=holder.id, drama=2)
                self._suspect(o, holder, intrigue.SUSPECT_FROM_CLUE + 0.15 * max(0.0, O), "worked out")

        # Commit movement + what everyone visibly does (the client animates this right away).
        for r in awake:
            a = actions[r.id]
            new_place = dest[r.id]
            if new_place != r.place:
                r.path = arr["paths"].get(r.id) or self._path_for(r, new_place)
                r.x, r.z = r.path[-1]
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
                r.last_outcome = text if quality != "0" else r.last_outcome
                # A notable result (went well / went wrong) is a small moment on screen; routine ones
                # only go to the story -- unless they are about to talk, which says more.
                notable = quality != "0" and r.id not in talk and not addressed_by[r.id]
                self.emit("outcome", text, actor=r.id, place=new_place, quality=quality, notable=notable,
                          dwell=1.4 if notable else 0.0)
        for rid, target in talk.items():
            topic = topics.get(rid) or {"intent": "small"}
            intent = topic["intent"]
            R[rid].pair_topics = {**R[rid].pair_topics, target: intent}
            drama = 3 if intent in ("confide", "confront") else 2 if (rid in brushoff or intent == "gossip") else 0
            self.emit("say", lines[rid][0], actor=rid, to=target, to_name=R[target].name, source=lines[rid][1],
                      brushoff=rid in brushoff, intent=intent, passing=rid in passing, at=passing.get(rid),
                      encounter=rid in arr["encounter"], drama=drama or None)
            if rid in brushoff:
                self._highlight(2, f"{R[target].name} walked off while {R[rid].name} was mid-sentence", [rid, target])
            self._conversation_effects(R[rid], R[target], topic, heard=rid not in brushoff)

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
                "levels": level, "dest": dest, "actions": actions, "lines": lines, "topics": topics,
                "forced": {rid: e["id"] for rid, e in forced.items()}, "forced_topic": {rid: e.get("topic") for rid, e in forced.items()},
                "forced_self": {rid: e.get("preset") or e["kind"] for rid, e in forced.items() if rid in (e.get("personal") or {})},
                "passing": passing, "salient": salient}

    def _conversation_effects(self, speaker: Resident, listener: Resident, topic: dict, *, heard: bool) -> None:
        """What a line does in the world: gossip passes on, a confession is shared, a confrontation lands."""
        if not heard:
            return
        intent = topic.get("intent")
        if intent == "gossip":
            fact = topic["fact"]
            about = self.residents.get(fact["about"])
            if about is None:
                return
            fact["told"] = fact.get("told", []) + [listener.id]
            intrigue.learn(listener, about.id, fact["text"], t=self.clock, src=f"heard:{speaker.id}",
                           clue=fact.get("clue", False), secret=fact.get("secret", False))
            if fact.get("secret") and about.secret:
                self._learn_secret(listener, about, f"heard it from {speaker.name}")
            elif fact.get("clue"):
                self._suspect(listener, about, intrigue.SUSPECT_FROM_GOSSIP, "pieced it together")
        elif intent == "confide" and speaker.secret:
            self._learn_secret(listener, speaker, "confided")
            rel = intrigue.normalize_rel(listener.relationships.setdefault(speaker.id, intrigue.new_rel()))
            intrigue.nudge_trust(rel, 0.15)
            mine = intrigue.normalize_rel(speaker.relationships.setdefault(listener.id, intrigue.new_rel()))
            intrigue.nudge_trust(mine, 0.08)
            self._highlight(3, f"{speaker.name} confided in {listener.name}: {intrigue.first_person_secret(speaker.secret['text'])}",
                            [speaker.id, listener.id])
        elif intent == "confront" and listener.secret:
            listener.secret["confronted_by"] = list(dict.fromkeys(listener.secret.get("confronted_by", []) + [speaker.id]))
            rel = intrigue.normalize_rel(listener.relationships.setdefault(speaker.id, intrigue.new_rel()))
            intrigue.nudge_trust(rel, -0.1)
            self._highlight(3, f"{speaker.name} confronted {listener.name} about {listener.secret['short']}",
                            [speaker.id, listener.id])

    def _suspect(self, observer: Resident, holder: Resident, amount: float, how: str) -> None:
        if not holder.secret or intrigue.knows_secret(observer, holder):
            return
        if intrigue.add_suspicion(observer, holder.id, amount) >= 1.0:
            self._learn_secret(observer, holder, how)

    def _learn_secret(self, knower: Resident, holder: Resident, how: str, *, announce: bool = True) -> None:
        if not holder.secret or knower.id in holder.secret.get("known_by", []):
            return
        holder.secret["known_by"] = holder.secret.get("known_by", []) + [knower.id]
        reveal = intrigue.reveal_text(holder.name, holder.secret)
        intrigue.learn(knower, holder.id, reveal, t=self.clock, src=how, secret=True)
        knower.suspicion = {**knower.suspicion, holder.id: 1.0}
        if how != "confided":
            rel = intrigue.normalize_rel(knower.relationships.setdefault(holder.id, intrigue.new_rel()))
            intrigue.nudge_trust(rel, -0.08)                     # they had been hiding it
        if announce:
            source = how[len("heard it from "):] if how.startswith("heard it from ") else None
            verb = {"confided": f"{holder.name} confided in {knower.name}",
                    "worked out": f"{knower.name} worked out {holder.name}'s secret",
                    "pieced it together": f"{knower.name} pieced together {holder.name}'s secret",
                    "caught a slip": f"{holder.name} let something slip to {knower.name}"}.get(
                how, f"{knower.name} heard {holder.name}'s secret from {source}" if source
                else f"{knower.name} found out {holder.name}'s secret")
            self.emit("secret", f"{verb}. {reveal}", actor=knower.id, other=holder.id, mode=how, drama=3,
                      headline=verb, reveal=reveal)
            self._highlight(3, f"{verb}: {reveal[len(holder.name) + 1:].rstrip('.')}" if reveal.startswith(holder.name)
                            else verb, [knower.id, holder.id])

    def _outcome(self, r: Resident, activity_id: str) -> str:
        return self._outcome_q(r, activity_id)[0]

    def _outcome_q(self, r: Resident, activity_id: str) -> tuple[str, str]:
        """(fact, quality) -- quality "+"/"-"/"0" is world odds, shown on screen, never to the mind.
        Nobody gets the same result twice in a row, and the island doesn't repeat itself while
        anything else could happen."""
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
        fresh = [p for p in pool if p[1] not in r.outcomes_seen[-4:] and p[1] not in self.recent_outcomes[-6:]]
        fresh = fresh or [p for p in pool if p[1] not in r.outcomes_seen[-2:]] or pool
        _, text, quality = rng.choices(fresh, weights=[w for w, _, _ in fresh])[0]
        r.outcomes_seen = (r.outcomes_seen + [text])[-8:]
        self.recent_outcomes = (self.recent_outcomes + [text])[-10:]
        return text, quality

    # ---- live: narrate, form, answer -----------------------------------------------------------------
    def _role(self, rid: str, plan: dict) -> tuple[str, str | None]:
        """How a resident's reply comes out this beat: (role, who it is addressed to)."""
        talk, brushoff, addressed_by = plan["talk"], plan["brushoff"], plan["addressed_by"]
        heard_from = [s for s in addressed_by.get(rid, []) if s not in brushoff]
        if heard_from:
            return "answer", heard_from[0]
        if rid in talk and rid not in brushoff and rid not in (plan.get("passing") or {}):
            return "close", talk[rid]
        if rid in (plan.get("forced") or {}):
            return "react", None
        return "inner", None

    def _voice_reply(self, rid: str, plan: dict, turn, replies: dict[str, str | None]) -> tuple[str | None, str, str]:
        """Outside the lock: the words this resident's reply comes out in. -> (line, source, role)."""
        R = self.residents
        r = R[rid]
        role, to_id = self._role(rid, plan)
        if not turn or not turn.reply:
            return None, "none", role
        emotion = read_emotion(turn.appraisal)
        raw_is_llm = ((turn.state or {}).get("sources") or {}).get("reply") == "llm"
        to = R.get(to_id) if to_id else None
        topic = {}
        heard = ""
        salient = (plan.get("salient") or {})
        if role == "answer":
            topic = dict((plan.get("topics") or {}).get(to_id) or {})
            about = {"event": topic.get("topic"), "gossip": topic.get("who"), "small": salient.get(rid)}
            topic["topic"] = about.get(topic.get("intent")) or ""
            heard = plan["lines"][to_id][0] if to_id in plan.get("lines", {}) else ""
        elif role == "close":
            topic = dict((plan.get("topics") or {}).get(rid) or {})
            topic.setdefault("topic", topic.get("who") or salient.get(rid) or "")
            heard = replies.get(to_id) or ""
        elif role == "react":
            topic = {"intent": "react", "topic": (plan.get("forced_topic") or {}).get(rid)}
        else:
            topic = {"intent": "inner", "topic": salient.get(rid) or ""}
        ctx = self._voice_ctx(r, role, to=to, emotion=emotion, topic=topic, heard=heard)
        key, fam = (emotion or {}).get("key"), _family(emotion)
        strong = (emotion or {}).get("strength", 0.0) >= HEADLINE_THRESHOLD
        rng = random.Random(f"{self.seed}:{self.beat}:{rid}:reply")
        fixed = None
        intent = topic.get("intent")
        if role == "answer":
            if intent == "pass":
                roles = ["pass:reply"]
            elif intent == "confide":
                roles = [f"reply:confide:{fam}", "answer:" + fam]
            elif intent == "confront" and r.secret:
                stance = (r.state.get("stance") or {}).get("mode")
                admit = (stance == "approach" or fam == "pos") if rng.random() < 0.8 else rng.random() < 0.5
                fixed = [intrigue.secret_line(r.secret, "admit" if admit else "deny", random.Random(i), r.name) for i in range(3)]
                roles = [f"reply:confront:{'admit' if admit else 'deny'}"]
                ctx["intent"] = "admit" if admit else "deny"
                if admit:
                    self._highlight(3, f"{r.name} admitted it to {to.name}", [rid, to_id])
            elif intent == "probe" and r.secret:
                slip = fam == "neg" and intrigue.trait_dict(r.state)["N"] > 0.1 and rng.random() < 0.45
                if slip:
                    fixed = [intrigue.secret_line(r.secret, "slip", random.Random(i), r.name) for i in range(3)]
                    ctx["intent"] = "slip"
                    plan.setdefault("slips", {})[rid] = to_id
                else:
                    ctx["intent"] = "deflect"
                roles = ["reply:probe"]
            elif intent == "gossip":
                roles = [f"reply:gossip:{fam}", "answer:" + fam]
            else:
                roles = ([f"answer:{key}", f"answer:{fam}"] if strong or rng.random() < 0.35 else [f"answer:{fam}", f"answer:{key}"])
                if ctx.get("topic") and rng.random() < 0.55:
                    roles.insert(0, f"answer:{fam}:topic")
        elif role == "close":
            roles = [f"close:{fam}", "close:neutral"]
            if r.secret and fam == "neg" and strong and rng.random() < 0.3 and to_id:
                fixed = [intrigue.secret_line(r.secret, "slip", random.Random(i), r.name) for i in range(3)]
                ctx["intent"] = "slip"
                plan.setdefault("slips", {})[rid] = to_id
        elif role == "react":
            roles = [f"react:{key}", f"react:{fam}", "react:neutral"]
            mine = (plan.get("forced_self") or {}).get(rid)          # the news is about them
            if mine:
                roles = [f"react:self:{mine}:{fam}", f"react:self:{mine}:neutral"] + roles
                ctx["intent"] = f"the news is about them ({mine})"
        else:
            if fam == "neutral" and not strong and rng.random() < 0.35 and not (r.secret and r.place == r.secret["place"]):
                return None, "silent", role                   # nothing much on their mind
            roles = [f"inner:{key}", f"inner:{fam}"] if strong else [f"inner:{fam}", f"inner:{key}"]
            if ctx.get("topic") and rng.random() < 0.6:
                roles.insert(0, f"inner:{fam}:topic")
            at_secret = r.secret and r.place == r.secret["place"]
            if r.secret and not r.secret.get("exposed") and (at_secret or fam == "neg" or rng.random() < 0.25):
                fixed = [intrigue.secret_line(r.secret, "inner", random.Random(i), r.name) for i in range(4)]
            elif r.goal and rng.random() < 0.3:
                roles = ["inner:goal"] + roles
        if fixed:
            fixed = list(dict.fromkeys(fixed))
            rng.shuffle(fixed)
        line, source = self._speak(r, roles, ctx, use_llm=True, raw=turn.reply, raw_is_llm=raw_is_llm, fixed=fixed)
        return line, source, role

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
            turn, error = self._experience(r, text)
            return rid, items, text, narr_source, turn, error

        def voice_one(result):
            rid, items, text, narr_source, turn, error = result
            spoken, voice_source, role = self._voice_reply(rid, plan, turn, replies) if turn else (None, "none", "inner")
            return rid, items, text, narr_source, turn, error, spoken, voice_source, role

        llm_voices = self.voice_mode == "styled" and self.brain == "llm" and llm.available()
        for lv in sorted(by_level):
            members = sorted(by_level[lv])
            with ThreadPoolExecutor(max_workers=max(1, min(8, len(members)))) as pool:
                results = list(pool.map(live_one, members))            # narrate + MindForm, in parallel
                # Words in a fixed order (the guard reads everyone's recent lines, so offline the same
                # seed gives the same lines); in parallel only when the model writes them.
                voiced = list(pool.map(voice_one, results)) if llm_voices and len(results) > 1 else [voice_one(x) for x in results]
            for rid, items, text, narr_source, turn, error, spoken, voice_source, role in voiced:
                replies[rid] = spoken if spoken is not None else (turn.reply if turn else None)
                self._settle(rid, items, text, narr_source, turn, error, plan,
                             spoken=spoken, voice_source=voice_source, role=role)

    def _experience(self, r: Resident, text: str):
        """One MindForm turn, retried once (the adapter restarts a dead mind process)."""
        error = None
        for attempt in range(2):
            try:
                return self.mind.experience(r.ref, text), None
            except Exception as exc:
                error = str(exc)
                log.warning("mind turn failed for %s (attempt %d): %s", r.id, attempt + 1, exc)
                time.sleep(0.5)
        return None, error

    def _settle(self, rid, items, text, narr_source, turn, error, plan, *, spoken: str | None = None,
                voice_source: str | None = None, role: str | None = None) -> None:
        R = self.residents
        talk, brushoff, addressed_by = plan["talk"], plan["brushoff"], plan["addressed_by"]
        with self.lock:
            r = R[rid]
            r.last_experience = text
            r.recent = (r.recent + [f"[{time_text(self.clock)}] {text}"])[-12:]
            r.mind_error = error
            if error:
                self.emit("system", f"{r.name}'s mind could not take this experience: {error}", actor=rid, quiet=True)
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
            line = spoken if voice_source is not None else (turn.reply if turn else None)
            if role is None:
                role = self._role(rid, plan)[0]
            spoke = False
            forced = rid in (plan.get("forced") or {})
            if line:
                if role == "answer":
                    self.emit("say", line, actor=rid, to=heard_from[0], to_name=R[heard_from[0]].name,
                              source=voice_source or "mind", raw=turn.reply if turn else None, answer=True, emotion=emotion,
                              passing=heard_from[0] in (plan.get("passing") or {}),
                              intent=((plan.get("topics") or {}).get(heard_from[0]) or {}).get("intent"))
                    spoke = True
                elif role == "close":
                    target = talk[rid]
                    self.emit("say", line, actor=rid, to=target, to_name=R[target].name, source=voice_source or "mind",
                              raw=turn.reply if turn else None, closing=True, emotion=emotion,
                              passing=rid in (plan.get("passing") or {}))
                    R[target].pending.append({"k": "parting", "who": r.name, "line": line})
                    spoke = True
                elif role == "react":
                    self.emit("say", line, actor=rid, to=None, to_name="", source=voice_source or "mind",
                              raw=turn.reply if turn else None, react=True, emotion=emotion, drama=2)
                    spoke = True
                else:
                    self.emit("reaction", line, actor=rid, source=voice_source or "mind",
                              raw=turn.reply if turn else None, emotion=emotion)
            for slipper, other in (plan.get("slips") or {}).items():
                if slipper == rid and other in R:
                    self._suspect(R[other], r, intrigue.SUSPECT_FROM_SLIP, "caught a slip")
            if emotion:
                headline = emotion["strength"] >= HEADLINE_THRESHOLD or (forced and emotion["strength"] >= SHOW_THRESHOLD)
                # Every reading updates their face; strong ones get a moment of their own on screen
                # (none when it plays alongside their spoken line).
                self.emit("emotion", f"{r.name} felt {emotion['label']}", actor=rid, headline=headline,
                          shown=emotion["strength"] >= SHOW_THRESHOLD or forced,
                          dwell=0.0 if (spoke or not headline) else None, **emotion)
                if emotion["strength"] >= 0.7:
                    self._highlight(1, f"{r.name} was {emotion['label']} at {self.place_name(r.place)}", [rid])
            r.inclination = None
            if turn and turn.formation:
                note = {"t": self.clock, "time": time_text(self.clock), **turn.formation}
                r.formation_log = (r.formation_log + [note])[-40:]
                self.emit("formation", f"{r.name} {turn.formation.get('note', 'changed')}", actor=rid,
                          key=turn.formation.get("key"), delta=turn.formation.get("delta"))
            if turn:
                self._smooth_traits(r)
            # Affection moves with how THIS resident read the encounter (MindForm's valence); trust
            # with what visibly happened.
            valence = float((appraisal or {}).get("valence", 0.0) or 0.0)
            involved: dict[str, tuple[str, float]] = {}
            if rid in talk:
                target = talk[rid]
                involved[target] = ((f"{R[target].name} walked off while you were talking", -0.08) if rid in brushoff
                                    else (f"talked at {self.place_name(r.place)}", 0.03))
            for s in heard_from:
                involved[s] = (f"talked at {self.place_name(r.place)}", 0.03)
            for s in leaving_from:
                involved[s] = (f"you walked off while {R[s].name} was talking", 0.0)
            for oid, (what, trust_delta) in involved.items():
                rel = intrigue.normalize_rel(r.relationships.setdefault(oid, intrigue.new_rel()))
                before = (rel.get("feeling") or _feeling(rel["affection"], rel["talks"])) if rel["talks"] else None
                before_trust = rel["trust"]
                rel["affection"] = max(-1.0, min(1.0, rel["affection"] + AFFINITY_RATE * valence * (1 - abs(rel["affection"]))))
                rel["affinity"] = rel["affection"]
                if valence < -0.4:
                    trust_delta -= 0.04
                intrigue.nudge_trust(rel, trust_delta)
                rel["talks"] += 1
                rel["last"] = f"{what} ({time_text(self.clock)})"
                after = _feeling_held(rel["affection"], rel["talks"], before)
                rel["feeling"] = after
                if before is not None and after != before:          # a relationship turned a corner
                    warmer = _FEELING_ORDER.index(after) > _FEELING_ORDER.index(before)
                    self.emit("bond", f"{r.name} now feels {after} toward {R[oid].name}", actor=rid,
                              other=oid, other_name=R[oid].name, feeling=after, warmer=warmer,
                              affinity=round(rel["affection"], 3), trust=round(rel["trust"], 3),
                              label=intrigue.bond_label(rel["affection"], rel["trust"], rel["talks"]), drama=2)
                    self._highlight(2, f"{r.name} now feels {after} toward {R[oid].name}", [rid, oid])
                if rel["trust"] > -0.15:
                    rel.pop("distrust", None)
                if before_trust >= -0.25 > rel["trust"] and not rel.get("distrust"):
                    rel["distrust"] = True
                    self.emit("bond", f"{r.name} no longer trusts {R[oid].name}", actor=rid, other=oid,
                              other_name=R[oid].name, feeling="distrust", warmer=False,
                              affinity=round(rel["affection"], 3), trust=round(rel["trust"], 3), drama=2)
                    self._highlight(2, f"{r.name} stopped trusting {R[oid].name}", [rid, oid])
            record = {"world": self.id, "beat": self.beat, "t": self.clock, "time": time_text(self.clock),
                      "resident": rid, "name": r.name, "place": r.place, "action": plan["actions"].get(rid),
                      "experience": text, "narration": narr_source, "facts": items,
                      "reply": turn.reply if turn else None, "spoken": line, "voice": voice_source,
                      "appraisal": appraisal, "formation": turn.formation if turn else None, "emotion": emotion,
                      "state": r.state, "error": error}
        self._append_jsonl("experiences.jsonl", record)

    def _smooth_traits(self, r: Resident) -> None:
        """Traits on screen follow MindForm through a moving average, so a trait that wobbles
        up and down turn to turn doesn't flicker; a card plays when it has really moved."""
        moved = []
        for t in r.state.get("traits") or []:
            key, value = t.get("key"), float(t.get("value") or 0.0)
            if not key:
                continue
            view = float(r.trait_view.get(key, value))
            view += TRAIT_SMOOTH * (value - view)
            r.trait_view[key] = round(view, 4)
            anchor = float(r.trait_anchor.setdefault(key, value))
            moved.append((abs(view - anchor), t, view, anchor))
        if not moved:
            return
        if self.beat - r.last_shift_beat < SHIFT_COOLDOWN:
            return
        best = None
        for diff, t, view, anchor in sorted(moved, key=lambda m: -m[0]):
            up = view > anchor
            last = r.shift_dirs.get(t["key"])                 # {"up": bool, "beat": n} of its last card
            reversal = last is not None and last["up"] != up
            need = SHIFT_STEP * (SHIFT_REVERSAL if reversal else 1.0)
            if diff >= need and not (reversal and self.beat - last["beat"] < REVERSAL_COOLDOWN):
                best = (t, view, anchor, up)
                break
        if best is None:
            return
        t, view, anchor, up = best
        r.trait_anchor[t["key"]] = round(view, 4)
        r.shift_dirs = {**r.shift_dirs, t["key"]: {"up": up, "beat": self.beat}}
        r.last_shift_beat = self.beat
        pole = t.get("high") if up else t.get("low")
        self.emit("shift", f"{r.name} · {t.get('name', t['key'])} {'↑' if up else '↓'}", actor=r.id, key=t["key"],
                  trait=t.get("name", t["key"]), up=up, pole=pole, value=round(view, 3), before=round(anchor, 3), drama=2)
        pole_text = (pole or "").replace(", ", " and ")
        self._highlight(2, f"{r.name} grew more {pole_text}" if pole else f"{r.name}'s {t.get('name')} shifted", [r.id])

    def _night(self) -> None:
        """23:00: the day's recap; everyone walks home and sleeps; one experience each; skip to 07:00."""
        bullets = recap.bullets(self.highlights, use_llm=self.brain == "llm", stats=self.stats)
        with self.lock:
            self.phase = "night"
            day = day_of(self.clock) - (1 if hour_of(self.clock) < WAKE_HOUR else 0)
            self.emit("recap", f"Today on {ISLAND_NAME}", day=day, bullets=bullets, title=f"Today on {ISLAND_NAME}")
            self.highlights = []
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
                "levels": {rid: 0 for rid in items}, "dest": {}, "actions": actions, "lines": {}, "topics": {}}
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
                self.fail_streak = 0
            except Exception as exc:
                # A recording shouldn't stop on one bad beat: skip it and keep going; pause only
                # if beats keep failing.
                log.exception("beat failed")
                self.fail_streak += 1
                with self.lock:
                    self.busy = False
                if self.fail_streak >= 3:
                    self.running = False
                    self.error = str(exc)
                    self.emit("system", f"The simulation paused after repeated errors: {exc}")
                else:
                    self.emit("system", f"A beat failed and was skipped: {exc}", quiet=True)
            pace = 0.0 if self.speed <= 0 else max(BASE_BEAT_SECONDS, WALK_SECONDS + self.beat_dwell) / self.speed
            remaining = pace - (time.monotonic() - t0)
            if remaining > 0:
                self._stop.wait(remaining)

    def set_running(self, running: bool) -> None:
        self.running = bool(running) and self.status in ("ready", "running")
        if self.running:
            self.error = None
            self.fail_streak = 0
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
                         | {"version_name": version_name(self.setup.get("version", "")), "voices": self.voice_mode},
                "llm": {"available": llm.available(), "model": llm.model_label(), **self.stats.snapshot()},
                "events": [self._event_public(e) for e in self.events
                           if e["end"] > self.clock and e["start"] - self.clock <= 180 and not e.get("target")],
                "residents": [r.public(self) for r in self.residents.values()],
                "presets": presets.PRESETS,
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
                "voices": self.voices.to_json(), "weather_hold_until": self.weather_hold_until,
                "ferry_cancelled_day": self.ferry_cancelled_day, "recent_incidents": self.recent_incidents,
                "recent_outcomes": self.recent_outcomes, "letters_sent": self.letters_sent,
                "highlights": self.highlights,
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
        world.voices = VoiceBook(world.seed, data.get("voices"))
        world.weather_hold_until = data.get("weather_hold_until", 0)
        world.ferry_cancelled_day = data.get("ferry_cancelled_day", 0)
        world.recent_incidents = data.get("recent_incidents", [])
        world.recent_outcomes = data.get("recent_outcomes", [])
        world.letters_sent = data.get("letters_sent", [])
        world.highlights = data.get("highlights", [])
        state = data.get("rng")
        if state:
            world.rng.setstate((state[0], tuple(state[1]), state[2]))
        known = set(Resident.__dataclass_fields__)
        for rd in data.get("residents", []):
            r = Resident(**{k: v for k, v in rd.items() if k in known})
            for rel in r.relationships.values():
                intrigue.normalize_rel(rel)
            if not r.voice:                       # a world from before voices: give them one now
                r.voice = world.voices.make_profile(r.id, r.name, r.spec, r.state)
            if r.secret is None and r.spec.get("secret"):
                r.secret = intrigue.make_secret(r.spec["secret"], JOBS[r.job]["place"], r.home)
            world.residents[r.id] = r
        world.voices.allocate(list(world.residents.values()))
        world.status = data.get("status", "ready")
        try:                                     # recent feed for the client
            lines = (root / "feed.jsonl").read_text().splitlines()[-300:]
            world.feed.extend(json.loads(line) for line in lines if line.strip())
        except OSError:
            pass
        return world
