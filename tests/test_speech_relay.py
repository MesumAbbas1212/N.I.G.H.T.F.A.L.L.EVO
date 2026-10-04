"""App text must be read out, not turned into a conversation.

Reported: after a Quick Action (Alt → Translate) the chat contained

    NIGHTFALL Evo: What is your name? My ...
    System Event: Executing save_memory / Memory saved.
    NIGHTFALL Evo: Acknowledged. I have ...
    NIGHTFALL Evo: Acknowledged. I'm working ...

Cause: ``speak()`` sent app text to the live session as a *user turn* -
"System Alert / Context: <text> ... Please relay this information to me
naturally now" - so the model held a conversation with its own status messages,
answered them ("Acknowledged..."), and even ran tools because the relayed text
looked personal ("my name is sydro" → save_memory).

The fix: app text is synthesized with the TTS model in Zephyr's voice and
played locally. Same voice, no turn, no tools, no memory.
"""

import importlib
import threading
import time
import types

import pytest


@pytest.fixture()
def main_module():
    return importlib.import_module("main")


class FakeSession:
    """Records every way the app could talk to the live session."""

    def __init__(self):
        self.sent = []
        self.client_content = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)

    async def send_client_content(self, turns, turn_complete=False):
        self.client_content.append(turns)


def _assistant(main, session=None):
    assistant = object.__new__(main.NIGHTFALLLive)
    ui = types.SimpleNamespace(muted=False, states=[],
                               set_state=lambda state: ui.states.append(state))
    assistant.ui = ui
    assistant.session = session if session is not None else FakeSession()
    assistant._loop = object()
    assistant._speaking_lock = threading.Lock()
    assistant._is_speaking = False
    assistant.set_speaking = lambda value: None
    return assistant


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# -- the regression ----------------------------------------------------------

def test_speaking_app_text_does_not_create_a_conversation_turn(monkeypatch, main_module):
    main = main_module
    session = FakeSession()
    assistant = _assistant(main, session)

    spoken = []
    monkeypatch.setattr(
        "actions.attention_monitor._speak_edge_native",
        lambda text, force_edge=False: spoken.append(text),
    )
    monkeypatch.setattr(main, "_gemini_tts_pcm", lambda text, voice="Zephyr": None)

    assistant.speak("Quick Action answer: my name is sydro")

    assert _wait_for(lambda: spoken), "the text was never read out"
    assert session.sent == [], "app text must never be sent as a live turn"
    assert session.client_content == []


def test_the_read_out_is_never_fed_to_memory_or_tools(monkeypatch, main_module):
    """No tool declarations are involved at all in a read-out."""
    import inspect

    speak_code = inspect.getsource(main_module.NIGHTFALLLive.speak)
    read_out_code = inspect.getsource(main_module.NIGHTFALLLive._speak_verbatim)
    for source in (speak_code, read_out_code):
        assert "send_client_content" not in source
        assert "session.send" not in source
    assert "_speak_verbatim" in speak_code


# -- Zephyr's voice is used --------------------------------------------------

def test_tts_requests_the_zephyr_voice(monkeypatch, main_module):
    main = main_module
    captured = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            captured["model"] = model
            captured["text"] = contents
            captured["config"] = config
            part = types.SimpleNamespace(
                inline_data=types.SimpleNamespace(data=b"\x00\x01" * 10)
            )
            return types.SimpleNamespace(
                candidates=[types.SimpleNamespace(
                    content=types.SimpleNamespace(parts=[part]))]
            )

    class Recorder:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    fake_types = types.SimpleNamespace(
        GenerateContentConfig=Recorder, SpeechConfig=Recorder,
        VoiceConfig=Recorder, PrebuiltVoiceConfig=Recorder,
    )
    monkeypatch.setattr(main, "types", fake_types)
    monkeypatch.setattr(main, "_get_api_key", lambda: "test-key")
    monkeypatch.setattr(main.genai, "Client",
                        lambda api_key=None: types.SimpleNamespace(models=FakeModels()))
    main._tts_model_ok = None

    pcm = main._gemini_tts_pcm("hello sir")
    assert pcm == b"\x00\x01" * 10
    speech = captured["config"].speech_config
    assert speech.voice_config.prebuilt_voice_config.voice_name == "Zephyr"
    assert captured["model"].endswith("tts") or "tts" in captured["model"]


def test_a_dead_tts_quota_is_not_retried_for_every_message(monkeypatch, main_module):
    main = main_module
    attempts = []

    class FakeModels:
        def generate_content(self, model, contents, config):
            attempts.append(model)
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")

    monkeypatch.setattr(main, "_get_api_key", lambda: "k")
    monkeypatch.setattr(main.genai, "Client",
                        lambda api_key=None: types.SimpleNamespace(models=FakeModels()))
    main._tts_model_ok = None
    main._tts_blocked_until = 0.0

    assert main._gemini_tts_pcm("first") is None
    first_round = list(attempts)
    assert first_round, "the first call should try the TTS models"

    # The quota is gone: later calls must not hammer the API again.
    assert main._gemini_tts_pcm("second") is None
    assert attempts == first_round, "TTS was retried while cooling down"
    assert main._tts_available() is False


def test_missing_tts_quota_falls_back_to_the_local_voice(monkeypatch, main_module):
    main = main_module
    assistant = _assistant(main)

    spoken = []
    monkeypatch.setattr(main, "_gemini_tts_pcm", lambda text, voice="Zephyr": None)
    monkeypatch.setattr(main, "_play_pcm_audio", lambda pcm, rate=24000: True)
    monkeypatch.setattr(
        "actions.attention_monitor._speak_edge_native",
        lambda text, force_edge=False: spoken.append(text),
    )

    assistant.speak("status update")
    assert _wait_for(lambda: spoken == ["status update"])


def test_successful_tts_is_played_without_the_local_voice(monkeypatch, main_module):
    main = main_module
    assistant = _assistant(main)

    played = []
    spoken = []
    monkeypatch.setattr(main, "_gemini_tts_pcm", lambda text, voice="Zephyr": b"pcm")
    monkeypatch.setattr(main, "_play_pcm_audio", lambda pcm, rate=24000: played.append(pcm) or True)
    monkeypatch.setattr(
        "actions.attention_monitor._speak_edge_native",
        lambda text, force_edge=False: spoken.append(text),
    )

    assistant.speak("your document is ready")
    assert _wait_for(lambda: played == [b"pcm"])
    assert spoken == []


# -- Quick Actions answer in the same single voice ---------------------------

def test_quick_action_guard_speaks_through_the_app_voice(monkeypatch, main_module):
    import inspect

    source = inspect.getsource(main_module.NIGHTFALLLive._handle_quick_action_request)
    assert "self.speak(reply" in source
    # The old code preferred the offline Edge voice for Quick Actions.
    assert source.index("self.speak(reply") < source.index("_speak_edge_native")


def test_quick_action_manager_prefers_the_registered_voice():
    import inspect

    from core import quick_actions as qa

    source = inspect.getsource(qa.QuickActionsManager._run_quick_action)
    assert "speak_native(reply)" in source
    assert source.index("speak_native(reply)") < source.index("_speak_edge_native")


def test_stopping_speech_stops_local_playback():
    from actions import attention_monitor as am

    calls = []

    class FakeSD:
        @staticmethod
        def stop():
            calls.append("stop")

    import sys
    original = sys.modules.get("sounddevice")
    sys.modules["sounddevice"] = FakeSD
    try:
        am._cleanup_current_audio()
    finally:
        if original is None:
            sys.modules.pop("sounddevice", None)
        else:
            sys.modules["sounddevice"] = original

    assert calls == ["stop"]
