# 项目缺陷与逻辑审计报告

> 项目：Apple Music Playlist Importer  
> 审计日期：2026-09-14  
> 审计基线：当前 HEAD edda4ed  
> 范围：Python 后端、匹配引擎、四类歌单提取器、Web 前端、B 站补全链路、CLI、授权与打包配置。  
> 本次仅生成报告；未修改任何已有代码或配置。

## 审计结论

发现 14 项问题：

| 级别 | 数量 | 结论 |
| --- | ---: | --- |
| P0 | 2 | 可使错误曲目被加入目标歌单，应优先修复 |
| P1 | 8 | 会造成漏配、空歌单、下载错误内容或敏感配置风险 |
| P2 | 4 | 影响稳定性、性能、登录可用性和导出正确性 |

当前代码的语法、基础导入和 CLI 初始化均正常；问题主要集中在“匹配结果的决策状态没有贯穿到导入动作”“失败被错误地当作正常结果处理”“备用路径缺少最后校验”。

## 已执行的离线验证

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| Python AST 解析 | 通过，23 个 Python 文件 | 未发现语法错误 |
| 模块导入 | 通过 | 已导入 auth、auto_token、client、cli、web.app、bilibili_downloader |
| CLI 帮助 | 通过 | run.py --help 可正常显示命令 |
| 曲库检索/外部平台实测 | 未执行 | 需要有效 Apple Music token、外部网络与真实账号数据；下列问题均来自可复现的本地逻辑或静态调用链 |

其中两项做了不访问网络的定向验证：

1. 构造“曲库只返回一首标题完全不同的歌曲”时，find_library_song_id(目标歌名) 返回了该错误歌曲的 ID。
2. 构造两首 QQ 音乐曲目且 original_id 均为字符串 None 时，稳定去重键相同，二者会被当作同一首歌处理。

## P0：会错误写入歌曲

### BUG-01：CLI 与 Web 按置信度状态导入，绕过 review 决策

**证据**

- applemusic/matcher/scorer.py 第 332–336 行：当候选分数较高但存在小分差或版本歧义时，函数返回 decision=review，且 status 可以是 high。
- applemusic/cli.py 第 223–225 行：所有 status 为 exact 或 high 的结果都直接加入待导入 ID，完全不检查 decision。
- applemusic/web/static/index.html 第 1171 行和第 1260 行：初次匹配后，status 为 exact、high、medium 的候选都会预先勾选。
- 同文件第 1391 行：所谓“仅选精准和高可信”的操作仍按 status=high 选择，而不是按 decision=auto_accept。

本地构造两个 0.80 与 0.79 分的候选，评分器输出为 high、review、分差 0.01；这类“需要复核”的结果在 CLI 会直接导入，在 Web 默认也会预选。

**影响**

同名曲、Live/Remix、不同艺人翻唱或前两名分数接近时，项目新增的 review 机制失效，错误版本仍可进入 Apple Music 歌单。

**修复方案**

1. 将 decision 作为唯一导入权限：只允许 auto_accept 与 user_confirmed 默认进入待同步列表。
2. review 候选必须默认不勾选；用户手工勾选或在候选弹窗确认后，再标记为 user_confirmed。
3. CLI 改为按 decision 分支：auto_accept 直接加入；review 无论 status 是 high 还是 medium 都要求 Confirm.ask；no_match 写入未匹配列表。
4. 前端的筛选、统计、批量选择均以 decision 为主，status 仅作为展示等级，避免同一首 review/high 同时计入“高可信”和“待复核”。
5. 增加回归用例：高分但低分差、版本冲突但高分、人工确认三类结果不得走同一导入路径。

**验收标准**

任何 decision=review 的歌曲，在用户没有明确操作前都不会出现在 sync 请求的 track_ids 中。

---

### BUG-02：本地资料库查询找不到标题时会返回第一首无关歌曲

**证据**

applemusic/client.py 第 373–380 行的逻辑是：

1. 只要 Apple 返回任何 library-songs 数据；
2. 尝试以标题子串匹配；
3. 如果没有任何标题匹配，直接返回 data[0] 的 ID。

这条路径用于 B 站补全后把本地音频添加进新歌单。离线伪造的曲库响应仅含标题 Completely Different，调用 find_library_song_id(Wanted Song, Artist) 实际返回 wrong-id。

**影响**

Apple Music 尚未完成自动导入、检索排序变化或歌曲同名时，应用会把资料库中第一首无关歌曲加入用户新建歌单。该问题是静默错误，用户很难发现。

**修复方案**

