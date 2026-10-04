"""
Clipboard Sentry for NIGHTFALL AI.
Monitors the Windows clipboard in the background for actionable technical content:
- Tracebacks / Exceptions
- JSON structures
- URLs or SQL queries
- Code snippets
Notifies NIGHTFALL so it can offer quick contextual assistance.
"""

import time
import threading
import json
import re
from typing import Optional, Callable


class ClipboardSentry:
    def __init__(self, callback: Optional[Callable[[str, str], None]] = None):
        self._callback = callback
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_clip = ""

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _classify_content(self, text: str) -> Optional[str]:
        text = text.strip()
        if len(text) < 15:
            return None

        # Check for python / node / java traceback
        if "Traceback (most recent call last):" in text or "Exception:" in text or "Error:" in text and "\n" in text:
            return "error_traceback"

        # Check for JSON
        if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
            try:
                json.loads(text)
                return "json_data"
            except Exception:
                pass

        # Check for SQL queries
        if re.search(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\s+", text, re.IGNORECASE):
            return "sql_query"

        # Check for code blocks
        if any(keyword in text for keyword in ["def ", "class ", "function ", "import ", "const ", "let ", "var "]) and "\n" in text:
            return "code_snippet"

        return None

    def _monitor_loop(self):
        import pyperclip
        try:
            self._last_clip = (pyperclip.paste() or "").strip()
        except Exception:
            self._last_clip = ""

        while self._running:
            time.sleep(1.2)
            try:
                current = (pyperclip.paste() or "").strip()
                if current and current != self._last_clip:
                    self._last_clip = current
                    category = self._classify_content(current)
                    if category and self._callback:
                        self._callback(category, current)
            except Exception:
                pass
