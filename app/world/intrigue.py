"""Secrets, goals, gossip and relationships: what residents hide, want, know and feel about each other.

The world's side of social life -- facts about who did what and who knows what. Feelings stay
MindForm's: how warm a resident grows toward someone moves with MindForm's own reading of their
encounters (the valence of its appraisal); trust moves with what visibly happened between them
(a confided secret, a walk-off, gossip that got back to them, a lie found out).

A SECRET is a short phrase that completes "<name> ...", e.g. "is secretly building a boat to
leave the island". The world matches it to a kind (boat, charts, ticket, debt, ...) for the
facts it produces: what the resident does about it (first person, for their own experience),
what someone nearby could notice (a clue), what they think, let slip, confess or deny. Others
come to know it by
    * seeing clues (being there while they work on it),
    * gossip (a clue or the secret itself, passed on in conversation),
    * a slip under stress, a confession to someone they trust,
    * a public reveal (god mode's anonymous letter).
Suspicion adds up per (observer, holder); at 1.0 the observer has worked it out.
"""
from __future__ import annotations

import random
import re

SUSPECT_FROM_CLUE = 0.45
SUSPECT_FROM_GOSSIP = 0.3
SUSPECT_FROM_SLIP = 0.4
KNOWLEDGE_KEEP = 24

