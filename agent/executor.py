from core.user_paths import get_user_data_dir
import json
import re
import sys
import threading
import subprocess
import tempfile
import os
from pathlib import Path
from typing import Callable, Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent.planner       import create_plan, replan
from agent.error_handler import analyze_error, generate_fix, ErrorDecision


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = get_user_data_dir() / "config" / "api_keys.json"


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]

def _run_skill_forge(
    goal: str,
    skill_name: str | None = None,
    parameters: dict | None = None,
    player: Any = None,
    speak: Callable | None = None,
) -> str:
    from core.skill_forge import SkillForge
    from core.dynamic_registry import DynamicToolRegistry

    if player and hasattr(player, "write_log"):
        player.write_log(f"🧬 [Evolution] Synthesizing capability for: '{goal}'")
    if speak:
        speak("Synthesizing a new capability for this task, sir.")

    result = SkillForge.forge_skill(goal, skill_name)
    message = str(result.get("message") or result.get("error") or "Skill creation failed.")

    if not result.get("success"):
        if player and hasattr(player, "write_log"):
            player.write_log(f"ERR: {message}")
        if speak:
            speak(message)
        return message

    name = str(result.get("name") or skill_name or "new_feature")
    description = str(result.get("description") or "")
    announcement = f"⚡ [NIGHTFALL Evo] Synthesized and activated feature '{name}'. {description}".strip()

    if player and hasattr(player, "write_log"):
        player.write_log(f"NIGHTFALL Evo: {announcement}")

    # Immediately execute the newly forged skill to satisfy the user's initial goal
    execution_output = ""
    try:
        if DynamicToolRegistry.has_tool(name):
            exec_args = dict(parameters or {})
            if "goal" not in exec_args and "query" not in exec_args and "input" not in exec_args:
                exec_args["query"] = goal
                exec_args["goal"] = goal
            if player and hasattr(player, "write_log"):
                player.write_log(f"▶️ [Evolution] Running newly registered skill '{name}'...")
            run_result = DynamicToolRegistry.execute_sync(name, exec_args)
            if run_result:
                if isinstance(run_result, dict):
                    execution_output = str(run_result.get("summary") or run_result.get("output") or run_result.get("text") or run_result).strip()
                else:
                    execution_output = str(run_result).strip()
    except Exception as e_run:
        execution_output = f"(Executed, but returned: {e_run})"

    full_result = announcement
    if execution_output:
        full_result += f"\n\nResult:\n{execution_output}"

    if speak:
        short_res = execution_output if len(execution_output) < 180 else f"{execution_output[:170]}..."
        speak(f"Created and activated feature {name}. {short_res}")

    return full_result


def _inject_context(params: dict, tool: str, step_results: dict, goal: str = "") -> dict:
    if not step_results:
        return params

    params = dict(params)
    if goal:
        params["goal"] = goal

    if tool in ("pdf_document", "create_pdf", "pdf_tools", "word_document", "docx_tools"):
        content = params.get("content", "")
        all_results = [
            v for v in step_results.values()
            if v and len(v) > 80 and v not in ("Done.", "Completed.", "Task completed successfully.")
        ]
        if all_results and (not content or len(content) < 500):
            combined = "\n\n---\n\n".join(all_results)
            translated = _translate_to_goal_language(combined, goal)
            params["content"] = translated
            print(f"[Executor] 💉 Injected research results into {tool}")

    elif tool == "file_controller" and params.get("action") in ("write", "create_file"):
        content = params.get("content", "")
        if not content or len(content) < 50:
            all_results = [
                v for v in step_results.values()
                if v and len(v) > 100 and v not in ("Done.", "Completed.", "Task completed successfully.")
            ]
            if all_results:
                combined = "\n\n---\n\n".join(all_results)
                translated = _translate_to_goal_language(combined, goal)
                params["content"] = translated
                print(f"[Executor] 💉 Injected + translated content into {tool}")

    return params


