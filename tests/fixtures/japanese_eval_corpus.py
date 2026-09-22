"""
Synthetic benchmark evaluation corpus for Japanese universal matching.
Used to verify scoring boundaries, script variations, conflict detection, and state machine transitions
under controlled synthetic candidates and hard negative scenarios.

NOTE: This is a synthetic algorithmic benchmark, NOT live Apple Music API recording data.
Under NO circumstances should this file be imported or read by production runtime code.

Contains:
- 200 positive synthetic test cases across script buckets:
  Hiragana, Katakana, Pure Kanji, Mixed scripts, Latin artist names, Bracket translations, Localized titles.
- 100 hard negative synthetic test cases:
  Same title different artist, same artist similar title, Live/Instrumental/Remix/Cover/Acoustic conflicts,
  character songs, short titles, Chinese homoglyphic Hanzi.
- 30 cross-storefront synthetic cases (available in JP but unavailable in target storefront or mapped via equivalents).
"""

from dataclasses import dataclass, field
from typing import List, Optional

from applemusic.matcher.cleaner import TextCleaner
from applemusic.models import AppleMusicTrack


@dataclass
class EvalTrack:
    id: str
    title: str
    artists: List[str]
    album: Optional[str] = None
    isrc: Optional[str] = None
    duration_ms: Optional[int] = None
    bucket: str = "hiragana"
    expected_target_id: Optional[str] = None
    expected_jp_id: Optional[str] = None
    target_available: bool = True
    is_hard_negative: bool = False
    negative_reason: Optional[str] = None
    recorded_candidates: List[AppleMusicTrack] = field(default_factory=list)
    is_synthetic: bool = True


def _make_positive_eval_track(
    global_idx: int,
    tid: str,
    title: str,
    artists: List[str],
    album: Optional[str],
    duration_ms: Optional[int],
    bucket: str,
) -> EvalTrack:
    target_id = str(1600000000 + global_idx)
    clean_t = TextCleaner.clean_title(title)
    cand_title = clean_t if clean_t else title
    target_cand = AppleMusicTrack(
        id=target_id,
        title=cand_title,
        artists=artists,
        album=album,
        duration_ms=duration_ms,
        storefront="cn",
    )
    tid_int = int(target_id)
    distractor_cover = AppleMusicTrack(
        id=str(tid_int + 20000000),
        title=cand_title,
        artists=["カバー歌手" if "カバー" not in (artists[0] if artists else "") else "別の歌手"],
        album="カバー名曲集",
        duration_ms=(duration_ms or 240000) + 7000,
        storefront="cn",
    )
    distractor_inst = AppleMusicTrack(
        id=str(tid_int + 30000000),
        title=f"{cand_title} (Instrumental)",
        artists=artists,
        album=album,
        duration_ms=duration_ms,
        storefront="cn",
    )
    distractor_diff = AppleMusicTrack(
        id=str(tid_int + 40000000),
        title="別の曲",
        artists=artists,
        album=album,
        duration_ms=(duration_ms or 240000) - 25000,
        storefront="cn",
    )
    return EvalTrack(
        id=tid,
        title=title,
        artists=artists,
        album=album,
        duration_ms=duration_ms,
        bucket=bucket,
        expected_target_id=target_id,
        target_available=True,
        recorded_candidates=[distractor_cover, target_cand, distractor_inst, distractor_diff],
    )


