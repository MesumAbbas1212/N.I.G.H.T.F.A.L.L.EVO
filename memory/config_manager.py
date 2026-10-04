"""
memory/config_manager.py - Centralized configuration access for NIGHTFALL AI.
Handles persistent app settings, audio device selection, push-to-talk,
and AI options. Backed by config/app_settings.json.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict

from core.user_paths import get_user_data_dir

BSE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = get_user_data_dir() / "config"
SETTINGS_FILE = CONFIG_DIR / "app_settings.json"



def _ensure_config() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


#: Settings are read on nearly every request (routing, providers, tools), and
#: each read used to hit the disk. They change only when this module saves
#: them, so one parsed copy is cached until the file's mtime/size moves.
_settings_cache: Dict[str, Any] = {}
_settings_stamp: tuple = ()


def _settings_file_stamp() -> tuple:
    try:
        stat = SETTINGS_FILE.stat()
        return (stat.st_mtime_ns, stat.st_size)
    except Exception:
        return ()


def invalidate_settings_cache() -> None:
    global _settings_stamp
    _settings_cache.clear()
    _settings_stamp = ()


def load_settings() -> Dict[str, Any]:
    global _settings_cache, _settings_stamp
    _ensure_config()
    stamp = _settings_file_stamp()
    if stamp and stamp == _settings_stamp and _settings_cache:
        return dict(_settings_cache)
    if not SETTINGS_FILE.exists():
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if isinstance(data, dict):
        _settings_cache = data
        _settings_stamp = stamp
        return dict(data)
    return {}


def save_settings(data: Dict[str, Any]) -> None:
    _ensure_config()
    current = load_settings()
    current.update(data)
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=4)
    except Exception as e:
        print(f"[CONFIG] Error saving settings: {e}")
        return
    invalidate_settings_cache()


def get_setting(key: str, default: Any = None) -> Any:
    return load_settings().get(key, default)


def set_setting(key: str, value: Any) -> None:
    save_settings({key: value})


# --- Audio Devices
 

def get_input_device() -> str:
    return str(get_setting("input_device", "") or "")


def set_input_device(name: str) -> None:
    set_setting("input_device", name)


def get_output_device() -> str:
    return str(get_setting("output_device", "") or "")


def set_output_device(name: str) -> None:
    set_setting("output_device", name)


# --- Push-to-Talk

def get_push_to_talk_enabled() -> bool:
    return bool(get_setting("push_to_talk_enabled", False))


def set_push_to_talk_enabled(enabled: bool) -> None:
    set_setting("push_to_talk_enabled", bool(enabled))


# --- Wake Word & Briefing

def get_wake_word_enabled() -> bool:
    return bool(get_setting("wake_word_enabled", True))


def save_wake_word_enabled(enabled: bool) -> None:
    set_setting("wake_word_enabled", bool(enabled))


def get_brief_enabled() -> bool:
    return bool(get_setting("brief_enabled", True))
