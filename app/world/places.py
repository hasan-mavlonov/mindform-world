"""Halcyon Isle: the places, what can be done there, and what can come of it.

The world's physics, not anyone's psychology. Every outcome line is a FACT written in the
first person and past tense -- what happened, what others visibly did or said -- never how
it felt. Feelings are MindForm's job: the same "the owner told me my pastries sold out
first" is pride to one resident and pressure to another.

Outcome tuples: ``(weight, quality, text, condition)``
    quality    "+" went well, "-" a setback, "0" neutral -- used ONLY for world odds (a
               resident working their own trade succeeds more often); never shown to minds
    condition  None (any weather), "bad" (only in rain/storm), "fair" (only when it isn't)
"""
from __future__ import annotations

import math

ISLAND_NAME = "Halcyon Isle"
ISLAND_RADIUS = 27.0
HUB = "plaza"

# kind -> which 3D model the client builds for the place
LOCATIONS: dict[str, dict] = {
    "plaza": {"name": "Fountain Plaza", "kind": "plaza", "x": 0.0, "z": 0.0, "outdoors": True,
              "social": 0.8, "blurb": "the town square around an old stone fountain"},
    "cafe": {"name": "Tidewater Café", "kind": "cafe", "x": -6.4, "z": -4.4, "outdoors": False,
             "social": 0.9, "blurb": "the island's café and bakery, busy at breakfast and lunch"},
    "market": {"name": "Harbor Market", "kind": "market", "x": 6.6, "z": -4.0, "outdoors": True,
               "social": 0.7, "blurb": "a row of stalls selling fish, bread, fruit and junk"},
    "library": {"name": "Old Library", "kind": "library", "x": -12.8, "z": -12.6, "outdoors": False,
                "social": 0.2, "blurb": "a quiet stone library with a reading room"},
    "town_hall": {"name": "Town Hall", "kind": "town_hall", "x": -1.0, "z": -17.5, "outdoors": False,
                  "social": 0.5, "blurb": "the clock-towered hall where the island meets and votes"},
    "clinic": {"name": "Island Clinic", "kind": "clinic", "x": 10.5, "z": -15.5, "outdoors": False,
               "social": 0.4, "blurb": "a small white clinic with one doctor and one nurse"},
    "workshop": {"name": "Boatyard Workshop", "kind": "workshop", "x": 17.5, "z": -4.5, "outdoors": False,
                 "social": 0.4, "blurb": "a timber boatyard that smells of tar and sawdust"},
    "lighthouse": {"name": "Lighthouse Point", "kind": "lighthouse", "x": 18.6, "z": -13.6, "outdoors": True,
                   "social": 0.1, "blurb": "a red-and-white lighthouse on the north-east point"},
    "dock": {"name": "Ferry Dock", "kind": "dock", "x": 20.5, "z": 6.0, "outdoors": True,
             "social": 0.5, "blurb": "the pier where the mainland ferry lands twice a day"},
    "rowing_club": {"name": "Rowing Club", "kind": "rowing_club", "x": 14.0, "z": 14.0, "outdoors": False,
                    "social": 0.6, "blurb": "a boathouse with a weights room and a rack of rowing shells"},
    "beach": {"name": "Sunset Beach", "kind": "beach", "x": 2.5, "z": 18.5, "outdoors": True,
              "social": 0.6, "blurb": "a long sandy beach with a bonfire pit, facing the sunset"},
    "cliffs": {"name": "Windward Cliffs", "kind": "cliffs", "x": -21.0, "z": 10.5, "outdoors": True,
               "social": 0.05, "blurb": "high grassy cliffs over the open sea, windy and empty"},
    "greenhouse": {"name": "Greenhouse Gardens", "kind": "greenhouse", "x": -19.5, "z": -4.5, "outdoors": False,
                   "social": 0.3, "blurb": "glasshouses and vegetable beds that feed the island"},
}

