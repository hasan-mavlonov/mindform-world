"""MindForm v0, driven through its own cockpit API -- the world never imports v0's code.

Each world launches exactly ONE v0 server process (v0's console, started through
``v0_boot.py`` so its memory encoder is loaded on the CPU and warm before it accepts a turn),
shared by every resident, with the working directory set to the world's folder. v0 keeps every file relative to its working directory
(``data/characters/*.json``, memories, the appraisal log, ``.env``), so this gives each
world an isolated roster for free -- your console's characters are never touched -- and it
lets two worlds run v0 in different modes side by side:

    LLM mode      v0 gets its LLM key (from v0's own .env, or the environment)
    offline mode  the key variables are blanked, so every v0 faculty takes its
                  deterministic fallback (lexicon / trained head, rule-based reply)

``PYTHONHASHSEED`` is pinned so v0's offline reply (which varies its wording by
``hash(text)``) is reproducible run to run.

The API key: v0 reads ``LLM_API_KEY`` / ``GEMINI_API_KEY`` from its environment (or from a
``.env`` in its working directory -- which is the world's folder, so v0's own ``.env`` is
read here and passed on). If neither v0's ``.env`` nor the environment has one, the world's
own key (this repo's ``.env``) is passed, so one key in ``mindform-world/.env`` powers both.
An LLM-mode world refuses to start when no key, or no ``openai`` package, would reach v0 --
instead of quietly running on v0's heuristics.

What v0 needs to run fully (memory recall needs ``sentence-transformers``) is checked in the
Python that runs it; ``./setup.sh`` installs it (see requirements-v0.txt).
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import random
import re
import signal
import socket
import subprocess
import sys
import threading
import tempfile
import time
from pathlib import Path

import httpx

from app import config
from app.config import parse_dotenv, v0_path, v0_python
from app.minds.base import Created, MindError, Turn, summarize_v0

log = logging.getLogger("mindform.world.v0")

VERSION = "mindform_v0"
_KEY_VARS = ("LLM_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY")
_BOOT = Path(__file__).with_name("v0_boot.py")   # starts v0's console warm and ready (see that file)
_STARTUP_TIMEOUT = 240.0       # torch import + encoder warm-up (+ a first download) before it listens
# How long one call may take before it counts as stuck. A v0 turn in LLM mode is ~5 sequential
# model calls (each a few seconds); offline it is local math plus the encoder.
_TIMEOUTS = {"turn_llm": 120.0, "turn": 45.0, "create_llm": 120.0, "create": 30.0, "read": 15.0}
_RETRY_DELAYS = (1.0, 3.0)      # backoff between attempts (two retries), with jitter
_SLOW_GRACE = 30.0              # a slow (not stuck) turn: how long to wait for it to land after the timeout
_START_BACKOFF = 20.0           # after a failed start, the other residents' turns fail fast instead of booting it again

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
    if use_llm and not any(env.get(k) for k in _KEY_VARS):
        cfg = config.llm_settings()                           # the world's key (mindform-world/.env)
        if cfg["api_key"]:
            env["LLM_API_KEY"] = cfg["api_key"]
            env.setdefault("LLM_BASE_URL", cfg["base_url"] or "")
            env.setdefault("LLM_MODEL", cfg["model"] or "")
    if not use_llm:
        for key in _KEY_VARS:
            env[key] = ""               # v0 treats empty as unset -> deterministic fallbacks
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("PORT", None)
    return env


# What v0 imports at runtime, and what each one is for.
_RUNTIME = {"openai": "its LLM calls", "numpy": "its memory", "torch": "its trained appraiser and memory",
            "sentence_transformers": "memory recall (similar past experiences)"}
_PROBE_SCRIPT = (
    "import importlib.util, json\n"
    f"print(json.dumps({{m: importlib.util.find_spec(m) is not None for m in {sorted(_RUNTIME)!r}}}))\n"
)
_probe_cache: dict[str, tuple[float, dict]] = {}


def runtime_check(path: Path | None = None) -> dict:
    """Which of v0's runtime packages the Python that runs it has (cached for a minute)."""
    path = path or v0_path()
    if path is None:
        return {}
    python = v0_python(path)
    hit = _probe_cache.get(python)
    if hit and time.monotonic() - hit[0] < 60:
        return hit[1]
    try:
        out = subprocess.run([python, "-c", _PROBE_SCRIPT], capture_output=True, text=True, timeout=30)
        found = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception as exc:
        log.warning("could not check MindForm v0's Python (%s): %s", python, exc)
        found = {m: importlib.util.find_spec(m) is not None for m in _RUNTIME} if python == sys.executable else {}
    _probe_cache[python] = (time.monotonic(), found)
    return found


