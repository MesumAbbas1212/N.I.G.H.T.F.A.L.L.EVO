from core.user_paths import get_user_data_dir
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = get_user_data_dir() / "config" / "api_keys.json"


PLANNER_PROMPT = """You are the planning module of NIGHTFALL Evo, an autonomous, self-evolving AI assistant.
Your job: break any user goal into a sequence of steps using ONLY the tools listed below.

ABSOLUTE RULES:
- NEVER use generated_code or write Python scripts directly.
- NEVER reference previous step results in parameters. Every step is independent.
- AUTONOMOUS SELF-EVOLUTION: When the user asks to create, build, or add a skill/feature, OR when the user asks for a capability, specialized query, calculation, or automation not covered by any available tool, use skill_forge with action="forge", goal="<the required capability>", and skill_name="<descriptive_name>". NIGHTFALL Evo will autonomously synthesize, sandbox-test in Crucible, and hot-load the new capability on the fly.
- Use web_search for ANY information retrieval, research, or current data.
- Use pdf_document to create, compile, or generate PDF files (NEVER use file_controller for .pdf files).
- Use word_document to create or generate Word (.docx) documents.
- Use file_controller to save text/code files to disk.
- Use cmd_control to open files or run system commands.
- Max 5 steps. Use the minimum steps needed.

AVAILABLE TOOLS AND THEIR PARAMETERS:

pdf_document
  action: "create" | "convert" | "create_letter" (default: create)
  title: string (required) — clear descriptive title for the PDF document
  subtitle: string (optional) — brief subtitle
  content: string (required) — full report or summary text to format into the PDF
  output_path: string (optional, e.g. "downloads" or full path ending in .pdf)
  auto_open: boolean (optional, default: true)

word_document
  action: "create" | "create_report" | "convert" (default: create)
  title: string (required) - clear descriptive document title
  content: string (optional) - the FINISHED document text; if omitted or too short,
    NIGHTFALL writes the full document content itself from the title/topic, so a
    detailed report only needs action + title. NEVER put a description, outline, or
    summary of the document in content.
  sections: list of {heading, body, bullets} (optional) - structured report body
  output_path: string (optional)
  Use word_document for Word/.docx/report/letter requests. Always emit the step -
  never answer the user with a description of the document instead.

open_app
  app_name: string (required)

web_search
  query: string (required) — write a clear, focused search query
  mode: "search" or "compare" (optional, default: search)
  items: list of strings (optional, for compare mode)
  aspect: string (optional, for compare mode)

game_updater
  action: "update" | "install" | "list" | "download_status" | "schedule" (required)
  platform: "steam" | "epic" | "both" (optional, default: both)
  game_name: string (optional)
  app_id: string (optional)
  shutdown_when_done: boolean (optional)

browser_control
  action: "go_to" | "search" | "click" | "hover" | "type" | "press" | "scroll" | "fill_form" | "snapshot" | "find" | "evaluate" | "screenshot" | "tabs" | "wait_for" | "select_option" | "upload" | "close" (required)
  url: string (for go_to / navigate)
  query: string (for search / find)
  text: string (for click / type / wait_for)
  selector: string (CSS selector or element ref e.g. "e2")
  element: string (element ref from snapshot)
  fields: dict or list of fields (for fill_form)
  direction: "up" | "down" (for scroll)
  key: string (for press, e.g. "Enter", "Tab", "Escape")
  expression: string (for evaluate, JavaScript expression)
  path: string (for screenshot / upload)

file_controller
  action: "write" | "create_file" | "read" | "list" | "delete" | "move" | "copy" | "find" | "disk_usage" (required)
  path: string — use "desktop" for Desktop folder
  name: string — filename
  content: string — file content (for write/create_file)

cmd_control
  task: string (required) — natural language description of what to do
  visible: boolean (optional)

computer_settings
  action: string (required)
  description: string — natural language description
  value: string (optional)

computer_control
  action: "type" | "click" | "hotkey" | "press" | "scroll" | "screenshot" | "screen_find" | "screen_click" (required)
  text: string (for type)
  x, y: int (for click)
  keys: string (for hotkey, e.g. "ctrl+c")
  key: string (for press)
  direction: "up" | "down" (for scroll)
  description: string (for screen_find/screen_click)

screen_process
  text: string (required) — what to analyze or ask about the screen
  angle: "screen" | "camera" (optional)

send_message
  receiver: string (required for DMs)
  message_text: string (required for DMs; optional caption for uploads)
  platform: string (required)
  mode: "dm" | "upload" (optional; use upload for Instagram media posts)
  media_path: string (optional; required for Instagram uploads)

reminder
  date: string YYYY-MM-DD (required)
  time: string HH:MM (required)
  message: string (required)

desktop_control
  action: "wallpaper" | "organize" | "preview" | "clean" | "undo" | "list" | "task" (required)
  path: string (optional)
  task: string (optional)

smart_organizer
  action: "preview" | "organize" | "undo" | "find_duplicates" | "clean_empty_folders" | "archive_old" (required)
  target: "desktop" | "downloads" | "documents" | "pictures" | string folder path (optional)
  mode: "by_type" | "by_date" (optional)
  days: integer (optional)

youtube_video
  action: "play" | "summarize" | "trending" (required)
  query: string (for play)

weather_report
  city: string (required)

flight_finder
  origin: string (required)
  destination: string (required)
  date: string (required)

spotify_controller
  action: "play" | "pause" | "toggle" | "next" | "previous" | "volume_up" | "volume_down" | "set_volume" | "search_play" | "open_spotify" | "get_now_playing" | "get_playlists" | "get_queue" | "get_devices" | "auth" (required)
  query: string (for search_play, song or artist name)
  volume: number (for set_volume)
  device_id: string (optional Spotify Connect target)

calendar_scheduler
  action: "add_event" | "list_events" | "check_day" | "delete_event" | "get_upcoming" | "export_ics" (required)
  title: string (for add_event)
  date: string (YYYY-MM-DD or "today", "tomorrow")
  time: string (HH:MM)
  duration_minutes: number (optional, default: 30)
  location: string (optional)

daily_briefing
  category: "all" | "tech" | "world" (optional)
  Use whenever user asks for their morning briefing, daily briefing, or news update.

claude_code
  description: string (required)
  workspace_path: string (optional)
  Use for all coding, website, project, file-editing, and developer requests.

presentation_builder
  topic: string (required) — topic, theme or title of the presentation
  theme: "corporate" | "neon" | "luxury" | "academic" | "sunset" | "creative" (optional)
  Use whenever user asks to create, build, design or make a presentation, ppt, pitch deck, or slideshow.

spreadsheet_builder
  topic: string (required) — purpose, topic or title of the spreadsheet
  Use whenever user asks to create, build, or generate a spreadsheet, Excel sheet, budget, tracker, or workbook.

google_workspace
  service: "gmail" | "calendar" | "drive" (required)
  action: "list" | "read" | "send" | "unread" | "search" | "create" | "delete" | "upload" (required)
  query: string (for email or drive search)
  message_id: string (for reading email)
  to: string (for sending email)
  subject: string (for email subject)
  body: string (for email body)
  title: string (for calendar event)
  date: string (for calendar date YYYY-MM-DD or today/tomorrow)
  time: string (for calendar time HH:MM)
  duration_minutes: number (optional, default: 30)
  filename: string (for drive file read/search)
  path: string (for drive upload)
  Use whenever user asks to check/send emails, read/search Gmail, check/schedule calendar meetings, or search Google Drive files.

system_diagnostics
  action: "status" | "ram_hogs" | "cpu_hogs" | "kill" | "brightness" | "battery" | "disk" (required)
  target: string (for kill, process name e.g. "chrome" or PID)
  level: number (for brightness, 0-100)
  monitor: string | number (for brightness, optional target monitor)
  relative: boolean (optional, if level is +10 or -10)
  limit: number (for ram_hogs/cpu_hogs, default: 5)
  Use whenever user asks to check RAM usage, find RAM/CPU hogs, check battery health, adjust screen brightness, terminate/kill frozen apps, or get full hardware telemetry.

auto_heal
  action: "status" | "heal" | "history" | "rollback" | "learn_rule" | "list_rules" (required)
  error_traceback: string (optional, exception traceback or error to repair)
  rule_text: string (optional, for learn_rule)
  category: string (optional, for learn_rule: general, formatting, workflow, habit)
  patch_id: string (optional, for rollback)
  Use whenever user asks to fix an error/bug, heal/patch NIGHTFALL, undo/rollback a patch, view patch history, or remember a permanent rule/behavioral preference.

circuit_assembler
  action: "assemble_components" | "analyze_screen" | "show_schematic" (required)
  components: string (optional)
  query: string (optional)
  Use to design safe wiring instructions and an interactive circuit schematic.

geospatial_globe
  action: "open" | "route" | "drive" | "location" | "fly_to" | "weather" | "flights" | "iss" | "earthquakes" | "nearby" | "radar" (required)
  origin, destination: strings (for routes)
  location: string (optional)
  query: string (for nearby places)
  Use for interactive globe maps, flight tracking, road routes, weather, earthquakes, nearby places, or ISS tracking.

call_screening
  action: "start" | "take_over" | "hang_up" (required)
  caller: string (optional)
  app: string (optional)
  Answering a call always requires the user to confirm on the NIGHTFALL Evo HUD.

skill_forge
  action: "forge" | "list" (required)
  goal: string (for forge)
  skill_name: string (optional)
  Use whenever user asks to create a new skill or feature, OR whenever a task cannot be solved by any existing tool. NIGHTFALL Evo will autonomously synthesize, sandbox-verify in Crucible, and hot-load the feature.

dynamic_skill
  action: "list" | "run" (required)
  skill_name: string (for run)
  arguments: object (optional)
  Running a generated skill requires user confirmation.

mobile_autopilot
  instruction: string (required) — what to do on the phone
  target: string (optional) — phone device name
  Use whenever user asks to do complex multi-step workflows on their phone (e.g., "open Instagram and message X", "play jazz on youtube on my phone").

EXAMPLES:

Goal: "research mechanical engineering and save it to a notepad file"
Steps:

web_search | query: "mechanical engineering overview definition history"
web_search | query: "mechanical engineering applications and future trends"
file_controller | action: write, path: desktop, name: mechanical_engineering.txt, content: "MECHANICAL ENGINEERING RESEARCH\n\nThis file will be filled with web research results."
cmd_control | task: "open mechanical_engineering.txt on desktop with notepad"

Goal: "What is the price of Bitcoin"
Steps:

web_search | query: "Bitcoin price today USD"

Goal: "List the files on the desktop and find the largest 5 files"
Steps:

file_controller | action: list, path: desktop
file_controller | action: largest, path: desktop, count: 5

Goal: "Install PUBG from Steam"
Steps:

game_updater | action: install, platform: steam, game_name: "PUBG"

Goal: "Update all my Steam games"
Steps:

game_updater | action: update, platform: steam

Goal: "Send John a message on WhatsApp saying there is a meeting tomorrow"
Steps:

send_message | receiver: John, message_text: "There is a meeting tomorrow", platform: WhatsApp

Goal: "Open the clock and set a reminder for 30 minutes later"
Steps:

reminder | date: [today], time: [now+30min], message: "Reminder"

Goal: "Build a premium website for my AI assistant"
Steps:

Goal: "Play Starboy song on Spotify"
Steps:

spotify_controller | action: search_play, query: "Starboy"

Goal: "Play some relaxing music"
Steps:

spotify_controller | action: search_play, query: "relaxing music"

Goal: "Pause the music"
Steps:

spotify_controller | action: pause

Goal: "Skip to next song"
Steps:

spotify_controller | action: next

Goal: "Add team sync to my calendar tomorrow at 4pm"
Steps:

calendar_scheduler | action: add_event, title: "Team sync", date: "tomorrow", time: "16:00", duration_minutes: 30

OUTPUT — return ONLY valid JSON, no markdown, no explanation, no code blocks:
{
  "goal": "...",
  "steps": [
    {
      "step": 1,
      "tool": "tool_name",
      "description": "what this step does",
      "parameters": {},
      "critical": true
    }
  ]
}
"""


