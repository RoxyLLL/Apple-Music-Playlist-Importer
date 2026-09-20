"""
FastAPI Web Application backend for Apple Music Playlist Importer.
"""

import asyncio
import os
import sys
import threading
import time
import hmac
import secrets
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from applemusic.auth import AppleMusicAuth
from applemusic.auto_token import BrowserTokenCapturer
from applemusic.client import AppleMusicClient
from applemusic.config import Config, get_config
from applemusic.extractors import get_extractor_for
from applemusic.matcher.engine import MatchingEngine
from applemusic.matcher.scorer import TrackScorer
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

SESSION_API_TOKEN = secrets.token_urlsafe(32)

app = FastAPI(title="Apple Music Playlist Importer", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def verify_local_app_token(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        token = request.headers.get("X-App-Token") or request.cookies.get("app_token")
        if not token or not hmac.compare_digest(token, SESSION_API_TOKEN):
            return JSONResponse(
                status_code=403,
                content={"success": False, "detail": "Forbidden: Invalid application token"}
            )
    return await call_next(request)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/favicon.ico")
async def get_favicon():
    fav = STATIC_DIR / "favicon.ico"
    if fav.exists():
        return FileResponse(fav)
    return HTMLResponse("", status_code=404)



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
    content = index_file.read_text(encoding="utf-8")
    token_tag = f'<script>window.__APP_TOKEN__ = "{SESSION_API_TOKEN}";</script>'
    if "<head>" in content:
        content = content.replace("<head>", f"<head>\n  {token_tag}", 1)
    else:
        content = f"{token_tag}\n{content}"
    response = HTMLResponse(content)
    response.set_cookie(
        key="app_token",
        value=SESSION_API_TOKEN,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response


# In-memory auth validation cache (30s TTL to prevent event-loop choking)
_auth_validation_cache: Dict[str, Tuple[float, bool, str]] = {}

@app.get("/api/config")
async def get_config_status():
    config = get_config()
    auth = AppleMusicAuth(config)
    is_valid, sf_info = False, "未配置"
    if config.media_user_token:
        cache_key = config.media_user_token[:24]
        now = time.time()
        cached = _auth_validation_cache.get(cache_key)
        if cached and (now - cached[0] < 30.0):
            is_valid, sf_info = cached[1], cached[2]
        else:
            is_valid, sf_info = await asyncio.to_thread(auth.validate_user_token)
            _auth_validation_cache[cache_key] = (now, is_valid, sf_info)

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
    is_valid, info = await asyncio.to_thread(auth.validate_user_token)
    if config.media_user_token:
        _auth_validation_cache[config.media_user_token[:24]] = (time.time(), is_valid, info)
    return {
        "success": True,
        "is_authorized": is_valid,
        "message": info,
    }


_active_capturer: Optional[BrowserTokenCapturer] = None
_capturer_lock = threading.Lock()


@app.get("/api/auto-login/status")
async def get_auto_login_status():
    """Query live status of Edge browser token capture session."""
    global _active_capturer
    with _capturer_lock:
        if _active_capturer is None:
            config = get_config()
            return {
                "active": False,
                "status": "idle",
                "message": "",
                "is_authorized": bool(config.media_user_token),
                "storefront": config.storefront,
            }
        state = _active_capturer.get_state()
        if not state.get("active") and not state.get("is_authorized"):
            config = get_config()
            if config.media_user_token:
                state["is_authorized"] = True
                state["storefront"] = config.storefront
        return state


@app.post("/api/auto-login/cancel")
async def cancel_auto_login():
    """Cancel and terminate running browser token capture session."""
    global _active_capturer
    with _capturer_lock:
        if _active_capturer is not None:
            _active_capturer.cancel()
            _active_capturer = None
    return {"success": True, "message": "已取消自动登录"}


@app.post("/api/auto-login")
async def trigger_auto_login():
    """Launch Edge browser in background to automatically capture media-user-token."""
    global _active_capturer
    with _capturer_lock:
        if _active_capturer is not None and _active_capturer.is_active():
            state = _active_capturer.get_state()
            return {
                "success": True,
                "active": True,
                "message": state.get("message", "正在监听浏览器登录..."),
            }
        capturer = BrowserTokenCapturer()
        _active_capturer = capturer

    def _run_capturer():
        try:
            capturer.capture(timeout_seconds=240)
        except Exception:
            pass

    thread_pool.submit(_run_capturer)
    return {
        "success": True,
        "active": True,
        "message": "已成功唤起 Edge 浏览器，请在弹出的 Apple Music 网页中登录您的 Apple ID...",
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


class PreflightRequest(BaseModel):
    storefront: Optional[str] = None


@app.post("/api/preflight")
async def api_preflight_check(req: Optional[PreflightRequest] = None):
    client, _ = get_shared_engine()
    sf = req.storefront if req and req.storefront else client.config.storefront or "cn"
    result = await asyncio.to_thread(client.preflight_check, sf)
    return result


@app.get("/api/diagnostics")
async def api_get_diagnostics(storefront: Optional[str] = None):
    client, _ = get_shared_engine()
    sf = storefront or client.config.storefront or "cn"
    summary = client.get_diagnostics(sf)
    return {"success": True, "diagnostics": summary.model_dump()}


@app.post("/api/diagnostics/reset")
async def api_reset_diagnostics():
    client, _ = get_shared_engine()
    client.reset_diagnostics()
    return {"success": True, "message": "诊断指标已重置"}


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
    outcome = await asyncio.to_thread(client.search_catalog, req.query, sf, req.limit)

    if outcome.kind in ("rate_limited", "auth_failed", "upstream_error", "network_error", "timeout"):
        return {
            "success": False,
            "kind": outcome.kind,
            "error": outcome.safe_message or f"检索失败 ({outcome.kind})",
            "retry_after_seconds": outcome.retry_after_seconds,
            "results": [],
        }

    raw_results = outcome.tracks

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
                "score": 0.0,
                "confidence": "unknown",
                "decision": "review",
                "decision_reasons": ["手动检索结果（待用户选择）"],
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
        engine.match_playlist, dummy_playlist, sf, 1
    )
    diagnostics = client.get_diagnostics(sf)
    cache_stats = client.persistent_cache.get_stats()

    return {
        "success": True,
        "results": [r.model_dump() for r in results],
        "diagnostics": diagnostics.model_dump(),
        "cache_stats": cache_stats,
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
        1,
    )
    diagnostics = client.get_diagnostics(sf)
    cache_stats = client.persistent_cache.get_stats()

    return {
        "success": True,
        "results": [r.model_dump() for r in results],
        "diagnostics": diagnostics.model_dump(),
        "cache_stats": cache_stats,
    }


@app.get("/api/cache/stats")
async def get_cache_stats():
    client, _ = get_shared_engine()
    return {
        "success": True,
        "stats": client.persistent_cache.get_stats(),
    }


@app.post("/api/cache/clear")
async def clear_cache():
    client, _ = get_shared_engine()
    cat_del, mat_del = client.persistent_cache.clear_expired()
    return {
        "success": True,
        "cleared_catalog": cat_del,
        "cleared_match": mat_del,
        "stats": client.persistent_cache.get_stats(),
    }


@app.post("/api/sync")
async def sync_to_apple_music(req: SyncRequest):
    config = get_config()
    if not config.is_authorized():
        raise HTTPException(status_code=401, detail="尚未授权 Apple Music，请先配置 media-user-token")

    loop = asyncio.get_running_loop()

    def _do_sync():
        client = AppleMusicClient(config)
        final_track_ids = list(req.track_ids or [])
        bilibili_added_count = 0
        bilibili_pending_count = 0
        still_pending_bilibili = []

        # Process structured tracks list if provided
        if req.tracks:
            bilibili_items = [
                item for item in req.tracks
                if item.is_bilibili or item.type == "bilibili_local"
            ]
            catalog_items = [
                item for item in req.tracks
                if not (item.is_bilibili or item.type == "bilibili_local") and item.id
            ]

            for item in catalog_items:
                if item.id not in final_track_ids:
                    final_track_ids.append(item.id)

            # Look up Bilibili items in user's personal cloud library with retry for freshly imported files
            still_pending_bilibili = list(bilibili_items)
            for attempt in range(3):
                current_to_check = still_pending_bilibili
                still_pending_bilibili = []
                for item in current_to_check:
                    if item.id and str(item.id).startswith("i."):
                        final_track_ids.append(str(item.id))
                        bilibili_added_count += 1
                        continue
                    lib_id = client.find_library_song_id(item.title, item.artist or "")
                    if lib_id:
                        final_track_ids.append(lib_id)
                        bilibili_added_count += 1
                    else:
                        still_pending_bilibili.append(item)

                if not still_pending_bilibili:
                    break
                if attempt < 2 and still_pending_bilibili:
                    time.sleep(2.0)  # Wait for Apple Music Windows to ingest and sync to iCloud

            bilibili_pending_count = len(still_pending_bilibili)

        # Deduplicate while preserving sequence
        seen_tids = set()
        deduped_ids = []
        for tid in final_track_ids:
            if tid and tid not in seen_tids:
                seen_tids.add(tid)
                deduped_ids.append(tid)

        pending_tracks_info = [
            {"title": t.title, "artist": t.artist or ""}
            for t in still_pending_bilibili
        ]

        if not deduped_ids:
            msg = (
                f"已下载的 {bilibili_pending_count} 首 B 站本地歌曲已进入 Apple Music 自动导入目录，但 Apple Music 尚未完成云端资料库同步（通常需要 10~30 秒）。请确保 Apple Music 客户端处于打开状态，稍候片刻再次点击「同步到 Apple Music」即可自动建单。"
                if bilibili_pending_count
                else "待同步歌曲列表为空，未创建空歌单。"
            )
            return {
                "success": False,
                "playlist_id": None,
                "added_count": 0,
                "bilibili_added_count": 0,
                "bilibili_pending_count": bilibili_pending_count,
                "pending_tracks": pending_tracks_info,
                "failed_count": 0,
                "failed_ids": [],
                "message": msg,
            }

        playlist_id = client.create_playlist(name=req.playlist_name, description=req.description or "")
        if not playlist_id:
            raise RuntimeError("在 Apple Music 中创建歌单失败，请检查 Token 是否有效")

        added_count, failed_ids = client.add_tracks_to_playlist(playlist_id, deduped_ids)

        return {
            "success": True,
            "playlist_id": playlist_id,
            "added_count": added_count,
            "bilibili_added_count": bilibili_added_count,
            "bilibili_pending_count": bilibili_pending_count,
            "pending_tracks": pending_tracks_info,
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
    find_existing_bilibili_audio,
    ensure_auto_imported,
)
from applemusic.extractors.local_manager import get_apple_music_library_media_dir


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
    client, _ = get_shared_engine()
    result = await asyncio.to_thread(
        download_bilibili_audio,
        bvid=req.bvid,
        target_title=req.title,
        target_artist=req.artist,
        target_album=req.album or "",
        cover_url=req.cover_url or "",
        auto_import_to_apple_music=req.auto_import if req.auto_import is not None else True,
    )
    if result.get("success") and client.config.is_authorized():
        lib_id = client.find_library_song_id(req.title, req.artist or "")
        if lib_id:
            result["lib_id"] = lib_id
            result["already_in_library"] = True
    return result


@app.post("/api/bilibili/batch-download")
async def api_bilibili_batch_download(req: BilibiliBatchDownloadRequest):
    def _do_batch():
        try:
            client, _ = get_shared_engine()
            results = []
            for t in req.tracks:
                try:
                    # 1. Personal iCloud Music Library fast-path
                    if client.config.is_authorized():
                        lib_id = client.find_library_song_id(t.title, t.artist_str or "")
                        if lib_id:
                            results.append({
                                "success": True,
                                "title": t.title,
                                "artist": t.artist_str,
                                "already_in_library": True,
                                "lib_id": lib_id,
                                "message": "已在 Apple Music 个人资料库中找到本地音源",
                            })
                            continue

                    # 2. Local audio files fast-path
                    local_file = find_existing_bilibili_audio(t.title, t.artist_str or "")
                    if local_file:
                        am_media_dir = get_apple_music_library_media_dir()
                        is_in_am = False
                        if am_media_dir and os.path.exists(local_file):
                            try:
                                is_in_am = os.path.commonpath([os.path.abspath(local_file), os.path.abspath(am_media_dir)]) == os.path.abspath(am_media_dir)
                            except Exception:
                                pass

                        if not is_in_am and req.auto_import is not False:
                            ensure_auto_imported(local_file, t.title, t.artist_str or "")

                        msg = "Apple Music 资料库中已存在该本地音源" if is_in_am else "本地已存在该歌曲音频，已放入 Apple Music 自动导入目录"
                        results.append({
                            "success": True,
                            "title": t.title,
                            "artist": t.artist_str,
                            "already_downloaded": True,
                            "local_path": local_file,
                            "message": msg,
                        })
                        continue

                    # 3. Search Bilibili
                    cands = search_bilibili(t.title, t.artist_str, limit=3)
                    if not cands:
                        results.append({
                            "success": False,
                            "title": t.title,
                            "artist": t.artist_str,
                            "error": "B 站未检索到相关视频",
                        })
                        continue
                    top = cands[0]
                    # BUG-09: Tier gate: only auto-download Tier 1 or clean Tier 2. Tier 3/4 require user manual confirmation.
                    if top.get("tier", 4) > 2:
                        results.append({
                            "success": False,
                            "title": t.title,
                            "artist": t.artist_str,
                            "minimum_quality_met": False,
                            "tier": top.get("tier"),
                            "match_reason": top.get("match_reason"),
                            "error": "最高候选为翻唱或杂音视频（未达自动下载标准），请点击候选弹窗手动选择",
                            "requires_manual_confirmation": True,
                            "candidates": cands,
                        })
                        continue

                    best_bvid = top["bvid"]
                    res = download_bilibili_audio(
                        bvid=best_bvid,
                        target_title=t.title,
                        target_artist=t.artist_str,
                        target_album=t.album or "Bilibili 视频提取",
                        cover_url="",
                        auto_import_to_apple_music=req.auto_import if req.auto_import is not None else True,
                    )
                    results.append(res)
                except Exception as e:
                    logger.exception("Batch single track error (%s): %s", t.title, e)
                    results.append({
                        "success": False,
                        "title": t.title,
                        "artist": t.artist_str,
                        "error": f"处理异常: {str(e)}",
                    })
            return results
        except Exception as e:
            logger.exception("Bilibili batch download error: %s", e)
            return [{"success": False, "title": "批量处理异常", "error": str(e)}]

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
        try:
            client, _ = get_shared_engine()

            # 1. Personal iCloud Music Library fast-path
            if client.config.is_authorized():
                lib_id = client.find_library_song_id(req.title, req.artist or "")
                if lib_id:
                    return {
                        "success": True,
                        "title": req.title,
                        "artist": req.artist,
                        "already_in_library": True,
                        "lib_id": lib_id,
                        "message": "已在 Apple Music 个人资料库中找到本地音源",
                    }

            # 2. Local audio files fast-path
            local_file = find_existing_bilibili_audio(req.title, req.artist or "")
            if local_file:
                am_media_dir = get_apple_music_library_media_dir()
                is_in_am = False
                if am_media_dir and os.path.exists(local_file):
                    try:
                        is_in_am = os.path.commonpath([os.path.abspath(local_file), os.path.abspath(am_media_dir)]) == os.path.abspath(am_media_dir)
                    except Exception:
                        pass

                if not is_in_am and req.auto_import is not False:
                    ensure_auto_imported(local_file, req.title, req.artist or "")

                msg = "Apple Music 资料库中已存在该本地音源" if is_in_am else "本地已存在该歌曲音频，已放入 Apple Music 自动导入目录"
                return {
                    "success": True,
                    "title": req.title,
                    "artist": req.artist,
                    "already_downloaded": True,
                    "local_path": local_file,
                    "message": msg,
                }

            # 3. Search Bilibili
            cands = search_bilibili(req.title, req.artist or "", limit=3)
            if not cands:
                return {
                    "success": False,
                    "title": req.title,
                    "artist": req.artist,
                    "error": "B 站未检索到相关视频",
                }
            top = cands[0]
            # BUG-09: Tier gate: only auto-download Tier 1 or clean Tier 2. Tier 3/4 require user manual confirmation.
            if top.get("tier", 4) > 2:
                return {
                    "success": False,
                    "title": req.title,
                    "artist": req.artist,
                    "minimum_quality_met": False,
                    "tier": top.get("tier"),
                    "match_reason": top.get("match_reason"),
                    "error": "最高候选为翻唱或非官方杂音视频，未达自动导入标准，请手动打开候选窗口确认",
                    "requires_manual_confirmation": True,
                    "candidates": cands,
                }

            best_bvid = top["bvid"]
            cover_url = top.get("pic", "")
            res = download_bilibili_audio(
                bvid=best_bvid,
                target_title=req.title,
                target_artist=req.artist or "",
                target_album=req.album or "Bilibili 视频提取",
                cover_url=cover_url,
                auto_import_to_apple_music=req.auto_import if req.auto_import is not None else True,
            )
            return res
        except Exception as e:
            logger.exception("Bilibili download single auto error: %s", e)
            return {
                "success": False,
                "title": req.title,
                "artist": req.artist,
                "error": f"补全异常: {str(e)}",
            }

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


# -----------------------------------------------------------------------------
# Apple Music User Library & Playlists Batch Management
# -----------------------------------------------------------------------------
class UpdatePlaylistRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class BatchDeletePlaylistsRequest(BaseModel):
    playlist_ids: List[str]


class BatchDeleteSongsRequest(BaseModel):
    song_ids: List[str]


@app.get("/api/user/playlists")
async def api_get_user_playlists(limit: int = 100, offset: int = 0):
    client, _ = get_shared_engine()
    if not client.config.is_authorized():
        raise HTTPException(status_code=401, detail="尚未授权 Apple ID")
    playlists = await asyncio.to_thread(client.get_user_playlists, limit, offset)
    return {"success": True, "playlists": playlists}


class PlaylistTracksActionRequest(BaseModel):
    track_ids: List[str]


@app.get("/api/user/playlists/{playlist_id}/tracks")
async def api_get_playlist_tracks(playlist_id: str, limit: int = 100, fetch_all: bool = True):
    client, _ = get_shared_engine()
    if not client.config.is_authorized():
        raise HTTPException(status_code=401, detail="尚未授权 Apple ID")
    tracks = await asyncio.to_thread(client.get_playlist_tracks, playlist_id, limit, fetch_all)
    return {"success": True, "tracks": tracks, "total": len(tracks)}


@app.delete("/api/user/playlists/{playlist_id}/tracks")
async def api_delete_playlist_tracks(playlist_id: str, req: PlaylistTracksActionRequest):
    client, _ = get_shared_engine()
    if not client.config.is_authorized():
        raise HTTPException(status_code=401, detail="尚未授权 Apple ID")
    deleted_cnt, failed = await asyncio.to_thread(
        client.delete_playlist_tracks, playlist_id, req.track_ids
    )
    return {"success": True, "deleted_count": deleted_cnt, "failed_ids": failed}


@app.post("/api/user/playlists/{playlist_id}/tracks")
async def api_add_playlist_tracks(playlist_id: str, req: PlaylistTracksActionRequest):
    client, _ = get_shared_engine()
    if not client.config.is_authorized():
        raise HTTPException(status_code=401, detail="尚未授权 Apple ID")
    added_cnt, failed = await asyncio.to_thread(
        client.add_playlist_tracks, playlist_id, req.track_ids
    )
    return {"success": True, "added_count": added_cnt, "failed_ids": failed}


class AddLocalTracksToPlaylistRequest(BaseModel):
    tracks: List[Dict[str, Any]]  # [{"title": str, "artist": str, "file_path": str}]


@app.post("/api/user/playlists/{playlist_id}/add-local-tracks")
async def api_add_local_tracks_to_playlist(playlist_id: str, req: AddLocalTracksToPlaylistRequest):
    client, _ = get_shared_engine()
    if not client.config.is_authorized():
        raise HTTPException(status_code=401, detail="尚未授权 Apple ID")

    added_count = 0
    failed_titles = []
    track_ids_to_add = []

    for t in req.tracks:
        title = t.get("title") or ""
        artist = t.get("artist") or ""
        lib_id = await asyncio.to_thread(client.find_library_song_id, title, artist)
        if not lib_id and title:
            try:
                cat_res = await asyncio.to_thread(
                    client.search_catalog, f"{title} {artist}".strip(), limit=1
                )
                if cat_res and cat_res.tracks:
                    lib_id = cat_res.tracks[0].id
            except Exception:
                pass
        if lib_id:
            track_ids_to_add.append(lib_id)
        else:
            failed_titles.append(title or "未知歌曲")

    if track_ids_to_add:
        success_cnt, failed = await asyncio.to_thread(
            client.add_playlist_tracks, playlist_id, track_ids_to_add
        )
        added_count = success_cnt

    return {"success": True, "added_count": added_count, "failed_titles": failed_titles}


@app.patch("/api/user/playlists/{playlist_id}")
async def api_update_playlist(playlist_id: str, req: UpdatePlaylistRequest):
    client, _ = get_shared_engine()
    if not client.config.is_authorized():
        raise HTTPException(status_code=401, detail="尚未授权 Apple ID")
    ok = await asyncio.to_thread(client.update_playlist, playlist_id, req.name, req.description)
    if not ok:
        raise HTTPException(status_code=500, detail="修改歌单失败")
    return {"success": True}


@app.delete("/api/user/playlists/{playlist_id}")
async def api_delete_playlist(playlist_id: str):
    client, _ = get_shared_engine()
    if not client.config.is_authorized():
        raise HTTPException(status_code=401, detail="尚未授权 Apple ID")
    ok = await asyncio.to_thread(client.delete_playlist, playlist_id)
    if not ok:
        raise HTTPException(status_code=500, detail="删除歌单失败")
    return {"success": True}


@app.post("/api/user/playlists/batch-delete")
async def api_batch_delete_playlists(req: BatchDeletePlaylistsRequest):
    client, _ = get_shared_engine()
    if not client.config.is_authorized():
        raise HTTPException(status_code=401, detail="尚未授权 Apple ID")
    success_cnt, failed = await asyncio.to_thread(client.batch_delete_playlists, req.playlist_ids)
    return {"success": True, "deleted_count": success_cnt, "failed_ids": failed}


@app.get("/api/user/library/songs")
async def api_get_library_songs(
    limit: int = 100,
    offset: int = 0,
    fetch_all: bool = False,
    sort: Optional[str] = "-dateAdded",
):
    client, _ = get_shared_engine()
    if not client.config.is_authorized():
        raise HTTPException(status_code=401, detail="尚未授权 Apple ID")
    songs = await asyncio.to_thread(client.get_library_songs, limit, offset, fetch_all, 5000, sort)
    return {"success": True, "songs": songs, "total": len(songs)}


@app.get("/api/user/library/search")
async def api_search_library_songs(term: str, limit: int = 100):
    client, _ = get_shared_engine()
    if not client.config.is_authorized():
        raise HTTPException(status_code=401, detail="尚未授权 Apple ID")
    songs = await asyncio.to_thread(client.search_library_songs, term, limit)
    return {"success": True, "songs": songs}


@app.delete("/api/user/library/songs/{song_id}")
async def api_delete_library_song(song_id: str):
    client, _ = get_shared_engine()
    if not client.config.is_authorized():
        raise HTTPException(status_code=401, detail="尚未授权 Apple ID")
    ok = await asyncio.to_thread(client.delete_library_song, song_id)
    if not ok:
        raise HTTPException(status_code=500, detail="从资料库删除歌曲失败")
    return {"success": True}


@app.post("/api/user/library/songs/batch-delete")
async def api_batch_delete_library_songs(req: BatchDeleteSongsRequest):
    client, _ = get_shared_engine()
    if not client.config.is_authorized():
        raise HTTPException(status_code=401, detail="尚未授权 Apple ID")
    success_cnt, failed = await asyncio.to_thread(client.batch_delete_library_songs, req.song_ids)
    return {"success": True, "deleted_count": success_cnt, "failed_ids": failed}


# -----------------------------------------------------------------------------
# Local Apple Music Library Batch Management
# -----------------------------------------------------------------------------
from applemusic.extractors.local_manager import (
    list_local_tracks,
    update_local_track_metadata,
    batch_delete_local_tracks,
    import_local_tracks_to_applemusic,
    get_apple_music_library_media_dir,
)


class UpdateLocalMetadataRequest(BaseModel):
    file_path: str
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    rename_file: Optional[bool] = False


class BatchLocalTracksRequest(BaseModel):
    file_paths: List[str]


@app.get("/api/local/songs")
async def api_list_local_songs(directory: Optional[str] = None):
    try:
        songs = await asyncio.to_thread(list_local_tracks, directory)
        active_dir = directory or get_apple_music_library_media_dir() or ""
        return {"success": True, "songs": songs, "directory": active_dir}
    except Exception as e:
        logger.exception("List local songs error: %s", e)
        return {"success": False, "detail": str(e), "songs": [], "directory": ""}


@app.post("/api/local/open-folder")
async def api_open_local_music_folder(directory: Optional[str] = None):
    import subprocess
    target_dir = directory or get_apple_music_library_media_dir()
    if not target_dir or not os.path.isdir(target_dir):
        userprofile = os.environ.get("USERPROFILE") or str(Path.home())
        target_dir = os.path.join(userprofile, "Music", "Apple Music")
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


@app.post("/api/local/songs/update-metadata")
async def api_update_local_metadata(req: UpdateLocalMetadataRequest):
    res = await asyncio.to_thread(
        update_local_track_metadata,
        req.file_path,
        req.title,
        req.artist,
        req.album,
        req.rename_file or False,
    )
    if not res.get("success"):
        raise HTTPException(status_code=500, detail=res.get("error", "修改元数据失败"))
    return res


@app.post("/api/local/songs/batch-delete")
async def api_batch_delete_local_songs(req: BatchLocalTracksRequest):
    success_cnt, failed = await asyncio.to_thread(batch_delete_local_tracks, req.file_paths)
    return {"success": True, "deleted_count": success_cnt, "failed_paths": failed}


@app.post("/api/local/songs/import-to-applemusic")
async def api_import_local_to_applemusic(req: BatchLocalTracksRequest):
    success_cnt, failed = await asyncio.to_thread(import_local_tracks_to_applemusic, req.file_paths)
    return {"success": True, "imported_count": success_cnt, "failed_paths": failed}

