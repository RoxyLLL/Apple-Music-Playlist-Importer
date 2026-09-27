# 清除本地检索缓存功能实施结果报告 (Local Cache Clear)

## 1. 概述与方案对照

根据 Codex 方案 `D:\applemusic\.codex-workflow\local-cache-clear\PLAN.md`，完成了“清除本地缓存”前后端完整功能开发、自动化测试编写及全量回归验证。

### 1.1 核心需求与实现对照
1. **语义解耦与既有接口保留**:
   - 原有 `POST /api/cache/clear` 接口语义完全保持不变，依然仅调用 `PersistentCache.clear_expired()` 清理过期记录，`tests/test_web_api.py::test_cache_clear_semantics` 保持 100% 通过。
   - 新增独立的 `POST /api/cache/clear-local` 路由，专门用于主动清空本机检索、跨区等价与算法匹配缓存。
2. **SQLite 单事务原子清理与核心资产保护 (`applemusic/cache.py`)**:
   - 新增 `PersistentCache.clear_local_search_cache() -> Dict[str, int]`：在单个 SQLite 事务内清空 `catalog_cache`、`equivalence_cache`，并删除 `match_cache` 中所有非 `user_confirmed` 的记录。
   - **严格保留用户资产**:
     - `decision == 'user_confirmed'` 的人工确认曲目映射在数据库中无条件保留；
     - 不删除 `cache.db` 文件，不使用任何宽泛的文件系统删除命令；
     - 不影响 Apple Music 云端资料库、个人歌单、本地音频文件、Apple ID 授权配置与认证令牌 (Token)。
   - **真实错误反馈**: 若 SQLite 事务发生异常，自动回滚并抛出真实异常，拒绝以 `success: true` 或全零数字掩盖错误。
3. **共享客户端内存缓存清空 (`applemusic/client.py`)**:
   - `AppleMusicClient` 新增 `clear_in_memory_cache() -> int` 与统一门面 `clear_local_search_cache() -> Dict[str, Any]`。
   - 在线程安全锁 `_in_flight_lock` 保护下安全清空 `_catalog_cache`（覆盖 ISRC、搜索词与建议词内存缓存），避免清理后立即被内存旧数据重新污染。
   - 清除后发起的同名查询将强制穿透，发起真实的曲库重新检索。
4. **前端交互与安全防护 (`applemusic/web/static/index.html`)**:
   - **入口布局**: 按钮放置在第一步“选择来源平台并解析歌单”卡片标题右侧，使用 `flex-wrap gap-2` 与弹性缩放，在窄屏/移动端保持完整可见且不遮挡标题。
   - **状态保护与防重**: 绑定 `:disabled="isBusy"`，在解析歌单、导入同步、重新匹配、B 站补全或正在清理缓存时处于不可用状态；点击后防连击。
   - **确认与安全声明**: 点击弹出确认模态框，明确向用户说明：
     - 将清除本机存储的 Apple Music 检索结果、跨区等价缓存及算法自动匹配结论；
     - 明确声明**不会**影响云端资料库、歌单、本地音频、授权配置和人工确认映射；
     - 点击“取消”时不发送任何网络请求。
   - **精确结果反馈与提示**:
     - 成功后展示删除明细（曲库检索记录、跨区等价映射、自动匹配结论、内存检索缓存各自删除条数）；
     - 提示用户当前界面已显示的匹配结果未被自动重写，引导用户点击“解析歌单”或“重新匹配”刷新屏幕数据；
     - 失败时展示真实后端错误详情，保持“重试清除”按钮可操作。

---

## 2. 详细改动清单

| 文件 | 改动性质 | 说明 |
| --- | --- | --- |
| `applemusic/cache.py` | 后端持久层 | 新增 `clear_local_search_cache`（SQLite 事务清理三大表，保留 `user_confirmed`）；更新 `get_stats` 增加 `equivalence_cache` 统计 |
| `applemusic/client.py` | 客户端缓存 | 新增 `clear_in_memory_cache` 与 `clear_local_search_cache`，清理共享 `_catalog_cache` |
| `applemusic/web/app.py` | Web API | 新增 `POST /api/cache/clear-local` 路由，受 `X-App-Token` 中间件保护，返回删除明细与最新统计 |
| `applemusic/web/static/index.html` | 前端界面 | Card 1 添加“清除本地缓存”按钮，添加确认/反馈模态框与 Vue 响应式状态逻辑；闭合 MODAL 5 (`showDiagnosticsModal`) 与 MODAL 8 (`editingLocalSong`) 遮罩层，彻底解除外层嵌套，使 MODAL 9 直接位于 `#app` 下且祖先链不含任何其他 `v-if`；按钮子元素增加 `pointer-events-none` 与 `type="button"` 消除 IAB/WebView 命中穿透与事件冒泡差异，模态框增加 `role="dialog"` 与 `aria-modal="true"` 辅助功能标准属性 |
| `tests/test_local_cache_clear.py` | 自动化测试 | 新增 13 项针对 SQLite 事务、`user_confirmed` 保留、内存清空、API 权限与错误处理、UI isBusy 状态防竞争、完整祖先链（零 `v-if`）DOM 断言、真实浏览器 Playwright 指针点击 (`page.get_by_role('button', name='清除本地缓存').click()`)、CDP `Input.dispatchMouseEvent`（真实鼠标按下/释放坐标点击）与 Network 请求捕获（断言 `/api/cache/clear-local` 零请求）的全面测试 |

