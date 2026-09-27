# 登录后资料库仍提示未授权：修复计划（交 Antigravity 实施）

## 现象与目标

用户完成 Apple ID 登录后，界面显示已连接，但进入“我的个人歌单”弹出 `获取歌单失败: 尚未授权 Apple ID`。目标是登录成功后立即可读取个人歌单和资料库歌曲；刷新页面或切换标签后状态仍一致。不要把“存在 token”当成“已通过 Apple Music 验证”。

## 已查到的链路与优先假设

1. `applemusic/auto_token.py` 的 `BrowserTokenCapturer._poll_for_token()` 验证 token 后写入 `config.json` 并报告 `is_authorized=True`。
2. `applemusic/web/app.py::get_shared_engine()` 只在 `_shared_client is None` 时读取配置并构建客户端；`/api/user/playlists` 等资料库接口却检查 `client.config.is_authorized()`。若登录前因检索等操作已建立客户端，它会一直持有旧的空 token，直接返回截图中的 401 文案。
3. 手动 `POST /api/config` 会清空 `_shared_client/_shared_engine`，但自动登录成功路径没有对应刷新。这是本次首要回归假设，须用失败测试先证明。
4. `/api/auto-login/status` 的空闲/兜底分支使用 `bool(config.media_user_token)` 宣称授权；前端轮询见 `is_authorized` 后没有等待 `fetchConfig()` 完成便关闭弹窗。两处可能造成“UI 绿灯、实际不可用”的假阳性/时序问题。

## 实施要求

### A. 先复现并定位

- 写无真实 Apple 凭证的回归测试：先构造无 token 的共享客户端，再模拟自动登录验证成功与配置持久化；断言 `/api/config`、`/api/auto-login/status` 和 `/api/user/playlists` 使用同一代凭证，不再在路由前置检查报“尚未授权”。
- 对照测试“登录前从未构建共享客户端”和“登录前已构建共享客户端”两种顺序。明确记录 401 来自本地前置检查、Apple 上游，还是本地 `X-App-Token` 门禁（后者应为 403），但日志不得输出完整 token、Cookie、请求头。
- 若测试表明实际原因不同，先记录证据，再按证据修复；不要假定截图证明 token 一定有效。

### B. 后端状态一致性

- 自动登录验证且保存成功后，使共享 `AppleMusicClient` 与 `MatchingEngine` 在下次请求前使用新配置。建议集中封装线程安全的“配置代际/指纹校验与重建”或统一失效入口，覆盖自动登录、手动保存、token 更换/清除和地区变更；避免仅在某一路由补一行全局变量赋值。
- 不要在有请求使用旧客户端时原地改其 token；必要时原子替换共享对象。指纹只用于内存比较，不记录原始 token，也不要沿用只取 token 前 24 位的方式识别不同凭证。
- 资料库读取/写入接口的前置检查必须基于当前有效配置，不得复用登录前空 token 的客户端。保持现有接口与授权保护，不新增跳过验证的后门。
- `/api/auto-login/status` 仅在成功校验过当前 token 时报告已授权；旧 token 存在、校验失败或网络校验不确定时返回可区分状态/信息，不得静默报成功。处理登录成功与前端第一次资料库请求并发时的状态一致性。

### C. 前端时序与错误提示

- `pollAutoLoginStatus()` 收到成功后 `await fetchConfig()`，确认 `/api/config` 返回授权成功再关闭登录弹窗、允许加载资料库；之后若当前就在资料库页，主动刷新当前子页数据。
- 手动 token 保存成功也重新获取完整 `/api/config` 状态，避免把仅有 `success/is_authorized/message` 的 `POST /api/config` 响应直接覆盖含 `storefront/version` 的完整 `config` 对象。
- 区分未登录、token 已失效、开发者 token/网络验证失败和本地请求被拒绝，避免统一提示“尚未授权”；失败后保留重试/重新连接入口，不清空已有歌单数据或用户 token。

## 验收矩阵

1. 首次自动登录：登录前共享客户端已创建且无 token；自动捕获成功后无需重启，资料库歌单/歌曲接口均通过本地授权检查并带新 `Music-User-Token`。
2. 首次自动登录：登录前没有共享客户端，结果相同。
3. 手动粘贴 token 与更新 token：保存后 UI、共享客户端和资料库读取使用同一新 token；刷新页面仍一致。
4. 旧 token 失效/被清除：不能显示“已授权”；资料库返回明确状态，不能沿用旧客户端或旧验证缓存。
5. 校验网络异常：不能被当成永久未登录或授权成功；可重试，诊断信息不泄露凭证。
6. 自动登录成功瞬间并发点击资料库、切换资料库标签及刷新：无时序性假 401、无旧客户端回写覆盖。
7. 保持本地 `X-App-Token` 防护和既有资料库接口行为；测试只用模拟 token/上游响应，不调用真实账号、不删除配置或用户数据。

交付：Antigravity 编写代码与测试、运行专项及全量测试、更新简洁 `RESULT.md`；Codex 再独立验收。不要提交、推送、发布或打包，除非用户另行要求。
