import json
import pytest
from unittest.mock import patch, MagicMock

from agent.planner import _planner_system_prompt, PLANNER_PROMPT
from agent.executor import _run_skill_forge, _call_tool
from core.learned_rules import LearnedRulesEngine
from core.dynamic_registry import DynamicToolRegistry


def test_planner_prompt_has_NIGHTFALL_evo_rebrand_and_rules():
    assert "NIGHTFALL Evo" in PLANNER_PROMPT
    assert "NIGHTFALL AI - Lite" not in PLANNER_PROMPT
    assert "Echo HUD" not in PLANNER_PROMPT
    assert "AUTONOMOUS SELF-EVOLUTION" in PLANNER_PROMPT


def test_planner_system_prompt_includes_learned_rules(tmp_path, monkeypatch):
    rules_file = tmp_path / "learned_rules.json"
    monkeypatch.setattr("core.learned_rules.RULES_FILE", rules_file)
    monkeypatch.setattr("core.learned_rules.CONFIG_DIR", tmp_path)

    # Initially empty
    prompt = _planner_system_prompt()
    assert "USER PREFERENCES & LEARNED BEHAVIORAL DIRECTIVES" not in prompt

    # Add rule
    LearnedRulesEngine.add_rule("Always summarize research in bullet points", category="formatting")

    # Injected into prompt
    prompt_with_rule = _planner_system_prompt()
    assert "USER PREFERENCES & LEARNED BEHAVIORAL DIRECTIVES" in prompt_with_rule
    assert "Always summarize research in bullet points" in prompt_with_rule


def test_executor_skill_forge_immediate_execution(monkeypatch):
    executed_args = []

    class MockSkill:
        manifest = {"name": "test_weather_synth", "author": "native"}

    monkeypatch.setattr(
        DynamicToolRegistry,
        "has_tool",
        lambda name: name == "test_weather_synth"
    )
    monkeypatch.setattr(
        DynamicToolRegistry,
        "execute_sync",
        lambda name, args: executed_args.append((name, args)) or "Current weather is 22C Sunny."
    )

    fake_forge_result = {
        "success": True,
        "name": "test_weather_synth",
        "description": "Fetches current weather.",
        "message": "Forged successfully.",
    }

    logs = []
    class FakePlayer:
        def write_log(self, text):
            logs.append(text)

    with patch("core.skill_forge.SkillForge.forge_skill", return_value=fake_forge_result):
        output = _run_skill_forge(
            goal="what is the weather in Tokyo",
            skill_name="test_weather_synth",
            parameters={"query": "Tokyo"},
            player=FakePlayer(),
            speak=None,
        )

    # Assert skill was synthesized AND immediately executed
    assert "test_weather_synth" in output
    assert "Current weather is 22C Sunny." in output
    assert len(executed_args) == 1
    assert executed_args[0][0] == "test_weather_synth"
    assert any("Synthesizing capability" in log for log in logs)


def test_executor_auto_heal_error_recording(monkeypatch):
    from actions.auto_heal_engine import AutoHealEngine
    from agent.executor import AgentExecutor

    captured_errors = []
    monkeypatch.setattr(AutoHealEngine, "record_last_error", lambda tb: captured_errors.append(tb))

    # Mock create_plan to return a step that calls a failing tool
    failing_plan = {
        "steps": [
            {
                "step": 1,
                "tool": "test_crash_tool",
                "description": "Trigger a crash to test Auto-Heal recording",
                "parameters": {},
            }
        ]
    }
    monkeypatch.setattr("agent.executor.create_plan", lambda goal: failing_plan)

    def failing_tool(*args, **kwargs):
        raise ValueError("Simulated tool crash for Auto-Heal test")

    monkeypatch.setattr("agent.executor._call_tool", failing_tool)

    executor = AgentExecutor()
    executor.execute(goal="test auto heal recording", speak=None, player=None)

    assert len(captured_errors) > 0
    assert "Simulated tool crash for Auto-Heal test" in captured_errors[0]
