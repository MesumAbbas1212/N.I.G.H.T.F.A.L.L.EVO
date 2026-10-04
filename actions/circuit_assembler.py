"""
NIGHTFALL AI Evo - Circuit Assembler & Hardware Vision Architect.
Analyzes electronic components on screen or from voice input, resolves pin-to-pin wiring,
safety warnings, and assembly steps, and launches the Holographic Circuit HUD.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.user_paths import get_user_data_dir

logger = logging.getLogger("CircuitAssembler")

BASE_DIR = Path(__file__).resolve().parent.parent
API_CONFIG_PATH = get_user_data_dir() / "config" / "api_keys.json"

PLUGIN = {
    "name": "circuit_assembler",
    "description": (
        "Analyzes electronic components (Arduino, ESP32, sensors, actuators, resistors) on the user's screen "
        "or from text/voice, calculates pin-to-pin wiring diagrams, safety warnings, and step-by-step assembly guides, "
        "and launches the interactive Holographic Circuit HUD display."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "analyze_screen | assemble_components | show_schematic (default: assemble_components)",
            },
            "components": {
                "type": "STRING",
                "description": "Comma-separated list or description of electronic components (e.g. 'Arduino Pro Mini, DHT11, 10k resistor')",
            },
            "query": {
                "type": "STRING",
                "description": "Specific project goal or wiring instructions (e.g. 'how to connect humidity sensor to arduino')",
            },
        },
        "required": ["action"],
    },
}


# =============================================================================
# BUILT-IN HARDWARE PRESETS (Zero-latency offline blueprints)
# =============================================================================

PRESET_DHT11_ARDUINO_PRO_MINI = {
    "title": "Connecting DHT11 and Arduino Pro Mini",
    "description": (
        "To connect the DHT11 humidity and temperature sensor to the Arduino Pro Mini, you will need to wire "
        "power (VCC), ground (GND), and a data pin between the two boards. A pull-up resistor "
        "(typically 4.7kΩ to 10kΩ) is also usually required between the VCC and data lines of the DHT11."
    ),
    "components": [
        {
            "id": "arduino",
            "name": "Arduino Pro Mini",
            "subtitle": "Atmega328, 5V",
            "highlight": False,
            "left_pins": [],
            "right_pins": [
                {"name": "VCC", "badge": "1", "color": "#f97316"},
                {"name": "2", "badge": "2", "color": "#fbbf24"},
                {"name": "GND", "badge": "3", "color": "#f8fafc"},
            ],
        },
        {
            "id": "dht11",
            "name": "Humidity Sensor",
            "subtitle": "DHT11 type",
            "highlight": True,
            "left_pins": [
                {"name": "VCC", "color": "#f97316"},
                {"name": "DATA", "color": "#fbbf24"},
                {"name": "GND", "color": "#f8fafc"},
            ],
            "right_pins": [
                {"name": "VCC", "badge": "4", "color": "#06b6d4"},
                {"name": "DATA", "badge": "5", "color": "#22c55e"},
            ],
        },
        {
            "id": "resistor",
            "name": "Resistor",
            "subtitle": "4.7kΩ to 10kΩ",
            "highlight": False,
            "left_pins": [
                {"name": "p8", "color": "#06b6d4"},
                {"name": "p9", "color": "#22c55e"},
            ],
            "right_pins": [],
        },
    ],
    "wires": [
        {"from": "arduino:right:VCC", "to": "dht11:left:VCC", "color": "#f97316", "label": "Power", "step": 1},
        {"from": "arduino:right:2", "to": "dht11:left:DATA", "color": "#fbbf24", "label": "Data (Pin 2)", "step": 3},
        {"from": "arduino:right:GND", "to": "dht11:left:GND", "color": "#f8fafc", "label": "Ground", "step": 2},
        {"from": "dht11:right:VCC", "to": "resistor:left:p8", "color": "#06b6d4", "label": "Pull-up VCC", "step": 4},
        {"from": "dht11:right:DATA", "to": "resistor:left:p9", "color": "#22c55e", "label": "Pull-up DATA", "step": 4},
    ],
    "warnings": [
        "Verify the pinout of your specific DHT11 module, as pin arrangements can vary.",
        "Using 5V on a 3.3V Arduino Pro Mini model will damage it. Ensure your board voltages match.",
    ],
    "steps": [
        "Connect the DHT11 VCC pin to the Arduino Pro Mini VCC pin. [1]",
        "Connect the DHT11 GND pin to the Arduino Pro Mini GND pin. [3]",
        "Connect the DHT11 DATA pin to Arduino Pro Mini digital pin 2. [2]",
        "Place a pull-up resistor (e.g., 4.7kΩ) between the DHT11 VCC and DATA pins. [4, 5]",
    ],
    "arduino_code": """#include "DHT.h"

