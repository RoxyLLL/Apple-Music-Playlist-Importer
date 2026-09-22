# 日语歌曲通用多语言检索规格

## 1. 目标

把当前依赖少量人工曲名/艺人别名的实现，改造成面向任意日语歌曲的通用检索管线。系统应覆盖日文原名、平假名、片假名、纯汉字、罗马字、英文艺名、括号译名、不同地区本地化名称及常见版本标记。

“几乎所有”定义为可量化目标，而不是承诺 Apple Music 不存在或目标区不可用的歌曲也能匹配：

- 对测试语料中“Apple Music 目标区确实存在”的日语歌曲，候选召回率 Recall@20 >= 95%。
- 每个文字类型分桶（假名、片假名、纯汉字、混合、拉丁艺名、跨区译名）Recall@20 >= 90%。
- 困难负例自动采纳错误数为 0；自动采纳精确率目标 >= 99.5%。
- 目标区不存在但日本区存在时，明确显示“目标区不可用”，不得伪装为“未找到相似歌”或返回日本区不可添加 ID。

## 2. 当前实现的结构性问题

1. `artist_aliases.py` 与 `title_aliases.py` 承担了主要跨语言能力，覆盖率随手工表规模增长，无法接近全量日语曲库。
2. `cleaner.py:get_japanese_romaji_variants()` 对无假名、且不在人工复合词表内的纯汉字直接返回空；大量日文汉字歌名和艺人名无法生成查询。
3. `query_planner.py` 只有歌名含假名时才生成罗马字，且动态艺人罗马字没有进入主查询计划；首轮 2 次、深度 6 次的固定截断又让后面的通用变体经常不执行。
4. `client.py:search_catalog()` 不支持 Apple Music 的 `l` 本地化参数，缓存键也没有 locale；不同语言响应会混用。
5. 搜索响应只保留艺人显示名，丢弃 artist ID、album ID、track/disc number、发行日期等可用于身份校验的字段。
6. 当前 `MatchEvidence` 未声明 scorer 实际传入的 `provenance/rule_version/alias_version`，Pydantic 会忽略这些字段；缓存版本校验名义存在、实际证据不能持久化。
7. `SingleTrackDiagnostics` 与 engine 使用的字段名不一致，应在扩展前先统一契约。

## 3. 核心原则

### 3.1 召回与采纳分离

- 机器转写、搜索提示、去标点、长音/空格变体只用于扩大候选召回。
- 这些弱信号不能因“歌名和艺人都转写相似”就被当成两项独立强证据。
- 自动采纳必须来自稳定身份或多个独立一致字段：ISRC、Apple 等价映射、稳定 artist ID、严格标题/艺人一致、时长及版本一致等。

### 3.2 目标区可用性优先

- 最终 `selected_candidate` 必须属于用户目标 storefront。
- 日本区仅作为发现区。发现候选后，优先用 Apple 官方 `filter[equivalents]` 映射到目标区；其次用 ISRC 在目标区查找。
- 无法映射时返回 `unavailable_in_target_storefront`，不得直接将日本区 ID 加入用户资料库。

### 3.3 静态表只做例外注册表

- 不再通过向 `ARTIST_GROUPS`、`TITLE_GROUPS`、`ARTIST_SCOPED_TITLE_ALIASES` 连续添加歌曲来提高覆盖率。
- 已核实的特殊艺名/官方改名可保留，但必须有来源、作用域、版本和测试。
- 测试语料中的真实曲目只属于 fixture/evaluation corpus，不得导入运行时别名表或缓存种子。

### 3.4 缓存保存结果，不发明事实

- 查询缓存按 storefront + locale + query + limit + query-policy-version 隔离。
- 匹配缓存按源平台、源 ID/ISRC、规范化元数据、目标区、规则版本隔离。
- 用户确认只复用于同一源身份和目标区，不得自动升级成全局曲名/艺人别名。
- 旧规则的 `auto_accept` 必须重评；人工确认保留但严格限定作用域。

## 4. 通用检索契约

每首歌按阶段执行，达到强证据即停止：

1. **Identity**：有 ISRC 时先查目标区 ISRC。
2. **Native target**：目标区检索原始 `title + artist`、规范化版本；使用目标区默认语言。
3. **Script variants**：生成假名/汉字转写、罗马字书写变体、艺人和歌名组合；纯汉字也允许生成，但标记为弱来源。
4. **Localized target**：仅使用 storefront 声明支持的语言标签发起本地化检索，缓存必须区分 locale。
5. **Apple hints/suggestions**：仅在未命中时获取 Apple 提供的搜索词，作为受限查询扩展，不直接作为身份真值。
6. **JP discovery**：在 `jp` storefront 以 `ja-JP` 和必要的罗马字变体发现候选。
7. **Target resolution**：把 JP 候选通过 `filter[equivalents]` 或 ISRC 映射回目标区，再统一评分。

默认预算：最多 8 次 catalog search、2 次提示/建议、1 次批量等价映射；命中强证据立即停止。预算需可配置，并由批量全局限流器约束。429/401/网络错误不得被计为 `no_match`。

## 5. 日语规范化要求

- Unicode NFKC、全半角、大小写、标点、日文空格与中点统一。
- 平假名/片假名互转；长音、促音、拨音、组合音正确处理。
- 罗马字至少支持 Hepburn 主输出及无空格/无连字符/长音简化等搜索变体。
- `pykakasi` 用于通用汉字读音候选，不再依赖逐字手写读音组合；不确定读音必须保留弱证据等级。
- 不做“机器翻译歌名 = 官方译名”的假设；英文/中文译名只能来自源元数据、Apple 本地化响应、Apple 等价实体或有来源的例外注册表。
- 中文汉字文本可能不是日语。纯汉字转写只能扩大查询，不能提升身份置信度。

## 6. 完成定义

- 运行时主路径不依赖新增歌曲级硬编码。
- 日本区发现和目标区等价映射真实接入 engine，且有 API mock 参数序列测试。
- 所有查询携带 provenance、locale、storefront、阶段与成本，诊断可导出。
- 前端区分 `matched/review/no_match/unavailable/error`，只有 `auto_accept/user_confirmed` 默认勾选。
- 完成 `TEST_MATRIX.md` 的离线单元/集成测试与只读 catalog 评估；不得以少量示例或纯 synthetic 100% 代替真实覆盖率报告。

