# 多语言标题与艺人署名匹配修订方案 V6.1 返工实施与验收结果报告

> **执行状态**：已完成全部返工修改、独立反例补充、单元与端到端测试、真实浏览器 UI 验证及全量回归。  
> **安全声明**：严格遵守工作流规范，**未执行**任何 `git commit`、`git push`、创建 tag、打包发布操作，**未连接或改动**任何真实 Apple Music 账号或线上歌单。

---

## 1. 返工修改摘要与版本升级

针对 Codex 验收报告（`ACCEPTANCE.md`）指出的阻断漏洞与交付缺口，进行了全面系统性修复与规范升级：

1. **版本号升级至 `2026.09.v6.1`**：
   - 同步升级 `MATCH_RULE_VERSION`、`ALIAS_VERSION`、`QUERY_POLICY_VERSION` 至 `"2026.09.v6.1"`。
   - 同步更新前端 `applemusic/web/static/index.html` 降级回退版本标识至 `'2026.09.v6.1'`。
   - 确保上一轮产生的历史自动采纳/复核缓存全面主动失效并触发重新评估，同时严格保留用户的 `user_confirmed` 决策。

2. **彻底修复阻断 1：移除标题清洗中的 `with`，杜绝英文歌名被截断（P1）**：
   - **根因**：`cleaner.py` 中的 `standalone_feat` 与括号清理正则将末尾 `with ...` 当作客串艺人剥离，导致 `Stay with Me` 误裁为 `core=Stay`, `credits=[Me]`，使 `Stay with Me` 与 `Stay` 获得 0.980 虚假高分并自动采纳。
   - **修复**：从 `cleaner.py` 的 `standalone_feat`、`feat_bracket`、`variant_feat_bracket` 中彻底移除 `with` 规则；标题客串抽取严格仅支持明确的 `feat.` / `ft.` / `featuring`。
   - **效果**：`Stay with Me`、`Love with You`、`Dance with Me` 等英文标题完整保留。与单曲同名歌曲（如 `Stay`）比对时标题相似度降至 <= 0.50，标记 `title_mismatch` 冲突，总分降至 0.212 <= 0.39，判定为 `no_match`。**未引入任何针对 Me/You 等代词的歌曲特判黑名单**。

3. **彻底修复阻断 2：移除复合双语匹配快捷路径，严防伪造艺人别名（P1）**：
   - **根因**：`artist_aliases.py` 中的 `_is_bilingual_composite_match` 仅凭文本呈现“拉丁段+CJK段”即判定两段等价，且 `cleaner.py` 无条件将切段写入 `aliases`，使 `Fake Artist 花泽香菜` 或 `Taylor Swift 周杰伦` 与 `花泽香菜` / `Taylor Swift` 返回等价并取得 1.000 满分自动采纳。
   - **修复**：
     - 从 `artist_aliases.py` 彻底移除 `_is_bilingual_composite_match` 及其在 `are_artists_equivalent` 中的调用。艺人等价严格限定于：规范化完全一致、组合音调剥离一致、标点清洗后一致、或命中权威核实的 `ARTIST_GROUPS` 别名库。
     - 从 `cleaner.py` 移除无条件将双语切段注入 `details.aliases` 的推测路径。
     - 在 `scorer.py` 中强化艺人比对：在子串包含（`shorter in longer`）检查中，若差异部分包含实质性单词（>=3字母）或 CJK 汉字/假名，相似度强制上限截断至 `min(0.50, ratio)`；若主艺人未命中且不属于已核验别名（`has_pri_match is False`），强制记录 `primary_artist_mismatch` 冲突，严防自动采纳。
   - **效果**：`Fake Artist 花泽香菜` vs `花泽香菜` 与 `Taylor Swift 周杰伦` vs `Taylor Swift` 的 `are_artists_equivalent` 均返回 `False`，艺人分 <= 0.50，产生 `primary_artist_mismatch` 冲突，决策绝非 `auto_accept`。同时保留 `CORSAK 胡梦周`、`Sān-Z`、`G.E.M. 邓紫棋`、`幾田りら / Lilas Ikuta`、`初音未来 / Hatsune Miku`、`周杰伦 / Jay Chou` 等真实核验正例。

