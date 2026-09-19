"""
Authentication module for Apple Music Web API.
Handles automatic Developer Token extraction and user token verification.
"""

import json
import os
import re
import time
from typing import Optional, Tuple
import requests

from applemusic.config import Config, get_config


class AppleMusicAuth:
    """Manages Developer Token and User Token authentication for Apple Music."""

    BASE_URL = "https://music.apple.com"
    API_URL = "https://api.music.apple.com/v1"

    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Origin": "https://music.apple.com",
            "Referer": "https://music.apple.com/",
        })

    def get_developer_token(self, force_refresh: bool = False) -> str:
        """
        Get a valid Developer Token.
        Priority:
        1. Environment variable: APPLE_MUSIC_DEVELOPER_TOKEN
        2. Cached token in config if valid and not expired
        3. Dynamic scrape from music.apple.com web client JS
        """
        # 1. Environment variable override
        env_token = os.environ.get("APPLE_MUSIC_DEVELOPER_TOKEN", "").strip()
        if env_token:
            return env_token

        now = int(time.time())
        # 2. Check cached token in config
        if (
            not force_refresh
            and self.config.developer_token
            and (self.config.developer_token_exp is None or self.config.developer_token_exp > now + 3600)
        ):
            return self.config.developer_token

        # 3. Try to scrape fresh token from music.apple.com
        token, exp = self._fetch_developer_token_from_web()
        if token:
            self.config.developer_token = token
            self.config.developer_token_exp = exp
            self.config.save()
            return token

        # 4. Fallback to cached token if still valid
        if self.config.developer_token and self.validate_developer_token(self.config.developer_token):
            return self.config.developer_token

        return ""

    def _fetch_developer_token_from_web(self) -> Tuple[Optional[str], Optional[int]]:
        """Scrape developer token from music.apple.com web client JS files across multiple storefronts."""
        entry_paths = ["/us/browse", "/cn/browse", "/hk/browse", "/tw/browse"]
        for entry in entry_paths:
            try:
                r = self.session.get(f"{self.BASE_URL}{entry}", timeout=10)
                if r.status_code != 200:
                    continue
                r.encoding = "utf-8"

                # Look for index/bundle JS bundles
                scripts = re.findall(r'<script[^>]*src=["\']([^"\']+)["\']', r.text)
                index_scripts = [
                    s for s in scripts if any(k in s.lower() for k in ("index", "bundle", "main", "musickit", "app"))
                ]
                candidate_urls = index_scripts + [s for s in scripts if s not in index_scripts]

                for s in candidate_urls[:8]:
                    url = s if s.startswith("http") else (self.BASE_URL + s)
                    try:
                        res = self.session.get(url, timeout=10)
                        if res.status_code != 200:
                            continue
                        res.encoding = "utf-8"

                        # Search for JWT format with single or double quotes
                        tokens = re.findall(
                            r'[\"\'](ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})[\"\']',
                            res.text
                        )
                        for t in tokens:
                            if len(t) > 100:
                                if self.validate_developer_token(t):
                                    exp = self._parse_jwt_exp(t)
                                    return t, exp
                    except Exception:
                        continue
            except Exception:
                continue

        return None, None

    @staticmethod
    def _parse_jwt_exp(token: str) -> Optional[int]:
        """Extract expiration timestamp from JWT token payload."""
        try:
            import base64
            parts = token.split(".")
            if len(parts) >= 2:
                payload = parts[1]
                # Pad base64
                payload += "=" * ((4 - len(payload) % 4) % 4)
                data = json.loads(base64.urlsafe_b64decode(payload))
                return data.get("exp")
        except Exception:
            pass
        return None

    def validate_developer_token(self, token: str) -> bool:
        """Validate developer token against Catalog search API."""
        try:
            headers = {
                "Authorization": f"Bearer {token}",
                "Origin": "https://music.apple.com",
            }
            r = self.session.get(
                f"{self.API_URL}/catalog/cn/search",
                headers=headers,
                params={"term": "test", "limit": 1},
                timeout=8,
            )
            return r.status_code == 200
        except Exception:
            return False

    def validate_user_token(self, media_user_token: Optional[str] = None) -> Tuple[bool, str]:
        """
        Validate Apple Music media-user-token by calling Library API.
        Returns (is_valid, message_or_storefront).
        """
        dev_token = self.get_developer_token()
        if not dev_token:
            return False, "无法获取 Apple Music 开发者 Token，请检查网络连接或环境变量"
        user_token = media_user_token or self.config.media_user_token

        if not user_token:
            return False, "未配置 media-user-token"

        headers = {
            "Authorization": f"Bearer {dev_token}",
            "Music-User-Token": user_token,
            "Origin": "https://music.apple.com",
            "Referer": "https://music.apple.com/",
        }

        try:
            # Query user's storefront
            r = self.session.get(f"{self.API_URL}/me/storefront", headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json().get("data", [{}])[0]
                sf_id = data.get("id", "cn")
                return True, sf_id
            elif r.status_code in (401, 403):
                return False, f"认证失败 (HTTP {r.status_code})：Token 已过期或无效，请重新登录网页端获取"
            else:
                return False, f"API 响应异常 (HTTP {r.status_code}): {r.text[:100]}"
        except Exception as e:
            return False, f"请求网络异常: {str(e)}"
