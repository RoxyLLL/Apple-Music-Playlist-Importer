"""
Local music library manager module.
Handles local audio file scanning, metadata reading/editing (via mutagen),
batch deletion, and one-click ingestion into Apple Music.
"""

import os
import shutil
import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from applemusic.extractors.bilibili_downloader import (
    get_apple_music_auto_add_dir,
    get_backup_download_dir,
    sanitize_filename,
)

logger = logging.getLogger(__name__)

SUPPORTED_AUDIO_EXTENSIONS = {".m4a", ".mp3", ".flac", ".wav", ".aac", ".ogg", ".alac"}


def format_file_size(size_bytes: int) -> str:
    """Format bytes into human-readable string (e.g. 3.4 MB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_duration(seconds: float) -> str:
    """Format seconds into mm:ss."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


def get_apple_music_library_media_dir() -> Optional[str]:
    """
    Locate the local Apple Music app's media library directory.
    On Windows:
      1. %USERPROFILE%\\Music\\Apple Music\\Media\\Music
      2. %USERPROFILE%\\Music\\Apple Music\\Media
      3. %USERPROFILE%\\Music\\iTunes\\iTunes Media\\Music
      4. %USERPROFILE%\\Music\\Apple Music
    """
    userprofile = os.environ.get("USERPROFILE") or str(Path.home())
    candidates = [
        os.path.join(userprofile, "Music", "Apple Music", "Media", "Music"),
        os.path.join(userprofile, "Music", "Apple Music", "Media"),
        os.path.join(userprofile, "Music", "iTunes", "iTunes Media", "Music"),
        os.path.join(userprofile, "Music", "Apple Music"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return os.path.normpath(c)
    return None


def get_default_local_dirs() -> List[str]:
    """Get list of scanned default music directories, prioritizing local Apple Music library."""
    dirs = []
    am_dir = get_apple_music_library_media_dir()
    if am_dir and os.path.isdir(am_dir):
        dirs.append(am_dir)

    backup_dir = get_backup_download_dir()
    if os.path.isdir(backup_dir) and backup_dir not in dirs:
        dirs.append(backup_dir)

    userprofile = os.environ.get("USERPROFILE", "")
    standard_music = os.path.join(userprofile, "Music")
    if os.path.isdir(standard_music) and standard_music not in dirs:
        dirs.append(standard_music)

    return dirs


def list_local_tracks(directory: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Scan local Apple Music library directory (or specified directory) for audio files recursively.
    Extracts metadata, duration, file size, and cover availability.
    """
    target_dirs = [directory] if directory and os.path.isdir(directory) else get_default_local_dirs()

    results: List[Dict[str, Any]] = []
    seen_paths = set()

    for d in target_dirs:
        if not os.path.isdir(d):
            continue

        try:
            for root, _, filenames in os.walk(d):
                for fname in filenames:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in SUPPORTED_AUDIO_EXTENSIONS:
                        continue

                    abs_path = os.path.abspath(os.path.join(root, fname))
                    if abs_path in seen_paths:
                        continue
                    seen_paths.add(abs_path)

                    try:
                        stat = os.stat(abs_path)
                        size_bytes = stat.st_size
                        mtime = stat.st_mtime
                        mtime_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
                    except Exception:
                        size_bytes = 0
                        mtime = 0
                        mtime_str = ""

                    base_name = os.path.splitext(fname)[0]
                    inferred_artist = "未知歌手"
                    inferred_title = base_name
                    if " - " in base_name:
                        parts = base_name.split(" - ", 1)
                        inferred_artist = parts[0].strip()
                        inferred_title = parts[1].strip()

                    title = inferred_title
                    artist = inferred_artist
                    album = "Apple Music 资料库"
                    duration_sec = 0.0
                    has_cover = False

                    # Read actual metadata via mutagen if available
                    try:
                        import mutagen
                        audio = mutagen.File(abs_path)
                        if audio is not None:
                            if hasattr(audio, "info") and hasattr(audio.info, "length"):
                                duration_sec = float(audio.info.length or 0)

                            if ext == ".m4a":
                                tags = audio.tags or {}
                                if "\xa9nam" in tags and tags["\xa9nam"]:
                                    title = str(tags["\xa9nam"][0])
                                if "\xa9ART" in tags and tags["\xa9ART"]:
                                    artist = str(tags["\xa9ART"][0])
                                elif "aART" in tags and tags["aART"]:
                                    artist = str(tags["aART"][0])
                                if "\xa9alb" in tags and tags["\xa9alb"]:
                                    album = str(tags["\xa9alb"][0])
                                if "covr" in tags and tags["covr"]:
                                    has_cover = True
                            elif ext == ".mp3":
                                tags = audio.tags or {}
                                if "TIT2" in tags:
                                    title = str(tags["TIT2"])
                                if "TPE1" in tags:
                                    artist = str(tags["TPE1"])
                                if "TALB" in tags:
                                    album = str(tags["TALB"])
                                if any(k.startswith("APIC") for k in tags.keys()):
                                    has_cover = True
                            elif ext == ".flac":
                                tags = audio.tags or {}
                                if "title" in tags and tags["title"]:
                                    title = tags["title"][0]
                                if "artist" in tags and tags["artist"]:
                                    artist = tags["artist"][0]
                                if "album" in tags and tags["album"]:
                                    album = tags["album"][0]
                                if hasattr(audio, "pictures") and audio.pictures:
                                    has_cover = True
                    except Exception as meta_err:
                        logger.debug("读取文件元数据跳过 (%s): %s", fname, meta_err)

                    results.append({
                        "file_path": abs_path,
                        "file_name": fname,
                        "title": title,
                        "artist": artist,
                        "album": album,
                        "duration_sec": duration_sec,
                        "duration_str": format_duration(duration_sec) if duration_sec > 0 else "--:--",
                        "file_size": size_bytes,
                        "file_size_str": format_file_size(size_bytes),
                        "mtime": mtime,
                        "mtime_str": mtime_str,
                        "extension": ext,
                        "has_cover": has_cover,
                    })
        except Exception as scan_err:
            logger.warning("扫描目录异常 (%s): %s", d, scan_err)

    # Sort by mtime descending (most recently modified first)
    results.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    return results


def update_local_track_metadata(
    file_path: str,
    title: Optional[str] = None,
    artist: Optional[str] = None,
    album: Optional[str] = None,
    rename_file: bool = False,
) -> Dict[str, Any]:
    """
    Update ID3/MP4 metadata for a local audio file.
    Optionally renames the file to match 'Artist - Title.ext'.
    """
    if not os.path.isfile(file_path):
        return {"success": False, "error": "文件不存在"}

    ext = os.path.splitext(file_path)[1].lower()
    updated_title = title.strip() if title and title.strip() else None
    updated_artist = artist.strip() if artist and artist.strip() else None
    updated_album = album.strip() if album and album.strip() else None

    try:
        import mutagen

        audio = mutagen.File(file_path)
        if audio is None:
            return {"success": False, "error": "无法解析音频文件格式"}

        if ext == ".m4a":
            if audio.tags is None:
                audio.add_tags()
            if updated_title:
                audio.tags["\xa9nam"] = updated_title
            if updated_artist:
                audio.tags["\xa9ART"] = updated_artist
            if updated_album:
                audio.tags["\xa9alb"] = updated_album
            audio.save()

        elif ext == ".mp3":
            from mutagen.easyid3 import EasyID3
            try:
                tags = EasyID3(file_path)
            except Exception:
                from mutagen.id3 import ID3
                id3 = ID3()
                id3.save(file_path)
                tags = EasyID3(file_path)

            if updated_title:
                tags["title"] = updated_title
            if updated_artist:
                tags["artist"] = updated_artist
            if updated_album:
                tags["album"] = updated_album
            tags.save()

        elif ext == ".flac":
            if audio.tags is None:
                audio.add_tags()
            if updated_title:
                audio.tags["title"] = updated_title
            if updated_artist:
                audio.tags["artist"] = updated_artist
            if updated_album:
                audio.tags["album"] = updated_album
            audio.save()

        new_file_path = file_path
        if rename_file and (updated_title or updated_artist):
            dir_name = os.path.dirname(file_path)
            cur_artist = updated_artist or "未知歌手"
            cur_title = updated_title or os.path.splitext(os.path.basename(file_path))[0]
            new_name = f"{sanitize_filename(cur_artist)} - {sanitize_filename(cur_title)}{ext}"
            target_path = os.path.join(dir_name, new_name)
            if target_path != file_path and not os.path.exists(target_path):
                os.rename(file_path, target_path)
                new_file_path = target_path

        return {
            "success": True,
            "file_path": new_file_path,
            "title": updated_title,
            "artist": updated_artist,
            "album": updated_album,
        }

    except Exception as e:
        logger.error("修改文件元数据失败 (%s): %s", file_path, e)
        return {"success": False, "error": str(e)}


def batch_delete_local_tracks(file_paths: List[str]) -> Tuple[int, List[str]]:
    """
    Safely delete local audio files from disk.
    Returns (success_count, failed_paths).
    """
    success = 0
    failed = []

    for fp in file_paths:
        try:
            if os.path.isfile(fp):
                dir_name = os.path.dirname(fp)
                os.remove(fp)
                success += 1
                try:
                    if os.path.isdir(dir_name) and not os.listdir(dir_name):
                        os.rmdir(dir_name)
                        parent_dir = os.path.dirname(dir_name)
                        if os.path.isdir(parent_dir) and not os.listdir(parent_dir):
                            os.rmdir(parent_dir)
                except Exception:
                    pass
            else:
                failed.append(fp)
        except Exception as e:
            logger.error("删除本地文件失败 (%s): %s", fp, e)
            failed.append(fp)

    return success, failed


def import_local_tracks_to_applemusic(file_paths: List[str]) -> Tuple[int, List[str]]:
    """
    Copy local audio files to Apple Music 'Automatically Add to Music' directory.
    Returns (success_count, failed_paths).
    """
    auto_dir = get_apple_music_auto_add_dir()
    if not auto_dir or not os.path.isdir(auto_dir):
        return 0, file_paths

    success = 0
    failed = []

    for fp in file_paths:
        try:
            if os.path.isfile(fp):
                dest = os.path.join(auto_dir, os.path.basename(fp))
                shutil.copy2(fp, dest)
                success += 1
            else:
                failed.append(fp)
        except Exception as e:
            logger.error("复制到 Apple Music 导入目录失败 (%s): %s", fp, e)
            failed.append(fp)

    return success, failed
