# 多语言标题与艺人署名匹配修订方案 V6

工作流：Codex 提供方案 → Antigravity 编写代码、运行测试、交付 RESULT.md → Codex 独立验收。
本文件仅为实施要求，尚未实现。禁止提交、推送、发布、打包或操作真实账号、歌单。

## 1. 目标

修复双语标题、艺人别名、标题内演唱者署名与自动检索召回，不针对四首歌曲建立缓存特判，不降低全局阈值。
保留 V5.1 异歌拒绝、非确定性标题复核、主艺人与客串保护、版本冲突保护、缓存迁移和人工选择保护。
区分同一作品与同一录音版本；不能把日语版、英语版仅因同名同歌手自动合并。

## 2. 已核实基线

Codex 用截图文字构建内存模型，未提供时长、ISRC、完整专辑及实际搜索诊断，调用 TrackScorer.score：

| 来源 → 候选 | 标题分 | 艺人分 | 总分 | 决策 |
| --- | --- | --- | --- | --- |
| 不虚此行 On the Journey / 魏晨、Nea、HOYO-MiX → 不虚此行 / HOYO-MiX、魏晨、Nea | .348 | 1 | .185 | no_match，title_mismatch |
| 提瓦特民谣 / 宴宁、XY大甘蔗、柳知萧、闫夜桥 → 提瓦特民谣（游戏《原神》五周年同人曲）/ 同艺人并含陶典、孙晔 | .980 | 1 | .988 | review，无冲突 |
| Nameless Faces / 幾田りら (ikura) → Nameless Faces (feat. Lilas Ikuta) [Japanese Ver.] / HoYoFair | .980 | .154 | .192 | no_match，artist_mismatch |
| ReDreaming Angel 复梦天使 / 三Z-STUDIO、HOYO-MiX → 复梦天使 / Sān-Z、HOYO-MiX | .320 | .500 | .118 | no_match，title_mismatch |

原因：cleaner.extract_title_variants 不拆以空格连接的中英双语标题；scorer 只用 artists 字段比较艺人，标题 feat. 被清理后没有进入信用信息；现有 幾田りら/ikura 别名组缺 Lilas Ikuta，未找到 三Z-STUDIO/Sān-Z 别名关系。
图2评分已经可通过为 review，实际截图未匹配原因仍未确认，不能编造为评分问题。
当前版本：MATCH_RULE_VERSION=2026.09.v5.1，ALIAS_VERSION=2026.09.v5，QUERY_POLICY_VERSION=2026.09.v4。

## 3. 统一身份解析

扩展现有 cleaner/evidence，必要时新增内部解析模块；搜索、评分、冲突检查和诊断使用同一解析结果，不重复推断。
保留原始文本与现有对外接口。新增缓存字段必须有默认值，兼容旧 JSON。
标题结果包含 core_title、有限 title_variants、version_tags、language_version、title_credits；变体记录来源和是否推测。
艺人结果区分 primary、collaborators、featured、可靠识别的 publisher_or_project、aliases、unknown_role；不能把未知主体自动归类为发行方。
信用信息、目录关系与别名均记录出处；搜索返回和相似名称不等于身份核验。

## 4. 双语标题

- 通用支持“中文完整段 + 英文完整段”及反向形式，不能含这四首歌曲名称的条件分支。
- 原标题始终保留，每侧最多4个完整连续变体；不按每次脚本切换任意截词。
- 数字、ASCII缩写、英语插词、日语混合文字、短片段不能自动认作译名。
- 明确括号/分隔符译名沿用已有规则；空格双语段是推测，记录 bilingual_segment_exact 等独立方法及命中文本。
- 推测段完整相等可给 title_score >= .95，但不加入确定性标题方法集合；默认 medium/review。
- 相同艺人、相同时长、Single/EP或同专辑不能让推测切段自动采纳；可靠相同 ISRC 或已核验目录等价且无身份/版本冲突才可确认。
- 不允许任意子串变成别名。Love→Love Story、Super Mario→Super Mario Bros 保留现行保护，不能复活明确异歌。
- 歌名分、冲突、证据、排序、界面使用同一比较结果，不能只改总分却留下虚假 title_mismatch。

