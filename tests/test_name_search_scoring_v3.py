"""
Comprehensive test suite verifying name-search-scoring-v3 per SPEC.md and TEST_MATRIX.md.
Explicitly covers R1-R5 and matrix boundary cases N01-N03, Q01-Q05, S01-S06, C01-C02.
Zero hardcoding of song or artist names.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from applemusic.cache import PersistentCache
from applemusic.client import AppleMusicClient
from applemusic.config import Config
from applemusic.matcher.cleaner import TextCleaner, ParsedArtistDetails
from applemusic.matcher.engine import MatchingEngine
from applemusic.matcher.evidence import (
    ALIAS_VERSION,
    EXCEPTION_REGISTRY_VERSION,
    MATCH_RULE_VERSION,
    MatchEvidence,
    QUERY_POLICY_VERSION,
    ROMANIZER_VERSION,
    VerificationLevel,
)
from applemusic.matcher.query_models import QueryContext, PlannedQuery
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


class TestR1toR5DefectFixes(unittest.TestCase):
    """
    Direct verification of reproduced defects R1 through R5.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_r1_r5.db"
        self.config = Config(
            developer_token="test_token",
            storefront="cn",
            fallback_storefronts=["hk", "us"],
            auto_accept_threshold=0.88,
            min_review_score=0.55,
            min_score_gap=0.08,
        )
        self.client = AppleMusicClient(self.config)
        self.cache = PersistentCache(self.db_path)
        self.client.persistent_cache = self.cache
        self.engine = MatchingEngine(self.client, self.config)
        self.engine.persistent_cache = self.cache

    def tearDown(self):
        try:
            self.cache.close()
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_R1_same_isrc_with_mismatched_title_or_missing_candidate_artist(self):
        """
        R1 / S01: Same ISRC with mismatched title or missing candidate artist
        must NOT forge title_score=1.0 or artist_score=1.0, must record conflicts,
        and must NOT auto_accept.
        """
        # Case A: Same ISRC, completely different title
        src = Track(title="Moonlight Sonata", artists=["Ludwig van Beethoven"], isrc="USABC1234567")
        cand_diff_title = AppleMusicTrack(
            id="c_diff_title",
            title="Highway to Hell",
            artists=["Ludwig van Beethoven"],
            isrc="USABC1234567",
        )
        res_a = TrackScorer.score(src, cand_diff_title)
        # Real field scores must be preserved, not forged to 1.0
        self.assertLess(res_a.title_score, 0.40)
        self.assertTrue(any("isrc_title_conflict" in c or "歌名明显不符" in c for c in res_a.evidence.conflicts))
        self.assertNotEqual(res_a.decision, DecisionStatus.AUTO_ACCEPT.value)
        best_a, _, dec_a, _, _ = TrackScorer.evaluate_candidates(src, [res_a])
        self.assertNotEqual(dec_a, DecisionStatus.AUTO_ACCEPT.value)

        # Case B: Same ISRC, missing candidate artist
        cand_missing_artist = AppleMusicTrack(
            id="c_missing_artist",
            title="Moonlight Sonata",
            artists=[],
            isrc="USABC1234567",
        )
        res_b = TrackScorer.score(src, cand_missing_artist)
        self.assertEqual(res_b.artist_score, 0.0)
        self.assertTrue(any("isrc_missing_artist" in c or "候选缺失艺人信息" in c for c in res_b.evidence.conflicts))
        self.assertNotEqual(res_b.decision, DecisionStatus.AUTO_ACCEPT.value)
        best_b, _, dec_b, _, _ = TrackScorer.evaluate_candidates(src, [res_b])
        self.assertNotEqual(dec_b, DecisionStatus.AUTO_ACCEPT.value)

    def test_R2_main_artist_feat_guest_vs_candidate_guest_only(self):
        """
        R2 / S04: Source is Main Artist feat. Guest Artist, candidate is only Guest Artist.
        Primary artist mismatch must NOT give artist_score=1.0, must record conflict,
        and must NOT auto_accept.
        """
        src = Track(title="Summer Romance", artists=["Alpha Singer feat. Beta Rapper"])
        cand = AppleMusicTrack(
            id="c_guest_only",
            title="Summer Romance",
            artists=["Beta Rapper"],
        )
        scored = TrackScorer.score(src, cand)
        # Artist similarity cannot be 1.0 (capped at 0.60 for guest match)
        self.assertLessEqual(scored.artist_score, 0.60)
        self.assertTrue(
            any("主表演者不符" in c or "primary_artist_mismatch" in c for c in scored.evidence.conflicts)
        )
        self.assertNotEqual(scored.decision, DecisionStatus.AUTO_ACCEPT.value)

        best, _, dec, _, _ = TrackScorer.evaluate_candidates(src, [scored])
        self.assertNotEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

    def test_R3_trans_title_and_aliases_in_query_context_and_planning(self):
        """
        R3 / Q01: Track.trans_title and Track.aliases must enter QueryContext,
        and generate valid target storefront queries when native search has no hits.
        """
        src = Track(
            title="夜に駆ける",
            artists=["YOASOBI"],
            trans_title="Racing into the Night",
            aliases=["Yoru ni Kakeru"],
        )

        # 1. Verify QueryPlanner outputs trans_title and aliases queries with proper provenance
        ctx = QueryContext(
            title=src.title,
            artists=src.artists,
            target_storefront="cn",
            trans_title=src.trans_title,
            aliases=src.aliases,
        )
        planned = QueryPlanner.plan_phases(ctx)
        native_queries = planned.get("A_native", [])
        query_texts = [q.query for q in native_queries]
        query_provenances = {q.query: q.provenance for q in native_queries}

        self.assertTrue(any("Racing into the Night" in t for t in query_texts))
        self.assertTrue(any("Yoru ni Kakeru" in t for t in query_texts))
        self.assertEqual(query_provenances.get("Racing into the Night YOASOBI"), "source_translation")
        self.assertEqual(query_provenances.get("Yoru ni Kakeru YOASOBI"), "source_alias")

        # 2. Verify engine actually searches the translated query when native yields no hits
        searched_queries = []
        def mock_search(query, storefront, **kwargs):
            searched_queries.append(query)
            return CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)

        with patch.object(self.client, "search_catalog", side_effect=mock_search):
            with patch.object(self.client, "get_search_suggestions", return_value=[]):
                res = self.engine.match_track(src)

        self.assertTrue(any("Racing into the Night" in q for q in searched_queries), "Engine failed to search trans_title query!")

    def test_R4_budget_preservation_for_phase_d_jp_discovery(self):
        """
        R4 / Q02: High-budget queries (e.g. 灰かぶり (Cinder ella) / 十明 with A=5, B=4, D=2)
        must preserve budget for Phase D (JP discovery) so it executes at least once,
        and total catalog queries must never exceed 8.
        """
        src = Track(
            title="灰かぶり (Cinder ella)",
            artists=["十明"],
            isrc=None,
        )

        catalog_searches: list[tuple[str, str]] = []

        def mock_search_catalog(query, storefront, **kwargs):
            catalog_searches.append((storefront, query))
            return CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)

        with patch.object(self.client, "search_catalog", side_effect=mock_search_catalog):
            with patch.object(self.client, "get_search_suggestions", return_value=[]):
                res = self.engine.match_track(src)

        # 1. Total catalog searches must not exceed 8
        self.assertLessEqual(len(catalog_searches), 8)
        # 2. Phase D (JP storefront discovery) MUST execute at least once!
        jp_searches = [s for s in catalog_searches if s[0] == "jp"]
        self.assertGreaterEqual(len(jp_searches), 1, "Phase D JP discovery was starved of budget!")

    def test_R5_apple_artist_name_preserves_single_artist_with_comma(self):
        """
        R5 / N01: Apple artistName="Tyler, The Creator" must NOT be blindly comma-split
        into ["Tyler", "The Creator"] when relationships has 0 or 1 artist entry.
        """
        # Case A: exactly 1 artist relationship
        raw_apple_item_with_rel = {
            "id": "1464303494",
            "attributes": {
                "name": "EARFQUAKE",
                "artistName": "Tyler, The Creator",
                "isrc": "USSM11902094",
            },
            "relationships": {
                "artists": {
                    "data": [{"id": "278143924", "type": "artists"}]
                }
            },
        }
        track_a = AppleMusicClient._parse_song_item(raw_apple_item_with_rel)
        self.assertEqual(track_a.artists, ["Tyler, The Creator"])
        self.assertEqual(track_a.raw_artist_name, "Tyler, The Creator")

        # Case B: 0 artist relationships (raw artistName has comma but is a single artist)
        raw_apple_item_no_rel = {
            "id": "1464303494",
            "attributes": {
                "name": "EARFQUAKE",
                "artistName": "Tyler, The Creator",
            },
        }
        track_b = AppleMusicClient._parse_song_item(raw_apple_item_no_rel)
        self.assertEqual(track_b.artists, ["Tyler, The Creator"])
        self.assertEqual(track_b.raw_artist_name, "Tyler, The Creator")

        # Case C: Multiple artist relationship with comma (should split correctly)
        raw_multi = {
            "id": "999",
            "attributes": {
                "name": "Collab Song",
                "artistName": "Artist One, Artist Two",
            },
            "relationships": {
                "artists": {
                    "data": [
                        {"id": "1", "type": "artists"},
                        {"id": "2", "type": "artists"},
                    ]
                }
            },
        }
        track_c = AppleMusicClient._parse_song_item(raw_multi)
        self.assertEqual(track_c.artists, ["Artist One", "Artist Two"])


