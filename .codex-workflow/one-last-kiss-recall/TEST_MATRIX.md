# 验收测试矩阵

| 编号 | 场景与模拟 Catalog 返回 | 必须断言 |
| --- | --- | --- |
| R01 | 源 `One Last Kiss (最后一吻)` / `宇多田光 (宇多田ヒカル)`；严格组合查询无结果，只有目标区核心歌名独立查询返回 `One Last Kiss` / `Utada` | 引擎确实执行标题独立查询；候选入池；别名身份识别；结果为安全的 `auto_accept` 或有明确原因的 `review`，不能 `no_match`；记录分值与证据。若两项独立强证据满足当前门槛，应 `auto_accept`。 |
| R02 | R01 的候选改为同标题、无关歌手 | 不自动采纳，不能产生错误的艺人强证据；若有冲突应明确显示。 |
| R03 | 同歌手但 `magical mode` 等不同标题；另加翻唱、Live/Remix/伴奏候选 | 不因相同艺人或标题片段虚高分；保留现有版本冲突规则。 |
| R04 | 日文或中文歌名、括号译名、无歌手、短而常见的歌名及多别名歌手 | 查询可去重、稳定、预算 ≤8；短标题不能仅凭标题自动采纳；原有建议与日本区发现测试继续通过。 |
| R05 | 旧版 `no_match` 缓存有低分候选，升级查询策略后新的 Catalog 查询可找到正确歌曲 | `get_match/find_match` 不把旧 `no_match` 当最终结论；`match_playlist` 实际发起新检索并写入新结果。 |
| R06 | 旧版空候选 `no_match`、旧版 `review`、旧版 `user_confirmed`，分别通过主键与次级键读取 | 算法结论按新策略重新查询；人工确认不变；未实际重查的记录不能虚标新版本。 |
| R07 | Catalog `rate_limited`、`auth_failed`、`network_error` 和确实无命中 | 正确保留错误/不可用语义；不缓存暂时失败为可靠未匹配。 |

至少运行：新增专项测试、既有 `test_precision_matrix.py`、`test_name_search_scoring_v3.py`、`test_stale_review_cache.py`，然后全量 `pytest -q` 和 `git diff --check`。结果报告给出命令、通过/失败数量、截图样例的实际查询顺序和评分结论。
