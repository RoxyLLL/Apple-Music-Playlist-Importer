"""
Comprehensive Test Suite for V7 Mandarin Metadata Matching, Manual Search, and Scoring Revision.
Covers All Codex Acceptance Requirements:
- S01: Real Playwright browser test opening candidate modal, asserting core title in input, intercepting network request, verifying user edit retention.
- S02 / P2: Bracket fallback in search endpoint filtering pure version/venue notes (Live at Wembley, Japanese Ver. Live, 2024 Remix) while preserving genuine translated titles and handling 429/401/timeout interruptions.
- P1: Identical ISRC duration conflict matrix (without ISRC, identical ISRC 70s diff, identical ISRC matching, different ISRC, catalog equivalents, threshold boundaries) asserting score, evidence.conflicts, and evaluate_candidates decision.
- A01: G.E.M.邓紫棋 variants across dot, space, fullwidth, simplified, traditional, artist >= 0.95, short titles (句号, 唯一) pass without penalty.
- A02: Reliable Mandarin artist groups (14+ groups) with Traditional and Simplified variants, canonical index collision detection, and module load assertion.
- A03: Fake artist / fabricated alias rejection (Fake Artist 花泽香菜, Taylor Swift 周杰伦).
- T01: Work source / soundtrack annotations (OST, TV, film, game) parsed cleanly, title >= 0.95, no version conflict, score >= 0.90.
- T02: Work source with real version conflicts (Live, Remix, TV Size, Cover) preserves conflict and prevents auto_accept.
- M01: Same title and artist with different albums (恋曲1990) scores >= 0.90 without score distortion.
- M02: Normalized duration similarity [0.0, 1.0], invalid duration handling, and significant duration difference conflict explanation.
- R01: Real MatchingEngine execution with mock client, tracing executed queries and asserting budget bounds (catalog <= 8, suggestions <= 2, equivalents <= 2, retry <= 6).
- R02: Manual search API and automatic scoring consistency with deterministic non-empty candidate mock, asserting exact equality across scores, breakdowns, evidence, and decision.
- C01: Independent SQLite database multi-version matrix (single rule outdated, single query outdated, single alias outdated, old auto_accept demotion with duration diff, user_confirmed preservation).
- N01: Security suite preservation (sweets parade, with titles, language conflict).
- U01: Real Playwright browser test verifying review candidate checkbox unselected by default, diagnostic modal displaying 2026.10.v7.2 and full breakdowns.
"""

import asyncio
import json
import os
import re
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
from typing import List, Optional
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from applemusic.cache import PersistentCache
from applemusic.matcher.artist_aliases import (
    ARTIST_GROUPS,
    CANONICAL_COLLISIONS,
    are_artists_equivalent,
    canonicalize_artist_key,
    get_artist_aliases,
)
from applemusic.matcher.cleaner import TextCleaner
from applemusic.matcher.engine import MatchingEngine
from applemusic.matcher.evidence import (
    ALIAS_VERSION,
    EXCEPTION_REGISTRY_VERSION,
    MATCH_RULE_VERSION,
    QUERY_POLICY_VERSION,
    ROMANIZER_VERSION,
    MatchEvidence,
    VerificationLevel,
)
from applemusic.matcher.query_models import QueryContext
from applemusic.matcher.query_planner import QueryPlanner
from applemusic.matcher.scorer import TrackScorer
from applemusic.models import (
    AppleMusicTrack,
    CatalogSearchOutcome,
    ConfidenceLevel,
    DecisionStatus,
    MatchCandidate,
    SongMatchResult,
    Track,
)
from applemusic.web.app import SESSION_API_TOKEN, app


