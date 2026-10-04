"""
Active Window & Foreground Application Context for NIGHTFALL AI.
Uses Windows Win32 APIs via ctypes to inspect the user's current focused application,
window title, and capture targeted screenshots of the active workspace.
"""

import sys
import ctypes
from ctypes import wintypes
from typing import Optional, Dict, Any

user32 = ctypes.windll.user32 if sys.platform == "win32" else None
kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def get_foreground_window_info() -> Dict[str, Any]:
    """
    Returns metadata about the currently active foreground window:
    - title: Window title string
    - class_name: Executable or window class name
    - hwnd: Window handle
    - rect: (x, y, w, h)
    """
    if not user32:
        return {"title": "", "class_name": "unknown", "hwnd": 0, "rect": (0, 0, 0, 0)}

    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {"title": "", "class_name": "", "hwnd": 0, "rect": (0, 0, 0, 0)}

        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            title = buff.value.strip()

        class_buff = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buff, 256)
        class_name = class_buff.value.strip()

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = max(0, rect.right - rect.left)
        h = max(0, rect.bottom - rect.top)

        return {
            "title": title,
            "class_name": class_name,
            "pid": pid.value,
            "hwnd": hwnd,
            "rect": (rect.left, rect.top, w, h),
            "bbox": (rect.left, rect.top, rect.right, rect.bottom),
        }
    except Exception as exc:
        return {"title": "", "class_name": "error", "error": str(exc), "rect": (0, 0, 0, 0)}


def capture_active_window_screenshot() -> Optional[bytes]:
    """
    Captures a high-resolution JPEG screenshot of only the current foreground window.
    """
    info = get_foreground_window_info()
    bbox = info.get("bbox")
    if not bbox or info.get("rect", (0, 0, 0, 0))[2] < 10:
        return None

    try:
        from PIL import ImageGrab
        import io
        img = ImageGrab.grab(bbox=bbox)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return None
