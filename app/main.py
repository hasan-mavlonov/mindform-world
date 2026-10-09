"""MindForm World -- HTTP API + the single-page client.

    GET  /api/status                       world LLM availability, mind versions, the island map
    GET  /api/worlds                       saved worlds (newest first)
    POST /api/worlds                       create a world from a setup (cast is born in the background)
    GET  /api/worlds/{id}/state?since=N    poll: clock, weather, residents, events, feed after seq N
    POST /api/worlds/{id}/run              {"running": bool}
    POST /api/worlds/{id}/step             one beat while paused
    POST /api/worlds/{id}/speed            {"speed": 0.5 | 1 | 2 | 4 | 0 (as fast as possible)}
    POST /api/worlds/{id}/inject           god mode: {"kind": "whisper"|"event", "text", "target", "place", "minutes"}
    GET  /api/worlds/{id}/residents/{rid}  one resident in full (incl. the mind's raw snapshot)
    GET  /api/worlds/{id}/experiences      the experience log (optionally ?resident=)
    POST /api/worlds/{id}/clone            same cast, new world: {"seed", "world_brain", "mind_llm", "name"}
    POST /api/worlds/{id}/close            stop its mind process and unload it
    GET  /api/worlds/{id}/export           the whole world folder as a zip
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app import llm
from app.minds import versions
from app.world.manager import SetupError, WorldManager
from app.world.places import public_map

ROOT = Path(__file__).resolve().parent.parent
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

manager = WorldManager()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    manager.shutdown()


app = FastAPI(title="MindForm World", version="0.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

@app.middleware("http")
async def prevent_stale_frontend(request, call_next):
    """Keep a locally developed SPA from mixing old frontend files with a new API."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


def _world(wid: str):
    try:
        return manager.get(wid)
    except KeyError:
        raise HTTPException(404, detail="world not found")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "open_worlds": len(manager.worlds)}


@app.get("/api/status")
def status():
    return {"llm": {"available": llm.available(), "model": llm.model_label()},
            "versions": versions(), "map": public_map()}


@app.get("/api/worlds")
def list_worlds():
    return {"worlds": manager.list()}


@app.post("/api/worlds")
def create_world(setup: dict = Body(...)):
    try:
        world = manager.create(setup)
    except SetupError as exc:
        raise HTTPException(400, detail=str(exc))
    return {"id": world.id}


@app.get("/api/worlds/{wid}/state")
def world_state(wid: str, since: int = 0):
    return _world(wid).public_state(since)


@app.post("/api/worlds/{wid}/run")
def run(wid: str, body: dict = Body(...)):
    world = _world(wid)
    world.set_running(bool(body.get("running")))
    return {"running": world.running}


@app.post("/api/worlds/{wid}/step")
def step(wid: str):
    world = _world(wid)
    if not world.step():
        raise HTTPException(409, detail="busy, running, or not ready")
    return {"ok": True}


@app.post("/api/worlds/{wid}/speed")
def speed(wid: str, body: dict = Body(...)):
    world = _world(wid)
    try:
        value = float(body.get("speed", 1))
    except (TypeError, ValueError):
        raise HTTPException(400, detail="speed must be a number")
    if value not in (0, 0.5, 1, 2, 4):
        raise HTTPException(400, detail="speed must be 0, 0.5, 1, 2 or 4")
    world.speed = value
    return {"speed": world.speed}


@app.post("/api/worlds/{wid}/inject")
def inject(wid: str, body: dict = Body(...)):
    world = _world(wid)
    try:
        return world.inject(body.get("kind", "event"), body.get("text", ""), target=body.get("target"),
                            place=body.get("place") or None, minutes=int(body.get("minutes") or 120),
                            title=body.get("title"))
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))


@app.get("/api/worlds/{wid}/residents/{rid}")
def resident(wid: str, rid: str):
    world = _world(wid)
    r = world.residents.get(rid)
    if r is None:
        raise HTTPException(404, detail="resident not found")
    raw = None
    if world.mind is not None:
        try:
            raw = world.mind.state(r.ref)
        except Exception as exc:
            raw = {"error": str(exc)}
    with world.lock:
        return {**r.public(world), "mind": raw}


@app.get("/api/worlds/{wid}/experiences")
def experiences(wid: str, resident: str | None = None, limit: int = 200):
    world = _world(wid)
    path = world.dir / "experiences.jsonl"
    if not path.exists():
        return {"experiences": []}
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if resident:
        rows = [r for r in rows if r.get("resident") == resident]
    return {"experiences": rows[-max(1, min(limit, 2000)):]}


@app.post("/api/worlds/{wid}/clone")
def clone(wid: str, body: dict = Body(default={})):
    try:
        world = manager.clone(wid, {k: body.get(k) for k in ("name", "seed", "world_brain", "mind_llm",
                                                             "experiences_per_hour", "intensity")})
    except FileNotFoundError:
        raise HTTPException(404, detail="world not found")
    except SetupError as exc:
        raise HTTPException(400, detail=str(exc))
    return {"id": world.id}


@app.post("/api/worlds/{wid}/close")
def close(wid: str):
    manager.close(wid)
    return {"ok": True}


@app.get("/api/worlds/{wid}/export")
def export(wid: str):
    try:
        data = manager.export_zip(wid)
    except KeyError:
        raise HTTPException(404, detail="world not found")
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{wid}.zip"'})
