"""
Apple Music API client.
Interacts with Catalog Search and User Library endpoints.
"""

import threading
import time
from typing import Dict, List, Optional, Tuple
import requests

from applemusic.auth import AppleMusicAuth
from applemusic.config import Config, get_config
from applemusic.models import AppleMusicTrack


class AppleMusicClient:
    """Client for interacting with Apple Music API."""

    API_URL = "https://api.music.apple.com/v1"

    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()
        self.auth = AppleMusicAuth(self.config)
        self.session = requests.Session()
        # Thread-safe global rate limit coordinator across all concurrent search threads
        self._rate_limit_lock = threading.Lock()
        self._rate_limit_until = 0.0
        # Singleflight in-flight query deduplication table
        self._in_flight_lock = threading.Lock()
        self._in_flight_events: Dict[Tuple, threading.Event] = {}
        # Enlarge connection pool to match worker count and prevent reconnection overhead
        adapter = requests.adapters.HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=1)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._catalog_cache = {}
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Origin": "https://music.apple.com",
            "Referer": "https://music.apple.com/",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def _get_auth_headers(self, require_user: bool = False) -> Dict[str, str]:
        """Generate headers with Developer Token and optionally Music-User-Token."""
        dev_token = self.auth.get_developer_token()
        headers = {
            "Authorization": f"Bearer {dev_token}",
        }
        # If user is authorized, attach Music-User-Token to all requests.
        # This identifies requests as coming from an authenticated subscriber rather than
        # an anonymous scraper, preventing harsh 429 rate limit blocks from Apple API.
        if self.config.media_user_token:
            headers["Music-User-Token"] = self.config.media_user_token
        elif require_user:
            raise ValueError("未配置 media-user-token，无法执行资料库写入操作")
        return headers

    def search_by_isrc(
        self,
        isrc: str,
        storefront: Optional[str] = None,
        retries: int = 2,
    ) -> List[AppleMusicTrack]:
        """
        Search songs in Apple Music Catalog by exact ISRC using official filter[isrc] parameter.
        Returns list of matched AppleMusicTrack.
        """
        clean_isrc = isrc.strip().upper()
        if not clean_isrc:
            return []

        sf = storefront or self.config.storefront
        cache_key = ("isrc", sf, clean_isrc)

        # Check in-memory cache
        if hasattr(self, "_catalog_cache") and cache_key in self._catalog_cache:
            cache_time, cached_results = self._catalog_cache[cache_key]
            if time.time() - cache_time < 3600:  # 1 hour TTL for ISRC
                return cached_results

        url = f"{self.API_URL}/catalog/{sf}/songs"
        headers = self._get_auth_headers(require_user=False)
        params = {"filter[isrc]": clean_isrc}

        for attempt in range(retries):
            try:
                resp = self.session.get(url, headers=headers, params=params, timeout=(6.0, 10.0))
                if resp.status_code == 200:
                    data = resp.json()
                    songs_data = data.get("data", [])
                    results: List[AppleMusicTrack] = []
                    for item in songs_data:
                        attrs = item.get("attributes", {})
                        artist_name = attrs.get("artistName", "")
                        artists = [a.strip() for a in artist_name.split(",") if a.strip()] or [artist_name]
                        artwork = attrs.get("artwork", {})
                        artwork_url = artwork.get("url")
                        previews = attrs.get("previews") or []
                        preview_url = previews[0].get("url") if previews else None

                        results.append(AppleMusicTrack(
                            id=item.get("id"),
                            title=attrs.get("name", ""),
                            artists=artists,
                            album=attrs.get("albumName"),
                            duration_ms=attrs.get("durationInMillis"),
                            isrc=attrs.get("isrc") or clean_isrc,
                            artwork_url=artwork_url,
                            preview_url=preview_url,
                            storefront=sf,
                            url=attrs.get("url"),
                        ))

                    if not hasattr(self, "_catalog_cache"):
                        self._catalog_cache = {}
                    self._catalog_cache[cache_key] = (time.time(), results)
                    return results
                elif resp.status_code == 429:
                    time.sleep(1.0 + (0.5 * attempt))
                    continue
                else:
                    break
            except Exception:
                if attempt < retries - 1:
                    time.sleep(0.3)
                    continue

        return []

    def search_catalog(
        self,
        query: str,
        storefront: Optional[str] = None,
        limit: int = 10,
        retries: int = 3,
    ) -> List[AppleMusicTrack]:
        """
        Search songs in Apple Music Catalog for a specific storefront.
        Uses in-memory cache, singleflight request coalescing, and connection pooling.
        """
        import random
        sf = storefront or self.config.storefront
        clean_q = query.strip().lower()
        cache_key = (sf, clean_q, limit)

        # 1. Check in-memory cache
        if hasattr(self, "_catalog_cache") and cache_key in self._catalog_cache:
            cache_time, cached_results = self._catalog_cache[cache_key]
            if time.time() - cache_time < 900:  # 15 minutes TTL
                return cached_results

        # 2. Singleflight: If identical query is currently in-flight in another thread, wait for it
        wait_event = None
        is_initiator = False
        with self._in_flight_lock:
            if cache_key in self._in_flight_events:
                wait_event = self._in_flight_events[cache_key]
            else:
                event = threading.Event()
                self._in_flight_events[cache_key] = event
                is_initiator = True

        if wait_event:
            wait_event.wait(timeout=8.0)
            if hasattr(self, "_catalog_cache") and cache_key in self._catalog_cache:
                cache_time, cached_results = self._catalog_cache[cache_key]
                if time.time() - cache_time < 900:
                    return cached_results

        try:
            url = f"{self.API_URL}/catalog/{sf}/search"
            headers = self._get_auth_headers(require_user=False)
            params = {
                "term": query,
                "types": "songs",
                "limit": limit,
            }

            for attempt in range(retries):
                # Check global rate limit cooldown before sending request
                with self._rate_limit_lock:
                    cooldown = self._rate_limit_until - time.time()
                if cooldown > 0:
                    time.sleep(cooldown + random.uniform(0.05, 0.15))

                try:
                    resp = self.session.get(url, headers=headers, params=params, timeout=(6.0, 10.0))
                    if resp.status_code == 200:
                        data = resp.json()
                        songs_data = data.get("results", {}).get("songs", {}).get("data", [])
                        results: List[AppleMusicTrack] = []
                        for item in songs_data:
                            attrs = item.get("attributes", {})
                            # Artists list
                            artist_name = attrs.get("artistName", "")
                            artists = [a.strip() for a in artist_name.split(",") if a.strip()]
                            if not artists:
                                artists = [artist_name]

                            # Artwork & Preview
                            artwork = attrs.get("artwork", {})
                            artwork_url = artwork.get("url")
                            previews = attrs.get("previews") or []
                            preview_url = previews[0].get("url") if previews else None

                            track = AppleMusicTrack(
                                id=item.get("id"),
                                title=attrs.get("name", ""),
                                artists=artists,
                                album=attrs.get("albumName"),
                                duration_ms=attrs.get("durationInMillis"),
                                isrc=attrs.get("isrc"),
                                artwork_url=artwork_url,
                                preview_url=preview_url,
                                storefront=sf,
                                url=attrs.get("url"),
                            )
                            results.append(track)

                        # Store in memory cache
                        if not hasattr(self, "_catalog_cache"):
                            self._catalog_cache = {}
                        if len(self._catalog_cache) > 3000:
                            self._catalog_cache.clear()
                        self._catalog_cache[cache_key] = (time.time(), results)

                        return results

                    elif resp.status_code == 429:
                        # Coordinated global rate limit backoff:
                        pause = 0.8 + (0.3 * attempt) + random.uniform(0.1, 0.2)
                        with self._rate_limit_lock:
                            self._rate_limit_until = max(self._rate_limit_until, time.time() + pause)
                        time.sleep(pause)
                        continue
                    elif resp.status_code == 401:
                        dev_tok = self.auth.get_developer_token(force_refresh=True)
                        headers["Authorization"] = f"Bearer {dev_tok}"
                        time.sleep(0.3)
                        continue
                    else:
                        break
                except Exception:
                    if attempt < retries - 1:
                        time.sleep(0.3)
                        continue

            return []
        finally:
            if is_initiator:
                with self._in_flight_lock:
                    event = self._in_flight_events.pop(cache_key, None)
                    if event:
                        event.set()

    def create_playlist(
        self,
        name: str,
        description: str = "Imported by AppleMusicSync",
    ) -> Optional[str]:
        """
        Create a new playlist in user's Apple Music Library.
        Returns playlist ID (e.g. 'p.xxxxx') or None if failed.
        """
        url = f"{self.API_URL}/me/library/playlists"
        headers = self._get_auth_headers(require_user=True)
        payload = {
            "attributes": {
                "name": name,
                "description": description,
            }
        }

        try:
            resp = self.session.post(url, headers=headers, json=payload, timeout=12)
            if resp.status_code in (200, 201):
                data = resp.json()
                playlist_id = data.get("data", [{}])[0].get("id")
                return playlist_id
            else:
                print(f"[Error] 创建歌单失败: HTTP {resp.status_code} - {resp.text[:200]}")
                return None
        except Exception as e:
            print(f"[Error] 创建歌单网络异常: {e}")
            return None

    def add_tracks_to_playlist(
        self,
        playlist_id: str,
        track_ids: List[str],
        batch_size: int = 50,
    ) -> Tuple[int, List[str]]:
        """
        Add a list of catalog track IDs or personal library song IDs (starting with 'i.')
        into a library playlist.
        Returns (success_count, failed_track_ids).
        """
        if not track_ids:
            return 0, []

        url = f"{self.API_URL}/me/library/playlists/{playlist_id}/tracks"
        headers = self._get_auth_headers(require_user=True)

        success_count = 0
        failed_ids: List[str] = []

        # Apple Music API allows batching up to 100 tracks
        for i in range(0, len(track_ids), batch_size):
            batch = track_ids[i : i + batch_size]
            payload = {
                "data": [
                    {
                        "id": str(tid),
                        "type": "library-songs" if str(tid).startswith("i.") else "songs",
                    }
                    for tid in batch
                ]
            }
            try:
                resp = self.session.post(url, headers=headers, json=payload, timeout=15)
                if resp.status_code in (200, 201, 204):
                    success_count += len(batch)
                else:
                    print(f"[Warning] 批量添加歌曲失败 (批次 {i // batch_size + 1}): HTTP {resp.status_code} - {resp.text[:100]}")
                    failed_ids.extend(batch)
            except Exception as e:
                print(f"[Warning] 批量添加歌曲请求异常: {e}")
                failed_ids.extend(batch)

            # Polite delay between batches
            if i + batch_size < len(track_ids):
                time.sleep(0.5)

        return success_count, failed_ids

    def find_library_song_id(self, title: str, artist: str = "") -> Optional[str]:
        """
        Search user's personal iCloud Music Library for a track (e.g. locally ingested M4A).
        Returns library song ID (e.g. 'i.xxxx') if found.
        """
        import re
        clean_t = re.sub(r"[《》「」『』\"“”'‘’]", " ", title)
        prev = None
        while prev != clean_t:
            prev = clean_t
            clean_t = re.sub(r"[\(（【\[][^()（）【\]]*[\)）】\]]", " ", clean_t)
        clean_t = clean_t.strip()
        if not clean_t:
            clean_t = title.strip()

        url = f"{self.API_URL}/me/library/search"
        headers = self._get_auth_headers(require_user=True)

        queries = [clean_t]
        if artist:
            queries.append(f"{clean_t} {artist.split()[0]}")
        if title.strip() not in queries:
            queries.append(title.strip())

        for term in queries:
            try:
                resp = self.session.get(
                    url,
                    headers=headers,
                    params={"term": term, "types": "library-songs"},
                    timeout=8,
                )
                if resp.status_code == 200:
                    data = resp.json().get("results", {}).get("library-songs", {}).get("data", [])
                    if data:
                        for it in data:
                            it_name = it.get("attributes", {}).get("name", "").lower()
                            if clean_t.lower() in it_name or it_name in clean_t.lower():
                                return it.get("id")
                        return data[0].get("id")
            except Exception:
                pass
        return None

    def add_tracks_to_library(self, track_ids: List[str], batch_size: int = 50) -> int:
        """
        Add catalog tracks directly to user's iCloud Music Library.
        Returns count of successfully added tracks.
        """
        if not track_ids:
            return 0

        headers = self._get_auth_headers(require_user=True)
        added_count = 0

        for i in range(0, len(track_ids), batch_size):
            batch = track_ids[i : i + batch_size]
            url = f"{self.API_URL}/me/library"
            params = {"ids[songs]": ",".join(batch)}
            try:
                resp = self.session.post(url, headers=headers, params=params, timeout=15)
                if resp.status_code in (200, 202):
                    added_count += len(batch)
            except Exception:
                pass
            time.sleep(0.5)

        return added_count
