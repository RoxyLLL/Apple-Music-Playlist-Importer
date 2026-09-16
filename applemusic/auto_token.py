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
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
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
        
        # Use persistent profile so user login session and cookies persist across runs
        app_dir = Path.home() / ".applemusic"
        app_dir.mkdir(parents=True, exist_ok=True)
        self.profile_dir = str(app_dir / "browser_profile")
        
        self.proc: Optional[subprocess.Popen] = None
        self._cancelled = False
        self._lock = threading.Lock()
        self.state: Dict = {
            "active": False,
            "status": "idle",
            "message": "",
            "success": False,
            "is_authorized": False,
            "storefront": "",
        }

    def _update_state(self, message: str, status: str = "running", success: bool = False, is_authorized: bool = False, storefront: str = ""):
        with self._lock:
            self.state["message"] = message
            self.state["status"] = status
            self.state["success"] = success
            self.state["is_authorized"] = is_authorized
            if storefront:
                self.state["storefront"] = storefront

    def get_state(self) -> Dict:
        with self._lock:
            return dict(self.state)

    def is_active(self) -> bool:
        with self._lock:
            return bool(self.state.get("active", False))

    def cancel(self):
        """Cancel and terminate browser session."""
        self._cancelled = True
        with self._lock:
            self.state["active"] = False
            self.state["status"] = "cancelled"
            self.state["message"] = "自动登录已取消"
        if self.proc:
            try:
                self.proc.terminate()
            except Exception:
                pass

    def capture(
        self,
        timeout_seconds: int = 240,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> Optional[str]:
        """
        Launch browser, open music.apple.com, wait for user login,
        and automatically capture media-user-token.
        """
        if not self.browser_exe:
            msg = "未在系统中找到 Microsoft Edge 或 Google Chrome 浏览器"
            self._update_state(msg, status="failed")
            if on_status:
                on_status(msg)
            return None

        os.makedirs(self.profile_dir, exist_ok=True)
        with self._lock:
            self.state["active"] = True
            self.state["status"] = "starting"
            self.state["message"] = "正在唤起浏览器窗口并连接调试通道..."
            self._cancelled = False

        cmd = [
            self.browser_exe,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-fre",
            "--disable-features=msEdgeSyncDialog,msEdgeProfilePicker",
            "--window-size=1200,850",
            "https://music.apple.com",
        ]

        if on_status:
            on_status("正在打开 Apple Music 网页登录窗口，请在弹出的浏览器中登录您的 Apple ID...")

        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            msg = f"启动浏览器失败: {e}"
            self._update_state(msg, status="failed")
            if on_status:
                on_status(msg)
            return None

        try:
            token = asyncio.run(
                self._poll_for_token(timeout_seconds, on_status)
            )
            return token
        finally:
            with self._lock:
                self.state["active"] = False
            if self.proc:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=2)
                except Exception:
                    pass
                self.proc = None

    async def _poll_for_token(
        self,
        timeout_seconds: int,
        on_status: Optional[Callable[[str], None]],
    ) -> Optional[str]:
        """Poll CDP Storage.getCookies on Browser target until media-user-token is captured."""
        start_time = time.time()
        ws_url = None

        # 1. Wait for CDP endpoint to become ready
        for _ in range(30):
            if self._cancelled:
                return None
            if self.proc and self.proc.poll() is not None:
                msg = "浏览器窗口已关闭，未完成登录"
                self._update_state(msg, status="failed")
                if on_status:
                    on_status(msg)
                return None

            # Try Browser Target first (/json/version) - immune to tab switches & popups!
            try:
                ver_resp = requests.get(f"http://127.0.0.1:{self.port}/json/version", timeout=1.5)
                if ver_resp.status_code == 200:
                    ws_url = ver_resp.json().get("webSocketDebuggerUrl")
                    if ws_url:
                        break
            except Exception:
                pass

            # Fallback: search /json pages
            try:
                resp = requests.get(f"http://127.0.0.1:{self.port}/json", timeout=1.5)
                if resp.status_code == 200:
                    pages = resp.json()
                    for p in pages:
                        if "apple.com" in p.get("url", ""):
                            ws_url = p.get("webSocketDebuggerUrl")
                            break
                    if ws_url:
                        break
            except Exception:
                pass

            await asyncio.sleep(0.5)

        if not ws_url:
            msg = "无法建立与浏览器的调试通信通道"
            self._update_state(msg, status="failed")
            if on_status:
                on_status(msg)
            return None

        msg = "已成功连接 Edge 浏览器，请在弹出的 Apple Music 网页中登录您的 Apple ID..."
        self._update_state(msg, status="waiting_login")
        if on_status:
            on_status(msg)

        # 2. Connect WebSocket to Browser target and poll cookies
        try:
            async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
                msg_id = 1
                while time.time() - start_time < timeout_seconds:
                    if self._cancelled:
                        return None
                    if self.proc and self.proc.poll() is not None:
                        msg = "浏览器窗口已关闭，未能捕获登录凭据"
                        self._update_state(msg, status="failed")
                        if on_status:
                            on_status(msg)
                        return None

                    # Query all cookies across all domains in browser profile
                    req = {"id": msg_id, "method": "Storage.getCookies"}
                    msg_id += 1
                    try:
                        await ws.send(json.dumps(req))
                        resp_text = await asyncio.wait_for(ws.recv(), timeout=3.5)
                        resp_data = json.loads(resp_text)
                        cookies = resp_data.get("result", {}).get("cookies", [])
                        
                        for c in cookies:
                            cname = c.get("name", "").strip()
                            domain = c.get("domain", "").lower()
                            val = c.get("value", "").strip()
                            val = urllib.parse.unquote(val).strip('"').strip("'")
                            
                            # Apple Music token cookie
                            if cname.lower() in ("media-user-token", "music-user-token", "user-token") and len(val) > 20:
                                check_msg = "✓ 截获到 Apple Music 登录凭证！正在向 Apple 校验有效性..."
                                self._update_state(check_msg, status="verifying")
                                if on_status:
                                    on_status(check_msg)

                                auth = AppleMusicAuth(self.config)
                                is_valid, sf_info = auth.validate_user_token(val)
                                if is_valid:
                                    self.config.media_user_token = val
                                    self.config.storefront = sf_info
                                    self.config.save()
                                    succ_msg = f"🎉 验证成功！账号所属区域为 [{sf_info.upper()}]，已自动连接！"
                                    self._update_state(succ_msg, status="success", success=True, is_authorized=True, storefront=sf_info)
                                    if on_status:
                                        on_status(succ_msg)
                                    return val
                                else:
                                    fail_msg = f"检测到 Token 但验证未通过: {sf_info}，继续监听登录..."
                                    self._update_state(fail_msg, status="waiting_login")
                                    if on_status:
                                        on_status(fail_msg)
                    except (asyncio.TimeoutError, websockets.ConnectionClosed):
                        pass
                    except Exception:
                        pass

                    await asyncio.sleep(1.5)
        except Exception as e:
            if not self._cancelled:
                err_msg = f"调试通信异常: {e}"
                self._update_state(err_msg, status="failed")
                if on_status:
                    on_status(err_msg)
            return None

        timeout_msg = "登录等待超时（超过 4 分钟未完成登录）"
        self._update_state(timeout_msg, status="failed")
        if on_status:
            on_status(timeout_msg)
        return None
