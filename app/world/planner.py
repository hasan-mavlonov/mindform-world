"""The planner: what each resident DOES next. It is the world's, not the resident's.

The resident's mind is MindForm -- the planner only turns that formed inner state (needs,
stance, temperament, values, beliefs, voice, relationships) into a concrete next move on the
island. Two interchangeable brains:

    llm    the world's model reads the inner state + surroundings and picks an action
    rules  a seeded utility model over the same inputs (fast, free, reproducible)

The LLM is always backed by the rules: any failure or invalid choice falls back per resident.

An action is a dict:
    {"type": "do",   "activity": "<activity id>"}        (walks there first if needed)
    {"type": "go",   "place": "<place id>"}              (go and see what is going on)
    {"type": "talk", "person": "<resident id>", "say": "opening line"}
    {"type": "rest"} / {"type": "home"}
plus "intent" (a few words of why, shown on screen) and "source" ("llm" / "rules").
"""
from __future__ import annotations

import json
import math
import random

from app import llm
from app.world.places import ACTIVITIES, JOBS, LOCATIONS

_SOCIAL_KEYS = {"plaza.music", "cafe.breakfast", "cafe.coffee", "market.browse", "clinic.volunteer",
                "beach.bonfire", "rowing_club.row"}
_SKILL_KEYS = {"library.study", "workshop.repair", "workshop.build", "rowing_club.weights",
               "rowing_club.row", "cliffs.hike", "greenhouse.tend", "plaza.music", "dock.fish"}
_SOLO_PLACES = {"cliffs", "lighthouse", "library", "greenhouse"}

PLANNER_SYSTEM = """You are the PLANNER in a simulation of life on a small island, Halcyon Isle.
You decide what ONE resident physically does in the next {minutes} minutes.

You are NOT this person's mind. Their mind is MindForm, a personality engine that has been
forming them through everything they have lived; its current read-out is given to you. Your
job is to turn that inner state into a plausible, specific next move:
- starved needs push them toward what would meet them (connection, mastery, independence);
- their behavioral stance governs approach vs avoidance: "leaning in" seeks people and risk,
  "holding back" avoids confrontation, crowds and exposure;
- temperament, values, beliefs, relationships, job hours, time of day and weather all matter;
- respond to what just happened to them and what is going on around the island.
Never invent places, people or activities that are not in the options.

If they talk to someone, "say" is the exact first line they say out loud: short, natural,
in their own manner of speaking (see "Manner of speaking"). If they are already in a
conversation and want to keep talking, choose "talk" with the same person and leave "say"
empty -- their mind supplies the words.

Return JSON only, exactly:
{{"action": "do" | "go" | "talk" | "rest" | "home",
  "activity": "<activity id, for do>", "place": "<place id, for go>",
  "person": "<resident id, for talk>", "say": "<line, for talk>",
  "intent": "<5-12 words, third person: why they are doing this>"}}"""


def describe_context(ctx: dict) -> str:
    """Everything the LLM planner sees, as plain text."""
    lines = [f"Time: {ctx['time_text']} ({ctx['daypart']}). Weather: {ctx['weather_text']}."]
    if ctx["events"]:
        lines.append("Going on around the island:")
        lines += [f"  - {e['title']} at {e['place_name']}: {e['text']}" for e in ctx["events"]]
    ident = ", ".join(f"{k}: {v}" for k, v in (ctx["identity"] or {}).items()
                      if v and k not in ("name",) and isinstance(v, (str, int, float)))
    lines.append(f"\nRESIDENT: {ctx['name']} (id: {ctx['id']}). {ident}")
    lines.append(f"Job: {ctx['job_title']}" + (f", works {ctx['job_hours'][0]}:00-{ctx['job_hours'][1]}:00"
                                                if ctx.get("job_hours") else ""))
    if ctx.get("goal"):
        lines.append(f"What they want right now: {ctx['goal']}")
    lines.append(f"\nINNER STATE (from MindForm):\n{ctx['state_text']}")
    if ctx.get("inclination"):
        lines.append(f"What they are inclined to say next to {ctx['inclination']['to_name']}: "
                     f"\"{ctx['inclination']['text']}\"")
    lines.append(f"\nWHERE: {ctx['place_name']}.")
    if ctx["here"]:
        lines.append("Also here: " + "; ".join(f"{p['name']} (id {p['id']}) -- {p['doing']}" for p in ctx["here"]))
    if ctx.get("talking_with"):
        names = ", ".join(p["name"] for p in ctx["talking_with"])
        lines.append(f"In conversation with: {names} (for {ctx['conversation_len']} stretch(es) so far)")
    if ctx.get("last_experience"):
        lines.append(f"\nWhat just happened to them: {ctx['last_experience']}")
    if ctx.get("recent"):
        lines.append("Earlier: " + " | ".join(ctx["recent"]))
    if ctx["relationships"]:
        lines.append("\nRELATIONSHIPS:")
        for rel in ctx["relationships"]:
            lines.append(f"  - {rel['name']} (id {rel['id']}): {rel['feeling']}, talked {rel['talks']}x"
                         + (f"; last: {rel['last']}" if rel.get("last") else ""))
    lines.append("\nOPTIONS")
    lines.append("Do here: " + ("; ".join(f"{a} ({ACTIVITIES[a]['label']})" for a in ctx["options"]["here"])
                                 or "nothing special"))
    lines.append("Do elsewhere:")
    for place, acts in ctx["options"]["elsewhere"].items():
        if place.startswith("home:"):
            label = "your cottage"
        else:
            label = LOCATIONS[place]["name"]
        lines.append(f"  {place} ({label}): " + "; ".join(f"{a} ({ACTIVITIES[a]['label']})" for a in acts))
    lines.append("People they could talk to: " + ("; ".join(
        f"{p['name']} (id {p['id']}, at {p['place_name']})" for p in ctx["people"]) or "nobody is around"))
    return "\n".join(lines)


