# Codex 复验：仍未通过，剩余一项阻断问题

日期：2026-09-30。已读取更新后的 RESULT.md，独立运行 V5 专项：13 passed，包含 Playwright 实际页面测试，无跳过。git diff --check 通过。

## 已通过

- 截图正例：标题 0.98、艺人 1.0、总分 0.988、review，无标题冲突。
- 截图反例：同艺人、缺失或相同时长时均 no_match，无默认候选。
- Love → Love Story 的前缀错配已拒绝。
- 跨脚本未知译名按 title_unverified 判定，不再误称明确异歌。
- 页面候选不符文案、换版本入口和诊断方法透传已实现，实际浏览器测试通过。
- 别名版本已更新，报告中日文正例输入已修正。

## 剩余阻断：时长分支绕过模糊证据保护

使用内存模拟模型：双方艺人均为 Test Artist，无专辑、无 ISRC、无目录映射。两侧时长设置为 240000ms。

| 来源 → 候选 | 方法 | 无时长 | 相同时长 |
| --- | --- | --- | --- |
| 好きだから → Sukidakaro | romaji_fuzzy | 0.941 / medium / review | 0.944 / strong / auto_accept |
| こうこ → Koko | romaji_long_vowel_folded | 0.953 / medium / review | 0.956 / strong / auto_accept |

这些对应本来有拼写/读音歧义，当前修复只在没有辅助元数据时有效。时长相同并未核验歌曲身份，却把弱标题证据提升为自动采纳。报告中“模糊/长音折叠统一为中等证据、只能 review”与实际行为不符。

根因：scorer.py 的 verification_level 判断中，independent_corroborated（约 957 行）先于 romaji_fuzzy / containment / romaji_long_vowel_folded 分支执行。命中 text_artist + duration 等条件后，后面的模糊身份限制无法生效。

## Antigravity 修订要求

1. 将标题身份确定性作为普通元数据佐证升为 strong 的必要条件。前缀、模糊转写、长音折叠、多读音推测不能仅因同艺人 + 相近时长/同专辑变为 auto_accept。
2. 若可靠 ISRC 或经过核验的歌曲映射解决了身份歧义，继续按现有身份核验与冲突规则处理；不允许仅凭 discovery_path 字符串或 artist ID 绕过限制。
3. 新增组合回归：上述两例及 containment/variant_fuzzy，覆盖无时长、相同/差 1 秒/2.5 秒/3 秒，来源与候选同专辑或候选 Single。无可靠歌曲身份时均不能自动采纳，证据必须保留歧义。
4. 保留截图正例高分、可靠完整对应的正常匹配、既有页面与缓存保护；修订评分规则版本，避免上一轮 v5 自动采纳缓存继续命中。
5. 更新 RESULT.md，以实际运行结果描述限制，再提交 Codex 验收。

本次未改业务代码，未使用真实账号。
