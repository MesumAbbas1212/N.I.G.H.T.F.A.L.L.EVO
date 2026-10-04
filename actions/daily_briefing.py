from core.user_paths import get_user_data_dir
# actions/daily_briefing.py
"""
Unified Morning Intelligence Briefing for NIGHTFALL AI.

Compiles a comprehensive, executive daily intelligence report:
1. Live Weather (Temperature, conditions, humidity, outdoor feel)
2. Google Calendar / Local Agenda (Today's scheduled events)
3. Gmail VIP Communications (Unread count, primary senders & topics)
4. Instagram Direct Intelligence (Recent DMs & active conversations)
5. Top World / Technology Headlines (Google News RSS)

Outputs:
- Structured data dictionary for visual Holographic HUD card in ui.py
- Natural, conversational Iron Man-style spoken narrative for TTS
"""

import datetime
import json
import os
import queue
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
BRIEFING_FETCH_TIMEOUT_SECONDS = 8.0

PLUGIN = {
    "name": "daily_briefing",
    "description": (
        "Delivers a comprehensive morning intelligence briefing to the user including live weather, "
        "today's calendar meetings, unread Gmail communications, Instagram DMs, and top news headlines. "
        "Call this whenever the user asks for their morning briefing, daily update, schedule review, or what's happening today."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "category": {
                "type": "STRING",
                "description": "Optional category focus: all (default), tech, world, schedule",
            },
            "city": {
                "type": "STRING",
                "description": "Optional specific city for weather",
            }
        },
        "required": [],
    },
}


def _get_weather_intel(city: Optional[str] = None) -> Dict[str, Any]:
    """Fetches live weather telemetry from weather_report."""
    try:
        from actions.weather_report import get_live_weather
        return get_live_weather(city)
    except Exception as e:
        print(f"[DailyBriefing] Weather fetch notice: {e}")
        return {
            "status": "unavailable",
            "city": city or "Local Area",
            "temp_c": 26,
            "condition": "Clear",
            "humidity": "60%",
            "wind": "10 km/h",
            "summary": "Weather telemetry unavailable",
        }


