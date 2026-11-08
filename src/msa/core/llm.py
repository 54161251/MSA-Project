"""The one place the system talks to an LLM.

Every LLM step in every agent goes through `get_llm().complete_json(...)` and must pass a
`fallback`: a deterministic function producing the same JSON shape. In the default
"offline" mode the fallback *is* the answer, so the whole system runs and tests pass without
an API key. With MSA_LLM_PROVIDER=anthropic the model answers and the fallback is used only
if the call or the JSON parsing fails (that is recorded in llm_calls with ok=False).

G9: never put member names or emails in prompts; use ids.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from typing import Any

from msa.core.config import get_settings

Fallback = Callable[[], dict[str, Any]]


class LLM:
    provider = "offline"
    model = ""

    def complete_json(
        self, task: str, system: str, prompt: str, fallback: Fallback, max_tokens: int = 1500
    ) -> dict[str, Any]:
        out = fallback()
        _record(task, self.provider, self.model, 0, 0, True, "")
        return out


class AnthropicLLM(LLM):
    provider = "anthropic"

    def __init__(self, api_key: str, model: str):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key, max_retries=4)
        self.model = model

    def complete_json(self, task, system, prompt, fallback, max_tokens=1500):
        try:
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system + "\nRespond with one JSON object and nothing else.",
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(getattr(b, "text", "") for b in msg.content)
            _debug_dump(task, prompt, text)
            data = parse_json(text)
            _record(task, self.provider, self.model, msg.usage.input_tokens,
                    msg.usage.output_tokens, True, "")
            return data
        except Exception as exc:  # network, auth, JSON
            _record(task, self.provider, self.model, 0, 0, False, repr(exc)[:500])
            out = fallback()
            out["_fallback_used"] = True
            return out


class OpenAICompatibleLLM(LLM):
    """Any server that speaks the OpenAI chat-completions API: Ollama or LM Studio on your own
    computer, or hosted free tiers such as Google's Gemini API or Groq. Retries politely on
    rate limits (429), which free tiers hit often, and can space out calls
    (MSA_LLM_MIN_INTERVAL seconds) to stay under a requests-per-minute cap."""

    provider = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, model: str, min_interval: float = 0.0):
        import httpx

        self.base_url, self.model, self.min_interval = base_url, model, min_interval
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.http = httpx.Client(timeout=300, headers=headers)
        self._last = 0.0

    def complete_json(self, task, system, prompt, fallback, max_tokens=1500):
        import time

        body = {"model": self.model, "max_tokens": max_tokens, "temperature": 0.2,
                "messages": [{"role": "system",
                              "content": system + "\nRespond with one JSON object and nothing else."},
                             {"role": "user", "content": prompt}]}
        err = ""
        for attempt in range(6):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                self._last = time.time()
                r = self.http.post(f"{self.base_url}/chat/completions", json=body)
                if r.status_code in (429, 500, 502, 503, 529):
                    retry = float(r.headers.get("retry-after", 0) or 0)
                    time.sleep(max(retry, min(60, 2 ** attempt * 3)))
                    err = f"HTTP {r.status_code}: {r.text[:200]}"
                    continue
                r.raise_for_status()
                data = r.json()
                text = data["choices"][0]["message"]["content"] or ""
                _debug_dump(task, prompt, text)
                usage = data.get("usage") or {}
                out = parse_json(text)
                _record(task, self.provider, self.model, usage.get("prompt_tokens", 0),
                        usage.get("completion_tokens", 0), True, "")
                return out
            except Exception as exc:  # network, HTTP error, bad JSON
                err = repr(exc)[:500]
                break
        _record(task, self.provider, self.model, 0, 0, False, err)
        out = fallback()
        out["_fallback_used"] = True
        return out


class ScriptedLLM(LLM):
    """For tests: return canned answers per task (or the fallback when none is scripted)."""

    provider = "scripted"

    def __init__(self, answers: dict[str, Any] | None = None):
        self.answers = answers or {}
        self.calls: list[dict[str, str]] = []

    def complete_json(self, task, system, prompt, fallback, max_tokens=1500):
        self.calls.append({"task": task, "system": system, "prompt": prompt})
        ans = self.answers.get(task)
        if ans is None:
            return fallback()
        return ans(prompt) if callable(ans) else json.loads(json.dumps(ans))


def _debug_dump(task: str, prompt: str, reply: str) -> None:
    """MSA_LLM_DEBUG=1 saves every prompt and raw reply to var/llm_debug/ for troubleshooting.
    Prompts never contain keys, names or emails (G9)."""
    if os.environ.get("MSA_LLM_DEBUG", "").lower() not in ("1", "true", "on", "yes"):
        return
    import time

    from msa.core.config import VAR_DIR

    d = VAR_DIR / "llm_debug"
    d.mkdir(parents=True, exist_ok=True)
    name = f"{time.strftime('%H%M%S')}-{time.time_ns() % 10**6:06d}-{task}.txt"
    (d / name).write_text(f"TASK {task}\n\n--- PROMPT ---\n{prompt[:6000]}\n\n--- REPLY ---\n{reply}",
                          encoding="utf-8")


def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in reply")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("reply is not a JSON object")
    return data


def as_bool(v: Any) -> bool:
    """Models sometimes answer "false" (a string) instead of false."""
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1", "supported")
    return bool(v)


_tel_engine = None


def telemetry_engine():
    """Telemetry lives in its own SQLite file so recording an LLM call never waits on (or
    deadlocks with) the main database transaction the agent is in the middle of."""
    global _tel_engine
    if _tel_engine is None:
        from pathlib import Path

        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool

        from msa.core.config import VAR_DIR
        from msa.core.models import LLMCall

        url = os.environ.get("MSA_TELEMETRY_DB", f"sqlite:///{VAR_DIR / 'telemetry.db'}")
        kw: dict = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "memory"):
            url, kw["poolclass"] = "sqlite://", StaticPool
        else:
            Path(url.replace("sqlite:///", "", 1)).parent.mkdir(parents=True, exist_ok=True)
        _tel_engine = create_engine(url, **kw)
        LLMCall.__table__.create(_tel_engine, checkfirst=True)
    return _tel_engine


def _record(task, provider, model, tin, tout, ok, err) -> None:
    from sqlalchemy.orm import Session

    from msa.core.models import LLMCall

    try:
        with Session(telemetry_engine()) as s:
            s.add(LLMCall(task=task, provider=provider, model=model, input_tokens=tin,
                          output_tokens=tout, ok=ok, error=err, created_by="llm"))
            s.commit()
    except Exception:  # telemetry must never break an agent
        pass


_llm: LLM | None = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        s = get_settings()
        if s.llm_provider == "anthropic" and s.anthropic_api_key:
            _llm = AnthropicLLM(s.anthropic_api_key, s.llm_model)
        elif s.llm_provider in ("openai_compatible", "openai", "ollama", "gemini", "groq") \
                and s.llm_base_url:
            _llm = OpenAICompatibleLLM(s.llm_base_url, s.llm_api_key, s.llm_model, s.llm_min_interval)
        else:
            _llm = LLM()
    return _llm


def set_llm(llm: LLM | None) -> None:
    """Tests use this to install a ScriptedLLM; None resets to the configured provider."""
    global _llm
    _llm = llm
