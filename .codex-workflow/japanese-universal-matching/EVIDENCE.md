# 依据与边界

## 当前代码依据

- `matcher/query_planner.py`：首轮固定 2 条、深度固定 6 条；罗马字标题分支要求标题含假名。
- `matcher/cleaner.py`：纯汉字且不在人工复合词表时返回空罗马字候选。
- `matcher/title_aliases.py`：存在歌曲级 `ARTIST_SCOPED_TITLE_ALIASES`。
- `matcher/artist_aliases.py`：跨语言艺人主要来自静态 `ARTIST_GROUPS`。
- `client.py`：catalog search 没有 `l` 参数，缓存键不含 locale；解析时丢弃关系 ID。
- `matcher/evidence.py` 与 `matcher/scorer.py`：scorer 传入的版本字段未在模型中声明，必须先修复。

## Apple 官方能力

- Storefront 与本地化：<https://developer.apple.com/documentation/applemusicapi/storefronts-and-localization>
- Catalog Search：<https://developer.apple.com/documentation/applemusicapi/search>
- Search Suggestions：<https://developer.apple.com/documentation/applemusicapi/get-catalog-search-suggesions>
- 跨 storefront 等价歌曲：<https://developer.apple.com/documentation/applemusicapi/get-equivalent-ids-for-the-albums-3ce20>
- Alternate Versions and Equivalencies：<https://developer.apple.com/documentation/applemusicapi/managing-content-ratings-alternate-versions-and-equivalencies>
- 截图歌曲的 Apple Music JP 目录证据（Apple 官方歌单列出 `スパークル—幾田りら`）：<https://music.apple.com/jp/playlist/2022%E5%B9%B4%E3%83%88%E3%83%83%E3%83%97%E3%82%BD%E3%83%B3100-%E6%97%A5%E6%9C%AC/pl.b270c03a4cb84274afa78869155d8bd6>

Apple 文档明确说明：storefront 决定地区可用内容与支持语言；`l` 必须是 storefront 支持的语言标签；`filter[equivalents]` 可将一个 storefront 的歌曲 ID 映射为另一个 storefront 的最佳可用等价歌曲。

截图中的歌曲可在日本区 Apple Music 目录确认存在，因此它适合作为“目标区未命中 -> JP 发现 -> 目标区回映射”的回归用例，但不能由此假设每个目标区都存在可添加的 equivalent。

## 边界

- 无 ISRC、无可靠艺人、只有机器翻译标题时，无法安全自动判断任意两首歌相同；此类必须 review。
- Apple Music 目标区没有授权内容时，算法不能创造可加入的目标区歌曲，只能报告 unavailable。
- “几乎所有”通过真实评估集的召回指标衡量，不能靠不断加入已失败歌曲的硬编码来证明。