def _get_calendar_intel() -> Dict[str, Any]:
    """Gathers today's upcoming meetings from Google Calendar and local storage."""
    events_list = []
    
    # 1. Try Google Calendar Service
    try:
        from actions.google_workspace_mcp import GoogleCalendarEngine
        raw_events = GoogleCalendarEngine.list_events(days=1)
        if raw_events and "no upcoming events" not in raw_events.lower():
            for line in raw_events.split("\n"):
                line = line.strip()
                if line and (line.startswith("-") or line.startswith("•") or (line[0].isdigit() and "." in line[:3])):
                    clean_line = re.sub(r"^[-•\d\.\s]+", "", line).strip()
                    if clean_line:
                        events_list.append(clean_line)
    except Exception as e:
        print(f"[DailyBriefing] Google Calendar notice: {e}")

    # 2. Check local calendar storage as fallback/supplement
    try:
        events_path = get_user_data_dir() / "memory" / "calendar_events.json"
        if events_path.exists():
            with open(events_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            all_events = data if isinstance(data, list) else data.get("events", [])
            today_str = datetime.date.today().isoformat()
            today_events = [e for e in all_events if isinstance(e, dict) and e.get("date") == today_str]
            today_events.sort(key=lambda x: x.get("time", "00:00"))
            for e in today_events:
                t = e.get("time", "")
                title = e.get("title", "Event")
                entry = f"{title} at {t}" if t else title
                if entry not in events_list:
                    events_list.append(entry)
    except Exception as e:
        print(f"[DailyBriefing] Local calendar notice: {e}")

    count = len(events_list)
    summary = f"{count} event(s) scheduled" if count > 0 else "Schedule is clear"
    return {
        "count": count,
        "events": events_list[:5],
        "summary": summary,
    }


def _get_gmail_intel() -> Dict[str, Any]:
    """Checks for unread VIP emails from Gmail."""
    try:
        from actions.google_workspace_mcp import GmailEngine, get_stored_gmail_credentials
        email_addr, _ = get_stored_gmail_credentials()
        if not email_addr:
            return {"configured": False, "count": 0, "senders": [], "summary": "Gmail not configured"}

        raw_msgs = GmailEngine.list_messages(query="UNSEEN", max_results=3)
        if not raw_msgs or "no messages found" in raw_msgs.lower():
            return {"configured": True, "count": 0, "senders": [], "summary": "Zero unread emails"}

        # Parse message headers (From and Subject)
        senders = []
        subjects = []
        for line in raw_msgs.split("\n"):
            line = line.strip()
            if line.startswith("From:"):
                clean_from = re.sub(r"^From:\s*", "", line)
                clean_name = re.sub(r"<[^>]+>", "", clean_from).strip().strip('"')
                if clean_name and clean_name not in senders:
                    senders.append(clean_name)
            elif line.startswith("Subject:"):
                subj = re.sub(r"^Subject:\s*", "", line).strip()
                if subj and subj not in subjects:
                    subjects.append(subj)

        count = max(len(senders), len(subjects), 1)
        summary = f"{count} unread email(s)" if count > 0 else "Inbox clean"
        return {
            "configured": True,
            "count": count,
            "senders": senders[:3],
            "subjects": subjects[:3],
            "summary": summary,
        }
    except Exception as e:
        print(f"[DailyBriefing] Gmail fetch notice: {e}")
        return {"configured": False, "count": 0, "senders": [], "summary": "Gmail offline"}


def _get_instagram_intel() -> Dict[str, Any]:
    """Checks for recent unread or incoming Instagram direct messages."""
    try:
        from actions.instagram_mcp import InstagramService
        svc = InstagramService.instance()
        inbox = svc.get_inbox(amount=4)
        if not inbox or isinstance(inbox, str):
            return {"configured": True, "count": 0, "senders": [], "summary": "No incoming DMs"}

        incoming = [m for m in inbox if isinstance(m, dict) and not m.get("sent_by_me")]
        senders = list(dict.fromkeys([m.get("sender", "Unknown") for m in incoming if m.get("sender")]))
        count = len(incoming)
        summary = f"{count} new message(s)" if count > 0 else "No new DMs"
        return {
            "configured": True,
            "count": count,
            "senders": senders[:3],
            "recent": incoming[:3],
            "summary": summary,
        }
    except Exception as e:
        print(f"[DailyBriefing] Instagram notice: {e}")
        return {"configured": False, "count": 0, "senders": [], "summary": "Instagram offline"}


def _get_top_headlines(category: str = "all", limit: int = 2) -> List[str]:
    """Fetches top world/tech headlines using Google News RSS with zero rate limits."""
    headlines = []
    feed_url = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
    if category.lower() == "tech":
        feed_url = "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en"

    try:
        req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            root = ET.fromstring(resp.read())
            items = root.findall(".//item")[:limit]
            for item in items:
                title_elem = item.find("title")
                if title_elem is not None and title_elem.text:
                    title = title_elem.text.strip()
                    if " - " in title:
                        title = title.rsplit(" - ", 1)[0]
                    headlines.append(title)
    except Exception as e:
        print(f"[DailyBriefing] News fetch notice: {e}")

    return headlines


def _collect_briefing_intel(category: str, city: Optional[str]) -> Dict[str, Any]:
    try:
        from memory import config_manager
        is_offline = bool(config_manager.get_setting("offline_mode_enabled", False))
    except Exception:
        is_offline = False

    if is_offline:
        collectors = {
            "weather": lambda: {
                "status": "offline", "city": city or "Local Workstation", "temp_c": 24,
                "condition": "Offline Mode", "humidity": "--", "wind": "--",
                "summary": "Offline air-gapped environment",
            },
            "calendar": _get_calendar_intel,
            "gmail": lambda: {"configured": False, "count": 0, "senders": [], "summary": "Offline"},
            "instagram": lambda: {"configured": False, "count": 0, "senders": [], "summary": "Offline"},
            "headlines": lambda: [],
        }
    else:
        collectors = {
            "weather": lambda: _get_weather_intel(city),
            "calendar": _get_calendar_intel,
            "gmail": _get_gmail_intel,
            "instagram": _get_instagram_intel,
            "headlines": lambda: _get_top_headlines(category=category, limit=2),
        }

    fallbacks = {
        "weather": {
            "status": "unavailable", "city": city or "Local Area", "temp_c": 26,
            "condition": "Unavailable", "humidity": "--", "wind": "--",
            "summary": "Weather telemetry unavailable",
        },
        "calendar": {"count": 0, "events": [], "summary": "Schedule unavailable"},
        "gmail": {"configured": False, "count": 0, "senders": [], "summary": "Gmail unavailable"},
        "instagram": {"configured": False, "count": 0, "senders": [], "summary": "Instagram unavailable"},
        "headlines": [],
    }
    results_queue: queue.Queue = queue.Queue()

    def collect(name: str, collector) -> None:
        try:
            results_queue.put((name, collector()))
        except Exception as exc:
            results_queue.put((name, exc))

    for name, collector in collectors.items():
        threading.Thread(
            target=collect,
            args=(name, collector),
            daemon=True,
            name=f"briefing-{name}",
        ).start()

    results = {}
    pending = set(collectors)
    deadline = time.monotonic() + BRIEFING_FETCH_TIMEOUT_SECONDS
    while pending:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            name, value = results_queue.get(timeout=remaining)
        except queue.Empty:
            break
        pending.discard(name)
        if isinstance(value, Exception):
            print(f"[DailyBriefing] {name.title()} collection failed: {value}")
        else:
            results[name] = value

    for name in pending:
        print(f"[DailyBriefing] {name.title()} collection timed out.")

    return {name: results.get(name, fallbacks[name]) for name in collectors}


def compile_unified_briefing(category: str = "all", city: Optional[str] = None) -> Tuple[Dict[str, Any], str]:
    """
    Compiles the full Unified Morning Briefing.
    Returns:
      (briefing_data_dict, spoken_narrative_str)
    """
    now = datetime.datetime.now()
    time_str = now.strftime("%I:%M %p").lstrip("0")
    date_str = now.strftime("%A, %B %d")

    greeting = "Good morning"
    if 12 <= now.hour < 17:
        greeting = "Good afternoon"
    elif now.hour >= 17:
        greeting = "Good evening"

    intelligence = _collect_briefing_intel(category, city)
    weather = intelligence["weather"]
    calendar = intelligence["calendar"]
    gmail = intelligence["gmail"]
    instagram = intelligence["instagram"]
    headlines = intelligence["headlines"]

    # Compile cinematic, conversational spoken narrative
    narrative_parts = [f"{greeting}, sir. Today is {date_str}, and the time is {time_str}."]

    # 1. Weather
    if weather.get("status") == "success":
        c_name = weather.get("city", "your area")
        temp = weather.get("temp_c", 26)
        cond = weather.get("condition", "Clear")
        narrative_parts.append(f"Currently in {c_name}, it's {temp} degrees Celsius with {cond.lower()}.")
    
    # 2. Calendar
    if calendar.get("count", 0) > 0:
        events = calendar.get("events", [])
        narrative_parts.append(f"On your agenda today, you have {len(events)} item(s): {', '.join(events)}.")
    else:
        narrative_parts.append("Your schedule is completely clear today.")

    # 3. Communications (Gmail & Instagram)
    comm_parts = []
    if gmail.get("configured") and gmail.get("count", 0) > 0:
        senders = gmail.get("senders", [])
        s_str = f" from {', '.join(senders)}" if senders else ""
        comm_parts.append(f"{gmail.get('count')} unread email(s){s_str}")

    if instagram.get("configured") and instagram.get("count", 0) > 0:
        i_senders = instagram.get("senders", [])
        is_str = f" from {', '.join(['@' + s for s in i_senders])}" if i_senders else ""
        comm_parts.append(f"{instagram.get('count')} Instagram message(s){is_str}")

    if comm_parts:
        narrative_parts.append(f"Regarding communications, you have {' and '.join(comm_parts)}.")
    else:
        narrative_parts.append("All inboxes are currently caught up.")

    # 4. Top News Headline
    if headlines:
        narrative_parts.append(f"Top headline today: {headlines[0]}.")

    narrative_parts.append("All primary systems are nominal and ready for your directives.")

    spoken_narrative = " ".join(narrative_parts)

    briefing_data = {
        "greeting": greeting,
        "timestamp": f"{date_str} • {time_str}",
        "weather": weather,
        "calendar": calendar,
        "gmail": gmail,
        "instagram": instagram,
        "headlines": headlines,
        "spoken_narrative": spoken_narrative,
    }

    return briefing_data, spoken_narrative


def compile_daily_briefing(category: str = "all") -> str:
    """Legacy helper returning spoken narrative."""
    _, narrative = compile_unified_briefing(category=category)
    return narrative


def daily_briefing(
    parameters: dict | None = None,
    response: str | None = None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    """
    Action entry point for daily briefing.
    Renders visual holographic HUD card on UI and speaks narrative.
    """
    p = parameters or {}
    category = p.get("category", "all")
    city = p.get("city")

    data, narrative = compile_unified_briefing(category=category, city=city)

    if player:
        try:
            player.show_daily_briefing(data)
            player.write_log(f"NIGHTFALL Evo: {narrative}")
        except Exception as e:
            print(f"[DailyBriefing] UI render notice: {e}")

    # Speak daily briefing via provided speech callback (Gemini Live Charon voice) or fallback
    spoken = False
    if speak:
        try:
            speak(narrative)
            spoken = True
        except Exception as e:
            print(f"[DailyBriefing] Speech synthesis notice: {e}")

    if not spoken:
        try:
            from actions.attention_monitor import _speak_edge_native
            _speak_edge_native(narrative)
        except Exception as e:
            print(f"[DailyBriefing] Direct speech notice: {e}")

    return narrative


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin wrapper."""
    return daily_briefing(parameters, player=player, session_memory=session_memory)

