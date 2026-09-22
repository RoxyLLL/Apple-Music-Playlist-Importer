# 日语歌曲通用多语言检索 - 实现与二次返工验收报告 (RESULT.md)

## 1. 任务概述

本报告记录在 `D:\applemusic` 针对 Codex 复验报告中指出的 2 项阻断问题（旧 match 缓存兼容/失效与重评错误、评估集属性定位为 Synthetic Benchmark）所完成的系统返工与验证。
所有改进均基于通用算法、官方 API 规范（Storefront, Language Tag, Suggestions, Equivalents, ISRC）、双独立佐证准则、严格版本隔离与鲁棒迁移逻辑，未向静态别名表硬编码任何歌曲/艺人别名，未执行 git commit/push/tag/publish，交回 Codex 进行独立验收。

---

## 2. 阻断问题修复与代码修改清单

### 2.1 旧 Match 缓存兼容、反序列化与重评降级机制 (`matcher/evidence.py`, `cache.py`, `test_D08`)
- **阻断问题 1 根因**:
  1. 历史数据库中存储的 `MatchEvidence` JSON 包含 `"reasons"` 列表字段。新版模型将 `reasons` 移除并设置 `Config.extra = "forbid"`，导致反序列化抛出 `ValidationError`，使得整条旧缓存解析失败，包括用户手动确认的记录也被当成缓存未命中丢失。
  2. 即使去除 `"reasons"`，新模型字段默认值曾为 `"2026.09.v2"`，且数据库表迁移新增列 `ALTER TABLE ... ADD COLUMN rule_version` 默认值也曾为 `'2026.09.v2'`，导致历史缓存被伪装成当前最新版本，直接复活旧 `auto_accept`，未触发任何重评（独立探针输出 `auto_accept / 2026.09.v2`）。
- **修复实现**:
  - **`applemusic/matcher/evidence.py` (`MatchEvidence`)**:
    - 添加 `@root_validator(pre=True)` 的 `_handle_legacy_evidence` 方法：
      - 显式 `values.pop("reasons", None)`，安全移除历史旧字段，避免 `extra = "forbid"` 抛出 `ValidationError`。
      - 对缺失的版本字段（`rule_version`, `query_policy_version`, `romanizer_version`, `exception_registry_version`, `alias_version`），一律回退默认赋值为历史旧版本 `"2026.09.v1"`，严禁默认填入 `"2026.09.v2"`。
    - 将 `MatchEvidence` 内部字段默认值设为 `"2026.09.v1"`。运行时新打分均由 `TrackScorer.score` 显式注入当前常量 `MATCH_RULE_VERSION`（`"2026.09.v2"`）。
  - **`applemusic/cache.py` (`_init_db`, `get_match`)**:
    - 在 `_init_db` 中，`match_cache` 表的 `CREATE TABLE` 与 `ALTER TABLE ADD COLUMN` 默认值统一调整为 `'2026.09.v1'`，保证迁移后的旧数据在数据库列级别同样标记为旧版本。
    - 在 `get_match` 中完善版本检查与重评逻辑：
      - 若 `result.decision == "user_confirmed"`，严格予以保留并立即返回，保证用户手动确认决策不丢失。
      - 检查 `cached_rule_ver`、`cached_policy_ver`、`cached_romanizer_ver`、`cached_registry_ver` 及数据库列 `db_rule_ver` 等，若任一版本非当前 `2026.09.v2`，标记为 `versions_outdated = True`。
      - 当 `result.decision == "auto_accept"` 且 `versions_outdated` 为 True 时，调用 `TrackScorer.score` 与 `TrackScorer.evaluate_candidates` 对候选执行重新评分。
      - 若重评后不符合 v2 自动采纳条件（如缺少双独立强证据、存在版本/艺人冲突、得分未达及格线等），则将其降级为 `review` 或 `no_match`，并在 `decision_reasons` 中记录版本升级重评原因。
      - 重评结果（包括降级后的 `decision`、`status`、更新后的 `result_json` 与最新版本号 `2026.09.v2`）通过 SQL `UPDATE match_cache` 写回数据库，防止下次重复重评或旧状态残留。
  - **测试覆盖 (`test_D08`)**:
    - 新增独立测试 `test_D08_legacy_match_cache_migration_and_reevaluation`：
      - 模拟旧版 SQLite 表结构（无版本列），写入包含历史 `"reasons"` 且无版本字段的真实旧 JSON（包含旧 `auto_accept` 与旧 `user_confirmed`）。
      - 通过 `PersistentCache` 加载并执行数据库迁移。
      - 断言旧 `auto_accept` 成功解析且被触发重新评估，由于在 v2 规则下未能满足双强证据及阈值，被正确降级为 `review`，而非复活为 `auto_accept`。
      - 断言旧 `user_confirmed` 成功解析并严格保留 `decision == "user_confirmed"`。

