import os
import re
import time
import html
import shutil
import hashlib
import logging
import math
import urllib.parse
from typing import List, Dict, Optional, Tuple
import requests

try:
    from mutagen.mp4 import MP4, MP4Cover
except ImportError:
    MP4 = None
    MP4Cover = None

logger = logging.getLogger(__name__)

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52
]


class BilibiliClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.bilibili.com",
            "Origin": "https://www.bilibili.com",
        })
        self._mixin_key = None
        self._key_fetch_time = 0

    def _ensure_session(self):
        # Refresh session cookies if needed
        if "buvid3" not in self.session.cookies:
            try:
                spi_resp = self.session.get("https://api.bilibili.com/x/frontend/finger/spi", timeout=6).json()
                b_3 = spi_resp.get("data", {}).get("b_3")
                b_4 = spi_resp.get("data", {}).get("b_4")
                if b_3:
                    self.session.cookies.set("buvid3", b_3, domain=".bilibili.com")
                if b_4:
                    self.session.cookies.set("buvid4", b_4, domain=".bilibili.com")
            except Exception as e:
                logger.warning("Bilibili init SPI cookies error: %s", e)
            try:
                self.session.get("https://www.bilibili.com", timeout=6)
            except Exception as e:
                logger.warning("Bilibili init cookies error: %s", e)

    def _get_mixin_key(self) -> str:
        now = time.time()
        if self._mixin_key and (now - self._key_fetch_time < 3600):
            return self._mixin_key

        self._ensure_session()
        try:
            resp = self.session.get("https://api.bilibili.com/x/web-interface/nav", timeout=8).json()
            wbi_img = resp.get("data", {}).get("wbi_img", {})
            img_url = wbi_img.get("img_url", "")
            sub_url = wbi_img.get("sub_url", "")
            img_key = img_url.rsplit("/", 1)[1].split(".")[0]
            sub_key = sub_url.rsplit("/", 1)[1].split(".")[0]
            raw_key = img_key + sub_key
            self._mixin_key = "".join([raw_key[i] for i in MIXIN_KEY_ENC_TAB])[:32]
            self._key_fetch_time = now
            return self._mixin_key
        except Exception as e:
            logger.warning("Failed to fetch Bilibili WBI key: %s", e)
            return "ea1db124c3d70e7da341e300a86f16dd"

    def _sign_wbi(self, params: dict) -> dict:
        mixin_key = self._get_mixin_key()
        curr_time = round(time.time())
        new_params = dict(params)
        new_params["wts"] = curr_time
        filtered = {k: "".join(filter(lambda c: c not in "!'()*", str(v))) for k, v in new_params.items()}
        sorted_items = sorted(filtered.items())
        query = urllib.parse.urlencode(sorted_items)
        w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
        new_params["w_rid"] = w_rid
        return new_params


_client = BilibiliClient()


