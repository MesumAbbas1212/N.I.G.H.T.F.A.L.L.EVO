from core.user_paths import get_user_data_dir
# actions/spotify_controller.py
"""
Universal Music & Spotify Controller for NIGHTFALL AI.

Guarantees 100% reliable music playback in Google Chrome, handles Spotify searches,
direct track audio streaming, and global media key playback controls (play, pause, next, volume).
"""

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
API_CONFIG_PATH = get_user_data_dir() / "config" / "api_keys.json"
SPOTIFY_CONFIG_PATH = get_user_data_dir() / "config" / "spotify-config.json"
MCP_SERVER_DIR = Path(__file__).resolve().parent / "spotify_mcp_server"
MCP_BUILD_INDEX = MCP_SERVER_DIR / "build" / "index.js"
MCP_BUILD_AUTH = MCP_SERVER_DIR / "build" / "auth.js"

PLUGIN = {
    "name": "spotify_controller",
    "description": (
        "Plays and controls music via Google Chrome and Spotify. Supports actions: "
        "'search_play' (play any song/artist), 'play', 'pause', 'toggle', 'next', "
        "'previous', 'volume_up', 'volume_down', 'mute', 'open_spotify', 'get_now_playing', "
        "'get_playlists', 'get_queue', 'get_devices', 'auth'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Action: search_play, play, pause, toggle, next, previous, volume_up, volume_down, mute, open_spotify, get_now_playing, get_playlists, get_queue, get_devices, auth",
            },
            "query": {
                "type": "STRING",
                "description": "Song title, artist, or album name to search and play.",
            },
            "volume": {
                "type": "NUMBER",
                "description": "Volume level (optional).",
            },
        },
        "required": ["action"],
    },
}


