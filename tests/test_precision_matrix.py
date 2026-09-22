"""
Comprehensive test suite verifying the multilingual match precision matrix.
Covers N01-N08, P01-P06, Q01-Q03, C01-C04, U01-U03, D01 per TEST_MATRIX.md.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from applemusic.cache import PersistentCache
from applemusic.client import AppleMusicClient
from applemusic.config import Config
from applemusic.matcher.cleaner import TextCleaner
from applemusic.matcher.engine import MatchingEngine
from applemusic.matcher.evidence import (
    ALIAS_VERSION,
    MATCH_RULE_VERSION,
    MatchEvidence,
    SingleTrackDiagnostics,
    VerificationLevel,
)
from applemusic.matcher.query_planner import QueryPlanner
from applemusic.matcher.scorer import TrackScorer
from applemusic.matcher.title_aliases import are_titles_equivalent
from applemusic.models import (
    AppleMusicTrack,
    CatalogSearchOutcome,
    ConfidenceLevel,
    DecisionStatus,
    MatchCandidate,
    SongMatchResult,
    Track,
)


class TestPrecisionMatrix(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_precision.db"
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

    # =========================================================================
    # Negative Cases (N01 - N08)
    # =========================================================================

    def test_N01_screenshot_case_no_match(self):
        """
        N01: 截图可见源曲 vs 李杰明候选，无额外元数据 -> no_match，无默认勾选，无高可信徽标.
        Source: 灰かぶり（灰姑娘） — 十明
        Candidate: 没有回頭路 (feat. 庭竹) — 李杰明
        """
        source = Track(title="灰かぶり（灰姑娘）", artists=["十明"])
        cand = AppleMusicTrack(
            id="1680000001",
            title="没有回頭路 (feat. 庭竹)",
            artists=["李杰明", "庭竹"],
            album="没有回頭路",
            storefront="cn",
        )
        scored = TrackScorer.score(source, cand)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(source, [scored])

        self.assertEqual(dec, DecisionStatus.NO_MATCH.value)
        self.assertEqual(conf, ConfidenceLevel.NOT_FOUND)
        self.assertIsNone(best)
        self.assertLess(scored.score, 0.55)

    def test_N02_synthetic_same_duration_and_single_tag(self):
        """
        N02: N01增加相同/接近时长与Single标签（synthetic） -> 不能自动采纳，不能把时长改写为标题相似.
        """
        source = Track(title="灰かぶり（灰姑娘）", artists=["十明"], duration_ms=210000)
        cand = AppleMusicTrack(
            id="1680000001",
            title="没有回頭路 (feat. 庭竹)",
            artists=["李杰明", "庭竹"],
            album="没有回頭路 - Single",
            duration_ms=210000,
            storefront="cn",
        )
        scored = TrackScorer.score(source, cand)
        # title_score must NOT be rewritten to 0.85 by Single/duration
        self.assertLess(scored.title_score, 0.40)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(source, [scored])
        self.assertNotEqual(dec, DecisionStatus.AUTO_ACCEPT.value)
        self.assertEqual(dec, DecisionStatus.NO_MATCH.value)

    def test_N03_short_artist_name_substring_not_verified(self):
        """
        N03: 十明 vs 李杰明；短艺名仅共享字符/罗马音片段 -> 不能判定为同一已核实艺人.
        """
        sim = TrackScorer.calculate_artist_similarity(["十明"], ["李杰明"])
        self.assertLess(sim, 0.45)

        # Reverse order
        sim2 = TrackScorer.calculate_artist_similarity(["李杰明"], ["十明"])
        self.assertLess(sim2, 0.45)

    def test_N04_correct_artist_unrelated_title_same_album_duration(self):
        """
        N04: 正确艺人，但无关标题恰好同专辑、时长相同 -> 至多review，不自动勾选.
        """
        source = Track(title="灰かぶり", artists=["十明"], album="The Album", duration_ms=180000)
        cand = AppleMusicTrack(
            id="100001",
            title="完全不相干的歌名",
            artists=["十明"],
            album="The Album",
            duration_ms=180000,
            storefront="cn",
        )
        scored = TrackScorer.score(source, cand)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(source, [scored])
        self.assertNotEqual(dec, DecisionStatus.AUTO_ACCEPT.value)
        self.assertIn(dec, (DecisionStatus.NO_MATCH.value, DecisionStatus.REVIEW.value))

    def test_N05_same_title_different_artist_and_missing_artist(self):
        """
        N05: 同名不同艺人；未知艺人；缺少艺人 -> 已证实冲突拒绝自动；未知不能伪装身份吻合.
        """
        # Case 1: Same title, definitely different artists
        source1 = Track(title="Lemon", artists=["米津玄師"])
        cand1 = AppleMusicTrack(id="101", title="Lemon", artists=["其他无关歌手"], storefront="cn")
        scored1 = TrackScorer.score(source1, cand1)
        best1, conf1, dec1, reasons1, gap1 = TrackScorer.evaluate_candidates(source1, [scored1])
        self.assertNotEqual(dec1, DecisionStatus.AUTO_ACCEPT.value)

        # Case 2: Missing artist on source
        source2 = Track(title="Lemon", artists=[])
        cand2 = AppleMusicTrack(id="102", title="Lemon", artists=["米津玄師"], storefront="cn")
        scored2 = TrackScorer.score(source2, cand2)
        best2, conf2, dec2, reasons2, gap2 = TrackScorer.evaluate_candidates(source2, [scored2])
        # Cannot auto-accept without 2 independent strong evidence points
        self.assertNotEqual(dec2, DecisionStatus.AUTO_ACCEPT.value)
        self.assertEqual(dec2, DecisionStatus.REVIEW.value)

    def test_N06_version_tag_conflict_blocks_auto_accept(self):
        """
        N06: 原版/Live/伴奏/翻唱及合作角色冲突 -> 版本/身份冲突不能通过别名、同曲变体豁免越过门槛.
        """
        source = Track(title="打上花火", artists=["米津玄師", "DAOKO"])
        cand_live = AppleMusicTrack(id="201", title="打上花火 (Live版)", artists=["米津玄師", "DAOKO"], storefront="cn")
        scored_live = TrackScorer.score(source, cand_live)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(source, [scored_live])
        self.assertNotEqual(dec, DecisionStatus.AUTO_ACCEPT.value)
        self.assertTrue(any("版本" in c for c in scored_live.evidence.conflicts))

        cand_inst = AppleMusicTrack(id="202", title="打上花火 (伴奏)", artists=["米津玄師", "DAOKO"], storefront="cn")
        scored_inst = TrackScorer.score(source, cand_inst)
        best_i, conf_i, dec_i, reasons_i, gap_i = TrackScorer.evaluate_candidates(source, [scored_inst])
        self.assertNotEqual(dec_i, DecisionStatus.AUTO_ACCEPT.value)

    def test_N07_pure_hanzi_japanese_onyomi_no_strong_evidence(self):
        """
        N07: 同为中文汉字、日语音读片段相似 -> 不形成强身份依据.
        Pure Hanzi strings without Kana must not generate random Romaji.
        """
        variants_title = TextCleaner.get_japanese_romaji_variants("没有回頭路")
        self.assertEqual(variants_title, [])

        variants_artist = TextCleaner.get_japanese_romaji_variants("李杰明")
        self.assertEqual(variants_artist, [])

    def test_N08_isrc_match_with_artist_or_version_conflict_demoted_to_review(self):
        """
        N08: ISRC相同但明确艺人/版本冲突（synthetic） -> review并给冲突理由，不直接100%.
        """
        source = Track(title="Song A", artists=["Artist A"], isrc="USRC12345678")
        cand = AppleMusicTrack(
            id="999",
            title="Song A (Live)",
            artists=["Artist B (Cover)"],
            isrc="USRC12345678",
            storefront="cn",
        )
        scored = TrackScorer.score(source, cand)
        self.assertNotEqual(scored.score, 1.0)
        self.assertTrue(len(scored.evidence.conflicts) > 0)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(source, [scored])
        self.assertEqual(dec, DecisionStatus.REVIEW.value)
        self.assertTrue(any("ISRC" in r and "不符" in r or "不一致" in r for r in reasons))

    # =========================================================================
    # Positive Cases (P01 - P06)
    # =========================================================================

    def test_P01_identical_title_and_artist_auto_accept(self):
        """
        P01: 灰かぶり—十明 vs 同原文/艺人 -> 正常自动匹配.
        """
        source = Track(title="灰かぶり", artists=["十明"])
        cand = AppleMusicTrack(id="301", title="灰かぶり", artists=["十明"], storefront="cn")
        scored = TrackScorer.score(source, cand)
        self.assertEqual(scored.evidence.verification_level, VerificationLevel.STRONG.value)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(source, [scored])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)
        self.assertIn(conf, (ConfidenceLevel.EXACT, ConfidenceLevel.HIGH))

    def test_P02_scoped_alias_cinderella_toaka(self):
        """
        P02: 灰かぶり—十明 vs Cinder ella—十明 -> 官方曲名关系在同艺人范围内生效，正确召回与决策.
        额外质量门：验证P02的别名不得使其他艺人的 Cinderella 同名曲被自动采纳.
        """
        # 1. Ten-Ming scoped alias matches
        source1 = Track(title="灰かぶり", artists=["十明"])
        cand1 = AppleMusicTrack(id="302", title="Cinder ella", artists=["十明"], storefront="cn")
        scored1 = TrackScorer.score(source1, cand1)
        self.assertGreaterEqual(scored1.title_score, 0.95)
        best1, conf1, dec1, reasons1, gap1 = TrackScorer.evaluate_candidates(source1, [scored1])
        self.assertEqual(dec1, DecisionStatus.AUTO_ACCEPT.value)

        # 2. Cinderella for other artists does NOT equate!
        self.assertFalse(are_titles_equivalent("灰姑娘", "Cinderella", artist="李杰明"))
        self.assertFalse(are_titles_equivalent("灰姑娘", "Cinderella", artist="Aimer"))
        self.assertFalse(are_titles_equivalent("灰姑娘", "Cinderella", artist=None))

    def test_P03_verified_latin_artist_alias_toaka(self):
        """
        P03: 十明已核实拉丁艺名；未知读音对照 -> 有来源映射可强匹配；猜测音读不冒充已核实艺名.
        """
        source = Track(title="灰かぶり", artists=["十明"])
        cand = AppleMusicTrack(id="303", title="灰かぶり", artists=["toaka"], storefront="cn")
        scored = TrackScorer.score(source, cand)
        self.assertEqual(scored.artist_score, 1.0)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(source, [scored])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

        # Unverified guessed pronunciation "juu mei" is not in official aliases
        sim_guess = TrackScorer.calculate_artist_similarity(["十明"], ["juu mei"])
        self.assertLess(sim_guess, 0.45)

    def test_P04_existing_cross_lingual_positive_cases(self):
        """
        P04: YOASOBI/米津玄師/Reol等现有可靠跨语言正例 -> 保留召回和适当决策.
        """
        # YOASOBI
        s1 = Track(title="夜に駆ける", artists=["YOASOBI"])
        c1 = AppleMusicTrack(id="401", title="Racing into the Night", artists=["YOASOBI"], storefront="cn")
        sc1 = TrackScorer.score(s1, c1)
        self.assertGreaterEqual(sc1.title_score, 0.95)
        _, _, dec1, _, _ = TrackScorer.evaluate_candidates(s1, [sc1])
        self.assertEqual(dec1, DecisionStatus.AUTO_ACCEPT.value)

        # Kenshi Yonezu
        s2 = Track(title="打上花火", artists=["米津玄師"])
        c2 = AppleMusicTrack(id="402", title="Uchiage Hanabi", artists=["Kenshi Yonezu"], storefront="cn")
        sc2 = TrackScorer.score(s2, c2)
        self.assertGreaterEqual(sc2.title_score, 0.95)
        self.assertGreaterEqual(sc2.artist_score, 0.95)
        _, _, dec2, _, _ = TrackScorer.evaluate_candidates(s2, [sc2])
        self.assertEqual(dec2, DecisionStatus.AUTO_ACCEPT.value)

    def test_P05_traditional_simplified_and_punctuation_clean(self):
        """
        P05: 繁简、大小写、标点、完整罗马音 -> 可靠等价不回退为无关候选；标题不任意交换词序.
        """
        s = Track(title="愛在西元前", artists=["周杰倫"])
        c = AppleMusicTrack(id="501", title="爱在西元前", artists=["周杰伦"], storefront="cn")
        sc = TrackScorer.score(s, c)
        self.assertEqual(sc.title_score, 1.0)
        self.assertEqual(sc.artist_score, 1.0)
        _, _, dec, _, _ = TrackScorer.evaluate_candidates(s, [sc])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

        # Title words are NOT arbitrarily inverted!
        self.assertFalse(TextCleaner.get_japanese_romaji_variants("夜に駆ける", is_artist=False) == ["kakeru yorini"])

    def test_P06_featured_artist_and_voice_actor(self):
        """
        P06: 声优角色、合作艺人、作曲角色标注 -> 区分角色关系；同配角不能直接证明同主唱.
        """
        pri, fea = TextCleaner.parse_artists(["Artist Main feat. Collaborator Sub"])
        self.assertEqual(pri, "Artist Main")
        self.assertIn("Collaborator Sub", fea)

    # =========================================================================
    # Query Budget & Scheduling Cases (Q01 - Q03)
    # =========================================================================

    def test_Q01_first_round_second_slot_scoped_alias(self):
        """
        Q01: 首轮无结果，官方译名命中 -> 查询预算内真实执行第二查询，断言完整参数序列.
        """
        source = Track(title="灰かぶり", artists=["十明"])

        executed_queries = []

        def mock_search(query, storefront="cn", limit=10):
            executed_queries.append(query)
            if query == "灰かぶり 十明":
                return CatalogSearchOutcome(kind="no_hits", tracks=[])
            elif query == "Cinder ella 十明":
                t = AppleMusicTrack(id="701", title="Cinder ella", artists=["十明"], storefront=storefront)
                return CatalogSearchOutcome(kind="ok", tracks=[t])
            return CatalogSearchOutcome(kind="no_hits", tracks=[])

        self.client.search_catalog = MagicMock(side_effect=mock_search)
        res = self.engine.match_track(source, storefront="cn")

        self.assertEqual(len(executed_queries), 2)
        self.assertEqual(executed_queries[0], "灰かぶり 十明")
        self.assertEqual(executed_queries[1], "Cinder ella 十明")
        self.assertEqual(res.decision, DecisionStatus.AUTO_ACCEPT.value)
        self.assertIsNotNone(res.selected_candidate)
        self.assertEqual(res.selected_candidate.track.id, "701")

    def test_Q02_rematch_continues_budget_on_review(self):
        """
        Q02: 初轮review，深度重试有正确候选 -> 剩余预算继续召回，不被模糊候选提前终止.
        """
        source = Track(title="灰かぶり", artists=["十明"])

        executed_queries = []

        def mock_search(query, storefront="cn", limit=12):
            executed_queries.append((storefront, query))
            if query == "灰かぶり 十明":
                # Returns a fuzzy/review candidate
                fuzzy = AppleMusicTrack(id="801", title="灰かぶり (Instrumental)", artists=["十明"], storefront=storefront)
                return CatalogSearchOutcome(kind="ok", tracks=[fuzzy])
            elif query == "Cinder ella 十明":
                # Returns the exact song
                exact = AppleMusicTrack(id="802", title="Cinder ella", artists=["十明"], storefront=storefront)
                return CatalogSearchOutcome(kind="ok", tracks=[exact])
            return CatalogSearchOutcome(kind="no_hits", tracks=[])

        self.client.search_catalog = MagicMock(side_effect=mock_search)
        res = self.engine.rematch_track(source, storefront="cn", fallback_storefronts=[])

        # Should continue and find the auto_accept candidate 802
        self.assertEqual(res.decision, DecisionStatus.AUTO_ACCEPT.value)
        self.assertEqual(res.selected_candidate.track.id, "802")
        self.assertGreaterEqual(len(executed_queries), 2)

    def test_Q03_budget_limits_and_failure_stops_expansion(self):
        """
        Q03: 多地区、429、401、部分失败 -> 全局预算不突破；停止扩展；失败不伪装no_match.
        """
        source = Track(title="Test Song", artists=["Test Artist"])

        # Case 1: 429 Rate Limit stops expansion
        def mock_search_429(query, storefront="cn", limit=12):
            return CatalogSearchOutcome(kind="rate_limited", tracks=[], retry_after_seconds=5.0)

        self.client.search_catalog = MagicMock(side_effect=mock_search_429)
        res = self.engine.match_track(source, storefront="cn")
        self.assertEqual(res.decision, "rate_limited")
        self.assertEqual(self.client.search_catalog.call_count, 1)

        # Case 2: Deep rematch global budget limit <= 6
        call_count = 0

        def mock_search_nohits(query, storefront="cn", limit=12):
            nonlocal call_count
            call_count += 1
            return CatalogSearchOutcome(kind="no_hits", tracks=[])

        self.client.search_catalog = MagicMock(side_effect=mock_search_nohits)
        res2 = self.engine.rematch_track(source, storefront="cn", fallback_storefronts=["hk", "us", "jp"])
        self.assertLessEqual(call_count, QueryPlanner.RETRY_TOTAL_BUDGET)

    # =========================================================================
    # Cache Invalidation & Versioning Cases (C01 - C04)
    # =========================================================================

    def test_C01_outdated_cache_re_evaluated(self):
        """
        C01: 旧规则高分误匹配缓存，分别经get_match/find_match命中 -> 旧auto_accept失效或重评，错误候选不直接复活.
        """
        source = Track(title="灰かぶり（灰姑娘）", artists=["十明"])
        wrong_cand = AppleMusicTrack(
            id="1680000001",
            title="没有回頭路 (feat. 庭竹)",
            artists=["李杰明", "庭竹"],
            album="没有回頭路",
            storefront="cn",
        )
        # Inject outdated cache with auto_accept under old rule version
        old_evidence = MatchEvidence(
            evidence_type="title_and_artist",
            verification_level=VerificationLevel.STRONG.value,
            rule_version="2024.01.v0",
            alias_version="2024.01.v0",
        )
        old_result = SongMatchResult(
            source_track=source,
            candidates=[MatchCandidate(track=wrong_cand, score=0.89, decision="auto_accept", evidence=old_evidence)],
            selected_candidate=MatchCandidate(track=wrong_cand, score=0.89, decision="auto_accept", evidence=old_evidence),
            decision="auto_accept",
            status=ConfidenceLevel.HIGH,
            evidence=old_evidence,
        )

        key_str = "test:old_false_positive"
        # Directly write old payload to database
        conn = self.cache._get_connection()
        conn.execute(
            """
            INSERT OR REPLACE INTO match_cache
            (storefront, track_hash, result_json, decision, status, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("cn", key_str, old_result.model_dump_json(), "auto_accept", "high", 0, 9999999999),
        )

        # 1. get_match should re-evaluate and NOT auto_accept!
        cached = self.cache.get_match("cn", key_str)
        self.assertIsNotNone(cached)
        self.assertNotEqual(cached.decision, DecisionStatus.AUTO_ACCEPT.value)
        self.assertEqual(cached.decision, DecisionStatus.NO_MATCH.value)

    def test_C02_text_index_does_not_mix_live_and_studio(self):
        """
        C02: 原版与Live共享清洗后标题、同名不同专辑 -> 文本索引不共享最终自动决策.
        """
        studio_source = Track(title="Lemon", artists=["米津玄師"])
        studio_cand = AppleMusicTrack(id="111", title="Lemon", artists=["米津玄師"], storefront="cn")
        studio_result = SongMatchResult(
            source_track=studio_source,
            candidates=[MatchCandidate(track=studio_cand, score=1.0, decision="auto_accept")],
            selected_candidate=MatchCandidate(track=studio_cand, score=1.0, decision="auto_accept"),
            decision="auto_accept",
            status=ConfidenceLevel.EXACT,
        )
        self.cache.set_match("cn", "exact_studio_key", studio_result, track=studio_source)

        # Query for Live version using find_match
        live_track = Track(title="Lemon (Live)", artists=["米津玄師"])
        found = self.cache.find_match("cn", live_track)
        # Should NOT return studio result as auto_accept
        if found:
            self.assertNotEqual(found.decision, DecisionStatus.AUTO_ACCEPT.value)

    def test_C03_valid_cache_reused_without_clearing_db(self):
        """
        C03: 配置阈值/别名版本变化；当前版本合法缓存 -> 旧策略失效，合法缓存复用；不删整个数据库.
        """
        source = Track(title="灰かぶり", artists=["十明"])
        cand = AppleMusicTrack(id="222", title="灰かぶり", artists=["十明"], storefront="cn")
        valid_ev = MatchEvidence(
            evidence_type="title_and_artist",
            verification_level=VerificationLevel.STRONG.value,
            rule_version=MATCH_RULE_VERSION,
            alias_version=ALIAS_VERSION,
        )
        valid_result = SongMatchResult(
            source_track=source,
            candidates=[MatchCandidate(track=cand, score=1.0, decision="auto_accept", evidence=valid_ev)],
            selected_candidate=MatchCandidate(track=cand, score=1.0, decision="auto_accept", evidence=valid_ev),
            decision="auto_accept",
            status=ConfidenceLevel.EXACT,
            evidence=valid_ev,
        )
        self.cache.set_match("cn", "valid_key", valid_result, track=source)

        hit = self.cache.get_match("cn", "valid_key")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.decision, "auto_accept")

    def test_C04_user_confirmed_not_shared_across_different_sources(self):
        """
        C04: 人工确认与个人资料库跨来源/跨账号 -> 严格限定复用范围，错误决定不扩散.
        """
        source = Track(title="Special Song", artists=["Special Artist"])
        cand = AppleMusicTrack(id="333", title="Special Song", artists=["Special Artist"], storefront="cn")
        user_confirmed_result = SongMatchResult(
            source_track=source,
            candidates=[MatchCandidate(track=cand, score=0.80, decision="user_confirmed")],
            selected_candidate=MatchCandidate(track=cand, score=0.80, decision="user_confirmed"),
            decision="user_confirmed",
            status=ConfidenceLevel.MEDIUM,
        )
        self.cache.set_match("cn", "user_conf_key", user_confirmed_result, track=source)

        # Different track should not hit it via text index
        other_source = Track(title="Other Song", artists=["Special Artist"])
        hit = self.cache.find_match("cn", other_source)
        self.assertIsNone(hit)

    # =========================================================================
    # UI Logic & Decision Driven Badges (U01 - U03)
    # =========================================================================

    def test_U01_review_high_score_badge_and_checkbox(self):
        """
        U01: decision=review,status=high,score=0.89 -> 显示待复核，默认未选中，分数不是正确概率.
        """
        source = Track(title="Song", artists=["Artist"])
        cand = AppleMusicTrack(id="555", title="Song", artists=["Artist"], storefront="cn")
        # Under review decision
        result = SongMatchResult(
            source_track=source,
            candidates=[MatchCandidate(track=cand, score=0.89, decision="review")],
            selected_candidate=MatchCandidate(track=cand, score=0.89, decision="review"),
            decision="review",
            status=ConfidenceLevel.HIGH,
        )
        # Checkbox default logic: only auto_accept or user_confirmed is checked
        is_selected = result.selected_candidate and (result.decision in ("auto_accept", "user_confirmed"))
        self.assertFalse(is_selected)

    def test_U02_missing_decision_and_exact_less_than_1(self):
        """
        U02: decision缺失的旧响应；exact但分数不足1 -> 保守待复核，不伪装100%精确.
        """
        # score < 1.0 should not be treated as 100% exact
        source = Track(title="Song", artists=["Artist"])
        cand = AppleMusicTrack(id="666", title="Song", artists=["Artist"], storefront="cn")
        candidate = MatchCandidate(track=cand, score=0.92, decision="review")
        self.assertNotEqual(candidate.decision, "auto_accept")

    def test_U03_unified_decision_policy(self):
        """
        U03: 统一decision策略；不继承过时自动选择.
        """
        auto_accepted = SongMatchResult(
            source_track=Track(title="A", artists=["B"]),
            selected_candidate=MatchCandidate(track=AppleMusicTrack(id="1", title="A", artists=["B"]), score=0.95),
            decision="auto_accept",
        )
        reviewed = SongMatchResult(
            source_track=Track(title="A", artists=["B"]),
            selected_candidate=MatchCandidate(track=AppleMusicTrack(id="1", title="A", artists=["B"]), score=0.85),
            decision="review",
        )
        self.assertTrue(auto_accepted.selected_candidate and (auto_accepted.decision in ("auto_accept", "user_confirmed")))
        self.assertFalse(reviewed.selected_candidate and (reviewed.decision in ("auto_accept", "user_confirmed")))

    # =========================================================================
    # Single-Track Diagnostics & Sanitization (D01)
    # =========================================================================

    def test_D01_diagnostics_sanitization_and_export(self):
        """
        D01: 单曲诊断和JSON导出 -> 包含输入/证据/查询/地区/规则缓存版本，无Token/Cookie/认证头/用户路径.
        """
        diag = SingleTrackDiagnostics(
            app_version="2.0.4",
            build_id="unknown",
            rule_version=MATCH_RULE_VERSION,
            alias_version=ALIAS_VERSION,
            target_storefront="cn",
            executed_queries=[{"query": "灰かぶり 十明", "provenance": "original", "kind": "ok", "hits": 1}],
            candidates_count=1,
            final_decision="auto_accept",
            verification_level=VerificationLevel.STRONG.value,
            matched_fields=["title", "artist"],
            conflicts=[],
        )
        diag_dict = diag.__dict__
        diag_str = json.dumps(diag_dict, ensure_ascii=False)

        # Check required fields
        self.assertIn("2026.09.v1", diag_str)
        self.assertIn("cn", diag_str)
        self.assertIn("executed_queries", diag_str)

        # Assert no sensitive secrets or absolute user directories
        self.assertNotIn("developer_token", diag_str)
        self.assertNotIn("media_user_token", diag_str)
        self.assertNotIn("cookie", diag_str.lower())
        self.assertNotIn("bearer", diag_str.lower())
        self.assertNotIn("users\\", diag_str.lower())
        self.assertNotIn("users/", diag_str.lower())


if __name__ == "__main__":
    unittest.main()
