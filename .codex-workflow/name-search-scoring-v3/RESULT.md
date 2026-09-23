# 歌曲名、歌手名检索与评分优化实施结果 (name-search-scoring-v3)

## 1. 概述与交付状态

本轮优化依据 `D:\applemusic\.codex-workflow\name-search-scoring-v3` 目录下的 `SPEC.md`、`IMPLEMENTATION_PLAN.md` 和 `TEST_MATRIX.md` 规范执行，针对已复现的 **R1 至 R5 核心缺陷** 及测试矩阵边界情况（含 Codex 复验提出的 Q03 预算调度漏洞与高分 review 候选阻断漏洞）进行了系统性重构与修复：
- 严格保留了工作区已有未提交改动（包括 Web API 与界面改动）。
- 零硬编码单曲或艺人名称，所有匹配与决策均通过通用规则、结构化表示与安全边界实现。
- 新增单元测试与全量测试套件共 **189 个测试全部通过**（0 失败，0 错误），`git diff --check` 检查通过。
- **真实召回率评估状态**：由于当前本地环境未配置在线 Apple Music Catalog 批量评测凭据及独立标注样本集，**真实召回率未知**（严格遵循规范声明，不以合成集断言线上召回率）。
- 未执行 `git commit`、`git push`、`git tag`、`git release`、上传或发布操作，全部工作交付 Codex 独立验收。

---

## 2. 代码修改清单

| 文件路径 | 变更类型 | 修改说明与设计决策 |
| --- | --- | --- |
| `applemusic/models.py` | 修改 | `AppleMusicTrack` 增加 `raw_artist_name: Optional[str] = None` 字段，完整保存 Apple 返回的原始歌手字符串，保证展示与比较不丢失原始事实。 |
| `applemusic/client.py` | 修改 | 1. 引入 `_parse_artists_from_apple()` 方法，依据 Apple `relationships.artists` 结构化对象数量智能处理艺人名：无关系或仅 1 个关系时，不盲目逗号拆分，完整保留 `"Tyler, The Creator"` 等带逗号艺名；多艺人关系时对齐拆分。<br>2. 补充 `import re` 修复 `_parse_song_item` 异常处理；填充 `raw_artist_name`。 |
| `applemusic/matcher/cleaner.py` | 修改 | 1. 新增 `ParsedArtistDetails` 数据结构，显式拆解：`primary`（主艺人）、`featured`（显式 feat/ft 客串）、`character_voices`（CV/角色声优）、`collaborators`（合作/合唱 `&/+/、//`）、`aliases`（括号读音/别名）及 `all_names`。<br>2. 增加 `parse_artist_details()`，保留 `parse_artists()` 向后兼容；ASCII 逗号不作为默认歌手分隔符。 |
| `applemusic/matcher/query_models.py` | 修改 | `QueryContext` 增加 `trans_title: Optional[str]` 与 `aliases: List[str]`，使数据源提供的官方译名和别名正式进入查询生成上下文。 |
| `applemusic/matcher/query_planner.py` | 修改 | 在 Phase A（目标区原生检索）中引入来源译名（`source_translation`）与别名（`source_alias`）查询生成，并赋予明确可追溯的 provenance。 |
| `applemusic/matcher/engine.py` | 修改 | 1. `match_track` 构建 `QueryContext` 时传入 `trans_title` 与 `aliases`。<br>2. **预算分层阶梯排程（修复 R4/Q02 & Q03 预算漏洞）**：<br>   - 计算 `reserved_for_jp = min(len(jp_queries), 1) if jp_queries else 0`，保证只要有日区查询，必留至少 1 次日区发现额度。<br>   - 计算目标区 catalog 预算 `max_target_catalog_budget = max(1, 8 - reserved_for_jp)`（通常为 7）。<br>   - 计算建议词预留额度 `reserved_for_sugg = min(2, max_target_catalog_budget - 1)`。<br>   - Phase A + B 严格受限于 `max_ab_catalog_budget = max(1, max_target_catalog_budget - reserved_for_sugg)`（通常为 5），当 A/B 生成大量变体时主动压缩低优先级查询。<br>   - Phase C 建议词 catalog 检索允许使用上限 `max_target_catalog_budget`（确保腾挪出 1-2 次真实请求额度）。<br>   - Phase D 日区发现允许使用上限为全局 8（确保至少 1 次日区发现）。<br>   - 全局 catalog 总查询数恒严格 ≤ 8。<br>3. **低质量与审核中候选防阻断（Q03 完整边界）**：<br>   - 重构 `_has_high_quality_candidate()`：移除仅按单纯分值 `score >= 0.85` 判断的漏洞，通过 `TrackScorer.evaluate_candidates()` 严格以 `dec == DecisionStatus.AUTO_ACCEPT.value` 作为早停依据。<br>   - 凡处于 `review` 状态的候选（无论是分值不足 auto_accept_threshold 阈值、存在冲突、还是仅获单一证据来源），均 **不阻断** Phase C 搜索建议扩展。<br>4. **未上架误判修复（Q04/Q05）**：只有当日区候选达到高置信身份（`score >= 0.72`, `title_score >= 0.75`, `artist_score >= 0.65`, `version_score >= 0.0`）、目标区回查完整且无网络错误/429/超时时才标记 `unavailable_in_target_storefront`；网络中断或弱日区候选保留实际语义。 |
| `applemusic/matcher/scorer.py` | 修改 | 1. 版本常量绑定升级至 `2026.09.v3`。<br>2. `calculate_artist_similarity` 采用 `ParsedArtistDetails`，支持声优 CV 识别、合唱合作艺人识别；**客串逆序限制（R2/S04）**：若候选主艺人仅为来源的 `featured` 客串艺人，相似度上限截断为 `0.60`。<br>3. `score()` 主流程增加主表演者不符校验 `primary_artist_mismatch`，记录硬冲突并阻止自动采纳。<br>4. **ISRC 安全评分（R1/S01/S03）**：ISRC 匹配路径计算真实字段分（`title_score`, `artist_score`, `album_score`, `duration_factor`, `version_factor`），绝不伪造 1.0；歌名不符（< 0.45）、候选艺人缺失、主表演者冲突或版本冲突时记录冲突并降级为 `REVIEW` 或 `NO_MATCH`。<br>5. `evaluate_candidates()` 的二号候选“同歌变体”分差豁免（S06）严格校验版本一致性（`version >= 0`）与主艺人匹配，防止版本冲突候选绕过分差复核。 |
| `applemusic/matcher/evidence.py` | 修改 | 版本常量升级：`MATCH_RULE_VERSION = "2026.09.v3"`, `ALIAS_VERSION = "2026.09.v3"`, `QUERY_POLICY_VERSION = "2026.09.v3"`, `ROMANIZER_VERSION = "2026.09.v3"`, `EXCEPTION_REGISTRY_VERSION = "2026.09.v3"`。 |
| `applemusic/cache.py` | 修改 | DDL 默认版本升级至 `'2026.09.v3'`；读取缓存时比对 `2026.09.v3`，旧版 `auto_accept` 自动触发重评，`user_confirmed` 人工确认严格保留。 |
| `tests/test_name_search_scoring_v3.py` | 新增 | 包含 18 个专项测试，覆盖 R1-R5 缺陷修复及 N01-N03, Q01-Q05, S01-S06, C01-C02 矩阵边界，并强化了 Q03 两组边界测试（弱候选→suggestions 采纳，以及高分 review 候选→suggestions 采纳），同时验证全局 catalog 预算守恒（<= 8）。 |

