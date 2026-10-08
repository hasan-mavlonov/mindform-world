# MindForm World 0.1 — living 3D agent simulation

**Visual demo first.** A Three.js WebGL town with five independently moving characters, clickable agent details, live event feed, camera controls, simulation speed controls and a deterministic Python decision engine. Uses Mac mini's GPU for browser WebGL graphics. **Not connected to MindForm's actual personality API yet.**

## Run on Mac (PyCharm terminal)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8080
```

Visit http://127.0.0.1:8080.

The frontend uses **Three.js 0.180.0 from jsDelivr CDN**, so the browser needs network access to the CDN. If the page loads but 3D is blank, inspect browser Developer Tools → Console and check whether `cdn.jsdelivr.net` is reachable; Three.js can be bundled locally in a later iteration.

## APIs

- `GET /api/state` — current agents, traits, destination, positions, log
- `POST /api/step?n=1` — advance simulation by n ticks (1–100)
- `POST /api/reset` — reset to reproducible initial state
- `GET /api/health` — health

## Test

```bash
pytest -q
```

## Planned extensions

- Connect actual MindForm personality API and model inference (instead of deterministic placeholder rules).
- Persist snapshots in SQLite and restore after restarts.
- Scenario runner with fixed random seeds, baseline/control model comparison, independent scoring.
- Pathfinding around buildings and richer location interactions.
- Add glTF avatars and animated objects, better shading, sound and more detailed town.
- Remote inference so the Mac handles hosting and visuals while larger models run on GPU machines.

## Design notes

The browser GPU renders all objects and interpolates the motion between discrete backend simulation steps. `app/simulation.py` is the authoritative source of agents' positions. Never encode hidden MindForm API keys in frontend JavaScript.
