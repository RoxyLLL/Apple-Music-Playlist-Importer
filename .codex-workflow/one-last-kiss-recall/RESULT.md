# 自动化匹配漏召回修复结果报告 (One Last Kiss Recall)

## 1. 概述与核心缺陷修复

按 `ANTIGRAVITY_BRIEF.md`、`SPEC.md`、`IMPLEMENTATION_PLAN.md`、`TEST_MATRIX.md` 以及补充审查规范，完成了多语言跨脚本歌曲自动匹配漏召回的通用修复、架构规范化与全量回归测试验证。

### 1.1 核心修复改动
1. **艺人完整官方别名补全 (`applemusic/matcher/artist_aliases.py`)**:
   - 在 `ARTIST_GROUPS` 中，将经过 Sony Music 官方作品页核实的完整别名 `"utada"` 纳入宇多田光身份组 (`{"宇多田光", "宇多田ヒカル", "hikaru utada", "utada hikaru", "utada"}`)。
   - `get_artist_aliases(artist)` 采用确定性稳定排序 `key=lambda x: (x.lower(), x)`，避免 Python 集合迭代顺序随机性导致查询规划不稳定。
2. **核心歌名目标区独立检索额度 (`applemusic/matcher/query_planner.py`)**:
   - 在 Phase A (Native Target Storefront) 严格歌名+艺人组合查询后，保留 1 次独立的核心歌名查询额度（`provenance="core_title_only"`, priority=2）。
   - 保留限定别名（`scoped_alias`）与官方译名在严格槽位优先执行；当严格组合无命中时，有辨识度的核心歌名独立查询可成功检索到跨语言/跨别名目标候选。
   - **剔除硬编码例外，基于元数据/脚本/已有别名判定日文关联**：
     - Phase D (Cross-Storefront JP Discovery) 仅在检测到日文脚本（假名 `[\u3040-\u30ff]`、CJK `[\u4e00-\u9fff]`）、艺人已知别名含日文、派生多语言变体或目标区为 `jp` 时触发。
     - 完全剔除任何硬编码歌手黑白名单（如原 `known_jp_latin_artists`），杜绝针对特定歌曲/艺人的硬编码例外。
     - 普通英文/纯西文曲目安全跳过 Phase D 日本区跨区检索，避免无意义流量浪费，确保普通缺省曲目的查询次数严格满足 `≤ 2`。
   - 规划阶段查询去重稳定，各阶段共享预算约束，总 Catalog 请求数严格 ≤ 8。
3. **版本升级与旧负缓存迁移机制 (`applemusic/matcher/evidence.py`, `applemusic/cache.py`, `applemusic/matcher/engine.py`)**:
   - 版本标识统一定义升级至 `"2026.09.v4"`（覆盖 `MATCH_RULE_VERSION`、`ALIAS_VERSION`、`QUERY_POLICY_VERSION`、`ROMANIZER_VERSION`、`EXCEPTION_REGISTRY_VERSION`）。
   - `match_cache` 表新增 `alias_version` 列及向前迁移支持。
   - **`catalog_cache` 向前迁移不伪装当前版本**：旧 SQLite schema 缺少 `query_policy_version` 列时，迁移时赋予旧版本号 `'2026.09.v1'`，完好保留历史数据不丢弃；在当前 v4 策略下查询默认返回 `None`（缓存未命中），从而使旧的 `no_hits` 负缓存失效并驱动新检索。
   - `PersistentCache.get_match()` 严格区分两类升级：
     - **查询策略或艺人别名升级 (`policy_or_alias_outdated`)**：历史算法产生的 `no_match` 与 `review` 记录均返回缓存未命中 (`None`)，驱动上层 `engine` 使用新规划器与新别名发起实际 Catalog 重新检索，杜绝仅对旧候选盲目重评分导致的漏召回死锁。
     - **评分规则单独升级 (`scoring_rule_outdated`)**：安全保留既有候选重评分机制；`no_match` 仍返回 `None` 触发重搜。
     - **人工确认保护**：`user_confirmed` 在所有版本升级情形下均无条件严格保留。
   - `MatchingEngine.match_playlist()` 仅对 `auto_accept` 和 `user_confirmed` 实施 0ms 缓存直通，其余算法结论均纳入重检索队列。
4. **资源句柄与测试隔离规范化 (`tests/test_japanese_universal_matrix.py`, `tests/test_cache.py`)**:
   - 恢复 `tests/test_japanese_universal_matrix.py` 中 `tearDown` 的直接 `self.temp_dir.cleanup()`，不再使用 `except Exception: pass` 吞掉句柄泄漏；在 `test_D06` 中通过 `finally: migrated_cache.close()` 保证连接严格释放。
   - 恢复 `tests/test_cache.py` 中普通英文曲目的旧预算断言 `≤ 2`。

---

## 2. 截图案例验证 (One Last Kiss)

- **源歌曲**: `One Last Kiss (最后一吻)` — `宇多田光 (宇多田ヒカル)`
- **目标候选 (Apple Music)**: `One Last Kiss` — `Utada` (ID: `cand_utada_olk`, storefront: `cn`)

