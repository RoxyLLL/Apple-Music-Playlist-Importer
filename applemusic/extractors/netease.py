"""
NetEase Cloud Music (网易云音乐) playlist extractor.
"""

import json
import re
from typing import List, Optional
import requests

from applemusic.extractors.base import BaseExtractor
from applemusic.models import Playlist, Track


class NetEaseExtractor(BaseExtractor):
    """Extracts playlists from NetEase Cloud Music (163.com)."""

    source_name = "NetEase Cloud Music"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://music.163.com/",
    }

    def can_handle(self, source_input: str) -> bool:
        s = source_input.strip()
        return bool(
            "163.com" in s
            or "163cn.tv" in s
            or "music.163" in s
            or (s.isdigit() and len(s) >= 6)
        )

    def extract(self, source_input: str) -> Playlist:
        playlist_id = self._resolve_playlist_id(source_input)
        if not playlist_id:
            raise ValueError(f"无法从输入中解析网易云歌单 ID: {source_input}")

        # 1. Fetch playlist detail
        detail_url = "https://music.163.com/api/v6/playlist/detail"
        resp = requests.get(
            detail_url,
            params={"id": playlist_id},
            headers=self.HEADERS,
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 200 or "playlist" not in data:
            raise ValueError(f"获取网易云歌单失败 (code={data.get('code')}): 歌单可能不存在或为私密歌单")

        pl_info = data["playlist"]
        pl_name = pl_info.get("name", f"网易云歌单_{playlist_id}")
        pl_desc = pl_info.get("description")
        pl_cover = pl_info.get("coverImgUrl")

        track_ids = [str(x["id"]) for x in pl_info.get("trackIds", [])]
        if not track_ids:
            # Fallback to direct tracks array if trackIds is empty
            direct_tracks = pl_info.get("tracks", [])
            track_ids = [str(x["id"]) for x in direct_tracks]

        # 2. Batch fetch full song details (up to 1000 songs)
        tracks = self._fetch_songs_batch(track_ids)

        return Playlist(
            name=pl_name,
            description=pl_desc,
            cover_url=pl_cover,
            source=self.source_name,
            tracks=tracks,
        )

    def _resolve_playlist_id(self, source_input: str) -> Optional[str]:
        """Extract playlist ID from link or raw input."""
        s = source_input.strip()
        if s.isdigit():
            return s

        # Check if short link 163cn.tv
        if "163cn.tv" in s or "music.163.com" in s:
            if "163cn.tv" in s:
                try:
                    # Follow redirect
                    res = requests.head(s, allow_redirects=True, headers=self.HEADERS, timeout=10)
                    s = res.url
                except Exception:
                    pass

            # Match id=123456
            m = re.search(r"id=(\d+)", s)
            if m:
                return m.group(1)
            # Match /playlist/123456
            m2 = re.search(r"/playlist/(\d+)", s)
            if m2:
                return m2.group(1)

        return None

    def _fetch_songs_batch(self, track_ids: List[str], batch_size: int = 100) -> List[Track]:
        """Fetch song details in chunks from NetEase song/detail endpoint."""
        url = "https://music.163.com/api/v3/song/detail"
        tracks: List[Track] = []

        for i in range(0, len(track_ids), batch_size):
            batch = track_ids[i : i + batch_size]
            payload = {"c": json.dumps([{"id": int(tid)} for tid in batch])}
            try:
                r = requests.post(url, headers=self.HEADERS, data=payload, timeout=15)
                if r.status_code == 200:
                    songs = r.json().get("songs", [])
                    for s in songs:
                        artists = [a["name"].strip() for a in s.get("ar", []) if a.get("name")]
                        album_name = s.get("al", {}).get("name")
                        track = Track(
                            title=s.get("name", "").strip(),
                            artists=artists,
                            album=album_name,
                            duration_ms=s.get("dt"),
                            original_id=str(s.get("id")),
                            source="netease",
                        )
                        tracks.append(track)
            except Exception as e:
                print(f"[Warning] 获取网易云曲目详情批次失败: {e}")

        return tracks
