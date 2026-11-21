"""configs/llm.yaml routing and the OpenAI-compatible adapters."""
import json

import pytest

from common import llm
from common.llm_providers import LLMError, extract_json, openai_schema


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    f = tmp_path / "llm.yaml"
    monkeypatch.setattr(llm, "CONFIG_FILE", f)
    monkeypatch.setattr(llm, "LOCAL_FILE", tmp_path / "llm.local.yaml")  # not the model picked in your Studio
    for k in list(llm.os.environ):
        if k.startswith(("GEMINI_API_KEY", "NVIDIA_API_KEY", "NV_KEY")):
            monkeypatch.delenv(k)  # never fall back to a real provider from a test
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
    p2, models2 = llm._resolve("shorts.other", False, ["gemini-a"])
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


@pytest.fixture
def fake_provider(monkeypatch):
    """Records which model generate_json asked; answers {"from": <model>}."""
    asked: list[str] = []

    class Fake:
        supports_media = False

        def __init__(self, cfg):
            pass

        def generate(self, model, req, key=None):
            asked.append(model)
            return json.dumps({"from": model})

    monkeypatch.setitem(llm.KINDS, "openai", Fake)
    llm._cooldown.clear()
    return asked


def test_picking_a_model_in_the_ui_does_not_reuse_the_old_models_cached_answer(cfg, fake_provider, tmp_path,
                                                                                monkeypatch):
    local = tmp_path / "llm.local.yaml"
    monkeypatch.setattr(llm, "LOCAL_FILE", local)
    cfg("""
tasks:
  shorts.select: {provider: nvidia}
providers:
  nvidia: {kind: openai, base_url: https://example.test/v1, models: [nemotron, deepseek], min_interval_s: 0}
""")
    cache = tmp_path / "cache"
    call = dict(schema={"type": "OBJECT"}, models=["gemini-a"], cache_dir=cache, task="shorts.select", min_interval_s=0)

    assert llm.generate_json("pick moments", **call) == ({"from": "nemotron"}, "nemotron")
    assert llm.generate_json("pick moments", **call) == ({"from": "nemotron"}, "nemotron")   # cached
    assert fake_provider == ["nemotron"]

    llm.set_task_model(["shorts.select"], "nvidia", "deepseek")   # the UI's pick: deepseek first, nemotron fallback
    assert llm._resolve("shorts.select", False, ["gemini-a"])[1] == ["deepseek", "nemotron"]
    assert llm.generate_json("pick moments", **call) == ({"from": "deepseek"}, "deepseek")
    assert fake_provider == ["nemotron", "deepseek"]

    # a fallback's cached answer is still reused while the picked model is cooling down (quota / overload)
    (cache / f"deepseek-{llm._digest('nvidia', 'deepseek', llm.Request(parts=['pick moments'], schema={'type': 'OBJECT'}, system=None, temperature=0.4, timeout_s=180))}.json").unlink()
    llm._cooldown["nvidia/deepseek"] = llm.time.monotonic() + 60
    assert llm.generate_json("pick moments", **call) == ({"from": "nemotron"}, "nemotron")
    assert fake_provider == ["nemotron", "deepseek"]


@pytest.fixture
def scripted(monkeypatch):
    """Providers whose answers are scripted per "provider/model#key": an exception to raise, or a JSON answer."""
    from common.llm_providers import Retryable
    script: dict[str, object] = {}
    asked: list[str] = []

    def make(kind):
        class P:
            def __init__(self, cfg):
                self.cfg = cfg

            def generate(self, model, req, key=None):
                tag = f"{self.cfg.name}/{model}#{key}"
                asked.append(tag)
                r = script.get(tag, {"from": tag})
                if isinstance(r, Exception):
                    raise r
                return json.dumps(r)
        return P

    monkeypatch.setitem(llm.KINDS, "openai", make("openai"))
    monkeypatch.setitem(llm.KINDS, "gemini", make("gemini"))
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    llm._cooldown.clear()
    return script, asked, Retryable