def get_positive_eval_corpus() -> List[EvalTrack]:
    """200 positive Japanese tracks across script buckets."""
    corpus: List[EvalTrack] = []

    # Bucket 1: Hiragana (>= 25)
    hiragana_samples = [
        ("h01", "ひまわりの約束", ["秦基博"], "ひまわりの約束 - Single", 314000),
        ("h02", "奏 (かなで)", ["スキマスイッチ"], "奏(かなで) - Single", 328000),
        ("h03", "さくら", ["森山直太朗"], "さくら(独唱)", 294000),
        ("h04", "あじさい通り", ["スピッツ"], "ハチミツ", 312000),
        ("h05", "きらきら", ["もさを。"], "きらきら - Single", 240000),
        ("h06", "おもかげ", ["milet", "Aimer", "幾田りら"], "おもかげ - Single", 188000),
        ("h07", "うっせぇわ", ["Ado"], "狂言", 204000),
        ("h08", "なんでもないや", ["上白石萌音"], "chouchou", 345000),
        ("h09", "すずめ", ["十明"], "すずめの戸締まり", 238000),
        ("h10", "たぶん", ["YOASOBI"], "THE BOOK", 258000),
        ("h11", "あの夢をなぞって", ["YOASOBI"], "THE BOOK", 242000),
        ("h12", "ハルカ", ["YOASOBI"], "THE BOOK", 244000),
        ("h13", "アンコール", ["YOASOBI"], "THE BOOK", 271000),
        ("h14", "もしも命が描けたら", ["YOASOBI"], "THE BOOK 2", 202000),
        ("h15", "ツバメ", ["YOASOBI"], "THE BOOK 2", 218000),
        ("h16", "きらり", ["藤井風"], "HELP EVER HURT NEVER", 231000),
        ("h17", "まつり", ["藤井風"], "LASA", 225000),
        ("h18", "ガーデン", ["藤井風"], "LASA", 229000),
        ("h19", "死ぬのがいいわ", ["藤井風"], "HELP EVER HURT NEVER", 185000),
        ("h20", "旅路", ["藤井風"], "LASA", 277000),
        ("h21", "かくれんぼ", ["優里"], "壱", 280000),
        ("h22", "ピーターパン", ["優里"], "壱", 225000),
        ("h23", "シャッター", ["優里"], "壱", 274000),
        ("h24", "レオ", ["優里"], "壱", 273000),
        ("h25", "みかんハート", ["C&K"], "CK AND MORE...", 324000),
        ("h26", "茜さす", ["Aimer"], "daydream", 329000),
        ("h27", "カタオモイ", ["Aimer"], "daydream", 207000),
        ("h28", "ポラリス", ["Aimer"], "After Dark", 364000),
        ("h29", "春よ、来い", ["松任谷由実"], "THE DANCING SUN", 285000),
    ]
    global_idx = 0
    for tid, title, artists, album, dur in hiragana_samples:
        global_idx += 1
        corpus.append(_make_positive_eval_track(global_idx, tid, title, artists, album, dur, "hiragana"))

    # Bucket 2: Katakana (>= 25)
    katakana_samples = [
        ("k01", "スパークル", ["幾田りら"], "Sketch", 210000),
        ("k02", "ドライフラワー", ["優里"], "壱", 286000),
        ("k03", "ベテルギウス", ["優里"], "壱", 230000),
        ("k04", "マリーゴールド", ["あいみょん"], "瞬間的シックスセンス", 306000),
        ("k05", "ハルノヒ", ["あいみょん"], "おいしいパスタがあると聞いて", 326000),
        ("k06", "カブトムシ", ["aiko"], "桜の木の下", 314000),
        ("k07", "アイノカタチ", ["MISIA"], "Life is going on and on", 266000),
        ("k08", "プロミスザスター", ["BiSH"], "THE GUERRiLLA BiSH", 279000),
        ("k09", "オーケストラ", ["BiSH"], "KiLLER BiSH", 343000),
        ("k10", "シンデレラボーイ", ["Saucy Dog"], "レイジージャーニー", 298000),
        ("k11", "カタオモイ", ["Aimer"], "daydream", 207000),
        ("k12", "リフレインが叫んでる", ["松任谷由実"], "Delight Slight Light KISS", 258000),
        ("k13", "クリスマスソング", ["back number"], "シャンデリア", 340000),
        ("k14", "ヒロイン", ["back number"], "シャンデリア", 288000),
        ("k15", "ハッピーエンド", ["back number"], "アンコール", 315000),
        ("k16", "アイネクライネ", ["米津玄師"], "YANKEE", 289000),
        ("k17", "メトロノーム", ["米津玄師"], "Bremen", 259000),
        ("k18", "ピースサイン", ["米津玄師"], "BOOTLEG", 238000),
        ("k19", "パプリカ", ["米津玄師"], "STRAY SHEEP", 208000),
        ("k20", "カナデアイ", ["Amatsuki"], "スターライトキセキ", 220000),
        ("k21", "シルエット", ["KANA-BOON"], "TIME", 240000),
        ("k22", "バトンロード", ["KANA-BOON"], "NAMiDA", 282000),
        ("k23", "シュガーソングとビターステップ", ["UNISON SQUARE GARDEN"], "Dr.Izzy", 254000),
        ("k24", "オリオンをなぞる", ["UNISON SQUARE GARDEN"], "Populus Populus", 262000),
        ("k25", "ブルーバード", ["いきものがかり"], "My song Your song", 216000),
        ("k26", "ホタルノヒカリ", ["いきものがかり"], "ハジマリノウタ", 242000),
        ("k27", "サヨナラバス", ["ゆず"], "ゆずえん", 226000),
        ("k28", "スノースマイル", ["BUMP OF CHICKEN"], "ユグドラシル", 314000),
        ("k29", "チェリー", ["スピッツ"], "ハチミツ", 261000),
    ]
    for tid, title, artists, album, dur in katakana_samples:
        global_idx += 1
        corpus.append(_make_positive_eval_track(global_idx, tid, title, artists, album, dur, "katakana"))

    # Bucket 3: Pure Kanji (>= 25)
    kanji_samples = [
        ("kj01", "夜空", ["鈴木雅之"], "ALL TIME ROCK 'N' ROLL", 310000),
        ("kj02", "炎", ["LiSA"], "炎 - Single", 274000),
        ("kj03", "白日", ["King Gnu"], "CEREMONY", 275000),
        ("kj04", "一途", ["King Gnu"], "一途/逆夢 - Single", 191000),
        ("kj05", "逆夢", ["King Gnu"], "一途/逆夢 - Single", 307000),
        ("kj06", "雨燦々", ["King Gnu"], "雨燦々 - Single", 300000),
        ("kj07", "感電", ["米津玄師"], "STRAY SHEEP", 264000),
        ("kj08", "灰色と青", ["米津玄師", "菅田将暉"], "BOOTLEG", 332000),
        ("kj09", "春雷", ["米津玄師"], "BOOTLEG", 288000),
        ("kj10", "打上花火", ["DAOKO", "米津玄師"], "THANK YOU BLUE", 289000),
        ("kj11", "群青", ["YOASOBI"], "THE BOOK", 262000),
        ("kj12", "怪物", ["YOASOBI"], "THE BOOK 2", 206000),
        ("kj13", "三原色", ["YOASOBI"], "THE BOOK 2", 224000),
        ("kj14", "大正浪漫", ["YOASOBI"], "THE BOOK 2", 168000),
        ("kj15", "勇者", ["YOASOBI"], "THE BOOK 3", 194000),
        ("kj16", "宿命", ["Official髭男dism"], "Traveler", 280000),
        ("kj17", "点描の唄", ["井上苑子", "Mrs. GREEN APPLE"], "青と夏", 307000),
        ("kj18", "青と夏", ["Mrs. GREEN APPLE"], "Attitude", 271000),
        ("kj19", "僕のこと", ["Mrs. GREEN APPLE"], "Attitude", 322000),
        ("kj20", "春を告げる", ["yama"], "the meaning of life", 198000),
        ("kj21", "麻痺", ["yama"], "the meaning of life", 188000),
        ("kj22", "色彩", ["yama"], "色彩 - Single", 193000),
        ("kj23", "残響散歌", ["Aimer"], "Deep down", 184000),
        ("kj24", "朝が来る", ["Aimer"], "Deep down", 294000),
        ("kj25", "花束のかわりにメロディーを", ["清水翔太"], "PROUD", 310000),
        ("kj26", "糸", ["中島みゆき"], "EAST ASIA", 324000),
        ("kj27", "地上の星", ["中島みゆき"], "短篇集", 312000),
        ("kj28", "時代", ["中島みゆき"], "時代 - Single", 255000),
        ("kj29", "真夏の果実", ["サザンオールスターズ"], "稲村ジェーン", 278000),
    ]
    for tid, title, artists, album, dur in kanji_samples:
        global_idx += 1
        corpus.append(_make_positive_eval_track(global_idx, tid, title, artists, album, dur, "pure_kanji"))

    # Bucket 4: Mixed Scripts (>= 25)
    mixed_samples = [
        ("m01", "夜に駆ける", ["YOASOBI"], "THE BOOK", 261000),
        ("m02", "花に亡霊", ["ヨルシカ"], "盗作", 241000),
        ("m03", "春泥棒", ["ヨルシカ"], "創作", 290000),
        ("m04", "ただ君に晴れ", ["ヨルシカ"], "負け犬にアンコールはいらない", 198000),
        ("m05", "雨とカプチーノ", ["ヨルシカ"], "エルマ", 269000),
        ("m06", "心拍数#0822", ["蝶々P"], "Glorious World", 297000),
        ("m07", "六兆年と一夜物語", ["kemu"], "PANDORA VOXX", 214000),
        ("m08", "千本桜", ["WhiteFlame", "初音ミク"], "千本桜 - Single", 245000),
        ("m09", "命に嫌われている。", ["カンザキイオリ"], "白紙", 276000),
        ("m10", "天ノ弱", ["164"], "THEORY -164 feat.GUMI-", 186000),
        ("m11", "地球最後の告白を", ["kemu"], "PANDORA VOXX", 263000),
        ("m12", "深海少女", ["ゆうゆ"], "世迷言ユニバース", 217000),
        ("m13", "からくりピエロ", ["40mP"], "小さな自分と大きな世界", 253000),
        ("m14", "ロミオとシンデレラ", ["doriko"], "ロミオとシンデレラ", 280000),
        ("m15", "初恋が泣いている", ["あいみょん"], "瞳へ落ちるよレコード", 282000),
        ("m16", "愛を知るまでは", ["あいみょん"], "瞳へ落ちるよレコード", 276000),
        ("m17", "君はロックを聴かない", ["あいみょん"], "青春のエキサイトメント", 247000),
        ("m18", "今夜このまま", ["あいみょん"], "瞬間的シックスセンス", 238000),
        ("m19", "空の青さを知る人よ", ["あいみょん"], "おいしいパスタがあると聞いて", 306000),
        ("m20", "裸の心", ["あいみょん"], "おいしいパスタがあると聞いて", 298000),
        ("m21", "恋人ごっこ", ["マカロニえんぴつ"], "hope", 204000),
        ("m22", "ブルーベリー・ナイツ", ["マカロニえんぴつ"], "LiKE", 242000),
        ("m23", "なんでもないよ、", ["マカロニえんぴつ"], "ハッピーエンドへの期待は", 213000),
        ("m24", "沈丁花", ["DISH//"], "沈丁花 - Single", 230000),
        ("m25", "猫", ["DISH//"], "猫 - Single", 272000),
        ("m26", "勿忘", ["Awesome City Club"], "Grower", 251000),
        ("m27", "水平線", ["back number"], "ユーモア", 288000),
        ("m28", "怪獣の花唄", ["Vaundy"], "strobo", 225000),
        ("m29", "花束を君に", ["宇多田ヒカル"], "Fantôme", 278000),
    ]
    for tid, title, artists, album, dur in mixed_samples:
        global_idx += 1
        corpus.append(_make_positive_eval_track(global_idx, tid, title, artists, album, dur, "mixed"))

    # Bucket 5: Latin Artist Names with Japanese Songs (>= 25)
    latin_artist_samples = [
        ("la01", "紅蓮華", ["LiSA"], "LEO-NiNE", 237000),
        ("la02", "炎", ["LiSA"], "炎 - Single", 274000),
        ("la03", "明け星", ["LiSA"], "LANDER", 269000),
        ("la04", "白銀", ["LiSA"], "LANDER", 261000),
        ("la05", "踊", ["Ado"], "狂言", 210000),
        ("la06", "ギラギラ", ["Ado"], "狂言", 276000),
        ("la07", "レディメイド", ["Ado"], "狂言", 243000),
        ("la08", "新時代", ["Ado"], "ウタの歌 ONE PIECE FILM RED", 226000),
        ("la09", "私は最強", ["Ado"], "ウタの歌 ONE PIECE FILM RED", 257000),
        ("la10", "逆光", ["Ado"], "ウタの歌 ONE PIECE FILM RED", 237000),
        ("la11", "ウタカタララバイ", ["Ado"], "ウタの歌 ONE PIECE FILM RED", 173000),
        ("la12", "Tot Musica", ["Ado"], "ウタの歌 ONE PIECE FILM RED", 195000),
        ("la13", "世界のつづき", ["Ado"], "ウタの歌 ONE PIECE FILM RED", 288000),
        ("la14", "風のゆくえ", ["Ado"], "ウタの歌 ONE PIECE FILM RED", 248000),
        ("la15", "東京フラッシュ", ["Vaundy"], "strobo", 259000),
        ("la16", "不可幸力", ["Vaundy"], "strobo", 201000),
        ("la17", "napori", ["Vaundy"], "strobo", 202000),
        ("la18", "踊り子", ["Vaundy"], "replica", 229000),
        ("la19", "恋風邪にのせて", ["Vaundy"], "replica", 252000),
        ("la20", "花占い", ["Vaundy"], "replica", 205000),
        ("la21", "CHAINSAW BLOOD", ["Vaundy"], "replica", 201000),
        ("la22", "瞳惚れ", ["Vaundy"], "replica", 270000),
        ("la23", "怪獣の花唄", ["Vaundy"], "strobo", 225000),
        ("la24", "Subtitle", ["Official HIGE DANdism"], "Subtitle - Single", 305000),
        ("la25", "Pretender", ["Official HIGE DANdism"], "Traveler", 326000),
        ("la26", "I LOVE...", ["Official HIGE DANdism"], "Editorial", 282000),
        ("la27", "Cry Baby", ["Official HIGE DANdism"], "Editorial", 241000),
        ("la28", "Mixed Nuts", ["Official HIGE DANdism"], "ミックスナッツ EP", 213000),
    ]
    for tid, title, artists, album, dur in latin_artist_samples:
        global_idx += 1
        corpus.append(_make_positive_eval_track(global_idx, tid, title, artists, album, dur, "latin_artist"))

    # Bucket 6: Bracket Translations (>= 25)
    bracket_samples = [
        ("bt01", "夜に駆ける (Racing into the Night)", ["YOASOBI"], "THE BOOK", 261000),
        ("bt02", "群青 (Blue)", ["YOASOBI"], "THE BOOK", 262000),
        ("bt03", "怪物 (Monster)", ["YOASOBI"], "THE BOOK 2", 206000),
        ("bt04", "三原色 (RGB)", ["YOASOBI"], "THE BOOK 2", 224000),
        ("bt05", "優しい彗星 (Comet)", ["YOASOBI"], "THE BOOK 2", 215000),
        ("bt06", "大正浪漫 (Taisho Romance)", ["YOASOBI"], "THE BOOK 2", 168000),
        ("bt07", "アンコール (Encore)", ["YOASOBI"], "THE BOOK", 271000),
        ("bt08", "ハルジオン (Halzion)", ["YOASOBI"], "THE BOOK", 198000),
        ("bt09", "あの夢をなぞって (Tracing A Dream)", ["YOASOBI"], "THE BOOK", 242000),
        ("bt10", "たぶん (Probably)", ["YOASOBI"], "THE BOOK", 258000),
        ("bt11", "Sparkle [スパークル]", ["Lilas Ikuta"], "Sketch", 210000),
        ("bt12", "Lens [レンズ]", ["Lilas Ikuta"], "Sketch", 202000),
        ("bt13", "Answer [アンサー]", ["Lilas Ikuta"], "Sketch", 208000),
        ("bt14", "Romance [ロマンス]", ["YOASOBI"], "THE BOOK 2", 168000),
        ("bt15", "Heartbeat [ハナビラ]", ["Aimyon"], "Falling Into Your Eyes Record", 282000),
        ("bt16", "Naked Heart (裸の心)", ["Aimyon"], "Heard that there's good pasta", 298000),
        ("bt17", "Marigold (マリーゴールド)", ["Aimyon"], "Momentary Sixth Sense", 306000),
        ("bt18", "Harunohi (ハルノヒ)", ["Aimyon"], "Heard that there's good pasta", 326000),
        ("bt19", "Kimi wa Rock wo Kikanai (君はロックを聴かない)", ["Aimyon"], "Excitement of Youth", 247000),
        ("bt20", "Konya Konomama (今夜このまま)", ["Aimyon"], "Momentary Sixth Sense", 238000),
        ("bt21", "Lemon (レモン)", ["Kenshi Yonezu"], "BOOTLEG", 255000),
        ("bt22", "Flamingo (フラミンゴ)", ["Kenshi Yonezu"], "STRAY SHEEP", 196000),
        ("bt23", "Pale Blue (ペールブルー)", ["Kenshi Yonezu"], "STRAY SHEEP", 296000),
        ("bt24", "KICK BACK (キックバック)", ["Kenshi Yonezu"], "KICK BACK - Single", 193000),
        ("bt25", "Chouchou Musubi [蝶々結び]", ["Aimer"], "daydream", 306000),
        ("bt26", "Brave Shine [ブレイブシャイン]", ["Aimer"], "DAWN", 233000),
        ("bt27", "StarRingChild [スターリングチャイルド]", ["Aimer"], "Midnight Sun", 328000),
        ("bt28", "RE:I AM [リ・アイ・アム]", ["Aimer"], "Midnight Sun", 345000),
    ]
    for tid, title, artists, album, dur in bracket_samples:
        global_idx += 1
        corpus.append(_make_positive_eval_track(global_idx, tid, title, artists, album, dur, "bracket_trans"))

    # Bucket 7: Localized Storefront Titles (>= 25)
    localized_samples = [
        ("loc01", "Racing into the Night", ["YOASOBI"], "THE BOOK", 261000),
        ("loc02", "Blue", ["YOASOBI"], "THE BOOK", 262000),
        ("loc03", "Monster", ["YOASOBI"], "THE BOOK 2", 206000),
        ("loc04", "RGB", ["YOASOBI"], "THE BOOK 2", 224000),
        ("loc05", "Tracing A Dream", ["YOASOBI"], "THE BOOK", 242000),
        ("loc06", "Haven't", ["YOASOBI"], "THE BOOK", 258000),
        ("loc07", "Encore", ["YOASOBI"], "THE BOOK", 271000),
        ("loc08", "Halzion", ["YOASOBI"], "THE BOOK", 198000),
        ("loc09", "The Swallow", ["YOASOBI"], "THE BOOK 2", 218000),
        ("loc10", "Idol", ["YOASOBI"], "THE BOOK 3", 213000),
        ("loc11", "Biri-Biri", ["YOASOBI"], "THE BOOK 3", 187000),
        ("loc12", "The Brave", ["YOASOBI"], "THE BOOK 3", 194000),
        ("loc13", "Sparkle", ["Lilas Ikuta"], "Sketch", 210000),
        ("loc14", "Lens", ["Lilas Ikuta"], "Sketch", 202000),
        ("loc15", "JUMP", ["Lilas Ikuta"], "Sketch", 191000),
        ("loc16", "Hikari", ["Lilas Ikuta"], "Sketch", 232000),
        ("loc17", "Circle", ["Lilas Ikuta"], "Sketch", 206000),
        ("loc18", "Pretender", ["Official HIGE DANdism"], "Traveler", 326000),
        ("loc19", "I LOVE...", ["Official HIGE DANdism"], "Editorial", 282000),
        ("loc20", "Laughter", ["Official HIGE DANdism"], "Editorial", 353000),
        ("loc21", "Universe", ["Official HIGE DANdism"], "Editorial", 285000),
        ("loc22", "Cry Baby", ["Official HIGE DANdism"], "Editorial", 241000),
        ("loc23", "Anarchy", ["Official HIGE DANdism"], "ミックスナッツ EP", 268000),
        ("loc24", "Mixed Nuts", ["Official HIGE DANdism"], "ミックスナッツ EP", 213000),
        ("loc25", "Chokotto Love", ["Petitmoni"], "Tanpopo / Petitmoni Mega Best", 241000),
        ("loc26", "Koi no Dance Site", ["Morning Musume"], "3rd -Love Paradise-", 270000),
        ("loc27", "Love Machine", ["Morning Musume"], "3rd -Love Paradise-", 302000),
        ("loc28", "One Two Three", ["Morning Musume"], "13 Colorful Character", 267000),
    ]
    for tid, title, artists, album, dur in localized_samples:
        global_idx += 1
        corpus.append(_make_positive_eval_track(global_idx, tid, title, artists, album, dur, "localized"))

    return corpus