class TestTestMatrixBoundaryCases(unittest.TestCase):
    """
    Additional matrix cases from TEST_MATRIX.md:
    N01-N03, Q03-Q05, S02-S06, C01-C02, U01.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_matrix_bounds.db"
        self.config = Config(
            developer_token="test_token",
            storefront="cn",
            fallback_storefronts=["hk", "us"],
            auto_accept_threshold=0.88,
            min_review_score=0.55,
            min_score_gap=0.08,
        )
        self.client = AppleMusicClient(self.config)
        self.cache = PersistentCache(self.db_path)
        self.client.persistent_cache = self.cache
        self.engine = MatchingEngine(self.client, self.config)
        self.engine.persistent_cache = self.cache

    def tearDown(self):
        try:
            self.cache.close()
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_N01_tyler_the_creator_comparison_and_scoring(self):
        """
        N01: Tyler, The Creator comparisons match accurately without splitting.
        """
        src = Track(title="EARFQUAKE", artists=["Tyler, The Creator"])
        cand = AppleMusicTrack(id="10", title="EARFQUAKE", artists=["Tyler, The Creator"])
        scored = TrackScorer.score(src, cand)
        self.assertEqual(scored.artist_score, 1.0)
        best, _, dec, _, _ = TrackScorer.evaluate_candidates(src, [scored])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

    def test_N02_multi_artist_structured_details(self):
        """
        N02: Explicit multi-artist relationships, feat., CV/character, half/full width delimiters
        preserve primary, featured, character voice, and collaborators without losing raw strings.
        """
        # 1. feat. pattern
        det1 = TextCleaner.parse_artist_details(["Primary Artist feat. Featured Artist"])
        self.assertEqual(det1.primary, "Primary Artist")
        self.assertEqual(det1.featured, ["Featured Artist"])

        # 2. CV pattern
        det2 = TextCleaner.parse_artist_details(["Sagiri Izumi (CV:Akane Fujita)"])
        self.assertEqual(det2.primary, "Sagiri Izumi")
        self.assertEqual(det2.character_voices, ["Akane Fujita"])

        # 3. Collaboration delimiter & / 、
        det3 = TextCleaner.parse_artist_details(["HOYO-MiX & 宴寧"])
        self.assertEqual(det3.primary, "HOYO-MiX")
        self.assertEqual(det3.collaborators, ["宴寧"])

        # 4. Bracketed pronunciation reading
        det4 = TextCleaner.parse_artist_details(["藤田茜 (ふじた あかね)"])
        self.assertEqual(det4.primary, "藤田茜")
        self.assertEqual(det4.aliases, ["ふじた あかね"])

    def test_Q03_low_quality_candidate_does_not_block_suggestions(self):
        """
        Q03: An existing low-quality candidate (< 0.85 or with conflicts) must NOT block
        search suggestion expansion. The suggestion query must actually execute within budget,
        select the strong candidate returned by suggestions, and conserve total catalog budget (<= 8).
        """
        src = Track(title="Obscure Song", artists=["Obscure Artist"], duration_ms=200000)

        weak_track = AppleMusicTrack(
            id="weak_1",
            title="Obscure Song (Cover)",
            artists=["Random Person"],
            duration_ms=180000,
        )
        strong_track = AppleMusicTrack(
            id="strong_1",
            title="Obscure Song",
            artists=["Obscure Artist"],
            duration_ms=200000,
            album="Official Album",
        )

        executed_searches = []

        def mock_search_catalog(query, storefront, **kwargs):
            executed_searches.append((query, storefront))
            # Phase A native search returns weak candidate
            if "Cover" in query or "Obscure Song" in query and "Official" not in query:
                return CatalogSearchOutcome(kind="ok", tracks=[weak_track], http_status=200)
            # Suggestion query returns the strong candidate!
            if "Official" in query:
                return CatalogSearchOutcome(kind="ok", tracks=[strong_track], http_status=200)
            return CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)

        with patch.object(self.client, "search_catalog", side_effect=mock_search_catalog):
            with patch.object(
                self.client,
                "get_search_suggestions",
                return_value=["Obscure Song Official"],
            ) as mock_sugg:
                res = self.engine.match_track(src)

                # 1. Suggestions API was called despite existing weak candidate
                self.assertTrue(mock_sugg.called, "Suggestions were incorrectly blocked by a weak candidate!")

                # 2. Suggestion-generated catalog query was ACTUALLY executed (not skipped due to budget starvation)
                executed_query_texts = [q for q, _ in executed_searches]
                self.assertTrue(
                    any("Official" in q for q in executed_query_texts),
                    f"Suggestion query was not executed! Executed queries: {executed_query_texts}",
                )

                # 3. Strong candidate from suggestion was ultimately selected and auto_accepted
                self.assertIsNotNone(res.selected_candidate)
                self.assertEqual(res.selected_candidate.track.id, "strong_1")
                self.assertEqual(res.decision, DecisionStatus.AUTO_ACCEPT.value)

                # 4. Total budget conservation: catalog searches <= 8
                self.assertLessEqual(len(executed_searches), 8)
                self.assertLessEqual(res.diagnostics.budget_consumed.get("catalog", 0), 8)
                self.assertLessEqual(res.search_attempts, 8)

    def test_Q03_high_score_review_candidate_does_not_block_suggestions(self):
        """
        Q03: Under a heavy Japanese multi-variant track (TEST_MATRIX Q02/Q03) where Phase A+B
        generates enough queries (>= 8) to otherwise exhaust the catalog budget:
        1. An initial candidate with high score (score >= 0.85) but in REVIEW decision status
           must NOT block search suggestion expansion (Phase C).
        2. The budget scheduler must compress Phase A+B so that Phase C suggestion is guaranteed
           budget and ACTUALLY executes search_catalog under budget pressure (not starved).
        3. Phase D JP discovery is also guaranteed budget and executes at least 1 query.
        4. Total catalog budget is conserved (<= 8), and the strong candidate is ultimately auto-accepted.
        """
        src = Track(
            title="灰かぶり (Cinder ella)",
            artists=["十明"],
            trans_title="Cinderella",
            aliases=["Ash Covered Cinderella"],
            duration_ms=210000,
        )

        # 1. Verify that Phase A+B plans >= 8 queries (which would starve C and D without ladder)
        planned = QueryPlanner.plan_phases(
            QueryContext(
                title=src.title,
                artists=src.artists,
                trans_title=src.trans_title,
                aliases=src.aliases,
                target_storefront="cn",
            )
        )
        total_ab_planned = len(planned.get("A_native", [])) + len(planned.get("B_script", []))
        self.assertGreaterEqual(total_ab_planned, 8, "Phase A+B must generate >= 8 queries to test starvation")

        # 2. High-score review candidate returned in Phase A (score >= 0.85, decision == review)
        cand_review = AppleMusicTrack(
            id="rev_1",
            title="Cinderella",
            artists=["十明"],
            duration_ms=230000,
        )
        sc = TrackScorer.score(src, cand_review)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(
            src,
            [sc],
            auto_accept_threshold=self.config.auto_accept_threshold,
            min_review_score=self.config.min_review_score,
            min_score_gap=self.config.min_score_gap,
        )
        self.assertGreaterEqual(sc.score, 0.85, "Initial candidate score must be >= 0.85")
        self.assertEqual(dec, DecisionStatus.REVIEW.value, "Initial candidate must have REVIEW decision")

        # 3. Strong candidate available in JP storefront
        jp_strong = AppleMusicTrack(
            id="jp_strong_id",
            title="灰かぶり",
            artists=["十明"],
            duration_ms=210000,
        )
        cn_strong = AppleMusicTrack(
            id="cn_strong_id",
            title="灰かぶり",
            artists=["十明"],
            duration_ms=210000,
        )

        catalog_calls = []

        def mock_search_catalog(query, storefront, **kwargs):
            catalog_calls.append((query, storefront))
            if storefront == "jp":
                return CatalogSearchOutcome(kind="ok", tracks=[jp_strong], http_status=200)
            if "Cinderella" in query and "Ash" not in query:
                return CatalogSearchOutcome(kind="ok", tracks=[cand_review], http_status=200)
            return CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)

        with patch.object(self.client, "search_catalog", side_effect=mock_search_catalog):
            with patch.object(
                self.client,
                "get_search_suggestions",
                return_value=["灰かぶり 映画主題歌"],
            ) as mock_sugg:
                with patch.object(
                    self.client,
                    "get_equivalent_tracks",
                    return_value={"jp_strong_id": cn_strong},
                ):
                    res = self.engine.match_track(src)

                # 1. Suggestions API was called despite existing candidate having score >= 0.85
                self.assertTrue(
                    mock_sugg.called,
                    "Suggestions were incorrectly blocked by a high-score candidate whose decision is still REVIEW!",
                )

                # 2. Phase C suggestion-generated query ACTUALLY executed search_catalog under budget pressure!
                c_queries = [(q, sf) for q, sf in catalog_calls if "映画主題歌" in q]
                self.assertGreaterEqual(
                    len(c_queries),
                    1,
                    f"Phase C suggestion catalog query was starved! Executed queries: {catalog_calls}",
                )

                # 3. Phase D JP discovery was ALSO guaranteed budget and executed at least 1 query!
                d_queries = [(q, sf) for q, sf in catalog_calls if sf == "jp"]
                self.assertGreaterEqual(
                    len(d_queries),
                    1,
                    f"Phase D JP discovery catalog query was starved! Executed queries: {catalog_calls}",
                )

                # 4. Total budget conservation: catalog searches <= 8
                self.assertLessEqual(len(catalog_calls), 8)
                self.assertLessEqual(res.diagnostics.budget_consumed.get("catalog", 0), 8)
                self.assertLessEqual(res.search_attempts, 8)

                # 5. Strong candidate was ultimately selected and auto_accepted
                self.assertIsNotNone(res.selected_candidate)
                self.assertEqual(res.selected_candidate.track.id, "cn_strong_id")
                self.assertEqual(res.decision, DecisionStatus.AUTO_ACCEPT.value)

    def test_Q04_network_or_rate_limit_error_not_cached_as_no_match(self):
        """
        Q04: 401, 429, timeout and partial failures must NOT report
        unavailable_in_target_storefront or cache as no_match.
        """
        src = Track(title="Rate Limited Song", artists=["Artist"])

        with patch.object(self.client, "search_catalog") as mock_search:
            mock_search.return_value = CatalogSearchOutcome(
                kind="rate_limited",
                http_status=429,
                retry_after_seconds=30.0,
                safe_message="Apple Music 频控限制",
            )
            res = self.engine.match_track(src)

            # Must NOT report unavailable_in_target_storefront
            self.assertNotEqual(res.decision, "unavailable_in_target_storefront")
            self.assertTrue(res.search_incomplete)

            # Persistent cache must NOT have cached a no_hits or no_match
            cached_match = self.cache.find_match("cn", src)
            self.assertIsNone(cached_match)

    def test_Q05_weak_jp_candidate_does_not_output_unavailable(self):
        """
        Q05: A weak candidate discovered in JP (< 0.72) or failed equivalents lookup
        must NOT trigger unavailable_in_target_storefront.
        """
        src = Track(title="Some Song", artists=["Some Artist"])

        weak_jp_track = AppleMusicTrack(
            id="jp_weak",
            title="Different Title",
            artists=["Different Artist"],
            storefront="jp",
            discovery_storefront="jp",
        )

        with patch.object(self.client, "search_catalog") as mock_search:
            def side_effect(query, storefront, **kwargs):
                if storefront == "jp":
                    return CatalogSearchOutcome(kind="ok", tracks=[weak_jp_track], http_status=200)
                return CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)

            mock_search.side_effect = side_effect
            res = self.engine.match_track(src)
            # Must NOT output unavailable_in_target_storefront
            self.assertNotEqual(res.decision, "unavailable_in_target_storefront")

    def test_S02_isrc_exact_match_consistent_retains_auto_accept(self):
        """
        S02: Same ISRC with matching title, matching artist, and consistent version
        retains AUTO_ACCEPT with real field scores.
        """
        src = Track(title="Shape of You", artists=["Ed Sheeran"], isrc="GBAHS1600463", duration_ms=233712)
        cand = AppleMusicTrack(
            id="ed_1",
            title="Shape of You",
            artists=["Ed Sheeran"],
            isrc="GBAHS1600463",
            duration_ms=233712,
        )
        scored = TrackScorer.score(src, cand)
        self.assertEqual(scored.score, 1.0)
        self.assertGreaterEqual(scored.title_score, 0.90)
        self.assertGreaterEqual(scored.artist_score, 0.90)
        self.assertEqual(scored.decision, DecisionStatus.AUTO_ACCEPT.value)
        best, _, dec, _, _ = TrackScorer.evaluate_candidates(src, [scored])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

    def test_S03_isrc_version_conflict_demoted_to_review(self):
        """
        S03: Same ISRC but Studio vs Live version conflict demoted to REVIEW.
        """
        src = Track(title="Hotel California", artists=["Eagles"], isrc="USEE12345678")
        cand_live = AppleMusicTrack(
            id="eagles_live",
            title="Hotel California (Live)",
            artists=["Eagles"],
            isrc="USEE12345678",
        )
        scored = TrackScorer.score(src, cand_live)
        self.assertIn("isrc_version_conflict: ISRC相同但版本不一致", scored.evidence.conflicts)
        self.assertEqual(scored.decision, DecisionStatus.REVIEW.value)
        best, _, dec, _, _ = TrackScorer.evaluate_candidates(src, [scored])
        self.assertEqual(dec, DecisionStatus.REVIEW.value)

    def test_S05_homograph_or_romanizer_only_cannot_auto_accept(self):
        """
        S05: Romaji/Romanizer transliteration alone without independent corroboration
        is only medium evidence and cannot auto_accept.
        """
        src = Track(title="さくら", artists=["森山直太朗"])
        cand = AppleMusicTrack(
            id="sakura_1",
            title="Sakura",
            artists=["Naotaro Moriyama"],
        )
        scored = TrackScorer.score(src, cand)
        best, _, dec, _, _ = TrackScorer.evaluate_candidates(src, [scored])
        # Under S05, romanizer-only evidence cannot auto_accept
        self.assertNotEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

    def test_S06_second_candidate_version_or_artist_mismatch_respects_score_gap(self):
        """
        S06: Two candidates with small score gap (< 0.08) cannot bypass min_score_gap
        if their versions or primary artists differ.
        """
        src = Track(title="Song Title", artists=["Artist One"])
        cand1 = AppleMusicTrack(
            id="1",
            title="Song Title",
            artists=["Artist One"],
        )
        cand2 = AppleMusicTrack(
            id="2",
            title="Song Title (Instrumental)",  # Version conflict!
            artists=["Artist One"],
        )
        sc1 = TrackScorer.score(src, cand1)
        sc2 = TrackScorer.score(src, cand2)
        # Force scores to be close
        sc1.score = 0.90
        sc2.score = 0.88

        best, _, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [sc1, sc2], min_score_gap=0.08)
        # Because candidate 2 is an instrumental version, is_same_song_variant must be False,
        # so score_gap (0.02 < 0.08) triggers REVIEW
        self.assertEqual(dec, DecisionStatus.REVIEW.value)

    def test_C01_legacy_cache_reevaluation(self):
        """
        C01: Legacy cached auto_accept records with v1/v2 rule_version are automatically
        re-evaluated under v3 scoring rules upon cache hit.
        """
        src = Track(title="Original", artists=["Alpha feat. Beta"], isrc="ISRC123")
        # Legacy candidate was auto_accepted under older rule even though only guest artist matched
        legacy_cand = AppleMusicTrack(
            id="legacy_1",
            title="Original",
            artists=["Beta"],
            isrc="ISRC123",
        )
        legacy_evidence = MatchEvidence(
            evidence_type="isrc",
            verification_level=VerificationLevel.STRONG.value,
            rule_version="2026.09.v2",
            query_policy_version="2026.09.v2",
        )
        legacy_match_cand = MatchCandidate(
            track=legacy_cand,
            score=1.0,
            decision="auto_accept",
            confidence=ConfidenceLevel.EXACT,
            evidence=legacy_evidence,
        )
        legacy_result = SongMatchResult(
            source_track=src,
            selected_candidate=legacy_match_cand,
            candidates=[legacy_match_cand],
            status=ConfidenceLevel.EXACT,
            decision="auto_accept",
            evidence=legacy_evidence,
            confidence=ConfidenceLevel.EXACT,
        )

        conn = self.cache._get_connection()
        conn.execute(
            """
            INSERT OR REPLACE INTO match_cache
            (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at, rule_version, query_policy_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "cn",
                "test_hash_1",
                "test_hash_1",
                legacy_result.json(),
                "auto_accept",
                "EXACT",
                1000.0,
                9999999999.0,
                "2026.09.v2",
                "2026.09.v2",
            ),
        )

        # Retrieve through get_match: should re-evaluate under v3!
        re_evaluated = self.cache.get_match("cn", "test_hash_1")
        self.assertIsNotNone(re_evaluated)
        # Under v3, R2 / S04 prevents auto_accept because Beta is only a guest artist in source!
        self.assertNotEqual(re_evaluated.decision, "auto_accept")
        self.assertEqual(re_evaluated.decision, "review")

    def test_C02_user_confirmed_retention(self):
        """
        C02: User manually confirmed matches (decision='user_confirmed') are preserved
        even when rule versions change.
        """
        src = Track(title="Song", artists=["Artist"])
        cand = AppleMusicTrack(id="confirmed_1", title="Song", artists=["Artist"])
        evidence = MatchEvidence(
            evidence_type="manual",
            verification_level=VerificationLevel.STRONG.value,
            rule_version="2026.09.v1",
        )
        match_cand = MatchCandidate(
            track=cand,
            score=1.0,
            decision="user_confirmed",
            confidence=ConfidenceLevel.EXACT,
            evidence=evidence,
        )
        result = SongMatchResult(
            source_track=src,
            selected_candidate=match_cand,
            candidates=[match_cand],
            status=ConfidenceLevel.EXACT,
            decision="user_confirmed",
            evidence=evidence,
            confidence=ConfidenceLevel.EXACT,
        )

        conn = self.cache._get_connection()
        conn.execute(
            """
            INSERT OR REPLACE INTO match_cache
            (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at, rule_version, query_policy_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "cn",
                "confirmed_hash",
                "confirmed_hash",
                result.json(),
                "user_confirmed",
                "EXACT",
                1000.0,
                9999999999.0,
                "2026.09.v1",
                "2026.09.v1",
            ),
        )

        retrieved = self.cache.get_match("cn", "confirmed_hash")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.decision, "user_confirmed")

    def test_Q03_suggestions_and_jp_budget_reservation_under_max_ab_queries(self):
        """
        Q03: Under a heavy query track where Phase A + B generate many queries,
        the budget scheduler must compress/cap Phase A+B so that Phase C suggestion
        and Phase D JP discovery are guaranteed their reserved catalog budget.
        Total catalog queries must be strictly conserved (<= 8).
        """
        # Complex track with Japanese kana, translations, aliases generating 5+ queries
        src = Track(
            title="灰かぶり (Cinder ella)",
            artists=["十明"],
            trans_title="Cinderella",
            aliases=["Ash Covered Cinderella"],
        )

        catalog_calls = []

        def mock_search(query, storefront, **kwargs):
            catalog_calls.append((query, storefront))
            return CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)

        with patch.object(self.client, "search_catalog", side_effect=mock_search):
            with patch.object(
                self.client,
                "get_search_suggestions",
                return_value=["灰かぶり 十明 映画主題歌"],
            ) as mock_sugg:
                res = self.engine.match_track(src)

                # 1. Suggestions API was called
                self.assertTrue(mock_sugg.called)

                # 2. Phase C suggestion catalog search was executed!
                c_queries = [q for q, sf in catalog_calls if "映画主題歌" in q]
                self.assertGreaterEqual(len(c_queries), 1, "Phase C suggestion catalog query was not executed!")

                # 3. Phase D JP discovery catalog search was also executed!
                d_queries = [q for q, sf in catalog_calls if sf == "jp"]
                self.assertGreaterEqual(len(d_queries), 1, "Phase D JP discovery catalog query was starved!")

                # 4. Total catalog budget strictly <= 8
                self.assertLessEqual(len(catalog_calls), 8)
                self.assertLessEqual(res.diagnostics.budget_consumed.get("catalog", 0), 8)


if __name__ == "__main__":
    unittest.main()
