from core.user_paths import get_user_data_dir
import json
import re
import sys
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from actions.office_builder import create_presentation, create_spreadsheet

logger = logging.getLogger("office_generator")
logger.setLevel(logging.INFO)

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR = get_base_dir()
API_CONFIG_PATH = get_user_data_dir() / "config" / "api_keys.json"


import concurrent.futures

def _call_gemini_json(
    prompt: str,
    system_instruction: str,
    models: Optional[list] = None,
    timeout: int = 14,
) -> Optional[dict]:
    """Generates structured JSON using Gemini with model failover, timeouts, and retries."""
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            keys = json.load(f)
        gemini_key = keys.get("gemini_api_key", "").strip()

        if gemini_key:
            from google import genai
            client = genai.Client(api_key=gemini_key)

            models_to_try = models or [
                "gemini-3.1-flash-lite",
                "gemini-3.5-flash-lite",
                "gemini-2.5-flash-lite",
                "gemini-flash-latest",
                "gemini-2.5-flash",
                "gemini-3.6-flash",
            ]

            def _query_model(m_name: str):
                return client.models.generate_content(
                    model=m_name,
                    contents=prompt,
                    config={
                        "system_instruction": system_instruction,
                        "temperature": 0.2,
                        "response_mime_type": "application/json"
                    }
                )

            for model_name in models_to_try:
                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(_query_model, model_name)
                        resp = future.result(timeout=timeout)
                    if resp and resp.text:
                        clean = resp.text.strip()
                        return json.loads(clean)
                except concurrent.futures.TimeoutError:
                    logger.warning(f"[OfficeGen] Model {model_name} timed out after 14s")
                    continue
                except Exception as e:
                    logger.warning(f"[OfficeGen] Model {model_name} failed: {e}")
                    continue
    except Exception as exc:
        logger.warning(f"[OfficeGen] Gemini direct call error: {exc}")

    # Fallback to UnifiedAIClient if OpenRouter has key
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            or_key = json.load(f).get("openrouter_api_key", "").strip()
        if or_key:
            from llm_client import client as ai_client
            return ai_client.chat_json(prompt, system=system_instruction)
    except Exception:
        pass

    return None


def generate_presentation_from_prompt(user_prompt: str, player=None, speak: Optional[Callable[[str], None]] = None) -> str:
    """Creates a PowerPoint presentation (.pptx) from a natural language prompt."""
    if speak:
        speak("Designing your presentation slides, sir...")

    system_instruction = (
        "You are an expert presentation designer. "
        "Create a comprehensive, professional slide deck outline for the user's request. "
        "Return ONLY a valid JSON object matching this exact schema:\n"
        "{\n"
        '  "title": "Clear Presentation Title",\n'
        '  "subtitle": "Subtitle or Target Audience",\n'
        '  "theme": "corporate | neon | luxury | academic | sunset | creative",\n'
        '  "slides": [\n'
        "    {\n"
        '      "title": "Slide Title",\n'
        '      "kicker": "ALL CAPS SECTION",\n'
        '      "bullets": ["Detailed bullet point 1", "Detailed bullet point 2", "Detailed bullet point 3"],\n'
        '      "notes": "Speaker notes or visual callout",\n'
        '      "status": "Ready",\n'
        '      "focus": "Strategy",\n'
        '      "type": "Overview"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Generate 4 to 7 high-impact slides with detailed bullet points."
    )

    data = _call_gemini_json(user_prompt, system_instruction)

    if not data or not isinstance(data, dict):
        # Fallback template if model offline
        clean_title = re.sub(r"(?i)^(make|create|build|generate|design)\s+(a\s+)?(ppt|presentation|deck|slides)\s+(on|about|for)?\s*", "", user_prompt).strip()
        clean_title = clean_title.title() or "Presentation Overview"
        data = {
            "title": clean_title,
            "subtitle": "Prepared by NIGHTFALL AI",
            "theme": "corporate",
            "slides": [
                {
                    "title": "Executive Summary",
                    "kicker": "OVERVIEW",
                    "bullets": [f"Key initiatives and strategic focus for {clean_title}", "Market landscape and modern industry benchmarks", "Execution roadmap and key milestones"],
                    "notes": "Executive briefing",
                    "status": "Ready", "focus": "Executive", "type": "Summary"
                },
                {
                    "title": "Key Objectives & Pillars",
                    "kicker": "STRATEGY",
                    "bullets": ["High-value deliverables and timeline expectations", "Resource allocation and core competencies", "Measurable performance indicators (KPIs)"],
                    "notes": "Strategic focus",
                    "status": "In Progress", "focus": "Execution", "type": "Strategy"
                },
                {
                    "title": "Action Plan & Next Steps",
                    "kicker": "NEXT STEPS",
                    "bullets": ["Immediate action items for sprint 1", "Stakeholder alignment and review sessions", "Target completion date and deliverables"],
                    "notes": "Implementation plan",
                    "status": "Scheduled", "focus": "Roadmap", "type": "Action"
                }
            ]
        }

    title = data.get("title") or "NIGHTFALL Presentation"
    subtitle = data.get("subtitle") or ""
    theme = data.get("theme") or "corporate"
    slides = data.get("slides") or []

    result = create_presentation({
        "title": title,
        "subtitle": subtitle,
        "theme": theme,
        "slides": slides,
        "auto_open": True
    }, player=player)

    return result