def _planner_system_prompt() -> str:
    prompt = PLANNER_PROMPT

    # 1. Inject learned behavioral directives from user
    try:
        from core.learned_rules import LearnedRulesEngine
        rules_text = LearnedRulesEngine.get_prompt_injections()
        if rules_text:
            prompt += f"\n\n{rules_text}"
    except Exception as exc:
        print(f"[Planner] Learned rules injection unavailable: {exc}")

    # 2. Inject dynamically registered feature tools
    try:
        from core.dynamic_registry import DynamicToolRegistry
        declarations = DynamicToolRegistry.get_tool_declarations()
        reserved = {"spotify", "spotify_controller", "music", "flight_finder"}
        seen = set(reserved)
        lines = []
        for tool in declarations:
            name = tool.get("name", "")
            if not name or name in seen:
                continue
            seen.add(name)
            params = json.dumps(tool.get("parameters", {}), ensure_ascii=True)
            lines.append(f"{name}: {tool.get('description', '')} Parameters: {params}")
        if lines:
            prompt += "\n\nADDITIONAL REGISTERED FEATURE TOOLS:\n" + "\n".join(lines)
    except Exception as exc:
        print(f"[Planner] Dynamic feature listing unavailable: {exc}")

    return prompt


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _looks_like_website_goal(goal: str) -> bool:
    text = (goal or "").lower()
    return any(token in text for token in (
        "website",
        "landing page",
        "landingpage",
        "portfolio",
        "business site",
        "product site",
        "marketing site",
        "homepage",
        "web page",
        "site for",
        "build a site",
        "create a site",
        "create a website",
    ))


