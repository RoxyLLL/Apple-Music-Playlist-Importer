# 登录后资料库仍提示未授权：修复实施报告

## 1. 现象与根因定位

### 1.1 现象复现
用户在 Edge 自动登录 Apple ID 成功后，Web 界面提示连接成功，但点击“我的个人歌单”时立即弹出 `获取歌单失败: 尚未授权 Apple ID` (HTTP 401)。

### 1.2 根因分析
1. **共享客户端陈旧化 (Stale Client Reference)**：
   `applemusic/web/app.py::get_shared_engine()` 仅在 `_shared_client is None` 时根据配置构建单例。若应用在用户登录前因静态检索、缓存统计等操作已调用过 `get_shared_engine()`，该实例会一直持有启动时的空 token。当用户通过自动登录成功提取 token 并写入 `config.json` 后，内存中的 `_shared_client` 未被同步或失效，导致 `/api/user/playlists` 读取 `client.config.is_authorized()` 时判定为 False 并直接返回 401。
2. **授权状态假阳性 (False Positive Status)**：
   `/api/auto-login/status` 在 capturer 空闲或未授权时，通过兜底分支 `bool(config.media_user_token)` 宣称已授权，即便 token 未经校验或已过期；同时在 capturer 结束时若存在任何 token 字符串即强行标记 `is_authorized = True`。
3. **前端时序与状态覆盖**：
   - 前端 `pollAutoLoginStatus()` 在收到 `is_authorized` 后未 `await fetchConfig()` 便关闭弹窗，造成 UI 与后端配置不一致；
   - 手动配置保存 `testAndSaveToken()` 将 `POST /api/config` 仅返回的 `{success, is_authorized, message}` 直接赋值给 `config.value`，擦除了完整的 `storefront`、`version` 等状态。
4. **Token 指纹机制薄弱**：
   原内存验证缓存仅取 `token[:24]` 作为缓存键，存在碰撞风险；缺乏不暴露凭证的代际指纹对比。

---

## 2. 修改内容

### 2.1 配置指纹与生命周期管理 (`applemusic/config.py`)
- 新增 `Config.get_fingerprint() -> str`：基于 `media_user_token`、`storefront` 与 `developer_token` 计算内存专用 SHA-256 摘要，绝不暴露或记录原始凭证，用于跨线程检测配置代际变更。

### 2.2 自动登录完成回调 (`applemusic/auto_token.py`)
- `BrowserTokenCapturer.__init__` 增加 `on_token_saved: Optional[Callable[[Config], None]] = None`。
- 在 `_poll_for_token()` 完成 token 校验并持久化至 `config.json` 的瞬间，立即同步调用 `on_token_saved(self.config)` 回调。

### 2.3 后端鉴权统一与线程安全同步 (`applemusic/web/app.py`)
- **线程安全的代际指纹检测与重建**：
  引入 `_shared_engine_lock` 与 `_shared_config_fingerprint`。`get_shared_engine()` 在并发环境下对比当前配置指纹，若检测到配置发生变动或显式失效，原子替换共享的 `AppleMusicClient` 与 `MatchingEngine`，绝不在请求执行过程中原地修改对象属性。
- **统一本地前置授权门禁与异常处理**：
  封装 `_require_authorized_client() -> AppleMusicClient` 与 `_handle_upstream_permission_error()`，在所有 12 个 `/api/user/*` 端点及 `/api/sync` 中统一执行前置鉴权，返回明确且无泄露的 401 诊断信息，不设任何绕过验证的后门。
- **安全摘要缓存与防假阳性修复**：
  - 封装 `_get_token_hash(token)`，全面以 SHA-256 摘要作为 `_auth_validation_cache` 键。
  - 修复 `GET /api/auto-login/status`：彻底移除 `bool(config.media_user_token)` 盲目宣称授权的逻辑，仅在当前 token 经由 capturer 或缓存校验通过时报告 `is_authorized: True`。
  - `POST /api/auto-login` 注册 `_on_auto_token_saved`，在捕获 token 保存后即时写入校验缓存并触发 `invalidate_shared_engine()`。
  - 针对网络波动异常设置短 TTL（5s），避免因偶发断网将用户锁在未授权状态。

### 2.4 上游认证失效明确化与资料库写入统一返回 401 (`applemusic/client.py`)
- 在读取方法（`get_user_playlists`、`get_playlist_tracks`、`get_library_songs`、`search_library_songs`、`find_library_song_id`）以及写入/变更方法（`create_playlist`、`add_tracks_to_playlist`、`delete_playlist_tracks`、`add_playlist_tracks`、`update_playlist`、`delete_playlist`、`batch_delete_playlists`、`delete_library_song`、`batch_delete_library_songs`、`add_tracks_to_library`）中全面捕获 HTTP 401/403。
- **杜绝静默失败与假成功**：此前部分写入方法（如 `delete_playlist_tracks`、`add_playlist_tracks`）在收到 401/403 时仅记录 warning 并将所有歌曲放入 `failed_ids` 返回，路由随之返回 `{"success": True, "deleted_count": 0, "failed_ids": [...]}`，彻底掩盖了上游授权过期。现统一抛出 `PermissionError`，由路由层的 `_handle_upstream_permission_error()` 捕获并统一返回 HTTP 401，同时将该凭证在 `_auth_validation_cache` 中标记失效。

