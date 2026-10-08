"""Deterministic simulation backend; designed to replace decision rules with MindForm inference."""
from __future__ import annotations

import math
import random
import threading
from dataclasses import asdict, dataclass, field

SEED = 431
LOCATIONS = {
    "plaza": (0.0, 0.0),
    "library": (-8.5, -4.5),
    "workshop": (8.5, -4.5),
    "garden": (-8.5, 5.5),
    "lakeside": (8.0, 6.0),
    "campfire": (0.0, 8.0),
}


@dataclass
class Agent:
    id: str
    name: str
    color: str
    role: str
    personality: str
    traits: dict[str, float]
    x: float
    z: float
    destination: str = "plaza"
    mood: str = "curious"
    activity: str = "Exploring the world"
    memories: list[str] = field(default_factory=list)
    relationships: dict[str, int] = field(default_factory=dict)
    completed_visits: int = 0

    def public(self) -> dict:
        return asdict(self)


class World:
    def __init__(self):
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        with self.lock:
            self.rng = random.Random(SEED)
            self.tick_count = 0
            self.events: list[dict] = []
            self.agents = [
                Agent("aya", "Aya", "#e9abfb", "Mediator", "Warm, collaborative, socially attentive", {"openness": 0.8, "agreeableness": 0.96, "assertiveness": 0.3}, -2.5, 1.0, "garden"),
                Agent("rex", "Rex", "#ffb565", "Builder", "Bold, independent, ambitious", {"openness": 0.74, "agreeableness": 0.31, "assertiveness": 0.95}, 2.0, -1.0, "workshop"),
                Agent("mira", "Mira", "#84cffa", "Researcher", "Thoughtful, analytical, observant", {"openness": 0.93, "agreeableness": 0.62, "assertiveness": 0.38}, -4.0, -2.0, "library"),
                Agent("leo", "Leo", "#acf2a9", "Explorer", "Curious, energetic, risk-taking", {"openness": 0.99, "agreeableness": 0.71, "assertiveness": 0.68}, 4.0, 2.0, "lakeside"),
                Agent("nova", "Nova", "#ffd0db", "Strategist", "Pragmatic, strategic, socially cautious", {"openness": 0.65, "agreeableness": 0.42, "assertiveness": 0.75}, 0.0, 4.0, "campfire"),
            ]
            for a in self.agents:
                a.relationships = {other.id: 50 for other in self.agents if other.id != a.id}
            self._log("world", "Five agents entered the world. Their personalities will influence where they go.")
            return self.snapshot()

    def _log(self, actor: str, message: str):
        self.events.append({"tick": self.tick_count, "actor": actor, "message": message})
        self.events = self.events[-80:]

    def _choose_destination(self, agent: Agent):
        weights = {place: 1.0 for place in LOCATIONS}
        if agent.id == "aya":
            weights.update(plaza=5.0, garden=4.0, campfire=5.0)
        elif agent.id == "rex":
            weights.update(workshop=7.0, plaza=3.0)
        elif agent.id == "mira":
            weights.update(library=7.0, lakeside=3.0)
        elif agent.id == "leo":
            weights.update(lakeside=6.0, garden=5.0)
        else:
            weights.update(plaza=5.0, library=3.0, campfire=4.0)
        weights[agent.destination] = 0.0
        agent.destination = self.rng.choices(list(weights), weights=list(weights.values()))[0]
        agent.activity = f"Walking to the {agent.destination}"

    def _arrive(self, agent: Agent):
        descriptions = {
            "plaza": "joined the community square",
            "library": "studied old records in the library",
            "workshop": "examined a mechanism in the workshop",
            "garden": "tended plants in the garden",
            "lakeside": "observed the water by the lake",
            "campfire": "spent time reflecting at the campfire",
        }
        action = descriptions[agent.destination]
        agent.completed_visits += 1
        agent.memories.append(f"Tick {self.tick_count}: {action}.")
        agent.memories = agent.memories[-20:]
        agent.activity = action.capitalize()
        agent.mood = self.rng.choice(["curious", "focused", "hopeful", "calm", "excited"])
        self._log(agent.id, f"{agent.name} {action}.")
        for peer in self.agents:
            if peer.id == agent.id or math.dist((agent.x, agent.z), (peer.x, peer.z)) >= 3.4:
                continue
            other = peer.name
            delta = 2 if agent.traits["agreeableness"] > 0.6 else -1
            agent.relationships[peer.id] = max(0, min(100, agent.relationships[peer.id] + delta))
            peer.relationships[agent.id] = max(0, min(100, peer.relationships[agent.id] + delta))
            memory = f"Tick {self.tick_count}: crossed paths with {other}."
            agent.memories.append(memory)
            agent.memories = agent.memories[-20:]
            self._log(agent.id, f"{agent.name} met {other} near the {agent.destination}.")
        self._choose_destination(agent)

    def step(self, n: int = 1):
        with self.lock:
            for _ in range(n):
                self.tick_count += 1
                for a in self.agents:
                    tx, tz = LOCATIONS[a.destination]
                    dx, dz = tx - a.x, tz - a.z
                    dist = math.hypot(dx, dz)
                    speed = 0.55 + 0.19 * a.traits["openness"]
                    if dist <= speed:
                        a.x, a.z = tx, tz
                        self._arrive(a)
                    else:
                        a.x += dx / dist * speed
                        a.z += dz / dist * speed
            return self.snapshot()

    def snapshot(self):
        return {
            "tick": self.tick_count,
            "locations": {k: {"x": x, "z": z} for k, (x, z) in LOCATIONS.items()},
            "agents": [a.public() for a in self.agents],
            "events": self.events[-35:],
            "mode": "deterministic",
        }


world = World()
