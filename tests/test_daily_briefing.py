import time
from unittest.mock import patch

from actions import daily_briefing
from main import NIGHTFALLLive, _looks_like_daily_briefing_request, _speak_daily_briefing


def test_daily_briefing_returns_when_one_source_times_out(monkeypatch):
    def slow_weather(city=None):
        time.sleep(0.05)
        return {"status": "success", "city": city or "Test City"}

    monkeypatch.setattr(daily_briefing, "BRIEFING_FETCH_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(daily_briefing, "_get_weather_intel", slow_weather)
    monkeypatch.setattr(daily_briefing, "_get_calendar_intel", lambda: {"count": 0, "events": []})
    monkeypatch.setattr(daily_briefing, "_get_gmail_intel", lambda: {"configured": False, "count": 0})
    monkeypatch.setattr(daily_briefing, "_get_instagram_intel", lambda: {"configured": False, "count": 0})
    monkeypatch.setattr(daily_briefing, "_get_top_headlines", lambda **kwargs: ["Test headline"])

    started = time.monotonic()
    data, narrative = daily_briefing.compile_unified_briefing()

    assert time.monotonic() - started < 0.5
    assert data["weather"]["status"] == "unavailable"
    assert data["headlines"] == ["Test headline"]
    assert "Today is" in narrative


def test_daily_briefing_command_routes_directly_to_action():
    class FakeUI:
        def set_state(self, state):
            pass

        def begin_task_workspace(self, *args, **kwargs):
            pass

        def write_log(self, message):
            pass

    class InlineThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    assistant = object.__new__(NIGHTFALLLive)
    assistant.ui = FakeUI()
    assistant._reply_mode = False
    assistant._reset_idle_activity = lambda: None
    spoken = []
    assistant.speak = spoken.append
    calls = []

    with (
        patch("main._update_memory_async", lambda *args: None),
        patch("main.stop_native_speech"),
        patch("main._looks_like_screen_request", side_effect=AssertionError("screen route reached")),
        patch("main.threading.Thread", InlineThread),
        patch("actions.daily_briefing.daily_briefing", side_effect=lambda **kwargs: calls.append(kwargs)),
    ):
        assistant._on_text_command("Give me my daily briefing")

    assert _looks_like_daily_briefing_request("morning update")
    assert len(calls) == 1
    assert calls[0]["parameters"] == {"category": "all"}
    assert spoken == ["Preparing your daily briefing."]


def test_startup_daily_briefing_is_spoken_even_with_overlay_visible():
    class VisibleOverlay:
        def isVisible(self):
            return True

    class FakeUI:
        _overlay = VisibleOverlay()

        def __init__(self):
            self.messages = []

        def show_daily_briefing(self, data):
            self.messages.append(("briefing", data))

        def write_log(self, message):
            self.messages.append(("log", message))

    ui = FakeUI()
    data = {"greeting": "Good morning"}
    narrative = "Good morning. Here is your daily briefing."
    spoken = []

    with (
        patch("actions.daily_briefing.compile_unified_briefing", return_value=(data, narrative)),
        patch("main.speak_native") as native_speak,
    ):
        _speak_daily_briefing(ui, spoken.append)

    assert ui.messages == [("briefing", data), ("log", f"NIGHTFALL Evo: {narrative}")]
    assert spoken == [narrative]
    native_speak.assert_not_called()