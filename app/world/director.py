"""The world director: weather, the ferry, community events, incidents, letters.

The director makes things HAPPEN TO the island -- it never decides what a resident does,
thinks or feels. Rules mode draws from a seeded deck (reproducible); LLM mode asks the
world's model for an event that fits what has been going on, falling back to the deck.

``intensity``: 1 calm (everyday life), 2 lively (the default), 3 dramatic (crises,
mysteries, bad news) -- it scales how often incidents strike and which ones can.
"""
from __future__ import annotations

import json
import random

from app import llm
from app.world.places import LOCATIONS

WEATHER = ["clear", "cloudy", "rain", "storm", "fog"]
WEATHER_TEXT = {
    "clear": "clear skies", "cloudy": "an overcast sky", "rain": "steady rain",
    "storm": "a storm, with gale-force wind and lashing rain", "fog": "thick sea fog",
}
_WEATHER_NEXT = {   # hourly Markov chain
    "clear": {"clear": 0.78, "cloudy": 0.18, "fog": 0.04},
    "cloudy": {"cloudy": 0.55, "clear": 0.25, "rain": 0.17, "fog": 0.03},
    "rain": {"rain": 0.55, "cloudy": 0.3, "storm": 0.15},
    "storm": {"storm": 0.5, "rain": 0.5},
    "fog": {"fog": 0.5, "cloudy": 0.3, "clear": 0.2},
}
BAD_WEATHER = {"rain", "storm"}

# Evening community events, one per day, cycling: (title, place, start_h, end_h, what people see)
COMMUNITY = [
    ("Beach bonfire", "beach", 20, 23,
     "A bonfire is burning at Sunset Beach; townsfolk have brought blankets and a guitar."),
    ("Town meeting about the new pier", "town_hall", 18, 20,
     "The town meeting is on at the Town Hall: the council wants to build a new pier over Sunset Beach, "
     "and residents are lining up at the microphone to argue for and against it."),
    ("Lantern festival", "plaza", 19, 22,
     "The lantern festival has started in Fountain Plaza: paper lanterns, food stalls and a brass band."),
    ("Market fair", "market", 9, 14,
     "It is market fair day: extra stalls, a pie contest and a raffle at the Harbor Market."),
    ("Harbor regatta", "rowing_club", 15, 17,
     "The harbor regatta is running from the Rowing Club; crews are racing and a crowd is cheering on the pier."),
    ("Film night at the library", "library", 20, 22,
     "The library is showing an old film on a sheet in the reading room; chairs are set out in rows."),
]

# (intensity, title, place or None for island-wide, minutes, text people perceive)
INCIDENTS = [
    (1, "Dolphins off the beach", "beach", 60, "A pod of dolphins is passing close to Sunset Beach; people are pointing from the sand."),
    (1, "A street musician", "plaza", 90, "A street musician off the ferry is playing an accordion by the fountain."),
    (1, "The café oven broke", "cafe", 240, "The café's bread oven has broken down; there is only cold food today and the owner is stressed."),
    (1, "Strawberries at the market", "market", 120, "Fresh strawberries arrived from the mainland and there is a queue at the fruit stall."),
    (1, "A lost dog", "plaza", 180, "A scruffy lost dog with no collar is wandering around the plaza, following people."),
    (1, "Free books", "library", 240, "The library has put out a crate of free books on its front steps."),
    (2, "Power cut", None, 120, "The power has gone out across the whole island; no lights, no fridges, no phones charging."),
    (2, "Sailboat aground", "cliffs", 120, "A sailboat has run aground on the rocks below the cliffs; its two-person crew is waving for help."),
    (2, "An anonymous note", "town_hall", 600, "An anonymous note is pinned to the Town Hall door: 'Some people on this island are not who they say they are.'"),
    (2, "Ferry rumor", None, 360, "A rumor is going around that the ferry will stop running for the winter and the island will be cut off."),
    (2, "Greenhouse roof cracked", "greenhouse", 180, "The wind cracked the greenhouse roof; volunteers are needed to cover the glass before the plants freeze."),
    (2, "Flowers on the fountain", "plaza", 300, "Someone left a bouquet of wildflowers on the fountain, with a card that just says 'sorry'."),
    (2, "Film crew at the lighthouse", "lighthouse", 180, "A film crew from the mainland is shooting at the lighthouse and asking locals to be extras."),
    (3, "Boat overdue", "dock", 180, "A fishing boat with two islanders aboard has not come back, and the sea is getting rough. People are gathering on the pier."),
    (3, "Fire at the boatyard", "workshop", 60, "Smoke is pouring out of the boatyard workshop; people are forming a bucket line from the harbor."),
    (3, "Child missing near the cliffs", "cliffs", 120, "A seven-year-old has gone missing near the cliffs; a search party is gathering at the cliff path."),
    (3, "Greenhouse to be demolished", None, 600, "The council announced the greenhouse will be demolished to make room for the new pier."),
    (3, "A stranger asking questions", "dock", 240, "A stranger who came off the ferry is going around asking about {target} by name."),
]
FOLLOW_UPS = {
    "Boat overdue": (150, "dock", 60, "The missing fishing boat limped back into harbor; both islanders are safe but shaken."),
    "Child missing near the cliffs": (90, "cliffs", 60, "The missing child was found safe, asleep in a hollow on the cliff path."),
    "Fire at the boatyard": (50, "workshop", 120, "The boatyard fire is out; one wall is charred and the owner is counting the damage."),
}