SECRET_KINDS: dict[str, dict] = {
    "boat": {
        "match": r"\bboat|raft|hull|sail|escape\b", "place": "workshop", "short": "the boat", "hours": (17, 23),
        "cover": "staying late at the boatyard",
        "work": ["I worked on the boat at the back of the boatyard until my hands were black with tar, then pulled the tarp back over it.",
                 "I fitted two new planks to the hull under the tarp at the back of the boatyard and swept the shavings away.",
                 "I tested the boat's seams with a bucket of water behind the boatyard; one seam leaked and I caulked it again.",
                 "I sewed a patch into the old sail I keep rolled up under the workbench and checked the oars."],
        "clue": ["{name} was at the back of the boatyard after hours, pulling a tarp over something long and boat-shaped.",
                 "{name} came out of the boatyard long after closing, sawdust all over their sleeves.",
                 "{name} bought tar, rope and a compass at the market -- far more than a repair job needs.",
                 "{name} was down at the slipway at dusk, measuring the water's depth with a marked pole."],
        "inner": ["Two more planks and she floats.", "Nobody looks under the tarp. Nobody.", "One calm night. That's all I need.",
                  "Winter's coming. So am I — off this rock."],
        "slip": ["Some of us won't be around for winter anyway.", "Boats don't build themselves. Forget I said that.",
                 "A good hull's worth more than this whole island."],
        "confide": ["Can you keep something quiet? I'm building a boat, behind the boatyard. I'm getting off this island.",
                    "There's a boat under that tarp. Mine. I'm leaving, and I haven't told anyone."],
        "deny": ["A boat? It's a repair job. Drop it.", "I fix boats. That's my job. Nothing more."],
        "admit": ["Fine. Yes. I'm building a boat. And I'm leaving.", "So you saw it. She's nearly done. I'm going."],
    },
    "charts": {
        "match": r"chart|patient|dose|medic|prescri|records", "place": "clinic", "short": "the charts", "hours": (15, 21),
        "cover": "catching up on paperwork at the clinic",
        "work": ["I stayed late at the clinic and went through the patient files again, checking every chart I had touched that week.",
                 "I re-copied two patient charts by hand in the records room and put the old pages in my bag.",
                 "I checked the medicine log against the charts twice, then locked the records cabinet."],
        "clue": ["{name} was alone in the clinic records room after hours, going through patient files.",
                 "{name} snatched a patient chart off the desk the moment someone walked into the clinic.",
                 "{name} had a stack of clinic charts in their bag at the café, and covered it with a scarf."],
        "inner": ["If anyone pulls last Tuesday's charts, that's it.", "Two patients. Wrong charts. Nobody noticed. Yet.",
                  "Keep it together. Keep it filed."],
        "slip": ["Paperwork mistakes happen to everyone. Right?", "Why are you asking about the charts? I mean — what charts?",
                 "Nobody died. Nobody died, okay?"],
        "confide": ["I mixed up two patients' charts. Nobody caught it. I fixed it, but I never told anyone.",
                    "I made a mistake at the clinic. A bad one. The charts. I've been hiding it for weeks."],
        "deny": ["The charts are fine. I'd know.", "That's a serious thing to accuse a nurse of."],
        "admit": ["Yes. I mixed them up. I fixed it. I should have said.", "It was one night. I was exhausted. Yes, it was me."],
    },
    "ticket": {
        "match": r"ticket|one-way|ferry|mainland|leav|quit|move away|go back home", "place": "dock", "short": "the ticket", "hours": (9, 18),
        "cover": "watching the ferry come in",
        "work": ["I checked the ferry timetable on the dock board and touched the ticket in my coat pocket.",
                 "I asked the ferry office, quietly, whether a one-way ticket can be changed to an earlier date.",
                 "I counted my savings at home and folded the one-way ferry ticket back into its envelope."],
        "clue": ["{name} was at the ferry office, asking how much luggage a one-way passenger can bring.",
                 "{name} dropped an envelope on the pier; inside was a one-way ferry ticket.",
                 "{name} was reading the mainland job ads pinned at the ferry office, one by one."],
        "inner": ["One ferry. That's all it takes.", "Don't look back. Don't look back.", "They'll be fine without me. Won't they?"],
        "slip": ["It's not like I'll be here for the regatta anyway.", "Mainland rents are awful, apparently. Not that I've looked."],
        "confide": ["I bought a one-way ferry ticket. I haven't told anyone. I'm leaving.",
                    "Don't tell the others — I'm going. I've already got the ticket."],
        "deny": ["Leaving? Where would I even go?", "I'm not going anywhere. Who said that?"],
        "admit": ["Yes. I'm leaving. The ticket's in my coat.", "I was going to tell you. I swear I was."],
    },
    "debt": {
        "match": r"money|debt|owe|loan|gambl|stole|steal", "place": "dock", "short": "the money", "hours": (7, 20),
        "cover": "making a phone call at the dock",
        "work": ["I made a call from the payphone at the dock and promised the man on the line he'd have the money by next month.",
                 "I counted what I had left in the tin under my bed. Not enough. Not nearly."],
        "clue": ["{name} was on the dock payphone, hissing 'I said I'll pay' into the receiver.",
                 "{name} sold their good watch at the market for half what it's worth."],
        "inner": ["Next month. I'll have it by next month.", "Smile. Nobody needs to know."],
        "slip": ["Money's just numbers. Until it isn't.", "You don't happen to need a watch, do you?"],
        "confide": ["I owe money. To people you don't want to owe money to.", "I'm in debt. Deep. And they know where I am."],
        "deny": ["Money trouble? Me? I'm fine.", "That's nobody's business."],
        "admit": ["Yes, I owe money. A lot. I'm handling it.", "Fine. I'm broke and in trouble. Happy?"],
    },
    "generic": {
        "match": r"", "place": None, "short": "what I'm hiding", "hours": (8, 22),
        "cover": "keeping to themselves",
        "work": ["I spent the time making sure nobody would find out what I've been hiding.",
                 "I went over it all again in my head and checked that I hadn't left anything lying around."],
        "clue": ["{name} went quiet and changed the subject the moment someone walked in.",
                 "{name} quickly put something away when they saw people coming."],
        "inner": ["Nobody can know.", "Keep your head down. Keep it buried.", "If they knew…"],
        "slip": ["Everyone's got something they don't talk about. Right?", "Don't ask me about that. Ever."],
        "confide": ["I've never told anyone this. {secret_cap}.", "Can I trust you? Okay. {secret_cap}."],
        "deny": ["I don't know what you're talking about.", "Whoever told you that is lying."],
        "admit": ["…It's true. All of it.", "Yes. Now you know."],
    },
}
_KIND_ORDER = ["boat", "charts", "debt", "ticket"]


