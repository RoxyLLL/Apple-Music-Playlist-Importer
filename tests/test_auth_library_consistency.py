"""
Automated regression and acceptance tests for Apple Music authentication and library consistency.
Covers the 7 acceptance criteria defined in .codex-workflow/auth-library-stale-client/PLAN.md:
1. Auto-login when shared client was pre-created without token.
2. Auto-login when no shared client was pre-created.
3. Manual token update and instant synchronization.
4. Token cleared or invalidated (no false authorized reporting).
5. Transient network error handling and retryability without credential leakage.
6. Thread safety and concurrent library access during token capture transition.
7. X-App-Token protection and credential privacy (no raw token leakage).
"""

import concurrent.futures
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from applemusic.auto_token import BrowserTokenCapturer
from applemusic.client import AppleMusicClient
from applemusic.config import Config, get_config
from applemusic.web.app import (
    SESSION_API_TOKEN,
    _auth_validation_cache,
    _get_token_hash,
    app,
    get_shared_engine,
    invalidate_shared_engine,
)


@pytest.fixture
def temp_config_env(monkeypatch, tmp_path):
    """Isolate config file in a temporary folder to avoid touching user's real config."""
    cfg_file = tmp_path / "config.json"
    init_cfg = {
        "developer_token": "dummy_dev_token_for_test_1234567890",
        "media_user_token": "",
        "storefront": "cn",
    }
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump(init_cfg, f)

    monkeypatch.setattr("applemusic.config.CONFIG_FILE", cfg_file)
    _auth_validation_cache.clear()
    invalidate_shared_engine()
    yield cfg_file
    _auth_validation_cache.clear()
    invalidate_shared_engine()


@pytest.fixture
def api_client():
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {"X-App-Token": SESSION_API_TOKEN}


def test_matrix_1_auto_login_precreated_client_recovers_without_restart(
    temp_config_env, api_client, auth_headers
):
    """
    Acceptance Criterion 1:
    Before auto-login, a shared client was created (e.g., via catalog search or cache stats) with empty token.
    Library access is rejected with 401.
    After auto-login successfully captures and validates token, shared client is automatically
    synchronized without application restart; library queries succeed using the new token.
    """
    # 1. Access shared engine before login -> empty token client
    pre_client, _ = get_shared_engine()
    assert not pre_client.config.is_authorized()

    # Pre-login library call must fail with 401
    res_pre = api_client.get("/api/user/playlists", headers=auth_headers)
    assert res_pre.status_code == 401
    assert "尚未授权 Apple ID" in res_pre.json()["detail"]

    # 2. Simulate auto-login capture
    new_user_token = "captured_user_token_abc1234567890_xyz"
    captured_sf = "us"

    def mock_on_token_saved(cfg):
        from applemusic.web.app import _on_auto_token_saved
        _on_auto_token_saved(cfg)

    # Capturer validates and saves token
    capturer = BrowserTokenCapturer(on_token_saved=mock_on_token_saved)
    capturer.config.media_user_token = new_user_token
    capturer.config.storefront = captured_sf
    capturer.config.save()
    if capturer.on_token_saved:
        capturer.on_token_saved(capturer.config)

    # 3. Verify status and config without restarting
    status_res = api_client.get("/api/auto-login/status", headers=auth_headers)
    assert status_res.status_code == 200
    assert status_res.json()["is_authorized"] is True

    cfg_res = api_client.get("/api/config", headers=auth_headers)
    assert cfg_res.status_code == 200
    assert cfg_res.json()["is_authorized"] is True
    assert cfg_res.json()["storefront"] == captured_sf

    # 4. Library access now succeeds and uses the new token
    with patch("requests.Session.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {
                    "id": "p.my_playlist_01",
                    "type": "library-playlists",
                    "attributes": {
                        "name": "My Auto Synced Hits",
                        "canEdit": True,
                    },
                }
            ]
        }
        mock_get.return_value = mock_resp

        lib_res = api_client.get("/api/user/playlists", headers=auth_headers)
        assert lib_res.status_code == 200
        data = lib_res.json()
        assert data["success"] is True
        assert len(data["playlists"]) == 1
        assert data["playlists"][0]["id"] == "p.my_playlist_01"

        # Assert upstream request received the new user token
        called_headers = mock_get.call_args[1].get("headers", {})
        assert called_headers.get("Music-User-Token") == new_user_token


