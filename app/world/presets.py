"""God mode presets: one click, something big happens to the whole island.

Every preset hits EVERY resident in the next beat (whoever is not there hears about it), and
each of them reacts out loud: their mind reads it (MindForm decides how it lands) and the
reaction is shown on screen for everyone, not only the resident you are watching. Presets
also tilt what people do next (a fire pulls helpers in, a storm sends people home).

Each builder returns ``(event, effects)``: the event the world adds (with the facts each
resident perceives) and the world changes to apply (weather, jobs, secrets).
"""
from __future__ import annotations

import random

from app.world.intrigue import first_person_secret, reveal_text
from app.world.places import JOBS, LOCATIONS

PRESETS = [
    {"id": "storm", "label": "Storm", "sub": "ferry cancelled", "icon": "⛈️"},
    {"id": "stranger", "label": "A stranger arrives", "sub": "asking for someone", "icon": "🕵️", "target": "any"},
    {"id": "expose", "label": "Anonymous letter", "sub": "reveals a secret", "icon": "✉️", "target": "secret"},
    {"id": "fire", "label": "Fire!", "sub": "at a building", "icon": "🔥", "place": "building"},
    {"id": "festival", "label": "Festival", "sub": "in the plaza", "icon": "🎉"},
    {"id": "rumor", "label": "Rumor", "sub": "about a resident", "icon": "🗣️", "target": "any"},
    {"id": "fired", "label": "Job lost", "sub": "someone is let go", "icon": "📦", "target": "employed"},
]
PRESET_IDS = {p["id"] for p in PRESETS}
BUILDINGS = ["cafe", "library", "workshop", "greenhouse", "clinic", "town_hall", "rowing_club", "market"]

RUMORS = [
    "has been taking money from the café till",
    "is leaving the island for good",
    "was seen arguing with the mayor behind the Town Hall at midnight",
    "isn't who they say they are",
    "has a whole other family on the mainland",
    "has been reading other people's letters at the ferry office",
    "was the one who broke the greenhouse glass",
]


class PresetError(ValueError):
    pass


def _pick(rng: random.Random, ids: list[str], wanted: str | None, what: str) -> str:
    if wanted:
        if wanted not in ids:
            raise PresetError(f"pick {what}")
        return wanted
    if not ids:
        raise PresetError(f"nobody fits: {what}")
    return rng.choice(ids)