def secret_kind(text: str) -> str:
    low = (text or "").lower()
    for kind in _KIND_ORDER:
        if re.search(SECRET_KINDS[kind]["match"], low):
            return kind
    return "generic"


def make_secret(text: str, job_place: str | None, home: str) -> dict | None:
    text = (text or "").strip().rstrip(".")
    if not text:
        return None
    kind = secret_kind(text)
    spec = SECRET_KINDS[kind]
    return {"text": text, "kind": kind, "place": spec["place"] or job_place or home, "short": spec["short"],
            "known_by": [], "exposed": False, "confronted_by": []}


def reveal_text(name: str, secret: dict) -> str:
    return f"{name} {secret['text']}."


def secret_line(secret: dict, which: str, rng: random.Random, name: str = "") -> str:
    spec = SECRET_KINDS.get(secret.get("kind", "generic"), SECRET_KINDS["generic"])
    pool = spec.get(which) or SECRET_KINDS["generic"][which]
    line = rng.choice(pool)
    me = first_person_secret(secret["text"])
    return line.replace("{name}", name).replace("{secret_cap}", me[:1].upper() + me[1:])


def first_person_secret(text: str) -> str:
    """'is secretly building a boat' -> 'I'm secretly building a boat'."""
    t = text.strip()
    for a, b in (("is ", "I'm "), ("has ", "I have "), ("was ", "I was "), ("owes ", "I owe "), ("hides ", "I hide "),
                 ("lied ", "I lied "), ("stole ", "I stole ")):
        if t.startswith(a):
            return b + t[len(a):]
    return "I " + t


_SWAPS = {"her": "my", "his": "my", "their": "my", "he": "I", "she": "I", "they": "I", "him": "me",
          "herself": "myself", "himself": "myself", "themselves": "myself", "hers": "mine", "theirs": "mine",
          "owes": "owe", "is": "am", "has": "have"}


def first_person_goal(goal: str) -> str:
    """'save enough to open her own bakery' -> 'save enough to open my own bakery'."""
    goal = (goal or "").strip().rstrip(".")
    return " ".join(_SWAPS.get(w, w) for w in goal.split(" ")) if goal else ""


def cover_doing(secret: dict) -> str:
    return SECRET_KINDS.get(secret.get("kind", "generic"), SECRET_KINDS["generic"])["cover"]


def secret_hours(secret: dict) -> tuple[int, int]:
    return SECRET_KINDS.get(secret.get("kind", "generic"), SECRET_KINDS["generic"])["hours"]


# ---- goals -> where they pull a resident (rules planner) --------------------------------------
GOAL_PULLS = [
    (r"leav|ferry|mainland|off the island|go home|move away", ["dock.watch", "town_hall.notices", "library.letter"]),
    (r"baker|bakery|pastr|bread", ["cafe.work", "market.browse"]),
    (r"money|save|debt|pay back|rich", ["market.work", "dock.work", "cafe.work"]),
    (r"mayor|elect|council|vote|pier|contract", ["town_hall.notices", "town_hall.work"]),
    (r"son|daughter|mother|father|family|visit|home", ["library.letter"]),
    (r"liked|friend|popular|everyone", ["plaza.music", "cafe.coffee", "beach.bonfire"]),
    (r"note|book|mystery|find out|who left", ["library.read", "library.study"]),
    (r"lighthouse|lamp", ["lighthouse.watch", "lighthouse.climb"]),
    (r"fit|strong|race|regatta|row", ["rowing_club.row", "rowing_club.weights"]),
    (r"garden|grow|plant", ["greenhouse.tend"]),
    (r"boat|sail", ["workshop.build", "workshop.repair"]),
]


