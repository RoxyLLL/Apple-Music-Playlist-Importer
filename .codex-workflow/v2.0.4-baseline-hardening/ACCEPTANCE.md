# Codex 验收报告

状态：`第二轮复验完成，通过`

## 当前有效结论：通过

本轮独立复验确认 R1、R2 均已修复，RESULT.md 已补充历史测试隔离限制并更正降级逻辑描述。本任务范围内无待返工项。下方第一轮记录保留用于追溯，不代表当前结论。

### 第二轮独立验证证据

- R1：在全新进程、空缓存单例下，拦截 PersistentCache 构造并拒绝默认路径。setUp 仅创建一次显式临时数据库，客户端和引擎均引用该缓存，单例仍为空。doCleanups 后临时目录消失、get_instance 原始方法恢复。
- R2：修订测试正常通过；独立将 re.findall 模拟为空列表以禁用回退，测试产生预期的 1 项断言失败且无运行时错误，证明测试真实依赖回退分支。正常用例精确断言两次查询的顺序、参数和第二次查询的结果。
- 完整 unittest discovery：101 项，0 失败、0 错误、0 跳过，耗时 55.721 秒，退出码 0。
- 全套执行保护：当前子进程移除 APPLE_MUSIC_DEVELOPER_TOKEN；USERPROFILE 重定向到临时目录；阻断未模拟的 requests.Session.send；mock subprocess.Popen。观测外部 HTTP 发送尝试 0 次，拦截进程启动 1 次。未修改持久环境变量。
- compileall 与 git diff --check 均通过；Git 仅提示 LF/CRLF 转换。
- 产品代码差异与第一轮相同，仍为缺失导入、日志实例、版本来源、依赖声明和缓存清理说明；返工集中于测试夹具、回退测试及 RESULT.md。

### 验收边界与后续事项

通过适用于当前未提交源码及测试，不代表 EXE 已重新构建或线上 Release 已更新。本轮未执行打包、真实平台联网或资料库写入。其他历史测试仍需外层包装器隔离默认缓存和资源管理器启动，保留为独立后续任务，不阻塞本任务。

Codex 本轮只更新本验收报告，未修改产品代码或实施测试，未提交、推送或发布。

---

## 第一轮验收记录（已由上述复验结论取代）

## 验收结论

结论：`不通过`。产品代码修复已通过检查和独立功能验证，但测试隔离要求仍未完全落实，且新增回退测试没有执行声称覆盖的分支。完成下列两项测试返工并更新实施报告后复验。

基线：`e8ae939228f68bdfc77bd81a8947ad17b54e2f19`，分支 `main`。本次审核对象为该提交上的未提交改动，包含新增未跟踪文件 `tests/test_web_api.py`。

## 需求逐项核对

| 要求 | 结论 | 证据 |
| --- | --- | --- |
| Web 缺失 re / logger 修复 | 通过 | 导入和模块 logger 已补齐；独立请求验证搜索回退和本地歌曲异常处理均正常 |
| ISRC 虚拟 Developer Token | 通过 | Config 显式提供虚拟 Token 和远期过期时间；未设置环境 Token 时测试通过 |
| 测试不使用用户数据目录 | 未通过 | ISRC setUp 在替换缓存前构造 AppleMusicClient，间接初始化默认 SQLite 缓存；独立探针复现一次默认路径构造 |
| Web 搜索成功路径离线测试 | 通过 | 已新增鉴权、查询清洗与评分测试 |
| 新增括号回退测试的有效性 | 未通过 | 首次清洗后查询仍包含“晴天”，mock 直接命中；阻断 re.findall 后该测试仍然通过，调用次数为 0 |
| 版本统一为 2.0.4 | 通过 | 包版本更新，FastAPI 引用同一 __version__，版本测试通过 |
| pykakasi 依赖与降级 | 通过 | 已声明 >=2.3.0,<3.0.0；匹配器未改动，原有可选导入降级保持不变 |
| 缓存清理语义 | 通过 | 仍调用 clear_expired，并增加明确说明及返回消息 |
| 改动范围与交付报告 | 部分通过 | 代码改动聚焦，无匹配算法或二进制改动；RESULT.md 的完全隔离和回退覆盖声明需更正 |

