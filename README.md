<div align="center">

# 🎵 Apple Music Playlist Importer

**跨平台智能歌单迁移工具 · 免付 $99 开发者年费 · 智能模糊重排引擎 · B 站无损补全 · 现代桌面 Web 仪表盘**

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com/)
[![Vue 3](https://img.shields.io/badge/Vue-3.x-emerald.svg)](https://vuejs.org/)
[![TailwindCSS](https://img.shields.io/badge/TailwindCSS-3.x-38bdf8.svg)](https://tailwindcss.com/)
[![PyInstaller](https://img.shields.io/badge/PyInstaller-OneFile-red.svg)](https://pyinstaller.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[English](./README_EN.md) · [简体中文](./README.md) · [功能特性](#-功能特性) · [极速上手](#-极速上手双击即用) · [源码运行](#-源码部署与开发) · [常见问题](#-常见问题-faq)

</div>

---

## 📖 项目简介

**Apple Music Playlist Importer** 是一款面向音乐爱好者的全功能歌单迁移与音乐库管理利器。能够无缝将**网易云音乐**、**QQ 音乐**、**Spotify** 或 **本地文本/CSV/JSON** 歌单迁移到 **Apple Music** 个人资料库中。

不仅如此，针对 Apple Music 官方曲库**部分小众音乐、网络神曲或无版权歌曲无法命中**的痛点，本项目首创了 **B 站无损音源检索与自动补全系统**：基于多维匹配度隔离算法，智能下载高码率音频并深度封装封面、歌词与 ID3 元数据，助您实现 100% 满血歌单迁移！

---

## ✨ 核心亮点

### 1. 🔑 免付 $99 开发者年费 · 全自动登录提取 Token
- 巧妙采用 Apple Music 网页端端点会话机制，普通 Apple ID 订阅用户无需申请 Apple Developer 开发者账号；
- **全自动授权抓取**：内置浏览器自动化握手协议，点击按钮即可唤起系统浏览器，登录 Apple ID 后**秒级自动捕获 `media-user-token` 与区域代码**，全程无感、零门槛！

### 2. 🎯 智能多维匹配打分引擎 (Smart Matcher 2.0)
- **版本语义识别与强隔离**：精确识别 `Live` 现场版、`Remix` 混音版、`Acoustic` 不插电版、`伴奏/Instrumental`、`Demo` 等版本修饰，**版本冲突强制扣除 40% 分数**，杜绝原版歌单被误替换为演唱会嘈杂版；
- **短歌名防误配与动态保护**：针对《晴天》《红豆》《吻》《爱》等单字、双字超短歌名，启用严格歌手集合匹配门槛，杜绝同名串歌；
- **时长非线性高斯衰减**：引入时长偏差平滑函数，避免数十分钟长音频与标准 3-4 分钟单曲混淆；
- **中英拼音繁简去噪**：自动归一化全角半角标点，过滤无用音质标签（如 `[FLAC]`、`[Hi-Res]`、`无损`）。

### 3. 🔄 二次深度重试与跨区曲库探测 (Rematch System)
- 针对首次未命中的冷门歌曲，提供一键 **“🔄 重新检索未收录”** 功能；
- **宽松降噪查询生成**：智能剥离影视冗余尾缀（如 `- 电视剧《...》插曲`、`- 电影《...》主题曲`、`【官方MV】`、`OST` 等），去除 `群星`、`未知歌手` 干扰词；
- **全球曲库一键探测**：支持在 **中国大陆区 (CN)**、**香港区 (HK)**、**台湾区 (TW)**、**美国区 (US)**、**日本区 (JP)** 等曲库间一键切换重试。

### 4. 📺 B 站精准视频检索与自动音频补全 (Bilibili Extractor)
- **四层绝对隔离排序算法（Tier Architecture）**：
  - **Tier 1 (1,000,000 基分)**：歌名精准匹配 **且**（UP 主是原唱作者/官方唱片厂牌 **或** 标题含歌手名）；
  - **Tier 2 (  200,000 基分)**：歌名匹配，未出现歌手名（但无杂音）；
  - **Tier 3 (   30,000 基分)**：歌名匹配，但属于翻唱（Cover/改编/女声版/吉他弹唱）；
  - **Tier 4 (    1,000 基分)**：杂音/恶搞/教程/解说/一小时循环；
  - **彻底解决传统按播放量搜索导致翻唱劣币驱逐良币的问题**！任何千万播放量的翻唱也绝不会越级压制官方原唱；
- **非线性对数播放量增益**：$PlayBonus = \log_{10}(play + 10) \times 10,000$，仅在同级原作者视频中起到热度辅助；
- **全自动高码率转码与元数据注入**：调用 PyAV / FFmpeg 提取无损音轨，自动下载高清封面图并写入 ID3v2.4 / MP4 标签（Title, Artist, Album, Year, Cover Art）；
- **本地资料库无缝导入**：提供一键打开下载目录与双击批量导入 iTunes / Apple Music 本地资料库向导。

### 5. 🖥️ 现代化极简 Web 仪表盘 (Apple Style UI)
- 基于 **Vue 3 + Tailwind CSS** 构建的高颜值 Apple 设计风格界面；
- 适配**浅色/深色主题**切换；
- 歌曲多版本即时试听播放器（支持在线原声试听 30 秒）；
- 人工介入核对面板、B 站音源多候选可视化选择器；
- 实时导入进度条与状态统计（完全精准、高可信度、待复核、未收录、B站补全）。

---

## 🚀 极速上手（双击即用）

本项目已预构建好 **Windows 单文件便携版客户端**：

1. 前往本仓库的 [Releases](../../releases) 页面，下载 **`AppleMusicImporter.exe`**；
2. 直接双击运行 **`AppleMusicImporter.exe`**；
3. 程序会自动拉起轻量桌面独立应用窗口（或访问 `http://127.0.0.1:8000`）；
4. 点击界面右上角 **设置 ⚙️** -> **“一键自动抓取 Token”** 登录您的 Apple ID，即可开始畅享歌单导入！

> **说明**：单文件版内部已完全静态打包 Python 运行时、FastAPI 核心、Web 前端资源与音频处理组件，**无需系统预装任何环境**！

---

## 🛠️ 源码部署与开发

若您希望在本地运行源码、二次开发或在 Linux / macOS 上部署，请按以下步骤操作：

### 1. 克隆代码仓库
```bash
git clone https://github.com/RoxyLLL/Apple-Music-Playlist-Importer.git
cd Apple-Music-Playlist-Importer
```

### 2. 创建并激活虚拟环境 (推荐)
```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. 安装依赖包
```bash
pip install -r requirements.txt
```

### 4. 启动 Web 可视化界面
```bash
python run.py web
```
启动后终端将显示服务地址，自动在浏览器中打开：
👉 **http://127.0.0.1:8000**

---

## 💻 命令行 CLI 模式使用指南

除了可视化 Web 界面，本项目还支持功能强悍的纯命令行 CLI 交互模式，适合脚本自动化或极客用户：

### 1. 交互式账号授权配置
```bash
# 启动浏览器自动化捕获 Token
python run.py login

# 或手动粘贴 Token 与修改配置
python run.py config
```

### 2. 导入网易云音乐歌单
```bash
python run.py sync "https://music.163.com/playlist?id=3778678"
```

### 3. 导入 QQ 音乐歌单
```bash
python run.py sync "https://y.qq.com/n/ryqq/playlist/3805603854"
```

### 4. 导入 Spotify 歌单
```bash
python run.py sync "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
```

### 5. 导入本地文本/CSV 文件
```bash
python run.py sync test_playlist.txt
```
> 本地文件支持两列格式（`歌名 - 歌手`）或标准 CSV（包含 `Title, Artist` 列）。

### 6. 常用高级参数
| 参数 | 缩写 | 说明 |
| :--- | :---: | :--- |
| `--storefront` | `-s` | 指定检索的 Apple Music 曲库区域（如 `cn`, `us`, `hk`, `tw`, `jp`） |
| `--name` | `-n` | 自定义导入 Apple Music 后的歌单名称 |
| `--auto-confirm` | `-y` | 自动采纳中等匹配度歌曲，不进行控制台交互中断 |
| `--unmatched` | `-u` | 指定未命中歌曲清单的导出文件路径（默认 `unmatched.txt`） |

### 7. 直接在终端检索 Apple Music 曲库
```bash
python run.py search "周杰伦 晴天" -s cn --limit 5
```

---

## 📦 打包为单文件 EXE (Windows)

如果您对前端或核心代码进行了二次开发，想要重新打包独立运行的 Windows EXE 程序：

```bash
python build_exe.py
```
打包脚本将自动清理缓存、打包静态资源并生成独立可执行文件：
`dist/AppleMusicImporter.exe` -> `AppleMusicImporter.exe`

---

## 📁 目录结构说明

```
applemusic/
├── applemusic/
│   ├── models.py              # 核心数据模型 (Track, AppleMusicTrack, ConfidenceLevel)
│   ├── config.py              # 用户配置管理与 ~/.applemusic_sync/ 持久化
│   ├── auth.py                # 自动提取公共 Developer Token 与 User Token 鉴权
│   ├── auto_token.py          # Edge/Chrome 自动化握手提取 media-user-token 协议
│   ├── client.py              # Apple Music 官方 REST API 封装 (搜索/建歌单/批量写入)
│   ├── cli.py                 # Rich 彩色终端交互式命令行
│   ├── extractors/            # 外部平台音源提取器
│   │   ├── base.py            # 提取器抽象基类与正则路由中心
│   │   ├── netease.py         # 网易云音乐 API 解析与全量曲目提取
│   │   ├── qqmusic.py         # QQ 音乐分享链接与 ID 解析
│   │   ├── spotify.py         # Spotify 公开歌单解析
│   │   ├── local_file.py      # 本地 TXT / CSV 文件解析器
│   │   └── bilibili_downloader.py # B 站四层隔离打分、音视频下载与元数据标签写入
│   ├── matcher/               # 智能模糊匹配引擎 2.0
│   │   ├── cleaner.py         # 标题降噪、版本语义识别、宽松查询生成
│   │   ├── scorer.py          # 相似度打分算法 (Levenshtein/版本惩罚/时长高斯衰减)
│   │   └── engine.py          # 并发检索调度、置信度分类与二次重试引擎
│   └── web/                   # 桌面 Web 界面后端与静态资源
│       ├── app.py             # FastAPI RESTful API 路由与异步任务
│       └── static/
│           └── index.html     # Vue 3 + Tailwind CSS 单页应用仪表盘
├── run.py                     # CLI 与 Web 统一快捷入口
├── build_exe.py               # PyInstaller 单文件自动化打包构建脚本
├── requirements.txt           # 项目 Python 依赖库清单
├── .gitignore                 # 严密过滤规则 (已隔离私人密钥与二进制文件)
└── README.md                  # 详细使用与技术文档
```

---

## ❓ 常见问题 (FAQ)

#### Q1：需要付费购买 Apple Developer 开发者账号吗？
**不需要！** 本项目通过官方网页端会话授权协议，任何拥有 Apple Music 个人/家庭订阅的普通 Apple ID 均可直接一键抓取 Token 并同步写入个人资料库。

#### Q2：Token 的有效期是多久？失效后怎么办？
Apple Music 的 `media-user-token` 通常有效期为数周到数月。如果页面提示 `Token 已过期` 或 `401 Unauthorized`，只需在 Web 界面点击右上角 **设置 ⚙️** -> **“一键自动抓取 Token”** 重新授权一次即可。

#### Q3：为什么有些歌曲在 Apple Music 里搜不到？
由于各地区版权限制，部分歌曲可能仅在港区 (HK)、台区 (TW) 或美区 (US) 上架：
1. 您可以在界面中点击 **“🔄 重新检索未收录”**，程序会自动剥离影视干扰词并在宽松阈值下重试；
2. 如果该歌曲确实在 Apple Music 全网下架，直接点击该行右侧的 **“📺 B站”** 按钮（或顶部“批量下载 B 站音源”），系统会自动匹配原唱高品质音源下载并注入标签，一键补齐！

#### Q4：B 站下载的音频如何同步到手机上的 Apple Music？
1. 在 Web 界面点击 **“📁 打开下载文件夹”**；
2. 打开电脑上的 **iTunes**（Windows）或 **音乐 App**（Mac）；
3. 将下载好的 `.m4a` / `.mp3` 文件直接拖拽进 iTunes 音乐资料库中；
4. 开启 **“云端音乐资料库 / 同步资料库”**，稍等片刻，iPhone / iPad 上的 Apple Music 将自动完成云同步！

---

## 📄 开源许可证

本项目采用 [MIT License](LICENSE) 开源许可证。

> **免责声明**：本项目仅供学习交流与个人音乐库备份整理使用。所解析之多媒体内容版权均归原平台及唱片公司所有。请勿将本项目用于任何商业用途。
