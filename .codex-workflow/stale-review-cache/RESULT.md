# 旧规则复核缓存导致错误高分优化实施结果 (stale-review-cache)

## 1. 概述与交付状态

本轮优化依据 `D:\applemusic\.codex-workflow\stale-review-cache` 目录下的 `ANTIGRAVITY_BRIEF.md`、`SPEC.md`、`IMPLEMENTATION_PLAN.md` 和 `TEST_MATRIX.md` 规范执行，针对旧规则留下的算法复核记录（`review` 等）绕过版本校验、将陈旧高分和错误候选带入界面的缺陷，以及 Codex 复验指出的“旧无候选缓存若返回 review 会导致 `engine.py:850` 当作缓存命中跳过检索”的问题，进行了系统性重构与修复：
- **核心验收条件达成**：以截图样例 `sweets paper`（花澤香菜）对 `magical mode`（花泽香菜）为例，旧版留存的 `review / 0.72` 缓存，经当前评分规则读取后自动重评为 `no_match`（单候选复算总分约 `0.138`、`title_score` 约 `0.083`、`artist_score` 为 `1.0`），清空 `selected_candidate` 为 `None`，并同步更新 SQLite 数据库的 `result_json`、`decision`、`status` 与各版本列为当前值；随后读取直接返回 `no_match`，绝不恢复 72 分陈旧候选。
- **无候选/不可重评旧缓存严格返回未命中**：SPEC 与 IMPLEMENTATION_PLAN 明确要求“候选缺失或来源信息不全时让旧缓存失效并走重新搜索”。若返回空候选 review，会被 `engine.py:850` (`cached_match = ...`) 误判为命中从而跳过加入 `to_query_keys`，使曲目永久无法在线检索。当前已严格修正：凡规则过期的缓存，只要缺少候选集（`not cands_to_eval`）或缺少有效源曲目（`not has_valid_source`），均**严格返回 `None` 作为缓存未命中**，驱动引擎将其加入待检索队列触发全新检索。
- **用户确认严格保持**：`user_confirmed` 属于用户人工确认的明确决定，不随算法版本迁移而改写，严格保持原有选择与确认状态。
- **次级索引联动校验**：无论是通过主键 `track_hash` 还是次级索引（ISRC、`source:id`、规范化 `text:` 键）读取，均执行相同的版本校验与重新评估，且重评后同步更新同曲目关联的次级索引缓存。
- **零硬编码**：全流程无针对任何特定歌名或艺人的硬编码，所有判定均基于通用评分器、结构化证据与安全边界。
- **测试验证**：
  - 专项测试 `tests/test_stale_review_cache.py` 共 **8 项测试全部通过**（覆盖 `TEST_MATRIX.md` 全部 6 个场景、通用性测试、以及新增的引擎集成重检验证）。
  - 全量回归测试共 **197 项测试全部通过**（0 失败，0 错误）。
  - `git diff --check` 严格通过（退出码 0）。
- **真实召回率评估状态**：由于当前本地离线环境未连接在线 Apple Music Catalog 进行全量标注测试，**真实召回率未知**（严格遵循规范声明，不以合成集断言线上召回率）。
- **交付约束**：未执行 `git commit`、`git push`、`git tag`、`git release`、上传或发布操作，全部成果交付 Codex 独立复验。

---

## 2. 代码修改清单