def goal_activities(goal: str) -> list[str]:
    low = (goal or "").lower()
    out = []
    for pattern, acts in GOAL_PULLS:
        if re.search(pattern, low):
            out.extend(a for a in acts if a not in out)
    return out


# ---- relationships ---------------------------------------------------------------------------------
def new_rel() -> dict:
    return {"affinity": 0.0, "affection": 0.0, "trust": 0.0, "talks": 0, "last": ""}


def normalize_rel(rel: dict) -> dict:
    """Older saves had one number ('affinity'); it becomes affection, and trust starts at 0."""
    rel.setdefault("affection", rel.get("affinity", 0.0))
    rel.setdefault("trust", 0.0)
    rel.setdefault("talks", 0)
    rel.setdefault("last", "")
    rel["affinity"] = rel["affection"]
    return rel


def clamp(v: float) -> float:
    return max(-1.0, min(1.0, v))


def nudge_trust(rel: dict, delta: float) -> None:
    rel["trust"] = clamp(rel["trust"] + delta * (1 - abs(rel["trust"]) * 0.5))


def bond_label(affection: float, trust: float, talks: int) -> str:
    """Two numbers in one everyday word, for the inspector."""
    if not talks:
        return "strangers"
    if affection > 0.45 and trust > 0.35:
        return "close friends"
    if affection > 0.2 and trust > 0.1:
        return "friends"
    if affection > 0.2 and trust < -0.1:
        return "fond but wary"
    if affection < -0.35 and trust < -0.2:
        return "enemies"
    if affection < -0.2:
        return "rivals"
    if trust < -0.25:
        return "distrustful"
    if trust > 0.35:
        return "allies"
    return "acquaintances"


# ---- knowledge (what a resident knows about others) ---------------------------------------------
def learn(r, about: str, text: str, *, t: int, src: str, clue: bool = False, secret: bool = False) -> dict | None:
    """Add a fact to ``r.knowledge`` (no duplicates). Returns the fact if it was new."""
    for f in r.knowledge:
        if f["about"] == about and f["text"] == text:
            return None
    fact = {"about": about, "text": text, "t": t, "src": src, "clue": clue, "secret": secret, "told": []}
    r.knowledge = (r.knowledge + [fact])[-KNOWLEDGE_KEEP:]
    return fact


def gossip_for(speaker, listener, residents: dict, rng: random.Random) -> dict | None:
    """Something juicy the speaker knows about a third resident that the listener doesn't."""
    known_to_listener = {(f["about"], f["text"]) for f in listener.knowledge}
    pool = []
    for f in speaker.knowledge:
        if f["about"] in (speaker.id, listener.id) or f["about"] not in residents:
            continue
        if (f["about"], f["text"]) in known_to_listener or listener.id in f["told"]:
            continue
        weight = 3.0 if f["secret"] else 2.0 if f["clue"] else 1.0
        pool.append((weight, f))
    if not pool:
        return None
    return rng.choices([f for _, f in pool], weights=[w for w, _ in pool])[0]


def suspicion_of(r, holder_id: str) -> float:
    return float((r.suspicion or {}).get(holder_id, 0.0))


def add_suspicion(r, holder_id: str, amount: float) -> float:
    r.suspicion = dict(r.suspicion or {})
    r.suspicion[holder_id] = min(1.0, suspicion_of(r, holder_id) + amount)
    return r.suspicion[holder_id]


def knows_secret(r, holder) -> bool:
    return bool(holder.secret) and r.id in holder.secret.get("known_by", [])