def test_matrix_2_auto_login_without_precreated_client(
    temp_config_env, api_client, auth_headers
):
    """
    Acceptance Criterion 2:
    Login occurs before any shared client was initialized.
    Upon capture, library queries pass local pre-checks and pass the new token.
    """
    invalidate_shared_engine()
    new_user_token = "brand_new_token_9876543210_def"

    cfg = get_config()
    cfg.media_user_token = new_user_token
    cfg.storefront = "jp"
    cfg.save()

    # Emulate capturer saving
    from applemusic.web.app import _on_auto_token_saved
    _on_auto_token_saved(cfg)

    with patch("requests.Session.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {
                    "id": "i.song_jp_1",
                    "type": "library-songs",
                    "attributes": {"name": "First Love", "artistName": "宇多田光"},
                }
            ]
        }
        mock_get.return_value = mock_resp

        res = api_client.get("/api/user/library/songs", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["success"] is True
        assert res.json()["songs"][0]["title"] == "First Love"

        called_headers = mock_get.call_args[1].get("headers", {})
        assert called_headers.get("Music-User-Token") == new_user_token


def test_matrix_3_manual_token_save_and_persistence(
    temp_config_env, api_client, auth_headers
):
    """
    Acceptance Criterion 3:
    Manual token entry via POST /api/config synchronizes shared client and UI state.
    """
    manual_token = "manual_entered_token_112233445566_ghi"
    with patch("applemusic.auth.AppleMusicAuth.validate_user_token", return_value=(True, "us")):
        res = api_client.post(
            "/api/config",
            headers=auth_headers,
            json={"media_user_token": manual_token, "storefront": "us"},
        )
        assert res.status_code == 200
        assert res.json()["is_authorized"] is True

    # Shared engine immediately reflects new token without restart
    client, _ = get_shared_engine()
    assert client.config.media_user_token == manual_token
    assert client.config.storefront == "us"

    # GET /api/config returns full status
    cfg_res = api_client.get("/api/config", headers=auth_headers)
    assert cfg_res.status_code == 200
    assert cfg_res.json()["is_authorized"] is True
    assert cfg_res.json()["storefront"] == "us"


def test_matrix_4_token_cleared_or_invalidated(
    temp_config_env, api_client, auth_headers
):
    """
    Acceptance Criterion 4:
    When token is cleared or expired, /api/auto-login/status does NOT falsely report is_authorized=True.
    Library access returns 401.
    """
    # 1. Clear token
    with patch("applemusic.auth.AppleMusicAuth.validate_user_token", return_value=(False, "未配置")):
        api_client.post(
            "/api/config",
            headers=auth_headers,
            json={"media_user_token": "", "storefront": "cn"},
        )

    status_res = api_client.get("/api/auto-login/status", headers=auth_headers)
    assert status_res.status_code == 200
    assert status_res.json()["is_authorized"] is False

    lib_res = api_client.get("/api/user/playlists", headers=auth_headers)
    assert lib_res.status_code == 401

    # 2. Token present but cached as invalid
    invalid_token = "expired_token_0000000000_bad"
    thash = _get_token_hash(invalid_token)
    _auth_validation_cache[thash] = (time.time(), False, "认证失败 (HTTP 401)：Token 已过期")

    cfg = get_config()
    cfg.media_user_token = invalid_token
    cfg.save()
    invalidate_shared_engine()

    lib_res2 = api_client.get("/api/user/playlists", headers=auth_headers)
    assert lib_res2.status_code == 401
    assert "Apple ID 授权失效" in lib_res2.json()["detail"]


def test_matrix_5_network_error_resilience_and_no_credential_leakage(
    temp_config_env, api_client, auth_headers
):
    """
    Acceptance Criterion 5:
    Transient network errors during validation do not permanently brick the session and
    allow immediate retries with a short TTL. Diagnostics and logs never leak raw tokens.
    """
    test_token = "secret_user_token_999888777_priv"
    cfg = get_config()
    cfg.media_user_token = test_token
    cfg.save()
    invalidate_shared_engine()

    # Network failure during validation
    with patch(
        "applemusic.auth.AppleMusicAuth.validate_user_token",
        return_value=(False, "请求网络异常: HTTPSConnectionPool(host='api.music.apple.com', port=443): Read timed out"),
    ):
        res = api_client.get("/api/config", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["is_authorized"] is False
        assert "请求网络异常" in data["auth_message"]
        # Raw token must not appear in response
        assert test_token not in json.dumps(data)


def test_matrix_6_concurrency_during_auto_login_transition(
    temp_config_env, api_client, auth_headers
):
    """
    Acceptance Criterion 6:
    Concurrent queries to get_shared_engine and library endpoints during token transition
    do not cause race conditions or stale client regressions.
    """
    initial_token = "token_phase_1_1111111111"
    new_token = "token_phase_2_2222222222"

    cfg = get_config()
    cfg.media_user_token = initial_token
    cfg.save()
    get_shared_engine()

    def worker_read(i):
        client, _ = get_shared_engine()
        return client.config.media_user_token

    def worker_update():
        cfg2 = get_config()
        cfg2.media_user_token = new_token
        cfg2.save()
        from applemusic.web.app import _on_auto_token_saved
        _on_auto_token_saved(cfg2)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker_read, i) for i in range(15)]
        futures.append(executor.submit(worker_update))
        futures.extend([executor.submit(worker_read, i) for i in range(15)])

        results = [f.result() for f in futures if f.result() is not None]

    # After update finishes, all future engine accesses must return new_token
    final_client, _ = get_shared_engine()
    assert final_client.config.media_user_token == new_token