| 文件路径 | 变更类型 | 修改说明与设计决策 |
| --- | --- | --- |
| `applemusic/cache.py` | 修改 | 1. 重构 `MatchCache.get_match()`：移除原先仅针对 `auto_accept` 的 `if result.decision == "auto_accept":` 限制，将规则版本（`rule_version`）、查询策略版本（`query_policy_version`）、罗马化版本（`romanizer_version`）及例外版本（`exception_registry_version`）校验统一应用于 **所有算法判定（`auto_accept`、`review`、`no_match` 等）**。<br>2. 保持 `if result.decision == "user_confirmed": return result` 前置短路保护，确保用户人工选择不被覆盖。<br>3. 提取候选集合：智能合并 `result.candidates` 与 `result.selected_candidate`，去重且保留所有有效候选。<br>4. **严格缓存未命中逻辑（修复 Codex 发现的跳过检索漏洞）**：若 `not cands_to_eval` 或缺少有效 `source_track`，均无法安全重评，必须统一返回 `None` 作为缓存未命中，使 `engine.py:850` 的 `cached_match` 为空并将曲目加入 `to_query_keys` 触发在线检索，绝不包装成空候选 review，也不把未重评数据伪标为新版本。<br>5. 重新评分与判定：对具有有效源曲目与候选集的记录，调用 `TrackScorer.score` 与 `TrackScorer.evaluate_candidates` 进行完整重评。若新判定为 `no_match`，将 `selected_candidate` 置为 `None`，`search_status` 置为 `no_match`。<br>6. 数据库安全持久化：将重评结果的 `result_json`、`decision`、`status` 及当前规则版本持久化回 SQLite，并通过参数化 SQL 同步更新关联的次级索引键（ISRC、`source:id`、`text:`），且显式排除 `decision != 'user_confirmed'`。<br>7. 更新方法注释。 |
| `tests/test_japanese_universal_matrix.py` | 修改 | `test_D03_old_rule_version_re_evaluated` 补充真实候选数据（时长差异），使过期 `auto_accept` 真实经历 `TrackScorer` 重评后降级为 `review`，精准匹配测试用例标题和意图，同时避免旧测试受空候选回退逻辑干扰。 |
| `tests/test_stale_review_cache.py` | 新增/修改 | 包含 8 个专项测试：<br>- `test_01_stale_review_screenshot_example`：截图样例重评为 `no_match`、分数 `~0.138`、无 `selected_candidate`、DB 同步新版本且二次读取不复活。<br>- `test_02_stale_auto_accept_demoted`：过期 `auto_accept` 重评降级为 `review`。<br>- `test_03_current_version_review_not_recomputed`：当前版本 `review` 干净命中缓存，不改判不改分。<br>- `test_04_user_confirmed_preserved_even_when_outdated`：过期 `user_confirmed` 严格保持用户选择。<br>- `test_05_outdated_cache_cannot_reevaluate_safely`：**断言修正**：无论是缺失 `source_track` 还是缺失候选，过期缓存均严格断言返回 `None`（缓存未命中），且 DB 版本列不被伪标为新版。<br>- `test_06_secondary_index_re_evaluation_and_propagation`：次级索引（ISRC、source:id、text）读取同样触发重评并同步 DB。<br>- `test_07_no_hardcoding_behavioral_generality`：任意低相似度曲目均按通用评分逻辑重评为 `no_match`。<br>- `test_08_engine_integration_triggers_research_on_missing_candidates`：**新增引擎集成测试**：构造无候选过期缓存，调用 `engine.match_playlist()` 实际断言 `cached_match` 为空，曲目被送入待检索列表，`match_track` 实际被调用触发全新搜索；同时对比验证当前版本有效 `auto_accept` 正确跳过检索。 |

---

## 3. 核心问题修复前后对比

### 场景 1: 截图样例旧版 review 缓存 (`sweets paper` vs `magical mode`)

- **修改前**：
  ```python
  # cache.py:get_match()
  if result.decision == "auto_accept":
      # 仅 auto_accept 检查 versions_outdated 并重评
      ...
  return result  # review 记录直接原样返回！
  ```
  - 表现：数据库中旧版 `review` 记录带着旧候选 `magical mode` 和旧分数 `0.72` 被直接返回。Web 前端按 `selected_candidate.score * 100` 显示为 `72/100 待复核`，产生严重误导。
