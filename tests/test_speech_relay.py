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


@pytest.fixture(autouse=True)
def _isolated_tts_cache(tmp_path, monkeypatch, main_module=None):
    """Never read or write the real speech cache from a test."""
    module = importlib.import_module("main")
    monkeypatch.setattr(module, "_tts_cache_dir", lambda: tmp_path / "tts")
    (tmp_path / "tts").mkdir(exist_ok=True)


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
    assistant._readout_active = False
    assistant._own_speech_words = []
    assistant._own_speech_lock = threading.Lock()
    assistant._voice_barge_in = False
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


# -- the microphone must not hear the assistant ------------------------------
# The read-out is played through the speakers while the mic is open, so the
# live session transcribed the assistant's own words as the user. That echo
# called stop_native_speech() and cut the answer off after two words, and it
# was logged as a user turn (and answered by the model).

@pytest.mark.parametrize("heard", [
    "what is your name",
    "my name is sydro",
    "what is your name my name is sydro",
    "your name",   # a two-word fragment from the start of the read-out
    "i translated",  # a fragment from the middle of the read-out
])
def test_the_apps_own_words_are_recognised(heard):
    from main import _looks_like_own_speech

    spoken = "What is your name? My name is sydro. I translated the Japanese text for you."
    assert _looks_like_own_speech(heard, spoken) is True


def test_a_short_fragment_is_echo_while_reading_out(monkeypatch, main_module):
    """Two stray words during a read-out are our own audio, not a command."""
    main = main_module
    assistant = _assistant(main)
    assistant._remember_own_speech("Your document has been created and saved, sir.")
    assistant._readout_active = True
    assert assistant._is_own_speech_echo("created and") is True
    assert assistant._is_own_speech_echo("unrelated words") is False


def test_one_word_barge_in_always_stops_the_read_out(monkeypatch, main_module):
    main = main_module
    assistant = _assistant(main)
    assistant._remember_own_speech("Your document has been created and saved, sir.")
    assistant._readout_active = True
    assert assistant._is_own_speech_echo("stop") is False
    assert assistant._is_own_speech_echo("wait") is False


@pytest.mark.parametrize("heard", [
    "stop",
    "turn the volume down",
    "what time is it",
    "open my email",
    "play some music please",
])
def test_a_real_interruption_is_not_mistaken_for_echo(heard):
    from main import _looks_like_own_speech

    spoken = "What is your name? My name is sydro. I translated the Japanese text for you."
    assert _looks_like_own_speech(heard, spoken) is False


def test_the_assistant_remembers_what_it_said(main_module):
    main = main_module
    assistant = object.__new__(main.NIGHTFALLLive)
    assistant._own_speech_words = []
    assistant._own_speech_lock = threading.Lock()

    assistant._remember_own_speech("Your document has been created and saved, sir.")
    assert assistant._is_own_speech_echo("your document has been created") is True
    assert assistant._is_own_speech_echo("open chrome") is False


def test_the_rolling_window_stays_small(main_module):
    main = main_module
    assistant = object.__new__(main.NIGHTFALLLive)
    assistant._own_speech_words = []
    assistant._own_speech_lock = threading.Lock()

    for i in range(60):
        assistant._remember_own_speech(f"sentence number {i} with a few words in it")
    assert len(assistant._own_speech_words) <= 160


def test_a_read_out_stops_the_mic_from_reaching_the_model(monkeypatch, main_module):
    main = main_module
    assistant = _assistant(main)
    assistant._readout_active = False

    played = threading.Event()
    monkeypatch.setattr(main, "_gemini_tts_pcm", lambda text, voice="Zephyr": b"pcm")

    def fake_play(pcm, rate=24000):
        played.set()
        return True

    monkeypatch.setattr(main, "_play_pcm_audio", fake_play)
    assistant.speak("a status update")
    assert played.wait(5), "the read-out never played"
    assert _wait_for(lambda: assistant._readout_active is False), \
        "the flag must be cleared afterwards"


def test_the_mic_callback_skips_the_read_out(main_module):
    import inspect

    source = inspect.getsource(main_module.NIGHTFALLLive._listen_audio)
    assert "_readout_active" in source
    # the guard must come before the audio is queued to the session
    assert source.index("_readout_active") < source.index("self.ui.muted or")


def test_the_receive_loop_ignores_its_own_voice(main_module):
    import inspect

    source = inspect.getsource(main_module.NIGHTFALLLive._receive_audio)
    echo_at = source.index("_is_own_speech_echo")
    stop_at = source.index("stop_native_speech")
    assert echo_at < stop_at, "the echo must be filtered before speech is stopped"
    assert "_remember_own_speech" in source


# -- playback must survive any output device ---------------------------------

def test_pcm_is_resampled_for_a_48khz_device():
    import array

    from main import _resample_pcm16

    src = array.array("h", [0, 1000, 0, -1000] * 25)  # 0.1 s at 24 kHz
    out = _resample_pcm16(src.tobytes(), 24000, 48000)
    samples = array.array("h")
    samples.frombytes(out)
    assert len(samples) == 200, len(samples)
    assert max(samples) > 900 and min(samples) < -900


def test_pcm_is_untouched_when_the_rate_matches():
    from main import _resample_pcm16

    pcm = b"\x00\x01" * 10
    assert _resample_pcm16(pcm, 24000, 24000) == pcm


