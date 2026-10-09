"""Control residents: no MindForm inside -- a fixed persona that experiences never change.

The experimental baseline (does MindForm make residents behave differently from a plain
persona?) and the mind the test-suite runs on, since MindForm itself is a private repo.
State is born from the same creation form (sliders -> fixed traits) and then never moves.
Each experience is read with a small word list (pleasant / unpleasant / threatening / about
people) -- a reading, not formation: nothing it reads ever changes who they are. In LLM mode
the world's own model voices a reply from the static persona; offline the reply is a short
fixed line for that reading.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from app import llm
from app.minds.base import Created, MindError, Turn, blank_state

VERSION = "control"
_LEVELS = {1: -0.8, 2: -0.4, 3: 0.0, 4: 0.4, 5: 0.8}
_POLES = {
    "O": ("Openness", "practical, conventional", "curious, imaginative"),
    "C": ("Conscientiousness", "spontaneous, easygoing", "disciplined, organized"),
    "E": ("Extraversion", "reserved, private", "outgoing, energetic"),
    "A": ("Agreeableness", "blunt, competitive", "warm, cooperative"),
    "N": ("Neuroticism", "calm, resilient", "sensitive, easily stressed"),
}
_REPLY_SYSTEM = """You voice a fictional island resident. You are given who they are (fixed --
they never change) and something that just happened to them. Answer with what they say or
think out loud, one or two short spoken sentences, first person.
Return JSON only: {"reply": "..."}"""


_POS = {"laughed", "clapped", "praised", "thanked", "smiled", "hugged", "won", "best", "free", "found", "caught",
        "festival", "music", "lanterns", "dancing", "safe", "kind", "friend", "hello", "welcome", "glad", "great",
        "lovely", "good", "beautiful", "sold", "record", "proud", "thank", "thanks", "love", "nice", "fun", "happy"}
_NEG = {"fire", "smoke", "storm", "cancelled", "lost", "missing", "argued", "shouted", "complained", "refused",
        "broke", "broken", "burned", "nothing", "empty", "alone", "rumor", "letter", "stranger", "fell", "cut",
        "stung", "slipped", "mistake", "wrong", "cold", "late", "angry", "sad", "bad", "worse", "sorry", "lying",
        "secret", "hiding", "walked", "without"}
_THREAT = {"fire", "storm", "stranger", "missing", "gale", "smoke", "rumor", "anonymous", "secret", "exposed"}
_SOCIAL = {"said", "talked", "asked", "answered", "told", "friend", "together", "everyone", "people"}
_REPLIES = {
    "pos": ["That was a good one.", "Nice. I'll take that.", "Well, that went alright."],
    "neg": ["That wasn't great.", "Not my favourite moment.", "I'd rather that hadn't happened."],
    "neutral": ["Hm. Alright.", "Fair enough.", "Okay then."],
}


def _read(text: str) -> dict:
    """A word-list appraisal in MindForm's shape (valence, intensity, threat, social)."""
    words = set(re.findall(r"[a-z']+", text.lower()))
    pos, neg = len(words & _POS), len(words & _NEG)
    threat, social = len(words & _THREAT), len(words & _SOCIAL)
    total = pos + neg
    valence = 0.0 if not total else (pos - neg) / (total + 1.0)
    return {"valence": round(valence, 3), "intensity": round(min(1.0, 0.25 + 0.15 * total + 0.2 * threat), 3),
            "threat_challenge": round(-min(1.0, 0.35 * threat), 3) if threat and valence < 0 else 0.0,
            "social": round(min(1.0, 0.2 * social), 3), "novelty": 0.3, "agency": 0.0, "outcome": 0.0}


def _plain(appraisal: dict, text: str) -> str:
    v = appraisal["valence"]
    family = "pos" if v >= 0.15 else "neg" if v <= -0.15 else "neutral"
    pool = _REPLIES[family]
    return pool[sum(map(ord, text)) % len(pool)]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "resident"


def _static_state(levels: dict) -> dict:
    state = blank_state()
    for key, (name, low, high) in _POLES.items():
        try:
            value = _LEVELS.get(int(levels.get(key, 3)), 0.0)
        except (TypeError, ValueError):
            value = 0.0
        state["traits"].append({"key": key, "name": name, "value": value, "base": value,
                                "low": low, "high": high, "glyph": high if value >= 0 else low})
    top = max(state["traits"], key=lambda t: abs(t["value"]))
    state["dominant"] = {"key": top["key"], "name": top["name"], "glyph": top["glyph"],
                         "value": top["value"], "dir": 1 if top["value"] >= 0 else -1}
    state["sources"] = {"appraisal": None, "push": "none", "reply": None}
    return state


class ControlMind:
    version = VERSION

    def __init__(self, workdir: Path, use_llm: bool):
        self.workdir = Path(workdir)
        self.use_llm = use_llm
        self.stats = llm.Stats()
        self._lock = threading.Lock()
        self._residents: dict[str, dict] = {}

    @property
    def _file(self) -> Path:
        return self.workdir / "control_residents.json"

    def start(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        if self._file.exists():
            self._residents = json.loads(self._file.read_text())

    def stop(self) -> None:
        pass

    def _save(self) -> None:
        self._file.write_text(json.dumps(self._residents, indent=2))

    def create(self, spec: dict) -> Created:
        if spec.get("mode") == "manual":
            identity = {k: v for k, v in (spec.get("identity") or {}).items() if v not in (None, "")}
            levels = spec.get("levels") or {}
        else:
            bio = (spec.get("bio") or "").strip()
            if not bio:
                raise MindError("a biography is required")
            identity = {"name": re.split(r"[,.;:]", bio, maxsplit=1)[0].strip()[:40], "bio": bio}
            levels = {}
        name = (identity.get("name") or "").strip()
        if not name:
            raise MindError("a name is required")
        with self._lock:
            ref, n = _slug(name), 2
            while ref in self._residents:
                ref, n = f"{_slug(name)}-{n}", n + 1
            state = _static_state(levels)
            self._residents[ref] = {"identity": identity, "state": state, "turn": 0}
            self._save()
        return Created(ref=ref, name=name, identity=identity, state=state, via="control")

    def experience(self, ref: str, text: str) -> Turn:
        with self._lock:
            resident = self._residents.get(ref)
            if resident is None:
                raise MindError(f"unknown resident {ref}")
            resident["turn"] += 1
            resident["state"]["turn"] = resident["turn"]
            self._save()
            state = json.loads(json.dumps(resident["state"]))
        appraisal = _read(text)
        reply = None
        if self.use_llm and llm.available():
            persona = json.dumps(resident["identity"], ensure_ascii=False)
            try:
                reply = str(llm.complete_json(_REPLY_SYSTEM, f"Who they are: {persona}\n\nWhat happened: {text}",
                                              stats=self.stats).get("reply") or "").strip() or None
            except Exception:
                reply = None
            state["sources"] = {**state.get("sources", {}), "reply": "llm" if reply else "rules"}
        if reply is None:
            reply = _plain(appraisal, text)
        return Turn(state=state, reply=reply, appraisal=appraisal)

    def state(self, ref: str) -> dict:
        resident = self._residents.get(ref)
        if resident is None:
            raise MindError(f"unknown resident {ref}")
        return {"name": resident["identity"].get("name"), "identity": resident["identity"],
                "turn": resident["turn"], "control": True, "state": resident["state"]}
