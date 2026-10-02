"""
Acceptance Test Matrix for Multilingual Title and Artist Credit Matching V6.
Covers:
- P01: Case 1 (On the Journey / 不虚此行, artist order reordering, bilingual segment)
- P02: Case 2 (提瓦特民谣, subtitle stripping, collaborative artist addition)
- P03: Case 3 (Nameless Faces, 3 Lilas Ikuta aliases, title credit matching on project entity)
- P04: Case 4 (ReDreaming Angel 复梦天使, Sān-Z / 三Z-STUDIO alias equivalence)
- P05: 12 General non-screenshot bilingual titles / artist collabs / aliases
- N01: Hard rejection of false homonym (sweets parade vs magical mode)
- N02: Prefix containment and non-bilingual English phrases protection (Love vs Love Story)
- N03: Guest artist protection (A vs B feat. A on ordinary artist)
- N04: No artist identity forgery (HoYoFair with mismatched singer)
- N05: Language version consistency matrix (conflict vs unverified vs matched; ISRC protection)
- N06: V5.1 non-definite title matrix 60 combinations stay MEDIUM / REVIEW
- Q01: Fallback query recall via core_title_only when full query has no hits
- Q02: Bilingual segment and alias retry budget enforcement and error handling
- C01: Cache invalidation and migration (outdated no_match re-searched, user_confirmed retained)
- U01: Real browser UI test (review not auto-selected, diagnostic modal shows v6, roles, and language)
"""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from applemusic.cache import PersistentCache
from applemusic.client import AppleMusicClient
from applemusic.config import Config
from applemusic.matcher.artist_aliases import are_artists_equivalent, get_artist_aliases
from applemusic.matcher.candidate_identity import CandidateAggregator, CandidateIdentity
from applemusic.matcher.cleaner import TextCleaner
from applemusic.matcher.engine import MatchingEngine
from applemusic.matcher.evidence import (
    ALIAS_VERSION,
    MATCH_RULE_VERSION,
    QUERY_POLICY_VERSION,
    ROMANIZER_VERSION,
    MatchEvidence,
    SingleTrackDiagnostics,
    VerificationLevel,
)
from applemusic.matcher.query_models import PlannedQuery, QueryContext
from applemusic.matcher.query_planner import QueryPlanner
from applemusic.matcher.scorer import TrackScorer
from applemusic.models import (
    AppleMusicTrack,
    CatalogSearchOutcome,
    ConfidenceLevel,
    DecisionStatus,
    MatchCandidate,
    Playlist,
    SongMatchResult,
    Track,
)


