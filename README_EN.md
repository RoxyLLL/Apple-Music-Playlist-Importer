<div align="center">

# 🎵 Apple Music Playlist Importer

### The Most Powerful and Elegant Cross-Platform Playlist Migrator & Library Manager for Apple Music

**English | [简体中文](README.md)**

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

[📥 Download Latest Release (Standalone EXE)](https://github.com/RoxyLLL/Apple-Music-Playlist-Importer/releases/latest) · [✨ Key Features](#-key-features) · [🚀 3-Step Quickstart](#-3-step-quickstart) · [🛠️ Build from Source](#-development--build-from-source) · [❓ FAQ](#-faq)

</div>

---

> 💡 **No Python installation or complex environment setup required!**  
> Simply download the standalone `AppleMusicImporter.exe` from [Releases](https://github.com/RoxyLLL/Apple-Music-Playlist-Importer/releases/latest) and double-click to run.

---

## 🌟 Why Choose Apple Music Playlist Importer?

Migrating playlists from platforms like NetEase Cloud Music, QQ Music, or Spotify to Apple Music often leads to common frustrations:

- ❌ **Missing niche songs, Live concerts, and ACG anime tracks** not present in Apple Music catalog;
- ❌ **Cross-lingual mismatches** (Japanese Romaji vs. Kana, mixed bilingual subtitles causing wrong matches);
- ❌ **Homonym song false positives**, where original hits are incorrectly replaced by covers or instrumental tracks;
- ❌ **Expensive developer accounts required** by other tools, or painful manual packet sniffing for auth tokens;
- ❌ **Unordered imported tracks**, making newly added songs difficult to find, and Apple’s official client capping batch views at 100 songs.

**Apple Music Playlist Importer is purpose-built to solve every one of these problems:**

| Feature Comparison | Traditional Importers / Plugins | Apple Music Playlist Importer (v2.0.7) |
| :--- | :--- | :--- |
| **Uncataloged / Unavailable Songs** | ❌ Lost completely, resulting in broken playlists | ✅ **One-click automatic audio retrieval from Bilibili**, embedding HD album artwork and ID3 tags, saved locally and synced directly to **iCloud Music Library** |
| **Multilingual & ACG Matching** | ❌ Basic fuzzy text search only; fails on Kana, voice actors, and project credits | ✅ **Proprietary V6.1 Matching Engine** with Katakana phonetics, bilingual splitting, ACG project entities, CV & featured artist extraction, and Apple Equivalents cross-storefront discovery |
| **Homonym & Cover Protection** | ⚠️ High error rate (mismatched to covers, instrumentals, or remixes) | ✅ **Multi-dimensional scoring & strict safety guardrails**, distinguishing Live/Original/Covers with 30s official audio previews and version switching |
| **Ease of Use** | ❌ Requires Python, Node.js, Docker, or manual proxy sniffing | ✅ **Single-file portable Windows executable (EXE)** with one-click Edge browser automatic Token capture (no Apple Developer account needed) |
| **Cloud Library Management** | ❌ One-time import only | ✅ **Full Library Butler** (bypasses the 100-song cap for batch deletion, version switching, and sorted by newly added date) |
| **All-Device Roaming** | ⚠️ Stored locally on PC only | ✅ **Direct iCloud Music Library integration**, listening seamlessly across iPhone, iPad, Mac, and CarPlay |

---

## 🎯 Key Features

### 1. Instant Cross-Platform Migration
- Supports pasting playlist share links or share text from **NetEase Cloud Music**, **QQ Music**, and **Spotify**.
- Supports importing local **TXT / CSV** playlist files, parsing hundreds of tracks in seconds.

### 2. V6.1 Multilingual & Complex Credit Matching Engine 🌟
- **Katakana Phonetic Engine & Romaji Transliteration**: Native bidirectional phonetic matching for Japanese Hiragana, Katakana, Kanji, and Romaji (e.g., `すきだから` / `Sukidakara` / `好きだから`).
- **Bilingual Title Segmentation**: Automatically identifies and segments bilingual combined titles (e.g., `Nameless Faces / 问名无面`).
- **ACG Project & Publisher Recognition**: Deeply adapted for project entities like HoYoFair, BanG Dream!, LoveLive!, The Idolmaster, VTuber agencies, accurately extracting project brands, voice actors, and featured artists (`feat.`, `CV`, `Vocalist`).
- **Homonym False-Positive Guard & English Title Protection**: Prevents accidental false positives caused by similar track durations or cover songs, strictly enforcing safety boundary rules.
- **Apple Equivalents Cross-Storefront Discovery**: Discovers equivalent tracks across regional storefronts (CN, US, JP, etc.).
- **30-Second Official Audio Preview**: Listen to 30-second official audio snippets and freely switch versions before deciding.

### 3. Automatic Audio Gap-Filling from Bilibili (Flagship Feature 🌟)
- For songs not available on Apple Music due to licensing, click **"Fill with Bilibili"** to retrieve original or Live audio.
- Automatically extracts high-fidelity audio, downloads and embeds **HD album artwork, artist metadata, and standard ID3 tags**.
- Automatically copies to the Apple Music Auto-Add directory, syncing straight to **iCloud Music Library** for seamless playback across mobile, tablet, and desktop.

### 4. Full-Featured Apple Music Library Manager
- **Bypass the 100-Song Limit**: Smoothly view, multi-select, and batch remove tracks from large playlists with thousands of songs.
- **Playlist Management**: View and modify playlist names and descriptions; support single or multi-playlist batch deletion.
- **Date Added Sorting**: Default reverse chronological sort by **Date Added**, locating newly imported tracks in seconds, with ascending/descending toggles by title or artist.
- **Local Music Management**: Scan local audio files, reveal in file explorer, and manage audio metadata.

---

## 🚀 3-Step Quickstart

### Step 1: Paste Playlist Link & Parse

Launch the application, select your source platform (NetEase / QQ / Spotify / Local File), paste the playlist URL or share snippet, and click **"Start Parsing"**:

<p align="center">
  <img src="docs/images/step1-parse.png" alt="Step 1: Select platform and parse playlist" width="850">
</p>

---

### Step 2: Review Smart Matches & Auto Gap-Filling

The system matches songs concurrently against the Apple Music catalog within seconds:
- 🟢 **100% Exact / High Confidence**: Pre-selected by default;
- 🟡 **Needs Review**: Click the ▶️ button to play a 30s preview, or click "Switch Version" to pick an alternative;
- 🔴 **Uncataloged Songs**: Click **"Fill with Bilibili"** at the top to automatically fetch high-quality audio and add to your library!

<p align="center">
  <img src="docs/images/step2-match.png" alt="Step 2: Review smart matches and fill gaps" width="850">
</p>

---

### Step 3: One-Click Import to Apple Music Library

Verify selected tracks, enter a playlist name, and click **"Import to Apple Music"**. In moments, the playlist is created! Open the Apple Music app on your phone, and your new playlist will be ready.

<p align="center">
  <img src="docs/images/step3-import.png" alt="Step 3: One-click import to Apple Music" width="850">
</p>

---

## 💻 First-Time Setup: Connect Apple ID (Takes 30 Seconds)

Before your first import, authorize your Apple ID once (no Apple Developer account required; any standard subscriber account works):

1. Click **"Connect Apple ID"** in the top-right corner of the app;
2. Click **"Launch Edge & Auto-Capture Token"**;
3. The built-in Microsoft Edge browser opens Apple Music Web. Simply log in with your Apple ID or scan the QR code;
4. Upon successful login, the app **automatically captures the Music User Token in the background**, and displays "Connected".

> 🔒 **Privacy Guarantee**: All operations run purely locally on your machine (`127.0.0.1`). Credentials and tokens are stored solely in your local configuration and **are never uploaded to any external server**.

---

## 🛠️ Development & Build from Source

If you want to run from source code or contribute:

### Prerequisites
- Python 3.10+
- Windows 10 / 11 (64-bit)

### Run from Source
```powershell
# 1. Clone the repository
git clone https://github.com/RoxyLLL/Apple-Music-Playlist-Importer.git
cd Apple-Music-Playlist-Importer

# 2. Install dependencies
pip install -r requirements.txt

# 3. Launch GUI application
python run_gui.py
```

### Run Full Test Suite
```powershell
pytest -v
```

### Build Standalone Windows EXE
```powershell
pip install pyinstaller
python build_exe.py
```
Upon completion, the executable `AppleMusicImporter.exe` will be generated in both the project root and `dist/` directory.

---

## ❓ FAQ

<details>
<summary><b>Q1: Do I need to buy an Apple Developer account?</b></summary>
<br>
<b>No, absolutely not!</b> This project uses the official Apple Music web authorization flow. Any standard personal or family Apple Music subscription works out of the box.
</details>

<details>
<summary><b>Q2: Can I listen to songs filled from Bilibili on my iPhone or iPad?</b></summary>
<br>
<b>Yes!</b> When audio is retrieved and converted, it is automatically placed in Apple Music's "Automatically Add to Music" folder. As long as "Sync Library" (iCloud Music Library) is enabled in Apple Music or iTunes on your computer, the audio is uploaded to your personal iCloud storage and synced across all your Apple devices.
</details>

<details>
<summary><b>Q3: What should I do if I encounter a "Rate Limit" prompt when importing large playlists?</b></summary>
<br>
When fetching metadata for hundreds of tracks in rapid succession, Apple Music API may issue a temporary HTTP 429 rate limit. The software has built-in adaptive rate-limiting protection; simply wait for the countdown to complete, and it will resume automatically without manual intervention.
</details>

<details>
<summary><b>Q4: Why isn't the imported playlist showing up on my phone?</b></summary>
<br>
1. Verify that both your computer and phone are signed into the exact same Apple ID;<br>
2. On your iPhone, open <b>Settings > Music</b> and ensure <b>"Sync Library"</b> is switched ON;<br>
3. In the Apple Music app on your phone, pull down on the Library tab to trigger a manual refresh.
</details>

---

## 💖 Star & Support

If this tool made your music migration easier, please consider giving it a **⭐️ Star**!  
Your support is the greatest motivation for continuous maintenance and new features.

[![Star History Chart](https://api.star-history.com/svg?repos=RoxyLLL/Apple-Music-Playlist-Importer&type=Date)](https://star-history.com/#RoxyLLL/Apple-Music-Playlist-Importer&Date)

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).  
*Disclaimer: This tool is strictly intended for personal music library backup, learning, and research. All parsed music content and copyright belong to their respective platforms and record labels.*