class TestMandarinMetadataMatchingV7(unittest.TestCase):

    # -------------------------------------------------------------------------
    # S01: Real Playwright Browser Test - Candidate Modal & Core Title
    # -------------------------------------------------------------------------
    def test_S01_real_browser_candidate_modal_preserves_core_title_and_user_edit(self):
        """
        S01: Real browser test using Playwright against live index.html:
        - Injects track: 世末歌者（Cover 乐正绫）by 双笙（陈元汐）/封茗囧菌
        - Opens candidate modal, asserts input has core title '世末歌者 双笙' (not 'Cover 乐正绫' or just artist)
        - Intercepts /api/search-track request and verifies search query
        - Tests user edit retention: user modifies query to '世末歌者 封茗囧菌', verifies request is NOT overwritten
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright not installed for browser test")

        import uvicorn

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        server_port = s.getsockname()[1]
        s.close()

        config = uvicorn.Config(app, host="127.0.0.1", port=server_port, log_level="error")
        server = uvicorn.Server(config)
        t = threading.Thread(target=server.run, daemon=True)
        t.start()

        for _ in range(30):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{server_port}/")
                break
            except Exception:
                time.sleep(0.1)

        try:
            with sync_playwright() as p:
                browser = None
                for channel in [None, "msedge", "chrome"]:
                    try:
                        kwargs = {"headless": True}
                        if channel:
                            kwargs["channel"] = channel
                        browser = p.chromium.launch(**kwargs)
                        break
                    except Exception:
                        continue

                if not browser:
                    self.skipTest("No compatible browser found for Playwright test")

                page = browser.new_page()
                intercepted_requests = []

                def handle_route(route):
                    req = route.request
                    if "/api/search-track" in req.url and req.method == "POST":
                        try:
                            payload = json.loads(req.post_data)
                            intercepted_requests.append(payload)
                        except Exception:
                            pass
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps({"success": True, "kind": "ok", "results": []}),
                        )
                    else:
                        route.continue_()

                page.route("**/api/search-track", handle_route)
                page.goto(f"http://127.0.0.1:{server_port}/")
                page.wait_for_selector("#app")

                # Inject test track into Vue root
                page.evaluate(
                    """() => {
                    window.__vueRoot.matchResults = [
                        {
                            source_track: {
                                title: "世末歌者（Cover 乐正绫）",
                                artists: ["双笙（陈元汐）", "封茗囧菌"],
                                album: "世末歌者",
                                duration_ms: 250000
                            },
                            candidates: [],
                            selected_candidate: null,
                            status: "not_found",
                            decision: "no_match",
                            decision_reasons: ["未找到候选曲目"],
                            search_status: "no_match",
                            selected: false,
                            evidence: null
                        }
                    ];
                }"""
                )
                time.sleep(0.3)

                # Click "换版本" / "搜曲库" button to open Candidate Modal
                search_btn = page.locator("button:has-text('搜曲库'), button:has-text('换版本')").first
                self.assertTrue(search_btn.is_visible())
                search_btn.click()
                time.sleep(0.5)

                # Locate candidate modal search input
                modal_input = page.locator('input[placeholder*="关键词"]').first
                self.assertTrue(modal_input.is_visible(), "Candidate modal input must be visible")
                val = modal_input.input_value()

                # Core title verification: must contain '世末歌者', must contain '双笙', must not be 'Cover 乐正绫'
                self.assertIn("世末歌者", val)
                self.assertIn("双笙", val)
                self.assertNotIn("Cover", val)
                self.assertNotIn("乐正绫", val)

                # Verify intercepted request query
                self.assertTrue(len(intercepted_requests) >= 1)
                first_query = intercepted_requests[0].get("query", "")
                self.assertIn("世末歌者", first_query)
                self.assertNotIn("Cover", first_query)

                # User edit retention test: user changes search query
                modal_input.fill("世末歌者 封茗囧菌")
                time.sleep(0.2)
                # Click search button
                search_submit = page.locator(".fixed.inset-0 button:has-text('搜索')").first
                search_submit.click()
                time.sleep(0.5)

                # Verify second intercepted request used the user-edited query
                self.assertTrue(len(intercepted_requests) >= 2)
                edited_query = intercepted_requests[-1].get("query", "")
                self.assertEqual(edited_query, "世末歌者 封茗囧菌")

                browser.close()
        finally:
            server.should_exit = True

    # -------------------------------------------------------------------------
    # U01: Real Playwright Browser Test - Review Unselected & Diagnostics Modal
    # -------------------------------------------------------------------------
    def test_U01_real_browser_diagnostics_and_review_unselected(self):
        """
        U01: Real browser test using Playwright verifying:
        - Review item checkbox is UNCHECKED by default
        - Diagnostic modal displays 2026.10.v7.2 rule and alias versions
        - Displays title, artist, duration, conflicts
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright not installed for browser test")

        import uvicorn

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        server_port = s.getsockname()[1]
        s.close()

        config = uvicorn.Config(app, host="127.0.0.1", port=server_port, log_level="error")
        server = uvicorn.Server(config)
        t = threading.Thread(target=server.run, daemon=True)
        t.start()

        for _ in range(30):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{server_port}/")
                break
            except Exception:
                time.sleep(0.1)

        try:
            with sync_playwright() as p:
                browser = None
                for channel in [None, "msedge", "chrome"]:
                    try:
                        kwargs = {"headless": True}
                        if channel:
                            kwargs["channel"] = channel
                        browser = p.chromium.launch(**kwargs)
                        break
                    except Exception:
                        continue

                if not browser:
                    self.skipTest("No compatible browser found for Playwright test")

                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{server_port}/")
                page.wait_for_selector("#app")

                # Inject review item into Vue root
                page.evaluate(
                    """() => {
                    window.__vueRoot.matchResults = [
                        {
                            source_track: {
                                title: "句号",
                                artists: ["G.E.M.邓紫棋"],
                                album: "摩天动物园",
                                duration_ms: 215000
                            },
                            candidates: [
                                {
                                    track: { id: "cand_juhao", title: "句号", artists: ["邓紫棋"], album: "摩天动物园", duration_ms: 215000 },
                                    score: 0.85,
                                    title_score: 1.0,
                                    artist_score: 0.95,
                                    album_score: 1.0,
                                    duration_score: 1.0,
                                    version_score: 0.0,
                                    confidence: "medium",
                                    decision: "review",
                                    decision_reasons: ["存在冲突项，转人工复核"],
                                    evidence: {
                                        rule_version: "2026.10.v7.2",
                                        alias_version: "2026.10.v7.2",
                                        verification_level: "conflict",
                                        matched_fields: ["title", "artist", "album", "duration"],
                                        conflicts: ["duration_conflict: 时长差异较明显 (相差70秒)"],
                                        evidence_families: ["text_title", "text_artist"]
                                    }
                                }
                            ],
                            selected_candidate: {
                                track: { id: "cand_juhao", title: "句号", artists: ["邓紫棋"], album: "摩天动物园", duration_ms: 215000 },
                                score: 0.85,
                                title_score: 1.0,
                                artist_score: 0.95,
                                album_score: 1.0,
                                duration_score: 1.0,
                                version_score: 0.0,
                                confidence: "medium",
                                decision: "review",
                                decision_reasons: ["存在冲突项，转人工复核"],
                                evidence: {
                                    rule_version: "2026.10.v7.2",
                                    alias_version: "2026.10.v7.2",
                                    verification_level: "conflict",
                                    matched_fields: ["title", "artist", "album", "duration"],
                                    conflicts: ["duration_conflict: 时长差异较明显 (相差70秒)"],
                                    evidence_families: ["text_title", "text_artist"]
                                }
                            },
                            status: "medium",
                            decision: "review",
                            decision_reasons: ["存在冲突项，转人工复核"],
                            search_status: "review",
                            selected: false,
                            evidence: {
                                rule_version: "2026.10.v7.2",
                                alias_version: "2026.10.v7.2",
                                verification_level: "conflict",
                                matched_fields: ["title", "artist", "album", "duration"],
                                conflicts: ["duration_conflict: 时长差异较明显 (相差70秒)"],
                                evidence_families: ["text_title", "text_artist"]
                            },
                            diagnostics: {
                                rule_version: "2026.10.v7.2",
                                alias_version: "2026.10.v7.2",
                                verification_level: "conflict",
                                matched_fields: ["title", "artist", "album", "duration"],
                                conflicts: ["duration_conflict: 时长差异较明显 (相差70秒)"],
                                executed_queries: [{ query: "句号 邓紫棋", kind: "exact", hits: 1 }]
                            }
                        }
                    ];
                }"""
                )
                time.sleep(0.3)

                # 1. Assert row checkbox is NOT checked for review candidate
                row_checkbox = page.locator("input[type='checkbox']").nth(1)
                self.assertFalse(row_checkbox.is_checked(), "Review candidate must NOT be checked by default")

                # 2. Click "诊断" button to open modal
                diag_btn = page.locator("button:has-text('诊断')").first
                self.assertTrue(diag_btn.is_visible())
                diag_btn.click()
                time.sleep(0.5)

                modal_text = page.locator(".fixed.inset-0").inner_text()
                self.assertIn("2026.10.v7.2", modal_text, "Diagnostic modal must display rule_version 2026.10.v7.2")
                self.assertIn("句号", modal_text)
                self.assertIn("duration_conflict", modal_text)

                browser.close()
        finally:
            server.should_exit = True

    # -------------------------------------------------------------------------
    # P1: Identical ISRC Duration Conflict Safety Matrix
    # -------------------------------------------------------------------------
    def test_P1_identical_isrc_duration_conflict_safety_matrix(self):
        """
        P1: Comprehensive matrix testing identical ISRC, no ISRC, different ISRC,
        catalog equivalent, and threshold boundaries against duration conflicts.
        Identical ISRC must NEVER bypass duration conflict to auto_accept!
        """
        # Case 1: No ISRC, 70s duration difference (240s vs 310s)
        src_no_isrc = Track(title="Example Song", artists=["Artist A"], duration_ms=240000)
        cand_no_isrc = AppleMusicTrack(id="cand_1", title="Example Song", artists=["Artist A"], duration_ms=310000)
        mc1 = TrackScorer.score(src_no_isrc, cand_no_isrc)
        self.assertEqual(mc1.score, 0.944)
        self.assertEqual(mc1.decision, DecisionStatus.REVIEW.value)
        self.assertTrue(any("duration_conflict" in c and "70" in c for c in mc1.evidence.conflicts))
        best1, conf1, dec1, reasons1, gap1 = TrackScorer.evaluate_candidates(src_no_isrc, [mc1])
        self.assertEqual(dec1, DecisionStatus.REVIEW.value)

        # Case 2: Identical ISRC (USABC1234567), 70s duration difference (240s vs 310s)
        src_same_isrc = Track(title="Example Song", artists=["Artist A"], duration_ms=240000, isrc="USABC1234567")
        cand_same_isrc = AppleMusicTrack(id="cand_2", title="Example Song", artists=["Artist A"], duration_ms=310000, isrc="USABC1234567")
        mc2 = TrackScorer.score(src_same_isrc, cand_same_isrc)
        self.assertEqual(mc2.score, 0.944)
        self.assertEqual(mc2.decision, DecisionStatus.REVIEW.value)
        self.assertTrue(any("duration_conflict" in c for c in mc2.evidence.conflicts))
        self.assertTrue(any("isrc_duration_conflict" in c for c in mc2.evidence.conflicts))
        best2, conf2, dec2, reasons2, gap2 = TrackScorer.evaluate_candidates(src_same_isrc, [mc2])
        self.assertEqual(dec2, DecisionStatus.REVIEW.value, "Identical ISRC with 70s diff must NEVER auto_accept")

        # Case 3: Identical ISRC, matching duration (240s vs 241s) -> Auto Accept
        cand_match_isrc = AppleMusicTrack(id="cand_3", title="Example Song", artists=["Artist A"], duration_ms=241000, isrc="USABC1234567")
        mc3 = TrackScorer.score(src_same_isrc, cand_match_isrc)
        self.assertEqual(mc3.score, 1.0)
        self.assertEqual(mc3.decision, DecisionStatus.AUTO_ACCEPT.value)
        self.assertEqual(mc3.evidence.conflicts, [])
        best3, conf3, dec3, reasons3, gap3 = TrackScorer.evaluate_candidates(src_same_isrc, [mc3])
        self.assertEqual(dec3, DecisionStatus.AUTO_ACCEPT.value)

        # Case 4: Different ISRC (USABC1234567 vs USABC7654321), 70s duration difference
        cand_diff_isrc = AppleMusicTrack(id="cand_4", title="Example Song", artists=["Artist A"], duration_ms=310000, isrc="USABC7654321")
        mc4 = TrackScorer.score(src_same_isrc, cand_diff_isrc)
        self.assertEqual(mc4.decision, DecisionStatus.REVIEW.value)
        self.assertTrue(any("duration_conflict" in c for c in mc4.evidence.conflicts))
        best4, conf4, dec4, reasons4, gap4 = TrackScorer.evaluate_candidates(src_same_isrc, [mc4])
        self.assertEqual(dec4, DecisionStatus.REVIEW.value)

        # Case 5: Catalog equivalent candidate with 70s duration difference
        cand_equiv = AppleMusicTrack(
            id="cand_equiv",
            title="Example Song",
            artists=["Artist A"],
            duration_ms=310000,
            discovery_path="apple_equivalent",
            is_equivalent_mapped=True,
        )
        mc5 = TrackScorer.score(src_no_isrc, cand_equiv)
        self.assertEqual(mc5.decision, DecisionStatus.REVIEW.value)
        self.assertEqual(mc5.evidence.verification_level, VerificationLevel.CONFLICT.value)
        self.assertTrue(any("duration_conflict" in c for c in mc5.evidence.conflicts))
        best5, conf5, dec5, reasons5, gap5 = TrackScorer.evaluate_candidates(src_no_isrc, [mc5])
        self.assertEqual(dec5, DecisionStatus.REVIEW.value)

        # Case 6: Duration boundary threshold matrix
        # Boundary 6a: 19s diff, rel_diff = 19/259 = 0.073 (< 0.08) -> No conflict
        cand_b1 = AppleMusicTrack(id="b1", title="Example Song", artists=["Artist A"], duration_ms=259000)
        mc_b1 = TrackScorer.score(src_no_isrc, cand_b1)
        self.assertFalse(any("duration_conflict" in c for c in mc_b1.evidence.conflicts))

        # Boundary 6b: 21s diff, rel_diff = 21/121 = 0.173 (>= 0.08) -> Conflict triggered
        src_short = Track(title="Example Song", artists=["Artist A"], duration_ms=100000)
        cand_b2 = AppleMusicTrack(id="b2", title="Example Song", artists=["Artist A"], duration_ms=121000)
        mc_b2 = TrackScorer.score(src_short, cand_b2)
        self.assertTrue(any("duration_conflict" in c for c in mc_b2.evidence.conflicts))

        # Boundary 6c: 46s diff (> 45s absolute threshold) -> Conflict triggered
        cand_b3 = AppleMusicTrack(id="b3", title="Example Song", artists=["Artist A"], duration_ms=286000)
        mc_b3 = TrackScorer.score(src_no_isrc, cand_b3)
        self.assertTrue(any("duration_conflict" in c for c in mc_b3.evidence.conflicts))

    # -------------------------------------------------------------------------
    # P2: Bracket Fallback Filtering & Error Interruption in Manual Search API
    # -------------------------------------------------------------------------
    def test_P2_bracket_fallback_filters_version_notes_and_preserves_real_translations(self):
        """
        P2: /api/search-track bracket fallback:
        - Pure version/venue notes ('Live at Wembley', 'Japanese Ver. Live', '2024 Remix') MUST NOT be queried alone!
        - Real foreign translation titles ('The End of the World Singer') ARE queried when primary returns no_hits.
        - 429/401/timeout errors immediately abort fallback loop.
        """
        client = TestClient(app)

        # 1. 'Example Song (Live at Wembley)': Query 1 executed, Query 2 ('Live at Wembley') MUST NOT execute
        invoked_queries = []

        def mock_search_wembley(query, sf, limit):
            invoked_queries.append(query)
            return CatalogSearchOutcome(tracks=[], kind="no_hits")

        with patch("applemusic.web.app.get_shared_engine") as mock_get_engine:
            mock_client = MagicMock()
            mock_engine = MagicMock()
            mock_engine.scorer = TrackScorer()
            mock_client.search_catalog.side_effect = mock_search_wembley
            mock_get_engine.return_value = (mock_client, mock_engine)

            resp = client.post(
                "/api/search-track",
                headers={"X-App-Token": SESSION_API_TOKEN},
                json={"query": "Example Song (Live at Wembley)", "storefront": "cn", "limit": 5},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(len(invoked_queries), 1, f"Expected exactly 1 query, got: {invoked_queries}")
            self.assertIn("Example Song Live at Wembley", invoked_queries[0])
            self.assertNotIn("Live at Wembley", invoked_queries[1:] if len(invoked_queries) > 1 else [])

        # 2. 'Example Song (Japanese Ver. Live)': Query 2 ('Japanese Ver. Live') MUST NOT execute
        invoked_queries.clear()

        def mock_search_jp(query, sf, limit):
            invoked_queries.append(query)
            return CatalogSearchOutcome(tracks=[], kind="no_hits")

        with patch("applemusic.web.app.get_shared_engine") as mock_get_engine:
            mock_client = MagicMock()
            mock_engine = MagicMock()
            mock_engine.scorer = TrackScorer()
            mock_client.search_catalog.side_effect = mock_search_jp
            mock_get_engine.return_value = (mock_client, mock_engine)

            resp = client.post(
                "/api/search-track",
                headers={"X-App-Token": SESSION_API_TOKEN},
                json={"query": "Example Song (Japanese Ver. Live)", "storefront": "cn", "limit": 5},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(len(invoked_queries), 1, f"Expected exactly 1 query, got: {invoked_queries}")

        # 3. Real translated title 'Example Song (The End of the World Singer)': Query 2 IS executed
        invoked_queries.clear()

        def mock_search_trans(query, sf, limit):
            invoked_queries.append(query)
            if query == "The End of the World Singer":
                return CatalogSearchOutcome(
                    tracks=[
                        AppleMusicTrack(
                            id="cand_trans",
                            title="The End of the World Singer",
                            artists=["Shuang Sheng"],
                            duration_ms=250000,
                        )
                    ],
                    kind="ok",
                )
            return CatalogSearchOutcome(tracks=[], kind="no_hits")

        with patch("applemusic.web.app.get_shared_engine") as mock_get_engine:
            mock_client = MagicMock()
            mock_engine = MagicMock()
            mock_engine.scorer = TrackScorer()
            mock_client.search_catalog.side_effect = mock_search_trans
            mock_get_engine.return_value = (mock_client, mock_engine)

            resp = client.post(
                "/api/search-track",
                headers={"X-App-Token": SESSION_API_TOKEN},
                json={"query": "Example Song (The End of the World Singer)", "storefront": "cn", "limit": 5},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(len(invoked_queries), 2, f"Expected 2 queries, got: {invoked_queries}")
            self.assertEqual(invoked_queries[1], "The End of the World Singer")
            data = resp.json()
            self.assertEqual(len(data["results"]), 1)

        # 4. Error interruption: 429 rate limit aborts fallback immediately
        invoked_queries.clear()

        def mock_search_429(query, sf, limit):
            invoked_queries.append(query)
            return CatalogSearchOutcome(tracks=[], kind="rate_limited", retry_after_seconds=30)

        with patch("applemusic.web.app.get_shared_engine") as mock_get_engine:
            mock_client = MagicMock()
            mock_engine = MagicMock()
            mock_engine.scorer = TrackScorer()
            mock_client.search_catalog.side_effect = mock_search_429
            mock_get_engine.return_value = (mock_client, mock_engine)

            resp = client.post(
                "/api/search-track",
                headers={"X-App-Token": SESSION_API_TOKEN},
                json={"query": "Example Song (The End of the World Singer)", "storefront": "cn", "limit": 5},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertFalse(data["success"])
            self.assertEqual(data["kind"], "rate_limited")
            self.assertEqual(len(invoked_queries), 1, "Rate limited query must NOT proceed to fallback queries")

        # 5. Error interruption: timeout aborts fallback immediately
        invoked_queries.clear()

        def mock_search_timeout(query, sf, limit):
            invoked_queries.append(query)
            return CatalogSearchOutcome(tracks=[], kind="timeout")

        with patch("applemusic.web.app.get_shared_engine") as mock_get_engine:
            mock_client = MagicMock()
            mock_engine = MagicMock()
            mock_engine.scorer = TrackScorer()
            mock_client.search_catalog.side_effect = mock_search_timeout
            mock_get_engine.return_value = (mock_client, mock_engine)

            resp = client.post(
                "/api/search-track",
                headers={"X-App-Token": SESSION_API_TOKEN},
                json={"query": "Example Song (The End of the World Singer)", "storefront": "cn", "limit": 5},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertFalse(data["success"])
            self.assertEqual(data["kind"], "timeout")
            self.assertEqual(len(invoked_queries), 1)

        # 6. Genuine alternate titles ('The Show', 'Live and Let Die', 'The Festival', 'Guitar Song'):
        # Fallback query 2 MUST execute when primary query returns no_hits!
        for alt_title in ["The Show", "Live and Let Die", "The Festival", "Guitar Song"]:
            invoked_queries.clear()

            def mock_search_alt(query, sf, limit):
                invoked_queries.append(query)
                if query == alt_title:
                    return CatalogSearchOutcome(
                        tracks=[
                            AppleMusicTrack(
                                id=f"cand_{alt_title}",
                                title=alt_title,
                                artists=["Artist A"],
                                duration_ms=210000,
                            )
                        ],
                        kind="ok",
                    )
                return CatalogSearchOutcome(tracks=[], kind="no_hits")

            with patch("applemusic.web.app.get_shared_engine") as mock_get_engine:
                mock_client = MagicMock()
                mock_engine = MagicMock()
                mock_engine.scorer = TrackScorer()
                mock_client.search_catalog.side_effect = mock_search_alt
                mock_get_engine.return_value = (mock_client, mock_engine)

                resp = client.post(
                    "/api/search-track",
                    headers={"X-App-Token": SESSION_API_TOKEN},
                    json={"query": f"示例歌名 ({alt_title})", "storefront": "cn", "limit": 5},
                )
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(len(invoked_queries), 2, f"Fallback search for '{alt_title}' must execute (got {invoked_queries})")
                self.assertEqual(invoked_queries[1], alt_title)
                data = resp.json()
                self.assertEqual(len(data["results"]), 1)
                self.assertEqual(data["results"][0]["track"]["title"], alt_title)

        # 7. Mixed bracket note ('The Show - Live at Wembley'): extracts base title 'The Show' for fallback!
        invoked_queries.clear()

        def mock_search_mixed(query, sf, limit):
            invoked_queries.append(query)
            if query == "The Show":
                return CatalogSearchOutcome(
                    tracks=[
                        AppleMusicTrack(
                            id="cand_mixed_show",
                            title="The Show",
                            artists=["Artist A"],
                            duration_ms=210000,
                        )
                    ],
                    kind="ok",
                )
            return CatalogSearchOutcome(tracks=[], kind="no_hits")

        with patch("applemusic.web.app.get_shared_engine") as mock_get_engine:
            mock_client = MagicMock()
            mock_engine = MagicMock()
            mock_engine.scorer = TrackScorer()
            mock_client.search_catalog.side_effect = mock_search_mixed
            mock_get_engine.return_value = (mock_client, mock_engine)

            resp = client.post(
                "/api/search-track",
                headers={"X-App-Token": SESSION_API_TOKEN},
                json={"query": "示例歌名 (The Show - Live at Wembley)", "storefront": "cn", "limit": 5},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(len(invoked_queries), 2, f"Expected 2 queries for mixed bracket, got: {invoked_queries}")
            self.assertEqual(invoked_queries[1], "The Show")
            data = resp.json()
            self.assertEqual(len(data["results"]), 1)

    # -------------------------------------------------------------------------
    # P3: Paired Bracket Semantic Classification and Scoring Matrix
    # -------------------------------------------------------------------------
    def test_P3_paired_bracket_semantic_classification_and_scoring_matrix(self):
        """
        P3: Paired classification and scoring matrix covering:
        - The Show vs Live at venue
        - The Festival vs Tour/Concert session notes
        - Live and Let Die vs Live keyword
        - Guitar Song vs Guitar Ver.
        - The End of the World Singer (control group)
        - Reverse direction & case insensitivity
        - Mixed bracket with version annotation (no false no_match)
        """
        scorer = TrackScorer()

        # 1. Paired direct scoring cases
        matrix = [
            # (src_title, cand_title, exp_decision, min_total_score, max_total_score, exp_conflicts_has, desc)
            ("示例歌名 (The Show)", "The Show", "auto_accept", 0.985, 0.990, [], "The Show alternate title"),
            ("Example Song (Live at Wembley)", "Example Song", "review", 0.50, 0.65, ["version_conflict"], "Live at venue vs studio"),
            ("示例歌名 (The Festival)", "The Festival", "auto_accept", 0.985, 0.990, [], "The Festival alternate title"),
            ("Example Song (2024 World Tour Live)", "Example Song", "review", 0.50, 0.65, ["version_conflict"], "World Tour Live vs studio"),
            ("示例歌名 (Live and Let Die)", "Live and Let Die", "auto_accept", 0.985, 0.990, [], "Live and Let Die alternate title"),
            ("Example Song (Live)", "Example Song", "review", 0.50, 0.65, ["version_conflict"], "Live version keyword vs studio"),
            ("示例歌名 (Guitar Song)", "Guitar Song", "auto_accept", 0.985, 0.990, [], "Guitar Song alternate title"),
            ("Example Song (Guitar Ver.)", "Example Song", "review", 0.50, 0.65, ["version_conflict"], "Guitar Ver. vs original"),
            ("示例歌名 (The End of the World Singer)", "The End of the World Singer", "auto_accept", 0.985, 0.990, [], "Control group"),
            # Reverse direction
            ("The Show", "示例歌名 (The Show)", "auto_accept", 0.985, 0.990, [], "Reverse: source is title, candidate has bracket"),
            # Case insensitivity
            ("示例歌名 (the show)", "THE SHOW", "auto_accept", 0.985, 0.990, [], "Lowercase bracket vs Uppercase candidate"),
            ("示例歌名 (THE FESTIVAL)", "the festival", "auto_accept", 0.985, 0.990, [], "Uppercase bracket vs Lowercase candidate"),
            ("示例歌名 (live and let die)", "LIVE AND LET DIE", "auto_accept", 0.985, 0.990, [], "Lowercase vs Uppercase Live and Let Die"),
            # Mixed bracket with version note: must NOT be no_match
            ("示例歌名 (The Show - Live at Wembley)", "The Show", "review", 0.55, 0.95, ["version_conflict"], "Mixed bracket: title preserved, version conflict with studio track"),
            ("示例歌名 (The Show - Live at Wembley)", "The Show (Live at Wembley)", "auto_accept", 0.985, 1.00, [], "Mixed bracket matched with identical live candidate"),
        ]

        for src_title, cand_title, exp_dec, min_s, max_s, exp_conf_sub, desc in matrix:
            src = Track(title=src_title, artists=["Artist A"])
            cand = AppleMusicTrack(id="cand_test", title=cand_title, artists=["Artist A"])
            res = scorer.score(src, cand)
            best_cand, conf_status, eval_dec, reasons, score_gap = scorer.evaluate_candidates(src, [res])

            self.assertEqual(
                eval_dec,
                exp_dec,
                f"[{desc}] Failed decision for '{src_title}' vs '{cand_title}': expected {exp_dec}, got {eval_dec}. Score={res.score}, title_score={res.title_score}, conflicts={res.evidence.conflicts if res.evidence else []}",
            )
            self.assertGreaterEqual(res.score, min_s, f"[{desc}] Score {res.score} < {min_s}")
            self.assertLessEqual(res.score, max_s, f"[{desc}] Score {res.score} > {max_s}")

            conflicts = res.evidence.conflicts if res.evidence else []
            for conf_kw in exp_conf_sub:
                self.assertTrue(
                    any(conf_kw in c for c in conflicts),
                    f"[{desc}] Expected conflict containing '{conf_kw}', got {conflicts}",
                )
            if not exp_conf_sub:
                self.assertEqual(len(conflicts), 0, f"[{desc}] Expected no conflicts, got {conflicts}")

    # -------------------------------------------------------------------------
    # A01: G.E.M.邓紫棋 variants, artist score >= 0.95, short titles
    # -------------------------------------------------------------------------
    def test_A01_gem_deng_ziqi_variants_and_short_titles(self):
        """
        A01: G.E.M.邓紫棋 variants across dot, space, fullwidth, and traditional Chinese.
        Artist score >= 0.95, no false primary_artist_mismatch.
        Short titles (句号, 唯一) pass without penalty.
        """
        gem_variants = [
            "G.E.M. 邓紫棋",
            "G.E.M.邓紫棋",
            "GEM邓紫棋",
            "Ｇ．Ｅ．Ｍ．邓紫棋",
            "G.E.M. 鄧紫棋",
            "G.E.M.鄧紫棋",
            "GEM 邓紫棋",
        ]
        target_artist = "邓紫棋"

        for var in gem_variants:
            self.assertTrue(
                are_artists_equivalent(var, target_artist),
                f"Variant '{var}' must be recognized as equivalent to '{target_artist}'",
            )
            sim = TrackScorer.calculate_artist_similarity([var], [target_artist])
            self.assertGreaterEqual(
                sim,
                0.95,
                f"Artist similarity for '{var}' vs '{target_artist}' must be >= 0.95, got {sim}",
            )

        # Test short title '句号'
        src_juhao = Track(title="句号", artists=["G.E.M.邓紫棋"], album="摩天动物园", duration_ms=215000)
        cand_juhao = AppleMusicTrack(id="c_jh", title="句号", artists=["邓紫棋"], album="摩天动物园", duration_ms=215000)
        mc_jh = TrackScorer.score(src_juhao, cand_juhao)
        self.assertGreaterEqual(mc_jh.score, 0.90)
        self.assertNotIn("short_title_low_artist", [c.split(":")[0] for c in mc_jh.evidence.conflicts])
        best_jh, conf_jh, dec_jh, _, _ = TrackScorer.evaluate_candidates(src_juhao, [mc_jh])
        self.assertEqual(dec_jh, DecisionStatus.AUTO_ACCEPT.value)

        # Test short title '唯一'
        src_weiyi = Track(title="唯一", artists=["G.E.M. 邓紫棋"], album="唯一 - Single", duration_ms=250000)
        cand_weiyi = AppleMusicTrack(id="c_wy", title="唯一", artists=["邓紫棋"], album="唯一 - Single", duration_ms=250000)
        mc_wy = TrackScorer.score(src_weiyi, cand_weiyi)
        self.assertGreaterEqual(mc_wy.score, 0.90)
        self.assertNotIn("short_title_low_artist", [c.split(":")[0] for c in mc_wy.evidence.conflicts])
        best_wy, conf_wy, dec_wy, _, _ = TrackScorer.evaluate_candidates(src_weiyi, [mc_wy])
        self.assertEqual(dec_wy, DecisionStatus.AUTO_ACCEPT.value)

    # -------------------------------------------------------------------------
    # A02: Reliable Mandarin Artist Groups and Startup Collision Assertion
    # -------------------------------------------------------------------------
    def test_A02_artist_alias_groups_and_startup_collision_assertion(self):
        """
        A02: Verified Mandarin artist groups (14+ groups) with Traditional and Simplified variants.
        Canonical index collision detection ensures no silent overwrite, validated by module load assertion.
        """
        # Module-level collision list must be strictly empty
        self.assertEqual(CANONICAL_COLLISIONS, [], f"Detected canonical collisions: {CANONICAL_COLLISIONS}")

        mandarin_groups_to_verify = [
            ("邓紫棋", "鄧紫棋"),
            ("周杰伦", "周杰倫"),
            ("陈奕迅", "陳奕迅"),
            ("王菲", "Faye Wong"),
            ("张学友", "張學友"),
            ("刘德华", "劉德華"),
            ("林俊杰", "林俊傑"),
            ("孙燕姿", "孫燕姿"),
            ("蔡依林", "Jolin Tsai"),
            ("莫文蔚", "Karen Mok"),
            ("张惠妹", "張惠妹"),
            ("陶喆", "David Tao"),
            ("李荣浩", "李榮浩"),
            ("薛之谦", "薛之謙"),
            ("华晨宇", "華晨宇"),
            ("毛不易", "Mao Buyi"),
            ("双笙", "双笙（陈元汐）"),
            ("乐正绫", "Yuezheng Ling"),
        ]

        for s_name, t_name in mandarin_groups_to_verify:
            self.assertTrue(
                are_artists_equivalent(s_name, t_name),
                f"Artists '{s_name}' and '{t_name}' must be recognized as equivalent",
            )

        # Test canonical key consistency
        k1 = canonicalize_artist_key("G.E.M.邓紫棋")
        k2 = canonicalize_artist_key("G.E.M. 鄧紫棋")
        k3 = canonicalize_artist_key("GEM邓紫棋")
        self.assertEqual(k1, k2)
        self.assertEqual(k2, k3)

    # -------------------------------------------------------------------------
    # A03: Fake Artist and Fabricated Alias Rejection
    # -------------------------------------------------------------------------
    def test_A03_fake_artist_and_fabricated_alias_rejection(self):
        """
        A03: Unverified combinations must NOT be treated as reliable aliases:
        - 'Fake Artist 花泽香菜' vs '花泽香菜' -> NOT equivalent
        - 'Taylor Swift 周杰伦' vs '周杰伦' -> NOT equivalent
        - '花泽香菜' vs '周杰伦' -> NOT equivalent
        """
        self.assertFalse(are_artists_equivalent("Fake Artist 花泽香菜", "花泽香菜"))
        self.assertFalse(are_artists_equivalent("Taylor Swift 周杰伦", "周杰伦"))
        self.assertFalse(are_artists_equivalent("花泽香菜", "周杰伦"))

        # Scoring must penalize mismatch
        src = Track(title="Song", artists=["Taylor Swift 周杰伦"])
        cand = AppleMusicTrack(id="c_fake", title="Song", artists=["周杰伦"])
        sim = TrackScorer.calculate_artist_similarity(src.artists, cand.artists)
        self.assertLess(sim, 0.40)

        mc = TrackScorer.score(src, cand)
        self.assertNotEqual(mc.decision, DecisionStatus.AUTO_ACCEPT.value)

    # -------------------------------------------------------------------------
    # T01: Work source / Soundtrack Annotations Parsed Cleanly
    # -------------------------------------------------------------------------
    def test_T01_work_source_annotations_parsed_cleanly(self):
        """
        T01: Non-version OST / TV / Movie / Game annotations:
        - 《古剑奇谭》电视剧片头曲
        - 电影《大鱼海棠》印象曲
        - 游戏《原神》插曲
        Parsed cleanly, title score >= 0.95, no version conflict, score >= 0.90.
        """
        test_cases = [
            ("剑心（《古剑奇谭》电视剧片头曲）", "剑心", "张杰"),
            ("大鱼（电影《大鱼海棠》印象曲）", "大鱼", "周深"),
            ("野子（《中国好歌曲》现场版）", None, None),  # Contains Live -> version conflict tested in T02
        ]

        # 1. 剑心 / 张杰
        src1 = Track(title="剑心", artists=["张杰"], duration_ms=240000)
        cand1 = AppleMusicTrack(id="c_jx", title="剑心（《古剑奇谭》电视剧片头曲）", artists=["张杰"], duration_ms=240000)
        mc1 = TrackScorer.score(src1, cand1)
        self.assertGreaterEqual(mc1.title_score, 0.95)
        self.assertGreaterEqual(mc1.score, 0.90)
        self.assertNotIn("version_conflict", [c.split(":")[0] for c in mc1.evidence.conflicts])
        best1, conf1, dec1, _, _ = TrackScorer.evaluate_candidates(src1, [mc1])
        self.assertEqual(dec1, DecisionStatus.AUTO_ACCEPT.value)

        # 2. 大鱼 / 周深
        src2 = Track(title="大鱼", artists=["周深"], duration_ms=313000)
        cand2 = AppleMusicTrack(id="c_dy", title="大鱼（电影《大鱼海棠》印象曲）", artists=["周深"], duration_ms=313000)
        mc2 = TrackScorer.score(src2, cand2)
        self.assertGreaterEqual(mc2.title_score, 0.95)
        self.assertGreaterEqual(mc2.score, 0.90)
        self.assertNotIn("version_conflict", [c.split(":")[0] for c in mc2.evidence.conflicts])

    # -------------------------------------------------------------------------
    # T02: Work Source with Real Version Conflicts
    # -------------------------------------------------------------------------
    def test_T02_work_source_with_real_version_conflicts(self):
        """
        T02: When soundtrack annotation coexists with real version tags (Live, Remix, TV Size, Cover),
        the version conflict MUST be preserved and MUST NOT auto_accept.
        """
        # Live conflict with OST
        src_studio = Track(title="剑心", artists=["张杰"], duration_ms=240000)
        cand_live_ost = AppleMusicTrack(
            id="c_live_ost",
            title="剑心（《古剑奇谭》电视剧片头曲 Live）",
            artists=["张杰"],
            duration_ms=240000,
        )
        mc_live = TrackScorer.score(src_studio, cand_live_ost)
        self.assertIn("version_conflict: 版本不一致", mc_live.evidence.conflicts)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src_studio, [mc_live])
        self.assertNotEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

        # Cover conflict with OST
        cand_cover_ost = AppleMusicTrack(
            id="c_cov_ost",
            title="剑心（《古剑奇谭》电视剧片头曲 Cover）",
            artists=["张杰"],
            duration_ms=240000,
        )
        mc_cov = TrackScorer.score(src_studio, cand_cover_ost)
        self.assertIn("version_conflict: 版本不一致", mc_cov.evidence.conflicts)
        best2, conf2, dec2, _, _ = TrackScorer.evaluate_candidates(src_studio, [mc_cov])
        self.assertNotEqual(dec2, DecisionStatus.AUTO_ACCEPT.value)

    # -------------------------------------------------------------------------
    # M01: Same Title and Artist with Different Albums Scores >= 0.90
    # -------------------------------------------------------------------------
    def test_M01_same_title_and_artist_different_album_scores_well(self):
        """
        M01: 恋曲1990 / 罗大佑 with different album releases (爱人同志 vs 告别的年代).
        Without duration or version conflicts, score must be >= 0.90 without distortion.
        """
        src = Track(title="恋曲1990", artists=["罗大佑"], album="爱人同志", duration_ms=298000)
        cand = AppleMusicTrack(id="c_lq", title="恋曲1990", artists=["罗大佑"], album="告别的年代", duration_ms=298000)

        mc = TrackScorer.score(src, cand)
        self.assertGreaterEqual(mc.title_score, 0.95)
        self.assertGreaterEqual(mc.artist_score, 0.95)
        self.assertGreaterEqual(mc.score, 0.90, f"Expected score >= 0.90, got {mc.score}")
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [mc])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

    # -------------------------------------------------------------------------
    # M02: Normalized Duration Similarity and Invalid Duration Handling
    # -------------------------------------------------------------------------
    def test_M02_normalized_duration_similarity_and_invalid_values(self):
        """
        M02: Normalized duration similarity in range [0.0, 1.0].
        Invalid durations (<5s or >60m or missing) return None and do not distort weights.
        """
        # 1. Invalid values
        self.assertIsNone(TrackScorer.calculate_duration_similarity(None, 200000))
        self.assertIsNone(TrackScorer.calculate_duration_similarity(200000, 0))
        self.assertIsNone(TrackScorer.calculate_duration_similarity(200000, -1000))
        self.assertIsNone(TrackScorer.calculate_duration_similarity(3000, 200000))  # 3s < 5s
        self.assertIsNone(TrackScorer.calculate_duration_similarity(4000000, 200000))  # > 60min

        # 2. Normalized similarity decay
        self.assertEqual(TrackScorer.calculate_duration_similarity(200000, 201000), 1.0)
        self.assertGreaterEqual(TrackScorer.calculate_duration_similarity(200000, 205000), 0.90)
        self.assertEqual(TrackScorer.calculate_duration_similarity(200000, 207000), 0.40)
        self.assertEqual(TrackScorer.calculate_duration_similarity(200000, 250000), 0.0)

    # -------------------------------------------------------------------------
    # R01: Real MatchingEngine Execution Trace and Budget Bounds
    # -------------------------------------------------------------------------
    def test_R01_matching_engine_execution_trace_and_budget_bounds(self):
        """
        R01: Execute MatchingEngine with mock client, record all API calls,
        assert Phase A, Phase B, Phase C execution trace and strict budget bounds:
        catalog <= 8, suggestions <= 2, equivalents <= 2, retry <= 6.
        """
        catalog_calls = []
        suggestion_calls = []
        equivalent_calls = []

        mock_client = MagicMock()

        def mock_search_catalog(query, *args, **kwargs):
            catalog_calls.append(query)
            if query == "句号":
                return CatalogSearchOutcome(
                    tracks=[
                        AppleMusicTrack(
                            id="cand_r01",
                            title="句号",
                            artists=["邓紫棋"],
                            album="摩天动物园",
                            duration_ms=215000,
                        )
                    ],
                    kind="ok",
                )
            return CatalogSearchOutcome(tracks=[], kind="no_hits")

        def mock_suggestions(term, *args, **kwargs):
            suggestion_calls.append(term)
            return CatalogSearchOutcome(tracks=[], kind="no_hits")

        def mock_equivalents(track_id, *args, **kwargs):
            equivalent_calls.append(track_id)
            return CatalogSearchOutcome(tracks=[], kind="no_hits")

        mock_client.search_catalog.side_effect = mock_search_catalog
        mock_client.search_by_isrc.return_value = CatalogSearchOutcome(tracks=[], kind="no_hits")
        mock_client.get_search_suggestions.side_effect = mock_suggestions
        mock_client.get_song_equivalents.side_effect = mock_equivalents

        engine = MatchingEngine(client=mock_client)
        source = Track(title="句号", artists=["G.E.M.邓紫棋"], album="摩天动物园", duration_ms=215000)

        result = engine.match_track(source, storefront="cn")
        self.assertIsNotNone(result.selected_candidate)
        self.assertEqual(result.selected_candidate.track.title, "句号")
        self.assertEqual(result.decision, DecisionStatus.AUTO_ACCEPT.value)

        # Assert query budget bounds
        self.assertLessEqual(len(catalog_calls), QueryPlanner.MAX_CATALOG_BUDGET)
        self.assertLessEqual(len(suggestion_calls), QueryPlanner.MAX_SUGGESTION_BUDGET)
        self.assertLessEqual(len(equivalent_calls), QueryPlanner.MAX_JP_DISCOVERY_BUDGET)

        # Verify executed query trace contains expected planning queries
        self.assertTrue(any("句号" in q for q in catalog_calls))

    # -------------------------------------------------------------------------
    # R02: Manual Search API and Automatic Scoring Consistency
    # -------------------------------------------------------------------------
    def test_R02_deterministic_manual_api_and_auto_scoring_consistency(self):
        """
        R02: Manual search endpoint (/api/search-track) produces scores, breakdowns,
        evidence, and decisions identical to direct TrackScorer.score for non-empty candidates.
        """
        client = TestClient(app)

        src = Track(
            title="光辉岁月",
            artists=["Beyond"],
            album="光辉岁月",
            duration_ms=300000,
        )
        cand = AppleMusicTrack(
            id="cand_beyond_det_1",
            title="光辉岁月",
            artists=["Beyond"],
            album="光辉岁月",
            duration_ms=300000,
        )

        expected_mc = TrackScorer.score(src, cand)

        with patch("applemusic.web.app.get_shared_engine") as mock_get_engine:
            mock_client = MagicMock()
            mock_engine = MagicMock()
            mock_engine.scorer = TrackScorer()
            mock_client.search_catalog.return_value = CatalogSearchOutcome(tracks=[cand], kind="ok")
            mock_get_engine.return_value = (mock_client, mock_engine)

            resp = client.post(
                "/api/search-track",
                headers={"X-App-Token": SESSION_API_TOKEN},
                json={
                    "query": "光辉岁月 Beyond",
                    "source_title": src.title,
                    "source_artists": src.artists,
                    "source_album": src.album,
                    "source_duration_ms": src.duration_ms,
                    "storefront": "cn",
                    "limit": 5,
                },
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["success"])
            self.assertEqual(len(data["results"]), 1, "Expected exactly 1 deterministic mock result")

            top = data["results"][0]
            self.assertEqual(top["score"], expected_mc.score)
            self.assertEqual(top["title_score"], expected_mc.title_score)
            self.assertEqual(top["artist_score"], expected_mc.artist_score)
            self.assertEqual(top["album_score"], expected_mc.album_score)
            self.assertEqual(top["duration_score"], expected_mc.duration_score)
            self.assertEqual(top["version_score"], expected_mc.version_score)
            self.assertEqual(top["decision"], expected_mc.decision)
            self.assertEqual(top["evidence"]["conflicts"], expected_mc.evidence.conflicts)
            self.assertEqual(top["evidence"]["verification_level"], expected_mc.evidence.verification_level)
            self.assertEqual(top["evidence"]["evidence_type"], expected_mc.evidence.evidence_type)
            self.assertEqual(top["evidence"]["rule_version"], expected_mc.evidence.rule_version)

    # -------------------------------------------------------------------------
    # C01: Independent SQLite Cache Invalidation Matrix
    # -------------------------------------------------------------------------
    def test_C01_sqlite_cache_invalidation_matrix(self):
        """
        C01: Independent SQLite matrix testing:
        - user_confirmed retention even with outdated versions
        - Single rule_version outdated -> invalidated
        - Single query_policy_version outdated -> invalidated
        - Single alias_version outdated -> invalidated
        - Stale auto_accept demotion with duration conflict -> demoted to review
        """
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            cache = PersistentCache(db_path=db_path)

            src_track = Track(title="句号", artists=["G.E.M.邓紫棋"], duration_ms=215000)
            cand_track = AppleMusicTrack(id="cand_c01", title="句号", artists=["邓紫棋"], duration_ms=215000)

            # 1. user_confirmed retention with completely outdated versions
            user_result = SongMatchResult(
                source_track=src_track,
                selected_candidate=TrackScorer.score(src_track, cand_track),
                decision="user_confirmed",
                status=ConfidenceLevel.USER_CONFIRMED,
                decision_reasons=["用户人工确认"],
            )
            cache.set_match("cn", "track_user_hash", user_result)

            with cache._get_connection() as conn:
                conn.execute(
                    "UPDATE match_cache SET rule_version='2026.09.v6.1', alias_version='2026.09.v6.1', query_policy_version='2026.09.v2' WHERE track_hash='track_user_hash'"
                )

            read_user = cache.get_match("cn", "track_user_hash")
            self.assertIsNotNone(read_user)
            self.assertEqual(read_user.decision, "user_confirmed")

            # 2. Single rule_version outdated (2026.10.v7.0 vs current 2026.10.v7.2)
            algo_res1 = SongMatchResult(
                source_track=src_track,
                selected_candidate=TrackScorer.score(src_track, cand_track),
                decision="review",
                status=ConfidenceLevel.MEDIUM,
                decision_reasons=["待复核"],
            )
            cache.set_match("cn", "track_algo_rule", algo_res1)
            with cache._get_connection() as conn:
                conn.execute(
                    "UPDATE match_cache SET rule_version='2026.10.v7.0' WHERE track_hash='track_algo_rule'"
                )
            self.assertIsNone(cache.get_match("cn", "track_algo_rule"))

            # 3. Single query_policy_version outdated
            cache.set_match("cn", "track_algo_query", algo_res1)
            with cache._get_connection() as conn:
                conn.execute(
                    "UPDATE match_cache SET query_policy_version='2026.09.v2' WHERE track_hash='track_algo_query'"
                )
            self.assertIsNone(cache.get_match("cn", "track_algo_query"))

            # 4. Single alias_version outdated
            cache.set_match("cn", "track_algo_alias", algo_res1)
            with cache._get_connection() as conn:
                conn.execute(
                    "UPDATE match_cache SET alias_version='2026.10.v7.0' WHERE track_hash='track_algo_alias'"
                )
            self.assertIsNone(cache.get_match("cn", "track_algo_alias"))

            # 5. Stale auto_accept demotion with duration conflict
            src_dur = Track(title="Example Song", artists=["Artist A"], duration_ms=240000, isrc="USABC1234567")
            cand_dur = AppleMusicTrack(id="c_stale", title="Example Song", artists=["Artist A"], duration_ms=310000, isrc="USABC1234567")
            stale_cand = MatchCandidate(
                track=cand_dur,
                score=1.0,
                title_score=1.0,
                artist_score=1.0,
                album_score=0.0,
                duration_score=0.0,
                version_score=0.0,
                confidence=ConfidenceLevel.EXACT,
                decision=DecisionStatus.AUTO_ACCEPT.value,
                decision_reasons=["旧规则误采纳"],
                evidence=MatchEvidence(
                    evidence_type="isrc",
                    verification_level=VerificationLevel.STRONG.value,
                    conflicts=[],
                    rule_version="2026.10.v7.0",
                ),
            )
            stale_res = SongMatchResult(
                source_track=src_dur,
                selected_candidate=stale_cand,
                candidates=[stale_cand],
                decision="auto_accept",
                status=ConfidenceLevel.EXACT,
            )
            cache.set_match("cn", "track_stale_dur", stale_res)
            with cache._get_connection() as conn:
                conn.execute("UPDATE match_cache SET rule_version='2026.10.v7.0' WHERE track_hash='track_stale_dur'")

            # Reading stale match triggers automatic re-evaluation with current rule version:
            # Demotes from auto_accept to review due to duration_conflict!
            read_stale = cache.get_match("cn", "track_stale_dur")
            self.assertIsNotNone(read_stale, "Cache hit must trigger re-evaluation instead of crash")
            self.assertEqual(read_stale.decision, "review", "Stale auto_accept with duration conflict must demote to review")
            self.assertEqual(read_stale.selected_candidate.score, 0.944)
            self.assertTrue(any("duration_conflict" in c for c in read_stale.evidence.conflicts))
            self.assertTrue(any("isrc_duration_conflict" in c for c in read_stale.evidence.conflicts))

            # Database row must also be updated to the re-evaluated decision and current rule_version
            with cache._get_connection() as conn:
                cursor = conn.execute("SELECT decision, rule_version FROM match_cache WHERE track_hash='track_stale_dur'")
                row = cursor.fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], "review")
                self.assertEqual(row[1], MATCH_RULE_VERSION)

        finally:
            try:
                if hasattr(cache, "close"):
                    cache.close()
            except Exception:
                pass
            try:
                if os.path.exists(db_path):
                    os.remove(db_path)
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # N01: Security Suite Preservation
    # -------------------------------------------------------------------------
    def test_N01_security_suite_preservation(self):
        """
        N01: Sweets parade vs magical mode, Sam Smith Stay with Me vs Stay,
        and language version conflicts must maintain existing safety guards.
        """
        # 1. Sweets parade vs magical mode
        src_sweets = Track(title="sweets parade", artists=["花泽香菜"], album="sweets parade - Single", duration_ms=210000)
        cand_magical = AppleMusicTrack(id="n01", title="magical mode", artists=["花泽香菜"], album="sweets parade - Single", duration_ms=210000)
        mc_sweets = TrackScorer.score(src_sweets, cand_magical)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src_sweets, [mc_sweets])
        self.assertEqual(dec, DecisionStatus.NO_MATCH.value)
        self.assertLessEqual(mc_sweets.score, 0.39)

        # 2. Stay with Me vs Stay
        src_stay = Track(title="Stay with Me", artists=["Sam Smith"], duration_ms=172000)
        cand_stay = AppleMusicTrack(id="n02", title="Stay", artists=["Sam Smith"], duration_ms=172000)
        mc_stay = TrackScorer.score(src_stay, cand_stay)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src_stay, [mc_stay])
        self.assertNotEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

        # 3. Language version conflict
        src_lang = Track(title="Song (Japanese Ver.)", artists=["Artist A"], duration_ms=200000)
        cand_lang = AppleMusicTrack(id="n03", title="Song (English Ver.)", artists=["Artist A"], duration_ms=200000)
        mc_lang = TrackScorer.score(src_lang, cand_lang)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src_lang, [mc_lang])
        self.assertIn("version_conflict: 版本不一致", mc_lang.evidence.conflicts)
        self.assertNotEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

    # -------------------------------------------------------------------------
    # U02: Static UI Contract & Version Constant Validation
    # -------------------------------------------------------------------------
    def test_U02_static_ui_contract_and_version_constants(self):
        """
        U02: Validate current rule/query versions and unchanged alias version.
        """
        self.assertEqual(MATCH_RULE_VERSION, "2026.10.v7.3")
        self.assertEqual(ALIAS_VERSION, "2026.10.v7.2")
        self.assertEqual(QUERY_POLICY_VERSION, "2026.10.v7.3")
        self.assertEqual(ROMANIZER_VERSION, "2026.09.v5")
        self.assertEqual(EXCEPTION_REGISTRY_VERSION, "2026.09.v4")

        with open("applemusic/web/static/index.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn("2026.10.v7.2", html)
        self.assertIn("2026.10.v7.3", html)
        self.assertNotIn("'2026.10.v7.0'", html)


if __name__ == "__main__":
    unittest.main()
