"""MindForm v0 under a whole island's load: ONE server per world, a warm CPU encoder before it
listens, retries that never apply a turn twice, and timeouts that tell 'slow' from 'stuck'.

Most tests drive the adapter against a stand-in v0 (an httpx mock transport) so every failure
can be scripted; the last ones use a real v0 checkout and are skipped without it.
"""
import json
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import httpx
import pytest

import app.minds.mindform_v0 as v0mod
from app.minds.base import MindError
from conftest import requires_v0

ROOT = Path(__file__).resolve().parent.parent
NAMES = ("Aya", "Rex", "Ines", "Leo", "Mira")


class FakeProc:
    pid = 4242

    def __init__(self):
        self.dead = False

    def poll(self):
        return 1 if self.dead else None

    def terminate(self):
        self.dead = True

    kill = terminate

    def wait(self, timeout=None):
        return 1


class FakeV0:
    """v0's API with a turn counter per mind. ``script`` says what the next POST /api/turn does."""

    def __init__(self):
        self.turns = {n: 3 for n in NAMES}
        self.posts = 0
        self.script: list[str] = []
        self.config_ok = True
        self.lock = threading.Lock()

    def snap(self, name):
        return {"name": name, "turn": self.turns[name], "traits": [], "drives": []}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/config":
            if not self.config_ok:
                raise httpx.ReadTimeout("no answer", request=request)
            return httpx.Response(200, json={})
        if path == "/api/state":
            return httpx.Response(200, json=self.snap(request.url.params["name"]))
        name = json.loads(request.content)["name"]
        with self.lock:
            self.posts += 1
            step = self.script.pop(0) if self.script else "ok"
            if step.startswith("apply") or step == "ok":
                self.turns[name] += 1
        if step.endswith("disconnect"):
            raise httpx.RemoteProtocolError("Server disconnected without sending a response", request=request)
        if step.endswith("timeout"):
            raise httpx.ReadTimeout("timed out", request=request)
        if step.endswith("500"):
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={**self.snap(name), "reply": "Fine.", "appraisal": {"valence": 0.3}})


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A MindFormV0 adapter whose 'process' is the stand-in above; counts every (re)start."""
    monkeypatch.setattr(v0mod, "v0_path", lambda: tmp_path)
    monkeypatch.setattr(v0mod, "_RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.setattr(v0mod, "_SLOW_GRACE", 0.0)
    server = FakeV0()
    mind = v0mod.MindFormV0(tmp_path / "minds", use_llm=False)
    mind.starts = 0

    def start():
        with mind._lock:
            if mind.alive():
                return
            time.sleep(0.05)                             # a start takes a moment: widens any race
            mind.starts += 1
            mind.proc = FakeProc()
            mind._client = httpx.Client(base_url="http://v0", transport=httpx.MockTransport(server.handler))
            mind.generation += 1
            mind._last_ok = time.monotonic()

    mind.start = start
    mind.start()
    yield mind, server
    mind._client.close()


def test_a_crash_restarts_one_server_for_everyone(fake):
    """The bug: after a crash, each resident's parallel turn started its own console."""
    mind, server = fake
    for n in NAMES:
        mind._turns[n] = server.turns[n]
    mind.proc.dead = True                                # v0 crashed
    barrier, results = threading.Barrier(len(NAMES)), {}

    def turn(n):
        barrier.wait()
        try:
            results[n] = mind.experience(n, "I woke up at 7:00.").reply
        except Exception as exc:
            results[n] = exc

    threads = [threading.Thread(target=turn, args=(n,)) for n in NAMES]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results == {n: "Fine." for n in NAMES}
    assert mind.starts == 2 and mind.restarts == 1       # the first start + ONE restart
    assert server.posts == len(NAMES)


def test_a_turn_that_landed_is_not_sent_again(fake):
    mind, server = fake
    server.script = ["apply-then-disconnect"]            # v0 applied it, then the reply was lost
    turn = mind.experience("Aya", "I sat by the fountain.")
    assert server.posts == 1 and server.turns["Aya"] == 4 and turn.state is not None