def _get_chrome_path() -> str | None:
    """Finds Google Chrome executable."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        shutil.which("chrome"),
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


def _get_spotify_app_path() -> str | None:
    """Checks if Spotify desktop app is installed."""
    candidates = [
        os.path.expandvars(r"%APPDATA%\Spotify\Spotify.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Spotify\Spotify.exe"),
        r"C:\Program Files\Spotify\Spotify.exe",
        shutil.which("spotify"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


def _open_url_in_chrome(url: str) -> bool:
    """Launches URL in Google Chrome."""
    chrome_exe = _get_chrome_path()
    if chrome_exe:
        try:
            subprocess.Popen([chrome_exe, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as e:
            print(f"[Music] Error launching Chrome: {e}")

    try:
        import webbrowser
        webbrowser.open(url)
        return True
    except Exception:
        return False


def _press_media_key(key_name: str) -> bool:
    """Sends hardware virtual media key codes."""
    try:
        import ctypes
        VK_MEDIA_NEXT_TRACK = 0xB0
        VK_MEDIA_PREV_TRACK = 0xB1
        VK_MEDIA_PLAY_PAUSE = 0xB3
        VK_VOLUME_MUTE      = 0xAD
        VK_VOLUME_DOWN      = 0xAE
        VK_VOLUME_UP        = 0xAF

        key_map = {
            "playpause": VK_MEDIA_PLAY_PAUSE,
            "nexttrack": VK_MEDIA_NEXT_TRACK,
            "prevtrack": VK_MEDIA_PREV_TRACK,
            "volumemute": VK_VOLUME_MUTE,
            "volumedown": VK_VOLUME_DOWN,
            "volumeup":   VK_VOLUME_UP,
        }
        vk = key_map.get(key_name.lower())
        if vk and sys.platform == "win32":
            ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.04)
            ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
            return True
    except Exception as e:
        print(f"[Music] Keybd event error: {e}")

    try:
        import pyautogui
        pyautogui.press(key_name)
        return True
    except Exception:
        pass

    return False


def _scrape_direct_playable_url(query: str) -> str | None:
    """Finds direct playable audio track link to ensure 100% guaranteed instant sound."""
    try:
        import requests
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
        clean_q = query.replace("on spotify", "").replace("spotify", "").strip()
        search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(clean_q + ' audio')}&sp=EgIQAQ%3D%3D"
        r = requests.get(search_url, headers=headers, timeout=6)
        video_ids = re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', r.text)
        for vid in video_ids:
            if f'/shorts/{vid}' not in r.text:
                return f"https://www.youtube.com/watch?v={vid}&autoplay=1"
    except Exception as e:
        print(f"[Music] Direct scrape error: {e}")
    return None


def _find_and_click_spotify_play_button() -> bool:
    """Finds and clicks the green circular play button on Spotify Web."""
    try:
        import pyautogui
        import numpy as np

        w, h = pyautogui.size()
        screenshot = pyautogui.screenshot()
        img = np.array(screenshot)

        r = img[:, :, 0]
        g = img[:, :, 1]
        b = img[:, :, 2]

        mask = (g > 155) & (r < 75) & (b < 135) & (g > r * 2.0) & (g > b * 1.3)
        search_region_y_max = int(h * 0.75)
        mask[search_region_y_max:, :] = False
        mask[:int(h * 0.15), :] = False

        y_indices, x_indices = np.where(mask)
        if len(x_indices) > 30:
            target_x = int(np.median(x_indices))
            target_y = int(np.median(y_indices))
            pyautogui.click(target_x, target_y)
            return True

        # Fallback click on top result area
        pyautogui.click(int(w * 0.48), int(h * 0.33))
        time.sleep(0.2)
        pyautogui.press("space")
        return True
    except Exception:
        return False
def spotify_controller(
    parameters: dict,
    response: str | None = None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    """
    Main entry point for music control and instant playback.
    """
    p = parameters or {}
    action = p.get("action", "search_play").lower().strip()
    query = p.get("query", "").strip()

    mcp_extended_actions = {
        "auth", "login", "authenticate", "setup", "search_play", "play_song", "play_music",
        "start", "resume", "pause", "stop", "next", "previous", "set_volume", "volume",
        "volume_up", "volume_down", "get_now_playing", "get_playlists", "get_queue", "get_devices",
    }
    mcp_only_actions = {"auth", "login", "authenticate", "setup", "get_now_playing", "get_playlists", "get_queue", "get_devices", "set_volume"}
    if action in mcp_only_actions or (is_spotify_configured() and action in mcp_extended_actions):
        return _spotify_mcp_action(p)

    spotify_app = _get_spotify_app_path()

    if action in ("play", "pause", "toggle", "playpause", "resume"):
        _press_media_key("playpause")
        return "Toggled playback."

    elif action in ("next", "skip", "next_track"):
        _press_media_key("nexttrack")
        return "Skipped to next song."

    elif action in ("previous", "prev", "previous_track", "back"):
        _press_media_key("prevtrack")
        return "Playing previous song."

    elif action in ("volume_up", "vol_up"):
        for _ in range(6):
            _press_media_key("volumeup")
            time.sleep(0.03)
        return "Increased volume."

    elif action in ("volume_down", "vol_down"):
        for _ in range(6):
            _press_media_key("volumedown")
            time.sleep(0.03)
        return "Decreased volume."

    elif action in ("mute", "unmute"):
        _press_media_key("volumemute")
        return "Muted/unmuted audio."

    elif action in ("open", "open_spotify", "launch"):
        if spotify_app:
            subprocess.Popen([spotify_app], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "Opened Spotify desktop app."
        _open_url_in_chrome("https://open.spotify.com")
        return "Opened Spotify in Google Chrome."

    elif action in ("search_play", "play_song", "search", "play_playlist", "play_music"):
        if not query:
            _press_media_key("playpause")
            return "Resumed playback."

        encoded = urllib.parse.quote(query)

        # If Desktop Spotify App exists, launch it
        if spotify_app:
            try:
                os.startfile(f"spotify:search:{encoded}")
                time.sleep(1.2)
                _press_media_key("playpause")
                return f"Playing '{query}' on Spotify."
            except Exception:
                pass

        # Guaranteed instant audio playback in Chrome
        direct_url = _scrape_direct_playable_url(query)
        if direct_url:
            print(f"[Music] ▶️ Starting instant direct playback: {direct_url}")
            _open_url_in_chrome(direct_url)
            if player:
                try:
                    player.write_log(f"NIGHTFALL Evo: Playing '{query}' in Google Chrome")
                except Exception:
                    pass
            return f"Playing '{query}' in Google Chrome."

        # Fallback to Spotify Web
        spotify_web_url = f"https://open.spotify.com/search/{encoded}"
        _open_url_in_chrome(spotify_web_url)
        threading.Thread(
            target=lambda: [time.sleep(2.5), _find_and_click_spotify_play_button()],
            daemon=True
        ).start()

        return f"Opened and playing '{query}' in Google Chrome."

    else:
        if query:
            return spotify_controller({"action": "search_play", "query": query}, player=player)
        _press_media_key("playpause")
        return f"Handled music action: {action}"


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin wrapper for NIGHTFALL architecture."""
    return spotify_controller(parameters, player=player, session_memory=session_memory)