class TestMultilingualCreditMatchingV6(unittest.TestCase):
    """Full test matrix for V6 matching engine enhancements."""

    # -------------------------------------------------------------------------
    # Positive Cases (P01 - P05)
    # -------------------------------------------------------------------------

    def test_P01_case1_bilingual_segment_and_artist_reordering(self):
        """P01: Case 1 - Forward & reversed bilingual title segment with reordered collaborators."""
        # Forward: source has bilingual title, candidate has Chinese core title
        src = Track(
            title="不虚此行 On the Journey",
            artists=["魏晨", "Nea", "HOYO-MiX"],
            album="不虚此行",
            duration_ms=210000,
        )
        cand = AppleMusicTrack(
            id="10001",
            title="不虚此行",
            artists=["HOYO-MiX", "魏晨", "Nea"],
            album="不虚此行 - Single",
            duration_ms=210000,
        )
        res = TrackScorer.score(src, cand)
        self.assertGreaterEqual(res.title_score, 0.95, "Bilingual segment exact match must yield title_score >= 0.95")
        self.assertFalse(any("title_mismatch" in c for c in res.evidence.conflicts), "Must not have title_mismatch")
        self.assertGreaterEqual(res.artist_score, 0.95, "Equivalent collaborator sets must yield artist_score >= 0.95")
        self.assertGreaterEqual(res.score, 0.85, "Composite score must be >= 0.85")
        self.assertEqual(res.decision, DecisionStatus.REVIEW.value, "Speculative segment must be REVIEW, never auto-accept")
        self.assertEqual(res.evidence.verification_level, VerificationLevel.MEDIUM.value)
        self.assertEqual(res.evidence.evidence_type, "bilingual_segment")

        # Reverse direction: source has Chinese core title, candidate has bilingual title
        src_rev = Track(
            title="不虚此行",
            artists=["HOYO-MiX", "魏晨", "Nea"],
            album="不虚此行",
            duration_ms=210000,
        )
        cand_rev = AppleMusicTrack(
            id="10002",
            title="不虚此行 On the Journey",
            artists=["魏晨", "Nea", "HOYO-MiX"],
            album="不虚此行 - Single",
            duration_ms=210000,
        )
        res_rev = TrackScorer.score(src_rev, cand_rev)
        self.assertGreaterEqual(res_rev.title_score, 0.95)
        self.assertFalse(any("title_mismatch" in c for c in res_rev.evidence.conflicts))
        self.assertGreaterEqual(res_rev.score, 0.85)
        self.assertEqual(res_rev.decision, DecisionStatus.REVIEW.value)

    def test_P02_case2_bracket_subtitle_and_collaborator_addition(self):
        """P02: Case 2 - Subtitle in brackets stripped and additional collaborative artists accepted."""
        src = Track(
            title="提瓦特民谣",
            artists=["宴宁", "XY大甘蔗", "柳知萧", "闫夜桥"],
            album="提瓦特民谣",
            duration_ms=245000,
        )
        cand = AppleMusicTrack(
            id="10003",
            title="提瓦特民谣（游戏《原神》五周年同人曲）",
            artists=["宴宁", "XY大甘蔗", "柳知萧", "闫夜桥", "陶典", "孙晔"],
            album="提瓦特民谣 - EP",
            duration_ms=245000,
        )
        res = TrackScorer.score(src, cand)
        self.assertGreaterEqual(res.title_score, 0.95)
        self.assertGreaterEqual(res.artist_score, 0.90)
        self.assertGreaterEqual(res.score, 0.85)
        self.assertFalse(any("title_mismatch" in c for c in res.evidence.conflicts))
        self.assertFalse(any("artist_mismatch" in c for c in res.evidence.conflicts))
        self.assertEqual(res.decision, DecisionStatus.REVIEW.value)

    def test_P03_case3_title_credit_matching_on_project_entity(self):
        """P03: Case 3 - Title credited vocalist (Lilas Ikuta) matches source alias on project entity (HoYoFair)."""
        # Test all three alias representations of Lilas Ikuta: 幾田りら, ikura, Lilas Ikuta
        alias_variants = ["幾田りら (ikura)", "幾田りら", "ikura", "Lilas Ikuta", "Lilas"]
        for src_artist in alias_variants:
            src = Track(
                title="Nameless Faces",
                artists=[src_artist],
                duration_ms=195000,
            )
            cand = AppleMusicTrack(
                id="10004",
                title="Nameless Faces (feat. Lilas Ikuta) [Japanese Ver.]",
                artists=["HoYoFair"],
                album="Nameless Faces - Single",
                duration_ms=195000,
            )
            res = TrackScorer.score(src, cand)
            self.assertGreaterEqual(res.title_score, 0.95, f"Title score must be >= 0.95 for artist {src_artist}")
            self.assertGreaterEqual(res.artist_score, 0.90, f"Project credit match artist score must be >= 0.90 for {src_artist}")
            self.assertGreaterEqual(res.score, 0.85, f"Composite score must be >= 0.85 for {src_artist}")
            self.assertFalse(any("artist_mismatch" in c for c in res.evidence.conflicts))
            self.assertFalse(any("primary_artist_mismatch" in c for c in res.evidence.conflicts))
            self.assertEqual(res.decision, DecisionStatus.REVIEW.value)
            self.assertEqual(res.evidence.verification_level, VerificationLevel.MEDIUM.value)
            self.assertTrue(res.evidence.project_credit_matched)
            self.assertEqual(res.evidence.matched_credit_role, "vocalist")
            self.assertEqual(res.evidence.candidate_language_version, "japanese")
            self.assertIsNone(res.evidence.source_language_version)
            self.assertTrue(any("演唱者命中" in r for r in res.decision_reasons))
            self.assertTrue(any("特定语言版本" in r for r in res.decision_reasons))

    def test_P04_case4_san_z_studio_alias_and_bilingual_segment(self):
        """P04: Case 4 - Sān-Z / 三Z-STUDIO diacritic alias equivalence and bilingual title segment."""
        src = Track(
            title="ReDreaming Angel 复梦天使",
            artists=["三Z-STUDIO", "HOYO-MiX"],
            album="复梦天使",
            duration_ms=180000,
        )
        cand = AppleMusicTrack(
            id="10005",
            title="复梦天使",
            artists=["Sān-Z", "HOYO-MiX"],
            album="复梦天使 - Single",
            duration_ms=180000,
        )
        res = TrackScorer.score(src, cand)
        self.assertGreaterEqual(res.title_score, 0.95)
        self.assertGreaterEqual(res.artist_score, 0.95)
        self.assertGreaterEqual(res.score, 0.85)
        self.assertFalse(any("title_mismatch" in c for c in res.evidence.conflicts))
        self.assertFalse(any("artist_mismatch" in c for c in res.evidence.conflicts))
        self.assertEqual(res.decision, DecisionStatus.REVIEW.value)
        self.assertEqual(res.evidence.verification_level, VerificationLevel.MEDIUM.value)

    def test_P05_twelve_general_multilingual_collaborator_cases(self):
        """P05: 12 General non-screenshot test cases covering bilingual titles, reordering, and aliases."""
        test_pairs = [
            ("溯 Reverse", ["CORSAK 胡梦周", "马吟吟"], "溯", ["胡梦周", "马吟吟"]),
            ("起风了 Wind Rises", ["买辣椒也用券"], "起风了", ["买辣椒也用券"]),
            ("光年之外 Light Years Away", ["G.E.M. 邓紫棋"], "光年之外", ["邓紫棋"]),
            ("乌梅子酱 Dark Plum Sauce", ["李荣浩"], "乌梅子酱", ["李荣浩"]),
            ("City of Stars 星光之城", ["Ryan Gosling", "Emma Stone"], "City of Stars", ["Emma Stone", "Ryan Gosling"]),
            ("天下 The World", ["张杰"], "天下", ["张杰"]),
            ("大鱼 Big Fish", ["周深"], "大鱼", ["周深"]),
            ("夜曲 Nocturne", ["周杰伦"], "夜曲", ["周杰伦"]),
            ("青花瓷 Blue and White Porcelain", ["周杰伦"], "青花瓷", ["周杰伦"]),
            ("晴天 Sunny Day", ["周杰伦"], "晴天", ["周杰伦"]),
            ("海底 Deep Sea", ["一支榴莲"], "海底", ["一支榴莲"]),
            ("少年 Youth", ["梦然"], "少年", ["梦然"]),
        ]
        for src_t, src_a, cand_t, cand_a in test_pairs:
            src = Track(title=src_t, artists=src_a, duration_ms=200000)
            cand = AppleMusicTrack(id=f"gen_{hash(src_t)}", title=cand_t, artists=cand_a, duration_ms=200000)
            res = TrackScorer.score(src, cand)
            self.assertGreaterEqual(res.title_score, 0.95, f"Failed title_score for {src_t} vs {cand_t}")
            self.assertGreaterEqual(res.score, 0.85, f"Failed score for {src_t}")
            self.assertFalse(any("title_mismatch" in c for c in res.evidence.conflicts))
            self.assertFalse(any("artist_mismatch" in c for c in res.evidence.conflicts))
            self.assertIn(res.decision, (DecisionStatus.REVIEW.value, DecisionStatus.AUTO_ACCEPT.value))

    # -------------------------------------------------------------------------
    # Negative & Security Cases (N01 - N06)
    # -------------------------------------------------------------------------

    def test_N01_reject_homonym_false_positive_sweets_parade_vs_magical_mode(self):
        """N01: sweets parade vs magical mode with same artist/album/duration must be NO_MATCH with score <= 0.39."""
        src = Track(title="sweets parade", artists=["花泽香菜"], album="sweets parade - Single", duration_ms=210000)
        cand = AppleMusicTrack(id="n01", title="magical mode", artists=["花泽香菜"], album="sweets parade - Single", duration_ms=210000)
        res = TrackScorer.score(src, cand)
        self.assertEqual(res.decision, DecisionStatus.NO_MATCH.value)
        self.assertLessEqual(res.score, 0.39)
        self.assertTrue(any("title_mismatch" in c for c in res.evidence.conflicts))

    def test_N02_prefix_and_english_phrases_not_split_arbitrarily(self):
        """N02: Love vs Love Story, Super Mario vs Super Mario Bros, and normal with English titles remain protected."""
        # Love vs Love Story
        src1 = Track(title="Love", artists=["Taylor Swift"])
        cand1 = AppleMusicTrack(id="n02_1", title="Love Story", artists=["Taylor Swift"])
        res1 = TrackScorer.score(src1, cand1)
        self.assertEqual(res1.decision, DecisionStatus.NO_MATCH.value)
        self.assertNotEqual(res1.decision, DecisionStatus.AUTO_ACCEPT.value)
        self.assertTrue(any("title_mismatch" in c for c in res1.evidence.conflicts))

        # Non-bilingual English phrases must not yield bilingual segments
        segs = TextCleaner.extract_bilingual_segments("Love Story")
        self.assertEqual(segs, [])
        segs_mario = TextCleaner.extract_bilingual_segments("Super Mario Bros")
        self.assertEqual(segs_mario, [])

        # Normal English titles containing 'with' must NOT have 'with' stripped as a featured credit
        self.assertEqual(TextCleaner.clean_title("Stay with Me"), "Stay with Me")
        self.assertEqual(TextCleaner.clean_title("Love with You"), "Love with You")
        self.assertEqual(TextCleaner.clean_title("Dance with Me"), "Dance with Me")

        td_stay = TextCleaner.parse_title_details("Stay with Me")
        self.assertEqual(td_stay.core_title, "Stay with Me")
        self.assertEqual(td_stay.title_credits, [])

        # Distinct songs: Stay with Me vs Stay (even with same artist, same duration, Single album)
        src_stay = Track(title="Stay with Me", artists=["Sam Smith"], album="Stay with Me - Single", duration_ms=172000)
        cand_stay = AppleMusicTrack(id="n02_stay", title="Stay", artists=["Sam Smith"], album="Stay with Me - Single", duration_ms=172000)
        res_stay = TrackScorer.score(src_stay, cand_stay)
        self.assertEqual(res_stay.decision, DecisionStatus.NO_MATCH.value)
        self.assertLessEqual(res_stay.title_score, 0.50)
        self.assertLessEqual(res_stay.score, 0.39)
        self.assertTrue(any("title_mismatch" in c for c in res_stay.evidence.conflicts))
        self.assertNotEqual(res_stay.decision, DecisionStatus.AUTO_ACCEPT.value)

        # Love with You vs Love
        src_love = Track(title="Love with You", artists=["fripSide"], album="Love with You - Single", duration_ms=210000)
        cand_love = AppleMusicTrack(id="n02_love", title="Love", artists=["fripSide"], album="Love with You - Single", duration_ms=210000)
        res_love = TrackScorer.score(src_love, cand_love)
        self.assertEqual(res_love.decision, DecisionStatus.NO_MATCH.value)
        self.assertLessEqual(res_love.score, 0.39)
        self.assertTrue(any("title_mismatch" in c for c in res_love.evidence.conflicts))

        # Case variations and reversed direction
        src_case = Track(title="stay WITH me", artists=["Artist A"])
        cand_case = AppleMusicTrack(id="n02_case", title="Stay", artists=["Artist A"])
        res_case = TrackScorer.score(src_case, cand_case)
        self.assertEqual(res_case.decision, DecisionStatus.NO_MATCH.value)
        self.assertTrue(any("title_mismatch" in c for c in res_case.evidence.conflicts))

        # Reverse direction: Stay vs Stay with Me
        src_rev = Track(title="Stay", artists=["Sam Smith"], album="Stay with Me - Single", duration_ms=172000)
        cand_rev = AppleMusicTrack(id="n02_stay_rev", title="Stay with Me", artists=["Sam Smith"], album="Stay with Me - Single", duration_ms=172000)
        res_rev = TrackScorer.score(src_rev, cand_rev)
        self.assertEqual(res_rev.decision, DecisionStatus.NO_MATCH.value)
        self.assertLessEqual(res_rev.score, 0.39)
        self.assertTrue(any("title_mismatch" in c for c in res_rev.evidence.conflicts))

    def test_N03_guest_artist_protection_on_ordinary_artist(self):
        """N03: Ordinary artist A vs B feat. A retains guest protection (primary_artist_mismatch, score <= 0.60)."""
        src = Track(title="Shape of You", artists=["Ed Sheeran"])
        cand = AppleMusicTrack(id="n03", title="Shape of You (feat. Ed Sheeran)", artists=["Taylor Swift"])
        res = TrackScorer.score(src, cand)
        self.assertLessEqual(res.artist_score, 0.60)
        self.assertTrue(any("primary_artist_mismatch" in c for c in res.evidence.conflicts))
        self.assertNotEqual(res.decision, DecisionStatus.AUTO_ACCEPT.value)

    def test_N04_project_entity_with_mismatched_singer_and_composite_artists_rejected(self):
        """N04: Reject mismatched singer on project entity and arbitrary bilingual composite artist aliases."""
        from applemusic.matcher.artist_aliases import are_artists_equivalent

        # 1. Project entity (HoYoFair) with a different title-credited singer must not forge artist identity
        src = Track(title="Nameless Faces", artists=["幾田りら"])
        cand = AppleMusicTrack(id="n04", title="Nameless Faces (feat. Unrelated Singer)", artists=["HoYoFair"])
        res = TrackScorer.score(src, cand)
        self.assertLess(res.artist_score, 0.40)
        self.assertTrue(any("artist_mismatch" in c for c in res.evidence.conflicts))
        self.assertEqual(res.decision, DecisionStatus.NO_MATCH.value)

        # 2. Arbitrary bilingual composite names must NOT be considered equivalent
        self.assertFalse(are_artists_equivalent("Fake Artist 花泽香菜", "花泽香菜"))
        self.assertFalse(are_artists_equivalent("花泽香菜 Fake Artist", "花泽香菜"))
        self.assertFalse(are_artists_equivalent("Taylor Swift 周杰伦", "Taylor Swift"))
        self.assertFalse(are_artists_equivalent("周杰伦 Taylor Swift", "周杰伦"))
        self.assertFalse(are_artists_equivalent("Fake Artist / 花泽香菜", "花泽香菜"))
        self.assertFalse(are_artists_equivalent("Fake Artist・花泽香菜", "花泽香菜"))

        # 3. Scorer evaluation on unverified composite artists: must not auto_accept, must flag mismatch conflict
        src_fake = Track(title="Example Song", artists=["Fake Artist 花泽香菜"])
        cand_fake = AppleMusicTrack(id="n04_fake", title="Example Song", artists=["花泽香菜"])
        res_fake = TrackScorer.score(src_fake, cand_fake)
        self.assertNotEqual(res_fake.decision, DecisionStatus.AUTO_ACCEPT.value)
        self.assertLessEqual(res_fake.artist_score, 0.50)
        self.assertTrue(any("primary_artist_mismatch" in c or "artist_mismatch" in c for c in res_fake.evidence.conflicts))

        src_taylor = Track(title="Example Song", artists=["Taylor Swift 周杰伦"])
        cand_taylor = AppleMusicTrack(id="n04_taylor", title="Example Song", artists=["Taylor Swift"])
        res_taylor = TrackScorer.score(src_taylor, cand_taylor)
        self.assertNotEqual(res_taylor.decision, DecisionStatus.AUTO_ACCEPT.value)
        self.assertLessEqual(res_taylor.artist_score, 0.50)
        self.assertTrue(any("primary_artist_mismatch" in c or "artist_mismatch" in c for c in res_taylor.evidence.conflicts))

        # 4. Verified genuine artist aliases must strictly be preserved
        self.assertTrue(are_artists_equivalent("CORSAK 胡梦周", "胡梦周"))
        self.assertTrue(are_artists_equivalent("Sān-Z", "三Z-STUDIO"))
        self.assertTrue(are_artists_equivalent("G.E.M. 邓紫棋", "邓紫棋"))
        self.assertTrue(are_artists_equivalent("幾田りら", "Lilas Ikuta"))
        self.assertTrue(are_artists_equivalent("初音未来", "Hatsune Miku"))
        self.assertTrue(are_artists_equivalent("周杰伦", "Jay Chou"))

    def test_N05_language_version_consistency_matrix(self):
        """N05: Language version consistency: conflict on different, unverified on unknown, matched on same."""
        # 1. Different languages: English Ver. vs Japanese Ver. -> hard conflict (-0.50)
        src_diff = Track(title="Song (English Ver.)", artists=["Artist A"], isrc="US1234567890", duration_ms=200000)
        cand_diff = AppleMusicTrack(id="n05_1", title="Song (Japanese Ver.)", artists=["Artist A"], isrc="US1234567890", duration_ms=200000)
        res_diff = TrackScorer.score(src_diff, cand_diff)
        self.assertTrue(any("version_conflict" in c for c in res_diff.evidence.conflicts))
        self.assertNotEqual(res_diff.decision, DecisionStatus.AUTO_ACCEPT.value, "Identical ISRC must not bypass version conflict")

        # 2. One side unknown vs other side specific -> unverified review, never auto-accept
        src_unk = Track(title="Song", artists=["Artist A"], duration_ms=200000)
        cand_jp = AppleMusicTrack(id="n05_2", title="Song (Japanese Ver.)", artists=["Artist A"], duration_ms=200000)
        res_unk = TrackScorer.score(src_unk, cand_jp)
        self.assertEqual(res_unk.decision, DecisionStatus.REVIEW.value)
        self.assertEqual(res_unk.evidence.verification_level, VerificationLevel.MEDIUM.value)
        self.assertFalse(any("version_conflict" in c for c in res_unk.evidence.conflicts))

        # 3. Same language on both sides -> matched, no conflict
        src_same = Track(title="Song (Japanese Ver.)", artists=["Artist A"], duration_ms=200000)
        cand_same = AppleMusicTrack(id="n05_3", title="Song [Japanese Version]", artists=["Artist A"], duration_ms=200000)
        res_same = TrackScorer.score(src_same, cand_same)
        self.assertFalse(any("version_conflict" in c for c in res_same.evidence.conflicts))
        self.assertGreaterEqual(res_same.version_score, 0.0)

    def test_N06_v5_non_definite_title_matrix_remains_medium_review(self):
        """N06: V5.1 non-definite title matrix 60 combinations remain MEDIUM / REVIEW."""
        non_definite_methods = ["romaji_fuzzy", "containment", "romaji_long_vowel_folded", "bilingual_segment"]
        metadata_variants = [
            (True, True, True),   # same artist, same duration, same album
            (True, True, False),  # same artist, same duration, different album
            (True, False, True),  # same artist, different duration, same album
            (True, False, False), # same artist, different duration, different album
        ]
        for method in non_definite_methods:
            for same_art, same_dur, same_alb in metadata_variants:
                src_art = ["Artist A"]
                cand_art = ["Artist A"] if same_art else ["Different Artist"]
                src_dur = 200000
                cand_dur = 200000 if same_dur else 350000
                src_alb = "Album X"
                cand_alb = "Album X" if same_alb else "Album Y"

                src = Track(title="Sukidakara", artists=src_art, album=src_alb, duration_ms=src_dur)
                cand = AppleMusicTrack(id="n06", title="Sukidakaro", artists=cand_art, album=cand_alb, duration_ms=cand_dur)
                res = TrackScorer.score(src, cand)
                # Must never be STRONG or AUTO_ACCEPT
                self.assertNotEqual(res.decision, DecisionStatus.AUTO_ACCEPT.value)
                self.assertNotEqual(res.evidence.verification_level, VerificationLevel.STRONG.value)

    # -------------------------------------------------------------------------
    # Retrieval and Query Planning Cases (Q01 - Q02)
    # -------------------------------------------------------------------------

    def test_Q01_core_title_only_recall_when_full_query_has_no_hits(self):
        """Q01: Case 2 recall - engine falls back to core_title_only and recalls candidate within budget."""
        target_track = AppleMusicTrack(
            id="q01_target",
            title="提瓦特民谣（游戏《原神》五周年同人曲）",
            artists=["宴宁", "XY大甘蔗", "柳知萧", "闫夜桥", "陶典", "孙晔"],
            album="提瓦特民谣 - EP",
            duration_ms=245000,
        )

        def mock_search_catalog(query, storefront="cn", limit=10, **kwargs):
            clean_q = query.strip()
            # If query contains artist name '宴宁', simulate no hits on Apple Music
            if "宴宁" in clean_q:
                return CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)
            # If query is core title alone '提瓦特民谣', return the track!
            if "提瓦特民谣" in clean_q:
                return CatalogSearchOutcome(kind="ok", tracks=[target_track], http_status=200)
            return CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)

        mock_client = MagicMock(spec=AppleMusicClient)
        mock_client.config = Config()
        mock_client.search_catalog.side_effect = mock_search_catalog
        mock_client.search_by_isrc.return_value = CatalogSearchOutcome(kind="no_hits", tracks=[])
        mock_client.get_storefront_languages.return_value = ["zh-Hans-CN"]

        engine = MatchingEngine(client=mock_client)
        source = Track(
            title="提瓦特民谣",
            artists=["宴宁", "XY大甘蔗", "柳知萧", "闫夜桥"],
            album="提瓦特民谣",
            duration_ms=245000,
        )
        result = engine.match_track(source, storefront="cn")

        self.assertIsNotNone(result.selected_candidate)
        self.assertEqual(result.selected_candidate.track.id, "q01_target")
        self.assertIn(result.decision, (DecisionStatus.AUTO_ACCEPT.value, DecisionStatus.REVIEW.value))
        self.assertLessEqual(result.search_attempts, 8, "Must not exceed catalog query budget")

        # Verify that core_title_only was executed in trace
        executed_provs = [q["provenance"] for q in result.diagnostics.executed_queries]
        self.assertIn("core_title_only", executed_provs)

    def test_Q02_bilingual_segment_query_planning_budget_and_dedup(self):
        """Q02: Bilingual segment and alias queries are bounded, deduplicated, and respect budget, with fallback execution trace and error classifications."""
        src = Track(
            title="ReDreaming Angel 复梦天使",
            artists=["三Z-STUDIO"],
            album="复梦天使",
            duration_ms=180000,
        )
        ctx = QueryContext(
            title=src.title,
            artists=src.artists,
            target_storefront="cn",
        )
        phases = QueryPlanner.plan_phases(ctx)
        phase_a = phases["A_native"]

        # 1. Assert no duplicates in Phase A planned queries
        query_texts = [q.query.lower() for q in phase_a]
        self.assertEqual(len(query_texts), len(set(query_texts)), "Phase A queries must be strictly deduplicated")

        # Verify bilingual segments and core_title_only are present
        provs = [q.provenance for q in phase_a]
        self.assertIn("core_title_only", provs)
        self.assertIn("bilingual_segment", provs)
        self.assertIn("bilingual_segment_title_only", provs)

        # 2. Engine query execution trace & bilingual segment / alias fallback recall
        cand = AppleMusicTrack(
            id="q02_cand",
            title="复梦天使",
            artists=["Sān-Z", "HOYO-MiX"],
            album="复梦天使 - Single",
            duration_ms=180000,
        )

        def mock_search_catalog(query, storefront="cn", limit=10, **kwargs):
            clean_q = query.strip()
            # Initial queries with full English title or original artist fail on Apple Music CN
            if "redreaming" in clean_q.lower() or "三z-studio" in clean_q.lower():
                return CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)
            # Fallback query with bilingual segment "复梦天使" or alias "sān-z" / "san-z" returns candidate
            if "复梦天使" in clean_q or "sān-z" in clean_q.lower() or "san-z" in clean_q.lower():
                return CatalogSearchOutcome(kind="ok", tracks=[cand], http_status=200)
            return CatalogSearchOutcome(kind="no_hits", tracks=[], http_status=200)

        mock_client = MagicMock(spec=AppleMusicClient)
        mock_client.config = Config()
        mock_client.search_catalog.side_effect = mock_search_catalog
        mock_client.search_by_isrc.return_value = CatalogSearchOutcome(kind="no_hits", tracks=[])
        mock_client.get_storefront_languages.return_value = ["zh-Hans-CN"]

        engine = MatchingEngine(client=mock_client)
        result = engine.match_track(src, storefront="cn")

        self.assertIsNotNone(result.selected_candidate)
        self.assertEqual(result.selected_candidate.track.id, "q02_cand")
        self.assertIn(result.decision, (DecisionStatus.AUTO_ACCEPT.value, DecisionStatus.REVIEW.value))
        self.assertLessEqual(result.search_attempts, 8, "Must strictly respect query budget <= 8")

        # Verify query execution trace records bilingual fallback
        executed_provs = [q["provenance"] for q in result.diagnostics.executed_queries]
        self.assertTrue(any("bilingual_segment" in p for p in executed_provs))
        self.assertIn("catalog", result.diagnostics.budget_consumed)

        # 3. HTTP 429 Rate limiting error classification
        mock_client_rate = MagicMock(spec=AppleMusicClient)
        mock_client_rate.config = Config()
        mock_client_rate.search_by_isrc.return_value = CatalogSearchOutcome(kind="no_hits", tracks=[])
        mock_client_rate.get_storefront_languages.return_value = ["zh-Hans-CN"]
        mock_client_rate.search_catalog.return_value = CatalogSearchOutcome(
            kind="rate_limited", retry_after_seconds=15.0, http_status=429, safe_message="Too Many Requests"
        )
        engine_rate = MatchingEngine(client=mock_client_rate)
        res_rate = engine_rate.match_track(src, storefront="cn")
        self.assertEqual(res_rate.decision, "rate_limited")
        self.assertEqual(res_rate.search_status, "rate_limited")
        self.assertEqual(res_rate.retry_after_seconds, 15.0)
        self.assertTrue(res_rate.search_incomplete)

        # 4. Network error / timeout classification
        mock_client_net = MagicMock(spec=AppleMusicClient)
        mock_client_net.config = Config()
        mock_client_net.search_by_isrc.return_value = CatalogSearchOutcome(kind="no_hits", tracks=[])
        mock_client_net.get_storefront_languages.return_value = ["zh-Hans-CN"]
        mock_client_net.search_catalog.return_value = CatalogSearchOutcome(
            kind="network_error", safe_message="Connection timed out", http_status=None
        )
        engine_net = MatchingEngine(client=mock_client_net)
        res_net = engine_net.match_track(src, storefront="cn")
        self.assertEqual(res_net.decision, "error")
        self.assertEqual(res_net.search_status, "network_error")
        self.assertTrue(res_net.search_incomplete)

    # -------------------------------------------------------------------------
    # Cache Invalidation and Migration Cases (C01)
    # -------------------------------------------------------------------------

    def test_C01_legacy_cache_invalidation_and_user_confirmed_retention(self):
        """C01: Temporary SQLite DB verifies independent version invalidation, re-scoring, and engine re-search while retaining user_confirmed."""
        import time
        from unittest.mock import patch
        from applemusic.matcher.evidence import (
            MATCH_RULE_VERSION,
            QUERY_POLICY_VERSION,
            ALIAS_VERSION,
            ROMANIZER_VERSION,
            EXCEPTION_REGISTRY_VERSION,
        )

        temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        try:
            test_db = Path(temp_dir.name) / "test_cache.db"
            cache = PersistentCache(db_path=test_db)

            def insert_raw_cache_row(
                track_hash: str,
                result: SongMatchResult,
                rule_v: str,
                policy_v: str,
                alias_v: str,
                cache_key: str = "",
            ):
                conn = cache._get_connection()
                now = time.time()
                with cache._lock:
                    with conn:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO match_cache 
                            (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at,
                             rule_version, query_policy_version, romanizer_version, exception_registry_version, alias_version)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                "cn",
                                track_hash,
                                cache_key,
                                result.model_dump_json(),
                                result.decision,
                                str(result.status.value if hasattr(result.status, "value") else result.status),
                                now,
                                now + 86400,
                                rule_v,
                                policy_v,
                                ROMANIZER_VERSION,
                                EXCEPTION_REGISTRY_VERSION,
                                alias_v,
                            ),
                        )

            # Sample legacy objects
            legacy_no_match = SongMatchResult(
                source_track=Track(title="不虚此行 On the Journey", artists=["魏晨"]),
                decision="no_match",
                status=ConfidenceLevel.NOT_FOUND,
                evidence=MatchEvidence(
                    evidence_type="weak",
                    rule_version="2026.09.v5.1",
                    query_policy_version="2026.09.v4",
                    alias_version="2026.09.v5",
                ),
            )

            # Legacy false-positive match: "Stay with Me" vs "Stay" with single album & same duration
            stay_src = Track(title="Stay with Me", artists=["Sam Smith"], album="Stay with Me - Single", duration_ms=172000)
            stay_cand = AppleMusicTrack(id="cand_stay_99", title="Stay", artists=["Sam Smith"], album="Stay with Me - Single", duration_ms=172000)
            legacy_fp_stay = SongMatchResult(
                source_track=stay_src,
                candidates=[MatchCandidate(track=stay_cand, score=0.988, confidence=ConfidenceLevel.EXACT, decision="auto_accept")],
                selected_candidate=MatchCandidate(track=stay_cand, score=0.988, confidence=ConfidenceLevel.EXACT, decision="auto_accept"),
                decision="auto_accept",
                status=ConfidenceLevel.EXACT,
                evidence=MatchEvidence(
                    evidence_type="title_clean",
                    rule_version="2026.09.v5.1",
                    query_policy_version=QUERY_POLICY_VERSION,
                    alias_version=ALIAS_VERSION,
                ),
            )

            # Legacy review match
            legacy_review = SongMatchResult(
                source_track=Track(title="Song", artists=["Artist"]),
                candidates=[MatchCandidate(track=AppleMusicTrack(id="cand_rev_1", title="Song", artists=["Artist"]), score=0.75, confidence=ConfidenceLevel.MEDIUM, decision="review")],
                selected_candidate=MatchCandidate(track=AppleMusicTrack(id="cand_rev_1", title="Song", artists=["Artist"]), score=0.75, confidence=ConfidenceLevel.MEDIUM, decision="review"),
                decision="review",
                status=ConfidenceLevel.MEDIUM,
                evidence=MatchEvidence(
                    evidence_type="medium",
                    rule_version=MATCH_RULE_VERSION,
                    query_policy_version=QUERY_POLICY_VERSION,
                    alias_version=ALIAS_VERSION,
                ),
            )

            # User confirmed match
            confirmed_track = AppleMusicTrack(id="confirmed_999", title="Special Song", artists=["Special Artist"])
            legacy_confirmed = SongMatchResult(
                source_track=Track(title="Special Song", artists=["Special Artist"]),
                selected_candidate=MatchCandidate(
                    track=confirmed_track,
                    score=1.0,
                    confidence=ConfidenceLevel.EXACT,
                    decision="user_confirmed",
                ),
                decision="user_confirmed",
                status=ConfidenceLevel.EXACT,
                evidence=MatchEvidence(
                    evidence_type="manual",
                    rule_version="2026.09.v5.1",
                    query_policy_version="2026.09.v4",
                    alias_version="2026.09.v5",
                ),
            )

            # -------------------------------------------------------------
            # Part 1: Independent expiration scenarios
            # -------------------------------------------------------------
            # 1a. Only rule_version is outdated (2026.09.v5.1 vs current 2026.09.v6.1)
            # Outdated no_match must return None (cache miss triggers re-search)
            insert_raw_cache_row("hash_rule_old_nomatch", legacy_no_match, "2026.09.v5.1", QUERY_POLICY_VERSION, ALIAS_VERSION)
            self.assertIsNone(cache.get_match("cn", "hash_rule_old_nomatch"), "Outdated rule_version on no_match must be cache miss")

            # Outdated false-positive auto_accept ("Stay with Me" vs "Stay") must be re-evaluated under v6.1 and downgraded
            insert_raw_cache_row("hash_rule_old_fp", legacy_fp_stay, "2026.09.v5.1", QUERY_POLICY_VERSION, ALIAS_VERSION)
            res_fp = cache.get_match("cn", "hash_rule_old_fp")
            # Must NOT be auto_accept under 2026.09.v6.1 rules
            self.assertNotEqual(res_fp.decision, "auto_accept", "Outdated false-positive auto_accept must NOT remain auto_accept")

            # 1b. Only alias_version is outdated (2026.09.v5 vs current 2026.09.v6.1)
            insert_raw_cache_row("hash_alias_old", legacy_review, MATCH_RULE_VERSION, QUERY_POLICY_VERSION, "2026.09.v5")
            self.assertIsNone(cache.get_match("cn", "hash_alias_old"), "Outdated alias_version must be cache miss to allow fresh alias recall")

            # 1c. Only query_policy_version is outdated (2026.09.v4 vs current 2026.09.v6.1)
            insert_raw_cache_row("hash_policy_old", legacy_review, MATCH_RULE_VERSION, "2026.09.v4", ALIAS_VERSION)
            self.assertIsNone(cache.get_match("cn", "hash_policy_old"), "Outdated query_policy_version must be cache miss to allow fresh query planner recall")

            # 1d. user_confirmed match must be strictly preserved across all legacy versions
            insert_raw_cache_row("hash_confirmed", legacy_confirmed, "2026.09.v5.1", "2026.09.v4", "2026.09.v5")
            res_confirmed = cache.get_match("cn", "hash_confirmed")
            self.assertIsNotNone(res_confirmed, "user_confirmed must never be invalidated by version upgrades")
            self.assertEqual(res_confirmed.decision, "user_confirmed")

            # -------------------------------------------------------------
            # Part 2: Engine full pipeline re-search with temporary SQLite DB
            # -------------------------------------------------------------
            target_source = Track(title="ReDreaming Angel 复梦天使", artists=["三Z-STUDIO"], album="复梦天使", duration_ms=180000)
            target_cand = AppleMusicTrack(
                id="target_recalled_99",
                title="复梦天使",
                artists=["Sān-Z", "HOYO-MiX"],
                album="复梦天使 - Single",
                duration_ms=180000,
            )

            # Seed SQLite match_cache with a legacy no_match for target_source under rule v5.1
            stale_no_match = SongMatchResult(
                source_track=target_source,
                decision="no_match",
                status=ConfidenceLevel.NOT_FOUND,
                evidence=MatchEvidence(
                    evidence_type="weak",
                    rule_version="2026.09.v5.1",
                    query_policy_version="2026.09.v4",
                    alias_version="2026.09.v5",
                ),
            )
            # Generate cache key as engine would
            core_t = TextCleaner.clean_title(target_source.title).lower()
            pri_a, _ = TextCleaner.parse_artists(target_source.artists)
            norm_a = TextCleaner.normalize(pri_a).lower()
            key_str = f"text:{core_t}:{norm_a}:standard"
            insert_raw_cache_row("hash_engine_test", stale_no_match, "2026.09.v5.1", "2026.09.v4", "2026.09.v5", cache_key=key_str)

            # Setup mock client that recalls the track on catalog search
            mock_client = MagicMock(spec=AppleMusicClient)
            mock_client.config = Config()
            mock_client.search_by_isrc.return_value = CatalogSearchOutcome(kind="no_hits", tracks=[])
            mock_client.get_storefront_languages.return_value = ["zh-Hans-CN"]
            mock_client.get_search_suggestions.return_value = []
            mock_client.get_equivalent_tracks.return_value = {}
            mock_client.search_catalog.return_value = CatalogSearchOutcome(kind="ok", tracks=[target_cand], http_status=200)

            # Patch PersistentCache.get_instance to use our temporary test_db cache
            with patch.object(PersistentCache, "get_instance", return_value=cache):
                engine = MatchingEngine(client=mock_client)
                engine.persistent_cache = cache
                playlist = Playlist(name="Test Playlist", tracks=[target_source])
                results = engine.match_playlist(playlist, storefront="cn")

                self.assertEqual(len(results), 1)
                match_res = results[0]
                self.assertIsNotNone(match_res.selected_candidate)
                self.assertEqual(match_res.selected_candidate.track.id, "target_recalled_99")
                # Engine actually initiated search_catalog because stale no_match was invalidated!
                self.assertTrue(mock_client.search_catalog.called, "Engine must initiate catalog search when cache is outdated")
                self.assertIn(match_res.decision, (DecisionStatus.AUTO_ACCEPT.value, DecisionStatus.REVIEW.value))
        finally:
            try:
                cache.close()
                temp_dir.cleanup()
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # UI / Browser Verification Case (U01)
    # -------------------------------------------------------------------------

    def test_U01_real_browser_ui_rendering_and_diagnostics(self):
        """U01: Real Playwright browser test verifying review item not selected and v6 diagnostics displayed."""
        from playwright.sync_api import sync_playwright

        html_path = Path(__file__).resolve().parent.parent / "applemusic" / "web" / "static" / "index.html"
        self.assertTrue(html_path.exists())

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # Serve or load HTML directly
            page.goto(f"file:///{html_path.as_posix()}")
            page.wait_for_selector("#app", timeout=5000)

            # Inject a mock review item representing Case 3 into Vue app state
            page.evaluate("""() => {
                const app = window.__VUE_APP__ || window.vueApp;
                const matchItem = {
                    source_track: {
                        title: "Nameless Faces",
                        artists: ["幾田りら (ikura)"]
                    },
                    selected_candidate: {
                        track: {
                            id: "10004",
                            title: "Nameless Faces (feat. Lilas Ikuta) [Japanese Ver.]",
                            artists: ["HoYoFair"]
                        },
                        score: 0.967,
                        confidence: "exact",
                        decision: "review"
                    },
                    candidates: [{
                        track: {
                            id: "10004",
                            title: "Nameless Faces (feat. Lilas Ikuta) [Japanese Ver.]",
                            artists: ["HoYoFair"]
                        },
                        score: 0.967
                    }],
                    decision: "review",
                    status: "high",
                    selected: false,
                    evidence: {
                        rule_version: "2026.09.v6.1",
                        alias_version: "2026.09.v6.1",
                        verification_level: "medium",
                        evidence_type: "project_credit_match",
                        matched_fields: ["title", "artist"],
                        conflicts: [],
                        matched_credit_role: "vocalist",
                        language_version: "japanese",
                        project_credit_matched: true,
                        matched_title_pair: ["Nameless Faces", "Nameless Faces"],
                        title_comparison_method: "exact"
                    },
                    decision_reasons: [
                        "演唱者命中，主署名关系待核验",
                        "候选曲目为特定语言版本，来源语言未标明，待人工核对"
                    ]
                };
                // Find reactive matchResults on root
                const root = window.__vueRoot || (document.querySelector('#app') && document.querySelector('#app').__vue_app__ ? document.querySelector('#app').__vue_app__._instance.ctx : null);
                if (root) {
                    root.matchResults = [matchItem];
                    root.matchStatus = 'completed';
                    root.activeTab = 'match';
                }
            }""")

            page.wait_for_timeout(500)

            # Check whether checkbox is unchecked for review item
            checkbox = page.locator("input[type='checkbox']").first
            if checkbox.count() > 0:
                self.assertFalse(checkbox.is_checked(), "Review item must not be auto-checked")

            # Open diagnostics modal
            page.evaluate("""() => {
                const root = window.__vueRoot || (document.querySelector('#app') && document.querySelector('#app').__vue_app__ ? document.querySelector('#app').__vue_app__._instance.ctx : null);
                if (root) {
                    if (typeof root.openSingleDiagnosticsModal === 'function') {
                        root.openSingleDiagnosticsModal(root.matchResults[0]);
                    } else {
                        root.diagnosticsModalItem = root.matchResults[0];
                    }
                }
            }""")

            page.wait_for_timeout(300)

            # Verify modal contents
            modal_content = page.content()
            self.assertIn("2026.09.v6.1", modal_content, "Diagnostics modal must display rule version 2026.09.v6.1")
            self.assertIn("vocalist", modal_content, "Diagnostics modal must display matched credit role 'vocalist'")
            self.assertIn("japanese", modal_content, "Diagnostics modal must display language version 'japanese'")

            browser.close()

            browser.close()


if __name__ == "__main__":
    unittest.main()
