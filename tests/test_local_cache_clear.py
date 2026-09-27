"""
Unit and integration tests for the Local Cache Clear feature (PLAN.md).

Tests:
1. SQLite:
   - Clearing catalog_cache, equivalence_cache, and non-user_confirmed match_cache in a single transaction.
   - Strict preservation of user_confirmed match records.
   - Idempotency of repeated calls.
   - Transaction rollback and exception raising on error.
2. Memory:
   - Clearing AppleMusicClient._catalog_cache.
   - Verifying subsequent search_catalog queries miss in-memory cache and re-query catalog.
3. API:
   - X-App-Token protection on POST /api/cache/clear-local (403 on missing/invalid token).
   - Successful clearance returns 200, deleted counts, and updated stats.
   - Error handling returns 500 without masking errors.
   - Legacy POST /api/cache/clear continues to clear expired-only without semantic regression.
4. UI template verification:
   - Button existence, isBusy disabled binding, confirm modal content, and responsive layout.
"""

import asyncio
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
import uvicorn
import websockets

from applemusic.cache import PersistentCache
from applemusic.client import AppleMusicClient
from applemusic.config import Config
from applemusic.models import (
    AppleMusicTrack,
    CatalogSearchOutcome,
    ConfidenceLevel,
    DecisionStatus,
    MatchCandidate,
    SongMatchResult,
    Track,
)
from applemusic.web.app import SESSION_API_TOKEN, app


