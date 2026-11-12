"""Minimal Gemini REST client: JSON output, retries/backoff, model fallback, on-disk response cache.

The prompt can be plain text or a list of parts: text, or (mime_type, bytes) for inline media
(e.g. a small mp4 proxy, so Gemini can watch a scene).

Plain urllib on purpose: one endpoint, no SDK churn.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class LLMError(RuntimeError):
    """Gemini could not produce an answer (after retries and fallbacks)."""


class _Retryable(Exception):
    def __init__(self, msg: str, wait: float | None = None, switch_model: bool = False, overloaded: bool = False):
        super().__init__(msg)
        self.wait, self.switch_model, self.overloaded = wait, switch_model, overloaded


_last_call = {"t": 0.0}
_cooldown: dict[str, float] = {}  # model -> monotonic time until which it is skipped (overloaded / out of quota)
OVERLOAD_COOLDOWN_S = 600
QUOTA_COOLDOWN_S = 3600


Part = str | tuple[str, bytes]


def generate_json(
    prompt: str | list[Part],
    schema: dict,
    *,
    models: list[str],
    cache_dir: Path | None = None,
    temperature: float = 0.4,
    timeout_s: float = 180,
    max_retries: int = 4,
    min_interval_s: float = 4,
    system: str | None = None,
    refresh: bool = False,
    on_status: Callable[[str], None] = lambda m: None,
) -> tuple[dict, str]:
    """Returns (parsed JSON, model used). Raises LLMError."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise LLMError("GEMINI_API_KEY is not set (add it to .env)")

    body = {
        "contents": [{"role": "user", "parts": _parts(prompt)}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    last_err = "no models configured"
    order = list(dict.fromkeys(models))  # dedupe, keep order
    for model in order:
        cache = None
        if cache_dir:
            digest = hashlib.sha256(json.dumps([model, body], sort_keys=True).encode()).hexdigest()[:16]
            cache = cache_dir / f"{model}-{digest}.json"
            if cache.exists() and not refresh:
                return json.loads(cache.read_text()), model
        if _cooldown.get(model, 0) > time.monotonic() and model != order[-1]:
            continue  # failed moments ago in this run: don't spend two more timeouts finding that out

        overloaded = 0  # consecutive timeouts / 5xx on this model
        for attempt in range(max_retries + 1):
            gap = min_interval_s - (time.monotonic() - _last_call["t"])
            if gap > 0:
                time.sleep(gap)
            try:
                _last_call["t"] = time.monotonic()
                text = _call(model, body, key, timeout_s)
                data = json.loads(text)
            except _Retryable as e:
                last_err = f"{model}: {e}"
                overloaded = overloaded + 1 if e.overloaded else 0
                # a model that keeps timing out / returning 503 is overloaded: the next model is faster than waiting
                if e.switch_model or attempt == max_retries or (overloaded >= 2 and model != order[-1]):
                    _cooldown[model] = time.monotonic() + (QUOTA_COOLDOWN_S if e.switch_model else OVERLOAD_COOLDOWN_S)
                    on_status(f"{model} unavailable ({e}), trying next model…")
                    break
                wait = e.wait if e.wait is not None else min(5 * 2 ** attempt, 60)
                on_status(f"{model}: {e} · retrying in {wait:.0f}s ({attempt + 1}/{max_retries})")
                time.sleep(wait)
                continue
            except json.JSONDecodeError:
                last_err = f"{model}: returned invalid JSON"
                on_status(f"{model} returned invalid JSON, retrying…")
                continue
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(data))
            return data, model
    raise LLMError(last_err)


def _parts(prompt: str | list[Part]) -> list[dict]:
    items = [prompt] if isinstance(prompt, str) else prompt
    out = []
    for it in items:
        if isinstance(it, str):
            out.append({"text": it})
        else:
            mime, data = it
            out.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}})
    return out


def _call(model: str, body: dict, key: str, timeout_s: float) -> str:
    req = urllib.request.Request(
        API.format(model=model),
        data=json.dumps(body).encode(),
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            err = json.loads(raw).get("error", {})
        except json.JSONDecodeError:
            err = {"message": raw[:300]}
        msg = err.get("message", "").split("\n")[0][:200]
        if e.code == 429:
            daily = bool(re.search(r"per ?day|PerDay|daily", raw))
            return_wait = _retry_delay(err)
            raise _Retryable(f"rate limited{' (daily quota used up)' if daily else ''}",
                             wait=return_wait, switch_model=daily) from None
        if e.code in (500, 502, 503, 504):
            raise _Retryable(f"HTTP {e.code} {err.get('status', '')}".strip(), overloaded=True) from None
        if e.code == 404:
            raise _Retryable(f"model not found", switch_model=True) from None
        raise LLMError(f"Gemini HTTP {e.code}: {msg}") from None  # 400/401/403: retrying won't help
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        raise _Retryable(f"network: {getattr(e, 'reason', e)}",
                         overloaded=isinstance(e, (socket.timeout, TimeoutError)) or "timed out" in str(e)) from None

    cands = resp.get("candidates") or []
    if not cands:
        block = (resp.get("promptFeedback") or {}).get("blockReason")
        raise _Retryable(f"empty response{f' (blocked: {block})' if block else ''}")
    c = cands[0]
    parts = (c.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if c.get("finishReason") not in (None, "STOP") and not text:
        raise _Retryable(f"finish reason {c.get('finishReason')}")
    return text


def _retry_delay(err: dict) -> float | None:
    for d in err.get("details", []):
        if d.get("@type", "").endswith("RetryInfo") and (m := re.match(r"([\d.]+)s", d.get("retryDelay", ""))):
            return min(float(m.group(1)) + 1, 120)
    return None