def build(world, preset: str, *, target: str | None = None, place: str | None = None,
          rng: random.Random) -> tuple[dict, dict]:
    if preset not in PRESET_IDS:
        raise PresetError("unknown preset")
    R = world.residents
    now = world.clock
    name = lambda rid: R[rid].name

    if preset == "storm":
        event = {"title": "Storm — ferries cancelled", "kind": "storm", "place": None, "minutes": 240,
                 "topic": "the storm",
                 "text": "A storm broke over the island: gale-force wind and lashing rain. The harbor master "
                         "announced that all ferries are cancelled until further notice.",
                 "pull": {"home": 1.4}, "recap": "A storm cancelled every ferry off the island"}
        return event, {"weather": "storm", "weather_hold": 180, "ferry_cancelled": True}

    if preset == "stranger":
        rid = _pick(rng, list(R), target, "a resident")
        event = {"title": f"A stranger asking for {name(rid)}", "kind": "stranger", "place": "dock", "minutes": 240,
                 "target_name": name(rid), "topic": f"the stranger asking about {name(rid)}",
                 "text": f"A stranger in a long grey coat came off a fishing boat at the Ferry Dock and is going "
                         f"around asking for {name(rid)} by name.",
                 "far_text": f"Word is going around the island that a stranger at the Ferry Dock is asking for "
                             f"{name(rid)} by name.",
                 "personal": {rid: "Word reached me that a stranger in a long grey coat at the Ferry Dock is going "
                                   "around asking for me by name."},
                 "pull": {"place": "dock", "weight": 1.2},
                 "recap": f"A stranger came off a boat asking for {name(rid)}"}
        return event, {}

    if preset == "expose":
        holders = [rid for rid, r in R.items() if r.secret and not r.secret.get("exposed")]
        rid = _pick(rng, holders, target, "a resident with a secret that isn't out yet")
        reveal = reveal_text(name(rid), R[rid].secret)
        event = {"title": f"A letter about {name(rid)}", "kind": "expose", "place": None, "minutes": 300,
                 "target_name": name(rid), "topic": f"the letter about {name(rid)}",
                 "text": f"An anonymous letter was slipped under every door on the island. It says: '{reveal}'",
                 "personal": {rid: f"An anonymous letter was slipped under every door on the island. It says: "
                                   f"'{reveal}' Everyone has read it."},
                 "recap": f"An anonymous letter exposed {name(rid)}'s secret to the whole island"}
        return event, {"expose": rid}

    if preset == "fire":
        where = place if place in LOCATIONS else rng.choice(BUILDINGS)
        pname = LOCATIONS[where]["name"]
        workers = {rid: f"My workplace, the {pname}, is on fire." for rid, r in R.items()
                   if JOBS.get(r.job, JOBS["none"])["place"] == where}
        event = {"title": f"Fire at the {pname}", "kind": "fire", "place": where, "minutes": 90,
                 "topic": f"the fire at the {pname}",
                 "text": f"Fire! Smoke is pouring out of the {pname}; people are forming a bucket line from the harbor.",
                 "far_text": f"Smoke is rising over the {pname} and the fire bell is ringing across the island.",
                 "personal": workers, "pull": {"place": where, "weight": 2.0},
                 "follow": {"delay": 90, "minutes": 120, "title": f"Fire at the {pname} -- out",
                            "text": f"The fire at the {pname} is out. One wall is black with soot and the windows are gone."},
                 "recap": f"Fire tore through the {pname}"}
        return event, {}

    if preset == "festival":
        event = {"title": "Surprise festival", "kind": "festival", "place": "plaza", "minutes": 180,
                 "topic": "the festival",
                 "text": "A surprise festival started in Fountain Plaza: a brass band, paper lanterns, free food and dancing.",
                 "far_text": "Music and cheering are drifting over from Fountain Plaza: a surprise festival has started.",
                 "pull": {"place": "plaza", "weight": 1.6}, "recap": "A surprise festival filled Fountain Plaza"}
        return event, {}

    if preset == "rumor":
        rid = _pick(rng, list(R), target, "a resident")
        pred = rng.choice(RUMORS)
        about = f"{name(rid)} {pred}"
        event = {"title": f"A rumor about {name(rid)}", "kind": "rumor", "place": None, "minutes": 360,
                 "target_name": name(rid), "topic": f"the rumor about {name(rid)}",
                 "text": f"A rumor is going around the island: {about}.",
                 "personal": {rid: f"People all over the island are saying that {first_person_secret(pred)}."},
                 "rumor": {"about": rid, "text": about + "."},
                 "recap": f"A rumor that {about} swept the island"}
        return event, {}

    # fired
    employed = [rid for rid, r in R.items() if r.job and r.job != "none"]
    rid = _pick(rng, employed, target, "a resident who has a job")
    job = JOBS[R[rid].job]
    pname = LOCATIONS[job["place"]]["name"] if job["place"] else "their job"
    event = {"title": f"{name(rid)} lost their job", "kind": "fired", "place": None, "minutes": 300,
             "target_name": name(rid), "topic": f"{name(rid)} losing their job",
             "text": f"Word went around the island that {name(rid)} lost their job at the {pname}.",
             "personal": {rid: f"I was told today that I no longer have my job at the {pname}. I handed back my keys."},
             "recap": f"{name(rid)} lost their job at the {pname}"}
    return event, {"fire_job": rid}


def custom(text: str, title: str | None, place: str | None) -> dict:
    """A free-text event that still hits everyone and forces reactions."""
    title = (title or "").strip() or "Something happened"
    event = {"title": title, "kind": "custom", "place": place, "minutes": 180, "topic": None, "text": text}
    if place:
        event["far_text"] = f"News is spreading from the {LOCATIONS[place]['name']}: {text}"
    return event
