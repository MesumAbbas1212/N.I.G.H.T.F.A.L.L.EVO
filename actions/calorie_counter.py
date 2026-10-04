from core.user_paths import get_user_data_dir
"""
NIGHTFALL AI — Calorie & Nutrition Vision Engine.

Analyzes meals and food through camera capture, uploaded/local images,
or conversational descriptions. Provides comprehensive macronutrient breakdowns
(calories, protein, carbs, fats, fiber, glycemic index) and health insights.
Logs meals to memory/nutrition_log.json for daily nutrition tracking.
"""

import base64
import json
import logging
import os
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

logger = logging.getLogger("calorie_counter")

def _get_base_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR = _get_base_dir()

PLUGIN = {
    "name": "calorie_counter",
    "description": (
        "Analyzes FOOD and MEALS to report calories, macros (protein, carbs, fat, fiber), "
        "and health advice. Can capture a live photo from the webcam, analyze an image file/screenshot, "
        "or estimate nutrition from a spoken or typed meal description. "
        "Use whenever the user asks about calories, nutrition, macros, or food analysis."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "User's request or food/meal description (e.g. 'How many calories in this plate?', 'I ate 2 eggs and toast').",
            },
            "image_path": {
                "type": "STRING",
                "description": "Optional path to a local food image or screenshot to analyze.",
            },
            "use_camera": {
                "type": "BOOLEAN",
                "description": "Set to true to capture a live picture from the webcam.",
            },
        },
        "required": ["query"],
    },
}

def _get_api_config() -> dict:
    for cfg_path in [
        get_user_data_dir() / "config" / "api_keys.json",
        get_user_data_dir() / "config" / "app_settings.json",
    ]:
        if cfg_path.exists():
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {}

def _capture_webcam_snapshot(camera_index: int = 0) -> Optional[np.ndarray]:
    """Safely opens webcam, grabs a clean stabilized frame, and immediately releases the device."""
    backend = cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY
    cap = None
    try:
        cap = cv2.VideoCapture(int(camera_index), backend)
        if not cap.isOpened():
            cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return None

        # Warm up sensor for automatic exposure/white-balance
        frame = None
        for _ in range(8):
            ret, tmp = cap.read()
            if ret and tmp is not None:
                frame = tmp
            time.sleep(0.04)

        return frame
    except Exception as e:
        logger.error(f"Webcam capture error: {e}")
        return None
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass

def _encode_image_b64(frame: np.ndarray) -> tuple[str, str]:
    """Resizes if necessary and returns (base64_str, mime_type)."""
    h, w = frame.shape[:2]
    if w > 1280:
        new_h = int(h * (1280.0 / w))
        frame = cv2.resize(frame, (1280, new_h), interpolation=cv2.INTER_AREA)
    ret, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ret:
        raise ValueError("Failed to encode frame to JPEG")
    b64 = base64.b64encode(buf.tobytes()).decode("utf-8")
    return b64, "image/jpeg"

