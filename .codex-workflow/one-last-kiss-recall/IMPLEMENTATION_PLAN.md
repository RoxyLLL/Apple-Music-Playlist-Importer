# Antigravity 实施框架

1. 在 `applemusic/matcher/query_planner.py` 和 `engine.py` 调整规划与执行：先查严格歌名＋歌手；无可靠命中时执行保留的核心歌名独立查询，随后继续受预算约束的别名、脚本变体、建议和跨区发现。所有阶段共享去重键；别名排序稳定。根据现有测试对建议及日本区额度的约束安排预算，不牺牲原有的 8 次上限和错误处理。
2. 在 `artist_aliases.py` 补充经 Sony Music 核实的完整艺人别名 `Utada`。检查 `get_artist_aliases()` 的集合迭代顺序和候选归一化；使查询优先级可复现。`scorer.py` 仅在确实需要时修改，不以子串匹配提高艺人分。
3. 在 `evidence.py` 提升相应规则/查询/别名版本；在 `cache.py` 让查询策略升级后的算法 `no_match`/`review` 走缓存未命中并重新搜索，而非只重评旧候选。普通评分规则变化仍可沿用既有安全重评逻辑。保留 `user_confirmed`；验证 `find_match()` 的次级键和 `match_playlist()` 的实际检索分支。
4. 补充针对查询规划、引擎集成、艺人同一性、缓存迁移的回归测试。沿用项目模型和 mock Catalog，断言实际执行的查询、候选 ID、得分组成、最终决策及请求数，而不只断言规划列表。
5. 运行专项与全量测试、`git diff --check`，记录结果和未验证事项到本目录 `RESULT.md`。如有线上 Catalog 凭据且连接正常，可另做一次只读真实检索并记录 storefront、实际查询词、候选 ID/名称及最终决策；线上不可用时明确说明。

代码、测试与 `RESULT.md` 由 Antigravity 完成；Codex 依据 `TEST_MATRIX.md` 独立复验。请勿 commit、push、tag 或发布。
