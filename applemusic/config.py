"""
Configuration management for Apple Music Playlist Importer.
"""

import json
import base64
import ctypes
import os
import sys
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field

DEFAULT_CONFIG_DIR = Path.home() / ".applemusic_sync"
CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"
DPAPI_PREFIX = "dpapi:"


def _encrypt_token(token: Optional[str]) -> Optional[str]:
    """Encrypt a sensitive token using Windows DPAPI."""
    if not token or not token.strip():
        return token
    if token.startswith(DPAPI_PREFIX):
        return token
    if os.name != "nt":
        return token

    try:
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        data_bytes = token.encode("utf-8")
        in_blob = DATA_BLOB()
        in_blob.cbData = len(data_bytes)
        in_blob.pbData = ctypes.cast(ctypes.create_string_buffer(data_bytes), ctypes.POINTER(ctypes.c_byte))
        out_blob = DATA_BLOB()

        if ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(in_blob), "AppleMusicUserToken", None, None, None, 0, ctypes.byref(out_blob)
        ):
            encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)
            return DPAPI_PREFIX + base64.b64encode(encrypted).decode("ascii")
    except Exception as e:
        print(f"[Warning] DPAPI encryption failed: {e}")
    return token


def _decrypt_token(token: Optional[str]) -> Optional[str]:
    """Decrypt a DPAPI-encrypted token."""
    if not token or not token.strip():
        return token
    if not token.startswith(DPAPI_PREFIX):
        return token
    if os.name != "nt":
        return token

    try:
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        cipher_b64 = token[len(DPAPI_PREFIX):]
        data_bytes = base64.b64decode(cipher_b64.encode("ascii"))
        in_blob = DATA_BLOB()
        in_blob.cbData = len(data_bytes)
        in_blob.pbData = ctypes.cast(ctypes.create_string_buffer(data_bytes), ctypes.POINTER(ctypes.c_byte))
        out_blob = DATA_BLOB()

        if ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
        ):
            decrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)
            return decrypted.decode("utf-8")
    except Exception as e:
        print(f"[Warning] DPAPI decryption failed: {e}")
    return token


class Config(BaseModel):
    """User configuration schema."""
    developer_token: Optional[str] = None
    developer_token_exp: Optional[int] = None
    media_user_token: Optional[str] = None
    storefront: str = "cn"
    auto_confirm: bool = False
    min_score: float = 0.55
    auto_accept_threshold: float = 0.88
    min_review_score: float = 0.55
    min_score_gap: float = 0.08
    search_limit: int = 10

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "Config":
        """Load configuration from disk, creating default if not exists."""
        target_file = config_path or CONFIG_FILE
        if target_file.exists():
            try:
                with open(target_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "media_user_token" in data and data["media_user_token"]:
                        data["media_user_token"] = _decrypt_token(data["media_user_token"])
                    return cls(**data)
            except Exception as e:
                print(f"[Warning] Failed to load config from {target_file}: {e}")
        return cls()

    def save(self, config_path: Optional[Path] = None) -> None:
        """Save configuration to disk with encrypted sensitive tokens."""
        target_file = config_path or CONFIG_FILE
        target_file.parent.mkdir(parents=True, exist_ok=True)
        data = self.model_dump()
        if data.get("media_user_token"):
            data["media_user_token"] = _encrypt_token(data["media_user_token"])
        with open(target_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def is_authorized(self) -> bool:
        """Check if both developer token and media user token are configured."""
        return bool(self.media_user_token and len(self.media_user_token.strip()) > 10)


def get_config() -> Config:
    """Convenience accessor to get active config."""
    return Config.load()