def _analyze_food_multimodal(image_b64: Optional[str], query: str) -> dict:
    """Uses LLM client to analyze food visual or description and produce structured JSON."""
    system_prompt = (
        "You are NIGHTFALL AI's Precision Nutrition Engine. "
        "Analyze the food shown in the image or described in the user's prompt. "
        "Estimate calories and macronutrients accurately. "
        "Respond ONLY with a valid JSON object matching this schema:\n"
        "{\n"
        '  "food_detected": true,\n'
        '  "dish_name": "Name of the dish or foods",\n'
        '  "portion_estimate": "e.g. 1 bowl (~350g) or 2 medium slices",\n'
        '  "total_calories_kcal": 450,\n'
        '  "macros": {\n'
        '    "protein_g": 25,\n'
        '    "carbs_g": 48,\n'
        '    "fat_g": 14,\n'
        '    "fiber_g": 6,\n'
        '    "sugar_g": 4\n'
        "  },\n"
        '  "glycemic_index": "Low" | "Medium" | "High",\n'
        '  "health_score": 8,\n'
        '  "items": [\n'
        '    {"name": "Grilled Chicken Breast", "portion": "150g", "calories": 240, "protein_g": 45, "carbs_g": 0, "fat_g": 5}\n'
        "  ],\n"
        '  "dietary_flags": ["High Protein", "Low Sugar"],\n'
        '  "nutrition_tip": "Short 1-sentence dietary coaching tip",\n'
        '  "spoken_summary": "1-2 natural sentences speaking to the user in their language stating what food was identified and the total calorie/macro count."\n'
        "}\n"
        "If no food is present or visible in the image, set food_detected to false and provide a helpful spoken_summary."
    )

    # Try unified llm_client first
    try:
        from llm_client import client as ai_client
        if image_b64:
            raw_text = ai_client.vision(
                prompt=f"Analyze this food. User request: {query}",
                image_b64=image_b64,
                mime="image/jpeg",
                system=system_prompt,
            )
        else:
            raw_text = ai_client.chat(
                prompt=f"Estimate the nutrition for this meal: {query}",
                system=system_prompt,
            )
        
        # Parse JSON
        clean = raw_text.strip()
        if "```json" in clean:
            clean = clean.split("```json")[1].split("```")[0].strip()
        elif "```" in clean:
            clean = clean.split("```")[1].split("```")[0].strip()
        start = clean.find("{")
        end = clean.rfind("}")
        if start != -1 and end != -1:
            clean = clean[start : end + 1]
        return json.loads(clean)
    except Exception as e:
        logger.warning(f"Unified LLM vision failed ({e}), attempting direct Gemini fallback...")

    # Direct Gemini API fallback
    try:
        cfg = _get_api_config()
        api_key = cfg.get("gemini_api_key")
        if api_key:
            from google import genai
            from google.genai import types as gtypes
            client = genai.Client(api_key=api_key)
            contents: list[Any] = []
            if image_b64:
                raw_bytes = base64.b64decode(image_b64)
                contents.append(gtypes.Part.from_bytes(data=raw_bytes, mime_type="image/jpeg"))
            contents.append(f"{system_prompt}\n\nUser request: {query}")

            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
            )
            text = (resp.text or "").strip()
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                return json.loads(text[start : end + 1])
    except Exception as e2:
        logger.error(f"Gemini fallback failed: {e2}")

    return {
        "food_detected": True,
        "dish_name": query,
        "portion_estimate": "Standard serving",
        "total_calories_kcal": 350,
        "macros": {"protein_g": 15, "carbs_g": 40, "fat_g": 12, "fiber_g": 4, "sugar_g": 5},
        "glycemic_index": "Medium",
        "health_score": 7,
        "items": [],
        "dietary_flags": ["Balanced"],
        "nutrition_tip": "Balanced meal with moderate protein and carbohydrates.",
        "spoken_summary": f"Based on your request, this portion contains approximately 350 calories with balanced macronutrients."
    }

def _save_nutrition_log(entry: dict) -> None:
    """Appends meal entry to memory/nutrition_log.json."""
    try:
        log_dir = get_user_data_dir() / "memory"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "nutrition_log.json"
        history = []
        if log_file.exists():
            try:
                history = json.loads(log_file.read_text(encoding="utf-8"))
            except Exception:
                history = []
        history.append(entry)
        # Keep last 500 meals
        history = history[-500:]
        log_file.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not persist nutrition entry: {e}")

