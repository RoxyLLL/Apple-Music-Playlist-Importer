# Antigravity 实施结果报告

- **实施任务**: 多语言匹配精度、可追溯诊断与缓存版本治理（Multilingual Match Precision & Traceability）
- **基线版本**: v2.0.4 (`42d60c9`)
- **实施状态**: 已完成全部代码开发、测试矩阵验证、既有回归测试，等待 Codex 验收。

---

## 1. 修改与新增文件清单

| 文件路径 | 操作 | 核心改动说明 |
|---|---|---|
| `applemusic/matcher/evidence.py` | **NEW** | 定义 `VerificationLevel`, `MatchEvidence`, `SingleTrackDiagnostics`，定义版本常量 `MATCH_RULE_VERSION = "2026.09.v1"`, `ALIAS_VERSION = "2026.09.v1"`。提供脱敏字典输出 `to_sanitized_dict()`。 |
| `applemusic/matcher/query_planner.py` | **NEW** | 定义 `QueryPlanner` 与 `PlannedQuery`。负责首轮（预算2）与深度重试（全局预算6/每店预算3）的确定性查询编排与 provenance 溯源。 |
| `tests/test_precision_matrix.py` | **NEW** | 覆盖 `TEST_MATRIX.md` 规范要求的全部 25 个精度测试用例（N01-N08, P01-P06, Q01-Q03, C01-C04, U01-U03, D01）。 |
| `applemusic/models.py` | **MODIFY** | `MatchCandidate` 新增 `evidence: Optional[MatchEvidence]`；`SongMatchResult` 新增 `evidence: Optional[MatchEvidence]` 与 `diagnostics: Optional[SingleTrackDiagnostics]`。各数字/枚举字段均附带安全默认值。 |
| `applemusic/matcher/title_aliases.py` | **MODIFY** | 新增 `ARTIST_SCOPED_TITLE_ALIASES` 作用域映射及 `get_scoped_title_aliases`；`are_titles_equivalent` 接入艺人作用域检查并保持大小写不敏感匹配。 |
| `applemusic/matcher/artist_aliases.py` | **MODIFY** | 新增经过官方唱片厂牌核实的拉丁别名映射（十明 <-> toaka）。 |
| `applemusic/matcher/cleaner.py` | **MODIFY** | `get_japanese_romaji_variants` 增加纯汉字守卫（无假名且无内置词组时不执行 pykakasi / 多音字展开）；倒序罗马音仅在 `is_artist=True` 时生效；确定性顺序输出。 |
| `applemusic/matcher/scorer.py` | **MODIFY** | 移除 Single/时长直接重写 `title_score` 的后门；强化艺人短名子串防卫；`evaluate_candidates` 严格要求双独立强证据、无冲突、且各维度指标达标才允许 `AUTO_ACCEPT`；ISRC 冲突降级为 `REVIEW`；跨语言拉丁与罗马音双向对比修复。 |
| `applemusic/matcher/engine.py` | **MODIFY** | 接入 `QueryPlanner`，执行首轮 2 次预算与深度重试 6 次预算；跨店与去重键标准化为 `(storefront, track_id)`；个人资料库匹配打上 `REVIEW` 标签；构建并注入 `SingleTrackDiagnostics`。 |
| `applemusic/cache.py` | **MODIFY** | `get_match` 读时检测 `rule_version` / `alias_version`，旧版 `auto_accept` 自动重评分；`find_match` 文本索引包含版本标记，并对命中候选重算评分；`user_confirmed` 严格保持源曲目限定。 |
| `applemusic/matcher/__init__.py` | **MODIFY** | 使用 `__getattr__` 懒加载 `TrackScorer`, `MatchingEngine`, `TextCleaner`，彻底消除模块循环引用。 |
| `applemusic/web/static/index.html` | **MODIFY** | 徽标完全由 `decision` 驱动（"匹配分 X/100 自动匹配" / "匹配分 X/100 待复核"），消除 `status === 'high'` 带来的假高可信覆盖；默认复选框仅对 `auto_accept` 或 `user_confirmed` 勾选；重试不强制选中 review 项；新增单曲诊断弹窗与脱敏 JSON 导出。 |

