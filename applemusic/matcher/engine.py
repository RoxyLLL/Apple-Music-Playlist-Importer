"""
Matching engine that searches Apple Music and evaluates candidates.
Implements pre-search stable track deduplication, L0 ISRC exact lookup,
multi-tiered query budgets, and confidence-calibrated stopping conditions.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple
from applemusic.config import Config, get_config
from applemusic.matcher.cleaner import TextCleaner
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
        from applemusic.cache import PersistentCache
        self.persistent_cache = PersistentCache.get_instance()

    @staticmethod
    def get_stable_track_key(track: Track) -> Tuple:
        """
        Generate a unique stable signature for a track to prevent duplicate network searches.
        Prefers ISRC if present; then platform + original_id; falls back to normalized core title, primary artist, and duration bucket.
        """
        isrc = (track.isrc or "").strip().upper()
        if isrc and len(isrc) >= 8:
            return ("isrc", isrc)

        orig_id = str(track.original_id).strip() if track.original_id is not None else ""
        if orig_id and orig_id.lower() not in ("none", "null", "unknown", "undefined") and track.source != "unknown":
            return (track.source, orig_id)

        core_t = TextCleaner.clean_title(track.title).lower()
        pri_a, _ = TextCleaner.parse_artists(track.artists)
        norm_a = TextCleaner.normalize(pri_a)
        norm_alb = TextCleaner.normalize(track.album or "")
        dur_bucket = round(track.duration_ms / 5000) if track.duration_ms else 0
        return (core_t, norm_a, norm_alb, dur_bucket)

    def _evaluate_and_aggregate(
        self,
        source: Track,
        outcomes: List[CatalogSearchOutcome],
        collected_candidates: Dict[str, AppleMusicTrack],
        query_attempts: int,
        failures: List[str],
        has_partial_failures: bool,
        storefront: str,
        rate_limited_retry_after: Optional[float] = None,
        relaxed: bool = False,
        is_rematch: bool = False,
    ) -> SongMatchResult:
        sf = storefront or self.config.storefront or "cn"

        # Case 1: Candidates were found
        if collected_candidates:
            scored_candidates: List[MatchCandidate] = [
                TrackScorer.score(source, cand) for cand in collected_candidates.values()
            ]
            scored_candidates.sort(key=lambda x: x.score, reverse=True)

            effective_min_review = 0.45 if (relaxed and is_rematch) else self.config.min_review_score
            best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(
                source,
                scored_candidates,
                auto_accept_threshold=self.config.auto_accept_threshold,
                min_review_score=effective_min_review,
                min_score_gap=self.config.min_score_gap,
            )

            if is_rematch and best and best.score >= effective_min_review and dec == DecisionStatus.NO_MATCH.value:
                dec = DecisionStatus.REVIEW.value
                conf = ConfidenceLevel.MEDIUM
                reasons.append(f"重试检索放宽推荐 (得分: {best.score:.2f})")

            if has_partial_failures:
                reasons.append("部分检索词请求受限或异常，已保留当前已发现候选供复核")

            search_status = "matched" if dec == DecisionStatus.AUTO_ACCEPT.value else "review"
            return SongMatchResult(
                source_track=source,
                candidates=scored_candidates[:10],
                selected_candidate=best if dec in (DecisionStatus.AUTO_ACCEPT.value, DecisionStatus.REVIEW.value) else None,
                status=conf,
                score_gap=gap,
                decision=dec,
                decision_reasons=reasons,
                search_status=search_status,
                search_attempts=query_attempts,
                search_failures=failures,
                retry_after_seconds=rate_limited_retry_after,
                search_incomplete=has_partial_failures,
            )

        # Case 2: Zero candidates found. Determine EXACT reason from outcomes
        # Priority: auth_failed > rate_limited > timeout > network_error > upstream_error > invalid_response > no_match
        kinds = [o.kind for o in outcomes] if outcomes else []

        if "auth_failed" in kinds:
            return SongMatchResult(
                source_track=source,
                candidates=[],
                selected_candidate=None,
                status=ConfidenceLevel.NOT_FOUND,
                score_gap=None,
                decision="auth_required",
                decision_reasons=["Apple Music 授权失效或未登录 (HTTP 401/403)，请先连接 Apple ID"],
                search_status="auth_required",
                search_attempts=query_attempts,
                search_failures=failures,
                retry_after_seconds=None,
                search_incomplete=True,
            )
        elif "rate_limited" in kinds:
            retry_sec = rate_limited_retry_after
            if not retry_sec:
                for o in outcomes:
                    if o.kind == "rate_limited" and o.retry_after_seconds:
                        retry_sec = o.retry_after_seconds
                        break
            retry_sec = retry_sec or 10.0
            return SongMatchResult(
                source_track=source,
                candidates=[],
                selected_candidate=None,
                status=ConfidenceLevel.NOT_FOUND,
                score_gap=None,
                decision="rate_limited",
                decision_reasons=[f"Apple Music 暂时限制检索 (HTTP 429)，预计 {round(retry_sec, 1)} 秒后恢复，请稍后重试"],
                search_status="rate_limited",
                search_attempts=query_attempts,
                search_failures=failures,
                retry_after_seconds=retry_sec,
                search_incomplete=True,
            )
        elif "timeout" in kinds:
            return SongMatchResult(
                source_track=source,
                candidates=[],
                selected_candidate=None,
                status=ConfidenceLevel.NOT_FOUND,
                score_gap=None,
                decision="error",
                decision_reasons=["检索 Apple Music 曲库网络超时，请重试"],
                search_status="timeout",
                search_attempts=query_attempts,
                search_failures=failures,
                retry_after_seconds=None,
                search_incomplete=True,
            )
        elif "network_error" in kinds:
            return SongMatchResult(
                source_track=source,
                candidates=[],
                selected_candidate=None,
                status=ConfidenceLevel.NOT_FOUND,
                score_gap=None,
                decision="error",
                decision_reasons=["网络连接异常，未能连通 Apple Music 服务器，请检查网络后重试"],
                search_status="network_error",
                search_attempts=query_attempts,
                search_failures=failures,
                retry_after_seconds=None,
                search_incomplete=True,
            )
        elif "upstream_error" in kinds:
            return SongMatchResult(
                source_track=source,
                candidates=[],
                selected_candidate=None,
                status=ConfidenceLevel.NOT_FOUND,
                score_gap=None,
                decision="error",
                decision_reasons=["Apple Music 上游曲库服务器返回 5xx 异常，请稍后重试"],
                search_status="upstream_error",
                search_attempts=query_attempts,
                search_failures=failures,
                retry_after_seconds=None,
                search_incomplete=True,
            )
        elif "invalid_response" in kinds:
            return SongMatchResult(
                source_track=source,
                candidates=[],
                selected_candidate=None,
                status=ConfidenceLevel.NOT_FOUND,
                score_gap=None,
                decision="error",
                decision_reasons=["Apple Music 响应数据解析异常，请重试"],
                search_status="search_error",
                search_attempts=query_attempts,
                search_failures=failures,
                retry_after_seconds=None,
                search_incomplete=True,
            )
        else:
            # All executed requests returned HTTP 200 with no hits!
            prefix = "重试检索在" if is_rematch else "在"
            return SongMatchResult(
                source_track=source,
                candidates=[],
                selected_candidate=None,
                status=ConfidenceLevel.NOT_FOUND,
                score_gap=None,
                decision=DecisionStatus.NO_MATCH.value,
                decision_reasons=[f"{prefix} Apple Music [{sf.upper()}] 区域曲库中未检索到匹配歌曲"],
                search_status="no_match",
                search_attempts=query_attempts,
                search_failures=[],
                retry_after_seconds=None,
                search_incomplete=False,
            )

    def match_track(self, source: Track, storefront: Optional[str] = None) -> SongMatchResult:
        """Search and match a single source track against Apple Music Catalog."""
        sf = storefront or self.config.storefront or "cn"

        # Enrich source track with structured parsing
        core_title, version_tags = TextCleaner.parse_title(source.title)
        primary_artist, featured_artists = TextCleaner.parse_artists(source.artists)
        source.clean_title = core_title
        source.version_tags = version_tags
        source.primary_artist = primary_artist
        source.featured_artists = featured_artists

        collected_candidates: Dict[str, AppleMusicTrack] = {}
        outcomes: List[CatalogSearchOutcome] = []
        query_attempts = 0
        failures: List[str] = []
        has_partial_failures = False
        rate_limited_retry_after: Optional[float] = None
        stop_expansion = False

        # -------------------------------------------------------------
        # Tier 0 (L0): ISRC Direct Exact Lookup
        # -------------------------------------------------------------
        if source.isrc and len(source.isrc.strip()) >= 8:
            query_attempts += 1
            isrc_outcome = self.client.search_by_isrc(source.isrc.strip(), storefront=sf)
            outcomes.append(isrc_outcome)
            if isrc_outcome.kind == "ok":
                for r in isrc_outcome.tracks:
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
                        search_status="matched",
                        search_attempts=query_attempts,
                        search_failures=[],
                        retry_after_seconds=None,
                        search_incomplete=False,
                    )
            elif isrc_outcome.kind == "no_hits":
                pass
            else:
                has_partial_failures = True
                failures.append(f"ISRC: {isrc_outcome.safe_message or isrc_outcome.kind}")
                if isrc_outcome.kind == "auth_failed":
                    stop_expansion = True
                elif isrc_outcome.kind == "rate_limited":
                    rate_limited_retry_after = isrc_outcome.retry_after_seconds
                    stop_expansion = True

        # -------------------------------------------------------------
        # Streamlined Query Budget for Initial Matching (Max 1-2 targeted queries)
        # -------------------------------------------------------------
        primary_q = f"{core_title} {primary_artist}".strip() if primary_artist else core_title
        queries_to_run: List[str] = [primary_q] if primary_q else []

        def _evaluate_current():
            if not collected_candidates:
                return None, ConfidenceLevel.NOT_FOUND, DecisionStatus.NO_MATCH.value, [], None
            temp_scored = [TrackScorer.score(source, cand) for cand in collected_candidates.values()]
            temp_scored.sort(key=lambda x: x.score, reverse=True)
            return TrackScorer.evaluate_candidates(
                source,
                temp_scored,
                auto_accept_threshold=self.config.auto_accept_threshold,
                min_review_score=self.config.min_review_score,
                min_score_gap=self.config.min_score_gap,
            )

        # 1. Primary Query: core_title + primary_artist
        if not stop_expansion and queries_to_run:
            q_str = queries_to_run[0]
            query_attempts += 1
            outcome = self.client.search_catalog(q_str, storefront=sf, limit=self.config.search_limit)
            outcomes.append(outcome)

            if outcome.kind == "ok":
                for r in outcome.tracks:
                    if r.id not in collected_candidates:
                        collected_candidates[r.id] = r
                best, conf, dec, reasons, gap = _evaluate_current()
                if dec == DecisionStatus.AUTO_ACCEPT.value:
                    stop_expansion = True
                else:
                    # Candidates found but not auto-accepted: schedule 1 refined fallback if available
                    raw_title = source.title.strip()
                    if best is None or best.score < self.config.min_review_score:
                        # None of the candidates scored high enough; try core title alone as fallback
                        if primary_artist and core_title:
                            queries_to_run.append(core_title)
                    elif version_tags and primary_artist:
                        queries_to_run.append(f"{core_title} {primary_artist} {version_tags[0]}")
                    elif source.album and primary_artist:
                        clean_alb = TextCleaner.clean_title(source.album)
                        if clean_alb:
                            queries_to_run.append(f"{core_title} {primary_artist} {clean_alb}")
                    elif raw_title and raw_title.lower() != core_title.lower() and primary_artist:
                        queries_to_run.append(f"{raw_title} {primary_artist}")
            elif outcome.kind == "no_hits":
                # Primary query returned 0 hits.
                # If raw title differs from core title, schedule fallback query with raw title;
                # Otherwise, fallback to core_title alone so the scorer can evaluate potential artist aliases.
                raw_title = source.title.strip()
                if raw_title and raw_title.lower() != core_title.lower() and primary_artist:
                    queries_to_run.append(f"{raw_title} {primary_artist}")
                elif primary_artist and core_title:
                    queries_to_run.append(core_title)
                elif not primary_artist and raw_title and raw_title.lower() != core_title.lower():
                    queries_to_run.append(raw_title)
            else:
                has_partial_failures = True
                failures.append(f"{q_str}: {outcome.safe_message or outcome.kind}")
                if outcome.kind == "auth_failed":
                    stop_expansion = True
                elif outcome.kind == "rate_limited":
                    rate_limited_retry_after = outcome.retry_after_seconds
                    stop_expansion = True

        # 2. Secondary Query (executed only if needed and scheduled, max 1 extra query)
        if not stop_expansion and len(queries_to_run) > 1:
            q_str = queries_to_run[1]
            query_attempts += 1
            outcome = self.client.search_catalog(q_str, storefront=sf, limit=self.config.search_limit)
            outcomes.append(outcome)

            if outcome.kind == "ok":
                for r in outcome.tracks:
                    if r.id not in collected_candidates:
                        collected_candidates[r.id] = r
            elif outcome.kind == "no_hits":
                pass
            else:
                has_partial_failures = True
                failures.append(f"{q_str}: {outcome.safe_message or outcome.kind}")
                if outcome.kind == "auth_failed":
                    stop_expansion = True
                elif outcome.kind == "rate_limited":
                    rate_limited_retry_after = outcome.retry_after_seconds
                    stop_expansion = True

        return self._evaluate_and_aggregate(
            source=source,
            outcomes=outcomes,
            collected_candidates=collected_candidates,
            query_attempts=query_attempts,
            failures=failures,
            has_partial_failures=has_partial_failures,
            storefront=sf,
            rate_limited_retry_after=rate_limited_retry_after,
            relaxed=False,
            is_rematch=False,
        )

    def match_playlist(
        self,
        playlist: Playlist,
        storefront: Optional[str] = None,
        max_workers: int = 2,
        on_progress: Optional[Callable[[int, int, SongMatchResult], None]] = None,
    ) -> List[SongMatchResult]:
        """
        Match all tracks in a playlist with pre-search stable track deduplication
        and batch early-abort protection for auth loss or rate limits.
        """
        sf = storefront or self.config.storefront or "cn"
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

        # Batch early-abort coordinator
        batch_abort_lock = threading.Lock()
        init_status = None
        init_reason = None
        init_retry = None
        limiter = getattr(self.client, "limiter", None)
        if limiter and getattr(limiter, "circuit_broken", False):
            init_status = "rate_limited"
            init_reason = "批次已暂停：触发 Apple Music 频控保护熔断，等待重试"
            init_retry = max(getattr(limiter, "circuit_break_until", 0) - time.time(), 5.0)
        abort_state: Dict[str, Any] = {"status": init_status, "reason": init_reason, "retry_after": init_retry}

        def _worker_task(track: Track) -> SongMatchResult:
            with batch_abort_lock:
                if abort_state["status"] is not None:
                    return SongMatchResult(
                        source_track=track,
                        candidates=[],
                        selected_candidate=None,
                        status=ConfidenceLevel.NOT_FOUND,
                        decision="unprocessed",
                        decision_reasons=[abort_state["reason"]],
                        search_status=abort_state["status"],
                        retry_after_seconds=abort_state["retry_after"],
                        search_incomplete=True,
                    )

            res = self.match_track(track, sf)

            with batch_abort_lock:
                if abort_state["status"] is None:
                    if res.search_status == "auth_required":
                        abort_state["status"] = "auth_required"
                        abort_state["reason"] = "批次已中止：Apple Music 授权已失效，请重新连接 Apple ID"
                    elif res.search_status == "rate_limited" and getattr(self.client.limiter, "circuit_broken", False):
                        abort_state["status"] = "rate_limited"
                        abort_state["reason"] = "批次已暂停：触发 Apple Music 频控保护，等待重试"
                        abort_state["retry_after"] = res.retry_after_seconds

            return res

        # Check persistent match cache first for instant 0ms resolution (supports multi-index lookup)
        to_query_keys: List[Tuple] = []
        for key in unique_keys:
            track = unique_tracks[key]
            key_str = ":".join(str(x) for x in key)
            cached_match = self.persistent_cache.get_match(sf, key_str) or self.persistent_cache.find_match(sf, track)
            if cached_match:
                indices = key_to_indices[key]
                for idx in indices:
                    res_copy = cached_match.model_copy(deep=True)
                    res_copy.source_track = playlist.tracks[idx]
                    results[idx] = res_copy
                    completed_count += 1
                    if on_progress:
                        on_progress(completed_count, total, res_copy)
            else:
                to_query_keys.append(key)

        # Phase 1.5: Batch ISRC Fast-Path Resolution (reduces HTTP calls by up to 25x)
        isrc_keys = [k for k in to_query_keys if unique_tracks[k].isrc and len(unique_tracks[k].isrc.strip()) >= 8]
        if isrc_keys and abort_state["status"] is None:
            isrc_list = [unique_tracks[k].isrc.strip() for k in isrc_keys]
            batch_outcomes = self.client.search_by_isrc_batch(isrc_list, storefront=sf)

            remaining_to_query_keys: List[Tuple] = []
            for k in to_query_keys:
                if k not in isrc_keys:
                    remaining_to_query_keys.append(k)
                    continue

                track = unique_tracks[k]
                clean_isrc = track.isrc.strip().upper()
                outcome = batch_outcomes.get(clean_isrc)

                matched = False
                if outcome and outcome.kind == "ok" and outcome.tracks:
                    scored_isrc = [TrackScorer.score(track, cand) for cand in outcome.tracks]
                    scored_isrc.sort(key=lambda x: x.score, reverse=True)
                    best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(
                        track,
                        scored_isrc,
                        auto_accept_threshold=self.config.auto_accept_threshold,
                        min_review_score=self.config.min_review_score,
                        min_score_gap=self.config.min_score_gap,
                    )
                    if dec == DecisionStatus.AUTO_ACCEPT.value:
                        matched = True
                        match_res = SongMatchResult(
                            source_track=track,
                            candidates=scored_isrc[:5],
                            selected_candidate=best,
                            status=conf,
                            score_gap=gap,
                            decision=dec,
                            decision_reasons=["ISRC 批量极速精准匹配", *reasons],
                            search_status="matched",
                            search_attempts=1,
                            search_failures=[],
                        )
                        key_str = ":".join(str(x) for x in k)
                        self.persistent_cache.set_match(sf, key_str, match_res, track=track)

                        indices = key_to_indices[k]
                        for idx in indices:
                            res_copy = match_res.model_copy(deep=True)
                            res_copy.source_track = playlist.tracks[idx]
                            results[idx] = res_copy
                            completed_count += 1
                            if on_progress:
                                on_progress(completed_count, total, res_copy)

                elif outcome and outcome.kind in ("auth_failed", "rate_limited"):
                    with batch_abort_lock:
                        if abort_state["status"] is None:
                            if outcome.kind == "auth_failed":
                                abort_state["status"] = "auth_required"
                                abort_state["reason"] = "批次已中止：Apple Music 授权已失效，请重新连接 Apple ID"
                            else:
                                abort_state["status"] = "rate_limited"
                                abort_state["reason"] = "批次已暂停：触发 Apple Music 频控保护，等待重试"
                                abort_state["retry_after"] = outcome.retry_after_seconds

                if not matched:
                    remaining_to_query_keys.append(k)

            to_query_keys = remaining_to_query_keys

        if to_query_keys:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_key = {
                    executor.submit(_worker_task, unique_tracks[k]): k
                    for k in to_query_keys
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
                            decision="error",
                            decision_reasons=[f"检索处理异常: {str(e)}"],
                            search_status="network_error",
                            search_incomplete=True,
                        )

                    # Persist confident or verified no-match outcomes
                    if match_res.decision in ("auto_accept", "user_confirmed", "no_match") and not match_res.search_incomplete:
                        key_str = ":".join(str(x) for x in key)
                        self.persistent_cache.set_match(sf, key_str, match_res, track=unique_tracks[key])

                    # Broadcast result to all identical track locations
                    for idx in indices:
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
        sf = storefront or self.config.storefront or "cn"
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

        seen_q = set()
        deduped_queries: List[Tuple[int, str]] = []
        for tier, q_str in queries:
            q_norm = q_str.strip().lower()
            if q_norm and q_norm not in seen_q:
                seen_q.add(q_norm)
                deduped_queries.append((tier, q_str.strip()))

        collected_candidates: Dict[str, AppleMusicTrack] = {}
        outcomes: List[CatalogSearchOutcome] = []
        query_attempts = 0
        failures: List[str] = []
        has_partial_failures = False
        rate_limited_retry_after: Optional[float] = None
        stop_expansion = False

        # First try L0 ISRC if available
        if source.isrc and len(source.isrc.strip()) >= 8:
            query_attempts += 1
            isrc_outcome = self.client.search_by_isrc(source.isrc.strip(), storefront=sf)
            outcomes.append(isrc_outcome)
            if isrc_outcome.kind == "ok":
                for r in isrc_outcome.tracks:
                    collected_candidates[r.id] = r
            elif isrc_outcome.kind != "no_hits":
                has_partial_failures = True
                failures.append(f"ISRC: {isrc_outcome.safe_message or isrc_outcome.kind}")

        # Try searching catalog across storefronts
        for cur_sf in storefronts_to_try:
            if stop_expansion:
                break

            for tier, q_str in deduped_queries:
                if stop_expansion:
                    break

                query_attempts += 1
                outcome = self.client.search_catalog(q_str, storefront=cur_sf, limit=12)
                outcomes.append(outcome)

                if outcome.kind == "ok":
                    for r in outcome.tracks:
                        if r.id not in collected_candidates:
                            collected_candidates[r.id] = r
                elif outcome.kind == "no_hits":
                    pass
                else:
                    has_partial_failures = True
                    failures.append(f"[{cur_sf}] {q_str}: {outcome.safe_message or outcome.kind}")
                    if outcome.kind == "auth_failed":
                        stop_expansion = True
                    elif outcome.kind == "rate_limited":
                        rate_limited_retry_after = outcome.retry_after_seconds
                        stop_expansion = True

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
                        stop_expansion = True
                        break

        return self._evaluate_and_aggregate(
            source=source,
            outcomes=outcomes,
            collected_candidates=collected_candidates,
            query_attempts=query_attempts,
            failures=failures,
            has_partial_failures=has_partial_failures,
            storefront=sf,
            rate_limited_retry_after=rate_limited_retry_after,
            relaxed=relaxed,
            is_rematch=True,
        )

    def rematch_playlist(
        self,
        tracks: List[Track],
        storefront: Optional[str] = None,
        relaxed: bool = True,
        fallback_storefronts: Optional[List[str]] = None,
        max_workers: int = 2,
        on_progress: Optional[Callable[[int, int, SongMatchResult], None]] = None,
    ) -> List[SongMatchResult]:
        """
        Batch rematch a list of tracks with concurrency and batch early abort.
        """
        sf = storefront or self.config.storefront or "cn"
        total = len(tracks)
        results: List[Optional[SongMatchResult]] = [None] * total
        completed_count = 0

        batch_abort_lock = threading.Lock()
        init_status = None
        init_reason = None
        init_retry = None
        limiter = getattr(self.client, "limiter", None)
        if limiter and getattr(limiter, "circuit_broken", False):
            init_status = "rate_limited"
            init_reason = "批次已暂停：触发 Apple Music 频控保护熔断，等待重试"
            init_retry = max(getattr(limiter, "circuit_break_until", 0) - time.time(), 5.0)
        abort_state: Dict[str, Any] = {"status": init_status, "reason": init_reason, "retry_after": init_retry}

        def _worker_rematch(track: Track) -> SongMatchResult:
            with batch_abort_lock:
                if abort_state["status"] is not None:
                    return SongMatchResult(
                        source_track=track,
                        candidates=[],
                        selected_candidate=None,
                        status=ConfidenceLevel.NOT_FOUND,
                        decision="unprocessed",
                        decision_reasons=[abort_state["reason"]],
                        search_status=abort_state["status"],
                        retry_after_seconds=abort_state["retry_after"],
                        search_incomplete=True,
                    )

            res = self.rematch_track(track, sf, relaxed, fallback_storefronts)

            with batch_abort_lock:
                if abort_state["status"] is None:
                    if res.search_status == "auth_required":
                        abort_state["status"] = "auth_required"
                        abort_state["reason"] = "批次已中止：Apple Music 授权已失效，请重新连接 Apple ID"
                    elif res.search_status == "rate_limited" and getattr(self.client.limiter, "circuit_broken", False):
                        abort_state["status"] = "rate_limited"
                        abort_state["reason"] = "批次已暂停：触发 Apple Music 频控保护，等待重试"
                        abort_state["retry_after"] = res.retry_after_seconds

            return res

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {
                executor.submit(_worker_rematch, track): idx
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
                        decision="error",
                        decision_reasons=[f"重试检索处理异常: {str(e)}"],
                        search_status="network_error",
                        search_incomplete=True,
                    )

                # Persist confident or verified no-match outcomes
                if match_res.decision in ("auto_accept", "user_confirmed", "no_match") and not match_res.search_incomplete:
                    key = self.get_stable_track_key(tracks[idx])
                    key_str = ":".join(str(x) for x in key)
                    self.persistent_cache.set_match(sf, key_str, match_res)

                results[idx] = match_res
                completed_count += 1
                if on_progress:
                    on_progress(completed_count, total, match_res)

        return [r for r in results if r is not None]