def _detect_language(text: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel("gemini-3.1-flash-lite")
    try:
        response = model.generate_content(
            f"What language is this text written in? "
            f"Reply with ONLY the language name in English (e.g. Turkish, English, French).\n\n"
            f"Text: {text[:200]}"
        )
        return response.text.strip()
    except Exception:
        return "English"


def _translate_to_goal_language(content: str, goal: str) -> str:
    if not goal:
        return content
    try:
        import google.generativeai as genai
        genai.configure(api_key=_get_api_key())
        model = genai.GenerativeModel("gemini-3.1-flash-lite")

        target_lang = _detect_language(goal)
        print(f"[Executor] 🌐 Translating to: {target_lang}")

        prompt = (
            f"You are a professional translator. "
            f"Translate the following text into {target_lang}.\n"
            f"IMPORTANT:\n"
            f"- Translate EVERYTHING, leave nothing in English\n"
            f"- Keep all facts, numbers, and data intact\n"
            f"- Keep the structure and formatting\n"
            f"- Output ONLY the translated text, nothing else\n\n"
            f"Text to translate:\n{content[:4000]}"
        )
        response = model.generate_content(prompt)
        translated = response.text.strip()
        print(f"[Executor] ✅ Translation done ({target_lang})")
        return translated
    except Exception as e:
        print(f"[Executor] ⚠️ Translation failed: {e}")
        return content

def _call_tool(tool: str, parameters: dict, speak: Callable | None, player: Any = None) -> str:
    # Live Thinking Out Loud Breadcrumb
    params = parameters or {}
    breadcrumb = {
        "web_search": f"Searching: {params.get('query', 'the web')[:30]}...",
        "pdf_document": "Generating PDF document...",
        "word_document": "Formatting Word document...",
        "open_app": f"Launching {params.get('app_name', 'application')}...",
        "browser_control": "Navigating browser...",
        "file_controller": "Managing files...",
        "screen_process": "Inspecting display...",
        "office_builder": "Synthesizing presentation...",
        "system_diagnostics": "Checking system diagnostics...",
    }.get(tool, f"Running {tool}...")

    if player and hasattr(player, "ui"):
        try:
            player.ui.set_state("THINKING", detail=breadcrumb)
            player.ui.write_log(f"THINKING: {breadcrumb}")
        except Exception:
            pass

    if tool in ("pdf_document", "create_pdf", "pdf_tools"):
        from actions.pdf_tools import create_pdf
        p = dict(parameters or {})
        p.setdefault("auto_open", True)
        return create_pdf(parameters=p, player=player) or "PDF created."

    elif tool in ("word_document", "docx_tools"):
        from actions.docx_tools import word_document
        p = dict(parameters or {})
        return word_document(parameters=p, player=player, speak=speak) or "Word document created."

    elif tool == "open_app":
        from actions.open_app import open_app
        return open_app(parameters=parameters, player=player) or "Done."

    elif tool == "web_search":
        from actions.web_search import web_search
        return web_search(parameters=parameters, player=player) or "Done."
    elif tool == "game_updater":
        from actions.game_updater import game_updater
        return game_updater(parameters=parameters, player=player, speak=speak) or "Done."
    elif tool == "browser_control" or tool.startswith("browser_"):
        from actions.browser_control import browser_control
        p = dict(parameters or {})
        if tool.startswith("browser_"):
            p.setdefault("action", tool.replace("browser_", ""))
        return browser_control(parameters=p, player=player) or "Done."

    elif tool == "file_controller":
        from actions.file_controller import file_controller
        return file_controller(parameters=parameters, player=player) or "Done."

    elif tool == "cmd_control":
        from actions.cmd_control import cmd_control
        return cmd_control(parameters=parameters, player=player) or "Done."

    elif tool == "claude_code":
        from actions.claude_code_bridge import run_developer_mode_request
        claude_parameters = dict(parameters or {})
        claude_parameters.setdefault("workspace_path", str(Path.cwd()))
        return run_developer_mode_request(claude_parameters, speak=speak)

    elif tool == "screen_process":
        from actions.screen_processor import screen_process
        screen_process(parameters=parameters, player=player)
        return "Screen captured and analyzed."

    elif tool in ("mobile_autopilot", "android_autopilot"):
        from actions.mobile_autopilot import mobile_autopilot
        return mobile_autopilot(parameters=parameters, player=player) or "Done."

    elif tool == "send_message":
        from actions.send_message import send_message
        return send_message(parameters=parameters, player=None) or "Done."

    elif tool == "reminder":
        from actions.reminder import reminder
        return reminder(parameters=parameters, player=None) or "Done."

    elif tool == "youtube_video":
        from actions.youtube_video import youtube_video
        return youtube_video(parameters=parameters, player=None) or "Done."

    elif tool == "weather_report":
        from actions.weather_report import weather_action
        return weather_action(parameters=parameters, player=None) or "Done."

    elif tool == "computer_settings":
        from actions.computer_settings import computer_settings
        return computer_settings(parameters=parameters, player=None) or "Done."

    elif tool in ("smart_organizer", "desktop_organizer"):
        from actions.desktop_organizer_mcp import smart_organizer
        return smart_organizer(parameters=parameters, player=player) or "Done."

    elif tool == "desktop_control":
        from actions.desktop import desktop_control
        return desktop_control(parameters=parameters, player=None) or "Done."

    elif tool == "computer_control":
        from actions.computer_control import computer_control
        return computer_control(parameters=parameters, player=None) or "Done."

    elif tool == "generated_code":
        description = parameters.get("description", "")
        if not description:
            raise ValueError("generated_code requires a 'description' parameter.")
        from actions.claude_code_bridge import run_developer_mode_request
        return run_developer_mode_request(
            {"description": description, "workspace_path": str(Path.cwd())},
            speak=speak,
        )

    elif tool == "flight_finder":
        from actions.flight_finder import flight_finder
        return flight_finder(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool in ("spotify_controller", "spotify", "music"):
        from actions.spotify_controller import spotify_controller
        return spotify_controller(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool in ("calendar_scheduler", "calendar", "schedule"):
        from actions.calendar_scheduler import calendar_scheduler
        return calendar_scheduler(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool in ("daily_briefing", "briefing"):
        from actions.daily_briefing import daily_briefing
        return daily_briefing(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool in ("code_helper", "code_agent"):
        from actions.code_helper import code_helper
        return code_helper(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "calorie_counter":
        from actions.calorie_counter import run as run_calorie_counter
        return run_calorie_counter(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "pushup_counter":
        from actions.pushup_counter import run as run_pushup_counter
        return run_pushup_counter(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "system_monitor":
        from actions.system_diagnostics_mcp import system_diagnostics
        return system_diagnostics(parameters={"action": "ram_hogs"}, player=None, speak=speak) or "Done."

    elif tool == "upload_video":
        from actions.upload_video import run as run_upload_video
        return run_upload_video(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool in ("presentation_builder", "presentation", "create_presentation"):
        from actions.office_generator import generate_presentation_from_prompt
        topic = parameters.get("topic") or parameters.get("title") or parameters.get("description") or "Presentation"
        return generate_presentation_from_prompt(topic, player=None, speak=speak) or "Presentation created."

    elif tool in ("spreadsheet_builder", "spreadsheet", "create_spreadsheet"):
        from actions.office_generator import generate_spreadsheet_from_prompt
        topic = parameters.get("topic") or parameters.get("title") or parameters.get("description") or "Spreadsheet"
        return generate_spreadsheet_from_prompt(topic, player=None, speak=speak) or "Spreadsheet created."

    elif tool in ("google_workspace", "workspace_gmail", "workspace_calendar", "workspace_drive") or tool.startswith("workspace_"):
        from actions.google_workspace_mcp import google_workspace
        p = dict(parameters or {})
        if tool.startswith("workspace_"):
            parts = tool.split("_", 2)
            if len(parts) > 1:
                p.setdefault("service", parts[1])
            if len(parts) > 2:
                p.setdefault("action", parts[2])
        return google_workspace(parameters=p, player=None, speak=speak) or "Done."

    elif tool in ("system_diagnostics", "diagnostics", "os_hardware", "hardware_control", "ram_hogs", "kill_process", "brightness_control"):
        from actions.system_diagnostics_mcp import system_diagnostics
        p = dict(parameters or {})
        if tool == "ram_hogs":
            p.setdefault("action", "ram_hogs")
        elif tool == "kill_process":
            p.setdefault("action", "kill")
        elif tool == "brightness_control":
            p.setdefault("action", "brightness")
        return system_diagnostics(parameters=p, player=None, speak=speak) or "Done."

    elif tool in ("auto_heal", "self_patch", "rollback"):
        from actions.auto_heal_engine import auto_heal
        p = dict(parameters or {})
        if tool == "rollback":
            p.setdefault("action", "rollback")
        return auto_heal(parameters=p, player=None, speak=speak) or "Done."

    elif tool == "circuit_assembler":
        from actions.circuit_assembler import circuit_assembler
        result = circuit_assembler(parameters=parameters or {}, player=player, speak=speak)
        return result.get("summary", "Circuit schematic ready.") if isinstance(result, dict) else str(result or "Circuit schematic ready.")

    elif tool == "geospatial_globe":
        from core.globe_window import GlobeWindow
        parent = getattr(player, "_win", None) if player else None
        globe = GlobeWindow.get_instance(parent=parent)
        action = str((parameters or {}).get("action", "open")).lower()
        p = parameters or {}
        location = p.get("location") or "current"
        if action == "route":
            result = globe.show_route(p.get("origin", ""), p.get("destination", ""))
            return f"Flight route: {result.get('origin')} to {result.get('destination')}, {result.get('distance_km')} km."
        if action == "drive":
            result = globe.show_driving_route(p.get("origin", ""), p.get("destination", ""))
            return f"Driving route: {result.get('origin')} to {result.get('destination')}, {result.get('distance_km')} km."
        if action == "weather":
            return str(globe.show_weather(location))
        if action == "flights":
            return f"Showing {len(globe.show_live_flights(p.get('location')))} live aircraft."
        if action == "iss":
            return str(globe.show_iss_tracker())
        if action == "earthquakes":
            return f"Showing {len(globe.show_earthquakes(float(p.get('min_magnitude', 2.5))))} earthquakes."
        if action == "nearby":
            return f"Found {len(globe.show_nearby(p.get('query') or 'hospitals', p.get('location')))} nearby places."
        if action == "radar":
            return f"Weather radar {'enabled' if globe.toggle_weather_radar(bool(p.get('enable', True))) else 'disabled'}."
        if action == "location":
            return str(globe.show_location(location))
        if action == "fly_to":
            return str(globe.fly_to(location))
        globe.open_globe(p.get("location"))
        return "Opened the interactive map."

    elif tool == "call_screening":
        p = parameters or {}
        action = str(p.get("action", "start")).lower()
        from actions.call_assistant import hang_up_active_call, start_call_proxy, take_over_active_call
        if action == "take_over":
            take_over_active_call()
            return "Taking over the screened call."
        if action == "hang_up":
            hang_up_active_call()
            return "Ended the screened call."
        from core.confirm import request
        event = {"title": p.get("caller") or "Incoming call", "app": p.get("app") or "Phone / Call"}
        return request(
            "start-call-screening",
            "Answer this call as NIGHTFALL Evo",
            f"NIGHTFALL will answer {event['title']} in {event['app']} and prepare a transcript and summary.",
            lambda: (start_call_proxy(event, ui=player, speak_fn=speak) and "Call screening started."),
        )

    elif tool == "skill_forge":
        p = parameters or {}
        if str(p.get("action", "forge")).lower() == "list":
            from core.dynamic_registry import DynamicToolRegistry
            return "Installed skills: " + ", ".join(item["name"] for item in DynamicToolRegistry.list_skills())
        goal = str(p.get("goal", "")).strip()
        if not goal:
            return "Describe the capability you want NIGHTFALL to learn."
        return _run_skill_forge(
            goal=goal,
            skill_name=p.get("skill_name"),
            parameters=p.get("arguments") or p,
            player=player,
            speak=speak,
        )

    elif tool == "dynamic_skill":
        p = parameters or {}
        from core.dynamic_registry import DynamicToolRegistry
        if str(p.get("action", "list")).lower() == "list":
            return "Installed skills: " + ", ".join(item["name"] for item in DynamicToolRegistry.list_skills())
        name = str(p.get("skill_name", "")).strip()
        if not DynamicToolRegistry.has_tool(name):
            return f"No active skill named '{name}'."
        run_res = DynamicToolRegistry.execute_sync(name, p.get("arguments") or p or {})
        if isinstance(run_res, dict):
            out_str = str(run_res.get("summary") or run_res.get("output") or run_res.get("text") or run_res).strip()
        else:
            out_str = str(run_res).strip()
        if player and hasattr(player, "write_log"):
            player.write_log(f"NIGHTFALL Evo [{name}]:\n{out_str}")
        return out_str

    else:
        try:
            from core.dynamic_registry import DynamicToolRegistry
            if DynamicToolRegistry.has_tool(tool):
                run_res = DynamicToolRegistry.execute_sync(tool, parameters or {})
                if isinstance(run_res, dict):
                    out_str = str(run_res.get("summary") or run_res.get("output") or run_res.get("text") or run_res).strip()
                else:
                    out_str = str(run_res).strip()
                if player and hasattr(player, "write_log"):
                    player.write_log(f"NIGHTFALL Evo [{tool}]:\n{out_str}")
                return out_str
        except Exception as exc:
            return f"Feature '{tool}' failed: {exc}"
        print(f"[Executor] ⚠️ Unknown tool '{tool}' — no developer fallback is configured")
        return f"Unknown action: {tool}"

class AgentExecutor:

    MAX_REPLAN_ATTEMPTS = 2

    def execute(
        self,
        goal:        str,
        speak:       Callable | None        = None,
        cancel_flag: threading.Event | None = None,
        player:      Any                    = None,
    ) -> str:
        print(f"\n[Executor] 🎯 Goal: {goal}")

        replan_attempts = 0
        completed_steps = []
        step_results    = {} 
        plan            = create_plan(goal)

        while True:
            steps = plan.get("steps", [])

            if not steps:
                msg = "I couldn't create a valid plan for this task, sir."
                if speak: speak(msg)
                return msg

            success      = True
            failed_step  = None
            failed_error = ""

            for step in steps:
                if cancel_flag and cancel_flag.is_set():
                    if speak: speak("Task cancelled, sir.")
                    return "Task cancelled."

                step_num = step.get("step", "?")
                tool     = step.get("tool", "generated_code")
                desc     = step.get("description", "")
                params   = step.get("parameters", {})

                params = _inject_context(params, tool, step_results, goal=goal)

                print(f"\n[Executor] ▶️ Step {step_num}: [{tool}] {desc}")

                # Update HUD right telemetry wing with live step operation
                if player and hasattr(player, "show_hud_operation"):
                    step_sources = []
                    if "query" in params:
                        step_sources.append(str(params["query"]))
                    if "url" in params:
                        step_sources.append(str(params["url"]))
                    if "path" in params or "output_path" in params:
                        step_sources.append(str(params.get("path") or params.get("output_path")))
                    player.show_hud_operation(
                        f"Step {step_num}: {tool.replace('_', ' ').title()}",
                        desc or f"Executing {tool}...",
                        sources=step_sources,
                        tool=tool
                    )

                attempt = 1
                step_ok = False

                while attempt <= 3:
                    if cancel_flag and cancel_flag.is_set():
                        break
                    try:
                        result = _call_tool(tool, params, speak, player=player)
                        step_results[step_num] = result 
                        completed_steps.append(step)
                        print(f"[Executor] ✅ Step {step_num} done: {str(result)[:100]}")
                        step_ok = True

                        # Check if this step produced a deliverable file
                        import re
                        file_match = re.search(r'([A-Za-z]:\\[^\s"\'<>`\r\n]+\.(?:pdf|docx|xlsx|pptx|png|jpg|mp4|py|html|json|txt))', str(result))
                        if not file_match and ("path" in params or "output_path" in params or "name" in params):
                            cand = str(params.get("output_path") or params.get("path") or params.get("name") or "")
                            if cand.lower().endswith((".pdf", ".docx", ".xlsx", ".pptx")):
                                cand_path = Path(cand).expanduser()
                                if not cand_path.is_absolute():
                                    cand_path = Path.home() / "Downloads" / cand_path.name
                                if cand_path.exists():
                                    file_match = re.match(r'.*', str(cand_path))
                        if file_match and player and hasattr(player, "show_hud_deliverable"):
                            fpath = file_match.group(0).strip()
                            player.show_hud_deliverable(
                                title=Path(fpath).name,
                                summary=f"Deliverable created by step {step_num}.",
                                file_path=fpath,
                                kind="file"
                            )
                        break

                    except Exception as e:
                        error_msg = str(e)
                        try:
                            import traceback
                            from actions.auto_heal_engine import AutoHealEngine
                            AutoHealEngine.record_last_error(traceback.format_exc())
                        except Exception:
                            pass
                        print(f"[Executor] ❌ Step {step_num} attempt {attempt} failed: {error_msg}")

                        recovery = analyze_error(step, error_msg, attempt=attempt)
                        decision = recovery["decision"]
                        user_msg = recovery.get("user_message", "")

                        if speak and user_msg:
                            speak(user_msg)

                        if decision == ErrorDecision.RETRY:
                            attempt += 1
                            import time; time.sleep(2)
                            continue

                        elif decision == ErrorDecision.SKIP:
                            print(f"[Executor] ⏭️ Skipping step {step_num}")
                            completed_steps.append(step)
                            step_ok = True
                            break

                        elif decision == ErrorDecision.ABORT:
                            msg = f"Task aborted, sir. {recovery.get('reason', '')}"
                            if speak: speak(msg)
                            return msg

                        else: 
                            fix_suggestion = recovery.get("fix_suggestion", "")
                            if fix_suggestion and tool != "generated_code":
                                try:
                                    fixed_step = generate_fix(step, error_msg, fix_suggestion)
                                    if speak: speak("Trying an alternative approach, sir.")
                                    res = _call_tool(
                                        fixed_step["tool"],
                                        fixed_step["parameters"],
                                        speak,
                                        player=player
                                    )
                                    step_results[step_num] = res
                                    completed_steps.append(step)
                                    step_ok = True
                                    break
                                except Exception as fix_err:
                                    print(f"[Executor] ⚠️ Fix failed: {fix_err}")

                            failed_step  = step
                            failed_error = error_msg
                            success      = False
                            break

                if not step_ok and not failed_step:
                    failed_step  = step
                    failed_error = "Max retries exceeded"
                    success      = False

                if not success:
                    break

            if success:
                summary = self._summarize(goal, completed_steps, speak)
                if player and hasattr(player, "show_hud_deliverable"):
                    import re
                    found_file = None
                    for res_text in step_results.values():
                        fm = re.search(r'([A-Za-z]:\\[^\s"\'<>`\r\n]+\.(?:pdf|docx|xlsx|pptx|png|jpg|mp4|py|html|json|txt))', str(res_text))
                        if fm and Path(fm.group(0).strip()).exists():
                            found_file = fm.group(0).strip()
                            break
                    bullets = [s.get("description", "") for s in completed_steps if s.get("description")]
                    player.show_hud_deliverable(
                        title=Path(found_file).name if found_file else goal[:40],
                        summary=summary,
                        bullets=bullets[:4],
                        file_path=found_file or "",
                        kind="file" if found_file else "result"
                    )
                return summary

            if replan_attempts >= self.MAX_REPLAN_ATTEMPTS:
                msg = f"Task failed after {replan_attempts} replan attempts, sir."
                if speak: speak(msg)
                return msg

            if speak: speak("Adjusting my approach, sir.")

            replan_attempts += 1
            plan = replan(goal, completed_steps, failed_step, failed_error)

    def _summarize(self, goal: str, completed_steps: list, speak: Callable | None) -> str:
        fallback = f"All done, sir. Completed {len(completed_steps)} steps for: {goal[:60]}."
        try:
            import google.generativeai as genai
            genai.configure(api_key=_get_api_key())
            model = genai.GenerativeModel(model_name="gemini-2.5-flash")
            steps_str = "\n".join(f"- {s.get('description', '')}" for s in completed_steps)
            prompt    = (
                f'User goal: "{goal}"\n'
                f"Completed steps:\n{steps_str}\n\n"
                "Write a single natural sentence summarizing what was accomplished. "
                "Address the user as 'sir'. Be direct and positive."
            )
            response = model.generate_content(prompt)
            summary  = response.text.strip()
            if speak: speak(summary)
            return summary
        except Exception:
            if speak: speak(fallback)
            return fallback
