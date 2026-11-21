"""The app's single doorway to AI models: JSON answers (generate_json).

Which provider and model answer is decided by configs/llm.yaml, per task, so you can switch to NVIDIA,
OpenRouter, Groq, a local Ollama... without touching the apps. How each provider is called lives in
common/llm_providers.py. This file adds what every provider needs: retries with backoff, model fallback,
cool-downs for models that are out of quota, call spacing for free tiers and an on-disk answer cache.

Switching, in order: each model of the task's provider (with each of its keys: GEMINI_API_KEY, GEMINI_API_KEY_2...),
then the other providers that have a key set (`fallback:` in llm.yaml). A retired model (410), a refused key or a
used-up quota is skipped at once and cools down, so the next call doesn't wait for it again.

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

__all__ = ["LLMError", "Part", "generate_json", "route_info", "load_llm_config", "model_choices", "set_task_model",
           "any_key_set"]

CONFIG_FILE = CONFIG_DIR / "llm.yaml"
LOCAL_FILE = CONFIG_DIR / "llm.local.yaml"   # model picked in the UI (overrides llm.yaml tasks)
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
    picked: bool = False   # chosen in the UI: the provider's other models are kept as fallbacks


@dataclass
class LLMConfig:
    default: Route = field(default_factory=Route)
    media: Route = field(default_factory=Route)
    tasks: dict[str, Route] = field(default_factory=dict)
    providers: dict[str, ProviderCfg] = field(default_factory=lambda: {
        "gemini": ProviderCfg("gemini", "gemini", api_key_env="GEMINI_API_KEY")})
    fallback: list[str] | None = None   # providers tried after the route's own; None = every other one with a key


_cfg_cache: dict = {"mtime": None, "cfg": None}


def load_llm_config() -> LLMConfig:
    """configs/llm.yaml (+ llm.local.yaml from the UI), re-read when either changes (no restart needed)."""
    def _mt(f: Path):
        try:
            return f.stat().st_mtime
        except FileNotFoundError:
            return None
    mtime = (_mt(CONFIG_FILE), _mt(LOCAL_FILE))
    if _cfg_cache["cfg"] is not None and _cfg_cache["mtime"] == mtime:
        return _cfg_cache["cfg"]
    cfg = LLMConfig()
    def route(d, picked: bool = False) -> Route:
        d = d or {}
        return Route(str(d.get("provider", "gemini")), [str(m) for m in d.get("models") or []], picked)

    if mtime[0] is not None:
        raw = yaml.safe_load(CONFIG_FILE.read_text()) or {}

        cfg.default = route(raw.get("default"))
        cfg.media = route(raw.get("media"))
        cfg.tasks = {k: route(v) for k, v in (raw.get("tasks") or {}).items() if v}
        fb = raw.get("fallback", "auto")
        cfg.fallback = None if fb in (None, "auto") else [] if fb in ("none", False) else [str(x) for x in fb]
        for name, p in (raw.get("providers") or {}).items():
            p = p or {}
            cfg.providers[name] = ProviderCfg(
                name=name, kind=p.get("kind", "gemini"), base_url=p.get("base_url"),
                api_key_env=p.get("api_key_env"), models=[str(m) for m in p.get("models") or []],
                min_interval_s=p.get("min_interval_s"), json_mode=p.get("json_mode", "object"),
                extra_body=p.get("extra_body") or {}, headers=p.get("headers") or {})
    if mtime[1] is not None:
        local = yaml.safe_load(LOCAL_FILE.read_text()) or {}
        cfg.tasks.update({k: route(v, picked=True) for k, v in (local.get("tasks") or {}).items() if v})
    _cfg_cache.update(mtime=mtime, cfg=cfg)
    return cfg


def _resolve(task: str | None, has_media: bool, caller_models: list[str]) -> tuple[ProviderCfg, list[str]]:
    """The provider and models the task asks first."""
    cfg = load_llm_config()
    route = cfg.tasks.get(task or "") or (cfg.media if has_media else cfg.default)
    p = cfg.providers.get(route.provider)
    if p is None:
        raise LLMError(f"configs/llm.yaml: unknown provider '{route.provider}'")
    if p.kind not in KINDS:
        raise LLMError(f"configs/llm.yaml: provider '{p.name}' has unknown kind '{p.kind}'")
    own = _own_models(p, caller_models)
    models = route.models or own
    if route.picked:
        models = [*route.models, *own]   # UI pick first, the rest still catch quota / overload
    if not models:
        raise LLMError(f"configs/llm.yaml: no models for provider '{p.name}' (task {task or 'default'})")
    return p, list(dict.fromkeys(models))


def _own_models(p: ProviderCfg, caller_models: list[str]) -> list[str]:
    # the apps' own model lists are Gemini model names: only valid when Gemini answers
    return list(dict.fromkeys((caller_models if p.kind == "gemini" else []) or p.models))


def _chain(task: str | None, has_media: bool, caller_models: list[str]) -> list[tuple[ProviderCfg, list[str]]]:
    """The task's provider first, then the fallback providers: (provider, models) each."""
    cfg = load_llm_config()
    first, models = _resolve(task, has_media, caller_models)
    names = cfg.fallback if cfg.fallback is not None else [
        n for n, q in cfg.providers.items() if q.api_key_env and q.has_key()]  # keyless (local) ones: only if listed
    out = [(first, models)]
    for n in names:
        q = cfg.providers.get(n)
        if q is None or q.name == first.name or q.kind not in KINDS or (has_media and q.kind != "gemini"):
            continue
        if (ms := _own_models(q, caller_models)) and q.has_key():
            out.append((q, ms))
    return out


