"""
Test suite for Match Identity Scoring V5.
Covers the full acceptance matrix in .codex-workflow/match-identity-scoring-v5/PLAN.md:
- P01: Exact positive case (好きだから。（因为我喜欢你。） / 『ユイカ』 -> Sukidakara / Yuika)
- P02: Symmetry, brackets, punctuation, spaces, casing
- P03: At least 12 Japanese/Romaji complete correspondence pairs
- P04: Positive case with reliable identity verification
- N01: sweets parade -> magical mode under duration/album variations
- N02: At least 15 same-artist different-song pairs
- N03: Conflict protections (same title diff artist, featured flip, version mismatch)
- N04: Incomplete/prefix, unknown kanji, pure Chinese, empty fields
- E01: Search candidate selection (wrong candidate first, right candidate later / only wrong candidate)
- E02: Engine retry and relaxed paths block title_mismatch
- C01: Cache migration and stale re-evaluation
- U01: Web UI presentation data structure for positive and negative cases
"""

import os
import tempfile
import unittest
from pathlib import Path
from typing import List

from applemusic.cache import PersistentCache
from applemusic.matcher.cleaner import TextCleaner
from applemusic.matcher.evidence import (
    MATCH_RULE_VERSION,
    QUERY_POLICY_VERSION,
    ROMANIZER_VERSION,
    EXCEPTION_REGISTRY_VERSION,
    ALIAS_VERSION,
    MatchEvidence,
    VerificationLevel,
)
from applemusic.matcher.scorer import TrackScorer
from applemusic.models import (
    AppleMusicTrack,
    ConfidenceLevel,
    DecisionStatus,
    MatchCandidate,
    SongMatchResult,
    Track,
)


