"""
Local file (TXT / CSV) playlist extractor.
"""

import csv
from pathlib import Path
from typing import List
from applemusic.extractors.base import BaseExtractor
from applemusic.models import Playlist, Track


class LocalFileExtractor(BaseExtractor):
    """Extracts track lists from local CSV or TXT text files."""

    source_name = "Local File"

    def can_handle(self, source_input: str) -> bool:
        p = Path(source_input.strip(' "\''))
        return p.exists() and p.is_file() and p.suffix.lower() in (".txt", ".csv")

    def extract(self, source_input: str) -> Playlist:
        p = Path(source_input.strip(' "\''))
        if not p.exists():
            raise FileNotFoundError(f"未找到文件: {p}")

        if p.suffix.lower() == ".csv":
            tracks = self._parse_csv(p)
        else:
            tracks = self._parse_txt(p)

        return Playlist(
            name=p.stem,
            description=f"从本地文件 {p.name} 导入",
            source=self.source_name,
            tracks=tracks,
        )

    def _parse_csv(self, file_path: Path) -> List[Track]:
        tracks: List[Track] = []
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            headers = None
            for row in reader:
                if not row or not any(row):
                    continue
                if headers is None:
                    # Check if first row is header
                    row_lower = [c.strip().lower() for c in row]
                    if any(k in row_lower for k in ["title", "name", "歌名", "歌曲", "track"]):
                        headers = row_lower
                        continue
                    else:
                        headers = []

                if headers:
                    # Named columns
                    title = ""
                    artist = ""
                    album = ""
                    isrc = ""
                    for idx, h in enumerate(headers):
                        if idx < len(row):
                            val = row[idx].strip()
                            if h in ("title", "name", "歌名", "歌曲", "track"):
                                title = val
                            elif h in ("artist", "artists", "singer", "歌手"):
                                artist = val
                            elif h in ("album", "专辑"):
                                album = val
                            elif h in ("isrc", "isrc_code"):
                                isrc = val
                    if title:
                        artists = [a.strip() for a in artist.split("/") if a.strip()] if artist else []
                        tracks.append(Track(
                            title=title,
                            artists=artists,
                            album=album or None,
                            isrc=isrc.strip().upper() if isrc.strip() else None,
                            source="local_csv",
                        ))
                else:
                    # Positional columns: Col 0 = Title, Col 1 = Artist
                    title = row[0].strip()
                    artist = row[1].strip() if len(row) > 1 else ""
                    artists = [a.strip() for a in artist.split("/") if a.strip()] if artist else []
                    tracks.append(Track(title=title, artists=artists, source="local_csv"))

        return tracks

    def _parse_txt(self, file_path: Path) -> List[Track]:
        tracks: List[Track] = []
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                title = ""
                artists: List[str] = []

                # Delimiter checks: " - " or "\t" or ","
                if " - " in line:
                    parts = line.split(" - ", 1)
                    title, artist_part = parts[0].strip(), parts[1].strip()
                    artists = [a.strip() for a in artist_part.split("/") if a.strip()]
                elif "\t" in line:
                    parts = line.split("\t", 1)
                    title, artist_part = parts[0].strip(), parts[1].strip()
                    artists = [a.strip() for a in artist_part.split("/") if a.strip()]
                elif "," in line:
                    parts = line.split(",", 1)
                    title, artist_part = parts[0].strip(), parts[1].strip()
                    artists = [a.strip() for a in artist_part.split("/") if a.strip()]
                else:
                    title = line

                tracks.append(Track(title=title, artists=artists, source="local_txt"))

        return tracks