### 2.2 评测集定位明确为 Synthetic Benchmark (`japanese_eval_corpus.py`, `test_japanese_universal_matrix.py`, `RESULT.md`)
- **阻断问题 2 根因**: 原评测集采用 `1600000000 + index` 生成 ID，并在 `_make_positive_eval_track` 现场合成干扰项，并非真实 Apple Music 线上实时响应录制，不应声称“真实线上曲库 Recall@20 达到 100%”。
- **修复实现**:
  - **`tests/fixtures/japanese_eval_corpus.py`**:
    - 模块文档明确声明为 **Synthetic Benchmark**（合成算法评测集），用于验证评分模型、跨脚本变体、冲突检测、去相关机制及状态机决策边界。
    - `EvalTrack` 数据类新增 `is_synthetic: bool = True` 标记。
  - **`tests/test_japanese_universal_matrix.py`**:
    - `TestGroupFEvaluationCorpusMetrics` 类及各测试用例（`test_F01`, `test_F02`, `test_F03`）文档明确标注为针对 Synthetic Benchmark 的算法召回、困难负例精度及跨区等价判定验证。
  - **`RESULT.md`**:
    - 明确将离线评估集定义为 Synthetic Benchmark，如实说明各项指标是在受控合成评测集上的算法验证结果，不作任何真实线上曲库 100% 的虚假声称。

### 2.3 测试套件 Subprocess 告警消除 (`test_library_manager.py`)
- 在 `tests/test_library_manager.py` 的 `test_open_local_folder_api` 中，补齐对 `subprocess.Popen` 的 mock（原先仅 mock 了 `os.startfile`，导致测试中实际拉起 `explorer.exe` 并留下未关闭子进程），彻底消除 `ResourceWarning: subprocess ... is still running` 告警。

---

## 3. SQLite 原地迁移与版本失效机制说明 (`cache.py`)

1. **结构重建安全性 (`catalog_cache`)**:
   - `_init_db` 通过 `PRAGMA table_info` 识别 `catalog_cache`。
   - 若主键不匹配或缺少关键列（如包含旧版 `query_key`, `tracks_json`），在事务中新建 `catalog_cache_new`，通过 SQL `INSERT OR IGNORE INTO catalog_cache_new ... SELECT ... FROM catalog_cache` 进行数据迁移，保留历史缓存，重命名替换旧表。
2. **版本失效与重评机制 (`match_cache`)**:
   - `match_cache` 新增列默认值为 `'2026.09.v1'`。
   - `MatchEvidence` 缺失版本反序列化默认值为 `"2026.09.v1"`。
   - 读取时，若 `cached_rule_ver != MATCH_RULE_VERSION`，且决策为 `auto_accept`，必须重新执行 `TrackScorer.score` 与 `TrackScorer.evaluate_candidates`。
   - 用户确认决策（`user_confirmed`）不受规则版本升级失效影响，保持人工决定的权威性与局部性。
3. **连接生命周期管理**:
   - `PersistentCache` 提供显式的 `close()` 方法清理连接。所有涉及独立数据库实例的测试均采用 `try...finally: cache.close()`，彻底杜绝 Windows 平台上的文件占用与连接泄漏。

---

## 4. 测试验证与执行结果

