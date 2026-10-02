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
from applemusic.matcher.cleaner import TextCleaner, NOISE_ARTISTS
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


from dataclasses import dataclass


@dataclass
class TitleComparisonResult:
    similarity: float
    method: str = "raw_ratio"
    matched_pair: Optional[Tuple[str, str]] = None
    is_transliteration: bool = False
    is_mismatch: bool = False
    is_unverified: bool = False
    details: Optional[str] = None


@lru_cache(maxsize=8192)
def _compare_single_title_pair(source_title: str, candidate_title: str, artist: Optional[str] = None) -> TitleComparisonResult:
    """Core cached comparison between two single titles returning structured TitleComparisonResult."""
    if not source_title or not candidate_title:
        return TitleComparisonResult(similarity=0.0, method="empty", is_mismatch=False)

    s1 = TextCleaner.normalize(source_title)
    s2 = TextCleaner.normalize(candidate_title)
    if not s1 or not s2:
        return TitleComparisonResult(similarity=0.0, method="empty", is_mismatch=False)

    if s1 == s2:
        return TitleComparisonResult(similarity=1.0, method="exact", matched_pair=(source_title, candidate_title))

    # Core cleaned versions (without version tags/noise)
    c1 = TextCleaner.normalize(TextCleaner.clean_title(source_title))
    c2 = TextCleaner.normalize(TextCleaner.clean_title(candidate_title))
    if c1 and c2 and c1 == c2:
        return TitleComparisonResult(similarity=0.98, method="normalized", matched_pair=(c1, c2))

    # Punctuation-stripped comparison
    p1 = re.sub(r"[^\w\u4e00-\u9fa5\u3040-\u30ff]", "", c1)
    p2 = re.sub(r"[^\w\u4e00-\u9fa5\u3040-\u30ff]", "", c2)
    if p1 and p2 and p1 == p2:
        return TitleComparisonResult(similarity=0.99, method="punctuation_stripped", matched_pair=(p1, p2))

    # Known cross-lingual title aliases (e.g. 夜に駆ける <-> Racing into the Night)
    if are_titles_equivalent(s1, s2, artist=artist) or are_titles_equivalent(c1, c2, artist=artist):
        s_ro = TextCleaner.get_japanese_romaji_variants(s1, is_artist=False) or []
        c_ro = TextCleaner.get_japanese_romaji_variants(s2, is_artist=False) or []
        s_ro_clean = {re.sub(r"[^\w]", "", v.lower()) for v in s_ro if v}
        c_ro_clean = {re.sub(r"[^\w]", "", v.lower()) for v in c_ro if v}
        cand_clean = re.sub(r"[^\w]", "", s2.lower())
        src_clean = re.sub(r"[^\w]", "", s1.lower())
        is_trans = bool(
            (cand_clean and cand_clean in s_ro_clean)
            or (src_clean and src_clean in c_ro_clean)
            or (bool(s_ro_clean & c_ro_clean))
        )
        return TitleComparisonResult(similarity=1.0, method="alias", matched_pair=(s1, s2), is_transliteration=is_trans)

    # Check extracted title variants (e.g. bracketed subtitles, translations, bilingual segments)
    v1_details = TextCleaner.extract_title_variant_details(source_title)
    v2_details = TextCleaner.extract_title_variant_details(candidate_title)
    v1_list = [v[0] for v in v1_details if not v[2]]
    v2_list = [v[0] for v in v2_details if not v[2]]

    best_variant_fuzzy: Optional[TitleComparisonResult] = None
    for v1, prov1, is_spec1 in v1_details:
        for v2, prov2, is_spec2 in v2_details:
            is_spec = is_spec1 or is_spec2
            if v1 == v2 or are_titles_equivalent(v1, v2, artist=artist):
                method = "bilingual_segment_exact" if is_spec else "variant_exact"
                return TitleComparisonResult(similarity=0.98, method=method, matched_pair=(v1, v2), is_mismatch=False, is_unverified=False)
            nv1 = TextCleaner.normalize(v1)
            nv2 = TextCleaner.normalize(v2)
            if nv1 == nv2 or are_titles_equivalent(nv1, nv2, artist=artist):
                method = "bilingual_segment_normalized" if is_spec else "variant_normalized"
                return TitleComparisonResult(similarity=0.98, method=method, matched_pair=(nv1, nv2), is_mismatch=False, is_unverified=False)
            p_nv1 = re.sub(r"[^\w\u4e00-\u9fa5\u3040-\u30ff]", "", nv1)
            p_nv2 = re.sub(r"[^\w\u4e00-\u9fa5\u3040-\u30ff]", "", nv2)
            if p_nv1 and p_nv2 and (p_nv1 == p_nv2 or are_titles_equivalent(p_nv1, p_nv2, artist=artist)):
                method = "bilingual_segment_normalized" if is_spec else "variant_punct_stripped"
                return TitleComparisonResult(similarity=0.98, method=method, matched_pair=(p_nv1, p_nv2), is_mismatch=False, is_unverified=False)

            if not is_spec:
                if len(nv1) >= 4 and len(nv2) >= 4:
                    v_ratio = _fast_sequence_ratio(nv1, nv2)
                    if v_ratio >= 0.85:
                        if not best_variant_fuzzy or max(v_ratio, 0.95) > best_variant_fuzzy.similarity:
                            best_variant_fuzzy = TitleComparisonResult(similarity=max(v_ratio, 0.95), method="variant_fuzzy", matched_pair=(nv1, nv2))
                if len(p_nv1) >= 4 and len(p_nv2) >= 4:
                    shorter_p, longer_p = (p_nv1, p_nv2) if len(p_nv1) <= len(p_nv2) else (p_nv2, p_nv1)
                    ratio_p = len(shorter_p) / max(1, len(longer_p))
                    if (p_nv1 in p_nv2 or p_nv2 in p_nv1) and ratio_p >= 0.65:
                        if not best_variant_fuzzy or 0.85 > best_variant_fuzzy.similarity:
                            best_variant_fuzzy = TitleComparisonResult(similarity=0.85, method="variant_containment", matched_pair=(p_nv1, p_nv2), details=f"variant containment ratio: {ratio_p:.2f}")
                    p_ratio = _fast_sequence_ratio(p_nv1, p_nv2)
                    if p_ratio >= 0.85 and (not best_variant_fuzzy or max(p_ratio, 0.90) > best_variant_fuzzy.similarity):
                        best_variant_fuzzy = TitleComparisonResult(similarity=max(p_ratio, 0.90), method="variant_fuzzy", matched_pair=(p_nv1, p_nv2), details=f"variant fuzzy ratio: {p_ratio:.2f}")

    if best_variant_fuzzy and best_variant_fuzzy.similarity >= 0.90:
        return best_variant_fuzzy

    # Japanese Kana/Kanji <-> Romaji / Latin comparison with multi-reading and morphological analysis
    from applemusic.matcher.title_aliases import KANJI_TO_ROMAJI_COMPOUNDS

    def has_jp(t: str) -> bool:
        return bool(
            re.search(r"[\u3040-\u30ff]", t)
            or any(k in t for k, _ in KANJI_TO_ROMAJI_COMPOUNDS)
        )

    def is_latin_text(t: str) -> bool:
        return bool(re.search(r"[a-zA-Z]", t) and not re.search(r"[\u4e00-\u9fa5\u3040-\u30ff]", t))

    raw_c1 = TextCleaner.clean_title(source_title)
    raw_c2 = TextCleaner.clean_title(candidate_title)
    all_s1 = [raw_c1, c1] + [v for v in v1_list if v and v not in (raw_c1, c1)]
    all_s2 = [raw_c2, c2] + [v for v in v2_list if v and v not in (raw_c2, c2)]

    best_romaji_res: Optional[TitleComparisonResult] = None

    for t1 in all_s1:
        for t2 in all_s2:
            jp1 = has_jp(t1)
            jp2 = has_jp(t2)
            lat1 = is_latin_text(t1)
            lat2 = is_latin_text(t2)

            if jp1 or jp2:
                v1_ro = TextCleaner.get_japanese_romaji_variants(t1, is_artist=False) if jp1 else ([t1] if lat1 else [])
                v2_ro = TextCleaner.get_japanese_romaji_variants(t2, is_artist=False) if jp2 else ([t2] if lat2 else [])

                if v1_ro and v2_ro:
                    for r1 in v1_ro:
                        for r2 in v2_ro:
                            if r1 == r2 or are_titles_equivalent(r1, r2, artist=artist):
                                return TitleComparisonResult(similarity=0.98, method="romaji_exact", matched_pair=(r1, r2), is_transliteration=True)

                            rp1 = re.sub(r"[^\w]", "", r1).lower()
                            rp2 = re.sub(r"[^\w]", "", r2).lower()
                            if rp1 and rp2:
                                if rp1 == rp2 or are_titles_equivalent(rp1, rp2, artist=artist):
                                    if len(rp1) >= 3:
                                        return TitleComparisonResult(similarity=0.98, method="romaji_exact", matched_pair=(r1, r2), is_transliteration=True)

                                r1_norm = re.sub(r"\bwo\b", "o", r1).replace("ou", "o").replace("uu", "u").replace("oo", "o")
                                r2_norm = re.sub(r"\bwo\b", "o", r2).replace("ou", "o").replace("uu", "u").replace("oo", "o")
                                rp1_norm = re.sub(r"[^\w]", "", r1_norm).lower()
                                rp2_norm = re.sub(r"[^\w]", "", r2_norm).lower()
                                if rp1_norm and rp1_norm == rp2_norm and len(rp1_norm) >= 3:
                                    if rp1 == rp2:
                                        return TitleComparisonResult(similarity=0.98, method="romaji_exact", matched_pair=(r1, r2), is_transliteration=True)
                                    else:
                                        cur_folded = TitleComparisonResult(
                                            similarity=0.92,
                                            method="romaji_long_vowel_folded",
                                            matched_pair=(r1, r2),
                                            is_transliteration=True,
                                            details=f"folded: '{r1}' vs '{r2}'",
                                        )
                                        if not best_romaji_res or cur_folded.similarity > best_romaji_res.similarity:
                                            best_romaji_res = cur_folded

                                if len(rp1) >= 4 and len(rp2) >= 4:
                                    ratio = _fast_sequence_ratio(r1, r2)
                                    if ratio >= 0.85:
                                        cur_fuzzy = TitleComparisonResult(
                                            similarity=max(ratio, 0.88),
                                            method="romaji_fuzzy",
                                            matched_pair=(r1, r2),
                                            is_transliteration=True,
                                            details=f"fuzzy ratio: {ratio:.3f}",
                                        )
                                        if not best_romaji_res or cur_fuzzy.similarity > best_romaji_res.similarity:
                                            best_romaji_res = cur_fuzzy

    if best_romaji_res and best_romaji_res.similarity >= 0.85:
        return best_romaji_res

    # Katakana loanword phonetic alignment (e.g. ミラージュ <-> mirage)
    loan_sim = TextCleaner.match_katakana_loanword(c1, c2)
    if loan_sim >= 0.85:
        return TitleComparisonResult(similarity=0.98, method="katakana_loanword", matched_pair=(c1, c2), is_transliteration=True)

    for v1 in v1_list:
        for v2 in v2_list:
            v_loan = TextCleaner.match_katakana_loanword(v1, v2)
            if v_loan >= 0.85:
                return TitleComparisonResult(similarity=0.98, method="katakana_loanword", matched_pair=(v1, v2), is_transliteration=True)

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
    contain_detected = False
    if c1 and c2:
        shorter, longer = (c1, c2) if len(c1) <= len(c2) else (c2, c1)
        ratio = len(shorter) / max(1, len(longer))
        if longer.startswith(shorter):
            if ratio >= 0.65:
                contain_bonus = max(contain_bonus, 0.80)
                contain_detected = True
        elif shorter in longer and ratio >= 0.75:
            contain_bonus = max(contain_bonus, 0.75)
            contain_detected = True

    if clean_sim < 0.40:
        base_sim = clean_sim
    else:
        base_sim = max(raw_sim, clean_sim)

    final_sim = max(base_sim, contain_bonus)

    # Determine if this is a confirmed title_mismatch or title_unverified
    is_mismatch = False
    is_unverified = False
    lat1 = is_latin_text(c1)
    lat2 = is_latin_text(c2)
    has_cjk1 = bool(re.search(r"[\u4e00-\u9fa5\u3040-\u30ff]", c1))
    has_cjk2 = bool(re.search(r"[\u4e00-\u9fa5\u3040-\u30ff]", c2))
    jp_involved = has_jp(raw_c1) or has_jp(raw_c2) or has_jp(c1) or has_jp(c2)

    method_used = "containment" if (contain_detected or contain_bonus > base_sim) else "raw_ratio"
    if (lat1 and lat2 and final_sim < 0.80) or ((has_cjk1 and has_cjk2) and final_sim < 0.70):
        is_mismatch = True
    elif ((has_cjk1 != has_cjk2) or (lat1 != lat2) or jp_involved) and final_sim < 0.60:
        is_unverified = True
    elif final_sim < 0.45:
        if (lat1 and lat2) or (has_cjk1 and has_cjk2):
            is_mismatch = True
        else:
            is_unverified = True

    return TitleComparisonResult(
        similarity=final_sim,
        method=method_used,
        matched_pair=(c1, c2),
        is_transliteration=False,
        is_mismatch=is_mismatch,
        is_unverified=is_unverified,
    )


