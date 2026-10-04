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
