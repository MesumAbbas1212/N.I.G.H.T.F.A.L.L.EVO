"""
Autonomous AI Call Attendant & Call Screening Assistant ("Call Proxy")
Part of NIGHTFALL AI.

Allows NIGHTFALL AI Evo to autonomously answer voice/video calls on Windows
(WhatsApp, Teams, Phone Link, Zoom, Skype, etc.), introduce itself as the user's
AI executive assistant, converse with the caller, transcribe the dialogue in real time,
record messages and urgency, and deliver a structured debriefing card.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import threading
import time
import urllib.request
import wave
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import sounddevice as sd
import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _MATPLOTLIB_OK = True
except Exception:
    _MATPLOTLIB_OK = False

from core.user_paths import get_user_data_dir
from core.identity import identity

logger = logging.getLogger("CallAssistant")

CONFIG_DIR = get_user_data_dir() / "config"
API_CONFIG_PATH = CONFIG_DIR / "api_keys.json"
DELIVERABLES_DIR = get_user_data_dir() / "deliverables"


def _get_api_key() -> str:
    if API_CONFIG_PATH.exists():
        try:
            with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                key = data.get("gemini_api_key", "").strip()
                if key:
                    return key
        except Exception:
            pass
    return (os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")).strip()


def _get_audio_loopback_device() -> dict:
    """Finds best input/loopback device to record caller's voice with maximum clarity."""
    try:
        devices = sd.query_devices()
    except Exception:
        return {}

    # 1. Prefer Stereo Mix (direct internal loopback from Realtek) or virtual cable
    for i, dev in enumerate(devices):
        if dev.get("max_input_channels", 0) > 0:
            name = (dev.get("name") or "").lower()
            if "stereo mix" in name or "cable output" in name or "vb-audio" in name:
                return {
                    "device": i,
                    "channels": 1,
                    "samplerate": int(dev.get("default_samplerate") or 16000),
                    "label": dev.get("name") or f"Input {i}",
                }

    # 2. Fallback to default system input microphone
    try:
        in_dev = sd.default.device[0]
        if in_dev is not None and in_dev >= 0 and in_dev < len(devices):
            dev = devices[in_dev]
            if dev.get("max_input_channels", 0) > 0:
                return {
                    "device": in_dev,
                    "channels": 1,
                    "samplerate": int(dev.get("default_samplerate") or 16000),
                    "label": dev.get("name") or "Default Mic",
                }
    except Exception:
        pass

    # 3. Any available input device
    for i, dev in enumerate(devices):
        if dev.get("max_input_channels", 0) > 0:
            return {
                "device": i,
                "channels": 1,
                "samplerate": int(dev.get("default_samplerate") or 16000),
                "label": dev.get("name") or f"Input {i}",
            }

    return {}


def _boost_system_audio_for_call() -> Optional[float]:
    """
    Temporarily raises microphone gain for the call and returns its original level.
    """
    orig_mic_level = None

    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        mic_dev = AudioUtilities.GetMicrophone()
        if mic_dev:
            vol_obj = mic_dev.Activate(IAudioEndpointVolume._iid_, 7, None)
            mic_ep = vol_obj.QueryInterface(IAudioEndpointVolume)
            orig_mic_level = mic_ep.GetMasterVolumeLevelScalar()
            mic_ep.SetMasterVolumeLevelScalar(1.0, None)
            mic_ep.SetMute(0, None)
    except Exception as e:
        logger.debug(f"[CallAssistant] Mic boost notice: {e}")

    return orig_mic_level


def _restore_system_audio_after_call(orig_mic_level: Optional[float]) -> None:
    """Restores the user's personal microphone level after the call proxy finishes."""
    if orig_mic_level is not None:
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            mic_dev = AudioUtilities.GetMicrophone()
            if mic_dev:
                vol_obj = mic_dev.Activate(IAudioEndpointVolume._iid_, 7, None)
                mic_ep = vol_obj.QueryInterface(IAudioEndpointVolume)
                mic_ep.SetMasterVolumeLevelScalar(orig_mic_level, None)
        except Exception:
            pass


