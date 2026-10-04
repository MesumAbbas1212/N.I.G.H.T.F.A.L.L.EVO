from core.user_paths import get_user_data_dir
"""
NIGHTFALL AI — Workout & Exercise Tracker.

Tracks repetitions, posture, pace, and caloric burn for pushups, squats,
and other bodyweight exercises. Uses computer vision (MediaPipe pose tracking
with CV motion-flow fallback) via the webcam, displaying live rep counters
and form feedback on the NIGHTFALL HUD.

Logs workout sessions to memory/workout_history.json.
"""

import json
import logging
import math
import platform
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

logger = logging.getLogger("pushup_counter")

def _get_base_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR = _get_base_dir()

PLUGIN = {
    "name": "pushup_counter",
    "description": (
        "Counts repetitions and tracks workout form live through the WEBCAM or timer. "
        "Supports pushups, squats, and bodyweight exercises. Tracks reps, sets, tempo, "
        "and estimated calories burned. Use whenever the user says they want to do pushups, "
        "squats, or work out (e.g. 'count my pushups', 'track my workout', 'I will do 20 pushups')."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "The user's request (e.g. 'count my pushups', 'track 15 squats').",
            },
            "exercise": {
                "type": "STRING",
                "description": "Exercise type: 'pushups', 'squats', 'general' (defaults to 'pushups').",
            },
            "target": {
                "type": "INTEGER",
                "description": "Target rep goal if mentioned (e.g. 20).",
            },
        },
        "required": ["query"],
    },
}

# NIGHTFALL UI Colors (BGR for OpenCV)
_COLOR_CYAN   = (255, 200, 50)     # NIGHTFALL Cyan/Electric Blue
_COLOR_GOLD   = (50, 215, 255)     # NIGHTFALL Gold/Amber
_COLOR_WHITE  = (255, 255, 255)
_COLOR_DARK   = (15, 12, 10)
_COLOR_GREEN  = (80, 220, 100)

_COUNTDOWN_SECONDS   = 4
_MAX_SESSION_SECONDS = 180
_IDLE_TIMEOUT_SECONDS = 25
_FPS                 = 20

# Calories per rep estimate (MET-based)
_CALORIES_PER_REP = {
    "pushups": 0.32,
    "squats": 0.40,
    "general": 0.30,
}

def _get_camera_index() -> int:
    try:
        cfg = json.loads((get_user_data_dir() / "config" / "app_settings.json").read_text(encoding="utf-8"))
        return int(cfg.get("camera_index", 0))
    except Exception:
        return 0

def _open_camera(index: int = 0):
    backend = cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        return None
    # Quick flush of initial buffered frames
    for _ in range(5):
        cap.read()
    return cap

def _emit_frame(frame_sig, frame: np.ndarray) -> None:
    if frame_sig is None or frame is None:
        return
    try:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            frame_sig.emit(buf.tobytes())
    except Exception:
        pass

class _SignalRepCounter:
    """Smooths a 1D movement signal (0.0=top, 1.0=bottom) and counts reps with hysteresis."""
    def __init__(self, down_threshold: float = 0.65, up_threshold: float = 0.30):
        self.down_thresh = down_threshold
        self.up_thresh = up_threshold
        self.state = "up"
        self.reps = 0
        self.min_val = 1.0
        self.max_val = 0.0

    def update(self, signal: float) -> bool:
        signal = float(np.clip(signal, 0.0, 1.0))
        if self.state == "up" and signal >= self.down_thresh:
            self.state = "down"
        elif self.state == "down" and signal <= self.up_thresh:
            self.state = "up"
            self.reps += 1
            return True
        return False