def health(path: Path | None = None) -> dict:
    """What the lobby shows about v0: can it use its LLM, does it have its memory."""
    path = path or v0_path()
    if path is None:
        return {}
    found = runtime_check(path)
    env = _base_env(path, True)
    key = next((k for k in _KEY_VARS if env.get(k)), None)
    python = v0_python(path)
    missing = [m for m in _RUNTIME if found and not found.get(m)]
    return {
        "python": python, "key": bool(key), "key_name": key, "missing": missing,
        "llm_ready": bool(key) and found.get("openai", False),
        "memory": bool(found.get("sentence_transformers")) and bool(found.get("torch")),
        "fix": "./setup.sh" if missing else None,
    }


def llm_problem(path: Path) -> str | None:
    """Why v0 could NOT use its LLM if started now, in words for the user (None = fine)."""
    h = health(path)
    if not h.get("key"):
        return ("MindForm is set to read experiences with its LLM, but no API key would reach it. "
                "Put GEMINI_API_KEY=... in mindform-world/.env (or mindform_v0/.env) and restart, "
                "or create the world with 'Offline fallback'.")
    if h.get("missing") and "openai" in h["missing"]:
        return (f"MindForm is set to read experiences with its LLM, but the Python that runs it "
                f"({h['python']}) has no 'openai' package. Run ./setup.sh, then restart.")
    return None


_FALLBACK = re.compile(r"LLM [\w ]*?unavailable \((.*)\); using")
_MEMORY_OFF = re.compile(r"memory/encoder unavailable \((.*)\); skipping")


