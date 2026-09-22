# Antigravity 实施结果

状态：`返工完成，待Codex复验`

## 1. 修改摘要

- **修复 Web 模块缺失导入与日志记录器**：在 `applemusic/web/app.py` 中引入标准库 `re`、`logging`，并声明模块级 `logger = logging.getLogger(__name__)`，消除 `/api/search-track` 检索清洗及后续异常日志中的 `NameError`。
- **R1 修复（ISRC 夹具隔离）**：在 `tests/test_batch_isrc.py` 的 `TestBatchISRCAndMultiIndexCache.setUp` 中，在构造 `AppleMusicClient` 和 `MatchingEngine` 之前使用 `patch.object(PersistentCache, "get_instance", return_value=self.cache)` 拦截单例获取，并通过 `self.addCleanup` 注册清理，杜绝在夹具构造时隐式初始化默认路径数据库（`~/.applemusic_sync/cache.db`）。同时显式注入测试专用 mock Developer Token 及远期过期时间戳。
- **R2 修复（括号回退有效覆盖）**：在 `tests/test_web_api.py` 的 `test_search_track_bracket_fallback` 中，将 mock 改为精确两阶段响应（首轮 `Unmatched Main 晴天` 返回 `no_hits`，次轮 `晴天` 返回 `ok`），严格断言调用序列为这两次，并验证禁用回退逻辑时测试失败，彻底消除假覆盖。
- **统一版本来源**：将 `applemusic.__version__` 提升为 `"2.0.4"`，并让 `FastAPI(..., version=__version__)` 从包中读取版本，防止版本号碎片化。
- **补齐依赖声明**：在 `requirements.txt` 中显式声明受约束的 `pykakasi>=2.3.0,<3.0.0` 依赖。
- **澄清缓存清理语义**：为 `POST /api/cache/clear` 补充 docstring 并在响应中明确返回 `"message": "已清理过期缓存"`，保持只清理过期记录的安全语义不变。

## 2. 修改文件

- `applemusic/__init__.py`：将 `__version__` 从 `"2.0.3"` 提升为 `"2.0.4"`。
- `applemusic/web/app.py`：
  - 导入 `logging`、`re` 与 `__version__`。
  - 定义 `logger = logging.getLogger(__name__)`。
  - 将 `FastAPI` 实例版本设置为 `__version__`。
  - 明确 `/api/cache/clear` 的文档和返回信息。
- `requirements.txt`：追加 `pykakasi>=2.3.0,<3.0.0`。
- `tests/test_batch_isrc.py`：
  - 在 `setUp` 中于客户端构造前 patch `PersistentCache.get_instance` 返回临时缓存；
  - 注入 mock developer token 与远期过期时间戳；
  - 使用 `self.addCleanup` 恢复 patch、关闭 SQLite 连接及清理临时目录。
- `tests/test_web_api.py`（新增）：包含 5 个测试用例，覆盖版本一致性、应用 Token 拦截、`/api/search-track` 正则清洗与严格两阶段括号回退、缓存清理语义。
- `.codex-workflow/v2.0.4-baseline-hardening/RESULT.md`：更新本轮实施报告。

## 3. 问题解决情况

### Web 缺失导入

- 在 `applemusic/web/app.py` 文件头增加了 `import logging`、`import re`。
- 声明了模块级 `logger = logging.getLogger(__name__)`。
- 解决了 `/api/search-track` 在清洗查询条件（`re.sub`）和提取括号内容（`re.findall`）时出现的 `NameError: re is not defined`。
- 解决了 Bilibili 与本地文件相关路由捕获异常时调用 `logger.exception(...)` 出现的 `NameError: logger is not defined`。

### 测试认证与 ISRC 夹具隔离 (R1)

- 在 `tests/test_batch_isrc.py` 的 `TestBatchISRCAndMultiIndexCache.setUp` 中：
  1. 先创建 `tempfile.TemporaryDirectory` 并初始化临时 `PersistentCache(self.db_path)`；
  2. 在构造任何客户端或引擎之前，通过 `patch.object(PersistentCache, "get_instance", return_value=self.cache).start()` 拦截缓存单例获取；
  3. 为 `Config` 显式设置 `developer_token="mock_dev_token"` 与 `developer_token_exp=9999999999`；
  4. 使用 `self.addCleanup` 注册 patch 停止、SQLite 连接关闭及临时目录清理，保证异常情况下也能执行；
  5. 构造 `AppleMusicClient(self.config)` 与 `MatchingEngine(...)`。
- 经独立探针验证：在空单例全新进程中执行 `setUp`，仅产生 1 次指向临时目录数据库的构造，默认路径数据库构造次数为 0。当 `APPLE_MUSIC_DEVELOPER_TOKEN` 环境变量未设置时，不访问真实认证服务或外部网络。

### 括号回退测试真实覆盖 (R2)

