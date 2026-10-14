"""Literal song titles must survive annotation parsing and search fallback."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from applemusic.cache import PersistentCache
from applemusic.matcher.cleaner import TextCleaner
from applemusic.matcher.evidence import MatchEvidence
from applemusic.matcher.scorer import TrackScorer
from applemusic.models import AppleMusicTrack, CatalogSearchOutcome, MatchCandidate, SongMatchResult, Track
from applemusic.web.app import SESSION_API_TOKEN, app


@pytest.mark.parametrize("title", ["With or Without You", "By Your Side", "Self-Control", "Love/Story"])
def test_complete_alternate_title_survives_parsing_and_search(title):
    source = Track(title=f"示例歌名 ({title})", artists=["Artist A"])
    target = AppleMusicTrack(id="literal-title", title=title, artists=["Artist A"])
    assert not TextCleaner.is_version_or_context_note(title)
    assert title in TextCleaner.extract_title_variants(source.title)
    scored = TrackScorer.score(source, target)
    assert scored.title_score >= 0.95
    assert not scored.evidence.conflicts
    assert TrackScorer.evaluate_candidates(source, [scored])[2] == "auto_accept"

    catalog = MagicMock()
    catalog.search_catalog.side_effect = lambda query, *args, **kwargs: CatalogSearchOutcome(
        kind="ok" if query == title else "no_hits",
        tracks=[target] if query == title else [],
    )
    with patch("applemusic.web.app.get_shared_engine", return_value=(catalog, MagicMock())):
        response = TestClient(app).post(
            "/api/search-track",
            headers={"X-App-Token": SESSION_API_TOKEN},
            json={"query": source.title, "source_title": source.title, "source_artists": source.artists},
        )
    assert response.status_code == 200
    assert response.json()["results"][0]["track"]["id"] == target.id
    assert [call.args[0] for call in catalog.search_catalog.call_args_list][1:] == [title]


@pytest.mark.parametrize("title,piece", [("Self-Control", "Self"), ("Love/Story", "Story")])
def test_internal_title_punctuation_does_not_prove_fragment_identity(title, piece):
    source = Track(title=title, artists=["Artist A"])
    candidate = AppleMusicTrack(id="different-song", title=piece, artists=["Artist A"])
    assert piece not in TextCleaner.extract_title_variants(title)
    scored = TrackScorer.score(source, candidate)
    assert TrackScorer.evaluate_candidates(source, [scored])[2] != "auto_accept"


def test_hyphenated_alternate_title_with_explicit_live_note_preserves_version():
    source = Track(title="示例歌名 (Self-Control - Live at Wembley)", artists=["Artist A"])
    assert "Self-Control" in TextCleaner.extract_title_variants(source.title)
    assert "live" in TextCleaner.parse_title_details(source.title).version_tags
    studio = AppleMusicTrack(id="studio", title="Self-Control", artists=["Artist A"])
    scored = TrackScorer.score(source, studio)
    assert scored.title_score >= 0.95
    assert any("version_conflict" in conflict for conflict in scored.evidence.conflicts)
    assert TrackScorer.evaluate_candidates(source, [scored])[2] != "auto_accept"


@pytest.mark.parametrize("note", ["Cover 乐正绫", "feat. Artist B", "Live at Wembley", "Japanese Ver. Live", "2024 Remix"])
def test_explicit_version_and_credit_notes_remain_excluded(note):
    assert TextCleaner.is_version_or_context_note(note)
    assert note not in TextCleaner.extract_title_variants(f"Example Song ({note})")


def test_old_title_fragment_results_are_invalidated_without_losing_manual_choices(tmp_path):
    cache = PersistentCache(db_path=tmp_path / "titles.db")
    try:
        source = Track(title="Self-Control", artists=["Artist A"])
        candidate = MatchCandidate(
            track=AppleMusicTrack(id="wrong-fragment", title="Self", artists=["Artist A"]),
            score=0.988,
            decision="auto_accept",
        )
        old_evidence = MatchEvidence(
            rule_version="2026.10.v7.2", query_policy_version="2026.10.v7.2",
        )
        for decision in ("no_match", "auto_accept", "user_confirmed"):
            result = SongMatchResult(
                source_track=source, decision=decision, evidence=old_evidence,
                candidates=[] if decision == "no_match" else [candidate],
                selected_candidate=None if decision == "no_match" else candidate,
            )
            cache.set_match("cn", decision, result)
            with cache._get_connection() as connection:
                connection.execute(
                    "UPDATE match_cache SET rule_version=?, query_policy_version=? WHERE track_hash=?",
                    ("2026.10.v7.2", "2026.10.v7.2", decision),
                )
            retrieved = cache.get_match("cn", decision)
            if decision == "no_match":
                assert retrieved is None
            elif decision == "auto_accept":
                assert retrieved is not None
                assert retrieved.decision != "auto_accept"
            else:
                assert retrieved is not None
                assert retrieved.decision == "user_confirmed"
                assert retrieved.selected_candidate.track.id == candidate.track.id
    finally:
        cache.close()
