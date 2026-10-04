from core.user_paths import get_user_data_dir
import os

# Hardware acceleration & WebGL flags for smooth 180fps+ rendering in Chromium
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--enable-gpu-rasterization --enable-zero-copy --ignore-gpu-blocklist --enable-accelerated-2d-canvas --enable-webgl --enable-webgl2-compute-context --disable-frame-rate-limit --disable-gpu-vsync --num-raster-threads=4 --use-angle=d3d11 --disable-gpu-driver-bug-workarounds"
)

try:
    from PyQt6.QtCore import QCoreApplication, Qt
    from PyQt6.QtGui import QSurfaceFormat
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    fmt = QSurfaceFormat.defaultFormat()
    fmt.setSwapInterval(0)
    QSurfaceFormat.setDefaultFormat(fmt)
except Exception:
    pass

from core import undo as undo_stack
from core import audio_devices
from core.echo import EchoGuard
from core.hotkey import PushToTalk
from memory import config_manager
from memory.memory_manager import search_memory

import core.boot_sentry
from core.sensorium import sensorium
from core.protocols import protocols
from core.local_brain import local_brain
import asyncio
import threading
import json
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
import os
import pyperclip
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import sounddevice as sd
from google import genai
from google.genai import types
from ui import NIGHTFALLUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    should_extract_memory, extract_memory, auto_learn_interaction
)

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import screen_process
from actions.meeting_assistant import MeetingAssistant
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.office_builder     import create_presentation, create_spreadsheet
from actions.docx_tools        import word_document
from actions.pdf_tools         import create_pdf
from actions.nightfall_connect    import (
    connect_list_devices,
    connect_get_device,
    connect_get_capabilities,
    connect_execute,
    connect_pair_device,
    connect_disconnect_device,
)
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from actions.attention_monitor import AttentionMonitor, speak_native, stop_native_speech, handle_call_action, read_event_preview, set_speech_sink
# from actions.daily_briefing import compile_daily_briefing
from llm_client import client as openrouter_client
from workspace_store import store as workspace_store
from smart_home.service import SmartHomeService
from plugin_manager import PluginManager
from updater import restart_application, update_from_github

try:
    from dashboard.server import DashboardServer
except Exception:
    DashboardServer = None

try:
    from actions.instagram_mcp import start_daemon as start_ig_daemon, set_ig_prompt_callback
except ImportError:
    try:
        from actions.instagram_chat import start_daemon as start_ig_daemon, set_ig_prompt_callback
    except ImportError:
        start_ig_daemon = None

try:
    from nightfall_connect.service import get_service as get_nightfall_connect_service
except Exception:
    get_nightfall_connect_service = None


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = get_user_data_dir() / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
STARTUP_LOG     = Path(os.environ.get("LOCALAPPDATA", str(BASE_DIR))) / "nightfall evo" / "startup.log"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024
LIVE_CONNECT_TIMEOUT = 12


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((host, port)) == 0


def _startup_log(message: str) -> None:
    try:
        STARTUP_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(STARTUP_LOG, "a", encoding="utf-8") as f:
            f.write(message + "\n")
    except Exception:
        pass


def _ensure_desktop_shortcut() -> None:
    if os.name != "nt":
        return

    marker_path = get_user_data_dir() / "config" / ".desktop_shortcut_created"
    if marker_path.exists():
        return

    try:
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
            desktop_raw, _ = winreg.QueryValueEx(key, "Desktop")
            winreg.CloseKey(key)
            desktop_dir = Path(os.path.expandvars(desktop_raw))
        except Exception:
            desktop_dir = Path(os.path.expanduser("~")) / "Desktop"
            
        desktop_dir.mkdir(parents=True, exist_ok=True)
        shortcut_path = desktop_dir / "NIGHTFALL Evo.lnk"
        script_path = BASE_DIR / "main.py"
        icon_path = BASE_DIR / "assets" / "NIGHTFALL_Lite_Logo.ico"

        if not icon_path.exists():
            icon_path = None

        python_exe = sys.executable
        if not python_exe:
            python_exe = shutil.which("python") or shutil.which("py") or "python"

        shortcut_target = python_exe
        shortcut_args = f'"{script_path}"'
        if getattr(sys, "frozen", False):
            shortcut_target = python_exe
            shortcut_args = ""

        powershell_exe = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell_exe is None:
            raise RuntimeError("PowerShell is not available")

        def _ps_escape(value: str) -> str:
            return value.replace("'", "''")

        icon_value = str(icon_path) if icon_path and icon_path.exists() else ""
        ps1_path = get_user_data_dir() / "config" / "create_desktop_shortcut.ps1"
        ps1_script = "\n".join([
            "$WshShell = New-Object -ComObject WScript.Shell",
            f"$Shortcut = $WshShell.CreateShortcut('{_ps_escape(str(shortcut_path))}')",
            f"$Shortcut.TargetPath = '{_ps_escape(shortcut_target)}'",
            f"$Shortcut.Arguments = '{_ps_escape(shortcut_args)}'",
            f"$Shortcut.WorkingDirectory = '{_ps_escape(str(BASE_DIR))}'",
            "$Shortcut.WindowStyle = 1",
            "$Shortcut.Description = 'Launch NIGHTFALL Evo'",
            f"if ('{_ps_escape(icon_value)}') {{ $Shortcut.IconLocation = '{_ps_escape(icon_value)},0' }}",
            "$Shortcut.Save()",
        ])
        ps1_path.write_text(ps1_script, encoding="utf-8")

        subprocess.run(
            [powershell_exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        marker_path.write_text("created", encoding="utf-8")
        _startup_log(f"desktop shortcut created at {shortcut_path}")
    except Exception as exc:
        _startup_log(f"desktop shortcut creation skipped: {exc}")


def _load_system_prompt() -> str:
    try:
        base_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        base_prompt = (
            "You are NIGHTFALL Evo, a calm, direct, and professional AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool. "
            "If the user asks to create, build, launch, or open a website, always use the selected workspace folder."
        )
        
    try:
        from core.identity import identity
        ast_name = identity.get_assistant_name() or "nightfall evo"
        own_name = identity.get_owner_name() or "the user"
        role = identity.get_owner_role()
        mode = identity.get_behavior_mode()
        
        identity_str = f"You are {ast_name}. You are assisting {own_name}"
        if role:
            identity_str += f" (Role: {role}).\n"
        else:
            identity_str += ".\n"
            
        identity_str += f"Your current behavior mode is: {mode}.\n"
        
        custom = identity.get_custom_instructions()
        if custom:
            identity_str += f"Custom Instructions: {custom}\n\n"

        # Inject continuously learned rules and behavioral directives
        try:
            from core.learned_rules import LearnedRulesEngine
            learned_directives = LearnedRulesEngine.get_prompt_injections()
            if learned_directives:
                identity_str += f"{learned_directives}\n\n"
        except Exception as e_rules:
            print(f"[LearnedRules] Error injecting rules into prompt: {e_rules}")

        return identity_str + base_prompt
    except Exception as e:
        print(f"Error injecting identity: {e}")
        try:
            from core.learned_rules import LearnedRulesEngine
            learned_directives = LearnedRulesEngine.get_prompt_injections()
            if learned_directives:
                return f"{learned_directives}\n\n" + base_prompt
        except Exception:
            pass
        return base_prompt


def _speak_daily_briefing(ui=None, speak=None) -> None:
    try:
        from actions.daily_briefing import compile_unified_briefing
        data, narrative = compile_unified_briefing()
        if ui:
            ui.show_daily_briefing(data)
            ui.write_log(f"NIGHTFALL Evo: {narrative}")
        (speak or speak_native)(narrative)
    except Exception as e:
        print(f"[DailyBriefing] Error: {e}")
    
def _extract_gemini_text(response) -> str:
    text_parts: list[str] = []
    try:
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []) or []:
                part_text = getattr(part, "text", None)
                if part_text:
                    text_parts.append(part_text)
    except Exception:
        pass

    text = "".join(text_parts).strip()
    if text:
        return text

    try:
        return (getattr(response, "text", "") or "").strip()
    except Exception:
        return ""


def _gemini_text_reply(prompt: str) -> str:
    client = genai.Client(
        api_key=_get_api_key(),
        http_options={"api_version": "v1beta"},
    )
    system_prompt = (
        "You are NIGHTFALL Evo, a concise, helpful desktop assistant. "
        "Reply naturally and briefly. Do not mention internal implementation details."
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=f"{system_prompt}\n\nUser: {prompt}",
        config={"temperature": 0.6},
    )
    return _extract_gemini_text(response)


def _ig_gemini_reply(username: str, text: str) -> str:
    system_prompt = (
        "You are NIGHTFALL Evo, an AI personal assistant acting on behalf of your user. "
        "You have taken over their Instagram chat with the user's permission. "
        "Reply naturally, briefly, and conversationally to the incoming message. "
        "Do not sound like a bot. Keep your replies under 2 sentences."
    )
    prompt = f"Instagram DM from {username}: {text}"
    
    try:
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"},
        )
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"{system_prompt}\n\nUser: {prompt}",
            config={"temperature": 0.6},
        )
        return _extract_gemini_text(response)
    except Exception as e:
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) or _is_gemini_limit_error(e):
            print("[InstagramChat] Gemini Rate Limit hit, falling back to OpenRouter...")
            try:
                from llm_client import client as openrouter_client
                return openrouter_client.chat(prompt, system=system_prompt)
            except Exception as or_e:
                print(f"[InstagramChat] OpenRouter fallback failed: {or_e}")
                return "Hey, I'm currently busy. I will get back to you later!"
        print(f"[InstagramChat] Gemini Reply Error: {e}")
        return "Hey, I'm currently busy. I will get back to you later!"


def _clipboard_gemini_reply(text: str) -> str:
    system_prompt = (
        "You are NIGHTFALL Evo, a witty and helpful AI assistant. "
        "The user just copied the following text to their clipboard. "
        "Make a very short, interesting, or helpful 1-sentence comment or question about it. "
        "Do not offer to 'help' or ask 'how can I help'. Just make a standalone witty observation or summary."
    )
    prompt = text
    try:
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"},
        )
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"{system_prompt}\n\nClipboard Text: {prompt}",
            config={"temperature": 0.8},
        )
        return _extract_gemini_text(response)
    except Exception as e:
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) or _is_gemini_limit_error(e):
            try:
                from llm_client import client as openrouter_client
                return openrouter_client.chat(prompt, system=system_prompt)
            except Exception:
                pass
        return "Interesting stuff you copied there!"


def _looks_like_code_request(text: str) -> bool:
    low = (text or "").lower()
    code_words = (
        "build", "create", "write", "implement", "code", "python", "app",
        "module", "function", "class", "project", "script", "api",
        "ui", "webpage", "bot", "server", "service"
    )
    return any(word in low for word in code_words)


def _looks_like_website_request(text: str) -> bool:
    low = (text or "").lower()
    website_words = (
        "website",
        "web site",
        "webpage",
        "web page",
        "landing page",
        "homepage",
        "home page",
        "portfolio",
        "product site",
        "business site",
        "marketing site",
        "web app",
        "frontend",
        "site",
        "html",
        "react",
        "web",
    )
    action_words = ("make", "create", "build", "design", "develop", "generate", "code", "edit", "update", "fix")
    has_web = any(re.search(rf"\b{re.escape(w)}\b", low) for w in website_words)
    has_action = any(re.search(rf"\b{re.escape(a)}\b", low) for a in action_words)
    return has_web and (has_action or any(w in low for w in ("landing page", "homepage", "portfolio", "website", "web app", "web page")))


def _looks_like_document_request(text: str) -> bool:
    """True when the user wants a Word document written for them.

    Mirrors the presentation/spreadsheet routers: requests to *read*, *open*,
    *edit* or *convert* an existing file are left to the document tools, and a
    user who dictates the body themselves is also left alone.
    """
    low = (text or "").lower()
    if not low.strip():
        return False
    stripped = re.sub(
        r"^(please|pls|kindly)\s+|^(?:can|could|would)\s+you\s+|^hey\s+nightfall(?:\s+evo)?\s+",
        "",
        low,
    ).strip()
    first = stripped.split()[0] if stripped.split() else ""
    if first in {
        "open", "read", "summarize", "summarise", "extract", "convert", "edit",
        "print", "find", "search", "show", "what", "whats", "how", "why", "when",
        "where", "who", "is", "are", "do", "does", "did", "tell", "list", "check",
    }:
        return False
    doc_keywords = (
        "word document", "word file", "docx", ".doc", "ms word", "microsoft word",
        "document", "report", "essay", "assignment", "paper", "write-up", "writeup",
        "letter", "notes",
    )
    action_words = ("write", "create", "make", "generate", "draft", "prepare", "build", "produce")
    has_keyword = any(keyword in low for keyword in doc_keywords)
    has_action = any(re.search(rf"\b{re.escape(action)}\b", low) for action in action_words)
    if not (has_keyword and has_action):
        return False
    # The user dictated the body themselves - keep it verbatim.
    if len(text) > 400 and any(m in low for m in (":", '"', "saying", "text:", "content:", "reads:", "reading")):
        return False
    return True


def _looks_like_presentation_request(text: str) -> bool:
    low = (text or "").lower()
    ppt_keywords = (
        "presentation", "powerpoint", "slideshow", "slides", "slide deck",
        "pitch deck", "deck", "ppt", "pptx"
    )
    action_words = ("make", "create", "build", "design", "develop", "generate", "draft", "prepare")
    has_keyword = any(re.search(rf"\b{re.escape(k)}\b", low) for k in ppt_keywords)
    has_action = any(re.search(rf"\b{re.escape(a)}\b", low) for a in action_words)
    return has_keyword and (has_action or any(k in low for k in ("slide deck", "pitch deck", "powerpoint", "pptx", "ppt")))


def _looks_like_spreadsheet_request(text: str) -> bool:
    low = (text or "").lower()
    sheet_keywords = (
        "spreadsheet", "excel", "sheet", "sheets", "workbook", "xlsx",
        "tracker", "expense tracker", "budget sheet"
    )
    action_words = ("make", "create", "build", "design", "develop", "generate", "draft", "prepare")
    has_keyword = any(re.search(rf"\b{re.escape(k)}\b", low) for k in sheet_keywords)
    has_action = any(re.search(rf"\b{re.escape(a)}\b", low) for a in action_words)
    return has_keyword and (has_action or any(k in low for k in ("spreadsheet", "excel sheet", "expense tracker", "budget sheet", "xlsx")))


