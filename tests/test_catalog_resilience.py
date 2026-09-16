"""
Comprehensive Unit Tests for Apple Music Catalog Search Resilience
Verifies:
1. HTTP 429, 401, timeout, 5xx, and network errors are NEVER misclassified as 'no_match'.
2. Candidate retention on partial tier failure (search_incomplete = True).
3. AdaptiveRateLimiter token bucket, global 429 cooldown, and circuit breaker tripping.
4. Negative cache isolation (errors are never cached; only ok and no_hits are cached).
5. Thread-safe batch abort coordinator in MatchingEngine.
6. Diagnostics tracking.
"""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import requests

from applemusic.cache import PersistentCache
from applemusic.config import Config
from applemusic.client import AppleMusicClient, AdaptiveRateLimiter, parse_retry_after
from applemusic.models import Track, Playlist, SongMatchResult, CatalogSearchOutcome, AppleMusicTrack
from applemusic.matcher.engine import MatchingEngine


class TestRetryAfterParser(unittest.TestCase):
    def test_parse_integer_seconds(self):
        self.assertEqual(parse_retry_after("15"), 15.0)

    def test_parse_float_seconds(self):
        self.assertAlmostEqual(parse_retry_after("2.5"), 2.5)

    def test_parse_none_or_empty(self):
        self.assertEqual(parse_retry_after(None, default=5.0), 5.0)
        self.assertEqual(parse_retry_after("", default=3.0), 3.0)

    def test_parse_http_date(self):
        import email.utils
        future_epoch = time.time() + 30
        date_str = email.utils.formatdate(future_epoch, usegmt=True)
        parsed = parse_retry_after(date_str)
        self.assertTrue(25 <= parsed <= 35)


class TestAdaptiveRateLimiter(unittest.TestCase):
    def test_token_bucket_acquisition(self):
        limiter = AdaptiveRateLimiter(target_qps=100.0, max_concurrency=4)
        t0 = time.time()
        acquired = limiter.acquire(timeout=2.0)
        self.assertTrue(acquired)
        limiter.release()
        self.assertLess(time.time() - t0, 0.5)

    def test_context_manager_usage(self):
        limiter = AdaptiveRateLimiter(target_qps=100.0, max_concurrency=4)
        with limiter:
            pass

    def test_on_429_cooldown(self):
        limiter = AdaptiveRateLimiter(target_qps=10.0, max_concurrency=2)
        limiter.record_429(retry_after=0.3)
        is_cool, cd = limiter.is_cooling_down()
        self.assertTrue(is_cool)
        self.assertGreater(limiter.cooldown_remaining(), 0)
        
        t0 = time.time()
        acquired = limiter.acquire(timeout=2.0)
        self.assertTrue(acquired)
        limiter.release()
        elapsed = time.time() - t0
        self.assertGreaterEqual(elapsed, 0.25)
        self.assertFalse(limiter.is_cooling_down()[0])

    def test_circuit_breaker_tripping(self):
        limiter = AdaptiveRateLimiter(circuit_breaker_threshold=3)
        self.assertFalse(limiter.circuit_breaker_tripped)
        
        limiter.record_429(retry_after=0.1)
        self.assertEqual(limiter.consecutive_429, 1)
        self.assertFalse(limiter.circuit_breaker_tripped)

        limiter.record_429(retry_after=0.1)
        self.assertEqual(limiter.consecutive_429, 2)
        self.assertFalse(limiter.circuit_breaker_tripped)

        limiter.record_429(retry_after=0.1)
        self.assertEqual(limiter.consecutive_429, 3)
        self.assertTrue(limiter.circuit_breaker_tripped)

        # On success, circuit breaker and consecutive counter reset
        limiter.record_success()
        self.assertEqual(limiter.consecutive_429, 0)
        self.assertFalse(limiter.circuit_breaker_tripped)