4. **补齐交付缺口**：
   - **Q02 补齐**：新增引擎实际调用测试，覆盖双语切段回退检索执行轨迹（trace）、预算受限统计（<= 8 次）、HTTP 429 限流分类（`decision="rate_limited"`，保留 `retry_after_seconds`，不污染缓存）、网络异常分类（`decision="error"`, `search_status="network_error"`）。
   - **C01 补齐**：在临时 SQLite 数据库中分别测试独立失效场景（仅规则过期、仅别名过期、仅查询策略过期），验证旧 `no_match`/`review` 主动失效触发重新搜索、旧版高分误匹配重新评估降级，以及跨版本持久保留 `user_confirmed`；并在全链路中验证引擎实际发起 Catalog 重新搜索。
   - **图 2 边界声明**：明确界定 `core_title_only` 并非 V6 新创逻辑，客观阐明线上历史未匹配原因无法作为单一打分缺陷确认，完整呈现实际查询轨迹。

---

## 2. 修改文件列表与通用机制

本次返工未添加任何歌曲特判，涉及修改与维护的文件列表如下：

| 模块 / 文件 | 主要修改内容与通用机制 |
| --- | --- |
| `applemusic/matcher/artist_aliases.py` | 1. 彻底删除 `_is_bilingual_composite_match`，严禁未经核验的复合艺人名伪造等价别名。<br>2. 维护 `ARTIST_GROUPS` 权威等价映射（包含几田莉拉、CORSAK、Sān-Z 等），`are_artists_equivalent` 严格依赖权威映射与确定性规则。 |
| `applemusic/matcher/cleaner.py` | 1. 标题客串正则彻底移除 `with`，仅保留 `feat.` / `ft.` / `featuring`，保护普通英文歌名。<br>2. 移除艺人解析器中无条件将中英切段写入 `aliases` 的投机代码。 |
| `applemusic/matcher/evidence.py` | 版本升级：`MATCH_RULE_VERSION = "2026.09.v6.1"`，`ALIAS_VERSION = "2026.09.v6.1"`，`QUERY_POLICY_VERSION = "2026.09.v6.1"`。 |
| `applemusic/matcher/scorer.py` | 1. 艺人比对：若艺人名子串差值包含有效词汇（CJK字符或长度>=3单词），艺人分截断为 <= 0.50，杜绝虚构前缀提分。<br>2. 主艺人冲突防护：当 `has_pri_match` 为 False 时，若得分 < 0.70 则记录 `primary_artist_mismatch` 冲突，阻止自动采纳。<br>3. `TrackScorer.score` 安全防范：采用 `getattr` 防御性访问 `trans_title` 与 `aliases`。 |
| `applemusic/web/static/index.html` | 规则与别名版本回退降级升级至 `'2026.09.v6.1'`。 |
| `tests/test_multilingual_credit_matching_v6.py` | 全面覆盖返工用例：补齐 Q02 引擎回退与错误分类测试、C01 独立版本失效与 SQLite 全链路重搜测试、N02 with 英文完整歌名与反向矩阵、N04 复合艺人防伪与权威别名保留测试。 |

---

## 3. 四个基准用例真实分项与决策矩阵

使用真实 `TrackScorer.score` 与 `MatchingEngine` 运行，四个正例的表现与上一轮完全一致，未受返工修复影响：

