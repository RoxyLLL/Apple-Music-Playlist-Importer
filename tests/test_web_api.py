"""
Regression and offline integration tests for the Web API module.
Covers:
- Version consistency between applemusic and FastAPI metadata.
- App token security middleware verification.
- Offline regression for /api/search-track (query sanitation with re, bracket fallback).
- Cache clearance semantics (/api/cache/clear clears expired only).
"""

import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import applemusic
from applemusic.models import AppleMusicTrack, CatalogSearchOutcome
from applemusic.web.app import SESSION_API_TOKEN, app


class TestWebApi(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.auth_headers = {"X-App-Token": SESSION_API_TOKEN}

    def test_version_consistency(self):
        """Ensure __version__ is 2.0.5 and FastAPI version matches."""
        self.assertEqual(applemusic.__version__, "2.0.5")
        self.assertEqual(app.version, "2.0.5")
        self.assertEqual(app.version, applemusic.__version__)

    def test_app_token_middleware_enforcement(self):
        """API routes must reject requests without valid X-App-Token."""
        resp = self.client.post("/api/search-track", json={"query": "晴天"})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json().get("success"))

    @patch("applemusic.web.app.get_shared_engine")
    def test_search_track_with_punctuation_and_scoring(self, mock_get_shared):
        """
        Verify /api/search-track runs cleanly with punctuation/brackets in query,
        exercising re.sub without NameError, and calculates scores when source info is given.
        """
        mock_client = MagicMock()
        mock_client.config.storefront = "cn"
        mock_engine = MagicMock()
        mock_get_shared.return_value = (mock_client, mock_engine)

        mock_client.search_catalog.return_value = CatalogSearchOutcome(
            kind="ok",
            tracks=[
                AppleMusicTrack(
                    id="10001",
                    title="晴天",
                    artists=["周杰伦"],
                    album="叶惠美",
                    duration_ms=269000,
                    storefront="cn",
                )
            ],
        )

        resp = self.client.post(
            "/api/search-track",
            headers=self.auth_headers,
            json={
                "query": "周杰伦 - 晴天 (Live版) [2004] 《无与伦比》",
                "source_title": "晴天",
                "source_artists": ["周杰伦"],
                "source_album": "叶惠美",
                "source_duration_ms": 269000,
            },
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["results"]), 1)
        res_item = data["results"][0]
        self.assertEqual(res_item["track"]["id"], "10001")
        self.assertGreater(res_item["score"], 0.8)

        # Verify search_catalog was called with cleaned query
        called_query = mock_client.search_catalog.call_args[0][0]
        self.assertNotIn("(", called_query)
        self.assertNotIn(")", called_query)
        self.assertNotIn("《", called_query)
        self.assertNotIn("》", called_query)

    @patch("applemusic.web.app.get_shared_engine")
    def test_search_track_bracket_fallback(self, mock_get_shared):
        """
        Verify /api/search-track falls back to bracketed text when initial query has no hits,
        exercising re.findall and re.sub.
        1. search_catalog("Unmatched Main 晴天", "cn", 8) -> no_hits
        2. search_catalog("晴天", "cn", 8) -> ok
        Assert exactly these two calls in order, and response uses the second outcome.
        """
        mock_client = MagicMock()
        mock_client.config.storefront = "cn"
        mock_engine = MagicMock()
        mock_get_shared.return_value = (mock_client, mock_engine)

        def mock_search(query, storefront="cn", limit=8):
            if query == "Unmatched Main 晴天":
                return CatalogSearchOutcome(kind="no_hits")
            elif query == "晴天":
                return CatalogSearchOutcome(
                    kind="ok",
                    tracks=[
                        AppleMusicTrack(
                            id="10002",
                            title="晴天",
                            artists=["周杰伦"],
                            album="叶惠美",
                            storefront="cn",
                        )
                    ],
                )
            return CatalogSearchOutcome(kind="no_hits")

        mock_client.search_catalog.side_effect = mock_search

        resp = self.client.post(
            "/api/search-track",
            headers=self.auth_headers,
            json={"query": "Unmatched Main (晴天)"},
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["track"]["id"], "10002")

        # Verify exactly two calls were made in order with exact arguments
        self.assertEqual(mock_client.search_catalog.call_count, 2)
        mock_client.search_catalog.assert_has_calls([
            unittest.mock.call("Unmatched Main 晴天", "cn", 8),
            unittest.mock.call("晴天", "cn", 8),
        ])

    @patch("applemusic.web.app.get_shared_engine")
    def test_cache_clear_semantics(self, mock_get_shared):
        """Verify /api/cache/clear only clears expired cache and returns clear message."""
        mock_client = MagicMock()
        mock_engine = MagicMock()
        mock_client.persistent_cache.clear_expired.return_value = (5, 10)
        mock_client.persistent_cache.get_stats.return_value = {"catalog_count": 50, "match_count": 100}
        mock_get_shared.return_value = (mock_client, mock_engine)

        resp = self.client.post("/api/cache/clear", headers=self.auth_headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["cleared_catalog"], 5)
        self.assertEqual(data["cleared_match"], 10)
        self.assertIn("已清理过期缓存", data.get("message", ""))
        mock_client.persistent_cache.clear_expired.assert_called_once()


if __name__ == "__main__":
    unittest.main()
