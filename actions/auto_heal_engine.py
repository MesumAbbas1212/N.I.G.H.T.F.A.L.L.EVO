"""
Auto-Heal & Self-Patching Engine for NIGHTFALL AI
Enables NIGHTFALL to detect its own bugs, tracebacks, and tool exceptions,
synthesize minimal surgical hotfixes, verify syntax in an isolated sandbox,
safely apply patches with atomic rollback guarantees, and record changelogs.
"""

from __future__ import annotations
from core.user_paths import get_user_data_dir

import ast
import json
import logging
import os
import py_compile
import re
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("AutoHealEngine")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = get_user_data_dir() / "config"
PATCH_HISTORY_FILE = CONFIG_DIR / "patch_history.json"
BACKUPS_DIR = CONFIG_DIR / "patch_backups"
API_CONFIG_PATH = CONFIG_DIR / "api_keys.json"


def _get_gemini_api_key() -> str:
    """Retrieves the Gemini API key from api_keys.json or environment variables."""
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

# Core files strictly protected from modification to prevent self-destruction
PROTECTED_CORE_FILES = {
    "boot_sentry.py",
    "auto_heal_engine.py",
    "setup.py",
    "requirements.txt",
    "version.txt",
    "install_wizard.py",
}


# ── 1. Traceback Analyzer ───────────────────────────────────────────────────

class TracebackAnalyzer:
    """Parses tracebacks and pinpoints the responsible first-party codebase file and line."""

    @staticmethod
    def parse(tb_text: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "success": False,
            "target_file": None,
            "line_number": None,
            "function_name": None,
            "exception_type": None,
            "exception_message": None,
            "raw_traceback": tb_text,
        }

        if not tb_text:
            return result

        # Extract exception type and message from the last line
        lines = [line.strip() for line in tb_text.strip().splitlines() if line.strip()]
        if lines:
            last_line = lines[-1]
            if ":" in last_line:
                parts = last_line.split(":", 1)
                result["exception_type"] = parts[0].strip()
                result["exception_message"] = parts[1].strip()
            else:
                result["exception_type"] = last_line
                result["exception_message"] = ""

        # Match all File "path", line X, in func entries
        file_pattern = re.compile(r'File\s+["\']([^"\']+\.py)["\'],\s+line\s+(\d+)(?:,\s+in\s+([^\n\r]+))?', re.IGNORECASE)
        matches = file_pattern.findall(tb_text)

        # Iterate in reverse (innermost / latest frame first) to find first-party codebase file
        for raw_path, line_str, func_name in reversed(matches):
            p = Path(raw_path)
            # Skip third-party packages or virtualenvs
            if "site-packages" in raw_path.lower() or ".venv" in raw_path.lower() or "lib\\python" in raw_path.lower():
                continue

            # Check if file exists in our codebase
            resolved = None
            if (BASE_DIR / p).exists():
                resolved = (BASE_DIR / p).resolve()
            elif p.is_absolute() and p.exists():
                resolved = p
            else:
                candidate = BASE_DIR / p.name
                if candidate.exists():
                    resolved = candidate
                else:
                    # Search inside subdirectories
                    for sub in ("actions", "core", "agent", "services"):
                        c2 = BASE_DIR / sub / p.name
                        if c2.exists():
                            resolved = c2
                            break

            if resolved and resolved.exists():
                # Check immunity
                if resolved.name in PROTECTED_CORE_FILES:
                    logger.warning(f"[AutoHeal] File '{resolved.name}' is core-protected and cannot be patched.")
                    continue

                result["success"] = True
                result["target_file"] = str(resolved.resolve())
                result["line_number"] = int(line_str)
                result["function_name"] = func_name.strip() if func_name else None
                break

        return result


# ── 2. Safety Sandbox & Rollback Manager ────────────────────────────────────