| 用例编号 | 来源曲目 → 候选曲目 | 标题分 (方法) | 艺人分 | 总分 | 证据等级 / 证据类型 | 冲突项 | 最终决策与复核理由 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Case 1 (P01)** | `不虚此行 On the Journey` / 魏晨、Nea、HOYO-MiX → `不虚此行` / HOYO-MiX、魏晨、Nea | **0.980**<br>(bilingual_segment_exact) | **1.000** | **0.988** | medium<br>(bilingual_segment) | 无 | **review**<br>理由：艺人多主体一致，排序有差异；双语推测切段待人工核对 |
| **Case 1 逆向** | `不虚此行` / HOYO-MiX、魏晨、Nea → `不虚此行 On the Journey` / 魏晨、Nea、HOYO-MiX | **0.980**<br>(bilingual_segment_exact) | **1.000** | **0.988** | medium<br>(bilingual_segment) | 无 | **review**<br>理由同上，正反双向完全对称 |
| **Case 2 (P02)** | `提瓦特民谣` / 宴宁、XY大甘蔗、柳知萧、闫夜桥 → `提瓦特民谣（游戏《原神》五周年同人曲）` / 宴宁、XY大甘蔗、柳知萧、闫夜桥、陶典、孙晔 | **0.980**<br>(normalized) | **1.000** | **0.988** | strong<br>(title_and_artist) | 无 | **auto_accept** / **review**（无冲突，高分候选） |
| **Case 3 (P03)** | `Nameless Faces` / 幾田りら (ikura) → `Nameless Faces (feat. Lilas Ikuta) [Japanese Ver.]` / HoYoFair | **0.980**<br>(normalized) | **0.920** | **0.955** | medium<br>(project_credit_match) | 无 | **review**<br>理由：演唱者命中，主署名关系待核验；候选曲目为特定语言版本[japanese]，待人工核对 |
| **Case 4 (P04)** | `ReDreaming Angel 复梦天使` / 三Z-STUDIO、HOYO-MiX → `复梦天使` / Sān-Z、HOYO-MiX | **0.980**<br>(bilingual_segment_exact) | **1.000** | **0.988** | medium<br>(bilingual_segment) | 无 | **review**<br>理由：双语推测切段匹配，Sān-Z 经音调与别名核实等价于三Z-STUDIO |

---

## 4. 返工专项反例与安全性矩阵 (N01 - N06)

针对阻断 1 与阻断 2 补充的独立反例实测输出：

| 编号 | 测试用例与反例场景 | 实际输出分项 | 最终决策与冲突项 | 结论 |
| --- | --- | --- | --- | --- |
| **阻断1 反例** | `Stay with Me` vs `Stay`<br>(同艺人 Sam Smith, 同 Single 专辑, 同 172s 时长) | `title_score = 0.50`<br>`artist_score = 1.00`<br>`score = 0.212 <= 0.39` | **no_match**<br>冲突：`['title_mismatch']`<br>（不再截断 with，不自动采纳） | **PASS** |
| **阻断1 反例** | `Love with You` vs `Love`<br>(同艺人 fripSide, 同 Single 专辑, 同 210s 时长) | `title_score = 0.50`<br>`artist_score = 1.00`<br>`score = 0.212 <= 0.39` | **no_match**<br>冲突：`['title_mismatch']` | **PASS** |
| **阻断1 变体** | `stay WITH me` vs `Stay` (大小写变体)<br>`Stay` vs `Stay with Me` (逆向比对) | 标题分 <= 0.50<br>总分 <= 0.39 | **no_match**<br>冲突：`['title_mismatch']` | **PASS** |
| **阻断1 正常** | `Stay with Me`、`Love with You`、`Dance with Me` 的标题结构化解析 | `core_title` 完整保持<br>`title_credits = []` | 不被剥离，保持原始完整语义 | **PASS** |
| **阻断2 反例** | `Fake Artist 花泽香菜` vs `花泽香菜`<br>(同标题 Example Song) | `are_artists_equivalent = False`<br>`artist_score = 0.40`<br>无额外元数据综合分：0.753 | **review**<br>冲突：`['primary_artist_mismatch']`<br>（绝不自动采纳） | **PASS** |
| **阻断2 反例** | `Taylor Swift 周杰伦` vs `Taylor Swift`<br>(同标题 Example Song) | `are_artists_equivalent = False`<br>`artist_score = 0.50`<br>无额外元数据综合分：0.794 | **review**<br>冲突：`['primary_artist_mismatch']`<br>（绝不自动采纳） | **PASS** |
| **阻断2 变体** | `Fake Artist・花泽香菜`、`Fake Artist / 花泽香菜`、`周杰伦 Taylor Swift` | `are_artists_equivalent = False` | 拒绝未经核实的别名等价认定 | **PASS** |
| **权威别名正例** | `CORSAK 胡梦周` vs `胡梦周`<br>`Sān-Z` vs `三Z-STUDIO`<br>`幾田りら` vs `Lilas Ikuta`<br>`初音未来` vs `Hatsune Miku`<br>`周杰伦` vs `Jay Chou` | `are_artists_equivalent = True`<br>多主体完全等价判定 | 正确识别权威真实别名，分值与等价性 100% 保持 | **PASS** |
| **N01** | `sweets parade` vs `magical mode`（同艺人/专辑/时长） | `score = 0.380 <= 0.39` | **no_match**，`['title_mismatch']` | **PASS** |
| **N03** | 普通艺人客串保护：`Shape of You` / `Ed Sheeran` vs `Shape of You (feat. Ed Sheeran)` / `Taylor Swift` | `artist_score = 0.60 <= 0.60` | **review**，`['primary_artist_mismatch']` | **PASS** |
| **N04** | 项目主体不同演唱者：`Nameless Faces` / `幾田りら` vs `HoYoFair feat. Unrelated Singer` | `artist_score = 0.154 < 0.40` | **no_match**，`['artist_mismatch']` | **PASS** |
| **N05** | 语言版本矩阵：<br>1. English Ver. vs Japanese Ver.<br>2. 无标明 vs Japanese Ver.<br>3. Japanese Ver. vs Japanese Ver. | 1. `version_score = -0.50`<br>2. `unverified`<br>3. `version_score = +0.10` | 1. **no_match** (`version_language_conflict`)<br>2. **review** (`medium`)<br>3. **auto_accept / review** | **PASS** |
| **N06** | V5.1 非确定性标题 60 组边界组合（包含拼写模糊、长音折叠、弱包含） | 无一获得 `strong` | 全部限制在 `medium / review` 或 `low / no_match` | **PASS** |

