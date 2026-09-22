# v2.0.4 基线加固需求说明

## 1. 任务目标

在不改变现有业务功能和匹配策略的前提下，修复当前 `v2.0.4` 基线中已经确认的 Web 运行时错误、测试环境依赖、版本不一致和未声明依赖，使代码库具备稳定进入下一轮功能开发的条件。

## 2. 当前基线

- 分支：`main`
- 基线提交：`e8ae939228f68bdfc77bd81a8947ad17b54e2f19`
- Git 标签：`v2.0.4`
- 初始工作区：干净
- Python 静态编译：通过
- 初始测试：96 项，95 项通过、1 项失败
- 失败测试：`tests.test_batch_isrc.TestBatchISRCAndMultiIndexCache.test_batch_isrc_chunking_and_caching`

## 3. 必须解决的问题

### P0：Web 手动搜索运行时错误

`applemusic/web/app.py` 使用了 `re.sub`、`re.findall` 和 `logger.exception`，但没有导入 `re`、`logging`，也没有定义模块级 `logger`。

要求：

- `/api/search-track` 的成功路径不再出现 `NameError: re is not defined`。
- Web 后端异常记录路径不再出现 `NameError: logger is not defined`。
- 增加回归测试覆盖 `/api/search-track` 的成功路径。

### P0：测试依赖真实 Developer Token

`test_batch_isrc_chunking_and_caching` 没有隔离 Apple Music Developer Token，可能触发真实网络抓取并在无网络或受限环境中失败。

要求：

- 测试必须自行提供模拟 Developer Token 或 mock 认证头。
- 测试不得依赖用户目录中的真实配置、环境变量或 Apple 网站。
- 在未设置 `APPLE_MUSIC_DEVELOPER_TOKEN` 的环境中，完整测试套件必须通过。

### P1：版本号不一致

当前存在以下版本：

- Git 标签：`v2.0.4`
- `applemusic.__version__`：`2.0.3`
- FastAPI 元数据版本：`1.0.0`

要求：

- `applemusic.__version__` 更新为 `2.0.4`。
- FastAPI 应用版本从 `applemusic.__version__` 读取，不再单独硬编码。
- 添加轻量测试防止两者再次漂移。

### P1：`pykakasi` 未声明

匹配器会选择性导入 `pykakasi`，PyInstaller 脚本也会收集该包，但 `requirements.txt` 未声明它。

要求：

- 在 `requirements.txt` 中明确声明兼容的 `pykakasi` 2.x 版本范围。
- 不改变当前 `try/except` 降级行为，缺少可选组件时仍不能导致匹配器整体崩溃。

## 4. 建议解决的问题

### P2：缓存清理接口语义

`POST /api/cache/clear` 当前只调用 `clear_expired()`，名称容易被理解成清空全部缓存。

本任务采用保守方案：暂不改变删除行为，给 API 返回值和代码注释明确标注“仅清理过期缓存”。如修改前端文案，应同步为“清理过期缓存”。

### P2：异常可观测性

不得在本任务中大规模重写异常处理，但本次触及的代码不得新增无日志的宽泛异常吞噬。

## 5. 非目标

- 不重构约 4700 行的单文件 Vue 前端。
- 不拆分 `applemusic/web/app.py`。
- 不调整匹配分数、阈值、别名库或跨区搜索策略。
- 不修改 Apple Music、Spotify、网易云、QQ 音乐或 B站接口协议。
- 不执行真实 Apple ID 登录、真实云端写入、真实歌曲删除或真实 B站下载。
- 不创建新 Release、不推送 Tag、不提交或推送 Git，除非用户另行授权。

## 6. 验收标准

以下条件必须全部满足：

1. `python -m compileall -q applemusic run.py exe_entry.py build_exe.py` 返回 0。
2. 在显式移除 `APPLE_MUSIC_DEVELOPER_TOKEN` 后，`python -m unittest discover -s tests -v` 全部通过。
3. `/api/search-track` 回归测试通过，并且测试中不访问真实 Apple Music 网络。
4. `applemusic.__version__ == "2.0.4"`。
5. `applemusic.web.app.app.version == applemusic.__version__`。
6. `requirements.txt` 包含受约束的 `pykakasi` 2.x 依赖。
7. `git diff` 中不存在匹配算法、别名表、用户数据目录或生成二进制的无关改动。
8. Antigravity 在 `RESULT.md` 中记录改动文件、测试命令、测试结果和遗留风险。

## 7. 风险控制

- 不读取、打印或提交用户真实 Token。
- 不使用本机 `~/.applemusic_sync/config.json` 作为测试夹具。
- 测试必须使用临时目录、mock session 和虚拟凭据。
- 不修改或删除 `AppleMusicImporter.exe`、`build/`、`dist/` 中的本地产物。

