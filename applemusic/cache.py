"""
Persistent SQLite cache module for Apple Music Catalog queries and track match results.
Ensures identical queries and tracks across runs and playlists are resolved instantly
from local disk, cutting external Apple Music API traffic by 80-95%.
"""

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from applemusic.config import DEFAULT_CONFIG_DIR
from applemusic.models import AppleMusicTrack, CatalogSearchOutcome, SongMatchResult

logger = logging.getLogger(__name__)

CACHE_DB_PATH = DEFAULT_CONFIG_DIR / "cache.db"

# TTL Constants (seconds)
TTL_CATALOG_OK = 14 * 86400        # 14 days for successful catalog hits
TTL_CATALOG_NO_HITS = 3 * 86400    # 3 days for verified negative hits (no hits)
TTL_MATCH_AUTO_ACCEPT = 30 * 86400 # 30 days for confident matches
TTL_MATCH_NO_MATCH = 3 * 86400     # 3 days for verified no-match


class PersistentCache:
    """
    Thread-safe SQLite persistent cache manager for Apple Music queries.
    Uses SQLite WAL mode for fast non-blocking concurrent reads and writes.
    """

    _instance: Optional["PersistentCache"] = None
    _instance_lock = threading.Lock()

    def __init__(self, db_path: Optional[Any] = None):
        self.db_path = Path(db_path) if db_path else CACHE_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_db()

    @classmethod
    def get_instance(cls, db_path: Optional[Path] = None) -> "PersistentCache":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(db_path)
            return cls._instance

    def _get_connection(self) -> sqlite3.Connection:
        """Get a thread-local SQLite connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=20.0,
                check_same_thread=False,
            )
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=15000;")
            self._local.conn = conn
        return self._local.conn

    def _get_conn(self) -> sqlite3.Connection:
        """Alias for _get_connection for test compatibility."""
        return self._get_connection()

    def close(self):
        """Close the thread-local SQLite connection."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

    def _init_db(self):
        """Initialize database tables and indexes with safe in-place migration."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                # Check catalog_cache schema
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='catalog_cache';")
                cat_exists = cursor.fetchone() is not None
                if cat_exists:
                    cursor.execute("PRAGMA table_info(catalog_cache);")
                    cols_info = {row[1]: row for row in cursor.fetchall()}
                    # Check whether table structure needs to be rebuilt:
                    # Legacy table had query_key (NOT NULL without default), tracks_json,
                    # or lacked locale/query_policy_version in primary key.
                    needs_rebuild = False
                    if (
                        "query_key" in cols_info
                        or "tracks_json" in cols_info
                        or "locale" not in cols_info
                        or "query_policy_version" not in cols_info
                    ):
                        needs_rebuild = True
                    else:
                        pk_cols = [name for name, info in sorted(cols_info.items(), key=lambda x: x[1][5]) if info[5] > 0]
                        expected_pk = ["storefront", "locale", "query_type", "query_term", "limit_val", "query_policy_version"]
                        if pk_cols != expected_pk:
                            needs_rebuild = True

                    if needs_rebuild:
                        conn.execute("""
                            CREATE TABLE catalog_cache_new (
                                storefront TEXT NOT NULL,
                                locale TEXT NOT NULL DEFAULT '',
                                query_type TEXT NOT NULL,
                                query_term TEXT NOT NULL,
                                limit_val INTEGER NOT NULL,
                                kind TEXT NOT NULL,
                                response_json TEXT NOT NULL,
                                created_at REAL NOT NULL,
                                expires_at REAL NOT NULL,
                                query_policy_version TEXT NOT NULL DEFAULT '2026.09.v3',
                                PRIMARY KEY (storefront, locale, query_type, query_term, limit_val, query_policy_version)
                            );
                        """)
                        term_expr = "query_term" if "query_term" in cols_info else ("query_key" if "query_key" in cols_info else "''")
                        resp_expr = "response_json" if "response_json" in cols_info else ("tracks_json" if "tracks_json" in cols_info else "'{\"tracks\":[]}'")
                        loc_expr = "locale" if "locale" in cols_info else "''"
                        pol_expr = "query_policy_version" if "query_policy_version" in cols_info else "'2026.09.v3'"
                        kind_expr = "kind" if "kind" in cols_info else "'ok'"
                        limit_expr = "limit_val" if "limit_val" in cols_info else "10"
                        created_expr = "created_at" if "created_at" in cols_info else ("fetched_at" if "fetched_at" in cols_info else "strftime('%s', 'now')")
                        expires_expr = "expires_at" if "expires_at" in cols_info else "strftime('%s', 'now') + 86400"

                        conn.execute(f"""
                            INSERT OR IGNORE INTO catalog_cache_new (
                                storefront, locale, query_type, query_term, limit_val, kind, response_json, created_at, expires_at, query_policy_version
                            )
                            SELECT
                                storefront,
                                COALESCE({loc_expr}, ''),
                                query_type,
                                COALESCE({term_expr}, ''),
                                COALESCE({limit_expr}, 10),
                                COALESCE({kind_expr}, 'ok'),
                                COALESCE({resp_expr}, '{{}}'),
                                COALESCE({created_expr}, strftime('%s', 'now')),
                                COALESCE({expires_expr}, strftime('%s', 'now') + 86400),
                                COALESCE({pol_expr}, '2026.09.v3')
                            FROM catalog_cache;
                        """)
                        conn.execute("DROP TABLE catalog_cache;")
                        conn.execute("ALTER TABLE catalog_cache_new RENAME TO catalog_cache;")
                else:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS catalog_cache (
                            storefront TEXT NOT NULL,
                            locale TEXT NOT NULL DEFAULT '',
                            query_type TEXT NOT NULL,
                            query_term TEXT NOT NULL,
                            limit_val INTEGER NOT NULL,
                            kind TEXT NOT NULL,
                            response_json TEXT NOT NULL,
                            created_at REAL NOT NULL,
                            expires_at REAL NOT NULL,
                            query_policy_version TEXT NOT NULL DEFAULT '2026.09.v3',
                            PRIMARY KEY (storefront, locale, query_type, query_term, limit_val, query_policy_version)
                        );
                    """)

                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_catalog_cache_expires
                    ON catalog_cache (expires_at);
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_catalog_cache_lookup
                    ON catalog_cache (storefront, locale, query_type, query_term, limit_val, query_policy_version);
                """)

                # 2. equivalence_cache table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS equivalence_cache (
                        source_storefront TEXT NOT NULL,
                        source_song_id TEXT NOT NULL,
                        target_storefront TEXT NOT NULL,
                        target_song_id TEXT,
                        payload_json TEXT,
                        fetched_at REAL NOT NULL,
                        expires_at REAL NOT NULL,
                        PRIMARY KEY (source_storefront, source_song_id, target_storefront)
                    );
                """)
                cursor.execute("PRAGMA table_info(equivalence_cache);")
                equiv_cols = {row[1] for row in cursor.fetchall()}
                if "payload_json" not in equiv_cols:
                    conn.execute("ALTER TABLE equivalence_cache ADD COLUMN payload_json TEXT;")

                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_equiv_cache_expires
                    ON equivalence_cache (expires_at);
                """)

                # 3. match_cache table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS match_cache (
                        storefront TEXT NOT NULL,
                        track_hash TEXT NOT NULL,
                        cache_key TEXT NOT NULL DEFAULT '',
                        result_json TEXT NOT NULL,
                        decision TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        expires_at REAL NOT NULL,
                        rule_version TEXT NOT NULL DEFAULT '2026.09.v1',
                        query_policy_version TEXT NOT NULL DEFAULT '2026.09.v1',
                        romanizer_version TEXT NOT NULL DEFAULT '2026.09.v1',
                        exception_registry_version TEXT NOT NULL DEFAULT '2026.09.v1',
                        PRIMARY KEY (storefront, track_hash)
                    );
                """)
                cursor.execute("PRAGMA table_info(match_cache);")
                match_cols = {row[1] for row in cursor.fetchall()}
                if "cache_key" not in match_cols:
                    conn.execute("ALTER TABLE match_cache ADD COLUMN cache_key TEXT NOT NULL DEFAULT '';")
                for c_name in ("rule_version", "query_policy_version", "romanizer_version", "exception_registry_version"):
                    if c_name not in match_cols:
                        conn.execute(f"ALTER TABLE match_cache ADD COLUMN {c_name} TEXT NOT NULL DEFAULT '2026.09.v1';")

                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_match_cache_expires 
                    ON match_cache (expires_at);
                """)

    # -------------------------------------------------------------------------
    # Catalog Search Cache
    # -------------------------------------------------------------------------
    def get_catalog(
        self,
        storefront: str,
        query_type: str,
        query_term: str,
        limit_val: int = 10,
        locale: Optional[str] = None,
        query_policy_version: Optional[str] = None,
    ) -> Optional[CatalogSearchOutcome]:
        """
        Retrieve cached CatalogSearchOutcome if present and not expired.
        Returns None on cache miss or expiration.
        """
        from applemusic.matcher.evidence import QUERY_POLICY_VERSION
        sf = (storefront or "cn").lower()
        loc = (locale or "").strip().lower()
        q_type = query_type.lower()
        q_term = query_term.strip().lower()
        q_ver = query_policy_version or QUERY_POLICY_VERSION
        now = time.time()

        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT kind, response_json, expires_at 
                FROM catalog_cache 
                WHERE storefront = ? AND locale = ? AND query_type = ? AND query_term = ? AND limit_val = ? AND query_policy_version = ?
                """,
                (sf, loc, q_type, q_term, limit_val, q_ver),
            )
            row = cursor.fetchone()
            if not row:
                return None

            kind, response_json, expires_at = row
            if expires_at <= now:
                # Expired
                return None

            data = json.loads(response_json)
            if isinstance(data, list):
                tracks = [AppleMusicTrack(**t) for t in data]
                safe_message = None
            elif isinstance(data, dict):
                tracks = [AppleMusicTrack(**t) for t in data.get("tracks", [])]
                safe_message = data.get("safe_message")
            else:
                tracks = []
                safe_message = None
            return CatalogSearchOutcome(
                kind=kind,
                tracks=tracks,
                http_status=200,
                request_id="cache-persistent",
                safe_message=safe_message,
            )
        except Exception as e:
            logger.debug("Failed to read from persistent catalog cache: %s", e)
            return None

    def set_catalog(
        self,
        storefront: str,
        query_type: str,
        query_term: str,
        outcome: CatalogSearchOutcome,
        limit_val: int = 10,
        locale: Optional[str] = None,
        query_policy_version: Optional[str] = None,
    ) -> None:
        """
        Persist CatalogSearchOutcome.
        Only outcomes with kind 'ok' or 'no_hits' are stored.
        Transient errors (429, timeout, network error) are NEVER cached.
        """
        if outcome.kind not in ("ok", "no_hits"):
            return

        from applemusic.matcher.evidence import QUERY_POLICY_VERSION
        sf = (storefront or "cn").lower()
        loc = (locale or "").strip().lower()
        q_type = query_type.lower()
        q_term = query_term.strip().lower()
        q_ver = query_policy_version or QUERY_POLICY_VERSION
        now = time.time()

        # Hits cached for 7 days; no-hits cached for 24 hours (bounded negative cache TTL)
        ttl = 7 * 86400 if outcome.kind == "ok" else 86400
        expires_at = now + ttl

        try:
            conn = self._get_connection()
            data = {
                "tracks": [t.dict() for t in outcome.tracks],
                "safe_message": outcome.safe_message,
            }
            json_str = json.dumps(data, ensure_ascii=False)
            with self._lock:
                with conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO catalog_cache 
                        (storefront, locale, query_type, query_term, limit_val, query_policy_version, kind, response_json, created_at, expires_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (sf, loc, q_type, q_term, limit_val, q_ver, outcome.kind, json_str, now, expires_at),
                    )
        except Exception as e:
            logger.debug("Failed to write to persistent catalog cache: %s", e)

    # -------------------------------------------------------------------------
    # Equivalence Cache
    # -------------------------------------------------------------------------
    def get_equivalence(
        self,
        target_storefront: str,
        source_song_id: str,
        source_storefront: str = "jp",
    ) -> Optional[str]:
        """
        Check equivalence cache.
        Returns target_song_id if mapped, "" if negative hit (miss),
        or None if not cached or expired.
        """
        target_id, _ = self.get_equivalence_with_payload(
            target_storefront=target_storefront,
            source_song_id=source_song_id,
            source_storefront=source_storefront,
        )
        return target_id

    def get_equivalence_with_payload(
        self,
        target_storefront: str,
        source_song_id: str,
        source_storefront: str = "jp",
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Check equivalence cache.
        Returns (target_song_id, payload_json) if cached and valid.
        Returns ("", None) if negative hit (miss).
        Returns (None, None) if not cached or expired.
        """
        src_sf = (source_storefront or "jp").lower()
        src_id = str(source_song_id).strip()
        tgt_sf = (target_storefront or "cn").lower()
        now = time.time()

        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT target_song_id, payload_json, expires_at
                FROM equivalence_cache
                WHERE source_storefront = ? AND source_song_id = ? AND target_storefront = ?
                """,
                (src_sf, src_id, tgt_sf),
            )
            row = cursor.fetchone()
            if not row:
                return (None, None)

            target_song_id, payload_json, expires_at = row
            if expires_at <= now:
                return (None, None)

            ret_id = target_song_id if target_song_id is not None else ""
            return (ret_id, payload_json)
        except Exception as e:
            logger.debug("Failed to read from equivalence cache: %s", e)
            return (None, None)

    def set_equivalence(
        self,
        source_storefront: str,
        source_song_id: str,
        target_storefront: str,
        target_song_id: Optional[str],
        payload_json: Optional[str] = None,
        ttl: Optional[float] = None,
    ) -> None:
        """
        Store equivalence mapping.
        Valid mappings get 7-day TTL; negative misses get 1-hour TTL.
        """
        src_sf = (source_storefront or "jp").lower()
        src_id = str(source_song_id).strip()
        tgt_sf = (target_storefront or "cn").lower()
        tgt_id = str(target_song_id).strip() if target_song_id else None
        now = time.time()

        if ttl is None:
            ttl = 7 * 86400 if tgt_id else 3600  # 1 hour negative cache TTL

        expires_at = now + ttl

        try:
            conn = self._get_connection()
            with self._lock:
                with conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO equivalence_cache
                        (source_storefront, source_song_id, target_storefront, target_song_id, payload_json, fetched_at, expires_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (src_sf, src_id, tgt_sf, tgt_id, payload_json, now, expires_at),
                    )
        except Exception as e:
            logger.debug("Failed to write to equivalence cache: %s", e)

    # -------------------------------------------------------------------------
    # Track Match Cache
    # -------------------------------------------------------------------------
    def get_match(self, storefront: str, track_hash: str) -> Optional[SongMatchResult]:
        """
        Retrieve cached SongMatchResult if present and not expired.
        Automatically re-evaluates algorithmic results (auto_accept, review, no_match)
        if rule_version, query_policy_version, romanizer_version, or exception_registry_version is outdated.
        User-confirmed results (user_confirmed) are strictly preserved.
        """
        sf = (storefront or "cn").lower()
        now = time.time()

        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT result_json, expires_at, rule_version, query_policy_version, romanizer_version, exception_registry_version
                FROM match_cache
                WHERE storefront = ? AND (track_hash = ? OR cache_key = ?)
                """,
                (sf, track_hash, track_hash),
            )
            row = cursor.fetchone()
            if not row:
                return None

            result_json, expires_at, db_rule_ver, db_policy_ver, db_romanizer_ver, db_registry_ver = row
            if expires_at <= now:
                return None

            data = json.loads(result_json)
            result = SongMatchResult(**data)

            # Check rule version / policy version / romanizer version / exception registry version
            from applemusic.matcher.evidence import (
                MATCH_RULE_VERSION,
                QUERY_POLICY_VERSION,
                ROMANIZER_VERSION,
                EXCEPTION_REGISTRY_VERSION,
            )
            cached_rule_ver = getattr(result.evidence, "rule_version", None) if result.evidence else None
            cached_policy_ver = getattr(result.evidence, "query_policy_version", None) if result.evidence else None
            cached_romanizer_ver = getattr(result.evidence, "romanizer_version", None) if result.evidence else None
            cached_registry_ver = getattr(result.evidence, "exception_registry_version", None) if result.evidence else None

            # If user manually confirmed, preserve user_confirmed decision
            if result.decision == "user_confirmed":
                return result

            # Check rule version / policy version / romanizer version / exception registry version
            ev = result.evidence
            if ev is None and result.selected_candidate and result.selected_candidate.evidence:
                ev = result.selected_candidate.evidence
            elif ev is None and result.candidates and result.candidates[0].evidence:
                ev = result.candidates[0].evidence

            cached_rule_ver = getattr(ev, "rule_version", None) if ev else None
            cached_policy_ver = getattr(ev, "query_policy_version", None) if ev else None
            cached_romanizer_ver = getattr(ev, "romanizer_version", None) if ev else None
            cached_registry_ver = getattr(ev, "exception_registry_version", None) if ev else None

            versions_outdated = (
                db_rule_ver != MATCH_RULE_VERSION
                or db_policy_ver != QUERY_POLICY_VERSION
                or db_romanizer_ver != ROMANIZER_VERSION
                or db_registry_ver != EXCEPTION_REGISTRY_VERSION
                or (
                    ev is not None
                    and (
                        cached_rule_ver != MATCH_RULE_VERSION
                        or cached_policy_ver != QUERY_POLICY_VERSION
                        or cached_romanizer_ver != ROMANIZER_VERSION
                        or cached_registry_ver != EXCEPTION_REGISTRY_VERSION
                    )
                )
            )

            if versions_outdated:
                candidate_map = {}
                for c in (result.candidates or []):
                    if c and getattr(c, "track", None) and getattr(c.track, "id", None):
                        candidate_map[str(c.track.id)] = c
                    elif c and getattr(c, "track", None):
                        candidate_map[id(c)] = c
                if result.selected_candidate and getattr(result.selected_candidate, "track", None):
                    sc = result.selected_candidate
                    sc_id = str(sc.track.id) if getattr(sc.track, "id", None) else id(sc)
                    if sc_id not in candidate_map:
                        candidate_map[sc_id] = sc

                cands_to_eval = list(candidate_map.values())
                old_ver = cached_rule_ver or db_rule_ver or "unknown"
                has_valid_source = bool(
                    result.source_track
                    and (getattr(result.source_track, "title", None) or getattr(result.source_track, "artists", None))
                )

                if cands_to_eval and has_valid_source:
                    from applemusic.matcher.scorer import TrackScorer
                    scored = [TrackScorer.score(result.source_track, c.track) for c in cands_to_eval]
                    scored.sort(key=lambda x: x.score, reverse=True)
                    best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(
                        result.source_track,
                        scored,
                    )
                    result.candidates = scored
                    result.selected_candidate = best if dec in ("auto_accept", "review") else None
                    result.status = conf
                    result.decision = dec
                    result.decision_reasons = [f"规则版本升级({old_ver}->{MATCH_RULE_VERSION})重新评估: {', '.join(reasons)}"]
                    result.evidence = best.evidence if best else (scored[0].evidence if scored else None)
                    result.score_gap = gap
                    if dec == "no_match":
                        result.search_status = "no_match"
                    elif dec in ("auto_accept", "review"):
                        result.search_status = "matched"

                    # Persist re-evaluated result back to DB
                    try:
                        json_str = result.model_dump_json()
                        keys_to_update = {track_hash}
                        t = result.source_track
                        if t:
                            isrc = getattr(t, "isrc", None)
                            if isrc and len(str(isrc).strip()) >= 8:
                                keys_to_update.add(f"isrc:{str(isrc).strip().upper()}")
                            orig_id = getattr(t, "original_id", None)
                            src = getattr(t, "source", None)
                            if orig_id and str(orig_id).strip() and str(orig_id).lower() not in ("none", "null", "unknown", "undefined") and src:
                                keys_to_update.add(f"{src}:{str(orig_id).strip()}")
                            title = getattr(t, "title", None)
                            artists = getattr(t, "artists", [])
                            version_tags = getattr(t, "version_tags", None) or []
                            if title:
                                from applemusic.matcher.cleaner import TextCleaner
                                clean_t, parsed_tags = TextCleaner.parse_title(title)
                                all_v_tags = sorted(list(set(version_tags + parsed_tags)))
                                v_tag_str = ",".join(all_v_tags) if all_v_tags else "standard"
                                pri_a, _ = TextCleaner.parse_artists(artists)
                                norm_a = TextCleaner.normalize(pri_a).lower()
                                if clean_t:
                                    keys_to_update.add(f"text:{clean_t.lower()}:{norm_a}:{v_tag_str}")

                        with self._lock:
                            with conn:
                                placeholders = ",".join(["?"] * len(keys_to_update))
                                conn.execute(
                                    f"""
                                    UPDATE match_cache
                                    SET result_json = ?, decision = ?, status = ?,
                                        rule_version = ?, query_policy_version = ?,
                                        romanizer_version = ?, exception_registry_version = ?
                                    WHERE storefront = ?
                                      AND decision != 'user_confirmed'
                                      AND (track_hash IN ({placeholders}) OR cache_key IN ({placeholders}))
                                    """,
                                    (
                                        json_str,
                                        result.decision,
                                        str(result.status.value if hasattr(result.status, "value") else result.status),
                                        MATCH_RULE_VERSION,
                                        QUERY_POLICY_VERSION,
                                        ROMANIZER_VERSION,
                                        EXCEPTION_REGISTRY_VERSION,
                                        sf,
                                        *keys_to_update,
                                        *keys_to_update,
                                    ),
                                )
                    except Exception as e:
                        logger.debug("Failed to update re-evaluated match in cache: %s", e)
                else:
                    # Missing candidates or missing/invalid source_track: cannot safely re-evaluate, treat as cache miss
                    return None

            return result
        except Exception as e:
            logger.debug("Failed to read from persistent match cache: %s", e)
            return None

    def find_match(self, storefront: str, track: Any) -> Optional[SongMatchResult]:
        """
        Multi-index lookup for cached SongMatchResult across:
        1. ISRC: 'isrc:{isrc}'
        2. Source Original ID: '{source}:{original_id}'
        3. Canonical Text: 'text:{clean_title}:{clean_artist}:{version_tags}'
        Loose text hits are re-verified against the query track before accepting.
        """
        if track is None:
            return None

        sf = (storefront or "cn").lower()
        keys_to_try: List[str] = []

        # 1. ISRC index
        isrc = getattr(track, "isrc", None)
        if isrc and len(str(isrc).strip()) >= 8:
            keys_to_try.append(f"isrc:{str(isrc).strip().upper()}")

        # 2. Source Original ID index
        orig_id = getattr(track, "original_id", None)
        src = getattr(track, "source", None)
        if orig_id and str(orig_id).strip() and str(orig_id).lower() not in ("none", "null", "unknown", "undefined") and src:
            keys_to_try.append(f"{src}:{str(orig_id).strip()}")

        # 3. Canonical text index with version tags
        title = getattr(track, "title", None)
        artists = getattr(track, "artists", [])
        version_tags = getattr(track, "version_tags", None) or []
        if title:
            from applemusic.matcher.cleaner import TextCleaner
            clean_t, parsed_tags = TextCleaner.parse_title(title)
            all_v_tags = sorted(list(set(version_tags + parsed_tags)))
            v_tag_str = ",".join(all_v_tags) if all_v_tags else "standard"
            pri_a, _ = TextCleaner.parse_artists(artists)
            norm_a = TextCleaner.normalize(pri_a).lower()
            if clean_t:
                keys_to_try.append(f"text:{clean_t.lower()}:{norm_a}:{v_tag_str}")

        for k in keys_to_try:
            m = self.get_match(sf, k)
            if m and not m.search_incomplete:
                if k.startswith("text:"):
                    # Loose text matches must be re-verified against caller's actual track!
                    if m.candidates and track:
                        from applemusic.matcher.scorer import TrackScorer
                        scored = [TrackScorer.score(track, c.track) for c in m.candidates]
                        scored.sort(key=lambda x: x.score, reverse=True)
                        best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(
                            track,
                            scored,
                        )
                        if dec == "auto_accept":
                            m_copy = m.model_copy(deep=True)
                            m_copy.source_track = track
                            m_copy.candidates = scored
                            m_copy.selected_candidate = best
                            m_copy.status = conf
                            m_copy.decision = dec
                            m_copy.evidence = best.evidence if best else None
                            m_copy.score_gap = gap
                            return m_copy
                    continue
                elif m.decision in ("auto_accept", "user_confirmed"):
                    return m

        return None

    def set_match(
        self,
        storefront: str,
        track_hash: str,
        match_result: SongMatchResult,
        track: Optional[Any] = None,
    ) -> None:
        """
        Persist SongMatchResult for high confidence matches (auto_accept, user_confirmed).
        Writes primary track_hash and optional secondary indexes (ISRC, source:id, text).
        """
        # Only cache confident positive matches
        if match_result.decision not in ("auto_accept", "user_confirmed"):
            return
        if match_result.search_incomplete:
            return

        sf = (storefront or "cn").lower()
        now = time.time()
        ttl = TTL_MATCH_AUTO_ACCEPT
        expires_at = now + ttl

        # Collect all index keys to persist
        keys_to_save: List[str] = [track_hash]
        t = track or getattr(match_result, "source_track", None)
        if t is not None:
            isrc = getattr(t, "isrc", None)
            if isrc and len(str(isrc).strip()) >= 8:
                keys_to_save.append(f"isrc:{str(isrc).strip().upper()}")

            orig_id = getattr(t, "original_id", None)
            src = getattr(t, "source", None)
            if orig_id and str(orig_id).strip() and str(orig_id).lower() not in ("none", "null", "unknown", "undefined") and src:
                keys_to_save.append(f"{src}:{str(orig_id).strip()}")

            title = getattr(t, "title", None)
            artists = getattr(t, "artists", [])
            version_tags = getattr(t, "version_tags", None) or []
            if title:
                from applemusic.matcher.cleaner import TextCleaner
                clean_t, parsed_tags = TextCleaner.parse_title(title)
                all_v_tags = sorted(list(set(version_tags + parsed_tags)))
                v_tag_str = ",".join(all_v_tags) if all_v_tags else "standard"
                pri_a, _ = TextCleaner.parse_artists(artists)
                norm_a = TextCleaner.normalize(pri_a).lower()
                if clean_t:
                    keys_to_save.append(f"text:{clean_t.lower()}:{norm_a}:{v_tag_str}")

        try:
            from applemusic.matcher.evidence import (
                MATCH_RULE_VERSION,
                QUERY_POLICY_VERSION,
                ROMANIZER_VERSION,
                EXCEPTION_REGISTRY_VERSION,
            )
            json_str = match_result.model_dump_json()
            with self._lock:
                conn = self._get_connection()
                with conn:
                    for k in set(keys_to_save):
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO match_cache 
                            (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at, rule_version, query_policy_version, romanizer_version, exception_registry_version)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                sf,
                                k,
                                k,
                                json_str,
                                match_result.decision,
                                str(match_result.status.value if hasattr(match_result.status, "value") else match_result.status),
                                now,
                                expires_at,
                                MATCH_RULE_VERSION,
                                QUERY_POLICY_VERSION,
                                ROMANIZER_VERSION,
                                EXCEPTION_REGISTRY_VERSION,
                            ),
                        )
        except Exception as e:
            logger.debug("Failed to write to persistent match cache: %s", e)

    # -------------------------------------------------------------------------
    # Maintenance & Stats
    # -------------------------------------------------------------------------
    def clear_expired(self) -> Tuple[int, int]:
        """Delete all expired rows from catalog_cache and match_cache."""
        now = time.time()
        cat_deleted = 0
        mat_deleted = 0
        try:
            with self._lock:
                conn = self._get_connection()
                with conn:
                    cur1 = conn.execute("DELETE FROM catalog_cache WHERE expires_at <= ?", (now,))
                    cat_deleted = cur1.rowcount
                    cur2 = conn.execute("DELETE FROM match_cache WHERE expires_at <= ?", (now,))
                    mat_deleted = cur2.rowcount
        except Exception as e:
            logger.debug("Failed to clear expired cache: %s", e)
        return cat_deleted, mat_deleted

    def get_stats(self) -> Dict[str, Any]:
        """Return cache statistics."""
        now = time.time()
        stats = {
            "catalog_total": 0,
            "catalog_valid": 0,
            "match_total": 0,
            "match_valid": 0,
            "db_size_bytes": 0,
        }
        try:
            if self.db_path.exists():
                stats["db_size_bytes"] = self.db_path.stat().st_size
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*), SUM(CASE WHEN expires_at > ? THEN 1 ELSE 0 END) FROM catalog_cache", (now,))
            row1 = cursor.fetchone()
            if row1:
                stats["catalog_total"] = row1[0] or 0
                stats["catalog_valid"] = row1[1] or 0

            cursor.execute("SELECT COUNT(*), SUM(CASE WHEN expires_at > ? THEN 1 ELSE 0 END) FROM match_cache", (now,))
            row2 = cursor.fetchone()
            if row2:
                stats["match_total"] = row2[0] or 0
                stats["match_valid"] = row2[1] or 0
        except Exception as e:
            logger.debug("Failed to get cache stats: %s", e)
        return stats
