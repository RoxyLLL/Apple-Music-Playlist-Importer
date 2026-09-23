# 歌曲名、歌手名检索与评分优化规格

## 分工与范围

Codex 提供方案并在交付后独立验收；Antigravity 编写代码、测试和实现报告。基于现有 `japanese-universal-matching` 实现迭代，保留已有跨区等价映射、缓存迁移、错误状态和人工确认语义。当前工作区的 `applemusic/web/app.py` 与 `applemusic/web/static/index.html` 已有用户改动，实施时必须先查看 diff 并保留。

## 已核对的现状

1. 来源平台在 `Track` 中提供原始标题、歌手列表、部分译名/别名、ISRC 等；`TextCleaner.parse_title/parse_artists` 清洗版本与歌手，`JapaneseNormalizer` 生成假名和罗马字变体。
2. `QueryPlanner` 依次生成目标区原文/静态别名、跨文字变体、Apple 提示、日区发现；`MatchingEngine` 搜索并聚合候选，最终交给 `TrackScorer` 打分和 `evaluate_candidates` 决策。
3. `client.py:_parse_song_item()` 把 Apple `artistName` 按逗号直接拆分。关系中的 `artist_ids` 会保存，但评分主路径没有用这些 ID 验证艺人身份。
4. 当前分数是规则加权的匹配分，不是经过真实语料校准的正确概率。真实线上曲库的召回率仍未验证。

## 已复现的缺口

| 编号 | 现象 | 直接原因 | 风险 |
| --- | --- | --- | --- |
| R1 | 来源 `Original / Actual Artist` 与候选 `Different Title / 空艺人` 只因 ISRC 相同，即得到标题 1、艺人 1、总分 1、`auto_accept` | `scorer.py` 的 ISRC 分支覆盖真实字段分数；冲突判断只在双方都有艺人时执行 | 元数据不完整或污染时误自动采纳 |
| R2 | 来源 `Main Artist feat. Guest Artist` 与候选主艺人 `Guest Artist`、同歌名，艺人分 1、`auto_accept` | 艺人列表任意交叉命中可覆盖主艺人比较 | 客串、翻唱、角色歌错认主表演者 |
| R3 | `Track.trans_title`、`Track.aliases` 用于评分，却不进入 `QueryContext` 和查询计划 | 检索与打分使用不同元数据 | 目标曲目存在却检索不到 |
| R4 | 样例 `灰かぶり (Cinder ella) / 十明` 生成 A 阶段 5、B 阶段 4、D 阶段 2 条；总预算 8 条，全部未命中时 D 阶段没有额度 | 阶段按顺序遍历，仅全局截断 | 日区发现与回映射被别名查询挤掉 |
| R5 | Apple `artistName="Tyler, The Creator"` 被解析为 `["Tyler", "The Creator"]` | 客户端按逗号盲拆 | 错误主艺人、评分和展示 |

补充边界：`engine.py` 仅在候选列表完全为空时请求提示词；低质量候选也会阻止提示扩展。日区预筛只要求分数 0.50，目标区请求不完整时仍可能落入“目标区不可用”分支；这些场景应同时修正。

## 目标行为

### 名称与身份

- 保留原始歌名和 Apple 原始 `artistName` 供展示；另建结构化、可追溯的比较视图。不要用一个清洗字符串覆盖原始事实。
- 歌名比较区分：原文核心名、来源明确提供的译名/别名、Apple 本地化名称、机器转写、版本标签。每个变体记录来源和强弱。
- 歌手比较区分主艺人、客串/合作艺人、声优/角色、Apple 艺人 ID。逗号不一律代表歌手分隔；仅在有明确结构化关系或可信分隔符时拆分。
- 同一罗马字生成器产生的歌名和歌手命中仍只算一类相关弱证据；纯汉字读音、搜索提示与模糊包含不能自动升级为强身份依据。

### 查询与候选

- 将来源 `trans_title/aliases` 纳入有上限、带来源的查询候选；译名必须来自源数据或 Apple 元数据，不生成未经证实的机器译名。
- 全局查询预算默认最多 8 次 catalog、2 次 suggestions、1 次批量 equivalents。按预计信息增益排队并预留日区发现额度：目标区多种查询均未找到可靠候选时，日语高可能性歌曲仍至少能尝试 1 条日区查询；ISRC 占用预算时相应压缩低优先级别名。
- `no_hits`、低分候选、`review`、请求失败分别驱动不同的后续动作。低分候选不应阻止合理的提示或日区发现；401/429/超时不应被当成目标区确实无歌。
- 任何日区命中都必须映射回目标 storefront 后才能作为可添加候选。仅当日区身份有强依据、目标区搜索和 equivalents/ISRC 回查完整且确实无映射时，才标记 `unavailable_in_target_storefront`；其余情况保留 `review/search_incomplete/no_match` 的实际语义。

### 评分与决策

- 评分组件分别输出原始歌名、艺人主次、版本、专辑、时长、ISRC、Apple 等价映射的依据和冲突。分数必须反映真实字段；不可因为 ISRC 相同就把缺失的歌名、艺人写成满分。
- 同 ISRC 且完整元数据不冲突可自动采纳；歌名明显冲突、主艺人缺失或明显不符、版本冲突须转 `review`，并保留 ISRC 证据与冲突原因。
- 客串艺人命中可帮助召回，但主艺人明显不符时不能得到艺人满分或自动采纳，除非有可核实的同艺人稳定 ID/官方等价信息。
- `auto_accept` 同时要求分数阈值、无硬冲突、身份充分、目标区可用；`review` 不自动勾选。匹配分 `/100` 是排序分，不应被称为“正确概率”。
- 新规则/查询策略需要版本升级，旧 `auto_accept` 重评；同一来源身份与 storefront 的 `user_confirmed` 继续保留。

## 完成定义

`TEST_MATRIX.md` 全部针对性用例通过，现有全量测试无回归，`git diff --check` 通过。Antigravity 提交改动清单、复现前后结果、测试命令和输出、剩余限制。真实线上召回率只有在有来源的独立 Apple Catalog 样本上测得后才能报告；无凭据时标注未测，不以合成集替代。