def _valid(action: dict, ctx: dict) -> dict | None:
    kind = action.get("action") or action.get("type")
    intent = str(action.get("intent") or "").strip()[:120]
    people = {p["id"] for p in ctx["people"]}
    activities = set(ctx["options"]["here"]) | {a for acts in ctx["options"]["elsewhere"].values() for a in acts}
    if kind == "do" and action.get("activity") in activities:
        return {"type": "do", "activity": action["activity"], "intent": intent}
    if kind == "go" and (action.get("place") in LOCATIONS):
        return {"type": "go", "place": action["place"], "intent": intent}
    if kind == "talk" and action.get("person") in people:
        say = str(action.get("say") or "").strip().strip('"')[:300]
        return {"type": "talk", "person": action["person"], "say": say, "intent": intent}
    if kind in ("rest", "home"):
        return {"type": kind, "intent": intent}
    return None


def plan_llm(ctx: dict, stats: llm.Stats | None = None) -> dict:
    """Raises on failure or an invalid choice (the caller falls back to ``plan_rules``)."""
    system = PLANNER_SYSTEM.format(minutes=ctx["beat_minutes"])
    data = llm.complete_json(system, describe_context(ctx), temperature=0.9, stats=stats)
    action = _valid(data, ctx)
    if action is None:
        raise ValueError(f"planner chose an invalid action: {json.dumps(data)[:200]}")
    action["source"] = "llm"
    return action


# --- Rules ------------------------------------------------------------------------------
def _trait(state: dict, key: str) -> float:
    for t in state.get("traits") or []:
        if t.get("key") == key:
            return float(t.get("value") or 0.0)
    return 0.0


def _need(state: dict, key: str) -> float:
    for n in state.get("needs") or []:
        if n.get("key") == key:
            return float(n.get("tension") or 0.0)
    return 0.3


def _opener(ctx: dict, person: dict, rng: random.Random) -> str:
    voice = (ctx["state"].get("voice") or "")
    name = person["name"]
    if ctx["events"] and rng.random() < 0.5:
        return f"{name}, have you heard? {ctx['events'][0]['title']}."
    if "hedged" in voice or "wound tight" in voice:
        pool = [f"Sorry, {name} -- is now a bad time?", f"Um, hi {name}. Mind if I join you?"]
    elif "blunt" in voice or "plainspoken" in voice:
        pool = [f"{name}. Got a minute?", f"Hey {name}, I want to ask you something."]
    elif "warm" in voice:
        pool = [f"Hi {name}! How has your day been?", f"{name}! I was hoping I'd run into you."]
    elif "animated" in voice:
        pool = [f"{name}! Tell me something interesting.", f"{name}, there you are! What are you up to?"]
    else:
        pool = [f"Hello, {name}.", f"Hi {name}, how's it going?"]
    return rng.choice(pool)


_CONTINUE = ["So, anyway -- what have you been up to?", "Yeah. I know what you mean.",
             "Hm. Tell me more.", "Right. And then what?", "I hadn't thought of it like that.",
             "Anyway, I should let you get on.", "Fair enough."]


def continuation(rng: random.Random) -> str:
    """A filler line for minds that formed no reply of their own (control residents offline)."""
    return rng.choice(_CONTINUE)


