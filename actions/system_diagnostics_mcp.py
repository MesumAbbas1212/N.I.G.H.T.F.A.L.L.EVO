"""
OS Hardware and System Diagnostics MCP for NIGHTFALL AI
Provides 100% local, on-demand hardware telemetry, process management,
battery diagnostics, and multi-monitor brightness controls with zero background API credits.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Union

import psutil

logger = logging.getLogger("SystemDiagnosticsMCP")

# Safe blocklist of critical Windows system processes that must never be terminated
PROTECTED_SYSTEM_PROCESSES = {
    "system",
    "system idle process",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "fontdrvhost.exe",
    "winlogon.exe",
    "dwm.exe",
    "explorer.exe",
    "spoolsv.exe",
    "taskhostw.exe",
}

# ── 1. Telemetry and Hardware Vitals ──────────────────────────────────────────

def get_battery_status() -> Dict[str, Any]:
    """Retrieves battery percentage, charging state, and estimated runtime."""
    try:
        battery = psutil.sensors_battery()
        if battery is None:
            return {
                "has_battery": False,
                "percent": 100,
                "power_plugged": True,
                "status_text": "Desktop / Connected to direct AC power (No battery detected)",
                "secs_left": -1,
            }

        plugged = bool(battery.power_plugged)
        percent = round(battery.percent, 1)
        secs_left = battery.secsleft

        if plugged:
            if percent >= 99:
                status_text = f"Battery is fully charged ({percent}%) and connected to AC power."
            else:
                status_text = f"Battery is charging at {percent}% on AC power."
        else:
            if secs_left > 0 and secs_left != psutil.POWER_TIME_UNLIMITED:
                hours = int(secs_left // 3600)
                mins = int((secs_left % 3600) // 60)
                status_text = f"Battery is at {percent}% on battery power (~{hours}h {mins}m remaining)."
            else:
                status_text = f"Battery is at {percent}% on battery power."

        return {
            "has_battery": True,
            "percent": percent,
            "power_plugged": plugged,
            "status_text": status_text,
            "secs_left": secs_left,
        }
    except Exception as e:
        logger.error(f"[Diagnostics] Error reading battery: {e}")
        return {"has_battery": False, "status_text": f"Error reading battery: {e}"}


def get_cpu_temp() -> Optional[float]:
    """Attempts to read CPU temperature via WMI / psutil sensors."""
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz", "cpu-thermal"]:
                if name in temps and temps[name]:
                    return round(temps[name][0].current, 1)
            for entries in temps.values():
                if entries:
                    return round(entries[0].current, 1)
    except Exception:
        pass

    if platform.system() == "Windows":
        try:
            import wmi
            w = wmi.WMI(namespace="root/wmi")
            tz = w.MSAcpi_ThermalZoneTemperature()
            if tz and len(tz) > 0:
                return round((tz[0].CurrentTemperature / 10.0) - 273.15, 1)
        except Exception:
            pass
    return None


def get_storage_status() -> List[Dict[str, Any]]:
    """Inspects storage disk partitions and available space."""
    drives = []
    try:
        partitions = psutil.disk_partitions(all=False)
        for part in partitions:
            try:
                usage = psutil.disk_usage(part.mountpoint)
                drives.append({
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total_gb": round(usage.total / (1024 ** 3), 1),
                    "used_gb": round(usage.used / (1024 ** 3), 1),
                    "free_gb": round(usage.free / (1024 ** 3), 1),
                    "percent_used": usage.percent,
                })
            except (PermissionError, OSError):
                continue
    except Exception as e:
        logger.warning(f"[Diagnostics] Disk check error: {e}")
    return drives


def get_display_brightness_status() -> Dict[str, Any]:
    """Inspects connected monitors and current brightness levels."""
    try:
        import screen_brightness_control as sbc
        monitors = sbc.list_monitors()
        brightness = sbc.get_brightness()
        if isinstance(brightness, int):
            brightness = [brightness]
        return {
            "monitors": monitors,
            "brightness_levels": brightness,
            "avg_brightness": round(sum(brightness) / len(brightness)) if brightness else None,
        }
    except Exception as e:
        try:
            cmd = 'powershell -Command "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness"'
            out = subprocess.check_output(cmd, shell=True, text=True, timeout=3).strip()
            if out.isdigit():
                val = int(out)
                return {"monitors": ["Primary Display"], "brightness_levels": [val], "avg_brightness": val}
        except Exception:
            pass
        return {"monitors": [], "brightness_levels": [], "error": str(e)}


def get_full_diagnostics() -> Dict[str, Any]:
    """Returns a complete snapshot of CPU, RAM, Battery, Storage, and Display vitals."""
    cpu_percent = psutil.cpu_percent(interval=0.2)
    cpu_count_logical = psutil.cpu_count(logical=True)
    cpu_count_physical = psutil.cpu_count(logical=False)
    cpu_temp = get_cpu_temp()

    ram = psutil.virtual_memory()
    battery = get_battery_status()
    storage = get_storage_status()
    display = get_display_brightness_status()

    boot_time = psutil.boot_time()
    uptime_secs = time.time() - boot_time
    uptime_str = f"{int(uptime_secs // 3600)}h {int((uptime_secs % 3600) // 60)}m"

    return {
        "cpu": {
            "usage_percent": round(cpu_percent, 1),
            "logical_cores": cpu_count_logical,
            "physical_cores": cpu_count_physical,
            "temp_c": cpu_temp,
        },
        "ram": {
            "usage_percent": round(ram.percent, 1),
            "used_gb": round(ram.used / (1024 ** 3), 2),
            "total_gb": round(ram.total / (1024 ** 3), 2),
            "available_gb": round(ram.available / (1024 ** 3), 2),
        },
        "battery": battery,
        "storage": storage,
        "display": display,
        "uptime": uptime_str,
        "running_processes_count": len(psutil.pids()),
    }


# ── 2. RAM and CPU Hog Analysis ───────────────────────────────────────────────

def get_top_processes(sort_by: str = "memory", limit: int = 5) -> List[Dict[str, Any]]:
    """
    Identifies top resource-consuming applications.
    Groups multiple processes of the same application (e.g. Chrome tabs/workers).
    """
    proc_dict: Dict[str, Dict[str, Any]] = {}

    for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent"]):
        try:
            info = p.info
            name = (info.get("name") or "Unknown").strip()
            pid = info.get("pid")
            mem_bytes = info.get("memory_info").rss if info.get("memory_info") else 0
            cpu = info.get("cpu_percent") or 0.0

            clean_name = name.lower()
            if clean_name not in proc_dict:
                proc_dict[clean_name] = {
                    "display_name": name,
                    "pids": [pid],
                    "total_memory_bytes": mem_bytes,
                    "total_cpu_percent": cpu,
                    "instance_count": 1,
                }
            else:
                proc_dict[clean_name]["pids"].append(pid)
                proc_dict[clean_name]["total_memory_bytes"] += mem_bytes
                proc_dict[clean_name]["total_cpu_percent"] += cpu
                proc_dict[clean_name]["instance_count"] += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    process_list = list(proc_dict.values())
    total_system_ram = psutil.virtual_memory().total

    for item in process_list:
        mem_bytes = item["total_memory_bytes"]
        item["memory_mb"] = round(mem_bytes / (1024 ** 2), 1)
        item["memory_gb"] = round(mem_bytes / (1024 ** 3), 2)
        item["memory_percent"] = round((mem_bytes / total_system_ram) * 100, 1)
        item["total_cpu_percent"] = round(item["total_cpu_percent"], 1)

    if sort_by.lower() in ("cpu", "processor"):
        sorted_procs = sorted(process_list, key=lambda x: x["total_cpu_percent"], reverse=True)
    else:
        sorted_procs = sorted(process_list, key=lambda x: x["total_memory_bytes"], reverse=True)

    return sorted_procs[:limit]


# ── 3. Safe Process Termination ─────────────────────────────────────────────

def kill_process(target: Union[str, int], force: bool = False) -> Dict[str, Any]:
    """
    Terminates an application or process safely by name or PID.
    Rejects attempts to terminate protected critical Windows system components.
    """
    target_str = str(target).strip()
    killed_pids: List[int] = []
    target_name = ""

    if target_str.isdigit():
        target_pid = int(target_str)
        if target_pid == os.getpid():
            return {
                "success": False,
                "message": "Security Guard: Cannot terminate NIGHTFALL AI's own process.",
            }
        try:
            p = psutil.Process(target_pid)
            p_name = p.name().lower()
            if p_name in PROTECTED_SYSTEM_PROCESSES:
                return {
                    "success": False,
                    "message": f"Security Guard: '{p_name}' (PID: {target_pid}) is a protected system process and cannot be terminated.",
                }
            target_name = p.name()
            if force:
                p.kill()
            else:
                p.terminate()
            killed_pids.append(target_pid)
        except psutil.NoSuchProcess:
            return {"success": False, "message": f"Process with PID {target_pid} was not found."}
        except Exception as e:
            return {"success": False, "message": f"Failed to terminate PID {target_pid}: {e}"}
    else:
        clean_target = target_str.lower()
        clean_target_exe = clean_target if clean_target.endswith(".exe") else clean_target + ".exe"

        if clean_target in PROTECTED_SYSTEM_PROCESSES or clean_target_exe in PROTECTED_SYSTEM_PROCESSES:
            return {
                "success": False,
                "message": f"Security Guard: '{target_str}' is an essential Windows system component and cannot be terminated.",
            }

        for p in psutil.process_iter(["pid", "name"]):
            try:
                p_name = (p.info.get("name") or "").lower()
                if clean_target in p_name or clean_target_exe in p_name:
                    if p_name in PROTECTED_SYSTEM_PROCESSES:
                        continue
                    if force:
                        p.kill()
                    else:
                        p.terminate()
                    killed_pids.append(p.info["pid"])
                    target_name = p.info.get("name") or target_str
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    if not killed_pids:
        return {"success": False, "message": f"No running processes found matching '{target_str}'."}

    action_word = "Force-killed" if force else "Terminated"
    return {
        "success": True,
        "target": target_name or target_str,
        "killed_count": len(killed_pids),
        "killed_pids": killed_pids,
        "message": f"Successfully {action_word.lower()} {len(killed_pids)} process(es) for '{target_name or target_str}'.",
    }


# ── 4. Display Brightness Control ───────────────────────────────────────────

def set_brightness(level: int, monitor: Optional[Union[int, str]] = None, relative: bool = False) -> Dict[str, Any]:
    """
    Adjusts brightness across all monitors or a specific display.
    Supports absolute percentage (0-100) or relative offset (+10, -20).
    """
    try:
        import screen_brightness_control as sbc
        current_levels = sbc.get_brightness()
        if isinstance(current_levels, int):
            current_levels = [current_levels]

        target_level = level
        if relative and current_levels:
            target_level = current_levels[0] + level

        target_level = max(0, min(100, int(target_level)))

        if monitor is not None:
            sbc.set_brightness(target_level, display=monitor)
            msg = f"Set display '{monitor}' brightness to {target_level}%."
        else:
            sbc.set_brightness(target_level)
            msg = f"Set display brightness to {target_level}% across connected monitor(s)."

        return {
            "success": True,
            "new_brightness": target_level,
            "message": msg,
        }
    except Exception as err:
        logger.warning(f"[Diagnostics] screen_brightness_control fallback to PowerShell: {err}")
        try:
            target_val = max(0, min(100, int(level)))
            ps_cmd = (
                f"(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods)"
                f".WmiSetBrightness(1, {target_val})"
            )
            subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True, timeout=5, check=True)
            return {
                "success": True,
                "new_brightness": target_val,
                "message": f"Set display brightness to {target_val}%.",
            }
        except Exception as ps_err:
            return {
                "success": False,
                "message": f"Unable to set brightness: {err} (PowerShell fallback: {ps_err})",
            }


# ── 5. Unified MCP Tool Dispatcher ──────────────────────────────────────────

def system_diagnostics(
    parameters: Optional[Dict[str, Any]] = None,
    player: Any = None,
    speak: Optional[Callable[[str], None]] = None,
) -> str:
    """
    Unified entry point for the OS Hardware and System Diagnostics MCP.
    """
    params = parameters or {}
    action = (params.get("action") or params.get("command") or "status").lower().strip()

    if action in ("ram_hogs", "ram", "memory", "top_ram"):
        limit = int(params.get("limit") or 5)
        hogs = get_top_processes(sort_by="memory", limit=limit)
        ram_info = psutil.virtual_memory()
        used_gb = round(ram_info.used / (1024 ** 3), 1)
        total_gb = round(ram_info.total / (1024 ** 3), 1)

        lines = [f"📊 RAM Usage: {ram_info.percent}% ({used_gb} GB / {total_gb} GB used)", "Top Memory Consuming Applications:"]
        spoken_apps = []
        for i, proc in enumerate(hogs, 1):
            count_str = f" ({proc['instance_count']} instances)" if proc["instance_count"] > 1 else ""
            lines.append(f"{i}. {proc['display_name']}{count_str} — {proc['memory_mb']} MB ({proc['memory_percent']}% RAM)")
            if i <= 3:
                spoken_apps.append(f"{proc['display_name']} using {proc['memory_mb']} megabytes")

        result = "\n".join(lines)
        if speak:
            spoken_summary = f"Your RAM is at {ram_info.percent} percent. Top consumers are " + ", and ".join(spoken_apps) + "."
            speak(spoken_summary)
        return result

    elif action in ("cpu_hogs", "cpu", "processor"):
        limit = int(params.get("limit") or 5)
        hogs = get_top_processes(sort_by="cpu", limit=limit)
        overall_cpu = psutil.cpu_percent(interval=0.2)
        lines = [f"⚡ CPU Usage: {overall_cpu}%", "Top CPU-Consuming Applications:"]
        spoken_apps = []
        for i, proc in enumerate(hogs, 1):
            lines.append(f"{i}. {proc['display_name']} — {proc['total_cpu_percent']}% CPU ({proc['memory_mb']} MB RAM)")
            if i <= 3:
                spoken_apps.append(f"{proc['display_name']} at {proc['total_cpu_percent']} percent")

        result = "\n".join(lines)
        if speak:
            speak(f"CPU load is at {overall_cpu} percent. Heaviest apps are " + ", and ".join(spoken_apps) + ".")
        return result

    elif action in ("kill", "terminate", "close_app", "end_process"):
        target = params.get("target") or params.get("app") or params.get("name") or params.get("pid")
        if not target:
            return "Please specify an application name or PID to terminate (e.g. target='chrome')."
        force = bool(params.get("force", False))
        res = kill_process(target, force=force)
        msg = res.get("message", "Done.")
        if speak:
            speak(msg)
        return msg

    elif action in ("brightness", "set_brightness", "dim", "brighten"):
        level = params.get("level") or params.get("value")
        monitor = params.get("monitor")
        relative = bool(params.get("relative", False))

        if level is None:
            status = get_display_brightness_status()
            levels = status.get("brightness_levels", [])
            monitors = status.get("monitors", [])
            if levels:
                msg = f"Current display brightness: {levels[0]}% across {len(monitors)} monitor(s)."
            else:
                msg = "Could not detect display brightness."
            if speak:
                speak(msg)
            return msg

        res = set_brightness(int(level), monitor=monitor, relative=relative)
        msg = res.get("message", "Done.")
        if speak:
            speak(msg)
        return msg

    elif action in ("battery", "power", "battery_health"):
        bat = get_battery_status()
        msg = bat.get("status_text", "Battery status unavailable.")
        if speak:
            speak(msg)
        return msg

    elif action in ("disk", "storage", "drives"):
        storage = get_storage_status()
        if not storage:
            return "No storage partitions accessible."
        lines = ["💾 Storage Status:"]
        for d in storage:
            lines.append(f"• Drive {d['device']} [{d['fstype']}]: {d['free_gb']} GB free of {d['total_gb']} GB ({d['percent_used']}% used)")
        result = "\n".join(lines)
        if speak:
            free_c = next((d['free_gb'] for d in storage if 'c' in d['device'].lower()), None)
            if free_c is not None:
                speak(f"Drive C has {free_c} gigabytes of free storage.")
            else:
                speak("Storage report generated, sir.")
        return result

    else:
        diag = get_full_diagnostics()
        cpu = diag["cpu"]
        ram = diag["ram"]
        bat = diag["battery"]
        disp = diag["display"]

        lines = [
            "🖥️ OS HARDWARE & SYSTEM DIAGNOSTICS REPORT",
            "─────────────────────────────────────────",
            f"⚡ CPU: {cpu['usage_percent']}% utilization ({cpu['logical_cores']} logical cores)" + (f" | Temp: {cpu['temp_c']}°C" if cpu['temp_c'] else ""),
            f"📊 RAM: {ram['usage_percent']}% used ({ram['used_gb']} GB / {ram['total_gb']} GB) — {ram['available_gb']} GB free",
            f"🔋 Power: {bat.get('status_text', 'N/A')}",
            f"⏱️ Uptime: {diag['uptime']} ({diag['running_processes_count']} active processes)",
        ]

        if disp.get("brightness_levels"):
            lines.append(f"🔆 Brightness: {disp['brightness_levels'][0]}%")

        storage = diag.get("storage", [])
        if storage:
            lines.append("💾 Drives:")
            for d in storage:
                lines.append(f"   • {d['device']} {d['free_gb']} GB free / {d['total_gb']} GB total")

        report = "\n".join(lines)
        if speak:
            speak(f"System status: CPU is at {cpu['usage_percent']} percent, RAM at {ram['usage_percent']} percent. {bat.get('status_text', '')}")
        return report
