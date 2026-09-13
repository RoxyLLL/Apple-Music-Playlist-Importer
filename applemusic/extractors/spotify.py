"""
Spotify playlist extractor.
Extracts public Spotify playlists without requiring a paid developer account.
"""

import json
import re
from typing import Optional
import requests

from applemusic.extractors.base import BaseExtractor
from applemusic.models import Playlist, Track


class SpotifyExtractor(BaseExtractor):
    """Extracts playlists from Spotify."""

    source_name = "Spotify"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }

    def can_handle(self, source_input: str) -> bool:
        s = source_input.strip()
        return "spotify.com" in s or "spotify:playlist:" in s

    def extract(self, source_input: str) -> Playlist:
        playlist_id = self._resolve_playlist_id(source_input)
        if not playlist_id:
            raise ValueError(f"无法从输入中解析 Spotify 歌单 ID: {source_input}")

        # Try web embed page first (fast & reliable)
        try:
            embed_url = f"https://open.spotify.com/embed/playlist/{playlist_id}"
            r = requests.get(embed_url, headers=self.HEADERS, timeout=12)
            if r.status_code == 200:
                m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text)
                if m:
                    data = json.loads(m.group(1))
                    entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
                    name = entity.get("name", f"Spotify 歌单 {playlist_id}")
                    desc = entity.get("subtitle")
                    track_list = entity.get("trackList", [])
                    
                    tracks = []
                    for t in track_list:
                        raw_title = t.get("title", "").strip()
                        raw_artists = t.get("subtitle", "").strip()
                        artists = [a.strip() for a in raw_artists.split(",") if a.strip()]
                        tracks.append(Track(
                            title=raw_title,
                            artists=artists,
                            duration_ms=t.get("duration"),
                            original_id=t.get("uri"),
                            source="spotify",
                        ))

                    if tracks:
                        return Playlist(
                            name=name,
                            description=desc,
                            source=self.source_name,
                            tracks=tracks,
                        )
        except Exception:
            pass

        # Fallback to public web access token API
        return self._extract_via_api(playlist_id)

    def _extract_via_api(self, playlist_id: str) -> Playlist:
        """Fetch using Spotify Web anonymous access token."""
        token_res = requests.get("https://open.spotify.com/get_access_token", headers=self.HEADERS, timeout=10)
        token_data = token_res.json()
        access_token = token_data.get("accessToken")
        if not access_token:
            raise ValueError("无法获取 Spotify Web Access Token")

        api_headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": self.HEADERS["User-Agent"],
        }

        # Fetch playlist detail
        pl_url = f"https://api.spotify.com/v1/playlists/{playlist_id}"
        resp = requests.get(pl_url, headers=api_headers, timeout=15)
        if resp.status_code != 200:
            raise ValueError(f"获取 Spotify 歌单详情失败: HTTP {resp.status_code}")

        pl_data = resp.json()
        name = pl_data.get("name", f"Spotify 歌单 {playlist_id}")
        desc = pl_data.get("description")

        tracks: list[Track] = []
        next_url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks?limit=100"

        while next_url and len(tracks) < 2000:
            r = requests.get(next_url, headers=api_headers, timeout=15)
            if r.status_code != 200:
                break
            items_data = r.json()
            for item in items_data.get("items", []):
                t = item.get("track")
                if not t:
                    continue
                artists = [a["name"] for a in t.get("artists", []) if a.get("name")]
                tracks.append(Track(
                    title=t.get("name", "").strip(),
                    artists=artists,
                    album=t.get("album", {}).get("name"),
                    duration_ms=t.get("duration_ms"),
                    isrc=t.get("external_ids", {}).get("isrc"),
                    original_id=t.get("id"),
                    source="spotify",
                ))
            next_url = items_data.get("next")

        return Playlist(
            name=name,
            description=desc,
            source=self.source_name,
            tracks=tracks,
        )

    def _resolve_playlist_id(self, source_input: str) -> Optional[str]:
        s = source_input.strip()
        m = re.search(r"playlist[/:]([a-zA-Z0-9]+)", s)
        if m:
            return m.group(1)
        return None
