"""
Unit tests for PersistentCache and query budget optimizations.
"""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from applemusic.cache import PersistentCache
from applemusic.config import Config
from applemusic.client import AppleMusicClient
from applemusic.matcher.engine import MatchingEngine
from applemusic.models import AppleMusicTrack, CatalogSearchOutcome, ConfidenceLevel, DecisionStatus, MatchCandidate, Playlist, SongMatchResult, Track


class TestPersistentCache(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_cache.db"
        self.cache = PersistentCache(self.db_path)

    def tearDown(self):
        self.cache.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_catalog_cache_ok_flow(self):
        sample_track = AppleMusicTrack(
            id="123456",
            title="Sunny Day",
            artists=["Jay Chou"],
            album="Yeh Hui-Mei",
            duration_ms=269000,
            isrc="CN12345678",
            storefront="cn",
        )
        outcome = CatalogSearchOutcome(
            kind="ok",
            tracks=[sample_track],
            http_status=200,
            safe_message=None,
        )
        # Store
        self.cache.set_catalog("cn", "term", "sunny day", outcome, limit_val=10)

        # Retrieve
        cached = self.cache.get_catalog("cn", "term", "sunny day", limit_val=10)
        self.assertIsNotNone(cached)
        self.assertEqual(cached.kind, "ok")
        self.assertEqual(len(cached.tracks), 1)
        self.assertEqual(cached.tracks[0].id, "123456")
        self.assertEqual(cached.tracks[0].title, "Sunny Day")

    def test_catalog_cache_no_hits_flow(self):
        outcome = CatalogSearchOutcome(
            kind="no_hits",
            tracks=[],
            http_status=200,
            safe_message="No songs found",
        )
        self.cache.set_catalog("cn", "term", "totally_absent_track_xyz", outcome, limit_val=10)

        cached = self.cache.get_catalog("cn", "term", "totally_absent_track_xyz", limit_val=10)
        self.assertIsNotNone(cached)
        self.assertEqual(cached.kind, "no_hits")
        self.assertEqual(len(cached.tracks), 0)

    def test_catalog_cache_transient_error_isolation(self):
        # 429 and network errors must NEVER be cached persistently
        err_outcome = CatalogSearchOutcome(
            kind="rate_limited",
            tracks=[],
            http_status=429,
            retry_after_seconds=15.0,
            safe_message="Rate limited",
        )
        self.cache.set_catalog("cn", "term", "rate_limited_term", err_outcome, limit_val=10)
        self.assertIsNone(self.cache.get_catalog("cn", "term", "rate_limited_term", limit_val=10))

    def test_match_cache_flow(self):
        source = Track(title="晴天", artists=["周杰伦"], album="叶惠美")
        matched_cand = AppleMusicTrack(
            id="123456",
            title="晴天",
            artists=["周杰伦"],
            storefront="cn",
        )
        res = SongMatchResult(
            source_track=source,
            candidates=[],
            selected_candidate=MatchCandidate(
                track=matched_cand,
                score=1.0,
                title_score=1.0,
                artist_score=1.0,
                confidence=ConfidenceLevel.EXACT,
            ),
            status=ConfidenceLevel.EXACT,
            decision="auto_accept",
            decision_reasons=["测试匹配"],
            search_status="matched",
            search_incomplete=False,
        )

        key = "晴天:周杰伦:叶惠美:0"
        self.cache.set_match("cn", key, res)

        cached = self.cache.get_match("cn", key)
        self.assertIsNotNone(cached)
        self.assertEqual(cached.decision, "auto_accept")
        self.assertEqual(cached.selected_candidate.track.id, "123456")

    def test_cache_stats_and_expiration(self):
        stats = self.cache.get_stats()
        self.assertIn("catalog_total", stats)
        self.assertIn("match_total", stats)
        self.assertIn("db_size_bytes", stats)


class TestClientWithPersistentCache(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_client_cache.db"
        self.config = Config(developer_token="dummy_token", storefront="cn")
        self.client = AppleMusicClient(self.config)
        self.client.persistent_cache = PersistentCache(self.db_path)

    def tearDown(self):
        self.client.persistent_cache.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_client_search_catalog_cache_hit(self):
        # Pre-seed persistent cache
        sample_track = AppleMusicTrack(
            id="999",
            title="Cached Song",
            artists=["Artist"],
            storefront="cn",
        )
        outcome = CatalogSearchOutcome(kind="ok", tracks=[sample_track], http_status=200)
        self.client.persistent_cache.set_catalog("cn", "term", "cached song", outcome, limit_val=10)

        # Calling search_catalog should return cached result without any network requests
        res = self.client.search_catalog("cached song", storefront="cn", limit=10)
        self.assertEqual(res.kind, "ok")
        self.assertEqual(res.tracks[0].id, "999")
        self.assertEqual(self.client.diagnostics.cache_hits, 1)


class TestEngineQueryBudget(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_engine_cache.db"
        self.config = Config(developer_token="dummy_token", storefront="cn")
        self.client = AppleMusicClient(self.config)
        self.client.persistent_cache = PersistentCache(self.db_path)
        self.engine = MatchingEngine(self.client, self.config)
        self.engine.persistent_cache = self.client.persistent_cache

    def tearDown(self):
        self.client.persistent_cache.close()
        self.engine.persistent_cache.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_absent_track_query_budget_capped(self):
        # When a song is completely absent (returns no_hits), match_track must NOT fire more than 2 queries
        mock_outcome = CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)
        with patch.object(self.client, "search_catalog", return_value=mock_outcome) as mock_search:
            track = Track(title="Nonexistent Song 123", artists=["Ghost Artist"], album="None")
            result = self.engine.match_track(track, storefront="cn")

            self.assertEqual(result.decision, DecisionStatus.NO_MATCH.value)
            # Must be <= 2 queries
            self.assertLessEqual(mock_search.call_count, 2)
            self.assertGreaterEqual(mock_search.call_count, 1)

    def test_match_playlist_persistent_cache_hit(self):
        # Pre-seed match cache for a track
        track = Track(title="七里香", artists=["周杰伦"], album="七里香")
        key = self.engine.get_stable_track_key(track)
        key_str = ":".join(str(x) for x in key)

        cand = MatchCandidate(
            track=AppleMusicTrack(id="777", title="七里香", artists=["周杰伦"], storefront="cn"),
            score=1.0,
            title_score=1.0,
            artist_score=1.0,
            confidence=ConfidenceLevel.EXACT,
        )
        pre_result = SongMatchResult(
            source_track=track,
            candidates=[],
            selected_candidate=cand,
            status=ConfidenceLevel.EXACT,
            decision="auto_accept",
            decision_reasons=["Cached auto accept"],
            search_status="matched",
            search_incomplete=False,
        )
        self.engine.persistent_cache.set_match("cn", key_str, pre_result)

        # Call match_playlist with this track
        playlist = Playlist(name="Test PL", tracks=[track])
        with patch.object(self.engine, "match_track") as mock_match_track:
            results = self.engine.match_playlist(playlist, storefront="cn")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].decision, "auto_accept")
            self.assertEqual(results[0].selected_candidate.track.id, "777")
            # match_track was completely bypassed thanks to persistent match cache!
            mock_match_track.assert_not_called()


if __name__ == "__main__":
    unittest.main()
