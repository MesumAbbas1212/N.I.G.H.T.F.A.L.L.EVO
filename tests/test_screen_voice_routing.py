"""One screenshot must produce exactly one spoken answer.

The app used to answer a screenshot twice: the unified live session (Zephyr)
described it, and the standalone vision module described it again in a second
voice (Charon).  These tests pin the routing rules that keep a single voice:

* the vision module itself only ever speaks with the Zephyr voice;
* a screen request that reaches the live session is answered by that session -
  the separately-voiced module is not started;
* the captured frame is delivered to the live session *after* the tool
  response, as fresh client content.
"""

import asyncio
import importlib
import inspect
import time

import pytest


@pytest.fixture()
def main_module():
    return importlib.import_module("main")


class FakeUI:
    def __init__(self):
        self.muted = False
        self.logs = []
        self.scanning = []

    def write_log(self, message):
        self.logs.append(message)

    def set_state(self, *args, **kwargs):
        pass

    def set_scanning(self, *args, **kwargs):
        self.scanning.append(args)

    def show_hud_operation(self, **kwargs):
        pass

    def update_task_workspace(self, **kwargs):
        pass


class FakeSession:
    def __init__(self):
        self.client_content = []

    async def send_client_content(self, turns, turn_complete=False):
        self.client_content.append(turns)


class FakeFunctionCall:
    def __init__(self, name, args):
        self.name = name
        self.args = args
        self.id = "call-1"


class FakeFunctionResponse:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _assistant(monkeypatch, main, ui=None, session=None):
    assistant = object.__new__(main.NIGHTFALLLive)
    assistant.ui = ui if ui is not None else FakeUI()
    assistant.session = session if session is not None else FakeSession()
    assistant._loop = object()  # truthy: the live session is connected
    assistant._last_user_utterance = "what is on my screen?"
    assistant._pending_screen_frame = None
    assistant.speak = lambda *args, **kwargs: None
    monkeypatch.setattr(main.types, "FunctionResponse", FakeFunctionResponse)
    return assistant


# -- the vision module must not use a second voice --------------------------

def test_vision_module_speaks_with_the_same_voice_as_the_app():
    module = importlib.import_module("actions.screen_processor")
    source = inspect.getsource(module)
    assert module.VISION_VOICE == "Zephyr"
    assert "Charon" not in source


# -- a live session answers screen questions itself -------------------------

def test_screen_tool_hands_the_frame_to_the_live_session(monkeypatch, main_module):
    main = main_module
    assistant = _assistant(monkeypatch, main)

    started = []
    monkeypatch.setattr(main, "screen_process", lambda **kwargs: started.append(kwargs) or True)
    monkeypatch.setattr(
        main.NIGHTFALLLive, "_capture_screen_bytes", lambda self: b"\xff\xd8fake-jpeg"
    )

    response = asyncio.run(
        assistant._execute_tool(FakeFunctionCall("screen_process", {"text": "what is on my screen?"}))
    )

    assert started == [], "the separately-voiced vision module must not run"
    assert assistant._pending_screen_frame == (b"\xff\xd8fake-jpeg", "what is on my screen?")
    assert "Screenshot attached" in response.response["result"]


def test_screen_tool_falls_back_to_the_module_without_a_live_session(monkeypatch, main_module):
    main = main_module
    assistant = _assistant(monkeypatch, main)
    assistant.session = None

    started = []
    monkeypatch.setattr(main, "screen_process", lambda **kwargs: started.append(kwargs) or True)
    monkeypatch.setattr(
        main.NIGHTFALLLive, "_capture_screen_bytes", lambda self: b"\xff\xd8fake-jpeg"
    )

    response = asyncio.run(
        assistant._execute_tool(FakeFunctionCall("screen_process", {"text": "look at my screen"}))
    )

    assert len(started) == 1, "the vision module is the offline fallback"
    assert assistant._pending_screen_frame is None
    assert "vision module will speak" in response.response["result"]


def test_captured_frame_is_delivered_after_the_tool_response(monkeypatch, main_module):
    main = main_module
    session = FakeSession()
    assistant = _assistant(monkeypatch, main, session=session)
    assistant._pending_screen_frame = (b"jpeg-bytes", "what is on my screen?")

    monkeypatch.setattr(main.asyncio, "sleep", lambda *_a, **_k: _noop())

    asyncio.run(assistant._deliver_pending_screen_frame())

    assert len(session.client_content) == 1
    parts = session.client_content[0]["parts"]
    assert parts[0]["inline_data"]["mime_type"] == "image/jpeg"
    assert parts[0]["inline_data"]["data"]
    assert parts[1]["text"] == "what is on my screen?"
    assert assistant._pending_screen_frame is None


async def _noop():
    return None


def test_no_frame_pending_sends_nothing(monkeypatch, main_module):
    main = main_module
    session = FakeSession()
    assistant = _assistant(monkeypatch, main, session=session)
    asyncio.run(assistant._deliver_pending_screen_frame())
    assert session.client_content == []


# -- helpers used by the typed "look at my screen" path ---------------------

