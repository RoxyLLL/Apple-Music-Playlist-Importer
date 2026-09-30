# 日语罗马字漏判与同艺人异歌虚高分：V5 修订与复验返工执行报告 (RESULT.md)

## 1. 修改文件清单

- `applemusic/matcher/scorer.py`:
  - **标题身份确定性作为强证据前置条件**：将 `is_title_definite` 作为普通元数据佐证（`independent_corroborated` 和 `title_and_artist`）升为 `STRONG` 的强制必要条件。
  - **非确定性标题证据强保护**：对于 `romaji_fuzzy`、`romaji_long_vowel_folded`、`containment`、`variant_containment`、`variant_fuzzy`，将其判定前置于普通元数据佐证分支。即使艺人相同、相同时长或同专辑，证据级别严格封顶在 `VerificationLevel.MEDIUM`，最终决策严格为 `REVIEW`，绝不因时长相符或同专辑伪造强证据自动采纳（`AUTO_ACCEPT`）。
  - **防御 discovery_path 字符串与 artist ID 绕过限制**：
    - 仅凭 `candidate.discovery_path` 字符串或 `artist_ids` 匹配不能绕过标题非确定性限制。
    - 只有经过 Apple 官方跨区核验映射（`is_equivalent_mapped=True` 且原区曲目具有确定性标题对应）或可靠 ISRC（`s_isrc == c_isrc` 且无版本/艺人冲突）才能确认歌曲身份。
  - **包含关系与前缀匹配识别**：优化前缀与子串包含检测（阈值 `ratio >= 0.65`），统一将片段包含归入 `containment` 证据类型与待人工核对说明。
  - **彻底移除** `Strict Album Track Fingerprint Fallback` 中将低歌名分覆写为 `0.60` 的逻辑，杜绝 Single/EP 标签与时长伪造歌曲身份。
  - 严格执行标题冲突与未核验上限：`composite = min(composite, 0.39)`，明确区分 `title_mismatch` 与 `title_unverified`，在 `evaluate_candidates` 中彻底拒绝候选（`selected_candidate = None, decision = "no_match"`）。
- `applemusic/models.py` & `applemusic/matcher/candidate_identity.py`:
  - `AppleMusicTrack` 模型显式支持 `is_equivalent_mapped: bool = False` 字段，并在聚合阶段规范透传，确保仅真实映射曲目具有核验标记。
- `applemusic/matcher/evidence.py`:
  - 评分规则版本升级至 `MATCH_RULE_VERSION = "2026.09.v5.1"`，使上一轮 v5 产生歧义的旧自动采纳缓存失效并强制触发重评；别名库版本保持 `ALIAS_VERSION = "2026.09.v5"`。
- `applemusic/matcher/cleaner.py`:
  - 移除 `get_japanese_romaji_variants` 内硬编码的长音自动平替（避免将 `こうこ` 污染生成为 `koko` 导致误判为 `romaji_exact`），交由 `scorer.py` 判定为 `romaji_long_vowel_folded`。
  - `parse_artist_details` 通用剥离艺人名称周围的书名号与引号（如 `『ユイカ』` -> `ユイカ`），同时保留原始值于别名列表。
- `applemusic/matcher/engine.py`:
  - `_evaluate_and_aggregate` 严格防线：包含 `title_mismatch`、`title_unverified` 或 `title_missing` 冲突的候选绝不因 relaxed 重试或二次搜索被放宽为 `REVIEW`。
  - 修正全部候选均被拒绝时的 `search_status`：置为 `"no_match"`。
- `applemusic/matcher/artist_aliases.py`:
  - 补充 `{"上白石萌音", "mone kamishiraishi", "kamishiraishi mone"}` 与 `{"优里", "優里", "yuuri", "yuri"}` 艺人别名映射。
- `applemusic/web/static/index.html`:
  - 分支修复：当存在候选但全部被拒绝时，副标题显示“未找到可信匹配 (N个候选不符)”，徽章为“候选不符”，操作按钮自适应为“换版本”。
  - 诊断弹窗：展示“歌名比对与变体”卡片，呈现比对方法、命中文本对与详细判定；规则版本回退显示 `2026.09.v5.1`。
- `tests/test_match_identity_scoring_v5.py`:
  - 新增 `test_n05_corroboration_cannot_bypass_non_definite_titles`，完整实现 4 种非确定性比对方法 × 5 种时长偏移 × 3 种专辑状态（共 60 种组合）全覆盖矩阵测试，以及 `discovery_path` / `artist_ids` 绕过阻断测试与 ISRC / 目录等价对比测试。
  - 包含真实 Playwright 浏览器页面测试（U02）。
- `tests/test_cross_lingual_matcher.py`:
  - 更新断言匹配真实标题分与冲突拒绝规则。

---

## 2. 验收矩阵结果

