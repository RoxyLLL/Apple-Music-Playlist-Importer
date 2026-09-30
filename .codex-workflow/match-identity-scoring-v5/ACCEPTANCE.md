# Codex 独立验收：暂不通过

日期：2026-09-30。截图两例的核心评分修复有效，但通用身份保护和页面交付尚未完成。

## 独立验证

- V5 专项：12 passed。
- 全量：239 passed，23 项弃用警告。
- 内存模型独立复算（模拟数据、无真实凭证）：
  - `好きだから。（因为我喜欢你。）` / `『ユイカ』` → `Sukidakara` / `Yuika`：标题 0.98、艺人 1.0、总分 0.988、review，无标题冲突。
  - `sweets parade` → `magical mode`，同艺人、缺失时长：总分 0.166、no_match、无选中候选；V5 参数矩阵确认相同模拟时长/Single 也被拒绝。

## 必须修正

1. **前缀和模糊转写仍升级为自动采纳。**
   - 模拟同艺人 `Test Artist`，无时长/专辑/ISRC：`Love` → `Love Story`，标题 0.92、总分 0.953、`variant_containment`、strong、auto_accept。
   - 同样条件：`好きだから` → `Sukidakaro`，标题 0.90、总分 0.941、`romaji_fuzzy`、strong、auto_accept。
   - `こうこ` → `Koko`，长音折叠后标题 0.98、总分 0.988，被当作 `romaji_exact` 并 auto_accept。此类折叠可帮助召回，但不能隐去原始读音差异和歧义。
   - 位置：scorer.py 的 variant_containment/romaji_fuzzy/长音转换分支，以及验证等级和 evaluate_candidates。
   - 要求：完整确定对应、前缀、编辑相似、长音/多读音近似各自保留方法与歧义；后几类不能只凭艺人一致变为独立强身份并 auto_accept。新增独立边界回归，保留截图正例高分。

2. **没有实现 title_unverified。**
   - scorer.py:791 把所有 title_score < 0.45 都归成 title_mismatch，包括没有查明官方译名的跨脚本候选和缺失标题。
   - 要求：未知对应与明确异歌分开；二者都可不默认选择，但诊断不能把“未核验”写成“已证明歌名不符”。可靠身份映射和现有 ISRC 冲突规则按方案处理。

3. **页面验证报告与代码不符。**
   - index.html 未修改；633 行仍为“Apple Music 当前曲库未收录”，683 行仍为“未收录”。全部候选拒绝后不会自动变成报告宣称的灰色“未匹配”。
   - tests/test_match_identity_scoring_v5.py 的 U01 仅手动构造 SongMatchResult 并检查字段，不运行页面、不检查勾选与弹窗。
   - 无 selected_candidate 时现有操作区显示“搜曲库”，不是报告所说的“换版本”。
   - TitleComparisonResult 的 method/matched_pair/details 没有进入 MatchEvidence 或诊断序列化，页面也无法显示方案要求的命中方法与变体。
   - 要求：修正“未收录”与“候选不符/未找到可信匹配”的页面分支，透传必要诊断，执行实际浏览器测试并附记录。报告中的页面行为只能据实填写。

## 交付补充

- RESULT.md 多处把实际日文正例改写为“喜欢是因为你。”；修正为真实测试输入，避免报告误导。
- artist_aliases.py 已变更，ALIAS_VERSION 仍为 v4；按方案升级实际变动的别名版本，验证旧 review/no_match 在查询别名变化后触发必要的新检索。
- 完成上述修订后更新 RESULT.md，重新提交验收。此次 Codex 未修改业务代码。
