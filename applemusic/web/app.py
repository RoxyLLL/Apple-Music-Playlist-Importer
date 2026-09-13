"""
FastAPI Web Application backend for Apple Music Playlist Importer.
"""

import asyncio
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from applemusic.auth import AppleMusicAuth
from applemusic.auto_token import BrowserTokenCapturer
from applemusic.client import AppleMusicClient
from applemusic.config import Config, get_config
from applemusic.extractors import get_extractor_for
from applemusic.matcher.engine import MatchingEngine
from applemusic.models import Playlist, SongMatchResult, Track

thread_pool = ThreadPoolExecutor(max_workers=8)

_shared_client: Optional[AppleMusicClient] = None
_shared_engine: Optional[MatchingEngine] = None

def get_shared_engine() -> Tuple[AppleMusicClient, MatchingEngine]:
    global _shared_client, _shared_engine
    if _shared_client is None:
        cfg = get_config()
        _shared_client = AppleMusicClient(cfg)
        _shared_engine = MatchingEngine(_shared_client, cfg)
    return _shared_client, _shared_engine

import sys

if getattr(sys, "frozen", False):
    STATIC_DIR = Path(sys._MEIPASS) / "applemusic" / "web" / "static"
    if not STATIC_DIR.exists():
        STATIC_DIR = Path(sys._MEIPASS) / "static"
else:
    STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Apple Music Playlist Importer", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConfigUpdateRequest(BaseModel):
    media_user_token: Optional[str] = None
    storefront: Optional[str] = None


class ParseRequest(BaseModel):
    source: Optional[str] = None
    url: Optional[str] = None
    text: Optional[str] = None
    custom_text: Optional[str] = None
    playlist_name: Optional[str] = None


class MatchRequest(BaseModel):
    tracks: List[Track]
    storefront: Optional[str] = None


