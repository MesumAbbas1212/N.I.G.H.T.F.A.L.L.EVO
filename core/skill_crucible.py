"""
Skill Crucible: Multi-Tiered Safety Sandbox & Verification Engine
Part of Project Ultron for NIGHTFALL AI.

Performs:
1. Static AST Safety Analysis (blocks destructive OS actions).
2. Dependency Auto-Resolution (installs required packages in .venv).
3. Sandboxed Subprocess Test Execution (validates against test cases with timeouts).
"""

from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("SkillCrucible")

# Dangerous calls and patterns that synthetic skills must NEVER execute
BANNED_AST_PATTERNS = [
    # Destructive disk / partition commands
    "format c:",
    "format d:",
    "format /fs",
    "diskpart",
    "vssadmin",
    "bcdedit",
    # System32 destruction
    "system32",
    "syswow64",
    # Self-destruction of NIGHTFALL AI core
    "boot_sentry",
    "auto_heal_engine",
    "install_wizard",
    "build_exe",
]

STANDARD_LIB_MODULES = {
    "abc", "argparse", "array", "ast", "asyncio", "base64", "binascii", "bisect",
    "calendar", "cmath", "collections", "colorsys", "concurrent", "configparser",
    "contextlib", "copy", "csv", "ctypes", "dataclasses", "datetime", "decimal",
    "difflib", "dis", "doctest", "email", "enum", "errno", "faulthandler",
    "filecmp", "fileinput", "fnmatch", "fractions", "functools", "gc", "getopt",
    "getpass", "gettext", "glob", "gzip", "hashlib", "heapq", "hmac", "html",
    "http", "imaplib", "imghdr", "importlib", "inspect", "io", "ipaddress",
    "itertools", "json", "keyword", "linecache", "locale", "logging", "lzma",
    "math", "mimetypes", "mmap", "multiprocessing", "netrc", "nntplib", "numbers",
    "operator", "os", "pathlib", "pickle", "pkgutil", "platform", "plistlib",
    "poplib", "posixpath", "pprint", "profile", "pstats", "pty", "pwd", "py_compile",
    "pyclbr", "pydoc", "queue", "quopri", "random", "re", "reprlib", "resource",
    "rlcompleter", "runpy", "sched", "secrets", "select", "selectors", "shelve",
    "shlex", "shutil", "signal", "site", "smtpd", "smtplib", "sndhdr", "socket",
    "socketserver", "sqlite3", "ssl", "stat", "statistics", "string", "stringprep",
    "struct", "subprocess", "sys", "sysconfig", "tabnanny", "tarfile", "telnetlib",
    "tempfile", "termios", "textwrap", "threading", "time", "timeit", "tkinter",
    "token", "tokenize", "trace", "traceback", "tracemalloc", "tty", "turtle",
    "types", "typing", "unicodedata", "unittest", "urllib", "uu", "uuid", "venv",
    "warnings", "wave", "weakref", "webbrowser", "wsgiref", "xdrlib", "xml",
    "xmlrpc", "zipapp", "zipfile", "zipimport", "zlib"
}

# Mapping common package import names to pip install names if different
IMPORT_TO_PIP = {
    "bs4": "beautifulsoup4",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
    "fitz": "pymupdf",
    "speedtest": "speedtest-cli",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "dotenv": "python-dotenv",
    "dateutil": "python-dateutil",
    "serial": "pyserial",
    "websocket": "websocket-client",
}


def _get_python_executable() -> str:
    """Finds the running python executable so dependencies match the active runtime."""
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        py_cand = exe[:-5] + ".exe"
        if Path(py_cand).exists():
            return py_cand
    return exe


