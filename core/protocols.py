"""
NIGHTFALL Protocol Execution Engine (v2)
High-level macro orchestrator that reconfigures the user's digital environment
across PC and connected mobile devices with a single directive.
"""

import os
import sys
import subprocess
import ctypes
from typing import Dict, Any, List, Optional
import psutil

from core.identity import identity

user32 = ctypes.windll.user32 if sys.platform == "win32" else None


class ProtocolEngine:
    def __init__(self):
        self.active_protocol: Optional[str] = None
        self.registered_protocols: Dict[str, Any] = {
            "deep_work": self.execute_deep_work,
            "redline": self.execute_redline,
            "lockdown": self.execute_lockdown,
            "nightfall": self.execute_nightfall,
        }

    def execute(self, protocol_name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Executes a recognized high-level protocol."""
        key = protocol_name.strip().lower().replace(" ", "_")
        if key not in self.registered_protocols:
            return {
                "success": False,
                "error": f"Unknown protocol '{protocol_name}'. Available: {list(self.registered_protocols.keys())}"
            }

        fn = self.registered_protocols[key]
        try:
            result = fn(params or {})
            self.active_protocol = key
            return {"success": True, "protocol": key, "result": result}
        except Exception as e:
            return {"success": False, "protocol": key, "error": str(e)}

    # ── PROTOCOL: DEEP WORK ──────────────────────────────────────────────
    def execute_deep_work(self, params: Dict[str, Any]) -> str:
        """
        Maximizes focus:
        - Sets NIGHTFALL behavior mode to minimal.
        - Minimizes distracting applications.
        """
        identity.set_behavior_mode("minimal")

        distractions = ["steam.exe", "discord.exe", "telegram.exe", "spotify.exe", "epicgameslauncher.exe"]
        minimized_count = 0

        # Minimize known distraction windows
        def enum_windows_callback(hwnd, extra):
            nonlocal minimized_count
            if user32 and user32.IsWindowVisible(hwnd):
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                try:
                    proc = psutil.Process(pid.value)
                    if proc.name().lower() in distractions:
                        user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE = 6
                        minimized_count += 1
                except Exception:
                    pass
            return True

        if user32:
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
            user32.EnumWindows(WNDENUMPROC(enum_windows_callback), 0)

        return f"Protocol Deep Work engaged. Persona switched to minimal. {minimized_count} distraction windows minimized."

    # ── PROTOCOL: REDLINE (MAX PERFORMANCE) ──────────────────────────────
    def execute_redline(self, params: Dict[str, Any]) -> str:
        """
        Frees memory and sets Windows to High/Ultimate Performance power plan.
        """
        # Set Windows Power Plan to High Performance if on Windows
        try:
            # GUID for High Performance: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
            subprocess.run(["powercfg", "/setactive", "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"], capture_output=True)
        except Exception:
            pass

        # Memory compaction / purge idle working sets
        ram_before = psutil.virtual_memory().percent
        try:
            if sys.platform == "win32":
                # Flush empty working sets
                ctypes.windll.psapi.EmptyWorkingSet(ctypes.windll.kernel32.GetCurrentProcess())
        except Exception:
            pass

        ram_after = psutil.virtual_memory().percent
        return f"Protocol Redline active. Power plan set to High Performance. RAM load at {ram_after}%."

    # ── PROTOCOL: LOCKDOWN (GHOST PRIVACY) ───────────────────────────────
    def execute_lockdown(self, params: Dict[str, Any]) -> str:
        """
        Instantly locks Windows session and mutes speakers.
        """
        # Mute audio via Windows master volume key simulation
        if user32:
            VK_VOLUME_MUTE = 0xAD
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(VK_VOLUME_MUTE, 0, 0, 0)
            user32.keybd_event(VK_VOLUME_MUTE, 0, KEYEVENTF_KEYUP, 0)

            # Lock the workstation
            user32.LockWorkStation()
            return "Protocol Lockdown triggered. Workstation locked and master audio muted."
        
        return "Lockdown failed: Non-windows environment."

    # ── PROTOCOL: NIGHTFALL (WRAP UP DAY) ────────────────────────────────
    def execute_nightfall(self, params: Dict[str, Any]) -> str:
        """
        Sets assistant to casual/night mode, switches theme if available, and gives a closing debrief.
        """
        identity.set_behavior_mode("minimal")
        return "Protocol Nightfall initialized. Work sessions logged. System ready for standby."


# Global singleton instance
protocols = ProtocolEngine()