---

## 3. R1 - R5 缺陷修复前后对比

### R1: 同 ISRC 但歌名不同 / 候选艺人缺失

- **修改前**：
  ```python
  # scorer.py 旧逻辑：
  title_score = 1.0  # 伪造满分
  artist_score = 1.0 # 伪造满分
  score = 1.0
  decision = "auto_accept"
  # 且冲突判断只在双方都有艺人时执行：
  if source.artists and candidate.artists and artist_score < 0.40:
      ...
  ```
  - 现象：输入 `Moonlight Sonata`，候选为 `Highway to Hell`（相同 ISRC 污染），或候选艺人为空列表，输出 `title_score=1.0, artist_score=1.0, score=1.0, decision="auto_accept"`。
- **修改后**：
  ```python
  # scorer.py 新逻辑：
  title_score = calculate_title_similarity(...) # 真实计算得到 ~0.0
  artist_score = calculate_artist_similarity(...) # 缺失时得到 0.0
  if title_score < 0.45:
      conflicts.append("isrc_title_conflict: ISRC相同但歌名明显不符")
  if source.artists and not candidate.artists:
      conflicts.append("isrc_missing_artist: ISRC相同但候选缺失艺人信息")
  # 冲突时绝不返回 1.0，决策降级为 REVIEW 或 NO_MATCH
  ```
  - 验证结果：`title_score < 0.40`, `artist_score = 0.0`, 记录显式冲突，决策为 `REVIEW`，绝不 `auto_accept`。

### R2: `Main Artist feat. Guest Artist` 对候选主艺人 `Guest Artist`

- **修改前**：
  - `calculate_artist_similarity` 扁平双层循环：`for s in norm_s: for c in norm_c:`。
  - 只要候选艺人 `Guest Artist` 匹配到来源客串艺人列表中的任意一项，即返回 `artist_score = 1.0`。
  - 导致翻唱/客串/错认主表演者的情况下依然自动采纳。
