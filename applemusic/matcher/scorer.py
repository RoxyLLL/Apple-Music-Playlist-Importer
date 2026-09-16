"""
Similarity scoring algorithm for matching source tracks against Apple Music candidates.
Implements structured version consistency, continuous duration decay, metadata adequacy,
short title strictness, ISRC priority, and score-gap decision boundaries.
"""

import re
from difflib import SequenceMatcher
from typing import List, Optional, Tuple
from applemusic.matcher.artist_aliases import are_artists_equivalent
from applemusic.matcher.cleaner import TextCleaner
from applemusic.models import AppleMusicTrack, ConfidenceLevel, DecisionStatus, MatchCandidate, Track


class TrackScorer:
    """Calculates match confidence scores and decision statuses for Apple Music candidates."""

    @classmethod
    def calculate_title_similarity(cls, source_title: str, candidate_title: str) -> float:
        """Calculate similarity between core titles."""
        s1 = TextCleaner.normalize(source_title)
        s2 = TextCleaner.normalize(candidate_title)

        if s1 == s2:
            return 1.0

        # Compare core cleaned versions (without version tags/noise)
        c1 = TextCleaner.normalize(TextCleaner.clean_title(source_title))
        c2 = TextCleaner.normalize(TextCleaner.clean_title(candidate_title))
        if c1 == c2:
            return 0.98

        # Compare ignoring all punctuation
        p1 = re.sub(r"[^\w\u4e00-\u9fa5]", "", c1)
        p2 = re.sub(r"[^\w\u4e00-\u9fa5]", "", c2)
        if p1 and p1 == p2:
            return 0.99

        raw_sim = SequenceMatcher(None, s1, s2).ratio()
        clean_sim = SequenceMatcher(None, c1, c2).ratio()

        # Prefix or substring containment bonus
        contain_bonus = 0.0
        if c1 and c2:
            shorter, longer = (c1, c2) if len(c1) <= len(c2) else (c2, c1)
            ratio = len(shorter) / max(1, len(longer))
            if longer.startswith(shorter):
                if len(shorter) <= 2:
                    if len(longer) <= 3:
                        contain_bonus = 0.88
                else:
                    contain_bonus = 0.88
            elif shorter in longer and ratio >= 0.70:
                contain_bonus = 0.80

        return max(raw_sim, clean_sim, contain_bonus)

    @classmethod
    def calculate_artist_similarity(
        cls, source_artists: List[str], candidate_artists: List[str]
    ) -> float:
        """
        Calculate similarity between artist lists.
        Avoids false neutral score (0.60) when artists are missing.
        Supports cross-lingual alias equivalence (e.g. 周华健 <-> Emil Wakin Chau).
        """
        if not source_artists or not candidate_artists:
            return 0.0

        pri_s, fea_s = TextCleaner.parse_artists(source_artists)
        pri_c, fea_c = TextCleaner.parse_artists(candidate_artists)

        norm_pri_s = TextCleaner.normalize(pri_s)
        norm_pri_c = TextCleaner.normalize(pri_c)

        # Primary artist exact or known cross-lingual alias match
        if norm_pri_s and norm_pri_c and (norm_pri_s == norm_pri_c or are_artists_equivalent(norm_pri_s, norm_pri_c)):
            return 1.0

        # Primary artist substring or high similarity
        pri_score = 0.0
        if norm_pri_s and norm_pri_c:
            if norm_pri_s in norm_pri_c or norm_pri_c in norm_pri_s:
                pri_score = 0.95
            else:
                pri_score = SequenceMatcher(None, norm_pri_s, norm_pri_c).ratio()

        # Check full list cross-matching with parsed artists
        raw_s_list = ([pri_s] + fea_s) if pri_s else source_artists
        raw_c_list = ([pri_c] + fea_c) if pri_c else candidate_artists
        norm_s = [TextCleaner.normalize(a) for a in raw_s_list if a.strip()]
        norm_c = [TextCleaner.normalize(a) for a in raw_c_list if a.strip()]

        if set(norm_s) == set(norm_c):
            return 1.0

        max_cross_score = 0.0
        for s in norm_s:
            for c in norm_c:
                if s == c or are_artists_equivalent(s, c):
                    max_cross_score = max(max_cross_score, 1.0)
                elif s in c or c in s:
                    max_cross_score = max(max_cross_score, 0.90)
                else:
                    ratio = SequenceMatcher(None, s, c).ratio()
                    max_cross_score = max(max_cross_score, ratio)

        return max(pri_score, max_cross_score)

    @classmethod
    def calculate_duration_factor(
        cls, dur1_ms: Optional[int], dur2_ms: Optional[int]
    ) -> float:
        """Continuous smooth duration decay factor."""
        if not dur1_ms or not dur2_ms:
            return 0.0

        diff_sec = abs(dur1_ms - dur2_ms) / 1000.0
        if diff_sec <= 3.0:
            return 0.05
        elif diff_sec <= 8.0:
            return 0.02
        elif diff_sec <= 15.0:
            return 0.0
        elif diff_sec <= 30.0:
            return -0.08
        elif diff_sec <= 60.0:
            return -0.18
        else:
            return -0.30

    @classmethod
    def calculate_version_consistency(
        cls, source_title: str, candidate_title: str, candidate_album: Optional[str] = None
    ) -> Tuple[float, List[str]]:
        """
        Evaluate version consistency.
        Hard conflict (e.g. Live vs Studio, Instrumental vs Vocal) incurs heavy penalties.
        """
        _, v_src = TextCleaner.parse_title(source_title)
        _, v_cand = TextCleaner.parse_title(candidate_title)

        if candidate_album:
            alb_lower = candidate_album.lower()
            if "live" in alb_lower and "live" not in v_cand:
                v_cand.append("live")

        v_src_set = set(v_src)
        v_cand_set = set(v_cand)

        critical_tags = {"live", "remix", "instrumental", "acoustic", "demo", "cover"}
        reasons = []
        score_mod = 0.0

        # Check critical tag conflicts
        for tag in critical_tags:
            in_src = tag in v_src_set
            in_cand = tag in v_cand_set

            if in_src and not in_cand:
                score_mod -= 0.40
                reasons.append(f"版本不一致: 来源要求[{tag}]但候选非[{tag}]")
            elif not in_src and in_cand:
                score_mod -= 0.45
                reasons.append(f"版本不一致: 候选为[{tag}]但来源非[{tag}]")
            elif in_src and in_cand:
                score_mod += 0.10
                reasons.append(f"版本吻合: [{tag}]")

        # Remaster handling: If source has no version tag, remaster candidate is acceptable without penalty
        if "remaster" in v_cand_set and not v_src_set:
            score_mod += 0.02
            reasons.append("接受母带重置版(Remaster)")

        return score_mod, reasons

    @classmethod
    def calculate_album_similarity(cls, source_album: Optional[str], candidate_album: Optional[str]) -> float:
        """Calculate album similarity if both available."""
        if not source_album or not candidate_album:
            return 0.0
        a1 = TextCleaner.normalize(source_album)
        a2 = TextCleaner.normalize(candidate_album)
        if a1 == a2:
            return 1.0
        if a1 in a2 or a2 in a1:
            return 0.90
        return SequenceMatcher(None, a1, a2).ratio()

    @classmethod
    def score(cls, source: Track, candidate: AppleMusicTrack) -> MatchCandidate:
        """Compute composite score and return MatchCandidate."""
        reasons: List[str] = []

        # 1. ISRC Exact Match Check
        if source.isrc and candidate.isrc:
            s_isrc = source.isrc.strip().upper()
            c_isrc = candidate.isrc.strip().upper()
            if s_isrc and s_isrc == c_isrc:
                reasons.append("ISRC精确匹配")
                return MatchCandidate(
                    track=candidate,
                    score=1.0,
                    title_score=1.0,
                    artist_score=1.0,
                    album_score=1.0,
                    duration_score=0.05,
                    version_score=0.1,
                    confidence=ConfidenceLevel.EXACT,
                    decision=DecisionStatus.AUTO_ACCEPT.value,
                    decision_reasons=reasons,
                )

        # 2. Individual feature similarities
        title_score = cls.calculate_title_similarity(source.title, candidate.title)
        artist_score = cls.calculate_artist_similarity(source.artists, candidate.artists)
        duration_factor = cls.calculate_duration_factor(source.duration_ms, candidate.duration_ms)
        album_score = cls.calculate_album_similarity(source.album, candidate.album)
        version_factor, v_reasons = cls.calculate_version_consistency(
            source.title, candidate.title, candidate.album
        )
        reasons.extend(v_reasons)

        # 3. Dynamic Weighting & Metadata Adequacy
        has_artist = bool(source.artists and candidate.artists)
        has_album = bool(source.album and candidate.album)
        has_duration = bool(source.duration_ms and candidate.duration_ms)

        if has_artist:
            w_title = 0.50
            w_artist = 0.35
            w_album = 0.10 if has_album else 0.0
            w_duration = 0.05 if has_duration else 0.0
            total_w = w_title + w_artist + w_album + w_duration
            base_score = (
                (title_score * w_title)
                + (artist_score * w_artist)
                + (album_score * w_album)
                + (duration_factor if has_duration else 0.0)
            ) / total_w
        else:
            # Missing artist on one side: normalize to title only, no fake neutral boost
            base_score = title_score * 0.70
            reasons.append("缺少艺人信息")

        # Apply version factor
        composite = base_score + version_factor

        # 4. Hard Guards
        # Check cross-script artist (CJK vs Latin)
        s_cjk = any(re.search(r"[\u4e00-\u9fa5]", a) for a in (source.artists or []))
        c_cjk = any(re.search(r"[\u4e00-\u9fa5]", a) for a in (candidate.artists or []))
        is_cross_script = (s_cjk and not c_cjk) or (not s_cjk and c_cjk)

        # Artist mismatch guard:
        if source.artists and artist_score < 0.35:
            if is_cross_script and title_score >= 0.80:
                composite = min(composite, 0.78)
                reasons.append("艺人跨语种未直接匹配(待复核)")
            else:
                composite *= 0.30
                reasons.append("艺人明显不匹配")

        # Title mismatch guard:
        if title_score < 0.45:
            composite *= 0.30
            reasons.append("歌名相似度过低")

        # Short title safety guard:
        clean_core, _ = TextCleaner.parse_title(source.title)
        cjk_chars = len(re.findall(r"[\u4e00-\u9fa5]", clean_core))
        is_short = (cjk_chars > 0 and len(clean_core) <= 2) or (cjk_chars == 0 and len(clean_core) <= 4)
        if is_short:
            if artist_score < 0.85:
                composite *= 0.40
                reasons.append("短歌名缺乏高置信艺人佐证")

        composite = max(0.0, min(1.0, composite))

        # Initial confidence classification
        if composite >= 0.88:
            confidence = ConfidenceLevel.EXACT
        elif composite >= 0.72:
            confidence = ConfidenceLevel.HIGH
        elif composite >= 0.55:
            confidence = ConfidenceLevel.MEDIUM
        else:
            confidence = ConfidenceLevel.LOW

        return MatchCandidate(
            track=candidate,
            score=round(composite, 3),
            title_score=round(title_score, 3),
            artist_score=round(artist_score, 3),
            album_score=round(album_score, 3),
            duration_score=round(duration_factor, 3),
            version_score=round(version_factor, 3),
            confidence=confidence,
            decision=DecisionStatus.REVIEW.value,
            decision_reasons=reasons,
        )

    @classmethod
    def evaluate_candidates(
        cls,
        source: Track,
        scored_candidates: List[MatchCandidate],
        auto_accept_threshold: float = 0.88,
        min_review_score: float = 0.55,
        min_score_gap: float = 0.08,
    ) -> Tuple[Optional[MatchCandidate], ConfidenceLevel, str, List[str], Optional[float]]:
        """
        Evaluate ranked candidates and determine auto_accept vs review vs no_match.
        Returns (selected_candidate, confidence_status, decision_status, reasons, score_gap).
        """
        if not scored_candidates:
            return None, ConfidenceLevel.NOT_FOUND, DecisionStatus.NO_MATCH.value, ["未找到候选曲目"], None

        best = scored_candidates[0]
        second = scored_candidates[1] if len(scored_candidates) > 1 else None

        score_gap = round(best.score - second.score, 3) if second else 1.0
        reasons = list(best.decision_reasons)

        if best.score < min_review_score:
            return None, ConfidenceLevel.NOT_FOUND, DecisionStatus.NO_MATCH.value, ["无达到及格分的候选"], score_gap

        # Check auto_accept requirements:
        # 1. Score reaches threshold
        # 2. Score gap >= min_score_gap (or top candidates are multiple editions of the same song)
        # 3. Artist score >= 0.70 (if source has artists)
        # 4. Title score >= 0.80
        # 5. Version score >= 0.0 (no version conflict)
        has_artist = bool(source.artists)
        artist_ok = best.artist_score >= 0.70 if has_artist else False
        title_ok = best.title_score >= 0.80
        version_ok = best.version_score >= 0.0

        # Check if second candidate is actually the same song variant (same title core & primary artist)
        is_same_song_variant = False
        if second and second.score >= 0.65:
            sec_title_sim = cls.calculate_title_similarity(best.track.title, second.track.title)
            sec_artist_sim = cls.calculate_artist_similarity(best.track.artists, second.track.artists)
            if sec_title_sim >= 0.90 and sec_artist_sim >= 0.70:
                is_same_song_variant = True

        gap_ok = True if is_same_song_variant else ((score_gap >= min_score_gap) if (second and second.score >= 0.65) else True)

        if best.score >= auto_accept_threshold and artist_ok and title_ok and gap_ok and version_ok:
            reasons.append("各项校验完全通过，自动采纳")
            best.decision = DecisionStatus.AUTO_ACCEPT.value
            return best, ConfidenceLevel.EXACT if best.score >= 0.92 else ConfidenceLevel.HIGH, DecisionStatus.AUTO_ACCEPT.value, reasons, score_gap

        # If candidate is acceptable but has ambiguity (e.g. small gap, slight version difference)
        reasons.append("置信度较高但存在微小歧义或分差较小，建议人工复核")
        best.decision = DecisionStatus.REVIEW.value
        conf = ConfidenceLevel.HIGH if best.score >= 0.75 else ConfidenceLevel.MEDIUM
        return best, conf, DecisionStatus.REVIEW.value, reasons, score_gap
