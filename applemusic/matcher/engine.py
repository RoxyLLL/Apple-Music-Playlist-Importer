"""
Matching engine that searches Apple Music and evaluates candidates.
Implements pre-search stable track deduplication, L0 ISRC exact lookup,
multi-tiered query budgets, and confidence-calibrated stopping conditions.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple
from applemusic.config import Config, get_config
from applemusic.matcher.cleaner import TextCleaner
from applemusic.matcher.scorer import TrackScorer
from applemusic.models import (
    AppleMusicTrack,
    ConfidenceLevel,
    DecisionStatus,
    MatchCandidate,
    Playlist,
    SongMatchResult,
    Track,
)

if TYPE_CHECKING:
    from applemusic.client import AppleMusicClient


class MatchingEngine:
    """Coordinates search queries, candidate collection, and fuzzy scoring."""

    def __init__(self, client: Optional["AppleMusicClient"] = None, config: Optional[Config] = None):
        self.config = config or get_config()
        if client is None:
            from applemusic.client import AppleMusicClient
            client = AppleMusicClient(self.config)
        self.client = client

    @staticmethod
    def get_stable_track_key(track: Track) -> Tuple:
        """
        Generate a unique stable signature for a track to prevent duplicate network searches.
        Prefers platform + original_id; falls back to normalized core title, primary artist, and duration bucket.
        """
        orig_id = str(track.original_id).strip() if track.original_id is not None else ""
        if orig_id and orig_id.lower() not in ("none", "null", "unknown", "undefined") and track.source != "unknown":
            return (track.source, orig_id)

        core_t = TextCleaner.clean_title(track.title).lower()
        pri_a, _ = TextCleaner.parse_artists(track.artists)
        norm_a = TextCleaner.normalize(pri_a)
        norm_alb = TextCleaner.normalize(track.album or "")
        dur_bucket = round(track.duration_ms / 5000) if track.duration_ms else 0
        return (core_t, norm_a, norm_alb, dur_bucket)

    def match_track(self, source: Track, storefront: Optional[str] = None) -> SongMatchResult:
        """Search and match a single source track against Apple Music Catalog."""
        sf = storefront or self.config.storefront

        # Enrich source track with structured parsing
        core_title, version_tags = TextCleaner.parse_title(source.title)
        primary_artist, featured_artists = TextCleaner.parse_artists(source.artists)
        source.clean_title = core_title
        source.version_tags = version_tags
        source.primary_artist = primary_artist
        source.featured_artists = featured_artists

        collected_candidates: Dict[str, AppleMusicTrack] = {}

        # -------------------------------------------------------------
        # Tier 0 (L0): ISRC Direct Exact Lookup
        # -------------------------------------------------------------
        if source.isrc and len(source.isrc.strip()) >= 8:
            isrc_results = self.client.search_by_isrc(source.isrc.strip(), storefront=sf)
            if isrc_results:
                for r in isrc_results:
                    collected_candidates[r.id] = r

                # Check if ISRC candidate achieves auto_accept
                scored_isrc = [TrackScorer.score(source, cand) for cand in collected_candidates.values()]
                scored_isrc.sort(key=lambda x: x.score, reverse=True)
                best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(
                    source,
                    scored_isrc,
                    auto_accept_threshold=self.config.auto_accept_threshold,
                    min_review_score=self.config.min_review_score,
                    min_score_gap=self.config.min_score_gap,
                )
                if dec == DecisionStatus.AUTO_ACCEPT.value:
                    return SongMatchResult(
                        source_track=source,
                        candidates=scored_isrc[:5],
                        selected_candidate=best,
                        status=conf,
                        score_gap=gap,
                        decision=dec,
                        decision_reasons=reasons,
                    )

        # -------------------------------------------------------------
        # Tier 1 - 4: Multi-Tiered Query Expansion Budget
        # -------------------------------------------------------------
        tiered_queries = TextCleaner.generate_tiered_queries(
            title=source.title,
            artists=source.artists,
            album=source.album,
            version_tags=version_tags,
        )

        for tier, q_str in tiered_queries:
            results = self.client.search_catalog(q_str, storefront=sf, limit=self.config.search_limit)
            for r in results:
                if r.id not in collected_candidates:
                    collected_candidates[r.id] = r

            if collected_candidates:
                # Intermediary evaluation: Check if current top candidate satisfies auto_accept
                temp_scored = [TrackScorer.score(source, cand) for cand in collected_candidates.values()]
                temp_scored.sort(key=lambda x: x.score, reverse=True)

                best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(
                    source,
                    temp_scored,
                    auto_accept_threshold=self.config.auto_accept_threshold,
                    min_review_score=self.config.min_review_score,
                    min_score_gap=self.config.min_score_gap,
                )

                # Scientific stopping condition:
                # Stop ONLY if the candidate achieves auto_accept (>= 0.88, no version conflict, artist ok, gap ok)
                # Never stop early on a mediocre 0.60 score!
                if dec == DecisionStatus.AUTO_ACCEPT.value:
                    break

        if not collected_candidates:
            return SongMatchResult(
                source_track=source,
                candidates=[],
                selected_candidate=None,
                status=ConfidenceLevel.NOT_FOUND,
                score_gap=None,
                decision=DecisionStatus.NO_MATCH.value,
                decision_reasons=["在 Apple Music 曲库中未检索到任何相关结果"],
            )

        # -------------------------------------------------------------
        # Final Candidate Re-ranking and Confidence Decision
        # -------------------------------------------------------------
        scored_candidates: List[MatchCandidate] = [
            TrackScorer.score(source, cand) for cand in collected_candidates.values()
        ]
        scored_candidates.sort(key=lambda x: x.score, reverse=True)

        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(
            source,
            scored_candidates,
            auto_accept_threshold=self.config.auto_accept_threshold,
            min_review_score=self.config.min_review_score,
            min_score_gap=self.config.min_score_gap,
        )

        return SongMatchResult(
            source_track=source,
            candidates=scored_candidates[:8],
            selected_candidate=best if dec in (DecisionStatus.AUTO_ACCEPT.value, DecisionStatus.REVIEW.value) else None,
            status=conf,
            score_gap=gap,
            decision=dec,
            decision_reasons=reasons,
        )

    def match_playlist(
        self,
        playlist: Playlist,
        storefront: Optional[str] = None,
        max_workers: int = 4,
        on_progress: Optional[Callable[[int, int, SongMatchResult], None]] = None,
    ) -> List[SongMatchResult]:
        """
        Match all tracks in a playlist with pre-search stable track deduplication.
        Ensures identical tracks in the playlist are only queried once across the network.
        """
        sf = storefront or self.config.storefront
        total = len(playlist.tracks)
        results: List[Optional[SongMatchResult]] = [None] * total

        # Group duplicate tracks by stable signature
        key_to_indices: Dict[Tuple, List[int]] = {}
        unique_tracks: Dict[Tuple, Track] = {}

        for idx, track in enumerate(playlist.tracks):
            key = self.get_stable_track_key(track)
            if key not in key_to_indices:
                key_to_indices[key] = []
                unique_tracks[key] = track
            key_to_indices[key].append(idx)

        unique_keys = list(unique_tracks.keys())
        completed_count = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_key = {
                executor.submit(self.match_track, unique_tracks[k], sf): k
                for k in unique_keys
            }

            for future in as_completed(future_to_key):
                key = future_to_key[future]
                indices = key_to_indices[key]
                try:
                    match_res = future.result()
                except Exception as e:
                    match_res = SongMatchResult(
                        source_track=unique_tracks[key],
                        candidates=[],
                        selected_candidate=None,
                        status=ConfidenceLevel.NOT_FOUND,
                        decision=DecisionStatus.NO_MATCH.value,
                        decision_reasons=[f"检索异常: {str(e)}"],
                    )

                # Broadcast result to all identical track locations
                for idx in indices:
                    # Clone result with original track instance to preserve order
                    res_copy = match_res.model_copy(deep=True)
                    res_copy.source_track = playlist.tracks[idx]
                    results[idx] = res_copy
                    completed_count += 1
                    if on_progress:
                        on_progress(completed_count, total, res_copy)

        return [r for r in results if r is not None]

    def rematch_track(
        self,
        source: Track,
        storefront: Optional[str] = None,
        relaxed: bool = True,
        fallback_storefronts: Optional[List[str]] = None,
    ) -> SongMatchResult:
        """
        Deep rematch / retry for a track that was previously unmatched or missed.
        Uses expanded queries, relaxed score thresholds, and optional multi-storefront search.
        """
        sf = storefront or self.config.storefront
        storefronts_to_try = [sf]
        if fallback_storefronts:
            for fs in fallback_storefronts:
                if fs and fs != sf and fs not in storefronts_to_try:
                    storefronts_to_try.append(fs)

        core_title, version_tags = TextCleaner.parse_title(source.title)
        primary_artist, featured_artists = TextCleaner.parse_artists(source.artists)
        source.clean_title = core_title
        source.version_tags = version_tags
        source.primary_artist = primary_artist
        source.featured_artists = featured_artists

        # Collect queries: Standard tiered queries + Relaxed queries
        queries: List[Tuple[int, str]] = []
        if relaxed:
            queries.extend(
                TextCleaner.generate_relaxed_queries(
                    title=source.title,
                    artists=source.artists,
                    album=source.album,
                    version_tags=version_tags,
                )
            )
        queries.extend(
            TextCleaner.generate_tiered_queries(
                title=source.title,
                artists=source.artists,
                album=source.album,
                version_tags=version_tags,
            )
        )

        # Deduplicate queries by query string
        seen_q = set()
        deduped_queries: List[Tuple[int, str]] = []
        for tier, q_str in queries:
            q_norm = q_str.strip().lower()
            if q_norm and q_norm not in seen_q:
                seen_q.add(q_norm)
                deduped_queries.append((tier, q_str.strip()))

        collected_candidates: Dict[str, AppleMusicTrack] = {}

        # First try L0 ISRC if available
        if source.isrc and len(source.isrc.strip()) >= 8:
            isrc_results = self.client.search_by_isrc(source.isrc.strip(), storefront=sf)
            if isrc_results:
                for r in isrc_results:
                    collected_candidates[r.id] = r

        # Try searching catalog across storefronts
        for cur_sf in storefronts_to_try:
            for tier, q_str in deduped_queries:
                # Query with limit 12 to catch near matches
                results = self.client.search_catalog(q_str, storefront=cur_sf, limit=12)
                for r in results:
                    if r.id not in collected_candidates:
                        collected_candidates[r.id] = r

                if collected_candidates:
                    temp_scored = [TrackScorer.score(source, cand) for cand in collected_candidates.values()]
                    temp_scored.sort(key=lambda x: x.score, reverse=True)
                    best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(
                        source,
                        temp_scored,
                        auto_accept_threshold=self.config.auto_accept_threshold,
                        min_review_score=0.45 if relaxed else self.config.min_review_score,
                        min_score_gap=self.config.min_score_gap,
                    )
                    if dec == DecisionStatus.AUTO_ACCEPT.value:
                        break

            # If auto_accept candidate found in this storefront, stop early across storefronts
            if collected_candidates:
                temp_scored = [TrackScorer.score(source, cand) for cand in collected_candidates.values()]
                temp_scored.sort(key=lambda x: x.score, reverse=True)
                best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(
                    source,
                    temp_scored,
                    auto_accept_threshold=self.config.auto_accept_threshold,
                    min_review_score=0.45 if relaxed else self.config.min_review_score,
                    min_score_gap=self.config.min_score_gap,
                )
                if dec == DecisionStatus.AUTO_ACCEPT.value:
                    break

        if not collected_candidates:
            return SongMatchResult(
                source_track=source,
                candidates=[],
                selected_candidate=None,
                status=ConfidenceLevel.NOT_FOUND,
                score_gap=None,
                decision=DecisionStatus.NO_MATCH.value,
                decision_reasons=["重试检索在 Apple Music 曲库中仍未找到相关结果"],
            )

        scored_candidates: List[MatchCandidate] = [
            TrackScorer.score(source, cand) for cand in collected_candidates.values()
        ]
        scored_candidates.sort(key=lambda x: x.score, reverse=True)

        effective_min_review = 0.45 if relaxed else self.config.min_review_score
        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(
            source,
            scored_candidates,
            auto_accept_threshold=self.config.auto_accept_threshold,
            min_review_score=effective_min_review,
            min_score_gap=self.config.min_score_gap,
        )

        if best and best.score >= effective_min_review:
            if dec == DecisionStatus.NO_MATCH.value:
                dec = DecisionStatus.REVIEW.value
                conf = ConfidenceLevel.MEDIUM
                reasons.append(f"重试检索放宽推荐 (得分: {best.score:.2f})")

        return SongMatchResult(
            source_track=source,
            candidates=scored_candidates[:10],
            selected_candidate=best if dec in (DecisionStatus.AUTO_ACCEPT.value, DecisionStatus.REVIEW.value) else None,
            status=conf,
            score_gap=gap,
            decision=dec,
            decision_reasons=reasons,
        )

    def rematch_playlist(
        self,
        tracks: List[Track],
        storefront: Optional[str] = None,
        relaxed: bool = True,
        fallback_storefronts: Optional[List[str]] = None,
        max_workers: int = 4,
        on_progress: Optional[Callable[[int, int, SongMatchResult], None]] = None,
    ) -> List[SongMatchResult]:
        """
        Batch rematch a list of tracks with concurrency.
        """
        sf = storefront or self.config.storefront
        total = len(tracks)
        results: List[Optional[SongMatchResult]] = [None] * total
        completed_count = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {
                executor.submit(self.rematch_track, track, sf, relaxed, fallback_storefronts): idx
                for idx, track in enumerate(tracks)
            }

            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    match_res = future.result()
                except Exception as e:
                    match_res = SongMatchResult(
                        source_track=tracks[idx],
                        candidates=[],
                        selected_candidate=None,
                        status=ConfidenceLevel.NOT_FOUND,
                        decision=DecisionStatus.NO_MATCH.value,
                        decision_reasons=[f"重试检索异常: {str(e)}"],
                    )
                results[idx] = match_res
                completed_count += 1
                if on_progress:
                    on_progress(completed_count, total, match_res)

        return [r for r in results if r is not None]
