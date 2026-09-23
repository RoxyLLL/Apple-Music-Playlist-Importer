# 测试与验收矩阵

| ID | 输入/场景 | 必须验证 |
| --- | --- | --- |
| N01 | Apple `artistName="Tyler, The Creator"`，无 artists relationship | 显示与比较不拆成两个艺人 |
| N02 | 明确的多艺人 relationship、`feat.`、CV/角色、全角/半角分隔符 | 主次艺人和稳定 ID 正确保留；原文不丢失 |
| N03 | 日文/假名/纯汉字/拉丁艺名、括号官方译名、版本标签 | 变体有来源；纯汉字转写仅弱证据；Live/伴奏/Remix 不丢失 |
| Q01 | 来源 `trans_title="Official English Name"`、`aliases` 非空，原文检索无命中 | 译名/别名进入后续目标区查询，且不超预算 |
| Q02 | `灰かぶり (Cinder ella) / 十明` 的 A=5、B=4、D=2 场景，前面均无可靠候选 | 日区发现至少执行一次；catalog 总数 ≤8 |
| Q03 | 已有低质量候选或 `review`，后续提示/日区存在强候选 | 继续合理扩展，最终选强候选；高质量原文命中早停 |
| Q04 | 401、429、超时及部分搜索成功 | 停止/降级符合现有错误语义；不缓存为 `no_match`、不误报目标区未上架 |
| Q05 | 日区仅弱相似歌曲，或 equivalents/目标区 ISRC 回查失败 | 不输出肯定的 `unavailable_in_target_storefront` |
| S01 | 同 ISRC，候选歌名明显不同且艺人缺失 | `title_score`/`artist_score` 不伪造为 1；不得 `auto_accept` |
| S02 | 同 ISRC，歌名艺人一致、版本无冲突 | 保留自动采纳能力和强 ISRC 依据 |
| S03 | 同 ISRC，但主艺人/Live/伴奏/Remix 有显式冲突 | 转 `review`，诊断显示冲突 |
| S04 | `Main Artist feat. Guest Artist` 对候选主艺人 `Guest Artist`，歌名相同 | 主艺人不符时不得艺人满分或自动采纳 |
| S05 | 歌名同形、时长/专辑相近，但主艺人不同；或纯转写的歌名+艺人双命中 | 不得凭相关弱证据自动采纳 |
| S06 | 两个相似候选但版本/主艺人不同 | 不能仅凭模糊标题绕过分差复核 |
| C01 | 旧版本 `auto_accept` JSON、历史字段和缺失版本 | 可以解析并重评；当前版本结果可复用 |
| C02 | 同源同区 `user_confirmed`、跨来源/跨区查询 | 前者保留，后者不泄漏人工选择 |
| U01 | `review` 得分高、`auto_accept` 得分高、`unavailable`、`search_incomplete` | 展示/默认勾选与决策一致；分数不标为概率 |

测试须覆盖 API 参数和真实 engine 调度顺序，不能只断言 `QueryPlanner` 生成了查询而不验证其最终执行。保留既有 `test_precision_matrix.py`、`test_japanese_universal_matrix.py` 用例，新增回归用例不得把测试曲目写入运行时别名表。除合成边界测试外，真实召回评估需提供样本来源及快照，并把“候选召回”和“自动采纳准确性”分别报告。
