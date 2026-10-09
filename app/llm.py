"""The world's own LLM: one OpenAI-compatible JSON call (planner, narrator, director).

Same discipline as MindForm: the LLM is primary, never required. ``complete_json`` raises
on anything short of a parsed JSON object (no key, transport error, unparseable reply) and
every caller falls back to its rules. Speaks plain HTTP through httpx, so the world needs
no provider SDK; any OpenAI-compatible endpoint works (Gemini by default).

Built so a recording never stalls on the API:
    * transient failures (timeouts, connection drops, 429, 5xx) are retried with exponential
      backoff and jitter, honouring Retry-After;
    * a rejected key (401/403) is not retried, and calls skip the API for a few minutes;
    * after several transient failures in a row the circuit opens: for a short while every
      call fails fast (callers use their rules at once), then one call tries the API again.
"""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import time

import httpx

from app.config import llm_settings

log = logging.getLogger("mindform.world.llm")

JSON_MAX_TOKENS = 2048          # thinking models spend part of this before the JSON
_JSON_NUDGE = "\n\nReturn ONLY the JSON object now -- no thinking, no prose, no code fences."
_REASONING = re.compile(r"<(thought|thinking|think|reasoning|reflection|scratchpad)>.*?</\1>",
                        re.DOTALL | re.IGNORECASE)


class LLMUnavailable(RuntimeError):
    """No key configured (or it was just rejected) -- callers use their rules instead."""


AUTH_COOLDOWN = 300.0         # after a 401/403, skip calls for this long instead of hammering
_auth_blocked_until: dict[str, float] = {}
RETRY_BASE_DELAY = 0.6        # seconds; doubled per attempt, plus jitter (tests set 0)
RETRY_MAX_DELAY = 8.0
CIRCUIT_FAILURES = 4          # transient failures in a row that open the circuit
CIRCUIT_COOLDOWN = 45.0       # seconds the API is skipped once the circuit is open
_TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529}
_circuit = {"failures": 0, "open_until": 0.0}
_circuit_lock = threading.Lock()


class Stats:
    """Thread-safe call counters, surfaced in the UI so 'via llm' vs 'via rules' is honest."""

    def __init__(self):
        self._lock = threading.Lock()
        self.calls = 0
        self.failures = 0
        self.last_error = ""

    def record(self, ok: bool, error: str = ""):
        with self._lock:
            self.calls += 1
            if not ok:
                self.failures += 1
                self.last_error = error[:200]

    def snapshot(self) -> dict:
        with self._lock:
            return {"calls": self.calls, "failures": self.failures, "last_error": self.last_error}


def available() -> bool:
    return bool(llm_settings()["api_key"])


def model_label() -> str:
    return llm_settings()["model"] or "llm"


def parse_json_object(text: str) -> dict:
    """Pull the first JSON object out of a model reply (reasoning blocks and fences dropped)."""
    cleaned = _REASONING.sub("", text or "")
    cleaned = cleaned.replace("```json", "```")
    if "```" in cleaned:
        parts = cleaned.split("```")
        fenced = [p for p in parts[1::2] if "{" in p]
        if fenced:
            cleaned = fenced[0]
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth, in_str, escape = 0, False, False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                value = json.loads(cleaned[start:i + 1])
                if not isinstance(value, dict):
                    raise ValueError("reply JSON is not an object")
                return value
    raise ValueError("unterminated JSON object in reply")


def reset_circuit() -> None:
    with _circuit_lock:
        _circuit.update(failures=0, open_until=0.0)


def _circuit_open() -> bool:
    with _circuit_lock:
        return time.monotonic() < _circuit["open_until"]


def _circuit_record(ok: bool) -> None:
    with _circuit_lock:
        if ok:
            _circuit["failures"] = 0
            return
        _circuit["failures"] += 1
        if _circuit["failures"] >= CIRCUIT_FAILURES:
            _circuit["open_until"] = time.monotonic() + CIRCUIT_COOLDOWN
            _circuit["failures"] = CIRCUIT_FAILURES - 1          # half-open: one more failure re-opens it
            log.warning("world LLM failing repeatedly; using rules for %ds", CIRCUIT_COOLDOWN)


def _retry_after(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    value = response.headers.get("retry-after") if response is not None else None
    try:
        return float(value) if value else None
    except ValueError:
        return None


def complete_json(system: str, user: str, *, temperature: float = 0.8,
                  max_tokens: int = JSON_MAX_TOKENS, timeout: float = 45.0,
                  retries: int = 2, stats: Stats | None = None) -> dict:
    """Ask the configured chat model for a JSON object. Raises on failure (callers fall back)."""
    cfg = llm_settings()
    if not cfg["api_key"]:
        raise LLMUnavailable("no LLM API key configured")
    if time.monotonic() < _auth_blocked_until.get(cfg["api_key"], 0.0):
        if stats:
            stats.record(False, "API key rejected (HTTP 401/403); retrying in a few minutes")
        raise LLMUnavailable("API key was rejected recently")
    if _circuit_open():
        if stats:
            stats.record(False, "LLM paused after repeated failures; using rules for a moment")
        raise LLMUnavailable("LLM circuit open after repeated failures")
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    last: Exception | None = None
    nudge = False
    for attempt in range(retries + 1):
        content = user + _JSON_NUDGE if nudge else user
        payload = {
            "model": cfg["model"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": content}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        transient = False
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"]
            result = parse_json_object(text)
            if stats:
                stats.record(True)
            _circuit_record(True)
            return result
        except Exception as exc:          # transport, HTTP status, shape, or parse
            last = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if stats:
                stats.record(False, f"HTTP {status}" if status else f"{type(exc).__name__}: {exc}")
            if status in (401, 403):                 # a bad key will not get better on retry
                _auth_blocked_until[cfg["api_key"]] = time.monotonic() + AUTH_COOLDOWN
                log.warning("world LLM rejected the API key (HTTP %s); using rules for %ds", status, AUTH_COOLDOWN)
                break
            if status is not None and status not in _TRANSIENT_STATUS:
                break                                # 400/404/422...: asking again will not help
            transient = status is not None or isinstance(exc, httpx.TransportError)
            nudge = not transient                    # a reply we could not parse: ask for bare JSON
            log.info("world LLM call failed (attempt %d/%d): %s", attempt + 1, retries + 1, exc)
            if transient:
                _circuit_record(False)
                if _circuit_open():
                    break
            if attempt < retries:
                delay = _retry_after(exc) or RETRY_BASE_DELAY * (2 ** attempt) * (1 + 0.3 * random.random())
                time.sleep(min(RETRY_MAX_DELAY, delay))
    raise last  # type: ignore[misc]
