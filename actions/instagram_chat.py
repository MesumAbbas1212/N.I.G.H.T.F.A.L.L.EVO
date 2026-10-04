from core.user_paths import get_user_data_dir
# actions/instagram_chat.py
"""
Instagram Chat Integration for NIGHTFALL AI.

Listens for incoming DMs on Instagram and replies using NIGHTFALL's core generation.
"""

import threading
import time
import json
import os
from pathlib import Path

# Provide a fallback if instagrapi fails to install or load
try:
    from instagrapi import Client
    INSTAGRAPI_AVAILABLE = True
except ImportError:
    INSTAGRAPI_AVAILABLE = False

_reply_callback = None
_thread = None
_running = False
_client = None
_last_processed_msgs = {}
_auto_threads = set()

def ig_log(msg):
    try:
        print(msg)
    except Exception:
        print(msg.encode('ascii', 'replace').decode('ascii'))
    with open("ig_debug.log", "a", encoding="utf-8") as f:
        f.write(msg + "\n")

def get_base_dir():
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR = get_base_dir()
CONFIG_PATH = get_user_data_dir() / "config" / "api_keys.json"
SESSION_PATH = get_user_data_dir() / "config" / "ig_session.json"

def set_ig_prompt_callback(callback):
    from actions.instagram_mcp import set_ig_prompt_callback as _set
    return _set(callback)

def add_auto_thread(thread_id):
    from actions.instagram_mcp import add_auto_thread as _add
    return _add(thread_id)

def send_direct_reply(thread_id, text):
    from actions.instagram_mcp import send_direct_reply as _send
    return _send(thread_id, text)

def get_recent_messages(amount=5) -> str:
    from actions.instagram_mcp import get_recent_messages as _get
    return _get(amount)

def _load_credentials():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("instagram_username", ""), data.get("instagram_password", "")
    except Exception:
        return "", ""

def _instagram_loop():
    global _client, _running
    
    username, password = _load_credentials()
    if not username or not password:
        print("[InstagramChat] Missing credentials in config/api_keys.json. Stopping daemon.")
        _running = False
        return

    _client = Client()
    _client.delay_range = [1, 3] # Keep delay low to avoid rate limits but not seem totally like a bot

    ig_log(f"[InstagramChat] Attempting login for {username}...")
    try:
        if SESSION_PATH.exists():
            _client.load_settings(SESSION_PATH)
            
        try:
            _client.login(username, password)
            _client.get_timeline_feed() # Validate session
        except Exception as e:
            ig_log(f"[InstagramChat] Session invalid, relogging: {e}")
            _client.login(username, password, relogin=True)
            
        _client.dump_settings(SESSION_PATH)
        ig_log("[InstagramChat] Login successful. Listening for DMs...")
    except Exception as e:
        ig_log(f"[InstagramChat] Login failed: {e}")
        _running = False
        return

    # To avoid being rate-limited too fast, poll every 20 seconds
    POLL_INTERVAL = 20
    
    while _running:
        try:
            # Get recent threads
            threads = _client.direct_threads(amount=10)
            for thread in threads:
                latest_msg = thread.messages[0] if thread.messages else None
                
                # Check if it's sent by someone else and we haven't replied
                if latest_msg and str(latest_msg.user_id) != str(_client.user_id):
                    if _last_processed_msgs.get(thread.id) != latest_msg.id:
                        _last_processed_msgs[thread.id] = latest_msg.id
                        
                        text = latest_msg.text
                        sender_username = thread.users[0].username if thread.users else "Unknown"
                        
                        ig_log(f"[InstagramChat] New message from {sender_username}: {text}")
                        
                        if _reply_callback:
                            is_auto = str(thread.id) in _auto_threads
                            ai_response = _reply_callback(str(thread.id), sender_username, text, is_auto)
                            if ai_response:
                                ig_log(f"[InstagramChat] Replying to {sender_username}: {ai_response[:40]}...")
                                _client.direct_send(ai_response, thread_ids=[thread.id])
                                
                        time.sleep(2)
            
            # Check pending inbox (message requests) and approve them
            pending = _client.direct_pending_inbox()
            for thread in pending:
                ig_log(f"[InstagramChat] Approving message request from {thread.users[0].username if thread.users else 'Unknown'}")
                _client.direct_pending_approve(thread.id)
                time.sleep(1)
                        
        except Exception as e:
            ig_log(f"[InstagramChat] Error in polling loop: {e}")
            
        time.sleep(POLL_INTERVAL)

def start_daemon():
    from actions.instagram_mcp import start_daemon as _start
    return _start()

def stop_daemon():
    from actions.instagram_mcp import stop_daemon as _stop
    return _stop()