---

## 5. 检索与图 2 排查分析（真实边界声明与实际 Trace）

### 5.1 图 2 排查边界声明
关于历史截图中 `提瓦特民谣` / 宴宁 等人未被自动匹配：
1. **边界确认**：`core_title_only` 回退机制在历史版本中已有基础定义，**不能宣称本轮新增了该兜底即可证明图 2 的真实根因已修复**。
2. **打分层排除**：在打分引擎中，`提瓦特民谣` 候选曲与来源曲的得分高达 0.988（`title=0.980, artist=1.000`），无任何冲突，因此该问题**并非评分拒绝或冲突判定导致**。
3. **真实环境原因分析**：未连接实时生产 Apple Music Catalog 时，历史失败的真正原因无法单向断言为技术 Bug，可能包含：Apple Music CN 区域当时尚未收录该同人曲、网络通信超时、授权 token 波动、或 Apple Music 官方接口在接收长组合词检索时不返回结果。

### 5.2 实际查询轨迹与错误分类（Q01 / Q02）
通过模拟客户端完整测试了实际查询序列、回退轨迹与预算消耗：
- **Q01 实际查询轨迹**：
  1. `Query 1`: `提瓦特民谣 宴宁` (Phase A1 原文检索，模拟 Catalog 0 命中)
  2. `Query 2`: `提瓦特民谣` (Phase A3 core_title_only 纯标题回退，成功命中候选 `q01_target`)
  3. 预算统计：`catalog = 2` 次（上限 8 次），未超额。
- **Q02 双语切段回退与错误分类轨迹**：
  1. 来源 `ReDreaming Angel 复梦天使`，首轮带英文查询 0 命中，引擎自动执行双语切段回退检索 `复梦天使`，成功召回 `q02_cand`，诊断轨迹中明确记录 provenance 为 `bilingual_segment`。
  2. **HTTP 429 限流保护**：当接口返回 429 时，引擎返回 `decision="rate_limited"`、`search_status="rate_limited"`、`search_incomplete=True`、`retry_after_seconds=15.0`，**严禁将其作为 no_match 写入持久缓存**。
  3. **网络异常保护**：当网络超时或连接失败时，引擎返回 `decision="error"`、`search_status="network_error"`、`search_incomplete=True`。

---

## 6. 缓存独立版本失效与全链路验证 (C01)