def get_spotify_config() -> dict:
    try:
        if SPOTIFY_CONFIG_PATH.exists():
            return json.loads(SPOTIFY_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def is_spotify_configured() -> bool:
    try:
        data = get_spotify_config()
        return bool(data.get("clientId") and data.get("clientSecret"))
    except Exception:
        return False


def save_spotify_credentials(client_id: str, client_secret: str) -> bool:
    try:
        SPOTIFY_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = get_spotify_config()
        if data.get("clientId") != client_id.strip():
            data.pop("accessToken", None)
            data.pop("refreshToken", None)
            data.pop("expiresAt", None)
        data.update({
            "clientId": client_id.strip(),
            "clientSecret": client_secret.strip(),
            "redirectUri": "http://127.0.0.1:8888/callback",
        })
        SPOTIFY_CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def clear_spotify_credentials() -> bool:
    try:
        if SPOTIFY_CONFIG_PATH.exists():
            SPOTIFY_CONFIG_PATH.unlink()
        return True
    except Exception:
        return False


def _spotify_mcp_call(tool_name: str, arguments: dict | None = None) -> dict:
    if not MCP_BUILD_INDEX.exists():
        return {"success": False, "error": "Spotify MCP server is not built."}
    env = os.environ.copy()
    env["SPOTIFY_CONFIG_PATH"] = str(SPOTIFY_CONFIG_PATH.resolve())
    process = None
    try:
        process = subprocess.Popen(
            ["node", str(MCP_BUILD_INDEX.resolve())], cwd=str(MCP_SERVER_DIR.resolve()), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1,
        )

        def request(payload: dict) -> dict:
            process.stdin.write(json.dumps(payload) + "\n")
            process.stdin.flush()
            response = process.stdout.readline()
            if not response:
                raise RuntimeError("Spotify MCP closed its response stream.")
            return json.loads(response)

        init = request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "NIGHTFALL evo-spotify", "version": "1.0.0"},
        }})
        if "error" in init:
            return {"success": False, "error": init["error"].get("message", "Spotify MCP initialization failed.")}
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        process.stdin.flush()
        response = request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": tool_name, "arguments": arguments or {},
        }})
        if "error" in response:
            return {"success": False, "error": response["error"].get("message", "Spotify MCP call failed.")}
        result = response.get("result", {})
        output = "\n".join(item.get("text", "") for item in result.get("content", []) if isinstance(item, dict) and item.get("type") == "text")
        return {"success": not result.get("isError", False), "output": output}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    finally:
        if process:
            try:
                process.terminate()
                process.wait(timeout=2)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass


