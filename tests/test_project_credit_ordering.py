"""Credit ordering is presentation, not an artist-role or recording identity."""
from itertools import permutations

import pytest

from applemusic.matcher.scorer import TrackScorer
from applemusic.models import AppleMusicTrack, Track


def score(source_artists, candidate_artists, title="Credit Test", candidate_title=None, **metadata):
    source = Track(title=title, artists=source_artists, **metadata)
    candidate = AppleMusicTrack(id="test", title=candidate_title or title, artists=candidate_artists, **metadata)
    scored = TrackScorer.score(source, candidate)
    return scored, TrackScorer.evaluate_candidates(source, [scored])[2]


@pytest.mark.parametrize("performer,title", [("hanser", "食通万物 修心修身"), ("Sihan", "DAMIDAMI")])
def test_complete_credit_sets_ignore_all_orderings(performer, title):
    for source_artists in permutations([performer, "三Z-STUDIO", "HOYO-MiX"]):
        for candidate_artists in permutations([performer.upper(), "Sān-Z", "hoyo-mix"]):
            scored, decision = score(list(source_artists), list(candidate_artists), title)
            assert scored.artist_score == 1.0
            assert scored.score == 1.0
            assert decision == "auto_accept"


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("performer,title", [("雷雨心", "BITE! 咬合力"), ("ChiliChill乐团", "pinKing")])
def test_project_only_credits_rank_without_claiming_vocalist_identity(reverse, performer, title):
    artists = [[performer, "三Z-STUDIO", "HOYO-MiX"], ["HOYO-MiX", "Sān-Z"]]
    if reverse:
        artists.reverse()
    scored, decision = score(*artists, title=title, duration_ms=180000, album="Same Album")
    assert scored.evidence.evidence_type == "project_credit_incomplete"
    assert scored.evidence.verification_level == "medium"
    assert not scored.evidence.conflicts
    assert scored.score > 0.87
    assert decision == "review"
    assert any("演唱者未列明" in reason for reason in scored.decision_reasons)
    assert not any("主表演者不符" in reason or "仅文字相似" in reason for reason in scored.decision_reasons)


@pytest.mark.parametrize("reverse", [False, True])
def test_matched_performer_can_appear_after_project(reverse):
    artists = [["hanser"], ["HOYO-MiX", "hanser"]]
    if reverse:
        artists.reverse()
    scored, decision = score(*artists)
    assert scored.evidence.verification_level == "strong"
    assert decision == "auto_accept"


@pytest.mark.parametrize("candidate_artists", [["HOYO-MiX", "Other Singer"], ["Other Singer", "HOYO-MiX"]])
def test_same_project_cannot_hide_different_explicit_performer(candidate_artists):
    scored, decision = score(["HOYO-MiX", "Original Singer"], candidate_artists)
    assert decision != "auto_accept"
    assert any("artist_mismatch" in conflict for conflict in scored.evidence.conflicts)


def test_explicit_featured_artist_cannot_replace_primary():
    scored, decision = score(["Original Singer feat. Guest"], ["Guest feat. Original Singer"])
    assert decision != "auto_accept"
    assert any("primary_artist_mismatch" in conflict for conflict in scored.evidence.conflicts)


@pytest.mark.parametrize("candidate_credit", ["Guest feat. Original Singer", "Other Singer feat. Guest"])
def test_project_first_does_not_hide_featured_role_or_performer_conflict(candidate_credit):
    scored, decision = score(
        ["HOYO-MiX", "Original Singer feat. Guest"], ["HOYO-MiX", candidate_credit],
    )
    assert decision != "auto_accept"
    assert any("primary_artist_mismatch" in conflict for conflict in scored.evidence.conflicts)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("artists,title", [
    (["HOYO-MiX feat. Other Singer"], "Credit Test"),
    (["HOYO-MiX (CV: Other Singer)"], "Credit Test"),
    (["HOYO-MiX"], "Credit Test (feat. Other Singer)"),
])
def test_shared_project_does_not_erase_explicit_vocalist_conflicts(reverse, artists, title):
    source = Track(title="Credit Test", artists=["Original Singer", "HOYO-MiX"])
    candidate = AppleMusicTrack(id="other", title=title, artists=artists)
    if reverse:
        source, candidate = Track(**candidate.model_dump()), AppleMusicTrack(id="reversed", **source.model_dump())
    scored = TrackScorer.score(source, candidate)
    assert scored.evidence.evidence_type != "project_credit_incomplete"
    assert any("artist_mismatch" in conflict for conflict in scored.evidence.conflicts)
    assert TrackScorer.evaluate_candidates(source, [scored])[2] != "auto_accept"


def test_shared_project_does_not_erase_explicit_featured_role():
    scored, decision = score(["Singer", "HOYO-MiX"], ["HOYO-MiX feat. Singer"])
    assert scored.evidence.evidence_type == "project_credit_role_unverified"
    assert decision == "review"


def test_shared_project_does_not_override_different_title():
    scored, decision = score(["Singer", "HOYO-MiX"], ["HOYO-MiX"], candidate_title="An Entirely Different Song")
    assert decision != "auto_accept"
    assert scored.evidence.verification_level != "strong"


def test_fuzzy_artist_guard_remains():
    scored, decision = score(["John Williams"], ["John Williamson"], duration_ms=180000, album="Album")
    assert scored.score <= 0.87
    assert scored.evidence.evidence_type == "artist_identity_unverified"
    assert decision == "review"


@pytest.mark.parametrize("suffix", [" (Live)", " (Instrumental)", " (Japanese Ver.)"])
def test_project_credits_do_not_override_version_or_language(suffix):
    scored, decision = score(["Singer", "HOYO-MiX"], ["HOYO-MiX"], candidate_title="Credit Test" + suffix)
    assert decision != "auto_accept"


def test_matching_isrc_is_independent_recording_evidence():
    scored, decision = score(["Singer", "HOYO-MiX"], ["HOYO-MiX"], isrc="USABC2400001")
    assert scored.evidence.evidence_type == "isrc"
    assert decision == "auto_accept"


def test_verified_equivalent_is_independent_recording_evidence():
    source = Track(title="Credit Test", artists=["Singer", "HOYO-MiX"])
    candidate = AppleMusicTrack(id="mapped", title=source.title, artists=["HOYO-MiX"], is_equivalent_mapped=True)
    scored = TrackScorer.score(source, candidate)
    assert scored.evidence.evidence_type == "apple_equivalent"
    assert TrackScorer.evaluate_candidates(source, [scored])[2] == "auto_accept"