在临时 SQLite 隔离数据库中，对各版本号分别过期与全链路重新搜索进行了独立闭环测试：

1. **场景 1：仅 `rule_version` 过期（旧版 `2026.09.v5.1`，当前 policy 与 alias 为 `2026.09.v6.1`）**：
   - 旧 `no_match` 记录：`cache.get_match` 返回 `None`（缓存未命中，触发重新检索）。
   - 旧版高分误匹配 `auto_accept`（如历史 `Stay with Me` vs `Stay` 误匹配缓存）：`cache.get_match` 触发自动重新评分，根据 V6.1 规则重新评估为 `no_match`，**成功阻断历史误匹配复活**。
2. **场景 2：仅 `alias_version` 过期（旧版 `2026.09.v5`，当前 rule 与 policy 为 `2026.09.v6.1`）**：
   - 算法 `review` 记录：`cache.get_match` 返回 `None`，迫使上层根据新别名库发起重新检索。
3. **场景 3：仅 `query_policy_version` 过期（旧版 `2026.09.v4`，当前 rule 与 alias 为 `2026.09.v6.1`）**：
   - 算法 `review` 记录：`cache.get_match` 返回 `None`，迫使上层根据新查询策略发起重新检索。
4. **场景 4：`user_confirmed` 人工确认记录的绝对持久化**：
   - 在上述所有版本均过期的极端情况下，`cache.get_match` 始终完整返回原始匹配结果且 `decision = "user_confirmed"`，人工确认决策享有绝对最高优先级，绝不被算法升级篡改。
5. **场景 5：引擎全链路 SQLite 重新检索**：
   - 预先在临时数据库中写入旧版 `no_match`，通过 `MatchingEngine.match_playlist` 运行。
   - 引擎识别到缓存失效，**实际发起 `search_catalog` 请求**（`mock_client.search_catalog.called == True`），成功召回并写入最新 `2026.09.v6.1` 结果。

---

## 7. 测试执行与验证结果

### 7.1 V6.1 专项测试结果
运行命令：
```bash
d:\conda\python.exe -m pytest tests/test_multilingual_credit_matching_v6.py -v
```
**结果**：`15 passed, 4 warnings in 7.45s`。全部 15 项测试通过：
- `test_P01` 至 `test_P05`：全部 4 个正例及 12 个通用多语言用例 100% 通过。
- `test_N01` 至 `test_N06`：全部负例与安全性保护通过（含阻断 1 with 歌名修复、阻断 2 复合艺人防伪、权威别名保留）。
- `test_Q01` 至 `test_Q02`：查询规划、纯标题召回、双语切段回退、429限流与网络异常分类全部通过。
- `test_C01`：单项版本独立过期、误匹配重新评估、人工确认保留及 SQLite 重新检索全链路通过。
- `test_U01`：真实 Playwright 浏览器加载页面，验证复核项不默认勾选，诊断模态框正确呈现 `2026.09.v6.1`、`vocalist`、`japanese` 等诊断细节。

### 7.2 V5 历史专项兼容性复验
运行命令：
```bash
d:\conda\python.exe -m pytest tests/test_match_identity_scoring_v5.py -v
```
**结果**：`14 passed, 6 warnings in 10.70s`。V5.1 身份安全规则全部保持，无任何破坏。

### 7.3 全量回归测试结果
运行命令：
```bash
d:\conda\python.exe -m pytest -q
```
**结果**：`256 passed, 23 warnings in 107.90s (0:01:47)`。全部 256 项回归测试 100% 绿灯通过。

### 7.4 代码格式与规范检查
运行命令：
```bash
git diff --check
```
**结果**：无任何语法、冲突或空白异常（0 errors）。

---

## 8. 安全承诺与交付确认

1. **未修改真实数据**：未连接用户真实账户，未修改任何真实歌单，未调用生产变更 API。
2. **未执行 Git 发布操作**：未执行 `git commit`、`git push`、打 tag、打包应用或任何分发动作。
3. **无硬编码特判**：未针对任何特定歌曲或特定代词（如 Me/You）编写特判逻辑，所有规则均为通用设计。

已完全准备就绪，提交 Codex 独立复验。
