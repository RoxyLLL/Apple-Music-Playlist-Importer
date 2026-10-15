"""Desktop windows own their local server; ordinary Web mode does not."""

import time
import json
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.request import urlopen
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from applemusic.desktop import DesktopLifecycle, close_idle_browser
from applemusic.web.app import SESSION_API_TOKEN, app


def test_last_window_closes_server_but_refresh_and_other_windows_do_not():
    clock = Mock(return_value=100.0)
    shutdown = Mock()
    lifecycle = DesktopLifecycle(shutdown, Mock(), clock=clock)
    lifecycle.connected()
    lifecycle.connected()
    lifecycle.disconnected()
    clock.return_value = 120.0
    assert not lifecycle.check()
    lifecycle.disconnected()
    clock.return_value = 122.0
    assert not lifecycle.check()
    lifecycle.connected()  # Refresh reconnects within the five-second grace.
    clock.return_value = 150.0
    assert not lifecycle.check()
    lifecycle.disconnected()
    clock.return_value = 156.0
    assert lifecycle.check()
    shutdown.assert_called_once()
    lifecycle.check()
    shutdown.assert_called_once()
    lifecycle.stop()


def test_browser_launch_failure_has_bounded_startup_timeout():
    clock = Mock(return_value=100.0)
    shutdown = Mock()
    lifecycle = DesktopLifecycle(shutdown, Mock(), clock=clock)
    clock.return_value = 219.0
    assert not lifecycle.check()
    clock.return_value = 221.0
    assert lifecycle.check()
    shutdown.assert_called_once()
    lifecycle.stop()


def test_shutdown_fallback_is_bounded_even_with_stuck_workers():
    forced = Mock()
    lifecycle = DesktopLifecycle(Mock(), forced, close_grace=0.01, force_delay=0.05)
    lifecycle.connected()
    lifecycle.disconnected()
    time.sleep(0.02)
    assert lifecycle.check()
    lifecycle.stop()  # Server returned, but non-daemon work may still keep Python alive.
    time.sleep(0.1)
    forced.assert_called_once_with(0)


def test_desktop_socket_requires_cookie_and_same_origin(monkeypatch):
    lifecycle = DesktopLifecycle(Mock(), Mock())
    monkeypatch.setattr(app.state, "desktop_lifecycle", lifecycle, raising=False)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/desktop-session", headers={"Origin": "http://testserver"}):
                pass
        client.cookies.set("app_token", SESSION_API_TOKEN)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/desktop-session", headers={"Origin": "https://other.example"}):
                pass
        assert lifecycle.connections == 0
        with client.websocket_connect("/api/desktop-session", headers={"Origin": "http://testserver"}) as socket:
            assert socket.receive_json() == {"desktop": True}
            assert lifecycle.connections == 1
        assert lifecycle.connections == 0
    lifecycle.stop()


def test_web_mode_has_no_desktop_shutdown_or_script(monkeypatch):
    monkeypatch.setattr(app.state, "desktop_lifecycle", None, raising=False)
    with TestClient(app) as client:
        client.cookies.set("app_token", SESSION_API_TOKEN)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/desktop-session", headers={"Origin": "http://testserver"}):
                pass
        assert "desktop-session.js" not in client.get("/").text


def test_desktop_page_injects_session_script_before_vue(monkeypatch):
    monkeypatch.setattr(app.state, "desktop_lifecycle", SimpleNamespace(), raising=False)
    with TestClient(app) as client:
        html = client.get("/").text
    assert html.index("desktop-session.js") < html.index("VUE 3 APPLICATION SCRIPT")


@pytest.mark.parametrize("targets,nonce,expected", [
    ([], "owned-instance", True),
    ([{"type": "service_worker"}], "owned-instance", True),
    ([{"type": "page", "url": "https://example.org"}], "owned-instance", False),
    ([], "different-instance", False),
])
def test_idle_edge_cleanup_never_closes_live_pages_or_a_reused_port(tmp_path, targets, nonce, expected):
    (tmp_path / "DevToolsActivePort").write_text("12345\n/devtools/browser/owned-instance\n")
    version = {"webSocketDebuggerUrl": f"ws://localhost:12345/devtools/browser/{nonce}"}
    responses = []
    for body in (version, targets):
        response = Mock(status=200)
        response.read.return_value = json.dumps(body).encode()
        responses.append(response)
    connection = MagicMock()
    connection.getresponse.side_effect = responses
    websocket = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=websocket)
    context.__aexit__ = AsyncMock(return_value=False)
    with patch("http.client.HTTPConnection", return_value=connection), patch(
        "websockets.legacy.client.connect", return_value=context,
    ):
        assert close_idle_browser(tmp_path) is expected
    assert websocket.send.await_count == int(expected)
    if expected:
        assert json.loads(websocket.send.call_args.args[0])["method"] == "Browser.close"


def test_missing_browser_debug_file_does_not_target_other_edge_processes(tmp_path):
    assert not close_idle_browser(tmp_path)


