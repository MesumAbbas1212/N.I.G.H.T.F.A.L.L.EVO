"""Resource-usage regressions: the app must stay light while it is idle.

Everything here is measured, not guessed:

* config/settings reads used to hit the disk on every call, and they are called
  several times per request (routing, providers, tools, Quick Actions);
* ``actions.screen_processor`` imported OpenCV, mss, PortAudio and numpy at
  module import, and ``main.py`` imports it at startup - a ~100 MB stake in
  memory (and noticeable startup time) for a feature that may never run;
* the scanning overlay repainted at 60 fps even while hidden.
"""

import importlib
import json
import sys
import time


# -- settings are read from disk once, not per call --------------------------

def test_load_settings_caches_and_invalidates(tmp_path, monkeypatch):
    from memory import config_manager

    settings_file = tmp_path / "app_settings.json"
    monkeypatch.setattr(config_manager, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(config_manager, "_ensure_config", lambda: None)
    config_manager.invalidate_settings_cache()

    settings_file.write_text(json.dumps({"default_ai_provider": "Gemini"}), encoding="utf-8")
    assert config_manager.load_settings()["default_ai_provider"] == "Gemini"

    # The cached copy is returned without touching the disk again.
    reads = []
    real_open = open

    def counting_open(path, *a, **k):
        reads.append(str(path))
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", counting_open)
    for _ in range(25):
        config_manager.load_settings()
    assert reads == [], f"settings were re-read {len(reads)} times"

    # ...but a change on disk is still picked up.
    time.sleep(0.01)
    settings_file.write_text(json.dumps({"default_ai_provider": "Groq"}), encoding="utf-8")
    assert config_manager.load_settings()["default_ai_provider"] == "Groq"


def test_saving_settings_updates_the_cache(tmp_path, monkeypatch):
    from memory import config_manager

    settings_file = tmp_path / "app_settings.json"
    monkeypatch.setattr(config_manager, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(config_manager, "_ensure_config", lambda: None)
    config_manager.invalidate_settings_cache()

    config_manager.save_settings({"jeff_routing_enabled": True})
    assert config_manager.load_settings()["jeff_routing_enabled"] is True


def test_quick_action_config_reads_are_cached(tmp_path, monkeypatch):
    from core import quick_actions as qa

    keys = tmp_path / "api_keys.json"
    keys.write_text(json.dumps({"gemini_api_key": "k"}), encoding="utf-8")
    monkeypatch.setattr(qa, "_config_dirs", lambda: [tmp_path])
    qa._config_cache.clear()

    assert qa._load_config("api_keys.json")["gemini_api_key"] == "k"

    reads = []
    real_read_text = type(keys).read_text

    def counting_read_text(self, *a, **k):
        reads.append(str(self))
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(type(keys), "read_text", counting_read_text)
    for _ in range(10):
        qa._load_config("api_keys.json")
    assert reads == [], f"config was re-read {len(reads)} times"

    # a modified file is noticed
    time.sleep(0.01)
    keys.write_text(json.dumps({"gemini_api_key": "k2"}), encoding="utf-8")
    assert qa._load_config("api_keys.json")["gemini_api_key"] == "k2"


# -- heavy modules stay out of startup ---------------------------------------

def test_screen_processor_does_not_import_the_vision_stack_at_startup():
    for name in ("cv2", "mss", "sounddevice", "numpy"):
        sys.modules.pop(name, None)
    sys.modules.pop("actions.screen_processor", None)

    importlib.import_module("actions.screen_processor")

    loaded = [name for name in ("cv2", "mss", "sounddevice", "numpy") if name in sys.modules]
    assert loaded == [], f"importing the module pulled in {loaded}"


def test_screen_processor_still_imports_its_stack_on_demand(monkeypatch):
    module = importlib.import_module("actions.screen_processor")

    captured = {}

    class FakeCV2:
        CAP_DSHOW = 700

        @staticmethod
        def VideoCapture(index, backend=None):
            captured["index"] = index
            return None

    monkeypatch.setattr(module, "_import_cv2", lambda: FakeCV2)
    monkeypatch.setattr(module, "_get_camera_index", lambda: 2)
    try:
        module._capture_camera()
    except Exception:
        pass
    assert captured.get("index") == 2, "the CV stack is imported where it is used"


# -- the scan animation stops when hidden ------------------------------------

def test_scanning_overlay_stops_its_timer_when_hidden():
    import inspect

    import ui

    source = inspect.getsource(ui.ScanningOverlay.hideEvent)
    assert "_tmr.stop()" in source
    show_source = inspect.getsource(ui.ScanningOverlay.show_fullscreen)
    assert "_tmr.start(16)" in show_source
