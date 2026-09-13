"""
Base extractor interface for playlist sources.
"""

from abc import ABC, abstractmethod
from typing import Optional
import re
from applemusic.models import Playlist


class BaseExtractor(ABC):
    """Abstract base class for music platform playlist extractors."""

    source_name: str = "base"

    @abstractmethod
    def can_handle(self, source_input: str) -> bool:
        """Check whether this extractor can parse the given input (URL/path/ID)."""
        pass

    @abstractmethod
    def extract(self, source_input: str) -> Playlist:
        """Extract playlist metadata and tracks from the source."""
        pass


def get_extractor_for(source_input: str) -> Optional[BaseExtractor]:
    """Factory function to auto-detect and instantiate the appropriate extractor."""
    from applemusic.extractors.netease import NetEaseExtractor
    from applemusic.extractors.qqmusic import QQMusicExtractor
    from applemusic.extractors.spotify import SpotifyExtractor
    from applemusic.extractors.local_file import LocalFileExtractor

    extractors = [
        NetEaseExtractor(),
        QQMusicExtractor(),
        SpotifyExtractor(),
        LocalFileExtractor(),
    ]

    for ext in extractors:
        if ext.can_handle(source_input.strip()):
            return ext

    return None