def test_actual_isolated_edge_exits_after_its_last_page_closes(tmp_path):
    """Exercise Edge itself, using only a new headless temporary profile."""
    import asyncio
    import http.client
    from websockets.legacy.client import connect
    from applemusic.auto_token import find_browser_executable

    executable = find_browser_executable()
    if not executable or Path(executable).name.lower() != "msedge.exe":
        pytest.skip("Edge not installed")
    profile = tmp_path / "edge-profile"
    profile.mkdir()
    process = subprocess.Popen(
        [executable, "--headless=new", "--remote-debugging-port=0",
         f"--user-data-dir={profile}", "--disable-background-mode",
         "--no-first-run", "--no-default-browser-check", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        while not (profile / "DevToolsActivePort").exists():
            if process.poll() is not None or time.monotonic() > deadline:
                pytest.fail("Isolated headless Edge did not start its debug endpoint")
            time.sleep(0.05)
        lines = (profile / "DevToolsActivePort").read_text().splitlines()
        port, browser_path = int(lines[0]), lines[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request("GET", "/json/list")
            targets = json.loads(connection.getresponse().read())
        finally:
            connection.close()
        pages = [target for target in targets if target.get("type") == "page"]
        assert pages
        assert not close_idle_browser(profile)  # A live page must be preserved.
        assert process.poll() is None

        async def close_test_pages():
            async with connect(f"ws://127.0.0.1:{port}{browser_path}", open_timeout=2) as socket:
                for index, page in enumerate(pages, 1):
                    await socket.send(json.dumps({
                        "id": index, "method": "Target.closeTarget",
                        "params": {"targetId": page["id"]},
                    }))
                    while json.loads(await asyncio.wait_for(socket.recv(), 2)).get("id") != index:
                        pass

        asyncio.run(close_test_pages())
        deadline = time.monotonic() + 5
        while process.poll() is None and time.monotonic() < deadline:
            if close_idle_browser(profile):
                break
            time.sleep(0.05)
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.terminate()  # Only our isolated headless instance.
            process.wait(timeout=5)


@pytest.fixture
def desktop_server(request):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    program = r'''
import sys, threading
from pathlib import Path
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from applemusic.desktop import DesktopLifecycle
from applemusic.web.app import SESSION_API_TOKEN, desktop_window_session
app = FastAPI()
app.websocket("/api/desktop-session")(desktop_window_session)
@app.get("/")
def index():
    response = HTMLResponse('<script src="/session.js"></script>Desktop test window')
    response.set_cookie("app_token", SESSION_API_TOKEN, httponly=True, samesite="strict")
    return response
@app.get("/session.js")
def script():
    return FileResponse(Path(sys.argv[2]), media_type="application/javascript")
@app.get("/windows")
def windows():
    return {"count": app.state.desktop_lifecycle.connections}
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="error", timeout_graceful_shutdown=0.5))
import os
lifecycle = DesktopLifecycle(lambda: setattr(server, "should_exit", True), os._exit, close_grace=0.5, force_delay=1, startup_timeout=30)
app.state.desktop_lifecycle = lifecycle
if sys.argv[3] == "stuck":
    threading.Thread(target=threading.Event().wait, daemon=False).start()
lifecycle.start()
try:
    server.run()
finally:
    lifecycle.stop()
'''
    root = Path(__file__).resolve().parent.parent
    process = subprocess.Popen(
        [sys.executable, "-c", program, str(port),
         str(root / "applemusic/web/static/desktop-session.js"), request.param],
        cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("Isolated desktop server exited before it was ready")
            try:
                with urlopen(url + "/windows", timeout=0.5):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            pytest.fail("Isolated desktop server did not start")
        yield url, process
    finally:
        if process.poll() is None:
            process.terminate()  # Only the test subprocess created above.
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize("desktop_server", ["normal", "stuck"], indirect=True)
def test_real_browser_refresh_multiple_windows_and_last_close_exit_process(desktop_server):
    playwright = pytest.importorskip("playwright.sync_api")
    url, process = desktop_server
    with playwright.sync_playwright() as runtime:
        browser = None
        for options in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
            try:
                browser = runtime.chromium.launch(headless=True, **options)
                break
            except Exception:
                continue
        if browser is None:
            pytest.fail("No browser available for desktop lifetime verification")
        try:
            first = browser.new_page()
            first.goto(url)

            def wait_windows(count):
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with urlopen(url + "/windows", timeout=0.5) as response:
                        if json.load(response)["count"] == count:
                            return
                    time.sleep(0.05)
                pytest.fail(f"Desktop did not report {count} windows")

            wait_windows(1)
            first.reload()
            wait_windows(1)
            second = browser.new_page()
            second.goto(url)
            wait_windows(2)
            first.close()
            wait_windows(1)
            time.sleep(0.8)  # Longer than the grace; the remaining window keeps it alive.
            assert process.poll() is None
            second.close()
            assert process.wait(timeout=5) == 0
        finally:
            browser.close()
