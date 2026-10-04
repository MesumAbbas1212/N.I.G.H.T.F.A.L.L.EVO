"""
NIGHTFALL Passive Sensorium Engine (v2)
An omnipresent background telemetry and perception daemon.
Continuously senses user context, active task dwell time, system vitals,
and user idle state with zero performance overhead.
"""

import sys
import time
import threading
import ctypes
from ctypes import wintypes
from typing import Dict, Any, List, Optional, Callable
import psutil

from core.window_context import get_foreground_window_info

user32 = ctypes.windll.user32 if sys.platform == "win32" else None
kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


def get_idle_time_seconds() -> float:
    """Returns the number of seconds since the user last touched mouse or keyboard."""
    if not user32 or not kernel32:
        return 0.0
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if user32.GetLastInputInfo(ctypes.byref(lii)):
        millis = kernel32.GetTickCount() - lii.dwTime
        return max(0.0, millis / 1000.0)
    return 0.0


class PassiveSensorium:
    def __init__(self, poll_interval: float = 3.0):
        self.poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Telemetry State
        self.current_window: Dict[str, Any] = {}
        self.current_process_name: str = "Unknown"
        self.dwell_start_time: float = time.time()
        self.current_dwell_seconds: float = 0.0
        self.user_idle_seconds: float = 0.0

        # System Vitals
        self.cpu_percent: float = 0.0
        self.ram_percent: float = 0.0
        self.battery_percent: Optional[float] = None
        self.power_plugged: Optional[bool] = None

        # Observers & Interjection Callbacks
        self._interjection_callbacks: List[Callable[[str, Dict[str, Any]], None]] = []
        self._last_alert_time: Dict[str, float] = {}

    def register_interjection_handler(self, callback: Callable[[str, Dict[str, Any]], None]):
        """Register a callback for autonomous interjections: callback(alert_type, metadata)"""
        self._interjection_callbacks.append(callback)

    def start(self):
        """Starts the background sensorium loop."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="NIGHTFALLSensorium", daemon=True)
        self._thread.start()

    def stop(self):
        """Stops the sensorium loop."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def get_snapshot(self) -> Dict[str, Any]:
        """Returns the full active situational context of the user and machine."""
        return {
            "window_title": self.current_window.get("title", ""),
            "process_name": self.current_process_name,
            "dwell_seconds": round(self.current_dwell_seconds, 1),
            "user_idle_seconds": round(self.user_idle_seconds, 1),
            "cpu_percent": self.cpu_percent,
            "ram_percent": self.ram_percent,
            "battery_percent": self.battery_percent,
            "power_plugged": self.power_plugged,
            "is_user_active": self.user_idle_seconds < 60.0,
            "timestamp": time.time(),
        }

    def _loop(self):
        while self._running:
            try:
                self._update_telemetry()
                self._evaluate_heuristics()
            except Exception as e:
                # Sensorium must never crash the host process
                pass
            time.sleep(self.poll_interval)

    def _update_telemetry(self):
        # 1. Window & Process
        win_info = get_foreground_window_info()
        pid = win_info.get("pid")
        title = win_info.get("title", "")

        proc_name = "Unknown"
        if pid:
            try:
                proc = psutil.Process(pid)
                proc_name = proc.name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # Dwell time tracking
        if title != self.current_window.get("title", "") or proc_name != self.current_process_name:
            self.current_window = win_info
            self.current_process_name = proc_name
            self.dwell_start_time = time.time()
            self.current_dwell_seconds = 0.0
        else:
            self.current_dwell_seconds = time.time() - self.dwell_start_time

        # 2. Idle State
        self.user_idle_seconds = get_idle_time_seconds()

        # 3. System Vitals
        self.cpu_percent = psutil.cpu_percent(interval=None)
        self.ram_percent = psutil.virtual_memory().percent
        battery = psutil.sensors_battery()
        if battery:
            self.battery_percent = battery.percent
            self.power_plugged = battery.power_plugged
        else:
            self.battery_percent = None
            self.power_plugged = None

    def _evaluate_heuristics(self):
        now = time.time()

        # Alert: Severe RAM Pressure (> 92% sustained)
        if self.ram_percent > 92.0:
            if now - self._last_alert_time.get("ram_critical", 0) > 300:  # 5 min cooldown
                self._last_alert_time["ram_critical"] = now
                self._emit_alert(
                    "ram_critical",
                    {
                        "message": f"System memory threshold at {self.ram_percent}% capacity.",
                        "speech": f"Warning: system memory has reached {int(self.ram_percent)} percent capacity.",
                        "ram_percent": self.ram_percent,
                    },
                )

        # Alert: Battery Low (< 15% unplugged)
        if self.battery_percent is not None and not self.power_plugged and self.battery_percent < 15.0:
            if now - self._last_alert_time.get("battery_critical", 0) > 600:  # 10 min cooldown
                self._last_alert_time["battery_critical"] = now
                self._emit_alert(
                    "battery_critical",
                    {
                        "message": f"Battery critically low at {self.battery_percent}%.",
                        "speech": f"Sir, your battery is down to {int(self.battery_percent)} percent. Please connect a power source.",
                        "battery_percent": self.battery_percent,
                    },
                )

        # Alert: Deep Work Strain (Continuous active work on single task > 90 mins)
        if self.current_dwell_seconds > 5400 and self.user_idle_seconds < 120:
            if now - self._last_alert_time.get("flow_strain", 0) > 3600:  # 60 min cooldown
                self._last_alert_time["flow_strain"] = now
                self._emit_alert(
                    "flow_strain",
                    {
                        "message": f"You've been focused on {self.current_process_name} for over 90 minutes.",
                        "speech": f"Sir, you have been continuously working on {self.current_process_name} for over 90 minutes. I recommend saving your session and taking a short break.",
                        "task": self.current_process_name,
                        "dwell_seconds": self.current_dwell_seconds,
                    },
                )

        # Proactive: User Return after extended absence (> 5 mins away)
        if hasattr(self, "_was_away") and self._was_away and self.user_idle_seconds < 2.0:
            self._was_away = False
            if now - self._last_alert_time.get("welcome_back", 0) > 600:
                self._last_alert_time["welcome_back"] = now
                self._emit_alert(
                    "welcome_back",
                    {
                        "message": f"User returned to workspace ({self.current_process_name}).",
                        "speech": "Welcome back, sir. Your workspace is active and standing by.",
                        "task": self.current_process_name,
                    },
                )
        elif self.user_idle_seconds > 300:
            self._was_away = True

    def _emit_alert(self, alert_type: str, metadata: Dict[str, Any]):
        for cb in self._interjection_callbacks:
            try:
                cb(alert_type, metadata)
            except Exception:
                pass


# Global singleton daemon instance
sensorium = PassiveSensorium()
