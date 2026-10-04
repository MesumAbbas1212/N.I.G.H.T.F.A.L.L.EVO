"""Multi-provider API keys and Jeff model routing.

Jeff (https://github.com/logan-markewich/jeff) is a self-hosted drop-in
replacement for TypeSafe's jev System One API: it answers small
classification questions (choice / score / noul) about a piece of text.
NIGHTFALL Evo uses it to decide which provider and model should answer a
request. These tests cover the provider registry and the router, with the
network and the Jeff server stubbed out.
"""

import pytest

from core import jeff_router
from core import provider_registry as registry


# --------------------------------------------------------------- fixtures
@pytest.fixture
def providers(tmp_path, monkeypatch):
    """Three fake providers with different strengths."""
    monkeypatch.setattr(registry, "api_keys_path", lambda: tmp_path / "api_keys.json")
    monkeypatch.setattr(registry, "_repo_keys_path", lambda: tmp_path / "repo_keys.json")
    registry.save_custom_provider({
        "name": "Groq", "kind": "openai", "base_url": "https://api.groq.com/openai/v1",
        "api_key": "k-groq", "model": "llama-3.3-70b", "caps": ["chat", "coding", "fast"],
        "priority": 30,
    })
    registry.save_custom_provider({
        "name": "Claude", "kind": "anthropic", "base_url": "https://api.anthropic.com",
        "api_key": "k-claude", "model": "claude-sonnet-4-5",
        "caps": ["chat", "coding", "reasoning", "long_context"], "priority": 40,
    })
    registry.save_custom_provider({
        "name": "Obscure Vision API", "kind": "openai",
        "base_url": "https://vision.example.com/v1", "api_key": "k-vision",
        "model": "vision-1", "caps": ["chat", "vision"], "priority": 35,
    })
    return registry.all_providers()


# -------------------------------------------------------- provider registry
def test_custom_providers_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "api_keys_path", lambda: tmp_path / "api_keys.json")
    monkeypatch.setattr(registry, "_repo_keys_path", lambda: tmp_path / "repo_keys.json")

    saved = registry.save_custom_provider({
        "name": "My Obscure API", "kind": "openai",
        "base_url": "https://api.example.com/v1", "api_key": "sk-test",
        "model": "foo-1", "caps": "chat, coding",
    })
    assert saved["id"] == "my-obscure-api"
    assert saved["caps"] == ["chat", "coding"]
    assert saved["kind"] == "openai"

    stored = registry.list_custom_providers()
    assert [p["id"] for p in stored] == ["my-obscure-api"]
    assert registry.get_provider("my-obscure-api")["model"] == "foo-1"

    # updating by id replaces instead of duplicating
    registry.save_custom_provider({"id": "my-obscure-api", "name": "My Obscure API",
                                   "base_url": "https://api.example.com/v2", "model": "foo-2"})
    stored = registry.list_custom_providers()
    assert len(stored) == 1
    assert stored[0]["base_url"].endswith("/v2")

    assert registry.remove_custom_provider("my-obscure-api") is True
    assert registry.list_custom_providers() == []
    assert registry.remove_custom_provider("my-obscure-api") is False


def test_builtin_providers_are_always_available():
    ids = {p["id"] for p in registry.all_providers()}
    assert {"gemini", "openrouter", "anthropic", "local"} <= ids


def test_presets_cover_famous_and_generic_providers():
    names = {p["name"] for p in registry.PRESETS}
    assert "Groq" in names
    assert "DeepSeek" in names
    assert "OpenRouter" in names
    assert "Other OpenAI-compatible API" in names
    for preset in registry.PRESETS:
        assert preset["kind"] in registry.KINDS


def test_api_keys_are_read_and_written(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "api_keys_path", lambda: tmp_path / "api_keys.json")
    monkeypatch.setattr(registry, "_repo_keys_path", lambda: tmp_path / "repo_keys.json")
    registry.set_api_key("gemini_api_key", "  abc123  ")
    assert registry.get_api_key("gemini_api_key") == "abc123"
    assert registry.get_api_key("openrouter_api_key") == ""
    # built-in providers pick their key up from the same store
    gemini = registry.get_provider("gemini")
    assert gemini["api_key"] == "abc123"