def explain(raw: str) -> str:
    """v0's fallback message -> what the user can do about it."""
    low = raw.lower()
    if "no llm api key" in low:
        return "no API key reached MindForm (GEMINI_API_KEY)"
    if "no module named 'openai'" in low:
        return "the 'openai' package is missing in the Python that runs MindForm — run ./setup.sh"
    if (any(x in low for x in ("401", "403", "permission_denied", "unauthorized"))
            or ("api key" in low and any(x in low for x in ("valid", "expired", "denied", "revoked")))):
        return "the API key was rejected — check GEMINI_API_KEY"
    if "429" in low or "resource_exhausted" in low or "quota" in low:
        return "rate-limited by the API (429) — MindForm uses its heuristics until it recovers"
    if any(x in low for x in ("timed out", "timeout", "connection", "network", "name resolution")):
        return "can't reach the LLM API (network)"
    return raw[:200]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MindFormV0:
    """ONE v0 server process per world (see module docstring), shared by every resident.

    Every resident's turn of a beat arrives at once (in parallel), so starting and restarting
    the process is locked: if v0 dies, exactly one thread starts the next one while the others
    wait for it (they used to each start their own, leaving orphaned consoles behind). A
    pid file in the world's folder lets a restarted world server stop a console left over from
    before, and the console exits on its own when the world process goes away.
    """

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
        self._lock = threading.RLock()
        self.generation = 0                  # +1 per (re)start: only one thread restarts a dead process
        self.restarts = 0
        self._turns: dict[str, int] = {}     # ref -> v0's turn counter as last seen (retries never double-apply)
        self._last_ok = 0.0
        self._jitter = random.Random()
        self._start_failed: tuple[float, str] = (-1e9, "")
        env = os.environ.get("MINDFORM_TURN_TIMEOUT")
        self.turn_timeout = float(env) if env else _TIMEOUTS["turn_llm" if use_llm else "turn"]

    @property
    def _pidfile(self) -> Path:
        return self.workdir / "mindform_v0.pid"

    # ---- lifecycle ---------------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self.alive():
                return
            try:
                self._launch()
            except MindError as exc:
                self._start_failed = (time.monotonic(), str(exc))
                raise
            self._start_failed = (-1e9, "")

    def _launch(self) -> None:
        """Start v0 and wait for its readiness check. Under the lock."""
        with self._lock:
            if self.use_llm:
                problem = llm_problem(self.path)
                if problem:
                    raise MindError(problem)
            self.workdir.mkdir(parents=True, exist_ok=True)
            self._stop_leftover()
            head = self.path / "appraisal_head.pth"            # a trained offline appraiser, if any
            link = self.workdir / "appraisal_head.pth"
            if head.exists() and not link.exists():
                try:
                    link.symlink_to(head)
                except OSError:
                    pass
            port = _free_port()
            base_url = f"http://127.0.0.1:{port}"
            logpath = self.workdir / "mindform_v0.log"
            since = logpath.stat().st_size if logpath.exists() else 0      # this launch's lines only
            with open(logpath, "a") as logfile:
                proc = subprocess.Popen(
                    [v0_python(self.path), str(_BOOT), str(self.path), "--port", str(port), "--host", "127.0.0.1",
                     "--parent", str(os.getpid())],
                    cwd=self.workdir, env=_base_env(self.path, self.use_llm), stdout=logfile, stderr=subprocess.STDOUT)
            self._pidfile.write_text(f"{proc.pid} {port}\n")
            # Local process: never route it through HTTP(S)_PROXY / a system proxy (a VPN or proxy app
            # on the Mac otherwise answers 127.0.0.1 with 502 Bad Gateway and v0 never seems to start).
            client = httpx.Client(base_url=base_url, timeout=httpx.Timeout(self.turn_timeout, connect=5.0), trust_env=False)
            started = time.monotonic()
            deadline = started + _STARTUP_TIMEOUT
            status = None
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    client.close()
                    out = self._log_since(since)
                    raise MindError(f"MindForm v0 exited on startup: {_reason(out)}\n{out}")
                try:
                    status = client.get("/api/config", timeout=2.0).status_code
                    if status == 200:          # it listens only once its encoder is loaded and warm
                        self.proc, self.base_url, self._client = proc, base_url, client
                        self.generation += 1
                        self._last_ok = time.monotonic()
                        log.info("MindForm v0 (%s) ready at %s for %s in %.1fs", "LLM" if self.use_llm else "offline",
                                 base_url, self.workdir, time.monotonic() - started)
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
            client.close()
            _terminate(proc)
            answer = f" (it kept answering HTTP {status})" if status else ""
            out = self._log_since(since)
            raise MindError(f"MindForm v0 did not start within {_STARTUP_TIMEOUT:.0f}s{answer}: {_reason(out)}\n{out}")

    def _stop_leftover(self) -> None:
        """A console this world started before (a crashed or reloaded world server) still running?"""
        try:
            pid = int(self._pidfile.read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return
        if self.proc and self.proc.pid == pid:
            return
        try:
            cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout
        except Exception:
            return
        if "v0_boot.py" in cmd or "console.py" in cmd:
            log.warning("stopping a MindForm v0 console left over from before (pid %s)", pid)
            try:
                os.kill(pid, signal.SIGTERM)
                for _ in range(25):
                    time.sleep(0.2)
                    os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def stop(self) -> None:
        with self._lock:
            if self._client:
                self._client.close()
                self._client = None
            if self.proc:
                _terminate(self.proc)
            self.proc = None
            try:
                self._pidfile.unlink()
            except OSError:
                pass

    def alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def _ensure(self) -> tuple[int, httpx.Client | None]:
        """The process is up (starting it if not -- the other threads wait for that one start)."""
        with self._lock:
            if not self.alive():
                failed_at, why = self._start_failed
                if time.monotonic() - failed_at < _START_BACKOFF:
                    raise MindError(why)                 # it just failed to start: don't boot it again per resident
                if self.proc is not None:                # it was running and died between calls
                    log.warning("MindForm v0 exited (code %s); starting it again:\n%s", self.proc.poll(), self._log_tail())
                    self.restarts += 1
                self.start()
            return self.generation, self._client

    def _stuck(self) -> bool:
        """After a timeout: has v0 stopped answering (replace it), or is it just slow (the LLM)?"""
        client = self._client
        if not self.alive() or client is None:
            return True
        try:
            if client.get("/api/config", timeout=httpx.Timeout(5.0, connect=2.0)).status_code != 200:
                return True
        except Exception:
            return True
        # It answers, but nothing has come back for a long time: a wedged worker thread.
        return time.monotonic() - self._last_ok > 3 * self.turn_timeout

    def _restart(self, generation: int, why: str) -> None:
        """Replace the process -- once, however many threads saw it fail."""
        with self._lock:
            if generation != self.generation:
                return                                   # another thread already restarted it
            log.warning("MindForm v0 %s; restarting it", why)
            self.restarts += 1
            if self._client:
                self._client.close()
                self._client = None
            if self.proc:
                _terminate(self.proc)
            self.proc = None
            self.start()

    def diagnose(self) -> dict:
        """Why v0 last fell back to its heuristics / skipped memory, from its own log."""
        out = {}
        try:
            lines = (self.workdir / "mindform_v0.log").read_text(errors="replace").splitlines()[-400:]
        except OSError:
            return out
        for line in reversed(lines):
            if "llm" not in out:
                m = _FALLBACK.search(line)
                if m:
                    out["llm"] = explain(m.group(1))
            if "memory" not in out:
                m = _MEMORY_OFF.search(line)
                if m:
                    out["memory"] = ("sentence-transformers is not installed in the Python that runs MindForm — run ./setup.sh"
                                     if "sentence_transformers" in m.group(1) else m.group(1)[:200])
            if len(out) == 2:
                break
        return out

    def _log_since(self, offset: int, n: int = 25) -> str:
        try:
            with open(self.workdir / "mindform_v0.log", "rb") as f:
                f.seek(offset)
                return "\n".join(f.read().decode("utf-8", "replace").splitlines()[-n:])
        except OSError:
            return "(no log)"

    def _log_tail(self, n: int = 25) -> str:
        try:
            return "\n".join((self.workdir / "mindform_v0.log").read_text().splitlines()[-n:])
        except OSError:
            return "(no log)"

    def _call(self, method: str, url: str, *, timeout: float | None = None, retries: int = len(_RETRY_DELAYS),
              landed=None, **kwargs) -> dict:
        """One request to v0 with a timeout, retried with backoff (and jitter, so five residents
        don't retry in lockstep). A process that died is restarted -- once, however many threads
        notice. One that stopped answering is replaced. One that is merely slow is NOT sent the
        same turn again: ``landed()`` (for turns) says whether v0 applied it anyway -- before every
        retry, and after a slow timeout -- so a turn is never applied twice."""
        timeout = timeout or self.turn_timeout
        last: Exception | None = None
        for attempt in range(retries + 1):
            if attempt:
                time.sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS)) - 1] * self._jitter.uniform(0.8, 1.3))
                done = _quietly(landed)
                if done is not None:
                    return done
            generation, client = self._ensure()          # MindError if it can't even start: not retried
            try:
                if client is None:
                    raise httpx.ConnectError("not started")
                response = client.request(method, url, timeout=httpx.Timeout(timeout, connect=5.0), **kwargs)
            except httpx.TimeoutException as exc:
                last = exc
                if self._stuck():
                    self._restart(generation, f"stopped answering ({url}: nothing back in {timeout:.0f}s)")
                    continue
                log.warning("MindForm v0 is slow: %s took over %.0fs; not sending it again", url, timeout)
                done = _wait_for(landed, _SLOW_GRACE)
                if done is not None:
                    return done
                raise MindError(f"MindForm v0 is slow: no answer to {url} within {timeout:.0f}s "
                                f"(usually its LLM provider; MINDFORM_TURN_TIMEOUT raises the limit)") from exc
            except (httpx.TransportError, RuntimeError) as exc:      # refused / disconnected / client closed
                last = exc
                log.warning("MindForm v0 %s failed (try %d/%d): %s", url, attempt + 1, retries + 1, exc)
                if not self.alive():
                    self._restart(generation, "crashed")
                continue
            try:
                data = response.json()
            except ValueError:
                data = {}
            if response.status_code >= 500 and attempt < retries:
                last = MindError(f"HTTP {response.status_code}: {data.get('error') or ''}")
                log.warning("MindForm v0 %s answered %s (try %d/%d)", url, response.status_code, attempt + 1, retries + 1)
                continue
            if response.status_code >= 400:
                raise MindError(f"MindForm v0 {url}: {data.get('error') or response.status_code}")
            self._last_ok = time.monotonic()
            return data
        raise MindError(f"MindForm v0 unreachable after {retries + 1} tries: {last}")

    # ---- the contract --------------------------------------------------------------
    def create(self, spec: dict) -> Created:
        """``spec`` = {"mode": "bio", "bio": "..."} or
                      {"mode": "manual", "identity": {...}, "levels": {"O": 1-5, ...}}."""
        timeout = _TIMEOUTS["create_llm" if self.use_llm else "create"]
        if spec.get("mode") == "manual":
            identity = {k: v for k, v in (spec.get("identity") or {}).items() if v not in (None, "")}
            if not (identity.get("name") or "").strip():
                raise MindError("a name is required")
            name = identity["name"].strip()

            def landed():                                # created after all? then not a second "Aya"
                try:
                    return self._read_state(name)
                except MindError:
                    return None

            snap = self._call("POST", "/api/create/manual", timeout=timeout, landed=landed,
                              json={"identity": identity, "levels": spec.get("levels") or {}})
        else:
            bio = (spec.get("bio") or "").strip()
            if not bio:
                raise MindError("a biography is required")
            snap = self._call("POST", "/api/create/genesis", timeout=timeout, json={"bio": bio})
        self._turns[snap["name"]] = int(snap.get("turn") or 0)
        return Created(ref=snap["name"], name=snap["name"], identity=snap.get("identity") or {},
                       state=summarize_v0(snap), raw=snap, via=snap.get("created_via"))

    def experience(self, ref: str, text: str) -> Turn:
        if ref not in self._turns:                       # e.g. a world loaded from disk
            try:
                self._turns[ref] = int(self._read_state(ref).get("turn") or 0)
            except MindError:
                pass
        seen = self._turns.get(ref)

        def landed():
            """Did v0 apply this turn although the reply never arrived? Then don't apply it twice."""
            if seen is None:
                return None
            snap = self._read_state(ref)
            return snap if int(snap.get("turn") or 0) > seen else None

        try:
            snap = self._call("POST", "/api/turn", json={"name": ref, "message": text}, landed=landed)
        except MindError:
            # It may still land later (a slow turn keeps running in v0): read the counter afresh
            # next time, so a later retry can't mistake this turn for its own.
            self._turns.pop(ref, None)
            raise
        self._turns[ref] = int(snap.get("turn") or 0)
        return Turn(state=summarize_v0(snap), reply=snap.get("reply"), appraisal=snap.get("appraisal"),
                    formation=snap.get("formation"), raw=snap)

    def _read_state(self, ref: str) -> dict:
        return self._call("GET", "/api/state", params={"name": ref}, timeout=_TIMEOUTS["read"], retries=0)

    def state(self, ref: str) -> dict:
        return self._call("GET", "/api/state", params={"name": ref}, timeout=_TIMEOUTS["read"])


def _reason(log_text: str) -> str:
    """The line of a startup log that says what went wrong (the exception, usually last)."""
    lines = [l.strip() for l in log_text.splitlines() if l.strip()]
    for line in reversed(lines):
        if re.search(r"(Error|Exception|error:|No module named|Address already in use)", line):
            return line[:300]
    return lines[-1][:300] if lines else "no output"


def _quietly(check):
    if check is None:
        return None
    try:
        return check()
    except Exception:
        return None


def _wait_for(check, seconds: float):
    """Poll ``check()`` until it returns something or ``seconds`` pass."""
    if check is None:
        return None
    deadline = time.monotonic() + seconds
    while True:
        done = _quietly(check)
        if done is not None or time.monotonic() >= deadline:
            return done
        time.sleep(2.0)


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
