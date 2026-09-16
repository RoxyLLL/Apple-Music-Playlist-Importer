import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from applemusic.cache import PersistentCache
from applemusic.client import AdaptiveRateLimiter, AppleMusicClient
from applemusic.config import Config
from applemusic.matcher.engine import MatchingEngine
from applemusic.models import AppleMusicTrack, CatalogSearchOutcome, Playlist, Track


class TestBatchISRCAndMultiIndexCache(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_cache.db"
        self.cache = PersistentCache(self.db_path)

        self.config = Config(storefront="us")
        self.client = AppleMusicClient(self.config)
        self.client.persistent_cache = self.cache
        self.engine = MatchingEngine(client=self.client, config=self.config)
        self.engine.persistent_cache = self.cache

    def tearDown(self):
        self.cache.close()
        self.temp_dir.cleanup()

    def test_record_429_includes_jitter(self):
        """Verify record_429 adds positive random jitter to Retry-After backoff."""
        limiter = AdaptiveRateLimiter(target_qps=2.0)
        # Test with Retry-After = 5.0
        backoff = limiter.record_429(retry_after=5.0)
        # Should be strictly greater than 5.0 and at most 5.9
        self.assertGreater(backoff, 5.25)
        self.assertLessEqual(backoff, 5.85)

        # Test with no Retry-After
        limiter.consecutive_429 = 1
        backoff_no_ra = limiter.record_429(retry_after=None)
        self.assertGreater(backoff_no_ra, 2.5)

    def test_batch_isrc_chunking_and_caching(self):
        """Verify search_by_isrc_batch splits >25 ISRCs into chunks of 25 and caches results."""
        # 60 mock ISRCs
        isrcs = [f"USRC{i:08d}" for i in range(1, 61)]

        called_chunks = []

        def mock_get(url, headers=None, params=None, timeout=None):
            req_isrcs = params["filter[isrc]"].split(",")
            called_chunks.append(req_isrcs)

            # Return a hit for USRC00000001 only
            data = []
            if "USRC00000001" in req_isrcs:
                data.append({
                    "id": "am-101",
                    "type": "songs",
                    "attributes": {
                        "name": "Hit Song",
                        "artistName": "Artist A",
                        "albumName": "Album A",
                        "durationInMillis": 200000,
                        "isrc": "USRC00000001",
                        "url": "https://music.apple.com/song/101",
                    },
                })
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"data": data}
            mock_resp.headers = {"x-apple-request-id": "req-1"}
            return mock_resp

        with patch.object(self.client.session, "get", side_effect=mock_get):
            results = self.client.search_by_isrc_batch(isrcs, storefront="us")

        # 60 items should be chunked into 3 requests: 25, 25, 10
        self.assertEqual(len(called_chunks), 3)
        self.assertEqual(len(called_chunks[0]), 25)
        self.assertEqual(len(called_chunks[1]), 25)
        self.assertEqual(len(called_chunks[2]), 10)

        # Verify results
        self.assertIn("USRC00000001", results)
        self.assertEqual(results["USRC00000001"].kind, "ok")
        self.assertEqual(results["USRC00000001"].tracks[0].id, "am-101")

        # Unmatched ISRC should be cached as no_hits
        self.assertIn("USRC00000002", results)
        self.assertEqual(results["USRC00000002"].kind, "no_hits")

        # Verify persistent cache has the entry
        cached_hit = self.cache.get_catalog("us", "isrc", "USRC00000001")
        self.assertIsNotNone(cached_hit)
        self.assertEqual(cached_hit.kind, "ok")

        cached_miss = self.cache.get_catalog("us", "isrc", "USRC00000002")
        self.assertIsNotNone(cached_miss)
        self.assertEqual(cached_miss.kind, "no_hits")

        # Second call should make 0 network requests
        called_chunks.clear()
        with patch.object(self.client.session, "get", side_effect=mock_get):
            results2 = self.client.search_by_isrc_batch(isrcs[:10], storefront="us")

        self.assertEqual(len(called_chunks), 0)
        self.assertEqual(len(results2), 10)
        self.assertEqual(results2["USRC00000001"].tracks[0].id, "am-101")

    def test_multi_index_cache_resolution(self):
        """Verify find_match resolves across ISRC, source:orig_id, and text:title:artist."""
        source_track = Track(
            title="Blinding Lights",
            artists=["The Weeknd"],
            album="After Hours",
            duration_ms=200000,
            original_id="spotify-12345",
            isrc="USUM71900764",
            source="spotify",
        )

        match_candidate = AppleMusicTrack(
            id="1499378607",
            title="Blinding Lights",
            artists=["The Weeknd"],
            album="After Hours",
            duration_ms=200040,
            isrc="USUM71900764",
        )

        from applemusic.models import ConfidenceLevel, MatchCandidate, SongMatchResult
        cand = MatchCandidate(
            track=match_candidate,
            score=0.95,
            title_score=1.0,
            artist_score=1.0,
            confidence=ConfidenceLevel.HIGH,
            decision="auto_accept",
            decision_reasons=["ISRC 匹配"],
        )
        match_res = SongMatchResult(
            source_track=source_track,
            candidates=[cand],
            selected_candidate=cand,
            status=ConfidenceLevel.HIGH,
            decision="auto_accept",
            decision_reasons=["ISRC 匹配"],
            search_status="matched",
        )

        # Persist with track
        self.cache.set_match("us", "primary_hash_1", match_res, track=source_track)

        # 1. Query by identical ISRC from different platform
        track_from_qq = Track(
            title="Blinding Lights",
            artists=["The Weeknd"],
            isrc="USUM71900764",
            source="qqmusic",
        )
        res_isrc = self.cache.find_match("us", track_from_qq)
        self.assertIsNotNone(res_isrc)
        self.assertEqual(res_isrc.selected_candidate.track.id, "1499378607")

        # 2. Query by original_id from same platform
        track_from_spotify_reimport = Track(
            title="Blinding Lights (Remix)",  # altered title
            artists=["The Weeknd"],
            original_id="spotify-12345",
            source="spotify",
        )
        res_orig = self.cache.find_match("us", track_from_spotify_reimport)
        self.assertIsNotNone(res_orig)
        self.assertEqual(res_orig.selected_candidate.track.id, "1499378607")

        # 3. Query by text from local file without ISRC or ID
        track_from_local = Track(
            title="Blinding Lights",
            artists=["The Weeknd"],
            source="local_file",
        )
        res_text = self.cache.find_match("us", track_from_local)
        self.assertIsNotNone(res_text)
        self.assertEqual(res_text.selected_candidate.track.id, "1499378607")

    def test_engine_phase1_5_batch_isrc_resolution(self):
        """Verify MatchingEngine resolves tracks with ISRC in batch and auto-accepts them."""
        tracks = [
            Track(
                title="Starboy",
                artists=["The Weeknd"],
                isrc="USUM71605282",
                source="spotify",
            ),
            Track(
                title="Unknown Track Without ISRC",
                artists=["Indie Artist"],
                source="local_file",
            ),
        ]
        pl = Playlist(name="Test PL", tracks=tracks, source="test")

        # Mock search_by_isrc_batch
        def mock_isrc_batch(isrc_list, storefront=None, retries=2):
            out = {}
            for i in isrc_list:
                if i == "USUM71605282":
                    out[i] = CatalogSearchOutcome(
                        kind="ok",
                        tracks=[
                            AppleMusicTrack(
                                id="am-starboy",
                                title="Starboy",
                                artists=["The Weeknd", "Daft Punk"],
                                album="Starboy",
                                duration_ms=230000,
                                isrc="USUM71605282",
                            )
                        ],
                    )
                else:
                    out[i] = CatalogSearchOutcome(kind="no_hits")
            return out

        # Mock text search for track without ISRC
        def mock_text_search(query, storefront=None, limit=10, retries=3):
            return CatalogSearchOutcome(kind="no_hits")

        self.client.search_by_isrc_batch = MagicMock(side_effect=mock_isrc_batch)
        self.client.search_catalog = MagicMock(side_effect=mock_text_search)

        results = self.engine.match_playlist(pl, storefront="us", max_workers=1)

        self.assertEqual(len(results), 2)
        # Track 0: Starboy matched via batch ISRC fast path
        self.assertEqual(results[0].decision, "auto_accept")
        self.assertEqual(results[0].selected_candidate.track.id, "am-starboy")
        self.assertIn("ISRC 批量极速精准匹配", results[0].decision_reasons[0])

        # Track 1: Fell back to text search
        self.assertEqual(results[1].decision, "no_match")

        # Verify batch isrc was called with only Starboy's ISRC
        self.client.search_by_isrc_batch.assert_called_once()
        called_args = self.client.search_by_isrc_batch.call_args[0][0]
        self.assertEqual(called_args, ["USUM71605282"])


if __name__ == "__main__":
    unittest.main()