def _extract_skill_creation_goal(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    match = re.match(
        r"^(?:please\s+)?(?:(?:i\s+(?:want|need|would like)\s+you\s+to|(?:can|could)\s+you)\s+)?"
        r"(?:make|create|build|develop|generate|forge|write|implement|add|design)\s+"
        r"(?:me\s+)?(?:your\s+)?(?:(?:a|an|new|custom|own)\s+)*(?:skill|feature)\b(.*)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    goal = match.group(1).strip(" \t:,-")
    goal = re.sub(r"^(?:that|which|to)\s+", "", goal, flags=re.IGNORECASE)
    goal = re.sub(
        r"^(?:(?:will|can|could|should)\s+)?(?:allow|let|enable|allows|lets|enables)\s+(?:you\s+)?(?:to\s+)?",
        "",
        goal,
        flags=re.IGNORECASE,
    )
    return goal.strip()


def _is_gemini_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(token in msg for token in (
        "429",
        "resource_exhausted",
        "quota",
        "rate limit",
        "too many requests",
        "exceeded",
        "1008",
        "access denied",
        "permission denied",
    ))


def _looks_like_screen_request(text: str) -> bool:
    t = (text or "").lower()
    if not t:
        return False
    direct_phrases = (
        "what's on my screen",
        "whats on my screen",
        "what is on my screen",
        "what's on screen",
        "check my screen",
        "look at my screen",
        "analyze my screen",
        "analyse my screen",
        "tell me what's on my screen",
        "tell me what is on my screen",
        "read my screen",
        "what does my screen say",
        "explain this error",
        "what's this error",
        "what is this error",
        "explain the error",
        "look at this error",
        "explain what's on my screen",
        "inspect my screen",
        "what am i looking at",
    )
    if any(p in t for p in direct_phrases):
        return True
    screen_words = ("screen", "display", "monitor", "window")
    request_words = ("what", "check", "look", "analy", "analyse", "analyze", "read", "tell", "answer", "see", "explain")
    has_screen_target = any(re.search(rf"\b{re.escape(word)}\b", t) for word in screen_words)
    has_screen_request = any(re.search(rf"\b{re.escape(word)}\b", t) for word in request_words)
    return has_screen_target and has_screen_request


def _looks_like_daily_briefing_request(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").lower()).strip()
    phrases = (
        "daily briefing", "morning briefing", "morning update", "daily update",
        "brief me", "give me a briefing", "briefing for today", "what's happening today",
        "whats happening today", "what is happening today",
    )
    return any(phrase in normalized for phrase in phrases)


def _wakeword_detected(text: str) -> bool:
    t = re.sub(r"[^a-z0-9\s]+", " ", (text or "").lower())
    words = [w for w in t.split() if w]
    if not words:
        return False
    phrases = (
        "nightfall evo",
        "hey nightfall evo",
        "hi nightfall evo",
        "hello nightfall evo",
        "hey",
        "hi",
        "hello",
    )
    compact = " ".join(words)
    if compact in phrases or any(p in compact for p in phrases):
        return True
    return any(word in {"nightfall evo", "hey", "hi", "hello"} for word in words)


def _build_task_plan(text: str) -> list[str]:
    t = (text or "").lower()
    if any(word in t for word in ("presentation", "ppt", "slides", "deck")):
        return [
            "Understand the topic and goal",
            "Build a slide structure",
            "Generate and format the deck",
            "Open the finished presentation",
        ]
    if any(word in t for word in ("spreadsheet", "excel", "sheet", "table", "tracker", "budget")):
        return [
            "Read the data request",
            "Lay out sheets and columns",
            "Apply formulas and formatting",
            "Open the workbook",
        ]
    if any(word in t for word in ("word", "docx", "document", "report", "letter")):
        return [
            "Understand the document type",
            "Draft the structure and content",
            "Preserve formatting and polish",
            "Save the editable file",
        ]
    if any(word in t for word in ("website", "web site", "landing page", "saaS", "saas", "dashboard", "app")):
        return [
            "Interpret the brief",
            "Generate frontend and backend files",
            "Launch the local preview",
            "Debug and fix launch issues if needed",
        ]
    if any(word in t for word in ("browser", "website", "google", "search", "open url", "navigate")):
        return [
            "Open the browser",
            "Navigate to the target page",
            "Collect the needed information",
            "Return the result",
        ]
    if any(word in t for word in ("screen", "camera", "meeting", "call", "analyze", "analyse", "analyze")):
        return [
            "Capture the live screen or camera",
            "Inspect what is visible",
            "Answer with the important details",
            "Keep listening for follow-up commands",
        ]
    if any(re.search(r"\b" + re.escape(word) + r"\b", t) for word in ("fan", "light", "plug", "kasa", "atomberg", "smart home", "home device", "room", "bedroom", "living room", "kitchen", "office", "bathroom", "balcony")):
        return [
            "Identify the smart-home device or room",
            "Choose the correct action",
            "Send the command to the connected provider",
            "Confirm the result back to the user",
        ]
    return [
        "Understand the command",
        "Choose the right tool",
        "Execute the task",
        "Return the result",
    ]


_last_memory_input = ""

def _update_memory_async(user_text: str, NIGHTFALL_text: str) -> None:
    global _last_memory_input

    user_text   = (user_text   or "").strip()
    NIGHTFALL_text = (NIGHTFALL_text or "").strip()

    if len(user_text) < 4 or user_text == _last_memory_input:
        return
    _last_memory_input = user_text

    # Fast deterministic heuristic extraction (Pillar 5 - Living Knowledge Graph)
    try:
        learned = auto_learn_interaction(user_text, NIGHTFALL_text)
        if learned:
            print(f"[Memory] 🧠 Auto-learned: {list(learned.keys())}")
    except Exception as exc:
        print(f"[Memory] ⚠️ Auto-learn error: {exc}")

    try:
        api_key = _get_api_key()
        if not should_extract_memory(user_text, NIGHTFALL_text, api_key):
            return
        data = extract_memory(user_text, NIGHTFALL_text, api_key)
        if data:
            update_memory(data)
            print(f"[Memory] ✅ {list(data.keys())}")
    except Exception as e:
        if "429" not in str(e):
            print(f"[Memory] ⚠️ {e}")

def _memory_context_for_request(text: str) -> str:
    try:
        return workspace_store().memory_context(text, limit=5)
    except Exception:
        return ""


def _image_attachments(attachments) -> list[dict]:
    """Filter a chat attachment list down to readable local images."""
    result: list[dict] = []
    for att in (attachments or []):
        if isinstance(att, dict):
            path = str(att.get("path") or att.get("file") or att.get("name") or "").strip()
        else:
            path = str(att or "").strip()
        if not path:
            continue
        try:
            from core.image_blob import is_image_path
            if not is_image_path(path):
                continue
        except Exception:
            if not path.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")):
                continue
        if Path(path).is_file():
            result.append({"name": Path(path).name, "path": path, "type": "image"})
    return result


def _parse_quick_action_payload(text: str):
    """Detect a Quick Actions (Alt menu) payload.

    Returns ``{"action", "text"}`` or ``None``. Captured text carried by such a
    payload is DATA ONLY, so it must be answered with a plain, tool-free reply
    and must never be saved to memory or trigger any other action.
    """
    try:
        from core.quick_actions import parse_quick_action_payload
        return parse_quick_action_payload(text)
    except Exception:
        return None


TOOL_DECLARATIONS = [
    {
        "name": "undo",
        "description": (
            "Roll back the last change made to the computer — reversing a file move, "
            "copy, create, or edit, a desktop organization, a volume or brightness change, "
            "or a Wi-Fi toggle. Call this whenever the user says undo, revert, take it back, "
            "put it back, restore, or says they made a mistake. "
            "Use action='list' when they ask what can be undone."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "undo (default) — reverse the last change | list — show what can be undone",
                },
            },
        },
    },
    {
        "name": "recall_memory",
        "description": (
            "Search long-term memory for stored facts, preferences, user info, projects, or history. "
            "Use this when the user asks 'do you remember', 'what is my...', or when context about "
            "past instructions or preferences is needed."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Keyword or topic to search for in memory (e.g. 'project', 'coffee', 'birthday', 'preference')",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "execute_protocol",
        "description": (
            "Activates or switches high-level macro system directives (protocols) across the PC. "
            "Call this whenever the user asks to activate, engage, start, run, or switch to a protocol: "
            "'deep_work' (deep focus mode), 'redline' (gaming / max power performance), "
            "'lockdown' (instant lock and security mute), or 'nightfall' (evening standby wrap-up)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "protocol": {
                    "type": "STRING",
                    "description": "Name of the protocol: 'deep_work', 'redline', 'lockdown', 'nightfall'",
                },
            },
            "required": ["protocol"],
        },
    },
    {
        "name": "get_sensorium_telemetry",
        "description": (
            "Read real-time situational awareness telemetry: current active application, "
            "window title, task dwell time in seconds, user idle time, CPU/RAM load, and battery health."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        },
    },
    {
        "name": "manage_local_model",
        "description": (
            "Inspect, download, or switch offline local LLM models on the machine. "
            "action='status' returns available models and server health. "
            "action='pull' downloads a new model (e.g., 'qwen2.5:3b', 'llama3.2:3b'). "
            "action='toggle_offline' switches between local offline inference and cloud."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "One of: 'status', 'pull', 'toggle_offline', 'set_active'",
                },
                "model_name": {
                    "type": "STRING",
                    "description": "Model name when pulling or setting active (e.g. 'qwen2.5:3b', 'llama3.2:3b')",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "dev_agent",
        "description": (
            "An autonomous coding agent that builds full projects, writes code, installs dependencies, "
            "runs the project, and automatically fixes errors. Use this when the user asks you to 'write a script', "
            "'build an app', 'code a program', or 'run a project'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description": {
                    "type": "STRING",
                    "description": "A very detailed description of what the project should do."
                },
                "language": {
                    "type": "STRING",
                    "description": "The programming language to use (e.g., 'python', 'javascript')"
                },
                "project_name": {
                    "type": "STRING",
                    "description": "A short, snake_case name for the project folder."
                }
            },
            "required": ["description"]
        }
    },
    {
        "name": "background_monitor",
        "description": (
            "Sets up a background monitor to check crypto prices, system RAM/CPU, or website uptime. "
            "Use this when the user asks to be alerted when a condition is met (e.g., 'tell me if RAM goes over 90%' or 'alert me if bitcoin drops below 50000')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "'add' to create a monitor (default), 'list' to see active monitors."
                },
                "type": {
                    "type": "STRING",
                    "description": "One of: 'system', 'crypto', 'website'"
                },
                "target": {
                    "type": "STRING",
                    "description": "What to monitor (e.g. 'ram', 'cpu', 'bitcoin', 'https://example.com')"
                },
                "threshold": {
                    "type": "NUMBER",
                    "description": "The threshold value (e.g. 90 for 90%, 50000 for $50k)"
                },
                "condition": {
                    "type": "STRING",
                    "description": "'above' or 'below'"
                },
                "interval": {
                    "type": "INTEGER",
                    "description": "How often to check in seconds (default 60)"
                }
            },
            "required": []
        }
    },
    {
        "name": "system_manager",
        "description": (
            "Checks the system health (CPU, RAM, disk, battery) and lists top resource-hogging apps. "
            "Can also be used to forcefully close or kill frozen or heavy applications."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "What to do: 'status' to check system health (default), or 'kill' to close an app."
                },
                "process_name": {
                    "type": "STRING",
                    "description": "The exact name of the process to kill (if action is 'kill'), e.g. 'chrome.exe' or 'Spotify'"
                },
                "pid": {
                    "type": "INTEGER",
                    "description": "The PID of the process to kill (if action is 'kill')"
                }
            },
            "required": []
        }
    },
    {
        "name": "check_instagram_messages",
        "description": (
            "Checks your Instagram inbox for any recent unread or direct messages. "
            "Use this when the user asks 'do I have any messages', 'check my instagram', or similar."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "clipboard_processor",
        "description": (
            "Instantly reads the current text copied to the user's Windows clipboard. "
            "Use this whenever the user asks you to read, analyze, or fix what they just copied to their clipboard."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "instagram_reply",
        "description": "Replies to a pending Instagram message or takes over the Instagram chat in auto-mode.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "Must be 'take_over' to handle it automatically, or 'manual_reply' to send a specific text message."
                },
                "reply_text": {
                    "type": "STRING",
                    "description": "The exact message to send to the user if action is 'manual_reply'."
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "instagram_send_dm",
        "description": "Sends an Instagram direct message to a username or conversation thread, and automatically opens the chat in your browser.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "recipient": {
                    "type": "STRING",
                    "description": "The Instagram username (e.g. 'john_doe') or numeric thread ID."
                },
                "message": {
                    "type": "STRING",
                    "description": "The message text to send."
                },
                "open_in_browser": {
                    "type": "BOOLEAN",
                    "description": "Whether to auto-open the chat thread in your browser (default: true)."
                }
            },
            "required": ["recipient", "message"]
        }
    },
    {
        "name": "instagram_post_photo",
        "description": "Publishes a photo directly to your Instagram feed and automatically opens the live post in your browser.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "image_path": {
                    "type": "STRING",
                    "description": "Absolute path to the JPG or PNG image file to publish."
                },
                "caption": {
                    "type": "STRING",
                    "description": "The caption text with optional hashtags for the post."
                },
                "open_in_browser": {
                    "type": "BOOLEAN",
                    "description": "Whether to auto-open the published post in your browser (default: true)."
                }
            },
            "required": ["image_path"]
        }
    },
    {
        "name": "instagram_post_reel",
        "description": "Publishes a video or Reel directly to your Instagram account and automatically opens the live Reel in your browser.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "video_path": {
                    "type": "STRING",
                    "description": "Absolute path to the MP4 video file to publish."
                },
                "caption": {
                    "type": "STRING",
                    "description": "The caption text with optional hashtags."
                },
                "thumbnail_path": {
                    "type": "STRING",
                    "description": "Optional path to cover image."
                },
                "open_in_browser": {
                    "type": "BOOLEAN",
                    "description": "Whether to auto-open the published Reel in your browser (default: true)."
                }
            },
            "required": ["video_path"]
        }
    },
    {
        "name": "instagram_get_user_info",
        "description": "Looks up profile details, bio, follower count, following count, and verified badge for any Instagram handle.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "username": {
                    "type": "STRING",
                    "description": "The Instagram username to look up (e.g. 'natgeo' or 'openai')."
                }
            },
            "required": ["username"]
        }
    },
    {
        "name": "open_app",
        "description": (
            "Opens any application on the Windows computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "weather_report",
        "description": (
            "Fetches live real-time weather, forecast, and temperature for any city or location. "
            "Call this whenever the user asks about the weather, temperature, rain, or forecast for any city or location."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, Instagram DMs, or other messaging platform. Can also upload media to Instagram when mode=upload and media_path is supplied.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name for DMs"},
                "message_text": {"type": "STRING", "description": "The message to send or Instagram caption"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, Instagram, etc."},
                "mode":         {"type": "STRING", "description": "dm | upload (Instagram only; default: dm)"},
                "media_path":   {"type": "STRING", "description": "Optional image/video path for Instagram uploads"}
            },
            "required": ["platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Windows Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command. NEVER route to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."},
                "confirmed":   {"type": "STRING", "description": "Pass 'yes' if the user explicitly confirmed a dangerous action like 'shutdown' or 'restart'."}
            },
            "required": []
        }
    },
    {
        "name": "smart_home_control",
        "description": (
            "Controls connected smart-home devices such as Atomberg fans and TP-Link Kasa lights/plugs. "
            "Use when the user asks to turn devices on or off, set fan speed, change brightness, or control a room."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "command": {"type": "STRING", "description": "Natural language smart-home command"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "connect_list_devices",
        "description": (
            "Lists devices connected to NIGHTFALL Connect. Use when the user asks what devices are connected, "
            "what is online, or wants a simple inventory of paired devices."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "connect_get_device",
        "description": (
            "Gets the details for one connected device by name, id, or natural reference such as my phone, "
            "my laptop, my PC, or my tablet."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "device": {"type": "STRING", "description": "Device name, id, or natural reference"},
                "target": {"type": "STRING", "description": "Alias for device"},
                "device_id": {"type": "STRING", "description": "Exact device id"},
                "name": {"type": "STRING", "description": "Exact device name"},
                "query": {"type": "STRING", "description": "Search query"},
            },
            "required": ["device"]
        }
    },
    {
        "name": "connect_get_capabilities",
        "description": (
            "Returns the capabilities and permissions reported by a connected device. "
            "Use before trying any device command."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "device": {"type": "STRING", "description": "Device name, id, or natural reference"},
                "target": {"type": "STRING", "description": "Alias for device"},
                "device_id": {"type": "STRING", "description": "Exact device id"},
                "name": {"type": "STRING", "description": "Exact device name"},
                "query": {"type": "STRING", "description": "Search query"},
            },
            "required": ["device"]
        }
    },
    {
        "name": "connect_execute",
        "description": (
            "Routes a NIGHTFALL Connect command to a paired device through the gateway. "
            "Use for actions such as launch_app, open_url, get_battery, capture_screen, take_photo, "
            "clipboard_get, clipboard_set, send_file, receive_file, media_play, media_pause, volume_set, "
            "notification_list, get_device_info, close_app, mouse_move, keyboard_type, unlock_phone, file_list, file_read, file_write, file_delete."
            "Do not execute device operations directly anywhere else."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "device": {"type": "STRING", "description": "Target device name, id, or natural reference"},
                "target": {"type": "STRING", "description": "Alias for device"},
                "device_id": {"type": "STRING", "description": "Exact device id"},
                "name": {"type": "STRING", "description": "Exact device name"},
                "query": {"type": "STRING", "description": "Search query"},
                "action": {"type": "STRING", "description": "Command to execute on the device"},
                "parameters": {"type": "OBJECT", "description": "Action parameters"},
            },
            "required": ["device", "action"]
        }
    },
    {
        "name": "unlock_device",
        "description": "Unlocks a paired Android device using its saved PIN.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "target": {"type": "STRING", "description": "Device name or ID to unlock"}
            },
            "required": ["target"]
        }
    },
    {
        "name": "connect_pair_device",
        "description": (
            "Creates or approves NIGHTFALL Connect pairing. Use to generate a QR code / pairing code for a new device, "
            "or to approve a pending pairing request."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "device_name": {"type": "STRING", "description": "Optional device name shown in the pairing flow"},
                "platform": {"type": "STRING", "description": "android | windows | ios | tablet | other"},
                "pending_id": {"type": "STRING", "description": "Pending request id to approve"},
            },
            "required": []
        }
    },
    {
        "name": "connect_disconnect_device",
        "description": (
            "Disconnects a device from NIGHTFALL Connect and marks it offline. "
            "Use when the user asks to disconnect, log out, or stop a paired device."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "device": {"type": "STRING", "description": "Target device name, id, or natural reference"},
                "target": {"type": "STRING", "description": "Alias for device"},
                "device_id": {"type": "STRING", "description": "Exact device id"},
                "name": {"type": "STRING", "description": "Exact device name"},
                "query": {"type": "STRING", "description": "Search query"},
                "reason": {"type": "STRING", "description": "Optional reason for disconnect"},
            },
            "required": ["device"]
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Complete browser automation powered by Microsoft Playwright MCP. "
            "Use for: opening websites, web searching, navigating, semantic accessibility snapshots, "
            "clicking elements or snapshot references, typing, form filling, hovering, executing JavaScript, "
            "taking screenshots, managing tabs, and any web-based task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | navigate | search | click | hover | type | scroll | fill_form | snapshot | find | evaluate | screenshot | press | back | forward | refresh | open_tab | new_tab | switch_tab | list_tabs | wait_for | select_option | upload | console | network | close"},
                "url":         {"type": "STRING", "description": "URL for go_to/navigate"},
                "query":       {"type": "STRING", "description": "Search query or search term for find"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type/hover"},
                "element":     {"type": "STRING", "description": "Snapshot element reference (e.g. e2)"},
                "text":        {"type": "STRING", "description": "Text to click, type, or wait for"},
                "description": {"type": "STRING", "description": "Element description for smart targeting"},
                "direction":   {"type": "STRING", "description": "up or down for scroll"},
                "key":         {"type": "STRING", "description": "Key name for press action (Enter, Tab, Escape, etc.)"},
                "expression":  {"type": "STRING", "description": "JavaScript expression to evaluate in page"},
                "path":        {"type": "STRING", "description": "File path for screenshot or upload"},
                "tab":         {"type": "INTEGER", "description": "1-based tab index for switch_tab"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": (
            "Manages files and folders: open, close, list, create, delete, move, copy, rename, read, write, find, disk usage, "
            "and organizing a desktop or any folder into subfolders by type/date. Can also be used to explore and manage files on a connected Android phone."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "open | close | list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | organize_folder | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home. For android, use paths like downloads, documents, photos, movies, root."},
                "target":      {"type": "STRING", "description": "If operating on an Android device, provide the device name or ID. Leave empty for PC local files."},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
                "mode":        {"type": "STRING", "description": "by_type or by_date for organize actions"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "smart_organizer",
        "description": (
            "Smart Desktop & Downloads Organizer MCP with safe transaction rollback (undo), "
            "dry-run previews, duplicate file detection, empty folder cleanup, and archiving. "
            "Use when the user asks to organize, preview, declutter, clean, or find duplicate files on the desktop, downloads, or any folder."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "preview | organize | undo | find_duplicates | clean_empty_folders | archive_old"},
                "target": {"type": "STRING", "description": "Target folder: 'desktop' | 'downloads' | 'documents' | 'pictures' or a custom directory path"},
                "mode":   {"type": "STRING", "description": "by_type (categorizes into Documents, Images/Screenshots, Installers, Code, Archives, etc.) or by_date (YYYY-MM)"},
                "days":   {"type": "INTEGER", "description": "Number of days for archive_old (default: 30)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, preview, clean, undo, find_duplicates, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | preview | clean | undo | find_duplicates | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands. NEVER use for Steam/Epic — use game_updater."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use agent_task, browser_control, or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "file_processor",
        "description": (
            "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
            "text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
            "ALWAYS call this tool when a non-Word file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx via word_document; txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
        }
    },
    {
        "name": "presentation_builder",
        "description": (
            "Creates editable PowerPoint presentations (.pptx) from a structured slide outline. "
            "NIGHTFALL Evo automatically infers the best visual style from the topic, searches for a matching online template when available, "
            "reuses cached templates, and falls back to the built-in designer if no suitable template is found. "
            "Use when the user asks for a deck, slideshow, presentation, pitch deck, or report slides."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING", "description": "Presentation title"},
                "subtitle": {"type": "STRING", "description": "Optional subtitle or audience line"},
                "theme": {
                    "type": "STRING",
                    "description": "Optional presentation theme or visual direction such as neon, corporate, luxury, academic, sunset, or creative. If omitted, NIGHTFALL Evo infers the best style automatically."
                },
                "outline": {
                    "type": "STRING",
                    "description": "Slide-by-slide outline. Use blank lines to separate slides if slides array is omitted."
                },
                "slides": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "title": {"type": "STRING", "description": "Slide title"},
                            "kicker": {"type": "STRING", "description": "Short all-caps kicker"},
                            "bullets": {
                                "type": "ARRAY",
                                "items": {"type": "STRING"},
                                "description": "Bullet points for the slide"
                            },
                            "notes": {"type": "STRING", "description": "Optional speaker note or footnote"}
                        },
                        "required": ["title"]
                    },
                    "description": "Structured slides. Preferred when the model can format the deck directly."
                },
                "output_path": {"type": "STRING", "description": "Optional output path for the .pptx"},
                "auto_open": {"type": "BOOLEAN", "description": "Open the file after creating it (default: true)"},
            },
            "required": ["title"]
        }
    },
    {
        "name": "spreadsheet_builder",
        "description": (
            "Creates editable Excel workbooks (.xlsx) from structured sheet data. "
            "Use for trackers, tables, analysis workbooks, budgets, planners, and other spreadsheet requests."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING", "description": "Workbook title"},
                "worksheets": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "name": {"type": "STRING", "description": "Worksheet name"},
                            "title": {"type": "STRING", "description": "Optional sheet title row"},
                            "headers": {
                                "type": "ARRAY",
                                "items": {"type": "STRING"},
                                "description": "Column headers"
                            },
                            "rows": {
                                "type": "ARRAY",
                                "items": {
                                    "type": "ARRAY",
                                    "items": {"type": "STRING"},
                                },
                                "description": "Data rows"
                            },
                            "chart": {
                                "type": "OBJECT",
                                "properties": {
                                    "type": {"type": "STRING", "description": "bar | line | pie"},
                                    "title": {"type": "STRING", "description": "Chart title"},
                                    "anchor": {"type": "STRING", "description": "Cell anchor such as E2"},
                                    "x_axis": {"type": "STRING", "description": "Optional x-axis title"},
                                    "y_axis": {"type": "STRING", "description": "Optional y-axis title"},
                                }
                            }
                        },
                        "required": ["name"]
                    },
                    "description": "One or more worksheets to create."
                },
                "output_path": {"type": "STRING", "description": "Optional output path for the .xlsx"},
                "auto_open": {"type": "BOOLEAN", "description": "Open the file after creating it (default: true)"},
            },
            "required": ["title"]
        }
    },
    {
        "name": "word_document",
        "description": (
            "Creates, edits, reads, summarizes, extracts text from, and opens editable Word documents (.docx). "
            "Use for Word document requests, letters, reports, headings, bullets, formatting edits, and preserving existing formatting. "
            "You MUST call this tool for every document request - never reply in chat with an outline or a description of the document. "
            "For long documents (reports, essays, assignments, papers) you may pass only action and title: "
            "NIGHTFALL writes the complete document content itself and saves the .docx. "
            "If you do pass content, it must be the finished document text, never a description of what it would contain."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "create | create_letter | create_report | read | summarize | extract_text | append | replace_text | add_heading | add_bullets | reformat | open"
                },
                "file_path": {"type": "STRING", "description": "Existing .docx file path for read/edit/open actions"},
                "output_path": {"type": "STRING", "description": "Optional output path for the saved .docx"},
                "title": {"type": "STRING", "description": "Document title"},
                "doc_type": {"type": "STRING", "description": "letter | report | generic"},
                "content": {"type": "STRING", "description": "Main body content or text to append"},
                "body": {"type": "STRING", "description": "Body text for letter/report creation"},
                "paragraphs": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Paragraphs to add"},
                "bullets": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Bullet items to add"},
                "numbered": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Numbered items to add"},
                "sections": {"type": "ARRAY", "items": {"type": "OBJECT"}, "description": "Structured sections with heading/body/bullets"},
                "replacements": {"type": "OBJECT", "description": "Find/replace mapping for formatting-preserving edits"},
                "find": {"type": "STRING", "description": "Text to find for simple replace_text edits"},
                "replace": {"type": "STRING", "description": "Replacement text for simple replace_text edits"},
                "heading": {"type": "STRING", "description": "Heading text to append"},
                "level": {"type": "INTEGER", "description": "Heading level 1-3"},
                "recipient": {"type": "STRING", "description": "Letter recipient"},
                "salutation": {"type": "STRING", "description": "Custom letter salutation"},
                "closing": {"type": "STRING", "description": "Custom letter closing"},
                "date": {"type": "STRING", "description": "Letter date"},
                "author": {"type": "STRING", "description": "Document author"},
                "subject": {"type": "STRING", "description": "Document subject"},
                "open_after": {"type": "BOOLEAN", "description": "Open the saved document after writing (default: true)"},
                "save": {"type": "BOOLEAN", "description": "Save large generated summaries to a text file"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "pdf_document",
        "description": (
            "Creates editable-style PDF documents (.pdf) from structured content or converts DOCX / text files into PDFs. "
            "Use for PDF creation, PDF exports, and PDF generation requests that need a direct file output."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "create | create_report | create_letter | convert"
                },
                "file_path": {"type": "STRING", "description": "Existing file to convert, typically .docx or .txt"},
                "output_path": {"type": "STRING", "description": "Optional output path for the saved .pdf"},
                "title": {"type": "STRING", "description": "PDF title"},
                "subtitle": {"type": "STRING", "description": "Optional subtitle"},
                "content": {"type": "STRING", "description": "Main body content"},
                "body": {"type": "STRING", "description": "Main body content"},
                "paragraphs": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Paragraphs to add"},
                "bullets": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Bullet items to add"},
                "numbered": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Numbered items to add"},
                "sections": {"type": "ARRAY", "items": {"type": "OBJECT"}, "description": "Structured sections with heading/body/bullets"},
                "recipient": {"type": "STRING", "description": "Letter recipient"},
                "salutation": {"type": "STRING", "description": "Custom letter salutation"},
                "closing": {"type": "STRING", "description": "Custom letter closing"},
                "date": {"type": "STRING", "description": "Letter date"},
                "author": {"type": "STRING", "description": "Document author"},
                "subject": {"type": "STRING", "description": "Document subject"},
                "auto_open": {"type": "BOOLEAN", "description": "Open the file after creating it (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "shutdown_NIGHTFALL",
        "description": (
            "Shuts down the assistant completely. "
        "Call this when the user expresses intent to end the conversation, "
        "close the assistant, say goodbye, or stop NIGHTFALL Evo. "
        "The user can say this in ANY language."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {},
    }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. User, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "spotify_controller",
        "description": (
            "Plays and controls music via Spotify, Spotify MCP, and Google Chrome. "
            "ALWAYS use this tool whenever the user asks to play any song, music, track, or artist "
            "(e.g. 'play Starboy', 'play music on Spotify'), or control playback "
            "('pause the music', 'resume', 'skip song', 'next track', 'volume up', 'volume down', 'mute'). "
            "Do NOT use open_app for playing songs."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "search_play | play | pause | toggle | next | previous | volume_up | volume_down | set_volume | get_now_playing | get_playlists | get_queue | get_devices | auth | mute | open_spotify (default: search_play)"
                },
                "query": {
                    "type": "STRING",
                    "description": "Song title, artist name, album, or playlist to search and play"
                },
                "volume": {
                    "type": "NUMBER",
                    "description": "Volume level (optional)"
                },
                "device_id": {
                    "type": "STRING",
                    "description": "Optional Spotify Connect playback device ID"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "calendar_scheduler",
        "description": (
            "Manages calendar events, appointments, and schedules. "
            "Use whenever the user asks to schedule a meeting, check upcoming events, "
            "view schedule for today/tomorrow, or remove an event."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "add_event | list_events | check_day | delete_event | get_upcoming | export_ics"
                },
                "title": {
                    "type": "STRING",
                    "description": "Title or summary of the meeting/event"
                },
                "date": {
                    "type": "STRING",
                    "description": "Date (YYYY-MM-DD or 'today', 'tomorrow')"
                },
                "time": {
                    "type": "STRING",
                    "description": "Time (HH:MM in 24h format, e.g. '14:30')"
                },
                "duration_minutes": {
                    "type": "NUMBER",
                    "description": "Duration in minutes (default: 30)"
                },
                "location": {
                    "type": "STRING",
                    "description": "Location or meeting link (optional)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "daily_briefing",
        "description": (
            "Delivers a complete daily briefing including time, date, today's schedule/calendar events, "
            "and top world & tech headlines. Use whenever the user asks for their daily briefing, morning update, "
            "or what's happening today."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": "Optional news category: all (default), tech, world"
                }
            },
            "required": []
        }
    },
    {
        "name": "google_workspace",
        "description": (
            "Full Google Workspace MCP for Gmail, Calendar, and Google Drive automation. "
            "Supports: reading unread emails, listing inbox, searching emails, sending emails; "
            "listing calendar events, adding calendar events; "
            "searching Drive files, reading Drive files, and uploading files to Google Drive."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "service": {
                    "type": "STRING",
                    "description": "gmail | calendar | drive"
                },
                "action": {
                    "type": "STRING",
                    "description": "gmail actions: 'list', 'unread', 'read', 'search', 'send' | calendar actions: 'list', 'add' | drive actions: 'list', 'search', 'read', 'upload'"
                },
                "to": {
                    "type": "STRING",
                    "description": "Recipient email address for sending emails"
                },
                "subject": {
                    "type": "STRING",
                    "description": "Subject for sending or searching emails"
                },
                "body": {
                    "type": "STRING",
                    "description": "Body message for email"
                },
                "query": {
                    "type": "STRING",
                    "description": "Search query for Gmail or Drive"
                },
                "email_id": {
                    "type": "STRING",
                    "description": "ID of email to read"
                },
                "title": {
                    "type": "STRING",
                    "description": "Title/summary for calendar event"
                },
                "date": {
                    "type": "STRING",
                    "description": "Date for calendar event (YYYY-MM-DD or today/tomorrow)"
                },
                "time": {
                    "type": "STRING",
                    "description": "Time for calendar event (HH:MM)"
                },
                "duration_minutes": {
                    "type": "NUMBER",
                    "description": "Duration in minutes"
                },
                "file_path": {
                    "type": "STRING",
                    "description": "Local file path or name for Drive upload/read"
                }
            },
            "required": ["service", "action"]
        }
    },
    {
        "name": "system_diagnostics",
        "description": (
            "Local OS Hardware and System Diagnostics MCP. "
            "Checks battery health, power status, CPU usage and thermals, RAM utilization and top memory/CPU hogs, "
            "storage drive space, and controls multi-monitor display brightness. Can safely terminate frozen apps."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "status | ram_hogs | cpu_hogs | kill | brightness | battery | disk"
                },
                "target": {
                    "type": "STRING",
                    "description": "Application name (e.g. 'chrome', 'notepad') or numerical PID to terminate"
                },
                "level": {
                    "type": "NUMBER",
                    "description": "Display brightness level from 0 to 100"
                },
                "monitor": {
                    "type": "STRING",
                    "description": "Optional target monitor name or index"
                },
                "relative": {
                    "type": "BOOLEAN",
                    "description": "Whether brightness level is relative (+10, -10)"
                },
                "limit": {
                    "type": "NUMBER",
                    "description": "Maximum number of processes to return (default: 5)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "auto_heal",
        "description": (
            "Autonomous Self-Healing and Continuous Self-Improvement System. "
            "Analyzes tracebacks/exceptions in first-party codebase, synthesizes surgical code hotfixes, "
            "validates AST syntax in safety sandbox, applies hotfixes with atomic backup, or rolls back changes. "
            "Also manages learned behavioral rules and persistent user directives."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "status | heal | history | rollback | learn_rule | list_rules"
                },
                "error_traceback": {
                    "type": "STRING",
                    "description": "The exact exception traceback or error message to heal"
                },
                "rule_text": {
                    "type": "STRING",
                    "description": "The user preference, habit, or behavioral directive to remember permanently"
                },
                "category": {
                    "type": "STRING",
                    "description": "Category for learned rule (general, formatting, workflow, habit)"
                },
                "patch_id": {
                    "type": "STRING",
                    "description": "Specific patch ID to rollback (default: latest)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "circuit_assembler",
        "description": "Designs a safe electronic wiring schematic from component names or a screen image, then shows the interactive circuit HUD.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "assemble_components | analyze_screen | show_schematic"},
                "components": {"type": "STRING", "description": "Electronic components, such as Arduino Uno, ESP32, DHT11, or HC-SR04."},
                "query": {"type": "STRING", "description": "Circuit goal or wiring question."}
            },
            "required": ["action"]
        }
    },
    {
        "name": "geospatial_globe",
        "description": "Opens NIGHTFALL's interactive 3D map for routes, live aircraft, weather, earthquakes, nearby places, and ISS tracking.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "open | route | drive | location | fly_to | weather | flights | iss | earthquakes | nearby | radar | inspect"},
                "origin": {"type": "STRING", "description": "Origin city for a route."},
                "destination": {"type": "STRING", "description": "Destination city for a route."},
                "location": {"type": "STRING", "description": "City or area to focus on."},
                "query": {"type": "STRING", "description": "Nearby place category, such as hospitals, fuel, or restaurants."},
                "min_magnitude": {"type": "NUMBER", "description": "Minimum earthquake magnitude to display."},
                "enable": {"type": "BOOLEAN", "description": "Enable or disable the weather radar overlay."}
            },
            "required": ["action"]
        }
    },
    {
        "name": "call_screening",
        "description": "Screens an incoming call with NIGHTFALL as an AI attendant. Answering always waits for the user to confirm on the Echo HUD.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "start | take_over | hang_up"},
                "caller": {"type": "STRING", "description": "Caller name or notification title."},
                "app": {"type": "STRING", "description": "Calling application, such as Phone Link, WhatsApp, or Teams."}
            },
            "required": ["action"]
        }
    },
    {
        "name": "skill_forge",
        "description": "Use whenever the user explicitly asks to make, create, build, or add a new skill or feature. Starts creation immediately, validates the generated Python feature, and registers it for use. Do not use screen analysis for feature-creation requests.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "forge | list"},
                "goal": {"type": "STRING", "description": "What new capability the skill should provide."},
                "skill_name": {"type": "STRING", "description": "Optional short name for the skill."}
            },
            "required": ["action"]
        }
    },
    {
        "name": "dynamic_skill",
        "description": "Lists and runs installed NIGHTFALL skills, including stock, crypto, cricket, speed-test, and ISS examples. Running a skill requires user confirmation.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "list | run"},
                "skill_name": {"type": "STRING", "description": "Skill name to run."},
                "arguments": {"type": "OBJECT", "description": "Arguments for the selected skill."}
            },
            "required": ["action"]
        }
    },
    {
        "name": "calorie_counter",
        "description": (
            "Analyzes food and meals to report calories, macronutrients (protein, carbs, fat, fiber), "
            "and dietary advice. Can capture a live snapshot via webcam, inspect an image file, "
            "or analyze a spoken/typed meal description. "
            "Use whenever the user asks about the calories or nutritional value of food."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Food or meal description (e.g. 'How many calories in this plate?', 'I had 2 eggs and toast')."
                },
                "image_path": {
                    "type": "STRING",
                    "description": "Optional local file path to a food photo."
                },
                "use_camera": {
                    "type": "BOOLEAN",
                    "description": "True to capture a live photo from the webcam."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "pushup_counter",
        "description": (
            "Live AI workout and repetition counter. Tracks reps, tempo, and calories burned "
            "for pushups, squats, and bodyweight exercises through the webcam with live HUD overlay. "
            "Use whenever the user asks to count pushups, track squats, or start a workout."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "User's request (e.g. 'count my pushups', 'track 20 squats')."
                },
                "exercise": {
                    "type": "STRING",
                    "description": "pushups | squats | general"
                },
                "target": {
                    "type": "INTEGER",
                    "description": "Target rep goal (e.g. 20)."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "upload_video",
        "description": (
            "Automates video publishing to TikTok, YouTube Shorts, or Instagram. "
            "Auto-locates recent videos on Desktop/Downloads, generates viral SEO captions and tags, "
            "copies metadata to clipboard, reveals the file in Explorer, and launches the studio uploader."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description": {
                    "type": "STRING",
                    "description": "Video topic, caption brief, or context (in user's language)."
                },
                "platform": {
                    "type": "STRING",
                    "description": "tiktok | youtube | instagram (default: tiktok)"
                },
                "video_path": {
                    "type": "STRING",
                    "description": "Optional path to the video file to publish."
                }
            },
            "required": ["description"]
        }
    },
]