# Activities: id -> spec. ``job`` names the trade this is the work of (only that trade's
# residents are offered it); ``hours`` limits when it can be done.
ACTIVITIES: dict[str, dict] = {
    # --- Fountain Plaza
    "plaza.sit": {"place": "plaza", "label": "sit by the fountain and watch people pass", "solitary": True,
                  "outcomes": [
                      (3, "0", "I sat on the edge of the fountain while people crossed the plaza.", None),
                      (1, "+", "A pigeon landed right next to me on the fountain; a child laughed and pointed at it.", "fair"),
                      (1, "-", "A man on a bench nearby shouted into his phone the whole time I sat there.", None),
                      (2, "0", "I sat under the arcade out of the rain and watched the fountain overflow.", "bad")]},
    "plaza.music": {"place": "plaza", "label": "play music for passers-by", "social": True,
                    "outcomes": [
                        (2, "+", "I played music by the fountain; a small crowd gathered and clapped at the end.", "fair"),
                        (2, "-", "I played for a while; people walked past without stopping and nobody put anything in my case.", None),
                        (1, "+", "An old woman stopped, listened to the whole song, and asked me to play it again.", None),
                        (2, "-", "I tried to play but the rain soaked my sheet music and I packed up.", "bad")]},
    # --- Tidewater Café
    "cafe.breakfast": {"place": "cafe", "label": "have breakfast", "hours": (7, 11), "social": True,
                       "outcomes": [
                           (3, "0", "I ate eggs and toast at a window table while the café filled up around me.", None),
                           (1, "+", "I had breakfast at the counter; the cook asked my name and remembered it when I left.", None),
                           (1, "-", "The kitchen was slow; my breakfast took half an hour to arrive and came out cold.", None)]},
    "cafe.coffee": {"place": "cafe", "label": "have a coffee or tea", "social": True,
                    "outcomes": [
                        (3, "0", "I drank a cup at a corner table and watched the harbor through the window.", None),
                        (1, "-", "The espresso machine broke just as I ordered; the barista apologized and gave me a tea.", None),
                        (1, "+", "A stranger at the next table asked me about the island and we talked about the ferry schedule.", None)]},
    "cafe.work": {"place": "cafe", "label": "work a shift at the café", "job": "baker",
                  "outcomes": [
                      (2, "0", "The rush hit all at once; I worked the counter for the whole stretch without a break.", None),
                      (1, "-", "A customer complained loudly, in front of the whole queue, that I had gotten her order wrong.", None),
                      (1, "+", "The owner told me my pastries had sold out first and asked for a double batch tomorrow.", None),
                      (1, "0", "It was quiet; I wiped tables and restocked cups while rain ran down the windows.", "bad"),
                      (1, "-", "I burned a whole tray of bread and the owner made me throw it out.", None)]},
    # --- Harbor Market
    "market.groceries": {"place": "market", "label": "buy groceries", "hours": (8, 18),
                         "outcomes": [
                             (3, "0", "I bought bread, onions and a bag of oranges from the stalls.", None),
                             (1, "-", "The fish stall was sold out by the time I got there.", None),
                             (1, "+", "The fruit vendor threw in an extra lemon for free and wished me a nice day.", None)]},
    "market.browse": {"place": "market", "label": "browse the stalls", "hours": (8, 18), "social": True,
                      "outcomes": [
                          (2, "+", "At the junk table I found an old hand-drawn map of the island for one coin.", None),
                          (2, "-", "Two vendors were arguing loudly over a stall space as I walked past; one of them knocked over a crate.", None),
                          (2, "0", "I wandered the stalls under the dripping awnings without buying anything.", "bad")]},
    "market.work": {"place": "market", "label": "run your market stall", "job": "vendor", "hours": (8, 18),
                    "outcomes": [
                        (2, "+", "I sold almost everything on my stall before noon.", None),
                        (2, "-", "Hardly anyone stopped at my stall; I packed up most of what I had brought.", None),
                        (1, "-", "A customer haggled me down to half price and walked off laughing.", None),
                        (1, "+", "A regular bought three jars and told the next customer my stall was the best on the row.", None)]},
    # --- Old Library
    "library.read": {"place": "library", "label": "read in the reading room", "solitary": True,
                     "outcomes": [
                         (3, "0", "I read three chapters of a novel in the quiet reading room.", None),
                         (1, "-", "I could not get past the first page; the radiator kept clanking and a child kept crying.", None),
                         (1, "+", "Inside an old book I found a handwritten note: 'Whoever finds this -- the cliffs at dawn are worth it.'", None)]},
    "library.study": {"place": "library", "label": "study something new", "solitary": True,
                      "outcomes": [
                          (2, "+", "I worked through a navigation textbook and plotted a full course on the practice chart.", None),
                          (2, "-", "I spent the whole stretch on one chapter of statistics and still could not solve the exercises.", None),
                          (1, "0", "I took notes on the island's history from a crumbling archive folder.", None)]},
    "library.letter": {"place": "library", "label": "write a letter to someone back home", "solitary": True,
                       "outcomes": [
                           (2, "0", "I wrote a two-page letter home and sealed it for the next ferry.", None),
                           (1, "-", "I started a letter home three times and threw all three drafts away.", None)]},
    "library.work": {"place": "library", "label": "work the library desk", "job": "librarian",
                     "outcomes": [
                         (2, "0", "I catalogued a box of donated books.", None),
                         (1, "-", "A visitor shouted at me about a late fee in front of everyone in the reading room.", None),
                         (1, "+", "A girl I had recommended a book to came back and said she had read it twice.", None)]},
    # --- Town Hall
    "town_hall.notices": {"place": "town_hall", "label": "read the notice board",
                          "outcomes": [
                              (2, "0", "The notice board listed the next ferry times and a call for volunteers at the clinic.", None),
                              (1, "-", "A notice on the board said ferry service may be cut to twice a week next month.", None),
                              (1, "0", "Someone had pinned a lost-and-found list to the board; nothing on it was mine.", None)]},
    "town_hall.work": {"place": "town_hall", "label": "work as town clerk", "job": "clerk", "hours": (9, 17),
                       "outcomes": [
                           (2, "0", "I processed a stack of permits; two residents came in to complain about noise from the boatyard.", None),
                           (1, "+", "The mayor read the agenda I drafted and approved it without changing a word.", None),
                           (1, "-", "I misfiled a land deed and the mayor pointed it out sharply in front of a visitor.", None)]},
    # --- Island Clinic
    "clinic.checkup": {"place": "clinic", "label": "get a check-up", "hours": (8, 17),
                       "outcomes": [
                           (3, "+", "The nurse took my blood pressure and said everything looked normal.", None),
                           (1, "-", "The doctor said my blood pressure was high and asked me to come back next week.", None)]},
    "clinic.volunteer": {"place": "clinic", "label": "volunteer at the clinic", "social": True, "hours": (8, 18),
                         "outcomes": [
                             (2, "0", "I helped the nurse roll bandages and sort supplies.", None),
                             (1, "+", "An elderly patient held my hand and thanked me for sitting with her.", None),
                             (1, "-", "The doctor told me to stay out of the way during an emergency and sent me to the waiting room.", None)]},
    "clinic.work": {"place": "clinic", "label": "work a shift as the nurse", "job": "nurse",
                    "outcomes": [
                        (2, "+", "We stitched up a fisherman's hand; he shook mine on the way out.", None),
                        (2, "0", "The waiting room was full of coughs and sprained ankles all shift.", None),
                        (1, "-", "I mixed up two patients' charts and the doctor caught it before any harm was done.", None)]},
    # --- Boatyard Workshop
    "workshop.repair": {"place": "workshop", "label": "repair a boat",
                        "outcomes": [
                            (2, "+", "I replaced two cracked planks on a fishing boat's hull; the owner checked it and paid me on the spot.", None),
                            (2, "-", "I spent the whole time on an outboard engine and it still would not start.", None),
                            (1, "-", "I slipped with a chisel and cut my hand; it needed a bandage.", None)]},
    "workshop.build": {"place": "workshop", "label": "build something by hand", "solitary": True,
                       "outcomes": [
                           (2, "+", "I built a small wooden stool from scrap wood; it stood level on the first try.", None),
                           (2, "-", "The joint I was gluing split apart and I had to start over.", None)]},
    "workshop.work": {"place": "workshop", "label": "work in the boatyard", "job": "boatbuilder",
                      "outcomes": [
                          (2, "0", "I worked steadily through a backlog of oars and rudders.", None),
                          (1, "+", "The harbor master praised my repair work in front of the other workers.", None),
                          (1, "-", "A customer refused to pay, saying the mast I fixed still leaked.", None)]},
    # --- Lighthouse Point
    "lighthouse.climb": {"place": "lighthouse", "label": "climb to the top of the lighthouse", "solitary": True,
                         "outcomes": [
                             (2, "+", "I climbed the 130 steps to the lamp room and could see the mainland coast.", "fair"),
                             (1, "-", "I was out of breath halfway up the stairs and had to stop twice.", None),
                             (2, "0", "From the lamp room I watched the rain squalls march across the sea.", "bad")]},
    "lighthouse.watch": {"place": "lighthouse", "label": "watch the ships from the point", "solitary": True,
                         "outcomes": [
                             (2, "0", "Two ships passed far out on the horizon while I sat at the base of the lighthouse.", None),
                             (1, "+", "A seal hauled itself onto the rocks below me and stayed there the whole time.", "fair")]},
    "lighthouse.work": {"place": "lighthouse", "label": "keep the lighthouse lamp", "job": "keeper",
                        "outcomes": [
                            (2, "0", "I cleaned the lens and checked the lamp's fuel, right on schedule.", None),
                            (1, "-", "The lamp's motor jammed and the light was dark for ten minutes before I fixed it.", None),
                            (1, "+", "A fishing boat flashed its lights at the lighthouse as it came in -- the fishermen's thank-you.", None)]},
    # --- Ferry Dock
    "dock.fish": {"place": "dock", "label": "fish off the pier",
                  "outcomes": [
                      (3, "-", "I fished off the pier and caught nothing.", None),
                      (2, "+", "I caught two mackerel off the pier.", None),
                      (1, "+", "A big sea bass took my line and I landed it after a long fight; an old fisherman nodded at me.", None),
                      (2, "-", "The waves were slamming the pier; I gave up fishing and went back.", "bad")]},
    "dock.watch": {"place": "dock", "label": "watch the boats and the ferry", "solitary": True,
                   "outcomes": [
                       (2, "0", "I watched the fishing boats come and go from the end of the pier.", None),
                       (1, "0", "A deckhand tossed me a rope to hold while he tied up, then nodded thanks.", None)]},
    "dock.work": {"place": "dock", "label": "work on the boats as a fisher", "job": "fisher",
                  "outcomes": [
                      (2, "+", "My lobster pots came up full this time.", None),
                      (2, "-", "I hauled up every pot and all of them were empty.", None),
                      (1, "-", "The ferry captain yelled at me for coiling a line the wrong way.", None),
                      (1, "0", "I spent the whole stretch mending nets on the pier.", "bad")]},
    # --- Rowing Club
    "rowing_club.weights": {"place": "rowing_club", "label": "train with weights",
                            "outcomes": [
                                (2, "+", "I lifted heavy for the whole session and set a new personal best on the squat.", None),
                                (1, "-", "I strained my lower back on the last set and had to stop.", None),
                                (1, "-", "The coach corrected my form twice in front of the others.", None),
                                (1, "0", "I did a steady session of rows and presses and stretched afterwards.", None)]},
    "rowing_club.row": {"place": "rowing_club", "label": "row along the coast",
                        "outcomes": [
                            (2, "+", "I rowed out along the coast and back with two club members; we kept perfect time.", "fair"),
                            (1, "-", "I caught a crab with my oar and fell off the seat in front of the crew.", "fair"),
                            (2, "-", "The club kept the boats in because of the swell, so I sat in the boathouse.", "bad")]},
    "rowing_club.work": {"place": "rowing_club", "label": "coach the rowers", "job": "coach",
                         "outcomes": [
                             (1, "+", "My crew beat the club record on the harbor sprint.", None),
                             (1, "-", "Two rowers quit halfway through my session, saying it was too hard.", None),
                             (2, "0", "I ran drills on the rowing machines with six members.", None)]},
    # --- Sunset Beach
    "beach.swim": {"place": "beach", "label": "swim in the sea",
                   "outcomes": [
                       (2, "+", "I swam out to the buoy and back; the water was cold and clear.", "fair"),
                       (1, "-", "A jellyfish stung my arm; the sting swelled up red.", "fair"),
                       (2, "-", "The waves were too big to swim; I stood at the waterline and turned back.", "bad")]},
    "beach.walk": {"place": "beach", "label": "walk along the shore", "solitary": True,
                   "outcomes": [
                       (2, "+", "I walked the whole beach and found an old glass fishing float washed up on the sand.", None),
                       (2, "0", "I walked along the shore while the tide came in.", None),
                       (1, "-", "The wind whipped sand into my eyes the whole way along the beach.", "bad")]},
    "beach.bonfire": {"place": "beach", "label": "sit by the beach bonfire", "hours": (19, 23), "social": True,
                      "outcomes": [
                          (2, "+", "Townsfolk sat around the bonfire singing old fishing songs; someone handed me a cup of tea.", "fair"),
                          (1, "0", "I sat by the bonfire and watched the sparks go up.", "fair"),
                          (2, "-", "The bonfire would not stay lit in the wet wind, and most people went home.", "bad")]},
    # --- Windward Cliffs
    "cliffs.sit": {"place": "cliffs", "label": "sit at the cliff edge and look out to sea", "solitary": True,
                   "outcomes": [
                       (2, "0", "I sat in the grass at the cliff edge and watched gulls ride the wind below me.", None),
                       (1, "-", "A gust nearly knocked me off balance at the edge and I stepped back fast.", None)]},
    "cliffs.hike": {"place": "cliffs", "label": "hike the cliff path",
                    "outcomes": [
                        (2, "+", "I hiked the full cliff-path loop; at the top I could see the whole island.", "fair"),
                        (2, "-", "The path was mud; I slipped and fell on my side.", "bad"),
                        (1, "0", "I hiked the cliff path; my legs were shaking at the end.", None)]},
    # --- Greenhouse Gardens
    "greenhouse.tend": {"place": "greenhouse", "label": "tend the plants", "solitary": True,
                        "outcomes": [
                            (2, "+", "I watered and pruned the tomato vines; the first fruits were turning red.", None),
                            (1, "-", "Aphids had covered the young basil; most of the tray was lost.", None),
                            (1, "0", "I repotted twenty seedlings in the warm, damp air of the glasshouse.", None)]},
    "greenhouse.harvest": {"place": "greenhouse", "label": "harvest vegetables",
                           "outcomes": [
                               (2, "+", "I filled two baskets with lettuce and beans.", None),
                               (2, "-", "The wind had broken a glass pane and chilled the beds; half the beans had wilted.", "bad")]},
    "greenhouse.work": {"place": "greenhouse", "label": "work as the island gardener", "job": "gardener",
                        "outcomes": [
                            (1, "+", "The café ordered a weekly crate of herbs from me.", None),
                            (1, "-", "The head gardener told me I had overwatered the seedlings again.", None),
                            (2, "0", "I weeded the outdoor beds alone for the whole stretch.", None)]},
    # --- Home (any resident's own cottage)
    "home.rest": {"place": "home", "label": "rest at home", "solitary": True,
                  "outcomes": [
                      (2, "0", "I lay on my bed at home and listened to the rain on the roof.", "bad"),
                      (2, "0", "I tidied my cottage and made a pot of tea.", None),
                      (1, "0", "I sat at home with the window open and did nothing for a while.", "fair")]},
    "home.cook": {"place": "home", "label": "cook a meal at home", "solitary": True,
                  "outcomes": [
                      (2, "+", "I cooked a fish stew at home and ate it slowly at my table.", None),
                      (1, "-", "I burned the rice and ate dry bread instead.", None)]},
}

