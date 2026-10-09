"""All the worlds on this machine: create, open (resume), clone the cast, list, shut down.

Each world lives in ``data/worlds/<id>/``:
    setup.json          how it was made (mind version, modes, seed, pace, the cast's specs)
    world.json          the current state (saved after every beat -- resume any time)
    feed.jsonl          everything that happened, in order
    experiences.jsonl   every experience each resident lived: the facts, the narrated text,
                        their reply, MindForm's appraisal and formation, their state after
    minds/              the mind's own files (MindForm v0's roster, memories, logs)
Several worlds can be open at once (each with its own MindForm process), so two variants of
an experiment can run side by side.
"""
from __future__ import annotations

import io
import json
import logging
import re
import threading
import time
import zipfile
from pathlib import Path

from app import config
from app.minds import versions
from app.world.engine import MAX_RESIDENTS, World
from app.world.places import JOBS

log = logging.getLogger("mindform.world.manager")


class SetupError(ValueError):
    pass


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "world"


def validate_setup(setup: dict) -> dict:
    available = {v["id"]: v for v in versions()}
    version = setup.get("version")
    if version not in available:
        raise SetupError(f"unknown mind version {version!r}")
    if not available[version]["available"]:
        raise SetupError(available[version]["note"])
    characters = setup.get("characters") or []
    if not 1 <= len(characters) <= MAX_RESIDENTS:
        raise SetupError(f"a world needs 1-{MAX_RESIDENTS} residents")
    clean = []
    for i, c in enumerate(characters):
        mode = c.get("mode") if c.get("mode") in ("bio", "manual") else "bio"
        if mode == "bio" and not (c.get("bio") or "").strip():
            raise SetupError(f"resident {i + 1}: write a short biography")
        if mode == "manual" and not ((c.get("identity") or {}).get("name") or "").strip():
            raise SetupError(f"resident {i + 1}: a name is required")
        clean.append({
            "mode": mode, "name": (c.get("name") or "").strip()[:60], "bio": (c.get("bio") or "").strip()[:1200],
            "identity": {k: str(v).strip()[:120] for k, v in (c.get("identity") or {}).items() if v not in (None, "")},
            "levels": {k: int(v) for k, v in (c.get("levels") or {}).items() if str(v).isdigit() and 1 <= int(v) <= 5},
            "job": c.get("job") if c.get("job") in JOBS else "none",
            "goal": (c.get("goal") or "").strip()[:200],
            "color": c.get("color") if re.fullmatch(r"#[0-9a-fA-F]{6}", c.get("color") or "") else None,
        })
    try:
        seed = int(setup.get("seed", 431))
    except (TypeError, ValueError):
        raise SetupError("seed must be a whole number")
    return {
        "name": (setup.get("name") or "").strip()[:60] or "Halcyon Isle",
        "version": version,
        "world_brain": "llm" if setup.get("world_brain") == "llm" else "rules",
        "mind_llm": bool(setup.get("mind_llm")),
        "seed": seed,
        "experiences_per_hour": max(3, min(6, int(setup.get("experiences_per_hour", 4)))),
        "intensity": max(1, min(3, int(setup.get("intensity", 2)))),
        "characters": clean,
        "cloned_from": setup.get("cloned_from"),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


class WorldManager:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else config.worlds_dir()
        self.worlds: dict[str, World] = {}
        self._lock = threading.Lock()

    def _dir(self, wid: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", wid or "") or ".." in wid:
            raise KeyError(wid)
        return self.root / wid

    def _new_id(self, name: str) -> str:
        base = time.strftime("%Y%m%d-%H%M%S") + "-" + _slug(name)
        wid, n = base, 2
        while (self.root / wid).exists():
            wid, n = f"{base}-{n}", n + 1
        return wid

    def create(self, setup: dict, *, background: bool = True) -> World:
        clean = validate_setup(setup)
        with self._lock:
            wid = self._new_id(clean["name"])
            wdir = self.root / wid
            wdir.mkdir(parents=True)
            (wdir / "setup.json").write_text(json.dumps(clean, indent=2, ensure_ascii=False))
            world = World(wid, wdir, clean)
            self.worlds[wid] = world
        if background:
            threading.Thread(target=self._create_and_start, args=(world,), daemon=True).start()
        else:
            self._create_and_start(world)
        return world

    def _create_and_start(self, world: World) -> None:
        world.create_residents(world.setup["characters"])
        world.start_loop()

    def clone(self, wid: str, overrides: dict, *, background: bool = True) -> World:
        """A fresh world with the same cast (re-born from the same specs) -- for A/B runs."""
        try:
            source = json.loads((self._dir(wid) / "setup.json").read_text())
        except (KeyError, OSError):
            raise FileNotFoundError(wid)
        setup = {**source, **{k: v for k, v in overrides.items() if v is not None}, "cloned_from": wid}
        setup["characters"] = source["characters"]
        return self.create(setup, background=background)

    def get(self, wid: str) -> World:
        with self._lock:
            world = self.worlds.get(wid)
            if world:
                return world
            wdir = self._dir(wid)
            if not (wdir / "world.json").exists():
                raise KeyError(wid)
            world = World.load(wdir)
            self.worlds[wid] = world
        if world.status == "ready":
            from app.minds import make_mind
            world.mind = make_mind(world.setup["version"], wdir / "minds", world.mind_llm)
            try:
                world.mind.start()
            except Exception as exc:
                world.status, world.error = "error", f"could not start the mind: {exc}"
        world.start_loop()
        return world

    def list(self) -> list[dict]:
        out = []
        if not self.root.exists():
            return out
        for wdir in sorted(self.root.iterdir(), reverse=True):
            if wdir.name in self.worlds:
                out.append(self.worlds[wdir.name].summary())
                continue
            try:
                setup = json.loads((wdir / "setup.json").read_text())
                state = json.loads((wdir / "world.json").read_text()) if (wdir / "world.json").exists() else {}
            except (OSError, ValueError):
                continue
            from app.minds import version_name
            from app.world.engine import time_text
            out.append({"id": wdir.name, "name": setup.get("name", wdir.name),
                        "time_text": time_text(state.get("clock", 420)), "beat": state.get("beat", 0),
                        "status": state.get("status", "unfinished"),
                        "residents": [r.get("name") for r in state.get("residents", [])],
                        "setup": {k: setup.get(k) for k in ("version", "world_brain", "mind_llm", "seed",
                                                            "experiences_per_hour", "intensity")},
                        "version_name": version_name(setup.get("version", "")), "loaded": False})
        return out

    def export_zip(self, wid: str) -> bytes:
        wdir = self._dir(wid)
        if not wdir.is_dir():
            raise KeyError(wid)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(wdir.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    zf.write(path, f"{wid}/{path.relative_to(wdir)}")
        return buffer.getvalue()

    def close(self, wid: str) -> None:
        with self._lock:
            world = self.worlds.pop(wid, None)
        if world:
            world.shutdown()
            world.save()

    def shutdown(self) -> None:
        with self._lock:
            worlds = list(self.worlds.values())
            self.worlds.clear()
        for world in worlds:
            try:
                world.shutdown()
                if world.status == "ready":
                    world.save()
            except Exception:
                log.exception("shutdown of %s failed", world.id)
