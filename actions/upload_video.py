"""
NIGHTFALL AI — Multi-Platform Video Publisher & Content Optimizer.

Automates video uploading and publishing across TikTok Studio, YouTube Shorts,
and Instagram Reels. Discovers the latest video file from Desktop/Downloads/Videos,
generates viral AI hooks, descriptions, and high-ranking SEO hashtags,
copies captions to the clipboard, reveals the video file in Windows Explorer,
and launches the studio uploader for seamless one-click publishing.
"""

import json
import logging
import os
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import pyperclip
    _HAS_PYPERCLIP = True
except ImportError:
    _HAS_PYPERCLIP = False

logger = logging.getLogger("upload_video")

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR = _get_base_dir()

PLUGIN = {
    "name": "upload_video",
    "description": (
        "Prepares, optimizes, and publishes video files to TikTok, YouTube Shorts, or Instagram. "
        "Locates recent video files automatically (or from a path), generates viral captions and SEO "
        "hashtags, copies them to the clipboard, and launches the creator upload studio. "
        "Use whenever the user asks to upload, post, or publish a video."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "platform": {
                "type": "STRING",
                "description": "Target platform: 'tiktok', 'youtube', 'instagram', or 'all' (defaults to 'tiktok').",
            },
            "description": {
                "type": "STRING",
                "description": "Topic, concept, or caption brief for the video (in user's language).",
            },
            "video_path": {
                "type": "STRING",
                "description": "Optional exact path to the video file to upload. If omitted, finds the latest video automatically.",
            },
        },
        "required": ["description"],
    },
}

_PLATFORM_URLS = {
    "tiktok": "https://www.tiktok.com/creator-center/upload",
    "youtube": "https://studio.youtube.com/channel/UC/videos/upload?d=ud",
    "instagram": "https://www.instagram.com/",
}

_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}

def _find_candidate_video(custom_path: Optional[str] = None) -> Optional[Path]:
    """Finds target video file: custom path, or most recent video in common folders."""
    if custom_path:
        p = Path(custom_path).expanduser().resolve()
        if p.exists() and p.is_file() and p.suffix.lower() in _VIDEO_EXTENSIONS:
            return p

    search_dirs = [
        Path.home() / "Desktop",
        Path.home() / "Downloads",
        Path.home() / "Videos",
    ]

    candidates = []
    for d in search_dirs:
        if d.exists():
            try:
                for f in d.iterdir():
                    if f.is_file() and f.suffix.lower() in _VIDEO_EXTENSIONS:
                        try:
                            candidates.append((f.stat().st_mtime, f))
                        except Exception:
                            pass
            except Exception:
                pass

    if not candidates:
        return None

    # Sort newest first
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]

def _generate_video_copy(brief: str, platform: str) -> dict:
    """Uses NIGHTFALL's AI client to generate high-performing hooks, captions, and tags."""
    system_prompt = (
        "You are NIGHTFALL AI's Social Media Video Director and Viral Copywriter. "
        f"Generate high-engagement publication metadata for {platform.title()}. "
        "Respond ONLY with a valid JSON object matching this schema:\n"
        "{\n"
        '  "hook_title": "Catchy 5-7 word high-CTR title or on-screen hook",\n'
        '  "caption": "2-3 engaging, conversational sentences for the post description",\n'
        '  "hashtags": ["#Tag1", "#Tag2", "#Tag3", "#Tag4", "#Tag5"],\n'
        '  "call_to_action": "e.g. Follow for more or drop your thoughts below",\n'
        '  "formatted_post": "Complete copy-pasteable text including caption, call to action, and hashtags"\n'
        "}"
    )

    try:
        from llm_client import client as ai_client
        res = ai_client.chat_json(
            prompt=f"Create viral video copy for: {brief}",
            system=system_prompt,
        )
        if isinstance(res, dict) and "formatted_post" in res:
            return res
    except Exception as e:
        logger.warning(f"AI copy generation via unified client failed: {e}")

    # Clean fallback copy
    tags = [f"#{platform}", "#viral", "#trending", "#fyp", "#contentcreator"]
    return {
        "hook_title": brief[:60],
        "caption": brief,
        "hashtags": tags,
        "call_to_action": "Check this out and follow for more!",
        "formatted_post": f"{brief}\n\n{' '.join(tags)}"
    }

