#!/bin/bash
# One-time setup, safe to re-run:
#   1. this repo's .venv with the world's requirements;
#   2. what MindForm v0 needs at runtime (its LLM client, torch, sentence-transformers for
#      memory recall) in the Python that runs v0 -- see requirements-v0.txt.
# ./run.sh calls "./setup.sh --if-missing" on every start: it only installs what is missing.
cd "$(dirname "$0")"
MODE="${1:-}"
say() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }

if [ ! -x .venv/bin/python ]; then
  say "Creating .venv for MindForm World..."
  python3 -m venv .venv || { warn "Could not create .venv (is python3 installed?)"; exit 1; }
fi
if [ "$MODE" != "--if-missing" ] || ! .venv/bin/python -c "import fastapi, uvicorn, httpx" 2>/dev/null; then
  say "Installing MindForm World's requirements..."
  .venv/bin/python -m pip install -q --upgrade pip >/dev/null 2>&1
  .venv/bin/python -m pip install -q -r requirements.txt || warn "pip install -r requirements.txt failed"
fi

# The Python that runs MindForm v0 (same rule as app/config.py).
V0=$(.venv/bin/python -c "from app.config import v0_path, v0_python; p = v0_path(); print(v0_python(p) if p else '')" 2>/dev/null)
if [ -z "$V0" ]; then
  warn "MindForm v0 checkout not found (set MINDFORM_V0_PATH in .env). Skipping its packages."
  exit 0
fi
MISSING=$("$V0" -c "
import importlib.util
need = {'openai': 'openai', 'numpy': 'numpy', 'torch': 'torch', 'sentence_transformers': 'sentence-transformers'}
print(' '.join(pip for mod, pip in need.items() if importlib.util.find_spec(mod) is None))" 2>/dev/null)
if [ -z "$MISSING" ]; then
  [ "$MODE" = "--if-missing" ] || say "MindForm v0's Python ($V0) has everything it needs."
  exit 0
fi

say "MindForm v0 runs on: $V0"
say "Installing what it is missing: $MISSING  (torch is large; the first time takes a few minutes)"
"$V0" -m pip --version >/dev/null 2>&1 || "$V0" -m ensurepip --upgrade >/dev/null 2>&1
TORCH_INDEX=()
if [ "$(uname)" = "Linux" ] && ! command -v nvidia-smi >/dev/null 2>&1; then
  TORCH_INDEX=(--extra-index-url https://download.pytorch.org/whl/cpu)   # no GPU: the small CPU build
fi
if ! "$V0" -m pip install -q "${TORCH_INDEX[@]}" -r requirements-v0.txt; then
  warn "Installing MindForm v0's packages failed. Memory recall stays off and LLM mode won't start"
  warn "until they are installed. Try:  $V0 -m pip install -r requirements-v0.txt"
  exit 0
fi
say "Downloading the memory encoder (all-MiniLM-L6-v2, ~90 MB, once)..."
"$V0" -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')" >/dev/null 2>&1 \
  || warn "Could not download the encoder now; MindForm will fetch it on first use."
say "MindForm v0 is ready: LLM client + memory recall."
