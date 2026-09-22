# v2.0.4 基线加固实施计划

## 阶段 0：建立实现前基线

1. 运行 `git status --short --branch`，确认并记录现有用户改动。
2. 确认 `HEAD`；若与 `SPEC.md` 中的基线不同，记录差异但不要重置用户代码。
3. 运行目标失败测试，确认问题仍可复现。

## 阶段 1：修复 Web 模块运行时依赖

目标文件：`applemusic/web/app.py`

1. 在标准库导入区增加 `logging` 和 `re`。
2. 定义模块级 `logger = logging.getLogger(__name__)`。
3. 保持现有路由、响应结构和前端协议不变。
4. 检查 `/api/search-track` 中清洗查询和括号候选回退逻辑可以执行。

## 阶段 2：消除测试对真实认证和网络的依赖

目标文件：`tests/test_batch_isrc.py`，必要时增加独立 Web 回归测试文件。

1. 为 `TestBatchISRCAndMultiIndexCache` 使用的 `Config` 设置测试专用 Developer Token 和足够远的过期时间，或在具体测试中 mock `_get_auth_headers()`。
2. 不设置全局永久环境变量，不使用用户配置文件。
3. 保留对 60 个 ISRC 被切分为 `25 + 25 + 10` 的原始断言。
4. 为 `/api/search-track` 增加离线测试：
   - mock `get_shared_engine()` 返回虚拟客户端和引擎；
   - mock `search_catalog()` 返回 `CatalogSearchOutcome(kind="ok")`；
   - 请求包含括号或书名号，确保正则清洗路径被执行；
   - 带上本地应用 Token，避免绕过实际中间件；
   - 断言 HTTP 200、`success == true` 且结果结构正确。

## 阶段 3：统一版本来源

目标文件：

- `applemusic/__init__.py`
- `applemusic/web/app.py`
- 对应测试文件

步骤：

1. 将 `__version__` 更新为 `2.0.4`。
2. Web 应用导入 `__version__` 并作为 `FastAPI(..., version=__version__)` 的版本值。
3. 添加版本一致性断言。

## 阶段 4：补齐运行时依赖声明

目标文件：`requirements.txt`

1. 增加 `pykakasi>=2.3.0,<3.0.0`。
2. 不移除现有匹配器内的可选导入降级逻辑。
3. 不执行无必要的全量依赖升级。

## 阶段 5：澄清缓存清理语义

目标文件：`applemusic/web/app.py`，以及存在对应按钮时的 `applemusic/web/static/index.html`。

1. 保持“只删除过期记录”的行为不变。
2. 将函数文档、响应消息或前端文案明确为“清理过期缓存”。
3. 不增加清空全部有效缓存的行为。

## 阶段 6：验证

按顺序执行：

```powershell
python -m compileall -q applemusic run.py exe_entry.py build_exe.py
python -m unittest tests.test_batch_isrc.TestBatchISRCAndMultiIndexCache.test_batch_isrc_chunking_and_caching -v
python -m unittest discover -s tests -v
```

完整测试前应在当前测试进程环境中确保 `APPLE_MUSIC_DEVELOPER_TOKEN` 未设置，以验证隔离性；不得修改用户的持久环境变量。

如果本机已安装 PyInstaller，可额外运行一次构建烟雾测试；构建不是本任务通过的强制条件，但若运行，必须在 `RESULT.md` 记录结果与产物位置。

## 阶段 7：交付

1. 检查 `git diff --check`。
2. 检查 `git status --short`，不得纳入缓存数据库、Token、音频、EXE、`build/` 或 `dist/`。
3. 填写 `RESULT.md`。
4. 停止，不执行提交、推送、打 Tag 或发布。

