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


if __name__ == "__main__":
    unittest.main()