### 2.5 前端时序、授权状态判断与错误提示 (`applemusic/web/static/index.html`)
- **`pollAutoLoginStatus`**：捕获成功后先 `await fetchConfig()`，确认全局配置已处于授权状态后再关闭弹窗；若当前停留在资料库标签页，自动触发当前子页（歌单或歌曲）数据刷新。
- **`testAndSaveToken`**：
  - 彻底移除 `|| (config.value && config.value.is_authorized)` 旧状态兜底，仅根据当前提交 token 的验证结果（`data && data.is_authorized`）判定是否登录成功，杜绝重新获取配置失败时旧状态让无效 token 显示为登录成功的漏洞；
  - 保存成功后重新获取完整 `/api/config` 状态，避免字段覆盖，并在有效后刷新当前资料库。
- **资料库操作全面 401/403 门禁与提示**：
  - 在 `deleteSingleCloudPlaylist`、`batchDeleteCloudPlaylists`、`openPlaylistTracksModal`、`deleteSinglePlaylistTrack`、`batchDeletePlaylistTracks`、`batchAddCloudSongsToPlaylist`、`addSingleLocalSongToPlaylist`、`batchAddLocalSongsToPlaylist`、`addSingleTrackToPlaylist`、`savePlaylistEdit`、`searchCloudSongsServer`、`fetchUserLibrarySongs`、`deleteSingleCloudSong`、`batchDeleteCloudSongs` 等全部操作中，显式拦截 `res.status === 401`（提示授权过期并引导右上角重新连接）与 `res.status === 403`（本地安全校验拦截），错误时不破坏清空已有列表。

### 2.6 测试鲁棒性调优 (`tests/test_local_cache_clear.py`)
- 将真实浏览器 CDP 鼠标点击测试中的 Vue 挂载等待条件升级为显式等待“清除本地缓存”按钮 DOM 节点就绪，避免在高负载并发测试下发生微秒级时序竞争。

---

## 3. 验收矩阵验证

| 序号 | 验收标准 (PLAN.md & 补充验收) | 测试用例 | 验证结果 |
| :--- | :--- | :--- | :--- |
| 1 | **首次自动登录（已有无 token 共享客户端）**：自动捕获成功后无需重启，资料库接口通过本地前置检查并带新 token | `test_matrix_1_auto_login_precreated_client_recovers_without_restart` | **PASS** |
| 2 | **首次自动登录（无预先创建客户端）**：自动捕获后资料库接口正常访问 | `test_matrix_2_auto_login_without_precreated_client` | **PASS** |
| 3 | **手动粘贴/更新 token**：保存后 UI、共享客户端和资料库读取使用同一新 token；刷新仍一致 | `test_matrix_3_manual_token_save_and_persistence` | **PASS** |
| 4 | **旧 token 失效/清除**：不显示“已授权”；资料库返回明确 401 状态，不沿用旧客户端或失效缓存 | `test_matrix_4_token_cleared_or_invalidated` | **PASS** |
| 5 | **网络校验异常**：可重试，不被当成永久未登录或授权成功，诊断信息不泄露凭证 | `test_matrix_5_network_error_resilience_and_no_credential_leakage` | **PASS** |
| 6 | **并发安全性**：自动登录捕获瞬间并发读取资料库及配置，无假 401、无旧客户端回写覆盖 | `test_matrix_6_concurrency_during_auto_login_transition` | **PASS** |
| 7 | **防护与隐私**：维持 `X-App-Token` 403 门禁，测试全程使用 mock，无真实 Apple 凭证泄露 | `test_matrix_7_security_and_privacy` | **PASS** |
| 8 | **资料库写入方法抛出异常**：Client 写入与修改方法在 401/403 时统一抛出 `PermissionError` | `test_client_write_methods_raise_permission_error_on_auth_rejection` | **PASS** |
| 9 | **资料库写入路由统一返回 401**：Web 写入路由（删歌单/改歌单/批量删/加歌曲/移出歌曲/同步）在 401 时返回 HTTP 401，并使本地缓存失效 | `test_api_write_routes_uniform_401_on_upstream_auth_failure` | **PASS** |
| 10 | **前端手动登录无旧状态残留**：`testAndSaveToken` 仅依据当前校验结果，无 `config.value.is_authorized` 回退 | `test_frontend_manual_login_strictly_checks_current_token_validation` | **PASS** |

---

## 4. 测试运行结果

1. **专项一致性与写入鉴权回归测试**：
   ```bash
   pytest tests/test_auth_library_consistency.py -v
   # 10 passed in 1.16s
   ```
2. **资料库管理测试**：
   ```bash
   pytest tests/test_library_manager.py -v
   # 26 passed in 2.16s
   ```
3. **本地缓存清理全链路测试**：
   ```bash
   pytest tests/test_local_cache_clear.py -v
   # 13 passed in 14.50s
   ```
4. **全量回归测试**：
   ```bash
   pytest -q
   # 227 passed, 23 warnings in 82.05s (100% 通过)
   ```
5. **代码格式与差异检查**：
   ```bash
   git diff --check
   # 返回 0，无任何行尾多余空白或格式错误
   ```

---

## 5. 交付说明

- **未执行** `git commit`、`git push`、打 tag 或打包，代码完好保存在本地工作区，等待 Codex 独立验收。
- 未使用真实 Apple 账号或凭证，未修改或删除用户生产数据。