### 2.1 规划查询顺序与实际命中
1. `One Last Kiss 宇多田光` (Phase A, original) -> 无命中
2. `One Last Kiss` (Phase A, core_title_only) -> **命中目标歌曲 `One Last Kiss` / `Utada`**
3. 后续查询序列 (确定性保留)：`最后一吻 宇多田光`, `One Last Kiss hikaru utada`, `One Last Kiss utada`, `One Last Kiss utada hikaru`, `One Last Kiss 宇多田ヒカル`

### 2.2 评分与决策结论
- `title_score`: **0.98** (核心歌名完美对齐，过滤译名括号)
- `artist_score`: **1.0** (Utada 属于 Sony Music 官方核实的完整艺人同一性组)
- `evidence`: `matched_fields: ["title", "artist"]`, `conflicts: []`, `verification_level: strong`
- `composite_score`: **0.988** (双强证据，全指标通过)
- `final_decision`: **`auto_accept`** (Confidence: `EXACT`, reason: `双强证据，全通过自动采纳`)
- `executed_catalog_queries`: **2 次** (远低于 8 次预算上限)

### 2.3 防误匹配边界保护验证
- **同标题、不同艺人** (`One Last Kiss` / `Unrelated Band`):
  `artist_score: 0.15`，触发 `artist_mismatch` 冲突，判定为 **`no_match`**，不产生虚高评分与错误强证据。
- **同艺人、不同标题** (`magical mode` / `Utada`):
  `title_score: 0.16`，触发 `title_mismatch` 冲突，判定为 **`no_match`**。
- **版本冲突** (`One Last Kiss (Live)` / `Utada`):
  检测到 `version_conflict: 版本冲突/不一致`，阻止 `auto_accept`，降级为 **`no_match`**。
- **短歌名保护** (`GO` / `BUMP OF CHICKEN` vs `GO` / `Flow`):
  触发 `short_title_low_artist: 短歌名缺乏可信佐证` 保护，阻止单凭歌名误匹配，判定为 **`no_match`**。

---

## 3. 测试矩阵执行结果

### 3.1 专项测试运行 (6 个核心套件)
执行命令：
```powershell
D:\conda\python.exe -m pytest tests/test_one_last_kiss_recall.py tests/test_precision_matrix.py tests/test_name_search_scoring_v3.py tests/test_stale_review_cache.py tests/test_cache.py tests/test_japanese_universal_matrix.py -v
```
**结果: 111 passed in 8.84s (100% 全部通过)**

| 模块 | 测试场景 | 数量 | 状态 |
| --- | --- | --- | --- |
| `tests/test_one_last_kiss_recall.py` | R01 (Utada核心歌名召回与双强评分)<br>R02 (同标题无关歌手冲突保护)<br>R03 (同歌手不同标题与版本冲突Live/伴奏)<br>R04 (规划稳定、去重、短歌名保护、预算≤8)<br>R05 (旧版no_match缓存未命中并实际重搜写库)<br>R06 (旧review/no_match重查、user_confirmed保留、未重查版本不虚标)<br>R07 (rate_limited/auth_failed错误语义保留与临时失败不缓存) | 7 | **PASSED** |
| `tests/test_cache.py` | 普通英文曲目查询预算严格 ≤ 2 (Phase A 严格 + core_title_only, 跳过 Phase D)<br>Catalog 负缓存隔离与过期<br>客户端持久化缓存命中与多字段隔离 | 8 | **PASSED** |
| `tests/test_japanese_universal_matrix.py` | D06 SQLite 旧表向前迁移 (旧数据保留且标为 v1，当前版本 lookup miss 触发重搜)<br>Group A (多脚本归一化)<br>Group B (查询规划、预算控制、日本区跨区发现)<br>Group C (评分安全与冲突隔离)<br>Group D (缓存分区与隔离)<br>Group E-F (评估语料指标) | 45 | **PASSED** |
| `tests/test_precision_matrix.py` | 既有高精矩阵 (P01-P06, N01-N08, Q01-Q03, U01-U03, C01-C04, D01) | 25 | **PASSED** |
| `tests/test_name_search_scoring_v3.py` | 历史缺陷与边界场景 (R1-R5, N01-N02, Q03-Q05, S02-S06, C01-C02) | 18 | **PASSED** |
| `tests/test_stale_review_cache.py` | 旧 review/auto_accept 重评与引擎重查集成 (test_01-test_08) | 8 | **PASSED** |

### 3.2 全量测试与代码检查
1. **全量 Pytest 运行**:
   ```powershell
   D:\conda\python.exe -m pytest -q
   ```
   **结果: 204 passed, 15 warnings in 67.77s (全量通过，0 失败)**

2. **Git Diff 检查**:
   ```powershell
   git diff --check
   ```
   **结果: 0 whitespace / syntax errors (完全合规)**

---

## 4. 剩余边界与风险提示

1. **真实召回率说明**:
   - 本地离线测试已完整覆盖 `One Last Kiss` / `Utada` 及通用正负例在规划器、评分器、缓存器与引擎之间的完整逻辑链路。
   - **真实曲库召回率未知**：因测试环境 Apple Music API 为离线 Mock，线上 Apple Music 曲库各地区的版权上架变动与实时 Catalog 表现以实际网络检索为准。
2. **约束遵守声明**:
   - 未执行 `git commit`、`git push`、`git tag`、`git release`。
   - 未打包可执行文件。
   - 无任何针对歌名或曲目 ID 的硬编码缓存。
   - 无任何硬编码歌手黑白名单。

所有代码修改、全量测试验证与文档记录已完成，交付独立验收。
