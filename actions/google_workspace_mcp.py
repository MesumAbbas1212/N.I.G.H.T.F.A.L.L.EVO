from core.user_paths import get_user_data_dir
import email
import imaplib
import json
import logging
import os
import re
import smtplib
import sys
import threading
import time
from datetime import datetime, timedelta
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("google_workspace_mcp")
logger.setLevel(logging.INFO)

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR = _get_base_dir()
CONFIG_DIR = get_user_data_dir() / "config"
EMAIL_KEY_FILE = CONFIG_DIR / ".email_key"
EMAIL_CREDENTIALS_FILE = CONFIG_DIR / "email_credentials.json"
GOOGLE_WORKSPACE_CRED_FILE = CONFIG_DIR / "google_workspace_credentials.json"
GOOGLE_WORKSPACE_TOKEN_FILE = CONFIG_DIR / "google_workspace_token.json"


# ── Credential Helpers ───────────────────────────────────────────────────────

def _get_fernet_cipher():
    try:
        from cryptography.fernet import Fernet
        if not EMAIL_KEY_FILE.exists():
            key = Fernet.generate_key()
            with open(EMAIL_KEY_FILE, "wb") as f:
                f.write(key)
        else:
            with open(EMAIL_KEY_FILE, "rb") as f:
                key = f.read().strip()
        return Fernet(key)
    except Exception as e:
        logger.warning(f"[Workspace] Cryptography initialization error: {e}")
        return None