# Letters by the ferry: (intensity, text) -- private, delivered to one resident.
LETTERS = [
    (1, "A letter from an old friend says they have been thinking about me and hope I am well on the island."),
    (1, "My sister wrote that she is getting married in the spring and wants me there."),
    (1, "A parcel came with a hand-knitted scarf and no note, just my name on the label."),
    (2, "A job I applied for on the mainland wrote back: they chose another candidate."),
    (2, "My landlord back home wrote that my old apartment has been rented out and my things are in storage."),
    (2, "A letter from the mainland bank says my savings account has been frozen pending a review."),
    (2, "A job I applied for on the mainland wrote back: I got the interview, next month."),
    (3, "A letter from my mother says my father is in hospital after a fall, stable for now."),
    (3, "An unsigned letter says: 'I know why you really came to this island.'"),
]

_INCIDENT_RATE = {1: 0.06, 2: 0.14, 3: 0.24}     # chance per hour
_LETTER_RATE = {1: 0.12, 2: 0.25, 3: 0.35}       # chance per ferry

_DIRECTOR_SYSTEM = """You are the DIRECTOR of a small-island life simulation that is being filmed.
Residents are raised by a personality engine; your job is only to make things HAPPEN to the
island so life stays interesting -- never decide what any resident does, thinks or feels.

Invent ONE new event that fits the time, weather and what has been going on. Mix the everyday
with the surprising; occasionally make it personal (a letter, a visitor, news) for one resident.
No graphic violence, no deaths of residents, nothing sexual. Describe it as something people
can see or hear, in one or two plain sentences.

Return JSON only:
{"title": "short title", "text": "what people perceive", "place": "<place id or null for island-wide>",
 "minutes": <duration 30-600>, "target": "<resident id for a private event, else null>"}"""


def next_weather(rng: random.Random, current: str) -> str:
    table = _WEATHER_NEXT.get(current, _WEATHER_NEXT["clear"])
    return rng.choices(list(table), weights=list(table.values()))[0]


def community_event(day: int) -> dict:
    title, place, start, end, text = COMMUNITY[(day - 1) % len(COMMUNITY)]
    base = (day - 1) * 1440
    return {"title": title, "place": place, "start": base + start * 60, "end": base + end * 60,
            "text": text, "kind": "community"}


def ferry_times(day: int) -> list[int]:
    base = (day - 1) * 1440
    return [base + 10 * 60, base + 17 * 60]


def incident_due(rng: random.Random, intensity: int) -> bool:
    """Whether an incident strikes this hour (seeded)."""
    return rng.random() < _INCIDENT_RATE.get(intensity, 0.14)


def pick_incident(rng: random.Random, intensity: int, now: int, resident_names: list[str]) -> dict:
    """Rules mode: one incident from the deck, no stronger than ``intensity``."""
    pool = [i for i in INCIDENTS if i[0] <= intensity and ("{target}" not in i[4] or resident_names)]
    level, title, place, minutes, text = rng.choice(pool)
    if "{target}" in text:
        text = text.replace("{target}", rng.choice(resident_names))
    return {"title": title, "place": place, "start": now, "end": now + minutes, "text": text,
            "kind": "incident", "level": level}


def follow_up(event: dict) -> dict | None:
    spec = FOLLOW_UPS.get(event.get("title", ""))
    if not spec:
        return None
    delay, place, minutes, text = spec
    start = event["start"] + delay
    return {"title": event["title"] + " -- resolved", "place": place, "start": start,
            "end": start + minutes, "text": text, "kind": "incident"}


def roll_letter(rng: random.Random, intensity: int, resident_ids: list[str]) -> tuple[str, str] | None:
    if not resident_ids or rng.random() >= _LETTER_RATE.get(intensity, 0.25):
        return None
    pool = [text for level, text in LETTERS if level <= intensity]
    return rng.choice(resident_ids), rng.choice(pool)


def llm_event(context: dict, stats: llm.Stats | None = None) -> dict:
    """LLM mode: one fresh event; raises on any failure (caller uses the deck)."""
    places = ", ".join(f"{k} ({v['name']})" for k, v in LOCATIONS.items())
    user = (f"Place ids: {places}\n\nRight now:\n{json.dumps(context, ensure_ascii=False, indent=1)}\n\n"
            "Create the next event.")
    data = llm.complete_json(_DIRECTOR_SYSTEM, user, temperature=1.0, stats=stats)
    title = str(data.get("title") or "").strip()[:80]
    text = str(data.get("text") or "").strip()[:400]
    if not title or not text:
        raise ValueError("director returned an empty event")
    place = data.get("place")
    place = place if place in LOCATIONS else None
    try:
        minutes = max(30, min(600, int(data.get("minutes") or 120)))
    except (TypeError, ValueError):
        minutes = 120
    target = data.get("target")
    return {"title": title, "text": text, "place": place, "minutes": minutes,
            "target": target if isinstance(target, str) else None}