---

## 2. 截图复现情况与限制说明

### 2.1 复现分析与诊断边界
用户反馈的截图表现为：
- 源曲目：「灰かぶり（灰姑娘）— 十明」
- 匹配结果：「没有回頭路 (feat. 庭竹) — 李杰明」
- 徽标显示：89% 高可信，且默认处于勾选状态

经源码审查与单步推导，造成该误匹配的复合根因如下：
1. **纯汉字日文假名展开误伤**: 原 `get_japanese_romaji_variants` 中包含了 `\u4e00-\u9fa5`，导致纯中文汉字「十明」被作为日文送入 pykakasi / 多音展开，生成各种罗马音拼写，与包含「明」字的「李杰明」在子串与短名相似度计算中产生了不可靠的交集。
2. **两词颠倒污染歌名**: 原代码对所有由空格分隔的两词无差别执行 `parts[1] parts[0]` 颠倒，原本针对艺人姓名（如 "Akane Fujita" <-> "Fujita Akane"）的规则污染了歌名。
3. **Single/EP 与时长重写标题分的后门**: 原 `scorer.py` 中存在一条规则：若候选曲目为 Single/EP 且时长误差 <= 2.5s，代码直接执行 `title_score = 0.85`，使歌名毫无关联的单曲获得了虚假的高标题分。
4. **查询预算与第二槽位缺失**: 原 `engine.py` 内部查询构建缺乏预算和优先级管理，提取的有效翻译未能在前两个查询中真实发出。
5. **缓存无版本守卫**: 旧缓存记录未记录规则版本，一旦被错误写入 `auto_accept`，系统在后续运行中会直接命中旧缓存并信任。
6. **前端徽标覆盖**: 前端 `index.html` 存在 `status === 'high'` 时无视 `decision: "review"` 直接渲染为「89% 高可信」并默认勾选。

### 2.2 限制与真实性原则
- 离线测试环境无真实 Apple Music API Token，采用符合 Apple Music 官方协议格式的 `CatalogSearchOutcome` / `AppleMusicTrack` 模拟数据。
- 绝不针对「十明」与「李杰明」设置私有硬编码黑名单，而是通过架构层面的独立双强证据链、短艺名防卫、纯汉字假名守卫以及 Single/EP 后门消除来根除同类误匹配。

---

## 3. 规则和别名来源