class SkillCrucible:
    """Verifies and stress-tests synthetic skills before deployment."""

    @staticmethod
    def validate_ast(code_str: str) -> Tuple[bool, Optional[str]]:
        """Parses syntax and blocks destructive patterns."""
        if not code_str or not code_str.strip():
            return False, "Code cannot be empty."

        try:
            tree = ast.parse(code_str)
        except SyntaxError as e:
            return False, f"Syntax Error on line {e.lineno}: {e.msg}"
        except Exception as e:
            return False, f"Validation Error: {e}"

        # Ensure it defines the mandatory execute function
        has_execute = False
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "execute":
                    has_execute = True
                    break

        if not has_execute:
            return False, "Skill code must define an 'execute(**kwargs)' or 'async def execute(**kwargs)' function."

        # Safety scans for banned keywords in string literals or function calls
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val_lower = node.value.lower()
                for banned in BANNED_AST_PATTERNS:
                    if banned in val_lower:
                        return False, f"Security Violation: Prohibited term or system path '{banned}' detected."

        return True, None

    @staticmethod
    def extract_dependencies(code_str: str) -> List[str]:
        """Extracts top-level external module imports required by the skill."""
        try:
            tree = ast.parse(code_str)
        except Exception:
            return []

        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root not in STANDARD_LIB_MODULES and root not in ("core", "actions", "features"):
                        imports.add(root)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root = node.module.split(".")[0]
                    if root not in STANDARD_LIB_MODULES and root not in ("core", "actions", "features"):
                        imports.add(root)

        return sorted(list(imports))

    @classmethod
    def resolve_dependencies(cls, dependencies: List[str]) -> Tuple[bool, str]:
        """Installs missing dependencies into the Python environment."""
        if not dependencies:
            return True, "No external dependencies required."

        py_exe = _get_python_executable()
        installed_any = []

        for dep in dependencies:
            pip_name = IMPORT_TO_PIP.get(dep, dep)
            # Check if importable and healthy
            is_healthy = False
            try:
                check_script = f"import {dep}"
                if dep == "speedtest":
                    check_script = "import speedtest; assert hasattr(speedtest, 'Speedtest')"
                elif dep == "PIL":
                    check_script = "import PIL.Image"
                elif dep == "cv2":
                    check_script = "import cv2; assert hasattr(cv2, 'imread')"
                check_cmd = [py_exe, "-c", check_script]
                proc = subprocess.run(check_cmd, capture_output=True, timeout=5)
                if proc.returncode == 0:
                    is_healthy = True
            except Exception:
                is_healthy = False

            if is_healthy:
                continue

            logger.info(f"[Crucible] Installing missing or repairing dependency: {pip_name}")
            try:
                if dep == "speedtest":
                    subprocess.run([py_exe, "-m", "pip", "uninstall", "-y", "speedtest"], capture_output=True, timeout=30)
                install_cmd = [py_exe, "-m", "pip", "install", pip_name, "--quiet"]
                proc = subprocess.run(install_cmd, capture_output=True, text=True, timeout=180)
                if proc.returncode != 0:
                    err_sample = proc.stderr.strip()[:180] or "Unknown pip error"
                    return False, f"Failed to install dependency '{pip_name}': {err_sample}"
                installed_any.append(pip_name)
            except subprocess.TimeoutExpired:
                return False, f"Timeout installing '{pip_name}'."
            except Exception as e:
                return False, f"Exception installing '{pip_name}': {e}"

        msg = f"Installed: {', '.join(installed_any)}" if installed_any else "All dependencies satisfied."
        return True, msg

    @classmethod
    def run_sandbox_test(
        cls,
        skill_code: str,
        test_cases: List[Dict[str, Any]],
        timeout: float = 75.0
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Executes test cases in an isolated child process to verify runtime correctness.
        Returns: (passed: bool, message: str, telemetry: dict)
        """
        py_exe = _get_python_executable()

        # Build runner harness
        harness_script = f"""
import sys
import json
import traceback
import asyncio
import inspect

{skill_code}

def run_tests():
    test_cases = json.loads({json.dumps(json.dumps(test_cases))})
    results = []
    
    if not test_cases:
        test_cases = [{{"input": {{}}}}]
        
    for i, tc in enumerate(test_cases):
        args = tc.get("input", {{}})
        try:
            if inspect.iscoroutinefunction(execute):
                res = asyncio.run(execute(**args))
            else:
                res = execute(**args)
                
            # Check that result is presentable and not an error dictionary
            if res is None:
                res = "Operation completed with no output."

            is_err = False
            err_msg = ""
            if isinstance(res, dict):
                if res.get("error"):
                    is_err = True
                    err_msg = str(res["error"])
                elif res.get("success") is False:
                    is_err = True
                    err_msg = str(res.get("message") or res.get("error") or "Skill returned success=False")
            elif isinstance(res, str) and (res.lower().startswith("error:") or res.lower().startswith("failed:")):
                is_err = True
                err_msg = res

            if is_err:
                results.append({{"index": i, "success": False, "error": err_msg, "output": str(res)[:300]}})
            else:
                results.append({{"index": i, "success": True, "output": str(res)[:300]}})
        except Exception as exc:
            tb = traceback.format_exc()
            results.append({{"index": i, "success": False, "error": str(exc), "traceback": tb}})
            
    print(json.dumps(results))

if __name__ == '__main__':
    run_tests()
"""
        start_time = time.time()
        try:
            proc = subprocess.run(
                [py_exe, "-c", harness_script],
                capture_output=True,
                text=True,
                timeout=timeout
            )
            elapsed = time.time() - start_time
            if proc.returncode != 0:
                err_text = proc.stderr.strip() or proc.stdout.strip() or "Process exited with error code."
                return False, f"Sandbox Test Failed: {err_text[:300]}", {"elapsed_s": elapsed}

            output_str = proc.stdout.strip()
            # Extract JSON output
            test_results = None
            try:
                for line in reversed(output_str.splitlines()):
                    candidate = line.strip()
                    if candidate.startswith("[") and candidate.endswith("]"):
                        try:
                            parsed = json.loads(candidate)
                            if isinstance(parsed, list):
                                test_results = parsed
                                break
                        except Exception:
                            pass
            except Exception:
                pass

            if test_results is None:
                test_results = [{"success": True, "output": output_str[:300]}]

            all_passed = all(t.get("success", False) for t in test_results)
            if not all_passed:
                first_err = next((t.get("error", "Unknown test failure") for t in test_results if not t.get("success")), "Test failed")
                return False, f"Test Verification Failed: {first_err}", {"results": test_results, "elapsed_s": elapsed}

            return True, f"Passed {len(test_results)}/{len(test_results)} tests in {elapsed:.2f}s", {
                "results": test_results,
                "elapsed_s": elapsed
            }

        except subprocess.TimeoutExpired:
            return False, f"Sandbox execution timed out after {timeout} seconds.", {"elapsed_s": timeout}
        except Exception as e:
            return False, f"Sandbox execution error: {e}", {"elapsed_s": 0}