def any_key_set() -> bool:
    """At least one AI provider can be asked (a key is set)."""
    return any(p.api_key_env and p.has_key() for p in load_llm_config().providers.values())


def route_info(task: str | None = None, has_media: bool = False, caller_models: list[str] | None = None) -> dict:
    """What would answer this task: for status pages."""
    try:
        chain = _chain(task, has_media, caller_models or ["gemini-3.8-flash"])
        p, models = chain[0]
        return {"provider": p.name, "kind": p.kind, "models": models, "key_set": p.has_key(),
                "keys": len(p.keys()) if p.has_key() else 0,
                "fallbacks": [{"provider": q.name, "models": ms} for q, ms in chain[1:]]}
    except LLMError as e:
        return {"provider": None, "error": str(e), "key_set": False}


def model_choices(caller_models: list[str]) -> list[dict]:
    """Every provider/model the UI can offer: Gemini with the app's models, the rest with their llm.yaml list."""
    out = []
    for p in load_llm_config().providers.values():
        if p.kind not in KINDS:
            continue
        key_ok = p.has_key()
        for m in dict.fromkeys(caller_models if p.kind == "gemini" else p.models):
            out.append({"provider": p.name, "model": m, "key_set": key_ok})
    return out


def picked_model(task: str) -> dict | None:
    r = load_llm_config().tasks.get(task)
    return {"provider": r.provider, "model": r.models[0] if r.models else None} if r and r.picked else None


def set_task_model(tasks: list[str], provider: str | None, model: str | None) -> None:
    """Save the UI's pick for these tasks to configs/llm.local.yaml; provider=None goes back to llm.yaml."""
    cfg = load_llm_config()
    if provider is not None and provider not in cfg.providers:
        raise LLMError(f"unknown provider '{provider}'")
    local = (yaml.safe_load(LOCAL_FILE.read_text()) or {}) if LOCAL_FILE.exists() else {}
    t = local.setdefault("tasks", {}) or {}
    for name in tasks:
        if provider is None:
            t.pop(name, None)
        else:
            t[name] = {"provider": provider, "models": [model] if model else []}
    local["tasks"] = t
    LOCAL_FILE.write_text("# Written by Studio's model picker. Delete this file to use configs/llm.yaml only.\n"
                          + yaml.safe_dump(local, sort_keys=False))


# ------------------------------------------------------------------ calls

def _space(provider: str, min_interval_s: float) -> None:
    with _lock:
        gap = min_interval_s - (time.monotonic() - _last_call.get(provider, 0.0))
        if gap > 0:
            time.sleep(gap)
        _last_call[provider] = time.monotonic()


@dataclass
class _Try:
    p: ProviderCfg
    model: str
    key_i: int
    key: str

    @property
    def tag(self) -> str:        # cool-down of this model with this key (quota is per key and model)
        return f"{self.p.name}/{self.model}" + (f"#{self.key_i}" if self.key_i else "")

    @property
    def key_tag(self) -> str:    # cool-down of a refused key, for every model
        return f"{self.p.name}#key{self.key_i}"

    @property
    def label(self) -> str:
        return f"{self.p.name}: {self.model}" + (f" (key {self.key_i + 1})" if self.key_i else "")