def _create_mediapipe_tracker(exercise: str):
    """Initializes MediaPipe Pose detector if installed."""
    try:
        import mediapipe as mp
        pose = mp.solutions.pose.Pose(
            model_complexity=0,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )
        lm_enum = mp.solutions.pose.PoseLandmark

        def _calc_angle(a, b, c) -> float:
            ba = np.array([a.x - b.x, a.y - b.y])
            bc = np.array([c.x - b.x, c.y - b.y])
            cos = np.dot(ba, bc) / (np.linalg.norm(ba) * np.linalg.norm(bc) + 1e-9)
            return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))

        def _evaluate(frame: np.ndarray) -> Optional[float]:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = pose.process(rgb)
            if not res or not res.pose_landmarks:
                return None
            pts = res.pose_landmarks.landmark

            if exercise == "squats":
                # Knee angle: HIP -> KNEE -> ANKLE
                left_angle = _calc_angle(pts[lm_enum.LEFT_HIP], pts[lm_enum.LEFT_KNEE], pts[lm_enum.LEFT_ANKLE])
                right_angle = _calc_angle(pts[lm_enum.RIGHT_HIP], pts[lm_enum.RIGHT_KNEE], pts[lm_enum.RIGHT_ANKLE])
                angle = min(left_angle, right_angle)
                # 175° (straight leg) -> 0.0, 85° (deep squat) -> 1.0
                return float(np.clip((175.0 - angle) / 90.0, 0.0, 1.0))
            else:
                # Pushups: SHOULDER -> ELBOW -> WRIST
                left_angle = _calc_angle(pts[lm_enum.LEFT_SHOULDER], pts[lm_enum.LEFT_ELBOW], pts[lm_enum.LEFT_WRIST])
                right_angle = _calc_angle(pts[lm_enum.RIGHT_SHOULDER], pts[lm_enum.RIGHT_ELBOW], pts[lm_enum.RIGHT_WRIST])
                angle = min(left_angle, right_angle)
                # 165° (straight arm) -> 0.0, 75° (bottom of pushup) -> 1.0
                return float(np.clip((165.0 - angle) / 90.0, 0.0, 1.0))

        return _evaluate
    except Exception:
        return None

def _create_optical_tracker():
    """Fallback: Computer Vision motion tracking using face/head vertical displacement."""
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    baseline_y = None

    def _evaluate(frame: np.ndarray) -> Optional[float]:
        nonlocal baseline_y
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (0, 0), fx=0.5, fy=0.5)
        faces = cascade.detectMultiScale(small, 1.15, 4, minSize=(30, 30))
        if len(faces) == 0:
            return None
        x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        center_y = (y + fh / 2.0) * 2.0  # map back to original frame
        if baseline_y is None:
            baseline_y = center_y
            return 0.0
        # Adapt baseline slowly upward
        baseline_y = min(baseline_y, center_y)
        delta = center_y - baseline_y
        # Normalize: movement of ~20% frame height = full rep
        norm = np.clip(delta / (h * 0.22), 0.0, 1.0)
        return float(norm)

    return _evaluate

