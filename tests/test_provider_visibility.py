"""Providers added in Settings must be visible to the assistant.

Regression test for the split-store bug: Settings wrote providers into
``api_keys.json`` (``custom_providers``) while the assistant's
``dynamic_api_configuration`` skill read a separate ``dynamic_providers.json``
and answered "0 registered dynamic providers" - so the user was told the app
could not see providers that were plainly listed on the Settings screen.
"""

import importlib
import json

import pytest

from core import provider_registry as registry


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """Point the registry (and the legacy file) at throwaway paths."""
    monkeypatch.setattr(registry, "api_keys_path", lambda: tmp_path / "api_keys.json")
    monkeypatch.setattr(registry, "_repo_keys_path", lambda: tmp_path / "repo_keys.json")
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    return tmp_path


def _load_skill_module():
    """Load the skill by path: features/__init__ imports every feature, and
    some of those need packages the app only has installed on the user's PC."""
    import importlib.util
    from pathlib import Path

    path = (Path(__file__).resolve().parent.parent
            / "features" / "dynamic_api_configuration" / "skill.py")
    spec = importlib.util.spec_from_file_location("qa_dynamic_api_skill", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def skill(tmp_path, monkeypatch):
    module = _load_skill_module()
    monkeypatch.setattr(module, "_get_config_path", lambda: str(tmp_path / "dynamic_providers.json"))
    return module


@pytest.fixture()
def store_providers(store):
    """Two registered providers, in registry order."""
    _settings_provider("Agnes AI", "https://api.agnes-ai.com/v1")
    _settings_provider("AINative Studio", "https://api.ainative.studio/api/v1")
    return registry.configured_providers


def _settings_provider(name="Agnes AI", base_url="https://api.agnes-ai.com/v1"):
    """Exactly what the Settings dialog saves."""
    return registry.save_custom_provider({
        "name": name,
        "kind": "openai",
        "base_url": base_url,
        "api_key": "sk-agnes-123",
        "model": "agnes-2.0-flashfree",
        "caps": ["chat", "vision", "coding"],
        "enabled": True,
    })


# -- the assistant sees what Settings saved ---------------------------------

def test_status_reports_providers_added_in_settings(store, skill):
    _settings_provider()

    result = skill.execute(action="list")

    assert result["status"] == "success"
    assert "Agnes AI" in result["summary"]
    assert "0 registered" not in result["summary"]
    assert "agnes-ai" in result["summary"].lower() or "Agnes AI" in result["providers"]


def test_status_lists_every_settings_provider(store, skill):
    for name, url in (
        ("Agnes AI", "https://api.agnes-ai.com/v1"),
        ("AINative Studio", "https://api.ainative.studio/api/v1"),
        ("OmniRoute", "http://localhost:20128/v1"),
    ):
        _settings_provider(name, url)

    result = skill.execute(action="status")

    for name in ("Agnes AI", "AINative Studio", "OmniRoute"):
        assert name in result["providers"], result["providers"]
    assert "3 provider" in result["summary"]


def test_registry_summary_matches_the_settings_screen(store):
    _settings_provider()
    summary = registry.describe_configured()
    assert "Agnes AI" in summary
    assert "agnes-2.0-flashfree" in summary


# -- the assistant can also add providers -----------------------------------

def test_voice_added_provider_becomes_a_real_provider(store, skill):
    result = skill.execute(
        action="set", provider="agnes", api_key="sk-live-xyz",
        base_url="https://api.agnes-ai.com/v1",
    )

    assert result["status"] == "success"
    assert "ready to use" in result["summary"]
    ready = {p["id"]: p for p in registry.configured_providers()}
    assert "agnes" in ready
    assert ready["agnes"]["api_key"] == "sk-live-xyz"


def test_preset_names_reuse_the_known_endpoint(store, skill):
    skill.execute(action="set", provider="groq", api_key="gsk-1")

    groq = registry.get_provider("groq")
    assert groq is not None
    assert groq["base_url"] == "https://api.groq.com/openai/v1"
    assert groq["model"]
    assert "coding" in groq["caps"]


def test_builtin_names_store_their_key_instead_of_duplicating(store, skill):
    skill.execute(action="set", provider="openrouter", api_key="or-key")

    assert registry.get_api_key("openrouter_api_key") == "or-key"
    assert all(p["id"] != "openrouter" or not p.get("custom") for p in registry.all_providers())


def test_unknown_provider_still_works(store, skill):
    skill.execute(
        action="set", provider="Brand New Service", api_key="k",
        base_url="https://api.brandnew.example/v1",
    )
    provider = registry.get_provider("brand-new-service")
    assert provider is not None
    assert provider["base_url"] == "https://api.brandnew.example/v1"
    assert provider["kind"] == "openai"


def test_delete_removes_the_provider(store, skill):
    _settings_provider()
    skill.execute(action="delete", provider="Agnes AI")
    assert "agnes-ai" not in {p["id"] for p in registry.list_custom_providers()}


# -- legacy stores are folded in ---------------------------------------------

def test_legacy_dynamic_providers_are_imported(store, skill, tmp_path):
    (tmp_path / "dynamic_providers.json").write_text(json.dumps({
        "deepseek": {"key": "ds-key", "base_url": "https://api.deepseek.com/v1", "active": True},
    }), encoding="utf-8")

    result = skill.execute(action="list")

    assert "deepseek" in result["providers"]
    assert registry.get_provider("deepseek") is not None, "imported into the registry"
    assert registry.get_provider("deepseek")["api_key"] == "ds-key"


# -- and the rest of the app uses them --------------------------------------

def test_quick_actions_can_use_a_settings_provider(store):
    """The Quick Action chain must offer the provider as an option."""
    from core import quick_actions as qa

    _settings_provider()
    # the routed custom-provider backend is offered first, ahead of Gemini
    assert qa._provider_backends()[0].__name__ == "_custom_provider_quick_reply"
    assert qa._custom_provider_backends()[0][1]["name"] == "Agnes AI"


def test_routing_prefers_a_configured_custom_provider(store):
    from core import jeff_router

    _settings_provider()
    decision = jeff_router.route("write me a python script", client=None)
    assert decision.provider["id"] == "agnes-ai"

# -- provider failover -------------------------------------------------------
# A Quick Action used to report "quota used up" when the routed provider was a
# dead localhost server: the first provider failed, the chain fell through to
# Gemini (whose free tier was exhausted) and the message blamed the quota.

def test_failover_tries_every_provider_before_giving_up(store_providers):
    from core import provider_registry as reg

    attempts = []

    def fake_chat(provider, messages, **kwargs):
        attempts.append(provider["id"])
        if provider["id"] != "agnes-ai":
            raise RuntimeError("Connection refused")
        return "translated text"

    import types
    import sys

    saved = reg.chat
    reg.chat = fake_chat
    try:
        reply, provider, errors = reg.chat_failover(
            [{"role": "user", "content": "hola"}],
            providers=store_providers(),
        )
    finally:
        reg.chat = saved

    assert reply == "translated text"
    assert provider["id"] == "agnes-ai"
    assert len(attempts) >= 2, attempts
    assert any("Connection refused" in e for e in errors)


def test_failover_skips_a_local_server_that_is_not_running(monkeypatch, store_providers):
    from core import provider_registry as reg

    dead_local = {
        "id": "9router", "name": "9router", "kind": "local",
        "base_url": "http://localhost:3000/v1", "model": "m", "api_key": "",
        "caps": ["chat"], "custom": True, "enabled": True,
    }
    providers = store_providers()
    monkeypatch.setattr(reg, "local_ai_running", lambda base_url="", force=False: False)

    tried = []

    def fake_chat(provider, messages, **kwargs):
        tried.append(provider["id"])
        return "ok"

    saved = reg.chat
    reg.chat = fake_chat
    try:
        # it is offered first (highest priority) and must still be skipped
        reply, provider, errors = reg.chat_failover(
            [{"role": "user", "content": "x"}],
            providers=providers,
            order=[dead_local],
        )
    finally:
        reg.chat = saved

    assert "9router" not in tried, "a dead local server must not be called"
    assert any("local server not running" in e for e in errors)


def test_dead_local_providers_are_not_routed_to(monkeypatch, store_providers):
    from core import provider_registry as reg

    monkeypatch.setattr(reg, "local_ai_running", lambda base_url="", force=False: False)
    providers = reg.list_custom_providers() + [{
        "id": "omniroute", "name": "OmniRoute", "kind": "local",
        "base_url": "http://localhost:20128/v1", "model": "auto", "api_key": "",
        "caps": ["chat", "coding"], "custom": True, "enabled": True,
    }]
    saved = reg.list_custom_providers
    reg.list_custom_providers = lambda: providers
    try:
        ready = [p["id"] for p in reg.configured_providers()]
    finally:
        reg.list_custom_providers = saved

    assert "omniroute" not in ready


def test_connectivity_failure_is_not_reported_as_a_quota_problem():
    from core import quick_actions as qa

    message = qa.friendly_quick_action_error(
        "translate",
        "9router: Connection refused; gemini: 429 RESOURCE_EXHAUSTED",
    )
    # The quota is real here, so it is mentioned - but the sentence must not
    # claim quota when only servers were unreachable (next test).
    assert "quota" in message.lower()


def test_unreachable_providers_get_a_connectivity_message():
    from core import quick_actions as qa

    message = qa.friendly_quick_action_error(
        "translate", "OmniRoute: Connection refused; Ollama: local server not running",
    )
    assert "quota" not in message.lower()
    assert "answered" in message.lower() or "start" in message.lower()


def test_quick_action_uses_failover_across_providers(monkeypatch):
    from core import quick_actions as qa

    calls = []

    def fake_failover(messages, providers=None, order=None, **kwargs):
        calls.append([p["id"] for p in (providers or [])])
        return "translated", (providers or [{}])[0], ["first: connection refused"]

    import core.provider_registry as reg

    monkeypatch.setattr(reg, "chat_failover", fake_failover)
    monkeypatch.setattr(reg, "configured_providers", lambda: [{
        "id": "agnes-ai", "name": "Agnes AI", "kind": "openai",
        "base_url": "https://api.agnes-ai.com/v1", "api_key": "k",
        "model": "agnes-2.0-flashfree", "caps": ["chat"], "custom": True,
    }])

    assert qa._custom_provider_quick_reply("translate this") == "translated"
    assert calls and calls[0] == ["agnes-ai"]