1. 删除无匹配时返回第一条数据的兜底逻辑。
2. 复用 MatchingEngine 或至少复用 TrackScorer，对标题、艺人、专辑和时长进行评分；未达到阈值时返回 None。
3. 对 B 站导入写入可追踪元数据，例如 bvid、标准化标题和文件指纹；搜索资料库时优先校验该标识。
4. 在 Apple Music 自动导入尚未完成时返回 pending，不创建错误映射；可在稍后重试。

**验收标准**

资料库响应中没有标题和艺人均符合的歌曲时，函数必定返回 None，而不是任意资料库 ID。

## P1：主要功能与数据正确性

### BUG-03：跨 storefront 重新匹配会在当前区存在普通候选时提前退出

**证据**

applemusic/matcher/engine.py 第 288–314 行先遍历目标区与备用区，但在当前区只要 collected_candidates 中存在任一分数大于或等于 0.50 的候选，就 break 跳出整个 storefront 循环。该条件比 auto_accept 宽得多。

**影响**

用户开启 hk、tw、us 备用曲库后，当前区一个低质量候选即可阻止后续地区搜索；跨区补全名义上开启，实际未执行。

**修复方案**

仅在当前 storefront 已得到 auto_accept 时停止遍历备用区。若当前区只有 review 或低分候选，应继续备用区搜索，并在结果中标注候选的 storefront 与可导入性。

**验收标准**

当前区最高分 0.50–0.87、备用区存在 auto_accept 候选时，返回备用区候选或明确提示该候选不能直接导入当前区。

---

### BUG-04：B 站下载缓存只按歌手和歌名命名，切换候选版本仍会复用旧音频

**证据**

applemusic/extractors/bilibili_downloader.py 第 448–455 行将缓存文件名固定为 歌手 - 歌名.m4a；文件存在且大于 100KB 就直接返回 reused=True。缓存校验没有比较 bvid、内容哈希或版本标签。

**影响**

用户第一次下载 Live，后来在 B 站候选中选择 Studio，或同名不同版本的歌曲再次下载时，应用会把旧文件当成新选择的音频。返回结果看似成功，但实际内容错误。

**修复方案**

1. 缓存键至少包含 bvid，或包含 bvid 与版本标签的安全短哈希。
2. 在 MP4 注释中保存 bvid，并在复用前读取和校验。
3. 若希望按“同一首歌”复用，必须显式比较标准化标题、艺人、版本标签与用户选择的 bvid。
4. UI 说明复用的是哪一个 bvid，允许用户强制重新下载。

**验收标准**

同一歌名选择不同 bvid 时，生成或复用的文件必须属于用户刚选择的 bvid。

---

### BUG-05：同步接口先创建歌单，后确认实际可加入曲目

**证据**

applemusic/web/app.py 第 397–401 行先调用 create_playlist。第 403–427 行才解析 B 站本地曲目、去重并得到 deduped_ids。若所有本地 B 站音频仍处于 pending，最终仍会创建一个空歌单。

**影响**

用户会看到成功创建的空播放列表，且重复重试可能产生多个空歌单。

**修复方案**

1. 先解析所有 SyncTrackItem、查找本地资料库、去重并构建 deduped_ids。
2. 当 deduped_ids 为空时返回 409 或结构化 pending 结果，不创建播放列表。
3. 如果允许部分成功，在响应中返回 pending 项，并由前端让用户确认“仅导入已就绪的 N 首”。
4. 为创建歌单和添加歌曲增加请求级幂等键，防止网络重试创建重复歌单。

**验收标准**

零首可写入时 Apple Music 不产生新歌单；部分可写入时结果中准确区分已添加、待处理与失败曲目。

---

### BUG-06：异常 QQ 曲目 ID 会被字符串化为 None，错误合并多个不同歌曲

**证据**

- applemusic/extractors/qqmusic.py 第 87 行执行 str(songmid or songid)。两个字段均缺失时结果为字符串 None。
- applemusic/matcher/engine.py 第 37–38 行只要 original_id 是非空字符串，就优先用 source + original_id 作为稳定去重键。

离线验证中，两首不同歌名且 original_id=None 的 QQ 曲目均得到键 (qqmusic, None)。

**影响**

异常接口数据、下架曲或字段变化时，多首不同歌曲只会检索第一首，之后将第一首的候选广播给所有被错误合并的行。

**修复方案**

提取器仅在 songmid 或 songid 实际存在时赋 original_id；否则传 None。引擎还应显式排除空串、None、null、unknown 等保留值，再使用元数据回退键。

**验收标准**

任何两首 title 或 artist 不同、且缺少可信原始 ID 的曲目都不得仅因占位字符串而合并。

---

