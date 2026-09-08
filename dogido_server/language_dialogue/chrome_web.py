"""公式MCP SDKで専用stdioプロセスへ接続。普段のChromeや本体設定は変更しない。"""

import asyncio
from concurrent.futures import wait
import json
from pathlib import Path
import threading
import time


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COMMAND = ROOT / ".dogido_tools/chrome-web-mcp-macos/.venv/bin/chrome-web-mcp"


class ChromeWebClient:
    """一つの試験実行で接続と表示中のタブを維持する。closeで所有プロセスだけ終了。"""

    def __init__(self, command=DEFAULT_COMMAND, *, timeout=60, config=None, child_view=False,
                 overview_search=False):
        self.command = str(command)
        self.child_view = child_view
        self.overview_search = overview_search
        self.timeout = timeout
        config_name = "chrome-web-child-config.json" if child_view else "chrome-web-config.json"
        self.config = str(config or ROOT / "scripts" / config_name)
        self._thread = None
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._error = None
        self._loop = None
        self._session = None
        self._stop = None
        self._work_scope = None
        self._closed = False

    async def _serve(self):
        # 任意依存。Webを使わない起動やテストにSDKを要求しない。
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        import anyio

        parameters = StdioServerParameters(
            command=self.command,
            env={"CW_CONFIG": self.config},
            cwd=str(ROOT),
        )
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                # 終了処理はこのscopeの外。初期化中も取消でき、SDK自身がstdioを終了する。
                with anyio.CancelScope() as work_scope:
                    self._work_scope = work_scope
                    if self._closed or self._stopping.is_set():
                        return
                    with anyio.fail_after(self.timeout):
                        await session.initialize()
                        if self.child_view or self.overview_search:
                            available = await session.list_tools()
                            guard = any(
                                t.name == "fetch_url"
                                and "expected_url" in t.inputSchema.get("properties", {})
                                and (not self.overview_search or {"wait_for_ai_overview", "existing_tab_id"}
                                     <= t.inputSchema.get("properties", {}).keys())
                                for t in available.tools
                            )
                            if not guard:
                                raise RuntimeError("chrome_web_review_guard_missing")
                    self._session = session
                    self._ready.set()
                    await self._stop.wait()

    def _run(self):
        try:
            asyncio.run(self._serve())
        except BaseException as exc:
            self._error = type(exc).__name__
        finally:
            self._session = None
            self._ready.set()

    def _start(self, cancelled):
        if self._closed:
            raise RuntimeError("chrome_web_closed")
        if self._thread is None:
            if not Path(self.command).is_file():
                raise RuntimeError("chrome_web_not_installed")
            self._error = None
            self._stopping.clear()
            self._thread = threading.Thread(target=self._run, name="dogido-chrome-web", daemon=True)
            self._thread.start()
        deadline = time.monotonic() + self.timeout
        while not self._ready.wait(0.1):
            if self._closed or cancelled() or time.monotonic() >= deadline:
                self._shutdown()
                raise TimeoutError("chrome_web_start_cancelled_or_timeout")
        if self._error or self._session is None:
            self._shutdown()
            raise RuntimeError("chrome_web_start_failed:" + str(self._error))

    def call(self, name, arguments, *, cancelled=lambda: False):
        if name not in {"google_search", "fetch_url", "health_check"}:
            raise ValueError("unsupported_chrome_web_tool")
        with self._lock:
            if cancelled():
                raise RuntimeError("chrome_web_cancelled")
            self._start(cancelled)
            if self._closed or cancelled():
                self._shutdown()
                raise RuntimeError("chrome_web_cancelled")
            future = asyncio.run_coroutine_threadsafe(
                self._session.call_tool(name, arguments), self._loop
            )
            deadline = time.monotonic() + self.timeout
            while True:
                if self._closed or cancelled() or time.monotonic() >= deadline:
                    future.cancel()
                    # SDKの待機取消だけではサーバー処理は止まらない。所有接続を終了する。
                    self._shutdown()
                    raise TimeoutError("chrome_web_call_cancelled_or_timeout")
                done, _ = wait([future], timeout=0.1)
                if done:
                    try:
                        result = future.result()
                    except Exception:
                        self._shutdown()
                        raise
                    break
            if result.isError:
                raise RuntimeError("chrome_web_tool_error")
            blocks = [b.text for b in result.content if b.type == "text"]
            if len(blocks) != 1:
                raise ValueError("chrome_web_invalid_response")
            payload = json.loads(blocks[0])
            if not isinstance(payload, dict):
                raise ValueError("chrome_web_invalid_response")
            return payload

    def _shutdown(self):
        self._stopping.set()
        if self._loop and self._loop.is_running():
            if self._work_scope:
                self._loop.call_soon_threadsafe(self._work_scope.cancel)
            elif self._stop:
                self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                self._closed = True
                raise RuntimeError("chrome_web_shutdown_incomplete")
        self._thread = self._session = self._loop = self._stop = self._work_scope = None
        self._ready.clear()

    def close(self):
        self._closed = True
        with self._lock:
            self._shutdown()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
