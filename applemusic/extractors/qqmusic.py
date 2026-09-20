"""
QQ Music (QQ音乐) playlist extractor.
"""

import re
from typing import Optional
import requests

from applemusic.extractors.base import BaseExtractor
from applemusic.models import Playlist, Track


class QQMusicExtractor(BaseExtractor):
    """Extracts playlists from QQ Music (y.qq.com)."""

    source_name = "QQ Music"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://y.qq.com/",
    }

    def can_handle(self, source_input: str) -> bool:
        s = source_input.strip()
        return bool(
            "qq.com" in s
            or "y.qq.com" in s
            or "c6.y.qq.com" in s
        )

    def extract(self, source_input: str) -> Playlist:
        disstid = self._resolve_disstid(source_input)
        if not disstid:
            raise ValueError(f"无法解析 QQ 音乐歌单 ID: {source_input}")

        url = "https://i.y.qq.com/qzone-music/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg"
        params = {
            "type": 1,
            "json": 1,
            "utf8": 1,
            "onlysong": 0,
            "nosign": 1,
            "disstid": disstid,
            "g_tk": 5381,
            "loginUin": 0,
            "hostUin": 0,
            "format": "json",
            "inCharset": "GB2312",
            "outCharset": "utf-8",
            "notice": 0,
            "platform": "yqq",
            "needNewCode": 0,
        }
        headers = dict(self.HEADERS)
        headers["Referer"] = f"https://y.qq.com/n/ryqq/playlist/{disstid}"

        resp = requests.get(url, params=params, headers=headers, timeout=15)
        data = resp.json()

        cdlist = data.get("cdlist", [])
        if not cdlist:
            raise ValueError(f"获取 QQ 音乐歌单失败: 未找到歌单数据或歌单未公开")

        cd = cdlist[0]
        pl_name = cd.get("dissname", f"QQ音乐歌单_{disstid}")
        pl_desc = cd.get("desc")
        pl_cover = cd.get("logo")

        raw_songs = cd.get("songlist", [])
        tracks = []
        for s in raw_songs:
            singers = [a["name"].strip() for a in s.get("singer", []) if isinstance(a, dict) and a.get("name")]
            if not singers and s.get("singer_name"):
                singers = [s.get("singer_name").strip()]
            song_name = s.get("songname", "") or s.get("name", "") or s.get("title", "")
            album_name = s.get("albumname", "") or s.get("album", {}).get("name", "") or s.get("album_name", "")
            interval = s.get("interval", 0)  # seconds
            duration_ms = interval * 1000 if interval else None

            orig_id = s.get("songmid") or s.get("songid")
            raw_isrc = s.get("isrc") or s.get("song_isrc") or s.get("f_isrc") or ""
            clean_isrc = str(raw_isrc).strip().upper() if raw_isrc else None

            raw_trans = (s.get("trans_name") or s.get("subtitle") or "").strip()
            trans_title = raw_trans if raw_trans else None
            alias_list = [raw_trans] if raw_trans else []

            track = Track(
                title=song_name.strip(),
                artists=singers,
                album=album_name.strip() if album_name else None,
                duration_ms=duration_ms,
                original_id=str(orig_id).strip() if orig_id else None,
                isrc=clean_isrc if clean_isrc and len(clean_isrc) >= 8 else None,
                source="qqmusic",
                trans_title=trans_title,
                aliases=alias_list,
            )
            tracks.append(track)

        return Playlist(
            name=pl_name,
            description=pl_desc,
            cover_url=pl_cover,
            source=self.source_name,
            tracks=tracks,
        )

    def _resolve_disstid(self, source_input: str) -> Optional[str]:
        """Extract disstid from QQ Music link or text."""
        s = source_input.strip()
        if s.isdigit() and len(s) >= 8:
            return s

        if "c6.y.qq.com" in s or "y.qq.com" in s:
            if "c6.y.qq.com" in s:
                try:
                    res = requests.head(s, allow_redirects=True, headers=self.HEADERS, timeout=10)
                    s = res.url
                except Exception:
                    pass

            # /playlist/(\d+)
            m = re.search(r"playlist/(\d+)", s)
            if m:
                return m.group(1)
            # id=(\d+)
            m2 = re.search(r"id=(\d+)", s)
            if m2:
                return m2.group(1)
            # disstid=(\d+)
            m3 = re.search(r"disstid=(\d+)", s)
            if m3:
                return m3.group(1)

        return None
