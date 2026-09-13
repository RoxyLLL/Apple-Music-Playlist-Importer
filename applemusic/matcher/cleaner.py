"""
Title and artist normalization and cleaning for music matching.
"""

import re
import unicodedata
from typing import List, Tuple, Optional

# Patterns commonly appended to song titles on NetEase / QQ Music / Spotify
NOISE_PATTERNS = [
    r"\(live[^\)]*\)",
    r"\[live[^\]]*\]",
    r"\(现场[^\)]*\)",
    r"\[现场[^\]]*\]",
    r"\(伴奏[^\)]*\)",
    r"\[伴奏[^\]]*\]",
    r"\(instrumental[^\)]*\)",
    r"\[instrumental[^\]]*\]",
    r"\(remix[^\)]*\)",
    r"\[remix[^\]]*\]",
    r"\(feat[^\)]*\)",
    r"\[feat[^\]]*\]",
    r"\(ft\.[^\)]*\)",
    r"\(acoustic[^\)]*\)",
    r"\[acoustic[^\]]*\]",
    r"\(piano[^\)]*\)",
    r"\[piano[^\]]*\]",
    r"\(guitar[^\)]*\)",
    r"\[guitar[^\]]*\]",
    r"\(radio edit[^\)]*\)",
    r"\(deluxe[^\)]*\)",
    r"\(bonus track[^\)]*\)",
    r"\(remastered?[^\)]*\)",
    r"\[remastered?[^\)]*\)",
    r"\(20\d\d remastered?[^\)]*\)",
    r"\(19\d\d remastered?[^\)]*\)",
    r"\(version[^\)]*\)",
    r"【[^】]*】",
    r"（[^）]*）",
]


VERSION_MAP = {
    "live": ["live", "现场", "现场版", "演唱会", "live version", "live at"],
    "remix": ["remix", "混音", "club mix", "extended mix", "dj版"],
    "instrumental": ["instrumental", "伴奏", "伴奏版", "纯伴奏", "karaoke", "卡拉ok", "off vocal"],
    "acoustic": ["acoustic", "原声", "不插电", "acoustic version"],
    "demo": ["demo", "小样", "试听版"],
    "cover": ["cover", "翻唱"],
    "remaster": ["remaster", "remastered", "重制", "重制版", "version"],
    "piano": ["piano", "钢琴", "钢琴版"],
    "guitar": ["guitar", "吉他", "吉他版"],
    "orchestral": ["orchestral", "交响", "管弦乐", "symphonic"],
    "radio_edit": ["radio edit", "电台版"],
    "deluxe": ["deluxe", "bonus track", "豪华版"],
    "sped_up": ["sped up", "加速版"],
    "slowed": ["slowed", "慢速版"],
}

NOISE_ARTISTS = {
    "群星", "various artists", "v.a.", "v.a", "未知", "未知歌手", "unknown",
    "unknown artist", "合唱", "原声带", "ost", "网络歌手", "佚名", "null"
}

SUBTITLE_PATTERN = r"\s*[-–—/]\s*(?:电视剧|电影|网剧|网综|综艺|动漫|动画|游戏|广播剧|舞台剧|话剧|纪录片|短剧|OST|主题曲|插曲|片尾曲|片头曲|宣传曲|推广曲|概念曲|角色曲|原声带|插曲|同人曲|纯享版|精选版|完整版|高品质|无损|官方版|动态歌词|剪辑版|影视原声).*"