---

## 3. 测试矩阵执行结果

### 3.1 专项自动化测试
执行命令：
```powershell
D:\conda\python.exe -m pytest tests/test_local_cache_clear.py tests/test_web_api.py -v
```
**结果: 18 passed in 13.55s (100% 通过)**

| 测试用例 | 测试点 | 结果 |
| --- | --- | --- |
| `TestLocalCacheClearSQLite::test_clear_local_search_cache_semantics_and_user_confirmed_preservation` | SQLite 单事务清空 catalog/equivalence/算法 match，保留 `user_confirmed`，验证幂等性 | **PASSED** |
| `TestLocalCacheClearSQLite::test_clear_local_search_cache_transaction_failure_rollback` | SQLite 事务失败回滚，异常不被吞掉，数据不损坏 | **PASSED** |
| `TestLocalCacheClearClient::test_clear_in_memory_cache_flushes_all_keys` | `AppleMusicClient._catalog_cache` 多键值全部清空 | **PASSED** |
| `TestLocalCacheClearClient::test_clear_local_search_cache_forces_subsequent_search_to_requery` | 清除后同一搜索词不再命中缓存，验证确实穿透并实际访问上游网络请求 (HTTP GET) | **PASSED** |
| `TestLocalCacheClearWebApi::test_clear_local_requires_app_token` | 无 `X-App-Token` 访问 `/api/cache/clear-local` 返回 403 Forbidden | **PASSED** |
| `TestLocalCacheClearWebApi::test_clear_local_success_flow` | 携带合法 Token 访问返回 200、删除分类计数及最新 stats | **PASSED** |
| `TestLocalCacheClearWebApi::test_clear_local_failure_returns_500` | 后端异常时返回 500 及真实 detail 错误信息 | **PASSED** |
| `TestLocalCacheClearWebApi::test_legacy_clear_expired_unaffected` | 既有 `/api/cache/clear` 仍严格仅清理过期缓存（Web 接口隔离） | **PASSED** |
| `TestLocalCacheClearUI::test_html_ui_elements_and_safety_declarations` | 静态模板完整包含按钮、isBusy 全局禁用与提交防竞争、确认说明与核心资产不影响声明 | **PASSED** |
| `TestLocalCacheClearUI::test_legacy_clear_expired_only_deletes_expired_in_real_db` | 真实 SQLite 数据库中验证既有 `clear_expired()` 仅删过期行，完全保留有效行 | **PASSED** |
| `TestLocalCacheClearUI::test_dom_homepage_open_modal_and_cancel_without_api_call` | 静态 DOM 严格断言：MODAL 9 为 `#app` 直接子节点、完整祖先链（`ancestor_v_ifs == []`）无任何 `showDiagnosticsModal` 或其他 `v-if` 条件、Vue 状态模拟验证 | **PASSED** |
| `TestLocalCacheClearUI::test_real_browser_cdp_mouse_dispatch_open_and_cancel_modal` | 真实浏览器 CDP `Input.dispatchMouseEvent`（计算包围盒中心坐标派发真实鼠标按下/抬起）、网络事件监听断言 `/api/cache/clear-local` 严格为 0 次请求 | **PASSED** |
| `TestLocalCacheClearUI::test_real_browser_page_click_open_and_cancel_modal` | 真实浏览器 Playwright 指针点击 (`page.get_by_role('button', name='清除本地缓存').click()`)、真实点击“取消”按钮、网络请求捕获断言 `/api/cache/clear-local` 严格为 0 次请求 | **PASSED** |
| `TestWebApi::*` (既有 5 项用例) | API 版本一致性、Token 鉴权、歌名标点回退、过期缓存语义保持全部通过 | **PASSED** |