- 在 `tests/test_web_api.py` 的 `test_search_track_bracket_fallback` 中：
  1. 将 mock 搜索函数精确定义为：首轮 `Unmatched Main 晴天` 返回 `no_hits`，次轮 `晴天` 返回包含预期 ID `10002` 的 `ok`；
  2. 严格断言调用次数正好为 2 次，且调用参数顺序严格匹配 `[call("Unmatched Main 晴天", "cn", 8), call("晴天", "cn", 8)]`；
  3. 断言响应结果来自第二阶段的候选；
  4. 进行了反向验证：当人为阻断回退逻辑（如 mock `re.findall` 返回空列表）时，测试立即断言失败（`AssertionError: 0 != 1`），证明回退分支被真实执行且不可或缺。

### 版本统一

- `applemusic/__init__.py` 中的 `__version__` 更新为 `"2.0.4"`。
- `applemusic/web/app.py` 改为 `app = FastAPI(title="Apple Music Playlist Importer", version=__version__)`。
- 在 `tests/test_web_api.py` 中添加了断言，确保 `applemusic.__version__ == "2.0.4"` 且 `app.version == applemusic.__version__`。

### pykakasi 依赖

- 在 `requirements.txt` 中添加了 `pykakasi>=2.3.0,<3.0.0`。
- 跨语种匹配器代码（`applemusic/matcher/cleaner.py:197`）实际使用 `try...except Exception` 进行容错降级，本轮保持原有容错降级逻辑不变。

### 缓存接口语义

- 保持调用 `client.persistent_cache.clear_expired()` 的核心逻辑不变（不误删有效缓存）。
- 增加了函数 docstring：`"""清理过期缓存（保留有效缓存）。"""`。
- 在返回 JSON 中增加 `"message": "已清理过期缓存"`。

## 4. 测试与验证

| 命令 / 测试项 | 结果 | 备注 |
| --- | --- | --- |
| `python -m compileall -q applemusic run.py exe_entry.py build_exe.py` | 通过 | 静态编译无语法或编译错误，退出码 0 |
| `tests.test_batch_isrc.TestBatchISRCAndMultiIndexCache.test_batch_isrc_chunking_and_caching` | 通过 | 在进程环境无 `APPLE_MUSIC_DEVELOPER_TOKEN` 下执行，耗时 2.14s，1/1 通过 |
| `tests.test_web_api.TestWebApi.test_search_track_bracket_fallback` | 通过 | 验证精确两阶段回退与调用序列，反向阻断验证通过 |
| `python -m unittest tests.test_web_api -v` | 通过 | 覆盖版本一致性、Token 鉴权、正则清洗、两阶段回退、缓存清理，5/5 通过 |
| 审计包装器全套 101 项测试 | 通过 | 101/101 全部通过，耗时 55.99s，退出码 0（详见下方隔离说明） |
| `git diff --check` | 通过 | 无空白错误或冲突标记，退出码 0 |
| `git status --short` | 通过 | 仅 4 个修改文件与 2 个未跟踪项（`.codex-workflow/` 与 `tests/test_web_api.py`），无二进制或数据文件污染 |

### 审计包装器施加的额外隔离说明

全套 101 项测试执行使用了内存审计包装器（`run_audit.py`），施加了以下独立隔离与观测措施：
1. **环境变量重定向**：在测试进程中重定向 `USERPROFILE` 和 `HOME` 到独立临时目录，并在子进程中移除 `APPLE_MUSIC_DEVELOPER_TOKEN`。
2. **外部 HTTP 阻断与统计**：拦截 `requests.Session.send`，如遇未 mock 的外部 HTTP 请求立即抛出异常并记录。在全套 101 项测试运行中，**实际拦截到外部 HTTP 发送尝试 0 次**。
3. **外部进程启动拦截**：拦截 `subprocess.Popen` 与 `os.startfile`，避免测试拉起宿主机真实资源管理器。在全套 101 项测试中，**拦截到外部进程启动 1 次**（来自既有测试 `test_open_local_folder_api` 对 `explorer.exe` 的调用）。

## 5. 遗留问题与风险

- **本次修改范围**：本次加固任务针对的 Web 模块运行时错误、ISRC 夹具缓存隔离、括号回退测试真实性、版本一致性与 pykakasi 依赖均已完全解决，无遗留风险。
- **完整测试套件中历史既有测试的已知限制**（非本次任务改动引入，建议后续立项优化）：
  1. `tests/test_library_manager.py:test_open_local_folder_api` 仅 mock 了 `os.startfile`，未 mock `subprocess.Popen`，在未加外部包装器时会直接调用 `explorer.exe`。
  2. 完整套件中其他未修改的历史测试夹具（如 `tests/test_cache.py`、`tests/test_library_manager.py` 等）仍存在直接使用或默认构造缓存单例的情况，在 Windows 环境下 SQLite 文件句柄可能保持打开至进程退出。

## 6. Codex 验收重点

1. **`tests/test_batch_isrc.py` (R1)**：
   - 检查 `setUp` 中 patch `PersistentCache.get_instance` 的位置（必须在 `AppleMusicClient` 与 `MatchingEngine` 实例化之前）。
   - 检查 `addCleanup` 是否完整覆盖了临时目录、缓存连接与 patch。
2. **`tests/test_web_api.py` (R2)**：
   - 检查 `test_search_track_bracket_fallback` 中的两阶段 mock 实现与 `assert_has_calls` 调用序列断言。
3. **工作区状态**：
   - 确认未执行 commit、push、tag 或 release。