def run(parameters: dict, player=None, speak=None, session_memory=None) -> str:
    """Main execution function for NIGHTFALL Video Publisher."""
    brief = (parameters.get("description") or parameters.get("query") or "").strip()
    platform_name = (parameters.get("platform") or "tiktok").strip().lower()
    custom_video_path = parameters.get("video_path")

    if platform_name not in _PLATFORM_URLS:
        if "yt" in platform_name or "short" in platform_name or "tube" in platform_name:
            platform_name = "youtube"
        elif "insta" in platform_name or "reel" in platform_name:
            platform_name = "instagram"
        else:
            platform_name = "tiktok"

    def _log(msg: str):
        if player and hasattr(player, "write_log"):
            try:
                player.write_log(msg)
            except Exception:
                pass
        logger.info(msg)

    _log(f"[Publisher] Locating video for {platform_name.title()} upload...")

    # Step 1: Locate Video File
    video_file = _find_candidate_video(custom_video_path)
    file_info = ""
    if video_file and video_file.exists():
        size_mb = video_file.stat().st_size / (1024 * 1024)
        file_info = f"'{video_file.name}' ({size_mb:.1f} MB)"
        _log(f"[Publisher] Found candidate video: {video_file} ({size_mb:.1f} MB)")
    else:
        file_info = "video file"

    # Step 2: Generate Viral Copy
    _log(f"[Publisher] Generating viral caption and hashtags for '{brief}'...")
    copy_data = _generate_video_copy(brief or "New video release", platform_name)

    hook = copy_data.get("hook_title", brief)
    full_copy = copy_data.get("formatted_post") or f"{copy_data.get('caption', brief)}\n\n{' '.join(copy_data.get('hashtags', []))}"

    # Step 3: Copy to Windows Clipboard
    clipboard_status = False
    if _HAS_PYPERCLIP:
        try:
            pyperclip.copy(full_copy)
            clipboard_status = True
            _log("[Publisher] Optimized caption & hashtags copied to clipboard.")
        except Exception as e:
            logger.warning(f"Failed to copy to clipboard: {e}")

    # Step 4: Reveal file in Windows Explorer
    if video_file and video_file.exists() and sys.platform == "win32":
        try:
            subprocess.Popen(["explorer.exe", f"/select,{str(video_file)}"])
            _log(f"[Publisher] Highlighted {video_file.name} in Windows Explorer for drag & drop.")
        except Exception as e:
            logger.warning(f"Could not reveal file in explorer: {e}")

    # Step 5: Launch Creator Upload Portal
    upload_url = _PLATFORM_URLS.get(platform_name, _PLATFORM_URLS["tiktok"])
    try:
        webbrowser.open(upload_url)
        _log(f"[Publisher] Launched {platform_name.title()} Creator Studio in browser.")
    except Exception as e:
        logger.error(f"Failed to open upload URL: {e}")

    # Step 6: Present Rich Card in NIGHTFALL UI
    ui_card = (
        f"### 🎬 {platform_name.upper()} VIDEO PUBLISHING ASSISTANT\n\n"
        f"- **Selected Video**: `{video_file.name if video_file else 'None located'}`\n"
        f"- **Path**: `{video_file if video_file else 'N/A'}`\n"
        f"- **Hook Title**: **{hook}**\n"
        f"- **Platform Studio**: [{platform_name.title()} Upload Page]({upload_url})\n\n"
        f"#### 📋 Generated Caption & Tags (Copied to Clipboard):\n"
        f"```text\n{full_copy}\n```\n\n"
        f"💡 **Next Steps**:\n"
        f"1. Drag `{video_file.name if video_file else 'your video'}` into the browser window.\n"
        f"2. Press **Ctrl + V** in the description box to paste your optimized caption and hashtags."
    )

    if player and hasattr(player, "show_content"):
        try:
            player.show_content("🚀 NIGHTFALL VIDEO PUBLISHER", ui_card)
        except Exception:
            pass

    spoken = (
        f"I've prepped your video {file_info} for {platform_name.title()}! "
        f"The studio uploader is open, and your viral caption and hashtags are copied to your clipboard. "
        f"Simply drag the file in and press Control V."
    )
    return spoken

# Aliases for dispatch
upload_video = run