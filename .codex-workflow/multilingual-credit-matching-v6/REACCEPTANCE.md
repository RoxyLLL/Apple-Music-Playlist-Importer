# V6.1 Codex 独立复验

日期：2026-10-02。结论：通过本轮返工验收，无新增阻断项。不是生产账号或实际线上目录验收。

## 独立验证

- 代码核对：标题客串解析移除普通 with；复合艺人快捷等价和无条件切段 aliases 已移除；规则、别名、查询策略均为2026.09.v6.1。
- `D:\conda\python.exe -m pytest tests/test_multilingual_credit_matching_v6.py tests/test_match_identity_scoring_v5.py -q`：29 passed，6 warnings，15.35秒，包含真实 Playwright 页面测试。
- `D:\conda\python.exe -m pytest -q`：256 passed，23 warnings，106.14秒。
- `git diff --check`通过，仅另有LF/CRLF提示；警告为现有Pydantic/WebSocket弃用提示。
- 本次仅运行诊断/测试及写本文档，没有修改业务代码、发布、访问真实账号或改动真实歌单。

## 四例独立内存重测

无时长、ISRC或专辑佐证，调用真实score及evaluate_candidates：

| 对应截图 | 标题分 | 艺人分 | 总分 | 最终决策 |
| --- | --- | --- | --- | --- |
| 不虚此行双语标题、艺人重排 | .980 | 1.000 | .988 | review，medium |
| 提瓦特民谣说明后缀、多人署名 | .980 | 1.000 | .988 | auto_accept，strong |
| Nameless Faces / Lilas Ikuta credit / HoYoFair | .980 | .920 | .955 | review，medium |
| 复梦天使双语标题、三Z/Sān-Z别名 | .980 | 1.000 | .988 | review，medium |

四例无虚假标题/艺人冲突；推测双语段、语言未标和署名角色疑问仍待复核，不以高分冒充已核验录音身份。

## 上轮阻断项复验

- Stay with Me → Stay：标题.500、总分.212、title_mismatch、最终no_match。
- Love with You → Love：标题.471、总分.207、title_mismatch、最终no_match。
- Stay with Me解析保留完整歌名，title_credits为空。
- Fake Artist 花泽香菜 → 花泽香菜：艺人.400、总分.753、primary_artist_mismatch、最终review，不自动采纳。
- Taylor Swift 周杰伦 → Taylor Swift：艺人.500、总分.794、primary_artist_mismatch、最终review，不自动采纳。
- V5.1非确定性标题60组合和sweets parade异歌拒绝回归均通过。

## 缓存与检索

专项新增双语段实际引擎回退轨迹、限流/网络分类、SQLite全链路重搜测试通过。
Codex额外使用独立临时SQLite，全部其它版本置为当前值（包括romanizer/exception registry），分别只改变规则、别名、查询版本为2026.09.v6，覆盖9种情况：

- 每种单独过期的旧no_match：缓存MISS，允许重搜。
- 每种单独过期的旧Stay误匹配auto_accept：重新评分为no_match。
- 每种单独过期的user_confirmed：完整保留人工决策。

不操作真实缓存；没有真实账号/线上曲库验证。图2历史未匹配的实际根因仍未确认，不扩大本轮结论。

## 非阻断文档/测试建议

1. RESULT.md中复合艺人反例总分.50/.55与本次真实无元数据评分.753/.794不一致；最终都review且有明确冲突，不影响阻断修复。后续记录具体输入及score/evaluate阶段，避免混淆。
2. 仓库C01辅助函数仍硬写旧romanizer/exception registry版本，所谓单版本过期测试存在混杂；Codex本次已独立保持这些版本为当前值并验证通过。建议后续将测试辅助函数改为当前常量，让长期回归真正隔离目标版本。

保留上一轮ACCEPTANCE.md作为失败记录；本文件为更新后代码的复验结论。