class SyncTrackItem(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = "songs"
    title: Optional[str] = ""
    artist: Optional[str] = ""
    is_bilibili: Optional[bool] = False


class SyncRequest(BaseModel):
    playlist_name: str
    track_ids: Optional[List[str]] = []
    tracks: Optional[List[SyncTrackItem]] = []
    description: Optional[str] = "Imported by Apple Music Playlist Importer"


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Web UI static files not found</h1>", status_code=404)
    return FileResponse(index_file)


@app.get("/api/config")
async def get_config_status():
    config = get_config()
    auth = AppleMusicAuth(config)
    is_valid, sf_info = False, "未配置"
    if config.media_user_token:
        is_valid, sf_info = auth.validate_user_token()

    masked_token = (
        config.media_user_token[:10] + "..." + config.media_user_token[-8:]
        if config.media_user_token
        else ""
    )

    return {
        "storefront": config.storefront or "cn",
        "has_token": bool(config.media_user_token),
        "token_preview": masked_token,
        "is_authorized": is_valid,
        "detected_storefront": sf_info if is_valid else None,
        "auth_message": sf_info if not is_valid else "连接正常",
    }


@app.post("/api/config")
async def update_config(req: ConfigUpdateRequest):
    config = get_config()
    if req.media_user_token is not None:
        config.media_user_token = req.media_user_token.strip()
    if req.storefront is not None:
        config.storefront = req.storefront.strip().lower()

    config.save()
    global _shared_client, _shared_engine
    _shared_client = None
    _shared_engine = None

    auth = AppleMusicAuth(config)
    is_valid, info = auth.validate_user_token()
    return {
        "success": True,
        "is_authorized": is_valid,
        "message": info,
    }


@app.post("/api/auto-login")
async def trigger_auto_login():
    """Launch Edge browser to automatically capture media-user-token."""
    capturer = BrowserTokenCapturer()
    loop = asyncio.get_running_loop()
    
    token = await loop.run_in_executor(thread_pool, lambda: capturer.capture(timeout_seconds=180))
    if token:
        config = get_config()
        return {
            "success": True,
            "message": f"成功捕获并验证 Token！已连接至 Apple Music [{config.storefront.upper()}]",
            "storefront": config.storefront,
            "is_authorized": True,
        }
    else:
        return {
            "success": False,
            "message": "未能捕获 Token（可能浏览器已提前关闭或未完成登录）",
            "is_authorized": False,
        }


@app.post("/api/parse")
async def parse_playlist(req: ParseRequest):
    source = (req.source or req.url or "").strip()
    if not source:
        raise HTTPException(status_code=400, detail="请输入歌单链接或内容")

    extractor = get_extractor_for(source)
    if not extractor:
        raise HTTPException(
            status_code=400,
            detail="无法识别输入来源，请提供有效的网易云、QQ音乐、Spotify链接或本地文件",
        )

    try:
        playlist: Playlist = await asyncio.to_thread(extractor.extract, source)
        pl_data = {
            "name": playlist.name,
            "description": playlist.description,
            "cover_url": playlist.cover_url,
            "source": playlist.source,
            "track_count": playlist.track_count,
            "tracks": [t.model_dump() for t in playlist.tracks],
        }
        return {
            "success": True,
            **pl_data,
            "playlist": pl_data,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解析歌单失败: {str(e)}")


@app.post("/api/parse-text")
async def parse_text_lines(req: ParseRequest):
    text = (req.source or req.text or req.custom_text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="内容不能为空")
    lines = text.split("\n")
    tracks = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if " - " in line:
            parts = line.split(" - ", 1)
            title, artist_part = parts[0].strip(), parts[1].strip()
            artists = [a.strip() for a in artist_part.split("/") if a.strip()]
        else:
            title = line
            artists = []
        tracks.append(Track(title=title, artists=artists, source="pasted_text"))

    if not tracks:
        raise HTTPException(status_code=400, detail="未解析到有效歌曲行，格式如：晴天 - 周杰伦")

    pl_data = {
        "name": req.playlist_name or "自定义导入歌单",
        "description": "从粘贴文本导入",
        "source": "文本粘贴",
        "track_count": len(tracks),
        "tracks": [t.model_dump() for t in tracks],
    }
    return {
        "success": True,
        **pl_data,
        "playlist": pl_data,
    }


class UploadFileRequest(BaseModel):
    filename: str
    content: str


@app.post("/api/upload-file")
async def upload_file(req: UploadFileRequest):
    import tempfile
    import os
    suffix = Path(req.filename or "playlist.txt").suffix.lower()
    if suffix not in (".txt", ".csv"):
        raise HTTPException(status_code=400, detail="仅支持上传 .txt 或 .csv 格式歌单文件")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="w", encoding="utf-8") as tmp:
        tmp.write(req.content)
        tmp_path = tmp.name
    try:
        from applemusic.extractors.local_file import LocalFileExtractor
        extractor = LocalFileExtractor()
        playlist = extractor.extract(tmp_path)
        playlist.name = Path(req.filename).stem
        pl_data = {
            "name": playlist.name,
            "description": f"从文件 {req.filename} 上传导入",
            "source": "本地文件",
            "track_count": playlist.track_count,
            "tracks": [t.model_dump() for t in playlist.tracks],
        }
        return {
            "success": True,
            **pl_data,
            "playlist": pl_data,
        }
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


class SearchTrackRequest(BaseModel):
    query: str
    storefront: Optional[str] = None
    limit: int = 8
    source_title: Optional[str] = None
    source_artists: Optional[List[str]] = None
    source_album: Optional[str] = None
    source_duration_ms: Optional[int] = None


@app.post("/api/search-track")
async def search_single_track(req: SearchTrackRequest):
    client, engine = get_shared_engine()
    sf = req.storefront or client.config.storefront or "cn"
    raw_results = await asyncio.to_thread(client.search_catalog, req.query, sf, req.limit)

    # If source track metadata is provided, compute real similarity scores
    if req.source_title:
        temp_src = Track(
            title=req.source_title,
            artists=req.source_artists or [],
            album=req.source_album,
            duration_ms=req.source_duration_ms,
        )
        scored_list = [TrackScorer.score(temp_src, r) for r in raw_results]
        scored_list.sort(key=lambda x: x.score, reverse=True)
        return {
            "success": True,
            "results": [
                {
                    "track": c.track.model_dump(),
                    "score": c.score,
                    "title_score": c.title_score,
                    "artist_score": c.artist_score,
                    "album_score": c.album_score,
                    "duration_score": c.duration_score,
                    "version_score": c.version_score,
                    "confidence": c.confidence,
                    "decision": c.decision,
                    "decision_reasons": c.decision_reasons,
                }
                for c in scored_list
            ],
        }

    return {
        "success": True,
        "results": [
            {
                "track": r.model_dump(),
                "score": 0.85,
                "confidence": "high",
                "decision": "user_confirmed",
                "decision_reasons": ["手动检索结果"],
            }
            for r in raw_results
        ],
    }


@app.post("/api/match")
async def match_tracks(req: MatchRequest):
    client, engine = get_shared_engine()
    sf = req.storefront or client.config.storefront or "cn"

    dummy_playlist = Playlist(name="temp", tracks=req.tracks)
    results: List[SongMatchResult] = await asyncio.to_thread(
        engine.match_playlist, dummy_playlist, sf, 3
    )

    return {
        "success": True,
        "results": [r.model_dump() for r in results],
    }


class RematchRequest(BaseModel):
    tracks: List[Track]
    storefront: Optional[str] = None
    relaxed_mode: bool = True
    fallback_storefronts: Optional[List[str]] = None


@app.post("/api/rematch")
async def rematch_tracks(req: RematchRequest):
    client, engine = get_shared_engine()
    sf = req.storefront or client.config.storefront or "cn"

    results: List[SongMatchResult] = await asyncio.to_thread(
        engine.rematch_playlist,
        req.tracks,
        sf,
        req.relaxed_mode,
        req.fallback_storefronts,
        4,
    )

    return {
        "success": True,
        "results": [r.model_dump() for r in results],
    }


@app.post("/api/sync")
async def sync_to_apple_music(req: SyncRequest):
    config = get_config()
    if not config.is_authorized():
        raise HTTPException(status_code=401, detail="尚未授权 Apple Music，请先配置 media-user-token")

    loop = asyncio.get_running_loop()

    def _do_sync():
        client = AppleMusicClient(config)
        playlist_id = client.create_playlist(name=req.playlist_name, description=req.description or "")
        if not playlist_id:
            raise RuntimeError("在 Apple Music 中创建歌单失败，请检查 Token 是否有效")

        final_track_ids = list(req.track_ids or [])
        bilibili_added_count = 0
        bilibili_pending_count = 0

        # Process structured tracks list if provided
        if req.tracks:
            for item in req.tracks:
                if item.is_bilibili or item.type == "bilibili_local":
                    # Look up user's personal cloud library
                    lib_id = client.find_library_song_id(item.title, item.artist or "")
                    if lib_id:
                        final_track_ids.append(lib_id)
                        bilibili_added_count += 1
                    else:
                        bilibili_pending_count += 1
                elif item.id and item.id not in final_track_ids:
                    final_track_ids.append(item.id)

        # Deduplicate while preserving sequence
        seen_tids = set()
        deduped_ids = []
        for tid in final_track_ids:
            if tid and tid not in seen_tids:
                seen_tids.add(tid)
                deduped_ids.append(tid)

        added_count, failed_ids = client.add_tracks_to_playlist(playlist_id, deduped_ids)

        return {
            "success": True,
            "playlist_id": playlist_id,
            "added_count": added_count,
            "bilibili_added_count": bilibili_added_count,
            "bilibili_pending_count": bilibili_pending_count,
            "failed_count": len(failed_ids),
            "failed_ids": failed_ids,
        }

    try:
        result = await loop.run_in_executor(thread_pool, _do_sync)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


from applemusic.extractors.bilibili_downloader import (
    search_bilibili,
    download_bilibili_audio,
    get_apple_music_auto_add_dir,
    get_backup_download_dir,
    sanitize_filename,
)


class BilibiliSearchRequest(BaseModel):
    title: str
    artist: Optional[str] = ""
    limit: Optional[int] = 6


class BilibiliDownloadRequest(BaseModel):
    bvid: str
    title: str
    artist: str
    album: Optional[str] = ""
    cover_url: Optional[str] = ""
    auto_import: Optional[bool] = True


class BilibiliBatchDownloadRequest(BaseModel):
    tracks: List[Track]
    auto_import: Optional[bool] = True


@app.post("/api/bilibili/search")
async def api_bilibili_search(req: BilibiliSearchRequest):
    candidates = await asyncio.to_thread(
        search_bilibili, req.title, req.artist or "", req.limit or 6
    )
    return {"success": True, "candidates": candidates}


@app.post("/api/bilibili/download")
async def api_bilibili_download(req: BilibiliDownloadRequest):
    result = await asyncio.to_thread(
        download_bilibili_audio,
        bvid=req.bvid,
        target_title=req.title,
        target_artist=req.artist,
        target_album=req.album or "",
        cover_url=req.cover_url or "",
        auto_import_to_apple_music=req.auto_import if req.auto_import is not None else True,
    )
    return result


@app.post("/api/bilibili/batch-download")
async def api_bilibili_batch_download(req: BilibiliBatchDownloadRequest):
    def _do_batch():
        results = []
        for t in req.tracks:
            cands = search_bilibili(t.title, t.artist_str, limit=3)
            if not cands:
                results.append({
                    "success": False,
                    "title": t.title,
                    "artist": t.artist_str,
                    "error": "B 站未检索到相关视频",
                })
                continue
            best_bvid = cands[0]["bvid"]
            res = download_bilibili_audio(
                bvid=best_bvid,
                target_title=t.title,
                target_artist=t.artist_str,
                target_album=t.album or "Bilibili 视频提取",
                cover_url="",
                auto_import_to_apple_music=req.auto_import if req.auto_import is not None else True,
            )
            results.append(res)
        return results

    results = await asyncio.to_thread(_do_batch)
    return {"success": True, "results": results}


class BilibiliSingleAutoRequest(BaseModel):
    title: str
    artist: Optional[str] = ""
    album: Optional[str] = ""
    auto_import: Optional[bool] = True


@app.post("/api/bilibili/download-single-auto")
async def api_bilibili_download_single_auto(req: BilibiliSingleAutoRequest):
    def _do():
        cands = search_bilibili(req.title, req.artist or "", limit=3)
        if not cands:
            return {
                "success": False,
                "title": req.title,
                "artist": req.artist,
                "error": "B 站未检索到相关视频",
            }
        best_bvid = cands[0]["bvid"]
        cover_url = cands[0].get("pic", "")
        res = download_bilibili_audio(
            bvid=best_bvid,
            target_title=req.title,
            target_artist=req.artist or "",
            target_album=req.album or "Bilibili 视频提取",
            cover_url=cover_url,
            auto_import_to_apple_music=req.auto_import if req.auto_import is not None else True,
        )
        return res

    result = await asyncio.to_thread(_do)
    return result


@app.post("/api/bilibili/open-folder")
async def api_bilibili_open_folder():
    import subprocess
    auto_dir = get_apple_music_auto_add_dir()
    target_dir = auto_dir if (auto_dir and os.path.isdir(auto_dir)) else get_backup_download_dir()
    os.makedirs(target_dir, exist_ok=True)
    norm_path = os.path.normpath(target_dir)
    try:
        subprocess.Popen(f'explorer.exe "{norm_path}"', shell=True)
    except Exception:
        pass
    try:
        os.startfile(norm_path)
    except Exception:
        pass
    return {"success": True, "path": norm_path}

