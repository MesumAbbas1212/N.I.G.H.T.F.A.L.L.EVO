from core.user_paths import get_user_data_dir
import os
import re
import sys
import json
import fnmatch
import logging
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("NIGHTFALL_dev_agent")
logger.setLevel(logging.INFO)

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR = get_base_dir()
API_CONFIG_PATH = get_user_data_dir() / "config" / "api_keys.json"
SETTINGS_PATH = get_user_data_dir() / "config" / "app_settings.json"

# ==============================================================================
# NATIVE CLAUDE-CODE TOOLS (REBRANDED FOR NIGHTFALL DEV)
# ==============================================================================

class NativeTools:
    def __init__(self, workspace_dir: Path, on_action: Optional[Callable[[str], None]] = None):
        self.workspace_dir = Path(workspace_dir).resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.on_action = on_action

    def _notify(self, message: str):
        if self.on_action:
            try:
                self.on_action(message)
            except Exception:
                pass

    def bash(self, command: str, timeout: int = 120) -> str:
        """Executes a shell command in the workspace directory."""
        self._notify(f"⚡ Running command: {command}")
        try:
            is_win = sys.platform.startswith("win")
            shell_cmd = ["powershell", "-NoProfile", "-Command", command] if is_win else command
            
            res = subprocess.run(
                shell_cmd,
                cwd=str(self.workspace_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=not is_win
            )
            stdout = res.stdout.strip()
            stderr = res.stderr.strip()
            exit_code = res.returncode
            
            output = []
            if stdout:
                output.append(stdout)
            if stderr:
                output.append(f"[stderr]:\n{stderr}")
            if not stdout and not stderr:
                output.append("(No output)")
            output.append(f"[Exit code: {exit_code}]")
            return "\n".join(output)
        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {timeout} seconds."
        except Exception as e:
            return f"Error running command: {e}"

    def file_read(self, file_path: str, offset: int = 1, limit: int = 2000) -> str:
        """Reads a file with line numbers starting at offset up to limit lines."""
        path = (self.workspace_dir / file_path).resolve() if not Path(file_path).is_absolute() else Path(file_path)
        self._notify(f"📖 Reading file: {path.name}")
        if not path.exists():
            return f"Error: File does not exist: {path}"
        if path.is_dir():
            return f"Error: Path is a directory, not a file: {path}"
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            
            total_lines = len(lines)
            start_idx = max(0, offset - 1)
            end_idx = min(total_lines, start_idx + limit)
            
            numbered_lines = []
            for idx in range(start_idx, end_idx):
                numbered_lines.append(f"{idx + 1:6d}\t{lines[idx].rstrip()}")
            
            header = f"[Reading {path.name} | Lines {start_idx + 1} to {end_idx} of {total_lines}]\n"
            return header + "\n".join(numbered_lines)
        except Exception as e:
            return f"Error reading file {path}: {e}"

    def file_write(self, file_path: str, content: str) -> str:
        """Writes entire content to a file, creating parent folders if needed."""
        path = (self.workspace_dir / file_path).resolve() if not Path(file_path).is_absolute() else Path(file_path)
        self._notify(f"📝 Writing file: {path.name}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Successfully wrote {len(content)} characters to {path}."
        except Exception as e:
            return f"Error writing file {path}: {e}"

    def file_edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        """Surgically edits a file by replacing old_string with new_string."""
        path = (self.workspace_dir / file_path).resolve() if not Path(file_path).is_absolute() else Path(file_path)
        self._notify(f"✏️ Editing file: {path.name}")
        if not path.exists():
            return f"Error: File does not exist: {path}"
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            count = content.count(old_string)
            if count == 0:
                return (
                    f"Error: `old_string` was not found in {path.name}. "
                    "Make sure indentation and whitespace match exactly from Read tool."
                )
            if count > 1 and not replace_all:
                return (
                    f"Error: `old_string` occurs {count} times in {path.name}. "
                    "Provide more surrounding context to uniquely identify the block, or set `replace_all=true`."
                )

            if replace_all:
                new_content = content.replace(old_string, new_string)
            else:
                new_content = content.replace(old_string, new_string, 1)

            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return f"Successfully updated {path.name} (replaced {count if replace_all else 1} occurrence(s))."
        except Exception as e:
            return f"Error editing file {path}: {e}"

    def glob(self, pattern: str, path: str = ".") -> str:
        """Finds files matching glob pattern."""
        search_root = (self.workspace_dir / path).resolve()
        self._notify(f"🔍 Searching files for '{pattern}'")
        if not search_root.exists():
            return f"Error: Path does not exist: {search_root}"
        
        matches = []
        try:
            for root, dirs, files in os.walk(search_root):
                # Ignore node_modules, .git, venv
                dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", "__pycache__", "dist", "build")]
                for filename in files:
                    full_p = Path(root) / filename
                    rel_p = full_p.relative_to(self.workspace_dir)
                    if fnmatch.fnmatch(str(rel_p).replace("\\", "/"), pattern) or fnmatch.fnmatch(filename, pattern):
                        matches.append(str(rel_p))
                    if len(matches) >= 100:
                        break
                if len(matches) >= 100:
                    break
            
            if not matches:
                return f"No files matching pattern '{pattern}' found."
            return "\n".join(matches[:100])
        except Exception as e:
            return f"Error performing glob: {e}"

    def grep(self, pattern: str, path: str = ".", case_sensitive: bool = True) -> str:
        """Searches for regex/literal text inside files."""
        search_root = (self.workspace_dir / path).resolve()
        self._notify(f"🔎 Grepping '{pattern}'")
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except Exception as e:
            return f"Error: Invalid regex '{pattern}': {e}"

        results = []
        try:
            for root, dirs, files in os.walk(search_root):
                dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", "__pycache__", "dist", "build")]
                for filename in files:
                    fpath = Path(root) / filename
                    # Skip large binary files
                    if fpath.stat().st_size > 500_000:
                        continue
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                            for lineno, line in enumerate(f, 1):
                                if regex.search(line):
                                    rel = fpath.relative_to(self.workspace_dir)
                                    results.append(f"{rel}:{lineno}: {line.strip()}")
                                    if len(results) >= 50:
                                        break
                    except Exception:
                        pass
                if len(results) >= 50:
                    break
            if not results:
                return f"No matches found for '{pattern}'."
            return "\n".join(results[:50])
        except Exception as e:
            return f"Error running grep: {e}"


# ==============================================================================
# CLAUDE-CODE LEAKED SYSTEM PROMPT (REBRANDED FOR NIGHTFALL DEV)
# ==============================================================================

NIGHTFALL_DEV_SYSTEM_PROMPT = """You are NIGHTFALL Dev, the expert software engineering autonomous agent built natively into NIGHTFALL AI.
You operate on the local user machine inside the user's project workspace.
You have native access to developer tools to inspect codebases, execute terminal commands, edit existing files, write new code, and verify project functionality.

# DOING TASKS
- The user will primarily request software engineering tasks: creating full applications/websites, fixing bugs, refactoring, explaining code, and running builds.
- Understand existing code before modifying. NEVER propose or apply changes to code you haven't read. Always read files first with `FileRead`.
- ALWAYS prefer editing existing files using `FileEdit` over creating new files.
- Don't add features, refactor code, or make "improvements" beyond what was asked. Keep changes focused and minimal.
- Don't create premature abstractions or speculative utility wrappers. The right amount of complexity is what the task actually requires.
- Before reporting a task complete, verify it actually works: run the test, build the bundle, or run the script using `Bash` to confirm there are no syntax or runtime errors.

# AVAILABLE TOOLS
You communicate tool calls using XML blocks:
<tool_call>
<name>TOOL_NAME</name>
<arguments>
{
  "arg_name": "arg_value"
}
</arguments>
</tool_call>

Available tools:
1. `Bash`: Execute shell commands (e.g. npm, pip, git, python, node).
   Parameters: `command` (string, required), `timeout` (integer, optional)
2. `FileRead`: Read a file with line numbers.
   Parameters: `file_path` (string, required), `offset` (integer, optional), `limit` (integer, optional)
3. `FileWrite`: Create or overwrite a file.
   Parameters: `file_path` (string, required), `content` (string, required)
4. `FileEdit`: Surgically edit an existing file using exact string replacement.
   Parameters: `file_path` (string, required), `old_string` (string, required), `new_string` (string, required), `replace_all` (bool, optional)
5. `Glob`: Search for files matching pattern.
   Parameters: `pattern` (string, required), `path` (string, optional)
6. `Grep`: Search for text patterns inside files.
   Parameters: `pattern` (string, required), `path` (string, optional), `case_sensitive` (bool, optional)

# TOOL CALLING FORMAT
Always reason first inside `<thinking>` tags before executing tools.
You can call multiple tools in one turn if they are independent.
When your work is fully done and verified, provide your final response to the user without any `<tool_call>` tags.
"""

def _summarize_action(msg: str) -> str:
    """One short spoken sentence for a tool action - never the raw command."""
    text = (msg or "").strip()
    if not text:
        return ""
    if text.startswith("⚡"):
        command = text.split(":", 1)[1].strip() if ":" in text else ""
        low = command.lower()
        if "pytest" in low or "unittest" in low or "test" in low.split():
            return "Running the tests."
        if "npm " in low or "yarn " in low or "build" in low:
            return "Building the project."
        if "pip install" in low or "npm install" in low or "npm i " in low:
            return "Installing a dependency."
        if "python" in low or low.startswith("py "):
            return "Running a Python command."
        verb = command.split()[0] if command.split() else "a command"
        return f"Running {verb}."
    if text.startswith("📝"):
        name = text.split(":", 1)[1].strip() if ":" in text else ""
        return f"Writing {name}." if name else "Writing a file."
    if text.startswith("✏️"):
        name = text.split(":", 1)[1].strip() if ":" in text else ""
        return f"Editing {name}." if name else "Editing a file."
    if text.startswith("🔍") or text.startswith("📖"):
        return "Looking through the project."
    return ""


# ==============================================================================
# AUTONOMOUS HERMES-STYLE AGENT LOOP
# ==============================================================================

class NIGHTFALLDevAgent:
    def __init__(self, workspace_dir: str | Path, speak: Optional[Callable[[str], None]] = None):
        self.workspace_dir = Path(workspace_dir).resolve()
        self.speak = speak
        self.tools = NativeTools(self.workspace_dir, on_action=self._on_action)
        self.history: list[dict[str, str]] = []

    def _on_action(self, msg: str):
        if self.speak:
            # Speak a one-line summary, never the raw command: dumping the
            # whole of `python -c "...urls and selectors..."` into the chat
            # buried the conversation in shell text.
            spoken = _summarize_action(msg)
            if spoken:
                self.speak(spoken)
        print(f"[NIGHTFALLDev] {msg}")

    def _execute_tool(self, name: str, args: dict[str, Any]) -> str:
        tool_map = {
            "bash": lambda a: self.tools.bash(a.get("command", ""), a.get("timeout", 120)),
            "fileread": lambda a: self.tools.file_read(a.get("file_path", ""), int(a.get("offset", 1)), int(a.get("limit", 2000))),
            "filewrite": lambda a: self.tools.file_write(a.get("file_path", ""), a.get("content", "")),
            "fileedit": lambda a: self.tools.file_edit(a.get("file_path", ""), a.get("old_string", ""), a.get("new_string", ""), bool(a.get("replace_all", False))),
            "glob": lambda a: self.tools.glob(a.get("pattern", "*"), a.get("path", ".")),
            "grep": lambda a: self.tools.grep(a.get("pattern", ""), a.get("path", "."), bool(a.get("case_sensitive", True))),
        }
        key = name.lower().strip()
        func = tool_map.get(key)
        if not func:
            return f"Error: Tool '{name}' is not recognized. Available: Bash, FileRead, FileWrite, FileEdit, Glob, Grep."
        try:
            return func(args)
        except Exception as e:
            return f"Error executing {name}: {e}"

    def _parse_tool_calls(self, text: str) -> list[tuple[str, dict[str, Any]]]:
        """Extracts all <tool_call><name>...</name><arguments>...</arguments></tool_call> blocks."""
        pattern = re.compile(r"<tool_call>\s*<name>(.*?)</name>\s*<arguments>(.*?)</arguments>\s*</tool_call>", re.DOTALL | re.IGNORECASE)
        calls = []
        for match in pattern.finditer(text):
            tool_name = match.group(1).strip()
            raw_args = match.group(2).strip()
            try:
                args = json.loads(raw_args)
            except Exception:
                # Try relaxed parsing if JSON had raw newlines or trailing quotes
                try:
                    cleaned = re.sub(r",\s*([\]}])", r"\1", raw_args)
                    args = json.loads(cleaned)
                except Exception:
                    args = {"raw_input": raw_args}
            calls.append((tool_name, args))
        return calls

    def _call_llm(self) -> str:
        """Dispatches conversation to Gemini directly with model fallback and rate-limit retries."""
        try:
            with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                keys = json.load(f)
            gemini_key = keys.get("gemini_api_key", "").strip()

            if gemini_key:
                import time
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=gemini_key)

                system_instruction = NIGHTFALL_DEV_SYSTEM_PROMPT
                contents = []
                for msg in self.history:
                    if msg["role"] == "system":
                        system_instruction = msg["content"]
                    else:
                        role = "user" if msg["role"] == "user" else "model"
                        contents.append(types.Content(
                            role=role,
                            parts=[types.Part.from_text(text=msg["content"])]
                        ))

                # Attempt primary model and fallback model with retries
                models_to_try = ["gemini-2.5-flash", "gemini-3.6-flash"]
                last_err = None

                for model_name in models_to_try:
                    for attempt in range(3):
                        try:
                            resp = client.models.generate_content(
                                model=model_name,
                                contents=contents,
                                config={"system_instruction": system_instruction, "temperature": 0.2}
                            )
                            if resp.text:
                                return resp.text.strip()
                        except Exception as e:
                            last_err = e
                            err_str = str(e).lower()
                            if "429" in err_str or "quota" in err_str or "resource_exhausted" in err_str or "503" in err_str:
                                time.sleep(2 * (attempt + 1))
                                continue
                            else:
                                break

                if last_err:
                    logger.warning(f"[NIGHTFALLDev] Gemini calls exhausted: {last_err}")
        except Exception as e:
            logger.warning(f"[NIGHTFALLDev] Direct Gemini setup error: {e}")

        # Check if openrouter key actually exists before falling back
        try:
            with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                or_key = json.load(f).get("openrouter_api_key", "").strip()
            if or_key:
                from llm_client import client as ai_client
                return ai_client.multi_turn(self.history, temperature=0.2)
        except Exception:
            pass

        # Finally, every provider configured in Settings, in Jeff's order. The
        # dev agent used to stop at Gemini + OpenRouter, so an exhausted free
        # Gemini tier made every build fail while other providers sat unused.
        try:
            from core import provider_registry
            messages = [
                {"role": str(m.get("role") or "user"), "content": str(m.get("content") or "")}
                for m in self.history
            ]
            reply, provider, errors = provider_registry.chat_failover(
                messages, temperature=0.2, timeout=120
            )
            if errors:
                logger.info(
                    "[NIGHTFALLDev] providers skipped before %s: %s",
                    provider.get("name"), "; ".join(errors)[:300],
                )
            if reply:
                return reply
        except Exception as exc:
            logger.warning(f"[NIGHTFALLDev] configured providers failed too: {exc}")

        raise RuntimeError(
            "No AI provider could answer (Gemini quota, then every configured provider failed). "
            "Add another key in Settings, Custom AI Providers."
        )



    def run(self, user_instruction: str, max_turns: int = 25) -> str:
        """Runs the autonomous Think -> Act -> Observe loop until completion."""
        print(f"\n[NIGHTFALLDev] Starting Developer Task in {self.workspace_dir}")
        print(f"[NIGHTFALLDev] Prompt: {user_instruction}\n")
        
        system_info = (
            f"\n\nCURRENT ENVIRONMENT:\n"
            f"- Workspace Directory: {self.workspace_dir}\n"
            f"- OS: {sys.platform} ({os.name})\n"
        )
        self.history = [
            {"role": "system", "content": NIGHTFALL_DEV_SYSTEM_PROMPT + system_info},
            {"role": "user", "content": user_instruction}
        ]

        final_response = ""
        for turn in range(1, max_turns + 1):
            print(f"--- [NIGHTFALLDev] Turn {turn}/{max_turns} ---")
            try:
                reply = self._call_llm()
            except Exception as e:
                err_msg = f"LLM error: {e}"
                logger.error(err_msg)
                return f"NIGHTFALL Dev encountered an error during inference: {err_msg}"

            self.history.append({"role": "assistant", "content": reply})

            # Check for tool calls
            tool_calls = self._parse_tool_calls(reply)
            if not tool_calls:
                # No more tools called; model is done and gave final response
                # Clean any <thinking> blocks out for user display
                clean_reply = re.sub(r"<thinking>.*?</thinking>", "", reply, flags=re.DOTALL).strip()
                final_response = clean_reply if clean_reply else reply
                break

            # Execute tool calls
            results_content = []
            for tool_name, args in tool_calls:
                result = self._execute_tool(tool_name, args)
                # Truncate overly long tool outputs to preserve token budget
                if len(result) > 10000:
                    result = result[:10000] + "\n\n...[Output truncated to 10,000 characters]..."
                results_content.append(f"<tool_result name=\"{tool_name}\">\n{result}\n</tool_result>")

            observation_block = "\n\n".join(results_content)
            self.history.append({"role": "user", "content": observation_block})

        if not final_response:
            final_response = "Completed developer task after max iterations. Please check your workspace."
        return final_response

def run_dev_agent(parameters: dict[str, Any], speak: Optional[Callable[[str], None]] = None) -> str:
    params = dict(parameters or {})
    description = str(params.get("description") or params.get("brief") or "").strip()
    workspace = str(params.get("workspace_path") or params.get("output_dir") or Path.home() / "Desktop" / "NIGHTFALLProjects").strip()
    
    agent = NIGHTFALLDevAgent(workspace_dir=workspace, speak=speak)
    return agent.run(description)