#define DHTPIN 2
#define DHTTYPE DHT11

DHT dht(DHTPIN, DHTTYPE);

void setup() {
  Serial.begin(9600);
  Serial.println("DHT11 Test Started!");
  dht.begin();
}

void loop() {
  delay(2000);
  float humidity = dht.readHumidity();
  float temperature = dht.readTemperature();

  if (isnan(humidity) || isnan(temperature)) {
    Serial.println("Failed to read from DHT sensor!");
    return;
  }

  Serial.print("Humidity: ");
  Serial.print(humidity);
  Serial.print("%  Temperature: ");
  Serial.print(temperature);
  Serial.println("°C");
}
""",
}

PRESET_ULTRASONIC_ARDUINO_UNO = {
    "title": "Connecting HC-SR04 Ultrasonic Sensor to Arduino Uno",
    "description": (
        "Wiring an HC-SR04 ultrasonic distance sensor to an Arduino Uno. The sensor uses sonar to measure "
        "distance from 2cm to 400cm with 3mm accuracy."
    ),
    "components": [
        {
            "id": "arduino",
            "name": "Arduino Uno R3",
            "subtitle": "ATmega328P, 5V",
            "left_pins": [],
            "right_pins": [
                {"name": "5V", "badge": "1", "color": "#f97316"},
                {"name": "GND", "badge": "2", "color": "#f8fafc"},
                {"name": "9", "badge": "3", "color": "#fbbf24"},
                {"name": "10", "badge": "4", "color": "#38bdf8"},
            ],
        },
        {
            "id": "sonar",
            "name": "Ultrasonic HC-SR04",
            "subtitle": "Sonar Rangefinder (5V)",
            "left_pins": [
                {"name": "VCC", "color": "#f97316"},
                {"name": "GND", "color": "#f8fafc"},
                {"name": "TRIG", "color": "#fbbf24"},
                {"name": "ECHO", "color": "#38bdf8"},
            ],
            "right_pins": [],
        },
    ],
    "wires": [
        {"from": "arduino:5V", "to": "sonar:VCC", "color": "#f97316", "label": "5V Power", "step": 1},
        {"from": "arduino:GND", "to": "sonar:GND", "color": "#f8fafc", "label": "Ground", "step": 2},
        {"from": "arduino:9", "to": "sonar:TRIG", "color": "#fbbf24", "label": "Trigger (Pin 9)", "step": 3},
        {"from": "arduino:10", "to": "sonar:ECHO", "color": "#38bdf8", "label": "Echo (Pin 10)", "step": 4},
    ],
    "warnings": [
        "HC-SR04 operates at 5V logic. If interfacing with ESP32 or Raspberry Pi, use a 1k/2k voltage divider on the ECHO pin to step 5V down to 3.3V.",
        "Ensure power supply can provide sufficient current (sensor draws ~15mA).",
    ],
    "steps": [
        "Connect HC-SR04 VCC pin to Arduino Uno 5V pin. [1]",
        "Connect HC-SR04 GND pin to Arduino Uno GND pin. [2]",
        "Connect HC-SR04 TRIG pin to Arduino Uno digital pin 9. [3]",
        "Connect HC-SR04 ECHO pin to Arduino Uno digital pin 10. [4]",
    ],
    "arduino_code": """const int trigPin = 9;
