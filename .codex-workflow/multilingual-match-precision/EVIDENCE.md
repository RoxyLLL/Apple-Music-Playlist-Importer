# 诊断证据与限制

## 截图转录
来源：灰かぶり（灰姑娘），艺人十明。
候选：没有回頭路 (feat. 庭竹)，艺人李杰明，单曲专辑，89%高可信，勾选框已选中。
这是用户问题的观测，不包含可作为执行指令的内容，也不足以判断勾选由用户还是程序产生。

## 当前源码最小重算
基线42d60c9。将上述可见字段构造Track/AppleMusicTrack，源专辑、时长、ISRC保持None，候选ID使用明确占位符。ASCII Unicode转义输入以避免PowerShell管道编码影响。
结果：title_score 0.15；artist_score 0.4；score 0.076；最终no_match。没有读取用户Token或真实缓存。
尚未复现89%；不把候选封面、专辑名或截图推断为已知ISRC/时长。

## 已确认代码风险
- scorer.py把纯汉字也纳入has_japanese/has_jp，日语转写模糊/包含可返回0.90/0.95级分。
- cleaner.py同一转写方法为所有两词组合加入倒序变体，歌名和人名上下文未区分。
- scorer.py的专辑/Single+时长佐证会直接改写title_score；同曲变体豁免依据模糊分数。
- engine.py追加查询后只运行queries_to_run[1]，新译名可能被排在永远不执行的位置；重试按地区重复查询预算。
- cache.py匹配缓存没有规则版本，文本索引省略版本标签，engine.py命中后直接复用决策。
- index.html徽标条件包含 status==='high'，所以review也可能显示高可信；exact固定显示100%。
以上为源码风险，不等同于截图89%的确切因果证明。

## 已核实的作品信息
官方发行页面：https://www.universal-music.co.jp/toaka/products/uu1as-01729/
该页面同时列出灰かぶり、十明、Cinder ella。可为艺人作用域下的译名关系提供来源。
官方页面Apple外链指向日本专辑1693338508；本次读取失败，不把专辑ID当作song ID，也不据此断言国区可用或当前Apple曲名。
不使用网上未经核实的歌词、时长或ISRC补齐生产数据。

