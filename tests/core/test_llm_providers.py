import json

import httpx

from msa.core import llm


def test_openai_compatible_retries_rate_limits_then_parses(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, text="slow down")
        return httpx.Response(200, json={"choices": [{"message": {"content": '```json\n{"ok": 1}\n```'}}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 2}})

    monkeypatch.setattr("time.sleep", lambda s: None)
    m = llm.OpenAICompatibleLLM("http://local/v1", "", "test-model")
    m.http = httpx.Client(transport=httpx.MockTransport(handler))
    out = m.complete_json("t", "sys", "prompt", lambda: {"ok": 0})
    assert out == {"ok": 1} and len(calls) == 2
    assert calls[1]["model"] == "test-model" and calls[1]["messages"][0]["role"] == "system"


def test_openai_compatible_falls_back_on_errors(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    m = llm.OpenAICompatibleLLM("http://local/v1", "k", "x")
    m.http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="bad key")))
    out = m.complete_json("t", "sys", "prompt", lambda: {"ok": 0})
    assert out["ok"] == 0 and out["_fallback_used"]


def test_provider_selection(monkeypatch):
    monkeypatch.setenv("MSA_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("MSA_LLM_BASE_URL", "http://localhost:11434/v1")
    llm.set_llm(None)
    try:
        assert isinstance(llm.get_llm(), llm.OpenAICompatibleLLM)
    finally:
        llm.set_llm(None)
        monkeypatch.setenv("MSA_LLM_PROVIDER", "offline")
