"""Bound the lifetime of a desktop-only local Web server to its windows."""

import threading
import time


def close_idle_browser(profile_dir):
    """Close only the idle browser instance identified by our own profile.

    Never close a browser with page targets, or follow an arbitrary debug URL.
    DevToolsActivePort's browser nonce prevents a stale port from selecting a
    different browser. No daily Edge profile or process-wide kill is involved.
    """
    import asyncio
    import http.client
    import json
    from urllib.parse import urlsplit
    from websockets.exceptions import WebSocketException
    from websockets.legacy.client import connect

    try:
        lines = (profile_dir / "DevToolsActivePort").read_text(encoding="utf-8").splitlines()
        port, browser_path = int(lines[0]), lines[1]
        if not 0 < port < 65536 or not browser_path.startswith("/devtools/browser/"):
            return False

        def debug_json(path):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
            try:
                connection.request("GET", path)
                response = connection.getresponse()
                if response.status != 200:
                    raise ValueError("Browser debug endpoint unavailable")
                return json.loads(response.read(1024 * 1024))
            finally:
                connection.close()

        version = debug_json("/json/version")
        if not isinstance(version, dict):
            return False
        actual_path = urlsplit(version.get("webSocketDebuggerUrl", "")).path
        if actual_path != browser_path:
            return False
        targets = debug_json("/json/list")
        if not isinstance(targets, list) or any(not isinstance(target, dict) for target in targets):
            return False
        if any(target.get("type") == "page" for target in targets):
            return False

        async def close():
            url = f"ws://127.0.0.1:{port}{browser_path}"
            async with connect(url, open_timeout=1, close_timeout=1) as socket:
                await socket.send(json.dumps({"id": 1, "method": "Browser.close"}))

        asyncio.run(close())
        return True
    except (OSError, ValueError, IndexError, KeyError, TypeError, TimeoutError,
            asyncio.TimeoutError, WebSocketException):
        return False


class DesktopLifecycle:
    def __init__(self, shutdown, force_exit, *, clock=time.monotonic,
                 close_grace=5.0, startup_timeout=120.0, force_delay=10.0):
        self.shutdown = shutdown
        self.force_exit = force_exit
        self.clock = clock
        self.close_grace = close_grace
        self.startup_timeout = startup_timeout
        self.force_delay = force_delay
        self.connections = 0
        self.started = clock()
        self.last_disconnect = None
        self.closing = False
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def connected(self):
        with self._lock:
            if self.closing:
                return False
            self.connections += 1
            return True

    def disconnected(self):
        with self._lock:
            self.connections = max(0, self.connections - 1)
            if not self.connections:
                self.last_disconnect = self.clock()

    def check(self):
        with self._lock:
            if self.closing:
                return True
            if self.connections:
                return False
            deadline = (self.started + self.startup_timeout if self.last_disconnect is None
                        else self.last_disconnect + self.close_grace)
            if self.clock() < deadline:
                return False
            self.closing = True
        # A blocked executor can survive Uvicorn's graceful shutdown. Only this
        # application process is bounded, never Edge or unrelated user processes.
        fallback = threading.Timer(self.force_delay, self.force_exit, args=(0,))
        fallback.daemon = True
        fallback.start()
        self.shutdown()
        return True

    def start(self):
        def watch():
            while not self._stop.wait(0.2):
                if self.check():
                    return
        threading.Thread(target=watch, name="desktop-lifecycle", daemon=True).start()

    def stop(self):
        self._stop.set()
        # Do not cancel the bounded fallback: non-daemon workers may still exist
        # after server.run() returned. On a clean process exit it cannot fire.
