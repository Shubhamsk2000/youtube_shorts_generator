"""The app's single doorway to AI models: JSON answers (generate_json).

Which provider and model answer is decided by configs/llm.yaml, per task, so you can switch to NVIDIA,
OpenRouter, Groq, a local Ollama... without touching the apps. How each provider is called lives in
common/llm_providers.py. This file adds what every provider needs: retries with backoff, model fallback,
cool-downs for models that are out of quota, call spacing for free tiers and an on-disk answer cache.

The prompt can be plain text or a list of parts: text, or (mime_type, bytes) for inline media
(e.g. a small mp4 proxy so Gemini can watch a scene). Media calls use the `media:` route.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from .llm_providers import KINDS, LLMError, Part, ProviderCfg, Request, Retryable
from .paths import CONFIG_DIR

__all__ = ["LLMError", "Part", "generate_json", "route_info", "load_llm_config"]

CONFIG_FILE = CONFIG_DIR / "llm.yaml"
OVERLOAD_COOLDOWN_S = 600
QUOTA_COOLDOWN_S = 3600

_last_call: dict[str, float] = {}
_cooldown: dict[str, float] = {}   # "provider/model" -> monotonic time until which it is skipped
_lock = threading.Lock()


# ------------------------------------------------------------------ config

@dataclass
class Route:
    provider: str = "gemini"
    models: list[str] = field(default_factory=list)   # empty = the calling app's own models (Gemini only)


@dataclass
class LLMConfig:
    default: Route = field(default_factory=Route)
    media: Route = field(default_factory=Route)
    tasks: dict[str, Route] = field(default_factory=dict)
    providers: dict[str, ProviderCfg] = field(default_factory=lambda: {
        "gemini": ProviderCfg("gemini", "gemini", api_key_env="GEMINI_API_KEY")})


_cfg_cache: dict = {"mtime": None, "cfg": None}


def load_llm_config() -> LLMConfig:
    """configs/llm.yaml, re-read when it changes (no restart needed)."""
    try:
        mtime = CONFIG_FILE.stat().st_mtime
    except FileNotFoundError:
        mtime = None
    if _cfg_cache["cfg"] is not None and _cfg_cache["mtime"] == mtime:
        return _cfg_cache["cfg"]
    cfg = LLMConfig()
    if mtime is not None:
        raw = yaml.safe_load(CONFIG_FILE.read_text()) or {}

        def route(d) -> Route:
            d = d or {}
            return Route(str(d.get("provider", "gemini")), [str(m) for m in d.get("models") or []])

        cfg.default = route(raw.get("default"))
        cfg.media = route(raw.get("media"))
        cfg.tasks = {k: route(v) for k, v in (raw.get("tasks") or {}).items() if v}
        for name, p in (raw.get("providers") or {}).items():
            p = p or {}
            cfg.providers[name] = ProviderCfg(
                name=name, kind=p.get("kind", "gemini"), base_url=p.get("base_url"),
                api_key_env=p.get("api_key_env"), models=[str(m) for m in p.get("models") or []],
                min_interval_s=p.get("min_interval_s"), json_mode=p.get("json_mode", "object"),
                extra_body=p.get("extra_body") or {}, headers=p.get("headers") or {})
    _cfg_cache.update(mtime=mtime, cfg=cfg)
    return cfg


def _resolve(task: str | None, has_media: bool, caller_models: list[str]) -> tuple[ProviderCfg, list[str]]:
    cfg = load_llm_config()
    route = cfg.tasks.get(task or "") or (cfg.media if has_media else cfg.default)
    p = cfg.providers.get(route.provider)
    if p is None:
        raise LLMError(f"configs/llm.yaml: unknown provider '{route.provider}'")
    if p.kind not in KINDS:
        raise LLMError(f"configs/llm.yaml: provider '{p.name}' has unknown kind '{p.kind}'")
    # the apps' own model lists are Gemini model names: only valid when Gemini answers
    models = route.models or (caller_models if p.kind == "gemini" else []) or p.models
    if not models:
        raise LLMError(f"configs/llm.yaml: no models for provider '{p.name}' (task {task or 'default'})")
    return p, list(dict.fromkeys(models))


def route_info(task: str | None = None, has_media: bool = False, caller_models: list[str] | None = None) -> dict:
    """What would answer this task: for status pages."""
    try:
        p, models = _resolve(task, has_media, caller_models or ["gemini-3.8-flash"])
        key_ok = True
        try:
            p.key()
        except LLMError:
            key_ok = False
        return {"provider": p.name, "kind": p.kind, "models": models, "key_set": key_ok}
    except LLMError as e:
        return {"provider": None, "error": str(e), "key_set": False}


# ------------------------------------------------------------------ calls

def _space(provider: str, min_interval_s: float) -> None:
    with _lock:
        gap = min_interval_s - (time.monotonic() - _last_call.get(provider, 0.0))
        if gap > 0:
            time.sleep(gap)
        _last_call[provider] = time.monotonic()


def _with_retries(p: ProviderCfg, models: list[str], call: Callable[[str], object], *, max_retries: int,
                  min_interval_s: float, on_status: Callable[[str], None], accept: Callable[[object], object]):
    last_err = "no models configured"
    interval = p.min_interval_s if p.min_interval_s is not None else min_interval_s
    for model in models:
        tag = f"{p.name}/{model}"
        if _cooldown.get(tag, 0) > time.monotonic() and model != models[-1]:
            continue  # failed moments ago: don't spend two more timeouts finding that out
        overloaded = 0
        for attempt in range(max_retries + 1):
            _space(p.name, interval)
            try:
                return accept(call(model)), model
            except Retryable as e:
                last_err = f"{model}: {e}"
                overloaded = overloaded + 1 if e.overloaded else 0
                # a model that keeps timing out / returning 503 is overloaded: the next model is faster than waiting
                if e.switch_model or attempt == max_retries or (overloaded >= 2 and model != models[-1]):
                    _cooldown[tag] = time.monotonic() + (QUOTA_COOLDOWN_S if e.switch_model else OVERLOAD_COOLDOWN_S)
                    on_status(f"{model} unavailable ({e}), trying next model…")
                    break
                wait = e.wait if e.wait is not None else min(5 * 2 ** attempt, 60)
                on_status(f"{model}: {e} · retrying in {wait:.0f}s ({attempt + 1}/{max_retries})")
                time.sleep(wait)
            except json.JSONDecodeError:
                last_err = f"{model}: returned invalid JSON"
                on_status(f"{model} returned invalid JSON, retrying…")
    raise LLMError(last_err)


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
    task: str | None = None,
    on_status: Callable[[str], None] = lambda m: None,
) -> tuple[dict, str]:
    """Returns (parsed JSON, model used). Raises LLMError.

    `models` are the calling app's (Gemini) models; configs/llm.yaml may route the task elsewhere."""
    req = Request(parts=[prompt] if isinstance(prompt, str) else list(prompt), schema=schema, system=system,
                  temperature=temperature, timeout_s=timeout_s)
    p, order = _resolve(task, req.has_media, models)
    provider = KINDS[p.kind](p)

    if cache_dir:  # an answer cached for the first model that would be asked
        for model in order:
            cache = cache_dir / f"{model.replace('/', '_')}-{_digest(p.name, model, req)}.json"
            if cache.exists() and not refresh:
                return json.loads(cache.read_text()), model

    def accept(text):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise json.JSONDecodeError("expected a JSON object", str(text)[:50], 0)
        return data

    data, model = _with_retries(p, order, lambda m: provider.generate(m, req), max_retries=max_retries,
                                min_interval_s=min_interval_s, on_status=on_status, accept=accept)
    if cache_dir:
        cache = cache_dir / f"{model.replace('/', '_')}-{_digest(p.name, model, req)}.json"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data))
    return data, model


def _digest(provider: str, model: str, req: Request) -> str:
    parts = [p if isinstance(p, str) else [p[0], hashlib.sha256(p[1]).hexdigest()] for p in req.parts]
    key = json.dumps([provider if provider != "gemini" else "", model, parts, req.schema, req.system, req.temperature],
                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def gemini_key_set() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())
