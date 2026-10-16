"""Offline regressions for concurrent configuration writes and transient auth outages."""

import importlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from applemusic.auth import AppleMusicAuth
from applemusic.config import Config

web = importlib.import_module("applemusic.web.app")


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    monkeypatch.setattr("applemusic.config.CONFIG_FILE", path)
    Config(media_user_token="old-account-token-long", storefront="cn").save()
    web._auth_validation_cache.clear()
    web.invalidate_shared_engine()
    yield path
    web._auth_validation_cache.clear()
    web.invalidate_shared_engine()


def test_stale_developer_refresh_preserves_new_login_and_settings(isolated_config, monkeypatch):
    auth = AppleMusicAuth(Config.load())

    def fetch():
        Config(media_user_token="new-account-token-long", storefront="us", search_limit=27).save()
        return "new-developer-token", int(time.time()) + 10000

    monkeypatch.delenv("APPLE_MUSIC_DEVELOPER_TOKEN", raising=False)
    monkeypatch.setattr(auth, "_fetch_developer_token_from_web", fetch)
    assert auth.get_developer_token() == "new-developer-token"
    latest = Config.load()
    assert latest.media_user_token == "new-account-token-long"
    assert latest.storefront == "us"
    assert latest.search_limit == 27
    assert latest.developer_token == "new-developer-token"


def test_stale_login_save_preserves_new_developer_and_other_fields(isolated_config):
    login = Config.load()
    latest = Config.load()
    latest.developer_token = "fresh-dev"
    latest.developer_token_exp = 123456
    latest.search_limit = 23
    latest.save()
    login.media_user_token = "captured-account-token-long"
    login.storefront = "jp"
    login.save()
    latest = Config.load()
    assert latest.developer_token == "fresh-dev"
    assert latest.developer_token_exp == 123456
    assert latest.search_limit == 23
    assert latest.media_user_token == "captured-account-token-long"
    assert latest.storefront == "jp"


def test_refresh_updates_snapshot_before_later_login_save(isolated_config, monkeypatch):
    auth = AppleMusicAuth(Config.load())
    monkeypatch.delenv("APPLE_MUSIC_DEVELOPER_TOKEN", raising=False)
    monkeypatch.setattr(auth, "_fetch_developer_token_from_web", lambda: ("dev-first", 12345))
    auth.get_developer_token()
    latest = Config.load()
    latest.developer_token = "dev-later"
    latest.save()
    auth.config.media_user_token = "captured-account-token-long"
    auth.config.save()
    assert Config.load().developer_token == "dev-later"
    assert Config.load().media_user_token == "captured-account-token-long"


def test_new_config_full_save_and_explicit_clear(isolated_config):
    Config(media_user_token="replacement-token-long", storefront="tw", min_score=0.71).save()
    latest = Config.load()
    assert latest.storefront == "tw"
    assert latest.min_score == 0.71
    latest.media_user_token = ""
    latest.save()
    assert Config.load().media_user_token == ""


def test_concurrent_loaded_config_updates_merge_without_partial_json(isolated_config):
    first, second = Config.load(), Config.load()
    barrier = Barrier(2)

    def save(config, field, value):
        setattr(config, field, value)
        barrier.wait(timeout=5)
        config.save()
        assert isinstance(json.loads(isolated_config.read_text(encoding="utf-8")), dict)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save, first, "storefront", "us"),
                   pool.submit(save, second, "search_limit", 29)]
        for future in futures:
            future.result(timeout=5)
    assert Config.load().storefront == "us"
    assert Config.load().search_limit == 29


@pytest.mark.parametrize("message", [
    "请求网络异常: timeout",
    "无法获取 Apple Music 开发者 Token，请检查网络连接或环境变量",
    "API 响应异常 (HTTP 429): rate limit",
    "API 响应异常 (HTTP 500): server error",
    "API 响应异常 (HTTP 503): unavailable",
])
def test_transient_validation_never_blocks_library_or_poisons_cache(isolated_config, monkeypatch, message):
    monkeypatch.setattr(AppleMusicAuth, "validate_user_token", lambda self: (False, message))
    client, _ = web.get_shared_engine()
    upstream = MagicMock(return_value=[])
    monkeypatch.setattr(client, "get_user_playlists", upstream)
    headers = {"X-App-Token": web.SESSION_API_TOKEN}
    with TestClient(web.app) as api:
        status = api.get("/api/config", headers=headers).json()
        assert status["auth_state"] == "unknown"
        assert status["is_authorized"] is False
        assert web._get_token_hash(client.config.media_user_token) not in web._auth_validation_cache
        assert api.get("/api/user/playlists", headers=headers).status_code == 200
        upstream.assert_called_once()
        monkeypatch.setattr(AppleMusicAuth, "validate_user_token", lambda self: (True, "us"))
        restored = api.get("/api/config", headers=headers).json()
        assert restored["auth_state"] == "valid"
        assert restored["is_authorized"] is True


@pytest.mark.parametrize("code", [401, 403])
def test_explicit_rejection_blocks_library(isolated_config, monkeypatch, code):
    monkeypatch.setattr(AppleMusicAuth, "validate_user_token",
                        lambda self: (False, f"认证失败 (HTTP {code})：Token 已过期"))
    headers = {"X-App-Token": web.SESSION_API_TOKEN}
    with TestClient(web.app) as api:
        status = api.get("/api/config", headers=headers).json()
        assert status["auth_state"] == "invalid"
        assert status["is_authorized"] is False
        assert api.get("/api/user/playlists", headers=headers).status_code == 401


def test_outage_preserves_previously_verified_state_without_extending_ttl(isolated_config, monkeypatch):
    key = web._get_token_hash(Config.load().media_user_token)
    previous = (time.time() - 31, True, "us")
    web._auth_validation_cache[key] = previous
    monkeypatch.setattr(AppleMusicAuth, "validate_user_token", lambda self: (False, "请求网络异常: timeout"))
    with TestClient(web.app) as api:
        status = api.get("/api/config", headers={"X-App-Token": web.SESSION_API_TOKEN}).json()
    assert status["auth_state"] == "unknown"
    assert status["is_authorized"] is True
    assert status["detected_storefront"] == "us"
    assert web._auth_validation_cache[key] == previous


def test_manual_update_transient_failure_does_not_cache_rejection(isolated_config, monkeypatch):
    monkeypatch.setattr(AppleMusicAuth, "validate_user_token", lambda self: (False, "请求网络异常: timeout"))
    with TestClient(web.app) as api:
        result = api.post("/api/config", json={"media_user_token": "new-account-token-long"},
                          headers={"X-App-Token": web.SESSION_API_TOKEN}).json()
    assert result["auth_state"] == "unknown"
    assert web._get_token_hash("new-account-token-long") not in web._auth_validation_cache