### 4.1 专用矩阵测试 (45/45 全部通过)
```bash
d:\conda\python.exe -m unittest tests/test_japanese_universal_matrix.py -v
```
**执行结果摘要**:
```text
test_A01_field_definition_and_roundtrip (tests.test_japanese_universal_matrix.TestGroupAContractsAndEvidence.test_A01_field_definition_and_roundtrip) ... ok
test_A02_extra_fields_forbidden (tests.test_japanese_universal_matrix.TestGroupAContractsAndEvidence.test_A02_extra_fields_forbidden) ... ok
test_A03_discovery_chain_structure (tests.test_japanese_universal_matrix.TestGroupAContractsAndEvidence.test_A03_discovery_chain_structure) ... ok
test_A04_diagnostics_sanitization (tests.test_japanese_universal_matrix.TestGroupAContractsAndEvidence.test_A04_diagnostics_sanitization) ... ok
test_A05_track_extended_fields (tests.test_japanese_universal_matrix.TestGroupAContractsAndEvidence.test_A05_track_extended_fields) ... ok
test_A06_version_columns_in_match_cache (tests.test_japanese_universal_matrix.TestGroupAContractsAndEvidence.test_A06_version_columns_in_match_cache) ... ok
test_B01_native_target_hit_early_stop (tests.test_japanese_universal_matrix.TestGroupBQueryPlanningAndAPI.test_B01_native_target_hit_early_stop) ... ok
test_B02_english_artist_japanese_title_dynamic (tests.test_japanese_universal_matrix.TestGroupBQueryPlanningAndAPI.test_B02_english_artist_japanese_title_dynamic) ... ok
test_B03_japanese_artist_romaji_title (tests.test_japanese_universal_matrix.TestGroupBQueryPlanningAndAPI.test_B03_japanese_artist_romaji_title) ... ok
test_B04_pure_kanji_title_and_artist_algorithmic (tests.test_japanese_universal_matrix.TestGroupBQueryPlanningAndAPI.test_B04_pure_kanji_title_and_artist_algorithmic) ... ok
test_B05_localized_search_locale_param (tests.test_japanese_universal_matrix.TestGroupBQueryPlanningAndAPI.test_B05_localized_search_locale_param) ... ok
test_B06_apple_suggestion_token_alignment (tests.test_japanese_universal_matrix.TestGroupBQueryPlanningAndAPI.test_B06_apple_suggestion_token_alignment) ... ok
test_B07_b_client_equivalents_official_json_and_cache_reuse (tests.test_japanese_universal_matrix.TestGroupBQueryPlanningAndAPI.test_B07_b_client_equivalents_official_json_and_cache_reuse) ... ok
test_B07_target_fails_jp_hits_and_maps (tests.test_japanese_universal_matrix.TestGroupBQueryPlanningAndAPI.test_B07_target_fails_jp_hits_and_maps) ... ok
test_B08_equivalent_missing_isrc_fallback (tests.test_japanese_universal_matrix.TestGroupBQueryPlanningAndAPI.test_B08_equivalent_missing_isrc_fallback) ... ok
test_B09_jp_exists_target_unavailable (tests.test_japanese_universal_matrix.TestGroupBQueryPlanningAndAPI.test_B09_jp_exists_target_unavailable) ... ok
test_B10_rate_limited_and_auth_failed_handling (tests.test_japanese_universal_matrix.TestGroupBQueryPlanningAndAPI.test_B10_rate_limited_and_auth_failed_handling) ... ok
test_B11_budget_enforcement (tests.test_japanese_universal_matrix.TestGroupBQueryPlanningAndAPI.test_B11_budget_enforcement) ... ok
test_B12_determinism_across_python_hash_seed (tests.test_japanese_universal_matrix.TestGroupBQueryPlanningAndAPI.test_B12_determinism_across_python_hash_seed) ... ok
test_B13_screenshot_regression_sparkle_ikuta (tests.test_japanese_universal_matrix.TestGroupBQueryPlanningAndAPI.test_B13_screenshot_regression_sparkle_ikuta) ... ok
test_C01_romanizer_derived_decorrelation (tests.test_japanese_universal_matrix.TestGroupCScoringSafety.test_C01_romanizer_derived_decorrelation) ... ok
test_C02_same_title_different_artist (tests.test_japanese_universal_matrix.TestGroupCScoringSafety.test_C02_same_title_different_artist) ... ok
test_C03_same_artist_different_title_review_only (tests.test_japanese_universal_matrix.TestGroupCScoringSafety.test_C03_same_artist_different_title_review_only) ... ok
test_C04_live_remix_instrumental_conflicts (tests.test_japanese_universal_matrix.TestGroupCScoringSafety.test_C04_live_remix_instrumental_conflicts) ... ok
test_C05_isrc_exact_auto_accept (tests.test_japanese_universal_matrix.TestGroupCScoringSafety.test_C05_isrc_exact_auto_accept) ... ok
test_C06_isrc_with_conflict_demoted_to_review (tests.test_japanese_universal_matrix.TestGroupCScoringSafety.test_C06_isrc_with_conflict_demoted_to_review) ... ok
test_C07_apple_equivalent_strong_with_safety_check (tests.test_japanese_universal_matrix.TestGroupCScoringSafety.test_C07_apple_equivalent_strong_with_safety_check) ... ok
test_C08_short_title_safety_guard (tests.test_japanese_universal_matrix.TestGroupCScoringSafety.test_C08_short_title_safety_guard) ... ok
test_C09_suggestion_alone_does_not_elevate (tests.test_japanese_universal_matrix.TestGroupCScoringSafety.test_C09_suggestion_alone_does_not_elevate) ... ok
test_C10_candidate_aggregation_priority (tests.test_japanese_universal_matrix.TestGroupCScoringSafety.test_C10_candidate_aggregation_priority) ... ok
test_D01_locale_isolation_in_catalog_cache (tests.test_japanese_universal_matrix.TestGroupDCacheAndMigration.test_D01_locale_isolation_in_catalog_cache) ... ok
test_D02_equivalence_cache_storefront_isolation (tests.test_japanese_universal_matrix.TestGroupDCacheAndMigration.test_D02_equivalence_cache_storefront_isolation) ... ok
test_D03_old_rule_version_re_evaluated (tests.test_japanese_universal_matrix.TestGroupDCacheAndMigration.test_D03_old_rule_version_re_evaluated) ... ok
test_D04_current_version_cache_hit_without_re_evaluation (tests.test_japanese_universal_matrix.TestGroupDCacheAndMigration.test_D04_current_version_cache_hit_without_re_evaluation) ... ok
test_D05_user_confirmed_preserved_locally (tests.test_japanese_universal_matrix.TestGroupDCacheAndMigration.test_D05_user_confirmed_preserved_locally) ... ok
test_D06_sqlite_forward_migration (tests.test_japanese_universal_matrix.TestGroupDCacheAndMigration.test_D06_sqlite_forward_migration) ... ok
test_D07_negative_cache_ttl (tests.test_japanese_universal_matrix.TestGroupDCacheAndMigration.test_D07_negative_cache_ttl) ... ok
test_D08_legacy_match_cache_migration_and_reevaluation (tests.test_japanese_universal_matrix.TestGroupDCacheAndMigration.test_D08_legacy_match_cache_migration_and_reevaluation) ... ok
test_E01_review_high_score_not_auto_accepted (tests.test_japanese_universal_matrix.TestGroupEUIAndDiagnostics.test_E01_review_high_score_not_auto_accepted) ... ok
test_E02_unavailable_status_semantic (tests.test_japanese_universal_matrix.TestGroupEUIAndDiagnostics.test_E02_unavailable_status_semantic) ... ok
test_E03_search_incomplete_status (tests.test_japanese_universal_matrix.TestGroupEUIAndDiagnostics.test_E03_search_incomplete_status) ... ok
test_E04_diagnostics_sanitization (tests.test_japanese_universal_matrix.TestGroupEUIAndDiagnostics.test_E04_diagnostics_sanitization) ... ok
test_F01_positive_eval_corpus_metrics (tests.test_japanese_universal_matrix.TestGroupFEvaluationCorpusMetrics.test_F01_positive_eval_corpus_metrics) ... ok
test_F02_hard_negative_corpus_zero_auto_accept (tests.test_japanese_universal_matrix.TestGroupFEvaluationCorpusMetrics.test_F02_hard_negative_corpus_zero_auto_accept) ... ok
test_F03_cross_storefront_corpus (tests.test_japanese_universal_matrix.TestGroupFEvaluationCorpusMetrics.test_F03_cross_storefront_corpus) ... ok

----------------------------------------------------------------------
Ran 45 tests in 2.662s

OK
```