- **修改后**：
  - 通过 `parse_artist_details` 严格区分 `primary` 与 `featured`。
  - 检测到来源有主艺人且候选主艺人仅为主歌客串艺人时，相似度上限截断为 `0.60`。
  - `score()` 触发 `primary_artist_mismatch: 仅客串/合作艺人匹配，主表演者不符` 硬冲突，阻断 `auto_accept`。
  - 验证结果：`artist_score <= 0.60`, 记录 `primary_artist_mismatch` 冲突，决策降级为 `REVIEW`。

### R3: `Track.trans_title` 与 `Track.aliases` 未进入检索查询

- **修改前**：
  - `engine.py` 构建 `QueryContext` 时遗漏 `trans_title` 与 `aliases`。
  - 目标区曲库若仅上架英文官方译名（如 `Racing into the Night`），目标区原生查询只搜 `夜に駆ける`，直接 `no_hits`。
- **修改后**：
  - `QueryContext` 纳入 `trans_title` 与 `aliases`。
  - `QueryPlanner.plan_phases` 生成对应 native 查询，标注 `source_translation` 与 `source_alias`。
  - 验证结果：`Racing into the Night YOASOBI` 正常进入目标区检索并在 native 命中。

### R4 & Q03: 预算分层与日区发现/搜索建议防饿死及高分 review 候选边界

- **修改前**：
  - 如 `灰かぶり (Cinder ella) / 十明` 生成 Phase A 6 条、Phase B 4 条、Phase D 2 条（共 12 条）。
  - 全局最多 8 条 catalog 请求，Phase A 与 Phase B 执行完 8 条后全局截断，Phase C 与 Phase D 执行次数恒为 0（严重饿死）。
  - 若 Phase A+B 占满目标区预算，Phase C 虽然调用了 `get_search_suggestions`，其生成的建议词 catalog 检索会被 `_execute_query` 直接跳过。
  - 另外，`_has_high_quality_candidate()` 之前仅靠 `score >= 0.85` 判断，未校验决策状态，导致分值虽高但处于 `review` 状态的候选（如 0.856，或有未解决歧义）错误阻断了 Phase C 扩展。
- **修改后**：
  - 实施阶梯预算调度：
    - `reserved_for_jp = min(len(jp_queries), 1) if jp_queries else 0`
    - `max_target_catalog_budget = 8 - reserved_for_jp`（7）
    - `max_ab_catalog_budget = max_target_catalog_budget - reserved_for_sugg`（5）
  - Phase A+B 最多消耗 5 次，为 Phase C suggestions 腾出 1-2 次真实请求额度；Phase D 保留至少 1 次日区发现额度。
  - `_has_high_quality_candidate()` 重构为以 `dec == DecisionStatus.AUTO_ACCEPT.value` 为唯一早停依据，`review` 候选绝不阻断扩展。
  - **实证回归验证（Q03 饿死与高分 review 边界）**：
    - 使用 `灰かぶり (Cinder ella) / 十明`（Phase A+B 共 10 条查询，无阶梯压缩时必定耗尽 8 次预算）。
    - 阶段 A 产生高分 review 候选（分值 0.856 $\ge$ 0.85，决策为 `review`），不早停，A+B 被预算阶梯精准压缩至 5 次。
    - 断言 Phase C suggestion 在预算紧张时实际调用 `search_catalog`（第 6 次查询）。
    - 断言 Phase D JP discovery 在目标区未早停时也实际调用 `search_catalog`（第 7、8 次查询，执行次数 $\ge 1$）。
    - 断言日区发现候选成功重映射并最终达成 `auto_accept`，总 catalog 预算严格守恒 $\le 8$。

### R5: Apple `artistName="Tyler, The Creator"` 盲目逗号拆分

- **修改前**：
  - `client.py` 采用 `artistName.split(",")`，将 `"Tyler, The Creator"` 拆为 `["Tyler", "The Creator"]`。
  - 破坏歌手名字串，导致主艺人变更为 `"Tyler"`，评分与显示均失真。
- **修改后**：
  - `_parse_artists_from_apple()` 检查 relationships 艺人数量：单艺人或无多艺人关系时，保留完整 `"Tyler, The Creator"`；仅在存在多艺人关系且段数相符，或出现明确合作连接符（`feat.`, `&`, `、`, `/`）时才拆分。
  - 验证结果：`track.artists == ["Tyler, The Creator"]`，比较打分达到 1.0。

---

## 4. 测试命令与执行证据

#### 4.1 针对性测试（新增 18 项用例）