def get_hard_negative_corpus() -> List[EvalTrack]:
    """100 hard negative examples (must NEVER be auto-accepted)."""
    negatives: List[EvalTrack] = []

    # 1. Same title, different artist (20)
    same_title_diff_artist = [
        ("n01", "奏 (かなで)", ["雨宮天"], "雨宮天 翻唱", 320000, "cover_different_artist"),
        ("n02", "マリーゴールド", ["カバー歌手"], "J-POP BEST", 306000, "cover_unknown_artist"),
        ("n03", "Lemon", ["Unknown Artist"], "Acoustic Hits", 255000, "unknown_artist"),
        ("n04", "白日", ["アカペラグループ"], "アカペラ名曲集", 270000, "acapella_group"),
        ("n05", "炎", ["オルゴール"], "癒しのオルゴール", 274000, "music_box"),
        ("n06", "ドライフラワー", ["ピアノ奏者"], "ピアノで聴くJ-POP", 286000, "piano_instrumental"),
        ("n07", "群青", ["合唱団"], "混声合唱名曲選", 260000, "chorus_group"),
        ("n08", "打上花火", ["オーケストラ"], "交響曲アニメ", 290000, "orchestra"),
        ("n09", "夜に駆ける", ["琴奏者"], "和楽器J-POP", 261000, "traditional_instrument"),
        ("n10", "紅蓮華", ["吹奏楽団"], "ブラスバンド名曲集", 237000, "brass_band"),
        ("n11", "残響散歌", ["カバー少女"], "アニメカバー", 184000, "indie_cover"),
        ("n12", "怪物", ["路上シンガー"], "ストリートライブ", 206000, "street_singer"),
        ("n13", "新時代", ["童謡合唱団"], "こどものうた", 226000, "children_choir"),
        ("n14", "怪獣の花唄", ["ギターソロ"], "アコースティックギター", 225000, "guitar_solo"),
        ("n15", "シンデレラボーイ", ["弾き語り女子"], "弾き語り", 298000, "acoustic_singer"),
        ("n16", "ブルーバード", ["トロンボーンカルテット"], "管楽器", 216000, "quartet"),
        ("n17", "シルエット", ["ドラムソロ"], "ドラム練習", 240000, "drums_solo"),
        ("n18", "スパークル", ["RADWIMPS"], "君の名は。", 537000, "same_title_radwimps_vs_ikuta"),
        ("n19", "花に亡霊", ["ピアノ独奏"], "ピアノソロ", 241000, "piano_solo"),
        ("n20", "春泥棒", ["オルゴールコレクション"], "癒しの音楽", 290000, "music_box_alt"),
    ]
    for nid, title, artists, album, dur, reason in same_title_diff_artist:
        negatives.append(EvalTrack(id=nid, title=title, artists=artists, album=album, duration_ms=dur, is_hard_negative=True, negative_reason=reason))

    # 2. Same artist, different/similar title (20)
    same_artist_diff_title = [
        ("n21", "夜に駆ける (Instrumental)", ["YOASOBI"], "THE BOOK", 261000, "instrumental_vs_vocal"),
        ("n22", "群青 (Instrumental)", ["YOASOBI"], "THE BOOK", 262000, "instrumental_vs_vocal"),
        ("n23", "怪物 (TV Size)", ["YOASOBI"], "怪物 - Single", 90000, "tv_size_vs_full"),
        ("n24", "三原色 (English Version)", ["YOASOBI"], "E-SIDE", 224000, "english_alt_version"),
        ("n25", "Blue (English Version)", ["YOASOBI"], "E-SIDE", 262000, "english_alt_version"),
        ("n26", "Monster (English Version)", ["YOASOBI"], "E-SIDE", 206000, "english_alt_version"),
        ("n27", "Haven't (English Version)", ["YOASOBI"], "E-SIDE", 258000, "english_alt_version"),
        ("n28", "RGB (English Version)", ["YOASOBI"], "E-SIDE", 224000, "english_alt_version"),
        ("n29", "Into The Night (English Version)", ["YOASOBI"], "E-SIDE", 261000, "english_alt_version"),
        ("n30", "Encore (English Version)", ["YOASOBI"], "E-SIDE", 271000, "english_alt_version"),
        ("n31", "Tracing A Dream (English Version)", ["YOASOBI"], "E-SIDE", 242000, "english_alt_version"),
        ("n32", "The Swallow (English Version)", ["YOASOBI"], "E-SIDE 2", 218000, "english_alt_version"),
        ("n33", "Halzion (English Version)", ["YOASOBI"], "E-SIDE 2", 198000, "english_alt_version"),
        ("n34", "If I Could Draw Life (English Version)", ["YOASOBI"], "E-SIDE 2", 202000, "english_alt_version"),
        ("n35", "乾杯 (Live at 武道館)", ["長渕剛"], "LIVE", 360000, "live_vs_studio"),
        ("n36", "白日 (Live at 横浜アリーナ)", ["King Gnu"], "LIVE CEREMONY", 300000, "live_vs_studio"),
        ("n37", "Lemon (Live Version)", ["米津玄師"], "LIVE 2019", 280000, "live_vs_studio"),
        ("n38", "感電 (Remix)", ["米津玄師"], "REMIXES", 240000, "remix_vs_original"),
        ("n39", "マリーゴールド (Acoustic)", ["あいみょん"], "ACOUSTIC", 310000, "acoustic_vs_original"),
        ("n40", "ドライフラワー (ディレクターズカット)", ["優里"], "特別版", 295000, "alternate_cut"),
    ]
    for nid, title, artists, album, dur, reason in same_artist_diff_title:
        negatives.append(EvalTrack(id=nid, title=title, artists=artists, album=album, duration_ms=dur, is_hard_negative=True, negative_reason=reason))

    # 3. Live / Remix / Instrumental / Acoustic explicit tag conflicts (20)
    tag_conflicts = [
        ("n41", "紅蓮华 (Live)", ["LiSA"], "LEO-NiNE", 260000, "live_tag_conflict"),
        ("n42", "炎 (Remix)", ["LiSA"], "REMIX", 240000, "remix_tag_conflict"),
        ("n43", "明け星 (Instrumental)", ["LiSA"], "LANDER", 269000, "inst_tag_conflict"),
        ("n44", "白銀 (Acoustic Version)", ["LiSA"], "LANDER", 250000, "acoustic_tag_conflict"),
        ("n45", "踊 (Club Remix)", ["Ado"], "REMIXES", 215000, "club_remix"),
        ("n46", "ギラギラ (Live)", ["Ado"], "狂言 ライブ", 280000, "live_tag_conflict"),
        ("n47", "うっせぇわ (Piano Version)", ["Ado"], "PIANO", 210000, "piano_version"),
        ("n48", "新時代 (Orchestra Ver.)", ["Ado"], "ORCHESTRA", 250000, "orchestra_version"),
        ("n49", "私は最強 (Live at 国立)", ["Ado"], "LIVE", 270000, "live_tag_conflict"),
        ("n50", "逆光 (Retro Remix)", ["Ado"], "REMIX", 230000, "remix_tag_conflict"),
        ("n51", "Pretender (Acoustic)", ["Official HIGE DANdism"], "Traveler", 310000, "acoustic_tag_conflict"),
        ("n52", "Subtitle (Instrumental)", ["Official HIGE DANdism"], "Subtitle", 305000, "inst_tag_conflict"),
        ("n53", "I LOVE... (Live)", ["Official HIGE DANdism"], "Editorial Live", 300000, "live_tag_conflict"),
        ("n54", "Cry Baby (Piano Ver.)", ["Official HIGE DANdism"], "PIANO", 230000, "piano_version"),
        ("n55", "Mixed Nuts (TV Size)", ["Official HIGE DANdism"], "TV", 90000, "tv_size"),
        ("n56", "怪獣の花唄 (Live)", ["Vaundy"], "LIVE strobo", 240000, "live_tag_conflict"),
        ("n57", "東京フラッシュ (Sped Up)", ["Vaundy"], "SPED UP", 210000, "sped_up"),
        ("n58", "不可幸力 (Slowed + Reverb)", ["Vaundy"], "SLOWED", 240000, "slowed_reverb"),
        ("n59", "踊り子 (Remix)", ["Vaundy"], "REMIX", 215000, "remix_tag_conflict"),
        ("n60", "花占い (Off Vocal)", ["Vaundy"], "OFF VOCAL", 205000, "off_vocal"),
    ]
    for nid, title, artists, album, dur, reason in tag_conflicts:
        negatives.append(EvalTrack(id=nid, title=title, artists=artists, album=album, duration_ms=dur, is_hard_negative=True, negative_reason=reason))

    # 4. Character songs and sub-identities (15)
    char_songs = [
        ("n61", "コネクト", ["鹿目まどか"], "まどか☆マギカ キャラソン", 270000, "character_vs_claris"),
        ("n62", "Magia", ["暁美ほむら"], "まどか☆マギカ キャラソン", 310000, "character_vs_kalafina"),
        ("n63", "君の知らない物語", ["戦場ヶ原ひたぎ"], "化物語 キャラソン", 340000, "character_vs_supercell"),
        ("n64", "only my railgun", ["御坂美琴"], "レールガン キャラソン", 257000, "character_vs_fripSide"),
        ("n65", "LEVEL5-judgelight-", ["白井黒子"], "レールガン キャラソン", 265000, "character_vs_fripSide"),
        ("n66", "crossing field", ["アスナ"], "SAO キャラソン", 249000, "character_vs_lisa"),
        ("n67", "Catch the Moment", ["キリト"], "SAO キャラソン", 282000, "character_vs_lisa"),
        ("n68", "oath sign", ["セイバー"], "Fate キャラソン", 250000, "character_vs_lisa"),
        ("n69", "unlasting", ["アリス"], "SAO キャラソン", 297000, "character_vs_lisa"),
        ("n70", "IGNITE", ["シノン"], "SAO キャラソン", 244000, "character_vs_aoi_eir"),
        ("n71", "シリウス", ["纏流子"], "キルラキル キャラソン", 264000, "character_vs_aoi_eir"),
        ("n72", "INNOCENCE", ["リーファ"], "SAO キャラソン", 276000, "character_vs_aoi_eir"),
        ("n73", "MEMORIA", ["アイリスフィール"], "Fate キャラソン", 285000, "character_vs_aoi_eir"),
        ("n74", "ラピスラズリ", ["アルスラーン"], "アルスラーン キャラソン", 251000, "character_vs_aoi_eir"),
        ("n75", "流星", ["レン"], "GGO キャラソン", 252000, "character_vs_aoi_eir"),
    ]
    for nid, title, artists, album, dur, reason in char_songs:
        negatives.append(EvalTrack(id=nid, title=title, artists=artists, album=album, duration_ms=dur, is_hard_negative=True, negative_reason=reason))

    # 5. Short titles & Chinese homoglyphs (25)
    short_and_homoglyphs = [
        ("n76", "心", ["十明"], "心 - Single", 195000, "short_title_ambiguity"),
        ("n77", "海", ["十明"], "海 - Single", 210000, "short_title_ambiguity"),
        ("n78", "空", ["大野智"], "空 - Single", 240000, "short_title_ambiguity"),
        ("n79", "光", ["宇多田ヒカル"], "DEEP RIVER", 304000, "short_title_ambiguity"),
        ("n80", "花", ["中孝介"], "ユライ花", 285000, "short_title_ambiguity"),
        ("n81", "風", ["コブクロ"], "NAMELESS WORLD", 320000, "short_title_ambiguity"),
        ("n82", "雨", ["森高千里"], "古今東西", 305000, "short_title_ambiguity"),
        ("n83", "雪", ["中島美嘉"], "TRUE", 310000, "short_title_ambiguity"),
        ("n84", "月", ["桑田佳祐"], "孤独の太陽", 288000, "short_title_ambiguity"),
        ("n85", "星", ["星野源"], "YELLOW DANCER", 230000, "short_title_ambiguity"),
        ("n86", "晴天", ["周杰伦"], "叶惠美", 269000, "chinese_jay_chou_homoglyph"),
        ("n87", "安静", ["周杰伦"], "范特西", 334000, "chinese_jay_chou_homoglyph"),
        ("n88", "简单爱", ["周杰伦"], "范特西", 270000, "chinese_jay_chou_homoglyph"),
        ("n89", "黑色幽默", ["周杰伦"], "Jay", 283000, "chinese_jay_chou_homoglyph"),
        ("n90", "星晴", ["周杰伦"], "Jay", 259000, "chinese_jay_chou_homoglyph"),
        ("n91", "龙卷风", ["周杰伦"], "Jay", 250000, "chinese_jay_chou_homoglyph"),
        ("n92", "江南", ["林俊杰"], "第二天堂", 268000, "chinese_jj_lin_homoglyph"),
        ("n93", "一千年以后", ["林俊杰"], "编号89757", 226000, "chinese_jj_lin_homoglyph"),
        ("n94", "修炼爱情", ["林俊杰"], "因你而在", 276000, "chinese_jj_lin_homoglyph"),
        ("n95", "可惜没如果", ["林俊杰"], "新地球", 298000, "chinese_jj_lin_homoglyph"),
        ("n96", "后来", ["刘若英"], "我等你", 341000, "chinese_kiroro_cover_homoglyph"),
        ("n97", "很爱很爱你", ["刘若英"], "很爱很爱你", 280000, "chinese_kiroro_cover_homoglyph"),
        ("n98", "伤心太平洋", ["任贤齐"], "爱像太平洋", 267000, "chinese_miyuki_cover_homoglyph"),
        ("n99", "天涯", ["任贤齐"], "为爱走天涯", 320000, "chinese_miyuki_cover_homoglyph"),
        ("n100", "漫步人生路", ["邓丽君"], "漫步人生路", 223000, "chinese_miyuki_cover_homoglyph"),
    ]
    for nid, title, artists, album, dur, reason in short_and_homoglyphs:
        negatives.append(EvalTrack(id=nid, title=title, artists=artists, album=album, duration_ms=dur, is_hard_negative=True, negative_reason=reason))

    return negatives


def get_cross_storefront_corpus() -> List[EvalTrack]:
    """30 cross-storefront tracks (available in JP, but target storefront is unavailable or different ID)."""
    cross_corpus: List[EvalTrack] = []
    # 15 unavailable in CN/US target storefront, but available in JP
    for i in range(1, 16):
        cross_corpus.append(EvalTrack(
            id=f"jp_only_{i:02d}",
            title=f"日本限定曲目_{i:02d}",
            artists=["日本限定歌手"],
            album="日本限定专辑",
            duration_ms=240000,
            bucket="jp_exclusive_unavailable",
            expected_jp_id=str(1500010000 + i),
            expected_target_id=None,
            target_available=False,
        ))

    # 15 available in JP and mapped to target storefront via equivalent
    for i in range(16, 31):
        cross_corpus.append(EvalTrack(
            id=f"jp_mapped_{i:02d}",
            title=f"跨区等价曲目_{i:02d}",
            artists=["跨区艺人"],
            album="跨区专辑",
            duration_ms=250000,
            bucket="jp_equivalent_mapped",
            expected_jp_id=str(1500020000 + i),
            expected_target_id=str(1600080000 + i),
            target_available=True,
        ))

    return cross_corpus
