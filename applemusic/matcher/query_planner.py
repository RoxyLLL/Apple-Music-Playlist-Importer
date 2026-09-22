"""
Search query planning and budget enforcement for Apple Music matching.
Ensures predictable, ordered queries with provenance tracking and hard budget limits.
"""

from dataclasses import dataclass
from typing import List, Optional
import re
from applemusic.matcher.cleaner import TextCleaner
from applemusic.matcher.title_aliases import get_scoped_title_aliases
from applemusic.matcher.artist_aliases import get_artist_aliases
from applemusic.models import Track


@dataclass
class PlannedQuery:
    query: str
    provenance: str  # "original", "scoped_alias", "trans_title", "bracket_variant", "artist_alias", "romaji", "title_only"
    priority: int    # 1: highest, 5: lowest


class QueryPlanner:
    """
    Plans search queries for first round and rematch according to strict budgets.
    First round: max 2 queries.
    Rematch: max 6 queries total across all storefronts (max 3 per storefront).
    """
    FIRST_ROUND_BUDGET = 2
    RETRY_PER_STOREFRONT_BUDGET = 3
    RETRY_TOTAL_BUDGET = 6

    @classmethod
    def clean_query_text(cls, text: str) -> str:
        """Strip brackets, book marks, and excess spaces for Apple Music search."""
        clean = re.sub(r"[()（）【】\[\]《》「」『』\"]", " ", text)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean

    @classmethod
    def plan_queries(cls, source: Track) -> List[PlannedQuery]:
        """
        Generate prioritized planned queries with provenance.
        Order:
        1. core_title + primary_artist (original)
        2. scoped_alias + primary_artist / trans_title + primary_artist
        3. bracket_variant + primary_artist
        4. core_title + verified_artist_alias / scoped_alias + verified_artist_alias
        5. romaji_title + primary_artist
        6. core_title alone (title_only fallback)
        """
        core_t, _ = TextCleaner.parse_title(source.title)
        primary_artist, _ = TextCleaner.parse_artists(source.artists)
        variants = TextCleaner.extract_title_variants(source.title)
        sub_titles = [v for v in variants if v.lower() != core_t.lower() and len(v) >= 2]

        planned: List[PlannedQuery] = []
        seen_queries = set()

        def add_item(q_text: str, provenance: str, priority: int):
            clean = cls.clean_query_text(q_text)
            if clean and clean.lower() not in seen_queries:
                seen_queries.add(clean.lower())
                planned.append(PlannedQuery(query=clean, provenance=provenance, priority=priority))

        # 1. Original core title + primary artist
        if primary_artist:
            add_item(f"{core_t} {primary_artist}", "original", 1)
        else:
            add_item(core_t, "original", 1)

        # 2. Scoped title aliases (e.g. ("十明", "灰かぶり") -> Cinder ella)
        if primary_artist:
            scoped_aliases = get_scoped_title_aliases(primary_artist, core_t)
            for sa in scoped_aliases:
                add_item(f"{sa} {primary_artist}", "scoped_alias", 2)

            # Trans title if present
            if source.trans_title:
                add_item(f"{source.trans_title} {primary_artist}", "trans_title", 2)

            # 3. Subtitles from brackets (e.g. 灰姑娘 from 灰かぶり（灰姑娘）)
            for sub in sub_titles:
                add_item(f"{sub} {primary_artist}", "bracket_variant", 3)

            # 4. Verified artist aliases (e.g. toaka for 十明)
            art_aliases = get_artist_aliases(primary_artist)
            for aa in art_aliases:
                if aa.lower() != primary_artist.lower():
                    add_item(f"{core_t} {aa}", "artist_alias", 4)
                    for sa in scoped_aliases:
                        add_item(f"{sa} {aa}", "scoped_alias_with_artist_alias", 4)

            # 5. Japanese Romaji variants (only if text has Kana/compounds)
            has_kana = bool(re.search(r"[\u3040-\u30ff]", core_t))
            if has_kana:
                romaji_list = TextCleaner.get_japanese_romaji_variants(core_t, is_artist=False)
                for rj in romaji_list[:2]:
                    add_item(f"{rj} {primary_artist}", "romaji", 5)

            # 6. Title only fallback
            add_item(core_t, "title_only", 6)
        else:
            for sub in sub_titles:
                add_item(sub, "bracket_variant", 2)
            if source.trans_title:
                add_item(source.trans_title, "trans_title", 2)

        return planned

    @classmethod
    def plan_first_round(cls, source: Track) -> List[PlannedQuery]:
        """First round query budget: max 2 queries."""
        all_q = cls.plan_queries(source)
        return all_q[:cls.FIRST_ROUND_BUDGET]

    @classmethod
    def plan_deep_retry(cls, source: Track, max_budget: int = RETRY_TOTAL_BUDGET) -> List[PlannedQuery]:
        """Deep retry queries: up to max_budget."""
        all_q = cls.plan_queries(source)
        return all_q[:max_budget]
