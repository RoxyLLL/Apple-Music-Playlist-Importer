# 交给 Antigravity 的任务

请按本目录 `SPEC.md`、`IMPLEMENTATION_PLAN.md`、`TEST_MATRIX.md` 实现并测试自动匹配漏召回修复。截图案例：源 `One Last Kiss (最后一吻)` — `宇多田光 (宇多田ヒカル)`；手动搜索发现目标区 `One Last Kiss` — `Utada`，自动匹配没有找到。

Codex 已用当前代码复现：计划查询不包含有歌手时的独立 `One Last Kiss`，且 `TrackScorer` 对该候选给出 `title≈0.98 / artist=0.15 / total≈0.191 / no_match`。Sony Music 官方页证实 `Utada` 是艺人使用的名称：<https://www.sonymusic.co.jp/Music/Info/utadahikaru/en/music/>。还要处理旧 `no_match` 缓存在查询策略升级后阻断重新搜索的问题。

请实际修改项目代码与测试，运行测试，写 `RESULT.md` 说明文件、命令、通过数、剩余风险。优先修整条通用查询与验证链路；不要把这首歌放进特例缓存，不要放宽不同歌手/不同标题的自动采纳条件。不要 commit/push/tag/release。完成后明确回复 Codex 可验收。
