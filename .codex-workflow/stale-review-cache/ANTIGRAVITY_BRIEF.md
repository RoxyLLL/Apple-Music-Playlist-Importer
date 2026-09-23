# 给 Antigravity 的执行指令

请按本目录 `SPEC.md`、`IMPLEMENTATION_PLAN.md` 和 `TEST_MATRIX.md` 修复 Apple Music 匹配结果的旧版 review 缓存问题。先检查现有代码和工作区改动，再实现代码及测试；不要硬编码截图中的歌曲或艺人。

核心验收条件：`sweets paper`（花澤香菜）对 `magical mode`（花泽香菜）的旧缓存 `review/0.72`，经当前规则读取后必须重评为 `no_match`（当前单候选复算总分约 0.138、title_score 约 0.083），且不再返回/展示该候选。人工 `user_confirmed` 必须保持；无法重评的旧记录不能伪装成已更新版本。

测试专项缓存路径、多键命中与所有 decision，再运行全量测试并将准确结果写入本目录 `RESULT.md`。不要提交、推送或发布；完成后交 Codex 复验。
