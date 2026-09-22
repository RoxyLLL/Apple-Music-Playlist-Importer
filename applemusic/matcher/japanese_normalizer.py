"""
Japanese script analysis, normalization, and Romanization variant generation.
Provides unified NFKC, Kana normalization, Hepburn transliteration via pykakasi,
and script profiling for multi-lingual catalog queries.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class ScriptProfile:
    has_hiragana: bool
    has_katakana: bool
    has_cjk: bool
    has_latin: bool
    japanese_likelihood: float  # 0.0 to 1.0 (controls query planning, not match score)
    ambiguous_cjk_only: bool    # True if CJK present but NO Kana (avoids treating Chinese as strong Japanese evidence)


@dataclass(frozen=True)
class TextVariant:
    text: str
    provenance: str            # "original", "nfkc", "kana_normalized", "hepburn", "hepburn_compact", "long_vowel_relaxed", "source_translation"
    verification_level: str    # "strong", "medium", "weak"


class JapaneseNormalizer:
    """
    Universal Japanese normalizer and script variant generator.
    Converts Japanese text into deterministic variants for catalog search.
    """

    # Regex for Japanese scripts
    HIRAGANA_RE = re.compile(r"[\u3040-\u309f]")
    KATAKANA_RE = re.compile(r"[\u30a0-\u30ff\u31f0-\u31ff\uff65-\uff9f]")
    CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
    LATIN_RE = re.compile(r"[a-zA-Z]")

    # Punctuation to normalize / remove
    JP_PUNCT_RE = re.compile(r"[・、。！？「」『』（）［］【】〜～―ー\s]+")

    _kakasi_instance = None

    @classmethod
    def _get_kakasi(cls):
        if cls._kakasi_instance is None:
            try:
                import pykakasi
                cls._kakasi_instance = pykakasi.kakasi()
            except ImportError:
                cls._kakasi_instance = False
        return cls._kakasi_instance if cls._kakasi_instance is not False else None

    @classmethod
    def analyze_script(cls, text: str) -> ScriptProfile:
        """Analyze the script composition of a given string."""
        if not text:
            return ScriptProfile(
                has_hiragana=False,
                has_katakana=False,
                has_cjk=False,
                has_latin=False,
                japanese_likelihood=0.0,
                ambiguous_cjk_only=False,
            )

        nfkc_text = unicodedata.normalize("NFKC", text)
        has_hira = bool(cls.HIRAGANA_RE.search(nfkc_text))
        has_kata = bool(cls.KATAKANA_RE.search(nfkc_text))
        has_cjk = bool(cls.CJK_RE.search(nfkc_text))
        has_lat = bool(cls.LATIN_RE.search(nfkc_text))

        # Determine Japanese likelihood
        if has_hira and has_kata:
            likelihood = 1.0
        elif has_hira:
            likelihood = 0.95
        elif has_kata:
            likelihood = 0.90
        elif has_cjk:
            # Pure CJK without Kana could be Chinese or Japanese
            likelihood = 0.35
        elif has_lat:
            likelihood = 0.10
        else:
            likelihood = 0.05

        ambiguous_cjk = has_cjk and not (has_hira or has_kata)

        return ScriptProfile(
            has_hiragana=has_hira,
            has_katakana=has_kata,
            has_cjk=has_cjk,
            has_latin=has_lat,
            japanese_likelihood=likelihood,
            ambiguous_cjk_only=ambiguous_cjk,
        )

    @classmethod
    def normalize_text(cls, text: str) -> str:
        """
        Standard NFKC normalization with Japanese-specific punctuation and whitespace cleaning.
        """
        if not text:
            return ""
        # 1. Unicode NFKC (converts full-width Latin/numbers/kana to standard)
        norm = unicodedata.normalize("NFKC", text)
        # 2. Replace Japanese full-width space and middle dot with standard space
        norm = norm.replace("\u3000", " ").replace("・", " ")
        # 3. Collapse multiple spaces
        norm = re.sub(r"\s+", " ", norm).strip()
        return norm

    @classmethod
    def kata_to_hira(cls, text: str) -> str:
        """Convert Katakana to Hiragana."""
        if not text:
            return ""
        out = []
        for c in text:
            code = ord(c)
            if 0x30A1 <= code <= 0x30F6:
                out.append(chr(code - 0x60))
            elif c == "ヴ":
                out.append("ゔ")
            else:
                out.append(c)
        return "".join(out)

    @classmethod
    def hira_to_kata(cls, text: str) -> str:
        """Convert Hiragana to Katakana."""
        if not text:
            return ""
        out = []
        for c in text:
            code = ord(c)
            if 0x3041 <= code <= 0x3096:
                out.append(chr(code + 0x60))
            elif c == "ゔ":
                out.append("ヴ")
            else:
                out.append(c)
        return "".join(out)

    @classmethod
    def extract_bracket_translations(cls, text: str) -> Tuple[str, Optional[str]]:
        """
        Extract bracket translations or alternate titles from text.
        e.g. "夜に駆ける (Racing into the Night)" -> ("夜に駆ける", "Racing into the Night")
        e.g. "Sparkle [スパークル]" -> ("Sparkle", "スパークル")
        Returns (clean_text, extracted_translation_or_none).
        """
        if not text:
            return "", None

        # Pattern for trailing or enclosed brackets with translation
        bracket_re = re.compile(r"[\(\[（【]([^\(\)\[\]（）【】]{2,})[\)\]）】]")
        match = bracket_re.search(text)
        if not match:
            return text.strip(), None

        inner = match.group(1).strip()
        # Ensure the bracket content isn't just noise like (Live), (Remix), etc.
        lower_inner = inner.lower()
        noise_words = {
            "live", "remix", "instrumental", "acoustic", "piano", "guitar",
            "orchestral", "demo", "cover", "remaster", "remastered", "version",
            "伴奏", "现场", "现场版", "纯伴奏", "off vocal", "karaoke"
        }
        if any(nw in lower_inner for nw in noise_words):
            return text.strip(), None

        clean_text = bracket_re.sub("", text).strip()
        # Clean up any leftover punctuation or whitespace
        clean_text = re.sub(r"\s+", " ", clean_text).strip()
        return clean_text, inner

    @classmethod
    def generate_variants(cls, text: str, is_artist: bool = False) -> List[TextVariant]:
        """
        Generate deterministic, ranked variants for a text field (title or artist).
        Caps output at maximum 4 variants per field.
        Deterministic order:
        1. original / nfkc
        2. source_translation (if bracket translation exists)
        3. hepburn (pykakasi Romanization)
        4. hepburn_compact or long_vowel_relaxed
        """
        if not text:
            return []

        raw = text.strip()
        nfkc = cls.normalize_text(raw)
        profile = cls.analyze_script(nfkc)

        variants: List[TextVariant] = []
        seen_texts: Set[str] = set()

        def add_variant(t: str, prov: str, vlevel: str):
            clean_t = t.strip()
            if not clean_t:
                return
            key = clean_t.lower()
            if key not in seen_texts:
                seen_texts.add(key)
                variants.append(TextVariant(text=clean_t, provenance=prov, verification_level=vlevel))

        # 1. Original / NFKC
        if raw == nfkc:
            add_variant(raw, "original", "strong")
        else:
            add_variant(raw, "original", "strong")
            add_variant(nfkc, "nfkc", "strong")

        # 2. Bracket translation (if present in source metadata)
        clean_base, bracket_trans = cls.extract_bracket_translations(raw)
        if bracket_trans:
            add_variant(bracket_trans, "source_translation", "medium")
            if clean_base != raw and clean_base != nfkc:
                add_variant(clean_base, "nfkc", "strong")

        # 3. Japanese script variants (Kana conversion & Romanization via pykakasi)
        if profile.has_hiragana or profile.has_katakana or profile.has_cjk:
            # Verification level is weak for pure CJK; medium for text with Kana
            romaji_vlevel = "weak" if profile.ambiguous_cjk_only else "medium"

            # Romanization via pykakasi
            kakasi = cls._get_kakasi()
            if kakasi:
                try:
                    # Convert clean base (without bracket noise)
                    target_to_convert = clean_base if bracket_trans else nfkc
                    res = kakasi.convert(target_to_convert)
                    if res:
                        hep_words = [item.get("hepburn", "") for item in res if item.get("hepburn")]
                        hep_text = " ".join(hep_words).strip()
                        if hep_text:
                            # 3a. Hepburn with spaces
                            add_variant(hep_text, "hepburn", romaji_vlevel)

                            # 3b. Long vowel relaxed (e.g. "ou" -> "o", "uu" -> "u")
                            relaxed = re.sub(r"([aeiou])\1+", r"\1", hep_text, flags=re.IGNORECASE)
                            relaxed = re.sub(r"ou\b", "o", relaxed, flags=re.IGNORECASE)
                            if relaxed.lower() != hep_text.lower():
                                add_variant(relaxed, "long_vowel_relaxed", "weak")

                            # 3c. Hepburn compact (no spaces or hyphens)
                            compact = re.sub(r"[\s\-_]+", "", hep_text)
                            if compact.lower() != hep_text.lower():
                                add_variant(compact, "hepburn_compact", "weak")
                except Exception:
                    pass

            # 3d. Kana normalization (Katakana <-> Hiragana)
            if profile.has_katakana and not profile.has_hiragana:
                hira_version = cls.kata_to_hira(nfkc)
                if hira_version and hira_version.lower() != nfkc.lower():
                    add_variant(hira_version, "kana_normalized", "medium")
            elif profile.has_hiragana and not profile.has_katakana:
                kata_version = cls.hira_to_kata(nfkc)
                if kata_version and kata_version.lower() != nfkc.lower():
                    add_variant(kata_version, "kana_normalized", "medium")

        # Enforce hard cap of 4 variants per field, deterministic ordering preserved
        return variants[:4]
