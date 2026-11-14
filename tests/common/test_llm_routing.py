"""configs/llm.yaml routing and the OpenAI-compatible adapters."""
import json

import pytest

from common import llm
from common.llm_providers import LLMError, extract_json, openai_schema


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    f = tmp_path / "llm.yaml"
    monkeypatch.setattr(llm, "CONFIG_FILE", f)
    llm._cfg_cache.update(mtime=None, cfg=None)

    def write(text: str):
        f.write_text(text)
        llm._cfg_cache.update(mtime=None, cfg=None)
    return write


def test_defaults_use_the_apps_own_gemini_models(cfg):
    cfg("default: {provider: gemini}\n")
    p, models = llm._resolve("shorts.select", False, ["gemini-a", "gemini-b"])
    assert p.kind == "gemini" and models == ["gemini-a", "gemini-b"]


def test_a_task_can_go_to_another_provider(cfg):
    cfg("""
tasks:
  shorts.select: {provider: nvidia}
providers:
  nvidia: {kind: openai, base_url: https://example.test/v1, api_key_env: NV_KEY, models: [meta/llama]}
""")
    p, models = llm._resolve("shorts.select", False, ["gemini-a"])
    assert p.name == "nvidia" and models == ["meta/llama"]
    p2, models2 = llm._resolve("anime.select", False, ["gemini-a"])
    assert p2.kind == "gemini" and models2 == ["gemini-a"]


def test_media_calls_use_the_media_route(cfg):
    cfg("""
default: {provider: groq}
media: {provider: gemini}
providers:
  groq: {kind: openai, base_url: https://example.test/v1, models: [llama]}
""")
    assert llm._resolve(None, True, ["gemini-x"])[0].kind == "gemini"
    assert llm._resolve(None, False, ["gemini-x"])[0].name == "groq"


def test_unknown_provider_is_a_clear_error(cfg):
    cfg("default: {provider: nope}\n")
    with pytest.raises(LLMError, match="unknown provider"):
        llm._resolve(None, False, ["m"])


def test_extract_json_from_chatty_answers():
    assert json.loads(extract_json('Sure!\n```json\n{"a": 1}\n```\nHope it helps')) == {"a": 1}
    assert json.loads(extract_json('<think>hmm {x}</think>{"b": [1, 2]}')) == {"b": [1, 2]}
    assert json.loads(extract_json('The answer is {"c": "}"} ok')) == {"c": "}"}


def test_gemini_schema_becomes_json_schema():
    s = openai_schema({"type": "OBJECT", "properties": {"x": {"type": "ARRAY", "items": {"type": "STRING"}},
                                                         "y": {"type": "STRING", "enum": ["a"]}}, "required": ["x"]})
    assert s == {"type": "object", "properties": {"x": {"type": "array", "items": {"type": "string"}},
                                                  "y": {"type": "string", "enum": ["a"]}}, "required": ["x"]}