def test_a_turn_that_lands_late_is_not_mistaken_for_the_next(fake):
    """A slow turn fails this beat but v0 finishes it later. The next beat's turn must not take
    that late landing for its own when its reply is lost: it re-reads the counter first."""
    mind, server = fake
    server.script = ["timeout"]
    with pytest.raises(MindError, match="slow"):
        mind.experience("Aya", "beat one")
    server.turns["Aya"] += 1                             # ...the slow turn lands after all
    server.script = ["disconnect"]                       # next beat: the reply is lost before v0 applies it
    mind.experience("Aya", "beat two")
    assert server.posts == 3 and server.turns["Aya"] == 5   # beat two was resent and applied once


def test_a_create_that_landed_is_not_made_twice(fake):
    mind, server = fake
    created = []

    def handler(request):
        if request.url.path == "/api/create/manual":
            created.append(1)
            server.turns["Nell"] = 0
            raise httpx.RemoteProtocolError("Server disconnected without sending a response", request=request)
        if request.url.path == "/api/state" and request.url.params["name"] not in server.turns:
            return httpx.Response(404, json={"error": "no such character"})
        return server.handler(request)

    mind._client = httpx.Client(base_url="http://v0", transport=httpx.MockTransport(handler))
    made = mind.create({"mode": "manual", "identity": {"name": "Nell"}, "levels": {}})
    assert made.ref == "Nell" and len(created) == 1


def test_a_turn_that_did_not_land_is_retried(fake):
    mind, server = fake
    server.script = ["disconnect"]
    turn = mind.experience("Aya", "I sat by the fountain.")
    assert turn.reply == "Fine." and server.posts == 2 and server.turns["Aya"] == 4


def test_a_server_error_after_the_turn_landed_is_not_resent(fake):
    mind, server = fake
    server.script = ["apply-then-500"]
    mind.experience("Aya", "I sat by the fountain.")
    assert server.posts == 1 and server.turns["Aya"] == 4


def test_slow_is_not_stuck(fake):
    """A slow LLM call: v0 still answers its health check, so it is NOT restarted (that would
    kill every other resident's turn) and the turn is NOT sent twice."""
    mind, server = fake
    server.script = ["apply-then-timeout"]
    mind.experience("Aya", "I sat by the fountain.")
    assert server.posts == 1 and mind.starts == 1 and server.turns["Aya"] == 4


def test_slow_and_still_running_fails_this_beat_without_resending(fake):
    mind, server = fake
    server.script = ["timeout"]
    with pytest.raises(MindError, match="slow"):
        mind.experience("Aya", "I sat by the fountain.")
    assert server.posts == 1 and mind.starts == 1


def test_a_stuck_server_is_replaced(fake):
    mind, server = fake
    server.script = ["timeout"]
    server.config_ok = False                             # not even the health check answers
    turn = mind.experience("Aya", "I sat by the fountain.")
    assert turn.reply == "Fine." and mind.starts == 2 and mind.restarts == 1


def test_a_server_that_never_comes_back_is_a_clear_error(fake):
    mind, server = fake
    server.script = ["disconnect"] * 5
    with pytest.raises(MindError, match="unreachable after 3 tries"):
        mind.experience("Aya", "I sat by the fountain.")


def test_a_mind_that_cannot_start_is_not_booted_once_per_resident(tmp_path, monkeypatch):
    """v0 broken (say, a bad checkout): the first turn's start fails and the others fail fast
    with the same reason instead of each booting it again."""
    monkeypatch.setattr(v0mod, "v0_path", lambda: tmp_path)
    mind = v0mod.MindFormV0(tmp_path / "minds", use_llm=False)
    launches = []

    def launch():
        launches.append(1)
        time.sleep(0.05)
        raise MindError("MindForm v0 exited on startup: ModuleNotFoundError: No module named 'web'\n(log)")

    mind._launch = launch
    errors = []

    def turn(n):
        try:
            mind.experience(n, "I woke up at 7:00.")
        except MindError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=turn, args=(n,)) for n in NAMES]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(launches) == 1 and len(errors) == len(NAMES)
    assert all("No module named 'web'" in e for e in errors)


