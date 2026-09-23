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
from applemusic.matcher.candidate_identity import CandidateAggregator, CandidateIdentity
from applemusic.matcher.query_models import PlannedQuery, QueryContext
from applemusic.matcher.cleaner import TextCleaner
from applemusic.matcher.scorer import TrackScorer
from applemusic.matcher.query_planner import QueryPlanner
from applemusic.matcher.evidence import SingleTrackDiagnostics, VerificationLevel, MATCH_RULE_VERSION, ALIAS_VERSION
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
        collected_candidates: Dict[Any, AppleMusicTrack],
        query_attempts: int,
        failures: List[str],
        has_partial_failures: bool,
        storefront: str,
        rate_limited_retry_after: Optional[float] = None,
        relaxed: bool = False,
        is_rematch: bool = False,
        executed_query_records: Optional[List[Dict[str, Any]]] = None,
        cache_status: str = "none",
        cache_version: Optional[str] = None,
        discovery_chain: Optional[List[str]] = None,
        budget_consumed: Optional[Dict[str, int]] = None,
        availability: str = "available",
        discovery_path: Optional[str] = None,
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
            diag = SingleTrackDiagnostics(
                target_storefront=sf,
                executed_queries=executed_query_records or [],
                candidates_count=len(scored_candidates),
                cache_status=cache_status,
                cache_version=cache_version,
                final_decision=dec,
                verification_level=best.evidence.verification_level if best and best.evidence else VerificationLevel.UNVERIFIED.value,
                matched_fields=best.evidence.matched_fields if best and best.evidence else [],
                conflicts=best.evidence.conflicts if best and best.evidence else [],
                discovery_chain=discovery_chain or [],
                budget_consumed=budget_consumed or {},
            )
            return SongMatchResult(
                source_track=source,
                candidates=scored_candidates[:10],
                selected_candidate=best if dec in (DecisionStatus.AUTO_ACCEPT.value, DecisionStatus.REVIEW.value) else None,
                status=conf,
                score_gap=gap,
                decision=dec,
                decision_reasons=reasons,
                evidence=best.evidence if best else None,
                diagnostics=diag,
                search_status=search_status,
                search_attempts=query_attempts,
                search_failures=failures,
                retry_after_seconds=rate_limited_retry_after,
                search_incomplete=has_partial_failures,
                availability=availability,
                discovery_path=discovery_path,
            )

        # Case 2: Zero candidates found. Determine EXACT reason from outcomes
        # Priority: auth_failed > rate_limited > timeout > network_error > upstream_error > invalid_response > no_match
        kinds = [o.kind for o in outcomes] if outcomes else []

        diag_base = SingleTrackDiagnostics(
            target_storefront=sf,
            executed_queries=executed_query_records or [],
            candidates_count=0,
            cache_status=cache_status,
            cache_version=cache_version,
        )

        if "auth_failed" in kinds:
            diag_base.final_decision = "auth_required"
            return SongMatchResult(
                source_track=source,
                candidates=[],
                selected_candidate=None,
                status=ConfidenceLevel.NOT_FOUND,
                score_gap=None,
                decision="auth_required",
                decision_reasons=["Apple Music 授权失效或未登录 (HTTP 401/403)，请先连接 Apple ID"],
                diagnostics=diag_base,
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
            diag_base.final_decision = "rate_limited"
            return SongMatchResult(
                source_track=source,
                candidates=[],
                selected_candidate=None,
                status=ConfidenceLevel.NOT_FOUND,
                score_gap=None,
                decision="rate_limited",
                decision_reasons=[f"Apple Music 暂时限制检索 (HTTP 429)，预计 {round(retry_sec, 1)} 秒后恢复，请稍后重试"],
                diagnostics=diag_base,
                search_status="rate_limited",
                search_attempts=query_attempts,
                search_failures=failures,
                retry_after_seconds=retry_sec,
                search_incomplete=True,
            )
        elif "timeout" in kinds:
            diag_base.final_decision = "error"
            return SongMatchResult(
                source_track=source,
                candidates=[],
                selected_candidate=None,
                status=ConfidenceLevel.NOT_FOUND,
                score_gap=None,
                decision="error",
                decision_reasons=["检索 Apple Music 曲库网络超时，请重试"],
                diagnostics=diag_base,
                search_status="timeout",
                search_attempts=query_attempts,
                search_failures=failures,
                retry_after_seconds=None,
                search_incomplete=True,
            )
        elif "network_error" in kinds:
            diag_base.final_decision = "error"
            return SongMatchResult(
                source_track=source,
                candidates=[],
                selected_candidate=None,
                status=ConfidenceLevel.NOT_FOUND,
                score_gap=None,
                decision="error",
                decision_reasons=["网络连接异常，未能连通 Apple Music 服务器，请检查网络后重试"],
                diagnostics=diag_base,
                search_status="network_error",
                search_attempts=query_attempts,
                search_failures=failures,
                retry_after_seconds=None,
                search_incomplete=True,
            )
        elif "upstream_error" in kinds:
            diag_base.final_decision = "error"
            return SongMatchResult(
                source_track=source,
                candidates=[],
                selected_candidate=None,
                status=ConfidenceLevel.NOT_FOUND,
                score_gap=None,
                decision="error",
                decision_reasons=["Apple Music 上游曲库服务器返回 5xx 异常，请稍后重试"],
                diagnostics=diag_base,
                search_status="upstream_error",
                search_attempts=query_attempts,
                search_failures=failures,
                retry_after_seconds=None,
                search_incomplete=True,
            )
        elif "invalid_response" in kinds:
            diag_base.final_decision = "error"
            return SongMatchResult(
                source_track=source,
                candidates=[],
                selected_candidate=None,
                status=ConfidenceLevel.NOT_FOUND,
                score_gap=None,
                decision="error",
                decision_reasons=["Apple Music 响应数据解析异常，请重试"],
                diagnostics=diag_base,
                search_status="search_error",
                search_attempts=query_attempts,
                search_failures=failures,
                retry_after_seconds=None,
                search_incomplete=True,
            )
        else:
            # All executed requests returned HTTP 200 with no hits!
            # Check user's personal iCloud Music Library if authorized
            if self.client and self.client.config.is_authorized():
                try:
                    lib_id = self.client.find_library_song_id(source.title, source.artist_str or "")
                    if lib_id:
                        lib_track = AppleMusicTrack(
                            id=lib_id,
                            title=f"[资料库] {source.title}",
                            artists=source.artists or ([source.primary_artist] if source.primary_artist else []),
                            album=source.album or "个人资料库",
                            storefront=sf,
                            url=None,
                        )
                        cand = TrackScorer.score(source, lib_track)
                        cand.decision = DecisionStatus.REVIEW.value
                        cand.decision_reasons.append("匹配至个人资料库，转待复核")
                        diag_base.candidates_count = 1
                        diag_base.final_decision = DecisionStatus.REVIEW.value
                        return SongMatchResult(
                            source_track=source,
                            candidates=[cand],
                            selected_candidate=cand,
                            status=ConfidenceLevel.HIGH,
                            score_gap=1.0,
                            decision=DecisionStatus.REVIEW.value,
                            decision_reasons=["已收录于个人资料库 (本地音源，待复核)"],
                            evidence=cand.evidence,
                            diagnostics=diag_base,
                            search_status="review",
                            search_attempts=query_attempts,
                            search_failures=[],
                            retry_after_seconds=None,
                            search_incomplete=False,
                        )
                except Exception:
                    pass

            prefix = "重试检索在" if is_rematch else "在"
            diag_base.final_decision = DecisionStatus.NO_MATCH.value
            return SongMatchResult(
                source_track=source,
                candidates=[],
                selected_candidate=None,
                status=ConfidenceLevel.NOT_FOUND,
                score_gap=None,
                decision=DecisionStatus.NO_MATCH.value,
                decision_reasons=[f"{prefix} Apple Music [{sf.upper()}] 区域曲库中未检索到匹配歌曲"],
                diagnostics=diag_base,
                search_status="no_match",
                search_attempts=query_attempts,
                search_failures=[],
                retry_after_seconds=None,
                search_incomplete=False,
            )

    def match_track(
        self,
        source: Track,
        storefront: Optional[str] = None,
        single_query_budget: bool = True,
    ) -> SongMatchResult:
        """Search and match a single source track against Apple Music Catalog."""
        sf = storefront or self.config.storefront or "cn"

        # Enrich source track with structured parsing
        core_title, version_tags = TextCleaner.parse_title(source.title)
        primary_artist, featured_artists = TextCleaner.parse_artists(source.artists)
        source.clean_title = core_title
        source.version_tags = version_tags
        source.primary_artist = primary_artist
        source.featured_artists = featured_artists

        aggregator = CandidateAggregator(target_storefront=sf)
        outcomes: List[CatalogSearchOutcome] = []
        executed_query_records: List[Dict[str, Any]] = []
        query_attempts = 0
        failures: List[str] = []
        has_partial_failures = False
        rate_limited_retry_after: Optional[float] = None
        stop_expansion = False
        discovery_chain: List[str] = []
        budget_consumed: Dict[str, int] = {"catalog": 0, "suggestions": 0, "equivalents": 0}

        # -------------------------------------------------------------
        # Tier 0 (L0): ISRC Direct Exact Lookup
        # -------------------------------------------------------------
        if source.isrc and len(source.isrc.strip()) >= 8:
            query_attempts += 1
            budget_consumed["catalog"] += 1
            isrc_outcome = self.client.search_by_isrc(source.isrc.strip(), storefront=sf)
            outcomes.append(isrc_outcome)
            executed_query_records.append({
                "query": source.isrc.strip(),
                "phase": "L0_isrc",
                "provenance": "isrc",
                "storefront": sf,
                "locale": None,
                "kind": isrc_outcome.kind,
                "hits": len(isrc_outcome.tracks) if isrc_outcome.tracks else 0,
            })
            discovery_chain.append("isrc_lookup")
            if isrc_outcome.kind == "ok":
                for r in isrc_outcome.tracks:
                    aggregator.add_candidate(r, discovery_path="isrc_exact")

                # Check if ISRC candidate achieves auto_accept
                scored_isrc = [TrackScorer.score(source, cand.track) for cand in aggregator.get_candidates()]
                scored_isrc.sort(key=lambda x: x.score, reverse=True)
                best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(
                    source,
                    scored_isrc,
                    auto_accept_threshold=self.config.auto_accept_threshold,
                    min_review_score=self.config.min_review_score,
                    min_score_gap=self.config.min_score_gap,
                )
                if dec == DecisionStatus.AUTO_ACCEPT.value:
                    diag = SingleTrackDiagnostics(
                        target_storefront=sf,
                        executed_queries=executed_query_records,
                        candidates_count=len(scored_isrc),
                        final_decision=dec,
                        verification_level=best.evidence.verification_level if best and best.evidence else VerificationLevel.UNVERIFIED.value,
                        matched_fields=best.evidence.matched_fields if best and best.evidence else [],
                        conflicts=best.evidence.conflicts if best and best.evidence else [],
                        discovery_chain=discovery_chain,
                        budget_consumed=budget_consumed,
                    )
                    return SongMatchResult(
                        source_track=source,
                        candidates=scored_isrc[:5],
                        selected_candidate=best,
                        status=conf,
                        score_gap=gap,
                        decision=dec,
                        decision_reasons=reasons,
                        evidence=best.evidence if best else None,
                        diagnostics=diag,
                        search_status="matched",
                        search_attempts=query_attempts,
                        search_failures=[],
                        retry_after_seconds=None,
                        search_incomplete=False,
                        availability="available",
                        discovery_path="isrc_exact",
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

        # Build QueryContext and get planned phases
        ctx = QueryContext(
            title=source.title,
            artists=source.artists,
            album=source.album,
            isrc=getattr(source, "isrc", None),
            duration_ms=source.duration_ms,
            target_storefront=sf,
            supported_locales=self.client.get_storefront_languages(sf) if hasattr(self.client, "get_storefront_languages") else [],
            trans_title=getattr(source, "trans_title", None),
            aliases=getattr(source, "aliases", []) or [],
        )
        planned_phases = QueryPlanner.plan_phases(ctx)
        import inspect
        target_fn = getattr(self.client.search_catalog, "side_effect", None)
        if not callable(target_fn):
            target_fn = self.client.search_catalog
        try:
            sig = inspect.signature(target_fn)
            accepts_locale = "locale" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        except Exception:
            accepts_locale = True

        # Determine Phase D queries and reserve catalog budget for JP discovery
        jp_queries = planned_phases.get("D_jp_discovery", []) if sf.lower() != "jp" else []
        reserved_for_jp = min(len(jp_queries), 1) if jp_queries else 0
        max_target_catalog_budget = max(1, 8 - reserved_for_jp)
        # Reserve catalog budget for Phase C suggestions so Phase A/B does not starve triggered suggestions
        reserved_for_sugg = min(2, max_target_catalog_budget - 1) if max_target_catalog_budget > 1 else 0
        max_ab_catalog_budget = max(1, max_target_catalog_budget - reserved_for_sugg)

        def _execute_query(pq: PlannedQuery, disc_path: str, max_allowed: int = 8) -> Optional[CatalogSearchOutcome]:
            nonlocal query_attempts, has_partial_failures, rate_limited_retry_after, stop_expansion
            if stop_expansion or budget_consumed["catalog"] >= max_allowed:
                return None
            query_attempts += 1
            budget_consumed["catalog"] += 1
            search_kwargs = {"storefront": pq.storefront, "limit": self.config.search_limit}
            if pq.locale and accepts_locale:
                search_kwargs["locale"] = pq.locale
            outcome = self.client.search_catalog(pq.query, **search_kwargs)
            outcomes.append(outcome)
            executed_query_records.append({
                "query": pq.query,
                "phase": pq.phase,
                "provenance": pq.provenance,
                "storefront": pq.storefront,
                "locale": pq.locale,
                "kind": outcome.kind,
                "hits": len(outcome.tracks) if outcome.tracks else 0,
            })
            if outcome.kind == "ok":
                for r in outcome.tracks:
                    aggregator.add_candidate(r, query=pq, discovery_path=disc_path)
            elif outcome.kind != "no_hits":
                has_partial_failures = True
                failures.append(f"{pq.query}: {outcome.safe_message or outcome.kind}")
                if outcome.kind == "auth_failed":
                    stop_expansion = True
                elif outcome.kind == "rate_limited":
                    rate_limited_retry_after = outcome.retry_after_seconds
                    stop_expansion = True
            return outcome

        def _check_auto_accept() -> Optional[SongMatchResult]:
            cands = aggregator.get_candidates()
            if not cands:
                return None
            scored = [TrackScorer.score(source, c.track) for c in cands]
            scored.sort(key=lambda x: x.score, reverse=True)
            best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(
                source,
                scored,
                auto_accept_threshold=self.config.auto_accept_threshold,
                min_review_score=self.config.min_review_score,
                min_score_gap=self.config.min_score_gap,
            )
            if dec == DecisionStatus.AUTO_ACCEPT.value:
                best_ident = next((c for c in cands if c.track.id == best.track.id), None)
                d_path = best_ident.discovery_path if best_ident else "native_search"
                diag = SingleTrackDiagnostics(
                    target_storefront=sf,
                    executed_queries=executed_query_records,
                    candidates_count=len(scored),
                    final_decision=dec,
                    verification_level=best.evidence.verification_level if best and best.evidence else VerificationLevel.UNVERIFIED.value,
                    matched_fields=best.evidence.matched_fields if best and best.evidence else [],
                    conflicts=best.evidence.conflicts if best and best.evidence else [],
                    discovery_chain=discovery_chain,
                    budget_consumed=budget_consumed,
                )
                return SongMatchResult(
                    source_track=source,
                    candidates=scored[:5],
                    selected_candidate=best,
                    status=conf,
                    score_gap=gap,
                    decision=dec,
                    decision_reasons=reasons,
                    evidence=best.evidence if best else None,
                    diagnostics=diag,
                    search_status="matched",
                    search_attempts=query_attempts,
                    search_failures=[],
                    retry_after_seconds=None,
                    search_incomplete=False,
                    availability="available",
                    discovery_path=d_path,
                )
            return None

        def _has_high_quality_candidate() -> bool:
            cands = aggregator.get_candidates()
            if not cands:
                return False
            scored = [TrackScorer.score(source, c.track) for c in cands]
            scored.sort(key=lambda x: x.score, reverse=True)
            best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(
                source,
                scored,
                auto_accept_threshold=self.config.auto_accept_threshold,
                min_review_score=self.config.min_review_score,
                min_score_gap=self.config.min_score_gap,
            )
            # Only unconflicted strong candidate that already meets auto-accept / early-stop stops expansion.
            # Review candidates (even with high score) must NOT block suggestions / JP expansion (TEST_MATRIX Q03).
            return dec == DecisionStatus.AUTO_ACCEPT.value

        # -------------------------------------------------------------
        # Phase A: Native Target Storefront
        # -------------------------------------------------------------
        discovery_chain.append("phase_a_native")
        for pq in planned_phases.get("A_native", []):
            if stop_expansion or budget_consumed["catalog"] >= max_ab_catalog_budget:
                break
            _execute_query(pq, "native_search", max_allowed=max_ab_catalog_budget)
            auto_res = _check_auto_accept()
            if auto_res:
                return auto_res

        # -------------------------------------------------------------
        # Phase B: Universal Multi-Script Variants (beam selection, max 4)
        # -------------------------------------------------------------
        if not stop_expansion:
            discovery_chain.append("phase_b_script")
            for pq in planned_phases.get("B_script", []):
                if stop_expansion or budget_consumed["catalog"] >= max_ab_catalog_budget:
                    break
                _execute_query(pq, "script_variant", max_allowed=max_ab_catalog_budget)
                auto_res = _check_auto_accept()
                if auto_res:
                    return auto_res

        # -------------------------------------------------------------
        # Phase C: Apple Search Suggestions (max 2, token-aligned)
        # -------------------------------------------------------------
        if not stop_expansion and not _has_high_quality_candidate() and budget_consumed["suggestions"] < 2:
            discovery_chain.append("phase_c_suggestions")
            try:
                suggestions = self.client.get_search_suggestions(
                    term=f"{core_title} {primary_artist}".strip(),
                    storefront=sf,
                    limit=5,
                )
                budget_consumed["suggestions"] += 1
                filtered = QueryPlanner.filter_suggestions(suggestions, core_title, primary_artist or "", max_count=2)
                for s_term in filtered:
                    if stop_expansion or budget_consumed["catalog"] >= max_target_catalog_budget:
                        break
                    s_pq = PlannedQuery(
                        query=s_term,
                        storefront=sf,
                        locale=None,
                        phase="C_suggestions",
                        provenance="apple_suggestion",
                        weak_only=True,
                    )
                    _execute_query(s_pq, "apple_suggestion", max_allowed=max_target_catalog_budget)
                    auto_res = _check_auto_accept()
                    if auto_res:
                        return auto_res
            except Exception:
                pass

        # -------------------------------------------------------------
        # Phase D: Cross-Storefront JP Discovery (max 2 queries in JP)
        # -------------------------------------------------------------
        jp_candidates_found: List[AppleMusicTrack] = []
        if not stop_expansion and sf.lower() != "jp":
            discovery_chain.append("phase_d_jp_discovery")
            for pq in jp_queries:
                if stop_expansion or budget_consumed["catalog"] >= 8:
                    break
                query_attempts += 1
                budget_consumed["catalog"] += 1
                jp_kwargs = {"storefront": "jp", "limit": 5}
                if accepts_locale:
                    jp_kwargs["locale"] = "ja-JP"
                jp_outcome = self.client.search_catalog(
                    pq.query,
                    **jp_kwargs,
                )
                outcomes.append(jp_outcome)
                executed_query_records.append({
                    "query": pq.query,
                    "phase": pq.phase,
                    "provenance": pq.provenance,
                    "storefront": "jp",
                    "locale": "ja-JP",
                    "kind": jp_outcome.kind,
                    "hits": len(jp_outcome.tracks) if jp_outcome.tracks else 0,
                })
                if jp_outcome.kind == "ok" and jp_outcome.tracks:
                    for jt in jp_outcome.tracks:
                        if jt not in jp_candidates_found:
                            jp_candidates_found.append(jt)
                elif jp_outcome.kind != "no_hits":
                    has_partial_failures = True
                    failures.append(f"{pq.query}: {jp_outcome.safe_message or jp_outcome.kind}")

            # If JP candidates found, resolve to target storefront!
            if jp_candidates_found:
                discovery_chain.append("jp_prescreen_and_remap")
                # 1. Conservative prescreen: score against source in JP
                scored_jp = [TrackScorer.score(source, jt) for jt in jp_candidates_found]
                top_jp_candidates = [sc for sc in scored_jp if sc.score >= 0.50]
                top_jp_candidates.sort(key=lambda x: x.score, reverse=True)
                top_jp = [sc.track for sc in top_jp_candidates[:5]]

                # Strong identity requires score >= 0.72 and no hard conflicts
                has_strong_jp_identity = any(
                    sc.score >= 0.72
                    and sc.title_score >= 0.75
                    and sc.artist_score >= 0.65
                    and sc.version_score >= 0.0
                    and not (sc.evidence and sc.evidence.conflicts)
                    for sc in top_jp_candidates
                )

                equiv_failed = False
                if top_jp:
                    budget_consumed["equivalents"] += 1
                    try:
                        equiv_map = self.client.get_equivalent_tracks(
                            [t.id for t in top_jp],
                            target_storefront=sf,
                            source_storefront="jp",
                        )
                    except Exception as eq_err:
                        has_partial_failures = True
                        equiv_failed = True
                        failures.append(f"equivalents_error: {eq_err}")
                        equiv_map = {}

                    # ISRC fallback for tracks with no equivalent
                    missing_isrc_tracks = [t for t in top_jp if t.isrc and (t.id not in equiv_map or equiv_map[t.id] is None)]
                    try:
                        isrc_remap = self.client.get_tracks_by_isrc([t.isrc for t in missing_isrc_tracks], storefront=sf) if missing_isrc_tracks else {}
                    except Exception as isrc_err:
                        has_partial_failures = True
                        equiv_failed = True
                        failures.append(f"isrc_remap_error: {isrc_err}")
                        isrc_remap = {}

                    found_target_track = False
                    for jt in top_jp:
                        tgt_track = equiv_map.get(jt.id)
                        if tgt_track:
                            aggregator.add_candidate(
                                tgt_track,
                                discovery_path="jp_equivalents",
                                is_equivalent_mapped=True,
                                original_jp_track=jt,
                            )
                            found_target_track = True
                        elif jt.isrc and jt.isrc in isrc_remap and isrc_remap[jt.isrc]:
                            tgt_track = isrc_remap[jt.isrc][0]
                            aggregator.add_candidate(
                                tgt_track,
                                discovery_path="jp_isrc_remap",
                                is_equivalent_mapped=True,
                                original_jp_track=jt,
                            )
                            found_target_track = True

                    # Check if mapped candidates achieve auto accept
                    auto_res = _check_auto_accept()
                    if auto_res:
                        return auto_res

                    # If NO candidate was ever found in target storefront, but JP has strong candidates:
                    # ONLY declare unavailable_in_target_storefront IF:
                    # 1. No target track found and no other target candidates
                    # 2. No partial search failures (clean 200/no_hits across all queries)
                    # 3. JP had a STRONG identity match (score >= 0.72)
                    # 4. Equivalents and ISRC lookups did not fail
                    if (
                        not found_target_track
                        and not aggregator.get_candidates()
                        and not has_partial_failures
                        and not equiv_failed
                        and has_strong_jp_identity
                    ):
                        diag_unavail = SingleTrackDiagnostics(
                            target_storefront=sf,
                            executed_queries=executed_query_records,
                            candidates_count=0,
                            final_decision="unavailable_in_target_storefront",
                            discovery_chain=discovery_chain,
                            budget_consumed=budget_consumed,
                        )
                        return SongMatchResult(
                            source_track=source,
                            candidates=[],
                            selected_candidate=None,
                            status=ConfidenceLevel.NOT_FOUND,
                            score_gap=None,
                            decision="unavailable_in_target_storefront",
                            decision_reasons=[f"该歌曲在目标地区 Apple Music [{sf.upper()}] 曲库未上架 (在日本区存在匹配音源)"],
                            diagnostics=diag_unavail,
                            search_status="unavailable",
                            search_attempts=query_attempts,
                            search_failures=[],
                            retry_after_seconds=None,
                            search_incomplete=False,
                            availability="unavailable_in_target_storefront",
                            discovery_path="jp_discovery_unavailable",
                        )

        # Final evaluation of all collected candidates
        final_candidates = {c.primary_id: c.track for c in aggregator.get_candidates()}
        best_dpath = aggregator.get_candidates()[0].discovery_path if aggregator.get_candidates() else None
        return self._evaluate_and_aggregate(
            source=source,
            outcomes=outcomes,
            collected_candidates=final_candidates,
            query_attempts=query_attempts,
            failures=failures,
            has_partial_failures=has_partial_failures,
            storefront=sf,
            rate_limited_retry_after=rate_limited_retry_after,
            relaxed=False,
            is_rematch=False,
            executed_query_records=executed_query_records,
            discovery_chain=discovery_chain,
            budget_consumed=budget_consumed,
            availability="available",
            discovery_path=best_dpath,
        )

    def match_playlist(
        self,
        playlist: Playlist,
        storefront: Optional[str] = None,
        max_workers: int = 1,
        on_progress: Optional[Callable[[int, int, SongMatchResult], None]] = None,
    ) -> List[SongMatchResult]:
        """
        Match all tracks in a playlist with pre-search stable track deduplication
        and batch early-abort protection for auth loss.
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

        # Batch early-abort coordinator (only aborts entire batch on unrecoverable auth failure or persistent circuit break)
        batch_abort_lock = threading.Lock()
        init_status = None
        init_reason = None
        init_retry = None
        limiter = getattr(self.client, "limiter", None)
        if limiter:
            if limiter.circuit_broken:
                init_status = "rate_limited"
                init_reason = "批次已暂停：触发 Apple Music 频控保护熔断，等待重试"
                init_retry = max(getattr(limiter, "circuit_break_until", 0) - time.time(), 5.0)
            else:
                is_cooling, cd_remaining = limiter.is_cooling_down()
                if is_cooling and cd_remaining > 0.05:
                    time.sleep(cd_remaining + 0.15)
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
        # Always prioritize the user's active storefront for playable library imports
        storefronts_to_try = [sf]
        fallbacks = fallback_storefronts if fallback_storefronts is not None else getattr(self.config, "fallback_storefronts", ["hk", "tw", "us"])
        if fallbacks:
            for f_sf in fallbacks:
                if f_sf and f_sf.lower() != sf.lower() and f_sf.lower() not in [s.lower() for s in storefronts_to_try]:
                    storefronts_to_try.append(f_sf.lower())

        core_title, version_tags = TextCleaner.parse_title(source.title)
        primary_artist, featured_artists = TextCleaner.parse_artists(source.artists)
        source.clean_title = core_title
        source.version_tags = version_tags
        source.primary_artist = primary_artist
        source.featured_artists = featured_artists

        # Collect planned queries from QueryPlanner
        planned_queries = QueryPlanner.plan_deep_retry(source, max_budget=QueryPlanner.RETRY_TOTAL_BUDGET)
        executed_query_records: List[Dict[str, Any]] = []

        collected_candidates: Dict[Any, AppleMusicTrack] = {}
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
            executed_query_records.append({
                "query": source.isrc.strip(),
                "provenance": "isrc",
                "storefront": sf,
                "kind": isrc_outcome.kind,
                "hits": len(isrc_outcome.tracks) if isrc_outcome.tracks else 0,
            })
            if isrc_outcome.kind == "ok":
                for r in isrc_outcome.tracks:
                    cand_key = (r.storefront or sf, r.id)
                    collected_candidates[cand_key] = r
            elif isrc_outcome.kind != "no_hits":
                has_partial_failures = True
                failures.append(f"ISRC: {isrc_outcome.safe_message or isrc_outcome.kind}")

        # Try searching catalog with strict query budgets
        total_queries_run = 0
        for cur_sf in storefronts_to_try:
            if stop_expansion or total_queries_run >= QueryPlanner.RETRY_TOTAL_BUDGET:
                break

            sf_queries_run = 0
            for pq in planned_queries:
                if (
                    stop_expansion
                    or total_queries_run >= QueryPlanner.RETRY_TOTAL_BUDGET
                    or sf_queries_run >= QueryPlanner.RETRY_PER_STOREFRONT_BUDGET
                ):
                    break

                q_str = pq.query
                query_attempts += 1
                total_queries_run += 1
                sf_queries_run += 1

                outcome = self.client.search_catalog(q_str, storefront=cur_sf, limit=12)
                outcomes.append(outcome)
                executed_query_records.append({
                    "query": q_str,
                    "provenance": pq.provenance,
                    "storefront": cur_sf,
                    "kind": outcome.kind,
                    "hits": len(outcome.tracks) if outcome.tracks else 0,
                })

                if outcome.kind == "ok":
                    for r in outcome.tracks:
                        cand_key = (r.storefront or cur_sf, r.id)
                        if cand_key not in collected_candidates:
                            collected_candidates[cand_key] = r
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
                    # Stop early only if candidate is auto accepted
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
            executed_query_records=executed_query_records,
            discovery_chain=["rematch_multi_storefront"],
            budget_consumed={"catalog": total_queries_run},
            availability="available",
        )

    def rematch_playlist(
        self,
        tracks: List[Track],
        storefront: Optional[str] = None,
        relaxed: bool = True,
        fallback_storefronts: Optional[List[str]] = None,
        max_workers: int = 1,
        on_progress: Optional[Callable[[int, int, SongMatchResult], None]] = None,
    ) -> List[SongMatchResult]:
        """
        Batch rematch a list of tracks with safe pacing and auth loss early abort.
        """
        sf = storefront or self.config.storefront or "cn"
        total = len(tracks)
        results: List[Optional[SongMatchResult]] = [None] * total
        completed_count = 0

        # Batch early-abort coordinator (only aborts entire batch on unrecoverable auth failure or persistent circuit break)
        batch_abort_lock = threading.Lock()
        init_status = None
        init_reason = None
        init_retry = None
        limiter = getattr(self.client, "limiter", None)
        if limiter:
            if limiter.circuit_broken:
                init_status = "rate_limited"
                init_reason = "批次已暂停：触发 Apple Music 频控保护熔断，等待重试"
                init_retry = max(getattr(limiter, "circuit_break_until", 0) - time.time(), 5.0)
            else:
                is_cooling, cd_remaining = limiter.is_cooling_down()
                if is_cooling and cd_remaining > 0.05:
                    time.sleep(cd_remaining + 0.15)
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
