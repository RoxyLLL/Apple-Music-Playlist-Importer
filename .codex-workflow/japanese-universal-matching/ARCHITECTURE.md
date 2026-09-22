# 架构方案

## 数据流

```text
源曲元数据
  -> ScriptProfile + CanonicalForms
  -> 分阶段 QueryPlan
  -> 目标区候选
  ->（未解决）JP 区发现候选
  -> equivalents / ISRC 映射回目标区
  -> CandidateIdentity 聚合去重
  -> EvidenceScorer
  -> auto_accept / review / no_match / unavailable / error
```

## 1. 新增/调整的数据模型

### ScriptProfile

- `has_hiragana/has_katakana/has_cjk/has_latin`
- `japanese_likelihood`：仅控制查询计划，不作为匹配分数。
- `ambiguous_cjk_only`：纯汉字必须为 true，避免把中文当日文强证据。

### TextVariants

- `original`
- `nfkc`
- `kana_normalized`
- `hepburn`
- `hepburn_compact`
- `long_vowel_relaxed`
- `source_translation`（只来自源字段）
- 每个 variant 带 `provenance` 与 `verification_level`。

### PlannedQuery

扩展为：

- `query`
- `storefront`
- `locale`
- `phase`
- `provenance`
- `cost`
- `weak_only`

排序必须稳定，不使用 set 的遍历顺序。查询去重键包含 storefront 和 locale。

### AppleMusicTrack / CandidateIdentity

保留：song ID、storefront、ISRC、artist IDs、album ID、title、artistName、albumName、duration、track/disc number、release date、content rating、关系来源、locale。

同一候选的聚合键优先为：

1. 目标区 song ID；
2. ISRC + version fingerprint；
3. Apple equivalents 映射链；
4. 最后才使用规范化文本 + 时长桶，并且只能进入 review。

## 2. 查询阶段

### A. 目标区快速路径

1. ISRC 精确检索。
2. 原始标题 + 原始主艺人。
3. NFKC/括号拆分后的标题 + 原始主艺人。

### B. 通用多文字变体

按收益/成本选择最多 4 条，不做笛卡尔积：

- 原标题 + 通用罗马字艺人；
- 通用罗马字标题 + 原艺人；
- 通用罗马字标题 + 通用罗马字艺人；
- 源数据中明确提供的译名 + 原艺人；
- 标题独立查询只在艺人元数据缺失或前述均无候选时执行。

### C. Apple 引导词

使用 `/search/suggestions` 或 `/search/hints` 获取 Apple 自身可检索词。只采用与源标题或艺人至少有一个规范化 token/转写 token 对齐的建议，最多执行 2 条。

### D. 日本区发现与回映射

1. 在 JP/ja-JP 执行最高收益的 2 条查询。
2. 先在 JP 候选内做保守预筛，最多保留 5 个。
3. 批量调用目标区 `/songs?filter[equivalents]=...`。
4. 对未返回 equivalent、但有 ISRC 的候选，批量按 ISRC 查询目标区。
5. 只有目标区返回实体进入最终评分；否则记录 unavailable。

Apple 官方支持 storefront 本地化参数和跨 storefront equivalencies，本设计直接使用这两个能力，不自己维护跨区 ID 对照表。

## 3. 证据等级

### 强证据

- ISRC 一致且无明确艺人/版本冲突。
- Apple `filter[equivalents]` 返回的跨区等价实体。
- 同一 Apple artist ID + 严格标题一致，并有时长/专辑/版本之一佐证。

### 中证据

- 标题与艺人规范化后严格一致，时长和版本一致。
- Apple 本地化响应中同一 song ID 的不同显示语言。

### 弱证据

- pykakasi/罗马字相似。
- 搜索提示词命中。
- 机器拆括号或去标点。
- 单独时长、专辑名、共享字符或子串。

弱证据负责召回或进入 review，不能组成“伪双强证据”。同一种算法生成的标题转写和艺人转写具有相关性，只算一个证据族。

## 4. 缓存设计

### Catalog cache

键必须包含：`storefront, locale, endpoint_kind, normalized_query/filter, limit, query_policy_version`。

### Equivalence cache

单独表：`source_storefront, source_song_id, target_storefront, target_song_id|null, fetched_at, expires_at`。空结果使用较短 TTL。

### Match cache

持久化完整 evidence 版本：`rule_version, query_policy_version, romanizer_version, exception_registry_version`。版本过期时重新评分，不复活旧 auto_accept。

用户确认不生成全局别名。若未来做学习型别名，只允许“同一 Apple 实体/ISRC 多次一致确认”形成局部建议，默认仍为 review。

## 5. 失败与界面语义

- `no_match`：查询完成，目标区和发现流程都没有合格候选。
- `unavailable_in_target_storefront`：JP 找到强候选但无法映射到目标区。
- `review`：候选存在但只有弱/中证据或版本歧义。
- `error/auth_required/rate_limited`：检索未完成，不能缓存成 no_match。
- 仅 `auto_accept` 与 `user_confirmed` 默认选择。