const int echoPin = 10;

void setup() {
  Serial.begin(9600);
  pinMode(trigPin, OUTPUT);
  pinMode(echoPin, INPUT);
}

void loop() {
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  long duration = pulseIn(echoPin, HIGH);
  float distanceCm = duration * 0.034 / 2.0;

  Serial.print("Distance: ");
  Serial.print(distanceCm);
  Serial.println(" cm");
  delay(250);
}
""",
}

PRESET_SERVO_ARDUINO = {
    "title": "Connecting SG90 Micro Servo to Arduino Uno",
    "description": (
        "Connecting an SG90 9g micro servo to an Arduino. Control pulse-width modulation (PWM) "
        "allows 0° to 180° rotation angle positioning."
    ),
    "components": [
        {
            "id": "arduino",
            "name": "Arduino Uno R3",
            "subtitle": "ATmega328P, 5V",
            "left_pins": [],
            "right_pins": [
                {"name": "5V", "badge": "1", "color": "#f97316"},
                {"name": "GND", "badge": "2", "color": "#f8fafc"},
                {"name": "9", "badge": "3", "color": "#fbbf24"},
            ],
        },
        {
            "id": "servo",
            "name": "SG90 Micro Servo",
            "subtitle": "TowerPro 9g (4.8V - 6V)",
            "left_pins": [
                {"name": "RED (VCC)", "color": "#f97316"},
                {"name": "BROWN (GND)", "color": "#f8fafc"},
                {"name": "ORANGE (SIG)", "color": "#fbbf24"},
            ],
            "right_pins": [],
        },
    ],
    "wires": [
        {"from": "arduino:5V", "to": "servo:RED (VCC)", "color": "#f97316", "label": "5V Power (Red)", "step": 1},
        {"from": "arduino:GND", "to": "servo:BROWN (GND)", "color": "#f8fafc", "label": "Ground (Brown)", "step": 2},
        {"from": "arduino:9", "to": "servo:ORANGE (SIG)", "color": "#fbbf24", "label": "PWM Signal (Pin 9)", "step": 3},
    ],
    "warnings": [
        "Do not power more than 1 servo directly from the Arduino 5V pin. Multiple servos cause voltage dips and resets; use an external 5V 2A power supply with shared ground.",
        "Servo wire colors: Red = VCC (Power), Brown/Black = GND, Orange/Yellow = PWM Signal.",
    ],
    "steps": [
        "Connect the RED wire of the servo to Arduino 5V. [1]",
        "Connect the BROWN wire of the servo to Arduino GND. [2]",
        "Connect the ORANGE wire of the servo to Arduino PWM digital pin 9. [3]",
    ],
    "arduino_code": """#include <Servo.h>

Servo myServo;

void setup() {
  myServo.attach(9);
}