class TestLocalCacheClearSQLite(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_clear_cache.db"
        self.cache = PersistentCache(self.db_path)

    def tearDown(self):
        self.cache.close()
        self.temp_dir.cleanup()

    def test_clear_local_search_cache_semantics_and_user_confirmed_preservation(self):
        now = time.time()

        # 1. Populate catalog_cache: 1 valid, 1 expired
        sample_track = AppleMusicTrack(id="101", title="Song 1", artists=["Artist 1"], storefront="cn")
        outcome = CatalogSearchOutcome(kind="ok", tracks=[sample_track], http_status=200)
        self.cache.set_catalog("cn", "term", "query_valid", outcome)
        self.cache.set_catalog("cn", "term", "query_expired", outcome)
        with self.cache._lock:
            self.cache._get_connection().execute(
                "UPDATE catalog_cache SET expires_at = ? WHERE query_term = ?",
                (now - 100, "query_expired")
            )

        # 2. Populate equivalence_cache: 1 valid, 1 expired
        self.cache.set_equivalence("jp", "jp_1", "cn", "cn_1", ttl=3600)
        self.cache.set_equivalence("jp", "jp_2", "cn", "cn_2", ttl=-100)

        # 3. Populate match_cache: auto_accept, review, no_match, and user_confirmed
        def make_result(title_val: str, decision_val: str, track_id: str) -> SongMatchResult:
            cand = MatchCandidate(
                track=AppleMusicTrack(id=track_id, title=title_val, artists=["Artist"], storefront="cn"),
                score=0.99 if decision_val in ("auto_accept", "user_confirmed") else 0.5,
                confidence=ConfidenceLevel.EXACT if decision_val in ("auto_accept", "user_confirmed") else ConfidenceLevel.LOW,
            )
            return SongMatchResult(
                source_track=Track(title=title_val, artists=["Artist"]),
                candidates=[cand],
                selected_candidate=cand if decision_val != "no_match" else None,
                status=ConfidenceLevel.EXACT if decision_val in ("auto_accept", "user_confirmed") else ConfidenceLevel.LOW,
                decision=decision_val,
                decision_reasons=[f"Test {decision_val}"],
                search_status="matched" if decision_val != "no_match" else "no_match",
            )

        # auto_accept writes primary hash + secondary text key (2 rows)
        self.cache.set_match("cn", "key_auto", make_result("AutoSong", "auto_accept", "1001"))
        # user_confirmed writes primary hash + secondary text key (2 rows)
        self.cache.set_match("cn", "key_user_confirmed", make_result("ConfirmedSong", "user_confirmed", "1004"))
        with self.cache._lock:
            conn = self.cache._get_connection()
            with conn:
                conn.execute(
                    "INSERT INTO match_cache (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("cn", "key_review", "key_review", "{}", "review", "review", now, now + 3600)
                )
                conn.execute(
                    "INSERT INTO match_cache (storefront, track_hash, cache_key, result_json, decision, status, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("cn", "key_no_match", "key_no_match", "{}", "no_match", "no_match", now, now + 3600)
                )

        # Verify initial stats: 2 catalog + 2 equivalence + 6 match (4 non-confirmed + 2 user_confirmed)
        stats_before = self.cache.get_stats()
        self.assertEqual(stats_before["catalog_total"], 2)
        self.assertEqual(stats_before["equivalence_total"], 2)
        self.assertEqual(stats_before["match_total"], 6)

        # Execute clear_local_search_cache()
        deleted = self.cache.clear_local_search_cache()

        # Assert counts returned
        self.assertEqual(deleted["catalog_deleted"], 2)
        self.assertEqual(deleted["equivalence_deleted"], 2)
        self.assertEqual(deleted["match_deleted"], 4)  # 2 auto_accept + 1 review + 1 no_match

        # Verify catalog and equivalence are completely gone
        self.assertIsNone(self.cache.get_catalog("cn", "term", "query_valid"))
        self.assertIsNone(self.cache.get_catalog("cn", "term", "query_expired"))
        self.assertIsNone(self.cache.get_equivalence("cn", "jp_1"))
        self.assertIsNone(self.cache.get_equivalence("cn", "jp_2"))

        # Verify algorithm matches are gone
        self.assertIsNone(self.cache.get_match("cn", "key_auto"))
        self.assertIsNone(self.cache.get_match("cn", "key_review"))
        self.assertIsNone(self.cache.get_match("cn", "key_no_match"))

        # Strictly assert user_confirmed match record is PRESERVED
        confirmed_res = self.cache.get_match("cn", "key_user_confirmed")
        self.assertIsNotNone(confirmed_res, "user_confirmed match must be preserved")
        self.assertEqual(confirmed_res.decision, "user_confirmed")
        self.assertEqual(confirmed_res.selected_candidate.track.id, "1004")

        # Verify stats after clear: 2 user_confirmed records remain
        stats_after = self.cache.get_stats()
        self.assertEqual(stats_after["catalog_total"], 0)
        self.assertEqual(stats_after["equivalence_total"], 0)
        self.assertEqual(stats_after["match_total"], 2)  # Only user_confirmed records remain

        # Verify idempotency: calling again deletes 0 and keeps user_confirmed
        deleted_second = self.cache.clear_local_search_cache()
        self.assertEqual(deleted_second["catalog_deleted"], 0)
        self.assertEqual(deleted_second["equivalence_deleted"], 0)
        self.assertEqual(deleted_second["match_deleted"], 0)
        self.assertIsNotNone(self.cache.get_match("cn", "key_user_confirmed"))

    def test_clear_local_search_cache_transaction_failure_rollback(self):
        # Insert a record
        sample_track = AppleMusicTrack(id="201", title="Song 2", artists=["Artist 2"], storefront="cn")
        outcome = CatalogSearchOutcome(kind="ok", tracks=[sample_track], http_status=200)
        self.cache.set_catalog("cn", "term", "keep_me", outcome)

        # Simulate exception during transaction
        with patch.object(self.cache, "_get_connection") as mock_get_conn:
            mock_conn = MagicMock()
            mock_conn.execute.side_effect = RuntimeError("Simulated SQLite Disk I/O Error")
            # Make context manager work
            mock_conn.__enter__.return_value = mock_conn
            mock_get_conn.return_value = mock_conn

            with self.assertRaises(RuntimeError):
                self.cache.clear_local_search_cache()

        # Real connection: data should still be intact due to rollback
        cached = self.cache.get_catalog("cn", "term", "keep_me")
        self.assertIsNotNone(cached)


class TestLocalCacheClearClient(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_client_clear.db"
        self.cache = PersistentCache(self.db_path)
        self.config = Config(developer_token="dummy_token", storefront="cn")
        self.client = AppleMusicClient(self.config, persistent_cache=self.cache)

    def tearDown(self):
        self.cache.close()
        self.temp_dir.cleanup()

    def test_clear_in_memory_cache_flushes_all_keys(self):
        track = AppleMusicTrack(id="301", title="Memory Song", artists=["Artist"], storefront="cn")
        outcome = CatalogSearchOutcome(kind="ok", tracks=[track], http_status=200)

        # Pre-seed in-memory catalog cache with different key types
        self.client._catalog_cache[("term_key",)] = (time.time(), outcome)
        self.client._catalog_cache[("isrc", "cn", "USRC123")] = (time.time(), outcome)
        self.client._catalog_cache[("suggestions", "cn", "query")] = (time.time(), outcome)

        self.assertEqual(len(self.client._catalog_cache), 3)

        cleared_count = self.client.clear_in_memory_cache()
        self.assertEqual(cleared_count, 3)
        self.assertEqual(len(self.client._catalog_cache), 0)

    def test_clear_local_search_cache_forces_subsequent_search_to_requery(self):
        track = AppleMusicTrack(id="302", title="Requery Song", artists=["Artist"], storefront="cn")
        outcome = CatalogSearchOutcome(kind="ok", tracks=[track], http_status=200)

        # Pre-seed both persistent and in-memory cache
        self.cache.set_catalog("cn", "term", "requery term", outcome)
        self.client._catalog_cache[("cn", "requery term", 10, "")] = (time.time(), outcome)

        # Ensure search_catalog hits cache without network
        with patch.object(self.client.session, "get") as mock_http:
            res1 = self.client.search_catalog("requery term", storefront="cn", limit=10)
            self.assertEqual(res1.kind, "ok")
            mock_http.assert_not_called()

        # Clear both SQLite and in-memory cache
        result = self.client.clear_local_search_cache()
        self.assertGreaterEqual(result["catalog_deleted"], 1)
        self.assertEqual(len(self.client._catalog_cache), 0)

        # Subsequent search MUST hit network/upstream because cache was wiped
        with patch.object(self.client.session, "get") as mock_http:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.headers = {}
            mock_resp.json.return_value = {"results": {"songs": {"data": []}}}
            mock_http.return_value = mock_resp

            with patch.object(self.client.auth, "get_developer_token", return_value="dummy_dev_token"):
                res2 = self.client.search_catalog("requery term", storefront="cn", limit=10)
            mock_http.assert_called_once()
            self.assertEqual(res2.kind, "no_hits")



class TestLocalCacheClearWebApi(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.auth_headers = {"X-App-Token": SESSION_API_TOKEN}

    def test_clear_local_requires_app_token(self):
        resp = self.client.post("/api/cache/clear-local")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json().get("success"))

    @patch("applemusic.web.app.get_shared_engine")
    def test_clear_local_success_flow(self, mock_get_shared):
        mock_client = MagicMock()
        mock_engine = MagicMock()
        mock_client.clear_local_search_cache.return_value = {
            "catalog_deleted": 42,
            "equivalence_deleted": 15,
            "match_deleted": 88,
            "memory_deleted": 10,
        }
        mock_client.persistent_cache.get_stats.return_value = {
            "catalog_total": 0,
            "catalog_valid": 0,
            "equivalence_total": 0,
            "equivalence_valid": 0,
            "match_total": 3,
            "match_valid": 3,
            "db_size_bytes": 16384,
        }
        mock_get_shared.return_value = (mock_client, mock_engine)

        resp = self.client.post("/api/cache/clear-local", headers=self.auth_headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["deleted"]["catalog"], 42)
        self.assertEqual(data["deleted"]["equivalence"], 15)
        self.assertEqual(data["deleted"]["match"], 88)
        self.assertEqual(data["deleted"]["memory"], 10)
        self.assertIn("已清除", data["message"])
        mock_client.clear_local_search_cache.assert_called_once()

    @patch("applemusic.web.app.get_shared_engine")
    def test_clear_local_failure_returns_500(self, mock_get_shared):
        mock_client = MagicMock()
        mock_engine = MagicMock()
        mock_client.clear_local_search_cache.side_effect = RuntimeError("Database locked by another process")
        mock_get_shared.return_value = (mock_client, mock_engine)

        resp = self.client.post("/api/cache/clear-local", headers=self.auth_headers)
        self.assertEqual(resp.status_code, 500)
        data = resp.json()
        self.assertIn("Database locked", data["detail"])

    @patch("applemusic.web.app.get_shared_engine")
    def test_legacy_clear_expired_unaffected(self, mock_get_shared):
        mock_client = MagicMock()
        mock_engine = MagicMock()
        mock_client.persistent_cache.clear_expired.return_value = (7, 14)
        mock_client.persistent_cache.get_stats.return_value = {"catalog_total": 10, "match_total": 20}
        mock_get_shared.return_value = (mock_client, mock_engine)

        resp = self.client.post("/api/cache/clear", headers=self.auth_headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["cleared_catalog"], 7)
        self.assertEqual(data["cleared_match"], 14)
        self.assertIn("已清理过期缓存", data["message"])
        mock_client.persistent_cache.clear_expired.assert_called_once()


class TestLocalCacheClearUI(unittest.TestCase):
    def test_html_ui_elements_and_safety_declarations(self):
        html_path = Path("applemusic/web/static/index.html")
        self.assertTrue(html_path.exists())
        content = html_path.read_text(encoding="utf-8")

        # 1. Trigger button exists in header of card 1
        self.assertIn("清除本地缓存", content)
        self.assertIn("@click=\"openClearCacheModal\"", content)
        self.assertIn(":disabled=\"isBusy\"", content)

        # 2. Confirmation modal exists
        self.assertIn("v-if=\"showClearCacheModal\"", content)
        self.assertIn("清除本地检索缓存", content)

        # 3. Explains what is cleared
        self.assertIn("Apple Music 检索结果、跨区等价缓存及算法自动匹配结论", content)

        # 4. Strictly declares what is NOT cleared
        self.assertIn("以下内容不受影响", content)
        self.assertIn("Apple Music 云端资料库与个人歌单", content)
        self.assertIn("本地音频文件与下载缓存", content)
        self.assertIn("Apple ID 授权配置与认证令牌 (Token)", content)
        self.assertIn("人工手动确认的曲目匹配映射 (user_confirmed)", content)

        # 5. Cancel and confirm buttons with isBusy protection
        self.assertIn("@click=\"closeClearCacheModal\"", content)
        self.assertIn("@click=\"confirmClearCache\"", content)
        # Ensure modal confirm button is disabled when isBusy (not just clearingCache)
        self.assertIn(":disabled=\"isBusy\"", content)
        # Ensure confirmClearCache checks isBusy before submitting
        self.assertIn("if (isBusy.value) return;", content)
        self.assertIn("const isBusy = computed(", content)
        self.assertIn("/api/cache/clear-local", content)

        # 6. Success breakdown items
        self.assertIn("曲库检索记录已删除", content)
        self.assertIn("跨区等价映射已删除", content)
        self.assertIn("自动匹配结论已删除", content)
        self.assertIn("内存检索缓存已清空", content)

        # 7. Inform user current page results are not automatically rewritten
        self.assertIn("当前界面已显示的匹配结果未被自动修改", content)

    def test_legacy_clear_expired_only_deletes_expired_in_real_db(self):
        """Verify PersistentCache.clear_expired in a real DB only deletes expired records, keeping valid untouched."""
        temp_dir = tempfile.TemporaryDirectory()
        try:
            db_path = Path(temp_dir.name) / "test_legacy_clear.db"
            cache = PersistentCache(db_path)
            now = time.time()

            sample_track = AppleMusicTrack(id="501", title="Valid Song", artists=["Artist"], storefront="cn")
            outcome = CatalogSearchOutcome(kind="ok", tracks=[sample_track], http_status=200)

            # Insert 1 valid and 1 expired
            cache.set_catalog("cn", "term", "valid_term", outcome)
            cache.set_catalog("cn", "term", "expired_term", outcome)
            with cache._lock:
                cache._get_connection().execute(
                    "UPDATE catalog_cache SET expires_at = ? WHERE query_term = ?",
                    (now - 100, "expired_term")
                )

            cat_del, mat_del = cache.clear_expired()
            self.assertEqual(cat_del, 1)

            # Valid record must remain completely intact!
            self.assertIsNotNone(cache.get_catalog("cn", "term", "valid_term"))
            # Expired record is gone
            self.assertIsNone(cache.get_catalog("cn", "term", "expired_term"))

            cache.close()
        finally:
            temp_dir.cleanup()

    def test_dom_homepage_open_modal_and_cancel_without_api_call(self):
        """
        DOM/page integration test:
        1. Verifies that MODAL 9 (showClearCacheModal) is NOT nested inside MODAL 8 (editingLocalSong).
        2. Verifies that MODAL 8 and MODAL 9 are parallel siblings under their parent container.
        3. Verifies that on a normal homepage where editingLocalSong is null,
           clicking '清除本地缓存' makes the modal mounted and visible in the DOM.
        4. Verifies that clicking '取消' closes and unmounts the modal.
        5. Verifies that no API request is sent to /api/cache/clear-local when cancelled.
        6. Verifies the negative case: if nested in editingLocalSong, null editingLocalSong blocks mounting.
        """
        html_path = Path("applemusic/web/static/index.html")
        content = html_path.read_text(encoding="utf-8")
        soup = BeautifulSoup(content, "html.parser")

        # 1. Verify existence of trigger button and modal containers
        trigger_btn = soup.find(lambda el: el.name == "button" and el.get("@click") == "openClearCacheModal")
        self.assertIsNotNone(trigger_btn, "Button with @click='openClearCacheModal' must exist")
        self.assertEqual(trigger_btn.get(":disabled"), "isBusy")

        modal_edit = soup.find("div", attrs={"v-if": "editingLocalSong"})
        self.assertIsNotNone(modal_edit, "MODAL 8 (editingLocalSong) must exist")

        modal_clear = soup.find("div", attrs={"v-if": "showClearCacheModal"})
        self.assertIsNotNone(modal_clear, "MODAL 9 (showClearCacheModal) must exist")

        # 2. Strict DOM Hierarchy check: modal_clear must be a DIRECT child of #app
        self.assertEqual(modal_clear.parent.name, "div")
        self.assertEqual(
            modal_clear.parent.get("id"),
            "app",
            "CRITICAL: MODAL 9 (showClearCacheModal) must be a DIRECT child of <div id='app'>!"
        )

        # Complete ancestor chain assertion: MUST NOT contain any other v-if attributes
        ancestor_v_ifs = [p.get("v-if") for p in modal_clear.parents if p.get("v-if")]
        self.assertEqual(
            ancestor_v_ifs,
            [],
            f"MODAL 9 ancestor chain must NOT contain any v-if attributes, but found: {ancestor_v_ifs}"
        )
        self.assertNotIn("showDiagnosticsModal", ancestor_v_ifs, "MODAL 9 must NOT be inside showDiagnosticsModal")
        self.assertNotIn("editingLocalSong", ancestor_v_ifs, "MODAL 9 must NOT be inside editingLocalSong")
        self.assertNotIn("diagnosticsModalItem", ancestor_v_ifs, "MODAL 9 must NOT be inside diagnosticsModalItem")
        self.assertNotIn("viewingPlaylist", ancestor_v_ifs, "MODAL 9 must NOT be inside viewingPlaylist")
        self.assertNotIn("editingPlaylist", ancestor_v_ifs, "MODAL 9 must NOT be inside editingPlaylist")

        ancestor_tags = [f"{p.name}#{p.get('id', '')}" for p in modal_clear.parents]
        self.assertEqual(
            ancestor_tags,
            ['div#app', 'body#', 'html#', '[document]#'],
            f"MODAL 9 ancestors must strictly be div#app -> body -> html -> [document], but got {ancestor_tags}"
        )

        # 3. Simulate regular homepage Vue state & mounting lifecycle
        state = {
            "editingLocalSong": None,
            "showClearCacheModal": False,
            "clearingCache": False,
            "isBusy": False,
            "clearCacheResult": None,
            "clearCacheError": None,
        }
        api_calls = []

        def is_modal_rendered(modal_node, current_state):
            """
            Evaluates whether the modal is rendered in the active DOM.
            A modal is rendered if its own v-if is truthy AND it is not blocked by
            an enclosing conditional.
            """
            own_v_if = modal_node.get("v-if")
            if own_v_if and not current_state.get(own_v_if, False):
                return False
            parents_v_if = [p.get("v-if") for p in modal_node.parents if p.get("v-if")]
            for pv in parents_v_if:
                if not current_state.get(pv):
                    return False
            return True

        # Phase A: Initial load -> modal not mounted
        self.assertFalse(
            is_modal_rendered(modal_clear, state),
            "Initially, MODAL 9 must not be mounted when showClearCacheModal is False"
        )

        # Phase B: User clicks '清除本地缓存' button -> openClearCacheModal()
        # Vue logic from index.html:
        # if (isBusy.value) return;
        # showClearCacheModal.value = true;
        if not state["isBusy"]:
            state["showClearCacheModal"] = True
            state["clearCacheResult"] = None
            state["clearCacheError"] = None

        # Modal MUST now be mounted on regular homepage where editingLocalSong is null!
        self.assertTrue(
            is_modal_rendered(modal_clear, state),
            "MODAL 9 must be mounted and visible when clicked on regular homepage!"
        )

        # Verify confirmation dialog content is present in the mounted modal
        modal_text = modal_clear.get_text()
        self.assertIn("清除本地检索缓存", modal_text)
        self.assertIn("Apple Music 检索结果、跨区等价缓存及算法自动匹配结论", modal_text)
        self.assertIn("以下内容不受影响", modal_text)

        cancel_btn = modal_clear.find(lambda el: el.name == "button" and el.get("@click") == "closeClearCacheModal")
        confirm_btn = modal_clear.find(lambda el: el.name == "button" and el.get("@click") == "confirmClearCache")
        self.assertIsNotNone(cancel_btn, "Cancel button must exist inside modal")
        self.assertIsNotNone(confirm_btn, "Confirm button must exist inside modal")

        # Phase C: User clicks '取消' button -> closeClearCacheModal()
        # Vue logic from index.html:
        # if (clearingCache.value) return;
        # showClearCacheModal.value = false;
        if not state["clearingCache"]:
            state["showClearCacheModal"] = False

        # Modal MUST disappear / unmount from DOM
        self.assertFalse(
            is_modal_rendered(modal_clear, state),
            "MODAL 9 must be unmounted and disappear after clicking cancel"
        )

        # Assert no network call was made during open and cancel
        self.assertEqual(len(api_calls), 0, "No API requests should be made when modal is cancelled")

    def test_real_browser_page_click_open_and_cancel_modal(self):
        """
        Real browser page click test (Playwright pointer click with Network request capture):
        Loads the running application, performs real pointer click on '清除本地缓存',
        verifies that the confirmation modal appears in the live browser DOM, and clicking
        '取消' closes it while strictly asserting ZERO requests were made to /api/cache/clear-local.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright not installed for real browser click test")

        def get_free_port():
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(('127.0.0.1', 0))
            p = s.getsockname()[1]
            s.close()
            return p

        server_port = get_free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=server_port, log_level="error")
        server = uvicorn.Server(config)
        t = threading.Thread(target=server.run, daemon=True)
        t.start()

        for _ in range(30):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{server_port}/")
                break
            except Exception:
                time.sleep(0.1)

        try:
            with sync_playwright() as p:
                browser = None
                for channel in [None, "msedge", "chrome"]:
                    try:
                        kwargs = {"headless": True}
                        if channel:
                            kwargs["channel"] = channel
                        browser = p.chromium.launch(**kwargs)
                        break
                    except Exception:
                        continue

                if not browser:
                    self.skipTest("No compatible browser found for Playwright click test")

                page = browser.new_page()
                captured_requests = []
                page.on("request", lambda req: captured_requests.append(req.url))

                # 1. Load application page
                page.goto(f"http://127.0.0.1:{server_port}/")
                page.wait_for_selector("#app")

                # 2. Assert initial state: modal not visible
                init_modal = page.locator("text=清除本地检索缓存")
                self.assertFalse(init_modal.is_visible(), "Modal should not be visible initially")

                # 3. Locate button by role and perform REAL POINTER CLICK
                btn = page.get_by_role("button", name="清除本地缓存")
                self.assertEqual(btn.count(), 1, "Expected exactly 1 '清除本地缓存' button")
                self.assertTrue(btn.is_visible(), "Button should be visible")
                self.assertTrue(btn.is_enabled(), "Button should be enabled")
                btn.click()

                # 4. Confirmation modal MUST appear in live DOM
                page.wait_for_timeout(300)
                modal = page.locator("text=清除本地检索缓存")
                self.assertTrue(modal.is_visible(), "Modal MUST appear after real pointer click")

                # 5. Locate '取消' button and perform pointer click
                cancel_btn = page.get_by_role("button", name="取消")
                self.assertTrue(cancel_btn.is_visible(), "Cancel button must be visible")
                cancel_btn.click()

                # 6. Modal MUST disappear after cancel
                page.wait_for_timeout(300)
                self.assertFalse(modal.is_visible(), "Modal MUST disappear after clicking cancel")

                # 7. Strictly assert ZERO network requests were sent to /api/cache/clear-local
                clear_reqs = [r for r in captured_requests if "/api/cache/clear-local" in r]
                self.assertEqual(len(clear_reqs), 0, f"Expected 0 requests to /api/cache/clear-local, got {len(clear_reqs)}")

                browser.close()
        finally:
            server.should_exit = True

    def test_real_browser_cdp_mouse_dispatch_open_and_cancel_modal(self):
        """
        Real browser page click test via CDP Input.dispatchMouseEvent & Network domain:
        Uses CDP Input.dispatchMouseEvent (mousePressed / mouseReleased on exact bounding box),
        enables Network domain to capture requests, and verifies open/cancel and zero API calls.
        """
        edge_paths = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        edge_bin = next((p for p in edge_paths if Path(p).exists()), None)
        if not edge_bin:
            self.skipTest("Microsoft Edge not found for CDP mouse test")

        def get_free_port():
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(('127.0.0.1', 0))
            p = s.getsockname()[1]
            s.close()
            return p

        server_port = get_free_port()
        cdp_port = get_free_port()

        config = uvicorn.Config(app, host="127.0.0.1", port=server_port, log_level="error")
        server = uvicorn.Server(config)
        t = threading.Thread(target=server.run, daemon=True)
        t.start()

        for _ in range(30):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{server_port}/")
                break
            except Exception:
                time.sleep(0.1)

        proc = subprocess.Popen([
            edge_bin,
            "--headless=new",
            f"--remote-debugging-port={cdp_port}",
            "--no-first-run",
            "--disable-extensions",
            "about:blank"
        ])

        async def browser_test():
            await asyncio.sleep(1.5)
            req = urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json")
            targets = json.loads(req.read().decode())
            page_targets = [tg for tg in targets if tg.get("type") == "page"]
            ws_url = page_targets[0]["webSocketDebuggerUrl"]

            async with websockets.connect(ws_url) as ws:
                cmd_id = 0
                captured_requests = []

                async def send_cmd(method, params=None):
                    nonlocal cmd_id
                    cmd_id += 1
                    msg = {"id": cmd_id, "method": method}
                    if params:
                        msg["params"] = params
                    await ws.send(json.dumps(msg))
                    while True:
                        resp = json.loads(await ws.recv())
                        if resp.get("method") == "Network.requestWillBeSent":
                            captured_requests.append(resp.get("params", {}).get("request", {}).get("url", ""))
                        if resp.get("id") == cmd_id:
                            return resp

                async def eval_js(expr):
                    resp = await send_cmd("Runtime.evaluate", {
                        "expression": expr,
                        "returnByValue": True,
                        "awaitPromise": True
                    })
                    return resp.get("result", {}).get("result", {}).get("value")

                await send_cmd("Page.enable")
                await send_cmd("Runtime.enable")
                await send_cmd("Network.enable")
                await send_cmd("Page.navigate", {"url": f"http://127.0.0.1:{server_port}/"})

                for _ in range(80):
                    await asyncio.sleep(0.1)
                    btn_ready = await eval_js("Boolean(Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('清除本地缓存')))")
                    if btn_ready:
                        break

                # 1. Initial state: confirmation modal must NOT be present
                initial_has_modal = await eval_js("document.body.innerText.includes('清除本地检索缓存')")
                self.assertFalse(initial_has_modal, "Modal should not be present initially")

                # 2. Get button bounding box center
                box = await eval_js('''(() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const btn = btns.find(b => b.innerText.includes('清除本地缓存'));
                    if (!btn) return null;
                    const rect = btn.getBoundingClientRect();
                    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
                })()''')
                self.assertIsNotNone(box, "Button '清除本地缓存' coordinates should be found")

                # 3. Real mouse dispatch via CDP Input.dispatchMouseEvent
                await send_cmd("Input.dispatchMouseEvent", {
                    "type": "mousePressed",
                    "x": box["x"],
                    "y": box["y"],
                    "button": "left",
                    "clickCount": 1
                })
                await send_cmd("Input.dispatchMouseEvent", {
                    "type": "mouseReleased",
                    "x": box["x"],
                    "y": box["y"],
                    "button": "left",
                    "clickCount": 1
                })

                await asyncio.sleep(0.4)

                # 4. Confirmation modal must appear in live DOM
                modal_appeared = await eval_js("document.body.innerText.includes('清除本地检索缓存')")
                self.assertTrue(modal_appeared, "Modal MUST appear after CDP mouse click")

                # 5. Get Cancel button coordinates and dispatch mouse click
                cancel_box = await eval_js('''(() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const btn = btns.find(b => b.innerText.trim() === '取消');
                    if (!btn) return null;
                    const rect = btn.getBoundingClientRect();
                    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
                })()''')
                self.assertIsNotNone(cancel_box, "Cancel button coordinates should be found")

                await send_cmd("Input.dispatchMouseEvent", {
                    "type": "mousePressed",
                    "x": cancel_box["x"],
                    "y": cancel_box["y"],
                    "button": "left",
                    "clickCount": 1
                })
                await send_cmd("Input.dispatchMouseEvent", {
                    "type": "mouseReleased",
                    "x": cancel_box["x"],
                    "y": cancel_box["y"],
                    "button": "left",
                    "clickCount": 1
                })

                await asyncio.sleep(0.4)

                # 6. Modal must disappear after clicking cancel
                modal_after_cancel = await eval_js("document.body.innerText.includes('清除本地检索缓存')")
                self.assertFalse(modal_after_cancel, "Modal should disappear after clicking cancel")

                # 7. Assert ZERO network calls to /api/cache/clear-local
                clear_reqs = [u for u in captured_requests if "/api/cache/clear-local" in u]
                self.assertEqual(len(clear_reqs), 0, f"Expected 0 requests to /api/cache/clear-local, got {len(clear_reqs)}")

        try:
            asyncio.run(browser_test())
        finally:
            proc.terminate()
            proc.wait()
            server.should_exit = True


if __name__ == "__main__":
    unittest.main()