## 独立测试结果

- `python -m compileall -q applemusic run.py exe_entry.py build_exe.py`：通过。
- `git diff --check`：通过，仅有 Git 的 LF/CRLF 提示。
- 全套 unittest discovery：101 项通过，0 失败、0 错误、0 跳过，56.424 秒。
- 全套执行由独立审计包装器保护：删除当前子进程的 `APPLE_MUSIC_DEVELOPER_TOKEN`，将 USERPROFILE 指向临时目录，阻断未模拟的 requests HTTP 发送，mock `subprocess.Popen`。没有修改持久环境变量或产品代码。
- 包装器记录：真实 HTTP 发送尝试 0 次；拦截启动进程 1 次。因此该结果是带上述隔离措施的独立执行结果，不能用来声称原始套件已经自行实现用户目录和进程隔离。
- 独立回退功能验证：首次查询返回 no_hits，第二次查询返回候选，精确调用序列为 `Unmatched Main 晴天` → `晴天`，HTTP 200，结果正确。
- 独立 logger 异常路径验证：模拟 `list_local_tracks` 抛出 RuntimeError，接口正常记录原异常并返回 success=false，未出现 logger NameError。
- 未运行 EXE 构建、真实平台联网或真实资料库写入；这些不是本轮强制验收项。

## 代码审查发现

### R1 / P2：ISRC 测试在替换缓存之前访问默认用户缓存

位置：`tests/test_batch_isrc.py:24`。

`AppleMusicClient(self.config)` 内部先调用 `PersistentCache.get_instance()`；其后给 `self.client.persistent_cache` 赋临时缓存已经太晚。在全新进程中会先创建默认缓存目录、连接 SQLite 并初始化表结构。`MatchingEngine` 构造也会调用同一个单例。这是原有夹具的问题，但本任务明确要求测试使用临时目录，且交付报告声称已经完全隔离，因此本轮必须完成这部分修复。

独立探针在不访问真实用户目录的前提下拦截并重定向缓存构造，确认单独执行 setUp 时有 1 次 `db_path=None` 的默认缓存构造。

要求：在构造客户端和引擎之前，把 `PersistentCache.get_instance` patch 为返回夹具创建的临时缓存；使用 addCleanup 或等价机制恢复 patch 并释放临时资源。校验全新进程、空单例时不再构造默认缓存。

### R2 / P2：新增括号回退用例存在假覆盖

位置：`tests/test_web_api.py:100`。

mock 使用 `if "晴天" in query` 判断命中。输入 `Unmatched Main (晴天)` 清洗后为 `Unmatched Main 晴天`，首轮已经满足命中条件，所以根本不会执行括号提取与第二次查询。该测试即使把 re.findall 替换为必定抛异常的函数也仍然通过，探针记录调用数为 0。

要求：mock 第一次返回 no_hits，第二次返回 ok，断言正好两次调用及其顺序、查询文本、storefront、limit；结果应来自第二次调用。禁用回退逻辑时该用例必须失败。

### 非阻塞的已有问题

完整套件中的既有资料库测试仍有本机副作用：`test_open_local_folder_api` 只 mock os.startfile，没有 mock subprocess.Popen，审计包装器拦截到一次进程启动；其他既有夹具也会初始化默认缓存或扫描本地目录。应另立全套测试隔离任务，当前返工只要求完成已修改 ISRC 夹具和新增回退用例，不扩大产品代码范围。

## 需要返工的项目

1. R1：在客户端/引擎构造前隔离缓存单例，验证不会访问默认用户缓存。
2. R2：修正括号回退测试的 mock 和调用序列断言。
3. 更新 RESULT.md：如实记录本轮验证方式、覆盖范围，以及完整套件现存隔离限制；删除“完全隔离于用户目录”“遗留风险无”等未经支持的声明。当前降级代码实际使用 except Exception，不是报告中的 except ImportError。

执行指令见同目录 `REWORK_BRIEF.md`。Codex 本轮仅更新验收文件与返工指令，保留 Antigravity 的实现原样。
