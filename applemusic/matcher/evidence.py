"""
Evidence and diagnostic models for Apple Music matching precision.
Provides structured evidence classification, conflict detection,
and safe single-track diagnostic reporting without sensitive data leakage.
"""

import re
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, root_validator

MATCH_RULE_VERSION = "2026.09.v2"
ALIAS_VERSION = "2026.09.v2"
QUERY_POLICY_VERSION = "2026.09.v2"
ROMANIZER_VERSION = "2026.09.v2"
EXCEPTION_REGISTRY_VERSION = "2026.09.v2"


class VerificationLevel(str, Enum):
    STRONG = "strong"
    MEDIUM = "medium"
    WEAK = "weak"
    UNVERIFIED = "unverified"
    CONFLICT = "conflict"


def _sanitize_diagnostic_data(val: Any) -> Any:
    """Sanitize data structure by stripping sensitive tokens, cookies, auth headers, and absolute user paths."""
    if isinstance(val, dict):
        res = {}
        for k, v in val.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in ("token", "secret", "cookie", "auth", "password", "session")):
                continue
            res[k] = _sanitize_diagnostic_data(v)
        return res
    elif isinstance(val, list):
        return [_sanitize_diagnostic_data(x) for x in val]
    elif isinstance(val, str):
        s = val
        s = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.]{10,}", r"\1[REDACTED]", s, flags=re.IGNORECASE)
        s = re.sub(r"(key=)[A-Za-z0-9_\-\.]{10,}", r"\1[REDACTED]", s, flags=re.IGNORECASE)
        s = re.sub(r"(secret=)[A-Za-z0-9_\-\.]{10,}", r"\1[REDACTED]", s, flags=re.IGNORECASE)
        s = re.sub(r"[A-Za-z]:\\[Uu]sers\\[^\\]+", "[USER_HOME]", s)
        s = re.sub(r"/home/[^/]+", "[USER_HOME]", s)
        s = re.sub(r"/Users/[^/]+", "[USER_HOME]", s)
        return s
    return val


class MatchEvidence(BaseModel):
    """
    Evidence collected for matching decisions with strict versioning.
    """
    evidence_type: str = "title_and_artist"
    verification_level: str = VerificationLevel.UNVERIFIED.value
    matched_fields: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    provenance: str = "search"
    evidence_families: List[str] = Field(default_factory=list)
    base_rule_version: str = "2026.09.v1"
    rule_version: str = "2026.09.v1"
    query_policy_version: str = "2026.09.v1"
    romanizer_version: str = "2026.09.v1"
    exception_registry_version: str = "2026.09.v1"
    alias_version: Optional[str] = "2026.09.v1"

    class Config:
        extra = "forbid"

    @root_validator(pre=True)
    def _handle_legacy_evidence(cls, values: Any) -> Any:
        if isinstance(values, dict):
            # Compatibility for historical MatchEvidence containing 'reasons'
            values.pop("reasons", None)
            # Legacy records without version fields must NOT be defaulted to v2
            if "rule_version" not in values:
                values["rule_version"] = "2026.09.v1"
            if "query_policy_version" not in values:
                values["query_policy_version"] = "2026.09.v1"
            if "romanizer_version" not in values:
                values["romanizer_version"] = "2026.09.v1"
            if "exception_registry_version" not in values:
                values["exception_registry_version"] = "2026.09.v1"
            if "alias_version" not in values:
                values["alias_version"] = "2026.09.v1"
        return values

    @property
    def is_strong(self) -> bool:
        return self.verification_level == VerificationLevel.STRONG.value and not self.conflicts


class SingleTrackDiagnostics(BaseModel):
    """
    Safe, sanitized diagnostic summary for a single track matching process.
    Guaranteed not to contain real tokens, cookies, auth headers, or absolute user paths.
    """
    app_version: str = "2.0.5"
    build_id: str = "unknown"
    base_rule_version: str = "2026.09.v1"
    rule_version: str = MATCH_RULE_VERSION
    alias_version: str = ALIAS_VERSION
    query_policy_version: str = QUERY_POLICY_VERSION
    romanizer_version: str = ROMANIZER_VERSION
    exception_registry_version: str = EXCEPTION_REGISTRY_VERSION
    storefront: str = "cn"
    target_storefront: str = "cn"
    source_title: str = ""
    source_artists: List[str] = Field(default_factory=list)
    executed_queries: List[Dict[str, Any]] = Field(default_factory=list)
    discovery_chain: List[Union[str, Dict[str, Any]]] = Field(default_factory=list)
    candidates_count: int = 0
    candidate_count: int = 0
    candidates_summary: List[Dict[str, Any]] = Field(default_factory=list)
    cache_status: Optional[str] = None
    cache_type: Optional[str] = None
    cache_version: Optional[str] = None
    score_breakdown: Dict[str, float] = Field(default_factory=dict)
    evidence: Optional[MatchEvidence] = None
    verification_level: str = VerificationLevel.UNVERIFIED.value
    matched_fields: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    final_decision: str = "no_match"
    decision_reasons: List[str] = Field(default_factory=list)
    budget_consumed: Dict[str, int] = Field(default_factory=dict)

    class Config:
        extra = "forbid"

    @root_validator(pre=True)
    def sync_counts_and_cache(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        if "candidates_count" in values and "candidate_count" not in values:
            values["candidate_count"] = values["candidates_count"]
        elif "candidate_count" in values and "candidates_count" not in values:
            values["candidates_count"] = values["candidate_count"]
        elif "candidates_count" in values and "candidate_count" in values:
            c = values["candidates_count"] or values["candidate_count"]
            values["candidates_count"] = c
            values["candidate_count"] = c

        if "cache_status" in values and "cache_type" not in values:
            values["cache_type"] = values["cache_status"]
        elif "cache_type" in values and "cache_status" not in values:
            values["cache_status"] = values["cache_type"]
        return values

    def to_sanitized_dict(self) -> Dict[str, Any]:
        """Return a clean dictionary guaranteed to be sanitized."""
        raw = self.model_dump() if hasattr(self, "model_dump") else self.dict()
        return _sanitize_diagnostic_data(raw)
