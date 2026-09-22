"""
Evidence and diagnostic models for Apple Music matching precision.
Provides structured evidence classification, conflict detection,
and safe single-track diagnostic reporting without sensitive data leakage.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

MATCH_RULE_VERSION = "2026.09.v1"
ALIAS_VERSION = "2026.09.v1"


class VerificationLevel(str, Enum):
    STRONG = "strong"
    MEDIUM = "medium"
    WEAK = "weak"
    UNVERIFIED = "unverified"
    CONFLICT = "conflict"


class MatchEvidence(BaseModel):
    """Structured evidence backing a candidate match decision."""
    evidence_type: str = "none"           # e.g. 'isrc_exact', 'exact_title_and_artist', 'artist_scoped_title_alias', 'transliteration_only', 'none'
    verification_level: str = "unverified" # 'strong', 'medium', 'weak', 'unverified'
    matched_fields: List[str] = Field(default_factory=list) # e.g. ['title', 'artist', 'isrc']
    conflicts: List[str] = Field(default_factory=list)      # e.g. ['version_conflict', 'artist_conflict']
    reasons: List[str] = Field(default_factory=list)

    @property
    def is_strong(self) -> bool:
        return self.verification_level == VerificationLevel.STRONG.value and not self.conflicts


class SingleTrackDiagnostics(BaseModel):
    """
    Safe, sanitized diagnostic summary for a single track matching process.
    Guaranteed not to contain real tokens, cookies, auth headers, or absolute user paths.
    """
    app_version: str = "2.0.4"
    build_id: str = "source"
    rule_version: str = MATCH_RULE_VERSION
    alias_version: str = ALIAS_VERSION
    storefront: str = "cn"
    source_title: str = ""
    source_artists: List[str] = Field(default_factory=list)
    executed_queries: List[Dict[str, Any]] = Field(default_factory=list)
    candidate_count: int = 0
    candidates_summary: List[Dict[str, Any]] = Field(default_factory=list)
    cache_type: Optional[str] = None
    cache_version: Optional[str] = None
    score_breakdown: Dict[str, float] = Field(default_factory=dict)
    evidence: Optional[MatchEvidence] = None
    final_decision: str = "no_match"
    decision_reasons: List[str] = Field(default_factory=list)
