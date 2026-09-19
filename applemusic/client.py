import random
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple
import requests

from applemusic.auth import AppleMusicAuth
from applemusic.cache import PersistentCache
from applemusic.config import Config, get_config
from applemusic.matcher.cleaner import TextCleaner
from applemusic.models import AppleMusicTrack, BatchDiagnosticSummary, CatalogSearchOutcome


def parse_retry_after(header_val: Optional[str], default: Optional[float] = None) -> Optional[float]:
    """Parse HTTP Retry-After header into seconds."""
    if not header_val:
        return default
    header_val = str(header_val).strip()
    try:
        return max(0.5, float(header_val))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(header_val)
        delta = dt.timestamp() - time.time()
        return max(0.5, delta)
    except Exception:
        return default


def _extract_request_id(headers: Any) -> Optional[str]:
    """Extract Apple request ID case-insensitively from response headers."""
    if not headers:
        return None
    for k in ("x-apple-request-id", "X-Apple-Request-Id", "request-id", "X-Request-Id"):
        if k in headers:
            return headers[k]
    return None


class AdaptiveRateLimiter:
    """
    Thread-safe adaptive token bucket, concurrency limiter, and circuit breaker
    shared by all outbound Apple Music Catalog and ISRC queries.
    """

    def __init__(
        self,
        target_qps: float = 0.65,
        max_concurrency: int = 1,
        circuit_breaker_threshold: int = 3,
        max_requests_per_minute: Optional[int] = None,
    ):
        self._lock = threading.Lock()
        self.target_qps = max(0.1, target_qps)
        self.max_concurrency = max_concurrency
        self.circuit_breaker_threshold = circuit_breaker_threshold
        # Strictly cap at 36 requests in any rolling 60-second window (completely immune to Apple 429)
        self.max_requests_per_minute = max_requests_per_minute or 36
        self._tokens = float(max_concurrency)
        self._last_token_time = time.time()
        self._last_request_time = 0.0
        self._request_history: List[float] = []

        self._semaphore = threading.BoundedSemaphore(max_concurrency)
        self.global_pause_until = 0.0
        self.consecutive_429 = 0
        self._circuit_broken = False
        self.circuit_break_until = 0.0

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    @property
    def circuit_broken(self) -> bool:
        with self._lock:
            now = time.time()
            if self._circuit_broken and now >= self.circuit_break_until:
                self._circuit_broken = False
                self.consecutive_429 = 0
            return self._circuit_broken

    @circuit_broken.setter
    def circuit_broken(self, val: bool):
        with self._lock:
            self._circuit_broken = val
            if val and self.circuit_break_until <= time.time():
                self.circuit_break_until = time.time() + 15.0

    @property
    def circuit_breaker_tripped(self) -> bool:
        return self.circuit_broken

    @circuit_breaker_tripped.setter
    def circuit_breaker_tripped(self, val: bool):
        self.circuit_broken = val

    def acquire(self, timeout: float = 90.0) -> bool:
        """Acquire a concurrency slot and rate-limit token, strictly respecting 60-second quota, global pause and cooldown."""
        start = time.time()

        # 1. Wait out any 429 pause or circuit breaker cooldown WITHOUT holding semaphore
        while True:
            with self._lock:
                now = time.time()
                if self._circuit_broken and now >= self.circuit_break_until:
                    self._circuit_broken = False
                    self.consecutive_429 = 0
                cooldown = max(self.global_pause_until - now, self.circuit_break_until - now, 0.0)
                if cooldown <= 0.0:
                    break
            if (time.time() - start) + cooldown > timeout:
                return False
            time.sleep(min(cooldown, 0.4))

        # 2. Acquire concurrency semaphore slot
        remaining = timeout - (time.time() - start)
        if remaining <= 0 or not self._semaphore.acquire(timeout=remaining):
            return False

        # 3. Refill and consume token, strictly enforcing 60-second rolling quota and minimum interval
        try:
            min_interval = 1.0 / self.target_qps if self.target_qps > 0 else 1.05
            while True:
                with self._lock:
                    now = time.time()
                    # Clean up timestamps older than 60.0s
                    cutoff = now - 60.0
                    self._request_history = [t for t in self._request_history if t > cutoff]

                    # Check rolling 60s quota
                    if len(self._request_history) >= self.max_requests_per_minute:
                        oldest = self._request_history[0]
                        window_wait = max(0.1, 60.0 - (now - oldest) + 0.15)
                    else:
                        window_wait = 0.0

                    elapsed = now - self._last_token_time
                    self._tokens = min(float(self.max_concurrency), self._tokens + elapsed * self.target_qps)
                    self._last_token_time = now

                    time_since_last = now - self._last_request_time
                    if (
                        window_wait <= 0.0
                        and self._tokens >= 1.0
                        and (self._last_request_time == 0.0 or time_since_last >= min_interval)
                    ):
                        self._tokens -= 1.0
                        self._last_request_time = now
                        self._request_history.append(now)
                        return True
                    else:
                        token_wait = max(0.0, (1.0 - self._tokens) / self.target_qps)
                        interval_wait = max(0.0, min_interval - time_since_last)
                        sleep_time = max(0.05, max(token_wait, interval_wait, window_wait))

                if (time.time() - start) + sleep_time > timeout:
                    self._semaphore.release()
                    return False
                time.sleep(min(sleep_time, 0.3))
        except Exception:
            self._semaphore.release()
            raise

    def release(self):
        """Release concurrency slot."""
        try:
            self._semaphore.release()
        except ValueError:
            pass

    def record_429(self, retry_after: Optional[float] = None) -> float:
        """Called when a 429 response is received. Updates coordinated pause and backoff with jitter."""
        with self._lock:
            now = time.time()
            self.consecutive_429 += 1
            if retry_after is not None and retry_after > 0:
                jitter = random.uniform(0.3, 0.55)
                backoff = float(retry_after) + jitter
            else:
                # Apple Music does not return Retry-After header.
                # Empirical tests confirm Apple's IP lockout duration is 30~40 seconds.
                jitter = random.uniform(1.0, 4.0)
                backoff = 32.0 + jitter

            self.global_pause_until = max(self.global_pause_until, now + backoff)

            # If threshold consecutive 429s occur, trip circuit breaker for a reasonable cooldown
            if self.consecutive_429 >= self.circuit_breaker_threshold:
                self._circuit_broken = True
                self.circuit_break_until = now + backoff + 5.0

            return backoff

    on_429 = record_429

    def record_success(self):
        """Called when a 200 OK response is received."""
        with self._lock:
            self.consecutive_429 = 0
            self._circuit_broken = False

    on_success = record_success

    def reset(self):
        """Reset all rate limiter state, pauses, and circuit breaker."""
        with self._lock:
            self.global_pause_until = 0.0
            self.consecutive_429 = 0
            self._circuit_broken = False
            self.circuit_break_until = 0.0
            self._request_history.clear()
            self._last_request_time = 0.0

    def is_cooling_down(self) -> Tuple[bool, float]:
        """Check if currently cooling down from 429 or circuit breaker."""
        with self._lock:
            now = time.time()
            if self._circuit_broken and now >= self.circuit_break_until:
                self._circuit_broken = False
                self.consecutive_429 = 0
            cooldown = max(self.global_pause_until - now, self.circuit_break_until - now, 0.0)
            return (cooldown > 0.0, cooldown)

    def cooldown_remaining(self) -> float:
        """Return remaining cooldown seconds."""
        return self.is_cooling_down()[1]


