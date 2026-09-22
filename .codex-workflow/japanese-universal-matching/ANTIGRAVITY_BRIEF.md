# Antigravity 执行指令

请在 `D:\applemusic` 实现“日语歌曲通用多语言检索”。先完整阅读本目录的 `SPEC.md`、`ARCHITECTURE.md`、`IMPLEMENTATION_PLAN.md`、`TEST_MATRIX.md`、`EVIDENCE.md`。

核心要求：

1. 不要再通过添加具体歌曲、具体歌手到静态别名表或预置缓存来修失败样例。
2. 先修 evidence/diagnostics 字段契约，再实现通用日语规范化、locale-aware 查询、Apple suggestions、JP discovery 和目标区 equivalents/ISRC 回映射。
3. 罗马字/pykakasi/提示词只负责召回，不能单独成为高可信；最终候选必须属于用户目标 storefront。
4. 区分 `no_match`、`unavailable_in_target_storefront` 和检索错误；只有 `auto_accept/user_confirmed` 默认勾选。
5. 缓存按 locale/storefront/策略版本隔离，SQLite 原地迁移；用户确认不得扩散成全局别名。
6. 完成强制矩阵和至少 200 正例 + 100 困难负例的独立评估集。评估 fixture 不能被生产代码读取。
7. 不访问或输出真实 Token，不写用户资料库，不清空用户缓存，不下载音频，不提交/推送/打 Tag/发布。

完成后在本目录新增 `RESULT.md`，简要记录：代码修改、迁移方式、测试命令与结果、真实/离线评估数据来源、分桶指标、请求预算、未执行的 live 验证和遗留失败案例。等待 Codex 独立验收。