def _render_hud_overlay(frame: np.ndarray, reps: int, target: int, exercise: str,
                        state: str, flash_until: float, countdown: Optional[int] = None) -> np.ndarray:
    """Renders a sleek, high-tech NIGHTFALL HUD overlay with exercise name, reps, and target."""
    h, w = frame.shape[:2]
    out = frame.copy()

    # Semi-transparent top HUD bar
    overlay = out.copy()
    cv2.rectangle(overlay, (0, 0), (w, 80), _COLOR_DARK, -1)
    cv2.addWeighted(overlay, 0.65, out, 0.35, 0, out)

    if countdown is not None:
        # Large central countdown
        txt = f"READY IN {countdown}"
        cv2.putText(out, txt, (int(w * 0.25), int(h * 0.52)), cv2.FONT_HERSHEY_DUPLEX, 2.0, _COLOR_GOLD, 4, cv2.LINE_AA)
        cv2.putText(out, "Get into position", (int(w * 0.32), int(h * 0.60)), cv2.FONT_HERSHEY_SIMPLEX, 0.9, _COLOR_WHITE, 2, cv2.LINE_AA)
        return out

    # Rep counter display
    is_flash = time.time() < flash_until
    rep_color = _COLOR_GOLD if is_flash else _COLOR_CYAN
    title_text = f"NIGHTFALL FIT // {exercise.upper()}"
    cv2.putText(out, title_text, (20, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.65, _COLOR_WHITE, 1, cv2.LINE_AA)

    rep_str = f"{reps}"
    if target > 0:
        rep_str += f" / {target}"
    cv2.putText(out, rep_str, (20, 70), cv2.FONT_HERSHEY_DUPLEX, 1.3, rep_color, 2, cv2.LINE_AA)

    # State indicator
    state_label = "DOWN (ENGAGED)" if state == "down" else "UP"
    state_color = _COLOR_GREEN if state == "down" else _COLOR_CYAN
    cv2.putText(out, state_label, (w - 240, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.7, state_color, 2, cv2.LINE_AA)

    # Progress bar at bottom
    if target > 0:
        pct = min(1.0, reps / float(target))
        bar_w = int(w * pct)
        cv2.rectangle(out, (0, h - 8), (bar_w, h), _COLOR_GOLD, -1)
    else:
        # Pulse bar showing down-state
        bar_w = int(w * (0.85 if state == "down" else 0.15))
        cv2.rectangle(out, (0, h - 6), (bar_w, h), _COLOR_CYAN, -1)

    return out

def _record_session(exercise: str, reps: int, seconds: float, calories: float) -> dict:
    """Saves completed session to memory/workout_history.json and long_term.json."""
    today = date.today().isoformat()
    entry = {
        "timestamp": datetime.now().isoformat(),
        "date": today,
        "exercise": exercise,
        "reps": reps,
        "duration_seconds": round(seconds),
        "calories_burned": round(calories, 1),
    }
    try:
        m_dir = get_user_data_dir() / "memory"
        m_dir.mkdir(parents=True, exist_ok=True)
        w_file = m_dir / "workout_history.json"
        history = []
        if w_file.exists():
            try:
                history = json.loads(w_file.read_text(encoding="utf-8"))
            except Exception:
                history = []
        history.append(entry)
        w_file.write_text(json.dumps(history[-200:], indent=2, ensure_ascii=False), encoding="utf-8")

        # Also update long_term.json summary
        lt_file = m_dir / "long_term.json"
        lt_data = {}
        if lt_file.exists():
            try:
                lt_data = json.loads(lt_file.read_text(encoding="utf-8"))
            except Exception:
                lt_data = {}
        sessions = lt_data.get("workout_sessions", [])
        sessions.append(entry)
        lt_data["workout_sessions"] = sessions[-100:]
        lt_file.write_text(json.dumps(lt_data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not persist workout session: {e}")
    return entry

def run(parameters: dict, player=None, speak=None, session_memory=None) -> str:
    """Main execution function for NIGHTFALL Workout Tracker."""
    query = (parameters.get("query") or "").strip()
    q_lower = query.lower()

    # Determine exercise
    exercise = parameters.get("exercise") or ""
    if not exercise:
        if "squat" in q_lower:
            exercise = "squats"
        else:
            exercise = "pushups"
    exercise = exercise.lower()

    # Extract target reps if any
    target = 0
    try:
        if parameters.get("target"):
            target = int(parameters["target"])
    except Exception:
        target = 0

    if target == 0:
        # Regex search for numbers in query
        import re
        m = re.search(r"\b(\d+)\b", query)
        if m:
            val = int(m.group(1))
            if 3 <= val <= 300:
                target = val

    def _log(msg: str):
        if player and hasattr(player, "write_log"):
            try:
                player.write_log(msg)
            except Exception:
                pass
        logger.info(msg)

    win = getattr(player, "_win", None) if player else None
    frame_sig = getattr(win, "_cam_frame_sig", None)
    stream_sig = getattr(win, "_cam_stream_sig", None)

    # Initialize tracker
    tracker = _create_mediapipe_tracker(exercise)
    tracker_name = "MediaPipe AI Pose Landmark"
    if tracker is None:
        tracker = _create_optical_tracker()
        tracker_name = "Optical Vertical Motion"

    cap = _open_camera(_get_camera_index())
    if cap is None:
        return (
            f"I couldn't open the webcam for the workout tracker. "
            f"Please ensure the camera is connected and not in use by another app."
        )

    counter = _SignalRepCounter()
    flash_until = 0.0
    view_active = False
    started = time.time()
    last_rep_time = started
    end_reason = "completed"

    try:
        if stream_sig:
            stream_sig.emit(True)
            view_active = True
        _log(f"[Workout] Started {exercise.title()} session with {tracker_name} tracking.")

        # Phase 1: 4-Second Countdown
        t0 = time.time()
        while time.time() - t0 < _COUNTDOWN_SECONDS:
            ret, frm = cap.read()
            if ret and frm is not None:
                rem = max(1, _COUNTDOWN_SECONDS - int(time.time() - t0))
                overlay = _render_hud_overlay(frm, 0, target, exercise, "up", 0, countdown=rem)
                _emit_frame(frame_sig, overlay)
            time.sleep(1.0 / _FPS)

        # Phase 2: Workout Tracking Loop
        started = time.time()
        last_rep_time = started

        while True:
            now = time.time()
            elapsed = now - started

            if elapsed > _MAX_SESSION_SECONDS:
                end_reason = "time limit reached"
                break

            idle = now - last_rep_time
            if counter.reps > 0 and idle > _IDLE_TIMEOUT_SECONDS:
                end_reason = "idle timeout"
                break
            if counter.reps == 0 and idle > 35.0:
                end_reason = "no movement detected"
                break

            if target > 0 and counter.reps >= target:
                end_reason = "target reached"
                break

            ret, frm = cap.read()
            if not ret or frm is None:
                time.sleep(1.0 / _FPS)
                continue

            # Evaluate movement signal
            sig = tracker(frm)
            if sig is not None:
                if counter.update(sig):
                    last_rep_time = time.time()
                    flash_until = last_rep_time + 0.6
                    _log(f"[Workout] Rep {counter.reps} completed! 💪")

            overlay = _render_hud_overlay(frm, counter.reps, target, exercise, counter.state, flash_until)
            _emit_frame(frame_sig, overlay)
            time.sleep(1.0 / _FPS)

    finally:
        try:
            cap.release()
        except Exception:
            pass
        if view_active and stream_sig:
            try:
                stream_sig.emit(False)
            except Exception:
                pass

    total_reps = counter.reps
    duration = max(1.0, time.time() - started)
    pace = total_reps / (duration / 60.0) if duration > 0 else 0
    cals = total_reps * _CALORIES_PER_REP.get(exercise, 0.35)

    if total_reps == 0:
        msg = f"The workout session ended with 0 detected {exercise}. Make sure your camera has a clear view of your whole body."
        _log(f"[Workout] {msg}")
        return msg

    # Save session
    _record_session(exercise, total_reps, duration, cals)

    # Show rich UI summary
    summary_card = (
        f"### 🏋️ {exercise.upper()} SESSION FINISHED\n\n"
        f"- **Completed Reps**: {total_reps}" + (f" / {target}" if target else "") + "\n"
        f"- **Duration**: {int(duration)} seconds\n"
        f"- **Cadence**: {pace:.1f} reps/min\n"
        f"- **Estimated Energy Burned**: {cals:.1f} kcal\n"
        f"- **Status**: {end_reason.title()}\n"
        f"- **Tracker**: {tracker_name}\n"
    )

    if player and hasattr(player, "show_content"):
        try:
            player.show_content("💪 NIGHTFALL FITNESS SUMMARY", summary_card)
        except Exception:
            pass

    spoken = (
        f"Awesome work! You completed {total_reps} {exercise} in {int(duration)} seconds, "
        f"burning approximately {cals:.0f} calories."
    )
    _log(f"[Workout] {spoken}")
    return spoken

# Aliases for dispatch
pushup_counter = run
