"""
Audit Regression Test Suite.
Verifies fixes for security token handling, storefront expansion,
decision thresholds, extractor fallbacks, and CSV escaping.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from applemusic.auth import AppleMusicAuth
from applemusic.cache import PersistentCache
from applemusic.client import AppleMusicClient
from applemusic.config import Config
from applemusic.extractors.local_file import LocalFileExtractor
from applemusic.extractors.qqmusic import QQMusicExtractor
from applemusic.matcher.engine import MatchingEngine
from applemusic.matcher.scorer import TrackScorer
from applemusic.models import (
    AppleMusicTrack,
    CatalogSearchOutcome,
    ConfidenceLevel,
    DecisionStatus,
    MatchCandidate,
    Track,
)


class TestAuditRegressions(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = Config(
            developer_token="test_dev_token",
            storefront="cn",
            fallback_storefronts=["hk", "us"],
            auto_accept_threshold=0.88,
            min_review_score=0.60,
            min_score_gap=0.08,
        )

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_small_score_gap_marks_review(self):
        """
        When candidate #1 and #2 have high scores but gap < min_score_gap (0.08),
        and candidate #2 is a competing track (e.g. cover version with different artist),
        evaluate_candidates must produce 'review' with ambiguous distinction.
        """
        source = Track(title="爱在西元前", artists=["周杰伦"], album="范特西")

        cand1 = MatchCandidate(
            track=AppleMusicTrack(id="101", title="爱在西元前", artists=["周杰伦"], album="范特西", storefront="cn"),
            score=0.89,
            title_score=1.0,
            artist_score=1.0,
            confidence=ConfidenceLevel.HIGH,
        )
        cand2 = MatchCandidate(
            track=AppleMusicTrack(id="102", title="爱在西元前", artists=["网络歌手翻唱"], album="翻唱合集", storefront="cn"),
            score=0.85,
            title_score=1.0,
            artist_score=0.50,
            confidence=ConfidenceLevel.HIGH,
        )

        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(
            source=source,
            scored_candidates=[cand1, cand2],
            auto_accept_threshold=0.88,
            min_review_score=0.60,
            min_score_gap=0.08,
        )

        self.assertEqual(dec, DecisionStatus.REVIEW.value)
        self.assertLess(gap, 0.08)
        self.assertTrue(any("分差较小" in r or "人工复核" in r for r in reasons))

    def test_storefront_expansion_not_stopped_by_review(self):
        """
        BUG-03 fix:
        In rematch_track, if primary storefront only has a review-level candidate (e.g. score 0.72),
        the search MUST NOT break early and must continue to fallback storefronts (e.g. hk)
        where an auto-accept candidate (score 1.0) exists.
        """
        client = AppleMusicClient(self.config)
        client.persistent_cache = PersistentCache(Path(self.temp_dir.name) / "rematch.db")
        engine = MatchingEngine(client, self.config)
        engine.persistent_cache = client.persistent_cache

        source = Track(title="一路向北", artists=["周杰伦"], album="J III")

        # Mock search_catalog:
        # CN storefront returns low-score/review track (score ~0.72)
        # HK storefront returns exact track (score 1.0)
        track_cn = AppleMusicTrack(
            id="cn_1",
            title="一路向北 (Live 伴奏)",
            artists=["周杰伦伴奏带"],
            storefront="cn",
        )
        track_hk = AppleMusicTrack(
            id="hk_1",
            title="一路向北",
            artists=["周杰伦"],
            album="J III",
            storefront="hk",
        )

        def mock_search(query, storefront=None, limit=10, retries=2):
            if storefront == "cn":
                return CatalogSearchOutcome(kind="ok", tracks=[track_cn], http_status=200)
            elif storefront == "hk":
                return CatalogSearchOutcome(kind="ok", tracks=[track_hk], http_status=200)
            return CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)

        with patch.object(client, "search_catalog", side_effect=mock_search) as mock_search_fn:
            result = engine.rematch_track(source, relaxed=False)

            self.assertEqual(result.decision, DecisionStatus.AUTO_ACCEPT.value)
            self.assertIsNotNone(result.selected_candidate)
            self.assertEqual(result.selected_candidate.track.id, "hk_1")
            self.assertEqual(result.selected_candidate.track.storefront, "hk")
            # Verify HK was indeed searched
            storefronts_queried = [call.kwargs.get("storefront") or call.args[1] for call in mock_search_fn.call_args_list if len(call.args) > 1 or "storefront" in call.kwargs]
            self.assertIn("hk", storefronts_queried)

        client.persistent_cache.close()

    def test_empty_tracks_dedup_no_playlist_created(self):
        """Verify that empty track lists do not trigger network requests in add_tracks_to_playlist."""
        client = AppleMusicClient(self.config)
        with patch.object(client.session, "post") as mock_post:
            count, failed = client.add_tracks_to_playlist("p.12345", [])
            self.assertEqual(count, 0)
            self.assertEqual(failed, [])
            mock_post.assert_not_called()

    def test_csv_parse_quotes_and_commas(self):
        """Verify that CSV with quotes and embedded commas parses correctly."""
        csv_content = (
            'title,artists,album\n'
            '"Hello, World","Adele, Greg Kurstin","25"\n'
            '"Theme from ""New York, New York""","Frank Sinatra","Trilogy"\n'
        )
        csv_file = Path(self.temp_dir.name) / "test.csv"
        csv_file.write_text(csv_content, encoding="utf-8")

        extractor = LocalFileExtractor()
        playlist = extractor.extract(str(csv_file))

        self.assertEqual(len(playlist.tracks), 2)
        self.assertEqual(playlist.tracks[0].title, "Hello, World")
        self.assertIn("Adele, Greg Kurstin", playlist.tracks[0].artists)
        self.assertEqual(playlist.tracks[1].title, 'Theme from "New York, New York"')

    def test_qq_metadata_fallback_keys(self):
        """Verify that QQMusicExtractor handles fallback keys (title, singer_name, album_name)."""
        extractor = QQMusicExtractor()
        fake_api_response = {
            "cdlist": [{
                "dissname": "测试歌单",
                "songlist": [
                    {
                        "title": "晴天",
                        "singer_name": "周杰伦",
                        "album_name": "叶惠美",
                        "interval": 269,
                        "songmid": "0039MnYb0qxYAc",
                        "isrc": "CN-A01-03-00123",
                    }
                ]
            }]
        }

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = fake_api_response
            mock_get.return_value = mock_resp

            playlist = extractor.extract("1234567890")
            self.assertEqual(len(playlist.tracks), 1)
            t = playlist.tracks[0]
            self.assertEqual(t.title, "晴天")
            self.assertEqual(t.artists, ["周杰伦"])
            self.assertEqual(t.album, "叶惠美")
            self.assertEqual(t.isrc, "CN-A01-03-00123")
            self.assertEqual(t.duration_ms, 269000)

    def test_auth_developer_token_env_override(self):
        """Verify that APPLE_MUSIC_DEVELOPER_TOKEN env var overrides any config or scraping."""
        auth = AppleMusicAuth(self.config)
        test_env_token = "ey_test_environment_token_123456789"
        with patch.dict(os.environ, {"APPLE_MUSIC_DEVELOPER_TOKEN": test_env_token}):
            tok = auth.get_developer_token(force_refresh=True)
            self.assertEqual(tok, test_env_token)


if __name__ == "__main__":
    unittest.main()