def test_chat_uses_the_right_wire_format(monkeypatch):
    calls = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "hello from openai"}}]}

    import requests

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["url"] = url
        calls["headers"] = headers
        calls["payload"] = json
        return FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)

    provider = {"id": "x", "name": "X", "kind": "openai",
                "base_url": "https://api.example.com/v1", "api_key": "sk-1", "model": "m-1"}
    reply = registry.chat(provider, [{"role": "system", "content": "be brief"},
                                     {"role": "user", "content": "hi"}])
    assert reply == "hello from openai"
    assert calls["url"] == "https://api.example.com/v1/chat/completions"
    assert calls["headers"]["Authorization"] == "Bearer sk-1"
    assert calls["payload"]["messages"][0] == {"role": "system", "content": "be brief"}

    anthropic = dict(provider, kind="anthropic", base_url="https://api.anthropic.com")
    registry.chat(anthropic, [{"role": "user", "content": "hi"}])
    assert calls["url"] == "https://api.anthropic.com/v1/messages"
    assert calls["headers"]["x-api-key"] == "sk-1"

    gemini = dict(provider, kind="gemini", base_url="https://generativelanguage.googleapis.com")
    registry.chat(gemini, [{"role": "user", "content": "hi"}])
    assert "generateContent" in calls["url"]


def test_local_providers_need_no_key():
    provider = {"id": "l", "name": "Ollama", "kind": "local",
                "base_url": "http://localhost:11434/v1", "model": "llama3.2"}
    assert "Authorization" not in registry._headers(provider)


# ------------------------------------------------------------ jeff routing
def test_routing_questions_match_the_jeff_api():
    questions = jeff_router.ROUTING_QUESTIONS
    assert questions["capability"]["type"] == "choice"
    assert questions["complexity"]["type"] == "score"
    assert questions["needs_vision"]["type"] == "noul"
    assert questions["complexity"]["criteria"] == ["low", "medium", "high"]
    for capability, cap in jeff_router.CAPABILITY_CAPS.items():
        assert cap in registry.CAPABILITIES, capability


def test_heuristic_routing_picks_the_right_provider(providers):
    decision = jeff_router.route("fix this python traceback in my script", providers=providers)
    assert decision.provider_id == "groq"
    assert decision.capability == "coding"
    assert decision.source == "heuristic"

    decision = jeff_router.route(
        "explain in detail, step by step, how a superscalar pipeline works",
        providers=providers,
    )
    assert decision.provider_id == "claude"
    assert decision.complexity == "high"

    decision = jeff_router.route("what does this screenshot say?", providers=providers)
    assert decision.provider_id == "obscure-vision-api"
    assert decision.capability == "vision"


def test_jeff_classification_drives_routing(providers):
    class FakeJeff:
        def classify(self, text):
            return {"capability": "long_document", "complexity": "high",
                    "flags": {"needs_long_context": 1.0}}

    decision = jeff_router.route("anything at all", client=FakeJeff(), providers=providers)
    assert decision.source == "jeff"
    assert decision.capability == "long_document"
    assert decision.provider_id == "claude"
    assert decision.confidence > 0.5
    assert "claude" in decision.describe()


def test_routing_without_providers_raises():
    with pytest.raises(RuntimeError):
        jeff_router.route("hello", providers=[])


def test_jeff_client_parses_system_one(monkeypatch):
    client = jeff_router.JeffClient(base_url="http://localhost:8000", api_key="devkey")
    monkeypatch.setattr(client, "health", lambda force=False: True)
    monkeypatch.setattr(client, "system_one", lambda state, questions: {
        "choices": {"capability": {"choice": "coding"}},
        "scores": {"complexity": {"score": "high"}},
        "nouls": {"needs_vision": {"noul": 0.12}},
    })
    result = client.classify("write a python script")
    assert result == {"capability": "coding", "complexity": "high",
                      "flags": {"needs_vision": 0.12}}


