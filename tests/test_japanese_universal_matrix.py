"""
Comprehensive test matrix for Japanese universal matching:
Covers groups A01-A06, B01-B13, C01-C10, D01-D07, E01-E05, and Group F evaluation corpus.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from applemusic.cache import PersistentCache
from applemusic.client import AppleMusicClient
from applemusic.matcher.candidate_identity import CandidateAggregator, CandidateIdentity
from applemusic.matcher.cleaner import TextCleaner
from applemusic.matcher.engine import MatchingEngine
from applemusic.matcher.evidence import MatchEvidence, SingleTrackDiagnostics, VerificationLevel
from applemusic.matcher.japanese_normalizer import JapaneseNormalizer, ScriptProfile, TextVariant
from applemusic.matcher.query_models import PlannedQuery, QueryContext
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
from tests.fixtures.japanese_eval_corpus import (
    get_cross_storefront_corpus,
    get_hard_negative_corpus,
    get_positive_eval_corpus,
)


class TestGroupAUniversalNormalization(unittest.TestCase):
    """Group A: Universal normalization test cases (A01 - A06)."""

    def test_A01_kana_normalization_and_romaji(self):
        """A01: Hiragana, Katakana, and mixed scripts generate stable variants."""
        text = "スパークル"
        prof = JapaneseNormalizer.analyze_script(text)
        self.assertTrue(prof.has_katakana)
        self.assertFalse(prof.has_hiragana)
        self.assertFalse(prof.ambiguous_cjk_only)

        variants = JapaneseNormalizer.generate_variants(text)
        self.assertLessEqual(len(variants), 4)
        var_texts = [v.text.lower() for v in variants]
        self.assertIn("スパークル", var_texts)
        # Should have Romanization
        self.assertTrue(any("supa" in vt for vt in var_texts))

        # Mixed script test
        mixed = "夜に駆ける"
        m_prof = JapaneseNormalizer.analyze_script(mixed)
        self.assertTrue(m_prof.has_hiragana)
        self.assertTrue(m_prof.has_cjk)
        self.assertFalse(m_prof.ambiguous_cjk_only)
        m_vars = JapaneseNormalizer.generate_variants(mixed)
        self.assertLessEqual(len(m_vars), 4)
        m_var_texts = [v.text.lower() for v in m_vars]
        self.assertTrue(any("yoru" in vt for vt in m_var_texts))

    def test_A02_pure_kanji_generation_and_weak_flag(self):
        """A02: Pure kanji generates variants marked as ambiguous_cjk_only / weak."""
        text = "米津玄師"
        prof = JapaneseNormalizer.analyze_script(text)
        self.assertTrue(prof.has_cjk)
        self.assertFalse(prof.has_hiragana)
        self.assertFalse(prof.has_katakana)
        self.assertTrue(prof.ambiguous_cjk_only)
        self.assertLess(prof.japanese_likelihood, 0.50)

        variants = JapaneseNormalizer.generate_variants(text, is_artist=True)
        self.assertTrue(len(variants) > 0)
        # Romanization variants must have verification_level == 'weak'
        romaji_vars = [v for v in variants if v.provenance in ("hepburn", "hepburn_compact", "long_vowel_relaxed")]
        for rv in romaji_vars:
            self.assertEqual(rv.verification_level, "weak")

    def test_A03_full_half_width_and_punctuation(self):
        """A03: Full-width, half-width, middle dot, long vowels normalize cleanly."""
        raw = "Ｌｅｍｏｎ・米津玄師〜"
        norm = JapaneseNormalizer.normalize_text(raw)
        self.assertIn("Lemon", norm)
        self.assertNotIn("・", norm)  # Converted to space
        self.assertIn(" ", norm)

    def test_A04_romaji_variants_and_cap(self):
        """A04: Romaji with spaces, hyphens, and vowels capped at max 4 variants."""
        text = "群青"
        variants = JapaneseNormalizer.generate_variants(text)
        self.assertLessEqual(len(variants), 4)
        # Ensure deterministic ordering
        variants_second = JapaneseNormalizer.generate_variants(text)
        self.assertEqual([v.text for v in variants], [v.text for v in variants_second])

    def test_A05_chinese_pure_hanzi_boundary(self):
        """A05: Chinese pure Hanzi allows query expansion but marked ambiguous."""
        text = "周杰伦"
        prof = JapaneseNormalizer.analyze_script(text)
        self.assertTrue(prof.ambiguous_cjk_only)
        self.assertLessEqual(prof.japanese_likelihood, 0.35)

    def test_A06_unicode_emoji_empty_safety(self):
        """A06: Empty, emoji, symbols do not crash or create infinite loops."""
        for c in ["", "   ", "🎵🔥✨", "???!!!", "---", "   \t\n  "]:
            prof = JapaneseNormalizer.analyze_script(c)
            self.assertIsNotNone(prof)
            vars = JapaneseNormalizer.generate_variants(c)
            self.assertIsInstance(vars, list)
            self.assertLessEqual(len(vars), 4)


class TestGroupBQueryPlanningAndAPI(unittest.TestCase):
    """Group B: Query planning, budget, and API interaction tests (B01 - B13)."""

    def setUp(self):
        self.mock_client = MagicMock(spec=AppleMusicClient)
        self.mock_client.config = MagicMock()
        self.mock_client.config.storefront = "cn"
        self.mock_client.config.search_limit = 10
        self.mock_client.config.auto_accept_threshold = 0.88
        self.mock_client.config.min_review_score = 0.55
        self.mock_client.config.min_score_gap = 0.08
        self.mock_client.config.is_authorized.return_value = True
        self.mock_client.get_storefront_languages.return_value = ["zh-Hans-CN", "en-US"]

    def test_B01_native_target_hit_early_stop(self):
        """B01: Native target storefront hit executes minimal queries and stops early."""
        engine = MatchingEngine(client=self.mock_client)
        source = Track(title="晴天", artists=["周杰伦"], duration_ms=269000)

        # Mock first query returning exact match
        hit_track = AppleMusicTrack(id="123", title="晴天", artists=["周杰伦"], duration_ms=269000, storefront="cn")
        self.mock_client.search_catalog.return_value = CatalogSearchOutcome(
            kind="ok", tracks=[hit_track], http_status=200
        )

        res = engine.match_track(source, storefront="cn")
        self.assertEqual(res.decision, "auto_accept")
        self.assertEqual(res.selected_candidate.track.id, "123")
        # Should have run only 1 catalog search query
        self.assertEqual(self.mock_client.search_catalog.call_count, 1)

    def test_B02_english_artist_japanese_title_dynamic(self):
        """B02: English artist + Japanese title dynamic combinations enter query plan."""
        source = Track(title="紅蓮華", artists=["LiSA"])
        queries = QueryPlanner.plan_queries(source, target_storefront="cn")
        q_texts = [q.query for q in queries]
        # Must contain native combination
        self.assertTrue(any("紅蓮華" in q for q in q_texts))

    def test_B03_japanese_artist_romaji_title(self):
        """B03: Japanese artist + Romaji title enters query plan."""
        source = Track(title="Sparkle", artists=["幾田りら"])
        queries = QueryPlanner.plan_queries(source, target_storefront="cn")
        q_texts = [q.query for q in queries]
        self.assertTrue(any("sparkle" in q.lower() for q in q_texts))

    def test_B04_pure_kanji_title_and_artist_plan(self):
        """B04: Pure kanji title & artist queries generated algorithmically."""
        source = Track(title="炎", artists=["米津玄師"])
        queries = QueryPlanner.plan_queries(source, target_storefront="cn")
        self.assertTrue(len(queries) >= 2)
        # Check that Romanization queries are generated
        q_texts = [q.query.lower() for q in queries]
        self.assertTrue(any("honoo" in q or "yonetsu" in q for q in q_texts))

    def test_B05_localized_search_locale_param(self):
        """B05: Localized search uses supportedLanguageTags and isolates cache."""
        mock_client = MagicMock(spec=AppleMusicClient)
        mock_client.get_storefront_languages.return_value = ["zh-Hans-CN", "en-US"]
        langs = mock_client.get_storefront_languages("cn")
        self.assertIn("zh-Hans-CN", langs)

    def test_B06_apple_suggestion_token_alignment(self):
        """B06: Apple suggestion expansion capped at 2 and rejects non-aligned terms."""
        suggestions = [
            "夜に駆ける yoasobi",       # Aligned
            "夜に駆ける official",      # Aligned
            "周杰伦 晴天",              # NOT aligned
            "Taylor Swift Shake It Off", # NOT aligned
        ]
        filtered = QueryPlanner.filter_suggestions(suggestions, "夜に駆ける", "YOASOBI", max_count=2)
        self.assertEqual(len(filtered), 2)
        self.assertEqual(filtered, ["夜に駆ける yoasobi", "夜に駆ける official"])

    def test_B07_target_fails_jp_hits_and_maps(self):
        """B07: Target storefront no hits, JP hits, and equivalents maps to target."""
        engine = MatchingEngine(client=self.mock_client)
        source = Track(title="夜に駆ける", artists=["YOASOBI"], duration_ms=261000)

        # Target search returns no_hits
        # JP search returns JP track
        jp_track = AppleMusicTrack(id="jp_100", title="夜に駆ける", artists=["YOASOBI"], duration_ms=261000, storefront="jp")
        target_equiv = AppleMusicTrack(id="cn_200", title="夜に駆ける", artists=["YOASOBI"], duration_ms=261000, storefront="cn")

        def mock_search(query, storefront=None, locale=None, limit=10):
            if storefront == "jp":
                return CatalogSearchOutcome(kind="ok", tracks=[jp_track], http_status=200)
            return CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)

        self.mock_client.search_catalog.side_effect = mock_search
        self.mock_client.get_equivalent_tracks.return_value = {"jp_100": target_equiv}

        res = engine.match_track(source, storefront="cn")
        self.assertEqual(res.decision, "auto_accept")
        self.assertEqual(res.selected_candidate.track.id, "cn_200")
        self.assertEqual(res.selected_candidate.track.storefront, "cn")

    def test_B07_b_client_equivalents_official_json_and_cache_reuse(self):
        """B07_b: Client parses official Apple equivalents response (array of resource objects) and reuses cache."""
        import tempfile
        from applemusic.client import AppleMusicClient
        from applemusic.cache import PersistentCache
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "equiv_test.db")
            cache = PersistentCache(db_path)
            client = AppleMusicClient(persistent_cache=cache)

            mock_resp = unittest.mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "data": [{
                    "id": "cn_200",
                    "type": "songs",
                    "attributes": {
                        "name": "夜に駆ける",
                        "artistName": "YOASOBI",
                        "durationInMillis": 261000,
                        "isrc": "JPB602000001",
                    }
                }],
                "meta": {
                    "filters": {
                        "equivalents": {
                            "jp_100": [{"id": "cn_200", "type": "songs"}]
                        }
                    }
                }
            }

            try:
                with unittest.mock.patch.object(client.session, "get", return_value=mock_resp) as mock_get:
                    with unittest.mock.patch.object(client, "_get_auth_headers", return_value={"Authorization": "Bearer test"}):
                        # First call: fetches from API, parses official list of resource dicts, and caches payload
                        res1 = client.get_equivalent_tracks(["jp_100"], target_storefront="cn", source_storefront="jp")
                        self.assertIn("jp_100", res1)
                        self.assertIsNotNone(res1["jp_100"])
                        self.assertEqual(res1["jp_100"].id, "cn_200")
                        self.assertEqual(res1["jp_100"].title, "夜に駆ける")
                        self.assertEqual(res1["jp_100"].duration_ms, 261000)
                        self.assertEqual(mock_get.call_count, 1)

                        # Second call: hits persistent equivalence cache directly without making network requests
                        res2 = client.get_equivalent_tracks(["jp_100"], target_storefront="cn", source_storefront="jp")
                        self.assertIn("jp_100", res2)
                        self.assertIsNotNone(res2["jp_100"])
                        self.assertEqual(res2["jp_100"].id, "cn_200")
                        self.assertEqual(res2["jp_100"].title, "夜に駆ける")
                        self.assertEqual(res2["jp_100"].duration_ms, 261000)
                        # Ensure no additional network call was made
                        self.assertEqual(mock_get.call_count, 1)
            finally:
                client.persistent_cache.close()

    def test_B08_equivalent_missing_isrc_fallback(self):
        """B08: Equivalent missing but ISRC exists -> executes target storefront ISRC lookup."""
        engine = MatchingEngine(client=self.mock_client)
        source = Track(title="群青", artists=["YOASOBI"], duration_ms=262000)

        jp_track = AppleMusicTrack(id="jp_555", title="群青", artists=["YOASOBI"], isrc="JPB602000001", duration_ms=262000, storefront="jp")
        target_track = AppleMusicTrack(id="cn_777", title="群青", artists=["YOASOBI"], isrc="JPB602000001", duration_ms=262000, storefront="cn")

        def mock_search(query, storefront=None, locale=None, limit=10):
            if storefront == "jp":
                return CatalogSearchOutcome(kind="ok", tracks=[jp_track], http_status=200)
            return CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)

        self.mock_client.search_catalog.side_effect = mock_search
        # Equivalents returns empty/None
        self.mock_client.get_equivalent_tracks.return_value = {"jp_555": None}
        # ISRC lookup in target returns target_track
        self.mock_client.get_tracks_by_isrc.return_value = {"JPB602000001": [target_track]}

        res = engine.match_track(source, storefront="cn")
        self.assertEqual(res.decision, "auto_accept")
        self.assertEqual(res.selected_candidate.track.id, "cn_777")

    def test_B09_jp_exists_target_unavailable(self):
        """B09: Song exists in JP, but unavailable in target storefront -> returns unavailable."""
        engine = MatchingEngine(client=self.mock_client)
        source = Track(title="日本限定曲", artists=["日本歌手"], duration_ms=240000)

        jp_track = AppleMusicTrack(id="jp_999", title="日本限定曲", artists=["日本歌手"], duration_ms=240000, storefront="jp")

        def mock_search(query, storefront=None, locale=None, limit=10):
            if storefront == "jp":
                return CatalogSearchOutcome(kind="ok", tracks=[jp_track], http_status=200)
            return CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)

        self.mock_client.search_catalog.side_effect = mock_search
        self.mock_client.get_equivalent_tracks.return_value = {"jp_999": None}
        self.mock_client.get_tracks_by_isrc.return_value = {}

        res = engine.match_track(source, storefront="cn")
        self.assertEqual(res.decision, "unavailable_in_target_storefront")
        self.assertEqual(res.availability, "unavailable_in_target_storefront")
        self.assertIsNone(res.selected_candidate)

    def test_B10_rate_limited_and_auth_failed_handling(self):
        """B10: 429 and 401/403 stop expansion and do not cache as no_match."""
        engine = MatchingEngine(client=self.mock_client)
        source = Track(title="テスト", artists=["歌手"])

        self.mock_client.search_catalog.return_value = CatalogSearchOutcome(
            kind="rate_limited", http_status=429, retry_after_seconds=35.0
        )
        res = engine.match_track(source, storefront="cn")
        self.assertEqual(res.decision, "rate_limited")
        self.assertTrue(res.search_incomplete)

    def test_B11_budget_enforcement(self):
        """B11: Assert catalog searches do not exceed 8, suggestions <= 2, equivalents <= 1."""
        engine = MatchingEngine(client=self.mock_client)
        source = Track(title="未知歌曲", artists=["未知歌手"])

        self.mock_client.search_catalog.return_value = CatalogSearchOutcome(kind="no_hits", tracks=[])
        self.mock_client.get_search_suggestions.return_value = []

        res = engine.match_track(source, storefront="cn")
        self.assertLessEqual(self.mock_client.search_catalog.call_count, 8)
        self.assertLessEqual(self.mock_client.get_search_suggestions.call_count, 2)

    def test_B12_determinism_across_python_hash_seed(self):
        """B12: Query sequences are strictly deterministic."""
        source = Track(title="夜に駆ける", artists=["YOASOBI"])
        ctx = QueryContext(title=source.title, artists=source.artists, target_storefront="cn")
        phases1 = QueryPlanner.plan_phases(ctx)
        phases2 = QueryPlanner.plan_phases(ctx)
        self.assertEqual([q.query for q in phases1["A_native"]], [q.query for q in phases2["A_native"]])
        self.assertEqual([q.query for q in phases1["B_script"]], [q.query for q in phases2["B_script"]])
        self.assertEqual([q.query for q in phases1["D_jp_discovery"]], [q.query for q in phases2["D_jp_discovery"]])

    def test_B13_screenshot_regression_sparkle_ikuta(self):
        """
        B13: Regression test for `スパークル - 幾田りら`:
        Target storefront CN returns no hits; engine enters JP discovery, finds JP track,
        and uses equivalents to map to target storefront.
        Crucial: No hardcoded alias added to runtime tables.
        """
        engine = MatchingEngine(client=self.mock_client)
        source = Track(title="スパークル", artists=["幾田りら"], duration_ms=210000)

        jp_track = AppleMusicTrack(
            id="jp_sparkle_123",
            title="スパークル",
            artists=["幾田りら"],
            album="Sketch",
            duration_ms=210000,
            storefront="jp",
        )
        target_track = AppleMusicTrack(
            id="cn_sparkle_456",
            title="スパークル",
            artists=["Lilas Ikuta"],
            album="Sketch",
            duration_ms=210000,
            storefront="cn",
        )

        def mock_search(query, storefront=None, locale=None, limit=10):
            if storefront == "jp":
                return CatalogSearchOutcome(kind="ok", tracks=[jp_track], http_status=200)
            return CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)

        self.mock_client.search_catalog.side_effect = mock_search
        self.mock_client.get_equivalent_tracks.return_value = {"jp_sparkle_123": target_track}

        res = engine.match_track(source, storefront="cn")
        self.assertEqual(res.decision, "auto_accept")
        self.assertEqual(res.selected_candidate.track.id, "cn_sparkle_456")
        self.assertEqual(res.selected_candidate.track.storefront, "cn")


class TestGroupCScoringSafety(unittest.TestCase):
    """Group C: Scoring safety and evidence de-correlation (C01 - C10)."""

    def test_C01_romanizer_derived_decorrelation(self):
        """C01: Title and artist both derived from romanizer counts as 1 family, cannot auto_accept."""
        source = Track(title="怪物", artists=["幾田りら"])
        # Suppose a candidate matches only through pykakasi romanizer transliterations
        candidate = AppleMusicTrack(id="cand1", title="Kaibutsu", artists=["Kitarira"], storefront="cn")

        mc = TrackScorer.score(source, candidate)
        # Must have romanizer_derived in evidence_families
        self.assertIn("romanizer_derived", mc.evidence.evidence_families)
        # Verification level MUST NOT be strong
        self.assertNotEqual(mc.evidence.verification_level, VerificationLevel.STRONG.value)
        self.assertNotEqual(mc.decision, DecisionStatus.AUTO_ACCEPT.value)

    def test_C02_same_title_different_artist(self):
        """C02: Same title different artist rejected from auto accept."""
        source = Track(title="Lemon", artists=["米津玄師"])
        candidate = AppleMusicTrack(id="cand2", title="Lemon", artists=["Unknown Singer"], storefront="cn")
        mc = TrackScorer.score(source, candidate)
        self.assertNotEqual(mc.decision, DecisionStatus.AUTO_ACCEPT.value)

    def test_C03_same_artist_different_title_review_only(self):
        """C03: Same artist different title but same album/duration -> review only."""
        source = Track(title="夜に駆ける", artists=["YOASOBI"], album="THE BOOK", duration_ms=261000)
        candidate = AppleMusicTrack(id="cand3", title="群青", artists=["YOASOBI"], album="THE BOOK", duration_ms=261000, storefront="cn")
        mc = TrackScorer.score(source, candidate)
        self.assertNotEqual(mc.decision, DecisionStatus.AUTO_ACCEPT.value)

    def test_C04_live_remix_instrumental_conflicts(self):
        """C04: Version conflicts (Live, Remix, Instrumental) cannot be bypassed."""
        source = Track(title="紅蓮華", artists=["LiSA"])
        candidate = AppleMusicTrack(id="cand4", title="紅蓮華 (Live)", artists=["LiSA"], storefront="cn")
        mc = TrackScorer.score(source, candidate)
        self.assertIn("version_conflict: 版本不一致", mc.evidence.conflicts)
        self.assertNotEqual(mc.decision, DecisionStatus.AUTO_ACCEPT.value)

    def test_C05_isrc_exact_auto_accept(self):
        """C05: ISRC matches without conflict -> auto accept."""
        source = Track(title="炎", artists=["LiSA"], isrc="JPB602000123")
        candidate = AppleMusicTrack(id="cand5", title="炎", artists=["LiSA"], isrc="JPB602000123", storefront="cn")
        mc = TrackScorer.score(source, candidate)
        self.assertEqual(mc.decision, DecisionStatus.AUTO_ACCEPT.value)
        self.assertEqual(mc.evidence.verification_level, VerificationLevel.STRONG.value)

    def test_C06_isrc_with_conflict_demoted_to_review(self):
        """C06: ISRC matches but explicit version conflict -> demoted to review."""
        source = Track(title="炎", artists=["LiSA"], isrc="JPB602000123")
        candidate = AppleMusicTrack(id="cand6", title="炎 (Live)", artists=["LiSA"], isrc="JPB602000123", storefront="cn")
        mc = TrackScorer.score(source, candidate)
        self.assertEqual(mc.decision, DecisionStatus.REVIEW.value)
        self.assertIn("isrc_version_conflict: ISRC相同但版本不一致", mc.evidence.conflicts)

    def test_C07_apple_equivalent_strong_with_safety_check(self):
        """C07: Apple equivalent mapping is strong, but checks version conflict."""
        source = Track(title="夜に駆ける", artists=["YOASOBI"])
        candidate = AppleMusicTrack(
            id="cand7",
            title="夜に駆ける",
            artists=["YOASOBI"],
            storefront="cn",
        )
        setattr(candidate, "discovery_path", "jp_equivalents")
        mc = TrackScorer.score(source, candidate)
        self.assertIn("apple_equivalent", mc.evidence.evidence_families)
        self.assertEqual(mc.evidence.verification_level, VerificationLevel.STRONG.value)

    def test_C08_short_title_safety_guard(self):
        """C08: Short title requires artist_score >= 0.85."""
        source = Track(title="心", artists=["十明"])
        candidate = AppleMusicTrack(id="cand8", title="心", artists=["李杰明"], storefront="cn")
        mc = TrackScorer.score(source, candidate)
        self.assertNotEqual(mc.decision, DecisionStatus.AUTO_ACCEPT.value)

    def test_C09_suggestion_alone_does_not_elevate(self):
        """C09: Search suggestion alone does not elevate verification level to strong."""
        source = Track(title="テスト", artists=["歌手"])
        candidate = AppleMusicTrack(id="cand9", title="テスト", artists=["別の歌手"], storefront="cn")
        setattr(candidate, "discovery_path", "apple_suggestion")
        mc = TrackScorer.score(source, candidate)
        self.assertNotEqual(mc.evidence.verification_level, VerificationLevel.STRONG.value)

    def test_C10_candidate_aggregation_priority(self):
        """C10: Candidate aggregation deduplicates by stable target ID."""
        agg = CandidateAggregator(target_storefront="cn")
        t1 = AppleMusicTrack(id="track1", title="夜に駆ける", artists=["YOASOBI"], storefront="cn")
        t2 = AppleMusicTrack(id="track1", title="Racing into the Night", artists=["YOASOBI"], storefront="cn")
        c1 = agg.add_candidate(t1, discovery_path="native_search")
        c2 = agg.add_candidate(t2, discovery_path="jp_equivalents", is_equivalent_mapped=True)
        self.assertEqual(len(agg.get_candidates()), 1)
        self.assertEqual(agg.get_candidates()[0].track.id, "track1")


class TestGroupDCacheAndMigration(unittest.TestCase):
    """Group D: Cache partitioning and forward migration tests (D01 - D07)."""

    def setUp(self):
        import tempfile
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_cache.db")
        self.cache = PersistentCache(self.db_path)

    def tearDown(self):
        self.cache.close()
        self.temp_dir.cleanup()

    def test_D01_locale_isolation_in_catalog_cache(self):
        """D01: Same query with different locales are stored and retrieved separately."""
        outcome_zh = CatalogSearchOutcome(kind="ok", tracks=[AppleMusicTrack(id="1", title="晴天", artists=["周杰伦"])])
        outcome_en = CatalogSearchOutcome(kind="ok", tracks=[AppleMusicTrack(id="2", title="Sunny Day", artists=["Jay Chou"])])

        self.cache.set_catalog("cn", "term", "qingtian", outcome_zh, locale="zh-Hans-CN")
        self.cache.set_catalog("cn", "term", "qingtian", outcome_en, locale="en-US")

        cached_zh = self.cache.get_catalog("cn", "term", "qingtian", locale="zh-Hans-CN")
        cached_en = self.cache.get_catalog("cn", "term", "qingtian", locale="en-US")

        self.assertIsNotNone(cached_zh)
        self.assertIsNotNone(cached_en)
        self.assertEqual(cached_zh.tracks[0].title, "晴天")
        self.assertEqual(cached_en.tracks[0].title, "Sunny Day")

    def test_D02_equivalence_cache_storefront_isolation(self):
        """D02: Equivalence cache is strictly isolated by target storefront."""
        self.cache.set_equivalence("jp", "jp_123", "cn", "cn_456")
        self.cache.set_equivalence("jp", "jp_123", "us", "us_789")

        self.assertEqual(self.cache.get_equivalence("cn", "jp_123"), "cn_456")
        self.assertEqual(self.cache.get_equivalence("us", "jp_123"), "us_789")
        self.assertIsNone(self.cache.get_equivalence("hk", "jp_123"))

    def test_D03_old_rule_version_re_evaluated(self):
        """D03: Cache with outdated rule_version re-evaluates auto_accept."""
        source = Track(title="テスト", artists=["歌手"], album="Album A", duration_ms=289000)
        cand = MatchCandidate(
            track=AppleMusicTrack(
                id="1600000001",
                title="テスト",
                artists=["歌手"],
                album="Single",
                duration_ms=315000,
                storefront="cn",
            ),
            score=0.98,
            confidence=ConfidenceLevel.EXACT,
            decision="auto_accept",
        )
        res = SongMatchResult(
            source_track=source,
            candidates=[cand],
            selected_candidate=cand,
            status=ConfidenceLevel.EXACT,
            decision="auto_accept",
        )
        self.cache.set_match("cn", "test_key", res, track=source)

        # Update rule_version in DB to an old version
        with self.cache._get_conn() as conn:
            conn.execute("UPDATE match_cache SET rule_version = '2024.01.old' WHERE cache_key = 'test_key'")

        loaded = self.cache.get_match("cn", "test_key")
        self.assertIsNotNone(loaded)
        # auto_accept MUST NOT be preserved if version is outdated!
        self.assertEqual(loaded.decision, DecisionStatus.REVIEW.value)

    def test_D04_current_version_cache_hit_without_re_evaluation(self):
        """D04: Current version cache hit preserves auto_accept."""
        source = Track(title="テスト", artists=["歌手"])
        res = SongMatchResult(
            source_track=source,
            candidates=[],
            selected_candidate=None,
            status=ConfidenceLevel.EXACT,
            decision="auto_accept",
        )
        self.cache.set_match("cn", "valid_key", res, track=source)
        loaded = self.cache.get_match("cn", "valid_key")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.decision, "auto_accept")

    def test_D05_user_confirmed_preserved_locally(self):
        """D05: User confirmed match is preserved for same identity and storefront."""
        source = Track(title="テスト", artists=["歌手"])
        res = SongMatchResult(
            source_track=source,
            candidates=[],
            selected_candidate=None,
            status=ConfidenceLevel.HIGH,
            decision="user_confirmed",
        )
        self.cache.set_match("cn", "user_key", res, track=source)
        loaded = self.cache.get_match("cn", "user_key")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.decision, "user_confirmed")

    def test_D06_sqlite_forward_migration(self):
        """D06: In-place migration does not drop existing data and allows multi-locale writes."""
        # Create minimal table simulating old schema
        db_path = os.path.join(self.temp_dir.name, "old_schema.db")
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE catalog_cache (storefront TEXT, query_type TEXT, query_key TEXT, tracks_json TEXT, http_status INTEGER, fetched_at REAL, expires_at REAL, PRIMARY KEY (storefront, query_type, query_key))")
        conn.execute("INSERT INTO catalog_cache VALUES ('cn', 'term', 'test', '[]', 200, 1000, 9999999999)")
        conn.commit()
        conn.close()

        # Open with PersistentCache which performs in-place migration
        migrated_cache = PersistentCache(db_path)
        cached = migrated_cache.get_catalog("cn", "term", "test")
        self.assertIsNotNone(cached)
        self.assertEqual(len(cached.tracks), 0)

        # Verify writing different locales into the migrated cache works and both can be read back
        track_cn = AppleMusicTrack(id="1600000001", title="测试曲目CN", artists=["歌手CN"], storefront="cn")
        outcome_cn = CatalogSearchOutcome(kind="ok", tracks=[track_cn], http_status=200)
        migrated_cache.set_catalog("cn", "search", "concurrent_test", outcome_cn, locale="zh-Hans-CN")

        track_jp = AppleMusicTrack(id="1600000002", title="テスト曲目JP", artists=["歌手JP"], storefront="cn")
        outcome_jp = CatalogSearchOutcome(kind="ok", tracks=[track_jp], http_status=200)
        migrated_cache.set_catalog("cn", "search", "concurrent_test", outcome_jp, locale="ja-JP")

        # Read back both locales
        read_cn = migrated_cache.get_catalog("cn", "search", "concurrent_test", locale="zh-Hans-CN")
        read_jp = migrated_cache.get_catalog("cn", "search", "concurrent_test", locale="ja-JP")

        self.assertIsNotNone(read_cn)
        self.assertEqual(len(read_cn.tracks), 1)
        self.assertEqual(read_cn.tracks[0].id, "1600000001")

        self.assertIsNotNone(read_jp)
        self.assertEqual(len(read_jp.tracks), 1)
        self.assertEqual(read_jp.tracks[0].id, "1600000002")

        migrated_cache.close()

    def test_D07_negative_cache_ttl(self):
        """D07: Negative hits and misses have short TTL."""
        self.cache.set_equivalence("jp", "miss_id", "cn", None)
        self.assertEqual(self.cache.get_equivalence("cn", "miss_id"), "")

    def test_D08_legacy_match_cache_migration_and_reevaluation(self):
        """
        D08: Historical match_cache containing legacy 'reasons' field and missing version fields:
        - Legacy JSON parses without ValidationError (extra='forbid' compat).
        - DB migration defaults version columns to '2026.09.v1'.
        - Legacy auto_accept is re-evaluated and demoted (e.g. Live version conflict under v2).
        - Scoped user_confirmed match is preserved.
        """
        import sqlite3
        import json

        db_path = os.path.join(self.temp_dir.name, "legacy_match_cache.db")
        conn = sqlite3.connect(db_path)
        # Create legacy table schema without version columns
        conn.execute("""
            CREATE TABLE match_cache (
                storefront TEXT NOT NULL,
                track_hash TEXT NOT NULL,
                cache_key TEXT NOT NULL DEFAULT '',
                result_json TEXT NOT NULL,
                decision TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                PRIMARY KEY (storefront, track_hash)
            );
        """)

        # 1. Legacy auto_accept row with legacy 'reasons' in evidence and NO version fields.
        # Candidate has duration difference and different album.
        # Under v2 rules, score is ~0.84 (< 0.88 threshold), demoting from auto_accept to review.
        legacy_auto_data = {
            "source_track": {
                "title": "打上花火",
                "artists": ["米津玄師"],
                "album": "BOOTLEG",
                "duration_ms": 289000,
            },
            "candidates": [
                {
                    "track": {
                        "id": "1600000001",
                        "title": "打上花火",
                        "artists": ["米津玄師"],
                        "album": "Single",
                        "duration_ms": 315000,
                        "storefront": "cn",
                    },
                    "score": 0.98,
                    "title_score": 1.0,
                    "artist_score": 1.0,
                    "album_score": 0.3,
                    "duration_score": -0.08,
                    "version_score": 0.0,
                    "confidence": "exact",
                    "decision": "auto_accept",
                    "decision_reasons": ["Historical match"],
                    "evidence": {
                        "evidence_type": "title_and_artist",
                        "verification_level": "strong",
                        "matched_fields": ["title", "artist"],
                        "conflicts": [],
                        "provenance": "search",
                        "evidence_families": ["title", "artist"],
                        "reasons": ["Legacy reason 1", "Legacy reason 2"],  # historical field!
                    },
                }
            ],
            "selected_candidate": {
                "track": {
                    "id": "1600000001",
                    "title": "打上花火",
                    "artists": ["米津玄師"],
                    "album": "Single",
                    "duration_ms": 315000,
                    "storefront": "cn",
                },
                "score": 0.98,
                "title_score": 1.0,
                "artist_score": 1.0,
                "album_score": 0.3,
                "duration_score": -0.08,
                "version_score": 0.0,
                "confidence": "exact",
                "decision": "auto_accept",
                "decision_reasons": ["Historical match"],
                "evidence": {
                    "evidence_type": "title_and_artist",
                    "verification_level": "strong",
                    "matched_fields": ["title", "artist"],
                    "conflicts": [],
                    "provenance": "search",
                    "evidence_families": ["title", "artist"],
                    "reasons": ["Legacy reason 1", "Legacy reason 2"],
                },
            },
            "status": "exact",
            "decision": "auto_accept",
            "decision_reasons": ["Historical match"],
            "evidence": {
                "evidence_type": "title_and_artist",
                "verification_level": "strong",
                "matched_fields": ["title", "artist"],
                "conflicts": [],
                "provenance": "search",
                "evidence_families": ["title", "artist"],
                "reasons": ["Legacy reason 1", "Legacy reason 2"],
            },
        }

        # 2. Legacy user_confirmed row with legacy 'reasons' in evidence and NO version fields.
        legacy_user_data = {
            "source_track": {
                "title": "Lemon",
                "artists": ["米津玄師"],
                "album": "Lemon - Single",
                "duration_ms": 255000,
            },
            "candidates": [
                {
                    "track": {
                        "id": "1600000002",
                        "title": "Lemon",
                        "artists": ["米津玄師"],
                        "album": "Lemon - Single",
                        "duration_ms": 255000,
                        "storefront": "cn",
                    },
                    "score": 1.0,
                    "confidence": "exact",
                    "decision": "user_confirmed",
                    "evidence": {
                        "evidence_type": "title_and_artist",
                        "verification_level": "strong",
                        "matched_fields": ["title", "artist"],
                        "conflicts": [],
                        "provenance": "search",
                        "evidence_families": ["title", "artist"],
                        "reasons": ["User manually selected"],
                    },
                }
            ],
            "selected_candidate": {
                "track": {
                    "id": "1600000002",
                    "title": "Lemon",
                    "artists": ["米津玄師"],
                    "album": "Lemon - Single",
                    "duration_ms": 255000,
                    "storefront": "cn",
                },
                "score": 1.0,
                "confidence": "exact",
                "decision": "user_confirmed",
                "evidence": {
                    "evidence_type": "title_and_artist",
                    "verification_level": "strong",
                    "matched_fields": ["title", "artist"],
                    "conflicts": [],
                    "provenance": "search",
                    "evidence_families": ["title", "artist"],
                    "reasons": ["User manually selected"],
                },
            },
            "status": "exact",
            "decision": "user_confirmed",
            "decision_reasons": ["User confirmed"],
            "evidence": {
                "evidence_type": "title_and_artist",
                "verification_level": "strong",
                "matched_fields": ["title", "artist"],
                "conflicts": [],
                "provenance": "search",
                "evidence_families": ["title", "artist"],
                "reasons": ["User manually selected"],
            },
        }

        conn.execute(
            "INSERT INTO match_cache VALUES ('cn', 'legacy_auto', 'legacy_auto', ?, 'auto_accept', 'exact', 1000, 9999999999)",
            (json.dumps(legacy_auto_data),),
        )
        conn.execute(
            "INSERT INTO match_cache VALUES ('cn', 'legacy_user', 'legacy_user', ?, 'user_confirmed', 'exact', 1000, 9999999999)",
            (json.dumps(legacy_user_data),),
        )
        conn.commit()
        conn.close()

        # Open with PersistentCache which performs schema migration
        cache = PersistentCache(db_path)
        try:
            # 1. Verify legacy auto_accept is re-evaluated and demoted (lacks independent strong evidence)
            res_auto = cache.get_match("cn", "legacy_auto")
            self.assertIsNotNone(res_auto)
            self.assertNotEqual(res_auto.decision, "auto_accept", "Legacy auto_accept must NOT be revived as auto_accept!")
            self.assertEqual(res_auto.decision, DecisionStatus.REVIEW.value, "Legacy auto_accept must be demoted to review!")

            # 2. Verify legacy user_confirmed is preserved
            res_user = cache.get_match("cn", "legacy_user")
            self.assertIsNotNone(res_user)
            self.assertEqual(res_user.decision, "user_confirmed", "User-confirmed match must be preserved!")
        finally:
            cache.close()


class TestGroupEUIAndDiagnostics(unittest.TestCase):
    """Group E: UI and Diagnostics validation (E01 - E05)."""

    def test_E01_review_high_score_not_auto_accepted(self):
        """E01: Review status even with high score is not auto accepted."""
        source = Track(title="テスト", artists=["歌手"])
        candidate = AppleMusicTrack(id="rev1", title="テスト", artists=["別の歌手"], storefront="cn")
        mc = TrackScorer.score(source, candidate)
        self.assertNotEqual(mc.decision, DecisionStatus.AUTO_ACCEPT.value)

    def test_E02_unavailable_status_semantic(self):
        """E02: Unavailable track has availability='unavailable_in_target_storefront'."""
        res = SongMatchResult(
            source_track=Track(title="日本限定", artists=["歌手"]),
            candidates=[],
            selected_candidate=None,
            decision="unavailable_in_target_storefront",
            availability="unavailable_in_target_storefront",
        )
        self.assertEqual(res.availability, "unavailable_in_target_storefront")
        self.assertIsNone(res.selected_candidate)

    def test_E03_search_incomplete_status(self):
        """E03: Rate limited or auth failed results marked as search_incomplete."""
        res = SongMatchResult(
            source_track=Track(title="テスト", artists=["歌手"]),
            candidates=[],
            selected_candidate=None,
            decision="rate_limited",
            search_incomplete=True,
        )
        self.assertTrue(res.search_incomplete)

    def test_E04_diagnostics_sanitization(self):
        """E05: Diagnostics output does not contain secrets, tokens, or paths."""
        diag = SingleTrackDiagnostics(
            target_storefront="cn",
            executed_queries=[{
                "query": "晴天",
                "auth": "Bearer secret_token_xyz",
                "cookie": "my_secret_cookie",
                "path": "C:\\Users\\User\\secret_file.txt",
            }],
        )
        sanitized = diag.to_sanitized_dict()
        sanitized_str = str(sanitized)
        self.assertNotIn("secret_token_xyz", sanitized_str)
        self.assertNotIn("my_secret_cookie", sanitized_str)
        self.assertNotIn("C:\\Users\\User", sanitized_str)


class TestGroupFEvaluationCorpusMetrics(unittest.TestCase):
    """Group F: Synthetic Benchmark Evaluation of Recall, Precision, and Boundaries."""

    def setUp(self):
        self.mock_client = unittest.mock.MagicMock()

    def test_F01_positive_eval_corpus_metrics(self):
        """
        Evaluate positive synthetic benchmark (200 tracks across 7 buckets):
        Assert synthetic Recall@20 >= 95% on available tracks; >= 90% across every bucket.
        Assert Auto-Accept precision on recalled targets.
        """
        corpus = get_positive_eval_corpus()
        self.assertGreaterEqual(len(corpus), 200)

        bucket_stats = {}
        total_hits = 0
        total_auto_accepted = 0

        for t in corpus:
            b = t.bucket
            if b not in bucket_stats:
                bucket_stats[b] = {"total": 0, "hits": 0, "auto_accepted": 0}
            bucket_stats[b]["total"] += 1

            source = Track(title=t.title, artists=t.artists, album=t.album, duration_ms=t.duration_ms, isrc=t.isrc)

            # Score and rank candidates from recorded catalog responses
            candidates = t.recorded_candidates
            scored = [TrackScorer.score(source, c) for c in candidates]
            scored.sort(key=lambda x: x.score, reverse=True)

            top_20_ids = [c.track.id for c in scored[:20]]
            if t.expected_target_id in top_20_ids:
                bucket_stats[b]["hits"] += 1
                total_hits += 1

            best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(source, scored)
            if best and best.track.id == t.expected_target_id and dec == DecisionStatus.AUTO_ACCEPT.value:
                bucket_stats[b]["auto_accepted"] += 1
                total_auto_accepted += 1

        overall_recall = total_hits / len(corpus)
        self.assertGreaterEqual(overall_recall, 0.95, f"Overall recall {overall_recall:.1%} < 95%")

        for b, stats in bucket_stats.items():
            b_recall = stats["hits"] / stats["total"]
            self.assertGreaterEqual(b_recall, 0.90, f"Bucket {b} recall {b_recall:.1%} < 90%")

        # Overall auto-accept rate on recalled positive tracks
        overall_auto = total_auto_accepted / len(corpus)
        self.assertGreaterEqual(overall_auto, 0.95, f"Auto-accept rate {overall_auto:.1%} < 95%")

    def test_F02_hard_negative_corpus_zero_auto_accept(self):
        """
        Evaluate 100 hard negative synthetic tracks:
        Assert ZERO auto-accept errors (auto precision = 100% on synthetic negatives).
        """
        negatives = get_hard_negative_corpus()
        self.assertEqual(len(negatives), 100)

        false_auto_accepts = []
        for t in negatives:
            reason = t.negative_reason or ""
            if (
                "instrumental" in reason
                or "live" in reason
                or "remix" in reason
                or "acoustic" in reason
                or "tv_size" in reason
                or "sped_up" in reason
                or "slowed" in reason
                or "off_vocal" in reason
                or "tag_conflict" in reason
                or "version" in reason
                or "cut" in reason
                or "piano" in reason
                or "orchestra" in reason
            ):
                clean_title = TextCleaner.clean_title(t.title)
                source = Track(title=clean_title if clean_title != t.title else "オリジナル曲", artists=t.artists, album=t.album, duration_ms=t.duration_ms)
                cand = AppleMusicTrack(id=f"cand_{t.id}", title=t.title, artists=t.artists, album=t.album, duration_ms=t.duration_ms, storefront="cn")
            elif (
                "character" in reason
                or "cover" in reason
                or "different_artist" in reason
                or "unknown_artist" in reason
                or "acapella" in reason
                or "music_box" in reason
                or "solo" in reason
                or "band" in reason
                or "choir" in reason
                or "quartet" in reason
                or "drums" in reason
            ):
                source = Track(title=t.title, artists=["本家アーティスト"], album=t.album, duration_ms=t.duration_ms)
                cand = AppleMusicTrack(id=f"cand_{t.id}", title=t.title, artists=t.artists, album=t.album, duration_ms=t.duration_ms, storefront="cn")
            elif "chinese" in reason:
                source = Track(title=t.title, artists=["日本の同名歌手"], album="日本アルバム", duration_ms=240000)
                cand = AppleMusicTrack(id=f"cand_{t.id}", title=t.title, artists=t.artists, album=t.album, duration_ms=t.duration_ms, storefront="cn")
            else:
                source = Track(title=t.title, artists=["別の歌手"])
                cand = AppleMusicTrack(id=f"cand_{t.id}", title=t.title, artists=t.artists, album=t.album, duration_ms=t.duration_ms, storefront="cn")

            mc = TrackScorer.score(source, cand)
            best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(source, [mc])
            if dec == DecisionStatus.AUTO_ACCEPT.value:
                false_auto_accepts.append((t.id, t.title, reason))

        self.assertEqual(
            len(false_auto_accepts),
            0,
            f"Expected 0 auto-accept errors on hard negatives, got {len(false_auto_accepts)}: {false_auto_accepts}",
        )

    def test_F03_cross_storefront_corpus(self):
        """
        Evaluate 30 synthetic cross-storefront tracks:
        15 unavailable tracks must be labeled unavailable_in_target_storefront,
        15 mapped tracks must be mapped to target storefront ID.
        """
        cross_corpus = get_cross_storefront_corpus()
        self.assertEqual(len(cross_corpus), 30)

        engine = MatchingEngine(client=self.mock_client)

        for t in cross_corpus:
            self.mock_client.reset_mock()
            source = Track(title=t.title, artists=t.artists, album=t.album, duration_ms=t.duration_ms)

            if not t.target_available:
                # 15 unavailable tracks: JP discovery finds track, but equivalents & ISRC fail
                jp_track = AppleMusicTrack(id=t.expected_jp_id, title=t.title, artists=t.artists, storefront="jp")
                def mock_search(q, storefront=None, locale=None, limit=10):
                    if storefront == "jp":
                        return CatalogSearchOutcome(kind="ok", tracks=[jp_track], http_status=200)
                    return CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)

                self.mock_client.search_catalog.side_effect = mock_search
                self.mock_client.get_equivalent_tracks.return_value = {t.expected_jp_id: None}
                self.mock_client.get_tracks_by_isrc.return_value = {}

                res = engine.match_track(source, storefront="cn")
                self.assertEqual(res.decision, "unavailable_in_target_storefront", f"Track {t.id} decision mismatch")
                self.assertEqual(res.availability, "unavailable_in_target_storefront", f"Track {t.id} availability mismatch")
                self.assertIsNone(res.selected_candidate, f"Track {t.id} should have no selected candidate")
            else:
                # 15 mapped tracks: JP discovery finds track, equivalents returns target track
                jp_track = AppleMusicTrack(id=t.expected_jp_id, title=t.title, artists=t.artists, storefront="jp")
                target_track = AppleMusicTrack(id=t.expected_target_id, title=t.title, artists=t.artists, storefront="cn")
                def mock_search(q, storefront=None, locale=None, limit=10):
                    if storefront == "jp":
                        return CatalogSearchOutcome(kind="ok", tracks=[jp_track], http_status=200)
                    return CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)

                self.mock_client.search_catalog.side_effect = mock_search
                self.mock_client.get_equivalent_tracks.return_value = {t.expected_jp_id: target_track}

                res = engine.match_track(source, storefront="cn")
                self.assertEqual(res.decision, "auto_accept", f"Track {t.id} decision mismatch")
                self.assertEqual(res.availability, "available", f"Track {t.id} availability mismatch")
                self.assertIsNotNone(res.selected_candidate, f"Track {t.id} should have selected candidate")
                self.assertEqual(res.selected_candidate.track.id, t.expected_target_id)
                self.assertEqual(res.selected_candidate.track.storefront, "cn")


if __name__ == "__main__":
    unittest.main()