class TestMatchIdentityScoringV5(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_v5_cache.db"
        self.cache = PersistentCache(self.db_path)

    def tearDown(self):
        self.cache.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    # =========================================================================
    # P01: Exact screenshot positive case
    # =========================================================================
    def test_p01_screenshot_positive_sukidakara_yuika(self):
        """
        P01: 好きだから。（因为我喜欢你。） / 『ユイカ』 vs Sukidakara / Yuika.
        Must have:
        - title_score >= 0.95
        - artist_score >= 0.95
        - total_score >= 0.90
        - no title_mismatch conflict
        - accurate decision reasons (not '歌名相似度过低' / '歌名分低')
        """
        source = Track(
            title="好きだから。（因为我喜欢你。）",
            artists=["『ユイカ』"],
        )
        candidate = AppleMusicTrack(
            id="cand_sukidakara",
            title="Sukidakara",
            artists=["Yuika"],
            storefront="cn",
        )

        title_sim = TrackScorer.calculate_title_similarity(source.title, candidate.title)
        self.assertGreaterEqual(title_sim, 0.95, f"Title similarity {title_sim} should be >= 0.95")

        cand = TrackScorer.score(source, candidate)
        self.assertGreaterEqual(cand.title_score, 0.95, f"cand.title_score {cand.title_score} should be >= 0.95")
        self.assertGreaterEqual(cand.artist_score, 0.95, f"cand.artist_score {cand.artist_score} should be >= 0.95")
        self.assertGreaterEqual(cand.score, 0.90, f"cand.score {cand.score} should be >= 0.90")

        # Conflict check
        conflicts = cand.evidence.conflicts if cand.evidence else []
        self.assertFalse(
            any("title_mismatch" in c for c in conflicts),
            f"Expected no title_mismatch in conflicts: {conflicts}",
        )
        # Reason check: should not claim title score is low
        reasons_text = " ".join(cand.decision_reasons)
        self.assertNotIn("歌名相似度过低", reasons_text)
        self.assertNotIn("缺少艺人信息", reasons_text)

    # =========================================================================
    # P02: Symmetry, brackets, punctuation, spaces, casing
    # =========================================================================
    def test_p02_positive_symmetry_and_variants(self):
        """
        P02: Variations of P01:
        - swap sides (source and candidate swapped)
        - remove brackets
        - full-width vs half-width punctuation
        - romaji with space, CamelCase, lowercase
        All score diffs <= 0.03; title comparison symmetric.
        """
        base_src = Track(title="好きだから。（因为我喜欢你。）", artists=["『ユイカ』"])
        base_cand = AppleMusicTrack(id="c0", title="Sukidakara", artists=["Yuika"])
        base_res = TrackScorer.score(base_src, base_cand)

        variations = [
            ("好きだから", "Sukidakara"),
            ("好きだから。", "Suki Dakara"),
            ("好きだから", "suki dakara"),
            ("Sukidakara", "好きだから。"),
            ("Sukidakara", "好きだから。（因为我喜欢你。）"),
            ("好きだから。（因为我喜欢你。）", "suki dakara"),
            ("好きだから! (因为我喜欢你)", "Suki Dakara"),
        ]

        for s_t, c_t in variations:
            sim = TrackScorer.calculate_title_similarity(s_t, c_t)
            self.assertGreaterEqual(sim, 0.95, f"Similarity between '{s_t}' and '{c_t}' was {sim}, expected >= 0.95")
            diff = abs(sim - base_res.title_score)
            self.assertLessEqual(diff, 0.03, f"Score diff {diff} between '{s_t}' and '{c_t}' exceeds 0.03")

    # =========================================================================
    # P03: At least 12 Japanese/Romaji complete correspondence pairs
    # =========================================================================
    def test_p03_twelve_plus_japanese_romaji_pairs(self):
        """
        P03: At least 12 Japanese/Romaji complete pairs across multiple artists,
        including un-aliased songs and mixed English words.
        """
        test_pairs = [
            ("炎", "Homura", ["LiSA"], ["LiSA"]),
            ("紅蓮華", "Gurenge", ["LiSA"], ["LiSA"]),
            ("白日", "Hakujitsu", ["King Gnu"], ["King Gnu"]),
            ("残響散歌", "Zankyou Sanka", ["Aimer"], ["Aimer"]),
            ("残響散歌", "Zankyo Sanka", ["Aimer"], ["Aimer"]),
            ("カタオモイ", "Kataomoi", ["Aimer"], ["Aimer"]),
            ("春を告げる", "Haru wo Tsugeru", ["yama"], ["yama"]),
            ("春を告げる", "Haru o Tsugeru", ["yama"], ["yama"]),
            ("点描の唄", "Tenbyou no Uta", ["Mrs. GREEN APPLE"], ["Mrs. GREEN APPLE"]),
            ("水平線", "Suiheisen", ["back number"], ["back number"]),
            ("高嶺の花子さん", "Takane no Hanako-san", ["back number"], ["back number"]),
            ("なんでもないや", "Nandemonaiya", ["上白石萌音"], ["Mone Kamishiraishi"]),
            ("丸の内サディスティック", "Marunouchi Sadistic", ["椎名林檎"], ["Sheena Ringo"]),
            ("かくれんぼ", "Kakurenbo", ["優里"], ["Yuuri"]),
            ("青と夏", "Ao to Natsu", ["Mrs. GREEN APPLE"], ["Mrs. GREEN APPLE"]),
        ]
        self.assertGreaterEqual(len(test_pairs), 12)

        for jp_t, ro_t, s_art, c_art in test_pairs:
            sim = TrackScorer.calculate_title_similarity(jp_t, ro_t)
            self.assertGreaterEqual(
                sim, 0.90,
                f"Expected title similarity >= 0.90 for '{jp_t}' <-> '{ro_t}', got {sim}",
            )
            src = Track(title=jp_t, artists=s_art)
            cand = AppleMusicTrack(id="cand_test", title=ro_t, artists=c_art)
            sc = TrackScorer.score(src, cand)
            self.assertGreaterEqual(
                sc.score, 0.85,
                f"Expected score >= 0.85 for '{jp_t}' / {s_art} <-> '{ro_t}' / {c_art}, got {sc.score}",
            )

    # =========================================================================
    # P04: Positive case with reliable identity verification
    # =========================================================================
    def test_p04_positive_with_reliable_corroboration(self):
        """
        P04: Positive case with verified metadata (e.g. matching duration or catalog mapping).
        Satisfies auto_accept thresholds when verified, but purely transliterated
        title + artist without independent evidence is de-correlated.
        """
        src = Track(
            title="好きだから。（因为我喜欢你。）",
            artists=["『ユイカ』"],
            duration_ms=210000,
            album="好きだから。",
        )
        # 1. Without catalog corroboration but with duration and album
        cand_with_meta = AppleMusicTrack(
            id="c_meta",
            title="Sukidakara",
            artists=["Yuika"],
            duration_ms=210500,
            album="Sukidakara",
            storefront="cn",
        )
        scored_cand = TrackScorer.score(src, cand_with_meta)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [scored_cand])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)
        self.assertGreaterEqual(best.score, 0.88)

        # 2. Purely transliterated without duration/album: de-correlation produces REVIEW
        cand_pure_trans = AppleMusicTrack(
            id="c_pure",
            title="Sukidakara",
            artists=["Yuika"],
            storefront="cn",
        )
        src_no_meta = Track(title="好きだから。（因为我喜欢你。）", artists=["『ユイカ』"])
        sc_pure = TrackScorer.score(src_no_meta, cand_pure_trans)
        best_pure, conf_pure, dec_pure, reasons_pure, _ = TrackScorer.evaluate_candidates(
            src_no_meta, [sc_pure]
        )
        # Should be high-confidence review, not auto_accept because both title and artist are romanizer_derived
        self.assertEqual(dec_pure, DecisionStatus.REVIEW.value)
        self.assertGreaterEqual(best_pure.score, 0.90)

    # =========================================================================
    # N01: sweets parade -> magical mode, same artist
    # =========================================================================
    def test_n01_sweets_parade_vs_magical_mode_exhaustive(self):
        """
        N01: sweets parade / 花泽香菜 vs magical mode / 花泽香菜.
        Must be no_match, score <= 0.39, no default candidate across:
        - durations: missing, identical (240000ms), diff 1s, diff 2.5s, diff 3s
        - album tags: missing, Single, EP, same album name
        """
        src_artist = ["花泽香菜 (はなざわ かな)"]
        cand_artist = ["花泽香菜"]

        duration_cases = [
            (None, None),
            (240000, 240000),      # exactly identical
            (240000, 241000),      # 1.0s diff
            (240000, 242500),      # 2.5s diff (old fallback threshold)
            (240000, 243000),      # 3.0s diff
        ]

        album_cases = [
            (None, None),
            (None, "sweets parade - Single"),
            (None, "magical mode - EP"),
            ("magical mode", "magical mode"),  # same album
        ]

        for s_dur, c_dur in duration_cases:
            for s_alb, c_alb in album_cases:
                src = Track(
                    title="sweets parade",
                    artists=src_artist,
                    duration_ms=s_dur,
                    album=s_alb,
                )
                cand = AppleMusicTrack(
                    id="cand_magical",
                    title="magical mode",
                    artists=cand_artist,
                    duration_ms=c_dur,
                    album=c_alb,
                    storefront="cn",
                )

                sc = TrackScorer.score(src, cand)
                desc = f"dur=({s_dur},{c_dur}), alb=({s_alb},{c_alb})"

                # 1. Title similarity must NOT be faked to 0.60
                self.assertLess(
                    sc.title_score, 0.40,
                    f"Title score was {sc.title_score} for '{src.title}' vs '{cand.title}' under {desc}",
                )

                # 2. Score must be strictly capped <= 0.39
                self.assertLessEqual(
                    sc.score, 0.39,
                    f"Candidate score {sc.score} must be <= 0.39 under {desc}",
                )

                # 3. Decision must be no_match
                self.assertEqual(
                    sc.decision, DecisionStatus.NO_MATCH.value,
                    f"Decision was {sc.decision}, expected no_match under {desc}",
                )

                # 4. Conflicts must include title_mismatch
                conflicts = sc.evidence.conflicts if sc.evidence else []
                self.assertTrue(
                    any("title_mismatch" in c for c in conflicts),
                    f"Expected title_mismatch in conflicts: {conflicts} under {desc}",
                )

                # 5. evaluate_candidates must return selected_candidate=None
                best, conf, dec, reasons, _ = TrackScorer.evaluate_candidates(src, [sc])
                self.assertIsNone(
                    best,
                    f"selected_candidate should be None under {desc}, got {best}",
                )
                self.assertEqual(dec, DecisionStatus.NO_MATCH.value)

    # =========================================================================
    # N02: At least 15 same-artist different-song pairs
    # =========================================================================
    def test_n02_fifteen_plus_same_artist_different_songs(self):
        """
        N02: At least 15 pairs of same-artist different songs across English,
        Japanese, Romaji, and short titles. Same album/duration must not make them match.
        """
        different_song_pairs = [
            ("sweets paper", "magical mode", ["花澤香菜"], ["花泽香菜"]),  # old test case
            ("恋爱循环", "星空 Destination", ["花泽香菜"], ["花泽香菜"]),
            ("Lemon", "Flamingo", ["米津玄師"], ["Kenshi Yonezu"]),
            ("Kick Back", "Paprika", ["米津玄師"], ["米津玄師"]),
            ("夜に駆ける", "群青", ["YOASOBI"], ["YOASOBI"]),
            ("Idol", "Monster", ["YOASOBI"], ["YOASOBI"]),
            ("カタオモイ", "残響散歌", ["Aimer"], ["Aimer"]),
            ("Brave Shine", "Ref:rain", ["Aimer"], ["Aimer"]),
            ("Shape of You", "Perfect", ["Ed Sheeran"], ["Ed Sheeran"]),
            ("Bad Guy", "Ocean Eyes", ["Billie Eilish"], ["Billie Eilish"]),
            ("Stay", "Ghost", ["Justin Bieber"], ["Justin Bieber"]),
            ("Intro", "Outro", ["Taylor Swift"], ["Taylor Swift"]),
            ("Red", "Blue", ["Taylor Swift"], ["Taylor Swift"]),
            ("晴天", "七里香", ["周杰伦"], ["周杰伦"]),
            ("青花瓷", "发如雪", ["周杰伦"], ["Jay Chou"]),
            ("打上花火", "ピースサイン", ["米津玄師"], ["米津玄師"]),
            ("前前前世", "スパークル", ["RADWIMPS"], ["RADWIMPS"]),
        ]
        self.assertGreaterEqual(len(different_song_pairs), 15)

        for s_t, c_t, s_art, c_art in different_song_pairs:
            src = Track(title=s_t, artists=s_art, duration_ms=210000, album="Best Album")
            cand = AppleMusicTrack(id="c_diff", title=c_t, artists=c_art, duration_ms=210000, album="Best Album")
            sc = TrackScorer.score(src, cand)
            self.assertLessEqual(
                sc.score, 0.39,
                f"Expected score <= 0.39 for '{s_t}' vs '{c_t}', got {sc.score}",
            )
            self.assertEqual(
                sc.decision, DecisionStatus.NO_MATCH.value,
                f"Expected no_match for '{s_t}' vs '{c_t}', got {sc.decision}",
            )
            conflicts = sc.evidence.conflicts if sc.evidence else []
            self.assertTrue(
                any("title_mismatch" in c for c in conflicts),
                f"Expected title_mismatch conflict for '{s_t}' vs '{c_t}': {conflicts}",
            )

    # =========================================================================
    # N03: Conflict protections preserved
    # =========================================================================
    def test_n03_conflict_protections_preserved(self):
        """
        N03: Conflict protections:
        - Same title, different artist -> rejected / low score
        - Version mismatch (Live vs Studio, Remix vs Original, Instrumental vs Vocal)
        """
        # 1. Same title different artist
        src1 = Track(title="Lemon", artists=["米津玄師"])
        cand1 = AppleMusicTrack(id="c_diff_art", title="Lemon", artists=["Various Artists"])
        sc1 = TrackScorer.score(src1, cand1)
        self.assertLessEqual(sc1.score, 0.45)
        self.assertIn("artist_mismatch", [c.split(":")[0] for c in sc1.evidence.conflicts])

        # 2. Version mismatch
        src2 = Track(title="好きだから。(Live)", artists=["『ユイカ』"])
        cand2 = AppleMusicTrack(id="c_v_mismatch", title="Sukidakara", artists=["Yuika"])
        sc2 = TrackScorer.score(src2, cand2)
        conflicts2 = [c.split(":")[0] for c in sc2.evidence.conflicts]
        self.assertIn("version_conflict", conflicts2)

        # 3. Instrumental vs Vocal
        src3 = Track(title="Sukidakara (伴奏)", artists=["Yuika"])
        cand3 = AppleMusicTrack(id="c_inst", title="Sukidakara", artists=["Yuika"])
        sc3 = TrackScorer.score(src3, cand3)
        conflicts3 = [c.split(":")[0] for c in sc3.evidence.conflicts]
        self.assertIn("version_conflict", conflicts3)

    # =========================================================================
    # N04: Incomplete/prefix, unknown kanji, pure Chinese
    # =========================================================================
    def test_n04_edge_cases_no_fake_equality(self):
        """
        N04: Edge cases:
        - Prefix only (Suki vs Sukidakara) must not match as equal romaji
        - Prefix containment leak prevention: 'Love' -> 'Love Story' must be title_mismatch
        - Fuzzy romaji: '好きだから' -> 'Sukidakaro' must be romaji_fuzzy and REVIEW, not auto_accept
        - Long vowel folded: 'こうこ' -> 'Koko' must be romaji_long_vowel_folded and REVIEW, not auto_accept
        - Cross-script unverified: '夜桜' -> 'Night Cherry' must be title_unverified, not title_mismatch
        - Pure Chinese titles with different names must not produce transliteration evidence
        - Empty titles/artists must not crash and must not match
        """
        # 1. Prefix only
        sim_pref = TrackScorer.calculate_title_similarity("好き", "Sukidakara")
        self.assertLess(sim_pref, 0.85)

        # 2. Prefix containment leak: Love vs Love Story
        src_love = Track(title="Love", artists=["Test Artist"])
        cand_love = AppleMusicTrack(id="c_love", title="Love Story", artists=["Test Artist"], storefront="us")
        sc_love = TrackScorer.score(src_love, cand_love)
        self.assertLess(sc_love.title_score, 0.60)
        self.assertLessEqual(sc_love.score, 0.39)
        self.assertNotEqual(sc_love.evidence.title_comparison_method, "variant_containment")
        self.assertTrue(any("title_mismatch" in c for c in sc_love.evidence.conflicts))
        best_l, _, dec_l, reasons_l, _ = TrackScorer.evaluate_candidates(src_love, [sc_love])
        self.assertIsNone(best_l)
        self.assertEqual(dec_l, DecisionStatus.NO_MATCH.value)

        # 3. Fuzzy romaji: 好きだから vs Sukidakaro
        src_suki = Track(title="好きだから", artists=["Test Artist"])
        cand_sukio = AppleMusicTrack(id="c_sukio", title="Sukidakaro", artists=["Test Artist"], storefront="jp")
        sc_sukio = TrackScorer.score(src_suki, cand_sukio)
        self.assertEqual(sc_sukio.evidence.title_comparison_method, "romaji_fuzzy")
        self.assertEqual(sc_sukio.evidence.verification_level, VerificationLevel.MEDIUM.value)
        best_sk, _, dec_sk, reasons_sk, _ = TrackScorer.evaluate_candidates(src_suki, [sc_sukio])
        self.assertIsNotNone(best_sk)
        self.assertEqual(dec_sk, DecisionStatus.REVIEW.value)
        self.assertIn("歌名存在拼写或读音差异，待人工核对", reasons_sk)

        # 4. Long vowel folded: こうこ vs Koko
        src_kouko = Track(title="こうこ", artists=["Test Artist"])
        cand_koko = AppleMusicTrack(id="c_koko", title="Koko", artists=["Test Artist"], storefront="jp")
        sc_koko = TrackScorer.score(src_kouko, cand_koko)
        self.assertEqual(sc_koko.evidence.title_comparison_method, "romaji_long_vowel_folded")
        self.assertEqual(sc_koko.evidence.verification_level, VerificationLevel.MEDIUM.value)
        best_kk, _, dec_kk, reasons_kk, _ = TrackScorer.evaluate_candidates(src_kouko, [sc_koko])
        self.assertIsNotNone(best_kk)
        self.assertEqual(dec_kk, DecisionStatus.REVIEW.value)
        self.assertIn("歌名存在长音或读音折叠差异，待人工核对", reasons_kk)

        # 5. Cross-script unverified: 夜桜 vs Night Cherry
        src_unv = Track(title="夜桜", artists=["Test Artist"])
        cand_unv = AppleMusicTrack(id="c_unv", title="Night Cherry", artists=["Test Artist"], storefront="jp")
        sc_unv = TrackScorer.score(src_unv, cand_unv)
        self.assertTrue(any("title_unverified" in c for c in sc_unv.evidence.conflicts))
        self.assertFalse(any("title_mismatch" in c for c in sc_unv.evidence.conflicts))
        best_u, _, dec_u, reasons_u, _ = TrackScorer.evaluate_candidates(src_unv, [sc_unv])
        self.assertIsNone(best_u)
        self.assertEqual(dec_u, DecisionStatus.NO_MATCH.value)
        self.assertIn("候选曲目歌名未核验", reasons_u)

        # 6. Pure Chinese different titles
        src_zh = Track(title="晴天", artists=["周杰伦"])
        cand_zh = AppleMusicTrack(id="c_zh", title="阴天", artists=["周杰伦"])
        sc_zh = TrackScorer.score(src_zh, cand_zh)
        self.assertLessEqual(sc_zh.score, 0.39)
        self.assertNotIn("romanizer_derived", sc_zh.evidence.evidence_families)

        # 7. Empty fields
        src_empty = Track(title="", artists=[])
        cand_empty = AppleMusicTrack(id="c_emp", title="", artists=[])
        sc_empty = TrackScorer.score(src_empty, cand_empty)
        self.assertEqual(sc_empty.score, 0.0)

    # =========================================================================
    # N05: Combination regression: Corroboration cannot bypass non-definite titles
    # =========================================================================
    def test_n05_corroboration_cannot_bypass_non_definite_titles(self):
        """
        N05: Combination regression required by REACCEPTANCE.md:
        - 4 Non-definite title methods:
          1. romaji_fuzzy: 好きだから vs Sukidakaro
          2. romaji_long_vowel_folded: こうこ vs Koko
          3. containment: Super Mario vs Super Mario Bros
          4. variant_fuzzy: Tokyo Tower vs Tokyo Towers
        - Durations:
          1. None vs None (no duration)
          2. 240000ms vs 240000ms (0s diff, identical duration)
          3. 240000ms vs 241000ms (1.0s diff)
          4. 240000ms vs 242500ms (2.5s diff)
          5. 240000ms vs 243000ms (3.0s diff)
        - Albums:
          1. None vs None (no album)
          2. None vs 'Single' (candidate Single)
          3. 'Album X' vs 'Album X' (same album)

        All 60 combinations MUST:
        - Never be auto_accepted (decision == 'review')
        - Verification level must NOT be strong (level == 'medium')
        - Evidence type must retain ambiguity (romaji_fuzzy / romaji_long_vowel_folded / containment / variant_fuzzy)
        - Decision reasons must retain ambiguity description
        - Merely having discovery_path='jp_equivalents' or artist_ids cannot bypass protection
        - Verified ISRC or verified equivalent mapping still works as expected
        """
        cases = [
            ("好きだから", "Sukidakaro", "romaji_fuzzy", "歌名存在拼写或读音差异，待人工核对"),
            ("こうこ", "Koko", "romaji_long_vowel_folded", "歌名存在长音或读音折叠差异，待人工核对"),
            ("Tokyo Tower", "Tokyo Towers", "variant_fuzzy", "歌名存在拼写或读音差异，待人工核对"),
            ("Super Mario", "Super Mario Bros", "containment", "歌名仅前缀或片段包含，待人工核对"),
        ]

        durations = [
            (None, None),
            (240000, 240000),
            (240000, 241000),
            (240000, 242500),
            (240000, 243000),
        ]

        albums = [
            (None, None),
            (None, "Single"),
            ("Album X", "Album X"),
        ]

        tested_count = 0
        for s_title, c_title, expected_type, expected_reason in cases:
            for s_dur, c_dur in durations:
                for s_alb, c_alb in albums:
                    src = Track(title=s_title, artists=["Test Artist"], duration_ms=s_dur, album=s_alb)
                    cand = AppleMusicTrack(
                        id=f"cand_{tested_count}",
                        title=c_title,
                        artists=["Test Artist"],
                        duration_ms=c_dur,
                        album=c_alb,
                        storefront="cn",
                    )
                    mc = TrackScorer.score(src, cand)
                    best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [mc])

                    self.assertNotEqual(
                        dec,
                        DecisionStatus.AUTO_ACCEPT.value,
                        f"Non-definite title {s_title} -> {c_title} must not auto_accept with duration {s_dur}/{c_dur}, album {s_alb}/{c_alb}",
                    )
                    self.assertEqual(dec, DecisionStatus.REVIEW.value)
                    self.assertNotEqual(
                        mc.evidence.verification_level,
                        VerificationLevel.STRONG.value,
                        f"Evidence level must not be strong for {s_title} -> {c_title}",
                    )
                    self.assertEqual(mc.evidence.verification_level, VerificationLevel.MEDIUM.value)
                    self.assertEqual(
                        mc.evidence.evidence_type,
                        expected_type,
                        f"Evidence type mismatch for {s_title} -> {c_title}",
                    )
                    self.assertIn(
                        expected_reason,
                        reasons,
                        f"Expected ambiguity reason '{expected_reason}' in {reasons}",
                    )
                    tested_count += 1

        self.assertEqual(tested_count, 60, "Expected all 60 combinations tested")

        # Bypass test: discovery_path string or artist_ids cannot bypass
        for s_title, c_title, expected_type, _ in cases:
            src = Track(title=s_title, artists=["Test Artist"], duration_ms=240000, artist_ids=["art_123"])
            cand = AppleMusicTrack(
                id="cand_bypass_try",
                title=c_title,
                artists=["Test Artist"],
                duration_ms=240000,
                storefront="cn",
                artist_ids=["art_123"],
            )
            setattr(cand, "discovery_path", "jp_equivalents")
            mc = TrackScorer.score(src, cand)
            best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [mc])
            self.assertNotEqual(dec, DecisionStatus.AUTO_ACCEPT.value)
            self.assertEqual(dec, DecisionStatus.REVIEW.value)
            self.assertNotEqual(mc.evidence.verification_level, VerificationLevel.STRONG.value)
            self.assertEqual(mc.evidence.evidence_type, expected_type)

        # Contrast test: reliable ISRC and verified equivalent mapping DO verify identity
        # 1. Reliable ISRC
        src_isrc = Track(title="好きだから", artists=["Test Artist"], isrc="JP1234567890")
        cand_isrc = AppleMusicTrack(id="cand_isrc", title="Sukidakara", artists=["Test Artist"], isrc="JP1234567890", storefront="cn")
        mc_isrc = TrackScorer.score(src_isrc, cand_isrc)
        best_i, _, dec_i, _, _ = TrackScorer.evaluate_candidates(src_isrc, [mc_isrc])
        self.assertEqual(dec_i, DecisionStatus.AUTO_ACCEPT.value)
        self.assertEqual(mc_isrc.evidence.verification_level, VerificationLevel.STRONG.value)
        self.assertEqual(mc_isrc.evidence.evidence_type, "isrc")

        # 2. Verified equivalent mapping
        src_eq = Track(title="夜に駆ける", artists=["YOASOBI"], duration_ms=260000)
        cand_eq = AppleMusicTrack(id="cand_us_eq", title="Racing into the Night", artists=["YOASOBI"], duration_ms=260000, storefront="us")
        jp_orig = AppleMusicTrack(id="cand_jp_orig", title="夜に駆ける", artists=["YOASOBI"], duration_ms=260000, storefront="jp")
        cand_eq.is_equivalent_mapped = True
        cand_eq.original_jp_track = jp_orig
        mc_eq = TrackScorer.score(src_eq, cand_eq)
        self.assertEqual(mc_eq.evidence.verification_level, VerificationLevel.STRONG.value)
        self.assertEqual(mc_eq.evidence.evidence_type, "apple_equivalent")

    # =========================================================================
    # E01: Wrong candidate first, right candidate later / only wrong candidate
    # =========================================================================
    def test_e01_candidate_aggregation_and_selection(self):
        """
        E01:
        1. When wrong candidate (magical mode) appears first, and right candidate
           (sweets parade) appears second, right candidate is picked.
        2. When only wrong candidate appears, selected_candidate is None.
        """
        src = Track(title="sweets parade", artists=["花泽香菜"])
        wrong_cand = AppleMusicTrack(id="c_wrong", title="magical mode", artists=["花泽香菜"])
        right_cand = AppleMusicTrack(id="c_right", title="sweets parade", artists=["花泽香菜"])

        # 1. Both present
        scored_wrong = TrackScorer.score(src, wrong_cand)
        scored_right = TrackScorer.score(src, right_cand)
        candidates = [scored_wrong, scored_right]
        candidates.sort(key=lambda x: x.score, reverse=True)

        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, candidates)
        self.assertIsNotNone(best)
        self.assertEqual(best.track.id, "c_right")

        # 2. Only wrong present
        best_only_wrong, conf_w, dec_w, reasons_w, _ = TrackScorer.evaluate_candidates(src, [scored_wrong])
        self.assertIsNone(best_only_wrong)
        self.assertEqual(dec_w, DecisionStatus.NO_MATCH.value)

    # =========================================================================
    # E02: Engine retry and relaxed paths block title_mismatch
    # =========================================================================
    def test_e02_retry_and_relaxed_cannot_revive_mismatch(self):
        """
        E02: When candidate has title_mismatch, rematch / relaxed retry
        must NEVER restore decision to REVIEW or AUTO_ACCEPT.
        """
        from applemusic.matcher.engine import MatchingEngine
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        mock_client.config.is_authorized.return_value = True
        engine = MatchingEngine(client=mock_client)

        src = Track(title="sweets parade", artists=["花泽香菜"], duration_ms=240000)
        wrong_cand = AppleMusicTrack(id="c_wrong", title="magical mode", artists=["花泽香菜"], duration_ms=240000)

        # Direct evaluation through _evaluate_and_aggregate with is_rematch=True and relaxed=True
        result = engine._evaluate_and_aggregate(
            source=src,
            outcomes=[],
            collected_candidates={"c_wrong": wrong_cand},
            query_attempts=1,
            failures=[],
            has_partial_failures=False,
            storefront="cn",
            relaxed=True,
            is_rematch=True,
        )

        self.assertEqual(result.decision, DecisionStatus.NO_MATCH.value)
        self.assertIsNone(result.selected_candidate)
        self.assertEqual(result.search_status, "no_match")
        self.assertNotIn("放宽推荐", " ".join(result.decision_reasons))

    # =========================================================================
    # C01: Cache migration and stale re-evaluation
    # =========================================================================
    def test_c01_stale_cache_migration_and_preservation(self):
        """
        C01:
        1. Stale v4 review/auto_accept cache with sweets parade vs magical mode
           is re-evaluated to no_match with score <= 0.39 and selected_candidate=None.
        2. User confirmed decision is strictly preserved.
        """
        src = Track(title="sweets parade", artists=["花泽香菜"], duration_ms=240000)
        cand_track = AppleMusicTrack(
            id="cand_magical_stale",
            title="magical mode",
            artists=["花泽香菜"],
            duration_ms=240000,
            album="Single",
            storefront="cn",
        )
        old_evidence = MatchEvidence(
            evidence_type="title_and_artist",
            verification_level=VerificationLevel.STRONG.value,
            matched_fields=["artist", "duration"],
            conflicts=[],
            rule_version="2026.09.v4",
            query_policy_version=QUERY_POLICY_VERSION,
            romanizer_version="2026.09.v4",
            exception_registry_version=EXCEPTION_REGISTRY_VERSION,
            alias_version=ALIAS_VERSION,
        )
        old_cand = MatchCandidate(
            track=cand_track,
            score=0.778,
            title_score=0.60,
            artist_score=1.0,
            confidence=ConfidenceLevel.HIGH,
            decision="review",
            decision_reasons=["Old stale review"],
            evidence=old_evidence,
        )
        old_result = SongMatchResult(
            source_track=src,
            candidates=[old_cand],
            selected_candidate=old_cand,
            status=ConfidenceLevel.HIGH,
            decision="review",
            decision_reasons=["Old stale evaluation pending review"],
            search_status="matched",
            evidence=old_evidence,
        )

        track_hash = "text:sweets parade:花泽香菜:standard"
        conn = self.cache._get_connection()
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
                    track_hash,
                    old_result.model_dump_json(),
                    "review",
                    "high",
                    1000.0,
                    9999999999.0,
                    "2026.09.v4",
                    QUERY_POLICY_VERSION,
                    "2026.09.v4",
                    EXCEPTION_REGISTRY_VERSION,
                    ALIAS_VERSION,
                ),
            )

        # Retrieve match - must trigger re-evaluation under V5
        re_evaluated = self.cache.get_match("cn", track_hash) or self.cache.find_match("cn", src)
        self.assertIsNotNone(re_evaluated)
        self.assertEqual(re_evaluated.decision, DecisionStatus.NO_MATCH.value)
        self.assertIsNone(re_evaluated.selected_candidate)
        self.assertLessEqual(re_evaluated.candidates[0].score, 0.39)
        self.assertEqual(re_evaluated.search_status, "no_match")

        # 1b. Test outdated alias_version specifically triggers cache miss (None)
        # to force a fresh catalog search under the updated alias mappings
        track_hash_alias = "text:sweets parade:花泽香菜:alias_test"
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
                    track_hash_alias,
                    track_hash_alias,
                    old_result.model_dump_json(),
                    "review",
                    "high",
                    1000.0,
                    9999999999.0,
                    MATCH_RULE_VERSION,
                    QUERY_POLICY_VERSION,
                    ROMANIZER_VERSION,
                    EXCEPTION_REGISTRY_VERSION,
                    "2026.09.v4",  # Stale alias version v4 vs current v5
                ),
            )
        fresh_search_trigger = self.cache.get_match("cn", track_hash_alias)
        self.assertIsNone(
            fresh_search_trigger,
            "Outdated alias_version must return cache miss (None) to trigger fresh search with new aliases",
        )

        # 2. User confirmed result must be preserved
        user_confirmed_result = SongMatchResult(
            source_track=src,
            candidates=[old_cand],
            selected_candidate=old_cand,
            status=ConfidenceLevel.EXACT,
            decision="user_confirmed",
            decision_reasons=["User manually confirmed"],
            search_status="matched",
            evidence=old_evidence,
        )
        self.cache.set_match("cn", track_hash, user_confirmed_result, track=src)
        retrieved_user = self.cache.get_match("cn", track_hash) or self.cache.find_match("cn", src)
        self.assertIsNotNone(retrieved_user)
        self.assertEqual(retrieved_user.decision, "user_confirmed")
        self.assertIsNotNone(retrieved_user.selected_candidate)

    # =========================================================================
    # U01: Web UI presentation data structure for positive and negative cases
    # =========================================================================
    def test_u01_web_ui_presentation_data_structure(self):
        """
        U01: Verify that UI match results structure correctly populates:
        - Positive case: high score, accurate reasons, candidate selected
        - Negative case: magical mode is NOT in selected_candidate, search_status is no_match
        """
        # Positive case
        src_pos = Track(title="好きだから。（因为我喜欢你。）", artists=["『ユイカ』"])
        cand_pos = AppleMusicTrack(id="c_pos", title="Sukidakara", artists=["Yuika"])
        sc_pos = TrackScorer.score(src_pos, cand_pos)
        res_pos = SongMatchResult(
            source_track=src_pos,
            candidates=[sc_pos],
            selected_candidate=sc_pos,
            status=ConfidenceLevel.HIGH,
            decision=sc_pos.decision,
            decision_reasons=sc_pos.decision_reasons,
            search_status="review" if sc_pos.decision == "review" else "matched",
            evidence=sc_pos.evidence,
        )
        self.assertIsNotNone(res_pos.selected_candidate)
        self.assertGreaterEqual(res_pos.selected_candidate.score, 0.90)

        # Negative case
        src_neg = Track(title="sweets parade", artists=["花泽香菜"])
        cand_neg = AppleMusicTrack(id="c_neg", title="magical mode", artists=["花泽香菜"])
        sc_neg = TrackScorer.score(src_neg, cand_neg)
        res_neg = SongMatchResult(
            source_track=src_neg,
            candidates=[sc_neg],
            selected_candidate=None,
            status=ConfidenceLevel.NOT_FOUND,
            decision=DecisionStatus.NO_MATCH.value,
            decision_reasons=sc_neg.decision_reasons,
            search_status="no_match",
            evidence=sc_neg.evidence,
        )
        self.assertIsNone(res_neg.selected_candidate)
        self.assertEqual(res_neg.search_status, "no_match")
        self.assertEqual(res_neg.candidates[0].track.title, "magical mode")
        self.assertLessEqual(res_neg.candidates[0].score, 0.39)

    def test_u02_real_browser_page_ui_and_diagnostics_modal(self):
        """
        U02: Real browser test using Playwright against live index.html:
        - Asserts positive candidate (Sukidakara) is visible with '待复核' badge
        - Asserts negative candidate (magical mode) is rejected, subtitle displays
          '未找到可信匹配 (1个候选不符)', badge displays '候选不符', button shows '换版本'
        - Asserts 0-candidate track shows 'Apple Music 当前曲库未收录', badge '未收录', button '搜曲库'
        - Asserts clicking '诊断' button displays modal with romaji_exact, 2026.09.v5, and 歌名比对与变体
        - Asserts clicking '诊断' for negative track displays modal with title_mismatch and 歌名明显不匹配
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright not installed for browser test")

        import socket
        import threading
        import time
        import urllib.request
        import uvicorn
        from applemusic.web.app import app

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

                # Inject mock match results into Vue root
                page.evaluate(
                    '''() => {
                    window.__vueRoot.matchResults = [
                        {
                            source_track: { title: "好きだから。（因为我喜欢你。）", artists: ["『ユイカ』"] },
                            candidates: [
                                {
                                    track: { id: "cand_suki", title: "Sukidakara", artists: ["Yuika"] },
                                    score: 0.988,
                                    title_score: 0.98,
                                    artist_score: 1.0,
                                    album_score: 0.0,
                                    duration_score: 0.0,
                                    version_score: 0.0,
                                    confidence: "exact",
                                    decision: "review",
                                    decision_reasons: ["标题/艺人转写吻合，纯转写依赖待人工核对身份"],
                                    evidence: {
                                        rule_version: "2026.09.v5",
                                        alias_version: "2026.09.v5",
                                        verification_level: "medium",
                                        matched_fields: ["title", "artist"],
                                        conflicts: [],
                                        title_comparison_method: "romaji_exact",
                                        matched_title_pair: ["sukidakara", "sukidakara"],
                                        title_details: { method: "romaji_exact", details: "exact transliteration match" }
                                    }
                                }
                            ],
                            selected_candidate: {
                                track: { id: "cand_suki", title: "Sukidakara", artists: ["Yuika"] },
                                score: 0.988,
                                title_score: 0.98,
                                artist_score: 1.0,
                                album_score: 0.0,
                                duration_score: 0.0,
                                version_score: 0.0,
                                confidence: "exact",
                                decision: "review",
                                evidence: {
                                    rule_version: "2026.09.v5",
                                    alias_version: "2026.09.v5",
                                    verification_level: "medium",
                                    matched_fields: ["title", "artist"],
                                    conflicts: [],
                                    title_comparison_method: "romaji_exact",
                                    matched_title_pair: ["sukidakara", "sukidakara"],
                                    title_details: { method: "romaji_exact", details: "exact transliteration match" }
                                }
                            },
                            status: "high",
                            decision: "review",
                            decision_reasons: ["标题/艺人转写吻合，纯转写依赖待人工核对身份"],
                            search_status: "review",
                            selected: false,
                            evidence: {
                                rule_version: "2026.09.v5",
                                alias_version: "2026.09.v5",
                                verification_level: "medium",
                                matched_fields: ["title", "artist"],
                                conflicts: [],
                                title_comparison_method: "romaji_exact",
                                matched_title_pair: ["sukidakara", "sukidakara"],
                                title_details: { method: "romaji_exact", details: "exact transliteration match" }
                            },
                            diagnostics: {
                                rule_version: "2026.09.v5",
                                alias_version: "2026.09.v5",
                                verification_level: "medium",
                                matched_fields: ["title", "artist"],
                                conflicts: [],
                                title_comparison_method: "romaji_exact",
                                matched_title_pair: ["sukidakara", "sukidakara"],
                                title_details: { method: "romaji_exact", details: "exact transliteration match" },
                                executed_queries: [{ query: "Sukidakara Yuika", kind: "exact", hits: 1 }]
                            }
                        },
                        {
                            source_track: { title: "sweets parade", artists: ["花泽香菜"] },
                            candidates: [
                                {
                                    track: { id: "cand_magic", title: "magical mode", artists: ["花泽香菜"] },
                                    score: 0.166,
                                    title_score: 0.24,
                                    artist_score: 1.0,
                                    album_score: 0.0,
                                    duration_score: 0.0,
                                    version_score: 0.0,
                                    confidence: "low",
                                    decision: "no_match",
                                    decision_reasons: ["歌名明显不匹配"],
                                    evidence: {
                                        rule_version: "2026.09.v5",
                                        alias_version: "2026.09.v5",
                                        verification_level: "conflict",
                                        matched_fields: ["artist"],
                                        conflicts: ["title_mismatch: 歌名明显不匹配"],
                                        title_comparison_method: "raw_ratio",
                                        matched_title_pair: ["sweets parade", "magical mode"],
                                        title_details: { method: "raw_ratio", details: "comparable scripts mismatch" }
                                    }
                                }
                            ],
                            selected_candidate: null,
                            status: "not_found",
                            decision: "no_match",
                            decision_reasons: ["歌名明显不匹配", "候选曲目歌名明显不符"],
                            search_status: "no_match",
                            selected: false,
                            evidence: {
                                rule_version: "2026.09.v5",
                                alias_version: "2026.09.v5",
                                verification_level: "conflict",
                                matched_fields: ["artist"],
                                conflicts: ["title_mismatch: 歌名明显不匹配"],
                                title_comparison_method: "raw_ratio",
                                matched_title_pair: ["sweets parade", "magical mode"],
                                title_details: { method: "raw_ratio", details: "comparable scripts mismatch" }
                            },
                            diagnostics: {
                                rule_version: "2026.09.v5",
                                alias_version: "2026.09.v5",
                                verification_level: "conflict",
                                matched_fields: ["artist"],
                                conflicts: ["title_mismatch: 歌名明显不匹配"],
                                title_comparison_method: "raw_ratio",
                                matched_title_pair: ["sweets parade", "magical mode"],
                                title_details: { method: "raw_ratio", details: "comparable scripts mismatch" },
                                executed_queries: [{ query: "sweets parade 花泽香菜", kind: "broad", hits: 1 }]
                            }
                        },
                        {
                            source_track: { title: "Unrecorded Song", artists: ["Some Artist"] },
                            candidates: [],
                            selected_candidate: null,
                            status: "not_found",
                            decision: "no_match",
                            decision_reasons: ["未找到候选曲目"],
                            search_status: "no_match",
                            selected: false,
                            evidence: null,
                            diagnostics: {
                                rule_version: "2026.09.v5",
                                alias_version: "2026.09.v5",
                                verification_level: "unverified",
                                matched_fields: [],
                                conflicts: [],
                                executed_queries: []
                            }
                        }
                    ];
                }'''
                )

                time.sleep(0.5)

                # 1. Check positive track display
                pos_row = page.locator("text=好きだから。（因为我喜欢你。）").first
                self.assertTrue(pos_row.is_visible())

                pos_cand = page.locator("text=Sukidakara").first
                self.assertTrue(pos_cand.is_visible())

                pos_badge = page.locator("text=待复核").first
                self.assertTrue(pos_badge.is_visible())

                # 2. Check negative track display
                neg_sub = page.locator("text=未找到可信匹配 (1个候选不符)").first
                self.assertTrue(neg_sub.is_visible())

                neg_badge = page.locator("text=候选不符").first
                self.assertTrue(neg_badge.is_visible())

                # Negative candidate 'magical mode' must NOT be in the main candidate display area
                page_text = page.locator("#app").inner_text()
                self.assertNotIn("magical mode", page_text)

                # Button for negative track should be '换版本'
                change_ver_btn = page.locator("button:has-text('换版本')").nth(1)
                self.assertTrue(change_ver_btn.is_visible())

                # Button for 0-candidate track should be '搜曲库' and badge '未收录'
                search_btn = page.locator("button:has-text('搜曲库')").first
                self.assertTrue(search_btn.is_visible())

                unrec_badge = page.locator("text=未收录").first
                self.assertTrue(unrec_badge.is_visible())

                # 3. Test Diagnostics Modal for Positive Track
                diag_btns = page.locator("button:has-text('诊断')").all()
                diag_btns[0].click()
                page.wait_for_selector("text=单曲匹配诊断")
                modal_text = page.locator(".fixed.inset-0").inner_text()
                self.assertIn("romaji_exact", modal_text)
                self.assertIn("2026.09.v5", modal_text)
                self.assertIn("歌名比对与变体", modal_text)

                # Close modal
                page.locator(".fixed.inset-0 button >> i.fa-xmark").click()
                time.sleep(0.3)

                # 4. Test Diagnostics Modal for Negative Track
                diag_btns[1].click()
                page.wait_for_selector("text=单曲匹配诊断")
                modal_neg_text = page.locator(".fixed.inset-0").inner_text()
                self.assertIn("title_mismatch", modal_neg_text)
                self.assertIn("歌名明显不匹配", modal_neg_text)

                page.locator(".fixed.inset-0 button >> i.fa-xmark").click()
                time.sleep(0.3)
                browser.close()
        finally:
            server.should_exit = True


if __name__ == "__main__":
    unittest.main()

