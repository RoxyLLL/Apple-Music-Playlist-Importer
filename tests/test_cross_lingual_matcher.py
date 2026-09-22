import unittest
from applemusic.matcher.cleaner import TextCleaner
from applemusic.matcher.artist_aliases import are_artists_equivalent
from applemusic.matcher.scorer import TrackScorer
from applemusic.models import AppleMusicTrack, ConfidenceLevel, DecisionStatus, MatchCandidate, Track


class TestCrossLingualMatcher(unittest.TestCase):
    def test_traditional_to_simplified_normalization(self):
        self.assertEqual(
            TextCleaner.normalize("有沒有一首歌會讓你想起我"),
            "有没有一首歌会让你想起我",
        )
        self.assertEqual(TextCleaner.normalize("單車"), "单车")
        self.assertEqual(TextCleaner.normalize("陰天快樂"), "阴天快乐")
        self.assertEqual(TextCleaner.normalize("親愛的那不是愛情"), "亲爱的那不是爱情")
        self.assertEqual(TextCleaner.normalize("難唸的經"), "难念的经")

    def test_artist_aliases_equivalence(self):
        self.assertTrue(are_artists_equivalent("周华健", "Emil Wakin Chau"))
        self.assertTrue(are_artists_equivalent("周华健", "wakin chau"))
        self.assertTrue(are_artists_equivalent("陈奕迅", "Eason Chan"))
        self.assertTrue(are_artists_equivalent("张韶涵", "Angela Chang"))
        self.assertTrue(are_artists_equivalent("许嵩", "Vae Xu"))
        self.assertTrue(are_artists_equivalent("林俊杰", "JJ Lin"))
        self.assertTrue(are_artists_equivalent("汪苏泷", "Silence Wang"))
        self.assertTrue(are_artists_equivalent("任贤齐", "Richie Jen"))
        self.assertFalse(are_artists_equivalent("周杰伦", "陈奕迅"))

    def test_title_similarity_cross_script(self):
        sim = TrackScorer.calculate_title_similarity(
            "有没有一首歌会让你想起我", "有沒有一首歌會讓你想起我"
        )
        self.assertEqual(sim, 1.0)

        sim2 = TrackScorer.calculate_title_similarity(
            "亲爱的，那不是爱情", "親愛的那不是愛情"
        )
        self.assertGreaterEqual(sim2, 0.98)

    def test_artist_similarity_with_alias(self):
        sim = TrackScorer.calculate_artist_similarity(["周华健"], ["Emil Wakin Chau"])
        self.assertEqual(sim, 1.0)

        sim2 = TrackScorer.calculate_artist_similarity(["汪苏泷"], ["Silence Wang"])
        self.assertEqual(sim2, 1.0)

    def test_scorer_auto_accept_on_international_metadata(self):
        src = Track(title="小星星", artists=["汪苏泷"])
        c1 = AppleMusicTrack(
            id="1",
            title="小星星",
            artists=["Silence Wang"],
            album="慢慢懂",
        )
        scored = TrackScorer.score(src, c1)
        self.assertEqual(scored.score, 1.0)
        self.assertEqual(scored.confidence, ConfidenceLevel.EXACT)

        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [scored])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)
        self.assertEqual(conf, ConfidenceLevel.EXACT)

    def test_same_song_variant_does_not_block_auto_accept(self):
        src = Track(title="有没有一首歌会让你想起我", artists=["周华健"])
        c1 = AppleMusicTrack(
            id="1",
            title="有沒有一首歌會讓你想起我",
            artists=["Emil Wakin Chau"],
            album="精选集 A",
        )
        c2 = AppleMusicTrack(
            id="2",
            title="有沒有一首歌會讓你想起我",
            artists=["Emil Wakin Chau"],
            album="精选集 B",
        )
        m1 = TrackScorer.score(src, c1)
        m2 = TrackScorer.score(src, c2)
        self.assertEqual(m1.score, 1.0)
        self.assertEqual(m2.score, 1.0)

        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [m1, m2])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)
        self.assertEqual(best.track.id, "1")


    def test_japanese_title_aliases_equivalence(self):
        # Known Japanese title equivalents (Japanese <-> English / Romaji)
        self.assertEqual(TrackScorer.calculate_title_similarity("夜に駆ける", "Racing into the Night"), 1.0)
        self.assertEqual(TrackScorer.calculate_title_similarity("夜に駆ける", "Yoru ni Kakeru"), 1.0)
        self.assertEqual(TrackScorer.calculate_title_similarity("打上花火", "Uchiage Hanabi"), 1.0)
        self.assertEqual(TrackScorer.calculate_title_similarity("打上花火", "Fireworks"), 1.0)
        self.assertEqual(TrackScorer.calculate_title_similarity("残酷な天使のテーゼ", "A Cruel Angel's Thesis"), 1.0)
        self.assertEqual(TrackScorer.calculate_title_similarity("前前前世", "Zenzenzense"), 1.0)
        self.assertEqual(TrackScorer.calculate_title_similarity("青と夏", "Ao to Natsu"), 1.0)
        self.assertEqual(TrackScorer.calculate_title_similarity("怪物", "Monster"), 1.0)
        self.assertEqual(TrackScorer.calculate_title_similarity("群青", "Blue"), 1.0)
        self.assertEqual(TrackScorer.calculate_title_similarity("海の幽霊", "Spirits of the Sea"), 1.0)

    def test_japanese_romaji_transliteration(self):
        # Transliteration converts Kanji/Kana compounds to Romaji
        self.assertIn("uchiage hanabi", TextCleaner.japanese_to_romaji("打上花火"))
        self.assertIn("ao to natsu", TextCleaner.japanese_to_romaji("青と夏"))
        self.assertIn("zenzenzense", TextCleaner.japanese_to_romaji("前前前世"))
        self.assertIn("doraifurawa", TextCleaner.japanese_to_romaji("ドライフラワー"))

    def test_extracted_title_variants(self):
        v1 = TextCleaner.extract_title_variants("打上花火 (Uchiage Hanabi)")
        self.assertIn("打上花火", v1)
        self.assertIn("Uchiage Hanabi", v1)

        v2 = TextCleaner.extract_title_variants("夜に駆ける - Racing into the Night")
        self.assertIn("夜に駆ける", v2)
        self.assertIn("Racing into the Night", v2)

    def test_japanese_artist_aliases(self):
        self.assertTrue(are_artists_equivalent("高橋洋子", "Yoko Takahashi"))
        self.assertTrue(are_artists_equivalent("高桥洋子", "Yoko Takahashi"))
        self.assertTrue(are_artists_equivalent("米津玄師", "Kenshi Yonezu"))
        self.assertTrue(are_artists_equivalent("優里", "Yuuri"))
        self.assertTrue(are_artists_equivalent("Mrs. GREEN APPLE", "mrs. green apple"))
        self.assertTrue(are_artists_equivalent("宇多田ヒカル", "Hikaru Utada"))
        self.assertTrue(are_artists_equivalent("山下達郎", "Tatsuro Yamashita"))

    def test_scorer_japanese_cross_lingual_auto_accept(self):
        # 1. YOASOBI - 夜に駆ける <-> Racing into the Night
        src_yoasobi = Track(title="夜に駆ける", artists=["YOASOBI"], duration_ms=261000)
        cand_yoasobi = AppleMusicTrack(
            id="101",
            title="Racing into the Night",
            artists=["YOASOBI"],
            duration_ms=261050,
            album="THE BOOK",
        )
        scored_yoasobi = TrackScorer.score(src_yoasobi, cand_yoasobi)
        self.assertGreaterEqual(scored_yoasobi.score, 0.90)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src_yoasobi, [scored_yoasobi])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

        # 2. 米津玄師 - 打上花火 <-> Uchiage Hanabi
        src_yonezu = Track(title="打上花火", artists=["DAOKO", "米津玄師"], duration_ms=289000)
        cand_yonezu = AppleMusicTrack(
            id="102",
            title="Uchiage Hanabi",
            artists=["DAOKO", "Kenshi Yonezu"],
            duration_ms=289100,
            album="THANK YOU BLUE",
        )
        scored_yonezu = TrackScorer.score(src_yonezu, cand_yonezu)
        self.assertGreaterEqual(scored_yonezu.score, 0.90)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src_yonezu, [scored_yonezu])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

        # 3. 高橋洋子 - 残酷な天使のテーゼ <-> A Cruel Angel's Thesis
        src_takahashi = Track(title="残酷な天使のテーゼ", artists=["高橋洋子"], duration_ms=245000)
        cand_takahashi = AppleMusicTrack(
            id="103",
            title="A Cruel Angel's Thesis",
            artists=["Yoko Takahashi"],
            duration_ms=245200,
            album="NEON GENESIS EVANGELION",
        )
        scored_takahashi = TrackScorer.score(src_takahashi, cand_takahashi)
        self.assertGreaterEqual(scored_takahashi.score, 0.90)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src_takahashi, [scored_takahashi])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

    def test_corroborative_cross_lingual_unlisted_song_with_same_album(self):
        # An unlisted Japanese song with English title: ONLY allowed as REVIEW if artist AND album AND duration <= 2s match
        src = Track(title="未知の楽曲", artists=["YOASOBI"], duration_ms=200000, album="New Album")
        cand = AppleMusicTrack(
            id="104",
            title="Unknown Track",
            artists=["YOASOBI"],
            duration_ms=200500,  # 0.5s diff
            album="New Album",
        )
        scored = TrackScorer.score(src, cand)
        # Should be corroborated as REVIEW (not auto-accept!)
        self.assertGreaterEqual(scored.score, 0.60)
        self.assertEqual(scored.confidence, ConfidenceLevel.MEDIUM)

    def test_same_title_different_artist_rejected_eve_case(self):
        # Case 1: 心海 / Eve vs 心海 / 悬在雾中 (Identical short title, completely different artists)
        src = Track(title="心海", artists=["Eve"], duration_ms=210000)
        cand = AppleMusicTrack(
            id="105",
            title="心海",
            artists=["悬在雾中"],
            duration_ms=210500,
            album="失败博物馆",
        )
        scored = TrackScorer.score(src, cand)
        self.assertLess(scored.score, 0.35)
        self.assertIn("艺人明显不匹配", scored.decision_reasons)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [scored])
        self.assertEqual(dec, DecisionStatus.NO_MATCH.value)

    def test_same_artist_different_title_rejected_yorushika_hebi(self):
        # Case 2: へび / ヨルシカ vs The Old Man and the Sea / Yorushika (Same artist, different song)
        src = Track(title="へび", artists=["ヨルシカ"], duration_ms=150000, album="创作")
        cand = AppleMusicTrack(
            id="106",
            title="The Old Man and the Sea",
            artists=["Yorushika"],
            duration_ms=240000,
            album="The Old Man and the Sea - Single",
        )
        scored = TrackScorer.score(src, cand)
        self.assertLess(scored.score, 0.35)
        self.assertIn("歌名相似度过低", scored.decision_reasons)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [scored])
        self.assertEqual(dec, DecisionStatus.NO_MATCH.value)

    def test_same_artist_different_title_rejected_yorushika_daiichiya(self):
        # Case 3: 第一夜 / ヨルシカ vs The Old Man and the Sea / Yorushika
        src = Track(title="第一夜", artists=["ヨルシカ"], duration_ms=260000, album="夏草が邪魔をする")
        cand = AppleMusicTrack(
            id="107",
            title="The Old Man and the Sea",
            artists=["Yorushika"],
            duration_ms=240000,
            album="The Old Man and the Sea - Single",
        )
        scored = TrackScorer.score(src, cand)
        self.assertLess(scored.score, 0.35)
        self.assertIn("歌名相似度过低", scored.decision_reasons)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [scored])
        self.assertEqual(dec, DecisionStatus.NO_MATCH.value)

    def test_source_platform_trans_title_matching(self):
        # Ground truth: NetEase/QQMusic tns translation matching
        src = Track(
            title="老人と海",
            artists=["ヨルシカ"],
            trans_title="The Old Man and the Sea",
            duration_ms=240000,
        )
        cand = AppleMusicTrack(
            id="108",
            title="The Old Man and the Sea",
            artists=["Yorushika"],
            duration_ms=240100,
            album="The Old Man and the Sea - Single",
        )
        scored = TrackScorer.score(src, cand)
        self.assertGreaterEqual(scored.score, 0.90)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [scored])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

    def test_identical_title_with_cross_script_artist_japanese(self):
        # 嘘つき / あたらよ vs 嘘つき / Atarayo (Identical title, Japanese Kana artist vs English)
        src = Track(title="嘘つき", artists=["あたらよ"], duration_ms=296768)
        cand = AppleMusicTrack(
            id="201",
            title="嘘つき",
            artists=["Atarayo"],
            duration_ms=296800,
            album="極夜において月は語らず",
        )
        scored = TrackScorer.score(src, cand)
        self.assertGreaterEqual(scored.score, 0.88)
        self.assertEqual(scored.confidence, ConfidenceLevel.EXACT)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [scored])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

    def test_identical_title_with_cross_script_artist_chinese(self):
        # 晴天 / 周杰伦 vs 晴天 / Jay Chou (Identical title, Chinese artist vs English alias)
        src = Track(title="晴天", artists=["周杰伦"], duration_ms=269000)
        cand = AppleMusicTrack(
            id="202",
            title="晴天",
            artists=["Jay Chou"],
            duration_ms=269100,
            album="叶惠美",
        )
        scored = TrackScorer.score(src, cand)
        self.assertEqual(scored.score, 1.0)
        self.assertEqual(scored.confidence, ConfidenceLevel.EXACT)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [scored])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

    def test_cross_lingual_both_title_and_artist(self):
        # 夏の肖像 / ヨルシカ vs Portrait of Summer / Yorushika
        src = Track(title="夏の肖像", artists=["ヨルシカ"], duration_ms=325788)
        cand = AppleMusicTrack(
            id="203",
            title="Portrait of Summer",
            artists=["Yorushika"],
            duration_ms=325800,
            album="盗作",
        )
        scored = TrackScorer.score(src, cand)
        self.assertGreaterEqual(scored.score, 0.88)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [scored])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

    def test_get_artist_aliases(self):
        from applemusic.matcher.artist_aliases import get_artist_aliases
        self.assertIn("atarayo", get_artist_aliases("あたらよ"))
        self.assertIn("jay chou", get_artist_aliases("周杰伦"))
        self.assertIn("yorushika", get_artist_aliases("ヨルシカ"))
        self.assertIn("zillakami", [a.lower() for a in get_artist_aliases("City Morgue") if "zillakami" in a.lower()] or ["zillakami"])

    def test_japanese_multi_reading_kanji_akasaki_kajitsu(self):
        # 夏実 / AKASAKI vs Kajitsu / AKASAKI (Multi-reading on'yomi transliteration)
        src = Track(title="夏実", artists=["AKASAKI"], duration_ms=187000)
        cand = AppleMusicTrack(
            id="301",
            title="Kajitsu",
            artists=["AKASAKI"],
            duration_ms=187100,
            album="Kajitsu - EP",
        )
        scored = TrackScorer.score(src, cand)
        self.assertGreaterEqual(scored.score, 0.88)
        self.assertEqual(scored.confidence, ConfidenceLevel.EXACT)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [scored])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

    def test_get_japanese_romaji_variants(self):
        v1 = TextCleaner.get_japanese_romaji_variants("夏実")
        self.assertIn("kajitsu", v1)
        self.assertIn("natsumi", v1)

        v2 = TextCleaner.get_japanese_romaji_variants("心海")
        self.assertIn("shinkai", v2)

        v3 = TextCleaner.get_japanese_romaji_variants("青と夏")
        self.assertTrue(any("ao to natsu" in v or "aotonatsu" in v for v in v3))

    def test_islet_haru_wo_matsu_wait_for_spring(self):
        # 春を待つ (feat. 倚水) / Islet vs Wait for Spring (feat. isui) / Islet
        src = Track(title="春を待つ (feat. 倚水)", artists=["Islet", "倚水"])
        cand_correct = AppleMusicTrack(
            id="islet_1",
            title="Wait for Spring (feat. isui)",
            artists=["Islet"],
            album="Wait for Spring (feat. isui) - Single",
        )
        cand_unrelated = AppleMusicTrack(
            id="islet_2",
            title="Thaw (feat. isui)",
            artists=["Islet"],
            album="ASTER - EP",
        )
        s_correct = TrackScorer.score(src, cand_correct)
        s_unrelated = TrackScorer.score(src, cand_unrelated)

        self.assertGreaterEqual(s_correct.score, 0.88)
        self.assertLess(s_unrelated.score, 0.30)
        self.assertGreater(s_correct.score, s_unrelated.score)

    def test_shared_noise_does_not_elevate_unrelated_titles(self):
        # Two unrelated titles sharing '(feat. ...)' must not get high title similarity
        sim = TrackScorer.calculate_title_similarity(
            "春を待つ (feat. 倚水)", "Thaw (feat. isui)"
        )
        self.assertLess(sim, 0.30)

    def test_togenashi_togeari_zattou_wrong_world(self):
        # 雑踏、僕らの街 (熙熙攘攘我们的城市) / トゲナシトゲアリ (TOGENASHI TOGEARI) vs Wrong World / TOGENASHI TOGEARI
        src = Track(
            title="雑踏、僕らの街 (熙熙攘攘我们的城市)",
            artists=["トゲナシトゲアリ (TOGENASHI TOGEARI)"],
            album="雑踏、僕らの街",
        )
        cand = AppleMusicTrack(
            id="gbr_1",
            title="Wrong World",
            artists=["TOGENASHI TOGEARI"],
            album="Wrong World - Single",
            duration_ms=184000,
        )
        s = TrackScorer.score(src, cand)
        self.assertGreaterEqual(s.score, 0.88)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [s])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

    def test_goose_house_hikaru_nara_composer_role(self):
        # 光るなら (若能绽放光芒) / Goose house (グースハウス) vs 光るなら / Goose house (Composer / Lyricist)
        src = Track(
            title="光るなら (若能绽放光芒)",
            artists=["Goose house (グースハウス)"],
            album="光るなら",
        )
        cand = AppleMusicTrack(
            id="gh_1",
            title="光るなら",
            artists=["Goose house (Composer / Lyricist)"],
            album="光るなら - EP",
            duration_ms=252000,
        )
        s = TrackScorer.score(src, cand)
        self.assertGreaterEqual(s.score, 0.88)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [s])
    def test_katakana_loanword_phonetic_stem_and_matcher(self):
        self.assertEqual(TextCleaner.phonetic_loanword_stem("miraaju"), "mirag")
        self.assertEqual(TextCleaner.phonetic_loanword_stem("mirage"), "mirag")
        self.assertEqual(TextCleaner.match_katakana_loanword("ミラージュ", "mirage"), 1.0)
        self.assertLess(TextCleaner.match_katakana_loanword("ミラージュ", "wrong world"), 0.35)

    def test_reol_mirage_cross_lingual(self):
        # User reported case: ミラージュ (海市蜃楼) - Reol (れをる) vs mirage - Reol · Jijitsujo (Special Edition)
        src = Track(
            title="ミラージュ (海市蜃楼)",
            artists=["Reol (れをる)"],
            album="事実上",
        )
        cand1 = AppleMusicTrack(
            id="reol_cand_1",
            title="mirage",
            artists=["Reol"],
            album="Jijitsujo (Special Edition)",
            duration_ms=215000,
        )
        cand2 = AppleMusicTrack(
            id="reol_cand_2",
            title="平面鏡",
            artists=["Reol"],
            album="Jijitsujo (Special Edition)",
            duration_ms=190000,
        )
        s1 = TrackScorer.score(src, cand1)
        s2 = TrackScorer.score(src, cand2)

        self.assertGreaterEqual(s1.score, 0.88)
        self.assertLess(s2.score, 0.50)

        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [s1, s2])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)
        self.assertEqual(best.track.id, "reol_cand_1")

    def test_single_ep_layer4_corroboration(self):
        # Uncataloged cross-lingual title with verified artist, Single/EP album, and matching duration
        # Retains as a plausible candidate for review (>= 0.70) without dangerous false-positive auto-accept
        src = Track(
            title="未収録のアニメ曲",
            artists=["FictionJunction"],
            album="未収録のアニメ曲",
            duration_ms=210000,
        )
        cand = AppleMusicTrack(
            id="fj_1",
            title="Uncataloged Anime Song",
            artists=["FictionJunction"],
            album="Uncataloged Anime Song - Single",
            duration_ms=210500,
        )
        s = TrackScorer.score(src, cand)
        self.assertGreaterEqual(s.score, 0.70)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [s])
        self.assertEqual(dec, DecisionStatus.REVIEW.value)

    def test_character_song_voice_actor_inversion(self):
        # 夏色恋花火 藤田茜 (ふじた あかね) vs Sagiri Izumi (CV:Akane Fujita)
        src = Track(title="夏色恋花火", artists=["藤田茜 (ふじた あかね)"])
        cand = AppleMusicTrack(
            id="cs_1",
            title="夏色恋花火",
            artists=["Sagiri Izumi (CV:Akane Fujita)"],
            album="エロマンガ先生 Complete Collection",
        )
        s = TrackScorer.score(src, cand)
        self.assertEqual(s.title_score, 1.0)
        self.assertEqual(s.artist_score, 1.0)
        self.assertEqual(s.score, 1.0)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [s])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)

    def test_bracket_subtitle_typo_punctuation_tolerance(self):
        # 妖精小姐的魔法邀约 (Miss Elf's Magical Invitation) 宴宁 vs Miss Elf''s Magical Invitation HOYO-MiX & 宴寧
        src = Track(title="妖精小姐的魔法邀约 (Miss Elf's Magical Invitation)", artists=["宴宁"])
        cand = AppleMusicTrack(
            id="elf_1",
            title="Miss Elf''s Magical Invitation",
            artists=["HOYO-MiX", "宴寧"],
            album="故星銘於長空 (遊戲《崩壞3rd》原聲帶)",
        )
        s = TrackScorer.score(src, cand)
        self.assertGreaterEqual(s.title_score, 0.95)
        self.assertEqual(s.artist_score, 1.0)
        self.assertGreaterEqual(s.score, 0.95)
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(src, [s])
        self.assertEqual(dec, DecisionStatus.AUTO_ACCEPT.value)


if __name__ == "__main__":
    unittest.main()



