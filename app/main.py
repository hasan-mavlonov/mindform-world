from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.simulation import world

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="MindForm World", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/state")
def state():
    with world.lock:
        return world.snapshot()


@app.post("/api/step")
def step(n: int = 1):
    if not 1 <= n <= 100:
        raise HTTPException(400, detail="n must be 1-100")
    return world.step(n)


@app.post("/api/reset")
def reset():
    return world.reset()


@app.get("/api/health")
def health():
    return {"ok": True, "tick": world.tick_count}