## 5. 艺人别名与角色

### 5.1 可靠别名

补齐 幾田りら / ikura / Lilas Ikuta，以及核实后的 三Z-STUDIO / Sān-Z。在注释或来源记录附可靠 URL。
不能把 HOYO-MiX、HoYoFair、Sān-Z、旗下歌手合并为一个等价组；主体关系不是同一人别名。
规范化覆盖大小写、全半角、空格、Unicode组合字符与音调字符（ā）；原文保留。艺人索引去音调不能改变日语标题长音判定。
检查现有 `.get(...) or .get(...)` 对 group_id=0 的行为，改用显式 None 判断；覆盖首组、标点形式、对称性测试。

### 5.2 标题信用信息

- 在清理标题前，从明确 feat./ft./featuring 语法提取署名；不能把所有括号或专辑说明视为艺人。
- 信用信息同时传入艺人评分、主艺人冲突、ISRC保护与 evidence，避免一处提高艺人分而另一处仍否决。
- 普通 A 对 B feat. A 仍是客串命中，不能变成 primary 一致，更不能自动采纳。
- 候选主体可靠识别为项目/发行方、标题明确署名已知演唱者时，建立 project_credit_match：不生成虚假 artist_mismatch，但保留角色不确定性，默认 medium/review。
- 主体角色未知时不能因 feat. 自动确认；仅演唱者命中允许 review，说明“演唱者命中，主署名关系待核验”。明确不同主艺人仍保留冲突。
- 标题一致、可靠同一演唱者 credit 命中且无其它冲突时，artist_score >= .90、总分 >= .85；不是概率，也不是自动采纳证明。
- 合作列表重排或增补不能仅因首位改变制造主艺人冲突；使用集合对应与角色/来源完整度解释，但仅交集一个主体不等于全体一致。

## 6. 语言版本

对称解析 Japanese Ver./Japanese Version/日语版 与 English Ver./英语版，不把语言版本当作噪声或 remaster。
双方语言相同不生成虚假冲突；明确不同禁止 auto_accept，保留版本冲突与安全决策，不自动勾选。
一侧未知、另一侧明确语言时，标 language_version_unverified，可展示 review，但不得假定同一语言。
图3来源未标语言必须 review；来源明确 English Ver. 对候选 Japanese Ver.，即使歌手、时长、ISRC吻合，也不能绕过明确版本冲突。
封面、发行项目相同不证明同一语言录音。

## 7. 检索与图2排查

共享解析结果生成：原文核心标题、完整双语段+已知艺人、完整段标题-only、可靠艺人别名查询。去重、限定变体，避免笛卡尔积。
优先原文和高辨识度标题-only回退，再安排双语段和别名；弱转写或大量别名不能占满必要召回预算。
保持现有上限：catalog 8、suggestions 2、JP discovery 2、retry total 6，按实际调用计数，不取消限流保护。
手工搜索有结果不证明自动执行了同样查询，必须记录实际 trace。

图2交付脱敏分析：实际查询文本、storefront/locale、顺序/预算、返回候选ID、是否聚合与评分、停止条件、缓存版本、运行规则版本。
若未访问真实运行环境，明确实际原因仍未确认；用模拟客户端证明仅标题-only返回正确歌曲时自动流程仍能召回并合法选择。
全部候选 review 或拒绝时，不能由虚假“高质量候选”阻断必要回退；同时不要为图2特判或强迫 review 永远搜满预算。
至少用其它带说明后缀及多人署名的歌曲验证同类召回。

## 8. 缓存、聚合与界面

