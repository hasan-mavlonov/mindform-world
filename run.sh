#!/bin/bash
# MindForm World on http://127.0.0.1:8090 (PORT=... to change it).
cd "$(dirname "$0")"
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8090}" "$@"