### 4.2 代码规范与空白字符检查 (0 违规)
```bash
git diff --check
```
执行返回码 0，无任何行尾多余空白字符（trailing whitespace）或格式冲突。

### 4.3 全仓库回归测试套件 (171/171 全部通过，0 警告)
```bash
d:\conda\python.exe -m unittest discover -s tests -p "test_*.py" -v
```
**执行结果**:
`Ran 171 tests in 62.545s` -> **OK**（覆盖 cross_lingual_matcher, precision_matrix, catalog_resilience, batch_isrc, web_api, library_manager, japanese_universal_matrix 等全部已有测试，零回归，且无 `ResourceWarning` 告警）。

---

## 5. Synthetic Benchmark 算法评测指标

> [!IMPORTANT]
> **Synthetic Benchmark 声明**:
> 本评测集为受控**合成算法评测集（Synthetic Benchmark）**，用于对算法打分模型、版本冲突惩罚、去相关规则与状态机流转进行确定性边界验证。
> 本评测集并非线上真实 Apple Music 曲库的采样录制，本报告不声称、亦不承诺“真实线上曲库达到 100% 召回”。

### 5.1 数据集构成
- **合成正例集 (200 首)**: 覆盖 7 个分桶，每个曲目生成了纯数字 Catalog ID，并构造了由目标曲目与确定性干扰项（翻唱、伴奏、异曲）组成的候选列表。
- **合成困难负例集 (100 首)**: 包含同名异人、同人异曲、版本冲突（Live/伴奏/TV Size/Remix/加速慢速）、角色歌、中文同形字等混淆样本。
- **合成跨区数据集 (30 首)**: 15 首目标区未上架曲目、15 首等价映射曲目。

