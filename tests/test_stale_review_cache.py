"""
Regression and boundary test suite for stale review cache handling.
Verifies all 6 scenarios in .codex-workflow/stale-review-cache/TEST_MATRIX.md:
1. Stale review cache (sweets paper vs magical mode) re-evaluated to no_match, ~0.138 score, no selected candidate, DB synced.
2. Stale auto_accept cache re-evaluated and demoted appropriately.
3. Current version review matches cleanly without re-calculation or alteration.
4. User confirmed match strictly preserved despite outdated versions.
5. Outdated cache with missing source track or candidates handled safely (cache miss / safe review fallback).
6. Multi-index hits (ISRC, source original ID, text key) execute the same version validation and migration.

Zero hardcoding of song or artist names.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from applemusic.cache import PersistentCache
from applemusic.matcher.cleaner import TextCleaner
from applemusic.matcher.evidence import (
    MATCH_RULE_VERSION,
    QUERY_POLICY_VERSION,
    ROMANIZER_VERSION,
    EXCEPTION_REGISTRY_VERSION,
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


class TestStaleReviewCache(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_match_cache.db"
        self.cache = PersistentCache(self.db_path)

    def tearDown(self):
        self.cache.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_01_stale_review_screenshot_example(self):
        """
        Matrix Scenario 1: Stale review cache (screenshot example).
        - Source: sweets paper - 花澤香菜
        - Candidate: magical mode - 花泽香菜
        - Stale cache: old rule version '2026.09.v1', review decision, score 0.72.
        - Expected: re-evaluated to no_match, candidate score ~0.138, title_score ~0.083,
          artist_score 1.0, selected_candidate is None, DB updated to MATCH_RULE_VERSION.
          Subsequent reads return no_match and do not revive 0.72 candidate.
        """
        src = Track(title="sweets paper", artists=["花澤香菜"])
        cand_track = AppleMusicTrack(
            id="cand_magical_mode",
            title="magical mode",
            artists=["花泽香菜"],
            storefront="cn",
        )
        old_evidence = MatchEvidence(
            evidence_type="title_and_artist",
            verification_level=VerificationLevel.MEDIUM.value,
            matched_fields=["artist"],
            conflicts=[],
            rule_version="2026.09.v1",
            query_policy_version="2026.09.v1",
            romanizer_version="2026.09.v1",
            exception_registry_version="2026.09.v1",
        )
        old_cand = MatchCandidate(
            track=cand_track,
            score=0.72,
            title_score=0.45,
            artist_score=1.0,
            confidence=ConfidenceLevel.HIGH,
            decision="review",
            decision_reasons=["Old evaluation"],
            evidence=old_evidence,
        )
        old_result = SongMatchResult(
            source_track=src,
            candidates=[old_cand],
            selected_candidate=old_cand,
            status=ConfidenceLevel.HIGH,
            decision="review",
            decision_reasons=["Old evaluation pending review"],
            evidence=old_evidence,
            search_status="matched",
        )

        track_hash = "hash_sweets_paper"
        conn = self.cache._get_connection()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO match_cache
                (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at,
                 rule_version, query_policy_version, romanizer_version, exception_registry_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    "2026.09.v1",
                    "2026.09.v1",
                    "2026.09.v1",
                    "2026.09.v1",
                ),
            )

        # 1. First read triggers re-evaluation
        re_evaluated = self.cache.get_match("cn", track_hash)
        self.assertIsNotNone(re_evaluated)
        self.assertEqual(re_evaluated.decision, "no_match")
        self.assertEqual(re_evaluated.status, ConfidenceLevel.NOT_FOUND)
        self.assertIsNone(re_evaluated.selected_candidate, "Must clear selected_candidate on no_match!")
        self.assertEqual(re_evaluated.search_status, "no_match")

        # Candidate scores verified under current TrackScorer
        self.assertEqual(len(re_evaluated.candidates), 1)
        res_cand = re_evaluated.candidates[0]
        self.assertAlmostEqual(res_cand.score, 0.138, places=2)
        self.assertAlmostEqual(res_cand.title_score, 0.083, places=2)
        self.assertEqual(res_cand.artist_score, 1.0)

        # 2. Verify database was synchronized to new version and decision
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT result_json, decision, status, rule_version, query_policy_version, romanizer_version, exception_registry_version
            FROM match_cache
            WHERE storefront = 'cn' AND track_hash = ?
            """,
            (track_hash,),
        )
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        db_json_str, db_dec, db_stat, db_rule, db_pol, db_rom, db_reg = row
        self.assertEqual(db_dec, "no_match")
        self.assertEqual(db_stat, "not_found")
        self.assertEqual(db_rule, MATCH_RULE_VERSION)
        self.assertEqual(db_pol, QUERY_POLICY_VERSION)
        self.assertEqual(db_rom, ROMANIZER_VERSION)
        self.assertEqual(db_reg, EXCEPTION_REGISTRY_VERSION)

        parsed_db = json.loads(db_json_str)
        self.assertEqual(parsed_db["decision"], "no_match")
        self.assertIsNone(parsed_db["selected_candidate"])

        # 3. Subsequent reads must return the updated no_match record without resurrecting 0.72 candidate
        second_read = self.cache.get_match("cn", track_hash)
        self.assertIsNotNone(second_read)
        self.assertEqual(second_read.decision, "no_match")
        self.assertIsNone(second_read.selected_candidate)
        self.assertEqual(second_read.status, ConfidenceLevel.NOT_FOUND)

    def test_02_stale_auto_accept_demoted(self):
        """
        Matrix Scenario 2: Stale auto_accept.
        - Legacy record has auto_accept, but under current rules the candidate has a version conflict or low score.
        - Expected: re-evaluated and demoted (not retained as auto_accept).
        """
        src = Track(title="打上花火", artists=["米津玄師"], album="BOOTLEG", duration_ms=289000)
        cand_track = AppleMusicTrack(
            id="cand_diff_duration",
            title="打上花火",
            artists=["米津玄師"],
            album="Single",
            duration_ms=315000,
            storefront="cn",
        )
        old_evidence = MatchEvidence(
            evidence_type="title_and_artist",
            verification_level=VerificationLevel.STRONG.value,
            matched_fields=["title", "artist"],
            rule_version="2026.09.v1",
        )
        old_cand = MatchCandidate(
            track=cand_track,
            score=0.98,
            title_score=1.0,
            artist_score=1.0,
            confidence=ConfidenceLevel.EXACT,
            decision="auto_accept",
            evidence=old_evidence,
        )
        old_result = SongMatchResult(
            source_track=src,
            candidates=[old_cand],
            selected_candidate=old_cand,
            status=ConfidenceLevel.EXACT,
            decision="auto_accept",
            evidence=old_evidence,
        )

        track_hash = "hash_auto_demote"
        conn = self.cache._get_connection()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO match_cache
                (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at,
                 rule_version, query_policy_version, romanizer_version, exception_registry_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "cn",
                    track_hash,
                    track_hash,
                    old_result.model_dump_json(),
                    "auto_accept",
                    "exact",
                    1000.0,
                    9999999999.0,
                    "2026.09.v1",
                    "2026.09.v1",
                    "2026.09.v1",
                    "2026.09.v1",
                ),
            )

        re_evaluated = self.cache.get_match("cn", track_hash)
        self.assertIsNotNone(re_evaluated)
        self.assertNotEqual(re_evaluated.decision, "auto_accept", "Legacy auto_accept must NOT be preserved!")
        self.assertEqual(re_evaluated.decision, "review")

    def test_03_current_version_review_not_recomputed(self):
        """
        Matrix Scenario 3: Current version review.
        - All versions match current version (MATCH_RULE_VERSION, etc.).
        - Expected: clean cache hit, returns original record without unnecessary re-scoring or alteration.
        """
        src = Track(title="春よ、来い", artists=["松任谷由実"])
        cand_track = AppleMusicTrack(
            id="cand_haruyo",
            title="春よ、来い",
            artists=["松任谷由実"],
            storefront="cn",
        )
        cur_evidence = MatchEvidence(
            evidence_type="title_and_artist",
            verification_level=VerificationLevel.MEDIUM.value,
            matched_fields=["title", "artist"],
            rule_version=MATCH_RULE_VERSION,
            query_policy_version=QUERY_POLICY_VERSION,
            romanizer_version=ROMANIZER_VERSION,
            exception_registry_version=EXCEPTION_REGISTRY_VERSION,
        )
        cur_cand = MatchCandidate(
            track=cand_track,
            score=0.78,
            title_score=1.0,
            artist_score=1.0,
            confidence=ConfidenceLevel.HIGH,
            decision="review",
            decision_reasons=["Current manual review pending"],
            evidence=cur_evidence,
        )
        cur_result = SongMatchResult(
            source_track=src,
            candidates=[cur_cand],
            selected_candidate=cur_cand,
            status=ConfidenceLevel.HIGH,
            decision="review",
            decision_reasons=["Current manual review pending"],
            evidence=cur_evidence,
        )

        track_hash = "hash_cur_review"
        conn = self.cache._get_connection()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO match_cache
                (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at,
                 rule_version, query_policy_version, romanizer_version, exception_registry_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "cn",
                    track_hash,
                    track_hash,
                    cur_result.model_dump_json(),
                    "review",
                    "high",
                    1000.0,
                    9999999999.0,
                    MATCH_RULE_VERSION,
                    QUERY_POLICY_VERSION,
                    ROMANIZER_VERSION,
                    EXCEPTION_REGISTRY_VERSION,
                ),
            )

        retrieved = self.cache.get_match("cn", track_hash)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.decision, "review")
        self.assertEqual(retrieved.selected_candidate.score, 0.78)
        self.assertIn("Current manual review pending", retrieved.decision_reasons)
        self.assertFalse(any("规则版本升级" in r for r in retrieved.decision_reasons))

    def test_04_user_confirmed_preserved_even_when_outdated(self):
        """
        Matrix Scenario 4: User confirmed match.
        - Decision is user_confirmed with outdated rule version ('2026.09.v1').
        - Expected: Strictly preserves user confirmation and selected candidate unconditionally.
        """
        src = Track(title="Custom Title", artists=["Custom Artist"])
        cand_track = AppleMusicTrack(
            id="user_sel_123",
            title="Different Title",
            artists=["Different Artist"],
            storefront="cn",
        )
        evidence = MatchEvidence(
            evidence_type="manual",
            verification_level=VerificationLevel.STRONG.value,
            rule_version="2026.09.v1",
        )
        cand = MatchCandidate(
            track=cand_track,
            score=1.0,
            decision="user_confirmed",
            confidence=ConfidenceLevel.EXACT,
            evidence=evidence,
        )
        result = SongMatchResult(
            source_track=src,
            selected_candidate=cand,
            candidates=[cand],
            status=ConfidenceLevel.EXACT,
            decision="user_confirmed",
            evidence=evidence,
        )

        track_hash = "hash_user_conf"
        conn = self.cache._get_connection()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO match_cache
                (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at,
                 rule_version, query_policy_version, romanizer_version, exception_registry_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "cn",
                    track_hash,
                    track_hash,
                    result.model_dump_json(),
                    "user_confirmed",
                    "exact",
                    1000.0,
                    9999999999.0,
                    "2026.09.v1",
                    "2026.09.v1",
                    "2026.09.v1",
                    "2026.09.v1",
                ),
            )

        retrieved = self.cache.get_match("cn", track_hash)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.decision, "user_confirmed")
        self.assertIsNotNone(retrieved.selected_candidate)
        self.assertEqual(retrieved.selected_candidate.track.id, "user_sel_123")

    def test_05_outdated_cache_cannot_reevaluate_safely(self):
        """
        Matrix Scenario 5: Outdated cache cannot be safely re-evaluated.
        5a: Missing source_track -> returns None (cache miss, triggering fresh search).
        5b: Missing candidates with source_track present -> safe review fallback without stale candidate or score.
        """
        # 5a: Missing source track
        cand_track = AppleMusicTrack(id="cand_x", title="Title", artists=["Artist"], storefront="cn")
        cand = MatchCandidate(track=cand_track, score=0.80)
        # Using dict directly to bypass Pydantic model validation of required source_track
        missing_src_data = {
            "source_track": None,
            "candidates": [cand.model_dump()],
            "selected_candidate": cand.model_dump(),
            "decision": "review",
            "status": "high",
        }
        conn = self.cache._get_connection()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO match_cache
                (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at,
                 rule_version, query_policy_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("cn", "hash_no_src", "hash_no_src", json.dumps(missing_src_data), "review", "high", 1000.0, 9999999999.0, "2026.09.v1", "2026.09.v1"),
            )

        miss = self.cache.get_match("cn", "hash_no_src")
        self.assertIsNone(miss, "Outdated cache with missing source_track must return None (cache miss)!")

        # 5b: Missing candidates with source_track present
        src = Track(title="Valid Title", artists=["Valid Artist"])
        res_no_cands = SongMatchResult(
            source_track=src,
            candidates=[],
            selected_candidate=None,
            status=ConfidenceLevel.EXACT,
            decision="auto_accept",
        )
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO match_cache
                (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at,
                 rule_version, query_policy_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("cn", "hash_no_cands", "hash_no_cands", res_no_cands.model_dump_json(), "auto_accept", "exact", 1000.0, 9999999999.0, "2026.09.v1", "2026.09.v1"),
            )

        fallback = self.cache.get_match("cn", "hash_no_cands")
        self.assertIsNone(fallback, "Outdated cache with missing candidates must return None (cache miss)!")

        # DB must NOT be marked with MATCH_RULE_VERSION since re-evaluation was not performed
        cursor = conn.cursor()
        cursor.execute("SELECT rule_version FROM match_cache WHERE track_hash = 'hash_no_cands'")
        row = cursor.fetchone()
        self.assertNotEqual(row[0], MATCH_RULE_VERSION, "Un-reevaluated record must not be marked as current version!")

    def test_06_secondary_index_re_evaluation_and_propagation(self):
        """
        Matrix Scenario 6: Secondary index lookup.
        - A track has secondary indexes: ISRC, source original ID, and canonical text key.
        - An outdated false-positive review (sweets paper vs magical mode) is stored across primary and secondary keys.
        - When accessed via secondary index (find_match or get_match with isrc:/text:),
          it must trigger the same re-evaluation, not return the demoted record, and synchronize DB.
        """
        src = Track(
            title="sweets paper",
            artists=["花澤香菜"],
            isrc="JPB001234567",
            original_id="src_9999",
            source="netease",
        )
        cand_track = AppleMusicTrack(
            id="cand_magical_sec",
            title="magical mode",
            artists=["花泽香菜"],
            storefront="cn",
        )
        old_evidence = MatchEvidence(
            evidence_type="title_and_artist",
            verification_level=VerificationLevel.MEDIUM.value,
            rule_version="2026.09.v1",
        )
        old_cand = MatchCandidate(
            track=cand_track,
            score=0.72,
            title_score=0.45,
            artist_score=1.0,
            confidence=ConfidenceLevel.HIGH,
            decision="review",
            evidence=old_evidence,
        )
        old_result = SongMatchResult(
            source_track=src,
            candidates=[old_cand],
            selected_candidate=old_cand,
            status=ConfidenceLevel.HIGH,
            decision="review",
            evidence=old_evidence,
        )

        isrc_key = "isrc:JPB001234567"
        src_id_key = "netease:src_9999"
        clean_t, parsed_tags = TextCleaner.parse_title(src.title)
        v_tag_str = ",".join(sorted(parsed_tags)) if parsed_tags else "standard"
        pri_a, _ = TextCleaner.parse_artists(src.artists)
        norm_a = TextCleaner.normalize(pri_a).lower()
        text_key = f"text:{clean_t.lower()}:{norm_a}:{v_tag_str}"

        conn = self.cache._get_connection()
        with conn:
            for k in ("primary_hash_sec", isrc_key, src_id_key, text_key):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO match_cache
                    (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at,
                     rule_version, query_policy_version, romanizer_version, exception_registry_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "cn",
                        k,
                        k,
                        old_result.model_dump_json(),
                        "review",
                        "high",
                        1000.0,
                        9999999999.0,
                        "2026.09.v1",
                        "2026.09.v1",
                        "2026.09.v1",
                        "2026.09.v1",
                    ),
                )

        # 1. find_match must NOT return the demoted no_match result
        find_res = self.cache.find_match("cn", src)
        self.assertIsNone(find_res, "find_match must not return demoted no_match/review!")

        # 2. get_match on the secondary ISRC key directly returns re-evaluated no_match
        isrc_res = self.cache.get_match("cn", isrc_key)
        self.assertIsNotNone(isrc_res)
        self.assertEqual(isrc_res.decision, "no_match")
        self.assertIsNone(isrc_res.selected_candidate)
        self.assertAlmostEqual(isrc_res.candidates[0].score, 0.138, places=2)

        # 3. All associated secondary keys must be synchronized in DB to no_match and current version
        cursor = conn.cursor()
        for k in (isrc_key, src_id_key, text_key):
            cursor.execute("SELECT decision, rule_version FROM match_cache WHERE track_hash = ?", (k,))
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "no_match", f"Key {k} must be synchronized to no_match")
            self.assertEqual(row[1], MATCH_RULE_VERSION, f"Key {k} must have rule_version updated")

        # 4. If primary key is subsequently accessed, it also re-evaluates properly
        prim_res = self.cache.get_match("cn", "primary_hash_sec")
        self.assertIsNotNone(prim_res)
        self.assertEqual(prim_res.decision, "no_match")
        self.assertIsNone(prim_res.selected_candidate)

    def test_07_no_hardcoding_behavioral_generality(self):
        """
        Verify behavioral generality: Any arbitrary mismatched track with old review score
        re-evaluates according to TrackScorer logic and clears selected_candidate.
        """
        src = Track(title="Sky High", artists=["Band A"])
        cand_track = AppleMusicTrack(id="arbitrary_99", title="Deep Ocean Blue", artists=["Band A"], storefront="cn")
        old_evidence = MatchEvidence(evidence_type="title_and_artist", rule_version="2026.09.v1")
        old_cand = MatchCandidate(track=cand_track, score=0.68, evidence=old_evidence)
        old_res = SongMatchResult(
            source_track=src,
            candidates=[old_cand],
            selected_candidate=old_cand,
            decision="review",
            status=ConfidenceLevel.HIGH,
            evidence=old_evidence,
        )

        conn = self.cache._get_connection()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO match_cache
                (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at, rule_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("cn", "arb_hash", "arb_hash", old_res.model_dump_json(), "review", "high", 1000.0, 9999999999.0, "2026.09.v1"),
            )

        res = self.cache.get_match("cn", "arb_hash")
        self.assertIsNotNone(res)
        self.assertEqual(res.decision, "no_match")
        self.assertIsNone(res.selected_candidate)
        self.assertEqual(res.status, ConfidenceLevel.NOT_FOUND)

    def test_08_engine_integration_triggers_research_on_missing_candidates(self):
        """
        Matrix Integration: When an outdated cache record has missing candidates (or missing source track),
        get_match returns None (cache miss).
        In engine.match_playlist(), cached_match is None, so key is appended to to_query_keys
        and engine actually calls search/match_track instead of skipping it.
        """
        from unittest.mock import MagicMock, patch
        from applemusic.client import AppleMusicClient
        from applemusic.config import Config
        from applemusic.matcher.engine import MatchingEngine
        from applemusic.models import Playlist

        mock_client = MagicMock(spec=AppleMusicClient)
        config = Config(storefront="cn")
        mock_client.config = config
        engine = MatchingEngine(mock_client, config)
        engine.persistent_cache = self.cache

        track = Track(title="Song Without Cands", artists=["Artist Alpha"])
        key = engine.get_stable_track_key(track)
        key_str = ":".join(str(x) for x in key)

        # Pre-seed match cache with an outdated record that has no candidates
        res_stale = SongMatchResult(
            source_track=track,
            candidates=[],
            selected_candidate=None,
            status=ConfidenceLevel.EXACT,
            decision="auto_accept",
        )
        conn = self.cache._get_connection()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO match_cache
                (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at,
                 rule_version, query_policy_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("cn", key_str, key_str, res_stale.model_dump_json(), "auto_accept", "exact", 1000.0, 9999999999.0, "2026.09.v1", "2026.09.v1"),
            )

        playlist = Playlist(name="Test Missing Cands", tracks=[track])
        with patch.object(engine, "match_track") as mock_match_track:
            mock_match_track.return_value = SongMatchResult(
                source_track=track,
                candidates=[],
                decision="no_match",
                status=ConfidenceLevel.NOT_FOUND,
            )
            results = engine.match_playlist(playlist, storefront="cn")
            self.assertEqual(len(results), 1)
            # Crucial assertion: match_track WAS called because cache returned None (miss)
            mock_match_track.assert_called_once()

            # Contrast test: verify that a valid current-version auto_accept DOES bypass match_track
            mock_match_track.reset_mock()
            valid_cand = MatchCandidate(
                track=AppleMusicTrack(id="valid_123", title="Song Without Cands", artists=["Artist Alpha"], storefront="cn"),
                score=1.0,
                confidence=ConfidenceLevel.EXACT,
            )
            res_valid = SongMatchResult(
                source_track=track,
                selected_candidate=valid_cand,
                candidates=[valid_cand],
                status=ConfidenceLevel.EXACT,
                decision="auto_accept",
            )
            self.cache.set_match("cn", key_str, res_valid, track=track)
            results_cached = engine.match_playlist(playlist, storefront="cn")
            self.assertEqual(len(results_cached), 1)
            self.assertEqual(results_cached[0].decision, "auto_accept")
            mock_match_track.assert_not_called()


if __name__ == "__main__":
    unittest.main()