def get_apple_music_auto_add_dir() -> Optional[str]:
    """Detect Windows Apple Music or iTunes 'Automatically Add to Music' directory."""
    userprofile = os.environ.get("USERPROFILE", "")
    candidates = [
        os.path.join(userprofile, "Music", "Apple Music", "Media", "Automatically Add to Apple Music"),
        os.path.join(userprofile, "Music", "Apple Music", "Music", "Media", "Automatically Add to Music"),
        os.path.join(userprofile, "Music", "iTunes", "iTunes Media", "Automatically Add to iTunes"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def get_backup_download_dir() -> str:
    """Local storage directory for downloaded Bilibili audio files."""
    userprofile = os.environ.get("USERPROFILE", "")
    folder = os.path.join(userprofile, "Music", "AppleMusic_Bilibili_Downloads")
    os.makedirs(folder, exist_ok=True)
    return folder


def sanitize_filename(name: str) -> str:
    """Strip illegal characters for Windows filenames."""
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", name)
    return cleaned.strip()[:80]


def find_existing_bilibili_audio(title: str, artist: str = "") -> Optional[str]:
    """
    Search local directories (Apple Music official media library, backup download folder, Apple Music auto-add folder)
    for an already downloaded audio file corresponding to title & artist.
    """
    from applemusic.matcher.cleaner import TextCleaner

    clean_t = TextCleaner.clean_title(title).lower().strip()
    norm_t = TextCleaner.normalize(title).lower().strip()
    if not clean_t and not norm_t:
        return None

    try:
        clean_a = TextCleaner.clean_artist(artist).lower().strip() if artist else ""
    except Exception:
        clean_a = artist.lower().strip() if artist else ""

    # Priority 1: Search Apple Music official media library (where imported files live)
    try:
        from applemusic.extractors.local_manager import get_apple_music_library_media_dir
        am_media_dir = get_apple_music_library_media_dir()
        if am_media_dir and os.path.isdir(am_media_dir):
            for root, _, filenames in os.walk(am_media_dir):
                for fname in filenames:
                    if fname.lower().endswith((".m4a", ".mp3", ".flac", ".wav", ".aac")):
                        fname_norm = TextCleaner.normalize(fname).lower()
                        if (clean_t and clean_t in fname_norm) or (norm_t and norm_t in fname_norm):
                            if clean_a:
                                rel_path = os.path.relpath(os.path.join(root, fname), am_media_dir).lower()
                                if clean_a not in rel_path and clean_a not in fname_norm:
                                    continue
                            file_path = os.path.join(root, fname)
                            if os.path.getsize(file_path) > 102400:
                                return file_path
    except Exception as e:
        logger.warning("扫描 Apple Music 媒体库异常: %s", e)

    # Priority 2: Backup download directory & auto-add directory
    dirs_to_check = [get_backup_download_dir()]
    auto_dir = get_apple_music_auto_add_dir()
    if auto_dir and os.path.isdir(auto_dir):
        dirs_to_check.append(auto_dir)

    for d in dirs_to_check:
        if not os.path.isdir(d):
            continue
        try:
            for entry in os.scandir(d):
                if entry.is_file() and entry.name.lower().endswith((".m4a", ".mp3")):
                    fname = entry.name
                    fname_norm = TextCleaner.normalize(fname).lower()
                    if (clean_t and clean_t in fname_norm) or (norm_t and norm_t in fname_norm):
                        if clean_a and clean_a not in fname_norm:
                            stem_lower = os.path.splitext(fname)[0].lower()
                            if clean_t and clean_t not in stem_lower:
                                continue
                        if entry.stat().st_size > 102400:
                            return entry.path
        except Exception as e:
            logger.warning("扫描本地音源目录异常 (%s): %s", d, e)

    return None


def ensure_auto_imported(file_path: str, target_title: str, target_artist: str) -> bool:
    """Ensure a local audio file is copied to Apple Music auto-add folder."""
    auto_dir = get_apple_music_auto_add_dir()
    if not auto_dir or not os.path.isdir(auto_dir):
        return False
    try:
        from applemusic.extractors.local_manager import get_apple_music_library_media_dir
        am_media_dir = get_apple_music_library_media_dir()
        if am_media_dir and os.path.isdir(am_media_dir):
            try:
                # If file_path is already inside Apple Music's media library, no need to copy
                if os.path.commonpath([os.path.abspath(file_path), os.path.abspath(am_media_dir)]) == os.path.abspath(am_media_dir):
                    return True
            except Exception:
                pass

        safe_title = sanitize_filename(target_title or "未知曲目")
        safe_artist = sanitize_filename(target_artist or "未知歌手")
        dest_filename = f"{safe_artist} - {safe_title}.m4a"
        dest_path = os.path.join(auto_dir, dest_filename)
        if os.path.normpath(file_path) != os.path.normpath(dest_path):
            if not os.path.exists(dest_path):
                shutil.copy2(file_path, dest_path)
                logger.info("已将现有本地音频复制到 Apple Music 导入目录: %s", dest_path)
        return True
    except Exception as e:
        logger.warning("复制到 Apple Music 导入目录异常: %s", e)
        return False



def parse_duration_to_sec(duration_str: str) -> int:
    """Convert mm:ss or hh:mm:ss to total seconds."""
    try:
        parts = [int(p) for p in duration_str.strip().split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        elif len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
    except Exception:
        pass
    return 0


def extract_audio_from_video(video_path: str, output_audio_path: str):
    """
    Extract or transcode audio track from any video file (MP4, FLV, MKV) to high-quality M4A using PyAV.
    """
    import av
    input_container = av.open(video_path)
    audio_stream = next((s for s in input_container.streams if s.type == "audio"), None)
    if not audio_stream:
        input_container.close()
        raise RuntimeError("下载的视频中未检测到可用音频轨")

    output_container = av.open(output_audio_path, mode="w", format="mp4")
    
    # If audio is already AAC, remux directly without re-encoding (instant and lossless)
    if audio_stream.codec_context.name == "aac":
        out_stream = output_container.add_stream_from_template(audio_stream)
        for packet in input_container.demux(audio_stream):
            if packet.dts is None:
                continue
            packet.stream = out_stream
            output_container.mux(packet)
    else:
        # Transcode other codecs (mp3, vorbis, opus, etc.) to 192k AAC
        sample_rate = audio_stream.rate or 44100
        out_stream = output_container.add_stream("aac", rate=sample_rate)
        out_stream.bit_rate = 192000
        for frame in input_container.decode(audio_stream):
            for packet in out_stream.encode(frame):
                output_container.mux(packet)
        for packet in out_stream.encode():
            output_container.mux(packet)

    output_container.close()
    input_container.close()


def parse_play_count(val) -> int:
    """Safely convert play count (e.g. 17489489, '12.5万', '1.2亿') into integer."""
    if isinstance(val, (int, float)):
        return int(val)
    if not val or not isinstance(val, str):
        return 0
    val = val.strip()
    try:
        if val.endswith("万"):
            return int(float(val[:-1]) * 10000)
        elif val.endswith("亿"):
            return int(float(val[:-1]) * 100000000)
        return int(float(val))
    except Exception:
        return 0


def format_play_count(num: int) -> str:
    """Format play count into human-friendly string (e.g. 1748.9万, 5.2万)."""
    if num >= 100000000:
        return f"{num / 100000000:.1f}亿"
    elif num >= 10000:
        return f"{num / 10000:.1f}万"
    return str(num)


OFFICIAL_LABELS = {
    "杰威尔", "索尼音乐", "环球音乐", "华纳音乐", "滚石唱片", "相信音乐", "福茂唱片", "太合音乐",
    "时代峰峻", "哇唧唧哇", "乐华娱乐", "摩登天空", "英皇娱乐", "寰亚音乐", "金牌大风",
    "qq音乐", "网易云音乐", "咪咕音乐", "酷狗音乐", "官方", "official", "universal music", "sony music", "warner music"
}

NOISE_KEYWORDS = [
    "教学", "吉他教学", "钢琴谱", "伴奏", "吉他谱", "简谱", "纯伴奏", "消音",
    "反应", "reaction", "解说", "恶搞", "鬼畜", "全员恶人", "吐槽", "盘点",
    "1小时", "单曲循环", "连播", "合集", "串烧", "直播录屏", "录播", "切片",
    "游戏解说", "不法者", "钢琴教学", "卡林巴", "尤克里里教学", "指弹教学",
    "居然是", "背后故事", "幕后故事", "创作故事", "的故事", "破防了", "背后原因", "原来是",
    "深度解析", "深度解说", "有多恐怖", "有多感人", "有多难听", "有多震撼", "听完想投币"
]

COVER_KEYWORDS = [
    "cover", "翻唱", "女声版", "男声版", "翻唱版", "女版", "男版", "变声", "降调", "升调", "夹子音", "ai翻唱"
]

QUALITY_KEYWORDS = [
    "mv", "官方", "原版", "原唱", "完整版", "无损", "hi-res", "杜比", "4k", "1080p", "超清", "首播", "正式版"
]


def score_candidate(cand: dict, target_title: str, target_artist: str) -> float:
    """
    Score Bilibili candidate.
    Prioritizes matching: Video title matches song name AND (UP host is artist/official OR title has artist).
    Categorizes into 4 strictly separated Tiers:
       - Tier 1 (1,000,000): Title + Artist/UP Match (Clean)
       - Tier 2 (  200,000): Title Match, Clean (Artist absent/unknown)
       - Tier 3 (   30,000): Title Match, but is Cover / Remix / Non-original version
       - Tier 4 (    1,000): Noise / Parody / Commentary / Missing Title
    Within the same Tier, ranks by logarithmic play count + quality bonuses.
    """
    c_title = cand.get("title", "").lower()
    c_author = cand.get("author", "").lower().strip()
    play = cand.get("play", 0)
    dur_sec = cand.get("duration_sec", 0)

    t_title = target_title.lower().strip()
    t_artist = target_artist.lower().strip() if target_artist else ""

    clean_t = re.sub(r"[^\w\u4e00-\u9fa5]", "", t_title)
    clean_c = re.sub(r"[^\w\u4e00-\u9fa5]", "", c_title)
    clean_a = re.sub(r"[^\w\u4e00-\u9fa5]", "", t_artist) if t_artist else ""
    clean_author = re.sub(r"[^\w\u4e00-\u9fa5]", "", c_author)

    title_matched = bool(clean_t and clean_t in clean_c)

    artist_matched = False
    up_is_artist = False
    up_is_official = False

    if clean_a:
        if clean_a in clean_author or clean_author in clean_a:
            artist_matched = True
            up_is_artist = True
        elif any(label in c_author for label in OFFICIAL_LABELS) or c_author.endswith("官方") or c_author.endswith("工作室"):
            up_is_official = True
            artist_matched = True

        if not artist_matched:
            first_a = re.sub(r"[^\w\u4e00-\u9fa5]", "", t_artist.split()[0])
            if first_a and first_a in clean_c:
                artist_matched = True

    is_noise = any(kw in c_title for kw in NOISE_KEYWORDS)
    is_cover = any(kw in c_title for kw in COVER_KEYWORDS)

    if is_noise or not title_matched:
        tier = 4
        tier_name = "Tier 4 (杂音/未命中)"
        base_score = 1000
    elif is_cover:
        tier = 3
        tier_name = "Tier 3 (翻唱/改编版)"
        base_score = 30000
    elif title_matched and artist_matched:
        tier = 1
        tier_name = "Tier 1 (歌名+歌手/UP主精准匹配)"
        base_score = 1000000
    elif title_matched:
        tier = 2
        tier_name = "Tier 2 (歌名精准匹配)"
        base_score = 200000
    else:
        tier = 4
        tier_name = "Tier 4 (未知)"
        base_score = 1000

    play_bonus = math.log10(max(play, 1) + 10) * 10000

    match_bonus = 0
    if up_is_artist:
        match_bonus += 50000
    elif up_is_official:
        match_bonus += 30000

    if clean_a and clean_a in clean_c:
        match_bonus += 20000

    for kw in QUALITY_KEYWORDS:
        if kw in c_title:
            match_bonus += 5000

    dur_bonus = 0
    if 120 <= dur_sec <= 360:
        dur_bonus += 10000
    elif dur_sec > 0 and dur_sec < 60:
        dur_bonus -= 30000
    elif dur_sec > 600:
        dur_bonus -= 30000

    final_score = float(base_score + play_bonus + match_bonus + dur_bonus)
    cand["tier"] = tier
    cand["up_is_artist"] = up_is_artist
    cand["up_is_official"] = up_is_official
    cand["match_reason"] = f"{tier_name} | UP主是作者: {up_is_artist} | 官方认证: {up_is_official} | 播放: {format_play_count(play)}"
    return final_score


def search_bilibili(title: str, artist: str = "", limit: int = 8) -> List[Dict]:
    """
    Search Bilibili for video candidates.
    Prioritizes matching: title and artist / UP host must match first before considering views.
    Uses multi-order search (totalrank for semantic relevance, click for popular items),
    filters noise/tutorials/commentary, and scores with tiered priority.
    """
    # 1. Clean title and artist
    clean_t = re.sub(r"[《》「」『』\"“”'‘’]", " ", title)
    prev = None
    while prev != clean_t:
        prev = clean_t
        clean_t = re.sub(r"[\(（【\[][^()（）【\]]*[\)）】\]]", " ", clean_t)
    clean_t = re.sub(r"\s+", " ", clean_t).strip()
    clean_t = re.sub(r"\s*[-–—/]\s*$", "", clean_t).strip()
    if not clean_t:
        clean_t = title.strip()

    clean_a = re.sub(r"[/\\,，、&]", " ", artist).strip() if artist else ""
    first_artist = clean_a.split()[0] if clean_a else ""

    queries = []
    if first_artist:
        queries.append((f"{first_artist} {clean_t}".strip(), "totalrank"))
        queries.append((f"{clean_t} {first_artist}".strip(), "click"))
    queries.append((clean_t, "totalrank"))
    queries.append((clean_t, "click"))

    seen_bvids = set()
    all_raw = []

    for query_str, order_mode in queries:
        raw_params = {
            "keyword": query_str,
            "search_type": "video",
            "page": 1,
            "order": order_mode,
            "duration": 1,
        }
        try:
            signed = _client._sign_wbi(raw_params)
            url = "https://api.bilibili.com/x/web-interface/wbi/search/type"
            resp = _client.session.get(url, params=signed, timeout=10)
            data = resp.json()
            if data.get("code") != 0:
                continue

            results = data.get("data", {}).get("result", [])
            if not results:
                continue

            for item in results:
                bvid = item.get("bvid", "")
                if not bvid or bvid in seen_bvids:
                    continue

                raw_title = item.get("title", "")
                clean_item_title = re.sub(r"<[^>]+>", "", raw_title)
                clean_item_title = html.unescape(clean_item_title).strip()

                duration_str = item.get("duration", "0:0")
                dur_sec = parse_duration_to_sec(duration_str)

                # Filter out abnormal durations (< 20s or > 15 min)
                if dur_sec > 0 and (dur_sec < 20 or dur_sec > 900):
                    continue

                pic = item.get("pic", "")
                if pic.startswith("//"):
                    pic = "https:" + pic

                play_cnt = parse_play_count(item.get("play", 0))

                seen_bvids.add(bvid)
                cand = {
                    "bvid": bvid,
                    "title": clean_item_title,
                    "author": item.get("author", ""),
                    "pic": pic,
                    "duration": duration_str,
                    "duration_sec": dur_sec,
                    "play": play_cnt,
                    "play_str": format_play_count(play_cnt),
                    "arcurl": item.get("arcurl", f"https://www.bilibili.com/video/{bvid}"),
                }
                cand["score"] = score_candidate(cand, clean_t, first_artist)
                all_raw.append(cand)

            # Early exit if we already have sufficient Tier 1 candidates
            tier1_count = sum(1 for c in all_raw if c.get("tier") == 1)
            if tier1_count >= 4:
                break
        except Exception as e:
            logger.error("Error searching Bilibili for '%s': %s", query_str, e)

    if not all_raw:
        return []

    # Score and rank candidates: Title & Artist/UP matching first, then views
    for cand in all_raw:
        if "score" not in cand:
            cand["score"] = score_candidate(cand, clean_t, first_artist)

    all_raw.sort(key=lambda x: x["score"], reverse=True)
    if all_raw:
        all_raw[0]["is_best_match"] = True

    return all_raw[:limit]


def download_bilibili_audio(
    bvid: str,
    target_title: str,
    target_artist: str,
    target_album: str = "",
    cover_url: str = "",
    auto_import_to_apple_music: bool = True
) -> Dict:
    """
    Download video or audio from Bilibili, transcode/remux to M4A, tag with Mutagen,
    and automatically copy to Apple Music's auto-add folder.
    """
    try:
        backup_dir = get_backup_download_dir()
        safe_title = sanitize_filename(target_title or "未知曲目")
        safe_artist = sanitize_filename(target_artist or "未知歌手")
        safe_bvid = sanitize_filename(bvid or "unknown")
        filename = f"{safe_artist} - {safe_title} [{safe_bvid}].m4a"
        local_path = os.path.join(backup_dir, filename)

        # 0. Local cache fast reuse: If valid M4A for this specific bvid already exists, reuse it immediately (0.01s)
        if os.path.exists(local_path) and os.path.getsize(local_path) > 102400:
            logger.info("本地已存在该 B 站视频音频 (%s)，直接秒级复用: %s", bvid, local_path)
            auto_imported = False
            auto_dest_path = None
            auto_dir = get_apple_music_auto_add_dir()
            if auto_import_to_apple_music and auto_dir and os.path.isdir(auto_dir):
                try:
                    dest_filename = f"{safe_artist} - {safe_title}.m4a"
                    auto_dest_path = os.path.join(auto_dir, dest_filename)
                    shutil.copy2(local_path, auto_dest_path)
                    auto_imported = True
                    logger.info("复用音频已放入 Apple Music 自动导入目录: %s", auto_dest_path)
                except Exception as copy_err:
                    logger.warning("复用复制到 Apple Music 自动导入目录失败: %s", copy_err)
            return {
                "success": True,
                "bvid": bvid,
                "filename": filename,
                "local_path": local_path,
                "auto_imported": auto_imported,
                "auto_dir": auto_dir,
                "auto_dest_path": auto_dest_path,
                "title": target_title,
                "artist": target_artist,
                "reused": True,
            }

        # 1. Fetch video cid
        view_url = "https://api.bilibili.com/x/web-interface/view"
        view_resp = _client.session.get(view_url, params={"bvid": bvid}, timeout=10)
        view_data = view_resp.json()
        if view_data.get("code") != 0:
            raise RuntimeError(f"获取视频信息失败: {view_data.get('message')}")

        cid = view_data["data"]["cid"]
        video_pic = view_data["data"].get("pic", "")
        if video_pic.startswith("//"):
            video_pic = "https:" + video_pic

        # 2. Priority 1: High-speed DASH audio stream extraction (Only ~3MB instead of 40MB+ video)
        # fnval=4048 enables DASH + Dolby + FLAC audio streams
        play_url = "https://api.bilibili.com/x/player/playurl"
        downloaded_audio = False

        try:
            play_params_dash = {"bvid": bvid, "cid": cid, "fnval": 4048, "qn": 64}
            dash_resp = _client.session.get(play_url, params=play_params_dash, timeout=10)
            dash_json = dash_resp.json()
            dash_data = dash_json.get("data", {}).get("dash", {})

            # Gather all candidate audio streams
            candidate_streams = []
            # Check FLAC lossless
            flac_obj = dash_data.get("flac", {})
            if flac_obj and flac_obj.get("audio"):
                candidate_streams.append(flac_obj["audio"])
            # Check Dolby
            dolby_obj = dash_data.get("dolby", {})
            if dolby_obj and dolby_obj.get("audio"):
                candidate_streams.extend(dolby_obj["audio"])
            # Standard DASH audios (e.g. 192k AAC, 132k AAC)
            normal_audios = dash_data.get("audio", [])
            candidate_streams.extend(normal_audios)

            if candidate_streams:
                # Sort by bandwidth descending (highest bitrate first)
                candidate_streams.sort(key=lambda x: x.get("bandwidth", 0), reverse=True)
                best_audio = candidate_streams[0]
                audio_url = (
                    best_audio.get("baseUrl")
                    or best_audio.get("base_url")
                    or (best_audio.get("backupUrl") and best_audio["backupUrl"][0])
                )
                if audio_url:
                    logger.info("🚀 极速下载 B 站 DASH 高品质纯音频轨 (约2~4MB): %s", bvid)
                    temp_raw_audio = local_path + ".temp_raw.m4a"
                    try:
                        stream_resp = _client.session.get(audio_url, stream=True, timeout=20)
                        stream_resp.raise_for_status()
                        with open(temp_raw_audio, "wb") as f:
                            for chunk in stream_resp.iter_content(chunk_size=262144):
                                if chunk:
                                    f.write(chunk)

                        # Remux pure audio into standards-compliant Apple M4A container (0.02s)
                        extract_audio_from_video(temp_raw_audio, local_path)
                        downloaded_audio = True
                    except Exception as dash_dl_err:
                        logger.warning("DASH 音频下载或封装异常，将尝试备用流: %s", dash_dl_err)
                    finally:
                        if os.path.exists(temp_raw_audio):
                            try:
                                os.remove(temp_raw_audio)
                            except Exception:
                                pass
        except Exception as dash_err:
            logger.warning("请求 DASH 流信息异常，尝试传统视频流: %s", dash_err)

        # 3. Fallback: Progressive video download (fnval=0) if DASH audio was not available
        if not downloaded_audio:
            logger.info("回退到标准视频流并提取音频: %s", bvid)
            play_params_video = {"bvid": bvid, "cid": cid, "fnval": 0, "qn": 64}
            play_resp = _client.session.get(play_url, params=play_params_video, timeout=10)
            play_data = play_resp.json()
            durl = play_data.get("data", {}).get("durl") or []
            if durl and len(durl) > 0 and durl[0].get("url"):
                video_url = durl[0]["url"]
                temp_video_path = local_path + ".temp_cache.mp4"
                try:
                    v_resp = _client.session.get(video_url, stream=True, timeout=60)
                    v_resp.raise_for_status()
                    with open(temp_video_path, "wb") as f:
                        for chunk in v_resp.iter_content(chunk_size=262144):
                            if chunk:
                                f.write(chunk)
                    extract_audio_from_video(temp_video_path, local_path)
                    downloaded_audio = True
                except Exception as v_err:
                    logger.warning("下载或提取视频音轨失败: %s", v_err)
                finally:
                    if os.path.exists(temp_video_path):
                        try:
                            os.remove(temp_video_path)
                        except Exception:
                            pass

        if not os.path.exists(local_path) or os.path.getsize(local_path) == 0:
            raise RuntimeError("未在 B 站视频中成功提取到可用音频内容")

        # 4. Fetch cover image
        cover_data = None
        img_url = cover_url or video_pic
        if img_url:
            try:
                img_resp = _client.session.get(img_url, timeout=8)
                if img_resp.status_code == 200:
                    cover_data = img_resp.content
            except Exception as e:
                logger.warning("下载封面失败: %s", e)

        # 5. Inject ID3 / MP4 metadata
        if MP4 is not None:
            try:
                tags = MP4(local_path)
                tags["\xa9nam"] = target_title
                tags["\xa9ART"] = target_artist
                tags["\xa9alb"] = target_album or "Bilibili 音源补全"
                tags["\xa9cmt"] = f"Bilibili: {bvid}"

                if cover_data and MP4Cover is not None:
                    img_fmt = MP4Cover.FORMAT_PNG if cover_data.startswith(b"\x89PNG") else MP4Cover.FORMAT_JPEG
                    tags["covr"] = [MP4Cover(cover_data, imageformat=img_fmt)]

                tags.save()
            except Exception as tag_err:
                logger.warning("注入 MP4 元数据异常: %s", tag_err)

        # 6. Auto-import to Apple Music
        auto_imported = False
        auto_dest_path = None
        auto_dir = get_apple_music_auto_add_dir()
        if auto_import_to_apple_music and auto_dir and os.path.isdir(auto_dir):
            try:
                dest_filename = f"{safe_artist} - {safe_title}.m4a"
                auto_dest_path = os.path.join(auto_dir, dest_filename)
                shutil.copy2(local_path, auto_dest_path)
                auto_imported = True
                logger.info("已将音频成功放入 Apple Music 自动导入目录: %s", auto_dest_path)
            except Exception as copy_err:
                logger.warning("复制到 Apple Music 自动导入目录失败: %s", copy_err)

        return {
            "success": True,
            "bvid": bvid,
            "filename": filename,
            "local_path": local_path,
            "auto_imported": auto_imported,
            "auto_dir": auto_dir,
            "auto_dest_path": auto_dest_path,
            "title": target_title,
            "artist": target_artist,
        }

    except Exception as e:
        logger.error("下载 B 站音频失败 (%s): %s", bvid, e)
        return {
            "success": False,
            "error": str(e),
            "title": target_title,
            "artist": target_artist,
        }