class ClientDiagnostics:
    """Thread-safe request metrics accumulator."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.total_queries = 0
            self.ok_hits_count = 0
            self.no_hits_count = 0
            self.rate_limit_count = 0
            self.auth_failed_count = 0
            self.upstream_error_count = 0
            self.timeout_count = 0
            self.network_error_count = 0
            self.backoff_count = 0
            self.cache_hits = 0
            self.latencies: List[float] = []

    def record(self, kind: str, latency_ms: float = 0.0, backoff: bool = False):
        with self._lock:
            self.total_queries += 1
            if latency_ms > 0:
                self.latencies.append(latency_ms)
                if len(self.latencies) > 600:
                    self.latencies = self.latencies[-300:]
            if backoff:
                self.backoff_count += 1

            if kind == "ok":
                self.ok_hits_count += 1
            elif kind == "no_hits":
                self.no_hits_count += 1
            elif kind == "cache_hit":
                self.cache_hits += 1
            elif kind == "rate_limited":
                self.rate_limit_count += 1
            elif kind == "auth_failed":
                self.auth_failed_count += 1
            elif kind == "upstream_error":
                self.upstream_error_count += 1
            elif kind == "timeout":
                self.timeout_count += 1
            elif kind == "network_error":
                self.network_error_count += 1

    def get_summary(self, storefront: str = "cn") -> BatchDiagnosticSummary:
        with self._lock:
            avg_lat = sum(self.latencies) / len(self.latencies) if self.latencies else 0.0
            p95_lat = 0.0
            if self.latencies:
                sorted_lat = sorted(self.latencies)
                idx = int(len(sorted_lat) * 0.95)
                p95_lat = sorted_lat[min(idx, len(sorted_lat) - 1)]

            return BatchDiagnosticSummary(
                storefront=storefront,
                total_queries=self.total_queries,
                ok_with_hits=self.ok_hits_count,
                no_hits=self.no_hits_count,
                rate_limits=self.rate_limit_count,
                auth_failures=self.auth_failed_count,
                timeouts=self.timeout_count,
                network_errors=self.network_error_count,
                upstream_errors=self.upstream_error_count,
                cache_hits=self.cache_hits,
                avg_latency_ms=round(avg_lat, 2),
                p95_latency_ms=round(p95_lat, 2),
                backoff_count=self.backoff_count,
            )


class AppleMusicClient:
    """Client for interacting with Apple Music API with adaptive rate limiting and structured outcomes."""

    API_URL = "https://api.music.apple.com/v1"

    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()
        self.auth = AppleMusicAuth(self.config)
        self.session = requests.Session()

        # Shared adaptive rate limiter & circuit breaker (strictly <= 55 req/min, Apple limit is 60)
        self.limiter = AdaptiveRateLimiter(target_qps=0.95, max_concurrency=1, circuit_breaker_threshold=6, max_requests_per_minute=55)
        # Shared diagnostics accumulator
        self.diagnostics = ClientDiagnostics()

        # Persistent SQLite cache instance
        self.persistent_cache = PersistentCache.get_instance()

        # Singleflight in-flight query deduplication table
        self._in_flight_lock = threading.Lock()
        self._in_flight_events: Dict[Tuple, threading.Event] = {}
        self._in_flight_results: Dict[Tuple, CatalogSearchOutcome] = {}

        # Cache: stores only verified ok / no_hits results (never errors or 429)
        self._catalog_cache: Dict[Tuple, Tuple[float, CatalogSearchOutcome]] = {}

        # HTTP connection pooling
        adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=1)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Origin": "https://music.apple.com",
            "Referer": "https://music.apple.com/",
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "Content-Type": "application/json",
        })

    def _get_auth_headers(self, require_user: bool = False) -> Dict[str, str]:
        """Generate headers with Developer Token and optionally Music-User-Token."""
        dev_token = self.auth.get_developer_token()
        if not dev_token:
            raise RuntimeError("无法获取 Apple Music Developer Token，请检查网络连接或设置环境变量 APPLE_MUSIC_DEVELOPER_TOKEN")
        headers = {
            "Authorization": f"Bearer {dev_token}",
        }
        if require_user:
            if self.config.media_user_token:
                headers["Music-User-Token"] = self.config.media_user_token
            else:
                raise ValueError("未配置 media-user-token，无法执行资料库写入操作")
        return headers

    def search_by_isrc_batch(
        self,
        isrc_list: List[str],
        storefront: Optional[str] = None,
        retries: int = 2,
    ) -> Dict[str, CatalogSearchOutcome]:
        """
        Search songs in Apple Music Catalog by a batch of ISRCs using official filter[isrc] parameter.
        Supports chunking of up to 25 ISRCs per request (Apple Music API limit).
        Checks both persistent SQLite cache and in-memory cache first to avoid unnecessary requests.
        Caches positive matches (14 days) and negative misses (3 days) in persistent storage.
        Returns a mapping of uppercase ISRC -> CatalogSearchOutcome.
        """
        sf = storefront or self.config.storefront or "cn"
        clean_isrcs: List[str] = []
        seen = set()
        for raw in isrc_list:
            c = raw.strip().upper() if raw else ""
            if c and len(c) >= 8 and c not in seen:
                seen.add(c)
                clean_isrcs.append(c)

        if not clean_isrcs:
            return {}

        results: Dict[str, CatalogSearchOutcome] = {}
        to_fetch: List[str] = []

        # 0. Check caches for each ISRC
        for c in clean_isrcs:
            cached_p = self.persistent_cache.get_catalog(sf, "isrc", c)
            if cached_p:
                self.diagnostics.record("cache_hit")
                results[c] = cached_p
                continue

            cache_key = ("isrc", sf, c)
            if cache_key in self._catalog_cache:
                cache_time, cached_outcome = self._catalog_cache[cache_key]
                if time.time() - cache_time < 3600:
                    results[c] = cached_outcome
                    continue

            to_fetch.append(c)

        if not to_fetch:
            return results

        # Apple Music Catalog API supports up to 25 comma-separated ISRCs in filter[isrc]
        CHUNK_SIZE = 25
        chunks = [to_fetch[i : i + CHUNK_SIZE] for i in range(0, len(to_fetch), CHUNK_SIZE)]
        url = f"{self.API_URL}/catalog/{sf}/songs"

        for chunk_idx, chunk in enumerate(chunks):
            chunk_param = ",".join(chunk)
            params = {"filter[isrc]": chunk_param}
            chunk_outcome: Optional[CatalogSearchOutcome] = None

            for attempt in range(retries):
                # Acquire rate limiter slot
                # Acquire rate limiter slot (waits out 429 pause / circuit breaker smoothly)
                acquired = self.limiter.acquire(timeout=65.0)
                if not acquired:
                    is_cool, cd = self.limiter.is_cooling_down()
                    chunk_outcome = CatalogSearchOutcome(
                        kind="rate_limited",
                        retry_after_seconds=cd or 10.0,
                        safe_message=f"Apple Music 频控冷却中，熔断保护中 (预计 {round(cd or 10.0, 1)} 秒)",
                    )
                    self.diagnostics.record("rate_limited", backoff=True)
                    break

                req_start = time.time()
                try:
                    headers = self._get_auth_headers(require_user=False)
                    resp = self.session.get(url, headers=headers, params=params, timeout=(6.0, 12.0))
                    latency_ms = (time.time() - req_start) * 1000.0

                    if resp.status_code == 200:
                        self.limiter.record_success()
                        data = resp.json()
                        songs_data = data.get("data", [])
                        req_id = _extract_request_id(resp.headers)

                        # Group returned songs by their ISRC
                        songs_by_isrc: Dict[str, List[AppleMusicTrack]] = {c: [] for c in chunk}
                        for item in songs_data:
                            attrs = item.get("attributes", {})
                            item_isrc = (attrs.get("isrc") or "").strip().upper()
                            artist_name = attrs.get("artistName", "")
                            artists = [a.strip() for a in artist_name.split(",") if a.strip()] or [artist_name]
                            artwork = attrs.get("artwork", {})
                            previews = attrs.get("previews") or []

                            track_obj = AppleMusicTrack(
                                id=item.get("id"),
                                title=attrs.get("name", ""),
                                artists=artists,
                                album=attrs.get("albumName"),
                                duration_ms=attrs.get("durationInMillis"),
                                isrc=item_isrc or None,
                                artwork_url=artwork.get("url"),
                                preview_url=previews[0].get("url") if previews else None,
                                storefront=sf,
                                url=attrs.get("url"),
                            )

                            if item_isrc in songs_by_isrc:
                                songs_by_isrc[item_isrc].append(track_obj)
                            else:
                                for c in chunk:
                                    if c == item_isrc:
                                        songs_by_isrc[c].append(track_obj)
                                        break

                        for c in chunk:
                            matched_tracks = songs_by_isrc.get(c, [])
                            kind = "ok" if matched_tracks else "no_hits"
                            outcome = CatalogSearchOutcome(
                                kind=kind,
                                tracks=matched_tracks,
                                http_status=200,
                                request_id=req_id,
                                safe_message=None if matched_tracks else "Apple Music 未收录此 ISRC 对应曲目",
                            )
                            results[c] = outcome
                            self._catalog_cache[("isrc", sf, c)] = (time.time(), outcome)
                            self.persistent_cache.set_catalog(sf, "isrc", c, outcome)

                        self.diagnostics.record("ok" if songs_data else "no_hits", latency_ms=latency_ms)
                        chunk_outcome = None
                        break  # Chunk success

                    elif resp.status_code == 429:
                        retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                        pause = self.limiter.record_429(retry_after)
                        self.diagnostics.record("rate_limited", latency_ms=latency_ms, backoff=True)
                        chunk_outcome = CatalogSearchOutcome(
                            kind="rate_limited",
                            http_status=429,
                            retry_after_seconds=retry_after or pause,
                            request_id=_extract_request_id(resp.headers),
                            safe_message=f"Apple Music 频控受限 (HTTP 429)，退避 {round(pause, 1)} 秒",
                        )
                        if attempt < retries - 1:
                            time.sleep(pause + 0.5)
                            continue
                        break

                    elif resp.status_code in (401, 403):
                        self.diagnostics.record("auth_failed", latency_ms=latency_ms)
                        if resp.status_code == 401 and attempt == 0:
                            self.auth.get_developer_token(force_refresh=True)
                            time.sleep(0.3)
                            continue
                        chunk_outcome = CatalogSearchOutcome(
                            kind="auth_failed",
                            http_status=resp.status_code,
                            request_id=_extract_request_id(resp.headers),
                            safe_message=f"Apple Music 授权失效或无权限 (HTTP {resp.status_code})",
                        )
                        break

                    elif resp.status_code >= 500:
                        self.diagnostics.record("upstream_error", latency_ms=latency_ms)
                        chunk_outcome = CatalogSearchOutcome(
                            kind="upstream_error",
                            http_status=resp.status_code,
                            request_id=_extract_request_id(resp.headers),
                            safe_message=f"Apple Music 上游服务器异常 (HTTP {resp.status_code})",
                        )
                        if attempt < retries - 1:
                            time.sleep(0.5 + 0.3 * attempt)
                            continue
                        break

                    else:
                        chunk_outcome = CatalogSearchOutcome(
                            kind="invalid_response",
                            http_status=resp.status_code,
                            safe_message=f"未预期的 HTTP 状态码: {resp.status_code}",
                        )
                        break

                except requests.Timeout:
                    self.diagnostics.record("timeout", backoff=True)
                    chunk_outcome = CatalogSearchOutcome(
                        kind="timeout",
                        safe_message="ISRC 批量检索 Apple Music 曲库超时",
                    )
                    if attempt < retries - 1:
                        time.sleep(0.4)
                        continue
                    break
                except requests.RequestException as e:
                    self.diagnostics.record("network_error")
                    chunk_outcome = CatalogSearchOutcome(
                        kind="network_error",
                        safe_message=f"ISRC 批量请求网络连接异常: {type(e).__name__}",
                    )
                    if attempt < retries - 1:
                        time.sleep(0.4)
                        continue
                    break
                except Exception as e:
                    self.diagnostics.record("invalid_response")
                    chunk_outcome = CatalogSearchOutcome(
                        kind="invalid_response",
                        safe_message=f"ISRC 批量响应解析异常: {str(e)[:100]}",
                    )
                    break
                finally:
                    self.limiter.release()

            if chunk_outcome is not None:
                for c in chunk:
                    if c not in results:
                        results[c] = chunk_outcome
                if chunk_outcome.kind in ("auth_failed", "rate_limited"):
                    for rem_chunk in chunks[chunk_idx + 1 :]:
                        for c in rem_chunk:
                            if c not in results:
                                results[c] = chunk_outcome
                    break

        return results

    def search_by_isrc(
        self,
        isrc: str,
        storefront: Optional[str] = None,
        retries: int = 2,
    ) -> CatalogSearchOutcome:
        """
        Search songs in Apple Music Catalog by exact ISRC using official filter[isrc] parameter.
        Delegates directly to search_by_isrc_batch for unified caching and rate limiting.
        """
        clean_isrc = isrc.strip().upper() if isrc else ""
        if not clean_isrc:
            return CatalogSearchOutcome(kind="no_hits", safe_message="ISRC 参数为空")

        batch_res = self.search_by_isrc_batch([clean_isrc], storefront=storefront, retries=retries)
        return batch_res.get(
            clean_isrc,
            CatalogSearchOutcome(kind="network_error", safe_message="ISRC 查询异常未完成"),
        )

    def search_catalog(
        self,
        query: str,
        storefront: Optional[str] = None,
        limit: int = 10,
        retries: int = 4,
    ) -> CatalogSearchOutcome:
        """
        Search songs in Apple Music Catalog for a specific storefront.
        Returns structured CatalogSearchOutcome with adaptive rate limiting and singleflight deduplication.
        """
        sf = storefront or self.config.storefront or "cn"
        clean_q = query.strip().lower()
        if not clean_q:
            return CatalogSearchOutcome(kind="no_hits", safe_message="检索词为空")

        cache_key = (sf, clean_q, limit)

        # 0. Check persistent SQLite cache (instant 0ms resolution)
        cached_p = self.persistent_cache.get_catalog(sf, "term", clean_q, limit_val=limit)
        if cached_p:
            self.diagnostics.record("cache_hit")
            return cached_p

        # 1. Check in-memory cache (only valid hits/no_hits are cached)
        if cache_key in self._catalog_cache:
            cache_time, cached_outcome = self._catalog_cache[cache_key]
            if time.time() - cache_time < 900:  # 15 minutes TTL
                return cached_outcome

        # 2. Singleflight: coalesce identical in-flight searches
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
            wait_event.wait(timeout=35.0)
            with self._in_flight_lock:
                if cache_key in self._in_flight_results:
                    return self._in_flight_results[cache_key]
            if cache_key in self._catalog_cache:
                return self._catalog_cache[cache_key][1]
            return CatalogSearchOutcome(kind="timeout", safe_message="并发等待检索查询超时")

        url = f"{self.API_URL}/catalog/{sf}/search"
        params = {
            "term": query,
            "types": "songs",
            "limit": limit,
        }
        last_outcome: Optional[CatalogSearchOutcome] = None

        try:
            for attempt in range(retries):
                # Acquire rate limiter slot (waits out 429 pause / circuit breaker smoothly)
                acquired = self.limiter.acquire(timeout=65.0)
                if not acquired:
                    is_cool, cd = self.limiter.is_cooling_down()
                    last_outcome = CatalogSearchOutcome(
                        kind="rate_limited",
                        retry_after_seconds=cd or 10.0,
                        safe_message=f"Apple Music 频控保护中，已熔断暂停 (预计 {round(cd or 10.0, 1)} 秒)",
                    )
                    self.diagnostics.record("rate_limited", backoff=True)
                    break

                req_start = time.time()
                try:
                    headers = self._get_auth_headers(require_user=False)
                    resp = self.session.get(url, headers=headers, params=params, timeout=(5.0, 9.0))
                    latency_ms = (time.time() - req_start) * 1000.0

                    if resp.status_code == 200:
                        self.limiter.record_success()
                        data = resp.json()
                        songs_data = data.get("results", {}).get("songs", {}).get("data", [])
                        results: List[AppleMusicTrack] = []
                        for item in songs_data:
                            attrs = item.get("attributes", {})
                            artist_name = attrs.get("artistName", "")
                            artists = [a.strip() for a in artist_name.split(",") if a.strip()] or [artist_name]
                            artwork = attrs.get("artwork", {})
                            previews = attrs.get("previews") or []

                            results.append(AppleMusicTrack(
                                id=item.get("id"),
                                title=attrs.get("name", ""),
                                artists=artists,
                                album=attrs.get("albumName"),
                                duration_ms=attrs.get("durationInMillis"),
                                isrc=attrs.get("isrc"),
                                artwork_url=artwork.get("url"),
                                preview_url=previews[0].get("url") if previews else None,
                                storefront=sf,
                                url=attrs.get("url"),
                            ))

                        kind = "ok" if results else "no_hits"
                        outcome = CatalogSearchOutcome(
                            kind=kind,
                            tracks=results,
                            http_status=200,
                            request_id=_extract_request_id(resp.headers),
                            safe_message=None if results else "Apple Music 曲库中未搜索到相关歌曲",
                        )
                        # Cache only verified ok/no_hits
                        self._catalog_cache[cache_key] = (time.time(), outcome)
                        self.persistent_cache.set_catalog(sf, "term", clean_q, outcome, limit_val=limit)
                        if len(self._catalog_cache) > 4000:
                            self._catalog_cache.clear()

                        self.diagnostics.record(kind, latency_ms=latency_ms)
                        last_outcome = outcome
                        return outcome

                    elif resp.status_code == 429:
                        retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                        pause = self.limiter.record_429(retry_after)
                        self.diagnostics.record("rate_limited", latency_ms=latency_ms, backoff=True)
                        last_outcome = CatalogSearchOutcome(
                            kind="rate_limited",
                            http_status=429,
                            retry_after_seconds=retry_after or pause,
                            request_id=_extract_request_id(resp.headers),
                            safe_message=f"Apple Music 频控限制 (HTTP 429)，预计 {round(pause, 1)} 秒后可恢复",
                        )
                        if attempt < retries - 1:
                            time.sleep(pause + 0.5)
                            continue
                        break

                    elif resp.status_code in (401, 403):
                        self.diagnostics.record("auth_failed", latency_ms=latency_ms)
                        if resp.status_code == 401 and attempt == 0:
                            self.auth.get_developer_token(force_refresh=True)
                            time.sleep(0.3)
                            continue
                        last_outcome = CatalogSearchOutcome(
                            kind="auth_failed",
                            http_status=resp.status_code,
                            request_id=_extract_request_id(resp.headers),
                            safe_message=f"Apple Music 授权失效或无权限 (HTTP {resp.status_code})",
                        )
                        break

                    elif resp.status_code >= 500:
                        self.diagnostics.record("upstream_error", latency_ms=latency_ms)
                        last_outcome = CatalogSearchOutcome(
                            kind="upstream_error",
                            http_status=resp.status_code,
                            request_id=_extract_request_id(resp.headers),
                            safe_message=f"Apple Music 上游服务器异常 (HTTP {resp.status_code})",
                        )
                        if attempt < retries - 1:
                            time.sleep(0.5 + 0.3 * attempt)
                            continue
                        break
                    else:
                        last_outcome = CatalogSearchOutcome(
                            kind="invalid_response",
                            http_status=resp.status_code,
                            safe_message=f"未预期的 HTTP 响应码: {resp.status_code}",
                        )
                        break

                except requests.Timeout:
                    self.diagnostics.record("timeout", backoff=True)
                    last_outcome = CatalogSearchOutcome(
                        kind="timeout",
                        safe_message="检索 Apple Music 曲库超时",
                    )
                    if attempt < retries - 1:
                        time.sleep(0.4)
                        continue
                    break
                except requests.RequestException as e:
                    self.diagnostics.record("network_error")
                    last_outcome = CatalogSearchOutcome(
                        kind="network_error",
                        safe_message=f"网络连接异常: {type(e).__name__}",
                    )
                    if attempt < retries - 1:
                        time.sleep(0.4)
                        continue
                    break
                except Exception as e:
                    self.diagnostics.record("invalid_response")
                    last_outcome = CatalogSearchOutcome(
                        kind="invalid_response",
                        safe_message=f"解析数据异常: {str(e)[:100]}",
                    )
                    break
                finally:
                    self.limiter.release()

            return last_outcome or CatalogSearchOutcome(kind="network_error", safe_message="检索异常未完成")
        finally:
            if is_initiator:
                with self._in_flight_lock:
                    if last_outcome:
                        self._in_flight_results[cache_key] = last_outcome
                    event = self._in_flight_events.pop(cache_key, None)
                    if event:
                        event.set()

    def preflight_check(self, storefront: Optional[str] = None) -> Dict[str, Any]:
        """
        Lightweight health check before initiating batch matching:
        1. Verify Developer Token generation
        2. Check media-user-token status (if configured)
        3. Check rate limiter cooldown / circuit breaker
        4. Execute low-cost probe query to storefront
        """
        sf = storefront or self.config.storefront or "cn"

        # 1. Developer token check
        try:
            dev_tok = self.auth.get_developer_token()
            if not dev_tok:
                return {
                    "can_proceed": False,
                    "reason": "无法生成有效的 Apple Developer Token",
                    "kind": "auth_failed",
                    "storefront": sf,
                }
        except Exception as e:
            return {
                "can_proceed": False,
                "reason": f"Developer Token 获取异常: {e}",
                "kind": "auth_failed",
                "storefront": sf,
            }

        # 2. Rate limit cooldown check
        is_cooling, cd_sec = self.limiter.is_cooling_down()
        if is_cooling:
            if cd_sec <= 2.5:
                time.sleep(cd_sec + 0.1)
            else:
                return {
                    "can_proceed": False,
                    "reason": f"Apple Music 当前正处于频控冷却中 (约 {round(cd_sec, 1)} 秒)，请稍后再试",
                    "kind": "rate_limited",
                    "retry_after_seconds": cd_sec,
                    "storefront": sf,
                }

        # 3. User token check (if configured)
        user_token_valid = True
        user_token_msg = "已连接"
        if self.config.media_user_token:
            try:
                valid, info = self.auth.validate_user_token()
                user_token_valid = valid
                user_token_msg = info
                if not valid:
                    return {
                        "can_proceed": False,
                        "reason": f"Apple ID 授权已失效 ({info})，请重新连接 Apple ID",
                        "kind": "auth_failed",
                        "storefront": sf,
                    }
            except Exception as e:
                user_token_msg = f"校验异常: {e}"

        # 4. Probe catalog
        probe_outcome = self.search_catalog("晴天", storefront=sf, limit=1, retries=1)
        if probe_outcome.kind == "rate_limited":
            return {
                "can_proceed": False,
                "reason": f"Apple Music 当前受频控限制，预计 {round(probe_outcome.retry_after_seconds or 10, 1)} 秒后恢复",
                "kind": "rate_limited",
                "retry_after_seconds": probe_outcome.retry_after_seconds,
                "storefront": sf,
            }
        elif probe_outcome.kind == "auth_failed":
            return {
                "can_proceed": False,
                "reason": "Apple Music API 授权失败 (HTTP 401/403)，请检查授权凭证",
                "kind": "auth_failed",
                "storefront": sf,
            }
        elif probe_outcome.kind in ("network_error", "timeout", "upstream_error"):
            return {
                "can_proceed": False,
                "reason": f"无法连接 Apple Music 曲库服务: {probe_outcome.safe_message}",
                "kind": probe_outcome.kind,
                "storefront": sf,
            }

        return {
            "can_proceed": True,
            "reason": "Apple Music 连接与授权正常",
            "kind": "ok",
            "storefront": sf,
            "user_token_valid": user_token_valid,
            "user_token_msg": user_token_msg,
        }

    def get_diagnostics(self, storefront: Optional[str] = None) -> BatchDiagnosticSummary:
        """Return diagnostic metrics of catalog search operations."""
        sf = storefront or self.config.storefront or "cn"
        return self.diagnostics.get_summary(storefront=sf)

    def reset_diagnostics(self):
        """Reset diagnostics metrics."""
        self.diagnostics.reset()

    def create_playlist(
        self,
        name: str,
        description: str = "Imported by Apple Music Playlist Importer",
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
            first_artist = artist.split()[0] if artist.split() else ""
            if first_artist:
                queries.append(f"{clean_t} {first_artist}")
        if title.strip() not in queries:
            queries.append(title.strip())

        norm_target_title = TextCleaner.normalize(clean_t).lower()
        norm_target_artist = TextCleaner.normalize(artist).lower() if artist else ""

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
                        best_cand_id = None
                        best_score = 0.0

                        for it in data:
                            attrs = it.get("attributes", {})
                            it_name = attrs.get("name", "")
                            it_artist = attrs.get("artistName", "")

                            norm_it_name = TextCleaner.normalize(it_name).lower()
                            norm_it_artist = TextCleaner.normalize(it_artist).lower()

                            # Calculate title similarity
                            title_sim = TextCleaner.similarity(norm_target_title, norm_it_name)
                            if norm_target_title in norm_it_name or norm_it_name in norm_target_title:
                                title_sim = max(title_sim, 0.85)

                            # Calculate artist similarity if artist was provided
                            if norm_target_artist and norm_it_artist:
                                artist_sim = TextCleaner.similarity(norm_target_artist, norm_it_artist)
                                if norm_target_artist in norm_it_artist or norm_it_artist in norm_target_artist:
                                    artist_sim = max(artist_sim, 0.85)
                                combined_score = title_sim * 0.65 + artist_sim * 0.35
                            else:
                                combined_score = title_sim

                            if combined_score > best_score and combined_score >= 0.75:
                                best_score = combined_score
                                best_cand_id = it.get("id")

                        if best_cand_id:
                            return best_cand_id
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