class CallAssistant:
    """
    Manages an active call screening session where NIGHTFALL acts as proxy.
    """

    _active_instance: Optional["CallAssistant"] = None

    def __init__(
        self,
        event: dict,
        ui: Any = None,
        speak_fn: Optional[Callable[[str], None]] = None,
    ):
        self.event = dict(event or {})
        self.ui = ui
        self.speak_fn = speak_fn
        self.caller_name = (self.event.get("title") or self.event.get("preview") or "Caller").strip()
        self.app_name = (self.event.get("app") or "Phone / Call").strip()
        self.owner_name = identity.get_owner_name() or "Ravi"
        self.start_time = time.time()
        self.is_active = False
        self.transcript: List[Dict[str, str]] = []
        self._loop_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._user_took_over = False
        self._orig_mic_level: Optional[float] = None

    @classmethod
    def get_active(cls) -> Optional["CallAssistant"]:
        return cls._active_instance

    def start(self):
        """Answers the call and begins the AI Call Proxy conversation."""
        CallAssistant._active_instance = self
        self.is_active = True
        self._stop_event.clear()

        # Boost system audio and microphone gain, disable Windows ducking
        self._orig_mic_level = _boost_system_audio_for_call()

        logger.info(f"[CallAssistant] Starting call proxy for {self.caller_name} on {self.app_name}")

        # 1. Click accept to answer the call on Windows
        try:
            from actions.attention_monitor import handle_call_action
            handle_call_action(self.event, "accept")
        except Exception as e:
            logger.warning(f"[CallAssistant] Accept click notice: {e}")

        # 2. Show Call Screening UI HUD
        if self.ui and hasattr(self.ui, "show_call_screening"):
            try:
                self.ui.show_call_screening({
                    "caller": self.caller_name,
                    "app": self.app_name,
                    "status": "Answering as AI Assistant...",
                })
            except Exception:
                pass

        if self.ui:
            self.ui.write_log(f"📞 CALL SCREENING: Answering call from {self.caller_name} on {self.app_name}...")

        # 3. Launch the conversation thread
        self._loop_thread = threading.Thread(target=self._run_conversation, daemon=True, name="call-proxy")
        self._loop_thread.start()

    def take_over(self):
        """User clicks 'Take Over' to jump into the call personally."""
        if not self.is_active:
            return
        self._user_took_over = True
        self.is_active = False
        self._stop_event.set()

        # Restore original microphone level
        _restore_system_audio_after_call(self._orig_mic_level)

        excuse = f"{self.owner_name} is joining the line right now. Thank you for holding, please go ahead."
        self._log_turn("NIGHTFALL", excuse)
        self._speak(excuse)

        if self.ui:
            self.ui.write_log(f"SYS: User took over the call with {self.caller_name}.")
            if hasattr(self.ui, "hide_call_screening"):
                self.ui.hide_call_screening()

        CallAssistant._active_instance = None

    def hang_up(self, message: str = "Thank you for calling. I will relay your message immediately. Goodbye!"):
        """Ends the call politely and saves the call report."""
        if not self.is_active:
            return
        self.is_active = False
        self._stop_event.set()

        if message:
            self._log_turn("NIGHTFALL", message)
            self._speak(message)
            time.sleep(0.5)

        # Restore original microphone level
        _restore_system_audio_after_call(self._orig_mic_level)

        # Hang up call via Windows action
        try:
            from actions.attention_monitor import handle_call_action
            handle_call_action(self.event, "decline")
        except Exception as e:
            logger.warning(f"[CallAssistant] Hang up notice: {e}")

        if self.ui and hasattr(self.ui, "hide_call_screening"):
            try:
                self.ui.hide_call_screening()
            except Exception:
                pass

        # Generate post-call briefing
        self._finalize_call_report()
        CallAssistant._active_instance = None

    def _speak(self, text: str):
        """
        Speaks text to the active call using NIGHTFALL's UNIFIED native voice (Gemini Live).
        Blocks until speech finishes so NIGHTFALL doesn't cut itself off or listen to its own voice.
        """
        text = (text or "").strip()
        if not text:
            return

        # Enforce Python session volume at 1.0 (un-ducked)
        try:
            from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
            for s in AudioUtilities.GetAllSessions():
                if s.Process and s.Process.name() in ("python.exe", "pythonw.exe"):
                    v = s._ctl.QueryInterface(ISimpleAudioVolume)
                    v.SetMasterVolume(1.0, None)
                    v.SetMute(0, None)
        except Exception:
            pass

        # 1. Primary: NIGHTFALL Unified Native Voice (Gemini Live Charon)
        spoken = False
        if self.speak_fn:
            try:
                import inspect
                sig = inspect.signature(self.speak_fn)
                if "wait_finish" in sig.parameters:
                    self.speak_fn(text, wait_finish=True)
                else:
                    self.speak_fn(text)
                    time.sleep(max(2.5, len(text.split()) * 0.45))
                spoken = True
            except Exception as e:
                logger.warning(f"[CallAssistant] Unified voice playback notice: {e}")

        # 2. Offline Fallback: Windows SAPI (instantaneous, 0ms latency)
        if not spoken:
            try:
                import win32com.client
                import pythoncom
                pythoncom.CoInitialize()
                speaker = win32com.client.Dispatch("SAPI.SpVoice")
                speaker.Volume = 100
                speaker.Speak(text, 0)  # 0 = synchronous
                spoken = True
            except Exception as e:
                logger.warning(f"[CallAssistant] SAPI fallback notice: {e}")

    def _log_turn(self, speaker: str, text: str):
        text = text.strip()
        if not text:
            return
        self.transcript.append({"speaker": speaker, "text": text, "time": datetime.now().strftime("%H:%M:%S")})
        if self.ui:
            self.ui.write_log(f"[{self.app_name}] {speaker}: {text}")
            if hasattr(self.ui, "update_call_screening_transcript"):
                try:
                    self.ui.update_call_screening_transcript(speaker, text)
                except Exception:
                    pass

    def _run_conversation(self):
        """Main interaction loop with the caller. Listens to caller message, acknowledges, and cuts call."""
        time.sleep(0.5)  # Brief pause after pickup for audio stream connection

        # Step 1: Warm, authentic human greeting
        greeting = (
            f"Hey! You've reached {self.owner_name}'s line. He's tied up right now, but leave a quick message and I'll make sure he gets it right away!"
        )
        self._log_turn("NIGHTFALL", greeting)
        self._speak(greeting)

        # Audio stream setup
        audio_spec = _get_audio_loopback_device()
        dev_idx = audio_spec.get("device")
        sample_rate = audio_spec.get("samplerate", 16000)
        channels = audio_spec.get("channels", 1)

        turns_count = 0
        max_turns = 2

        while self.is_active and not self._stop_event.is_set() and turns_count < max_turns:
            turns_count += 1

            # Wait for caller speech with fast VAD (max 8s, 0.7s silence cut)
            caller_text = self._listen_to_caller(dev_idx, channels, sample_rate, max_wait=8.0)
            if self._stop_event.is_set() or not self.is_active:
                break

            if not caller_text:
                if turns_count == 1:
                    prompt_again = f"Hey, are you still there? Go ahead with your message for {self.owner_name}."
                    self._log_turn("NIGHTFALL", prompt_again)
                    self._speak(prompt_again)
                    continue
                else:
                    self.hang_up(f"Looks like nobody's there—I'll just let {self.owner_name} know you called. Bye!")
                    break

            self._log_turn("Caller", caller_text)
            clean_caller = caller_text.strip().lower()

            # Check if caller wants to wrap up immediately
            if any(w in clean_caller for w in ("bye", "goodbye", "that's all", "thats all", "thank you bye", "see you")):
                self.hang_up(f"Got it! Thanks for calling, I'll let {self.owner_name} know right away. Bye!")
                break

            # Check if caller demands the user urgently
            if any(w in clean_caller for w in ("emergency", "very urgent", "life or death", "need him right now", "call him now")):
                if self.ui and hasattr(self.ui, "write_log"):
                    self.ui.write_log(f"🚨 CALL SCREENING URGENT: {self.caller_name} reports an urgent matter!")

            # Check if caller only said a brief check/greeting without an actual message yet
            words = clean_caller.split()
            is_just_greeting = len(words) <= 3 and any(
                w in clean_caller for w in ("hello", "hi", "hey", "can you hear me", "who is this", "are you there", "hello?", "hii")
            )

            if is_just_greeting and turns_count == 1:
                clarification = "Yeah, I'm here! Go ahead, I'm listening."
                self._log_turn("NIGHTFALL", clarification)
                self._speak(clarification)
                continue

            # The caller has completed leaving their message!
            # Generate a brief closing acknowledgment and cut the call immediately.
            closing_reply = self._generate_ai_closing(caller_text)
            self.hang_up(closing_reply)
            break

        if self.is_active:
            self.hang_up()

    def _listen_to_caller(self, dev_idx, channels, sample_rate, max_wait: float = 8.0) -> str:
        """
        Listens to caller speech with instant low-latency energy VAD and speech-to-text.
        Stops recording 0.7s after caller stops talking.
        """
        recorded_chunks: list[np.ndarray] = []
        speech_detected = False
        silence_after_speech = 0
        start_t = time.time()

        def audio_cb(indata, frames, time_info, status):
            nonlocal speech_detected, silence_after_speech
            lvl = float(np.sqrt(np.mean(np.square(indata, dtype=np.float32))))
            if lvl > 15.0:
                speech_detected = True
                silence_after_speech = 0
                recorded_chunks.append(indata.copy())
            elif speech_detected:
                silence_after_speech += 1
                recorded_chunks.append(indata.copy())

        try:
            with sd.InputStream(
                device=dev_idx,
                channels=channels,
                samplerate=sample_rate,
                dtype="int16",
                blocksize=1600,
                callback=audio_cb,
            ):
                while time.time() - start_t < max_wait:
                    if self._stop_event.is_set() or not self.is_active:
                        return ""
                    # If no speech started after 2.5 seconds, don't keep caller waiting
                    if not speech_detected and (time.time() - start_t > 2.5):
                        break
                    # Once caller spoke, wait for 7 blocks (~0.7s) of silence to conclude speech
                    if speech_detected and silence_after_speech > 7:
                        break
                    time.sleep(0.04)
        except Exception as exc:
            logger.warning(f"[CallAssistant] Audio input stream notice: {exc}")

        if not recorded_chunks:
            return ""

        # Concatenate audio bytes
        try:
            pcm_data = np.concatenate(recorded_chunks, axis=0).tobytes()
            wav_buf = io.BytesIO()
            with wave.open(wav_buf, "wb") as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(pcm_data)
            wav_bytes = wav_buf.getvalue()

            # Transcribe via Gemini
            return self._transcribe_audio(wav_bytes)
        except Exception as e:
            logger.warning(f"[CallAssistant] Transcription prep error: {e}")
            return ""

    def _transcribe_audio(self, wav_bytes: bytes) -> str:
        """Sends audio chunk to Gemini for fast speech-to-text."""
        api_key = _get_api_key()
        if not api_key:
            return ""
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=[
                    types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav"),
                    "Transcribe the spoken audio verbatim. Return only the transcribed text, nothing else.",
                ],
            )
            return (getattr(response, "text", "") or "").strip()
        except Exception as e:
            logger.warning(f"[CallAssistant] Speech-to-text notice: {e}")
            return ""

    def _generate_ai_response(self, caller_text: str) -> str:
        """Synthesizes a polite, 1-2 sentence assistant reply."""
        api_key = _get_api_key()
        history_str = "\n".join(f"{t['speaker']}: {t['text']}" for t in self.transcript[-6:])

        prompt = f"""You are NIGHTFALL AI Evo, an executive AI assistant answering a live phone call on behalf of {self.owner_name}.
{self.owner_name} is currently occupied and unavailable to pick up.
Caller: {self.caller_name} (App: {self.app_name}).

Conversation so far:
{history_str}
Caller just said: "{caller_text}"

Guidelines:
1. Speak directly to the caller. Keep answers very concise (1 to 2 sentences maximum).
2. Ascertain who they are, the purpose of their call, and whether they would like to leave a message.
3. Be polite, professional, and helpful. If they ask when {self.owner_name} will call back, say you will notify them right away.
4. Do not make false promises or disclose private data.
5. Return ONLY the spoken response text with no quotes or meta-text.
"""
        if api_key:
            try:
                from google import genai
                client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
                resp = client.models.generate_content(
                    model="gemini-2.5-flash-lite",
                    contents=prompt,
                )
                txt = (getattr(resp, "text", "") or "").strip()
                if txt:
                    return txt
            except Exception as e:
                logger.warning(f"[CallAssistant] LLM reply error: {e}")

        return f"Understood. I have made a note of that for {self.owner_name}. Is there anything else you'd like me to pass along?"

    def _generate_ai_closing(self, caller_text: str) -> str:
        """Synthesizes an ultra-natural, warm human closing acknowledgment and says goodbye."""
        api_key = _get_api_key()
        prompt = f"""You are answering a live phone call on behalf of your friend and colleague {self.owner_name}.
The caller just said: "{caller_text}".

Your goal:
Acknowledge receipt of their message in ONE short, natural, warm human sentence, confirm you'll pass it to {self.owner_name} right away, and say goodbye.
Rules:
- Speak casually like a real human assistant. Use contractions ("I'll", "Got it", "He'll").
- Maximum 14 words.
- End with a friendly signoff like "Catch you later, bye!" or "Talk soon, bye!"
- Output ONLY the exact spoken response line, nothing else. No quotes, no markdown.
"""
        if api_key:
            try:
                from google import genai
                client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
                resp = client.models.generate_content(
                    model="gemini-2.5-flash-lite",
                    contents=prompt,
                )
                txt = (getattr(resp, "text", "") or "").strip().strip('"')
                if txt:
                    return txt
            except Exception as e:
                logger.warning(f"[CallAssistant] AI closing reply error: {e}")

        return f"Got it! I've noted that down for {self.owner_name} and I'll pass it to him right away. Bye!"

    def _finalize_call_report(self):
        """Generates a structured post-call summary card and notifies the user."""
        duration_sec = int(time.time() - self.start_time)
        mins, secs = divmod(duration_sec, 60)
        duration_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

        history_text = "\n".join(f"[{t['time']}] {t['speaker']}: {t['text']}" for t in self.transcript)
        
        # Summarize via LLM
        summary = "Caller called but left no detailed message."
        action_item = "No immediate action required."
        urgency = "Normal"

        api_key = _get_api_key()
        if api_key and self.transcript:
            try:
                from google import genai
                client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
                sum_prompt = f"""Summarize this phone call screened by NIGHTFALL AI Evo on behalf of {self.owner_name}:
Caller: {self.caller_name}
Duration: {duration_str}

Transcript:
{history_text}

Output a clean JSON object:
{{
  "summary": "1-2 sentence executive summary of the caller's message and reason for calling",
  "urgency": "Low|Normal|High|Emergency",
  "action_item": "Clear next action for {self.owner_name} (e.g. Call back regarding invoice before 5 PM)"
}}
"""
                resp = client.models.generate_content(
                    model="gemini-2.5-flash-lite",
                    contents=sum_prompt,
                    config={"response_mime_type": "application/json"}
                )
                raw_json = (getattr(resp, "text", "") or "").strip()
                data = json.loads(raw_json)
                summary = data.get("summary", summary)
                urgency = data.get("urgency", urgency)
                action_item = data.get("action_item", action_item)
            except Exception:
                pass

        # Create Dark-mode Card Image
        image_path = None
        if _MATPLOTLIB_OK:
            try:
                DELIVERABLES_DIR.mkdir(parents=True, exist_ok=True)
                fig, ax = plt.subplots(figsize=(9, 6))
                fig.patch.set_facecolor("#0B0F19")
                ax.set_facecolor("#0B0F19")

                ax.text(0.05, 0.90, "CALL SCREENING BRIEFING", color="#00F0FF", fontsize=15, fontweight="bold")
                ax.text(0.05, 0.82, f"Caller: {self.caller_name}  |  App: {self.app_name}  |  Duration: {duration_str}", color="#94A3B8", fontsize=10)

                # Summary Section
                ax.text(0.05, 0.70, "EXECUTIVE SUMMARY", color="#F59E0B", fontsize=11, fontweight="bold")
                ax.text(0.05, 0.62, summary, color="#FFFFFF", fontsize=10, wrap=True)

                # Action Item Section
                urg_color = "#EF4444" if urgency in ("High", "Emergency") else "#10B981"
                ax.text(0.05, 0.48, f"ACTION ITEM  [{urgency.upper()}]", color=urg_color, fontsize=11, fontweight="bold")
                ax.text(0.05, 0.40, action_item, color="#FFFFFF", fontsize=10, wrap=True)

                # Recent turns
                ax.text(0.05, 0.26, "RECENT TRANSCRIPT SNIPPET", color="#64748B", fontsize=9, fontweight="bold")
                snippet = "\n".join(f"{t['speaker']}: {t['text'][:65]}" for t in self.transcript[-3:])
                ax.text(0.05, 0.16, snippet or "No dialogue captured.", color="#94A3B8", fontsize=8)

                ax.set_xticks([])
                ax.set_yticks([])
                for spine in ax.spines.values():
                    spine.set_color("#1E293B")

                image_path = str(DELIVERABLES_DIR / f"call_report_{int(time.time())}.png")
                plt.savefig(image_path, bbox_inches="tight", dpi=140)
                plt.close(fig)
            except Exception as e:
                logger.warning(f"[CallAssistant] Card render notice: {e}")

        # Post to UI HUD & Logs
        report_log = (
            f"📞 CALL SCREENED: {self.caller_name} ({self.app_name})\n"
            f"• Summary: {summary}\n"
            f"• Action: {action_item} (Urgency: {urgency})\n"
            f"• Duration: {duration_str}"
        )
        if self.ui:
            self.ui.write_log(report_log)
            if image_path and hasattr(self.ui, "show_hud_deliverable"):
                try:
                    self.ui.show_hud_deliverable(image_path)
                except Exception:
                    pass
            if hasattr(self.ui, "finish_task_workspace"):
                try:
                    self.ui.finish_task_workspace(report_log, f"Call Screened: {self.caller_name}", 100)
                except Exception:
                    pass

        # Speak debriefing aloud to user
        spoken_debrief = f"Sir, I screened the call from {self.caller_name} on {self.app_name}. {summary}"
        self._speak(spoken_debrief)


def start_call_proxy(event: dict, ui: Any = None, speak_fn: Optional[Callable[[str], None]] = None) -> CallAssistant:
    """Helper entry point to start the call proxy assistant."""
    assistant = CallAssistant(event=event, ui=ui, speak_fn=speak_fn)
    assistant.start()
    return assistant


def take_over_active_call() -> None:
    """Invoked when user clicks Take Over or speaks take over command."""
    inst = CallAssistant.get_active()
    if inst:
        inst.take_over()


def hang_up_active_call() -> None:
    """Invoked when user clicks Hang Up or asks NIGHTFALL to end the call."""
    inst = CallAssistant.get_active()
    if inst:
        inst.hang_up()

