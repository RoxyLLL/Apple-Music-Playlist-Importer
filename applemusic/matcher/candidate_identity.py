"""
Candidate identity aggregation and deduplication for Apple Music matching.
Ensures that tracks discovered across multiple queries or cross-storefront
mappings are deduplicated by stable identity (song ID, ISRC, equivalents).
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from applemusic.matcher.cleaner import TextCleaner
from applemusic.matcher.query_models import PlannedQuery
from applemusic.models import AppleMusicTrack


@dataclass
class CandidateIdentity:
    """
    Unified identity for a candidate track in the target storefront.
    """
    track: AppleMusicTrack
    discovery_path: str = "native_search"  # "isrc_exact", "native_search", "script_variant", "apple_suggestion", "jp_equivalents", "jp_isrc_remap"
    discovery_storefront: str = "cn"
    source_queries: List[PlannedQuery] = field(default_factory=list)
    is_equivalent_mapped: bool = False
    original_jp_track: Optional[AppleMusicTrack] = None

    @property
    def primary_id(self) -> str:
        """Returns the stable target song ID."""
        return self.track.id

    def identity_key(self) -> str:
        """
        Calculates the canonical deduplication key for this candidate.
        Priority:
        1. Target song ID (e.g. "id:123456789")
        2. ISRC + version fingerprint (e.g. "isrc:JPB601901234:v1")
        3. Normalized title + artist + duration bucket (e.g. "text:title:artist:dur_bucket")
        """
        if self.track.id:
            return f"id:{self.track.id}"

        clean_isrc = (self.track.isrc or "").strip().upper()
        if clean_isrc:
            # Add version fingerprint to distinguish live / remix with same ISRC
            v_tag = TextCleaner.extract_version_tag(self.track.title) or "orig"
            return f"isrc:{clean_isrc}:{v_tag}"

        norm_t = TextCleaner.clean_title(self.track.title).lower()
        norm_a = TextCleaner.clean_artist(self.track.artists[0] if self.track.artists else "").lower()
        dur_bucket = (self.track.duration_ms // 4000) if self.track.duration_ms else 0
        return f"text:{norm_t}:{norm_a}:{dur_bucket}"


class CandidateAggregator:
    """
    Thread-safe aggregator for accumulating and deduplicating candidate tracks.
    """

    def __init__(self, target_storefront: str = "cn"):
        self.target_storefront = target_storefront
        self._candidates: Dict[str, CandidateIdentity] = {}
        self._ordered_keys: List[str] = []

    def add_candidate(
        self,
        track: AppleMusicTrack,
        query: Optional[PlannedQuery] = None,
        discovery_path: str = "native_search",
        is_equivalent_mapped: bool = False,
        original_jp_track: Optional[AppleMusicTrack] = None,
    ) -> CandidateIdentity:
        """
        Add a track to the aggregator. If already present, merges query provenance.
        """
        # Ensure the track is attributed to the target storefront
        if not track.storefront:
            track.storefront = self.target_storefront
        if discovery_path and not getattr(track, "discovery_path", None):
            track.discovery_path = discovery_path
        if original_jp_track and not getattr(track, "original_jp_track", None):
            track.original_jp_track = original_jp_track

        temp_cand = CandidateIdentity(
            track=track,
            discovery_path=discovery_path,
            discovery_storefront=track.discovery_storefront or self.target_storefront,
            source_queries=[query] if query else [],
            is_equivalent_mapped=is_equivalent_mapped,
            original_jp_track=original_jp_track,
        )
        key = temp_cand.identity_key()

        if key in self._candidates:
            existing = self._candidates[key]
            if query and query not in existing.source_queries:
                existing.source_queries.append(query)
            if is_equivalent_mapped and not existing.is_equivalent_mapped:
                existing.is_equivalent_mapped = True
                existing.original_jp_track = original_jp_track
                existing.discovery_path = discovery_path
                existing.track.discovery_path = discovery_path
                existing.track.original_jp_track = original_jp_track
            return existing

        self._candidates[key] = temp_cand
        self._ordered_keys.append(key)
        return temp_cand

    def get_candidates(self) -> List[CandidateIdentity]:
        """Return candidates in stable insertion order."""
        return [self._candidates[k] for k in self._ordered_keys]

    def clear(self):
        self._candidates.clear()
        self._ordered_keys.clear()