def test_capture_prefers_the_ui_capture(monkeypatch, main_module):
    main = main_module
    assistant = _assistant(monkeypatch, main)

    class UIWithCapture(FakeUI):
        def capture_screen_bytes(self):
            return b"from-ui"

    assistant.ui = UIWithCapture()
    called = []
    monkeypatch.setattr(
        main, "screen_process", lambda **kwargs: called.append(kwargs) or True
    )
    assert assistant._capture_screen_bytes() == b"from-ui"


def test_send_screen_to_live_requires_a_session(monkeypatch, main_module):
    main = main_module
    assistant = _assistant(monkeypatch, main)
    assert assistant._live_session_available() is True
    assistant.session = None
    assert assistant._live_session_available() is False


# -- one screenshot, one answer ---------------------------------------------

def test_a_repeated_screen_request_does_not_capture_again(monkeypatch, main_module):
    """The model asking for the screen twice must not produce two answers.

    Real sessions showed three "Executing screen_process" events with three
    descriptions of the same screenshot, because the model called the tool
    again after the frame had already been attached.
    """
    main = main_module
    assistant = _assistant(monkeypatch, main)
    captures = []

    def fake_capture(self):
        captures.append(1)
        return b"\xff\xd8fake-jpeg"

    monkeypatch.setattr(main.NIGHTFALLLive, "_capture_screen_bytes", fake_capture)
    monkeypatch.setattr(main, "screen_process", lambda **kwargs: True)
    monkeypatch.setattr(main.asyncio, "sleep", lambda *a, **k: _noop())

    first = asyncio.run(
        assistant._execute_tool(FakeFunctionCall("screen_process", {"text": "what is on my screen?"}))
    )
    # deliver the frame, as the receive loop does after the tool response
    asyncio.run(assistant._deliver_pending_screen_frame())

    second = asyncio.run(
        assistant._execute_tool(FakeFunctionCall("screen_process", {"text": "what is on my screen?"}))
    )
    asyncio.run(assistant._deliver_pending_screen_frame())

    assert len(captures) == 1, "the second request must reuse the attached frame"
    assert "Screenshot attached" in first.response["result"]
    assert "already received a screenshot" in second.response["result"]


def test_the_cooldown_expires(monkeypatch, main_module):
    main = main_module
    assistant = _assistant(monkeypatch, main)
    captures = []
    monkeypatch.setattr(
        main.NIGHTFALLLive, "_capture_screen_bytes",
        lambda self: captures.append(1) or b"jpeg",
    )
    monkeypatch.setattr(main, "screen_process", lambda **kwargs: True)

    asyncio.run(assistant._execute_tool(FakeFunctionCall("screen_process", {"text": "screen"})))
    # pretend the capture happened long ago
    assistant._last_screen_capture_at -= 60
    assistant._pending_screen_frame = None
    asyncio.run(assistant._execute_tool(FakeFunctionCall("screen_process", {"text": "screen"})))

    assert len(captures) == 2


# -- a user-attached image is never re-captured ------------------------------
# The live model called screen_process even though the user's snip was already
# in the conversation, so the image was attached twice and the assistant
# answered twice. These tests pin the guard at both levels: the tool refuses to
# capture while a user image is current, and the session is told not to ask.

def test_screen_process_defers_to_a_user_attached_image(monkeypatch, main_module):
    main = main_module
    assistant = _assistant(monkeypatch, main)
    assistant._last_user_image_at = time.time()  # the snip is the subject
    captures = []

    monkeypatch.setattr(
        main.NIGHTFALLLive, "_capture_screen_bytes",
        lambda self: captures.append(1) or b"jpeg",
    )
    monkeypatch.setattr(main, "screen_process", lambda **kwargs: True)

    response = asyncio.run(
        assistant._execute_tool(FakeFunctionCall("screen_process", {"text": "translate this"}))
    )

    assert captures == [], "the screen must not be captured again"
    assert assistant._pending_screen_frame is None
    assert "already attached" in response.response["result"].lower()
    assert "do not repeat" in response.response["result"].lower()


def test_the_guard_expires_so_a_later_screen_request_still_works(monkeypatch, main_module):
    main = main_module
    assistant = _assistant(monkeypatch, main)
    assistant._last_user_image_at = time.time() - 600  # long ago
    captures = []

    monkeypatch.setattr(
        main.NIGHTFALLLive, "_capture_screen_bytes",
        lambda self: captures.append(1) or b"jpeg",
    )
    monkeypatch.setattr(main, "screen_process", lambda **kwargs: True)

    asyncio.run(assistant._execute_tool(FakeFunctionCall("screen_process", {"text": "screen"})))

    assert len(captures) == 1, "a fresh screen request must still capture"


def test_the_session_is_told_about_attached_images():
    """The live instruction must forbid screen_process for attached images."""
    import inspect

    import main

    source = inspect.getsource(main.NIGHTFALLLive._build_config)
    assert "ATTACHED IMAGES" in source
    declaration = [t for t in main.TOOL_DECLARATIONS if t["name"] == "screen_process"][0]
    assert "Do NOT call it when the user's message already carries an image" in declaration["description"]
