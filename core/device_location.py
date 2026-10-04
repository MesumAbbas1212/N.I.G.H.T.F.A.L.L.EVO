from core.user_paths import get_user_data_dir
# core/device_location.py

import json
import os
import platform
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Dict, Any, Optional

_BASE_DIR = Path(__file__).resolve().parent.parent
_CONFIG_DIR = get_user_data_dir() / "config"
_CACHE_FILE = _CONFIG_DIR / "device_location_cache.json"
_SETTINGS_FILE = _CONFIG_DIR / "app_settings.json"

_MEMORY_CACHE: Optional[Dict[str, Any]] = None
_MEMORY_CACHE_TIME: float = 0
_CACHE_TTL: float = 1800.0  # 30 minutes


def _read_manual_override() -> Optional[str]:
    """Checks if the user configured a manual city override in app_settings.json."""
    if _SETTINGS_FILE.exists():
        try:
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                override = data.get("location_override") or data.get("city")
                if override and isinstance(override, str) and override.strip():
                    return override.strip()
        except Exception:
            pass
    return None


def _read_disk_cache() -> Optional[Dict[str, Any]]:
    """Reads cached location from disk if fresh."""
    if _CACHE_FILE.exists():
        try:
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
                cached_time = cached.get("timestamp", 0)
                if time.time() - cached_time < _CACHE_TTL:
                    return cached
        except Exception:
            pass
    return None


def _write_disk_cache(data: Dict[str, Any]) -> None:
    """Writes detected location to disk cache."""
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data_copy = dict(data)
        data_copy["timestamp"] = time.time()
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data_copy, f, indent=2)
    except Exception:
        pass


def _detect_via_windows_api() -> Optional[Dict[str, Any]]:
    """Attempts hardware/Wi-Fi location detection via Windows System.Device.Location (High Accuracy)."""
    if platform.system() != "Windows":
        return None

    script = """
    Add-Type -AssemblyName System.Device
    $w = New-Object System.Device.Location.GeoCoordinateWatcher(1)
    $w.Start()
    Start-Sleep -Milliseconds 1800
    $pos = $w.Position.Location
    Write-Output "$($pos.Latitude),$($pos.Longitude),$($w.Status)"
    """
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=4.5,
        )
        out = proc.stdout.strip()
        parts = out.split(",")
        if len(parts) >= 3 and parts[2].strip().lower() == "ready":
            lat = float(parts[0])
            lon = float(parts[1])
            if abs(lat) > 0.001 and abs(lon) > 0.001:
                city = _reverse_geocode_osm(lat, lon)
                if city:
                    return {
                        "status": "success",
                        "city": city,
                        "latitude": lat,
                        "longitude": lon,
                        "source": "windows_location",
                    }
    except Exception:
        pass
    return None


def _reverse_geocode_osm(lat: float, lon: float) -> Optional[str]:
    """Reverse geocodes coordinates using OpenStreetMap Nominatim."""
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}"
        req = urllib.request.Request(url, headers={"User-Agent": "NIGHTFALLAI-LocationEngine/1.0"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            addr = data.get("address", {})
            return (
                addr.get("city")
                or addr.get("town")
                or addr.get("suburb")
                or addr.get("borough")
                or addr.get("county")
                or addr.get("state_district")
            )
    except Exception:
        return None


def _detect_via_ip_services() -> Optional[Dict[str, Any]]:
    """Fast, accurate IP geolocation using multi-provider cascade."""
    endpoints = [
        ("ipwho.is", "https://ipwho.is/", "city", "latitude", "longitude", "region"),
        ("ip-api.com", "http://ip-api.com/json", "city", "lat", "lon", "regionName"),
        ("freeipapi.com", "https://freeipapi.com/api/json", "cityName", "latitude", "longitude", "regionName"),
    ]

    for name, url, city_k, lat_k, lon_k, reg_k in endpoints:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                city = data.get(city_k)
                if city and isinstance(city, str) and city.strip() and city.lower() != "none":
                    return {
                        "status": "success",
                        "city": city.strip(),
                        "region": data.get(reg_k),
                        "country": data.get("country") or data.get("countryName"),
                        "latitude": data.get(lat_k),
                        "longitude": data.get(lon_k),
                        "source": name,
                    }
        except Exception:
            continue

    return None


def get_device_location(force_refresh: bool = False) -> Dict[str, Any]:
    global _MEMORY_CACHE, _MEMORY_CACHE_TIME

    # 1. Manual user override check
    override = _read_manual_override()
    if override:
        return {
            "status": "success",
            "city": override,
            "source": "manual_override",
        }

    # 2. In-memory cache check
    if not force_refresh and _MEMORY_CACHE and (time.time() - _MEMORY_CACHE_TIME < _CACHE_TTL):
        return _MEMORY_CACHE

    # 3. Disk cache check
    if not force_refresh:
        disk_cache = _read_disk_cache()
        if disk_cache:
            _MEMORY_CACHE = disk_cache
            _MEMORY_CACHE_TIME = disk_cache.get("timestamp", time.time())
            return disk_cache

    # 4. Primary: High-Accuracy Windows Hardware / Wi-Fi Geolocation
    loc = _detect_via_windows_api()

    # 5. Secondary: Multi-provider IP Geolocation fallback
    if not loc:
        loc = _detect_via_ip_services()

    if loc:
        _MEMORY_CACHE = loc
        _MEMORY_CACHE_TIME = time.time()
        _write_disk_cache(loc)
        return loc

    # 6. Fallback if completely offline
    fallback_cache = _read_disk_cache()
    if fallback_cache:
        return fallback_cache

    return {
        "status": "fallback",
        "city": "Local Area",
        "source": "default_fallback",
    }


def get_device_city(default: str = "Local Area") -> str:
    loc = get_device_location()
    return loc.get("city") or default