### BUG-07：Spotify 歌单静默截断到 2,000 首

**证据**

applemusic/extractors/spotify.py 第 102 行的分页条件为 while next_url and len(tracks) < 2000，第 123–128 行直接返回已收集 tracks，没有 truncated 标志或告警。

**影响**

超过 2,000 首的公开 Spotify 歌单会少导入后半部分，用户只会看到一个看似正常的成功结果。

**修复方案**

移除固定上限，或将上限做成明确配置并在达到上限时抛出可见告警。Playlist 模型可增加 extraction_warnings 与 source_total 字段，UI/CLI 必须显示已读取数与源总数。

**验收标准**

超过限制的歌单要么完整提取，要么明确告知“已截断，未处理 X 首”；不能静默成功。

---

### BUG-08：匹配 API 失败时前端仍显示 100% 进度，曲目永久停留在 matching

**证据**

applemusic/web/static/index.html 第 1157–1185 行和第 1247–1273 行没有检查 HTTP 响应是否成功。若 API 返回 4xx/5xx 的 JSON detail，matchData.success 为假，代码不会替换对应占位行，但仍增加 completed 并推进到 100%。

**影响**

用户看见任务已完成，但部分行仍显示“正在匹配”，这些条目既不明确失败，也不会进入未匹配/重试队列。

**修复方案**

1. 检查 response.ok；非成功状态统一转换为每首曲目的 error 或 no_match_with_error。
2. 当 success 为假时，将该批次中全部占位项更新为 error 状态并保留错误原因。
3. 进度中单列成功、无匹配、失败数；完成时若失败数非零显示重试入口。
4. 文件导入与链接导入共用同一批处理函数，消除两处重复但行为不一致的代码。

**验收标准**

任何一批 API 失败时，页面没有残留 matching 状态，且用户可区分“曲库无结果”与“请求失败”。

---

### BUG-09：自动 B 站补漏没有最低候选质量门槛

**证据**

applemusic/web/app.py 第 536–560 行和第 499–526 行均直接采用 search_bilibili 返回的 cands[0]。而 applemusic/extractors/bilibili_downloader.py 第 272–291 行会保留 Tier 4 的杂音/未命中候选，搜索结束后仍可将最高 Tier 4 标为 is_best_match。

**影响**

当没有合格候选时，自动补漏可能下载教学、解说、翻唱或无关视频，并将其伪装成原曲元数据导入资料库。

**修复方案**

自动下载仅接受 Tier 1，或在艺人未知时谨慎允许 Tier 2。Tier 3/4 必须展示候选并等待用户确认。响应中应返回 minimum_quality_met 与拒绝原因。

**验收标准**

搜索仅得到 Tier 3/4 候选时，自动补漏不下载、不写入 Apple Music 自动导入目录。

---

### BUG-10：Token 以明文写入磁盘，与 CLI 文案不符

**证据**

- applemusic/config.py 第 41–46 行将 Config 直接 model_dump_json 写入用户目录的 config.json。
- applemusic/cli.py 第 61 行提示“凭据已自动加密保存”。

项目虽声明 cryptography 依赖，但配置保存链路未使用加密。

**影响**

同一 Windows 用户下的其他进程、备份工具或误上传的配置文件可能获取 media-user-token；同时用户被错误告知已有加密保护。

**修复方案**

优先使用 Windows Credential Manager 或 DPAPI 保存 media-user-token；配置文件只保存凭据引用。若短期无法迁移，应纠正文案、设置仅当前用户可读权限、迁移旧明文并在首次启动提示用户。

**验收标准**

config.json 不出现 media-user-token 明文，CLI 不再宣称未实现的加密能力。

## P2：稳定性、性能与边界行为

### BUG-11：Singleflight 等待上限小于发起请求的最大执行时间

**证据**

applemusic/client.py 第 168–173 行中等待相同查询的线程仅等待 8 秒；第 184–251 行中发起线程最多 3 次请求，每次读取超时可达 10 秒，还包含退避和 token 刷新。

**影响**

慢网络或 Apple 限流时，等待者 8 秒后会再次发出同一请求，singleflight 失效，反而放大重复请求和 429。

**修复方案**

让等待者等待到发起者完成，或采用一个覆盖全部重试预算的共同 deadline。超出 deadline 时应返回结构化 timeout，而非悄悄自行发起重复请求。将发起结果或异常写入共享结果，所有等待者复用。

---

### BUG-12：自动登录使用固定调试端口与固定浏览器 profile，多个登录会互相冲突

**证据**

applemusic/auto_token.py 第 44 行默认端口固定为 9238，第 48 行 profile 固定为系统临时目录下 applemusic_sync_login，第 68–69 行同时传给浏览器。

