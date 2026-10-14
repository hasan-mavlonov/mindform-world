"""Boots MindForm v0's console for MindForm World: one process per world, ready for the turns
of every resident arriving at once. Runs as a script in v0's OWN Python (it imports nothing
from the world, and changes nothing in v0's code):

    python v0_boot.py <v0 checkout> --port N --host 127.0.0.1 --parent <world pid>

v0's console answers requests on parallel threads, and its memory encoder (a
sentence-transformers MiniLM) used to load lazily on the first turn -- so five residents'
first turns loaded and ran it at the same moment, on Apple's MPS device, and hung or crashed.
Before the console starts LISTENING (so the world's readiness check only passes once it is
truly ready), this:

  * loads the encoder once, on the CPU (MiniLM is small; MPS + threads is what deadlocked),
    with its tokenizer single-threaded (TOKENIZERS_PARALLELISM=false) and no progress bars;
  * makes every encode() run one at a time, behind a lock;
  * warms it up with a dummy encode;
  * loads it from the local Hugging Face cache once it has been downloaded (offline: no call
    to huggingface.co on every start).

It also exits by itself when the world that started it goes away, so a crashed or reloaded
world server never leaves consoles behind.
"""
import glob
import os
import sys
import threading
import time

MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def say(message):
    print(f"[world-boot] {message}", flush=True)


def hf_hub_cache():
    if os.environ.get("HF_HUB_CACHE"):
        return os.environ["HF_HUB_CACHE"]
    home = os.environ.get("HF_HOME") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "huggingface")
    return os.path.join(home, "hub")


def model_cached(model=MODEL):
    """Whether a complete copy of the model is already in the local cache."""
    snapshots = os.path.join(hf_hub_cache(), "models--" + model.replace("/", "--"), "snapshots", "*")
    for snap in glob.glob(snapshots):
        has_config = os.path.exists(os.path.join(snap, "config.json")) and os.path.exists(os.path.join(snap, "modules.json"))
        has_weights = any(os.path.exists(os.path.join(snap, w)) for w in ("model.safetensors", "pytorch_model.bin"))
        if has_config and has_weights:
            return True
    return False


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:                                # exists, but not ours to signal
        return True
    return True


def watch_parent(pid):
    """Exit when the world process that started us is gone (crashed, killed, reloaded)."""
    def loop():
        while alive(pid):
            time.sleep(2)
        say("the world that started this console is gone; exiting")
        os._exit(0)
    threading.Thread(target=loop, name="parent-watch", daemon=True).start()


def prepare_encoder(offline):
    """Load v0's encoder once (CPU, one encode at a time) and warm it up."""
    started = time.monotonic()
    try:
        import sentence_transformers
    except Exception as exc:                      # v0 then runs without memory recall, and says so per turn
        say(f"memory encoder unavailable ({exc}); run ./setup.sh in mindform-world")
        return
    base = sentence_transformers.SentenceTransformer
    lock = threading.Lock()

    class OneAtATimeOnCPU(base):
        def __init__(self, *args, **kwargs):
            kwargs["device"] = "cpu"
            super().__init__(*args, **kwargs)

        def encode(self, *args, **kwargs):
            kwargs.setdefault("show_progress_bar", False)
            with lock:
                return super().encode(*args, **kwargs)

    sentence_transformers.SentenceTransformer = OneAtATimeOnCPU
    try:
        import core.encoder as encoder            # v0's own module: builds the model with the class above
        encoder.encode_text("warm-up")
    except Exception as exc:
        say(f"memory encoder failed to load ({exc}); v0 runs without memory recall")
        return
    say(f"memory encoder ready on cpu ({'local cache' if offline else 'downloaded'}) "
        f"in {time.monotonic() - started:.1f}s")


def main(argv):
    if not argv:
        sys.exit("usage: v0_boot.py <v0 checkout> --port N [--host H] [--parent PID]")
    v0, args = argv[0], argv[1:]
    parent = os.getppid()
    if "--parent" in args:
        i = args.index("--parent")
        parent = int(args[i + 1])
        args = args[:i] + args[i + 2:]
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    offline = model_cached()
    if offline:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    sys.path.insert(0, v0)
    watch_parent(parent)
    prepare_encoder(offline)
    from web.server import main as serve       # v0's console; it starts listening only now
    serve(args)


if __name__ == "__main__":
    main(sys.argv[1:])