- **修改后**：
  ```python
  if result.decision == "user_confirmed":
      return result
  if versions_outdated:
      if cands_to_eval and has_valid_source:
          scored = [TrackScorer.score(result.source_track, c.track) for c in cands_to_eval]
          best, conf, dec, reasons, gap = TrackScorer.evaluate_candidates(result.source_track, scored)
          result.candidates = scored
          result.selected_candidate = best if dec in ("auto_accept", "review") else None
          ...
  ```
  - 表现：
    - `title_score = 0.083 < 0.45` 触发 `歌名相似度过低` 惩罚（复合分 `composite *= 0.30`）。
    - 最终得分 `0.138 < 0.55`，`evaluate_candidates` 返回 `dec = "no_match"`, `best = None`, `conf = NOT_FOUND`。
    - `result.selected_candidate` 被清空为 `None`。
    - 数据库行更新为 `decision = 'no_match'`, `status = 'not_found'`, `rule_version = '2026.09.v3'`。
    - 二次读取直接返回 `no_match` 结果，前端显示未匹配，72 分错误候选彻底消除。

### 场景 2: 旧无候选缓存与引擎重新检索联动

- **修改前**：
  - 无候选时回退为 `result.decision = "review"`，返回 `result`。
  - `engine.py:850` 执行 `cached_match = self.persistent_cache.get_match(sf, key_str)`，因 `cached_match` 不为 `None`，进入 `if cached_match:` 分支，直接记录结果，**未将 key 加入 `to_query_keys`**。曲目被跳过在线检索，用户界面呈现 0 个候选的待复核死结。
- **修改后**：
  - 无候选（`not cands_to_eval`）或缺少源曲目（`not has_valid_source`）时，`get_match` 均直接 `return None`。
  - `engine.py:850` 的 `cached_match` 判定为 False，进入 `else: to_query_keys.append(key)` 分支，触发全新检索。

### 场景 3: 人工确认记录的保护 (`user_confirmed`)

- 保持 `if result.decision == "user_confirmed": return result`。
- 即使数据库版本或 evidence 版本落后于 `MATCH_RULE_VERSION`，人工确认的匹配关系和选择候选均原样保持，不会被算法重评覆盖。

### 场景 4: 次级索引（ISRC、source:id、text）一致性

- `find_match()` 内部通过 `get_match()` 解析各候选键。读取任意次级索引均会触发版本判定与重评，并将重评后的 `no_match` 或 `review` 同步写回所有匹配键（`track_hash IN (...) OR cache_key IN (...)`），保证次级索引不会复活陈旧高分。

---

## 4. 测试矩阵对齐与执行结果

依据 `TEST_MATRIX.md` 矩阵用例逐项验证：

| 矩阵场景 | 测试用例 | 验证结果 |
|---|---|---|
| 旧版错误 review（截图样例） | `TestStaleReviewCache.test_01_stale_review_screenshot_example` | **PASSED**：重评为 `no_match`，分值 `0.138`，`title_score 0.083`，无 `selected_candidate`，DB 字段同步 `no_match` 与 `2026.09.v3`，二次读取稳定。 |
| 旧版 auto_accept | `TestStaleReviewCache.test_02_stale_auto_accept_demoted` | **PASSED**：旧版自动采纳被成功降级为 `review`，不保留旧 `auto_accept`。 |
| 当前版 review | `TestStaleReviewCache.test_03_current_version_review_not_recomputed` | **PASSED**：版本一致时直接命中缓存，保留原分数与决策，不触发重算或版本升级标注。 |
| 人工确认 | `TestStaleReviewCache.test_04_user_confirmed_preserved_even_when_outdated` | **PASSED**：过期人工确认记录完整保持 `user_confirmed` 决策与选定候选。 |
| 旧缓存不可重评 | `TestStaleReviewCache.test_05_outdated_cache_cannot_reevaluate_safely` | **PASSED**：缺失源曲目或缺失候选**均严格返回 `None`（缓存未命中）**，且不污染版本列。 |
| 次级索引命中 | `TestStaleReviewCache.test_06_secondary_index_re_evaluation_and_propagation` | **PASSED**：通过 ISRC/text key 读取同样重评为 `no_match`，`find_match` 不返回降级记录，DB 次级键全部同步。 |
| 通用性保证 | `TestStaleReviewCache.test_07_no_hardcoding_behavioral_generality` | **PASSED**：任意不同名歌曲重评为 `no_match`，证明无名称硬编码。 |
| 引擎集成验证 | `TestStaleReviewCache.test_08_engine_integration_triggers_research_on_missing_candidates` | **PASSED**：无候选旧缓存命中后，引擎正确走未命中分支，实际调用检索（`mock_match_track.assert_called_once()`），对比有效缓存跳过检索。 |

