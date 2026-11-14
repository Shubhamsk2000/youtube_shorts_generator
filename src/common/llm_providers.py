"""AI providers: the ONE place that knows how to talk to each LLM service.

To try another provider you normally only edit configs/llm.yaml (see the comments there). To support a
completely new kind of API, add a class here with the same `generate` method as the others and register it in
KINDS at the bottom; nothing else in the app changes.

Every provider turns a Request into the model's raw text answer (JSON text for generate_json) and raises:
  Retryable  - try again (rate limit, overload, network); switch_model=True: give up on this model now
  LLMError   - retrying won't help (bad key, bad request)
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field


class LLMError(RuntimeError):
    """The AI provider could not produce an answer (after retries and fallbacks)."""


class Retryable(Exception):
    def __init__(self, msg: str, wait: float | None = None, switch_model: bool = False, overloaded: bool = False):
        super().__init__(msg)
        self.wait, self.switch_model, self.overloaded = wait, switch_model, overloaded


Part = str | tuple[str, bytes]   # text, or (mime type, bytes) for inline audio / video / images


@dataclass
class Request:
    parts: list[Part]
    schema: dict | None = None        # Gemini-style JSON schema ("type": "OBJECT" ...); None = plain text
    system: str | None = None
    temperature: float = 0.4
    timeout_s: float = 180

    @property
    def has_media(self) -> bool:
        return any(not isinstance(p, str) for p in self.parts)

    @property
    def text(self) -> str:
        return "\n\n".join(p for p in self.parts if isinstance(p, str))


@dataclass
class ProviderCfg:
    """One entry under `providers:` in configs/llm.yaml."""
    name: str
    kind: str = "gemini"                      # gemini | openai (any OpenAI-compatible endpoint)
    base_url: str | None = None
    api_key_env: str | None = None            # name of the .env variable that holds the key
    models: list[str] = field(default_factory=list)
    min_interval_s: float | None = None       # spacing between calls (free tiers); None = caller's value
    json_mode: str = "object"                 # openai kind: object | schema | none
    extra_body: dict = field(default_factory=dict)   # merged into every request (provider-specific options)
    headers: dict = field(default_factory=dict)

    def key(self) -> str:
        if not self.api_key_env:
            return ""
        k = os.getenv(self.api_key_env, "").strip()
        if not k:
            raise LLMError(f"{self.api_key_env} is not set (add it to .env) for the '{self.name}' provider")
        return k


# ------------------------------------------------------------------ HTTP helper

def _post(url: str, body: dict, headers: dict, timeout_s: float, who: str) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            err = json.loads(raw)
            err = err.get("error", err) if isinstance(err, dict) else {"message": str(err)}
            if isinstance(err, str):
                err = {"message": err}
        except json.JSONDecodeError:
            err = {"message": raw[:300]}
        msg = str(err.get("message", "")).split("\n")[0][:200]
        if e.code == 429:
            daily = bool(re.search(r"per ?day|PerDay|daily|quota", raw, re.I)) and "minute" not in raw.lower()
            raise Retryable(f"rate limited{' (daily quota used up)' if daily else ''}",
                            wait=_retry_delay(err, e.headers), switch_model=daily) from None
        if e.code in (500, 502, 503, 504, 529):
            raise Retryable(f"HTTP {e.code} {err.get('status', '')}".strip(), overloaded=True) from None
        if e.code == 404:
            raise Retryable("model not found", switch_model=True) from None
        raise LLMError(f"{who} HTTP {e.code}: {msg}") from None   # 400/401/403: retrying won't help
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        raise Retryable(f"network: {getattr(e, 'reason', e)}",
                        overloaded=isinstance(e, (socket.timeout, TimeoutError)) or "timed out" in str(e)) from None


def _retry_delay(err: dict, headers=None) -> float | None:
    for d in err.get("details", []) if isinstance(err.get("details"), list) else []:
        if str(d.get("@type", "")).endswith("RetryInfo") and (m := re.match(r"([\d.]+)s", d.get("retryDelay", ""))):
            return min(float(m.group(1)) + 1, 120)
    if headers and (ra := headers.get("retry-after")):
        try:
            return min(float(ra) + 1, 120)
        except ValueError:
            pass
    return None


# ------------------------------------------------------------------ Gemini

class GeminiProvider:
    """Google AI Studio (generativelanguage.googleapis.com). Takes text, audio, video and images."""
    supports_media = True
    API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:{method}"

    def __init__(self, cfg: ProviderCfg):
        self.cfg = cfg

    def _url(self, model: str, method: str = "generateContent") -> str:
        base = self.cfg.base_url.rstrip("/") + "/models/{model}:{method}" if self.cfg.base_url else self.API
        return base.format(model=model, method=method)

    def generate(self, model: str, req: Request) -> str:
        body: dict = {
            "contents": [{"role": "user", "parts": _gemini_parts(req.parts)}],
            "generationConfig": {"temperature": req.temperature},
        }
        if req.schema:
            body["generationConfig"].update(responseMimeType="application/json", responseSchema=req.schema)
        if req.system:
            body["systemInstruction"] = {"parts": [{"text": req.system}]}
        resp = _post(self._url(model), body, {"x-goog-api-key": self.cfg.key(), **self.cfg.headers},
                     req.timeout_s, "Gemini")
        cands = resp.get("candidates") or []
        if not cands:
            block = (resp.get("promptFeedback") or {}).get("blockReason")
            raise Retryable(f"empty response{f' (blocked: {block})' if block else ''}")
        c = cands[0]
        parts = (c.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if c.get("finishReason") not in (None, "STOP") and not text:
            raise Retryable(f"finish reason {c.get('finishReason')}")
        return text



def _gemini_parts(parts: list[Part]) -> list[dict]:
    out = []
    for it in parts:
        if isinstance(it, str):
            out.append({"text": it})
        else:
            mime, data = it
            out.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}})
    return out


# ------------------------------------------------------------------ OpenAI-compatible

class OpenAICompatProvider:
    """Any /v1/chat/completions endpoint: NVIDIA NIM (build.nvidia.com), OpenRouter, Groq, Together,
    DeepSeek, Mistral, OpenAI, a local Ollama or LM Studio... Text only (audio/video calls go to `media:`)."""
    supports_media = False

    def __init__(self, cfg: ProviderCfg):
        if not cfg.base_url:
            raise LLMError(f"provider '{cfg.name}' needs a base_url in configs/llm.yaml")
        self.cfg = cfg

    def generate(self, model: str, req: Request) -> str:
        if req.has_media:
            raise LLMError(f"provider '{self.cfg.name}' can't take audio/video (set media: to gemini)")
        system = req.system or ""
        if req.schema:
            system += ("\n\nReply with ONE JSON object only (no markdown, no comments) that matches this JSON "
                       "schema:\n" + json.dumps(openai_schema(req.schema), ensure_ascii=False))
        msgs = ([{"role": "system", "content": system.strip()}] if system.strip() else []) + \
               [{"role": "user", "content": req.text}]
        body: dict = {"model": model, "messages": msgs, "temperature": req.temperature, **self.cfg.extra_body}
        if req.schema and self.cfg.json_mode == "object":
            body["response_format"] = {"type": "json_object"}
        elif req.schema and self.cfg.json_mode == "schema":
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "answer", "schema": openai_schema(req.schema), "strict": False}}
        headers = dict(self.cfg.headers)
        if key := self.cfg.key():
            headers["Authorization"] = f"Bearer {key}"
        resp = _post(self.cfg.base_url.rstrip("/") + "/chat/completions", body, headers, req.timeout_s, self.cfg.name)
        choices = resp.get("choices") or []
        if not choices:
            raise Retryable("empty response")
        text = (choices[0].get("message") or {}).get("content") or ""
        if not text.strip():
            raise Retryable(f"empty answer (finish reason {choices[0].get('finish_reason')})")
        return extract_json(text) if req.schema else text



def openai_schema(s: dict) -> dict:
    """Gemini schema ("type": "OBJECT") -> standard JSON schema ("type": "object")."""
    if not isinstance(s, dict):
        return s
    out = {}
    for k, v in s.items():
        if k == "type" and isinstance(v, str):
            out[k] = v.lower()
        elif k == "properties" and isinstance(v, dict):
            out[k] = {pk: openai_schema(pv) for pk, pv in v.items()}
        elif k == "items":
            out[k] = openai_schema(v)
        elif k in ("propertyOrdering", "nullable"):
            continue
        else:
            out[k] = v
    return out


def extract_json(text: str) -> str:
    """Models without a strict JSON mode wrap the answer in ```json fences or chatter: keep the JSON."""
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()   # reasoning models
    if m := re.search(r"```(?:json)?\s*(.*?)```", t, re.S):
        t = m.group(1).strip()
    if t[:1] not in "{[":
        starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
        if starts:
            t = t[min(starts):]
    if t[:1] in "{[":
        close = "}" if t[0] == "{" else "]"
        t = t[: t.rfind(close) + 1] or t
    return t


KINDS = {"gemini": GeminiProvider, "openai": OpenAICompatProvider}