### 5.2 Synthetic Benchmark 指标表现

| 分桶名称 | 样本数 | 算法召回率 (Recall@20) | 算法 Auto-Accept 率 | 基准目标 | 达标情况 |
| :--- | :---: | :---: | :---: | :---: | :---: |
| 1. 平假名 (hiragana) | 29 | 100.0% (29/29) | 100.0% | >= 90% | **达标** |
| 2. 片假名 (katakana) | 29 | 100.0% (29/29) | 100.0% | >= 90% | **达标** |
| 3. 纯汉字 (pure_kanji) | 29 | 100.0% (29/29) | 100.0% | >= 90% | **达标** |
| 4. 混合书写 (mixed) | 29 | 100.0% (29/29) | 100.0% | >= 90% | **达标** |
| 5. 罗马字/英文艺人名 (latin_artist) | 28 | 100.0% (28/28) | 100.0% | >= 90% | **达标** |
| 6. 括号/副标题/译名 (bracket_trans) | 28 | 100.0% (28/28) | 100.0% | >= 90% | **达标** |
| 7. 本地化标题 (localized) | 28 | 100.0% (28/28) | 100.0% | >= 90% | **达标** |
| **合成正例全集总计 (Total Synthetic Positive)** | **200** | **100.0% (200/200)** | **100.0%** | **Recall >= 95%** | **达标** |
| **合成困难负例 (Synthetic Hard Negatives)** | **100** | - | **0.0% (0/100 误自动采纳)** | **0 误判** | **达标** |
| **合成跨区用例 (Synthetic Cross-Storefront)** | **30** | - | **100% 正确语义 (15 unavailable / 15 mapped)** | **100%** | **达标** |

---

## 6. 边界与防护机制声明

1. **未执行 Live 线上曲库访问**:
   - 本地测试环境未配置 Apple Music 线上开发者令牌与密钥，测试均在离线 Synthetic Benchmark 与模拟网络环境中执行。
2. **安全防线与降级策略**:
   - **短歌名防线**: 极短单字/双字标题（如《心》、《空》、《花》）若缺少艺人高度相似（< 0.85）佐证，强制降级至人工 Review，拒绝自动采纳。
   - **版本冲突防线**: 来源与候选在 Live/Remix/伴奏/Cover/TV Size 等版本标签上出现不对称时，强行扣除 0.40~0.45 分并标记冲突项，阻断自动采纳。
   - **去相关防线**: 标题与艺人均依赖罗马音转写命中时，合并为一个证据族（`romanizer_derived`），无法凑齐双独立强证据，自动降级至 Review。
   - **跨区未上架语义**: 当日区存在曲目但目标区（如 CN 区）无版权且 Equivalents / ISRC 均无法映射时，输出 `unavailable_in_target_storefront`，不伪造匹配。

---

## 7. 交付声明

- **Git 状态**: 未执行 `git commit`、`git push`、`git tag` 或发布操作。
- **数据与安全**: 未向静态别名表硬编码测试歌曲或艺人别名；未泄漏任何用户路径、Token 或 Cookie。
- **返工完成**: 复验报告指出的 2 项阻断问题（旧 match 缓存兼容与重评、评测集定位为 Synthetic Benchmark）及子进程告警已全部解决并通过全量测试，现正式交回 Codex 验收。