def _rewrite_generated_step(step: dict, goal: str) -> None:
    if step.get("tool") != "generated_code":
        return
    desc = step.get("description", goal) or goal
    if _looks_like_website_goal(goal):
        print(f"[Planner] ⚠️ generated_code detected in step {step.get('step')} — replacing with claude_code")
        step["tool"] = "claude_code"
        step["parameters"] = {
          "description": desc[:1200],
        }
        return
    print(f"[Planner] ⚠️ generated_code detected in step {step.get('step')} — replacing with web_search")
    step["tool"] = "web_search"
    step["parameters"] = {"query": desc[:200]}


def create_plan(goal: str, context: str = "") -> dict:
    import google.generativeai as genai

    genai.configure(api_key=_get_api_key())
    system_prompt = _planner_system_prompt()
    candidates = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-latest"]
    
    user_input = f"Goal: {goal}"
    if context:
        user_input += f"\n\nContext: {context}"

    text = None
    for model_name in candidates:
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=system_prompt
            )
            response = model.generate_content(user_input)
            if response.text:
                text = response.text.strip()
                break
        except Exception as e:
            print(f"[Planner] ⚠️ {model_name} planning error: {e}")
            continue

    if not text:
        return _fallback_plan(goal)

    try:
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        plan = json.loads(text)

        if "steps" not in plan or not isinstance(plan["steps"], list):
            raise ValueError("Invalid plan structure")

        for step in plan["steps"]:
            _rewrite_generated_step(step, goal)

        print(f"[Planner] ✅ Plan: {len(plan['steps'])} steps")
        for s in plan["steps"]:
            print(f"  Step {s['step']}: [{s['tool']}] {s['description']}")

        return plan

    except json.JSONDecodeError as e:
        print(f"[Planner] ⚠️ JSON parse failed: {e}")
        return _fallback_plan(goal)
    except Exception as e:
        print(f"[Planner] ⚠️ Planning failed: {e}")
        return _fallback_plan(goal)


