"""
Similarity scoring algorithm for matching source tracks against Apple Music candidates.
Implements structured version consistency, continuous duration decay, metadata adequacy,
short title strictness, ISRC priority, and score-gap decision boundaries.
"""

import re
from difflib import SequenceMatcher
from functools import lru_cache
from typing import List, Optional, Tuple
from applemusic.matcher.artist_aliases import are_artists_equivalent
from applemusic.matcher.title_aliases import are_titles_equivalent
from applemusic.matcher.cleaner import TextCleaner
from applemusic.matcher.evidence import (
    MatchEvidence,
    VerificationLevel,
    MATCH_RULE_VERSION,
    ALIAS_VERSION,
    QUERY_POLICY_VERSION,
    ROMANIZER_VERSION,
    EXCEPTION_REGISTRY_VERSION,
)
from applemusic.models import AppleMusicTrack, ConfidenceLevel, DecisionStatus, MatchCandidate, Track


@lru_cache(maxsize=16384)
def _fast_sequence_ratio(s1: str, s2: str) -> float:
    """
    Fast sequence ratio calculation with theoretical bound pruning and LRU memoization.
    Eliminates expensive difflib.SequenceMatcher O(N*M) passes for strings with disparate lengths
    or minimal character overlap.
    """
    if s1 == s2:
        return 1.0
    len1 = len(s1)
    len2 = len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0

    min_len = min(len1, len2)
    max_len = max(len1, len2)
    upper_bound = (2.0 * min_len) / (len1 + len2)
    if upper_bound < 0.25:
        return upper_bound * 0.5

    # Substring check for fast short-circuiting
    if min_len >= 2 and (s1 in s2 or s2 in s1):
        ratio = min_len / max_len
        if ratio >= 0.85:
            return 0.90 + 0.10 * ratio

    # Quick character set overlap check for longer strings
    if min_len >= 4:
        common_chars = len(set(s1) & set(s2))
        if common_chars / min_len < 0.20:
            return 0.15

    return SequenceMatcher(None, s1, s2).ratio()


@lru_cache(maxsize=8192)
def _calculate_title_similarity_cached(source_title: str, candidate_title: str, artist: Optional[str] = None) -> float:
    """Core cached implementation of title similarity."""
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
    p1 = re.sub(r"[^\w\u4e00-\u9fa5\u3040-\u30ff]", "", c1)
    p2 = re.sub(r"[^\w\u4e00-\u9fa5\u3040-\u30ff]", "", c2)
    if p1 and p1 == p2:
        return 0.99

    # Known cross-lingual title aliases (e.g. 夜に駆ける <-> Racing into the Night)
    if are_titles_equivalent(s1, s2, artist=artist) or are_titles_equivalent(c1, c2, artist=artist):
        return 1.0

    # Check extracted title variants (e.g. bracketed English/Romaji subtitles)
    v1_list = TextCleaner.extract_title_variants(source_title)
    v2_list = TextCleaner.extract_title_variants(candidate_title)
    for v1 in v1_list:
        for v2 in v2_list:
            if v1 == v2 or are_titles_equivalent(v1, v2, artist=artist):
                return 0.98
            nv1 = TextCleaner.normalize(v1)
            nv2 = TextCleaner.normalize(v2)
            if nv1 == nv2 or are_titles_equivalent(nv1, nv2, artist=artist):
                return 0.98
            # Punctuation-stripped comparison (e.g. "Miss Elf's" vs "Miss Elf''s" / "Miss Elf s")
            p_nv1 = re.sub(r"[^\w\u4e00-\u9fa5\u3040-\u30ff]", "", nv1)
            p_nv2 = re.sub(r"[^\w\u4e00-\u9fa5\u3040-\u30ff]", "", nv2)
            if p_nv1 and p_nv2 and (p_nv1 == p_nv2 or are_titles_equivalent(p_nv1, p_nv2, artist=artist)):
                return 0.98
            # Fuzzy variant ratio for minor typos or official metadata discrepancies
            if len(nv1) >= 4 and len(nv2) >= 4:
                v_ratio = _fast_sequence_ratio(nv1, nv2)
                if v_ratio >= 0.85:
                    return max(v_ratio, 0.95)
                if len(p_nv1) >= 4 and len(p_nv2) >= 4:
                    if p_nv1 in p_nv2 or p_nv2 in p_nv1:
                        return 0.92
                    p_ratio = _fast_sequence_ratio(p_nv1, p_nv2)
                    if p_ratio >= 0.85:
                        return max(p_ratio, 0.95)

    # Japanese Kana/Kanji <-> Romaji comparison with multi-reading and morphological analysis
    from applemusic.matcher.title_aliases import KANJI_TO_ROMAJI_COMPOUNDS
    has_japanese = bool(
        re.search(r"[\u3040-\u30ff]", c1) or re.search(r"[\u3040-\u30ff]", c2)
        or any(k in c1 for k, _ in KANJI_TO_ROMAJI_COMPOUNDS)
        or any(k in c2 for k, _ in KANJI_TO_ROMAJI_COMPOUNDS)
    )
    if has_japanese:
        v1_romaji = TextCleaner.get_japanese_romaji_variants(c1, is_artist=False)
        v2_romaji = TextCleaner.get_japanese_romaji_variants(c2, is_artist=False)
        for r1 in v1_romaji:
            for r2 in v2_romaji:
                if r1 == r2 or are_titles_equivalent(r1, r2, artist=artist):
                    return 0.98
                rp1 = re.sub(r"[^\w]", "", r1).lower()
                rp2 = re.sub(r"[^\w]", "", r2).lower()
                if rp1 and rp2:
                    if rp1 == rp2 or are_titles_equivalent(rp1, rp2, artist=artist):
                        return 0.98
                    if len(rp1) >= 4 and len(rp2) >= 4:
                        ratio = _fast_sequence_ratio(r1, r2)
                        if ratio >= 0.85:
                            return max(ratio, 0.90)

    # Katakana loanword phonetic alignment (e.g. ミラージュ <-> mirage)
    loan_sim = TextCleaner.match_katakana_loanword(c1, c2)
    if loan_sim >= 0.85:
        return 0.98

    for v1 in v1_list:
        for v2 in v2_list:
            v_loan = TextCleaner.match_katakana_loanword(v1, v2)
            if v_loan >= 0.85:
                return 0.98

    raw_sim = _fast_sequence_ratio(s1, s2)
    clean_sim = _fast_sequence_ratio(c1, c2)

    # Prefix or substring containment bonus
    contain_bonus = 0.0
    if loan_sim >= 0.65:
        contain_bonus = max(contain_bonus, 0.90)
    for v1 in v1_list:
        for v2 in v2_list:
            if TextCleaner.match_katakana_loanword(v1, v2) >= 0.65:
                contain_bonus = max(contain_bonus, 0.90)
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

    # If core cleaned titles have very low similarity (< 0.40),
    # do not let shared noise/tags (e.g. '(feat. ...)', '(live)') in raw titles
    # artificially elevate the score.
    if clean_sim < 0.40:
        base_sim = clean_sim
    else:
        base_sim = max(raw_sim, clean_sim)

    return max(base_sim, contain_bonus)