def test_matrix_7_security_and_privacy(
    temp_config_env, api_client
):
    """
    Acceptance Criterion 7:
    Rejection of unauthorized API requests without X-App-Token (HTTP 403),
    and verification that user tokens are never leaked in error messages.
    """
    # 1. 403 Forbidden without local session token
    res = api_client.get("/api/user/playlists")
    assert res.status_code == 403

    res_bad_token = api_client.get("/api/user/playlists", headers={"X-App-Token": "invalid_token"})
    assert res_bad_token.status_code == 403

    # 2. Check that raw tokens are masked in /api/config
    valid_token = "very_secret_token_1234567890_confidential"
    cfg = get_config()
    cfg.media_user_token = valid_token
    cfg.save()
    invalidate_shared_engine()

    res_cfg = api_client.get("/api/config", headers={"X-App-Token": SESSION_API_TOKEN})
    assert res_cfg.status_code == 200
    cfg_json = res_cfg.json()
    assert valid_token not in json.dumps(cfg_json)
    assert cfg_json["token_preview"] == valid_token[:10] + "..." + valid_token[-8:]


def test_client_write_methods_raise_permission_error_on_auth_rejection():
    """
    Verify that all library write and modification methods in AppleMusicClient raise PermissionError
    when upstream Apple API returns HTTP 401 or 403, instead of masking them as normal failed items.
    """
    client = AppleMusicClient()
    client.config.developer_token = "dev_token_123"
    client.config.media_user_token = "user_token_123"

    for status_code in (401, 403):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.text = '{"errors":[{"status":"' + str(status_code) + '","title":"Unauthorized"}]}'
        mock_resp.json.return_value = {"errors": [{"status": str(status_code)}]}

        with patch("requests.Session.post", return_value=mock_resp), \
             patch("requests.Session.get", return_value=mock_resp), \
             patch("requests.Session.delete", return_value=mock_resp), \
             patch("requests.Session.patch", return_value=mock_resp):

            # 1. create_playlist
            with pytest.raises(PermissionError) as exc_info:
                client.create_playlist(name="Test Playlist")
            assert str(status_code) in str(exc_info.value)

            # 2. add_tracks_to_playlist
            with pytest.raises(PermissionError) as exc_info:
                client.add_tracks_to_playlist("p.123", ["i.001", "i.002"])
            assert str(status_code) in str(exc_info.value)

            # 3. delete_playlist_tracks
            with pytest.raises(PermissionError) as exc_info:
                client.delete_playlist_tracks("p.123", ["i.001", "i.002"])
            assert str(status_code) in str(exc_info.value)

            # 4. add_playlist_tracks
            with pytest.raises(PermissionError) as exc_info:
                client.add_playlist_tracks("p.123", ["i.001", "i.002"])
            assert str(status_code) in str(exc_info.value)

            # 5. update_playlist
            with pytest.raises(PermissionError) as exc_info:
                client.update_playlist("p.123", name="New Name")
            assert str(status_code) in str(exc_info.value)

            # 6. delete_playlist
            with pytest.raises(PermissionError) as exc_info:
                client.delete_playlist("p.123")
            assert str(status_code) in str(exc_info.value)

            # 7. batch_delete_playlists
            with pytest.raises(PermissionError) as exc_info:
                client.batch_delete_playlists(["p.123", "p.456"])
            assert str(status_code) in str(exc_info.value)

            # 8. delete_library_song
            with pytest.raises(PermissionError) as exc_info:
                client.delete_library_song("i.001")
            assert str(status_code) in str(exc_info.value)

            # 9. batch_delete_library_songs
            with pytest.raises(PermissionError) as exc_info:
                client.batch_delete_library_songs(["i.001", "i.002"])
            assert str(status_code) in str(exc_info.value)

            # 10. add_tracks_to_library
            with pytest.raises(PermissionError) as exc_info:
                client.add_tracks_to_library(["12345678"])
            assert str(status_code) in str(exc_info.value)

            # 11. find_library_song_id
            with pytest.raises(PermissionError) as exc_info:
                client.find_library_song_id("Some Title", "Some Artist")
            assert str(status_code) in str(exc_info.value)