# Trades a resident can be given at creation: id -> (title, place, work hours, activity).
JOBS: dict[str, dict] = {
    "none": {"title": "No job (newcomer)", "place": None, "hours": None, "activity": None},
    "baker": {"title": "Baker at the Tidewater Café", "place": "cafe", "hours": (7, 15), "activity": "cafe.work"},
    "vendor": {"title": "Market stall vendor", "place": "market", "hours": (8, 16), "activity": "market.work"},
    "librarian": {"title": "Librarian", "place": "library", "hours": (9, 17), "activity": "library.work"},
    "clerk": {"title": "Town clerk", "place": "town_hall", "hours": (9, 17), "activity": "town_hall.work"},
    "nurse": {"title": "Nurse at the clinic", "place": "clinic", "hours": (8, 16), "activity": "clinic.work"},
    "boatbuilder": {"title": "Boatbuilder", "place": "workshop", "hours": (8, 16), "activity": "workshop.work"},
    "keeper": {"title": "Lighthouse keeper", "place": "lighthouse", "hours": (16, 22), "activity": "lighthouse.work"},
    "fisher": {"title": "Fisher", "place": "dock", "hours": (7, 13), "activity": "dock.work"},
    "coach": {"title": "Rowing coach", "place": "rowing_club", "hours": (10, 18), "activity": "rowing_club.work"},
    "gardener": {"title": "Gardener", "place": "greenhouse", "hours": (7, 15), "activity": "greenhouse.work"},
}

