"""LLM プロバイダ判定と、SDK 未導入時のエラー。"""

import pytest

from evaluation import llm
from evaluation.llm import LLMError, resolve_provider


@pytest.mark.parametrize(
    ("model", "provider"),
    [
        ("gemini-2.5-pro", "gemini"),
        ("gemini-2.5-flash", "gemini"),
        ("claude-opus-5", "anthropic"),
        ("claude-sonnet-4-6", "anthropic"),
        ("gpt-4o", "openai"),
        ("o3-mini", "openai"),
        ("anthropic:claude-opus-5", "anthropic"),
        ("openai:gpt-4o-mini", "openai"),
    ],
)
def test_resolve_provider(model, provider):
    assert resolve_provider(model) == provider


def test_resolve_provider_unknown():
    with pytest.raises(LLMError):
        resolve_provider("mistral-large")


def test_generate_json_routes_to_backend(monkeypatch):
    seen = {}

    def fake_backend(prompt, system, model, max_output_tokens, *, want_json, light=False):
        seen.update(prompt=prompt, system=system, model=model, want_json=want_json)
        return '{"ok": true}'

    monkeypatch.setitem(llm._BACKENDS, "gemini", fake_backend)
    resp = llm.generate_json("PROMPT", system="SYS", model="gemini-2.5-pro")

    assert resp.provider == "gemini"
    assert resp.model == "gemini-2.5-pro"
    assert resp.text == '{"ok": true}'
    assert seen["want_json"] is True
    assert seen["system"] == "SYS"


def test_generate_json_strips_provider_prefix(monkeypatch):
    seen = {}

    def fake_backend(prompt, system, model, max_output_tokens, *, want_json, light=False):
        seen["model"] = model
        return "{}"

    monkeypatch.setitem(llm._BACKENDS, "anthropic", fake_backend)
    resp = llm.generate_json("p", model="anthropic:claude-opus-5")
    assert seen["model"] == "claude-opus-5"
    assert resp.model == "claude-opus-5"
