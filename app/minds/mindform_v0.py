"""MindForm v0, driven through its own cockpit API -- the world never imports v0's code.

Each world launches its OWN v0 server process (``console.py``) with the working directory
set to the world's folder. v0 keeps every file relative to its working directory
(``data/characters/*.json``, memories, the appraisal log, ``.env``), so this gives each
world an isolated roster for free -- your console's characters are never touched -- and it
lets two worlds run v0 in different modes side by side:

    LLM mode      v0 gets its LLM key (from v0's own .env, or the environment)
    offline mode  the key variables are blanked, so every v0 faculty takes its
                  deterministic fallback (lexicon / trained head, rule-based reply)

``PYTHONHASHSEED`` is pinned so v0's offline reply (which varies its wording by
``hash(text)``) is reproducible run to run.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from app.config import parse_dotenv, v0_path, v0_python
from app.minds.base import Created, MindError, Turn, summarize_v0

log = logging.getLogger("mindform.world.v0")

VERSION = "mindform_v0"
_KEY_VARS = ("LLM_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY")
_STARTUP_TIMEOUT = 60.0
_TURN_TIMEOUT = 300.0          # one v0 turn = several sequential LLM calls in LLM mode

# Used when v0's config cannot be read (should not happen with a real checkout).
_FALLBACK_SCHEMA = {
    "identity_fields": [
        {"key": "name", "label": "Name"}, {"key": "age", "label": "Age"},
        {"key": "gender", "label": "Gender"}, {"key": "origin", "label": "Where from (city / country)"},
        {"key": "culture", "label": "Culture / ethnicity"}, {"key": "language", "label": "Native language"},
        {"key": "religion", "label": "Religion raised in"}, {"key": "family", "label": "Family background"},
    ],
    "trait_questions": [
        {"key": "O", "name": "Openness", "low": "practical, conventional", "high": "curious, imaginative"},
        {"key": "C", "name": "Conscientiousness", "low": "spontaneous, easygoing", "high": "disciplined, organized"},
        {"key": "E", "name": "Extraversion", "low": "reserved, private", "high": "outgoing, energetic"},
        {"key": "A", "name": "Agreeableness", "low": "blunt, competitive", "high": "warm, cooperative"},
        {"key": "N", "name": "Neuroticism", "low": "calm, resilient", "high": "sensitive, easily stressed"},
    ],
}
_schema_cache: dict | None = None

_SCHEMA_SCRIPT = (
    "import json, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from core.config import IDENTITY_FIELDS, TRAIT_QUESTIONS\n"
    "print(json.dumps({'identity_fields': [{'key': k, 'label': l} for k, l in IDENTITY_FIELDS],\n"
    "                  'trait_questions': [{'key': k, 'name': n, 'low': lo, 'high': hi}\n"
    "                                      for k, n, lo, hi in TRAIT_QUESTIONS]}))\n"
)


def available() -> tuple[bool, str]:
    path = v0_path()
    if path is None:
        return False, ("MindForm v0 checkout not found. Set MINDFORM_V0_PATH in .env "
                       "(or put mindform_v0 next to this repo).")
    return True, str(path)


def creation_schema() -> dict:
    """v0's own creation form (identity fields + the five trait questions), read from its
    config in a throwaway process so the world's form can never drift from v0's."""
    global _schema_cache
    if _schema_cache is not None:
        return _schema_cache
    path = v0_path()
    if path is None:
        return _FALLBACK_SCHEMA
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run([v0_python(path), "-c", _SCHEMA_SCRIPT, str(path)], cwd=tmp,
                                 capture_output=True, text=True, timeout=30, env=_base_env(path, False))
        _schema_cache = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception as exc:
        log.warning("could not read v0 creation schema (%s); using the built-in copy", exc)
        _schema_cache = _FALLBACK_SCHEMA
    return _schema_cache


def _base_env(path: Path, use_llm: bool) -> dict[str, str]:
    env = dict(os.environ)
    for key, value in parse_dotenv(path / ".env").items():   # v0's own settings; real env wins
        if not env.get(key):
            env[key] = value
    if not use_llm:
        for key in _KEY_VARS:
            env[key] = ""               # v0 treats empty as unset -> deterministic fallbacks
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("PORT", None)
    return env


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MindFormV0:
    """One v0 server process per world (see module docstring)."""

    version = VERSION

    def __init__(self, workdir: Path, use_llm: bool):
        path = v0_path()
        if path is None:
            raise MindError(available()[1])
        self.path = path
        self.workdir = Path(workdir)
        self.use_llm = use_llm
        self.proc: subprocess.Popen | None = None
        self.base_url = ""
        self._client: httpx.Client | None = None

    # ---- lifecycle ---------------------------------------------------------------
    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        self.workdir.mkdir(parents=True, exist_ok=True)
        head = self.path / "appraisal_head.pth"            # a trained offline appraiser, if any
        link = self.workdir / "appraisal_head.pth"
        if head.exists() and not link.exists():
            try:
                link.symlink_to(head)
            except OSError:
                pass
        port = _free_port()
        self.base_url = f"http://127.0.0.1:{port}"
        logfile = open(self.workdir / "mindform_v0.log", "a")
        self.proc = subprocess.Popen(
            [v0_python(self.path), str(self.path / "console.py"), "--port", str(port), "--host", "127.0.0.1"],
            cwd=self.workdir, env=_base_env(self.path, self.use_llm),
            stdout=logfile, stderr=subprocess.STDOUT,
        )
        logfile.close()
        self._client = httpx.Client(base_url=self.base_url, timeout=_TURN_TIMEOUT)
        deadline = time.monotonic() + _STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise MindError("MindForm v0 exited on startup:\n" + self._log_tail())
            try:
                if self._client.get("/api/config", timeout=2.0).status_code == 200:
                    log.info("MindForm v0 (%s) up at %s for %s",
                             "LLM" if self.use_llm else "offline", self.base_url, self.workdir)
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        self.stop()
        raise MindError("MindForm v0 did not start in time:\n" + self._log_tail())

    def stop(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def _log_tail(self, n: int = 25) -> str:
        try:
            return "\n".join((self.workdir / "mindform_v0.log").read_text().splitlines()[-n:])
        except OSError:
            return "(no log)"

    def _call(self, method: str, url: str, **kwargs) -> dict:
        if not self.alive():
            self.start()
        assert self._client is not None
        try:
            response = self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise MindError(f"MindForm v0 unreachable: {exc}") from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            raise MindError(f"MindForm v0 {url}: {data.get('error') or response.status_code}")
        return data

    # ---- the contract --------------------------------------------------------------
    def create(self, spec: dict) -> Created:
        """``spec`` = {"mode": "bio", "bio": "..."} or
                      {"mode": "manual", "identity": {...}, "levels": {"O": 1-5, ...}}."""
        if spec.get("mode") == "manual":
            identity = {k: v for k, v in (spec.get("identity") or {}).items() if v not in (None, "")}
            if not (identity.get("name") or "").strip():
                raise MindError("a name is required")
            snap = self._call("POST", "/api/create/manual",
                              json={"identity": identity, "levels": spec.get("levels") or {}})
        else:
            bio = (spec.get("bio") or "").strip()
            if not bio:
                raise MindError("a biography is required")
            snap = self._call("POST", "/api/create/genesis", json={"bio": bio})
        return Created(ref=snap["name"], name=snap["name"], identity=snap.get("identity") or {},
                       state=summarize_v0(snap), raw=snap, via=snap.get("created_via"))

    def experience(self, ref: str, text: str) -> Turn:
        snap = self._call("POST", "/api/turn", json={"name": ref, "message": text})
        return Turn(state=summarize_v0(snap), reply=snap.get("reply"), appraisal=snap.get("appraisal"),
                    formation=snap.get("formation"), raw=snap)

    def state(self, ref: str) -> dict:
        return self._call("GET", "/api/state", params={"name": ref})