# Cottages sit outside the ring road, mostly in two villages: West Lane (between the beach and
# the cliffs) and a few by the rowing club, the greenhouse and the Town Hall. Listed in birth
# order, alternating sides so small casts look balanced. Layout checked: no cottage, place or
# road overlaps another.
_HOME_SPOTS = [(-4.4, 15.2), (13.6, 7.5), (-8.8, 13.1), (7.2, 13.5), (-12.5, 9.7),
               (-8.7, 18.6), (-7.5, -16.9), (-14.0, 15.0), (-16.3, 2.6), (5.0, -16.3)]
_HOME_SLOTS = len(_HOME_SPOTS)
RING_RADIUS = 12.5           # the ring road around the town centre; every outer place has a spur to it
_INNER = {"plaza", "cafe", "market"}


def home_slot(index: int) -> tuple[float, float]:
    return _HOME_SPOTS[index % _HOME_SLOTS]


_PROGRESSIVE = {
    "sit": "sitting", "play": "playing", "have": "having", "work": "working", "buy": "buying",
    "browse": "browsing", "run": "running", "read": "reading", "study": "studying", "write": "writing",
    "get": "getting", "volunteer": "volunteering", "repair": "repairing", "build": "building",
    "climb": "climbing", "watch": "watching", "keep": "keeping", "fish": "fishing", "train": "training",
    "row": "rowing", "coach": "coaching", "swim": "swimming", "walk": "walking", "hike": "hiking",
    "tend": "tending", "harvest": "harvesting", "rest": "resting", "cook": "cooking",
}