def test_turn_timeout_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setattr(v0mod, "v0_path", lambda: tmp_path)
    assert v0mod.MindFormV0(tmp_path, use_llm=True).turn_timeout == v0mod._TIMEOUTS["turn_llm"]
    assert v0mod.MindFormV0(tmp_path, use_llm=False).turn_timeout == v0mod._TIMEOUTS["turn"]
    monkeypatch.setenv("MINDFORM_TURN_TIMEOUT", "200")
    assert v0mod.MindFormV0(tmp_path, use_llm=True).turn_timeout == 200.0


# ---- the boot wrapper (v0_boot.py) --------------------------------------------------------
@pytest.fixture
def boot(monkeypatch):
    sys.path.insert(0, str(ROOT / "app" / "minds"))
    import v0_boot
    sys.path.pop(0)
    return v0_boot


def test_boot_loads_the_encoder_once_on_cpu_one_encode_at_a_time(boot, monkeypatch):
    seen = {"devices": [], "bars": [], "active": 0, "most": 0, "builds": 0}
    guard = threading.Lock()

    class SentenceTransformer:                           # sentence-transformers' class, as v0 uses it
        def __init__(self, name, device=None):
            seen["builds"] += 1
            seen["devices"].append(device)

        def encode(self, text, show_progress_bar=None):
            with guard:
                seen["active"] += 1
                seen["most"] = max(seen["most"], seen["active"])
            seen["bars"].append(show_progress_bar)
            time.sleep(0.01)
            with guard:
                seen["active"] -= 1
            return [0.0] * 384

    st = types.ModuleType("sentence_transformers")
    st.SentenceTransformer = SentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", st)
    core = types.ModuleType("core")
    encoder = types.ModuleType("core.encoder")
    core.encoder = encoder
    monkeypatch.setitem(sys.modules, "core", core)
    monkeypatch.setitem(sys.modules, "core.encoder", encoder)

    boot.prepare_encoder(offline=True)
    # v0's core/encoder.py: `from sentence_transformers import SentenceTransformer` at import,
    # then `_encoder = SentenceTransformer(MODEL_NAME)` -- with no device (MPS on a Mac).
    model = sys.modules["sentence_transformers"].SentenceTransformer("all-MiniLM-L6-v2")
    encoder.encode_text = model.encode
    threads = [threading.Thread(target=model.encode, args=("a",)) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert seen["devices"] == ["cpu"]                    # forced off MPS
    assert seen["most"] == 1                             # never two encodes at once
    assert set(seen["bars"]) == {False}                  # no "Batches: 0%" bars in the log


def test_boot_goes_offline_once_the_model_is_cached(boot, tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    assert not boot.model_cached()
    snap = tmp_path / "models--sentence-transformers--all-MiniLM-L6-v2" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    for name in ("config.json", "modules.json"):
        (snap / name).write_text("{}")
    assert not boot.model_cached()                       # no weights yet: a download is still needed
    (snap / "model.safetensors").write_bytes(b"x")
    assert boot.model_cached()


def _fake_v0(tmp_path, serve="print('LISTENING', argv, flush=True)"):
    v0 = tmp_path / "v0"
    (v0 / "web").mkdir(parents=True)
    (v0 / "core").mkdir()
    (v0 / "web" / "__init__.py").write_text("")
    (v0 / "core" / "__init__.py").write_text("")
    (v0 / "core" / "encoder.py").write_text("print('ENCODER-LOADED', flush=True)\ndef encode_text(t): return [0.0]\n")
    (v0 / "web" / "server.py").write_text(f"def main(argv):\n    {serve}\n")
    return v0


def test_boot_listens_only_after_warm_up(tmp_path):
    """The readiness check can't pass before the encoder is loaded and warm."""
    import os
    out = subprocess.run([sys.executable, str(ROOT / "app" / "minds" / "v0_boot.py"), str(_fake_v0(tmp_path)),
                          "--port", "1", "--parent", str(os.getpid())], capture_output=True, text=True, timeout=120,
                         env={"PATH": "/usr/bin:/bin", "HF_HUB_CACHE": str(tmp_path / "hf")})
    log = out.stdout
    # sentence-transformers may be missing in this Python: then it says so and still serves.
    assert ("ENCODER-LOADED" in log and log.index("ENCODER-LOADED") < log.index("LISTENING")) or "unavailable" in log, log
    assert "LISTENING ['--port', '1']" in log            # --parent is the wrapper's, not v0's


def test_boot_exits_with_its_world(tmp_path):
    """A console never outlives the world process that started it (no orphans after a crash)."""
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()                                          # a world pid that no longer exists
    proc = subprocess.Popen([sys.executable, str(ROOT / "app" / "minds" / "v0_boot.py"),
                             str(_fake_v0(tmp_path, serve="import time; time.sleep(600)")),
                             "--port", "1", "--parent", str(gone.pid)],
                            stdout=subprocess.PIPE, text=True, env={"PATH": "/usr/bin:/bin", "HF_HUB_CACHE": str(tmp_path)})
    try:
        assert proc.wait(timeout=60) == 0
        assert "is gone; exiting" in proc.stdout.read()
    finally:
        if proc.poll() is None:
            proc.kill()


# ---- the world waits for its mind --------------------------------------------------------
def test_the_first_beat_waits_for_the_mind_to_be_ready(tmp_path, monkeypatch):
    from app.world.engine import World
    from app.world.manager import WorldManager
    from conftest import control_setup

    manager = WorldManager(tmp_path / "worlds")
    try:
        world = manager.create(control_setup(), background=False)
        world.save()
        real_boot = World.boot_mind

        def slow_boot(self):
            time.sleep(0.5)                              # the encoder warming up
            real_boot(self)

        monkeypatch.setattr(World, "boot_mind", slow_boot)
        other = WorldManager(manager.root)
        try:
            resumed = other.get(world.id)
            assert not resumed.mind_ready.is_set()       # still waking up...
            assert resumed.public_state()["mind_ready"] is False
            assert resumed.run_beat()                    # ...the beat waits for it
            assert all(not r.mind_error for r in resumed.residents.values())
        finally:
            other.shutdown()
    finally:
        manager.shutdown()


# ---- against a real MindForm v0 -------------------------------------------------------------
@requires_v0
def test_real_v0_one_console_after_a_crash(tmp_path, monkeypatch):
    started = []
    real_popen = v0mod.subprocess.Popen

    def counting(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(v0mod.subprocess, "Popen", counting)
    mind = v0mod.MindFormV0(tmp_path / "minds", use_llm=False)
    try:
        mind.start()
        assert (tmp_path / "minds" / "mindform_v0.pid").exists()
        for n in NAMES:
            mind.create({"mode": "manual", "identity": {"name": n}, "levels": {}})
        mind.proc.kill()
        mind.proc.wait()
        barrier, results = threading.Barrier(len(NAMES)), {}

        def turn(n):
            barrier.wait()
            try:
                results[n] = mind.experience(n, "I woke up in my cottage at 7:00.").state["turn"]
            except Exception as exc:
                results[n] = exc

        threads = [threading.Thread(target=turn, args=(n,)) for n in NAMES]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert results == {n: 1 for n in NAMES}         # each turn applied exactly once
        assert len(started) == 2 and sum(p.poll() is None for p in started) == 1
        log = (tmp_path / "minds" / "mindform_v0.log").read_text()
        assert "[world-boot]" in log and "Batches:" not in log
    finally:
        mind.stop()
        for p in started:
            if p.poll() is None:
                p.kill()
    assert not (tmp_path / "minds" / "mindform_v0.pid").exists()