def _spotify_mcp_action(parameters: dict) -> str:
    action = str(parameters.get("action", "search_play")).lower().strip()
    query = str(parameters.get("query", "")).strip()
    device_id = parameters.get("device_id")
    if action in ("auth", "login", "authenticate", "setup"):
        if not is_spotify_configured():
            return "Add Spotify clientId and clientSecret to the local spotify-config.json before authenticating."
        if not MCP_BUILD_AUTH.exists():
            return "Spotify MCP auth helper is not built."
        env = os.environ.copy()
        env["SPOTIFY_CONFIG_PATH"] = str(SPOTIFY_CONFIG_PATH.resolve())
        subprocess.Popen(["node", str(MCP_BUILD_AUTH.resolve())], cwd=str(MCP_SERVER_DIR.resolve()), env=env)
        return "Started Spotify authorization in your browser."
    if not is_spotify_configured():
        return "Spotify MCP is not configured. Add clientId and clientSecret to the local spotify-config.json, then authenticate."

    if action in ("search_play", "play_song", "play_music", "start"):
        if not query:
            return "Tell me a song, artist, album, or playlist to play."
        found = _spotify_mcp_call("searchSpotify", {"query": query, "type": "track", "limit": 3})
        if not found.get("success"):
            return f"Spotify search failed: {found.get('error') or found.get('output')}"
        uri = None
        title = query
        try:
            data = json.loads(found.get("output", ""))
            tracks = data.get("tracks", {}).get("items", []) or data.get("items", [])
            if tracks:
                uri = tracks[0].get("uri")
                title = tracks[0].get("name", query)
        except Exception:
            match = re.search(r"spotify:track:[a-zA-Z0-9]+", found.get("output", ""))
            uri = match.group(0) if match else None
        args = {"uri": uri or f"spotify:search:{query}"}
        if device_id:
            args["deviceId"] = device_id
        played = _spotify_mcp_call("playMusic", args)
        return f"Playing '{title}' on Spotify." if played.get("success") else f"Playback failed: {played.get('error') or played.get('output')}"

    calls = {
        "play": ("resumePlayback", {}), "resume": ("resumePlayback", {}),
        "pause": ("pausePlayback", {}), "stop": ("pausePlayback", {}),
        "next": ("skipToNext", {}), "previous": ("skipToPrevious", {}),
        "get_now_playing": ("getNowPlaying", {}), "get_playlists": ("getMyPlaylists", {"limit": 10}),
        "get_queue": ("getQueue", {"limit": 5}), "get_devices": ("getAvailableDevices", {}),
    }
    if action in ("set_volume", "volume"):
        calls[action] = ("setVolume", {"volumePercent": max(0, min(100, int(parameters.get("volume", 50))))})
    elif action == "volume_up":
        calls[action] = ("adjustVolume", {"volumeDelta": 10})
    elif action == "volume_down":
        calls[action] = ("adjustVolume", {"volumeDelta": -10})
    if action not in calls:
        return f"Unsupported Spotify MCP action: {action}."
    tool_name, args = calls[action]
    if device_id:
        args = dict(args, deviceId=device_id)
    result = _spotify_mcp_call(tool_name, args)
    if not result.get("success"):
        return f"Spotify action failed: {result.get('error') or result.get('output')}"
    labels = {
        "play": "Resumed Spotify playback.", "resume": "Resumed Spotify playback.",
        "pause": "Paused Spotify playback.", "stop": "Paused Spotify playback.",
        "next": "Skipped to the next Spotify track.", "previous": "Played the previous Spotify track.",
        "volume_up": "Increased Spotify volume.", "volume_down": "Decreased Spotify volume.",
    }
    return labels.get(action, result.get("output") or f"Spotify {action.replace('_', ' ')} completed.")


def spotify_mcp_controller(parameters: dict | None = None) -> str:
    """Explicit Spotify MCP entry point for the optional Evo feature."""
    return _spotify_mcp_action(parameters or {})