规则、别名、查询策略版本升 V6（例如2026.09.v6）；未改转写实现则保留 ROMANIZER_VERSION。
旧规则结果重评；旧查询/别名造成的 no_match 必须重检索，不能仅重评缺失候选的缓存。
人工确认和自定义别名按原契约保留；禁止升级时清空真实数据库、登录凭证或人工记录。
聚合透传角色、语言和证据来源；不同语言录音不可被新去重逻辑误合并。
诊断展示命中的标题段/来源、署名角色、语言状态、规则版本、召回/评分/缓存分层原因；review不自动勾选。
正确候选 review 不显示“未收录”；授权、限流、网络失败继续单独分类，不缓存为 no_match。

## 9. 建议落点

cleaner.py：统一解析；artist_aliases.py：可靠等价关系；scorer.py：统一角色与标题决策；query_planner.py/engine.py：优先级、召回与trace。
candidate_identity.py：透传和语言去重；models.py/evidence.py：兼容字段与版本；cache.py：旧no_match重检索；web/static/index.html：诊断与review展示。
按实际仓库定位路由，不杜撰文件路径。新增 tests/test_multilingual_credit_matching_v6.py，保留所有旧测试，禁止削弱安全断言刷绿。

## 10. 必须验收矩阵

| 编号 | 用例 | 要求 |
| --- | --- | --- |
| P01 | 图1，方向反转、艺人顺序重排 | title >= .95，无假title_mismatch，score >= .85；仅推测段时review |
| P02 | 图2后缀及署名增补 | title >= .95，无错误冲突，可进入合法匹配结果 |
| P03 | 图3，三种演唱者别名，未知源语言 | credit正确命中，score >= .85，review；保留角色/语言核对理由，无假artist_mismatch |
| P04 | 图4，可靠主体别名 | title >= .95，score >= .85，无假title_mismatch；默认review，不仅凭共享HOYO-MiX确认主体 |
| P05 | 至少12组非截图双语标题/署名重排/增补/别名 | 通用处理，无歌名特判，歧义review不虚假拒绝 |
| N01 | sweets parade→magical mode，同艺人/专辑/时长 | no_match、score <= .39，无默认选中 |
| N02 | Love→Love Story、Mario前缀、混合插词/短段/数字 | 切段不伪造完整相等，不自动采纳 |
| N03 | A→B feat. A，明确主艺人不同 | 客串保护有效，不变成primary相同 |
| N04 | 同HOYO品牌不同歌手，虚构主体，空字段 | 不伪造艺人身份 |
| N05 | 两侧语言不同/一侧未知/两侧相同 | 冲突保护/review/无虚假冲突；ISRC不绕过明确冲突 |
| N06 | V5.1非确定性标题60组合 | 保持medium/review，新字段不能绕过 |
| Q01 | 图2完整查询无结果，仅标题-only返回正例 | 自动召回，排序合法，不超预算 |
| Q02 | 双语段及别名回退、全无结果、限流/网络失败 | 实际调用去重/计数，分类与缓存正确 |
| C01 | 旧规则/查询/别名no_match及人工确认 | 临时SQLite验证重评/重检索、人工记录保留 |
| U01 | 真实浏览器渲染匹配/review/无匹配及诊断 | review不勾选，角色/语言/规则证据可见 |

覆盖方向、大小写、全半角、Unicode组合字符、艺人数组与单字符串分隔形式、credit缺失。
区分模拟端到端与真实目录检索；没有真实环境验证不得声称截图实际运行已修复。

## 11. 交付

Antigravity 在此目录交付 RESULT.md：修改文件与通用逻辑、别名来源URL、四例分项/总分/方法/证据等级/冲突/决策，图2实际原因或未确认边界及模拟trace。
附专项和全量测试命令、结果与警告，真实浏览器验证、临时数据库迁移证明、实际查询预算计数、剩余限制和未操作真实账号说明。
Codex 独立检查代码、运行专项与必要回归、抽查正反例/缓存/检索管线；不以报告声明代替验收。
不要求强行让四例自动采纳；要求正确候选可召回、分数合理、复核理由真实且不恢复异歌高分。
