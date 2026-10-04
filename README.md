<div align="center">

# 🎵 Apple Music Playlist Importer

**[English](README_EN.md) | 简体中文**

<p align="center">
  <a href="https://github.com/RoxyLLL/Apple-Music-Playlist-Importer/releases">
    <img src="https://img.shields.io/github/v/release/RoxyLLL/Apple-Music-Playlist-Importer?color=ff2d55&style=for-the-badge&logo=apple&logoColor=white" alt="Latest Release">
  </a>
  <a href="https://github.com/RoxyLLL/Apple-Music-Playlist-Importer/releases">
    <img src="https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011%20(64--bit)-0078d7.svg?style=for-the-badge&logo=windows&logoColor=white" alt="Platform">
  </a>
  <a href="https://github.com/RoxyLLL/Apple-Music-Playlist-Importer/actions">
    <img src="https://img.shields.io/badge/Tests-256%20Passed-brightgreen.svg?style=for-the-badge&logo=pytest&logoColor=white" alt="Tests">
  </a>
  <a href="https://github.com/RoxyLLL/Apple-Music-Playlist-Importer/stargazers">
    <img src="https://img.shields.io/github/stars/RoxyLLL/Apple-Music-Playlist-Importer?style=for-the-badge&color=gold" alt="Stars">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-green.svg?style=for-the-badge" alt="License">
  </a>
</p>