class TextCleaner:
    """Provides methods for cleaning, parsing, and normalizing song metadata."""

    @staticmethod
    def normalize(text: str) -> str:
        """Normalize unicode, convert full-width characters, strip spaces."""
        if not text:
            return ""
        text = unicodedata.normalize("NFKC", text)
        # Convert non-breaking space
        text = text.replace("\xa0", " ").replace("\u3000", " ")
        # Lowercase
        text = text.lower().strip()
        return text

    @classmethod
    def parse_title(cls, title: str) -> Tuple[str, List[str]]:
        """
        Extract core title and structured version tags (e.g. live, remix, instrumental).
        Returns (title_core, version_tags).
        """
        if not title:
            return "", []

        cleaned = title
        version_tags: List[str] = []

        # 1. Extract bracketed blocks: (...), [...], 【...】, （...）
        bracket_pattern = r"[\(（【\[]([^()（）【\]]*)[\)）】\]]"
        matches = re.findall(bracket_pattern, cleaned)
        for m in matches:
            norm_m = m.lower().strip()
            for v_tag, keywords in VERSION_MAP.items():
                for kw in keywords:
                    if kw in norm_m:
                        if v_tag not in version_tags:
                            version_tags.append(v_tag)
                        break

        # Strip all bracket blocks from the core title
        prev = None
        while prev != cleaned:
            prev = cleaned
            cleaned = re.sub(bracket_pattern, " ", cleaned)

        # 2. Check trailing dash phrases: e.g. "Song - Live", "Song - 伴奏"
        dash_match = re.search(r"\s*[-–—]\s*([^–—-]*)$", cleaned)
        if dash_match:
            suffix = dash_match.group(1).lower().strip()
            matched_dash = False
            for v_tag, keywords in VERSION_MAP.items():
                for kw in keywords:
                    if kw in suffix:
                        if v_tag not in version_tags:
                            version_tags.append(v_tag)
                        matched_dash = True
                        break
            if matched_dash:
                cleaned = cleaned[: dash_match.start()].strip()

        # Check explanatory subtitle phrases: e.g. "Song - 电视剧《...》插曲", "Song - 电影《...》主题曲"
        sub_match = re.search(SUBTITLE_PATTERN, cleaned, flags=re.IGNORECASE)
        if sub_match:
            cleaned = cleaned[: sub_match.start()].strip()

        # 3. Check standalone trailing keywords, e.g. "晴天 伴奏", "晴天 现场版"
        for v_tag, keywords in VERSION_MAP.items():
            for kw in keywords:
                pat = rf"\s+{re.escape(kw)}$"
                if re.search(pat, cleaned, flags=re.IGNORECASE):
                    if v_tag not in version_tags:
                        version_tags.append(v_tag)
                    cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE).strip()

        # 4. Strip book title marks and quotation marks without deleting the song name
        cleaned = re.sub(r"[《》「」『』\"“”'‘’]", " ", cleaned)

        # 5. Remove extra whitespace and trailing hyphens
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = re.sub(r"\s*[-–—/]\s*$", "", cleaned).strip()

        core = cleaned if cleaned else title.strip()
        return core, version_tags

    @classmethod
    def clean_title(cls, title: str) -> str:
        """Strip noise and return core title."""
        core, _ = cls.parse_title(title)
        return core

    @classmethod
    def parse_artists(cls, artists: List[str]) -> Tuple[str, List[str]]:
        """
        Extract primary artist and list of featured/collaborative artists.
        Splits delimiters such as 'feat.', 'ft.', '&', '/', '、', ','
        Filters out generic noise artists like '群星', 'Various Artists'.
        """
        if not artists:
            return "", []

        all_names: List[str] = []
        for raw in artists:
            if not raw:
                continue
            # Split on common collaborative delimiters
            parts = re.split(r"\s+(?:feat\.?|ft\.?|with)\s+|\s*[/\\&,，、]\s*", raw, flags=re.IGNORECASE)
            for p in parts:
                p_clean = p.strip()
                if p_clean and p_clean not in all_names:
                    all_names.append(p_clean)

        if not all_names:
            return "", []

        # Filter out noise artists
        valid_names = [n for n in all_names if n.lower().strip() not in NOISE_ARTISTS]
        if not valid_names:
            return "", []

        primary = valid_names[0]
        featured = valid_names[1:]
        return primary, featured

    @classmethod
    def generate_tiered_queries(
        cls,
        title: str,
        artists: List[str],
        album: Optional[str] = None,
        version_tags: Optional[List[str]] = None,
    ) -> List[Tuple[int, str]]:
        """
        Generate prioritized multi-tier search queries for Apple Music Catalog:
        - Tier 1 (Strict): core_title + primary_artist
        - Tier 2 (Combined / Collaborators): core_title + all artists
        - Tier 3 (Version-aware): core_title + version keyword (if version exists)
        - Tier 4 (Fallback): core_title alone
        Returns list of (tier_level, query_str).
        """
        core_t, tags = cls.parse_title(title)
        all_tags = list(dict.fromkeys((version_tags or []) + tags))
        primary_artist, featured = cls.parse_artists(artists)

        queries: List[Tuple[int, str]] = []
        seen = set()

        def add_q(tier: int, q_str: str):
            clean_q = re.sub(r"\s+", " ", q_str).strip()
            if clean_q and clean_q.lower() not in seen:
                seen.add(clean_q.lower())
                queries.append((tier, clean_q))

        # Tier 1: Strict core title + primary artist
        if primary_artist:
            add_q(1, f"{core_t} {primary_artist}")
            # If there is a version tag (like live or remix), also create a versioned Tier 1 query
            if all_tags:
                tag_label = all_tags[0]
                add_q(1, f"{core_t} {primary_artist} {tag_label}")

        # Tier 2: Collaborator variants
        if featured:
            add_q(2, f"{core_t} {primary_artist} {featured[0]}")
            add_q(2, f"{core_t} {' '.join(artists)}")

        # Tier 3: Version tag with core title
        if all_tags and not primary_artist:
            add_q(3, f"{core_t} {all_tags[0]}")

        # Tier 4: Core title alone (fallback)
        add_q(4, core_t)
        if title.strip() != core_t:
            add_q(4, title.strip())

        return queries

    @classmethod
    def generate_relaxed_queries(
        cls,
        title: str,
        artists: List[str],
        album: Optional[str] = None,
        version_tags: Optional[List[str]] = None,
    ) -> List[Tuple[int, str]]:
        """
        Generate deep relaxed / fallback queries specifically for retry / rematch.
        - Strips all parenthetical explanations, OST/theme song suffixes
        - Filters out noise artists (e.g. '群星', '未知歌手')
        - Creates minimal query variants (core title alone, punctuation-stripped, artist-isolated)
        """
        core_t, tags = cls.parse_title(title)
        primary_artist, _ = cls.parse_artists(artists)

        queries: List[Tuple[int, str]] = []
        seen = set()

        def add_q(tier: int, q_str: str):
            clean_q = re.sub(r"\s+", " ", q_str).strip()
            if clean_q and clean_q.lower() not in seen:
                seen.add(clean_q.lower())
                queries.append((tier, clean_q))

        # 1. Clean Core Title + Primary Artist
        if primary_artist:
            add_q(1, f"{core_t} {primary_artist}")

        # If core title contains ' - ' (e.g. 'Song - Artist' or 'Song - Fluff')
        if " - " in core_t:
            prefix_t = core_t.split(" - ", 1)[0].strip()
            if prefix_t and prefix_t.lower() != core_t.lower():
                if primary_artist:
                    add_q(1, f"{prefix_t} {primary_artist}")
                add_q(2, prefix_t)

        # 2. Clean Core Title alone (broadest catalog search)
        add_q(2, core_t)

        # 3. Punctuation-stripped Core Title + Primary Artist
        no_punct_title = re.sub(r"[^\w\s\u4e00-\u9fa5]", " ", core_t)
        no_punct_title = re.sub(r"\s+", " ", no_punct_title).strip()
        if no_punct_title and no_punct_title.lower() != core_t.lower():
            if primary_artist:
                add_q(3, f"{no_punct_title} {primary_artist}")
            add_q(3, no_punct_title)

        # 4. If album provided, try Core Title + Album
        if album:
            clean_alb, _ = cls.parse_title(album)
            if clean_alb:
                add_q(4, f"{core_t} {clean_alb}")

        return queries

    @classmethod
    def generate_search_queries(cls, title: str, artists: List[str]) -> List[str]:
        """Backward-compatible query generator."""
        tiered = cls.generate_tiered_queries(title, artists)
        return [q for _, q in tiered]