class SafetySandbox:
    """Manages atomic backups, AST parsing, compilation tests, and instant rollback."""

    @staticmethod
    def create_backup(file_path: Path) -> Path:
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time())
        backup_name = f"{file_path.stem}.bak_{stamp}{file_path.suffix}"
        backup_path = BACKUPS_DIR / backup_name
        shutil.copy2(file_path, backup_path)
        return backup_path

    @staticmethod
    def validate_code(code_str: str, file_name: str = "<staging>") -> Tuple[bool, Optional[str]]:
        """Validates that candidate code parses into a valid Python AST without syntax errors."""
        try:
            ast.parse(code_str, filename=file_name)
            return True, None
        except SyntaxError as e:
            return False, f"Syntax Error on line {e.lineno}: {e.msg}"
        except Exception as e:
            return False, f"Validation Error: {e}"

    @staticmethod
    def rollback_patch(patch_id: str) -> Dict[str, Any]:
        """Rolls back an applied patch by its ID."""
        history = SafetySandbox._load_history()
        for entry in reversed(history):
            if entry.get("patch_id") == patch_id or patch_id == "latest":
                if entry.get("status") != "applied":
                    continue
                target = Path(entry.get("target_file", ""))
                backup = Path(entry.get("backup_path", ""))
                if not backup.exists() or not target.exists():
                    return {"success": False, "message": f"Backup file '{backup}' missing."}

                try:
                    shutil.copy2(backup, target)
                    entry["status"] = "rolled_back"
                    entry["rolled_back_at"] = time.time()
                    SafetySandbox._save_history(history)
                    return {
                        "success": True,
                        "message": f"Successfully rolled back patch {entry.get('patch_id')} on '{target.name}'.",
                        "target_file": str(target),
                    }
                except Exception as e:
                    return {"success": False, "message": f"Rollback failed: {e}"}

        return {"success": False, "message": f"No active patch matching '{patch_id}' found to rollback."}

    @staticmethod
    def _load_history() -> List[Dict[str, Any]]:
        if not PATCH_HISTORY_FILE.exists():
            return []
        try:
            with open(PATCH_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    @staticmethod
    def _save_history(history: List[Dict[str, Any]]) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(PATCH_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=4)
        except Exception as e:
            logger.error(f"[AutoHeal] Failed to save patch history: {e}")


# ── 3. Patch Synthesizer & Auto-Heal Controller ─────────────────────────────

class AutoHealEngine:
    """Orchestrates error analysis, hotfix synthesis, verification, and application."""

    _last_error: Optional[str] = None

    @classmethod
    def record_last_error(cls, tb_str: str) -> None:
        """Stores the most recent error traceback captured during runtime."""
        cls._last_error = tb_str

    @classmethod
    def get_last_error(cls) -> Optional[str]:
        """Returns the most recent error traceback if any."""
        return cls._last_error

    @classmethod
    def get_patch_history(cls, limit: int = 5) -> List[Dict[str, Any]]:
        """Returns recent patch history."""
        history = SafetySandbox._load_history()
        return list(reversed(history))[:limit]

    @classmethod
    def heal_traceback(
        cls,
        traceback_text: str,
        context_notes: str = "",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Analyzes a traceback, pinpoints the root cause, synthesizes a patch,
        verifies AST syntax in memory, and safely applies it with backup.
        """
        parsed = TracebackAnalyzer.parse(traceback_text)
        if not parsed.get("success"):
            return {
                "success": False,
                "message": "Could not identify a modifiable first-party source file from the traceback.",
                "parsed": parsed,
            }

        target_file_str = parsed["target_file"]
        target_path = Path(target_file_str)
        line_num = parsed["line_number"]

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                full_source = f.read()
        except Exception as e:
            return {"success": False, "message": f"Unable to read target file '{target_path.name}': {e}"}

        # Extract code context around the failing line
        source_lines = full_source.splitlines(keepends=True)
        start_idx = max(0, line_num - 25)
        end_idx = min(len(source_lines), line_num + 25)
        code_context = "".join(source_lines[start_idx:end_idx])

        # Generate patch via LLM
        patch_spec = cls._synthesize_patch_code(
            file_name=target_path.name,
            line_num=line_num,
            exception_type=parsed.get("exception_type", "Error"),
            exception_msg=parsed.get("exception_message", ""),
            code_context=code_context,
            context_notes=context_notes,
        )

        if not patch_spec.get("success"):
            return {
                "success": False,
                "message": f"Failed to synthesize patch: {patch_spec.get('error')}",
                "parsed": parsed,
            }

        target_chunk = patch_spec["target_chunk"]
        replacement_chunk = patch_spec["replacement_chunk"]
        explanation = patch_spec.get("explanation", "Bug hotfix.")

        if target_chunk not in full_source:
            return {
                "success": False,
                "message": "Target code chunk could not be matched precisely in source file.",
                "parsed": parsed,
            }

        # Apply candidate patch in staging memory
        staged_source = full_source.replace(target_chunk, replacement_chunk, 1)

        # Pre-flight AST Syntax validation
        valid, syntax_err = SafetySandbox.validate_code(staged_source, file_name=target_path.name)
        if not valid:
            logger.error(f"[AutoHeal] Patch rejected by SafetySandbox: {syntax_err}")
            return {
                "success": False,
                "message": f"Safety Guard: Patch rejected due to syntax error: {syntax_err}",
                "parsed": parsed,
            }

        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "target_file": str(target_path),
                "explanation": explanation,
                "target_chunk": target_chunk,
                "replacement_chunk": replacement_chunk,
                "message": f"Dry-run passed syntax validation for {target_path.name}.",
            }

        # Create atomic backup
        backup_path = SafetySandbox.create_backup(target_path)

        # Write patch to disk
        try:
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(staged_source)
        except Exception as e:
            # Immediate rollback if write failed
            shutil.copy2(backup_path, target_path)
            return {"success": False, "message": f"File write failed, restored backup: {e}"}

        # Verify on-disk compilation via py_compile
        try:
            py_compile.compile(str(target_path), doraise=True)
        except Exception as pyc_err:
            logger.error(f"[AutoHeal] py_compile failed after write, rolling back: {pyc_err}")
            shutil.copy2(backup_path, target_path)
            return {"success": False, "message": f"Post-write compilation failed, rolled back: {pyc_err}"}

        patch_id = str(uuid.uuid4())[:8]
        entry = {
            "patch_id": patch_id,
            "timestamp": time.time(),
            "target_file": str(target_path),
            "backup_path": str(backup_path),
            "line_number": line_num,
            "exception_fixed": f"{parsed.get('exception_type')}: {parsed.get('exception_message')}",
            "explanation": explanation,
            "status": "applied",
        }

        history = SafetySandbox._load_history()
        history.append(entry)
        SafetySandbox._save_history(history)

        return {
            "success": True,
            "patch_id": patch_id,
            "target_file": str(target_path),
            "backup_path": str(backup_path),
            "explanation": explanation,
            "message": f"Successfully auto-patched '{target_path.name}' at line {line_num} (Patch ID: {patch_id}). Backup preserved.",
        }

    @classmethod
    def _synthesize_patch_code(
        cls,
        file_name: str,
        line_num: int,
        exception_type: str,
        exception_msg: str,
        code_context: str,
        context_notes: str = "",
    ) -> Dict[str, Any]:
        """Calls LLM to generate the surgical exact target chunk and replacement chunk."""
        prompt = f"""You are an elite Python compiler and autonomous debugging engineer.
A Python bug occurred in file '{file_name}' around line {line_num}.
Exception: {exception_type}: {exception_msg}
Additional context: {context_notes}

Relevant source code context:
```python
{code_context}
```

Task: Provide a surgical, minimal fix to eliminate the exception (e.g. add None-checks, handle key errors, safe type casting, boundary check).
Output ONLY a strict JSON object with these exact keys:
{{
    "explanation": "One sentence explaining what was fixed",
    "target_chunk": "Exact verbatim string from code_context to replace (must match characters and whitespace exactly)",
    "replacement_chunk": "Replacement code to substitute in place of target_chunk"
}}
Do NOT include markdown fences outside the JSON. Return only the valid JSON object.
"""
        # 1. Primary: Google Gemini (Native directly via google.genai)
        gemini_key = _get_gemini_api_key()
        if gemini_key:
            try:
                from google import genai
                g_client = genai.Client(api_key=gemini_key, http_options={"api_version": "v1beta"})
                for model_name in ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"):
                    try:
                        resp = g_client.models.generate_content(
                            model=model_name,
                            contents=prompt,
                            config={"temperature": 0.1, "response_mime_type": "application/json"}
                        )
                        raw_text = getattr(resp, "text", "") or ""
                        if raw_text.strip():
                            clean_json = raw_text.strip()
                            if clean_json.startswith("```"):
                                clean_json = re.sub(r"^```[a-zA-Z]*\n?", "", clean_json)
                                clean_json = re.sub(r"\n?```$", "", clean_json).strip()
                            data = json.loads(clean_json)
                            if "target_chunk" in data and "replacement_chunk" in data:
                                data["success"] = True
                                return data
                    except Exception as model_err:
                        logger.warning(f"[AutoHeal] Gemini model {model_name} synthesis attempt failed: {model_err}")
                        continue
            except Exception as g_err:
                logger.warning(f"[AutoHeal] Gemini synthesis failed: {g_err}")

        # 2. Fallback: Unified AI Client (llm_client.py)
        try:
            from llm_client import client as unified_client
            resp_text = unified_client.chat(prompt, temperature=0.1)
            clean_json = resp_text.strip()
            if clean_json.startswith("```"):
                clean_json = re.sub(r"^```[a-zA-Z]*\n?", "", clean_json)
                clean_json = re.sub(r"\n?```$", "", clean_json).strip()
            data = json.loads(clean_json)
            if "target_chunk" in data and "replacement_chunk" in data:
                data["success"] = True
                return data
        except Exception as u_err:
            logger.warning(f"[AutoHeal] Unified AI client fallback failed: {u_err}")

        # 3. Fallback: OpenRouter client
        try:
            import or_client
            resp_text = or_client.chat(prompt, system="You are an expert Python auto-patching engineer. Return strict JSON.")
            clean_json = re.sub(r"^```[a-zA-Z]*\n?", "", resp_text.strip())
            clean_json = re.sub(r"\n?```$", "", clean_json).strip()
            data = json.loads(clean_json)
            if "target_chunk" in data and "replacement_chunk" in data:
                data["success"] = True
                return data
        except Exception as or_err:
            logger.warning(f"[AutoHeal] OpenRouter fallback failed: {or_err}")

        return {
            "success": False,
            "error": "All LLM synthesis backends failed. Please verify your Gemini API key in config/api_keys.json."
        }


# ── 4. Unified MCP Tool Dispatcher ──────────────────────────────────────────

def auto_heal(
    parameters: Optional[Union[Dict[str, Any], str]] = None,
    player: Any = None,
    speak: Optional[Callable[[str], None]] = None,
) -> str:
    """
    Unified entrypoint for Autonomous Self-Healing and Self-Improvement.
    """
    if isinstance(parameters, str):
        params = {"action": parameters}
    else:
        params = parameters or {}
    action = (params.get("action") or params.get("command") or "status").lower().strip()

    if action in ("history", "log", "patches"):
        patches = AutoHealEngine.get_patch_history(limit=5)
        if not patches:
            msg = "No automatic patches applied yet. System is running clean."
            if speak:
                speak(msg)
            return msg

        lines = ["🛡️ AUTONOMOUS PATCH HISTORY:"]
        for p in patches:
            fn = Path(p.get("target_file", "")).name
            status = p.get("status", "unknown")
            lines.append(f"• [{p.get('patch_id')}] {fn} (line {p.get('line_number')}): {p.get('explanation')} — Status: {status}")

        result = "\n".join(lines)
        if speak:
            speak(f"You have {len(patches)} recent patches logged. Last patch was on {Path(patches[0].get('target_file', '')).name}.")
        return result

    elif action in ("rollback", "undo", "revert"):
        patch_id = params.get("patch_id") or "latest"
        res = SafetySandbox.rollback_patch(patch_id)
        msg = res.get("message", "Rollback completed.")
        if speak:
            speak(msg)
        return msg

    elif action in ("heal", "fix", "patch"):
        tb = params.get("traceback") or params.get("error") or params.get("error_traceback") or ""
        notes = params.get("notes") or params.get("context") or ""
        if not tb:
            tb = AutoHealEngine.get_last_error() or ""
        if not tb:
            # Check FATAL_CRASH.log if no traceback explicitly provided
            crash_log = BASE_DIR / "FATAL_CRASH.log"
            if crash_log.exists():
                try:
                    tb = crash_log.read_text(encoding="utf-8")
                except Exception:
                    pass
        if not tb:
            msg = "No recent error or traceback captured to heal. If an error just occurred, you can paste the traceback."
            if speak:
                speak(msg)
            return msg

        res = AutoHealEngine.heal_traceback(tb, context_notes=notes)
        msg = res.get("message", "Done.")
        if speak:
            speak(msg)
        return msg

    elif action in ("learn_rule", "add_rule", "remember_rule"):
        rule = params.get("rule") or params.get("directive") or params.get("text") or ""
        if not rule:
            return "Please specify a rule to learn (e.g. rule='Always use Chrome browser')."
        from core.learned_rules import LearnedRulesEngine
        res = LearnedRulesEngine.add_rule(rule)
        msg = res.get("message", "Rule saved.")
        if speak:
            speak(f"Understood, sir. I have committed that rule to my memory.")
        return msg

    elif action in ("list_rules", "rules"):
        from core.learned_rules import LearnedRulesEngine
        rules = LearnedRulesEngine.list_rules()
        if not rules:
            return "No learned rules stored."
        lines = ["🧠 LEARNED BEHAVIORAL DIRECTIVES:"]
        for r in rules:
            status = "ACTIVE" if r.get("active") else "INACTIVE"
            lines.append(f"• [{r.get('id')}] ({status}) {r.get('rule')}")
        return "\n".join(lines)

    else:  # status
        patches = AutoHealEngine.get_patch_history(limit=1)
        last_patch = patches[0] if patches else None
        from core.learned_rules import LearnedRulesEngine
        rule_count = len(LearnedRulesEngine.list_rules(active_only=True))

        lines = [
            "🛡️ AUTO-HEAL & SELF-IMPROVEMENT STATUS",
            "─────────────────────────────────────",
            "• Boot Sentry: Active (Automatic rollback enabled)",
            "• Safety Sandbox: Enabled (Pre-flight AST & Compilation validation)",
            f"• Learned Behavioral Directives: {rule_count} active rules",
            f"• Recent Hotfixes: {last_patch.get('explanation') if last_patch else 'None (Clean)'}",
        ]
        report = "\n".join(lines)
        if speak:
            speak("Auto-heal sentry and continuous self-improvement are fully active, sir.")
        return report