void loop() {
  // Sweep from 0 to 180 degrees
  for (int pos = 0; pos <= 180; pos += 1) {
    myServo.write(pos);
    delay(15);
  }
  // Sweep back from 180 to 0 degrees
  for (int pos = 180; pos >= 0; pos -= 1) {
    myServo.write(pos);
    delay(15);
  }
}
""",
}


# =============================================================================
# SCREEN CAPTURE & VISION HELPER
# =============================================================================

def capture_screen_image() -> Optional[bytes]:
    """Captures the current desktop screen and returns JPEG bytes."""
    try:
        import mss
        import mss.tools
        import io
        from PIL import Image

        with mss.mss() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            sct_img = sct.grab(monitor)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            
            # Resize if too large for vision token efficiency
            max_w = 1280
            if img.width > max_w:
                ratio = max_w / float(img.width)
                new_h = int(img.height * ratio)
                img = img.resize((max_w, new_h), Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
    except Exception as e:
        logger.error(f"[CircuitAssembler] Screen capture failed: {e}")
        return None


def get_api_key() -> str:
    """Loads Gemini API key from user config."""
    try:
        if API_CONFIG_PATH.exists():
            with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
                return d.get("gemini_api_key", "").strip()
    except Exception:
        pass
    return os.environ.get("GEMINI_API_KEY", "")


def solve_circuit_with_ai(prompt: str, image_bytes: Optional[bytes] = None) -> Optional[Dict[str, Any]]:
    """
    Uses Gemini 2.5 Flash multimodal intelligence to resolve component pinouts,
    wiring paths, safety checks, and step-by-step instructions.
    """
    api_key = get_api_key()
    if not api_key:
        logger.warning("[CircuitAssembler] No Gemini API key found.")
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        system_instruction = (
            "You are NIGHTFALL Circuit Architect, an expert electrical engineer and embedded systems designer. "
            "Your task is to analyze electronic components (either identified from the screen image or the user's description) "
            "and generate a complete, safe, and accurate wiring schematic matching the NIGHTFALL Circuit HUD JSON schema. "
            "You MUST output valid, raw JSON ONLY (no markdown formatting, no backticks, no comments). "
            "The JSON structure must be: "
            "{\n"
            '  "title": "Connecting DHT11 and Arduino Pro Mini",\n'
            '  "description": "Clear technical overview of the circuit and function",\n'
            '  "components": [\n'
            '    {\n'
            '      "id": "arduino",\n'
            '      "name": "Arduino Pro Mini",\n'
            '      "subtitle": "Atmega328, 5V",\n'
            '      "left_pins": [],\n'
            '      "right_pins": [\n'
            '        {"name": "VCC", "badge": "1", "color": "#f97316"},\n'
            '        {"name": "2", "badge": "2", "color": "#fbbf24"},\n'
            '        {"name": "GND", "badge": "3", "color": "#f8fafc"}\n'
            '      ]\n'
            '    }\n'
            '  ],\n'
            '  "wires": [\n'
            '    {"from": "arduino:VCC", "to": "dht11:VCC", "color": "#f97316", "label": "Power (5V)", "step": 1}\n'
            '  ],\n'
            '  "warnings": ["Verify 5V vs 3.3V compatibility..."],\n'
            '  "steps": ["Connect DHT11 VCC to Arduino VCC. [1]"],\n'
            '  "arduino_code": "// Full working Arduino C++ sketch\\nvoid setup() {...}\\nvoid loop() {...}"\n'
            "}"
        )

        contents = []
        if image_bytes:
            contents.append(
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
            )
            contents.append(
                f"Identify the electronic parts on my screen and tell me how to assemble them. Query: {prompt or 'assemble these parts'}"
            )
        else:
            contents.append(
                f"Design the wiring diagram and assembly instructions for these parts: {prompt}"
            )

        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.2,
                response_mime_type="application/json",
            ),
        )

        raw = (resp.text or "").strip()
        # Clean any markdown if model wrapped it
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"```$", "", raw, flags=re.MULTILINE).strip()
        data = json.loads(raw)
        return data
    except Exception as e:
        logger.error(f"[CircuitAssembler] AI circuit solver failed: {e}")
        return None


# =============================================================================
# MAIN CONTROLLER FUNCTION
# =============================================================================

def circuit_assembler(
    action: str = "assemble_components",
    components: str = "",
    query: str = "",
    parameters: Optional[Dict[str, Any]] = None,
    player: Any = None,
    speak: Any = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Primary tool entry point:
    1. Checks presets for instant match.
    2. If screen analysis requested or components on screen, captures display and solves with vision.
    3. Solves circuit and launches the Holographic Circuit HUD.
    """
    if parameters and isinstance(parameters, dict):
        action = parameters.get("action", action)
        components = parameters.get("components", components)
        query = parameters.get("query", query)
        if not player and "player" in parameters:
            player = parameters["player"]
        if not speak and "speak" in parameters:
            speak = parameters["speak"]

    clean_action = (action or "assemble_components").strip().lower()
    full_prompt = f"{components} {query}".strip().lower()

    circuit_data = None

    # 1. Check for quick preset matches
    if "dht11" in full_prompt or ("humidity" in full_prompt and "temp" in full_prompt):
        circuit_data = PRESET_DHT11_ARDUINO_PRO_MINI
    elif "ultrasonic" in full_prompt or "hc-sr04" in full_prompt or "distance sensor" in full_prompt:
        circuit_data = PRESET_ULTRASONIC_ARDUINO_UNO
    elif "servo" in full_prompt or "sg90" in full_prompt:
        circuit_data = PRESET_SERVO_ARDUINO

    # 2. If screen analysis requested or no preset matched
    if not circuit_data or clean_action == "analyze_screen" or "screen" in full_prompt:
        img_bytes = None
        if clean_action == "analyze_screen" or "screen" in full_prompt or "see" in full_prompt or "look" in full_prompt:
            logger.info("[CircuitAssembler] Capturing desktop screen for hardware vision analysis...")
            img_bytes = capture_screen_image()

        solved = solve_circuit_with_ai(prompt=components or query or full_prompt, image_bytes=img_bytes)
        if solved and solved.get("components") and solved.get("wires"):
            circuit_data = solved

    # Fallback to default if AI unavailable
    if not circuit_data:
        circuit_data = PRESET_DHT11_ARDUINO_PRO_MINI

    # 3. Launch HUD Window on UI thread
    try:
        dispatched = False
        if player and hasattr(player, "show_circuit_hud"):
            player.show_circuit_hud(circuit_data)
            dispatched = True
        elif player and hasattr(player, "_win") and hasattr(player._win, "_circuit_hud_sig"):
            player._win._circuit_hud_sig.emit(circuit_data)
            dispatched = True
        else:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                for w in app.topLevelWidgets():
                    if hasattr(w, "_circuit_hud_sig"):
                        w._circuit_hud_sig.emit(circuit_data)
                        dispatched = True
                        break
        if not dispatched:
            from core.circuit_hud import show_circuit_schematic
            show_circuit_schematic(circuit_data)
    except Exception as e:
        logger.error(f"[CircuitAssembler] Failed to display Circuit HUD: {e}")

    # Build concise text summary for conversational agent
    title = circuit_data.get("title", "Circuit Assembly")
    steps_count = len(circuit_data.get("steps", []))
    wires_count = len(circuit_data.get("wires", []))
    summary_lines = [
        f"⚡ {title}",
        f"Generated interactive schematic with {len(circuit_data.get('components', []))} components and {wires_count} wires across {steps_count} assembly steps.",
    ]
    if circuit_data.get("warnings"):
        summary_lines.append(f"⚠️ Safety Note: {circuit_data['warnings'][0]}")

    # Build deliverable HTML file
    from core.circuit_hud import OUTPUT_DIR, generate_circuit_html
    html_file = OUTPUT_DIR / "circuit_schematic.html"
    try:
        html_file.write_text(generate_circuit_html(circuit_data), encoding="utf-8")
    except Exception:
        pass

    return {
        "status": "success",
        "title": title,
        "circuit": circuit_data,
        "file_path": str(html_file.resolve()),
        "deliverable": str(html_file.resolve()),
        "summary": "\n".join(summary_lines),
    }


def launch_circuit_hud_from_screen(query: str = "") -> Dict[str, Any]:
    """Helper shortcut to analyze screen and launch circuit HUD."""
    return circuit_assembler(action="analyze_screen", query=query)