| 编号 | 测试用例 | 验证要点 | 结果 |
| :--- | :--- | :--- | :--- |
| **P01** | `test_p01_screenshot_positive_sukidakara_yuika` | 截图正例 `好きだから。（因为我喜欢你。）` / `『ユイカ』` 对 `Sukidakara` / `Yuika`：`title_score >= 0.95`，`artist_score >= 0.95`，`score >= 0.90`，无 `title_mismatch`，理由准确 | **PASS** |
| **P02** | `test_p02_positive_symmetry_and_variants` | 正例对称性、去括号、标点全半角、空格/大小写变形，分差 <= 0.03 | **PASS** |
| **P03** | `test_p03_twelve_plus_japanese_romaji_pairs` | 15 组日文/罗马字完整对应（涵盖 LiSA、King Gnu、Aimer、yama、Mrs. GREEN APPLE、back number、上白石萌音、椎名林檎、优里等） | **PASS** |
| **P04** | `test_p04_positive_with_reliable_corroboration` | 纯转写依赖去相关（REVIEW，不伪造双强），加入独立可靠佐证（时长/专辑）后准确提升为 `AUTO_ACCEPT` | **PASS** |
| **N01** | `test_n01_sweets_parade_vs_magical_mode_exhaustive` | `sweets parade` 对 `magical mode`（同艺人花泽香菜）：在时长缺失、相同、差1s/2.5s/3s，专辑Single/EP/同名全组合下，均 `no_match`，`score <= 0.39`，无默认选中 | **PASS** |
| **N02** | `test_n02_fifteen_plus_same_artist_different_songs` | 16 组同歌手不同歌名（英文/日文/罗马字/短歌名，涵盖周杰伦、Taylor Swift、米津玄师、YOASOBI 等，保留 sweets paper 旧例）全部拒绝 | **PASS** |
| **N03** | `test_n03_conflict_protections_preserved` | 同名异艺人、客串倒置、Live/Remix/伴奏版本冲突保护全部有效 | **PASS** |
| **N04** | `test_n04_edge_cases_no_fake_equality` | 边界回归：`Love`->`Love Story`（0.224, no_match）、`好きだから`->`Sukidakaro`（romaji_fuzzy, review）、`こうこ`->`Koko`（romaji_long_vowel_folded, review）、`夜桜`->`Night Cherry`（title_unverified, no_match）、纯中文汉字避免伪造跨语言相等、空字段保护 | **PASS** |
| **N05** | `test_n05_corroboration_cannot_bypass_non_definite_titles` | **复验返工核心矩阵**：4 种模糊/非确定性方法（`romaji_fuzzy`、`romaji_long_vowel_folded`、`containment`、`variant_fuzzy`）在 5 种时长（无时长、相同、差1s/2.5s/3s）与 3 种专辑（无专辑、Single、同专辑）共 60 种组合下，全量断言 `decision == 'review'`，证据级别不为 strong，保留具体歧义理由；断言 `discovery_path` 字符串与 `artist_ids` 无法绕过；断言可靠 ISRC 与核验等价映射正常生效 | **PASS** |
| **E01** | `test_e01_candidate_aggregation_and_selection` | 候选聚合与排序：正确候选优于冲突候选；全部冲突时 `selected_candidate = None` | **PASS** |
| **E02** | `test_e02_retry_and_relaxed_cannot_revive_mismatch` | 明确异歌无法通过普通重试、宽松重试、JP映射或重评路径恢复为 `REVIEW` | **PASS** |
| **C01** | `test_c01_stale_cache_migration_and_preservation` | 规则版本与别名库版本升级：旧 v4 算法缓存就地重评为 `no_match`；旧别名库版本失效触发重新检索；本地人工确认（`user_confirmed`）完全保留 | **PASS** |
| **U01** | `test_u01_web_ui_presentation_data_structure` | Web 前端数据契约：正例展示高分与清晰转写复核理由；反例不填入 `selected_candidate`、不勾选，支持换版本 | **PASS** |
| **U02** | `test_u02_real_browser_page_ui_and_diagnostics_modal` | **真实 Playwright 浏览器页面测试**：Chromium 渲染 live DOM，正例展示待复核与 99 分；反例展示“未找到可信匹配 (1个候选不符)”、徽章“候选不符”、按钮“换版本”；0候选展示“未收录”与“搜曲库”；诊断弹窗完整显示比对方法（`romaji_exact` / `title_mismatch`）、`2026.09.v5.1` 与“歌名比对与变体” | **PASS** |

---

## 3. 两例真实分项、总分与决策对比

