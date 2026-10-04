import os
import sys
import json
import urllib.request
import urllib.error
import threading
from pathlib import Path
import subprocess

GITHUB_REPO = "titechprabhasolutions/NIGHTFALL---personal"

def get_current_version() -> str:
    try:
        if hasattr(sys, '_MEIPASS'):
            base_dir = Path(sys._MEIPASS)
        else:
            base_dir = Path(__file__).resolve().parent.parent
            
        version_file = base_dir / "app_version.txt"
        if version_file.exists():
            return version_file.read_text(encoding='utf-8').strip()
    except Exception:
        pass
    return "1.0.0"

def parse_semver(ver: str) -> tuple:
    if ver.startswith('v'):
        ver = ver[1:]
    parts = ver.split('.')
    return tuple(int(x) if x.isdigit() else 0 for x in parts)

def check_for_updates() -> dict | None:
    """Checks the GitHub API for a new release. Returns release data if an update is available."""
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(url, headers={'User-Agent': 'NIGHTFALLEcho-OTA'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            latest_version = data.get('tag_name', '')
            if not latest_version:
                return None
                
            current = parse_semver(get_current_version())
            latest = parse_semver(latest_version)
            
            if latest > current:
                assets = data.get('assets', [])
                setup_asset = next((a for a in assets if 'setup' in a.get('name', '').lower() or 'installer' in a.get('name', '').lower()), None)
                if not setup_asset:
                    setup_asset = next((a for a in assets if a.get('name', '').endswith('.exe')), None)
                    
                if setup_asset:
                    return {
                        'version': latest_version,
                        'url': setup_asset.get('browser_download_url'),
                        'size': setup_asset.get('size', 0),
                        'notes': data.get('body', '')
                    }
    except Exception as e:
        print(f"[OTA] Failed to check for updates: {e}")
    return None

def download_and_apply_update(url: str, ui_callback=None):
    """Downloads the setup executable and triggers the silent installation."""
    try:
        from core.user_paths import get_user_data_dir
        update_dir = get_user_data_dir() / "updates"
        update_dir.mkdir(parents=True, exist_ok=True)
        setup_path = update_dir / "NIGHTFALLEcho_Setup_Update.exe"
        
        req = urllib.request.Request(url, headers={'User-Agent': 'NIGHTFALLEcho-OTA'})
        with urllib.request.urlopen(req, timeout=15) as response:
            total_size = int(response.info().get('Content-Length', 0))
            downloaded = 0
            
            with open(setup_path, 'wb') as f:
                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if ui_callback and total_size > 0:
                        progress = int((downloaded / total_size) * 100)
                        ui_callback(progress)
                        
        if setup_path.exists():
            print(f"[OTA] Launching silent updater: {setup_path}")
            # Ensure DETACHED_PROCESS to survive sys.exit
            DETACHED_PROCESS = 0x00000008
            subprocess.Popen([str(setup_path), "--silent"], creationflags=subprocess.CREATE_NO_WINDOW | DETACHED_PROCESS)
            sys.exit(0)
            
    except Exception as e:
        print(f"[OTA] Failed to download or apply update: {e}")