[📥 立即下载最新版 (免安装 EXE)](https://github.com/RoxyLLL/Apple-Music-Playlist-Importer/releases/latest) · [✨ 核心功能](#-核心功能) · [🚀 3 步上手指南](#-3-步极速上手) · [❓ 常见问题](#-常见问题-faq)

</div>

---

> 💡 **无需安装 Python，无需配置复杂环境！**  
> 直接在 [Releases](https://github.com/RoxyLLL/Apple-Music-Playlist-Importer/releases/latest) 下载单个 `AppleMusicImporter.exe`，双击即可直接运行。

---

## 🌟 为什么选择它？

从网易云音乐、QQ 音乐、Spotify 等平台转到 Apple Music，你是否也遇到过这些痛点？

- ❌ **很多冷门歌曲、Live 现场版在 Apple Music 搜不到**，迁移后歌单缺斤少两；
- ❌ **同名歌曲防不胜防**，经常把原版误匹配成翻唱或伴奏；
- ❌ **很多工具必须购买昂贵的苹果开发者账号**，或需要复杂的抓包与配置；
- ❌ **歌单里的歌曲导入后乱序**，且官方客户端一次最多只能浏览或删除 100 首歌。

**Apple Music Playlist Importer 为解决这些痛点而生：**

| 功能对比 | 传统导入工具 / 插件 | Apple Music Playlist Importer |
| :--- | :--- | :--- |
| **未收录 / 灰色下架歌曲** | ❌ 直接丢失，歌单残缺不全 | ✅ **一键从 B 站检索高品质音源补漏**，自动嵌入封面与 ID3 标签，同步至 **iCloud 资料库** |
| **匹配准确率** | ⚠️ 容易错配翻唱 / 伴奏 / 假名混淆 | ✅ **智能多维打分**，严格区分 Live/翻唱/原版，支持 30 秒官方原声试听与自由换版本 |
| **使用门槛** | ❌ 需安装环境或手动抓包 | ✅ **Windows 单文件绿色 EXE**，Edge 浏览器一键秒级自动抓取 Token（无需开发者账号） |
| **云端资料库管理** | ❌ 仅支持一次性导歌 | ✅ **全功能资料库管家**（突破 100 首限制批量删歌、换版本、按最新添加时间排序） |
| **全设备漫游** | ⚠️ 仅保存在本机 | ✅ **直通 iCloud 音乐资料库**，iPhone、iPad、Mac、CarPlay 全端随心听 |

---

## 🎯 核心功能

- **跨平台歌单搬家**：支持网易云音乐、QQ 音乐、Spotify 分享链接与本地 TXT / CSV 文件快速解析导入。
- **智能高精度匹配**：自动识别歌曲与歌手，提供 30 秒官方原声在线试听与自由换版本功能，听完再决定，拒绝张冠李戴。
- **B 站音源无损补漏（核心特色 🌟）**：Apple Music 未收录或下架歌曲，一键从 B 站检索优质音源，自动补齐高清封面与元数据并同步至 iCloud。
- **资料库全功能管家**：突破 100 首限制，支持超大歌单全量查看与多选批量移除，支持按最新添加时间倒序排列。

---

## 🚀 3 步极速上手

### 第一步：粘贴歌单链接并解析

打开软件，选择来源平台（网易云/QQ/Spotify/本地文件），直接粘贴歌单链接或分享文本，点击 **“开始解析歌单”**：

<p align="center">
  <img src="docs/images/step1-parse.png" alt="步骤 1：选择来源平台并解析歌单" width="850">
</p>

---

### 第二步：智能匹配核对 & 一键 B 站补全

系统会在数秒内完成曲库并发匹配：
- 🟢 **100% 精确 / 高可信**：已为您默认勾选；
- 🟡 **待复核**：可点击 ▶️ 按钮在线试听 30 秒原声，或点击「换版本」手动挑选；
- 🔴 **未收录歌曲**：点击顶部的 **「从 B 站补全」**，系统自动寻找优质音源下载并放入 Apple Music 资料库！

<p align="center">
  <img src="docs/images/step2-match.png" alt="步骤 2：智能匹配核对与 B 站补全" width="850">
</p>

---

### 第三步：一键导入 Apple Music 个人资料库

确认勾选的歌曲，输入歌单名称，点击 **“导入到 Apple Music”**，稍等片刻即可同步完成！打开手机上的 Apple Music App，新歌单已经出现在您的资料库中。

<p align="center">
  <img src="docs/images/step3-import.png" alt="步骤 3：一键导入到 Apple Music" width="850">
</p>

---

## 💻 首次使用：连接 Apple ID（30 秒搞定）

在首次导入前，需要连接一次您的 Apple ID（无需开发者账号，普通订阅用户即可）：

1. 点击软件界面右上角的 **“连接 Apple ID”**；
2. 点击 **“启动 Edge 自动抓取 Token”**；
3. 软件会自动调起系统自带的 Edge 浏览器打开 Apple Music 网页版，您只需正常扫码或登录您的 Apple ID；
4. 登录成功后，软件将在**后台秒级自动捕获 Token**，客户端提示“已连接”即可开始使用。

> 🔒 **隐私安全承诺**：所有数据均仅在您的本地设备（`127.0.0.1`）运行，Token 仅保存在本地配置文件中，**绝不会上传到任何第三方服务器**。

---

## ❓ 常见问题 (FAQ)

<details>
<summary><b>Q1：我需要购买苹果开发者账号（Apple Developer）吗？</b></summary>
<br>
<b>完全不需要！</b> 本项目通过 Apple Music 官方网页端的安全授权机制，只要您的 Apple ID 拥有 Apple Music 个人或家庭订阅即可正常使用。
</details>

<details>
<summary><b>Q2：从 B 站补全的歌曲，手机（iPhone/iPad）上能听吗？</b></summary>
<br>
<b>可以！</b> 程序下载并转码音频后，会自动放入 Apple Music 的自动导入目录。只要您的电脑端 Apple Music 或 iTunes 开启了「iCloud 音乐资料库」，文件就会自动上传到苹果云端，手机端随之自动同步。
</details>

<details>
<summary><b>Q3：大歌单导入时提示“频控限制”怎么办？</b></summary>
<br>
当连续检索大量歌曲时，Apple Music 官方接口可能会返回 429 频控。软件内置了智能保护机制，只需等待几秒倒计时结束即可自动恢复并继续，无需手动干预。
</details>

<details>
<summary><b>Q4：为什么歌单导入后在手机上没看到？</b></summary>
<br>
1. 请确保手机和电脑登录的是同一个 Apple ID；<br>
2. 在手机「设置」->「音乐」中，确保开启了<b>「同步资料库」</b>（或「iCloud 音乐资料库」）；<br>
3. 在手机 Apple Music 资料库下拉刷新一下即可。
</details>

---

## 💖 支持与 Star

如果这个小工具帮您解决了歌单迁移的烦恼，欢迎给本项目点一个 **⭐️ Star**！  
您的支持是项目持续维护和迭代的最大动力！

[![Star History Chart](https://api.star-history.com/svg?repos=RoxyLLL/Apple-Music-Playlist-Importer&type=Date)](https://star-history.com/#RoxyLLL/Apple-Music-Playlist-Importer&Date)

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 开源发布。  
*免责声明：本项目仅供个人音乐备份与学习交流使用，解析内容版权均归各音乐平台及唱片公司所有。*
