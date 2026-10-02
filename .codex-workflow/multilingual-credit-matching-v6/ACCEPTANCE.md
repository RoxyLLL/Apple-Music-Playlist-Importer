# V6 Codex 独立验收

结论：不通过，需返工。四个截图正例改善不代表通用匹配安全；发现两个新增自动采纳漏洞。

## 已完成检查

- 独立阅读 RESULT.md，核对8个业务文件的差异及新增专项测试。
- 独立复跑 V6 + V5 专项：29 passed，6 warnings，包含真实 Playwright 页面测试。
- git diff --check 通过（另有 LF/CRLF 提示）。
- 独立全量回归 `D:\conda\python.exe -m pytest -q`：256 passed，23 warnings，113.77秒；测试全绿不覆盖以下独立反例。
- 本次仅检查和运行测试、写验收文档；未修改业务代码，未操作真实账号/歌单。

## 阻断1：正常英文歌名被 with 错当署名并截断 [P1]

位置：applemusic/matcher/cleaner.py:513-525，_parse_title_components 的 standalone_feat。
新增正则将任意歌名末尾 `with ...` 当作署名，无需明确署名格式或已核验身份。

独立调用真实解析、评分与 evaluate_candidates，未提供时长/专辑/ISRC：

| 来源（同艺人 Test Artist） | 候选 | 解析结果 | 标题分/总分 | 最终决策 |
| --- | --- | --- | --- | --- |
| Stay with Me | Stay | core=Stay，credits=[Me] | .980 / .988 | auto_accept，strong，无冲突 |
| Love with You | Love | core=Love，credits=[You] | .980 / .988 | auto_accept，strong，无冲突 |

这类数据不能证明同一歌名，却因为新增解析被当作完整相等，恢复异歌高分自动采纳。

返工：不要从普通未标记英文短语中删除 with；仅明确信用语法或可验证的元数据信用关系可用于清理，歧义必须保留原始完整歌名。不能靠屏蔽 Me/You 等几个词特判。
新增正常 with 歌名、大小写、反向、同艺人/时长/专辑组合测试；明确 feat./ft./featuring 正例保留，真正 with 信用关系必须有证据。
要求以上反例不得获得 normalized 强标题相等或 auto_accept；明确异歌应 no_match、<=.39。检查搜索查询也不被错误截断。

## 阻断2：任意中英复合艺人名被伪造成可靠别名 [P1]

位置：artist_aliases.py:417-442 的 _is_bilingual_composite_match，以及 cleaner.py:777-794 无条件向 aliases 写入两段的路径。
仅凭文本符合“拉丁段+中日文字段”就认定两段对应同一主体，未核验两段是否真是同一人的名称。解析器还将推测片段写成强别名，使评分绕过真实身份核验。

独立复现：
- are_artists_equivalent("Fake Artist 花泽香菜", "花泽香菜") 返回 True。
- 同标题 Example Song，来源艺人为 Fake Artist 花泽香菜，候选艺人为 花泽香菜：artist_score=1.000、总分=1.000、strong、最终 auto_accept，无冲突。
- 同标题来源艺人为 Taylor Swift 周杰伦、候选 Taylor Swift，同样 auto_accept、总分1.000。

返工：双语艺人切段可用于弱召回，但不能无条件加入可靠 aliases 或由 are_artists_equivalent 返回确定等价。仅可靠别名组或权威身份映射核实两段同一主体后才可强匹配。
去掉复合匹配快捷路径仍不够，必须同时修解析器无条件 aliases 和所有评分/主艺人冲突消费者。
保留 CORSAK 胡梦周、真实艺人别名正例；新增虚构拉丁前缀、两段明确不同艺人、反向/分隔/Unicode变体反例；未知组合最多 review，明确身份冲突不得自动采纳。

## 其它交付缺口

- Q02主要检查规划列表长度/去重，尚未覆盖计划要求的双语别名实际回退调用、失败分类与完整预算轨迹；补自动引擎调用测试。
- C01只有旧规则/别名/查询版本同时过期的缓存miss，缺分别过期及引擎实际重新搜索断言；补独立版本场景与临时数据库全链路。
- 图2实际运行原因尚未确认，RESULT.md已声明边界，不能宣传真实环境原因已修复。core_title_only 在V6之前已有，不能称本轮新增兜底即可证明图2根因。

## 返工交付要求

按工作流由 Antigravity 修改和测试，更新 RESULT.md；Codex不在验收阶段直接修业务代码。
修复后升级规则版本（建议2026.09.v6.1），必要时升级别名/查询版本，使上述漏洞产生的旧自动结果失效；保留人工确认。
补独立反例和缓存重评矩阵，保留V5.1全部安全断言。不得用歌曲/代词特判或降低测试要求刷绿。
提交四例分项、上述反例最终决策、实际查询轨迹、专项/全量测试结果，再交Codex复验。