TWO = """
tasks:
  shorts.select: {provider: nvidia}
providers:
  gemini: {kind: gemini, api_key_env: GEMINI_API_KEY, min_interval_s: 0}
  nvidia: {kind: openai, base_url: https://example.test/v1, api_key_env: NV_KEY, models: [nemo, deep], min_interval_s: 0}
"""


def test_falls_back_to_the_other_provider_when_one_fails(cfg, scripted, monkeypatch):
    script, asked, Retryable = scripted
    monkeypatch.setenv("NV_KEY", "n1")
    monkeypatch.setenv("GEMINI_API_KEY", "g1")
    cfg(TWO)
    script["nvidia/nemo#n1"] = Retryable("model retired (HTTP 410)", switch_model=True)
    script["nvidia/deep#n1"] = Retryable("HTTP 503", overloaded=True)
    data, model = llm.generate_json("x", {"type": "OBJECT"}, models=["gem-a"], task="shorts.select", max_retries=1)
    assert model == "gem-a" and data == {"from": "gemini/gem-a#g1"}
    assert asked == ["nvidia/nemo#n1", "nvidia/deep#n1", "nvidia/deep#n1", "gemini/gem-a#g1"]
    # the retired model cools down: the next call doesn't ask it again
    asked.clear()
    llm.generate_json("y", {"type": "OBJECT"}, models=["gem-a"], task="shorts.select", max_retries=1)
    assert "nvidia/nemo#n1" not in asked


def test_a_second_key_is_tried_on_quota_and_a_refused_key_is_skipped(cfg, scripted, monkeypatch):
    script, asked, Retryable = scripted
    monkeypatch.setenv("GEMINI_API_KEY", "g1")
    monkeypatch.setenv("GEMINI_API_KEY_2", "g2")
    cfg("default: {provider: gemini}\nfallback: none\n")
    script["gemini/gem-a#g1"] = Retryable("rate limited (daily quota used up)", switch_model=True)
    _, model = llm.generate_json("x", {"type": "OBJECT"}, models=["gem-a", "gem-b"])
    assert asked == ["gemini/gem-a#g1", "gemini/gem-a#g2"] and model == "gem-a"

    llm._cooldown.clear()
    asked.clear()
    script["gemini/gem-a#g1"] = Retryable("API key refused (HTTP 403)", switch_model=True, bad_key=True)
    script["gemini/gem-a#g2"] = Retryable("HTTP 503", overloaded=True)
    script["gemini/gem-b#g2"] = Retryable("HTTP 503", overloaded=True)
    with pytest.raises(LLMError, match="gem-b"):
        llm.generate_json("y", {"type": "OBJECT"}, models=["gem-a", "gem-b"], max_retries=0)
    assert "gemini/gem-b#g1" not in asked  # the refused key isn't tried with the next model


def test_fallback_needs_a_key_and_can_be_turned_off(cfg, monkeypatch):
    monkeypatch.setenv("NV_KEY", "n1")
    cfg(TWO)
    assert [p.name for p, _ in llm._chain("shorts.select", False, ["gem-a"])] == ["nvidia"]  # no Gemini key
    monkeypatch.setenv("GEMINI_API_KEY", "g1")
    assert [p.name for p, _ in llm._chain("shorts.select", False, ["gem-a"])] == ["nvidia", "gemini"]
    assert llm._chain("shorts.select", True, ["gem-a"])[0][0].name == "nvidia"  # (media goes by its own route)
    cfg(TWO + "fallback: none\n")
    assert [p.name for p, _ in llm._chain("shorts.select", False, ["gem-a"])] == ["nvidia"]


def test_http_410_and_refused_keys_switch_at_once():
    import io
    import urllib.error
    from common import llm_providers as lp

    def boom(code, body=b"{}"):
        def opener(*a, **k):
            raise urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body))
        return opener

    for code, attr in ((410, "switch_model"), (401, "bad_key"), (403, "bad_key")):
        lp.urllib.request.urlopen, orig = boom(code), lp.urllib.request.urlopen
        try:
            with pytest.raises(lp.Retryable) as e:
                lp._post("http://x", {}, {}, 5, "t")
            assert getattr(e.value, attr)
        finally:
            lp.urllib.request.urlopen = orig
