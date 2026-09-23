"""
Query models and context structures for multi-phase catalog search planning.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class PlannedQuery:
    """
    A single planned query in the multi-phase catalog search execution pipeline.
    """
    query: str
    storefront: str = "cn"
    locale: Optional[str] = None
    phase: str = "A_native"       # "A_native", "B_script", "C_suggestions", "D_jp_discovery"
    provenance: str = "title_artist"  # "isrc", "title_artist", "hepburn_title", "source_translation", "apple_suggestion", "jp_discovery"
    cost: int = 1
    weak_only: bool = False
    priority: int = 1

    @property
    def dedup_key(self) -> Tuple[str, str, str]:
        """Unique key for deduplication across storefront, locale, and query text."""
        return (
            self.storefront.lower().strip(),
            (self.locale or "").lower().strip(),
            self.query.lower().strip(),
        )


@dataclass
class QueryContext:
    """
    Context for generating search queries for a single track.
    """
    title: str
    artists: List[str]
    album: Optional[str] = None
    isrc: Optional[str] = None
    duration_ms: Optional[int] = None
    target_storefront: str = "cn"
    supported_locales: List[str] = field(default_factory=list)
    trans_title: Optional[str] = None
    aliases: List[str] = field(default_factory=list)
    max_catalog_queries: int = 8
    max_suggestion_queries: int = 2
    max_equivalents_queries: int = 1
