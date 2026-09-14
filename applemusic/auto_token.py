"""
Automatic Apple Music token extractor for Windows.
Launches Microsoft Edge (or Google Chrome) with DevTools Protocol (CDP),
listens for user login on music.apple.com, and automatically captures media-user-token
without requiring manual DevTools (F12) inspection or copy-pasting.
"""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Callable, Optional
import requests
import websockets

from applemusic.auth import AppleMusicAuth
from applemusic.config import Config, get_config


def find_free_port() -> int:
    """Find an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def find_browser_executable() -> Optional[str]:
    """Find Microsoft Edge or Google Chrome on Windows."""
    candidates = [
        # Edge (Available on all Windows 10/11)
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
        # Chrome
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


class BrowserTokenCapturer:
    """Manages launching browser in CDP mode and extracting media-user-token."""

    def __init__(self, port: Optional[int] = None, config: Optional[Config] = None):
        self.port = port or find_free_port()
        self.config = config or get_config()
        self.browser_exe = find_browser_executable()
        self.profile_dir = tempfile.mkdtemp(prefix="applemusic_login_")

    def capture(
        self,
        timeout_seconds: int = 180,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> Optional[str]:
        """
        Launch browser, open music.apple.com, wait for user login,
        and automatically capture media-user-token.
        """
        if not self.browser_exe:
            if on_status:
                on_status("未在系统中找到 Microsoft Edge 或 Google Chrome 浏览器")
            return None

        os.makedirs(self.profile_dir, exist_ok=True)

        cmd = [
            self.browser_exe,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "https://music.apple.com",
        ]

        if on_status:
            on_status("正在打开 Apple Music 网页登录窗口，请在弹出的浏览器中登录您的 Apple ID...")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            return asyncio.run(
                self._poll_for_token(proc, timeout_seconds, on_status)
            )
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                pass
            try:
                shutil.rmtree(self.profile_dir, ignore_errors=True)
            except Exception:
                pass

    async def _poll_for_token(
        self,
        proc: subprocess.Popen,
        timeout_seconds: int,
        on_status: Optional[Callable[[str], None]],
    ) -> Optional[str]:
        """Poll CDP Storage.getCookies until media-user-token is captured."""
        start_time = time.time()
        ws_url = None

        # Wait for CDP endpoint to become ready
        for _ in range(20):
            if proc.poll() is not None:
                if on_status:
                    on_status("浏览器窗口已关闭")
                return None
            try:
                resp = requests.get(f"http://127.0.0.1:{self.port}/json", timeout=2)
                if resp.status_code == 200:
                    pages = resp.json()
                    for p in pages:
                        if "apple.com" in p.get("url", ""):
                            ws_url = p.get("webSocketDebuggerUrl")
                            break
                    if not ws_url and pages:
                        ws_url = pages[0].get("webSocketDebuggerUrl")
                    if ws_url:
                        break
            except Exception:
                pass
            await asyncio.sleep(0.5)

        if not ws_url:
            if on_status:
                on_status("无法连接到浏览器调试通道")
            return None

        if on_status:
            on_status("已连接至浏览器，等待登录并捕获 Token...")

        # Connect WebSocket and poll cookies
        async with websockets.connect(ws_url) as ws:
            msg_id = 1
            while time.time() - start_time < timeout_seconds:
                if proc.poll() is not None:
                    if on_status:
                        on_status("浏览器窗口已关闭")
                    return None

                # Query cookies from all storage
                req = {"id": msg_id, "method": "Storage.getCookies"}
                msg_id += 1
                await ws.send(json.dumps(req))

                try:
                    resp_text = await asyncio.wait_for(ws.recv(), timeout=3.0)
                    resp_data = json.loads(resp_text)
                    cookies = resp_data.get("result", {}).get("cookies", [])
                    for c in cookies:
                        cname = c.get("name", "")
                        domain = c.get("domain", "")
                        val = c.get("value", "")
                        if cname == "media-user-token" and "apple.com" in domain and len(val) > 20:
                            # Verify and save!
                            if on_status:
                                on_status("✓ 成功捕获到 Token！正在验证有效性...")

                            auth = AppleMusicAuth(self.config)
                            is_valid, sf_info = auth.validate_user_token(val)
                            if is_valid:
                                self.config.media_user_token = val
                                self.config.storefront = sf_info
                                self.config.save()
                                if on_status:
                                    on_status(f"🎉 验证成功！账号所属区域为 [{sf_info.upper()}]，已自动保存！")
                                return val
                            else:
                                if on_status:
                                    on_status(f"捕获到 Token 但验证失败: {sf_info}，继续等待完整登录...")
                except asyncio.TimeoutError:
                    pass
                except Exception:
                    pass

                await asyncio.sleep(1.5)

        if on_status:
            on_status("等待超时 (超过 3 分钟未登录)")
        return None