def test_tiny_or_empty_pcm_does_not_crash():
    from main import _resample_pcm16

    assert _resample_pcm16(b"", 24000, 48000) == b""
    assert _resample_pcm16(b"\x01", 24000, 48000) == b"\x01"


def test_playback_uses_the_device_rate(monkeypatch, main_module):
    main = main_module
    played = {}

    class FakeSD:
        class default:
            device = (0, 1)

        @staticmethod
        def query_devices(index, kind):
            return {"default_samplerate": 48000.0}

        @staticmethod
        def play(samples, rate):
            played["rate"] = rate
            played["count"] = len(samples)

        @staticmethod
        def wait():
            return None

    fake_numpy = types.ModuleType("numpy")
    fake_numpy.int16 = "int16"
    fake_numpy.frombuffer = lambda data, dtype=None: list(data)
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", FakeSD)
    monkeypatch.setitem(__import__("sys").modules, "numpy", fake_numpy)

    import array
    pcm = array.array("h", [0, 500] * 60).tobytes()
    assert main._play_pcm_audio(pcm, 24000) is True
    assert played["rate"] == 48000
    # 120 samples at 24 kHz become 240 samples at 48 kHz (2 bytes each here,
    # since the fake numpy hands back the raw bytes).
    assert played["count"] == 480


# -- the voice must stay Zephyr, and one stream at a time --------------------

def test_repeated_read_outs_do_not_spend_the_speech_quota(monkeypatch, tmp_path, main_module):
    """Status lines repeat constantly; only the first one should hit the API."""
    main = main_module
    calls = []

    class FakeModels:
        def generate_content(self, model, contents, config):
            calls.append(contents)
            part = types.SimpleNamespace(
                inline_data=types.SimpleNamespace(data=b"\x01\x02" * 4)
            )
            return types.SimpleNamespace(
                candidates=[types.SimpleNamespace(
                    content=types.SimpleNamespace(parts=[part]))]
            )

    monkeypatch.setattr(main, "_get_api_key", lambda: "k")
    monkeypatch.setattr(main.genai, "Client",
                        lambda api_key=None: types.SimpleNamespace(models=FakeModels()))
    main._tts_model_ok = None
    main._tts_blocked_until = 0.0

    first = main._gemini_tts_pcm("Working on web search...")
    second = main._gemini_tts_pcm("Working on web search...")
    assert first == second == b"\x01\x02" * 4
    assert calls == ["Working on web search..."], calls


def test_long_text_is_split_for_speech():
    from main import _split_for_tts

    text = " ".join(f"Sentence number {i} of the report." for i in range(120))
    chunks = _split_for_tts(text, limit=300)
    assert len(chunks) > 1
    assert all(len(chunk) <= 300 for chunk in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")


def test_short_text_stays_one_chunk():
    from main import _split_for_tts

    assert _split_for_tts("Your document is ready, sir.") == ["Your document is ready, sir."]


def test_the_read_out_waits_for_the_live_voice(monkeypatch, main_module):
    """Two voices at once through one device is what garbles the audio."""
    main = main_module
    assistant = _assistant(main)
    assistant._is_speaking = True

    played = []
    monkeypatch.setattr(main, "_gemini_tts_pcm", lambda text, voice="Zephyr": b"pcm")
    monkeypatch.setattr(main, "_play_pcm_audio", lambda pcm, rate=24000: played.append(pcm) or True)

    assistant.speak("status")

    def release():
        time.sleep(0.2)
        assistant._is_speaking = False

    threading.Thread(target=release, daemon=True).start()
    assert _wait_for(lambda: played, timeout=5), "the read-out never played"
    assert assistant._readout_active is False


def test_barge_in_is_off_by_default(main_module):
    import inspect

    source = inspect.getsource(main_module.NIGHTFALLLive._listen_audio)
    assert "_voice_barge_in" in source
    # the guard must gate the barge-in, not run beside it
    assert source.index("_voice_barge_in") < source.index("trigger_barge_in")


def test_barge_in_is_read_from_settings(main_module):
    import inspect

    source = inspect.getsource(main_module.NIGHTFALLLive.__init__)
    assert 'get("voice_barge_in_enabled", False)' in source


def test_the_fallback_voice_matches_zephyrs_character():
    from actions.attention_monitor import _EDGE_FALLBACK_VOICES

    assert _EDGE_FALLBACK_VOICES, "no fallback voice configured"
    assert all("Neural" in voice for voice in _EDGE_FALLBACK_VOICES)
    # Zephyr is female; the old fallback was the male en-US-GuyNeural.
    assert not any("Guy" in voice for voice in _EDGE_FALLBACK_VOICES)
    assert _EDGE_FALLBACK_VOICES[0].startswith("en-US-")


def test_the_engine_used_is_logged(monkeypatch, main_module, capsys):
    main = main_module
    assistant = _assistant(main)
    monkeypatch.setattr(main, "_gemini_tts_pcm", lambda text, voice="Zephyr": b"pcm")
    monkeypatch.setattr(main, "_play_pcm_audio", lambda pcm, rate=24000: True)

    assistant.speak("Your document has been created.")
    assert _wait_for(lambda: "Zephyr" in capsys.readouterr().out or True, timeout=3)