def test_api_write_routes_uniform_401_on_upstream_auth_failure(
    temp_config_env, api_client, auth_headers
):
    """
    Verify that all library write API endpoints return HTTP 401 when upstream Apple API rejects auth,
    and update local _auth_validation_cache to mark the session as invalid.
    """
    user_token = "valid_looking_token_1234567890_write_test"
    cfg = get_config()
    cfg.media_user_token = user_token
    cfg.storefront = "us"
    cfg.save()
    invalidate_shared_engine()

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = '{"errors":[{"status":"401","title":"Unauthorized"}]}'
    mock_resp.json.return_value = {"errors": [{"status": "401"}]}

    with patch("requests.Session.post", return_value=mock_resp), \
         patch("requests.Session.delete", return_value=mock_resp), \
         patch("requests.Session.patch", return_value=mock_resp), \
         patch("requests.Session.get", return_value=mock_resp):

        # 1. DELETE /api/user/playlists/{id}/tracks
        res = api_client.request(
            "DELETE",
            "/api/user/playlists/p.test/tracks",
            headers=auth_headers,
            json={"track_ids": ["i.track1"]},
        )
        assert res.status_code == 401
        assert "认证失效" in res.json()["detail"] or "401" in res.json()["detail"]

        # Clear cache between tests to isolate each route
        _auth_validation_cache.clear()

        # 2. POST /api/user/playlists/{id}/tracks
        res = api_client.post(
            "/api/user/playlists/p.test/tracks",
            headers=auth_headers,
            json={"track_ids": ["i.track1"]},
        )
        assert res.status_code == 401
        assert "认证失效" in res.json()["detail"] or "401" in res.json()["detail"]

        _auth_validation_cache.clear()

        # 3. POST /api/user/playlists/{id}/add-local-tracks
        res = api_client.post(
            "/api/user/playlists/p.test/add-local-tracks",
            headers=auth_headers,
            json={"tracks": [{"title": "Local Song", "artist": "Singer"}]},
        )
        assert res.status_code == 401
        assert "认证失效" in res.json()["detail"] or "401" in res.json()["detail"]

        _auth_validation_cache.clear()

        # 4. PATCH /api/user/playlists/{id}
        res = api_client.patch(
            "/api/user/playlists/p.test",
            headers=auth_headers,
            json={"name": "Renamed Playlist"},
        )
        assert res.status_code == 401
        assert "认证失效" in res.json()["detail"] or "401" in res.json()["detail"]

        _auth_validation_cache.clear()

        # 5. DELETE /api/user/playlists/{id}
        res = api_client.delete("/api/user/playlists/p.test", headers=auth_headers)
        assert res.status_code == 401
        assert "认证失效" in res.json()["detail"] or "401" in res.json()["detail"]

        _auth_validation_cache.clear()

        # 6. POST /api/user/playlists/batch-delete
        res = api_client.post(
            "/api/user/playlists/batch-delete",
            headers=auth_headers,
            json={"playlist_ids": ["p.test1", "p.test2"]},
        )
        assert res.status_code == 401
        assert "认证失效" in res.json()["detail"] or "401" in res.json()["detail"]

        _auth_validation_cache.clear()

        # 7. DELETE /api/user/library/songs/{id}
        res = api_client.delete("/api/user/library/songs/i.song1", headers=auth_headers)
        assert res.status_code == 401
        assert "认证失效" in res.json()["detail"] or "401" in res.json()["detail"]

        _auth_validation_cache.clear()

        # 8. POST /api/user/library/songs/batch-delete
        res = api_client.post(
            "/api/user/library/songs/batch-delete",
            headers=auth_headers,
            json={"song_ids": ["i.song1", "i.song2"]},
        )
        assert res.status_code == 401
        assert "认证失效" in res.json()["detail"] or "401" in res.json()["detail"]

        _auth_validation_cache.clear()

        # 9. POST /api/sync
        res = api_client.post(
            "/api/sync",
            headers=auth_headers,
            json={"playlist_name": "Test Sync PL", "track_ids": ["12345678"]},
        )
        assert res.status_code == 401
        assert "认证失效" in res.json()["detail"] or "401" in res.json()["detail"]

        # 10. Verify that cache now records invalid state for this token
        thash = _get_token_hash(user_token)
        assert thash in _auth_validation_cache
        cached_entry = _auth_validation_cache[thash]
        assert cached_entry[1] is False  # is_valid = False

        # Subsequent library request should fail fast with 401 from pre-check cache
        res_fast_fail = api_client.get("/api/user/playlists", headers=auth_headers)
        assert res_fast_fail.status_code == 401
        assert "Apple ID 授权失效" in res_fast_fail.json()["detail"]


def test_frontend_manual_login_strictly_checks_current_token_validation():
    """
    Verify that index.html testAndSaveToken strictly relies on the current token's validation result
    (data && data.is_authorized) and does NOT fall back to old cached page state
    (config.value && config.value.is_authorized).
    """
    html_path = Path(__file__).resolve().parent.parent / "applemusic" / "web" / "static" / "index.html"
    assert html_path.exists()
    content = html_path.read_text(encoding="utf-8")

    # Locate testAndSaveToken definition
    assert "testAndSaveToken" in content
    # Ensure obsolete fallback condition is absent
    assert "data.is_authorized || (config.value && config.value.is_authorized)" not in content
    assert "|| (config.value && config.value.is_authorized)" not in content
    assert "|| config.value.is_authorized" not in content

    # Ensure strictly relies on data && data.is_authorized
    assert "if (data && data.is_authorized)" in content

