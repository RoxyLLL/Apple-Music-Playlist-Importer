"""
Data models for Apple Music Playlist Importer.
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, computed_field


class ConfidenceLevel(str, Enum):
    EXACT = "exact"                    # >= 0.88 & auto_accept
    HIGH = "high"                      # >= 0.72 & review/auto
    MEDIUM = "medium"                  # >= 0.55 & review
    LOW = "low"                        # < 0.55
    NOT_FOUND = "not_found"            # 0 matches found
    USER_CONFIRMED = "user_confirmed"  # Hand-picked by user


class DecisionStatus(str, Enum):
    AUTO_ACCEPT = "auto_accept"        # Verified, safe for auto-sync
    REVIEW = "review"                  # Ambiguity/gap/version conflict, needs review
    NO_MATCH = "no_match"              # No acceptable candidate
    USER_CONFIRMED = "user_confirmed"  # Explicitly chosen by user


class Track(BaseModel):
    """Source track extracted from external music platforms or files."""
    title: str
    artists: List[str] = Field(default_factory=list)
    album: Optional[str] = None
    duration_ms: Optional[int] = None
    isrc: Optional[str] = None
    original_id: Optional[str] = None
    source: str = "unknown"
    version_tags: List[str] = Field(default_factory=list)
    clean_title: Optional[str] = None
    primary_artist: Optional[str] = None
    featured_artists: List[str] = Field(default_factory=list)

    @computed_field
    @property
    def artist_str(self) -> str:
        return " / ".join(self.artists) if self.artists else "Unknown Artist"

    def __str__(self) -> str:
        return f"{self.title} - {self.artist_str}"


class AppleMusicTrack(BaseModel):
    """Track information from Apple Music Catalog."""
    id: str
    title: str
    artists: List[str] = Field(default_factory=list)
    album: Optional[str] = None
    duration_ms: Optional[int] = None
    isrc: Optional[str] = None
    artwork_url: Optional[str] = None
    preview_url: Optional[str] = None
    storefront: str = "cn"
    url: Optional[str] = None

    @computed_field
    @property
    def artist_str(self) -> str:
        return " / ".join(self.artists) if self.artists else "Unknown Artist"

    def get_artwork_url(self, width: int = 300, height: int = 300) -> Optional[str]:
        if not self.artwork_url:
            return None
        return self.artwork_url.replace("{w}", str(width)).replace("{h}", str(height))


class MatchCandidate(BaseModel):
    """Candidate match with scoring details."""
    track: AppleMusicTrack
    score: float
    title_score: float
    artist_score: float
    album_score: float = 0.0
    duration_score: float = 0.0
    version_score: float = 0.0
    confidence: ConfidenceLevel
    decision: str = "review"
    decision_reasons: List[str] = Field(default_factory=list)


class SongMatchResult(BaseModel):
    """Result of matching a single source track against Apple Music."""
    source_track: Track
    candidates: List[MatchCandidate] = Field(default_factory=list)
    selected_candidate: Optional[MatchCandidate] = None
    status: ConfidenceLevel = ConfidenceLevel.NOT_FOUND
    score_gap: Optional[float] = None
    decision: str = "no_match"
    decision_reasons: List[str] = Field(default_factory=list)
    metadata_adequacy: float = 1.0

    @property
    def is_matched(self) -> bool:
        return self.selected_candidate is not None


class Playlist(BaseModel):
    """Represents a playlist extracted from an external source."""
    name: str
    description: Optional[str] = None
    cover_url: Optional[str] = None
    source: str = "unknown"
    tracks: List[Track] = Field(default_factory=list)
    total_expected: Optional[int] = None
    warnings: List[str] = Field(default_factory=list)

    @property
    def track_count(self) -> int:
        return len(self.tracks)


class SyncOptions(BaseModel):
    """Options for playlist matching and synchronization."""
    storefront: str = "cn"
    playlist_name: Optional[str] = None
    auto_confirm: bool = False
    min_confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    add_to_library: bool = False
    create_playlist: bool = True