def _fallback_plan(goal: str) -> dict:
    print("[Planner] 🔄 Fallback plan")
    if _looks_like_website_goal(goal):
        return {
          "goal": goal,
          "steps": [
            {
              "step": 1,
              "tool": "claude_code",
              "description": f"Create the requested website with Claude Code: {goal}",
              "parameters": {"description": goal},
              "critical": True,
            }
          ],
        }
    return {
        "goal": goal,
        "steps": [
            {
                "step": 1,
                "tool": "web_search",
                "description": f"Search for: {goal}",
                "parameters": {"query": goal},
                "critical": True
            }
        ]
    }


def replan(goal: str, completed_steps: list, failed_step: dict, error: str) -> dict:
    import google.generativeai as genai

    genai.configure(api_key=_get_api_key())
    system_prompt = _planner_system_prompt()
    candidates = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-latest"]

    completed_summary = "\n".join(
        f"  - Step {s['step']} ({s['tool']}): DONE" for s in completed_steps
    )

    prompt = f"""Goal: {goal}

Already completed:
{completed_summary if completed_summary else '  (none)'}

Failed step: [{failed_step.get('tool')}] {failed_step.get('description')}
Error: {error}

Create a REVISED plan for the remaining work only. Do not repeat completed steps."""

    text = None
    for model_name in candidates:
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=system_prompt
            )
            response = model.generate_content(prompt)
            if response.text:
                text = response.text.strip()
                break
        except Exception as e:
            print(f"[Planner] ⚠️ {model_name} replan error: {e}")
            continue

    try:
        if not text:
            raise ValueError("All models failed during replanning")
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        plan = json.loads(text)

        for step in plan.get("steps", []):
            _rewrite_generated_step(step, goal)

        print(f"[Planner] 🔄 Revised plan: {len(plan['steps'])} steps")
        return plan
    except Exception as e:
        print(f"[Planner] ⚠️ Replan failed: {e}")
        return _fallback_plan(goal)
