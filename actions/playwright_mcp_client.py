import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("playwright_mcp_client")
logger.setLevel(logging.INFO)


def _get_app_data_dir() -> Path:
    """Returns local app data directory for NIGHTFALL AI."""
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            p = Path(base) / "NIGHTFALLAI" / "PlaywrightProfile"
            p.mkdir(parents=True, exist_ok=True)
            return p
    home = Path.home() / ".NIGHTFALL_ai" / "playwright_profile"
    home.mkdir(parents=True, exist_ok=True)
    return home


class PlaywrightMCPClient:
    """
    Python client for Microsoft's official @playwright/mcp server.
    Communicates with `npx @playwright/mcp` over stdio JSON-RPC 2.0.
    """

    def __init__(
        self,
        browser: str = "chrome",
        headless: bool = False,
        user_data_dir: Optional[str] = None,
        timeout: int = 45,
    ):
        self.browser = browser
        self.headless = headless
        self.user_data_dir = user_data_dir or str(_get_app_data_dir())
        self.timeout = timeout

        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self._req_id = 0
        self._pending_requests: Dict[int, threading.Event] = {}
        self._responses: Dict[int, dict] = {}
        self._reader_thread: Optional[threading.Thread] = None
        self._is_ready = threading.Event()
        self._tools: List[dict] = []
        self._running = False

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        with self._lock:
            if self.is_alive():
                return True

            npx_cmd = "npx.cmd" if platform.system() == "Windows" else "npx"
            cmd = [
                npx_cmd,
                "-y",
                "@playwright/mcp",
                f"--browser={self.browser}",
                f"--user-data-dir={self.user_data_dir}",
                "--caps=vision,pdf,devtools",
            ]
            if self.headless:
                cmd.append("--headless")

            logger.info(f"[PlaywrightMCP] Spawning MCP server: {' '.join(cmd)}")

            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    shell=(platform.system() == "Windows"),
                    bufsize=0,
                    encoding="utf-8",
                    errors="replace",
                )
            except Exception as e:
                logger.error(f"[PlaywrightMCP] Failed to spawn @playwright/mcp: {e}")
                return False

            self._running = True
            self._is_ready.clear()
            self._reader_thread = threading.Thread(
                target=self._read_loop,
                daemon=True,
                name="PlaywrightMCP-Reader"
            )
            self._reader_thread.start()

            # Perform MCP Handshake
            try:
                init_resp = self._send_request(
                    "initialize",
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "NIGHTFALL-ai", "version": "1.0.0"},
                    },
                    timeout=30,
                )
                if not init_resp:
                    logger.error("[PlaywrightMCP] Initialize handshake timed out or empty")
                    return False

                # Send initialized notification
                self._send_notification("notifications/initialized", {})

                # Cache tools
                tools_resp = self._send_request("tools/list", {}, timeout=20)
                if tools_resp and "result" in tools_resp:
                    self._tools = tools_resp["result"].get("tools", [])
                    logger.info(f"[PlaywrightMCP] Server initialized with {len(self._tools)} tools")

                self._is_ready.set()
                return True
            except Exception as exc:
                logger.error(f"[PlaywrightMCP] Error during handshake: {exc}")
                self.close()
                return False

    def _read_loop(self):
        """Reads JSON-RPC responses from stdout."""
        while self._running and self._proc and self._proc.stdout:
            try:
                line = self._proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                req_id = data.get("id")
                if req_id is not None and req_id in self._pending_requests:
                    self._responses[req_id] = data
                    self._pending_requests[req_id].set()

            except Exception as e:
                if self._running:
                    logger.debug(f"[PlaywrightMCP] Read error: {e}")
                break

        self._running = False
        self._is_ready.clear()

    def _send_notification(self, method: str, params: Optional[dict] = None) -> None:
        if not self.is_alive() or not self._proc or not self._proc.stdin:
            return
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        try:
            line = json.dumps(payload)
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()
        except Exception as e:
            logger.error(f"[PlaywrightMCP] Failed to send notification: {e}")

    def _send_request(self, method: str, params: Optional[dict] = None, timeout: Optional[int] = None) -> Optional[dict]:
        with self._lock:
            if not self.is_alive():
                if not self.start():
                    return None

            self._req_id += 1
            cur_id = self._req_id
            event = threading.Event()
            self._pending_requests[cur_id] = event

            payload = {
                "jsonrpc": "2.0",
                "id": cur_id,
                "method": method,
                "params": params or {},
            }

            try:
                line = json.dumps(payload)
                self._proc.stdin.write(line + "\n")
                self._proc.stdin.flush()
            except Exception as e:
                logger.error(f"[PlaywrightMCP] Error writing request {cur_id}: {e}")
                self._pending_requests.pop(cur_id, None)
                return None

        # Wait outside lock
        wait_time = timeout or self.timeout
        finished = event.wait(timeout=wait_time)
        with self._lock:
            self._pending_requests.pop(cur_id, None)
            if not finished:
                logger.warning(f"[PlaywrightMCP] Request {cur_id} ({method}) timed out after {wait_time}s")
                return None
            return self._responses.pop(cur_id, None)

    def call_tool(self, name: str, arguments: Optional[dict] = None, timeout: Optional[int] = None) -> dict:
        """Invokes any tool on the Playwright MCP server."""
        if not self.is_alive():
            if not self.start():
                return {"error": "Failed to start Playwright MCP server."}

        res = self._send_request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=timeout or self.timeout,
        )
        if not res:
            return {"error": f"Tool '{name}' execution timed out or failed to return response."}

        if "error" in res:
            return {"error": res["error"].get("message", str(res["error"]))}

        result_obj = res.get("result", {})
        # Extract text or structured content from MCP content array
        contents = result_obj.get("content", [])
        text_outputs = []
        for c in contents:
            if isinstance(c, dict):
                if c.get("type") == "text":
                    text_outputs.append(c.get("text", ""))
                elif c.get("type") == "image":
                    text_outputs.append(f"[Image: {c.get('mimeType', 'image/png')}]")
            else:
                text_outputs.append(str(c))

        combined_text = "\n".join(text_outputs).strip()
        return {
            "success": not result_obj.get("isError", False),
            "text": combined_text,
            "raw": result_obj,
        }

    # ── High-Level Convenience Methods for All 24 MCP Tools ─────────────────────

    def navigate(self, url: str) -> str:
        if not url.startswith("http://") and not url.startswith("https://") and not url.startswith("file://") and not url.startswith("about:"):
            url = "https://" + url
        res = self.call_tool("browser_navigate", {"url": url}, timeout=60)
        return res.get("text") or res.get("error") or f"Navigated to {url}"

    def navigate_back(self) -> str:
        res = self.call_tool("browser_navigate_back", {})
        return res.get("text") or res.get("error") or "Navigated back."

    def snapshot(self) -> str:
        """Returns clean accessibility tree snapshot of the current page."""
        res = self.call_tool("browser_snapshot", {})
        return res.get("text") or res.get("error") or "Snapshot unavailable."

    def find(self, text: str) -> str:
        """Searches the accessibility snapshot for text or regex."""
        res = self.call_tool("browser_find", {"text": text})
        return res.get("text") or res.get("error") or f"No matches found for '{text}'"

    def click(self, target: Optional[str] = None, element: Optional[str] = None, selector: Optional[str] = None) -> str:
        tgt = target or selector or element or "body"
        args: Dict[str, Any] = {"target": tgt}
        if element:
            args["element"] = element
        res = self.call_tool("browser_click", args)
        return res.get("text") or res.get("error") or "Clicked."

    def hover(self, target: Optional[str] = None, element: Optional[str] = None, selector: Optional[str] = None) -> str:
        tgt = target or selector or element or "body"
        args: Dict[str, Any] = {"target": tgt}
        if element:
            args["element"] = element
        res = self.call_tool("browser_hover", args)
        return res.get("text") or res.get("error") or "Hovered."

    def type_text(self, text: str, target: Optional[str] = None, element: Optional[str] = None, selector: Optional[str] = None, submit: bool = False) -> str:
        tgt = target or selector or element or ":focus"
        args: Dict[str, Any] = {"target": tgt, "text": text, "submit": submit}
        if element:
            args["element"] = element
        res = self.call_tool("browser_type", args)
        return res.get("text") or res.get("error") or "Typed text."

    def press_key(self, key: str) -> str:
        res = self.call_tool("browser_press_key", {"key": key})
        return res.get("text") or res.get("error") or f"Pressed '{key}'"

    def scroll(self, direction: str = "down", amount: int = 500) -> str:
        delta_y = amount if direction == "down" else -amount
        res = self.call_tool("browser_mouse_wheel", {"deltaX": 0, "deltaY": delta_y})
        return res.get("text") or res.get("error") or f"Scrolled {direction}."

    def fill_form(self, fields: Any) -> str:
        """Fills multiple form fields simultaneously using target element refs or selectors."""
        flist = []
        if isinstance(fields, dict):
            for k, v in fields.items():
                flist.append({"target": str(k), "value": str(v)})
        elif isinstance(fields, list):
            for item in fields:
                if isinstance(item, dict):
                    t = item.get("target") or item.get("element") or item.get("selector")
                    v = item.get("value", "")
                    if t:
                        flist.append({"target": str(t), "value": str(v)})
        res = self.call_tool("browser_fill_form", {"fields": flist})
        return res.get("text") or res.get("error") or "Form filled."

    def select_option(self, values: List[str], target: Optional[str] = None, element: Optional[str] = None, selector: Optional[str] = None) -> str:
        tgt = target or selector or element or "select"
        args: Dict[str, Any] = {"target": tgt, "values": values}
        if element:
            args["element"] = element
        res = self.call_tool("browser_select_option", args)
        return res.get("text") or res.get("error") or "Option selected."

    def drag(self, source: str, target: str) -> str:
        res = self.call_tool("browser_drag", {"startTarget": source, "endTarget": target})
        return res.get("text") or res.get("error") or "Dragged element."

    def evaluate(self, expression: str) -> str:
        """Evaluates JavaScript in page context."""
        fn = expression.strip()
        if not fn.startswith("() =>") and not fn.startswith("function") and not fn.startswith("(element) =>"):
            fn = f"() => {{ return {fn}; }}"
        res = self.call_tool("browser_evaluate", {"function": fn})
        return res.get("text") or res.get("error") or "Evaluated."

    def run_code_unsafe(self, code: str) -> str:
        """Executes a Playwright code snippet."""
        res = self.call_tool("browser_run_code_unsafe", {"code": code})
        return res.get("text") or res.get("error") or "Executed snippet."

    def take_screenshot(self, output_path: Optional[str] = None, full_page: bool = False) -> str:
        """Takes a screenshot of the current page."""
        args: Dict[str, Any] = {"scale": "css"}
        if output_path:
            args["filename"] = Path(output_path).name
        if full_page:
            args["fullPage"] = True

        res = self.call_tool("browser_take_screenshot", args)
        text = res.get("text") or ""

        # If custom output path requested and screenshot saved to relative output dir, copy it over
        if output_path:
            out_p = Path(output_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            if not out_p.exists():
                import re
                m = re.search(r"([^\s\(\)]+\.(?:png|jpeg|webp))", text)
                if m:
                    src_f = Path(m.group(1))
                    if src_f.exists():
                        try:
                            shutil.copy2(src_f, out_p)
                        except Exception:
                            pass
        return text or res.get("error") or "Screenshot captured."

    def save_pdf(self, filename: Optional[str] = None) -> str:
        args: Dict[str, Any] = {}
        if filename:
            args["filename"] = filename
        res = self.call_tool("browser_pdf_save", args)
        return res.get("text") or res.get("error") or "PDF saved."

    def tabs(self, action: str = "list", index: Optional[int] = None, url: Optional[str] = None) -> str:
        args: Dict[str, Any] = {"action": action}
        if index is not None:
            args["index"] = index
        if url:
            args["url"] = url
        res = self.call_tool("browser_tabs", args)
        return res.get("text") or res.get("error") or f"Tabs: {action}"

    def wait_for(self, text: Optional[str] = None, time_ms: Optional[int] = None) -> str:
        args: Dict[str, Any] = {}
        if text:
            args["text"] = text
        if time_ms:
            args["time"] = time_ms
        res = self.call_tool("browser_wait_for", args)
        return res.get("text") or res.get("error") or "Waited."

    def handle_dialog(self, accept: bool = True, prompt_text: Optional[str] = None) -> str:
        args: Dict[str, Any] = {"accept": accept}
        if prompt_text:
            args["promptText"] = prompt_text
        res = self.call_tool("browser_handle_dialog", args)
        return res.get("text") or res.get("error") or "Dialog handled."

    def file_upload(self, paths: List[str]) -> str:
        res = self.call_tool("browser_file_upload", {"paths": paths})
        return res.get("text") or res.get("error") or "File uploaded."

    def console_messages(self, level: str = "info") -> str:
        res = self.call_tool("browser_console_messages", {"level": level})
        return res.get("text") or res.get("error") or "No console messages."

    def network_requests(self, static: bool = False) -> str:
        res = self.call_tool("browser_network_requests", {"static": static})
        return res.get("text") or res.get("error") or "No network requests recorded."

    def resize(self, width: int, height: int) -> str:
        res = self.call_tool("browser_resize", {"width": width, "height": height})
        return res.get("text") or res.get("error") or f"Resized to {width}x{height}."

    def close(self) -> str:
        """Shuts down browser and MCP server cleanly."""
        with self._lock:
            try:
                if self.is_alive():
                    self.call_tool("browser_close", {}, timeout=5)
            except Exception:
                pass
            self._running = False
            if self._proc:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=3)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                self._proc = None
            self._is_ready.clear()
            return "Browser closed."


# Singleton client instance
_mcp_client: Optional[PlaywrightMCPClient] = None
_mcp_lock = threading.Lock()


def get_playwright_mcp_client() -> PlaywrightMCPClient:
    global _mcp_client
    with _mcp_lock:
        if _mcp_client is None:
            _mcp_client = PlaywrightMCPClient()
        return _mcp_client