**影响**

并发点击自动登录、上次浏览器异常未退出、或其他程序占用端口/profile 时，新的登录流程可能连接错误的调试实例、启动失败或误读旧会话。

**修复方案**

为每次登录分配空闲 loopback 端口与独立临时 profile，记录本次启动的进程 PID 和调试端点；完成后安全清理 profile。若端口已被占用，应明确报错而不是尝试连接未知实例。

---

### BUG-13：CSV 导出未转义双引号，合法歌名会破坏列结构

**证据**

applemusic/web/static/index.html 第 1491 行直接以双引号包裹字段，但没有把字段内双引号转成 CSV 标准的两个双引号。

**影响**

歌名、艺人或专辑含引号时，导出的 CSV 在 Excel 或其他工具中会出现错列，且后续再导入会产生错误数据。

**修复方案**

增加统一 csvEscape(value) 方法：先转字符串，再将内部双引号替换为两个双引号，最后用双引号包裹。优先使用成熟 CSV 库或浏览器端可靠序列化工具。

---

### BUG-14：配置状态接口在异步事件循环中执行阻塞网络请求

**证据**

applemusic/web/app.py 第 111–117 行的 async 路由直接调用 auth.validate_user_token()；该方法在 applemusic/auth.py 第 164–176 行使用同步 requests.get，超时为 10 秒。

**影响**

网络不通或 Apple Music 慢时，打开页面、刷新配置状态可能阻塞 FastAPI 事件循环，拖慢同一服务上的匹配、同步和其他请求。

**修复方案**

使用 asyncio.to_thread 包装同步验证，或迁移到异步 HTTP 客户端。增加短 TTL 的授权状态缓存，避免每次页面初始化都请求 Apple API。

## 安全边界建议

以下问题不是默认 127.0.0.1 使用方式下的必现业务错误，但在用户把 Web 服务绑定到非本机地址时风险显著：

| 问题 | 证据 | 建议 |
| --- | --- | --- |
| CORS 完全开放 | applemusic/web/app.py 第 50–56 行允许任意 origin、方法和头部，且允许 credentials | 默认仅允许本机 origin；若公开监听，启用随机本地管理令牌、认证与 CSRF 防护 |
| 可选公开监听无鉴权 | CLI 的 web 命令允许用户传入任意 host，后端的配置、同步和下载接口没有认证 | 限制为 loopback，或对非 loopback 强制鉴权并在启动时显示风险提示 |
| 不必要的原始错误回显 | 多个接口将 Exception 文本直接拼入 HTTP 500 detail | 记录详细错误到本地日志，对客户端返回固定错误码与安全摘要 |

## 推荐修复顺序

1. 先修 BUG-01 与 BUG-02，并为其添加单元测试。这两项直接决定是否会导入错误歌曲。
2. 修 BUG-05、BUG-08、BUG-09，防止空歌单、页面假完成和错误 B 站内容进入资料库。
3. 修 BUG-03、BUG-04、BUG-06、BUG-07，恢复跨区、缓存和大歌单逻辑的正确性。
4. 修 BUG-10 及安全边界问题，迁移已存在的明文 token。
5. 修 BUG-11 至 BUG-14，完善高并发、网络异常、登录冲突和导出体验。

## 建议加入的回归测试

| 场景 | 预期 |
| --- | --- |
| 两个高分候选分差 0.01 | decision=review，CLI/Web 默认不进入同步 |
| 本地资料库只返回无关歌曲 | find_library_song_id 返回 None |
| 当前区仅 0.50 候选、备用区有高分候选 | 重新匹配实际查询备用区 |
| B 站同歌名切换不同 bvid | 不复用旧 bvid 的音频缓存 |
| 全部 B 站本地曲目尚未入库 | 不创建空 Apple Music 歌单 |
| QQ 曲目缺失 songmid/songid | 每首歌使用元数据回退键，不互相合并 |
| Spotify 2,001 首歌单 | 完整获取，或明确报告被截断 |
| 匹配接口返回 500 | 前端行变为 error，不显示伪 100% 完成 |
| CSV 字段包含双引号和逗号 | 导出后列数与内容保持正确 |
| 两次并发自动登录 | 不共享调试端口或 profile |

## 审计限制

本报告没有使用真实 Apple ID、真实 media-user-token，也未对网易云、QQ、Spotify、B 站和 Apple Music 发起实网写操作。因此，外部接口的最新可用性、速率限制和地区授权需要在修复后通过隔离账号进行集成测试。报告中的 P0/P1 项均可由当前本地代码路径直接推导或通过无网络替身复现。
