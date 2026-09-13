"""
Configuration management for Apple Music Playlist Importer.
"""

import json
import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field

DEFAULT_CONFIG_DIR = Path.home() / ".applemusic_sync"
CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"


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
                    return cls(**data)
            except Exception as e:
                print(f"[Warning] Failed to load config from {target_file}: {e}")
        return cls()

    def save(self, config_path: Optional[Path] = None) -> None:
        """Save configuration to disk."""
        target_file = config_path or CONFIG_FILE
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(self.model_dump_json(indent=2))

    def is_authorized(self) -> bool:
        """Check if both developer token and media user token are configured."""
        return bool(self.media_user_token and len(self.media_user_token.strip()) > 10)


def get_config() -> Config:
    """Convenience accessor to get active config."""
    return Config.load()