def doing_text(activity_id: str) -> str:
    """'fish off the pier' -> 'fishing off the pier' (third person, for 'X was there too, ...')."""
    verb, _, rest = ACTIVITIES[activity_id]["label"].partition(" ")
    rest = rest.replace("your ", "their ")
    return f"{_PROGRESSIVE.get(verb, verb)} {rest}".strip()


def home_id(char_id: str) -> str:
    return f"home:{char_id}"


def place_name(place: str, homes: dict[str, dict] | None = None) -> str:
    if place.startswith("home:"):
        owner = (homes or {}).get(place, {}).get("owner_name")
        return f"{owner}'s cottage" if owner else "a cottage"
    return LOCATIONS[place]["name"]


def activity_place(activity_id: str, char_id: str) -> str:
    place = ACTIVITIES[activity_id]["place"]
    return home_id(char_id) if place == "home" else place


def available_activities(place: str, job: str | None, hour: int) -> list[str]:
    """Activity ids a resident with ``job`` may do at ``place`` at ``hour``."""
    key = "home" if place.startswith("home:") else place
    out = []
    for act_id, act in ACTIVITIES.items():
        if act["place"] != key:
            continue
        if act.get("job") and act["job"] != job:
            continue
        hours = act.get("hours")
        if hours and not (hours[0] <= hour < hours[1]):
            continue
        out.append(act_id)
    return out


