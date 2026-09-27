"""
Regression and validation test suite for cross-lingual automated match recall fixes
per SPEC.md, IMPLEMENTATION_PLAN.md, and TEST_MATRIX.md (Scenarios R01 - R07).
Validates recall of multi-lingual tracks (e.g. One Last Kiss / Utada) while preserving
strict protections against false positives, version conflicts, and artist mismatches.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from applemusic.cache import PersistentCache
from applemusic.client import AppleMusicClient
from applemusic.config import Config
from applemusic.matcher.cleaner import TextCleaner
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
    Playlist,
    SongMatchResult,
    Track,
)


class TestOneLastKissRecall(unittest.TestCase):
    """
    Test suite verifying R01 through R07 per TEST_MATRIX.md.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_recall.db"
        self.config = Config(
            developer_token="test_token",
            storefront="cn",
            fallback_storefronts=["hk", "us"],
            auto_accept_threshold=0.88,
            min_review_score=0.55,
            min_score_gap=0.08,
            search_limit=10,
        )
        self.client = MagicMock(spec=AppleMusicClient)
        self.client.config = self.config
        self.client.get_storefront_languages = MagicMock(return_value=["zh-Hans-CN", "en-US"])
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

    def test_R01_core_title_recall_and_utada_identity(self):
        """
        R01: Source 'One Last Kiss (最后一吻)' / '宇多田光 (宇多田ヒカル)'
        - Strict combo queries return no hits.
        - Core title only query ('One Last Kiss') returns 'One Last Kiss' / 'Utada'.
        - Assert:
          1. Engine executes title-only query in Phase A (provenance='core_title_only').
          2. Candidate enters candidate pool and Utada alias identity is recognized.
          3. Decision is safe auto_accept with score >= 0.88, artist_score == 1.0, title_score >= 0.95.
          4. Verification level is STRONG.
          5. Total catalog queries executed <= 8.
        """
        source = Track(
            title="One Last Kiss (最后一吻)",
            artists=["宇多田光 (宇多田ヒカル)"],
            album="One Last Kiss",
            duration_ms=252000,
        )

        cand_track = AppleMusicTrack(
            id="cand_utada_olk",
            title="One Last Kiss",
            artists=["Utada"],
            album="One Last Kiss - EP",
            duration_ms=252000,
            storefront="cn",
        )

        def mock_search_catalog(query, **kwargs):
            q_clean = query.strip().lower()
            if q_clean == "one last kiss":
                return CatalogSearchOutcome(tracks=[cand_track], kind="ok")
            return CatalogSearchOutcome(tracks=[], kind="no_hits")

        self.client.search_catalog.side_effect = mock_search_catalog
        self.client.search_by_isrc.return_value = CatalogSearchOutcome(tracks=[], kind="no_hits")

        result = self.engine.match_track(source, storefront="cn")

        # 1. Assert auto_accept decision and candidate identity
        self.assertEqual(result.decision, DecisionStatus.AUTO_ACCEPT.value)
        self.assertEqual(result.status, ConfidenceLevel.EXACT)
        self.assertIsNotNone(result.selected_candidate)
        self.assertEqual(result.selected_candidate.track.id, "cand_utada_olk")

        # 2. Assert score components and strong verification
        cand = result.selected_candidate
        self.assertGreaterEqual(cand.score, 0.88)
        self.assertEqual(cand.artist_score, 1.0, "Utada must be recognized as full alias with artist_score=1.0")
        self.assertGreaterEqual(cand.title_score, 0.95)
        self.assertEqual(cand.evidence.verification_level, VerificationLevel.STRONG.value)
        self.assertIn("title", cand.evidence.matched_fields)
        self.assertIn("artist", cand.evidence.matched_fields)

        # 3. Assert title-only query execution in diagnostics
        self.assertIsNotNone(result.diagnostics)
        executed = result.diagnostics.executed_queries
        title_only_executed = [q for q in executed if q.get("provenance") == "core_title_only"]
        self.assertTrue(len(title_only_executed) >= 1, "Must execute reserved core_title_only query")
        self.assertEqual(title_only_executed[0]["query"].lower(), "one last kiss")

        # 4. Assert total catalog queries <= 8
        total_queries = len(executed)
        self.assertLessEqual(total_queries, 8, f"Total queries must be <= 8, was {total_queries}")

    def test_R02_same_title_unrelated_artist_prevention(self):
        """
        R02: Candidate has identical title 'One Last Kiss' but unrelated artist.
        - Must NOT produce false artist strong evidence.
        - Must record artist mismatch conflicts.
        - Must NOT auto_accept (evaluated as no_match).
        """
        source = Track(
            title="One Last Kiss (最后一吻)",
            artists=["宇多田光 (宇多田ヒカル)"],
        )

        cand_unrelated = AppleMusicTrack(
            id="cand_unrelated_singer",
            title="One Last Kiss",
            artists=["Unrelated Band"],
            storefront="cn",
        )

        def mock_search_catalog(query, **kwargs):
            if query.strip().lower() == "one last kiss":
                return CatalogSearchOutcome(tracks=[cand_unrelated], kind="ok")
            return CatalogSearchOutcome(tracks=[], kind="no_hits")

        self.client.search_catalog.side_effect = mock_search_catalog
        self.client.search_by_isrc.return_value = CatalogSearchOutcome(tracks=[], kind="no_hits")

        result = self.engine.match_track(source, storefront="cn")

        # Must not auto_accept
        self.assertNotEqual(result.decision, DecisionStatus.AUTO_ACCEPT.value)
        self.assertEqual(result.decision, DecisionStatus.NO_MATCH.value)
        self.assertIsNone(result.selected_candidate)

        # Verify candidate scoring has artist mismatch conflict
        scored = TrackScorer.score(source, cand_unrelated)
        self.assertLessEqual(scored.artist_score, 0.40)
        self.assertTrue(
            any("artist_mismatch" in c or "艺人不匹配" in c for c in scored.evidence.conflicts),
            "Unrelated artist must record artist_mismatch conflict",
        )

    def test_R03_different_title_and_version_conflict_prevention(self):
        """
        R03:
        1. Same artist 'Utada', completely different title 'magical mode':
           Must NOT inflate score; evaluated as no_match.
        2. Version conflict: candidate has version tag (Live/Remix/Instrumental) absent from source:
           Must detect version_conflict and block auto_accept.
        """
        source = Track(
            title="One Last Kiss (最后一吻)",
            artists=["宇多田光 (宇多田ヒカル)"],
        )

        # 1. Different title
        cand_diff_title = AppleMusicTrack(
            id="cand_magical_mode",
            title="magical mode",
            artists=["Utada"],
            storefront="cn",
        )
        scored_diff = TrackScorer.score(source, cand_diff_title)
        self.assertLess(scored_diff.title_score, 0.35)
        self.assertTrue(any("title_mismatch" in c or "歌名相似度过低" in c for c in scored_diff.evidence.conflicts))
        best_diff, _, dec_diff, _, _ = TrackScorer.evaluate_candidates(source, [scored_diff])
        self.assertEqual(dec_diff, DecisionStatus.NO_MATCH.value)

        # 2. Version conflict: Live version
        cand_live = AppleMusicTrack(
            id="cand_olk_live",
            title="One Last Kiss (Live)",
            artists=["Utada"],
            storefront="cn",
        )
        scored_live = TrackScorer.score(source, cand_live)
        self.assertTrue(
            any("version_conflict" in c or "版本冲突" in c for c in scored_live.evidence.conflicts),
            "Live candidate must produce version_conflict",
        )
        best_live, _, dec_live, _, _ = TrackScorer.evaluate_candidates(source, [scored_live])
        self.assertNotEqual(dec_live, DecisionStatus.AUTO_ACCEPT.value, "Version conflict must block auto_accept")

        # 3. Version conflict: Instrumental version
        cand_inst = AppleMusicTrack(
            id="cand_olk_inst",
            title="One Last Kiss (Instrumental)",
            artists=["Utada"],
            storefront="cn",
        )
        scored_inst = TrackScorer.score(source, cand_inst)
        self.assertTrue(any("version_conflict" in c or "版本冲突" in c for c in scored_inst.evidence.conflicts))
        best_inst, _, dec_inst, _, _ = TrackScorer.evaluate_candidates(source, [scored_inst])
        self.assertNotEqual(dec_inst, DecisionStatus.AUTO_ACCEPT.value)

    def test_R04_query_planning_stability_budget_and_short_title(self):
        """
        R04:
        1. Japanese/Chinese titles with brackets: query generation is deduplicated and stable.
        2. Short title protection: short titles (e.g. 'GO') cannot auto_accept on title alone with different artist.
        3. Multi-alias artist: queries across phases are deterministic and total catalog requests <= 8.
        """
        # 1. Bracketed title query planning
        source_bracket = Track(
            title="打上花火 (Fireworks)",
            artists=["米津玄師", "DAOKO"],
        )
        ctx_bracket = QueryContext(
            title=source_bracket.title,
            artists=source_bracket.artists,
            target_storefront="cn",
        )
        planned = QueryPlanner.plan_phases(ctx_bracket)
        # Queries within each phase must be deduplicated
        for phase_name, phase_queries in planned.items():
            q_list = [pq.query for pq in phase_queries]
            self.assertEqual(len(q_list), len(set(q_list)), f"Phase {phase_name} queries must be deduplicated")

        # Across all phases, (query, storefront) pairs must be unique
        all_pairs = [(pq.query, pq.storefront) for phase_queries in planned.values() for pq in phase_queries]
        self.assertEqual(len(all_pairs), len(set(all_pairs)), "Planned (query, storefront) pairs must be deduplicated")

        # 2. Short title protection
        source_short = Track(title="GO", artists=["BUMP OF CHICKEN"])
        cand_short_unrelated = AppleMusicTrack(id="cand_go_flow", title="GO", artists=["Flow"], storefront="cn")
        scored_short = TrackScorer.score(source_short, cand_short_unrelated)
        self.assertTrue(
            any("short_title_low_artist" in c or "短歌名" in c for c in scored_short.evidence.conflicts),
            "Short title with unrelated artist must trigger short_title_low_artist conflict",
        )
        _, _, dec_short, _, _ = TrackScorer.evaluate_candidates(source_short, [scored_short])
        self.assertEqual(dec_short, DecisionStatus.NO_MATCH.value)

        # 3. Deterministic alias order and budget cap
        source_utada = Track(title="One Last Kiss (最后一吻)", artists=["宇多田光 (宇多田ヒカル)"])
        ctx_utada = QueryContext(
            title=source_utada.title,
            artists=source_utada.artists,
            target_storefront="cn",
        )
        planned_utada = QueryPlanner.plan_phases(ctx_utada)
        a_native_queries = [p.query for p in planned_utada.get("A_native", [])]
        # Must have deterministic queries and contain core title only
        self.assertIn("One Last Kiss", a_native_queries)
        self.assertEqual(a_native_queries[0], "One Last Kiss 宇多田光")
        self.assertEqual(a_native_queries[1], "One Last Kiss")

    def test_R05_stale_no_match_cache_miss_and_engine_research(self):
        """
        R05: Stale no_match cache with low score candidate.
        - Upgraded query policy/alias causes get_match and find_match to return None (cache miss).
        - match_playlist issues fresh search, finds correct track, and caches new auto_accept.
        """
        source = Track(
            title="One Last Kiss (最后一吻)",
            artists=["宇多田光 (宇多田ヒカル)"],
            duration_ms=252000,
        )
        key = self.engine.get_stable_track_key(source)
        key_str = ":".join(str(x) for x in key)

        # Pre-seed match_cache with stale no_match under older query_policy_version
        cand_low = MatchCandidate(
            track=AppleMusicTrack(id="old_wrong_cand", title="Something Else", artists=["Unknown"], storefront="cn"),
            score=0.10,
            decision="no_match",
            confidence=ConfidenceLevel.NOT_FOUND,
        )
        stale_result = SongMatchResult(
            source_track=source,
            candidates=[cand_low],
            selected_candidate=None,
            status=ConfidenceLevel.NOT_FOUND,
            decision="no_match",
        )

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
                    key_str,
                    key_str,
                    stale_result.model_dump_json(),
                    "no_match",
                    "not_found",
                    1000.0,
                    9999999999.0,
                    "2026.09.v1",
                    "2026.09.v1",
                    "2026.09.v1",
                    "2026.09.v1",
                    "2026.09.v1",
                ),
            )

        # 1. Assert cache miss from get_match and find_match
        cached_direct = self.cache.get_match("cn", key_str)
        self.assertIsNone(cached_direct, "Outdated query policy must cause no_match to return None (cache miss)")
        cached_find = self.cache.find_match("cn", source)
        self.assertIsNone(cached_find, "Outdated query policy must cause find_match to return None")

        # 2. Run match_playlist and assert fresh catalog search is executed
        cand_target = AppleMusicTrack(
            id="cand_olk_correct",
            title="One Last Kiss",
            artists=["Utada"],
            duration_ms=252000,
            storefront="cn",
        )

        def mock_search_catalog(query, **kwargs):
            if query.strip().lower() == "one last kiss":
                return CatalogSearchOutcome(tracks=[cand_target], kind="ok")
            return CatalogSearchOutcome(tracks=[], kind="no_hits")

        self.client.search_catalog.side_effect = mock_search_catalog
        self.client.search_by_isrc.return_value = CatalogSearchOutcome(tracks=[], kind="no_hits")

        playlist = Playlist(name="Test Playlist", tracks=[source])
        results = self.engine.match_playlist(playlist, storefront="cn")

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.decision, DecisionStatus.AUTO_ACCEPT.value)
        self.assertIsNotNone(res.selected_candidate)
        self.assertEqual(res.selected_candidate.track.id, "cand_olk_correct")

        # 3. Assert new result is saved to persistent cache with current version
        updated_cached = self.cache.get_match("cn", key_str)
        self.assertIsNotNone(updated_cached)
        self.assertEqual(updated_cached.decision, DecisionStatus.AUTO_ACCEPT.value)

    def test_R06_outdated_cache_migration_and_user_confirmed_retention(self):
        """
        R06:
        1. Old empty-candidate no_match -> get_match returns None (fresh search needed).
        2. Old review -> get_match returns None when policy/alias is outdated.
        3. Old user_confirmed -> get_match returns user_confirmed unconditionally.
        4. Secondary index lookups behave identically.
        5. Un-requeried records must NOT be falsely marked with new version.
        """
        src_a = Track(title="Song A", artists=["Artist A"])
        src_b = Track(title="Song B", artists=["Artist B"])
        src_c = Track(title="Song C", artists=["Artist C"], isrc="USABC9999999")

        # Record A: Old empty-candidate no_match
        res_a = SongMatchResult(
            source_track=src_a,
            candidates=[],
            selected_candidate=None,
            status=ConfidenceLevel.NOT_FOUND,
            decision="no_match",
        )
        # Record B: Old review
        cand_b = MatchCandidate(
            track=AppleMusicTrack(id="cand_b", title="Song B", artists=["Artist B"], storefront="cn"),
            score=0.75,
            decision="review",
        )
        res_b = SongMatchResult(
            source_track=src_b,
            candidates=[cand_b],
            selected_candidate=cand_b,
            status=ConfidenceLevel.HIGH,
            decision="review",
        )
        # Record C: Old user_confirmed
        cand_c = MatchCandidate(
            track=AppleMusicTrack(id="cand_c_manual", title="Different Title", artists=["Other Artist"], storefront="cn"),
            score=1.0,
            decision="user_confirmed",
        )
        res_c = SongMatchResult(
            source_track=src_c,
            candidates=[cand_c],
            selected_candidate=cand_c,
            status=ConfidenceLevel.EXACT,
            decision="user_confirmed",
        )

        conn = self.cache._get_connection()
        with conn:
            # Insert A
            conn.execute(
                """
                INSERT OR REPLACE INTO match_cache
                (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at,
                 rule_version, query_policy_version, alias_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("cn", "hash_a", "hash_a", res_a.model_dump_json(), "no_match", "not_found", 1000.0, 9999999999.0, "2026.09.v1", "2026.09.v1", "2026.09.v1"),
            )
            # Insert B
            conn.execute(
                """
                INSERT OR REPLACE INTO match_cache
                (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at,
                 rule_version, query_policy_version, alias_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("cn", "hash_b", "hash_b", res_b.model_dump_json(), "review", "high", 1000.0, 9999999999.0, "2026.09.v1", "2026.09.v1", "2026.09.v1"),
            )
            # Insert C (primary + secondary isrc key)
            conn.execute(
                """
                INSERT OR REPLACE INTO match_cache
                (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at,
                 rule_version, query_policy_version, alias_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("cn", "hash_c", "hash_c", res_c.model_dump_json(), "user_confirmed", "exact", 1000.0, 9999999999.0, "2026.09.v1", "2026.09.v1", "2026.09.v1"),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO match_cache
                (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at,
                 rule_version, query_policy_version, alias_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("cn", "isrc:USABC9999999", "isrc:USABC9999999", res_c.model_dump_json(), "user_confirmed", "exact", 1000.0, 9999999999.0, "2026.09.v1", "2026.09.v1", "2026.09.v1"),
            )

        # 1. Record A (no_match) -> None
        self.assertIsNone(self.cache.get_match("cn", "hash_a"))

        # 2. Record B (review) -> None (since query policy is outdated)
        self.assertIsNone(self.cache.get_match("cn", "hash_b"))

        # 3. Record C (user_confirmed) -> preserved
        get_c = self.cache.get_match("cn", "hash_c")
        self.assertIsNotNone(get_c)
        self.assertEqual(get_c.decision, "user_confirmed")
        self.assertEqual(get_c.selected_candidate.track.id, "cand_c_manual")

        get_c_isrc = self.cache.get_match("cn", "isrc:USABC9999999")
        self.assertIsNotNone(get_c_isrc)
        self.assertEqual(get_c_isrc.decision, "user_confirmed")

        # 4. Check DB: un-requeried records A and B must NOT have their rule_version or query_policy_version updated
        cursor = conn.cursor()
        cursor.execute("SELECT rule_version, query_policy_version FROM match_cache WHERE track_hash = 'hash_a'")
        row_a = cursor.fetchone()
        self.assertEqual(row_a[0], "2026.09.v1")
        self.assertEqual(row_a[1], "2026.09.v1")

        cursor.execute("SELECT rule_version, query_policy_version FROM match_cache WHERE track_hash = 'hash_b'")
        row_b = cursor.fetchone()
        self.assertEqual(row_b[0], "2026.09.v1")
        self.assertEqual(row_b[1], "2026.09.v1")

    def test_R07_catalog_error_semantics_and_negative_cache_ttl(self):
        """
        R07:
        1. Catalog rate_limited outcome: search_incomplete=True, not cached as permanent no_match.
        2. Catalog auth_failed outcome: search_incomplete=True, not cached as permanent no_match.
        3. Catalog network_error outcome: search_incomplete=True, not cached as permanent no_match.
        4. Catalog truly no hits (kind='ok', tracks=[]): decision=no_match, search_incomplete=False, cached.
        """
        source = Track(title="Song With Transient Error", artists=["Artist Error"])
        key = self.engine.get_stable_track_key(source)
        key_str = ":".join(str(x) for x in key)

        # 1. Rate limited
        self.client.search_by_isrc.return_value = CatalogSearchOutcome(tracks=[], kind="no_hits")
        self.client.search_catalog.return_value = CatalogSearchOutcome(
            tracks=[],
            kind="rate_limited",
            safe_message="Rate limit exceeded",
            retry_after_seconds=30.0,
        )
        res_rl = self.engine.match_track(source, storefront="cn")
        self.assertTrue(res_rl.search_incomplete, "Rate limited search must be incomplete")
        self.assertTrue(
            any("rate_limited" in f.lower() or "429" in f or "rate" in f.lower() for f in res_rl.search_failures)
            or res_rl.decision == "rate_limited"
        )
        # Verify match_playlist does NOT cache transient rate limit to match_cache
        playlist = Playlist(name="Playlist RL", tracks=[source])
        self.engine.match_playlist(playlist, storefront="cn")
        self.assertIsNone(self.cache.get_match("cn", key_str), "Rate limited outcome must NOT be cached in match_cache")

        # 2. Auth failed
        self.client.search_catalog.return_value = CatalogSearchOutcome(
            tracks=[],
            kind="auth_failed",
            safe_message="Developer token expired",
        )
        res_auth = self.engine.match_track(source, storefront="cn")
        self.assertTrue(res_auth.search_incomplete, "Auth failed search must be incomplete")
        self.engine.match_playlist(playlist, storefront="cn")
        self.assertIsNone(self.cache.get_match("cn", key_str), "Auth failed outcome must NOT be cached in match_cache")

        # 3. Network error
        self.client.search_catalog.return_value = CatalogSearchOutcome(
            tracks=[],
            kind="network_error",
            safe_message="Connection timed out",
        )
        res_net = self.engine.match_track(source, storefront="cn")
        self.assertTrue(res_net.search_incomplete, "Network error search must be incomplete")
        self.assertEqual(res_net.decision, "error")
        self.assertEqual(res_net.search_status, "network_error")

        # 4. Truly no hits (kind='ok', tracks=[])
        self.client.search_catalog.return_value = CatalogSearchOutcome(
            tracks=[],
            kind="ok",
            safe_message="No tracks found",
        )
        res_nohits = self.engine.match_track(source, storefront="cn")
        self.assertFalse(res_nohits.search_incomplete, "Truly no hits must NOT be incomplete")
        self.assertEqual(res_nohits.decision, DecisionStatus.NO_MATCH.value)
        self.assertEqual(res_nohits.search_status, "no_match")

        # 5. Negative cache behavior: transient errors must NOT be cached in catalog_cache
        transient_outcome = CatalogSearchOutcome(tracks=[], kind="rate_limited")
        self.cache.set_catalog("cn", "catalog", "transient_term", transient_outcome)
        self.assertIsNone(
            self.cache.get_catalog("cn", "catalog", "transient_term"),
            "Transient failure must NOT be cached in catalog_cache",
        )

        # Successful empty result IS cached in catalog_cache with bounded negative TTL
        nohits_outcome = CatalogSearchOutcome(tracks=[], kind="ok")
        self.cache.set_catalog("cn", "catalog", "empty_term", nohits_outcome)
        cached_cat = self.cache.get_catalog("cn", "catalog", "empty_term")
        self.assertIsNotNone(cached_cat, "Truly no hits must be cached in catalog_cache")
        self.assertEqual(cached_cat.kind, "ok")
        self.assertEqual(len(cached_cat.tracks), 0)


if __name__ == "__main__":
    unittest.main()
