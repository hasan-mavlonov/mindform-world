#!/bin/bash
cd "$(dirname "$0")"
./setup.sh --if-missing      # installs only what is missing (first run: MindForm v0's memory packages)
exec .venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8090