def _tries(chain: list[tuple[ProviderCfg, list[str]]]) -> list[_Try]:
    out = []
    for p, models in chain:
        keys = p.keys()
        for m in models:
            out += [_Try(p, m, i, k) for i, k in enumerate(keys)]
    return out


def _cooling(t: _Try) -> bool:
    now = time.monotonic()
    return _cooldown.get(t.tag, 0) > now or _cooldown.get(t.key_tag, 0) > now


def _with_retries(tries: list[_Try], call: Callable[[_Try], object], *, max_retries: int,
                  min_interval_s: float, on_status: Callable[[str], None], accept: Callable[[object], object]):
    errors: list[str] = []
    for n, t in enumerate(tries):
        last = n == len(tries) - 1
        if _cooling(t) and not last:
            continue  # failed moments ago: don't spend two more timeouts finding that out
        interval = t.p.min_interval_s if t.p.min_interval_s is not None else min_interval_s
        overloaded = 0
        err = None
        for attempt in range(max_retries + 1):
            _space(t.p.name, interval)
            try:
                return accept(call(t)), t
            except Retryable as e:
                err = str(e)
                if e.bad_key:  # this key is refused for every model: skip it everywhere
                    _cooldown[t.key_tag] = time.monotonic() + QUOTA_COOLDOWN_S
                overloaded = overloaded + 1 if e.overloaded else 0
                # a model that keeps timing out / returning 503 is overloaded: the next one is faster than waiting
                if e.switch_model or attempt == max_retries or (overloaded >= 2 and not last):
                    _cooldown[t.tag] = time.monotonic() + (QUOTA_COOLDOWN_S if e.switch_model else OVERLOAD_COOLDOWN_S)
                    nxt = next((u for u in tries[n + 1:] if not _cooling(u)), None) or (tries[-1] if not last else None)
                    if nxt:
                        on_status(f"{t.label} unavailable ({e}), trying {nxt.label}…")
                    break
                wait = e.wait if e.wait is not None else min(5 * 2 ** attempt, 60)
                on_status(f"{t.label}: {e} · retrying in {wait:.0f}s ({attempt + 1}/{max_retries})")
                time.sleep(wait)
            except json.JSONDecodeError:
                err = "returned invalid JSON"
                on_status(f"{t.label} returned invalid JSON, retrying…")
            except LLMError as e:  # a request this provider can't take: the next one may
                err = str(e)
                break
        if err:
            errors.append(f"{t.label}: {err}")
    if not errors:
        raise LLMError("no models configured")
    raise LLMError("; ".join(errors[-4:]) if len(errors) <= 4 else f"{len(errors)} models failed, last: " + "; ".join(errors[-3:]))


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
    chain = _chain(task, req.has_media, models)
    tries = _tries(chain)
    providers = {p.name: KINDS[p.kind](p) for p, _ in chain}

    if cache_dir and not refresh:
        # Reuse the answer of the model that would be asked now. A fallback's old answer only counts while the
        # models before it are cooling down (quota / overload): otherwise picking another model in the UI would
        # silently return the previous model's cached clips.
        for p, order in chain:
            stop = False
            for model in order:
                cache = cache_dir / f"{model.replace('/', '_')}-{_digest(p.name, model, req)}.json"
                if cache.exists():
                    return json.loads(cache.read_text()), model
                if not all(_cooling(t) for t in tries if t.p.name == p.name and t.model == model):
                    stop = True
                    break
            if stop:
                break

    def accept(text):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise json.JSONDecodeError("expected a JSON object", str(text)[:50], 0)
        return data

    data, t = _with_retries(tries, lambda t: providers[t.p.name].generate(t.model, req, t.key),
                            max_retries=max_retries, min_interval_s=min_interval_s, on_status=on_status, accept=accept)
    model = t.model
    if cache_dir:
        cache = cache_dir / f"{model.replace('/', '_')}-{_digest(t.p.name, model, req)}.json"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data))
    return data, model


def _digest(provider: str, model: str, req: Request) -> str:
    parts = [p if isinstance(p, str) else [p[0], hashlib.sha256(p[1]).hexdigest()] for p in req.parts]
    key = json.dumps([provider if provider != "gemini" else "", model, parts, req.schema, req.system, req.temperature],
                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def gemini_key_set() -> bool:
    p = load_llm_config().providers.get("gemini")
    return bool(p and p.has_key()) or bool(os.getenv("GEMINI_API_KEY", "").strip())