# ---- what to talk about ----------------------------------------------------------------------------
def choose_topic(speaker, listener, world, rng: random.Random) -> dict:
    """The world's pick of what an opening line is about. Returns
    {"intent": open|gossip|confide|confront|probe|goal|comfort|event|outcome|weather|warm|cool,
     ...slot data}. The mind supplies the feeling; this only supplies the subject."""
    rel = normalize_rel(speaker.relationships.setdefault(listener.id, new_rel()))
    traits = trait_dict(speaker.state)
    E, A, N = traits["E"], traits["A"], traits["N"]
    last = (speaker.pair_topics or {}).get(listener.id)
    options: list[tuple[float, dict]] = []

    def add(w: float, topic: dict):
        if w > 0 and topic["intent"] != last:
            options.append((w, topic))

    if listener.secret and knows_secret(speaker, listener):
        confided = any(f["about"] == listener.id and f["secret"] and f["src"] == "confided" for f in speaker.knowledge)
        if confided or speaker.id in listener.secret.get("confronted_by", []):
            add(0.5 + 0.5 * A + 0.4 * rel["affection"], {"intent": "ally"})      # they share it now
        else:
            add(2.2 + max(0.0, -A) + max(0.0, -rel["trust"]), {"intent": "confront", "their_secret": listener.secret["text"]})
    if speaker.secret and not knows_secret(listener, speaker) and rel["trust"] > 0.35 and rel["affection"] > 0.25:
        pressure = max(0.0, -((speaker.mood or {}).get("valence") or 0.0)) + (0.5 if world.secret_pressure(speaker) else 0.0)
        add(0.5 + 1.5 * pressure + rel["trust"], {"intent": "confide"})
    if listener.secret and not knows_secret(speaker, listener) and suspicion_of(speaker, listener.id) >= 0.3:
        add(1.2 + suspicion_of(speaker, listener.id) + 0.3 * traits["O"], {"intent": "probe"})
    fact = gossip_for(speaker, listener, world.residents, rng)
    if fact:
        add(1.0 + 0.6 * E - 0.5 * A + (1.0 if fact["secret"] or fact["clue"] else 0.0),
            {"intent": "gossip", "fact": fact, "who": world.residents[fact["about"]].name})
    big = world.headline_event()
    if big:
        add(2.0 if big.get("force") else 1.2, {"intent": "event", "topic": big.get("topic") or topic_np(big["title"])})
    mood = listener.mood or {}
    if (mood.get("valence") or 0.0) < -0.3 and (mood.get("strength") or 0.0) >= 0.35:
        add(0.6 + A + 0.5 * rel["affection"], {"intent": "comfort"})
    if speaker.goal and rel["trust"] > 0.0:
        add(0.35 + 0.6 * rel["trust"], {"intent": "goal"})
    if speaker.last_outcome:
        add(0.5 + 0.4 * E, {"intent": "outcome", "did": speaker.last_outcome})
    if world.weather in ("rain", "storm", "fog"):
        add(0.4, {"intent": "weather"})
    if rel["talks"] and rel["affection"] > 0.3:
        add(0.7, {"intent": "warm"})
    if rel["talks"] and rel["affection"] < -0.25:
        add(0.8, {"intent": "cool"})
    add(0.6, {"intent": "small"})
    if not options:
        return {"intent": "small"}
    return rng.choices([t for _, t in options], weights=[w for w, _ in options])[0]


def topic_np(title: str) -> str:
    """'Power cut' -> 'the power cut'; 'A lost dog' -> 'the lost dog'."""
    t = (title or "").strip().rstrip(".!")
    if not t:
        return "all this"
    t = re.sub(r"\s+--\s+resolved$", "", t)
    low = t[:1].lower() + t[1:]
    if re.match(r"^(the|a|an)\s", low):
        low = re.sub(r"^(a|an)\s", "the ", low)
        return low
    return "the " + low


def trait_dict(state: dict | None) -> dict[str, float]:
    out = {k: 0.0 for k in "OCEAN"}
    for t in (state or {}).get("traits") or []:
        if t.get("key") in out:
            out[t["key"]] = float(t.get("value") or 0.0)
    return out