### 正例 (P01)
- **输入**: `好きだから。（因为我喜欢你。）` / `『ユイカ』` 对 `Sukidakara` / `Yuika`（无专辑，无时长）
- **分项指标**:
  - `title_score`: **0.980**（核心标题 `好きだから。` 成功匹配罗马字变体 `sukidakara`，方法为 `romaji_exact`，标为转写）
  - `artist_score`: **1.000**（艺人通用剥离书名号 `『ユイカ』` -> `ユイカ`，匹配假名罗马字 `yuika`）
  - `album_score`: **0.000**（无专辑信息）
  - `duration_score`: **0.000**（无时长信息）
  - `version_score`: **0.000**
- **总分与置信度**: **0.988**，置信度 **EXACT**
- **最终决策**: **REVIEW**（待复核）
- **决策理由**: `["标题/艺人转写吻合，纯转写依赖待人工核对身份"]`
- **冲突列表**: `[]`（无任何 `title_mismatch` 或其他冲突）

### 反例 (N01)
- **输入**: `sweets parade` / `花泽香菜 (はなざわ かな)` 对 `magical mode` / `花泽香菜`（模拟时长完全一致 240000ms，候选专辑为 `magical mode - Single`）
- **分项指标**:
  - `title_score`: **0.240**（真实歌名比对分，无任何伪造）
  - `artist_score`: **1.000**（艺人一致）
  - `album_score`: **0.176**（Single 标签不作为专辑同名依据）
  - `duration_score`: **0.050**（相同时长辅助分）
  - `version_score`: **0.000**
- **总分与置信度**: **0.161**（严格受限 <= 0.39），置信度 **LOW**
- **最终决策**: **NO_MATCH**（未找到可信匹配，`selected_candidate = None`）
- **决策理由**: `["歌名明显不匹配", "候选曲目歌名明显不符"]`
- **冲突列表**: `["title_mismatch: 歌名明显不匹配"]`

---

## 4. 复验阻断问题实际修复效果对照

使用同艺人（`Test Artist`）、双方时长均为 240000ms、无 ISRC、无目录映射：

| 来源 → 候选 | 比对方法 | 修复前表现 (REACCEPTANCE) | 修复后表现 (V5.1) | 决策理由 |
| :--- | :--- | :--- | :--- | :--- |
| **好きだから → Sukidakaro** | `romaji_fuzzy` | 0.944 / strong / **auto_accept** | 0.944 / **medium** / **review** | `歌名存在拼写或读音差异，待人工核对` |
| **こうこ → Koko** | `romaji_long_vowel_folded` | 0.956 / strong / **auto_accept** | 0.956 / **medium** / **review** | `歌名存在长音或读音折叠差异，待人工核对` |
| **Super Mario → Super Mario Bros** | `containment` | 0.887 / strong / **auto_accept** | 0.887 / **medium** / **review** | `歌名仅前缀或片段包含，待人工核对` |
| **Tokyo Tower → Tokyo Towers** | `variant_fuzzy` | 0.994 / strong / **auto_accept** | 0.994 / **medium** / **review** | `歌名存在拼写或读音差异，待人工核对` |

- **佐证无法绕过**: 无论时长相差 0 秒、1 秒、2.5 秒、3 秒，还是候选为 Single 或同名专辑，上述两两对应均因标题非确定性保护，封顶为 `medium / review`。
- **discovery_path / 艺人 ID 绕过阻断**: 即使候选设置 `discovery_path="jp_equivalents"` 或两端存在相同 `artist_ids`，均无法提升至 `STRONG`，决策依然为 `REVIEW`。
- **规则版本升级**: `MATCH_RULE_VERSION` 升级至 `2026.09.v5.1`，确保上一轮 v5 产生歧义的旧自动采纳缓存条目不会继续命中。

---

## 5. 测试命令与数量统计

1. **V5 专项验收测试**:
   - 命令: `pytest tests/test_match_identity_scoring_v5.py -v`
   - 结果: **14 passed, 0 failed** (包含真实 Playwright 浏览器端到端测试与 N05 60组全组合矩阵测试)
2. **相关专项回归套件**:
   - 命令: `pytest tests/test_match_identity_scoring_v5.py tests/test_cross_lingual_matcher.py tests/test_japanese_universal_matrix.py tests/test_name_search_scoring_v3.py tests/test_one_last_kiss_recall.py tests/test_precision_matrix.py tests/test_stale_review_cache.py tests/test_cache.py -v`
   - 结果: **156 passed, 0 failed**
3. **仓库全量回归套件**:
   - 命令: `pytest -q`
   - 结果: **241 passed, 0 failed** (100% 全量通过)
4. **Git Diff 格式检查**:
   - 命令: `git diff --check`
   - 结果: **通过，代码格式完全规范，无空白或语法警告**

---

## 6. 剩余限制与说明

1. **测试范围与安全性**: 本次修改与测试全部在本地进行，使用内存模拟数据与本地隔离数据库，未连接真实 Apple Music 账号，未修改任何真实用户歌单或执行网络导入。
2. **版本与发布承诺**: 未执行任何 `git commit`、`git push`、打 tag、发布 release 或打包操作。