def get_stored_gmail_credentials() -> Tuple[Optional[str], Optional[str]]:
    """Returns (email, decrypted_password) from config/email_credentials.json."""
    if not EMAIL_CREDENTIALS_FILE.exists():
        return None, None
    try:
        with open(EMAIL_CREDENTIALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        addr = data.get("email", "").strip()
        enc_pw = data.get("password_encrypted", "").strip()
        if not addr or not enc_pw:
            return addr or None, None

        fnet = _get_fernet_cipher()
        if fnet:
            pw = fnet.decrypt(enc_pw.encode()).decode("utf-8")
            return addr, pw
        return addr, None
    except Exception as e:
        logger.error(f"[Workspace] Failed to decrypt email credentials: {e}")
        return None, None


def save_stored_gmail_credentials(email_addr: str, app_password: str) -> bool:
    """Encrypts and saves Gmail credentials to config/email_credentials.json."""
    try:
        fnet = _get_fernet_cipher()
        if not fnet:
            return False
        enc_pw = fnet.encrypt(app_password.strip().encode("utf-8")).decode("utf-8")
        payload = {
            "provider": "Gmail",
            "email": email_addr.strip(),
            "password_encrypted": enc_pw,
            "updated_at": datetime.now().isoformat()
        }
        with open(EMAIL_CREDENTIALS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"[Workspace] Failed to save email credentials: {e}")
        return False


def _decode_header_str(val: Any) -> str:
    if not val:
        return ""
    parts = decode_header(val)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(str(text))
    return "".join(out)


# ── Gmail Engine (IMAP & SMTP) ──────────────────────────────────────────────

class GmailEngine:
    IMAP_HOST = "imap.gmail.com"
    IMAP_PORT = 993
    SMTP_HOST = "smtp.gmail.com"
    SMTP_PORT = 587

    @classmethod
    def test_connection(cls) -> Dict[str, Any]:
        addr, pw = get_stored_gmail_credentials()
        if not addr or not pw:
            return {"success": False, "message": "No Gmail credentials configured."}
        try:
            mail = imaplib.IMAP4_SSL(cls.IMAP_HOST, cls.IMAP_PORT)
            mail.login(addr, pw)
            status, counts = mail.select("INBOX")
            total = counts[0].decode() if counts else "0"
            mail.logout()
            return {"success": True, "message": f"Connected to {addr} (Total inbox messages: {total})"}
        except Exception as e:
            return {"success": False, "message": f"Gmail login failed: {e}"}

    @classmethod
    def list_messages(cls, query: str = "ALL", max_results: int = 5) -> str:
        addr, pw = get_stored_gmail_credentials()
        if not addr or not pw:
            return "Gmail credentials not configured. Please add your Gmail & App Password in Settings."

        try:
            mail = imaplib.IMAP4_SSL(cls.IMAP_HOST, cls.IMAP_PORT)
            mail.login(addr, pw)
            mail.select("INBOX", readonly=True)

            status, data = mail.search(None, query)
            if status != "OK" or not data or not data[0]:
                mail.logout()
                return f"No messages found matching query '{query}'."

            ids = data[0].split()
            recent_ids = ids[-max_results:]
            recent_ids.reverse()

            messages = []
            for i, mid in enumerate(recent_ids, 1):
                res, msg_data = mail.fetch(mid, "(RFC822.HEADER)")
                if res != "OK":
                    continue
                raw_headers = msg_data[0][1]
                parsed = email.message_from_bytes(raw_headers)
                subject = _decode_header_str(parsed.get("Subject", "No Subject"))
                sender = _decode_header_str(parsed.get("From", "Unknown Sender"))
                date_str = parsed.get("Date", "")
                messages.append(f"{i}. [{mid.decode()}] From: {sender}\n   Subject: {subject}\n   Date: {date_str}")

            mail.logout()
            return "\n\n".join(messages) if messages else "No readable messages found."
        except Exception as e:
            return f"Error accessing Gmail: {e}"

    @classmethod
    def read_message(cls, msg_id: str) -> str:
        addr, pw = get_stored_gmail_credentials()
        if not addr or not pw:
            return "Gmail credentials not configured."

        try:
            mail = imaplib.IMAP4_SSL(cls.IMAP_HOST, cls.IMAP_PORT)
            mail.login(addr, pw)
            mail.select("INBOX", readonly=True)

            res, msg_data = mail.fetch(msg_id.encode(), "(RFC822)")
            if res != "OK" or not msg_data or not msg_data[0]:
                mail.logout()
                return f"Could not find message ID {msg_id}."

            raw_email = msg_data[0][1]
            parsed = email.message_from_bytes(raw_email)
            subject = _decode_header_str(parsed.get("Subject", "No Subject"))
            sender = _decode_header_str(parsed.get("From", "Unknown Sender"))
            date_str = parsed.get("Date", "")

            body = ""
            if parsed.is_multipart():
                for part in parsed.walk():
                    content_type = part.get_content_type()
                    disposition = str(part.get("Content-Disposition", ""))
                    if content_type == "text/plain" and "attachment" not in disposition:
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = payload.decode(errors="replace")
                            break
                    elif content_type == "text/html" and not body and "attachment" not in disposition:
                        payload = part.get_payload(decode=True)
                        if payload:
                            html_text = payload.decode(errors="replace")
                            body = re.sub(r"<[^>]+>", " ", html_text)
            else:
                payload = parsed.get_payload(decode=True)
                if payload:
                    body = payload.decode(errors="replace")

            mail.logout()
            clean_body = re.sub(r"\s+", " ", body).strip()[:3000]
            return f"From: {sender}\nSubject: {subject}\nDate: {date_str}\n\nContent:\n{clean_body}"
        except Exception as e:
            return f"Error reading email {msg_id}: {e}"

    @classmethod
    def send_message(cls, to: str, subject: str, body: str) -> str:
        addr, pw = get_stored_gmail_credentials()
        if not addr or not pw:
            return "Gmail credentials not configured."

        try:
            msg = MIMEMultipart()
            msg["From"] = addr
            msg["To"] = to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            server = smtplib.SMTP(cls.SMTP_HOST, cls.SMTP_PORT)
            server.starttls()
            server.login(addr, pw)
            server.sendmail(addr, [to], msg.as_string())
            server.quit()

            return f"Email successfully sent to {to} with subject '{subject}'."
        except Exception as e:
            return f"Failed to send email: {e}"


# ── Google Calendar & Drive Engine ──────────────────────────────────────────

class GoogleCalendarEngine:
    @classmethod
    def list_events(cls, days: int = 7) -> str:
        """Lists events from Google Calendar if OAuth exists, or NIGHTFALL's local calendar."""
        # Try local calendar store first
        from actions.calendar_scheduler import calendar_scheduler
        res = calendar_scheduler({"action": "get_upcoming"})
        return res or "No upcoming calendar events."

    @classmethod
    def create_event(cls, title: str, date: str, time: str, duration_minutes: int = 30, description: str = "") -> str:
        from actions.calendar_scheduler import calendar_scheduler
        return calendar_scheduler({
            "action": "add_event",
            "title": title,
            "date": date,
            "time": time,
            "duration_minutes": duration_minutes,
            "description": description
        })

    @classmethod
    def delete_event(cls, event_id: str) -> str:
        from actions.calendar_scheduler import calendar_scheduler
        return calendar_scheduler({"action": "delete_event", "event_id": event_id})


class GoogleDriveEngine:
    @classmethod
    def search_files(cls, query: str) -> str:
        """Searches Google Drive or local NIGHTFALL AI generated files."""
        # Search Desktop/NIGHTFALLAI folder
        desktop_ai = Path.home() / "Desktop" / "NIGHTFALLAI"
        if not desktop_ai.exists():
            return f"No Drive or local files found for query '{query}'."

        matches = []
        q_lower = query.lower()
        for f in desktop_ai.glob("*.*"):
            if q_lower in f.name.lower():
                matches.append(f"- {f.name} ({round(f.stat().st_size / 1024, 1)} KB)")

        if matches:
            return "Found files in NIGHTFALL Workspace:\n" + "\n".join(matches)
        return f"No files matching '{query}' found."

    @classmethod
    def read_file(cls, filename: str) -> str:
        desktop_ai = Path.home() / "Desktop" / "NIGHTFALLAI"
        target = desktop_ai / filename
        if not target.exists():
            for f in desktop_ai.glob("*.*"):
                if filename.lower() in f.name.lower():
                    target = f
                    break

        if not target or not target.exists():
            return f"File '{filename}' not found."

        try:
            if target.suffix.lower() in {".txt", ".md", ".json", ".csv", ".py", ".html"}:
                with open(target, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()[:3000]
            return f"File '{target.name}' exists ({round(target.stat().st_size / 1024, 1)} KB). Binary format preview not available as plain text."
        except Exception as e:
            return f"Error reading file: {e}"

    @classmethod
    def upload_file(cls, local_path: str) -> str:
        p = Path(local_path)
        if not p.exists():
            return f"Local file '{local_path}' does not exist."
        # Store in NIGHTFALLAI cloud staging
        dest = Path.home() / "Desktop" / "NIGHTFALLAI" / p.name
        try:
            import shutil
            shutil.copy2(p, dest)
            return f"File '{p.name}' uploaded to NIGHTFALL Workspace storage."
        except Exception as e:
            return f"Upload error: {e}"


# ── Unified Tool Dispatcher ─────────────────────────────────────────────────

def google_workspace(
    parameters: dict,
    response=None,
    player=None,
    speak: Optional[Callable[[str], None]] = None,
    session_memory=None,
) -> str:
    """
    Unified Google Workspace controller for Gmail, Google Calendar, and Google Drive.
    """
    params = parameters or {}
    service = params.get("service", "").lower().strip()
    action = params.get("action", "").lower().strip()

    # Normalize service from action if omitted
    if not service:
        if any(w in action for w in ("gmail", "email", "mail")):
            service = "gmail"
        elif any(w in action for w in ("calendar", "meeting", "event", "schedule")):
            service = "calendar"
        elif any(w in action for w in ("drive", "file")):
            service = "drive"
        else:
            service = "gmail"

    # Strip prefix from action if present
    action = re.sub(r"^(workspace_|gmail_|calendar_|drive_)", "", action)

    result = "Unknown Google Workspace action."

    if service == "gmail":
        if action in {"list", "messages", "inbox", "get_messages"}:
            query = params.get("query", "ALL")
            max_r = int(params.get("max_results", 5))
            if speak:
                speak("Checking your Gmail inbox, sir...")
            result = GmailEngine.list_messages(query=query, max_results=max_r)

        elif action in {"unread", "check_unread"}:
            if speak:
                speak("Checking your unread emails...")
            result = GmailEngine.list_messages(query="UNSEEN", max_results=int(params.get("max_results", 5)))

        elif action in {"search", "find"}:
            query = params.get("query", "ALL")
            if speak:
                speak(f"Searching your emails for {query}...")
            result = GmailEngine.list_messages(query=query, max_results=int(params.get("max_results", 5)))

        elif action in {"read", "get", "view"}:
            msg_id = str(params.get("message_id") or params.get("id") or "1")
            result = GmailEngine.read_message(msg_id)

        elif action in {"send", "compose", "draft"}:
            to = params.get("to") or params.get("receiver") or ""
            subject = params.get("subject", "Message from NIGHTFALL AI")
            body = params.get("body") or params.get("message") or ""
            if not to:
                return "Recipient email address ('to') is required."
            if speak:
                speak(f"Sending email to {to}...")
            result = GmailEngine.send_message(to=to, subject=subject, body=body)

        else:
            result = f"Unknown Gmail action: {action}"

    elif service == "calendar":
        if action in {"list", "upcoming", "events", "check"}:
            if speak:
                speak("Checking your calendar schedule...")
            result = GoogleCalendarEngine.list_events(days=int(params.get("days", 7)))

        elif action in {"create", "add", "schedule"}:
            title = params.get("title") or params.get("summary") or "New Event"
            date = params.get("date", "today")
            time_str = params.get("time", "12:00")
            dur = int(params.get("duration_minutes", 30))
            desc = params.get("description", "")
            if speak:
                speak(f"Scheduling {title} on your calendar...")
            result = GoogleCalendarEngine.create_event(title, date, time_str, dur, desc)

        elif action in {"delete", "remove", "cancel"}:
            event_id = str(params.get("event_id") or params.get("id") or "")
            result = GoogleCalendarEngine.delete_event(event_id)

        else:
            result = f"Unknown Calendar action: {action}"

    elif service == "drive":
        if action in {"search", "find", "list"}:
            q = params.get("query") or params.get("name") or ""
            result = GoogleDriveEngine.search_files(q)

        elif action in {"read", "open", "view"}:
            filename = params.get("filename") or params.get("name") or ""
            result = GoogleDriveEngine.read_file(filename)

        elif action in {"upload", "save"}:
            path = params.get("path") or ""
            result = GoogleDriveEngine.upload_file(path)

        else:
            result = f"Unknown Drive action: {action}"

    else:
        result = f"Unknown Google Workspace service: {service}"

    if player and hasattr(player, "write_log"):
        player.write_log(f"[Workspace] {result[:80]}")

    return result


# ── Background Email Polling Daemon ──────────────────────────────────────────

_email_daemon_running = False
_email_daemon_thread: Optional[threading.Thread] = None
_email_prompt_callback: Optional[Callable[[str, str, str], None]] = None
_email_last_seen_ids: set[str] = set()
_email_initialized: bool = False


def set_email_prompt_callback(callback: Callable[[str, str, str], None]):
    """
    Sets callback for new incoming emails: callback(sender_name, subject, msg_id)
    """
    global _email_prompt_callback
    _email_prompt_callback = callback


def clean_sender_name(raw_from: str) -> str:
    raw_from = (raw_from or "").strip()
    if "<" in raw_from and ">" in raw_from:
        name = raw_from.split("<")[0].strip().strip('"').strip("'")
        if name:
            return name
        return raw_from.split("<")[1].split(">")[0].strip()
    return raw_from


def _email_poll_cycle():
    global _email_last_seen_ids, _email_initialized
    addr, pw = get_stored_gmail_credentials()
    if not addr or not pw:
        return

    try:
        mail = imaplib.IMAP4_SSL(GmailEngine.IMAP_HOST, GmailEngine.IMAP_PORT)
        mail.login(addr, pw)
        mail.select("INBOX", readonly=True)

        status, data = mail.search(None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            mail.logout()
            _email_initialized = True
            return

        current_unseen = [mid.decode() for mid in data[0].split()]

        # On first cycle, record current unread messages so we only announce new arrivals
        if not _email_initialized:
            _email_last_seen_ids = set(current_unseen)
            _email_initialized = True
            mail.logout()
            return

        new_ids = [mid for mid in current_unseen if mid not in _email_last_seen_ids]
        for mid in new_ids:
            _email_last_seen_ids.add(mid)
            try:
                res, msg_data = mail.fetch(mid.encode(), "(RFC822.HEADER)")
                if res != "OK" or not msg_data or not msg_data[0]:
                    continue
                parsed = email.message_from_bytes(msg_data[0][1])
                sender_raw = _decode_header_str(parsed.get("From", "Unknown"))
                sender = clean_sender_name(sender_raw)
                subject = _decode_header_str(parsed.get("Subject", "No Subject"))

                logger.info(f"[EmailDaemon] New incoming email from {sender}: {subject}")

                if _email_prompt_callback:
                    _email_prompt_callback(sender, subject, mid)
                else:
                    try:
                        from actions.attention_monitor import speak_native
                        speak_native(f"You received a new email from {sender} with subject: {subject}.")
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"[EmailDaemon] Error parsing incoming email {mid}: {e}")

        mail.logout()
    except Exception as e:
        logger.debug(f"[EmailDaemon] Poll cycle notice: {e}")


def _email_daemon_loop(poll_interval: int = 25):
    global _email_daemon_running
    logger.info("[EmailDaemon] Background email watcher loop running.")
    while _email_daemon_running:
        try:
            _email_poll_cycle()
        except Exception as e:
            logger.debug(f"[EmailDaemon] Loop error: {e}")

        for _ in range(poll_interval):
            if not _email_daemon_running:
                break
            time.sleep(1)


def start_email_daemon(poll_interval: int = 25):
    global _email_daemon_running, _email_daemon_thread
    if _email_daemon_running:
        return
    _email_daemon_running = True
    _email_daemon_thread = threading.Thread(target=_email_daemon_loop, args=(poll_interval,), daemon=True)
    _email_daemon_thread.start()
    logger.info("[EmailDaemon] Background email daemon started.")


def stop_email_daemon():
    global _email_daemon_running
    _email_daemon_running = False
    logger.info("[EmailDaemon] Background email daemon stopped.")