def generate_spreadsheet_from_prompt(user_prompt: str, player=None, speak: Optional[Callable[[str], None]] = None) -> str:
    """Creates an Excel workbook (.xlsx) from a natural language prompt."""
    if speak:
        speak("Building your spreadsheet workbook, sir...")

    system_instruction = (
        "You are an expert financial and data analyst. "
        "Create a professional, structured Excel workbook for the user's request. "
        "Include relevant column headers, realistic sample data rows, and calculated formulas (e.g. '=SUM(B2:B5)'). "
        "Return ONLY a valid JSON object matching this exact schema:\n"
        "{\n"
        '  "title": "Workbook Title",\n'
        '  "worksheets": [\n'
        "    {\n"
        '      "name": "SheetName",\n'
        '      "title": "Worksheet Header Title",\n'
        '      "headers": ["Col1", "Col2", "Col3", "Col4"],\n'
        '      "rows": [\n'
        '        ["Item 1", 100, 200, "=B2+C2"],\n'
        '        ["Item 2", 150, 250, "=B3+C3"]\n'
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "Ensure headers and rows have matching column lengths. Generate 5 to 10 rows of realistic data."
    )

    data = _call_gemini_json(user_prompt, system_instruction)

    if not data or not isinstance(data, dict):
        clean_title = re.sub(r"(?i)^(make|create|build|generate)\s+(a\s+)?(spreadsheet|excel|sheet|table|tracker|budget)\s+(on|about|for)?\s*", "", user_prompt).strip()
        clean_title = clean_title.title() or "Data Tracker"
        data = {
            "title": clean_title,
            "worksheets": [{
                "name": "Overview",
                "title": f"{clean_title} - Master Tracker",
                "headers": ["Item / Category", "Q1 Planned", "Q1 Actual", "Variance", "Status"],
                "rows": [
                    ["Operations", 5000, 4800, "=B3-C3", "On Track"],
                    ["Marketing", 3000, 3200, "=B4-C4", "Under Review"],
                    ["Technology & AI", 4000, 3900, "=B5-C5", "On Track"],
                    ["Administration", 1500, 1450, "=B6-C6", "On Track"],
                    ["Contingency", 1000, 500, "=B7-C7", "Surplus"]
                ]
            }]
        }

    title = data.get("title") or "NIGHTFALL Workbook"
    worksheets = data.get("worksheets") or data.get("sheets") or []

    result = create_spreadsheet({
        "title": title,
        "worksheets": worksheets,
        "auto_open": True
    }, player=player)

    return result
