"""
Playlist extractors package.
"""

from applemusic.extractors.base import BaseExtractor, get_extractor_for
from applemusic.extractors.netease import NetEaseExtractor
from applemusic.extractors.qqmusic import QQMusicExtractor
from applemusic.extractors.spotify import SpotifyExtractor
from applemusic.extractors.local_file import LocalFileExtractor

__all__ = [
    "BaseExtractor",
    "get_extractor_for",
    "NetEaseExtractor",
    "QQMusicExtractor",
    "SpotifyExtractor",
    "LocalFileExtractor",
]