def plan_rules(ctx: dict, rng: random.Random) -> dict:
    """Seeded utility choice from the same inputs the LLM sees."""
    state = ctx["state"] or {}
    hour = ctx["hour"]
    O, C, E, A, N = (_trait(state, k) for k in "OCEAN")
    rel_need, comp_need, aut_need = _need(state, "relatedness"), _need(state, "competence"), _need(state, "autonomy")
    stance = state.get("stance") or {}
    lean = float(stance.get("tendency") or 0.0)
    esteem = float(state.get("esteem") or 0.0)
    bad_weather = ctx["weather"] in ("rain", "storm")
    job = JOBS.get(ctx["job"] or "none", JOBS["none"])
    in_hours = bool(job["hours"]) and job["hours"][0] <= hour < job["hours"][1]
    event_places = {e["place"]: e for e in ctx["events"] if e.get("place")}
    visited = set(ctx.get("visited_today") or [])

    options: list[tuple[float, dict]] = []

    def add(score: float, action: dict):
        options.append((score, action))

    all_acts = [(ctx["place"], a) for a in ctx["options"]["here"]]
    all_acts += [(p, a) for p, acts in ctx["options"]["elsewhere"].items() for a in acts]
    for place, act_id in all_acts:
        act = ACTIVITIES[act_id]
        key = "home" if place.startswith("home:") else place
        loc = LOCATIONS.get(key, {"social": 0.1, "outdoors": False})
        social = act_id in _SOCIAL_KEYS or loc["social"] >= 0.6
        solitary = bool(act.get("solitary")) or key in _SOLO_PLACES
        s = 0.0
        if act.get("job"):
            s += 3.0 + 1.2 * C if in_hours else -2.0
        elif in_hours and job["place"]:
            s -= 1.0 + 0.8 * max(C, 0.0)          # skipping work needs a reason
        if act_id == "cafe.breakfast" and hour < 10:
            s += 1.4
        if act_id == "home.cook" and 18 <= hour < 21:
            s += 1.5
        if act_id == "home.rest" and hour >= 21:
            s += 1.2
        if social:
            s += 2.0 * rel_need + 1.0 * E - 0.6 * max(-lean, 0.0)
        if solitary:
            s += 1.5 * aut_need - 0.8 * E + 0.8 * max(-lean, 0.0) + (0.5 if esteem < -0.2 else 0.0)
        if act_id in _SKILL_KEYS:
            s += 1.8 * comp_need + 0.4 * O
        if key not in visited and key != "home":
            s += 0.8 * O
        if loc.get("outdoors") and bad_weather:
            s -= 0.8 + 1.5 * max(N, 0.0)
        if key in event_places:
            s += 1.2 + 0.8 * E + 0.6 * lean
        crowd = sum(1 for p in ctx["people"] if p["place"] == place)
        if crowd and not act.get("job"):
            s += min(1.5, 0.45 * crowd) * (0.4 + E + rel_need + 0.5 * lean)
        if place == ctx["place"]:
            s += 0.5                              # staying put is cheaper than walking
        if act_id == ctx.get("activity_id"):
            s += 0.4                              # some inertia
        add(s, {"type": "do", "activity": act_id,
                "intent": _intent_for(act_id, rel_need, comp_need, aut_need, key in event_places, act.get("job"))})

    for place, event in event_places.items():
        if place != ctx["place"]:
            add(1.0 + 0.9 * E + 0.7 * lean + 0.4 * O, {"type": "go", "place": place,
                                                       "intent": f"drawn to the {event['title'].lower()}"})

    talking = {p["id"] for p in ctx.get("talking_with") or []}
    rels = {r["id"]: r for r in ctx["relationships"]}
    for person in ctx["people"]:
        rel = rels.get(person["id"], {})
        aff = float(rel.get("affinity") or 0.0)
        s = 1.0 + 2.0 * rel_need + 1.0 * E + 1.2 * lean + 1.5 * aff
        s += 1.0 if person["here"] else -0.3
        if person["id"] in talking:
            s += 2.5 - 0.6 * ctx["conversation_len"]
        elif not rel.get("talks"):
            s += 0.3 * (O + A) + 0.4                 # curiosity about someone new
        if in_hours and job["place"] and not person["here"]:
            s -= 1.2                                  # chatting at work is fine; leaving it isn't
        say = "" if person["id"] in talking else _opener(ctx, person, rng)
        intent = ("keeps the conversation going" if person["id"] in talking
                  else "wants company" if rel_need > 0.45 else f"curious about {person['name']}"
                  if not rel.get("talks") else f"seeks out {person['name']}")
        add(s, {"type": "talk", "person": person["id"], "say": say, "intent": intent})

    add(0.2 + 0.8 * max(-lean, 0.0) + 0.6 * max(N, 0.0) * (1 if bad_weather else 0.3),
        {"type": "home", "intent": "wants to be alone at home"})

    # Softmax pick (seeded): stable but not robotic.
    temperature = 0.6
    top = max(s for s, _ in options)
    weights = [math.exp((s - top) / temperature) for s, _ in options]
    action = dict(rng.choices([a for _, a in options], weights=weights)[0])
    action["source"] = "rules"
    return action


def _intent_for(act_id: str, rel: float, comp: float, aut: float, event: bool, job: str | None) -> str:
    if job:
        return "it's their working hours"
    if event:
        return "wants to see what's happening"
    top = max((rel, "looking for company"), (comp, "wants to get something right"),
              (aut, "wants time on their own terms"))
    return top[1] if top[0] > 0.35 else f"feels like it: {ACTIVITIES[act_id]['label']}"