def _calculate_title_similarity_cached(source_title: str, candidate_title: str, artist: Optional[str] = None) -> float:
    """Core cached implementation of title similarity."""
    return _compare_single_title_pair(source_title, candidate_title, artist=artist).similarity


class TrackScorer:
    """Calculates match confidence scores and decision statuses for Apple Music candidates."""

    @classmethod
    def compare_titles(
        cls,
        source_title: str,
        candidate_title: str,
        trans_title: Optional[str] = None,
        aliases: Optional[List[str]] = None,
        artist: Optional[str] = None,
    ) -> TitleComparisonResult:
        """
        Compare core titles, official translation (trans_title), and alternate aliases.
        Returns the structured TitleComparisonResult of the highest similarity match.
        """
        res = _compare_single_title_pair(source_title, candidate_title, artist=artist)
        if res.similarity >= 0.95:
            return res

        if trans_title:
            t_res = _compare_single_title_pair(trans_title, candidate_title, artist=artist)
            if t_res.similarity > res.similarity:
                res = t_res
            if res.similarity >= 0.95:
                return res

        if aliases:
            for a in aliases:
                if a and a.strip():
                    a_res = _compare_single_title_pair(a.strip(), candidate_title, artist=artist)
                    if a_res.similarity > res.similarity:
                        res = a_res
                    if res.similarity >= 0.95:
                        return res

        if res.similarity >= 0.80:
            res.is_mismatch = False
            res.is_unverified = False

        return res

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
        return cls.compare_titles(
            source_title,
            candidate_title,
            trans_title=trans_title,
            aliases=aliases,
            artist=artist,
        ).similarity

    @classmethod
    def calculate_artist_similarity(
        cls,
        source_artists: List[str],
        candidate_artists: List[str],
        source_title: Optional[str] = None,
        candidate_title: Optional[str] = None,
    ) -> float:
        """
        Calculate similarity between artist lists.
        Avoids false neutral score (0.60) when artists are missing.
        Supports cross-lingual alias equivalence (e.g. 周华健 <-> Emil Wakin Chau).
        Supports project credit matching (e.g. HoYoFair with Lilas Ikuta title credit).
        """
        if not source_artists or not candidate_artists:
            return 0.0

        s_title_credits: List[str] = []
        if source_title:
            s_title_credits = TextCleaner.parse_title_details(source_title).title_credits
        c_title_credits: List[str] = []
        if candidate_title:
            c_title_credits = TextCleaner.parse_title_details(candidate_title).title_credits

        det_s = TextCleaner.parse_artist_details(source_artists, title_credits=s_title_credits)
        det_c = TextCleaner.parse_artist_details(candidate_artists, title_credits=c_title_credits)

        norm_pri_s = TextCleaner.normalize(det_s.primary)
        norm_pri_c = TextCleaner.normalize(det_c.primary)

        # 0. Collaborative sets equivalence under aliases (order-independent, e.g. "魏晨、Nea、HOYO-MiX" vs "HOYO-MiX、魏晨、Nea")
        entities_s = [n for n in ([det_s.primary] + det_s.collaborators if det_s.primary else []) if n and n.lower() not in NOISE_ARTISTS]
        entities_c = [n for n in ([det_c.primary] + det_c.collaborators if det_c.primary else []) if n and n.lower() not in NOISE_ARTISTS]
        if entities_s and entities_c and len(entities_s) == len(entities_c):
            matched_indices = set()
            for s_name in entities_s:
                for idx, c_name in enumerate(entities_c):
                    if idx not in matched_indices and (
                        TextCleaner.normalize(s_name) == TextCleaner.normalize(c_name)
                        or are_artists_equivalent(s_name, c_name)
                    ):
                        matched_indices.add(idx)
                        break
            if len(matched_indices) == len(entities_s):
                return 1.0

        raw_names_s = [n for n in det_s.all_names if n and n.lower() not in NOISE_ARTISTS]
        raw_names_c = [n for n in det_c.all_names if n and n.lower() not in NOISE_ARTISTS]
        if raw_names_s and raw_names_c and len(raw_names_s) == len(raw_names_c):
            matched_indices = set()
            for s_name in raw_names_s:
                for idx, c_name in enumerate(raw_names_c):
                    if idx not in matched_indices and (
                        TextCleaner.normalize(s_name) == TextCleaner.normalize(c_name)
                        or are_artists_equivalent(s_name, c_name)
                    ):
                        matched_indices.add(idx)
                        break
            if len(matched_indices) == len(raw_names_s):
                return 1.0

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
                if ns == nc or are_artists_equivalent(ns, nc) or are_artists_equivalent(s_cand, c_cand):
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
                diff = longer.replace(shorter, "").strip()
                has_substantive_diff = bool(
                    re.search(r"[\u4e00-\u9fa5\u3040-\u30ff]", diff)
                    or any(w not in {"the", "band", "group"} for w in re.findall(r"[a-z]{3,}", diff.lower()))
                )
                if has_substantive_diff or short_cjk <= 2 or len(shorter) <= 3:
                    pri_score = min(0.50, _fast_sequence_ratio(norm_pri_s, norm_pri_c)) if has_substantive_diff else _fast_sequence_ratio(norm_pri_s, norm_pri_c)
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

        # 3. Project Credit Match (Recognized Publisher/Brand + Title Credited Performer)
        if det_c.publisher_or_project and det_c.title_credits:
            for tc in det_c.title_credits:
                for s_cand in pri_cands_s:
                    if are_artists_equivalent(s_cand, tc) or TextCleaner.normalize(s_cand) == TextCleaner.normalize(tc):
                        return 0.92
        if det_s.publisher_or_project and det_s.title_credits:
            for tc in det_s.title_credits:
                for c_cand in pri_cands_c:
                    if are_artists_equivalent(c_cand, tc) or TextCleaner.normalize(c_cand) == TextCleaner.normalize(tc):
                        return 0.92

        # 4. Explicit Guest / Featured Inversion Check (R2 / S04)
        # One side's primary artist matches the other side's featured artist, but primary is missing
        is_guest_inversion = False
        norm_feat_s = [TextCleaner.normalize(f) for f in det_s.featured if f]
        norm_feat_c = [TextCleaner.normalize(f) for f in det_c.featured if f]
        norm_tc_s = [TextCleaner.normalize(tc) for tc in det_s.title_credits if tc]
        norm_tc_c = [TextCleaner.normalize(tc) for tc in det_c.title_credits if tc]

        for fs in norm_feat_s + norm_tc_s:
            if fs == norm_pri_c or are_artists_equivalent(fs, norm_pri_c):
                is_guest_inversion = True
                break
        if not is_guest_inversion:
            for fc in norm_feat_c + norm_tc_c:
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
        td_src = TextCleaner.parse_title_details(source_title)
        td_cand = TextCleaner.parse_title_details(candidate_title)
        v_src = list(td_src.version_tags)
        v_cand = list(td_cand.version_tags)

        if candidate_album:
            alb_lower = candidate_album.lower()
            if "live" in alb_lower and "live" not in v_cand:
                v_cand.append("live")

        v_src_set = set(v_src)
        v_cand_set = set(v_cand)

        critical_tags = {
            "live", "remix", "instrumental", "acoustic", "demo", "cover",
            "piano", "guitar", "orchestral", "sped_up", "slowed",
            "tv_size", "alternate_cut",
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

        # Symmetrical language version check
        src_lang = td_src.language_version
        cand_lang = td_cand.language_version
        if src_lang and cand_lang:
            if src_lang == cand_lang:
                score_mod += 0.10
                reasons.append(f"语言版本吻合: [{src_lang}_ver]")
            else:
                score_mod -= 0.50
                reasons.append(f"语言版本冲突: 来源为[{src_lang}]，候选为[{cand_lang}]")
        elif (src_lang is not None) ^ (cand_lang is not None):
            known_lang = cand_lang or src_lang
            reasons.append(f"候选曲目为特定语言版本[{known_lang}]，来源语言未标明，待人工核对")

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
        t_sim = cls.calculate_title_similarity(source_album, candidate_album)
        if t_sim >= 0.85:
            return t_sim
        return _fast_sequence_ratio(a1, a2)

    @classmethod
    def score(cls, source: Track, candidate: AppleMusicTrack) -> MatchCandidate:
        """Compute composite score, evidence, and return MatchCandidate."""
        reasons: List[str] = []
        conflicts: List[str] = []
        matched_fields: List[str] = []

        # 1. Feature similarities
        pri_s_for_title = source.artists[0] if source.artists else None
        title_comp = cls.compare_titles(
            source.title,
            candidate.title,
            trans_title=getattr(source, "trans_title", None),
            aliases=getattr(source, "aliases", None),
            artist=pri_s_for_title,
        )
        title_score = title_comp.similarity
        version_factor, v_reasons = cls.calculate_version_consistency(
            source.title, candidate.title, candidate.album
        )
        reasons.extend(v_reasons)

        td_s = TextCleaner.parse_title_details(source.title)
        td_c = TextCleaner.parse_title_details(candidate.title)
        s_lang = td_s.language_version
        c_lang = td_c.language_version
        language_version_unverified = bool((s_lang is not None) ^ (c_lang is not None))
        active_lang = c_lang or s_lang

        artist_score = cls.calculate_artist_similarity(
            source.artists, candidate.artists, source_title=source.title, candidate_title=candidate.title
        )
        orig_jp = getattr(candidate, "original_jp_track", None)
        if orig_jp and getattr(orig_jp, "artists", None):
            jp_art_sim = cls.calculate_artist_similarity(
                source.artists, orig_jp.artists, source_title=source.title, candidate_title=getattr(orig_jp, "title", None)
            )
            artist_score = max(artist_score, jp_art_sim)
        elif getattr(candidate, "discovery_path", None) in ("jp_equivalents", "apple_equivalent") or getattr(candidate, "is_equivalent_mapped", False):
            if title_score >= 0.80 and version_factor >= 0.0:
                artist_score = max(artist_score, 0.90)
        duration_factor = cls.calculate_duration_factor(source.duration_ms, candidate.duration_ms)
        album_score = cls.calculate_album_similarity(source.album, candidate.album)

        # Check primary artist mismatch (R2 / S04) and credit roles
        det_s = TextCleaner.parse_artist_details(source.artists, title_credits=td_s.title_credits)
        det_c = TextCleaner.parse_artist_details(candidate.artists, title_credits=td_c.title_credits)
        has_primary_artist_mismatch = False
        project_credit_matched = False
        matched_credit_role = None

        if det_s.primary and det_c.primary:
            # Check if primary performers match (via direct, alias, CV, or Apple artist ID)
            pri_cands_s = [det_s.primary] + det_s.aliases + det_s.character_voices
            pri_cands_c = [det_c.primary] + det_c.aliases + det_c.character_voices
            has_pri_match = False
            for s_cand in pri_cands_s:
                ns = TextCleaner.normalize(s_cand)
                for c_cand in pri_cands_c:
                    nc = TextCleaner.normalize(c_cand)
                    if ns == nc or are_artists_equivalent(ns, nc) or are_artists_equivalent(s_cand, c_cand):
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

            # If not matched directly, check if collaborative sets are equivalent under aliases (order-independent)
            if not has_pri_match:
                raw_names_s = [n for n in det_s.all_names if n and n.lower() not in NOISE_ARTISTS]
                raw_names_c = [n for n in det_c.all_names if n and n.lower() not in NOISE_ARTISTS]
                if raw_names_s and raw_names_c and len(raw_names_s) == len(raw_names_c):
                    matched_indices = set()
                    for s_name in raw_names_s:
                        for idx, c_name in enumerate(raw_names_c):
                            if idx not in matched_indices and (
                                TextCleaner.normalize(s_name) == TextCleaner.normalize(c_name)
                                or are_artists_equivalent(s_name, c_name)
                            ):
                                matched_indices.add(idx)
                                break
                    if len(matched_indices) == len(raw_names_s):
                        has_pri_match = True
                        reasons.append("合作艺人集合一致（署名顺序调整）")

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

            # Check project credit match (Candidate primary is publisher/project and title credit matches source performer)
            if not has_pri_match and det_c.publisher_or_project and det_c.title_credits:
                for tc in det_c.title_credits:
                    for s_cand in pri_cands_s:
                        if are_artists_equivalent(s_cand, tc) or TextCleaner.normalize(s_cand) == TextCleaner.normalize(tc):
                            project_credit_matched = True
                            matched_credit_role = "vocalist"
                            has_pri_match = True
                            reasons.append("演唱者命中，主署名关系待核验")
                            break
                    if project_credit_matched:
                        break

            if not has_pri_match and det_s.publisher_or_project and det_s.title_credits:
                for tc in det_s.title_credits:
                    for c_cand in pri_cands_c:
                        if are_artists_equivalent(c_cand, tc) or TextCleaner.normalize(c_cand) == TextCleaner.normalize(tc):
                            project_credit_matched = True
                            matched_credit_role = "vocalist"
                            has_pri_match = True
                            reasons.append("演唱者命中，主署名关系待核验")
                            break
                    if project_credit_matched:
                        break

            if not has_pri_match:
                norm_pri_c = TextCleaner.normalize(det_c.primary)
                norm_pri_s = TextCleaner.normalize(det_s.primary)
                norm_feat_s = [TextCleaner.normalize(f) for f in det_s.featured if f]
                norm_feat_c = [TextCleaner.normalize(f) for f in det_c.featured if f]
                norm_credits_c = [TextCleaner.normalize(tc) for tc in det_c.title_credits if tc]
                norm_credits_s = [TextCleaner.normalize(tc) for tc in det_s.title_credits if tc]
                is_guest_match = (
                    (norm_pri_c in norm_feat_s or norm_pri_c in norm_credits_s)
                    or (norm_pri_s in norm_feat_c or norm_pri_s in norm_credits_c)
                )
                if is_guest_match:
                    has_primary_artist_mismatch = True
                    conflicts.append("primary_artist_mismatch: 仅客串/合作艺人匹配，主表演者不符")
                    reasons.append("仅客串/合作艺人匹配，主表演者不符")
                elif artist_score < 0.40:
                    conflicts.append("artist_mismatch: 艺人明显不匹配")
                    reasons.append("艺人明显不匹配")
                elif artist_score < 0.70:
                    has_primary_artist_mismatch = True
                    conflicts.append("primary_artist_mismatch: 主表演者不符")
                    reasons.append("主表演者不符")

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

        # 4. Hard Guards (Orthogonal Double-Independence Verification)
        # Rule 1: Artist mismatch guard
        if source.artists and candidate.artists and artist_score < 0.35:
            composite *= 0.30
            reasons.append("艺人明显不匹配")
            conflicts.append("artist_mismatch: 艺人明显不匹配")

        # Rule 2: Title mismatch & unverified guard
        is_title_mismatch = bool(getattr(title_comp, "is_mismatch", False))
        is_title_unverified = bool(getattr(title_comp, "is_unverified", False))

        if is_title_mismatch:
            composite *= 0.30
            reasons.append("歌名明显不匹配")
            if not any("title_mismatch" in c for c in conflicts):
                conflicts.append("title_mismatch: 歌名明显不匹配")
            composite = min(composite, 0.39)
        elif is_title_unverified:
            composite *= 0.30
            reasons.append("跨语言歌名未核验")
            if not any("title_unverified" in c for c in conflicts):
                conflicts.append("title_unverified: 跨语言歌名未核验")
            composite = min(composite, 0.39)
        elif not source.title or not candidate.title:
            composite *= 0.30
            reasons.append("缺少歌名信息")
            if not any("title_missing" in c for c in conflicts):
                conflicts.append("title_missing: 缺少歌名信息")
            composite = min(composite, 0.39)
        elif title_score < 0.45:
            composite *= 0.30
            reasons.append("歌名相似度过低")
            if not any("title_mismatch" in c for c in conflicts):
                conflicts.append("title_mismatch: 歌名相似度过低")
            composite = min(composite, 0.39)

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

        # Check if title match is derived from transliteration
        is_title_romanized = bool(title_comp and title_comp.is_transliteration)

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

        # Determine verification level
        DEFINITE_TITLE_METHODS = {
            "exact",
            "normalized",
            "punctuation_stripped",
            "alias",
            "variant_exact",
            "variant_normalized",
            "variant_punct_stripped",
            "romaji_exact",
        }
        is_title_definite = bool(title_comp and title_comp.method in DEFINITE_TITLE_METHODS)

        is_orig_jp_definite = False
        orig_jp = getattr(candidate, "original_jp_track", None)
        if orig_jp and getattr(orig_jp, "title", None):
            orig_comp = cls.compare_titles(
                source.title, orig_jp.title, trans_title=source.trans_title, aliases=source.aliases, artist=pri_s_for_title
            )
            if orig_comp and orig_comp.method in DEFINITE_TITLE_METHODS:
                is_orig_jp_definite = True

        # Verified catalog equivalent requires either definite title on candidate, or verified mapping from definite original JP track.
        # Discovery path string or artist ID alone cannot bypass non-definite title identity protection.
        is_verified_equivalent = False
        if getattr(candidate, "is_equivalent_mapped", False):
            if is_title_definite or is_orig_jp_definite:
                is_verified_equivalent = True
        elif is_title_definite and getattr(candidate, "discovery_path", None) in ("jp_equivalents", "apple_equivalent"):
            is_verified_equivalent = True

        if is_verified_equivalent:
            evidence_families.append("apple_equivalent")

        if "album" in matched_fields:
            evidence_families.append("album")
        if "duration" in matched_fields:
            evidence_families.append("duration")

        if isrc_conflict:
            v_level = VerificationLevel.CONFLICT.value
            ev_type = "isrc_conflict"
        elif conflicts:
            v_level = VerificationLevel.CONFLICT.value
            ev_type = "conflict"
        elif is_verified_equivalent:
            v_level = VerificationLevel.STRONG.value
            ev_type = "apple_equivalent"
        elif not is_title_definite:
            # Hard identity protection: non-definite title methods (prefix containment, edit fuzzy,
            # long-vowel folding, multi-reading approximation, bilingual segments) cannot become STRONG via ordinary metadata (artist+duration/album).
            # They strictly retain their ambiguity and remain at most MEDIUM / REVIEW.
            v_level = VerificationLevel.MEDIUM.value
            if title_comp and title_comp.method in ("romaji_fuzzy", "variant_fuzzy"):
                ev_type = "romaji_fuzzy" if title_comp.method == "romaji_fuzzy" else "variant_fuzzy"
            elif title_comp and title_comp.method in ("bilingual_segment_exact", "bilingual_segment_normalized"):
                ev_type = "bilingual_segment"
            elif title_comp and title_comp.method in ("containment", "variant_containment"):
                ev_type = "containment"
            elif title_comp and title_comp.method == "romaji_long_vowel_folded":
                ev_type = "romaji_long_vowel_folded"
            elif "title" in matched_fields or "artist" in matched_fields:
                ev_type = "partial"
            else:
                v_level = VerificationLevel.WEAK.value
                ev_type = "weak"
        elif is_title_definite and "text_title" in evidence_families and "text_artist" in evidence_families:
            v_level = VerificationLevel.STRONG.value
            ev_type = "title_and_artist"
        elif is_title_definite and not (is_title_romanized and is_artist_romanized) and "title" in matched_fields and "artist" in matched_fields:
            v_level = VerificationLevel.STRONG.value
            ev_type = "title_and_artist"
        elif is_title_definite and len(set(evidence_families)) >= 2 and ("text_title" in evidence_families or "text_artist" in evidence_families or ("romanizer_derived" in evidence_families and "album" in evidence_families)) and ("duration" in evidence_families or "album" in evidence_families):
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

        if project_credit_matched and not conflicts:
            v_level = VerificationLevel.MEDIUM.value
            ev_type = "project_credit_match"

        if language_version_unverified and not conflicts:
            v_level = VerificationLevel.MEDIUM.value
            if ev_type in ("title_and_artist", "independent_corroborated"):
                ev_type = "language_version_unverified"

        evidence = MatchEvidence(
            evidence_type=ev_type,
            verification_level=v_level,
            matched_fields=matched_fields,
            conflicts=conflicts,
            provenance=getattr(candidate, "discovery_path", "search") or "search",
            evidence_families=list(set(evidence_families)),
            title_comparison_method=title_comp.method if title_comp else None,
            matched_title_pair=list(title_comp.matched_pair) if (title_comp and title_comp.matched_pair) else None,
            title_details={"method": title_comp.method, "details": title_comp.details} if title_comp else None,
            language_version=active_lang,
            source_language_version=s_lang,
            candidate_language_version=c_lang,
            title_credits=det_c.title_credits,
            matched_credit_role=matched_credit_role,
            project_credit_matched=project_credit_matched,
            rule_version=MATCH_RULE_VERSION,
            query_policy_version=QUERY_POLICY_VERSION,
            romanizer_version=ROMANIZER_VERSION,
            exception_registry_version=EXCEPTION_REGISTRY_VERSION,
            alias_version=ALIAS_VERSION,
        )

        # Initial confidence & decision classification
        has_critical_conflict = any(
            any(k in c for k in ("title_mismatch", "title_unverified", "artist_mismatch", "isrc_conflict"))
            for c in conflicts
        )
        if has_critical_conflict or composite < 0.55:
            confidence = ConfidenceLevel.LOW
            cand_decision = DecisionStatus.NO_MATCH.value
        elif composite >= 0.88:
            confidence = ConfidenceLevel.EXACT
            cand_decision = DecisionStatus.REVIEW.value
        elif composite >= 0.72:
            confidence = ConfidenceLevel.HIGH
            cand_decision = DecisionStatus.REVIEW.value
        else:
            confidence = ConfidenceLevel.MEDIUM
            cand_decision = DecisionStatus.REVIEW.value

        return MatchCandidate(
            track=candidate,
            score=round(composite, 3),
            title_score=round(title_score, 3),
            artist_score=round(artist_score, 3),
            album_score=round(album_score, 3),
            duration_score=round(duration_factor, 3),
            version_score=round(version_factor, 3),
            confidence=confidence,
            decision=cand_decision,
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

        has_title_mismatch = bool(best.evidence and any("title_mismatch" in c for c in best.evidence.conflicts))
        has_title_unverified = bool(best.evidence and any("title_unverified" in c for c in best.evidence.conflicts))
        has_title_missing = bool(best.evidence and any("title_missing" in c for c in best.evidence.conflicts))
        if best.score < min_review_score or has_title_mismatch or has_title_unverified or has_title_missing:
            best.decision = DecisionStatus.NO_MATCH.value
            if has_title_mismatch:
                reject_reason = "候选曲目歌名明显不符"
            elif has_title_unverified:
                reject_reason = "候选曲目歌名未核验"
            elif has_title_missing:
                reject_reason = "候选曲目缺少歌名信息"
            else:
                reject_reason = "无达到及格分的候选"
            return None, ConfidenceLevel.NOT_FOUND, DecisionStatus.NO_MATCH.value, reasons + [reject_reason], score_gap

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
            if best.evidence and best.evidence.evidence_type == "romanizer_derived_unverified":
                reasons.append("标题/艺人转写吻合，纯转写依赖待人工核对身份")
            elif best.evidence and best.evidence.evidence_type in ("romaji_fuzzy", "variant_fuzzy"):
                reasons.append("歌名存在拼写或读音差异，待人工核对")
            elif best.evidence and best.evidence.evidence_type in ("containment", "variant_containment"):
                reasons.append("歌名仅前缀或片段包含，待人工核对")
            elif best.evidence and best.evidence.evidence_type == "romaji_long_vowel_folded":
                reasons.append("歌名存在长音或读音折叠差异，待人工核对")
            elif best.evidence and best.evidence.evidence_type == "bilingual_segment":
                reasons.append("歌名通过双语推测切段匹配，待人工核对身份")
            elif best.evidence and best.evidence.evidence_type == "project_credit_match":
                reasons.append("演唱者命中，主署名关系待核验")
            elif best.evidence and best.evidence.evidence_type == "language_version_unverified":
                reasons.append("候选曲目为特定语言版本，来源语言未标明，待人工核对")
            else:
                reasons.append("缺乏两项独立强证据佐证，转人工复核")
        else:
            reasons.append("置信度较高但存在微小歧义或分差较小，建议人工复核")

        best.decision = DecisionStatus.REVIEW.value
        conf = ConfidenceLevel.HIGH if best.score >= 0.75 else ConfidenceLevel.MEDIUM
        return best, conf, DecisionStatus.REVIEW.value, reasons, score_gap
