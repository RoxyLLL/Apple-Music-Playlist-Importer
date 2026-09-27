# 清除本地检索缓存按钮：实施与验收方案

## 现状

- `applemusic/web/app.py` 的 `POST /api/cache/clear` 只调用 `PersistentCache.clear_expired()`，不会清理未过期的错误匹配；现有 `tests/test_web_api.py::test_cache_clear_semantics` 依赖此语义，不能悄悄改为全清。
- `applemusic/cache.py` 的 SQLite `cache.db` 包含 `catalog_cache`、`equivalence_cache`、`match_cache`。`applemusic/client.py` 另有 `_catalog_cache` 内存缓存（含 ISRC、搜索词、建议词），只清 SQLite 会继续命中旧数据。
- UI 是 `applemusic/web/static/index.html` 的 Vue 单页应用。建议把按钮放在“选择来源平台并解析歌单”卡片标题右侧，匹配前就能找到；窄屏需保持可见、不遮挡标题。

## 目标行为

1. 增加一个明确标为“清除本地缓存”的按钮，点击后显示确认，说明会清除本机 Apple Music 检索结果、跨区等价缓存及自动匹配结论，之后搜索会重新访问曲库，可能耗时并增加请求；**不影响 Apple Music 云端资料库、歌单、本地音频文件、授权配置和手动确认的曲目映射**。取消时不得发 API 请求。
2. 新增独立的 `POST /api/cache/clear-local`（仍经现有 `X-App-Token` 保护），不要改变既有 `/api/cache/clear`“仅过期”行为。服务端在一个 SQLite 事务内删除 `catalog_cache`、`equivalence_cache`、`match_cache` 中非 `user_confirmed` 的记录，返回各表删除数量及清理后的统计。失败必须返回错误，不可用 `success: true` 或全零数字掩盖异常；不要删除 `cache.db` 文件或用广泛的文件系统删除命令。
3. SQLite 成功提交后清空共享 `AppleMusicClient._catalog_cache`。注意在途请求可能在清理后重新写入，至少在 UI 层对正在解析、重匹配、导入时禁用按钮，并防止重复点击；如现有锁机制允许，保证内存清理与并发检索不会把旧结果重新塞回缓存。无需清除诊断统计或 Apple Music 认证令牌。
4. 成功显示精确删除数量，失败显示真实错误且保持按钮可重试。不要自动重写当前页面的匹配结果为“未匹配”；提示用户重新解析/检索以刷新屏幕结果。

## 测试矩阵

- SQLite：填入有效/过期的 catalog、equivalence、算法 match 和 `user_confirmed` match；清理后前三类均不再命中，`user_confirmed` 保留，计数正确，重复清理幂等；异常时事务回滚、API 报错。
- 内存：预填 `_catalog_cache`，接口成功后为空；下一次同一查询实际调用上游，而非命中旧内存/SQLite。若 SQLite 清理失败，不能宣称成功。
- API：新接口需合法 app token；旧 `/api/cache/clear` 仍只删过期；不要改变云端资料库或本地歌曲相关路由。
- UI：确认/取消、运行时禁用、成功/失败反馈；在窄屏下按钮仍可用。
- 运行专项及全量 `pytest`，`git diff --check`。写 `RESULT.md`，报告本地模拟验证与真实 UI 手测情况。

## 工作边界

Antigravity 编写应用代码和测试，Codex 独立验收。保留当前工作区中上一轮 One Last Kiss 修复的未提交修改；不得覆盖、重置或误提交。此轮不执行 commit、push、打包或实际点击清除用户当前缓存。