---

## 5. 实际测试命令与输出

### 5.1 专项回归测试 (8/8 PASSED)

```bash
D:\conda\python.exe -m pytest tests/test_stale_review_cache.py -v
```

输出：
```text
============================= test session starts =============================
platform win32 -- Python 3.13.12, pytest-9.1.1, pluggy-1.5.0 -- D:\conda\python.exe
cachedir: .pytest_cache
rootdir: D:\applemusic
plugins: anyio-4.10.0
collecting ... collected 8 items

tests/test_stale_review_cache.py::TestStaleReviewCache::test_01_stale_review_screenshot_example PASSED [ 12%]
tests/test_stale_review_cache.py::TestStaleReviewCache::test_02_stale_auto_accept_demoted PASSED [ 25%]
tests/test_stale_review_cache.py::TestStaleReviewCache::test_03_current_version_review_not_recomputed PASSED [ 37%]
tests/test_stale_review_cache.py::TestStaleReviewCache::test_04_user_confirmed_preserved_even_when_outdated PASSED [ 50%]
tests/test_stale_review_cache.py::TestStaleReviewCache::test_05_outdated_cache_cannot_reevaluate_safely PASSED [ 62%]
tests/test_stale_review_cache.py::TestStaleReviewCache::test_06_secondary_index_re_evaluation_and_propagation PASSED [ 75%]
tests/test_stale_review_cache.py::TestStaleReviewCache::test_07_no_hardcoding_behavioral_generality PASSED [ 87%]
tests/test_stale_review_cache.py::TestStaleReviewCache::test_08_engine_integration_triggers_research_on_missing_candidates PASSED [100%]

======================== 8 passed, 4 warnings in 0.49s ========================
```

### 5.2 全量回归测试 (197/197 PASSED)

```bash
D:\conda\python.exe -m pytest
```

输出：
```text
============================= test session starts =============================
platform win32 -- Python 3.13.12, pytest-9.1.1, pluggy-1.5.0
rootdir: D:\applemusic
plugins: anyio-4.10.0
collected 197 items

tests\test_audit_regressions.py ......                                   [  3%]
tests\test_batch_isrc.py ....                                            [  5%]
tests\test_cache.py ........                                             [  9%]
tests\test_catalog_resilience.py .....................                   [ 19%]
tests\test_cross_lingual_matcher.py ...............................      [ 35%]
tests\test_japanese_universal_matrix.py ................................ [ 51%]
.............                                                            [ 58%]
tests\test_library_manager.py ..........................                 [ 71%]
tests\test_name_search_scoring_v3.py ..................                  [ 80%]
tests\test_precision_matrix.py .........................                 [ 93%]
tests\test_stale_review_cache.py ........                                [ 97%]
tests\test_web_api.py .....                                              [100%]

================= 197 passed, 15 warnings in 65.76s (0:01:05) =================
```

### 5.3 Git 格式与空白检查 (`git diff --check`)

```bash
git diff --check
```

输出：
```text
(退出码 0，无任何 whitespace 或格式异常)
```

---

## 6. 结论与移交 Codex 复验

所有分支逻辑与边界已严格按 SPEC 与复核反馈修正，通过完整自动化回归测试与引擎集成验证，无硬编码，未执行 git commit/push/release。现交付 Codex 独立复验。