### 3.1 艺人作用域限定歌曲别名 (Scoped Title Aliases)
- **来源**: Universal Music Japan 官方发行资料 (Universal Music LLC)
- **官方产品链接**: [https://www.universal-music.co.jp/toaka/products/uu1as-01729/](https://www.universal-music.co.jp/toaka/products/uu1as-01729/)
- **映射规则**:
  - `("十明", "灰かぶり") -> ["Cinder ella", "cinderella", "灰姑娘"]`
  - `("toaka", "灰かぶり") -> ["Cinder ella", "cinderella", "灰姑娘"]`
  - `("十明", "cinder ella") -> ["灰かぶり", "灰姑娘"]`
  - `("十明", "灰姑娘") -> ["灰かぶり", "Cinder ella", "cinderella"]`
- **安全保障**: 仅当艺人匹配「十明」或「toaka」时，该作品级别名才参与等价判定和查询生成；其他艺人的 Cinderella 绝不会等同于「灰かぶり」。

### 3.2 经核实的拉丁艺人别名 (Verified Latin Artist Alias)
- **来源**: Universal Music Japan 官方唱片与国际数字发行署名
- **映射规则**:
  - `十明 <-> toaka`
- **标注说明**: 测试矩阵中所有涉及的合成测试曲目（如 Synthetic ISRC、测试用时长的 Live 冲突等）均明确标注为 `synthetic`，不作为真实别名事实写入持久化代码。

---

## 4. 前后测试矩阵验证结果

### 4.1 精度与可追溯性专项矩阵 (`tests/test_precision_matrix.py`)
执行 25 项针对性测试，全部通过 (25/25):

| 编号 | 测试用例名称 | 核心验证点 | 预期结果 | 实际执行结果 |
|---|---|---|---|---|
| **N01** | `test_N01_screenshot_case_no_match` | 灰かぶり（灰姑娘）— 十明 vs 没有回頭路 — 李杰明 | 判定 `no_match`，默认未选中，无高可信伪装 | **PASS** |
| **N02** | `test_N02_synthetic_same_duration_and_single_tag` | N01 增加相同时长与 Single 标签 (synthetic) | `title_score` 不被改写为 0.85，拒绝自动采纳，判定 `no_match` | **PASS** |
| **N03** | `test_N03_short_artist_name_substring_not_verified` | 十明 vs 李杰明；短艺人名仅共享字符 | 相似度 < 0.45，不判定为同一已核实艺人 | **PASS** |
| **N04** | `test_N04_correct_artist_unrelated_title_same_album_duration` | 正确艺人但歌名无关，同专辑且同相近时长 | 判定为 `review` 或 `no_match`，不自动采纳 | **PASS** |
| **N05** | `test_N05_same_title_different_artist_and_missing_artist` | 同名不同艺人；未知艺人；缺少艺人 | 证实冲突拒绝自动采纳，未知艺人不伪装吻合 | **PASS** |
| **N06** | `test_N06_version_tag_conflict_blocks_auto_accept` | 原版 / Live / 伴奏 / 角色歌冲突 | 版本/演出冲突阻断自动采纳，降级至 `review` | **PASS** |
| **N07** | `test_N07_pure_hanzi_japanese_onyomi_no_strong_evidence` | 中日同形纯汉字字面 | 无假名时不展开 pykakasi 多音，不形成强证据 | **PASS** |
| **N08** | `test_N08_isrc_match_with_artist_or_version_conflict_demoted_to_review` | ISRC 相同但明确艺人/版本冲突 (synthetic) | 记录冲突项并降级为 `review` (0.65)，不直接判定 100% | **PASS** |
| **P01** | `test_P01_identical_title_and_artist_auto_accept` | 灰かぶり—十明 vs 同原文/艺人 | 双强证据完整通过，`auto_accept` | **PASS** |
| **P02** | `test_P02_scoped_alias_cinderella_toaka` | 灰かぶり—十明 vs Cinder ella—十明 | 官方作用域别名命中，正确召回并自动采纳 | **PASS** |
| **P03** | `test_P03_verified_latin_artist_alias_toaka` | 十明已核实别名 toaka | 强证据支持匹配，未核实别名拒绝冒充 | **PASS** |
| **P04** | `test_P04_existing_cross_lingual_positive_cases` | YOASOBI / 鹿乃 / Reol 等既有跨语言用例 | 正常召回与核验 | **PASS** |
| **P05** | `test_P05_traditional_simplified_and_punctuation_clean` | 繁简、大小写、标点清洗 | 确定性等价，不产生无谓分歧 | **PASS** |
| **P06** | `test_P06_featured_artist_and_voice_actor` | 包含角色名、声优、feat 标注 | 正确解析角色与声优对应关系，通过强证据核验 | **PASS** |
| **Q01** | `test_Q01_first_round_second_slot_scoped_alias` | 首轮原标题无结果，官方译名命中 | 查询预算内真实执行第二查询（`Cinder ella 十明`）并成功召回 | **PASS** |
| **Q02** | `test_Q02_rematch_continues_budget_on_review` | 初轮 review，深度重试在后置查询中命中 | 剩余预算继续召回，不被前置模糊候选提前阻断 | **PASS** |
| **Q03** | `test_Q03_budget_limits_and_failure_stops_expansion` | 遭遇 429 / 401 失败 | 全局预算不被突破，遇到鉴权/限流停止扩张，真实记录错误 | **PASS** |
| **C01** | `test_C01_outdated_cache_re_evaluated` | 旧版高分匹配缓存读取 | 识别旧规则版本并重新评分，误配项自动降级 | **PASS** |
| **C02** | `test_C02_text_index_does_not_mix_live_and_studio` | 原版与 Live 混杂 | 文本索引包含版本标记，防止跨版本串扰 | **PASS** |
| **C03** | `test_C03_valid_cache_reused_without_clearing_db` | 当前规则版本下的合法缓存 | 缓存有效复用，无需清空数据库 | **PASS** |
| **C04** | `test_C04_user_confirmed_not_shared_across_different_sources` | 人工确认跨源/跨账号 | 严格限定作用范围，不全局滥用 | **PASS** |
| **U01** | `test_U01_review_high_score_badge_and_checkbox` | `decision=review, score=0.89` | 渲染为「待复核」，默认不勾选 | **PASS** |
| **U02** | `test_U02_missing_decision_and_exact_less_than_1` | 缺少 decision 或分数 < 1.0 | 不得伪装为 100% 绝对精确 | **PASS** |
| **U03** | `test_U03_unified_decision_policy` | 前端全局选中决策策略统一 | 仅 `auto_accept` 或 `user_confirmed` 默认勾选 | **PASS** |
| **D01** | `test_D01_diagnostics_sanitization_and_export` | 单曲诊断脱敏与导出 | 包含规则版本、证据链、查询序列；无 Token/Cookie/绝对用户路径 | **PASS** |

### 4.2 全量既有测试套件回归验证
运行命令: `d:\conda\python.exe -m unittest discover -s tests -p "test_*.py" -v`
- **测试用例总数**: 126 项（原 101 项 + 新增 25 项）
- **测试通过数**: 126 项
- **失败/错误数**: 0
- **执行总耗时**: 56.84s
- **执行结果**: `OK`

---

## 5. 查询预算与限流策略

1. **首轮匹配查询预算 (First Round Budget)**:
   - 硬上限: **2 次** 查询。
   - 槽位 1: `core_title + primary_artist`（基础精准查询）。
   - 槽位 2: 优先选取 `scoped_alias + primary_artist`（官方作用域译名）或 `trans_title + primary_artist`（源平台翻译标题）。若无译名，则使用括号副标题或已核实艺人别名查询。
   - 提前终止: 槽位 1 命中且评估结果达到 `auto_accept`（独立双强证据通过），立即终止，不再消耗槽位 2。

2. **深度重试查询预算 (Deep Rematch Budget)**:
   - 全局硬上限: **6 次** 查询。
   - 单 Storefront 硬上限: **3 次** 查询。
   - 候选去重: 按 `(storefront, track_id)` 唯一键去重合并，防止重复打分。
   - 提前终止: 遇到完全符合条件的 `auto_accept` 候选时终止；仅有 `review` 候选时继续执行剩余预算，确保不遗漏后续更高质量的精确候选。

3. **异常与限流安全策略**:
   - 当收到 HTTP 401（`auth_failed`）时，立即中止后续查询，并记录 `search_incomplete=True`。
   - 当收到 HTTP 429（`rate_limited`）时，提取 `Retry-After`，终止当前曲目后续查询，并向上层报告重试间隔，不假装为 `no_match`。

---

## 6. 缓存迁移与人工确认策略

1. **缓存版本标识**:
   - `MATCH_RULE_VERSION = "2026.09.v1"`
   - `ALIAS_VERSION = "2026.09.v1"`
2. **读时自动再评估 (Read-Time Invalidation & Re-evaluation)**:
   - 在 `PersistentCache.get_match` 读取缓存时，检测记录中的 `rule_version` 与 `alias_version`。
   - 若缓存状态为 `auto_accept` 但规则版本过期或缺失：
     - 系统自动提取缓存中的候选曲目，使用当前最新 `TrackScorer` 与双强证据链重新进行评分和决策评估。
     - 若新规则评估未达到 `auto_accept`，自动降级为 `review` 并附带版本升级再评估理由，防止旧版误匹配污染当前运行。
   - **绝不删除用户现有数据库文件**，保持缓存数据库的持久化完整性。
3. **人工确认保留与边界限定**:
   - 标记为 `decision: "user_confirmed"` 的记录在读时予以保留，体现对用户显式选择的尊重。
   - 限制 `user_confirmed` 的复用范围仅限于同一源曲目指纹（`title:artist:album:duration`），不向全局不确定曲目扩散。

---

## 7. 前端行为证据与交互保护

1. **徽标渲染与文案规范**:
   - `applemusic/web/static/index.html` 中彻底废弃仅依据 `status === 'high'` 展示「89% 高可信」的逻辑。
   - 徽标文字与颜色完全由 `decision` 与实际分数决定：
     - `decision === 'auto_accept'`: 绿色徽标，文案 `匹配分 ${Math.round(score * 100)}/100 · 自动匹配`
     - `decision === 'review'`: 黄色徽标，文案 `匹配分 ${Math.round(score * 100)}/100 · 待复核`
     - `decision === 'user_confirmed'`: 蓝色徽标，文案 `匹配分 ${Math.round(score * 100)}/100 · 人工确认`
     - `decision === 'no_match'`: 灰色徽标，文案 `未找到`
2. **默认勾选策略统一**:
   - 首轮渲染与批量/单曲重试后，**仅** `auto_accept` 和 `user_confirmed` 默认勾选 `selected = true`。
   - 任何 `review` 项不论分数多高（即使达到 0.89），均保持 `selected = false`，杜绝用户在不知情下将待复核歌曲误导入。
3. **单曲诊断与脱敏导出**:
   - 单曲操作区增加「诊断」按钮，点击弹出诊断详情模态框，直观展示证据链类型、冲突项列表、尝试的查询序列及各候选详细分。
   - 提供「导出诊断 (JSON)」功能，调用 `exportSingleDiagnosticsJson(index)` 导出标准脱敏 JSON 文件。
   - 脱敏保证: 导出的 JSON 仅包含算法逻辑与元数据诊断，绝对不包含开发者 Token、Music User Token、Cookie、Authorization 标头或绝对用户目录。

---

## 8. 独立复现与执行命令

```bash
# 1. 语法与编译检查
d:\conda\python.exe -m compileall -q applemusic run.py exe_entry.py build_exe.py tests

# 2. 精度专项矩阵测试 (25 项)
d:\conda\python.exe -m unittest tests/test_precision_matrix.py -v

# 3. 跨语言与缓存专项回归测试 (39 项)
d:\conda\python.exe -m unittest tests/test_cross_lingual_matcher.py tests/test_cache.py -v

# 4. 全量测试套件执行 (126 项)
d:\conda\python.exe -m unittest discover -s tests -p "test_*.py" -v

# 5. 代码格式与空白检查
git diff --check
```

---

## 9. 遗留风险与未验证项目

1. **真实 Apple Music 在线网络与区域差异**:
   - 本地测试均使用标准 mock 数据集验证；在实际用户网络环境中，不同区域（如 `us`, `jp`, `hk` 等 Storefront）的实际网络延迟、Apple Music 搜索索引延迟可能会影响多 Storefront 深度重试的实际召回耗时。
2. **小众冷门曲目的多语言召回率**:
   - 对于完全未在官方曲库、维基百科或主流平台建立官方英文/罗马音译名的极小众日漫/同人曲目，在缺乏 ISRC 或准确专辑时长佐证时，系统将安全判定为 `review` 或 `no_match`。绝不为了提高召回率而降低强证据门槛。
3. **曲库精度承诺**:
   - 严禁向用户宣称「100% 真实曲库自动精准匹配」，客观向用户说明基于独立双强证据链的保守设计原则。