def test_jeff_client_returns_none_when_server_is_down(monkeypatch):
    client = jeff_router.JeffClient(base_url="http://localhost:1")
    monkeypatch.setattr(client, "health", lambda force=False: False)
    assert client.classify("hello") is None


def test_route_and_chat_uses_the_selected_provider(providers, monkeypatch):
    captured = {}

    def fake_chat(provider, messages, **kwargs):
        captured["provider"] = provider
        captured["messages"] = messages
        return "routed answer"

    monkeypatch.setattr(registry, "chat", fake_chat)
    reply, decision = jeff_router.route_and_chat(
        "fix this python bug", client=None, providers=providers
    )
    assert reply == "routed answer"
    assert captured["provider"]["id"] == "groq"
    assert captured["messages"][-1]["content"] == "fix this python bug"


def test_client_from_settings_is_none_when_disabled(monkeypatch):
    class FakeConfig:
        @staticmethod
        def load_settings():
            return {"jeff_routing_enabled": False, "jeff_base_url": "http://localhost:8000"}

    import sys
    import types

    module = types.ModuleType("memory.config_manager")
    module.load_settings = FakeConfig.load_settings
    monkeypatch.setitem(sys.modules, "memory.config_manager", module)
    assert jeff_router.client_from_settings() is None


def test_client_from_settings_reads_configuration(monkeypatch):
    import sys
    import types

    module = types.ModuleType("memory.config_manager")
    module.load_settings = lambda: {
        "jeff_routing_enabled": True,
        "jeff_base_url": "http://gpu-box:8000/",
        "jeff_api_key": "k1",
        "jeff_model": "jev",
    }
    monkeypatch.setitem(sys.modules, "memory.config_manager", module)
    client = jeff_router.client_from_settings()
    assert client is not None
    assert client.base_url == "http://gpu-box:8000"
    assert client.api_key == "k1"
    assert client.model == "jev"


# ------------------------------------------------- main.py fallback reply
def test_fallback_reply_is_routed_by_jeff(tmp_path, monkeypatch):
    import main

    monkeypatch.setattr(registry, "api_keys_path", lambda: tmp_path / "api_keys.json")
    monkeypatch.setattr(registry, "_repo_keys_path", lambda: tmp_path / "repo_keys.json")
    registry.save_custom_provider({
        "name": "Groq", "kind": "openai", "base_url": "https://api.groq.com/openai/v1",
        "api_key": "k", "model": "llama-3.3-70b", "caps": ["chat", "coding", "fast"],
        "priority": 30,
    })

    calls = []

    def fake_chat(provider, messages, **kwargs):
        calls.append(provider["id"])
        return "answer from Groq"

    monkeypatch.setattr(registry, "chat", fake_chat)
    import core.jeff_router as jeff

    monkeypatch.setattr(jeff.registry, "chat", fake_chat)

    class FakeUI:
        muted = True

        def __init__(self):
            self.logs = []

        def set_state(self, state, **kw):
            pass

        def write_log(self, message):
            self.logs.append(message)

        def update_task_workspace(self, **kw):
            pass

        def finish_task_workspace(self, *a, **kw):
            pass

    class FakeConfig:
        @staticmethod
        def load_settings():
            return {"jeff_routing_enabled": True, "default_ai_provider": "Gemini",
                    "offline_mode_enabled": False}

    monkeypatch.setattr(main, "config_manager", FakeConfig)
    monkeypatch.setattr(main, "_gemini_text_reply", lambda prompt: "gemini answer")

    assistant = object.__new__(main.NIGHTFALLLive)
    assistant.ui = FakeUI()
    assistant._use_openrouter_first = False
    assistant._last_user_utterance = ""
    assistant.speak = lambda *a, **kw: None

    assistant._fallback_reply("fix this python traceback in my script")

    assert calls == ["groq"]
    assert any("answer from Groq" in log for log in assistant.ui.logs)
    assert any("Jeff →" in log for log in assistant.ui.logs)