### 3.2 全量回归测试
执行命令：
```powershell
D:\conda\python.exe -m pytest -q
```
**结果: 217 passed, 23 warnings in 78.36s (全量 217 个测试 100% 全部通过，0 失败)**

### 3.3 代码规范与 Diff 检查
执行命令：
```powershell
git diff --check
```
**结果: 0 语法与空白字符错误**

---

## 4. 本地模拟与 UI 验证

1. **API 行为验证**:
   - `POST /api/cache/clear`：返回 `{"cleared_catalog": X, "cleared_match": Y, "message": "已清理过期缓存"}`，未过期的数据继续完好保留。
   - `POST /api/cache/clear-local`：返回 `{"success": true, "deleted": {"catalog": ..., "equivalence": ..., "match": ..., "memory": ...}, "stats": ...}`，清空未过期数据但 `user_confirmed` 依然存留在表中。
2. **状态防竞争与 UI 交互流程**:
   - **双重 isBusy 防护**: `isBusy` 计算属性统一覆盖了 `loading`（解析中）、`syncing`（导入同步中）、`rematchLoading`（重评中）、`bilibiliBatchLoading`（B站匹配中）及 `clearingCache`（清理中）。
   - **弹窗防并发提交**: 模态框确认按钮绑定 `:disabled="isBusy"`；同时 `confirmClearCache` 方法内部第一时间执行 `if (isBusy.value) return;` 阻断。即使弹窗已打开时后台状态变为 true，用户也绝对无法触发提交。
   - **安全声明展示**: 点击“清除本地缓存” -> 弹出居中确认模态框，列明影响范围与安全声明；
   - **无损取消**: 点击“取消”或右上角叉号 -> 模态框关闭，不产生任何后台网络请求；
   - **成功与重刷引导**: 成功后切换为绿色完成状态，展示分类删除条数并提示用户重新解析/匹配；
   - **上游重查保证**: 缓存清空后，再次查询同一词条必定实际请求 Apple Music 上游 API，验证了缓存失效与网络穿透链路。
3. **DOM 结构与完整祖先链修复**:
   - 闭合了 MODAL 5 (`showDiagnosticsModal`) 遗漏的遮罩与面板外层 `div`，使得包括 MODAL 9 在内的后续弹窗不再被错误包裹在诊断中心内；
   - MODAL 9 (`v-if="showClearCacheModal"`) 的直接父容器严格为 `<div id="app">`，其完整祖先链仅为 `div#app -> body -> html -> [document]`，不包含任何带有 `v-if` 的阻断节点（`ancestor_v_ifs == []`）。
4. **IAB / Edge 差异排查与前端健壮性增强**:
   - **子元素命中穿透与事件冒泡防护 (`pointer-events-none`)**: 按钮内部的图标 `<i>` 与文字 `<span>` 增加了 `pointer-events-none` 样式，保证在不同浏览器环境（包括内嵌 WebView/IAB 容器、Chromium 与 Edge）执行真实指针点击（Pointer/Mouse Hit-Testing）时，事件接收目标始终稳定为外部 `<button>` 元素，杜绝子节点拦截事件导致事件无法冒泡；
   - **明确声明 `type="button"`**: 避免在表单语义下被解释为默认提交；
   - **无障碍辅助树标准属性**: 模态框容器添加 `role="dialog"` 与 `aria-modal="true"`，并绑定 `aria-labelledby="clear-cache-modal-title"`，确保 AX 树和各类无障碍查询驱动（如 Playwright `getByRole`、Accessibility Tree 检查）能够无歧义定位该模态框；
   - **双轨真实浏览器自动化驱动测试**:
     - **Playwright 指针点击 (`test_real_browser_page_click_open_and_cancel_modal`)**: 使用 `page.get_by_role('button', name='清除本地缓存').click()` 执行标准指针点击与碰撞检测，全程监听 Network 请求，严格断言 `/api/cache/clear-local` 请求数为 0；
     - **CDP 鼠标事件派发 (`test_real_browser_cdp_mouse_dispatch_open_and_cancel_modal`)**: 通过 CDP `Input.dispatchMouseEvent` 对包围盒中心坐标发送真实的 `mousePressed` 与 `mouseReleased`，开启 `Network.enable` 监听底层网络包，同样断言网络零调用。

---

## 5. 工作边界与交付声明

- **保留改动**: 上一轮 `one-last-kiss-recall` 修复的未提交代码全部完好保留，未发生覆盖或回滚。
- **用户环境安全**: 未在真实用户数据目录执行缓存清除，未修改真实生产数据库。
- **交付约束**: 未执行 `git commit`、`git push`、`git tag`、`git release`，未打包可执行文件。现提交供 Codex 独立验收。
