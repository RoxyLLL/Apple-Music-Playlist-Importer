"""Regression coverage for recording deduplication and fuzzy artist identity."""
from unittest.mock import Mock

import pytest

from applemusic.cache import PersistentCache
from applemusic.config import Config
from applemusic.matcher.engine import MatchingEngine
from applemusic.matcher.cleaner import TextCleaner
from applemusic.matcher.scorer import TrackScorer
from applemusic.models import AppleMusicTrack, ConfidenceLevel, Playlist, SongMatchResult, Track


def _result(source, candidate):
    scored = TrackScorer.score(source, candidate)
    return SongMatchResult(
        source_track=source, candidates=[scored], selected_candidate=scored,
        status=ConfidenceLevel.EXACT, decision="auto_accept", search_status="matched",
        evidence=scored.evidence,
    )


@pytest.mark.parametrize("changes", [
    {"title": "Hello (Live)"}, {"title": "Hello (Japanese Ver.)"},
    {"title": "Hello (Remix)"}, {"version_tags": ["live"]},
    {"artists": ["Adele", "Guest"]}, {"title": "Hello (Cover Someone)"},
    {"duration_ms": 200001},
])
def test_recording_key_does_not_merge_identity(changes):
    source = Track(title="Hello", artists=["Adele"], album="Album", duration_ms=200000)
    other = source.model_copy(update=changes)
    assert MatchingEngine.get_stable_track_key(source) != MatchingEngine.get_stable_track_key(other)
    assert MatchingEngine.get_stable_track_key(source) == MatchingEngine.get_stable_track_key(source.model_copy(deep=True))


def test_recording_key_stable_after_title_enrichment():
    source = Track(title="Hello (Live)", artists=["Adele"], version_tags=["acoustic"])
    before = MatchingEngine.get_stable_track_key(source)
    _, parsed_tags = TextCleaner.parse_title(source.title)
    source.version_tags = list(dict.fromkeys(source.version_tags + parsed_tags))
    assert MatchingEngine.get_stable_track_key(source) == before
    assert "acoustic" in source.version_tags


def test_playlist_live_separate_but_real_duplicates_deduplicated(tmp_path):
    source = Track(title="Hello", artists=["Adele"])
    live = source.model_copy(update={"title": "Hello (Live)"})
    cache = PersistentCache(tmp_path / "cache.db")
    engine = MatchingEngine(client=Mock(limiter=None), config=Config())
    engine.persistent_cache = cache
    engine.match_track = Mock(side_effect=lambda t, sf: _result(t, AppleMusicTrack(id=t.title, title=t.title, artists=t.artists)))
    try:
        results = engine.match_playlist(Playlist(name="Test", tracks=[source, live, source.model_copy()]))
        assert engine.match_track.call_count == 2
        assert [r.selected_candidate.track.id for r in results] == ["Hello", "Hello (Live)", "Hello"]
    finally:
        cache.close()


@pytest.mark.parametrize("decision", ["auto_accept", "user_confirmed"])
def test_wrong_recording_cache_is_not_broadcast(tmp_path, decision):
    studio = Track(title="Hello", artists=["Adele"])
    live = studio.model_copy(update={"title": "Hello (Live)"})
    candidate = AppleMusicTrack(id="studio", title="Hello", artists=["Adele"])
    cached = _result(studio, candidate)
    cached.decision = decision
    engine = MatchingEngine(client=Mock(limiter=None), config=Config())
    engine.persistent_cache = Mock()
    engine.persistent_cache.get_match.return_value = cached
    engine.match_track = Mock(return_value=_result(live, AppleMusicTrack(id="live", title=live.title, artists=live.artists)))
    results = engine.match_playlist(Playlist(name="Test", tracks=[live]))
    engine.match_track.assert_called_once()
    assert results[0].selected_candidate.track.id == "live"


@pytest.mark.parametrize("source_artist,candidate_artist", [
    ("John Williams", "John Williamson"), ("Adela", "Adele"),
])
@pytest.mark.parametrize("metadata", [False, True])
def test_fuzzy_artist_cannot_auto_accept_even_with_shared_metadata(source_artist, candidate_artist, metadata):
    extra = {"duration_ms": 200000, "album": "Home"} if metadata else {}
    source = Track(title="Coming Home", artists=[source_artist], **extra)
    candidate = AppleMusicTrack(id="other", title="Coming Home", artists=[candidate_artist], **extra)
    scored = TrackScorer.score(source, candidate)
    assert scored.evidence.evidence_type == "artist_identity_unverified"
    assert scored.score <= 0.87
    assert TrackScorer.evaluate_candidates(source, [scored])[2] == "review"


@pytest.mark.parametrize("source_artist,candidate_artist", [
    ("John Williams", "John Williams"), ("G.E.M.邓紫棋", "鄧紫棋"),
    ("『ユイカ』", "Yuika"), ("宇多田ヒカル", "Hikaru Utada"),
])
def test_exact_or_verified_alias_preserves_auto_accept(source_artist, candidate_artist):
    source = Track(title="Home", artists=[source_artist])
    candidate = AppleMusicTrack(id="same", title="Home", artists=[candidate_artist])
    scored = TrackScorer.score(source, candidate)
    assert TrackScorer.evaluate_candidates(source, [scored])[2] == "auto_accept"


def test_old_fuzzy_artist_cache_reassessed(tmp_path):
    source = Track(title="Home", artists=["John Williams"])
    candidate = AppleMusicTrack(id="wrong", title="Home", artists=["John Williamson"])
    result = _result(source, candidate)
    result.selected_candidate.score = 0.995
    cache = PersistentCache(tmp_path / "cache.db")
    try:
        cache.set_match("cn", "old", result)
        cache._get_connection().execute("UPDATE match_cache SET rule_version = '2026.10.v7.3'")
        cache._get_connection().commit()
        refreshed = cache.get_match("cn", "old")
        assert refreshed.decision == "review"
        assert refreshed.selected_candidate.score <= 0.87
    finally:
        cache.close()


def test_original_short_title_artist_false_positive():
    source = Track(title="Home", artists=["John Williams"])
    candidate = AppleMusicTrack(id="wrong", title="Home", artists=["John Williamson"])
    scored = TrackScorer.score(source, candidate)
    assert TrackScorer.evaluate_candidates(source, [scored])[2] == "review"
    assert scored.score <= 0.87


def test_old_manual_confirmation_preserved_for_same_identity(tmp_path):
    source = Track(title="Home", artists=["John Williams"])
    candidate = AppleMusicTrack(id="manual", title="Home", artists=["John Williamson"])
    result = _result(source, candidate)
    result.decision = "user_confirmed"
    cache = PersistentCache(tmp_path / "cache.db")
    try:
        key = ":".join(str(x) for x in MatchingEngine.get_stable_track_key(source))
        cache.set_match("cn", key, result)
        cache._get_connection().execute("UPDATE match_cache SET rule_version = '2026.10.v7.3'")
        cache._get_connection().commit()
        engine = MatchingEngine(client=Mock(limiter=None), config=Config())
        engine.persistent_cache = cache
        engine.match_track = Mock()
        results = engine.match_playlist(Playlist(name="Manual", tracks=[source]))
        assert results[0].decision == "user_confirmed"
        assert results[0].selected_candidate.track.id == "manual"
        engine.match_track.assert_not_called()
    finally:
        cache.close()
