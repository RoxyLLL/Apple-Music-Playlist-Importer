"""
Search query planning and budget enforcement for Apple Music matching.
Ensures predictable, ordered queries with provenance tracking and hard budget limits across phases:
Phase A: Native Target Storefront
Phase B: Universal Multi-Script Variants (beam selection, max 4)
Phase C: Apple Hints / Suggestions (token-aligned, max 2)
Phase D: Cross-Storefront JP Discovery (max 2)
"""

import re
from typing import Dict, List, Optional, Set, Tuple

from applemusic.matcher.cleaner import TextCleaner
from applemusic.matcher.japanese_normalizer import JapaneseNormalizer, TextVariant
from applemusic.matcher.query_models import PlannedQuery, QueryContext
from applemusic.matcher.title_aliases import get_scoped_title_aliases
from applemusic.matcher.artist_aliases import get_artist_aliases
from applemusic.models import Track


class QueryPlanner:
    """
    Multi-phase query planner for Apple Music catalog matching.
    Enforces strict query budgets and prevents combinatorial explosion.
    """
    FIRST_ROUND_BUDGET = 2
    MAX_CATALOG_BUDGET = 8
    MAX_SUGGESTION_BUDGET = 2
    MAX_JP_DISCOVERY_BUDGET = 2
    RETRY_PER_STOREFRONT_BUDGET = 3
    RETRY_TOTAL_BUDGET = 6

    @classmethod
    def plan_deep_retry(cls, source: Track, max_budget: int = 6) -> List[PlannedQuery]:
        """Deep retry queries: up to max_budget."""
        all_q = cls.plan_queries(source)
        return all_q[:max_budget]

    @classmethod
    def clean_query_text(cls, text: str) -> str:
        """Strip brackets, book marks, and excess spaces for Apple Music search."""
        if not text:
            return ""
        clean = re.sub(r"[()（）【】\[\]《》「」『』\"]", " ", text)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean

    @classmethod
    def filter_suggestions(
        cls,
        suggestions: List[str],
        title: str,
        artist: str,
        max_count: int = 2,
    ) -> List[str]:
        """
        Keep only suggestions that have at least one significant token
        matching title or artist (or their Romanized forms).
        """
        def get_tokens(text: str) -> Set[str]:
            if not text:
                return set()
            cleaned = cls.clean_query_text(text).lower()
            words = set(re.findall(r"\w{2,}", cleaned))
            return words

        valid_tokens = get_tokens(title) | get_tokens(artist)
        for v in JapaneseNormalizer.generate_variants(title) + JapaneseNormalizer.generate_variants(artist, is_artist=True):
            valid_tokens |= get_tokens(v.text)

        matched_suggestions: List[str] = []
        for s in suggestions:
            s_tokens = get_tokens(s)
            if s_tokens & valid_tokens:
                matched_suggestions.append(s)
                if len(matched_suggestions) >= max_count:
                    break

        return matched_suggestions

    @classmethod
    def plan_phases(cls, ctx: QueryContext) -> Dict[str, List[PlannedQuery]]:
        """
        Plan queries grouped by phase:
        - 'A_native': Native target storefront queries (core title + primary artist, bracket variants, scoped aliases)
        - 'B_script': Universal multi-script variants (beam selection, max 4)
        - 'C_suggestions': Apple suggestions (filled dynamically during engine execution)
        - 'D_jp_discovery': Cross-storefront JP discovery queries (max 2)
        """
        core_t, _ = TextCleaner.parse_title(ctx.title)
        primary_artist, _ = TextCleaner.parse_artists(ctx.artists)
        variants = TextCleaner.extract_title_variants(ctx.title)
        sub_titles = [v for v in variants if v.lower() != core_t.lower() and len(v) >= 2]

        seen_keys: Set[Tuple[str, str, str]] = set()

        def make_planned(q_text: str, sf: str, loc: Optional[str], phase: str, prov: str, prio: int = 1, weak: bool = False) -> Optional[PlannedQuery]:
            clean = cls.clean_query_text(q_text)
            if not clean:
                return None
            key = (sf.lower().strip(), (loc or "").lower().strip(), clean.lower().strip())
            if key in seen_keys:
                return None
            seen_keys.add(key)
            return PlannedQuery(
                query=clean,
                storefront=sf,
                locale=loc,
                phase=phase,
                provenance=prov,
                priority=prio,
                weak_only=weak,
            )

        target_sf = ctx.target_storefront or "cn"
        default_loc = ctx.supported_locales[0] if ctx.supported_locales else None

        # -------------------------------------------------------------
        # Phase A: Native Target Storefront
        # -------------------------------------------------------------
        phase_a: List[PlannedQuery] = []

        # A1. Core title + primary artist
        if primary_artist:
            pq = make_planned(f"{core_t} {primary_artist}", target_sf, default_loc, "A_native", "original", 1)
            if pq:
                phase_a.append(pq)
        else:
            pq = make_planned(core_t, target_sf, default_loc, "A_native", "original", 1)
            if pq:
                phase_a.append(pq)

        # A2. Scoped aliases & source translations
        if primary_artist:
            scoped_aliases = get_scoped_title_aliases(primary_artist, core_t)
            for sa in scoped_aliases:
                pq = make_planned(f"{sa} {primary_artist}", target_sf, default_loc, "A_native", "scoped_alias", 2)
                if pq:
                    phase_a.append(pq)

            # Subtitles from brackets
            for sub in sub_titles:
                pq = make_planned(f"{sub} {primary_artist}", target_sf, default_loc, "A_native", "bracket_variant", 3)
                if pq:
                    phase_a.append(pq)

            # Verified artist aliases (as fallback within Phase A)
            art_aliases = get_artist_aliases(primary_artist)
            for aa in art_aliases:
                if aa.lower() != primary_artist.lower():
                    pq = make_planned(f"{core_t} {aa}", target_sf, default_loc, "A_native", "artist_alias", 4)
                    if pq:
                        phase_a.append(pq)
        else:
            for sub in sub_titles:
                pq = make_planned(sub, target_sf, default_loc, "A_native", "bracket_variant", 2)
                if pq:
                    phase_a.append(pq)

        # -------------------------------------------------------------
        # Phase B: Universal Multi-Script Variants (Beam selection, max 4)
        # -------------------------------------------------------------
        phase_b: List[PlannedQuery] = []
        title_vars = JapaneseNormalizer.generate_variants(core_t)
        artist_vars = JapaneseNormalizer.generate_variants(primary_artist, is_artist=True) if primary_artist else []

        title_romaji = [v for v in title_vars if v.provenance in ("hepburn", "hepburn_compact", "long_vowel_relaxed")]
        artist_romaji = [v for v in artist_vars if v.provenance in ("hepburn", "hepburn_compact", "long_vowel_relaxed")]
        title_trans = [v for v in title_vars if v.provenance == "source_translation"]

        # Beam 1: Native title + Romanized artist
        if primary_artist and artist_romaji:
            best_art_romaji = artist_romaji[0]
            pq = make_planned(
                f"{core_t} {best_art_romaji.text}",
                target_sf,
                default_loc,
                "B_script",
                "native_title_romaji_artist",
                2,
                weak=(best_art_romaji.verification_level == "weak"),
            )
            if pq:
                phase_b.append(pq)

        # Beam 2: Romanized title + Native artist
        if primary_artist and title_romaji:
            best_title_romaji = title_romaji[0]
            pq = make_planned(
                f"{best_title_romaji.text} {primary_artist}",
                target_sf,
                default_loc,
                "B_script",
                "romaji_title_native_artist",
                2,
                weak=(best_title_romaji.verification_level == "weak"),
            )
            if pq:
                phase_b.append(pq)

        # Beam 3: Romanized title + Romanized artist
        if primary_artist and title_romaji and artist_romaji:
            best_title_romaji = title_romaji[0]
            best_art_romaji = artist_romaji[0]
            pq = make_planned(
                f"{best_title_romaji.text} {best_art_romaji.text}",
                target_sf,
                default_loc,
                "B_script",
                "romaji_title_romaji_artist",
                3,
                weak=True,
            )
            if pq:
                phase_b.append(pq)

        # Beam 4: Source bracket translation + artist (or second Romaji variant)
        if primary_artist and title_trans:
            pq = make_planned(
                f"{title_trans[0].text} {primary_artist}",
                target_sf,
                default_loc,
                "B_script",
                "source_translation",
                2,
            )
            if pq:
                phase_b.append(pq)
        elif primary_artist and len(title_romaji) > 1:
            second_title_romaji = title_romaji[1]
            pq = make_planned(
                f"{second_title_romaji.text} {primary_artist}",
                target_sf,
                default_loc,
                "B_script",
                "romaji_title_variant",
                3,
                weak=True,
            )
            if pq:
                phase_b.append(pq)

        # If no artist or title-only needed
        if not primary_artist and title_romaji:
            pq = make_planned(title_romaji[0].text, target_sf, default_loc, "B_script", "romaji_title_only", 3, weak=True)
            if pq:
                phase_b.append(pq)

        # Cap Phase B to max 4 queries
        phase_b = phase_b[:4]

        # -------------------------------------------------------------
        # Phase D: Cross-Storefront JP Discovery (Max 2)
        # -------------------------------------------------------------
        phase_d: List[PlannedQuery] = []
        # Query 1: Best native Japanese in JP storefront with ja-JP locale
        jp_sf = "jp"
        jp_loc = "ja-JP"
        if primary_artist:
            pq = make_planned(f"{core_t} {primary_artist}", jp_sf, jp_loc, "D_jp_discovery", "jp_native", 1)
            if pq:
                phase_d.append(pq)
            if title_romaji:
                pq = make_planned(f"{title_romaji[0].text} {primary_artist}", jp_sf, jp_loc, "D_jp_discovery", "jp_romaji_title", 2)
                if pq:
                    phase_d.append(pq)
        else:
            pq = make_planned(core_t, jp_sf, jp_loc, "D_jp_discovery", "jp_native", 1)
            if pq:
                phase_d.append(pq)

        # Cap Phase D to max 2 queries
        phase_d = phase_d[:2]

        return {
            "A_native": phase_a,
            "B_script": phase_b,
            "C_suggestions": [],
            "D_jp_discovery": phase_d,
        }

    @classmethod
    def plan_queries(cls, source: Track, target_storefront: str = "cn") -> List[PlannedQuery]:
        """
        Generate prioritized planned queries across phases (backward compatible).
        """
        ctx = QueryContext(
            title=source.title,
            artists=source.artists,
            album=source.album,
            isrc=getattr(source, "isrc", None),
            duration_ms=source.duration_ms,
            target_storefront=target_storefront,
        )
        phases = cls.plan_phases(ctx)
        # Flatten respecting priority
        ordered: List[PlannedQuery] = []
        ordered.extend(phases["A_native"])
        ordered.extend(phases["B_script"])
        ordered.extend(phases["D_jp_discovery"])
        return ordered

    @classmethod
    def plan_first_round(cls, source: Track, target_storefront: str = "cn") -> List[PlannedQuery]:
        """First round query budget: max 2 queries from Phase A."""
        ctx = QueryContext(
            title=source.title,
            artists=source.artists,
            target_storefront=target_storefront,
        )
        phases = cls.plan_phases(ctx)
        candidates = phases["A_native"] or phases["B_script"]
        return candidates[:cls.FIRST_ROUND_BUDGET]

    @classmethod
    def plan_deep_retry(cls, source: Track, target_storefront: str = "cn", max_budget: int = MAX_CATALOG_BUDGET) -> List[PlannedQuery]:
        """Deep retry queries: up to max_budget."""
        all_q = cls.plan_queries(source, target_storefront=target_storefront)
        return all_q[:max_budget]