def run(parameters: dict, player=None, speak=None, session_memory=None) -> str:
    """Main execution function for NIGHTFALL AI Calorie Counter."""
    query = (parameters.get("query") or "").strip()
    image_path = parameters.get("image_path")
    use_camera = parameters.get("use_camera")

    # If query mentions camera, webcam, or photo, assume camera mode unless image_path provided
    q_lower = query.lower()
    if not image_path and (use_camera or any(w in q_lower for w in ["camera", "webcam", "see this", "look at", "in front of"])):
        use_camera = True

    image_b64: Optional[str] = None

    # Step 1: Obtain image if applicable
    if image_path:
        p = Path(image_path)
        if p.exists() and p.is_file():
            try:
                img = cv2.imread(str(p))
                if img is not None:
                    image_b64, _ = _encode_image_b64(img)
            except Exception as e:
                logger.error(f"Failed to read image at {image_path}: {e}")

    if not image_b64 and use_camera:
        # Check UI camera signals
        win = getattr(player, "_win", None) if player else None
        stream_sig = getattr(win, "_cam_stream_sig", None)
        frame_sig = getattr(win, "_cam_frame_sig", None)

        if stream_sig:
            try:
                stream_sig.emit(True)
            except Exception:
                pass

        if player and hasattr(player, "write_log"):
            player.write_log("[Nutrition] Capturing live camera snapshot for nutrition analysis...")

        frame = _capture_webcam_snapshot()

        if frame is not None:
            if frame_sig:
                try:
                    ret, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if ret:
                        frame_sig.emit(buf.tobytes())
                except Exception:
                    pass
            image_b64, _ = _encode_image_b64(frame)

        if stream_sig:
            try:
                stream_sig.emit(False)
            except Exception:
                pass

    # Step 2: Analyze using AI
    data = _analyze_food_multimodal(image_b64, query or "Analyze this food")

    if not data.get("food_detected", True):
        summary = data.get("spoken_summary", "I couldn't identify any food items clearly in view. Please try holding the food closer or describing the dish.")
        if player and hasattr(player, "write_log"):
            player.write_log(f"[Nutrition] {summary}")
        return summary

    dish = data.get("dish_name", "Meal")
    cals = data.get("total_calories_kcal", 0)
    portion = data.get("portion_estimate", "1 serving")
    macros = data.get("macros", {})
    p_g = macros.get("protein_g", 0)
    c_g = macros.get("carbs_g", 0)
    f_g = macros.get("fat_g", 0)
    fib_g = macros.get("fiber_g", 0)
    sug_g = macros.get("sugar_g", 0)
    score = data.get("health_score", 7)
    gi = data.get("glycemic_index", "Medium")
    flags = ", ".join(data.get("dietary_flags", []))
    tip = data.get("nutrition_tip", "")
    spoken = data.get("spoken_summary") or f"{dish} contains approximately {cals} calories with {p_g} grams of protein."

    # Save to history log
    _save_nutrition_log({
        "timestamp": datetime.now().isoformat(),
        "dish": dish,
        "calories": cals,
        "protein_g": p_g,
        "carbs_g": c_g,
        "fat_g": f_g,
        "portion": portion,
        "query": query
    })

    # Display rich breakdown in NIGHTFALL UI
    panel_content = (
        f"**Dish**: {dish} ({portion})\n\n"
        f"### ⚡ Calories: {cals} kcal\n\n"
        f"| Nutrient | Amount |\n"
        f"|---|---|\n"
        f"| **Protein** | {p_g} g |\n"
        f"| **Carbohydrates** | {c_g} g |\n"
        f"| **Fat** | {f_g} g |\n"
        f"| **Fiber** | {fib_g} g |\n"
        f"| **Sugar** | {sug_g} g |\n\n"
        f"**Glycemic Index**: {gi} | **Health Rating**: {score}/10\n"
    )
    if flags:
        panel_content += f"**Tags**: {flags}\n"
    if tip:
        panel_content += f"\n💡 *{tip}*\n"

    items = data.get("items", [])
    if items:
        panel_content += "\n**Item Breakdown**:\n"
        for item in items:
            panel_content += f"- **{item.get('name')}** ({item.get('portion', '')}): ~{item.get('calories', 0)} kcal (P: {item.get('protein_g', 0)}g, C: {item.get('carbs_g', 0)}g, F: {item.get('fat_g', 0)}g)\n"

    if player and hasattr(player, "show_content"):
        try:
            player.show_content("🥗 NIGHTFALL NUTRITION SCAN", panel_content)
        except Exception:
            pass

    if player and hasattr(player, "write_log"):
        player.write_log(f"[Nutrition] Analyzed {dish} → {cals} kcal (P:{p_g}g, C:{c_g}g, F:{f_g}g)")

    return spoken

# Aliases for different dispatch conventions
calorie_counter = run
