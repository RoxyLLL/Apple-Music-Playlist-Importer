"""Existing caches must not reintroduce the old combined-artist parse."""

import json

import pytest

from applemusic.cache import PersistentCache
from applemusic.matcher.scorer import TrackScorer
from applemusic.models import AppleMusicTrack, CatalogSearchOutcome, SongMatchResult, Track


@pytest.fixture
def cache(tmp_path):
    value = PersistentCache(tmp_path / "cache.db")
    yield value
    value.close()


def legacy_track():
    return AppleMusicTrack(
        id="legacy", title="DAMIDAMI", artists=["Sihan, Sān-Z", "HOYO-MiX"],
        artist_ids=["1", "2", "3"], raw_artist_name="Sihan, Sān-Z & HOYO-MiX",
    )


def test_catalog_cache_restores_combined_artist_names(cache):
    cache.set_catalog("cn", "term", "song", CatalogSearchOutcome(kind="ok", tracks=[legacy_track()]))
    restored = cache.get_catalog("cn", "term", "song")
    assert restored.tracks[0].artists == ["Sihan", "Sān-Z", "HOYO-MiX"]
    assert restored.tracks[0].artist_ids == ["1", "2", "3"]


def test_equivalent_cache_restores_names_without_changing_recording(cache):
    cache.set_equivalence("jp", "source", "cn", "legacy", legacy_track().model_dump_json())
    target_id, payload = cache.get_equivalence_with_payload("cn", "source", source_storefront="jp")
    assert target_id == "legacy"
    assert json.loads(payload)["artists"] == ["Sihan", "Sān-Z", "HOYO-MiX"]


@pytest.mark.parametrize("manual", [False, True])
def test_match_cache_repairs_names_before_rule_reassessment(cache, manual):
    source = Track(title="DAMIDAMI", artists=["Sihan", "三Z-STUDIO", "HOYO-MiX"])
    candidate = TrackScorer.score(source, legacy_track())
    result = SongMatchResult(
        source_track=source, candidates=[candidate], selected_candidate=candidate,
        decision="user_confirmed" if manual else "auto_accept", status="exact", search_status="matched",
    )
    cache.set_match("cn", "song", result)
    with cache._get_connection() as conn:
        conn.execute("UPDATE match_cache SET rule_version = '2026.10.v7.4'")
    restored = cache.get_match("cn", "song")
    assert restored.selected_candidate.track.artists == ["Sihan", "Sān-Z", "HOYO-MiX"]
    assert restored.selected_candidate.track.id == "legacy"
    assert restored.decision == ("user_confirmed" if manual else "auto_accept")
    if not manual:
        assert restored.selected_candidate.artist_score == 1.0


@pytest.mark.parametrize("artists,ids,raw", [
    (["Tyler, The Creator"], ["1"], "Tyler, The Creator"),
    (["Earth, Wind & Fire"], ["1"], "Earth, Wind & Fire"),
    (["Sān-Z", "Sihan", "HOYO-MiX"], ["1", "2", "3"], "Sihan, Sān-Z & HOYO-MiX"),
    (["Unresolved, Name"], [], "Unresolved, Name"),
])
def test_cache_does_not_guess_complete_names_or_missing_relationships(cache, artists, ids, raw):
    track = AppleMusicTrack(id="other", title="Song", artists=artists, artist_ids=ids, raw_artist_name=raw)
    cache.set_catalog("cn", "term", "song", CatalogSearchOutcome(kind="ok", tracks=[track]))
    assert cache.get_catalog("cn", "term", "song").tracks[0].artists == artists


@pytest.mark.parametrize("title,source_artists,display", [
    ("食通万物 修心修身", ["hanser", "三Z-STUDIO", "HOYO-MiX"], "HOYO-MiX, Hanser & Sān-Z"),
    ("DAMIDAMI", ["Sihan", "三Z-STUDIO", "HOYO-MiX"], "Sihan, Sān-Z & HOYO-MiX"),
])
def test_screenshot_full_credit_cases_through_parser_cache_and_scorer(cache, title, source_artists, display):
    from applemusic.client import AppleMusicClient

    candidate = AppleMusicClient._parse_song_item({
        "id": "recording", "attributes": {"name": title, "artistName": display},
        "relationships": {"artists": {"data": [{"id": str(i)} for i in range(3)]}},
    })
    cache.set_catalog("cn", "term", title, CatalogSearchOutcome(kind="ok", tracks=[candidate]))
    restored = cache.get_catalog("cn", "term", title).tracks[0]
    source = Track(title=title, artists=source_artists)
    scored = TrackScorer.score(source, restored)
    assert scored.artist_score == scored.score == 1.0
    assert TrackScorer.evaluate_candidates(source, [scored])[2] == "auto_accept"


@pytest.mark.parametrize("title,candidate_title,performer,projects", [
    ("BITE! 咬合力", "BITE!", "雷雨心", ["Sān-Z", "HOYO-MiX"]),
    ("pinKing", "pinKing", "ChiliChill乐团", ["HOYO-MiX", "Sān-Z"]),
])
def test_screenshot_project_only_cases_explain_missing_performer(title, candidate_title, performer, projects):
    source = Track(title=title, artists=[performer, "三Z-STUDIO", "HOYO-MiX"])
    candidate = AppleMusicTrack(id="project", title=candidate_title, artists=projects)
    scored = TrackScorer.score(source, candidate)
    assert scored.score > 0.87
    assert scored.evidence.evidence_type == "project_credit_incomplete"
    assert not scored.evidence.conflicts
    assert TrackScorer.evaluate_candidates(source, [scored])[2] == "review"