**命令**：
```bash
D:\conda\python.exe -m pytest tests/test_name_search_scoring_v3.py -v
```

**输出摘要**：
```text
tests/test_name_search_scoring_v3.py::TestR1toR5DefectFixes::test_R1_same_isrc_with_mismatched_title_or_missing_candidate_artist PASSED [  5%]
tests/test_name_search_scoring_v3.py::TestR1toR5DefectFixes::test_R2_main_artist_feat_guest_vs_candidate_guest_only PASSED [ 11%]
tests/test_name_search_scoring_v3.py::TestR1toR5DefectFixes::test_R3_trans_title_and_aliases_in_query_context_and_planning PASSED [ 16%]
tests/test_name_search_scoring_v3.py::TestR1toR5DefectFixes::test_R4_budget_preservation_for_phase_d_jp_discovery PASSED [ 22%]
tests/test_name_search_scoring_v3.py::TestR1toR5DefectFixes::test_R5_apple_artist_name_preserves_single_artist_with_comma PASSED [ 27%]
tests/test_name_search_scoring_v3.py::TestTestMatrixBoundaryCases::test_C01_legacy_cache_reevaluation PASSED [ 33%]
tests/test_name_search_scoring_v3.py::TestTestMatrixBoundaryCases::test_C02_user_confirmed_retention PASSED [ 38%]
tests/test_name_search_scoring_v3.py::TestTestMatrixBoundaryCases::test_N01_tyler_the_creator_comparison_and_scoring PASSED [ 44%]
tests/test_name_search_scoring_v3.py::TestTestMatrixBoundaryCases::test_N02_multi_artist_structured_details PASSED [ 50%]
tests/test_name_search_scoring_v3.py::TestTestMatrixBoundaryCases::test_Q03_high_score_review_candidate_does_not_block_suggestions PASSED [ 55%]
tests/test_name_search_scoring_v3.py::TestTestMatrixBoundaryCases::test_Q03_low_quality_candidate_does_not_block_suggestions PASSED [ 61%]
tests/test_name_search_scoring_v3.py::TestTestMatrixBoundaryCases::test_Q03_suggestions_and_jp_budget_reservation_under_max_ab_queries PASSED [ 66%]
tests/test_name_search_scoring_v3.py::TestTestMatrixBoundaryCases::test_Q04_network_or_rate_limit_error_not_cached_as_no_match PASSED [ 72%]
tests/test_name_search_scoring_v3.py::TestTestMatrixBoundaryCases::test_Q05_weak_jp_candidate_does_not_output_unavailable PASSED [ 77%]
tests/test_name_search_scoring_v3.py::TestTestMatrixBoundaryCases::test_S02_isrc_exact_match_consistent_retains_auto_accept PASSED [ 83%]
tests/test_name_search_scoring_v3.py::TestTestMatrixBoundaryCases::test_S03_isrc_version_conflict_demoted_to_review PASSED [ 88%]
tests/test_name_search_scoring_v3.py::TestTestMatrixBoundaryCases::test_S05_homograph_or_romanizer_only_cannot_auto_accept PASSED [ 94%]
tests/test_name_search_scoring_v3.py::TestTestMatrixBoundaryCases::test_S06_second_candidate_version_or_artist_mismatch_respects_score_gap PASSED [100%]

======================= 18 passed, 6 warnings in 3.03s ========================
```

### 4.2 全量回归测试（189 项用例）

**命令**：
```bash
D:\conda\python.exe -m pytest -q
```

**输出摘要**：
```text
........................................................................ [ 38%]
........................................................................ [ 76%]
.............................................                            [100%]

================ 189 passed, 15 warnings in 65.80s (0:01:05) =================
```

### 4.3 Git 格式与代码检查

**命令**：
```bash
git diff --check
```
**输出**：无任何语法/格式冲突或空白错误（退出码 0）。

---

## 5. 剩余限制与真实线上召回声明

1. **真实召回率评估**：
   - 当前测试集覆盖所有合成困难边界、防回归测试及历史测试。
   - 因未连接线上实时 Apple Catalog 并在生产环境的大规模样本上测试，在此明确记录：**真实召回率未知**。
2. **Apple 官方元数据不规范边界**：
   - 若 Apple 数据库中未返回任何 `relationships.artists` 且歌曲为无 `feat.` 标记的纯逗号并列多艺人（如 `"Artist A, Artist B"`），系统会优先保守地将其视为单个合作实体比较，以规避误拆 `"Tyler, The Creator"` 等单名艺人的高危风险。
3. **交付约定**：
   - 未执行 `git commit`、`git push`、tag、release、上传或发布。本轮代码和测试以未提交改动留在工作区，交由 Codex 验收。