def _ring(angle: float) -> list[float]:
    return [round(RING_RADIUS * math.cos(angle), 2), round(RING_RADIUS * math.sin(angle), 2)]


def _arc(a0: float, a1: float) -> list[list[float]]:
    """Points along the ring road from angle a0 to a1, the short way round."""
    delta = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
    steps = max(1, int(abs(delta) / math.radians(12)))
    return [_ring(a0 + delta * i / steps) for i in range(steps + 1)]


def _key(place: str) -> str:
    return "home" if place.startswith("home:") else place


def path_between(a: tuple[float, float], a_place: str, b: tuple[float, float], b_place: str) -> list[list[float]]:
    """Walk the roads: the town centre (plaza, café, market) is reached through the plaza;
    everything else hangs off the ring road on its own spur."""
    if a == b:
        return [list(a)]
    a_in, b_in = _key(a_place) in _INNER, _key(b_place) in _INNER
    hub = [0.0, 0.0]
    if a_in and b_in:
        return [list(a), list(b)] if HUB in (a_place, b_place) else [list(a), hub, list(b)]
    if a_in or b_in:                                   # centre <-> outside: plaza -> spoke -> ring
        inner_pt, outer_pt = (a, b) if a_in else (b, a)
        inner_place = a_place if a_in else b_place
        angle = math.atan2(outer_pt[1], outer_pt[0])
        spoke = round(angle / (math.pi / 2)) * (math.pi / 2)    # the plaza's four spokes: N/E/S/W
        route = ([list(inner_pt)] + ([] if inner_place == HUB else [hub]) +
                 _arc(spoke, angle) + [list(outer_pt)])
        return route if a_in else route[::-1]
    a_angle, b_angle = math.atan2(a[1], a[0]), math.atan2(b[1], b[0])
    return [list(a)] + _arc(a_angle, b_angle) + [list(b)]


def public_map() -> dict:
    """What the client needs to build the island."""
    return {
        "name": ISLAND_NAME,
        "radius": ISLAND_RADIUS,
        "hub": HUB,
        "locations": [{"id": k, **{f: v[f] for f in ("name", "kind", "x", "z", "outdoors", "blurb")}}
                      for k, v in LOCATIONS.items()],
        "jobs": [{"id": k, "title": v["title"], "place": v["place"],
                  "hours": list(v["hours"]) if v["hours"] else None} for k, v in JOBS.items()],
        "home_slots": [list(home_slot(i)) for i in range(_HOME_SLOTS)],
        "ring_radius": RING_RADIUS,
        "inner": sorted(_INNER),
    }
