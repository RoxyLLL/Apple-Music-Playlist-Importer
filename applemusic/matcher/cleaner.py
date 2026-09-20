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
    def kana_to_romaji(text: str) -> str:
        """Convert Japanese Hiragana and Katakana to lowercase Hepburn Romaji."""
        if not text:
            return ""
        kana_map = {
            # Digraphs & Combinations (2 chars)
            "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo",
            "しゃ": "sha", "しゅ": "shu", "しょ": "sho",
            "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho",
            "にゃ": "nya", "にゅ": "nyu", "にょ": "nyo",
            "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo",
            "みゃ": "mya", "みゅ": "myu", "みょ": "myo",
            "りゃ": "rya", "りゅ": "ryu", "りょ": "ryo",
            "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo",
            "じゃ": "ja", "じゅ": "ju", "じょ": "jo",
            "びゃ": "bya", "びゅ": "byu", "びょ": "byo",
            "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo",
            "てぃ": "ti", "でぃ": "di", "ふぁ": "fa", "ふぃ": "fi",
            "ふぇ": "fe", "ふぉ": "fo", "うぃ": "wi", "うぇ": "we", "うぉ": "wo",
            "しぇ": "she", "じぇ": "je", "ちぇ": "che", "つぁ": "tsa", "でゅ": "dyu",

            # Monographs (1 char)
            "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
            "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
            "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
            "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
            "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
            "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
            "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
            "や": "ya", "ゆ": "yu", "よ": "yo",
            "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
            "わ": "wa", "を": "o", "ん": "n",
            "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
            "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
            "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
            "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
            "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
            "ゔ": "vu",
            "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o", "ゎ": "wa",
        }
        hira = []
        for c in text:
            code = ord(c)
            if 0x30A1 <= code <= 0x30F6:
                hira.append(chr(code - 0x60))
            elif c in ("ー", "〜", "～"):
                continue
            elif c == "ヴ":
                hira.append("ゔ")
            else:
                hira.append(c)
        s = "".join(hira)

        out = []
        i = 0
        n = len(s)
        while i < n:
            if i + 1 < n and s[i:i+2] in kana_map:
                out.append(kana_map[s[i:i+2]])
                i += 2
            elif s[i] in kana_map:
                out.append(kana_map[s[i]])
                i += 1
            elif s[i] == "っ" and i + 1 < n:
                nxt = kana_map.get(s[i+1], "")
                if nxt:
                    out.append(nxt[0])
                i += 1
            else:
                out.append(s[i])
                i += 1
        return "".join(out)

    @staticmethod
    def japanese_to_romaji(text: str) -> str:
        """
        Convert Japanese text (Kanji + Kana) to lowercase Hepburn Romaji.
        Uses curated Kanji compound mappings first, then converts remaining Kana.
        """
        if not text:
            return ""
        from applemusic.matcher.title_aliases import KANJI_TO_ROMAJI_COMPOUNDS
        s = text
        for kanji, romaji in KANJI_TO_ROMAJI_COMPOUNDS:
            if kanji in s:
                s = s.replace(kanji, f" {romaji} ")

        romaji_s = TextCleaner.kana_to_romaji(s)
        romaji_s = re.sub(r"\s+", " ", romaji_s).strip().lower()
        return romaji_s

    @staticmethod
    def get_japanese_romaji_variants(text: str) -> List[str]:
        """
        Generate all plausible Romaji transliterations for Japanese text,
        accounting for Kana, morphological analysis (pykakasi), curated compounds,
        and On'yomi/Kun'yomi multi-readings for Kanji.
        """
        if not text:
            return []

        import itertools
        from typing import Set
        variants: Set[str] = set()

        # 1. Base curated compound replacement + Kana conversion
        base = TextCleaner.japanese_to_romaji(text)
        if base:
            clean_b = re.sub(r"\s+", " ", base).strip().lower()
            if clean_b:
                variants.add(clean_b)
                variants.add(re.sub(r"[^\w]", "", clean_b))

        # 2. pykakasi conversion (if available)
        try:
            import pykakasi
            k = pykakasi.kakasi()
            res = k.convert(text)
            if res:
                hep = " ".join(item.get("hepburn", "") for item in res if item.get("hepburn")).strip().lower()
                if hep:
                    clean_hep = re.sub(r"\s+", " ", hep).strip().lower()
                    variants.add(clean_hep)
                    variants.add(re.sub(r"[^\w]", "", clean_hep))
        except Exception:
            pass

        # 3. On'yomi and Kun'yomi multi-reading combinations
        from applemusic.matcher.title_aliases import KANJI_MULTI_READINGS
        c_text = TextCleaner.clean_title(text)
        kanji_in_text = [ch for ch in c_text if ch in KANJI_MULTI_READINGS]
        if kanji_in_text and len(kanji_in_text) <= 4:
            slots = []
            for ch in c_text:
                if ch in KANJI_MULTI_READINGS:
                    slots.append(KANJI_MULTI_READINGS[ch])
                elif re.search(r"[\u3040-\u30ff]", ch):
                    slots.append([TextCleaner.kana_to_romaji(ch)])
                elif ch.isspace():
                    slots.append([" "])
                elif re.match(r"[a-zA-Z0-9]", ch):
                    slots.append([ch.lower()])
                else:
                    slots.append([""])

            total_combos = 1
            for s in slots:
                total_combos *= len(s)
                if total_combos > 32:
                    break

            if total_combos <= 32:
                for combo in itertools.product(*slots):
                    raw = "".join(combo)
                    clean_v = re.sub(r"\s+", " ", raw).strip().lower()
                    clean_np = re.sub(r"[^\w]", "", raw).lower()
                    if clean_v:
                        variants.add(clean_v)
                    if clean_np:
                        variants.add(clean_np)

        return [v for v in variants if v]

    @staticmethod
    def normalize(text: str) -> str:
        """Normalize unicode, convert full-width characters, strip spaces, convert Traditional to Simplified Chinese."""
        if not text:
            return ""
        text = unicodedata.normalize("NFKC", text)
        # Convert non-breaking space
        text = text.replace("\xa0", " ").replace("\u3000", " ")
        # Convert Traditional to Simplified Chinese
        from applemusic.matcher.zh_table import to_simplified
        text = to_simplified(text)
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
    def extract_title_variants(cls, title: str) -> List[str]:
        """
        Extract title and any bracketed or dash-separated alternate titles.
        Useful for cross-lingual matches like '打上花火 (Uchiage Hanabi)' or
        '夜に駆ける - Racing into the Night'.
        """
        if not title:
            return []
        variants = [cls.clean_title(title)]

        # Check bracketed segments
        bracket_pattern = r"[\(（【\[]([^()（）【\]]*)[\)）】\]]"
        for m in re.findall(bracket_pattern, title):
            m_clean = m.strip()
            # Ignore if it's purely a version tag
            is_version = False
            for keywords in VERSION_MAP.values():
                if any(kw in m_clean.lower() for kw in keywords):
                    is_version = True
                    break
            if not is_version and len(m_clean) >= 2:
                variants.append(m_clean)

        # Check dash / slash separators: e.g. "Song - English Title"
        parts = re.split(r"\s*[-–—/]\s*", title)
        if len(parts) > 1:
            for p in parts:
                p_clean = cls.clean_title(p)
                if p_clean and p_clean not in variants and len(p_clean) >= 2:
                    variants.append(p_clean)

        return [v for v in variants if v]

    @classmethod
    def clean_artist(cls, artist: str) -> str:
        """Extract primary artist and clean delimiters."""
        if not artist:
            return ""
        pri, _ = cls.parse_artists([artist])
        return pri if pri else artist.strip()

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

            # Cross-lingual alias / Romaji variant
            from applemusic.matcher.artist_aliases import get_artist_aliases
            for alias in get_artist_aliases(primary_artist):
                if alias.lower() != primary_artist.lower():
                    add_q(2, f"{core_t} {alias}")
                    break
            romaji_artist = cls.japanese_to_romaji(primary_artist)
            if romaji_artist and romaji_artist != primary_artist.lower():
                add_q(2, f"{core_t} {romaji_artist}")

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
            from applemusic.matcher.artist_aliases import get_artist_aliases
            for alias in get_artist_aliases(primary_artist):
                if alias.lower() != primary_artist.lower():
                    add_q(2, f"{core_t} {alias}")
                    break
            romaji_artist = cls.japanese_to_romaji(primary_artist)
            if romaji_artist and romaji_artist != primary_artist.lower():
                add_q(2, f"{core_t} {romaji_artist}")

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
