# 实施计划（交 Antigravity）

## 1. 修复缓存新鲜度

- 检查 `applemusic/cache.py:MatchCache.get_match()` 与所有写缓存入口，区分算法判定（`auto_accept`、`review`、`no_match` 等）和用户确认（`user_confirmed`）。
- 所有算法判定统一比较 DB 列与 evidence 中的规则/查询/罗马化/例外版本；任一版本未知或不一致，都视为过期。
- 过期时对完整候选列表用当前 `TrackScorer` 重新评分和判定，不能只更新 badge 分数。
- 新判定为 `no_match` 时返回/持久化无候选结果；候选缺失或来源信息不全时让旧缓存失效并走重新搜索，绝不把旧候选降级包装成高分 review。
- 保留 `user_confirmed` 原有保护；只更新确实完成重评的缓存版本字段，避免将未重评旧数据标记成新版本。
- 检查多重 cache key/索引命中路径，保证相同结果不会经次级索引绕过版本校验。

## 2. 针对截图样例做回归

- 建立一条旧规则版本 `review` 缓存：`sweets paper` / 花澤香菜 vs `magical mode` / 花泽香菜，旧候选分数 0.72。
- 读取该缓存后必须以当前评分器重评：标题分约 0.083，总分约 0.138，决策 `no_match`，`selected_candidate is None`。
- 断言结果 JSON、DB 的 decision/status/version 字段一致，缓存随后读取不再恢复 72 分候选。

## 3. 回归与交付

- 覆盖旧 `auto_accept`、旧 `review`、版本一致的 `review`、人工 `user_confirmed`、候选缺失及主键/次级索引命中。
- 运行专项测试与全量 pytest，`git diff --check`；记录准确命令、数量和结果于本目录 `RESULT.md`。
- 不提交、不推送、不发布；完成后交 Codex 独立验收。
