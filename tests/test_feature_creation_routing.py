from unittest.mock import patch

from main import NIGHTFALLLive, _extract_skill_creation_goal, _looks_like_screen_request


def test_extract_skill_creation_goal():
    assert _extract_skill_creation_goal(
        "make a skill that will allow you to check for windows update"
    ) == "check for windows update"
    assert _extract_skill_creation_goal(
        "could you build me a new feature to summarize local logs"
    ) == "summarize local logs"
    assert _extract_skill_creation_goal("make a skill") == ""
    assert _extract_skill_creation_goal("what is on my screen") is None
    assert not _looks_like_screen_request(
        "make a skill that will allow you to check for windows update"
    )
    assert _looks_like_screen_request("check this window for the error")


def test_explicit_skill_request_precedes_screen_analysis():
    class FakeUI:
        def set_state(self, state):
            pass

        def write_log(self, message):
            pass

        def begin_task_workspace(self, *args, **kwargs):
            pass

    assistant = object.__new__(NIGHTFALLLive)
    assistant.ui = FakeUI()
    assistant._reply_mode = False
    assistant._reset_idle_activity = lambda: None
    spoken = []
    assistant.speak = spoken.append
    forge_goals = []
    assistant._forge_skill = forge_goals.append

    class InlineThread:
        def __init__(self, target, args=(), **kwargs):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    with (
        patch("main._update_memory_async", lambda *args: None),
        patch("main.stop_native_speech"),
        patch("main._looks_like_screen_request", side_effect=AssertionError("screen route reached")),
        patch("main.threading.Thread", InlineThread),
        patch("core.confirm.request", side_effect=AssertionError("confirmation requested")),
    ):
        assistant._on_text_command(
            "make a skill to analyze my screen and check for Windows Update"
        )

    assert forge_goals == ["analyze my screen and check for Windows Update"]
    assert spoken == ["Starting feature creation now."]


def test_forge_completion_is_saved_as_assistant_reply_and_spoken():
    class FakeUI:
        def __init__(self):
            self.logs = []
            self.deliverables = []

        def write_log(self, message):
            self.logs.append(message)

        def show_hud_deliverable(self, **kwargs):
            self.deliverables.append(kwargs)

    assistant = object.__new__(NIGHTFALLLive)
    assistant.ui = FakeUI()
    spoken = []
    assistant.speak = spoken.append

    with patch(
        "core.skill_forge.SkillForge.forge_skill",
        return_value={
            "success": True,
            "name": "windows_update_check",
            "description": "Checks for available Windows updates.",
            "message": "Successfully forged and activated feature.",
        },
    ):
        result = assistant._forge_skill("check for Windows updates")

    assert "windows_update_check" in assistant.ui.logs[0]
    assert assistant.ui.logs[0].startswith("NIGHTFALL Evo:")
    assert spoken == [assistant.ui.logs[0].split(":", 1)[1].strip()]
    assert result == "Successfully forged and activated feature."