# 给 Antigravity 的执行指令

你正在处理 `D:\applemusic` 项目的 **v2.0.4 基线加固任务**。

请先完整阅读：

1. `.codex-workflow/v2.0.4-baseline-hardening/SPEC.md`
2. `.codex-workflow/v2.0.4-baseline-hardening/IMPLEMENTATION_PLAN.md`
3. `.codex-workflow/v2.0.4-baseline-hardening/RESULT.md`

然后完成实现和测试。

## 你的职责

- 编写具体代码和回归测试。
- 保持改动最小、聚焦，不重构无关模块。
- 修复 Web 模块缺失的 `re`、`logging` 和 `logger`。
- 让 ISRC 批处理测试完全脱离真实 Token、用户配置和外部网络。
- 增加 `/api/search-track` 离线回归测试。
- 将项目版本统一为 `2.0.4`，FastAPI 从包版本读取。
- 在 `requirements.txt` 声明 `pykakasi>=2.3.0,<3.0.0`。
- 澄清缓存接口只清理过期项，但不要改变成删除全部有效缓存。

## 禁止事项

- 不读取、输出或提交真实 Apple Music Token。
- 不使用真实 Apple ID、真实资料库写入或删除接口做测试。
- 不修改匹配权重、评分阈值、别名表和平台解析逻辑。
- 不修改或提交 EXE、音频文件、缓存数据库、`build/`、`dist/`。
- 不运行 `git reset --hard`、`git checkout --` 等会覆盖用户改动的命令。
- 不提交、不推送、不打 Tag、不创建 Release。

## 必须执行的验证

```powershell
python -m compileall -q applemusic run.py exe_entry.py build_exe.py
python -m unittest tests.test_batch_isrc.TestBatchISRCAndMultiIndexCache.test_batch_isrc_chunking_and_caching -v
python -m unittest discover -s tests -v
git diff --check
git status --short
```

完整测试必须证明：即使当前测试进程没有 `APPLE_MUSIC_DEVELOPER_TOKEN`，测试也不会访问真实认证服务。

## 交付格式

完成后填写 `.codex-workflow/v2.0.4-baseline-hardening/RESULT.md`，至少包含：

- 实际修改的文件
- 每项问题如何解决
- 新增或修改的测试
- 每条验证命令及结果
- 未解决问题或风险
- 建议 Codex 验收时重点检查的位置

完成后停止，等待 Codex 验收。