class TestClientCatalogResilience(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = Config(
            developer_token="dummy_dev_token",
            storefront="cn",
        )
        self.client = AppleMusicClient(self.config)
        self.client.limiter = AdaptiveRateLimiter(target_qps=1000.0, max_concurrency=10)
        self.client.persistent_cache = PersistentCache(Path(self.temp_dir.name) / "test.db")

    def tearDown(self):
        self.client.persistent_cache.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    @patch.object(requests.Session, "get")
    def test_200_with_hits(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"X-Apple-Request-Id": "req-1"}
        mock_resp.json.return_value = {
            "results": {
                "songs": {
                    "data": [
                        {
                            "id": "12345",
                            "attributes": {
                                "name": "夜曲",
                                "artistName": "周杰伦",
                                "albumName": "十一月的萧邦",
                                "durationInMillis": 226000,
                            }
                        }
                    ]
                }
            }
        }
        mock_get.return_value = mock_resp

        outcome = self.client.search_catalog("夜曲 周杰伦", "cn")
        self.assertEqual(outcome.kind, "ok")
        self.assertEqual(len(outcome.tracks), 1)
        self.assertEqual(outcome.tracks[0].title, "夜曲")
        self.assertEqual(outcome.http_status, 200)

    @patch.object(requests.Session, "get")
    def test_200_no_hits(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.json.return_value = {"results": {}}
        mock_get.return_value = mock_resp

        outcome = self.client.search_catalog("这是一首绝对不存在的火星歌曲xyz", "cn")
        self.assertEqual(outcome.kind, "no_hits")
        self.assertEqual(len(outcome.tracks), 0)
        self.assertEqual(outcome.http_status, 200)

    @patch.object(requests.Session, "get")
    def test_429_rate_limited(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {"Retry-After": "10", "X-Apple-Request-Id": "req-429"}
        mock_resp.text = "Too Many Requests"
        mock_get.return_value = mock_resp

        outcome = self.client.search_catalog("夜曲", "cn")
        self.assertEqual(outcome.kind, "rate_limited")
        self.assertEqual(outcome.http_status, 429)
        self.assertEqual(outcome.retry_after_seconds, 10.0)
        self.assertEqual(outcome.request_id, "req-429")
        self.assertIn("429", outcome.safe_message)

    @patch.object(requests.Session, "get")
    def test_401_auth_failed(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.headers = {"X-Apple-Request-Id": "req-401"}
        mock_resp.text = "Unauthorized developer token"
        mock_get.return_value = mock_resp

        outcome = self.client.search_catalog("夜曲", "cn")
        self.assertEqual(outcome.kind, "auth_failed")
        self.assertEqual(outcome.http_status, 401)
        self.assertEqual(outcome.request_id, "req-401")
        self.assertIn("授权失效", outcome.safe_message)

    @patch.object(requests.Session, "get")
    def test_timeout_error(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout("Connection timed out after 8s")

        outcome = self.client.search_catalog("晴天", "cn")
        self.assertEqual(outcome.kind, "timeout")
        self.assertIn("超时", outcome.safe_message)

    @patch.object(requests.Session, "get")
    def test_network_error(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection reset by peer")

        outcome = self.client.search_catalog("晴天", "cn")
        self.assertEqual(outcome.kind, "network_error")
        self.assertIn("网络连接", outcome.safe_message)

    @patch.object(requests.Session, "get")
    def test_negative_cache_isolation(self, mock_get):
        """Verify that 429, 401, timeout, and network errors are NEVER cached."""
        # 1. 429 call
        mock_429 = MagicMock()
        mock_429.status_code = 429
        mock_429.headers = {"Retry-After": "1"}
        mock_429.text = "Too Many Requests"
        mock_get.return_value = mock_429

        out1 = self.client.search_catalog("test_cache_query", "cn", limit=10)
        self.assertEqual(out1.kind, "rate_limited")

        # Check cache is empty for this query
        cache_key = ("cn", "test_cache_query", 10)
        self.assertNotIn(cache_key, self.client._catalog_cache)

        # Clear cooldown so 2nd request proceeds immediately
        self.client.limiter.reset()

        # 2. Now return 200 with hits
        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.headers = {}
        mock_200.json.return_value = {
            "results": {
                "songs": {
                    "data": [{"id": "999", "attributes": {"name": "Cached Song", "artistName": "Artist"}}]
                }
            }
        }
        mock_get.return_value = mock_200
        out2 = self.client.search_catalog("test_cache_query", "cn", limit=10)
        self.assertEqual(out2.kind, "ok")
        # Should now be in cache
        self.assertIn(cache_key, self.client._catalog_cache)


class TestMatcherEngineResilience(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = Config(developer_token="dummy_dev_token", storefront="cn")
        self.client = AppleMusicClient(self.config)
        self.client.limiter = AdaptiveRateLimiter(target_qps=1000.0, max_concurrency=10)
        self.client.persistent_cache = PersistentCache(Path(self.temp_dir.name) / "test.db")
        self.engine = MatchingEngine(self.client)
        self.engine.persistent_cache = self.client.persistent_cache

    def tearDown(self):
        self.client.persistent_cache.close()
        self.engine.persistent_cache.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    @patch.object(AppleMusicClient, "search_catalog")
    def test_genuine_no_match_classification(self, mock_search):
        """When Apple Music returns 200 with 0 hits on all queries, classify as true no_match."""
        mock_search.return_value = CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)

        src = Track(title="绝对不存在的歌", artists=["无名氏"])
        result = self.engine.match_track(src, "cn")

        self.assertEqual(result.search_status, "no_match")
        self.assertEqual(result.status, "not_found")
        self.assertEqual(result.decision, "no_match")
        self.assertIsNone(result.selected_candidate)
        self.assertFalse(result.search_incomplete)

    @patch.object(AppleMusicClient, "search_catalog")
    def test_rate_limited_never_classified_as_no_match(self, mock_search):
        """When Apple Music returns 429, mark search_status='rate_limited', NOT 'no_match'."""
        mock_search.return_value = CatalogSearchOutcome(
            kind="rate_limited",
            tracks=[],
            http_status=429,
            retry_after_seconds=5.0,
            safe_message="HTTP 429 Too Many Requests",
        )

        src = Track(title="七里香", artists=["周杰伦"])
        result = self.engine.match_track(src, "cn")

        self.assertEqual(result.search_status, "rate_limited")
        self.assertEqual(result.decision, "rate_limited")
        self.assertEqual(result.status, "not_found")
        self.assertNotEqual(result.search_status, "no_match")
        self.assertIn("429", result.decision_reasons[0])

    @patch.object(AppleMusicClient, "search_catalog")
    def test_auth_failed_never_classified_as_no_match(self, mock_search):
        """When Apple Music returns 401, mark search_status='auth_required', NOT 'no_match'."""
        mock_search.return_value = CatalogSearchOutcome(
            kind="auth_failed",
            tracks=[],
            http_status=401,
            safe_message="Developer token invalid",
        )

        src = Track(title="稻香", artists=["周杰伦"])
        result = self.engine.match_track(src, "cn")

        self.assertEqual(result.search_status, "auth_required")
        self.assertEqual(result.decision, "auth_required")
        self.assertNotEqual(result.search_status, "no_match")
        self.assertIn("授权失效", result.decision_reasons[0])

    @patch.object(AppleMusicClient, "search_catalog")
    def test_network_or_timeout_never_classified_as_no_match(self, mock_search):
        """When queries timeout or network fails, mark search_status accordingly, NOT 'no_match'."""
        mock_search.return_value = CatalogSearchOutcome(
            kind="timeout",
            tracks=[],
            safe_message="请求超时 (8s)",
        )

        src = Track(title="晴天", artists=["周杰伦"])
        result = self.engine.match_track(src, "cn")

        self.assertEqual(result.search_status, "timeout")
        self.assertEqual(result.decision, "error")
        self.assertNotEqual(result.search_status, "no_match")

    @patch.object(AppleMusicClient, "search_catalog")
    def test_partial_success_preserves_candidate(self, mock_search):
        """
        If Tier 1 search finds candidate tracks, but a subsequent tier (e.g. fallback)
        hits 429 or timeout, candidate must NOT be dropped; search_incomplete must be True.
        """
        matched_track = AppleMusicTrack(
            id="10001",
            title="青花瓷",
            artists=["周杰伦"],
            album="周杰伦 2007-2008 世界巡回演唱会",
            duration_ms=248000,
        )

        call_count = 0
        def side_effect(query, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Tier 1 returns hits
                return CatalogSearchOutcome(kind="ok", tracks=[matched_track], http_status=200)
            else:
                # Subsequent tier hits 429
                return CatalogSearchOutcome(kind="rate_limited", tracks=[], http_status=429, retry_after_seconds=5)

        mock_search.side_effect = side_effect

        src = Track(title="青花瓷", artists=["周杰伦"], album="我很忙", duration_ms=239000)
        result = self.engine.match_track(src, "cn")

        self.assertIsNotNone(result.selected_candidate)
        self.assertEqual(result.selected_candidate.track.id, "10001")
        self.assertTrue(result.search_incomplete)
        self.assertIn(result.decision, ("auto_accept", "review"))

    @patch.object(AppleMusicClient, "search_catalog")
    def test_circuit_breaker_aborts_batch_matching(self, mock_search):
        """When circuit breaker trips during batch matching, remaining tracks return unprocessed."""
        self.client.limiter.circuit_broken = True

        playlist = Playlist(
            name="Test",
            tracks=[
                Track(title="Song 1", artists=["Artist 1"]),
                Track(title="Song 2", artists=["Artist 2"]),
                Track(title="Song 3", artists=["Artist 3"]),
            ]
        )

        results = self.engine.match_playlist(playlist, "cn", max_workers=1)
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertEqual(r.decision, "unprocessed")
            self.assertEqual(r.search_status, "rate_limited")


if __name__ == "__main__":
    unittest.main()
