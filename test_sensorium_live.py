"""
Interactive Sensorium Test Harness (v2)
Runs a real-time terminal monitor that shows how NIGHTFALL passively observes your actions
and triggers autonomous interjections without you saying or prompting a single word.
"""

import time
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from core.sensorium import sensorium

def speak_proactive(text: str):
    text = (text or "").strip()
    if not text:
        return
    import threading
    def _speak():
        try:
            # 1. Official NIGHTFALL Unified Neural Voice (Edge TTS GuyNeural)
            from actions.attention_monitor import _speak_edge_native
            _speak_edge_native(text)
            return
        except Exception:
            pass

        try:
            # 2. Offline fallback
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            voice.Volume = 100
            voice.Speak(text)
        except Exception as err:
            print(f"[Speech Error]: {err}")
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    threading.Thread(target=_speak, daemon=True).start()

def on_interjection(alert_type, meta):
    msg = meta.get("message", "")
    speech = meta.get("speech", msg)
    print(f"\n⚡ [AUTONOMOUS INTERJECTION - {alert_type.upper()}]: {msg}")
    print(f"🔊 [NIGHTFALL SPEAKING]: \"{speech}\"\n")
    speak_proactive(speech)

if __name__ == "__main__":
    print("=" * 65)
    print("  NIGHTFALL EVO v2: PASSIVE SENSORIUM LIVE TEST")
    print("=" * 65)
    print("Instructions:")
    print("1. Switch between different windows (Browser, Explorer, Terminal, etc.)")
    print("2. Leave your mouse and keyboard alone for 7 seconds to test 'Away' detection.")
    print("3. Notice how it tracks your focus without you asking anything.")
    print("Press Ctrl+C to stop.\n")

    sensorium.register_interjection_handler(on_interjection)
    sensorium.start()

    try:
        user_is_away = False
        streak_alerted = False

        while True:
            snap = sensorium.get_snapshot()
            current_win = snap["window_title"] or "Desktop / Background"
            proc = snap["process_name"]
            dwell = snap["dwell_seconds"]
            idle = snap["user_idle_seconds"]

            # Check for window switch
            if current_win != last_window:
                print(f"\n👁️  [FOCUS SWITCH] App: {proc} | Window: {current_win[:45]}...")
                last_window = current_win
                streak_alerted = False

            # Live status ticker on same line
            status = "💤 AWAY" if user_is_away else "⚡ ACTIVE"
            sys.stdout.write(f"\r[{status}] App: {proc[:15]} | Idle: {idle:.1f}s / 7.0s | Dwell: {dwell:.0f}s   ")
            sys.stdout.flush()

            # Step 1: Detect user leaving controls for >= 7 seconds
            if idle >= 7.0 and not user_is_away:
                user_is_away = True
                print(f"\n\n💤 [SENSORIUM EVENT]: Idle reached {idle:.1f}s! User marked as AWAY.")
                print("👉 Move your mouse or press any key to test Welcome Back speech!\n")

            # Step 2: Detect user returning (idle drops back under 2 seconds)
            elif idle < 2.0 and user_is_away:
                user_is_away = False
                msg = f"Welcome back, sir. Your workspace on {proc} is ready."
                print(f"\n\n✨ [PROACTIVE SPEECH TRIGGERED]: {msg}")
                print(f"🔊 [NIGHTFALL SPEAKING OUT LOUD NOW...]\n")
                speak_proactive(msg)

            # Step 3: Focus streak demo (15s continuous in window)
            if dwell >= 15.0 and not streak_alerted and not user_is_away:
                streak_alerted = True
                msg = f"Focus streak on {proc} detected. Running smoothly."
                print(f"\n\n🎯 [PROACTIVE FOCUS TRIGGER]: {msg}")
                speak_proactive(msg)

            time.sleep(0.5)

    except KeyboardInterrupt:
        sensorium.stop()
        print("\nSensorium stopped.")