class TrackScorer:
    """Calculates match confidence scores and decision statuses for Apple Music candidates."""

    @classmethod
    def calculate_title_similarity(
        cls,
        source_title: str,
        candidate_title: str,
        trans_title: Optional[str] = None,
        aliases: Optional[List[str]] = None,
        artist: Optional[str] = None,
    ) -> float:
        """
        Calculate similarity between core titles.
        Also checks official translation (trans_title) and alternate aliases.
        """
        sim = _calculate_title_similarity_cached(source_title, candidate_title, artist=artist)
        if sim >= 0.95:
            return sim

        if trans_title:
            t_sim = _calculate_title_similarity_cached(trans_title, candidate_title, artist=artist)
            sim = max(sim, t_sim)
            if sim >= 0.95:
                return sim

        if aliases:
            for a in aliases:
                if a and a.strip():
                    a_sim = _calculate_title_similarity_cached(a.strip(), candidate_title, artist=artist)
                    sim = max(sim, a_sim)
                    if sim >= 0.95:
                        return sim

        return sim

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

        det_s = TextCleaner.parse_artist_details(source_artists)
        det_c = TextCleaner.parse_artist_details(candidate_artists)

        norm_pri_s = TextCleaner.normalize(det_s.primary)
        norm_pri_c = TextCleaner.normalize(det_c.primary)

        # 1. Primary performer candidates (includes character voices & bracketed readings/aliases)
        pri_cands_s = [det_s.primary] + det_s.aliases + det_s.character_voices
        pri_cands_c = [det_c.primary] + det_c.aliases + det_c.character_voices

        # Check primary performer candidates cross-match
        for s_cand in pri_cands_s:
            if not s_cand:
                continue
            ns = TextCleaner.normalize(s_cand)
            for c_cand in pri_cands_c:
                if not c_cand:
                    continue
                nc = TextCleaner.normalize(c_cand)
                if ns == nc or are_artists_equivalent(ns, nc):
                    return 1.0
                sw = ns.split()
                cw = nc.split()
                if len(sw) == 2 and len(cw) == 2 and sw[0] == cw[1] and sw[1] == cw[0]:
                    return 1.0

                # Kana / Romaji transliteration
                has_jp = bool(
                    re.search(r"[\u3040-\u30ff]", ns)
                    or re.search(r"[\u3040-\u30ff]", nc)
                    or re.search(r"[\u3040-\u30ff]", s_cand)
                    or re.search(r"[\u3040-\u30ff]", c_cand)
                )
                if has_jp:
                    s_vars = TextCleaner.get_japanese_romaji_variants(s_cand, is_artist=True) or [ns]
                    c_vars = TextCleaner.get_japanese_romaji_variants(c_cand, is_artist=True) or [nc]
                    for rs in s_vars:
                        for rc in c_vars:
                            if rs == rc or are_artists_equivalent(rs, rc):
                                return 1.0
                            rsw = rs.split()
                            rcw = rc.split()
                            if len(rsw) == 2 and len(rcw) == 2 and rsw[0] == rcw[1] and rsw[1] == rcw[0]:
                                return 1.0
                            rsp = re.sub(r"[^\w]", "", rs)
                            rcp = re.sub(r"[^\w]", "", rc)
                            if rsp and rsp == rcp and (len(rsp) >= 4 or len(ns) < 2):
                                return 1.0

        # Substring / ratio check between primary artists
        pri_score = 0.0
        if norm_pri_s and norm_pri_c:
            s_cjk = len(re.findall(r"[\u4e00-\u9fa5]", norm_pri_s))
            c_cjk = len(re.findall(r"[\u4e00-\u9fa5]", norm_pri_c))
            shorter, longer = (norm_pri_s, norm_pri_c) if len(norm_pri_s) <= len(norm_pri_c) else (norm_pri_c, norm_pri_s)
            short_cjk = s_cjk if len(norm_pri_s) <= len(norm_pri_c) else c_cjk
            if shorter in longer:
                if short_cjk <= 2 or len(shorter) <= 3:
                    pri_score = _fast_sequence_ratio(norm_pri_s, norm_pri_c)
                else:
                    ratio = len(shorter) / max(1, len(longer))
                    pri_score = 0.90 if ratio >= 0.75 else _fast_sequence_ratio(norm_pri_s, norm_pri_c)
            else:
                pri_score = _fast_sequence_ratio(norm_pri_s, norm_pri_c)

        if pri_score >= 1.0:
            return 1.0

        # 2. Collaborative co-artists check (&, +, /, 、)
        # e.g. "宴宁" vs "HOYO-MiX & 宴寧"
        collab_match = False
        all_s = [TextCleaner.normalize(n) for n in det_s.all_names if n]
        all_c = [TextCleaner.normalize(n) for n in det_c.all_names if n]

        if set(all_s) == set(all_c) and all_s:
            return 1.0

        # If source matches candidate's collaborator (or vice versa) and it's NOT an explicit guest
        for sc in det_c.collaborators:
            n_collab = TextCleaner.normalize(sc)
            for s_cand in pri_cands_s:
                ns = TextCleaner.normalize(s_cand)
                if ns == n_collab or are_artists_equivalent(ns, n_collab):
                    collab_match = True
                    break
            if collab_match:
                break

        if not collab_match:
            for sc in det_s.collaborators:
                n_collab = TextCleaner.normalize(sc)
                for c_cand in pri_cands_c:
                    nc = TextCleaner.normalize(c_cand)
                    if nc == n_collab or are_artists_equivalent(nc, n_collab):
                        collab_match = True
                        break
                if collab_match:
                    break

        if collab_match:
            # If the source only specified one artist, matching the co-performer on a collab track is full match
            if len(source_artists) == 1 and not det_s.featured:
                return 1.0
            return max(0.92, pri_score)

        # 3. Explicit Guest / Featured Inversion Check (R2 / S04)
        # One side's primary artist matches the other side's featured artist, but primary is missing
        is_guest_inversion = False
        norm_feat_s = [TextCleaner.normalize(f) for f in det_s.featured if f]
        norm_feat_c = [TextCleaner.normalize(f) for f in det_c.featured if f]

        for fs in norm_feat_s:
            if fs == norm_pri_c or are_artists_equivalent(fs, norm_pri_c):
                is_guest_inversion = True
                break
        if not is_guest_inversion:
            for fc in norm_feat_c:
                if fc == norm_pri_s or are_artists_equivalent(fc, norm_pri_s):
                    is_guest_inversion = True
                    break

        if is_guest_inversion:
            # Guest artist match is useful for recall, but MUST NOT exceed 0.60 (cannot auto_accept)
            return 0.60

        # General cross similarity between non-primary artists: capped at 0.50 if primary artists differ
        max_cross_score = 0.0
        for s in all_s:
            for c in all_c:
                if s == c or are_artists_equivalent(s, c):
                    max_cross_score = max(max_cross_score, 0.50)
                    break
                ratio = _fast_sequence_ratio(s, c)
                max_cross_score = max(max_cross_score, ratio * 0.50)
            if max_cross_score >= 0.50:
                break

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

        critical_tags = {
            "live", "remix", "instrumental", "acoustic", "demo", "cover",
            "piano", "guitar", "orchestral", "sped_up", "slowed",
            "tv_size", "english_ver", "alternate_cut",
        }
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
        return _fast_sequence_ratio(a1, a2)

    @classmethod
    def score(cls, source: Track, candidate: AppleMusicTrack) -> MatchCandidate:
        """Compute composite score, evidence, and return MatchCandidate."""
        reasons: List[str] = []
        conflicts: List[str] = []
        matched_fields: List[str] = []

        # 1. Feature similarities
        pri_s_for_title = source.artists[0] if source.artists else None
        title_score = cls.calculate_title_similarity(
            source.title, candidate.title, trans_title=source.trans_title, aliases=source.aliases, artist=pri_s_for_title
        )
        version_factor, v_reasons = cls.calculate_version_consistency(
            source.title, candidate.title, candidate.album
        )
        reasons.extend(v_reasons)
        artist_score = cls.calculate_artist_similarity(source.artists, candidate.artists)
        orig_jp = getattr(candidate, "original_jp_track", None)
        if orig_jp and getattr(orig_jp, "artists", None):
            jp_art_sim = cls.calculate_artist_similarity(source.artists, orig_jp.artists)
            artist_score = max(artist_score, jp_art_sim)
        elif getattr(candidate, "discovery_path", None) in ("jp_equivalents", "apple_equivalent") or getattr(candidate, "is_equivalent_mapped", False):
            if title_score >= 0.80 and version_factor >= 0.0:
                artist_score = max(artist_score, 0.90)
        duration_factor = cls.calculate_duration_factor(source.duration_ms, candidate.duration_ms)
        album_score = cls.calculate_album_similarity(source.album, candidate.album)

        # Check primary artist mismatch (R2 / S04)
        det_s = TextCleaner.parse_artist_details(source.artists)
        det_c = TextCleaner.parse_artist_details(candidate.artists)
        has_primary_artist_mismatch = False

        if det_s.primary and det_c.primary:
            # Check if primary performers match (via direct, alias, CV, or Apple artist ID)
            pri_cands_s = [det_s.primary] + det_s.aliases + det_s.character_voices
            pri_cands_c = [det_c.primary] + det_c.aliases + det_c.character_voices
            has_pri_match = False
            for s_cand in pri_cands_s:
                ns = TextCleaner.normalize(s_cand)
                for c_cand in pri_cands_c:
                    nc = TextCleaner.normalize(c_cand)
                    if ns == nc or are_artists_equivalent(ns, nc):
                        has_pri_match = True
                        break
                    sw = ns.split()
                    cw = nc.split()
                    if len(sw) == 2 and len(cw) == 2 and sw[0] == cw[1] and sw[1] == cw[0]:
                        has_pri_match = True
                        break
                if has_pri_match:
                    break

            if not has_pri_match and candidate.artist_ids and hasattr(source, "artist_ids") and getattr(source, "artist_ids", None):
                if set(candidate.artist_ids) & set(getattr(source, "artist_ids", [])):
                    has_pri_match = True

            # If not matched directly, check if it's a valid collaboration match
            if not has_pri_match:
                for sc in det_c.collaborators:
                    n_collab = TextCleaner.normalize(sc)
                    for s_cand in pri_cands_s:
                        if TextCleaner.normalize(s_cand) == n_collab or are_artists_equivalent(TextCleaner.normalize(s_cand), n_collab):
                            has_pri_match = True
                            break
                    if has_pri_match:
                        break

            if not has_pri_match:
                norm_pri_c = TextCleaner.normalize(det_c.primary)
                norm_pri_s = TextCleaner.normalize(det_s.primary)
                norm_feat_s = [TextCleaner.normalize(f) for f in det_s.featured if f]
                norm_feat_c = [TextCleaner.normalize(f) for f in det_c.featured if f]
                is_guest_match = (norm_pri_c in norm_feat_s) or (norm_pri_s in norm_feat_c)
                if is_guest_match:
                    has_primary_artist_mismatch = True
                    conflicts.append("primary_artist_mismatch: 仅客串/合作艺人匹配，主表演者不符")
                    reasons.append("仅客串/合作艺人匹配，主表演者不符")
                elif artist_score < 0.40:
                    conflicts.append("artist_mismatch: 艺人明显不匹配")
                    reasons.append("艺人明显不匹配")

        # 2. ISRC Exact Match Check
        isrc_match = False
        isrc_conflict = False
        if source.isrc and candidate.isrc:
            s_isrc = source.isrc.strip().upper()
            c_isrc = candidate.isrc.strip().upper()
            if s_isrc and s_isrc == c_isrc:
                isrc_match = True
                matched_fields.append("isrc")
                # Missing artist conflict (R1/S01)
                if source.artists and not candidate.artists:
                    isrc_conflict = True
                    conflicts.append("isrc_missing_artist: ISRC相同但候选缺少艺人信息")
                    reasons.append("ISRC相同但候选缺少艺人信息")
                elif source.artists and candidate.artists and artist_score < 0.40:
                    isrc_conflict = True
                    conflicts.append("isrc_artist_conflict: ISRC相同但艺人明显不符")
                    reasons.append("ISRC相同但艺人明显不符")
                elif has_primary_artist_mismatch:
                    isrc_conflict = True
                    conflicts.append("isrc_artist_conflict: ISRC相同但主表演者不符")
                    reasons.append("ISRC相同但主表演者不符")

                # Title conflict (R1/S01)
                if title_score < 0.45:
                    isrc_conflict = True
                    conflicts.append("isrc_title_conflict: ISRC相同但歌名明显不符")
                    reasons.append("ISRC相同但歌名明显不符")

                # Version conflict (S03)
                if version_factor < 0:
                    isrc_conflict = True
                    conflicts.append("isrc_version_conflict: ISRC相同但版本不一致")
                    reasons.append("ISRC相同但版本不一致")

                if not isrc_conflict:
                    reasons.append("ISRC精确匹配")
                    if title_score >= 0.80:
                        matched_fields.append("title")
                    if artist_score >= 0.70:
                        matched_fields.append("artist")
                    if album_score >= 0.85:
                        matched_fields.append("album")
                    if source.duration_ms and candidate.duration_ms and abs(source.duration_ms - candidate.duration_ms) <= 3000:
                        matched_fields.append("duration")
                    if version_factor >= 0.0:
                        matched_fields.append("version")

                    evidence = MatchEvidence(
                        evidence_type="isrc",
                        verification_level=VerificationLevel.STRONG.value,
                        matched_fields=matched_fields,
                        conflicts=[],
                        provenance="isrc",
                        evidence_families=["isrc"],
                        rule_version=MATCH_RULE_VERSION,
                        query_policy_version=QUERY_POLICY_VERSION,
                        romanizer_version=ROMANIZER_VERSION,
                        exception_registry_version=EXCEPTION_REGISTRY_VERSION,
                        alias_version=ALIAS_VERSION,
                    )
                    return MatchCandidate(
                        track=candidate,
                        score=1.0,
                        title_score=round(title_score, 3),
                        artist_score=round(artist_score, 3),
                        album_score=round(album_score, 3),
                        duration_score=round(duration_factor, 3),
                        version_score=round(version_factor, 3),
                        confidence=ConfidenceLevel.EXACT,
                        decision=DecisionStatus.AUTO_ACCEPT.value,
                        decision_reasons=reasons,
                        evidence=evidence,
                    )
                else:
                    # S01/S03: ISRC match with conflict demoted to REVIEW
                    evidence = MatchEvidence(
                        evidence_type="isrc_conflict",
                        verification_level=VerificationLevel.CONFLICT.value,
                        matched_fields=["isrc"],
                        conflicts=conflicts,
                        provenance="isrc",
                        evidence_families=["isrc"],
                        rule_version=MATCH_RULE_VERSION,
                        query_policy_version=QUERY_POLICY_VERSION,
                        romanizer_version=ROMANIZER_VERSION,
                        exception_registry_version=EXCEPTION_REGISTRY_VERSION,
                        alias_version=ALIAS_VERSION,
                    )
                    cand_score = 0.65 if (title_score >= 0.50 or artist_score >= 0.50) else 0.40
                    return MatchCandidate(
                        track=candidate,
                        score=cand_score,
                        title_score=round(title_score, 3),
                        artist_score=round(artist_score, 3),
                        album_score=round(album_score, 3),
                        duration_score=round(duration_factor, 3),
                        version_score=round(version_factor, 3),
                        confidence=ConfidenceLevel.MEDIUM if cand_score >= 0.55 else ConfidenceLevel.LOW,
                        decision=DecisionStatus.REVIEW.value if cand_score >= 0.55 else DecisionStatus.NO_MATCH.value,
                        decision_reasons=reasons,
                        evidence=evidence,
                    )

        # 3. Strict Album Track Fingerprint Fallback (for unlisted translations only)
        # Triggers when artist is verified (>= 0.85), version is consistent, duration matches precisely (<= 2.5s),
        # and album matches (>= 0.85) OR candidate is a Single/EP.
        is_album_track_corroborated = False
        cand_album_is_single = False
        if candidate.album:
            ca_lower = candidate.album.lower()
            cand_album_is_single = "single" in ca_lower or "ep" in ca_lower

        album_ok_for_fallback = (album_score >= 0.85) or (cand_album_is_single and artist_score >= 0.85)

        if title_score < 0.45 and artist_score >= 0.85 and album_ok_for_fallback and version_factor >= 0.0:
            if source.duration_ms and candidate.duration_ms:
                diff_sec = abs(source.duration_ms - candidate.duration_ms) / 1000.0
                if diff_sec <= 2.5:
                    is_album_track_corroborated = True
                    # Set title_score to 0.60 for review (never >= 0.80, so title_ok is False and is_strong is False)
                    title_score = 0.60
                    reasons.append(f"同专辑/Single同艺人相同时长曲目({diff_sec:.1f}s误差)，疑似跨语种译名(待人工核对)")

        # 4. Dynamic Weighting & Metadata Adequacy
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

        # 5. Hard Guards (Orthogonal Double-Independence Verification)
        # Rule 1: Artist mismatch guard
        if source.artists and candidate.artists and artist_score < 0.35:
            composite *= 0.30
            reasons.append("艺人明显不匹配")
            conflicts.append("artist_mismatch: 艺人明显不匹配")

        # Rule 2: Title mismatch guard
        if title_score < 0.45 and not is_album_track_corroborated:
            composite *= 0.30
            reasons.append("歌名相似度过低")
            conflicts.append("title_mismatch: 歌名相似度过低")

        # Rule 3: Short title safety guard (e.g. "心海", "Lemon", "Stay", "Intro")
        clean_core, _ = TextCleaner.parse_title(source.title)
        cjk_chars = len(re.findall(r"[\u4e00-\u9fa5]", clean_core))
        is_short = (cjk_chars > 0 and len(clean_core) <= 2) or (cjk_chars == 0 and len(clean_core) <= 4)
        if is_short:
            if artist_score < 0.85:
                composite *= 0.30
                reasons.append("短歌名缺乏高置信艺人佐证")
                conflicts.append("short_title_low_artist: 短歌名缺乏高置信艺人佐证")

        composite = max(0.0, min(1.0, composite))

        # 5. Populate matched_fields & evidence
        if title_score >= 0.80:
            matched_fields.append("title")
        if artist_score >= 0.70:
            matched_fields.append("artist")
        if album_score >= 0.85:
            matched_fields.append("album")
        if has_duration and abs(source.duration_ms - candidate.duration_ms) <= 3000:
            matched_fields.append("duration")
        if version_factor >= 0.0:
            matched_fields.append("version")
        else:
            conflicts.append("version_conflict: 版本不一致")

        # Determine evidence families and verification level with de-correlation
        evidence_families: List[str] = []
        is_title_romanized = False
        is_artist_romanized = False

        norm_s_t = TextCleaner.normalize(TextCleaner.clean_title(source.title))
        norm_c_t = TextCleaner.normalize(TextCleaner.clean_title(candidate.title))

        # Check if title match is derived from romanization
        has_jp_title_s = bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fa5]", source.title))
        has_jp_title_c = bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fa5]", candidate.title))
        if has_jp_title_s != has_jp_title_c:
            s_title_romaji = TextCleaner.get_japanese_romaji_variants(source.title) or []
            c_title_romaji = TextCleaner.get_japanese_romaji_variants(candidate.title) or []
            s_title_clean = {re.sub(r"[^\w]", "", v.lower()) for v in s_title_romaji if v}
            c_title_clean = {re.sub(r"[^\w]", "", v.lower()) for v in c_title_romaji if v}
            cand_clean = re.sub(r"[^\w]", "", candidate.title.lower())
            src_clean = re.sub(r"[^\w]", "", source.title.lower())
            if (cand_clean and cand_clean in s_title_clean) or (src_clean and src_clean in c_title_clean) or (s_title_clean & c_title_clean):
                is_title_romanized = True

        if not is_title_romanized and norm_s_t != norm_c_t and not are_titles_equivalent(norm_s_t, norm_c_t, artist=pri_s_for_title):
            v1_list = TextCleaner.extract_title_variants(source.title)
            if source.trans_title:
                v1_list.append(source.trans_title)
            if source.aliases:
                v1_list.extend(source.aliases)
            v2_list = TextCleaner.extract_title_variants(candidate.title)
            direct_variant_match = any(
                TextCleaner.clean_title(v1) == TextCleaner.clean_title(v2)
                or _fast_sequence_ratio(TextCleaner.normalize(v1), TextCleaner.normalize(v2)) >= 0.85
                or are_titles_equivalent(v1, v2, artist=pri_s_for_title)
                or TextCleaner.match_katakana_loanword(v1, v2) >= 0.65
                for v1 in v1_list for v2 in v2_list
            )
            if not direct_variant_match:
                is_title_romanized = True

        pri_s, _ = TextCleaner.parse_artists(source.artists)
        pri_c, _ = TextCleaner.parse_artists(candidate.artists)
        norm_s_a = TextCleaner.normalize(pri_s)
        norm_c_a = TextCleaner.normalize(pri_c)

        # Check if artist match is derived from romanization
        has_jp_art_s = bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fa5]", pri_s))
        has_jp_art_c = bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fa5]", pri_c))
        if has_jp_art_s != has_jp_art_c:
            s_art_vars = TextCleaner.get_japanese_romaji_variants(norm_s_a, is_artist=True) or [norm_s_a]
            c_art_vars = TextCleaner.get_japanese_romaji_variants(norm_c_a, is_artist=True) or [norm_c_a]
            s_art_clean = {re.sub(r"[^\w]", "", v.lower()) for v in s_art_vars if v}
            c_art_clean = {re.sub(r"[^\w]", "", v.lower()) for v in c_art_vars if v}
            cand_art_clean = re.sub(r"[^\w]", "", norm_c_a.lower())
            src_art_clean = re.sub(r"[^\w]", "", norm_s_a.lower())
            if (cand_art_clean and cand_art_clean in s_art_clean) or (src_art_clean and src_art_clean in c_art_clean) or (s_art_clean & c_art_clean):
                is_artist_romanized = True
        elif norm_s_a and norm_c_a and norm_s_a != norm_c_a and not are_artists_equivalent(norm_s_a, norm_c_a):
            if not (are_artists_equivalent(norm_s_a, norm_c_a) or are_artists_equivalent(pri_s, pri_c)):
                s_art_vars = TextCleaner.get_japanese_romaji_variants(norm_s_a, is_artist=True) or [norm_s_a]
                c_art_vars = TextCleaner.get_japanese_romaji_variants(norm_c_a, is_artist=True) or [norm_c_a]
                if any(r_s == r_c for r_s in s_art_vars for r_c in c_art_vars):
                    is_artist_romanized = True

        if is_title_romanized and is_artist_romanized:
            # De-correlation: Both relied on romanizer transliteration -> Correlated! Only 1 family!
            evidence_families.append("romanizer_derived")
        else:
            if "title" in matched_fields:
                if is_title_romanized:
                    evidence_families.append("romanizer_derived")
                else:
                    evidence_families.append("text_title")
            if "artist" in matched_fields:
                if is_artist_romanized:
                    if "romanizer_derived" not in evidence_families:
                        evidence_families.append("romanizer_derived")
                else:
                    evidence_families.append("text_artist")

        if getattr(candidate, "discovery_path", None) in ("jp_equivalents", "apple_equivalent") or getattr(candidate, "is_equivalent_mapped", False):
            evidence_families.append("apple_equivalent")

        if "album" in matched_fields:
            evidence_families.append("album")
        if "duration" in matched_fields:
            evidence_families.append("duration")

        # Determine verification level
        if isrc_conflict:
            v_level = VerificationLevel.CONFLICT.value
            ev_type = "isrc_conflict"
        elif conflicts:
            v_level = VerificationLevel.CONFLICT.value
            ev_type = "conflict"
        elif "apple_equivalent" in evidence_families:
            v_level = VerificationLevel.STRONG.value
            ev_type = "apple_equivalent"
        elif "text_title" in evidence_families and "text_artist" in evidence_families:
            v_level = VerificationLevel.STRONG.value
            ev_type = "title_and_artist"
        elif not (is_title_romanized and is_artist_romanized) and "title" in matched_fields and "artist" in matched_fields:
            v_level = VerificationLevel.STRONG.value
            ev_type = "title_and_artist"
        elif len(set(evidence_families)) >= 2 and ("text_title" in evidence_families or "text_artist" in evidence_families) and ("duration" in evidence_families or "album" in evidence_families):
            v_level = VerificationLevel.STRONG.value
            ev_type = "independent_corroborated"
        elif evidence_families == ["romanizer_derived"]:
            # De-correlation rule: purely romanizer derived title and artist is only WEAK/MEDIUM, NEVER STRONG!
            v_level = VerificationLevel.MEDIUM.value
            ev_type = "romanizer_derived_unverified"
        elif "title" in matched_fields or "artist" in matched_fields:
            v_level = VerificationLevel.MEDIUM.value
            ev_type = "partial"
        else:
            v_level = VerificationLevel.WEAK.value
            ev_type = "weak"

        evidence = MatchEvidence(
            evidence_type=ev_type,
            verification_level=v_level,
            matched_fields=matched_fields,
            conflicts=conflicts,
            provenance=getattr(candidate, "discovery_path", "search") or "search",
            evidence_families=list(set(evidence_families)),
            rule_version=MATCH_RULE_VERSION,
            query_policy_version=QUERY_POLICY_VERSION,
            romanizer_version=ROMANIZER_VERSION,
            exception_registry_version=EXCEPTION_REGISTRY_VERSION,
            alias_version=ALIAS_VERSION,
        )

        # Initial confidence classification
        if is_album_track_corroborated:
            confidence = ConfidenceLevel.HIGH if (cand_album_is_single and composite >= 0.88) else ConfidenceLevel.MEDIUM
        elif composite >= 0.88:
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
            evidence=evidence,
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
            best.decision = DecisionStatus.NO_MATCH.value
            return None, ConfidenceLevel.NOT_FOUND, DecisionStatus.NO_MATCH.value, ["无达到及格分的候选"], score_gap

        # Check auto_accept requirements:
        # 1. Verification level is STRONG (two independent strong evidences or consistent ISRC)
        # 2. No conflicts
        # 3. Score reaches threshold
        # 4. Score gap >= min_score_gap (or top candidates are same song variant)
        # 5. Artist score >= 0.70 (if source has artists)
        # 6. Title score >= 0.80
        # 7. Version score >= 0.0
        has_artist = bool(source.artists)
        artist_ok = best.artist_score >= 0.70 if has_artist else False
        title_ok = best.title_score >= 0.80
        version_ok = best.version_score >= 0.0
        is_strong = bool(best.evidence and best.evidence.verification_level == VerificationLevel.STRONG.value)
        has_conflicts = bool(best.evidence and best.evidence.conflicts)

        # Check if second candidate is actually the same song variant (same title core & primary artist & consistent version)
        is_same_song_variant = False
        if second and second.score >= 0.65:
            sec_title_sim = cls.calculate_title_similarity(best.track.title, second.track.title)
            pri_b, _ = TextCleaner.parse_artists(best.track.artists)
            pri_sec, _ = TextCleaner.parse_artists(second.track.artists)
            norm_pri_b = TextCleaner.normalize(pri_b)
            norm_pri_sec = TextCleaner.normalize(pri_sec)
            same_pri_artist = (
                (norm_pri_b and norm_pri_sec and (norm_pri_b == norm_pri_sec or are_artists_equivalent(norm_pri_b, norm_pri_sec)))
                or (best.track.artist_ids and second.track.artist_ids and bool(set(best.track.artist_ids) & set(second.track.artist_ids)))
            )
            v_mod_between, _ = cls.calculate_version_consistency(best.track.title, second.track.title)
            same_version = (v_mod_between >= 0.0)

            # Both must have same core title, same primary artist, and NO version conflict (S06)
            if sec_title_sim >= 0.90 and same_pri_artist and same_version:
                is_same_song_variant = True

        gap_ok = True if is_same_song_variant else ((score_gap >= min_score_gap) if (second and second.score >= 0.65) else True)

        if (
            best.score >= auto_accept_threshold
            and is_strong
            and not has_conflicts
            and artist_ok
            and title_ok
            and gap_ok
            and version_ok
        ):
            reasons.append("独立双强证据校验完全通过，自动采纳")
            best.decision = DecisionStatus.AUTO_ACCEPT.value
            return best, ConfidenceLevel.EXACT if best.score >= 0.92 else ConfidenceLevel.HIGH, DecisionStatus.AUTO_ACCEPT.value, reasons, score_gap

        # If candidate is acceptable but has ambiguity or conflicts
        if has_conflicts:
            reasons.append(f"存在冲突项({', '.join(best.evidence.conflicts)})，转人工复核")
        elif not is_strong:
            reasons.append("缺乏两项独立强证据佐证，转人工复核")
        else:
            reasons.append("置信度较高但存在微小歧义或分差较小，建议人工复核")

        best.decision = DecisionStatus.REVIEW.value
        conf = ConfidenceLevel.HIGH if best.score >= 0.75 else ConfidenceLevel.MEDIUM
        return best, conf, DecisionStatus.REVIEW.value, reasons, score_gap
