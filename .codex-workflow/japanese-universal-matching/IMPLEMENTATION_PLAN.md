# 实施计划

## Phase 0：先修契约缺口

1. 给 `MatchEvidence` 正式增加 `provenance`、`evidence_families`、`rule_version`、`query_policy_version`、`romanizer_version`、`exception_registry_version`；禁止静默忽略未知字段。
2. 统一 `SingleTrackDiagnostics` 与 engine 字段名，增加序列化脱敏测试。
3. 修正现有缓存版本测试，证明当前版本命中不重评、旧版本必重评。

## Phase 1：模型与 Apple API 客户端

1. 扩展 `AppleMusicTrack` 保存 artist IDs、album ID、曲序、发行日期、locale 和 discovery storefront。
2. `search_catalog()` 增加 `locale`；URL 参数传 `l`，两级缓存键和 SQLite schema 同步包含 locale/policy version。
3. 增加 storefront 元数据读取与缓存，只使用 `supportedLanguageTags` 内的 locale。
4. 增加只读客户端方法：search hints/suggestions、批量 song equivalencies、目标区 ISRC 回查。
5. 所有新增请求纳入现有限流、singleflight、诊断及错误分类。

## Phase 2：通用日语规范化

1. 新建 `japanese_normalizer.py`，实现 `ScriptProfile`、NFKC、假名统一与罗马字变体。
2. 使用 pykakasi 覆盖含汉字文本，包括纯汉字；删除“无假名直接返回空”的覆盖阻断。
3. 不再使用逐字 `KANJI_MULTI_READINGS` 的组合爆炸作为主算法；该表可暂时兼容，但不得影响默认排序。
4. 输出稳定、有限的变体（建议每个字段最多 4 个），并为每个变体标注来源/强度。
5. 对空值、Emoji、符号艺名、数字、全角拉丁字符和中日纯汉字歧义加测试。

## Phase 3：查询规划器

1. 用 `QueryContext` 生成带 storefront/locale/phase/cost 的查询，不再只返回字符串。
2. 使用 beam-style 选择高收益组合，禁止 title variants × artist variants 全量笛卡尔积。
3. 引入阶段预算与全局批次预算；稳定去重，支持早停。
4. 旧静态 alias 仅作为低优先级 verified exception；新增任何条目必须有来源，不得为失败样例临时加表。

## Phase 4：跨区发现与候选聚合

1. 目标区无强候选时进入 JP discovery。
2. JP 预筛后批量调用 equivalents；再用 ISRC 补漏。
3. 最终结果只保留目标区实体，同时在 diagnostics 记录发现链。
4. 候选用稳定身份聚合，禁止同一歌曲因不同查询重复占据 top-N。
5. 区分 `no_match` 与 `unavailable_in_target_storefront`。

## Phase 5：评分与决策

1. 将“查询来源”和“身份依据”拆开；查询命中不等于匹配证据。
2. 证据按 family 去相关：同一 romanizer 生成的 title/artist 相似只算一个弱证据族。
3. 自动采纳要求稳定身份或至少两类独立一致证据，且无版本/主艺人冲突。
4. 纯汉字日文读音、包含关系、同专辑同长度、Apple suggestion 均不得单独提升为 strong。
5. 继续保护 Live/伴奏/翻唱/Remix/角色歌/feat. 主次身份差异。

## Phase 6：缓存、API、前端与诊断

1. SQLite 采用向前迁移，不删除用户数据库；新表/列缺失时自动迁移并保留已有人工确认。
2. Web API 返回 `decision`、`availability`、`discovery_path`、证据族和执行预算。
3. 界面只自动选择 `auto_accept/user_confirmed`；`review` 即使 99 分也不自动选。
4. “未收录”行展示失败阶段：目标区无结果、目标区不可用、授权/频控或检索未完成。
5. 单曲诊断不得包含 Token、Cookie、Authorization、用户绝对路径。

## Phase 7：验证和交付

1. 完成 `TEST_MATRIX.md` 全部测试。
2. 建立独立的日语 catalog 评估集，至少 200 个正例、100 个困难负例；按文字类型和版本类型分桶。
3. 评估数据可包含公开 catalog 元数据/ID，但不得被运行时代码读取为别名或缓存种子。
4. 输出基线与新版：Recall@20、MRR、auto precision、review/no_match/unavailable、平均/P95 请求数和延迟。
5. live 评估只读 catalog，不调用资料库写入接口；无凭据时明确标记未执行，不能用 mock 结果冒充线上覆盖率。
6. 运行完整测试套件、`git diff --check`、多 `PYTHONHASHSEED` 稳定性测试。

## 建议文件边界

- 新建：`matcher/japanese_normalizer.py`、`matcher/query_models.py`、`matcher/candidate_identity.py`。
- 修改：`models.py`、`client.py`、`matcher/query_planner.py`、`matcher/engine.py`、`matcher/scorer.py`、`matcher/evidence.py`、`cache.py`、Web API/前端。
- 逐步降级：`KANJI_TO_ROMAJI_COMPOUNDS`、`KANJI_MULTI_READINGS` 和歌曲级 scoped aliases，不要求一次删除，但测试必须证明主路径不依赖它们。