class NIGHTFALLLive:

    def __init__(self, ui: NIGHTFALLUI, dashboard=None, dashboard_started: bool = False, enable_dashboard: bool = True):
        self.ui             = ui
        self._smart_home    = SmartHomeService()
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._startup_briefing_started = False
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self._use_openrouter_first = False
        self._pending_attention: dict | None = None
        self._pending_reply_event: dict | None = None
        self._reply_mode = False
        # Latest thing the user asked for (typed or spoken). Document tools use it
        # when the model calls them without writing the content itself.
        self._last_user_utterance = ""
        #: (image bytes, question) captured for the live session by the
        #: screen_process tool, delivered right after the tool response.
        self._pending_screen_frame = None
        #: When the screen was last captured. The model sometimes calls
        #: screen_process several times in one turn; the cooldown turns the
        #: repeats into "use the image you already have" instead of capturing
        #: and answering the same screenshot three times.
        self._last_screen_capture_at = 0.0
        self._attention_lock = threading.Lock()
        self._attention_monitor = AttentionMonitor(on_event=self._on_external_notification)
        try:
            set_speech_sink(self.speak)
        except Exception:
            pass
            
        try:
            from actions.background_monitor import set_monitor_speech_sink
            set_monitor_speech_sink(self.speak)
        except Exception as e:
            print(f"[Main] Failed to init background monitor: {e}")
        self._meeting_lock = threading.Lock()
        self._meeting_active = False
        self._meeting_event: dict | None = None
        self._meeting_assistant = MeetingAssistant(
            on_update=self._on_meeting_update,
            on_state=self._on_meeting_state,
        )
        self._phone_active = False
        self._dashboard = dashboard if dashboard is not None else (DashboardServer() if (enable_dashboard and DashboardServer is not None) else None)
        self._dashboard_started = bool(dashboard_started and self._dashboard is not None)
        self.ui.on_text_command = self._on_text_command
        self.ui.on_attention_action = self._on_attention_action
        self.ui.on_remote_clicked = self._make_remote_key
        self._echo = EchoGuard()
        self._resume_handle = None
        self._ptt = None
        self._ptt_held = False
        try:
            self._ptt_enabled = config_manager.get_push_to_talk_enabled()
            if self._ptt_enabled:
                self.set_push_to_talk(True)
        except Exception:
            self._ptt_enabled = False

        try:
            audio_devices.configure(SEND_SAMPLE_RATE, RECEIVE_SAMPLE_RATE)
            audio_devices.prefetch()
        except Exception:
            pass
        self._last_activity = time.monotonic()
        self._idle_prompts = [
            "Hey, you there?",
            "Yo, get alive.",
            "How may I help, bro?",
            "Need anything?",
            "I'm here if you want me.",
        ]
        self._idle_speech_thread = threading.Thread(target=self._idle_speech_loop, daemon=True)
        self._idle_speech_thread.start()

    def set_push_to_talk(self, enabled: bool) -> str:
        self._ptt_enabled = bool(enabled)
        self._ptt_held = False
        if not enabled:
            if self._ptt is not None:
                self._ptt.stop()
                self._ptt = None
            return "off"
        if self._ptt is None:
            self._ptt = PushToTalk(self._on_ptt)
        scope = self._ptt.start()
        try:
            self.ui.write_log(
                f"SYS: Push-to-talk on — hold {self._ptt.label}"
                + ("." if scope == "global" else " (works while window focused).")
            )
        except Exception:
            pass
        return scope

    def _on_ptt(self, held: bool) -> None:
        self._ptt_held = held

    def _reset_idle_activity(self):
        self._last_activity = time.monotonic()

    def _should_announce_idle(self) -> bool:
        if self.ui.muted:
            return False
        if self._is_speaking:
            return False
        if self._meeting_active:
            return False
        if self._pending_attention:
            return False
        if time.monotonic() - self._last_activity < 240:
            return False
        return True

    def _idle_speech_loop(self):
        try:
            from actions.proactive import ProactiveEngine
            engine = ProactiveEngine(min_silence_secs=300, check_cooldown=600)  # Shorter defaults for testing
        except ImportError:
            engine = None

        while True:
            time.sleep(60.0)
            if not engine:
                continue
            
            try:
                if self.ui.muted or self._is_speaking or self._meeting_active or self._pending_attention:
                    continue
                
                # Check if it should trigger using the engine's time monotonic logic
                if engine.should_trigger(self._last_activity):
                    engine.mark_triggered()
                    prompt = engine.build_prompt(memory={})
                    
                    if self.session and self._loop:
                        import asyncio
                        async def _send():
                            try:
                                await self.session.send(input=prompt, end_of_turn=True)
                            except Exception as e:
                                print(f"[Proactive] Error: {e}")
                        asyncio.run_coroutine_threadsafe(_send(), self._loop)
                        self._reset_idle_activity()
                    
            except Exception as e:
                print(f"[Proactive] Error: {e}")

    def _make_remote_key(self):
        if self._dashboard is None:
            self.ui.write_log("ERR: Mobile Connect unavailable. Install fastapi, uvicorn, cryptography, and qrcode[pil].")
            return None
        key = self._dashboard.new_key()
        url = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    def _on_phone_connected(self):
        try:
            self.ui.notify_phone_connected()
        except Exception:
            pass

    def _handle_quick_action_request(self, parsed: dict) -> None:
        """Answer a Quick Actions request with a plain, data-only reply.

        The captured text is payload only: no memory extraction, no tool calls,
        no task workspace and no other side effects — just the requested
        translation / summary / explanation.
        """
        action = str((parsed or {}).get("action") or "translate")
        captured = str((parsed or {}).get("text") or "")
        self.ui.set_state("THINKING")
        try:
            stop_native_speech()
        except Exception:
            pass

        def _worker():
            try:
                from core.quick_actions import quick_action_reply
                reply = quick_action_reply(action, captured)
            except Exception as exc:
                print(f"[QuickActions] guard reply failed: {exc}")
                reply = f"Quick Action ({action}) failed: {exc}"
            reply = (reply or "").strip() or "No reply."
            try:
                self.ui.write_log(f"NIGHTFALL Evo: {reply}")
            except Exception:
                pass
            if not getattr(self.ui, "muted", False):
                try:
                    from actions.attention_monitor import _speak_edge_native
                    _speak_edge_native(reply)
                except Exception:
                    try:
                        self.speak(reply, proactive=True)
                    except Exception:
                        pass
            try:
                self.ui.set_state("LISTENING")
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True, name="quick-action-guard").start()

    def _handle_image_command(self, text: str, images: list[dict], source: str = "local") -> None:
        """Answer a question about user-supplied images (e.g. a snip).

        The image is sent to the model as an inline part together with the
        user's own instruction. No memory extraction and no task workspace:
        the image is the user's input, not something to act on behind the
        scenes.
        """
        text = (text or "").strip() or "What do you see in this image? Answer briefly."
        self.ui.set_state("THINKING")
        try:
            stop_native_speech()
        except Exception:
            pass

        def _worker():
            import base64

            parts: list[dict] = []
            blobs: list[tuple[bytes, str]] = []
            for image in images:
                try:
                    from core.image_blob import prepare_image_blob
                    blob = prepare_image_blob(image.get("path"))
                except Exception:
                    blob = None
                if not blob:
                    continue
                data, mime = blob
                blobs.append((data, mime))
                parts.append({
                    "inline_data": {
                        "mime_type": mime,
                        "data": base64.b64encode(data).decode("utf-8"),
                    }
                })
            if not parts:
                reply = "I couldn't read that screenshot. Please try capturing it again."
            else:
                parts.append({"text": text})
                live = bool(self.session and self._loop)
                if live:
                    try:
                        asyncio.run_coroutine_threadsafe(
                            self.session.send_client_content(
                                turns={"parts": parts},
                                turn_complete=True,
                            ),
                            self._loop,
                        )
                        # The voice session answers; its transcript lands in chat.
                        reply = ""
                    except Exception as exc:
                        print(f"[NIGHTFALL EVO] Image send failed: {exc}")
                        live = False
                else:
                    live = False
                if not live:
                    reply = self._vision_reply(text, blobs)
            if reply:
                try:
                    self.ui.write_log(f"NIGHTFALL Evo: {reply}")
                except Exception:
                    pass
                if not getattr(self.ui, "muted", False):
                    try:
                        from actions.attention_monitor import _speak_edge_native
                        _speak_edge_native(reply)
                    except Exception:
                        try:
                            self.speak(reply, proactive=True)
                        except Exception:
                            pass
            try:
                self.ui.set_state("LISTENING")
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True, name="image-command").start()

    # -- screen analysis routing -------------------------------------------
    def _live_session_available(self) -> bool:
        """True when the unified voice session can answer about a screenshot."""
        return bool(getattr(self, "session", None) and getattr(self, "_loop", None))

    def _capture_screen_bytes(self):
        """Screenshot the primary display, preferring the UI's own capture."""
        try:
            capture = getattr(self.ui, "capture_screen_bytes", None)
            if callable(capture):
                data = capture()
                if data:
                    return bytes(data)
        except Exception as exc:
            print(f"[NIGHTFALL EVO] UI screen capture failed: {exc}")
        try:
            from actions.screen_processor import _capture_screenshot
            return _capture_screenshot()
        except Exception as exc:
            print(f"[NIGHTFALL EVO] Screen capture failed: {exc}")
        return None

    def _send_screen_to_live(self, text: str, data: bytes, mime: str = "image/jpeg") -> bool:
        """Send a screenshot to the unified live session (Zephyr voice).

        One screenshot means one answer: the live session describes it with the
        same voice as the rest of the app, and the standalone vision module is
        left unused, so the user never hears two voices at once.
        """
        import base64

        parts = [
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode("utf-8")}},
            {"text": text},
        ]
        try:
            asyncio.run_coroutine_threadsafe(
                self.session.send_client_content(turns={"parts": parts}, turn_complete=True),
                self._loop,
            )
            return True
        except Exception as exc:
            print(f"[NIGHTFALL EVO] Screen send to live session failed: {exc}")
            return False

    def _screen_frame_is_fresh(self, window: float = 20.0) -> bool:
        """True when the screen was captured moments ago in the same turn."""
        try:
            return (time.time() - float(getattr(self, "_last_screen_capture_at", 0.0))) < window
        except Exception:
            return False

    async def _deliver_pending_screen_frame(self) -> None:
        """Send a captured screen frame to the live session after a tool call.

        The frame goes out only once the tool response has been sent, because
        the live API rejects client content that arrives while a tool call is
        still open.  The same session then answers in the app's single voice.
        """
        pending = getattr(self, "_pending_screen_frame", None)
        if not pending:
            return
        self._pending_screen_frame = None
        frame, question = pending
        import base64 as _b64

        try:
            await asyncio.sleep(0.4)
            await self.session.send_client_content(
                turns={"parts": [
                    {"inline_data": {
                        "mime_type": "image/jpeg",
                        "data": _b64.b64encode(frame).decode("utf-8"),
                    }},
                    {"text": question},
                ]},
                turn_complete=True,
            )
            print("[NIGHTFALL EVO] Screen frame delivered to the live session")
        except Exception as exc:
            print(f"[NIGHTFALL EVO] Screen frame delivery failed: {exc}")

    def _vision_reply(self, text: str, blobs: list[tuple[bytes, str]]) -> str:
        """Text-only vision fallback used when the live voice session is down."""
        if not blobs:
            return ""
        data, mime = blobs[0]
        # Custom providers (registered in Settings) get first crack at the image.
        try:
            import base64

            from core import jeff_router, provider_registry

            vision_providers = [
                p for p in provider_registry.configured_providers()
                if "vision" in {str(c).lower() for c in (p.get("caps") or [])}
            ]
            if vision_providers:
                client = self._jeff_client()
                try:
                    decision = jeff_router.route(
                        text or "describe this image",
                        client=client,
                        providers=vision_providers,
                    )
                except Exception:
                    decision = None
                ordered = vision_providers
                if decision is not None:
                    ordered = [decision.provider] + [
                        p for p in vision_providers if p.get("id") != decision.provider_id
                    ]
                    try:
                        self.ui.write_log(f"SYS: {decision.describe()}")
                    except Exception:
                        pass
                for provider in ordered:
                    try:
                        reply = provider_registry.vision(
                            provider,
                            text or "Describe this image concisely.",
                            base64.b64encode(data).decode("utf-8"),
                            mime,
                        )
                        if reply:
                            return reply.strip()
                    except Exception as exc:
                        print(f"[NIGHTFALL EVO] {provider.get('name')} vision failed: {exc}")
        except Exception as exc:
            print(f"[NIGHTFALL EVO] Custom vision providers failed: {exc}")
        try:
            from actions.screen_processor import screen_process
            if screen_process(parameters={"angle": "screen", "text": text}, player=self.ui, image_bytes=data):
                return ""  # the vision session speaks the answer itself
        except Exception as exc:
            print(f"[NIGHTFALL EVO] Vision session fallback failed: {exc}")
        try:
            import base64

            from google import genai
            from core.quick_actions import _load_config
            keys = _load_config("api_keys.json")
            key = str(keys.get("gemini_api_key") or "").strip()
            if key:
                client = genai.Client(api_key=key, http_options={"api_version": "v1beta"})
                resp = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[
                        {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode("utf-8")}},
                        text,
                    ],
                )
                return (getattr(resp, "text", "") or "").strip()
        except Exception as exc:
            print(f"[NIGHTFALL EVO] Gemini vision fallback failed: {exc}")
        try:
            import base64

            from llm_client import client as or_client
            return (or_client.vision(
                text,
                base64.b64encode(data).decode("utf-8"),
                mime,
                system="You are NIGHTFALL Evo. Look at the image and answer the user's question concisely.",
            ) or "").strip()
        except Exception as exc:
            print(f"[NIGHTFALL EVO] OpenRouter vision fallback failed: {exc}")
            return f"I couldn't analyse that screenshot: {exc}"

    def _on_text_command(self, text: str, source: str = "local", attachments: list | None = None):
        self._reset_idle_activity()
        text = (text or "").strip()
        images = _image_attachments(attachments)
        if not text and not images:
            return
        if text:
            self._last_user_utterance = text
        # Quick Actions (Alt menu) payloads carry untrusted captured text that
        # is DATA ONLY. Answer them with a plain, tool-free reply: no memory
        # extraction, no tools, no task workspace, no other action.
        quick_action = _parse_quick_action_payload(text)
        if quick_action is not None:
            self._handle_quick_action_request(quick_action)
            return
        # A message carrying an image (e.g. an Alt -> Screen snip) is a vision
        # question: send the image plus the user's own instruction to the model.
        if images:
            self._handle_image_command(text, images, source)
            return
        if len(text) > 4:
            threading.Thread(
                target=_update_memory_async,
                args=(text, ""),
                daemon=True
            ).start()
        self.ui.set_state("THINKING")
        try:
            stop_native_speech()
        except Exception:
            pass
        # allow plugins to handle the incoming text command first
        try:
            pm = getattr(self, "plugin_manager", None)
            if pm is not None:
                handled = pm.dispatch("on_text_command", text, source)
                if handled:
                    return
        except Exception:
            pass
        if self._reply_mode:
            if self._handle_pending_reply(text):
                return
            # Still in reply mode but no pending reply event means reset and continue
            self._reply_mode = False

        if getattr(self, "_ig_reply_mode", False):
            if self._handle_ig_reply_flow(text):
                return

        if getattr(self, "_email_mode", False):
            if self._handle_email_flow(text):
                return

        # Direct verbal toggle for Air-Gapped Offline Mode
        lower = text.lower().strip()
        if any(p in lower for p in ("switch to offline mode", "go offline", "turn on offline mode", "enable offline mode", "air gap mode")):
            config_manager.set_setting("offline_mode_enabled", True)
            config_manager.set_setting("default_ai_provider", "Local")
            self.ui.write_log("🔒 SYSTEM: Air-Gapped Offline Mode ENGAGED. All operations running 100% locally.")
            self.speak("Offline mode engaged, sir. Running 100 percent locally on your machine.", proactive=True)
            return

        if any(p in lower for p in ("switch to online mode", "go online", "turn off offline mode", "disable offline mode")):
            config_manager.set_setting("offline_mode_enabled", False)
            config_manager.set_setting("default_ai_provider", "Google Gemini")
            self.ui.write_log("🌐 SYSTEM: Offline Mode DISENGAGED. Cloud connectivity restored.")
            self.speak("Online mode restored, sir. Cloud connectivity is active.", proactive=True)
            return

        # Check for email command initiation
        lower = text.lower()
        skill_goal = _extract_skill_creation_goal(text)
        if skill_goal is not None:
            if not skill_goal:
                prompt = "What should the new skill or feature do?"
                self.ui.write_log(f"NIGHTFALL Evo: {prompt}")
                self.speak(prompt)
                return

            try:
                self.ui.begin_task_workspace(
                    text,
                    ["Generate the requested feature", "Validate it in the skill sandbox", "Register it for future use"],
                    source=source or "local",
                )
            except Exception:
                pass

            self.speak("Starting feature creation now.")
            threading.Thread(
                target=self._forge_skill,
                args=(skill_goal,),
                daemon=True,
                name="skill-forge",
            ).start()
            return

        # Check for dynamic/evolved skill execution (e.g. "run the skill", "system status", "show cpu and ram")
        try:
            from core.dynamic_registry import DynamicToolRegistry
            matched = DynamicToolRegistry.find_matching_skill(text)
            if matched:
                skill_name, skill_args = matched
                self.speak(f"Running {skill_name.replace('_', ' ')}.")
                def _run_dynamic_feature():
                    try:
                        res = DynamicToolRegistry.execute_sync(skill_name, skill_args or {})
                        out_text = ""
                        if isinstance(res, dict):
                            out_text = str(res.get("summary") or res.get("output") or res.get("text") or res).strip()
                        else:
                            out_text = str(res).strip()
                        self.ui.write_log(f"NIGHTFALL Evo [{skill_name}]:\n{out_text}")
                        if hasattr(self.ui, "finish_task_workspace"):
                            self.ui.finish_task_workspace(out_text, f"{skill_name} completed.", 100)
                        if hasattr(self.ui, "show_hud_deliverable"):
                            self.ui.show_hud_deliverable(
                                title=f"{skill_name.upper()} DELIVERABLE",
                                summary=out_text,
                                kind=skill_name
                            )
                        # Speak first line as voice response
                        first_line = out_text.strip().splitlines()[0] if out_text.strip() else "Task completed."
                        if len(first_line) > 120:
                            first_line = first_line[:115] + "..."
                        self.speak(first_line)
                    except Exception as err:
                        self.ui.write_log(f"ERR: {skill_name} failed: {err}")
                        self.speak(f"Error running {skill_name}: {err}")
                threading.Thread(target=_run_dynamic_feature, daemon=True, name=f"skill-{skill_name}").start()
                return
        except Exception as e_dyn:
            print(f"[Registry] Match error: {e_dyn}")

        if lower.startswith("email ") or lower.startswith("mail ") or "send email" in lower or "write email" in lower or "compose email" in lower:
            # Extract recipient
            rem = text
            for prefix in ("send email", "send an email", "write email", "write an email", "compose email", "compose an email", "email", "mail"):
                if rem.lower().strip().startswith(prefix):
                    rem = rem.strip()[len(prefix):].strip()
                    break
            
            # Remove leading "to " if present
            if rem.lower().startswith("to "):
                rem = rem[3:].strip()
                
            recipient = rem.strip()
            self._email_mode = True
            self._email_recipient = recipient
            
            try:
                self.ui.begin_task_workspace(
                    text,
                    [
                        "Identify the recipient",
                        "Select email application",
                        "Collect message content",
                        "Open application & compose",
                    ],
                    source=source or "local",
                )
            except Exception:
                pass
                
            if not recipient:
                self._email_step = 0
                prompt = "Who would you like to send the email to?"
                self.ui.write_log(f"NIGHTFALL Evo: {prompt}")
                self.speak(prompt)
                try:
                    self.ui.update_task_workspace(
                        status="Identifying recipient",
                        output="Asking for email recipient...",
                        percent=10,
                    )
                except Exception:
                    pass
            else:
                self._email_step = 1
                prompt = "Which email app would you like to use? (Gmail, default mail app, etc.)"
                self.ui.write_log(f"NIGHTFALL Evo: {prompt}")
                self.speak(prompt)
                try:
                    self.ui.update_task_workspace(
                        status="Selecting email app",
                        output=f"Recipient identified: {recipient}. Asking for email application...",
                        percent=25,
                    )
                except Exception:
                    pass
            return

        if _looks_like_daily_briefing_request(text):
            try:
                self.ui.begin_task_workspace(
                    text,
                    ["Collecting weather, schedule, inbox, and headlines", "Delivering your briefing"],
                    source=source or "local",
                )
            except Exception:
                pass
            self.speak("Preparing your daily briefing.")

            def _run_daily_briefing():
                try:
                    from actions.daily_briefing import daily_briefing
                    daily_briefing(parameters={"category": "all"}, player=self.ui, speak=self.speak)
                except Exception as exc:
                    message = f"Daily briefing failed: {exc}"
                    try:
                        self.ui.write_log(f"ERR: {message}")
                    except Exception:
                        pass
                    self.speak(message)

            threading.Thread(
                target=_run_daily_briefing,
                daemon=True,
                name="daily-briefing",
            ).start()
            return

        try:
            from smart_home.smart_device_manager import SmartDeviceManager
            sd_mgr = SmartDeviceManager()
            devices = self._smart_home.list_devices()
            routed_text_home = sd_mgr.route_command(text, devices)
            if routed_text_home != text:
                print(f"[NIGHTFALL EVO] Redirection: '{text}' -> '{routed_text_home}'")
                text = routed_text_home
        except Exception as e:
            print(f"[NIGHTFALL EVO] Redirection error: {e}")

        developer_settings = self.ui._load_app_settings() if hasattr(self.ui, "_load_app_settings") else {}
        developer_workspace = str(developer_settings.get("developer_mode_workspace", "")).strip()
        if not developer_workspace:
            developer_workspace = str(Path.home() / "Desktop" / "NIGHTFALLProjects")
            Path(developer_workspace).mkdir(parents=True, exist_ok=True)

        presentation_request = _looks_like_presentation_request(text)
        spreadsheet_request = _looks_like_spreadsheet_request(text)

        if presentation_request:
            self.speak("Designing your presentation slides...")
            if hasattr(self.ui, "begin_task_workspace"):
                self.ui.begin_task_workspace(
                    text,
                    ["Researching topic", "Outlining slides", "Applying typography & theme", "Generating PowerPoint deck"],
                    source=source or "local"
                )

            def _run_presentation():
                try:
                    from actions.office_generator import generate_presentation_from_prompt
                    res = generate_presentation_from_prompt(text, player=self.ui, speak=self.speak)
                    self.ui.write_log(f"[NIGHTFALLOffice] {res}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Presentation Completed", output=res, percent=100)
                    self.speak("Your presentation has been created and saved to Desktop, sir.")
                except Exception as exc:
                    self.ui.write_log(f"ERR: Presentation generation failed: {exc}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Generation Failed", output=str(exc), percent=0)
                    self.speak("There was an issue creating the presentation, sir. Please check the logs.")

            threading.Thread(target=_run_presentation, daemon=True).start()
            return

        if spreadsheet_request:
            self.speak("Building your spreadsheet workbook...")
            if hasattr(self.ui, "begin_task_workspace"):
                self.ui.begin_task_workspace(
                    text,
                    ["Analyzing data structure", "Defining headers & formulas", "Formatting workbook", "Saving Excel spreadsheet"],
                    source=source or "local"
                )

            def _run_spreadsheet():
                try:
                    from actions.office_generator import generate_spreadsheet_from_prompt
                    res = generate_spreadsheet_from_prompt(text, player=self.ui, speak=self.speak)
                    self.ui.write_log(f"[NIGHTFALLOffice] {res}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Spreadsheet Completed", output=res, percent=100)
                    self.speak("Your spreadsheet workbook has been created and saved to Desktop, sir.")
                except Exception as exc:
                    self.ui.write_log(f"ERR: Spreadsheet generation failed: {exc}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Generation Failed", output=str(exc), percent=0)
                    self.speak("There was an issue creating the spreadsheet, sir. Please check the logs.")

            threading.Thread(target=_run_spreadsheet, daemon=True).start()
            return

        document_request = _looks_like_document_request(text)

        if document_request:
            self.speak("Writing your document now...")
            if hasattr(self.ui, "begin_task_workspace"):
                self.ui.begin_task_workspace(
                    text,
                    ["Understanding the document brief", "Writing the full content",
                     "Formatting the document", "Saving the Word file"],
                    source=source or "local"
                )

            def _run_document():
                try:
                    from actions.document_generator import generate_document_from_prompt
                    res = generate_document_from_prompt(text, player=self.ui, speak=self.speak)
                    self.ui.write_log(f"[NIGHTFALLOffice] {res}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Document Completed", output=res, percent=100)
                    self.speak("Your document has been created and saved, sir.")
                except Exception as exc:
                    self.ui.write_log(f"ERR: Document generation failed: {exc}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Generation Failed", output=str(exc), percent=0)
                    self.speak("There was an issue creating the document, sir. Please check the logs.")

            threading.Thread(target=_run_document, daemon=True, name="document-generator").start()
            return

        website_request = _looks_like_website_request(text)
        code_request = (not presentation_request and not spreadsheet_request and not document_request) and _looks_like_code_request(text) and any(w in text.lower() for w in ("app", "website", "web", "program", "script", "project", "game", "calc", "html", "react"))

        if website_request or code_request:
            self.speak("Working on your project with NIGHTFALL Dev...")
            if hasattr(self.ui, "begin_task_workspace"):
                self.ui.begin_task_workspace(text, ["Analyzing specifications", "Scaffolding files", "Writing code", "Verifying build"], source=source or "local")

            def _run_NIGHTFALL_dev():
                try:
                    import webbrowser
                    from actions.nightfall_dev_agent import run_dev_agent
                    res = run_dev_agent({
                        "description": text,
                        "workspace_path": developer_workspace
                    }, speak=self.speak)
                    self.ui.write_log(f"[NIGHTFALLDev] {res[:400]}")

                    if "encountered an error during inference" in res or "LLM error:" in res:
                        if hasattr(self.ui, "update_task_workspace"):
                            self.ui.update_task_workspace(status="Build Error", output=res, percent=0)
                        self.speak("There was an error building the project, sir. Please check the logs.")
                        return

                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Project Completed", output=res, percent=100)

                    folder_display = developer_workspace
                    try:
                        folder_display = Path(developer_workspace).name or developer_workspace
                    except Exception:
                        pass

                    self.speak(f"Your project has been completed and saved to {folder_display}, sir.")

                    # Auto-open index.html in browser if created
                    try:
                        idx_path = Path(developer_workspace) / "index.html"
                        if idx_path.exists():
                            webbrowser.open(str(idx_path.resolve()))
                    except Exception:
                        pass
                except Exception as exc:
                    self.ui.write_log(f"ERR: NIGHTFALL Dev failed: {exc}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Build Failed", output=str(exc), percent=0)
                    self.speak("There was an issue building the project, sir. Please check the logs.")

            threading.Thread(target=_run_NIGHTFALL_dev, daemon=True).start()
            return

        # Google Workspace Direct Command Handling (Gmail & Calendar checks)
        lower_cmd = text.lower().strip()
        is_email_check = any(phrase in lower_cmd for phrase in ("check email", "check my email", "unread email", "read email", "my inbox", "check inbox", "show emails"))
        is_calendar_check = any(phrase in lower_cmd for phrase in ("my schedule", "what's on my calendar", "check calendar", "my meetings", "schedule today", "events today"))

        if is_email_check:
            self.speak("Checking your Gmail inbox, sir...")
            if hasattr(self.ui, "begin_task_workspace"):
                self.ui.begin_task_workspace(
                    text,
                    ["Connecting to Gmail", "Fetching unread messages", "Parsing headers & body", "Summarizing inbox"],
                    source=source or "local"
                )
            def _run_gw_email():
                try:
                    from actions.google_workspace_mcp import google_workspace
                    res = google_workspace({"service": "gmail", "action": "unread"}, player=self.ui, speak=self.speak)
                    self.ui.write_log(f"[GoogleWorkspace] {res}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Email Check Completed", output=res, percent=100)
                    self.speak("Here are your latest emails, sir.")
                except Exception as exc:
                    self.ui.write_log(f"ERR: Gmail check failed: {exc}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Email Check Failed", output=str(exc), percent=0)
                    self.speak("Unable to check emails. Please ensure your credentials are set up in Settings.")
            threading.Thread(target=_run_gw_email, daemon=True).start()
            return

        if is_calendar_check:
            self.speak("Checking your calendar schedule...")
            if hasattr(self.ui, "begin_task_workspace"):
                self.ui.begin_task_workspace(
                    text,
                    ["Connecting to Calendar", "Retrieving scheduled events", "Formatting timeline"],
                    source=source or "local"
                )
            def _run_gw_calendar():
                try:
                    from actions.google_workspace_mcp import google_workspace
                    res = google_workspace({"service": "calendar", "action": "list"}, player=self.ui, speak=self.speak)
                    self.ui.write_log(f"[GoogleWorkspace] {res}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Schedule Retrieved", output=res, percent=100)
                    self.speak("Here is your schedule, sir.")
                except Exception as exc:
                    self.ui.write_log(f"ERR: Calendar check failed: {exc}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Schedule Check Failed", output=str(exc), percent=0)
                    self.speak("There was an issue checking your schedule, sir.")
            threading.Thread(target=_run_gw_calendar, daemon=True).start()
            return

        # OS Hardware & Diagnostics Direct Command Handling (0 background API credits)
        is_ram_check = any(p in lower_cmd for p in ("eating my ram", "ram hogs", "memory hogs", "check ram", "ram usage", "memory usage", "who is using ram"))
        is_battery_check = any(p in lower_cmd for p in ("battery health", "check battery", "battery status", "battery percentage", "is laptop charging", "how much battery"))
        is_brightness_req = any(p in lower_cmd for p in ("dim screen", "dim monitor", "increase brightness", "lower brightness", "set brightness", "screen brightness"))
        is_kill_req = (lower_cmd.startswith("kill ") or lower_cmd.startswith("terminate ") or lower_cmd.startswith("force kill ")) and len(lower_cmd.split()) <= 4
        is_diag_req = any(p in lower_cmd for p in ("system status", "system diagnostics", "hardware status", "computer vitals", "pc diagnostics"))

        if is_ram_check:
            self.speak("Scanning memory consumers...")
            def _run_ram():
                from actions.system_diagnostics_mcp import system_diagnostics
                res = system_diagnostics({"action": "ram_hogs"}, player=self.ui, speak=self.speak)
                self.ui.write_log(f"[Diagnostics] {res}")
                if hasattr(self.ui, "finish_task_workspace"):
                    self.ui.finish_task_workspace(res, "Memory analysis complete.", 100)
            threading.Thread(target=_run_ram, daemon=True).start()
            return

        if is_battery_check:
            def _run_battery():
                from actions.system_diagnostics_mcp import system_diagnostics
                res = system_diagnostics({"action": "battery"}, player=self.ui, speak=self.speak)
                self.ui.write_log(f"[Diagnostics] {res}")
                if hasattr(self.ui, "finish_task_workspace"):
                    self.ui.finish_task_workspace(res, "Battery check complete.", 100)
            threading.Thread(target=_run_battery, daemon=True).start()
            return

        if is_brightness_req:
            import re
            m = re.search(r"(\d+)", lower_cmd)
            lvl = int(m.group(1)) if m else None
            rel = "dim" in lower_cmd or "lower" in lower_cmd or "increase" in lower_cmd or "boost" in lower_cmd
            if rel and lvl is None:
                lvl = -20 if ("dim" in lower_cmd or "lower" in lower_cmd) else 20
            def _run_brightness():
                from actions.system_diagnostics_mcp import system_diagnostics
                p = {"action": "brightness"}
                if lvl is not None:
                    p["level"] = lvl
                    p["relative"] = rel
                res = system_diagnostics(p, player=self.ui, speak=self.speak)
                self.ui.write_log(f"[Diagnostics] {res}")
                if hasattr(self.ui, "finish_task_workspace"):
                    self.ui.finish_task_workspace(res, "Brightness adjusted.", 100)
            threading.Thread(target=_run_brightness, daemon=True).start()
            return

        if is_kill_req:
            target_app = lower_cmd.split(None, 1)[1].strip()
            def _run_kill():
                from actions.system_diagnostics_mcp import system_diagnostics
                res = system_diagnostics({"action": "kill", "target": target_app, "force": "force" in lower_cmd}, player=self.ui, speak=self.speak)
                self.ui.write_log(f"[Diagnostics] {res}")
                if hasattr(self.ui, "finish_task_workspace"):
                    self.ui.finish_task_workspace(res, "Process management complete.", 100)
            threading.Thread(target=_run_kill, daemon=True).start()
            return

        if is_diag_req:
            self.speak("Retrieving system diagnostics report...")
            def _run_diag():
                from actions.system_diagnostics_mcp import system_diagnostics
                res = system_diagnostics({"action": "status"}, player=self.ui, speak=self.speak)
                self.ui.write_log(f"[Diagnostics] {res}")
                if hasattr(self.ui, "finish_task_workspace"):
                    self.ui.finish_task_workspace(res, "System report ready.", 100)
            threading.Thread(target=_run_diag, daemon=True).start()
            return

        # Autonomous Self-Healing & Continuous Learning Fast-Path
        is_trigger_bug = any(p in lower_cmd for p in ("trigger test bug", "simulate bug", "test bug", "create bug", "simulate error", "trigger error", "break test"))
        is_heal_cmd = any(p in lower_cmd for p in ("fix that bug", "fix the bug", "heal yourself", "auto heal", "patch yourself", "fix error", "fix this error"))
        is_rollback_cmd = any(p in lower_cmd for p in ("undo last patch", "rollback patch", "revert patch", "undo patch"))
        is_patch_history = any(p in lower_cmd for p in ("patch history", "patch log", "show patches", "auto heal status"))
        is_learn_rule = (
            lower_cmd.startswith("remember to ") or 
            lower_cmd.startswith("remember that ") or 
            (lower_cmd.startswith("always ") and len(lower_cmd.split()) > 2 and not lower_cmd.startswith("always open")) or
            (lower_cmd.startswith("never ") and len(lower_cmd.split()) > 2) or
            "new rule:" in lower_cmd
        )

        if is_trigger_bug:
            def _run_test_bug():
                try:
                    from actions.test_action import test_action
                    res = test_action({}, player=self.ui, speak=self.speak)
                    self.ui.write_log(f"[TestAction] {res}")
                    self.speak(f"Test action succeeded with no errors: {res}. The bug appears already healed!")
                except Exception as exc:
                    import traceback
                    from actions.auto_heal_engine import AutoHealEngine
                    tb_str = traceback.format_exc()
                    AutoHealEngine.record_last_error(tb_str)
                    err_msg = f"Simulated bug triggered in test_action.py: {type(exc).__name__}. Traceback captured! You can now say 'NIGHTFALL, fix that bug'."
                    self.speak(err_msg)
                    self.ui.write_log(f"[AutoHeal Test] {err_msg}")
                    if hasattr(self.ui, "finish_task_workspace"):
                        self.ui.finish_task_workspace(tb_str, "Simulated bug captured.", 100)
            threading.Thread(target=_run_test_bug, daemon=True).start()
            return

        if is_heal_cmd:
            self.speak("Analyzing last captured traceback and synthesizing hotfix...")
            def _run_heal():
                from actions.auto_heal_engine import auto_heal
                res = auto_heal({"action": "heal"}, player=self.ui, speak=self.speak)
                self.ui.write_log(f"[AutoHeal] {res}")
                if hasattr(self.ui, "finish_task_workspace"):
                    self.ui.finish_task_workspace(res, "Self-patching complete.", 100)
            threading.Thread(target=_run_heal, daemon=True).start()
            return

        if is_rollback_cmd:
            def _run_rollback():
                from actions.auto_heal_engine import auto_heal
                res = auto_heal({"action": "rollback"}, player=self.ui, speak=self.speak)
                self.ui.write_log(f"[AutoHeal] {res}")
                if hasattr(self.ui, "finish_task_workspace"):
                    self.ui.finish_task_workspace(res, "Rollback complete.", 100)
            threading.Thread(target=_run_rollback, daemon=True).start()
            return

        if is_patch_history:
            def _run_history():
                from actions.auto_heal_engine import auto_heal
                action_type = "status" if "status" in lower_cmd else "history"
                res = auto_heal({"action": action_type}, player=self.ui, speak=self.speak)
                self.ui.write_log(f"[AutoHeal] {res}")
                if hasattr(self.ui, "finish_task_workspace"):
                    self.ui.finish_task_workspace(res, "Auto-heal report ready.", 100)
            threading.Thread(target=_run_history, daemon=True).start()
            return

        if is_learn_rule:
            rule_raw = lower_cmd.replace("remember that ", "").replace("remember to ", "").replace("new rule: ", "").strip()
            def _run_learn():
                from core.learned_rules import LearnedRulesEngine
                res = LearnedRulesEngine.add_rule(rule_raw, origin="voice_directive")
                msg = res.get("message", f"Understood. I have committed '{rule_raw}' to my continuous memory.")
                self.speak(msg)
                self.ui.write_log(f"[LearnedRules] {msg}")
                if hasattr(self.ui, "finish_task_workspace"):
                    self.ui.finish_task_workspace(msg, "Rule learned.", 100)
            threading.Thread(target=_run_learn, daemon=True).start()
            return

        # Local PC Volume Control (Speakers)
        is_volume_cmd = any(p in lower_cmd for p in ("volume up", "volume down", "mute volume", "mute audio", "unmute", "set volume", "increase volume", "decrease volume", "lower volume")) or (lower_cmd in ("mute", "unmute", "volume max"))
        if is_volume_cmd and not any(m in lower_cmd for m in ("phone", "mobile", "android", "tablet")):
            def _run_volume():
                from actions.computer_settings import volume_up, volume_down, volume_mute, volume_set
                import re
                if any(w in lower_cmd for w in ("up", "increase", "higher", "raise")):
                    volume_up()
                    self.speak("Volume increased.")
                elif any(w in lower_cmd for w in ("down", "decrease", "lower")):
                    volume_down()
                    self.speak("Volume decreased.")
                elif "unmute" in lower_cmd or "mute" in lower_cmd:
                    volume_mute()
                    self.speak("Audio toggled.")
                else:
                    m = re.search(r"(\d+)", lower_cmd)
                    if m:
                        val = int(m.group(1))
                        volume_set(val)
                        self.speak(f"Volume set to {val} percent.")
                    else:
                        volume_up()
                        self.speak("Volume adjusted.")
            threading.Thread(target=_run_volume, daemon=True).start()
            return

        # Local PC App Launcher (runs on computer unless explicitly targeted to phone)
        has_mobile_target = any(m in lower_cmd for m in ("on phone", "on my phone", "on mobile", "on my mobile", "on android", "on tablet"))
        is_open_app_cmd = any(lower_cmd.startswith(prefix) for prefix in ("open app ", "open ", "launch app ", "launch ", "start app ", "start ")) and not has_mobile_target and not any(p in lower_cmd for p in ("website", "url", "http", "presentation", "sheet", "spreadsheet", "project", "code", "game"))
        if is_open_app_cmd:
            from actions.open_app import open_app, _APP_ALIASES
            clean_app_candidate = lower_cmd
            for prefix in ("open app ", "open ", "launch app ", "launch ", "start app ", "start "):
                if clean_app_candidate.startswith(prefix):
                    clean_app_candidate = clean_app_candidate[len(prefix):].strip()
                    break
            if clean_app_candidate in _APP_ALIASES or any(clean_app_candidate in k for k in _APP_ALIASES):
                def _run_open_local_app():
                    open_app({"app_name": clean_app_candidate}, player=self.ui)
                    self.speak(f"Opening {clean_app_candidate}, sir.")
                threading.Thread(target=_run_open_local_app, daemon=True).start()
                return

        memory_ctx = _memory_context_for_request(text)
        routed_text = f"{memory_ctx}\n\nCurrent User Request:\n{text}" if memory_ctx else text
        if source == "instagram":
            routed_text = f"Owner sent this via Instagram DM: {text}\n(SYSTEM: If this is an action like opening an app or running a command, you MUST execute it using your tools rather than just replying with text.)"
        if text.lower() in {"stop meeting mode", "end meeting mode", "close meeting mode"}:
            self._stop_meeting_mode("Meeting mode closed.")
            return
        if self._handle_attention_response(text):
            return
        try:
            self.ui.begin_task_workspace(text, _build_task_plan(text), source=source or "local")
        except Exception:
            pass
        if source != "instagram" and self._handle_nightfall_connect_command(text, source=source or "local"):
            return
        if self._handle_smart_home_command(text, source=source or "local"):
            return
        if _looks_like_screen_request(text):
            try:
                self.ui.update_task_workspace(
                    status="Scanning screen",
                    output="NIGHTFALL Evo is inspecting the screen for what you asked about.",
                    percent=40,
                )
            except Exception:
                pass
            print("[Main] Screen analysis request received")
            # One screenshot, one answer: when the unified voice session is
            # live it answers itself; the separately-voiced vision module is
            # only a fallback.
            if self._live_session_available() and not getattr(self.ui, "muted", False):
                frame = self._capture_screen_bytes()
                if frame and self._send_screen_to_live(text, frame):
                    self._last_screen_capture_at = time.time()
                    try:
                        self.ui.update_task_workspace(
                            status="Screen sent to the voice session",
                            output="NIGHTFALL Evo is looking at your screen.",
                            percent=80,
                        )
                    except Exception:
                        pass
                    print("[Main] Screenshot handed to the live voice session")
                    return
            img_bytes = None
            if hasattr(self.ui, "capture_screen_bytes"):
                print("[Main] Capturing screenshot from UI")
                img_bytes = self.ui.capture_screen_bytes()
                print(f"[Main] UI screenshot capture returned {len(img_bytes) if img_bytes is not None else 'None'} bytes")
            else:
                print("[Main] UI object has no capture_screen_bytes method")

            def _run_screen_process():
                print("[Main] Starting screen_process thread")
                success = screen_process(
                    parameters={"angle": "screen", "text": text},
                    response=None,
                    player=self.ui,
                    session_memory=None,
                    image_bytes=img_bytes,
                )
                print(f"[Main] screen_process finished: {success}")
                if not success:
                    try:
                        self.ui.update_task_workspace(
                            status="Screen analysis failed",
                            output=(
                                "Screen analysis could not complete. "
                                "Check your internet connection, API key, or screen capture permissions."
                            ),
                            percent=0,
                        )
                    except Exception:
                        pass

            threading.Thread(target=_run_screen_process, daemon=True).start()
            return
        # Route directly to Local Brain if preferred by user in settings or in air-gapped offline mode
        app_settings = config_manager.load_settings()
        configured_provider = app_settings.get("default_ai_provider", "Gemini")
        is_offline_mode = bool(app_settings.get("offline_mode_enabled", False))

        # If offline mode or Local provider selected, route directly to Local Brain
        if is_offline_mode or configured_provider == "Local":
            is_local_preferred = True
        else:
            is_local_preferred = False

        if is_local_preferred or self._use_openrouter_first or not self._loop or not self.session:
            threading.Thread(target=self._fallback_reply, args=(text, memory_ctx), daemon=True).start()
            return
        self.ui.set_state("THINKING")
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": routed_text}]},
                turn_complete=True
            ),
            self._loop
        )


    def _handle_smart_home_command(self, text: str, source: str = "local") -> bool:
        normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s%]", " ", text.lower())).strip()
        
        # Don't touch commands targeted at phone or mobile
        mobile_words = (
            "phone", "mobile", "android", "tablet", "NIGHTFALL connect",
            "my phone", "my mobile", "my tablet", "my android", "flashlight", "torch"
        )
        if any(word in normalized for word in mobile_words):
            return False

        # Don't hijack PC screen/monitor brightness
        if any(w in normalized for w in ("screen", "monitor", "display", "laptop", "pc")) and "brightness" in normalized:
            return False

        smart_home_words = (
            "fan", "fans", "light", "lights", "lamp", "plug", "socket", "outlet", "bulb",
            "kasa", "atomberg", "bedroom", "living room", "kitchen", "office room",
            "balcony", "bathroom", "hall", "dining", "smart home", "smart-home",
            "ac", "air conditioner", "thermostat"
        )
        has_smart_word = any(re.search(r"\b" + re.escape(word) + r"\b", normalized) for word in smart_home_words)
        if not has_smart_word:
            try:
                for d in self._smart_home.list_devices():
                    d_name = str(d.get("name", "")).lower()
                    d_room = str(d.get("room", "")).lower()
                    if (d_name and d_name in normalized) or (d_room and d_room in normalized):
                        has_smart_word = True
                        break
            except Exception:
                pass

        if not has_smart_word:
            return False
        try:
            result = self._smart_home.execute_command(text)
            detail = str(result.get("detail") or "Smart-home command completed.")
            title = f"Smart Home: {result.get('action', 'control')}"
            plan = [
                "Identify the target device or room",
                "Send the command to the smart-home provider",
                "Verify the new state",
                "Report the result",
            ]
            self.ui.update_task_workspace(
                title=title,
                command=text,
                plan=plan,
                status="Executing smart-home command",
                output=detail,
                percent=100,
                source=source,
            )
            self.ui.write_log(f"NIGHTFALL Evo: {detail}")
            self.speak(detail)
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return True
        except Exception as exc:
            message = f"I couldn't control the smart home device: {exc}"
            self.ui.write_log(f"ERR: {message}")
            self.ui.update_task_workspace(
                title="Smart Home Control",
                command=text,
                plan=[
                    "Identify the target device or room",
                    "Send the command to the smart-home provider",
                    "Verify the new state",
                ],
                status="Smart-home command failed",
                output=message,
                percent=100,
                source=source,
            )
            self.speak(message)
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return True

    def _extract_launch_app_name(self, text: str) -> str:
        normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s%]", " ", text.lower())).strip()
        if not normalized:
            return ""

        prefixes = (
            "launch app ",
            "open app ",
            "start app ",
            "open the app ",
            "launch the app ",
            "start the app ",
            "open ",
            "launch ",
            "start ",
            "run ",
            "bring up ",
        )

        candidate = normalized
        for prefix in prefixes:
            if candidate.startswith(prefix):
                candidate = candidate[len(prefix):]
                break

        candidate = re.sub(r"\s+(?:on|in|to)\s+(?:my\s+)?(?:phone|mobile|tablet|android|device)\b.*$", "", candidate).strip()
        candidate = re.sub(r"\b(?:app|application|please|the)\b", " ", candidate).strip()
        candidate = re.sub(r"\s+", " ", candidate)
        return candidate

    def _handle_nightfall_connect_command(self, text: str, source: str = "local") -> bool:
        normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s%]", " ", text.lower())).strip()
        
        # Explicit mobile indicators: only route to phone if user explicitly mentions phone/mobile
        # or asks for a phone-exclusive action (flashlight/torch, ring phone).
        is_flashlight = "flashlight" in normalized or "torch" in normalized
        is_ring_phone = any(p in normalized for p in ("ring phone", "ring my phone", "find my phone", "find phone", "locate phone"))
        has_phone_ref = any(token in normalized for token in (
            "phone", "mobile", "android", "tablet", "NIGHTFALL connect",
            "my phone", "my mobile", "my tablet", "my android"
        ))

        # Never hijack local PC commands (e.g. open chrome, volume up, pc battery) unless user says 'on phone'
        if not (has_phone_ref or is_flashlight or is_ring_phone):
            return False

        try:
            devices_json = connect_list_devices(parameters={}, player=self.ui)
            devices = json.loads(devices_json).get("devices", [])
        except Exception:
            devices = []

        if not devices:
            return False

        try:
            target = ""
            for device in devices:
                name = str(device.get("name", "")).lower()
                device_id = str(device.get("device_id", "")).lower()
                platform = str(device.get("platform", "")).lower()
                if any(token in normalized for token in (name, device_id, platform, "phone", "mobile", "android", "tablet")):
                    target = str(device.get("device_id") or device.get("name") or "").strip()
                    break
            if not target and (has_phone_ref or is_flashlight or is_ring_phone) and len(devices) == 1:
                target = str(devices[0].get("device_id") or devices[0].get("name") or "").strip()
            if not target:
                return False

            action = None
            params: dict[str, object] = {}
            
            # Check for complex multi-step mobile workflow first
            # Examples: "open X and do Y", "search for Z on X", "message X on Y"
            if (" and " in normalized or " search " in normalized or " play " in normalized or " message " in normalized or " tell " in normalized) and ("open " in normalized or "launch " in normalized or "app" in normalized):
                action = "mobile_autopilot"
                params["instruction"] = text
            elif "flashlight" in normalized and ("turn on" in normalized or "switch on" in normalized or "power on" in normalized or "on" == normalized):
                action = "flashlight_on"
            elif "flashlight" in normalized and ("turn off" in normalized or "switch off" in normalized or "power off" in normalized or "off" == normalized):
                action = "flashlight_off"
            elif "battery" in normalized or "charge" in normalized:
                action = "get_battery"
            elif "open url" in normalized or "open website" in normalized or "go to" in normalized:
                action = "open_url"
            elif any(word in normalized for word in ("launch app", "open app", "start app", "open ", "launch ", "start ", "run ", "bring up ")):
                app_name = self._extract_launch_app_name(text)
                if app_name:
                    action = "launch_app"
                    params["app_name"] = app_name
            elif "volume" in normalized and ("set" in normalized or "change" in normalized or "to " in normalized):
                action = "volume_set"
                match = re.search(r"\b(\d{1,3})\b", normalized)
                if match:
                    params["value"] = int(match.group(1))
            elif "volume" in normalized:
                action = "volume_get"
            elif "info" in normalized or "status" in normalized:
                action = "get_device_info"
                
            if not action:
                return False

            if action == "mobile_autopilot":
                # Route complex commands to the autopilot loop
                def _run_autopilot():
                    try:
                        from actions.mobile_autopilot import mobile_autopilot
                        result_json = mobile_autopilot({"target": target, "instruction": params["instruction"]}, player=self.ui, speak=self.speak)
                        print(f"[Autopilot Result]: {result_json}")
                        try:
                            result_dict = json.loads(result_json)
                            if not result_dict.get("success"):
                                print(f"[Autopilot Error]: {result_dict.get('error') or result_dict}")
                                self.speak(f"Autopilot encountered an error: {result_dict.get('error', 'unknown error')}")
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"Autopilot failed: {e}")
                    finally:
                        try:
                            self.ui.set_state("IDLE")
                        except Exception:
                            pass
                threading.Thread(target=_run_autopilot, daemon=True).start()
                return True

            if action == "launch_app":
                app_name = str(params.get("app_name") or "").strip()
                if not app_name:
                    return False
            if action == "open_url":
                m = re.search(r"(https?://\S+)", text, re.IGNORECASE)
                if m:
                    params["url"] = m.group(1)
                else:
                    return False

            payload = {
                "device": target,
                "action": action,
                "parameters": params,
            }
            result_json = connect_execute(parameters=payload, player=self.ui)
            result = json.loads(result_json)
            if result.get("success", False):
                detail = str(result.get("detail") or result.get("error") or "Device command completed.")
                title = f"NIGHTFALL Connect: {action}"
                self.ui.update_task_workspace(
                    title=title,
                    command=text,
                    plan=[
                        "Identify the paired phone or device",
                        "Route the command through NIGHTFALL Connect",
                        "Verify the device response",
                        "Report the result",
                    ],
                    status="Executing device command",
                    output=detail,
                    percent=100,
                    source=source,
                )
                self.ui.write_log(f"NIGHTFALL Evo: {detail}")
                self.speak(detail)
                if not self.ui.muted:
                    self.ui.set_state("LISTENING")
                return True

            self.ui.write_log(f"ERR: NIGHTFALL Connect command failed: {result.get('error') or 'Unknown error'}")
            return False
        except Exception:
            return False

    def _connect_tool_voice(self, name: str, raw_result: str) -> str | None:
        if not str(name or "").startswith("connect_"):
            return None
        try:
            data = json.loads(raw_result) if isinstance(raw_result, str) else dict(raw_result or {})
        except Exception:
            data = {}

        if name == "connect_list_devices":
            count = int(data.get("count") or len(data.get("devices") or []))
            return f"I found {count} connected device{'s' if count != 1 else ''}."

        if name == "connect_get_device":
            device = data.get("device") or {}
            label = str(device.get("name") or "the device")
            status = "online" if device.get("online") else "offline"
            return f"{label} is {status}."

        if name == "connect_get_capabilities":
            device = data.get("device") or {}
            label = str(device.get("name") or "The device")
            return f"{label} capabilities are ready."

        if name == "connect_pair_device":
            pairing = data.get("pairing") or data
            code = str(pairing.get("pairing_code") or "").strip()
            if code:
                return f"Pairing code ready: {code}."
            return "Pairing is ready."

        if name == "connect_disconnect_device":
            return "The device has been disconnected."

        if name == "connect_execute":
            if data.get("success", False):
                detail = data.get("detail")
                if isinstance(detail, dict):
                    detail = detail.get("message") or detail.get("status") or detail.get("result")
                if not detail and isinstance(data.get("data"), dict):
                    payload = data.get("data") or {}
                    detail = payload.get("message") or payload.get("status") or payload.get("result")
                if not detail:
                    detail = data.get("error") or "Task completed."
                return str(detail)
            return str(data.get("error") or "The device command failed.")

        return None

    def _attention_message(self, event: dict) -> str:
        app = (event.get("app") or "an app").strip()
        kind = (event.get("kind") or "message").strip().lower()
        if kind == "call":
            return f"Incoming call detected on {app}. Should I pick it up, ignore it, or cut the call?"
        title = (event.get("title") or "").strip()
        preview = (event.get("preview") or "").strip()
        if title:
            return f"You received a message on {app} from {title}. It says: {preview}"
        return f"You received a message on {app}. It says: {preview}"

    def _announce_attention(self, event: dict):
        msg = self._attention_message(event)
        self.ui.write_log(f"NIGHTFALL Evo: {msg}")
        if self.session and self._loop:
            self.speak(msg)
        else:
            threading.Thread(target=speak_native, args=(msg,), daemon=True).start()
        self.ui.show_attention_alert(event)

    def _on_external_notification(self, event: dict):
        if not isinstance(event, dict):
            return
        kind = (event.get("kind") or "message").strip().lower()
        app = (event.get("app") or "App").strip()
        preview = (event.get("preview") or "").strip()
        settings = {}
        try:
            settings = self.ui._load_app_settings()
        except Exception:
            settings = {}

        if kind == "call" and not bool(settings.get("attention_call_prompts", True)):
            return
        if kind == "message" and not bool(settings.get("attention_message_prompts", True)):
            return

        with self._attention_lock:
            current = self._pending_attention
            if current:
                same_app = (current.get("app") or "").strip().lower() == app.lower()
                same_kind = (current.get("kind") or "").strip().lower() == kind
                if same_app and same_kind:
                    return
            self._pending_attention = dict(event)

        self._announce_attention(event)
        if preview:
            self.ui.write_log(f"[Attention] {app}: {preview}")
        if kind == "call" and app.lower() in {"zoom", "teams", "whatsapp"}:
            self._start_meeting_mode(event)

    def _start_meeting_mode(self, event: dict):
        event = dict(event or {})
        app = (event.get("app") or "Meeting").strip()
        title = event.get("title") or f"{app} meeting"
        summary = f"Watching {app} for questions and answers."
        with self._meeting_lock:
            self._meeting_active = True
            self._meeting_event = event
        self.ui.set_meeting_mode(True, title, summary, "Listening for questions on screen...", self._meeting_assistant.latest_speech())
        self._meeting_assistant.start(title=title, context=summary)
        self.ui.write_log(f"SYS: Meeting mode enabled for {app}.")

    def _stop_meeting_mode(self, reason: str = "Meeting mode stopped."):
        with self._meeting_lock:
            was_active = self._meeting_active
            self._meeting_active = False
            self._meeting_event = None
        if was_active:
            self._meeting_assistant.stop()
            self.ui.set_meeting_mode(False, "", "", "")
            self.ui.write_log(f"SYS: {reason}")

    def _on_meeting_update(self, payload: dict):
        if not isinstance(payload, dict):
            return
        active = bool(payload.get("active"))
        title = payload.get("title") or "Meeting mode"
        summary = payload.get("summary") or ""
        answer = payload.get("answer") or ""
        speech = payload.get("speech") or ""
        self.ui.set_meeting_mode(active, title, summary, answer, speech)
        if summary:
            self.ui.write_log(f"[Meeting] {summary}")
        if answer:
            self.ui.write_log(f"NIGHTFALL Evo: {answer}")

    def _on_meeting_state(self, state: str):
        if state == "LISTENING":
            self.ui.set_state("LISTENING")
        elif state == "MEETING":
            self.ui.set_state("THINKING")

    def _attention_matches(self, text: str, words: tuple[str, ...]) -> bool:
        t = (text or "").lower()
        return any(word in t for word in words)

    def _prompt_message_reply(self, event: dict) -> bool:
        if not isinstance(event, dict):
            return False
        with self._attention_lock:
            self._pending_reply_event = dict(event)
            self._pending_attention = None
            self._reply_mode = True

        message = "What would you like to say in reply?"
        self.ui.write_log(f"NIGHTFALL Evo: {message}")
        if self.session and self._loop:
            self.speak(message)
        else:
            threading.Thread(target=speak_native, args=(message,), daemon=True).start()
        try:
            self.ui.begin_task_workspace(
                "Replying to message",
                [
                    "Type your response",
                    "I will reword it naturally",
                    f"Send via {event.get('app', 'the app')}",
                ],
                source="reply",
            )
            self.ui.update_task_workspace(
                status="Awaiting your reply",
                output="Type the message you want to send, and I will make it sound natural before sending it as you.",
                percent=10,
            )
        except Exception:
            pass
        return True

    def _handle_pending_reply(self, text: str) -> bool:
        with self._attention_lock:
            event = dict(self._pending_reply_event or {})
            self._pending_reply_event = None
        if not event:
            return False

        lower = (text or "").lower()
        if self._attention_matches(lower, ("cancel", "never mind", "skip", "do not send", "don't send")):
            self._reply_mode = False
            self.ui.write_log("SYS: Reply cancelled.")
            try:
                self.ui.finish_task_workspace("Reply cancelled.", "Cancelled", 100)
            except Exception:
                pass
            return True

        self.ui.write_log(f"SYS: Drafting reply to {event.get('title') or event.get('app')}.")
        threading.Thread(target=self._draft_and_send_reply, args=(event, text), daemon=True).start()
        return True

    def _rewrite_reply_text(self, user_text: str, event: dict) -> str:
        prompt = (
            "You are a friendly assistant helping a user rewrite their draft reply for a chat message. "
            "Keep the same meaning and intent, expand the wording slightly, and make it sound natural and human. "
            "Do not mention the notification, app, or any internal system details. "
            "Return only the rewritten reply text.\n\n"
            "Notification context:\n"
            f"App: {event.get('app', '')}\n"
            f"Sender: {event.get('title', '')}\n"
            f"Preview: {event.get('preview', '')}\n\n"
            "User draft reply:\n"
            f"{user_text}\n\n"
            "Reply text:"
        )
        try:
            return _gemini_text_reply(prompt) or user_text
        except Exception:
            try:
                return openrouter_client.chat(
                    prompt,
                    system="You are a friendly assistant. Rewrite the reply naturally and humanely.",
                )
            except Exception:
                return user_text

    def _draft_and_send_reply(self, event: dict, text: str):
        try:
            reply_text = self._rewrite_reply_text(text, event)
            if not reply_text:
                reply_text = text
            self.ui.update_task_workspace(
                status="Sending reply",
                output="Sending your expanded reply now...",
                percent=70,
            )
            receiver = (event.get("title") or "").strip()
            platform = (event.get("app") or "whatsapp").strip()
            if not receiver:
                self.ui.write_log("ERR: Could not determine recipient for reply.")
                try:
                    self.ui.finish_task_workspace("Reply failed: recipient not found.", "Reply failed", 100)
                except Exception:
                    pass
                return
            result = send_message(
                parameters={
                    "receiver": receiver,
                    "message_text": reply_text,
                    "platform": platform,
                },
                player=self.ui,
            )
            self._reply_mode = False
            self.ui.write_log(f"SYS: {result}")
            try:
                self.ui.finish_task_workspace(reply_text, "Reply delivered.", 100)
            except Exception:
                pass
        except Exception as e:
            self._reply_mode = False
            self.ui.write_log(f"ERR: Reply failed: {e}")
            try:
                self.ui.finish_task_workspace(f"Reply failed: {e}", "Reply failed", 100)
            except Exception:
                pass
        finally:
            if not self.ui.muted:
                self.ui.set_state("LISTENING")

    def _parse_ig_reply_intent(self, text: str) -> tuple[str, str]:
        system_prompt = (
            "You are an intent parser. The user received an Instagram DM. I asked: 'What should I reply, or should I take over?'. "
            "The user responded. Determine their intent.\n"
            "1. If they want me to take over/handle it, return TAKE_OVER.\n"
            "2. If they want to cancel/skip, return CANCEL.\n"
            "3. If they dictate a specific message to send (e.g. 'tell them I am busy', 'say hi'), return MANUAL_REPLY and the exact text.\n"
            "4. If they are just greeting me (e.g. 'hi') or making small talk, return IGNORE.\n"
            "Output ONLY valid JSON: {\"intent\": \"...\", \"reply_text\": \"...\"}"
        )
        try:
            client = genai.Client(api_key=_get_api_key(), http_options={"api_version": "v1beta"})
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=f"{system_prompt}\n\nUser Response: {text}",
                config={"temperature": 0.1, "response_mime_type": "application/json"}
            )
            data = json.loads(response.text.strip())
            return data.get("intent", "IGNORE"), data.get("reply_text", "")
        except Exception:
            lower = text.lower()
            if any(c in lower for c in ("cancel", "stop", "skip", "never mind", "abort", "don't reply", "do not reply")):
                return "CANCEL", ""
            if any(a in lower for a in ("take over", "auto mode", "handle it", "you reply", "auto reply", "take care of it")):
                return "TAKE_OVER", ""
            for verb in ("reply", "tell him", "tell her", "tell them", "tell", "say", "send"):
                if verb in lower:
                    import re
                    pattern = rf"^.*?\b{verb}\b(?:\s+(?:him|her|them|that|to\s+say|saying))?\s*"
                    cleaned = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
                    if cleaned:
                        return "MANUAL_REPLY", cleaned
            if len(text.strip().split()) >= 2 and not any(g in lower for g in ("hello", "hey nightfall", "who are you")):
                return "MANUAL_REPLY", text.strip()
            return "IGNORE", ""

    def _handle_ig_reply_flow(self, text: str) -> bool:
        if getattr(self, "_ig_pending_thread", None):
            thread_id = self._ig_pending_thread.get("thread_id")
            username = self._ig_pending_thread.get("username")
            message_text = self._ig_pending_thread.get('message')
            
            intent, payload = self._parse_ig_reply_intent(text)
            
            if intent == "CANCEL":
                self._ig_reply_mode = False
                msg = "Instagram reply cancelled."
                self.ui.write_log(f"NIGHTFALL Evo: {msg}")
                self.speak(msg)
                self._ig_pending_thread = None
                return True
                
            if intent == "TAKE_OVER":
                self.ui.write_log("SYS: Taking over Instagram thread.")
                self.speak(f"I will now take over the chat with {username}.")
                from actions.instagram_mcp import add_auto_thread, send_direct_reply
                add_auto_thread(thread_id)
                def _generate_and_send():
                    try:
                        reply = _ig_gemini_reply(username, message_text)
                        send_direct_reply(thread_id, reply)
                    except Exception as e:
                        print(f"Error taking over thread: {e}")
                threading.Thread(target=_generate_and_send, daemon=True).start()
                
            elif intent == "MANUAL_REPLY":
                self.ui.write_log(f"SYS: Sending manual reply to @{username}: '{payload}'")
                self.speak("Message sent.")
                from actions.instagram_mcp import send_direct_reply
                def _do_send():
                    try:
                        send_direct_reply(thread_id, payload)
                    except Exception as e:
                        print(f"Error sending manual reply to @{username}: {e}")
                threading.Thread(target=_do_send, daemon=True).start()
                
            elif intent == "IGNORE":
                return False
                
            self._ig_reply_mode = False
            self._ig_pending_thread = None
            return True
        return False

    def _handle_email_flow(self, text: str) -> bool:
        lower = text.lower()
        if any(cancel in lower for cancel in ("cancel", "never mind", "skip", "stop", "abort")):
            self._email_mode = False
            self._email_step = 0
            self._email_profiles = {}
            msg = "Email sending cancelled, sir."
            self.ui.write_log(f"NIGHTFALL Evo: {msg}")
            self.speak(msg)
            try:
                self.ui.finish_task_workspace("Email sending cancelled.", "Cancelled", 100)
            except Exception:
                pass
            return True

        if self._email_step == 0:
            # We just collected the recipient
            self._email_recipient = text.strip()
            self._email_step = 1
            prompt = "Which email app would you like to use? (Gmail, default mail app, etc.)"
            self.ui.write_log(f"NIGHTFALL Evo: {prompt}")
            self.speak(prompt)
            try:
                self.ui.update_task_workspace(
                    status="Selecting email app",
                    output=f"Recipient: {self._email_recipient}. Asking for email application...",
                    percent=40,
                )
            except Exception:
                pass
            return True

        elif self._email_step == 1:
            # We just collected the app name
            self._email_app = text.strip()
            self._email_step = 2
            
            prompt = "What is the message you'd like to send?"
            self.ui.write_log(f"NIGHTFALL Evo: {prompt}")
            self.speak(prompt)
            try:
                self.ui.update_task_workspace(
                    status="Collecting message",
                    output=f"Recipient: {self._email_recipient} | App: {self._email_app}. Asking for message content...",
                    percent=70,
                )
            except Exception:
                pass
            return True

        elif self._email_step == 2:
            # We just collected the message
            self._email_message = text.strip()
            self._email_mode = False
            self._email_step = 0
            
            # Now let's execute composing!
            msg = f"Opening {self._email_app} and composing email to {self._email_recipient}..."
            self.ui.write_log(f"NIGHTFALL Evo: {msg}")
            self.speak(msg)
            try:
                self.ui.update_task_workspace(
                    status="Composing email",
                    output=f"Composing message to {self._email_recipient} via {self._email_app}...",
                    percent=90,
                )
            except Exception:
                pass
            
            try:
                import urllib.parse
                import webbrowser
                subject = "Message from NIGHTFALL Evo"
                quoted_recipient = urllib.parse.quote(self._email_recipient)
                quoted_subject = urllib.parse.quote(subject)
                quoted_body = urllib.parse.quote(self._email_message)
                
                import os
                import subprocess
                import shutil

                app_lower = self._email_app.lower()
                chrome_opened = False

                if "gmail" in app_lower or "chrome" in app_lower:
                    import urllib.parse
                    quoted_recipient = urllib.parse.quote(self._email_recipient)
                    quoted_subject = urllib.parse.quote("Message from NIGHTFALL Evo")
                    quoted_body = urllib.parse.quote(self._email_message)
                    url = f"https://mail.google.com/mail/?view=cm&fs=1&to={quoted_recipient}&su={quoted_subject}&body={quoted_body}"
                    
                    # Use webbrowser to naturally open a new tab in the already running Chrome window
                    import webbrowser
                    webbrowser.open(url)

                elif "outlook" in app_lower:
                    url = f"https://outlook.live.com/default/?path=/mail/action/compose&to={quoted_recipient}&subject={quoted_subject}&body={quoted_body}"
                    webbrowser.open(url)
                else:
                    url = f"mailto:{quoted_recipient}?subject={quoted_subject}&body={quoted_body}"
                    webbrowser.open(url)
                
                try:
                    self.ui.finish_task_workspace("Email composed successfully.", "Composed", 100)
                except Exception:
                    pass
            except Exception as e:
                err_msg = f"Failed to compose email: {e}"
                self.ui.write_log(f"ERR: {err_msg}")
                self.speak(err_msg)
                try:
                    self.ui.finish_task_workspace(err_msg, "Failed", 100)
                except Exception:
                    pass
            return True

        return False

    def _handle_attention_response(self, text: str) -> bool:
        with self._attention_lock:
            event = dict(self._pending_attention or {})
        if not event:
            return False

        kind = (event.get("kind") or "message").strip().lower()
        lower = (text or "").lower()

        if kind == "message":
            if self._attention_matches(lower, ("reply", "respond", "answer", "write back", "send reply", "send a reply")):
                return self._prompt_message_reply(event)
            if self._attention_matches(lower, ("hear", "read", "what is it", "tell me", "show it", "open it")):
                preview = read_event_preview(event)
                self.ui.write_log(f"NIGHTFALL Evo: {preview}")
                threading.Thread(target=speak_native, args=(preview,), daemon=True).start()
                with self._attention_lock:
                    self._pending_attention = None
                return True
            if self._attention_matches(lower, ("ignore", "dismiss", "skip", "no", "not now")):
                self.ui.write_log("SYS: Message alert dismissed.")
                with self._attention_lock:
                    self._pending_attention = None
                return True
            return False

        if kind == "call":
            if self._attention_matches(lower, ("pick up", "answer", "accept", "take it", "join")):
                result = handle_call_action(event, "accept")
                self.ui.write_log(f"SYS: {result}")
                threading.Thread(target=speak_native, args=(result,), daemon=True).start()
                with self._attention_lock:
                    self._pending_attention = None
                return True
            if self._attention_matches(lower, ("ignore", "decline", "reject", "cut", "hang up", "end")):
                result = handle_call_action(event, "decline")
                self.ui.write_log(f"SYS: {result}")
                threading.Thread(target=speak_native, args=(result,), daemon=True).start()
                with self._attention_lock:
                    self._pending_attention = None
                return True
            if self._attention_matches(lower, ("x", "nothing", "do nothing", "close")):
                self.ui.write_log("SYS: Call alert dismissed.")
                with self._attention_lock:
                    self._pending_attention = None
                return True
            return False

        return False

    def _on_attention_action(self, event: dict, decision: str):
        if not isinstance(event, dict):
            return
        kind = (event.get("kind") or "message").strip().lower()
        decision = (decision or "").strip().lower()

        if kind == "meeting":
            if decision == "stop":
                self._stop_meeting_mode()
            return

        if kind == "message":
            if decision == "hear":
                preview = read_event_preview(event)
                self.ui.write_log(f"NIGHTFALL Evo: {preview}")
                threading.Thread(target=speak_native, args=(preview,), daemon=True).start()
            elif decision == "reply":
                self._prompt_message_reply(event)
                return
            else:
                self.ui.write_log("SYS: Message alert dismissed.")
            with self._attention_lock:
                self._pending_attention = None
            return

        if kind == "call":
            if decision in {"accept", "answer", "pick_up"}:
                result = handle_call_action(event, "accept")
                self.ui.write_log(f"SYS: {result}")
                threading.Thread(target=speak_native, args=(result,), daemon=True).start()
            elif decision in {"noop", "x", "none"}:
                self.ui.write_log("SYS: Call alert dismissed.")
            else:
                result = handle_call_action(event, "decline")
                self.ui.write_log(f"SYS: {result}")
                threading.Thread(target=speak_native, args=(result,), daemon=True).start()
            with self._attention_lock:
                self._pending_attention = None


    def _execute_tool_sync(self, name: str, args: dict, call_id: str = "call_local") -> str:
        class LocalFC:
            def __init__(self, n, a, cid):
                self.name = n
                self.args = a
                self.id = cid
                self.silent_completion = True

        fc = LocalFC(name, args, call_id)
        if self._loop and self._loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self._execute_tool(fc), self._loop)
                fr = future.result(timeout=60)
                res_obj = getattr(fr, "response", {})
                return res_obj.get("result", str(res_obj))
            except Exception as e:
                return f"Tool execution failed: {e}"
        else:
            try:
                fr = asyncio.run(self._execute_tool(fc))
                res_obj = getattr(fr, "response", {})
                return res_obj.get("result", str(res_obj))
            except Exception as e:
                return f"Tool execution failed: {e}"

    def _jeff_client(self):
        """Jeff routing client from settings (None when Jeff routing is off)."""
        try:
            from core import jeff_router

            return jeff_router.client_from_settings(config_manager.load_settings())
        except Exception as exc:
            print(f"[NIGHTFALL EVO] Jeff routing unavailable: {exc}")
            return None

    def _routed_reply(self, text: str, memory_ctx: str = "") -> str:
        """Answer with whichever provider Jeff routes the request to."""
        from core import jeff_router
        from core import provider_registry

        client = self._jeff_client()
        decision = jeff_router.route(text, client=client)
        try:
            self.ui.write_log(f"SYS: {decision.describe()}")
        except Exception:
            pass
        system = (
            "You are NIGHTFALL Evo, a calm, direct and professional desktop assistant. "
            "Answer concisely and never mention internal routing details."
        )
        request = f"{memory_ctx}\n\nCurrent User Request:\n{text}" if memory_ctx else text
        reply = provider_registry.chat(
            decision.provider,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": request},
            ],
            temperature=0.6,
        )
        return (reply or "").strip()

    def _fallback_reply(self, text: str, memory_ctx: str = ""):
        try:
            self.ui.set_state("THINKING")
            try:
                self.ui.update_task_workspace(
                    status="Thinking",
                    output="NIGHTFALL Evo is drafting a direct reply.",
                    percent=35,
                )
            except Exception:
                pass
            reply = ""
            gemini_first = not self._use_openrouter_first
            request_text = f"{memory_ctx}\n\nCurrent User Request:\n{text}" if memory_ctx else text

            app_settings = config_manager.load_settings()
            configured_provider = app_settings.get("default_ai_provider", "Gemini")
            local_model_target = app_settings.get("local_ai_model", "qwen2.5:3b")
            is_offline_mode = app_settings.get("offline_mode_enabled", False)

            is_cloud_gemini = configured_provider in ("Gemini", "Google Gemini")
            is_cloud_openrouter = configured_provider == "OpenRouter"

            # 0. Jeff model routing: let the router pick the provider/model.
            if app_settings.get("jeff_routing_enabled", False) and not is_offline_mode:
                try:
                    from core import provider_registry

                    if any(p.get("custom") for p in provider_registry.configured_providers()):
                        try:
                            self.ui.update_task_workspace(
                                status="Routing (Jeff)",
                                output="Jeff is picking the best model for this request...",
                                percent=45,
                            )
                        except Exception:
                            pass
                        reply = self._routed_reply(text, memory_ctx=memory_ctx)
                        if reply:
                            print("[NIGHTFALL EVO] 🧭 Jeff routed the request to a custom provider.")
                except Exception as e_jeff:
                    print(f"[NIGHTFALL EVO] Jeff routing failed: {e_jeff}")
                    reply = ""

            # 1. If user explicitly selected Google Gemini, run Gemini FIRST
            if not reply and is_cloud_gemini and not is_offline_mode:
                try:
                    self.ui.update_task_workspace(
                        status="Thinking (Gemini)",
                        output="Processing on Google Gemini...",
                        percent=50,
                    )
                    reply = _gemini_text_reply(request_text)
                    print("[NIGHTFALL EVO] 🌐 Google Gemini answered successfully!")
                except Exception as e_gem:
                    print(f"[NIGHTFALL EVO] ⚠️ Gemini failed: {e_gem}")
                    if _is_gemini_limit_error(e_gem):
                        self._use_openrouter_first = True

            # 2. If user explicitly selected OpenRouter, run OpenRouter FIRST
            elif not reply and is_cloud_openrouter and not is_offline_mode:
                try:
                    self.ui.update_task_workspace(
                        status="Thinking (OpenRouter)",
                        output="Processing on OpenRouter...",
                        percent=50,
                    )
                    reply = openrouter_client.chat(
                        request_text,
                        system=(
                            "You are NIGHTFALL Evo, a concise, helpful desktop assistant. "
                            "Reply naturally and briefly. Do not mention internal implementation details."
                        ),
                    )
                    print("[NIGHTFALL EVO] 🌐 OpenRouter answered successfully!")
                except Exception as e_or:
                    print(f"[NIGHTFALL EVO] ⚠️ OpenRouter failed: {e_or}")

            # 3. If user explicitly configured Local AI, is in Offline Mode, or cloud provider failed: run Local Brain
            if not reply and (configured_provider == "Local" or is_offline_mode or not (is_cloud_gemini or is_cloud_openrouter)) and local_brain.is_available():
                try:
                    self.ui.update_task_workspace(
                        status="Thinking (Local AI)",
                        output=f"Processing on local model ({local_model_target})...",
                        percent=50,
                    )
                    prompt_txt = ""
                    try:
                        if PROMPT_PATH.exists():
                            prompt_txt = PROMPT_PATH.read_text(encoding="utf-8").strip()
                    except Exception:
                        pass
                    if not prompt_txt:
                        prompt_txt = "You are NIGHTFALL Evo, the autonomous desktop operating system."

                    system_prompt = (
                        f"{prompt_txt}\n\n"
                        "CRITICAL OPERATING SYSTEM DIRECTIVE:\n"
                        "- You have FULL DIRECT ACCESS and authority over this Windows PC via your tools.\n"
                        "- NEVER state that you are a text-based AI, that you cannot perform automations, or that you lack real-time access.\n"
                        "- Whenever the user requests an action (opening an app, changing volume, setting a reminder, running a protocol, organizing files, searching, etc.), ALWAYS call the corresponding tool.\n"
                        "- Keep responses concise, direct, and conversational."
                    )

                    messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": request_text}
                    ]

                    res = local_brain.chat_complete(
                        messages,
                        model=local_model_target,
                        tools=TOOL_DECLARATIONS,
                        focus_core=True
                    )
                    msg = res.get("choices", [{}])[0].get("message", {})
                    tool_calls = msg.get("tool_calls")
                    if tool_calls:
                        for tc in tool_calls:
                            call_id = tc.get("id", "call_local")
                            fn_info = tc.get("function", {})
                            fn_name = fn_info.get("name")
                            fn_args_raw = fn_info.get("arguments", {})
                            if isinstance(fn_args_raw, str):
                                try:
                                    fn_args = json.loads(fn_args_raw)
                                except Exception:
                                    fn_args = {}
                            else:
                                fn_args = fn_args_raw or {}

                            print(f"[NIGHTFALL EVO] 🔒 Local Brain executing tool: {fn_name}({fn_args})")
                            self.ui.update_task_workspace(
                                status=f"Executing {fn_name}",
                                output=f"Running action: {fn_name} on local machine...",
                                percent=75,
                            )
                            tool_result = self._execute_tool_sync(fn_name, fn_args, call_id)

                            messages.append(msg)
                            messages.append({
                                "role": "tool",
                                "tool_call_id": call_id,
                                "name": fn_name,
                                "content": str(tool_result)
                            })
                            try:
                                followup_res = local_brain.chat_complete(
                                    messages,
                                    model=local_model_target,
                                    temperature=0.3
                                )
                                followup_reply = followup_res.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                                if followup_reply:
                                    reply = followup_reply
                            except Exception as e_fu:
                                print(f"[NIGHTFALL EVO] ⚠️ Local Brain follow-up failed: {e_fu}")
                                reply = str(tool_result) if tool_result else f"{fn_name.replace('_', ' ').capitalize()} completed."
                    else:
                        reply = msg.get("content", "").strip()
                    print(f"[NIGHTFALL EVO] 🔒 Local Brain ({local_model_target}) answered successfully!")
                except Exception as e_loc:
                    print(f"[NIGHTFALL EVO] ⚠️ Local Brain failed: {e_loc}")

            # 4. Fallback cascading: if primary cloud choice failed, try secondary cloud choice
            if not reply and not is_offline_mode:
                if is_cloud_gemini and self._use_openrouter_first:
                    try:
                        reply = openrouter_client.chat(request_text)
                    except Exception:
                        pass
                elif is_cloud_openrouter:
                    try:
                        reply = _gemini_text_reply(request_text)
                    except Exception:
                        pass

            # 4. Ultimate offline safety net: Local Brain fallback
            if not reply and local_brain.is_available():
                try:
                    res = local_brain.chat_complete([
                        {"role": "system", "content": "You are NIGHTFALL Evo, the autonomous desktop operating system. You control this PC. Never claim you cannot do automations."},
                        {"role": "user", "content": request_text}
                    ], model=local_model_target, tools=TOOL_DECLARATIONS, focus_core=True)
                    msg_net = res.get("choices", [{}])[0].get("message", {})
                    tc_net = msg_net.get("tool_calls")
                    if tc_net:
                        call_id = tc_net[0].get("id", "call_local")
                        fn_name = tc_net[0].get("function", {}).get("name")
                        fn_args_raw = tc_net[0].get("function", {}).get("arguments", {})
                        if isinstance(fn_args_raw, str):
                            try:
                                fn_args = json.loads(fn_args_raw)
                            except Exception:
                                fn_args = {}
                        else:
                            fn_args = fn_args_raw or {}
                        reply = self._execute_tool_sync(fn_name, fn_args, call_id)
                    else:
                        reply = msg_net.get("content", "").strip()
                    print(f"[NIGHTFALL EVO] 🔒 Local Brain offline safety net answered ({local_model_target})!")
                except Exception as e_net:
                    print(f"[NIGHTFALL EVO] ⚠️ Offline Local Brain fallback failed: {e_net}")
            reply = (reply or "").strip()
            if not reply:
                reply = "I’m ready, sir."
            self.ui.write_log(f"NIGHTFALL Evo: {reply}")
            if not getattr(self.ui, "muted", False):
                self.speak(reply, proactive=True)
            try:
                self.ui.finish_task_workspace(reply, "Reply delivered.", 100)
            except Exception:
                pass
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
        except Exception as e:
            msg = f"Fallback reply failed: {e}"
            print(f"[NIGHTFALL EVO] ⚠️ {msg}")
            self.ui.write_log(f"ERR: {msg}")
            try:
                self.ui.finish_task_workspace(msg, "Reply failed.", 100)
            except Exception:
                pass
            if not self.ui.muted:
                self.ui.set_state("LISTENING")

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def trigger_barge_in(self):
        """Immediately interrupts AI speech playback and switches state to LISTENING."""
        with self._speaking_lock:
            if not self._is_speaking:
                return
            self._is_speaking = False

        try:
            from actions.attention_monitor import stop_native_speech
            stop_native_speech()
        except Exception:
            pass

        try:
            if self.audio_in_queue:
                while not self.audio_in_queue.empty():
                    try:
                        self.audio_in_queue.get_nowait()
                    except Exception:
                        break
        except Exception:
            pass

        try:
            from sound_manager import SoundManager
            SoundManager.instance().play_listening_start()
        except Exception:
            pass

        if hasattr(self, "ui") and self.ui and not self.ui.muted:
            self.ui.set_state("LISTENING")

    def speak(self, text: str, proactive: bool = False):
        text = (text or "").strip()
        if not text:
            return

        if self.session and self._loop:
            # Route text through Gemini Live API for the unified native Zephyr voice
            import asyncio
            async def _send():
                try:
                    prompt = f"System Alert / Context: {text}\n\nPlease relay this information to me naturally now."
                    await self.session.send(input=prompt, end_of_turn=True)
                except Exception as e:
                    print(f"[NIGHTFALL EVO] Unified Speak (Zephyr) err: {e}")
                    def _fallback():
                        try:
                            self.set_speaking(True)
                            from actions.attention_monitor import _speak_edge_native
                            _speak_edge_native(text)
                        except Exception as exc:
                            print(f"[NIGHTFALL Speak] Fallback TTS failed: {exc}")
                        finally:
                            self.set_speaking(False)
                    threading.Thread(target=_fallback, daemon=True).start()
            asyncio.run_coroutine_threadsafe(_send(), self._loop)
        else:
            # Fallback when Gemini Live is disconnected or in offline mode
            def _speak_thread():
                try:
                    self.set_speaking(True)
                    from actions.attention_monitor import _speak_edge_native
                    _speak_edge_native(text)
                except Exception as exc:
                    print(f"[NIGHTFALL Speak] Unified TTS failed: {exc}")
                finally:
                    self.set_speaking(False)
            threading.Thread(target=_speak_thread, daemon=True).start()

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _forge_skill(self, goal: str, skill_name: str | None = None) -> str:
        from core.skill_forge import SkillForge

        try:
            result = SkillForge.forge_skill(goal=goal, skill_name=skill_name)
            message = str(result.get("message") or result.get("error") or "Skill creation failed.")
        except Exception as exc:
            result = {"success": False}
            message = f"Skill creation failed: {exc}"
        if result.get("success"):
            feature_name = str(result.get("name") or skill_name or "new feature")
            description = str(result.get("description") or "")
            announcement = f"I created and activated the feature '{feature_name}'."
            if description:
                announcement += f" {description}"
            announcement += f" I created it for this goal: {goal}."

            # Immediately execute the newly forged skill to satisfy the user's goal
            execution_output = ""
            try:
                from core.dynamic_registry import DynamicToolRegistry
                if DynamicToolRegistry.has_tool(feature_name):
                    run_res = DynamicToolRegistry.execute_sync(feature_name, {"query": goal, "goal": goal, "input": goal})
                    if run_res:
                        if isinstance(run_res, dict):
                            execution_output = str(run_res.get("summary") or run_res.get("output") or run_res.get("text") or run_res).strip()
                        else:
                            execution_output = str(run_res).strip()
            except Exception as e_run:
                execution_output = f"(Execution note: {e_run})"

            try:
                self.ui.show_hud_deliverable(
                    title=f"Evolution: {feature_name}",
                    summary=execution_output or announcement,
                    kind="result",
                )
            except Exception:
                pass
            try:
                self.ui.write_log(f"NIGHTFALL Evo: {announcement}")
                if execution_output:
                    self.ui.write_log(f"Result:\n{execution_output}")
            except Exception:
                pass
            self.speak(announcement)
        else:
            try:
                self.ui.write_log(f"ERR: {message}")
            except Exception:
                pass
            self.speak(message)
        return message

    def _run_confirmed_skill(self, skill_name: str, arguments: dict) -> str:
        from core.dynamic_registry import DynamicToolRegistry

        result = DynamicToolRegistry.execute_sync(skill_name, arguments or {})
        message = str(result)
        try:
            self.ui.show_content(f"Skill: {skill_name}", message)
            self.ui.write_log(f"SYS: Skill '{skill_name}' completed.")
        except Exception:
            pass
        return message

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        loc_ctx = ""
        try:
            from core.device_location import get_device_city
            dev_city = get_device_city(default="")
            if dev_city and dev_city != "Local Area":
                loc_ctx = (
                    f"[DEVICE PHYSICAL LOCATION]\n"
                    f"Current device location: {dev_city}\n"
                    f"Use this location for local weather, time zone, and neighborhood context.\n\n"
                )
        except Exception:
            pass

        parts = [time_ctx]
        if loc_ctx:
            parts.append(loc_ctx)
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)
        parts.append(
            "Wake-word mode: if the microphone is muted, still listen for the words 'NIGHTFALL Evo', 'hey', 'hi', and 'hello'. "
            "When you hear one of these activation cues, keep the session friendly and concise, "
            "and wait for the user's next command. "
            "IMPORTANT: Do NOT speak an unprompted generic greeting (like 'Thank you, how can I help you?') upon connecting. "
            "Remain completely silent until the user speaks to you or asks a question."
        )

        tool_declarations = list(TOOL_DECLARATIONS)
        declared_names = {tool.get("name") for tool in tool_declarations}
        try:
            from core.dynamic_registry import DynamicToolRegistry
            for declaration in DynamicToolRegistry.get_tool_declarations():
                if declaration.get("name") not in declared_names:
                    tool_declarations.append(declaration)
                    declared_names.add(declaration.get("name"))
        except Exception as exc:
            print(f"[SkillRegistry] Dynamic tools unavailable: {exc}")

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": tool_declarations}],
            session_resumption=types.SessionResumptionConfig(handle=getattr(self, '_resume_handle', None)),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Zephyr"
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[NIGHTFALL EVO] 🔧 {name}  {args}")
        self.speak(f"Working on {name.replace('_', ' ')}...")
        self.ui.set_state("THINKING")

        # Trigger NIGHTFALL Right Wing: Live Operations & Sources Telemetry
        tool_title = name.replace("_", " ").title()
        brief_query = (
            args.get("query")
            or args.get("description")
            or args.get("topic")
            or args.get("prompt")
            or args.get("title")
            or args.get("action")
            or ""
        )
        sources = []
        if "url" in args:
            sources.append(args["url"])
        if "file_path" in args:
            sources.append(Path(args["file_path"]).name)
        elif "path" in args:
            sources.append(Path(args["path"]).name)
        elif "query" in args and name in ("web_search", "flight_finder", "youtube_video"):
            sources.append(f"Query: {str(args['query'])[:28]}")

        try:
            self.ui.show_hud_operation(
                title=f"{tool_title.upper()} ACTIVE",
                step=f"Processing: {brief_query[:75]}" if brief_query else f"Executing {tool_title}...",
                sources=sources,
                tool=name
            )
        except Exception:
            pass

        try:
            self.ui.update_task_workspace(
                title=f"Running {name}",
                status=f"Executing {name}",
                output="Waiting for the tool to finish.",
                percent=45,
            )
        except Exception:
            pass
        if name == "undo":
            action = args.get("action", "undo")
            if action == "list":
                items = undo_stack.history()
                result = ("Things I can undo, most recent first:\n" +
                          "\n".join(f"  • {item}" for item in items)
                         ) if items else "I have not changed anything I can undo yet."
            else:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, undo_stack.undo_last)
            self.speak("Undone.")
            self.ui.set_state("LISTENING")
            return types.FunctionResponse(name=name, id=fc.id, response={"result": result})

        elif name == "recall_memory":
            query = args.get("query", "")
            result = search_memory(query, limit=8)
            self.ui.set_state("LISTENING")
            return types.FunctionResponse(name=name, id=fc.id, response={"result": result})

        elif name == "execute_protocol":
            protocol_name = args.get("protocol", "")
            res = protocols.execute(protocol_name)
            msg = res.get("result", res.get("error", "Protocol completed."))
            self.speak(msg)
            self.ui.set_state("LISTENING")
            return types.FunctionResponse(name=name, id=fc.id, response={"result": res})

        elif name == "get_sensorium_telemetry":
            snapshot = sensorium.get_snapshot()
            self.ui.set_state("LISTENING")
            return types.FunctionResponse(name=name, id=fc.id, response={"result": snapshot})

        elif name == "manage_local_model":
            action = args.get("action", "status")
            model_target = args.get("model_name", "")
            if action == "status":
                avail = local_brain.is_available()
                models = local_brain.list_installed_models()
                res = {
                    "server_online": avail,
                    "active_model": local_brain.default_model,
                    "local_mode_enabled": local_brain.enabled,
                    "installed_models": models,
                }
                msg = f"Local brain is {'online' if avail else 'offline'}. Installed models: {', '.join(models) if models else 'none'}."
            elif action == "pull" and model_target:
                local_brain.pull_model_async(model_target)
                msg = f"Initiated background download of local model: {model_target}."
                res = {"status": "downloading", "model": model_target}
            elif action == "toggle_offline":
                local_brain.enabled = not local_brain.enabled
                msg = f"Offline local inference mode {'engaged' if local_brain.enabled else 'disengaged'}."
                res = {"local_mode_enabled": local_brain.enabled}
            elif action == "set_active" and model_target:
                local_brain.default_model = model_target
                msg = f"Active local model set to {model_target}."
                res = {"active_model": local_brain.default_model}
            else:
                msg = "Invalid action."
                res = {"error": msg}

            self.speak(msg)
            self.ui.set_state("LISTENING")
            return types.FunctionResponse(name=name, id=fc.id, response={"result": res})

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
                try:
                    self.ui.finish_task_workspace("Memory saved.", "Memory updated.", 100)
                except Exception:
                    pass
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "computer_settings":
                from actions.computer_settings import computer_settings as cs_run
                r = await loop.run_in_executor(None, lambda: cs_run(parameters=args, player=self.ui))
                result = r or "Settings updated."

            elif name == "dev_agent":
                from actions.dev_agent import dev_agent as da_run
                r = await loop.run_in_executor(None, lambda: da_run(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."
                
            elif name == "check_instagram_messages":
                self.ui.write_log("SYS: Checking Instagram messages...")
                if hasattr(self.ui, "show_hud_operation"):
                    self.ui.show_hud_operation("Instagram Inbox", "Fetching recent direct messages...", sources=["instagram.com"])
                from actions.instagram_mcp import get_recent_messages, InstagramService
                amount = int(args.get("amount", 5)) if args else 5
                result = await loop.run_in_executor(None, get_recent_messages, amount)
                if hasattr(self.ui, "show_hud_deliverable"):
                    try:
                        inbox_data = InstagramService.instance().get_inbox(amount=amount)
                        bullets = [f"@{item['sender']}: {item['last_message']}" for item in inbox_data[:4]]
                        self.ui.show_hud_deliverable("Instagram Inbox", summary=f"Retrieved {len(inbox_data)} recent conversations", bullets=bullets, kind="result")
                    except Exception:
                        pass

            elif name == "instagram_send_dm":
                recipient = args.get("recipient", "")
                message = args.get("message", "")
                open_browser = args.get("open_in_browser", True)
                self.ui.write_log(f"SYS: Sending Instagram DM to @{recipient}...")
                if hasattr(self.ui, "show_hud_operation"):
                    self.ui.show_hud_operation("Instagram DM", f"Messaging @{recipient}...", sources=["instagram.com"])
                from actions.instagram_mcp import InstagramService
                def _do_send():
                    return InstagramService.instance().send_dm(recipient, message, open_in_browser=open_browser)
                res = await loop.run_in_executor(None, _do_send)
                if hasattr(self.ui, "show_hud_deliverable"):
                    self.ui.show_hud_deliverable("Instagram DM Sent", summary=f"Direct message sent to @{res.get('recipient')}", bullets=[f"Message: {message[:60]}...", "Opened in browser for verification"], kind="result")
                result = f"Direct message sent to @{recipient}. Chat opened in browser: {res.get('browser_url')}"

            elif name == "instagram_post_photo":
                image_path = args.get("image_path", "")
                caption = args.get("caption", "")
                open_browser = args.get("open_in_browser", True)
                self.ui.write_log(f"SYS: Publishing photo to Instagram...")
                if hasattr(self.ui, "show_hud_operation"):
                    self.ui.show_hud_operation("Instagram Post", f"Uploading {Path(image_path).name}...", sources=["instagram.com"])
                from actions.instagram_mcp import InstagramService
                def _do_post():
                    return InstagramService.instance().post_photo(image_path, caption=caption, open_in_browser=open_browser)
                res = await loop.run_in_executor(None, _do_post)
                if hasattr(self.ui, "show_hud_deliverable"):
                    self.ui.show_hud_deliverable("Instagram Post Published", summary="Photo published live to Instagram!", bullets=[f"URL: {res.get('post_url')}", f"Caption: {caption[:60]}..."], kind="deliverable")
                result = f"Successfully published photo to Instagram! Post URL: {res.get('post_url')}"

            elif name == "instagram_post_reel":
                video_path = args.get("video_path", "")
                caption = args.get("caption", "")
                thumb = args.get("thumbnail_path")
                open_browser = args.get("open_in_browser", True)
                self.ui.write_log(f"SYS: Publishing Reel to Instagram...")
                if hasattr(self.ui, "show_hud_operation"):
                    self.ui.show_hud_operation("Instagram Reel", f"Uploading {Path(video_path).name}...", sources=["instagram.com"])
                from actions.instagram_mcp import InstagramService
                def _do_reel():
                    return InstagramService.instance().post_reel(video_path, caption=caption, thumbnail_path=thumb, open_in_browser=open_browser)
                res = await loop.run_in_executor(None, _do_reel)
                if hasattr(self.ui, "show_hud_deliverable"):
                    self.ui.show_hud_deliverable("Instagram Reel Published", summary="Reel published live to Instagram!", bullets=[f"URL: {res.get('reel_url')}", f"Caption: {caption[:60]}..."], kind="deliverable")
                result = f"Successfully published Reel to Instagram! Reel URL: {res.get('reel_url')}"

            elif name == "instagram_get_user_info":
                username = args.get("username", "")
                self.ui.write_log(f"SYS: Looking up Instagram user @{username}...")
                if hasattr(self.ui, "show_hud_operation"):
                    self.ui.show_hud_operation("Instagram Intelligence", f"Looking up @{username}...", sources=["instagram.com"])
                from actions.instagram_mcp import InstagramService
                def _do_lookup():
                    return InstagramService.instance().get_user_profile(username)
                res = await loop.run_in_executor(None, _do_lookup)
                if hasattr(self.ui, "show_hud_deliverable"):
                    self.ui.show_hud_deliverable(f"@{res.get('username')} Profile", summary=res.get("bio", "No bio"), bullets=[f"Followers: {res.get('followers', 0):,}", f"Following: {res.get('following', 0):,}", f"Posts: {res.get('posts_count', 0):,}", f"Verified: {'Yes' if res.get('is_verified') else 'No'}"], kind="result")
                result = json.dumps(res, indent=2)

            elif name == "instagram_reply":
                action = args.get("action")
                reply_text = args.get("reply_text")
                if getattr(self, "_ig_pending_thread", None):
                    thread_id = self._ig_pending_thread.get("thread_id")
                    username = self._ig_pending_thread.get("username")
                    from actions.instagram_mcp import add_auto_thread, send_direct_reply
                    if action == "take_over":
                        self.ui.write_log("SYS: Taking over Instagram thread via tool.")
                        add_auto_thread(thread_id)
                        message_text = self._ig_pending_thread.get('message')
                        def _generate_and_send():
                            try:
                                reply = _ig_gemini_reply(username, message_text)
                                send_direct_reply(thread_id, reply)
                            except Exception as e:
                                print(f"Error taking over thread: {e}")
                        threading.Thread(target=_generate_and_send, daemon=True).start()
                        result = f"Successfully took over the chat with @{username}. The backend will now automatically reply to them."
                    else:
                        self.ui.write_log(f"SYS: Sending manual reply to @{username}.")
                        send_direct_reply(thread_id, reply_text)
                        result = f"Successfully sent manual reply to @{username} and opened thread in browser."
                        
                    self._ig_reply_mode = False
                    self._ig_pending_thread = None
                else:
                    recipient = args.get("recipient") or args.get("username")
                    from actions.instagram_mcp import InstagramService, add_auto_thread
                    thread_id = None
                    if not recipient:
                        try:
                            inbox = InstagramService.instance().get_inbox(amount=1)
                            if inbox:
                                recipient = inbox[0].get("sender")
                                thread_id = inbox[0].get("thread_id")
                        except Exception:
                            pass

                    if recipient:
                        if action == "take_over":
                            add_auto_thread(thread_id or recipient)
                            result = f"Successfully took over the chat with @{recipient}. NIGHTFALL Evo will now automatically reply."
                        else:
                            res = InstagramService.instance().send_dm(recipient, reply_text, open_in_browser=True)
                            result = f"Sent reply to @{recipient}: '{reply_text}'. Thread opened in browser."
                        self._ig_reply_mode = False
                    else:
                        result = "Could not find a recent conversation to reply to. Please specify who you want to message."

            elif name == "system_manager":
                from actions.system_manager import run as sm_run
                r = await loop.run_in_executor(None, lambda: sm_run(parameters=args, player=self.ui))
                result = r or "System status retrieved."

            elif name == "background_monitor":
                from actions.background_monitor import run as bm_run
                r = await loop.run_in_executor(None, lambda: bm_run(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "clipboard_processor":
                from actions.clipboard_processor import process_clipboard
                r = await loop.run_in_executor(None, lambda: process_clipboard(parameters=args, player=self.ui))
                result = r or "Clipboard read."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "presentation_builder":
                if not args.get("slides") and not args.get("outline"):
                    from actions.office_generator import generate_presentation_from_prompt
                    topic = args.get("topic") or args.get("title") or "Presentation"
                    r = await loop.run_in_executor(
                        None,
                        lambda: generate_presentation_from_prompt(topic, player=self.ui, speak=self.speak)
                    )
                else:
                    r = await loop.run_in_executor(
                        None,
                        lambda: create_presentation(parameters=args, player=self.ui)
                    )
                result = r or "Presentation created."

            elif name == "spreadsheet_builder":
                if not args.get("worksheets") and not args.get("sheets"):
                    from actions.office_generator import generate_spreadsheet_from_prompt
                    topic = args.get("topic") or args.get("title") or "Spreadsheet"
                    r = await loop.run_in_executor(
                        None,
                        lambda: generate_spreadsheet_from_prompt(topic, player=self.ui, speak=self.speak)
                    )
                else:
                    r = await loop.run_in_executor(
                        None,
                        lambda: create_spreadsheet(parameters=args, player=self.ui)
                    )
                result = r or "Spreadsheet created."


            elif name == "word_document":
                if not args.get("file_path") and self.ui.current_file:
                    current_file = Path(self.ui.current_file)
                    if current_file.suffix.lower() == ".docx":
                        args["file_path"] = self.ui.current_file
                from actions.docx_tools import document_needs_generated_content
                topic = (
                    getattr(self, "_last_user_utterance", "")
                    or args.get("topic")
                    or args.get("title")
                    or args.get("subject")
                    or ""
                )
                if document_needs_generated_content(args):
                    # The model described the document instead of writing it (or sent
                    # no content at all): generate the full document ourselves.
                    from actions.document_generator import generate_document_from_prompt
                    args["topic"] = topic
                    self.ui.write_log("THINKING: Writing the full document content...")
                    r = await loop.run_in_executor(
                        None,
                        lambda: generate_document_from_prompt(
                            topic,
                            player=self.ui,
                            speak=self.speak,
                            title=args.get("title"),
                            subtitle=args.get("subtitle"),
                            author=args.get("author"),
                            subject=args.get("subject"),
                            output_path=args.get("output_path"),
                        )
                    )
                else:
                    r = await loop.run_in_executor(
                        None,
                        lambda: word_document(parameters=args, player=self.ui, speak=self.speak)
                    )
                result = r or "Word document handled."

            elif name == "pdf_document":
                r = await loop.run_in_executor(
                    None,
                    lambda: create_pdf(parameters=args, player=self.ui)
                )
                result = r or "PDF created."

            elif name == "screen_process":
                screen_question = (
                    str(args.get("text") or args.get("user_text") or "").strip()
                    or getattr(self, "_last_user_utterance", "")
                    or "What do you see on my screen? Answer briefly."
                )
                # The voice session asked for the screen: capture it and hand
                # the frame straight back to that same session so the answer
                # comes in the app's single voice (Zephyr).  The standalone
                # vision module is only used when no live session exists.
                if self._live_session_available() and not getattr(self.ui, "muted", False):
                    if self._screen_frame_is_fresh():
                        # The model asked for the screen again in the same turn:
                        # do not capture and describe the same screenshot twice.
                        result = (
                            "You already received a screenshot of this screen a moment ago "
                            "(it is attached to this conversation). Answer the user's question "
                            "from that image instead of capturing the screen again."
                        )
                    else:
                        frame = await asyncio.get_event_loop().run_in_executor(
                            None, self._capture_screen_bytes
                        )
                        if frame:
                            self._pending_screen_frame = (frame, screen_question)
                            self._last_screen_capture_at = time.time()
                            result = (
                                "Screenshot attached to your view: answer the user's question "
                                "about the screen from it now, in your normal voice."
                            )
                        else:
                            result = "Screen capture failed."
                else:
                    if hasattr(self, "set_scanning"):
                        self.ui.set_scanning(True, "SCANNING SCREEN")
                    threading.Thread(
                        target=screen_process,
                        kwargs={
                            "parameters": args,
                            "response": None,
                            "player": self.ui,
                            "session_memory": None,
                        },
                        daemon=True,
                    ).start()
                    result = "Vision module activated. Stay completely silent — vision module will speak directly."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "smart_home_control":
                command_text = str(args.get("command") or "").strip()
                r = await loop.run_in_executor(None, lambda: self._smart_home.execute_command(command_text))
                result = str((r or {}).get("detail") or "Smart-home command completed.")

            elif name in ("smart_organizer", "desktop_organizer"):
                from actions.desktop_organizer_mcp import smart_organizer
                r = await loop.run_in_executor(None, lambda: smart_organizer(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "agent_task":
                from agent.task_queue import get_queue, TaskPriority
                priority_map = {"low": TaskPriority.LOW, "normal": TaskPriority.NORMAL, "high": TaskPriority.HIGH}
                priority = priority_map.get(args.get("priority", "normal").lower(), TaskPriority.NORMAL)
                task_id  = get_queue().submit(
                    goal=args.get("goal", ""),
                    priority=priority,
                    speak=self.speak,
                    player=self.ui
                )
                result   = f"Task started (ID: {task_id})."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "circuit_assembler":
                from actions.circuit_assembler import circuit_assembler
                r = await loop.run_in_executor(
                    None,
                    lambda: circuit_assembler(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r.get("summary", "Circuit schematic ready.") if isinstance(r, dict) else (r or "Circuit schematic ready.")
            elif name == "geospatial_globe":
                from core.globe_window import GlobeWindow
                globe = GlobeWindow.get_instance(parent=self.ui._win)
                action = (args.get("action") or "open").lower().strip()
                location = args.get("location") or "current"
                if action == "open":
                    globe.open_globe(location if args.get("location") else None)
                    result = "Opened the interactive NIGHTFALL map."
                elif action == "route":
                    r = await loop.run_in_executor(None, lambda: globe.show_route(args.get("origin", ""), args.get("destination", "")))
                    result = f"Flight route: {r.get('origin')} to {r.get('destination')}, {r.get('distance_km')} km, about {r.get('flight_time')}."
                elif action == "drive":
                    r = await loop.run_in_executor(None, lambda: globe.show_driving_route(args.get("origin", ""), args.get("destination", "")))
                    result = f"Driving route: {r.get('origin')} to {r.get('destination')}, {r.get('distance_km')} km, {r.get('duration_str')}."
                elif action in ("location", "fly_to"):
                    r = await loop.run_in_executor(None, lambda: globe.show_location(location) if action == "location" else globe.fly_to(location))
                    result = f"Map focused on {r.get('location', location)}."
                elif action == "weather":
                    r = await loop.run_in_executor(None, lambda: globe.show_weather(location))
                    result = f"Weather for {r.get('location')}: {r.get('weather')}."
                elif action == "flights":
                    r = await loop.run_in_executor(None, lambda: globe.show_live_flights(args.get("location")))
                    result = f"Showing {len(r)} live aircraft on the map."
                elif action == "iss":
                    r = await loop.run_in_executor(None, globe.show_iss_tracker)
                    result = f"ISS location: {r.get('lat')}, {r.get('lon')}; altitude {r.get('altitude_km')} km."
                elif action == "earthquakes":
                    r = await loop.run_in_executor(None, lambda: globe.show_earthquakes(float(args.get("min_magnitude", 2.5))))
                    result = f"Showing {len(r)} recent earthquakes on the map."
                elif action == "nearby":
                    r = await loop.run_in_executor(None, lambda: globe.show_nearby(args.get("query") or "hospitals", args.get("location")))
                    result = f"Found {len(r)} nearby places."
                elif action == "radar":
                    enabled = bool(args.get("enable", True))
                    await loop.run_in_executor(None, lambda: globe.toggle_weather_radar(enabled))
                    result = f"Weather radar {'enabled' if enabled else 'disabled'}."
                else:
                    result = f"Unsupported map action: {action}."
            elif name == "call_screening":
                action = (args.get("action") or "start").lower().strip()
                from actions.call_assistant import hang_up_active_call, start_call_proxy, take_over_active_call
                if action == "take_over":
                    take_over_active_call()
                    result = "Taking over the screened call."
                elif action == "hang_up":
                    hang_up_active_call()
                    result = "Ended the screened call."
                elif action == "start":
                    event = {"title": args.get("caller") or "Incoming call", "app": args.get("app") or "Phone / Call"}
                    from core.confirm import request
                    result = request(
                        "start-call-screening",
                        "Answer this call as NIGHTFALL Evo",
                        f"NIGHTFALL will answer {event['title']} in {event['app']}, listen to the caller, and prepare a transcript and summary.",
                        lambda: (start_call_proxy(event, ui=self.ui, speak_fn=self.speak) and "Call screening started.")
                    )
                    self.ui.set_state("LISTENING")
                    return types.FunctionResponse(id=fc.id, name=name, response={"result": result})
                else:
                    result = "Choose start, take_over, or hang_up."
            elif name == "skill_forge":
                action = (args.get("action") or "forge").lower().strip()
                from core.dynamic_registry import DynamicToolRegistry
                if action == "list":
                    skills = DynamicToolRegistry.list_skills()
                    result = "Installed skills: " + (", ".join(skill["name"] for skill in skills) or "none")
                elif action == "forge":
                    goal = (args.get("goal") or "").strip()
                    if not goal:
                        result = "Describe the capability you want NIGHTFALL to learn."
                    else:
                        threading.Thread(
                            target=self._forge_skill,
                            args=(goal, args.get("skill_name")),
                            daemon=True,
                            name="skill-forge",
                        ).start()
                        result = f"Started creating the requested skill: {goal}"
                        self.ui.set_state("LISTENING")
                        return types.FunctionResponse(id=fc.id, name=name, response={"result": result})
                else:
                    result = "Choose forge or list."
            elif name == "dynamic_skill":
                action = (args.get("action") or "list").lower().strip()
                from core.dynamic_registry import DynamicToolRegistry
                if action == "list":
                    skills = DynamicToolRegistry.list_skills()
                    result = "Installed skills: " + (", ".join(skill["name"] for skill in skills) or "none")
                elif action == "run":
                    skill_name = (args.get("skill_name") or "").strip()
                    if not DynamicToolRegistry.has_tool(skill_name):
                        result = f"No active skill named '{skill_name}'."
                    else:
                        run_res = await loop.run_in_executor(
                            None,
                            lambda: DynamicToolRegistry.execute_sync(skill_name, args.get("arguments") or args or {})
                        )
                        if isinstance(run_res, dict):
                            out_text = str(run_res.get("summary") or run_res.get("output") or run_res.get("text") or run_res).strip()
                        else:
                            out_text = str(run_res).strip()
                        self.ui.write_log(f"NIGHTFALL Evo [{skill_name}]:\n{out_text}")
                        result = out_text
                else:
                    result = "Choose list or run."
            elif name and name not in {tool.get("name") for tool in TOOL_DECLARATIONS}:
                from core.dynamic_registry import DynamicToolRegistry
                if DynamicToolRegistry.has_tool(name):
                    run_res = await loop.run_in_executor(
                        None,
                        lambda: DynamicToolRegistry.execute_sync(name, args or {})
                    )
                    if isinstance(run_res, dict):
                        out_text = str(run_res.get("summary") or run_res.get("output") or run_res.get("text") or run_res).strip()
                    else:
                        out_text = str(run_res).strip()
                    self.ui.write_log(f"NIGHTFALL Evo [{name}]:\n{out_text}")
                    result = out_text
                else:
                    result = f"Unknown tool: {name}"
            elif name == "connect_list_devices":
                r = await loop.run_in_executor(None, lambda: connect_list_devices(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "connect_get_device":
                r = await loop.run_in_executor(None, lambda: connect_get_device(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "connect_get_capabilities":
                r = await loop.run_in_executor(None, lambda: connect_get_capabilities(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "connect_execute":
                r = await loop.run_in_executor(None, lambda: connect_execute(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "connect_pair_device":
                r = await loop.run_in_executor(None, lambda: connect_pair_device(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "connect_disconnect_device":
                r = await loop.run_in_executor(None, lambda: connect_disconnect_device(parameters=args, player=self.ui))
                result = r or "Done."
            elif name in ("spotify_controller", "spotify", "music"):
                from actions.spotify_controller import spotify_controller
                r = await loop.run_in_executor(None, lambda: spotify_controller(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."
            elif name in ("calendar_scheduler", "calendar", "schedule"):
                from actions.calendar_scheduler import calendar_scheduler
                r = await loop.run_in_executor(None, lambda: calendar_scheduler(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."
            elif name in ("google_workspace", "gmail", "google_calendar", "google_drive", "workspace") or name.startswith("workspace_"):
                from actions.google_workspace_mcp import google_workspace
                p = dict(args or {})
                if name.startswith("workspace_"):
                    parts = name.split("_", 2)
                    if len(parts) > 1:
                        p.setdefault("service", parts[1])
                    if len(parts) > 2:
                        p.setdefault("action", parts[2])
                r = await loop.run_in_executor(None, lambda: google_workspace(parameters=p, player=self.ui, speak=self.speak))
                result = r or "Google Workspace task completed."
            elif name in ("system_diagnostics", "diagnostics", "os_hardware", "hardware_control", "ram_hogs", "kill_process", "brightness_control"):
                from actions.system_diagnostics_mcp import system_diagnostics
                p = dict(args or {})
                if name == "ram_hogs":
                    p.setdefault("action", "ram_hogs")
                elif name == "kill_process":
                    p.setdefault("action", "kill")
                elif name == "brightness_control":
                    p.setdefault("action", "brightness")
                r = await loop.run_in_executor(None, lambda: system_diagnostics(parameters=p, player=self.ui, speak=self.speak))
                result = r or "Diagnostics completed."
            elif name in ("daily_briefing", "briefing"):
                from actions.daily_briefing import daily_briefing
                r = await loop.run_in_executor(None, lambda: daily_briefing(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Delivered daily briefing."
            elif name == "unlock_device":
                from actions.unlock_device import unlock_device
                r = await loop.run_in_executor(None, lambda: unlock_device(parameters=args, player=self.ui))
                result = r or "Done."
            elif name in ("auto_heal", "self_patch", "rollback"):
                from actions.auto_heal_engine import auto_heal
                p = dict(args or {})
                if name == "rollback":
                    p.setdefault("action", "rollback")
                r = await loop.run_in_executor(None, lambda: auto_heal(parameters=p, player=self.ui, speak=self.speak))
                result = r or "Auto-heal completed."
            elif name in ("calorie_counter", "nutrition_scan", "food_analysis"):
                from actions.calorie_counter import calorie_counter
                r = await loop.run_in_executor(None, lambda: calorie_counter(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Nutrition analysis complete."
            elif name in ("pushup_counter", "workout_tracker", "rep_counter"):
                from actions.pushup_counter import pushup_counter
                r = await loop.run_in_executor(None, lambda: pushup_counter(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Workout session complete."
            elif name in ("upload_video", "video_publisher", "publish_video"):
                from actions.upload_video import upload_video
                r = await loop.run_in_executor(None, lambda: upload_video(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Video publishing ready."
            elif name == "shutdown_NIGHTFALL":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("Goodbye, sir.")

                def _shutdown():
                    import time, sys, os
                    time.sleep(1)
                    os._exit(0)

                threading.Thread(target=_shutdown, daemon=True).start()
            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            tb_str = traceback.format_exc()
            traceback.print_exc()
            try:
                from actions.auto_heal_engine import AutoHealEngine
                AutoHealEngine.record_last_error(tb_str)
            except Exception:
                pass
            self.speak_error(name, e)

        try:
            if not getattr(fc, "silent_completion", False):
                self.speak(f"{name.replace('_', ' ')} completed.")
            self.ui.finish_task_workspace(result, "Task completed.", 100)
        except Exception:
            pass

        # Trigger NIGHTFALL Left Wing: Final Deliverables & Results
        try:
            import re
            file_match = re.search(r'([A-Za-z]:\\[^\s"\'<>`\r\n]+\.(?:pdf|docx|xlsx|pptx|png|jpg|mp4|py|html|json|txt))', str(result))
            detected_file = file_match.group(1).strip() if file_match else None
            if not detected_file and "file_path" in args:
                p_cand = Path(args["file_path"])
                if p_cand.exists():
                    detected_file = str(p_cand.resolve())

            res_str = str(result)
            bullets = []
            for l in res_str.splitlines():
                cl = l.strip()
                if (cl.startswith(("-", "*", "•", ">")) or cl.startswith(tuple("123456789."))) and len(cl) > 3:
                    bullets.append(cl.lstrip("-*•>0123456789. ").strip())
                elif len(cl) > 15 and not cl.startswith("#") and len(bullets) < 4:
                    bullets.append(cl)

            self.ui.show_hud_deliverable(
                title=f"{tool_title.upper()} DELIVERABLE",
                summary=res_str[:160] if not bullets else "",
                bullets=bullets[:5],
                file_path=detected_file,
                kind=name
            )
        except Exception:
            pass

        tool_voice = self._connect_tool_voice(name, result)
        if tool_voice:
            try:
                self.ui.write_log(f"NIGHTFALL Evo: {tool_voice}")
            except Exception:
                pass
            try:
                self.speak(tool_voice)
            except Exception:
                pass

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[NIGHTFALL EVO] 📤 {name} → {str(result)[:80]}")

        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _serve_dashboard(self):
        if self._dashboard is None:
            self.ui.write_log("ERR: Mobile Connect disabled because dashboard dependencies are missing.")
            return
        try:
            self._dashboard.set_connect_callback(self._on_phone_connected)
            self._dashboard.set_wake_callback(lambda: None)
            await self._dashboard.serve()
        except Exception as e:
            self.ui.write_log(f"ERR: Mobile Connect server failed: {e}")
            traceback.print_exc()

    async def _consume_remote_commands(self):
        if self._dashboard is None:
            return
        while True:
            text = await self._dashboard._command_queue.get()
            if text:
                try:
                    self.ui.submit_external_command(text, source="mobile")
                except Exception:
                    self._on_text_command(text, source="mobile")

    async def _relay_phone_audio(self):
        if self._dashboard is None:
            return
        while True:
            frame = await self._dashboard._phone_audio_queue.get()
            if not self.out_queue:
                continue
            self._phone_active = True
            try:
                await self.out_queue.put(frame)
            finally:
                await asyncio.sleep(0.08)
                if self._dashboard._phone_audio_queue.empty():
                    self._phone_active = False

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[NIGHTFALL EVO] 🎤 Mic started")
        loop = asyncio.get_event_loop()
        import numpy as np

        _mic_name = config_manager.get_input_device()
        _mic_dev = audio_devices.resolve(_mic_name, "input") if _mic_name else None
        available_inputs = audio_devices.list_devices("input")
        if _mic_dev is None and available_inputs:
            _mic_name = available_inputs[0]
            _mic_dev = audio_devices.resolve(_mic_name, "input")
        if _mic_dev is None:
            self.ui.write_log(
                "SYS: No usable microphone is available at the configured sample rate. "
                "Voice input is paused; text and mobile remote remain available."
            )
            while True:
                await asyncio.sleep(60)

        speech_buffer = bytearray()
        silence_chunks = 0

        def callback(indata, frames, time_info, status):
            nonlocal silence_chunks
            with self._speaking_lock:
                NIGHTFALL_speaking = self._is_speaking
            if self._phone_active:
                return

            if getattr(self, "_ptt_enabled", False) and not getattr(self, "_ptt_held", False):
                data = np.zeros_like(indata).tobytes()
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )
                return
            
            if not self.ui.muted or getattr(self.ui, "_wakeword_listening", False):
                lvl = float(np.sqrt(np.mean(np.square(indata, dtype=np.float32))))
                
                # Handle Local AI voice input when in Local or Offline mode
                app_cfg = config_manager.load_settings()
                if app_cfg.get("default_ai_provider") == "Local" or app_cfg.get("offline_mode_enabled", False):
                    if not NIGHTFALL_speaking and not self.ui.muted:
                        if lvl > 22.0:
                            speech_buffer.extend(indata.tobytes())
                            silence_chunks = 0
                        elif len(speech_buffer) > 0:
                            silence_chunks += 1
                            # ~0.7s of silence (each chunk is ~30ms -> 20 chunks)
                            if silence_chunks > 18:
                                captured = bytes(speech_buffer)
                                speech_buffer.clear()
                                silence_chunks = 0
                                if len(captured) > (SEND_SAMPLE_RATE * 2 * 0.5):
                                    def _process_local_speech(pcm_bytes):
                                        try:
                                            import speech_recognition as sr
                                            r = sr.Recognizer()
                                            audio_data = sr.AudioData(pcm_bytes, SEND_SAMPLE_RATE, 2)
                                            text_cmd = r.recognize_google(audio_data)
                                            if text_cmd and len(text_cmd.strip()) > 1:
                                                print(f"[Local AI Voice] 🎙️ Heard: {text_cmd}")
                                                self._on_text_command(text_cmd, source="mic")
                                        except Exception:
                                            pass
                                    threading.Thread(target=_process_local_speech, args=(captured,), daemon=True).start()

                if NIGHTFALL_speaking:
                    if self._echo.is_user_speech(indata, SEND_SAMPLE_RATE, lvl) and lvl > 28.0:
                        loop.call_soon_threadsafe(self.trigger_barge_in)
                        data = indata.tobytes()
                    else:
                        data = np.zeros_like(indata).tobytes()
                else:
                    if not self.ui.muted:
                        try:
                            self.ui.set_audio_level(min(1.0, lvl / 1200.0))
                        except Exception:
                            pass
                    if self._echo._hist:
                        self._echo.reset()
                    if lvl > 10.0:
                        data = indata.tobytes()
                    else:
                        data = np.zeros_like(indata).tobytes()
                    
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                device=_mic_dev,
                callback=callback,
            ):
                print(f"[NIGHTFALL EVO] 🎤 Mic stream open ({_mic_name or 'Default'})")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[NIGHTFALL EVO] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[NIGHTFALL EVO] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():
                    _sru = getattr(response, "session_resumption_update", None)
                    if _sru is not None:
                        if getattr(_sru, "resumable", False) and getattr(_sru, "new_handle", None):
                            self._resume_handle = _sru.new_handle

                    if response.data:
                        self.set_speaking(True)
                        self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            self.set_speaking(True)
                            txt = sc.output_transcription.text.strip()
                            if txt:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                try:
                                    from actions.attention_monitor import stop_native_speech
                                    stop_native_speech()
                                except Exception:
                                    pass
                                in_buf.append(txt)
                                if self.ui.muted and _wakeword_detected(txt):
                                    try:
                                        self.ui.set_muted_state(False, wakeword=True)
                                        self.ui.write_log("SYS: Wake word detected. Mic active.")
                                    except Exception:
                                        pass

                        if sc.turn_complete:
                            self.set_speaking(False)

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self._last_user_utterance = full_in
                                self.ui.write_log(f"You: {full_in}")
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"NIGHTFALL Evo: {full_out}")
                            out_buf = []

                            if full_in and len(full_in) > 5:
                                threading.Thread(
                                    target=_update_memory_async,
                                    args=(full_in, full_out),
                                    daemon=True
                                ).start()

                    if response.tool_call:
                        self.ui.set_state("EXECUTING")
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[NIGHTFALL EVO] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        self.ui.set_state("THINKING")
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
                        await self._deliver_pending_screen_frame()

        except Exception as e:
            print(f"[NIGHTFALL EVO] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[NIGHTFALL EVO] 🔊 Play started")
        loop = asyncio.get_event_loop()
        import numpy as np

        _spk_name = config_manager.get_output_device()
        _spk_dev = audio_devices.resolve(_spk_name, "output") if _spk_name else None

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
            device=_spk_dev,
        )
        stream.start()
        try:
            while True:
                chunk = await self.audio_in_queue.get()
                try:
                    pcm = np.frombuffer(chunk, dtype=np.int16)
                    lvl = float(np.sqrt(np.mean(np.square(pcm, dtype=np.float32))))
                    self._echo.note_output(pcm, RECEIVE_SAMPLE_RATE, lvl)
                    self.ui.set_audio_level(min(1.0, lvl / 2500.0))
                except Exception:
                    pass
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[NIGHTFALL EVO] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    async def run(self):
        # announce boot steps to UI overlay (thread-safe wrappers)
        try:
            self.ui.boot_add_step("Load configuration")
            self.ui.boot_add_step("Start attention monitor")
            self.ui.boot_add_step("Start dashboard server")
            self.ui.boot_add_step("Initialize audio")
            self.ui.boot_add_step("Connect AI backend")
            self.ui.boot_add_step("Finalize startup")
            self.ui.boot_set_progress(3, "Preparing startup...")
        except Exception:
            pass

        self._attention_monitor.start()
        try:
            self.ui.boot_set_step_status("Start attention monitor", "done")
            self.ui.boot_set_progress(12, "Attention monitor online")
        except Exception:
            pass
        if self._dashboard is not None:
            if not self._dashboard_started:
                self._dashboard_started = True
                asyncio.create_task(self._serve_dashboard())
                try:
                    self.ui.boot_set_step_status("Start dashboard server", "done")
                    self.ui.boot_set_progress(22, "Mobile connect server running")
                except Exception:
                    pass
            asyncio.create_task(self._consume_remote_commands())
            asyncio.create_task(self._relay_phone_audio())
        try:
            self.ui.boot_set_progress(36, "Initializing AI client")
        except Exception:
            pass

        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"}
        )

        while True:
            try:
                print("[NIGHTFALL EVO] 🔌 Connecting...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                connect_cm = client.aio.live.connect(model=LIVE_MODEL, config=config)
                session = await asyncio.wait_for(connect_cm.__aenter__(), timeout=LIVE_CONNECT_TIMEOUT)
                try:
                    async with asyncio.TaskGroup() as tg:
                        self.session        = session
                        self._loop          = asyncio.get_event_loop()
                        self.audio_in_queue = asyncio.Queue()
                        self.out_queue      = asyncio.Queue()  # Fix: removed maxsize=10 to prevent dropping packets
                        
                        print("[NIGHTFALL EVO] ✅ Connected.")
                        try:
                            self.ui.boot_set_step_status("Connect AI backend", "done")
                            self.ui.boot_set_progress(75, "AI backend connected")
                        except Exception:
                            pass
                        self.ui.set_state("LISTENING")
                        self.ui.write_log("SYS: NIGHTFALL Evo online.")

                        tg.create_task(self._send_realtime())
                        tg.create_task(self._listen_audio())
                        tg.create_task(self._relay_phone_audio())
                        tg.create_task(self._receive_audio())
                        tg.create_task(self._play_audio())
                        if not self._startup_briefing_started:
                            self._startup_briefing_started = True
                            threading.Thread(
                                target=_speak_daily_briefing,
                                args=(self.ui, self.speak),
                                daemon=True,
                                name="daily-briefing",
                            ).start()
                        try:
                            self.ui.boot_set_step_status("Initialize audio", "done")
                            self.ui.boot_set_progress(92, "Audio subsystems online")
                        except Exception:
                            pass
                        # finalize
                        try:
                            self.ui.boot_set_step_status("Finalize startup", "done")
                            self.ui.boot_set_progress(100, "Startup complete")
                        except Exception:
                            pass
                finally:
                    try:
                        await connect_cm.__aexit__(None, None, None)
                    except Exception:
                        pass
                    
            except Exception as e:
                print(f"[NIGHTFALL EVO] ⚠️ {e}")
                traceback.print_exc()
                if _is_gemini_limit_error(e):
                    self._use_openrouter_first = True
                self.session = None
                self._loop = None
            self.set_speaking(False)
            self.ui.set_state("LISTENING")
            print("[NIGHTFALL EVO] 🔄 Reconnecting in 5s...")
            await asyncio.sleep(5)

def main():
    _startup_log("main entered")
    try:
        if update_from_github(BASE_DIR):
            _startup_log("updated from GitHub; restarting")
            restart_application(BASE_DIR)
            return
    except Exception as exc:
        _startup_log(f"GitHub update skipped: {exc}")
    _ensure_desktop_shortcut()
    ui = NIGHTFALLUI(str(BASE_DIR / "assets" / "NIGHTFALL_Lite_Logo.png"), show_immediately=True)
    try:
        from core.quick_actions import QuickActionsManager
        ui._quick_actions_mgr = QuickActionsManager(ui)
        ui._quick_actions_mgr.start()
    except Exception as _qa_exc:
        print(f"[QuickActions] unavailable: {_qa_exc}")
    dashboard = None
    dashboard_enabled = DashboardServer is not None and not _is_port_in_use(8000)
    if DashboardServer is not None and not dashboard_enabled:
        _startup_log("dashboard disabled: port 8000 already in use")
        try:
            ui.write_log("SYS: Mobile Connect is already running in another NIGHTFALL Evo instance.")
        except Exception:
            pass
    if dashboard_enabled:
        dashboard = DashboardServer()

    if dashboard is not None:
        def _start_dashboard_server():
            try:
                _startup_log("dashboard thread started")
                asyncio.run(dashboard.serve())
            except Exception as exc:
                _startup_log(f"dashboard thread error: {exc}")
                try:
                    ui.write_log(f"ERR: Mobile Connect server failed: {exc}")
                except Exception:
                    pass

        threading.Thread(target=_start_dashboard_server, daemon=True).start()
        _startup_log("dashboard thread spawned")

    nightfall_connect = None
    nightfall_connect_enabled = False
    if get_nightfall_connect_service is not None:
        try:
            nightfall_connect = get_nightfall_connect_service(BASE_DIR)
            nightfall_connect_enabled = bool(nightfall_connect.gateway.config.enabled)
        except Exception as exc:
            _startup_log(f"NIGHTFALL connect init failed: {exc}")
            try:
                ui.write_log(f"ERR: NIGHTFALL Connect failed to initialize: {exc}")
            except Exception:
                pass
            nightfall_connect = None
    try:
        if nightfall_connect is not None and hasattr(ui, "set_nightfall_connect_service"):
            ui.set_nightfall_connect_service(nightfall_connect)
    except Exception:
        pass
    if nightfall_connect is not None and nightfall_connect_enabled:
        connect_port = int(getattr(nightfall_connect.gateway.config, "port", 8765))
        if _is_port_in_use(connect_port):
            _startup_log(f"NIGHTFALL connect disabled: port {connect_port} already in use")
            try:
                ui.write_log(f"SYS: NIGHTFALL Connect is already running on port {connect_port}.")
            except Exception:
                pass
        else:
            def _start_nightfall_connect_server():
                try:
                    _startup_log("NIGHTFALL connect thread started")
                    nightfall_connect.start_background()
                    _startup_log("NIGHTFALL connect thread spawned")
                except Exception as exc:
                    _startup_log(f"NIGHTFALL connect thread error: {exc}")
                    try:
                        ui.write_log(f"ERR: NIGHTFALL Connect server failed: {exc}")
                    except Exception:
                        pass

            threading.Thread(target=_start_nightfall_connect_server, daemon=True).start()



    ui.show_main()
    _startup_log("ui shown")

    # Start NIGHTFALL Passive Sensorium Engine (v2)
    try:
        def _on_sensorium_alert(alert_type: str, meta: dict):
            msg = meta.get("message", "System state change detected.")
            try:
                ui.write_log(f"🧠 SENSORIUM: {msg}")
            except Exception:
                pass
        sensorium.register_interjection_handler(_on_sensorium_alert)
        sensorium.start()
        _startup_log("passive sensorium daemon started")
    except Exception as exc:
        _startup_log(f"sensorium start failed: {exc}")

    try:
        from core.globe_window import GlobeWindow
        GlobeWindow.get_instance(parent=None)
        _startup_log("globe window initialized")
    except Exception as exc:
        _startup_log(f"globe window initialization failed: {exc}")
        try:
            ui.write_log(f"ERR: Globe window initialization failed: {exc}")
        except Exception:
            pass

    # Initialize plugin manager and load any plugins from ./plugins
    try:
        plugin_manager = PluginManager(BASE_DIR)
        plugin_manager.load_plugins()
    except Exception:
        plugin_manager = None

    def runner():
        _startup_log("runner waiting api key")
        ui.wait_for_api_key()
        _startup_log("runner api key ready")
        NIGHTFALL_evo = NIGHTFALLLive(
            ui,
            dashboard=dashboard,
            dashboard_started=dashboard is not None,
            enable_dashboard=dashboard_enabled,
        )

        # Wire proactive voice to Sensorium
        def _proactive_sensorium_voice(alert_type: str, meta: dict):
            spoken_text = meta.get("speech")
            if spoken_text and not getattr(ui, "muted", False):
                try:
                    NIGHTFALL_evo.speak(spoken_text, proactive=True)
                except Exception as exc:
                    print(f"[Sensorium Speak Error]: {exc}")

        sensorium.register_interjection_handler(_proactive_sensorium_voice)
        try:
            if plugin_manager is not None:
                NIGHTFALL_evo.plugin_manager = plugin_manager
                plugin_manager.register_NIGHTFALL(NIGHTFALL_evo)
                # allow plugins to run a startup hook
                try:
                    plugin_manager.dispatch("on_startup", NIGHTFALL_evo)
                except Exception:
                    pass
        except Exception:
            pass

        print(f"DEBUG: start_ig_daemon is {start_ig_daemon}")
        
        if start_ig_daemon:
            try:
                from actions.instagram_mcp import set_ig_prompt_callback
            except ImportError:
                from actions.instagram_chat import set_ig_prompt_callback

            def _ig_handler(thread_id, username, text, is_auto):
                if is_auto:
                    return _ig_gemini_reply(username, text)
                else:
                    NIGHTFALL_evo._ig_reply_mode = True
                    NIGHTFALL_evo._ig_pending_thread = {
                        "thread_id": thread_id,
                        "username": username,
                        "message": text
                    }
                    clean_text = (text or "").strip()
                    snippet = f": '{clean_text[:75]}...'" if len(clean_text) > 75 else (f": '{clean_text}'" if clean_text else "")
                    msg = f"You received a new Instagram message from {username}{snippet}. What should I reply, or should I take over the chat?"
                    ui.write_log(f"📱 Insta (@{username}): {clean_text or '[Media/Attachment]'}")
                    ui.write_log(f"NIGHTFALL Evo: {msg}")
                    NIGHTFALL_evo.speak(msg)
                    return None
                
            set_ig_prompt_callback(_ig_handler)
            start_ig_daemon()

        # Background Email Watcher
        try:
            from actions.google_workspace_mcp import (
                start_email_daemon,
                set_email_prompt_callback,
                get_stored_gmail_credentials,
            )
            gmail_addr, gmail_pw = get_stored_gmail_credentials()
            if gmail_addr and gmail_pw:
                def _email_handler(sender, subject, msg_id):
                    clean_subj = (subject or "No Subject").strip()
                    subj_preview = f"'{clean_subj[:70]}...'" if len(clean_subj) > 70 else f"'{clean_subj}'"
                    msg = f"You received a new email from {sender} with subject: {subj_preview}."
                    ui.write_log(f"📧 Email ({sender}): {clean_subj}")
                    ui.write_log(f"NIGHTFALL Evo: {msg}")
                    NIGHTFALL_evo.speak(msg)

                set_email_prompt_callback(_email_handler)
                start_email_daemon(poll_interval=25)
                print("[NIGHTFALL Evo] Background email watcher started.")
        except Exception as e:
            print(f"[NIGHTFALL Evo] Email daemon initialization notice: {e}")

        def _clipboard_monitor():
            return  # Disabled: copied text no longer auto-triggers an LLM reply.

        try:
            asyncio.run(NIGHTFALL_evo.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    def start_runner():
        threading.Thread(target=runner, daemon=True).start()

    start_runner()
    ui.show_main()
    ui.root.mainloop()


if __name__ == "__main__":
    import sys
    import os
    import traceback

    # Intercept subprocess calls when running as PyInstaller .exe
    if len(sys.argv) >= 2:
        if sys.argv[1].endswith(".py") and os.path.exists(sys.argv[1]):
            # AgentExecutor is trying to run a dynamic script
            try:
                with open(sys.argv[1], 'r', encoding='utf-8') as f:
                    code = f.read()
                namespace = {"__name__": "__main__", "__file__": sys.argv[1]}
                exec(code, namespace)
                sys.exit(0)
            except Exception as e:
                traceback.print_exc()
                sys.exit(1)
        elif sys.argv[1] == "-m" and len(sys.argv) >= 3 and sys.argv[2] == "pip":
            try:
                from pip._internal.cli.main import main as pip_main
                sys.exit(pip_main(sys.argv[3:]))
            except ImportError:
                print("pip is not available in the compiled executable.")
                sys.exit(1)

    try:
        main()
    except Exception as e:
        with open("FATAL_CRASH.log", "w") as f:
            traceback.print_exc(file=f)
        raise
