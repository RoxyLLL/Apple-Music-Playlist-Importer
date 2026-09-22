# 第一轮验收返工指令

请先阅读本目录 ACCEPTANCE.md，完成 R1 和 R2。产品功能修复已经通过，本轮集中修改测试与实施报告。

## R1：ISRC 夹具隔离

文件：`tests/test_batch_isrc.py`。

在创建 AppleMusicClient 和 MatchingEngine 之前，patch `applemusic.cache.PersistentCache.get_instance`，返回当前测试自己的临时缓存。不要先构造客户端再覆盖其字段，因为构造过程已经访问了默认数据库。

使用 addCleanup 或等价机制恢复 patch、关闭 SQLite 连接并清理临时目录，保证异常情况下也能清理。验证全新进程且缓存单例为空时，setUp 不产生任何默认路径的缓存构造。保留 25/25/10 分批、缓存复用等原始断言和虚拟凭据。

## R2：真实执行括号回退

文件：`tests/test_web_api.py` 的 `test_search_track_bracket_fallback`。

输入仍为 `Unmatched Main (晴天)`，但模拟结果必须为：

1. `search_catalog("Unmatched Main 晴天", "cn", 8)` 返回 no_hits。
2. `search_catalog("晴天", "cn", 8)` 返回包含预期 ID 的 ok。

断言完整调用列表正好是上述两次调用，并断言响应采用第二次结果。不能再用包含“晴天”就返回命中的条件。应证明回退分支不执行时测试会失败。

## 验证与报告

- 运行 ISRC 模块及 Web API 模块的目标测试。
- 在子进程没有 APPLE_MUSIC_DEVELOPER_TOKEN 的条件下运行完整 101 项测试。
- 为全套测试采用临时 USERPROFILE，并 mock 既有打开目录测试中的 subprocess.Popen，以免打开资源管理器；可用内存中的审计包装器，不要求本轮改动其他测试模块。报告须明确说明包装器施加的额外隔离措施。
- 建议阻断未模拟的 requests.Session.send，统计实际外部 HTTP 尝试，确保为 0。
- 运行 compileall 和 git diff --check。
- 更新 RESULT.md，区分本次夹具已实现的隔离与完整套件仍存的历史问题；更正 pykakasi 降级异常类型的描述。
- 保留 ACCEPTANCE.md 作为上一轮独立验收记录，由 Codex 复验时更新。

不要修改产品匹配逻辑、Token、用户缓存、媒体文件或发布产物；不要提交、推送、打标签或发布。完成后交回 Codex 验收。
