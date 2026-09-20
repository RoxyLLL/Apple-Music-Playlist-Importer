"""
Cross-lingual song title alias mapping and Japanese Romanization for Apple Music matching.
Handles English, Romaji, Kanji, and Kana title variations between streaming platforms
(NetEase, QQ Music, Spotify) and Apple Music regional catalogs.
"""

from typing import Dict, List, Optional, Set
import re

# Curated bidirectional title alias groups for Japanese & Anime songs:
# All titles in a group are treated as equivalent representations of the same song.
TITLE_GROUPS: List[Set[str]] = [
    # YOASOBI
    {"夜に駆ける", "racing into the night", "yoru ni kakeru", "yorunikakeru"},
    {"あの夢をなぞって", "tracing a dream", "ano yume o nazotte", "ano yume wo nazotte"},
    {"ハルジオン", "halzion", "harujion"},
    {"たぶん", "probably", "tabun"},
    {"群青", "blue", "gunjou", "gunjo"},
    {"ハルカ", "haruka"},
    {"アンコール", "encore", "ankoru"},
    {"怪物", "monster", "kaibutsu"},
    {"優しい彗星", "gentle comet", "yasashii suisei"},
    {"もう少しだけ", "just a little step", "mousukoshi dake"},
    {"三原色", "rgb", "sangenshoku"},
    {"ラブレター", "love letter", "rabu reta"},
    {"大正浪漫", "romance", "taisho roman", "taishou roman"},
    {"ツバメ", "the swallow", "tsubame"},
    {"もしも命が描けたら", "if i could draw life", "moshimo inochi ga egaketara"},
    {"祝福", "the blessing", "shukufuku"},
    {"セブンティーン", "seventeen"},
    {"アイドル", "idol", "aidoru"},
    {"勇者", "the brave", "yuusha", "yusha"},
    {"Biri-Biri", "biri-biri"},
    {"HEART BEAT", "heart beat"},

    # Kenshi Yonezu (米津玄師)
    {"打上花火", "uchiage hanabi", "uchiagehanabi", "fireworks"},
    {"Lemon", "lemon"},
    {"LOSER", "loser"},
    {"ピースサイン", "peace sign", "pi-susain"},
    {"灰色と青", "haiiro to ao", "grey and blue", "gray and blue", "haiirotoao"},
    {"春雷", "shunrai"},
    {"Flamingo", "flamingo"},
    {"TEENAGE RIOT", "teenage riot"},
    {"海の幽霊", "spirits of the sea", "umi no yuurei", "uminoyuurei"},
    {"パプリカ", "paprika"},
    {"馬と鹿", "马与鹿", "uma to shika", "horse and deer"},
    {"感電", "kanden", "electric shock"},
    {"KICK BACK", "kick back"},
    {"地球儀", "spinning globe", "chikyugi", "chikyuugi"},
    {"さよーならまたいつか!", "さよーならまたいつか", "sayonara, mata itsuka!", "sayonara mata itsuka"},
    {"毎日", "every day", "mainichi"},
    {"がらくた", "junk", "garakuta"},
    {"アイネクライネ", "eine kleine"},
    {"メトロノーム", "metronome"},
    {"orion", "orion"},

    # RADWIMPS
    {"前前前世", "zenzenzense", "zen zen zense", "past past past life"},
    {"スパークル", "sparkle", "supakuru"},
    {"夢灯籠", "dream lantern", "yumetourou", "yumetoro"},
    {"なんでもないや", "nandemonaiya"},
    {"愛にできることはまだあるかい", "is there still anything that love can do?", "ai ni dekiru koto wa mada aru kai"},
    {"グランドエスケープ", "grand escape"},
    {"大丈夫", "no problem", "daijoubu"},
    {"すずめ", "suzume"},
    {"カナタハルカ", "kanata haruka"},
    {"有心論", "yushinron"},
    {"DADA", "dada"},
    {"おしゃかしゃま", "oshakashama"},

    # 椎名林檎 / 東京事変
    {"丸の内サディスティック", "marunouchi sadistic"},
    {"本能", "instinct", "honnou"},
    {"歌舞伎町の女王", "queen of kabukicho", "kabukichou no joou"},
    {"ギプス", "gips"},
    {"罪と罰", "crime and punishment", "tsumi to batsu"},
    {"群青日和", "ideal days for ultramarine", "gunjou biyori"},
    {"修羅場", "the rat's-nest", "adult", "shuraba"},
    {"能動的三分間", "3min.", "active 3 minutes", "noudouteki sanpunkan"},
    {"閃光少女", "flash girl", "senkou shoujo"},

    # 高橋洋子
    {"残酷な天使のテーゼ", "a cruel angel's thesis", "zankoku na tenshi no teze", "zankoku na tenshi no te-ze"},
    {"魂のルフラン", "soul's refrain", "tamashii no rufuran"},

    # 宇多田ヒカル (Hikaru Utada)
    {"First Love", "first love"},
    {"Automatic", "automatic"},
    {"Flavor Of Life", "flavor of life"},
    {"花束を君に", "hanataba wo kimini", "hanataba o kimi ni", "bouquets for you"},
    {"真夏の通り雨", "manatsu no tooriame", "midsummer shower"},
    {"道", "michi", "road"},
    {"One Last Kiss", "one last kiss"},
    {"君に夢中", "kimi ni muchuu", "bad mode"},
    {"Gold ～また逢う日まで～", "gold - until we meet again", "gold - mata au hi made"},

    # Aimer
    {"残響散歌", "zankyosanka", "zankyou sanka"},
    {"カタオモイ", "kataomoi"},
    {"Brave Shine", "brave shine"},
    {"Black Bird", "black bird"},
    {"I beg you", "i beg you"},
    {"茜さす", "akane sasu"},
    {"蝶々結び", "chouchou musubi", "choucho musubi"},
    {"Ref:rain", "ref:rain"},
    {"escalate", "escalate"},
    {"deep down", "deep down"},

    # LiSA
    {"紅蓮華", "gurenge", "red lotus"},
    {"炎", "homura", "flame"},
    {"Catch the Moment", "catch the moment"},
    {"crossing field", "crossing field"},
    {"oath sign", "oath sign"},
    {"unlasting", "unlasting"},
    {"明け星", "akeboshi", "morning star"},
    {"白銀", "shirogane", "silver"},

    # Official髭男dism
    {"Pretender", "pretender"},
    {"I LOVE...", "i love..."},
    {"宿命", "shukumei", "destiny"},
    {"ノーダウト", "no doubt"},
    {"115万キロのフィルム", "115 man kilo no film", "1.15 million kilometer film"},
    {"Cry Baby", "cry baby"},
    {"ミックスナッツ", "mixed nuts"},
    {"Subtitle", "subtitle"},
    {"TATTOO", "tattoo"},
    {"ホワイトノイズ", "white noise"},

    # King Gnu
    {"白日", "hakujitsu"},
    {"一途", "ichizu"},
    {"逆夢", "sakayume"},
    {"飛行艇", "hikoutei"},
    {"Teenager Forever", "teenager forever"},
    {"Boy", "boy"},
    {"雨燦々", "ame sansan"},
    {"SPECIALZ", "specialz"},

    # Mrs. GREEN APPLE
    {"青と夏", "ao to natsu", "blue and summer", "aotonatsu"},
    {"点描の唄", "tenbyou no uta", "tenbyounouta"},
    {"インフェルノ", "inferno"},
    {"ダンスホール", "dance hall", "dancehall"},
    {"Soranji", "soranji"},
    {"ケセラセラ", "que sera sera"},
    {"Magic", "magic"},
    {"ライラック", "lilac"},

    # 優里 (Yuuri)
    {"ドライフラワー", "dry flower", "dried flower"},
    {"ベテルギウス", "betelgeuse"},
    {"かくれんぼ", "kakurenbo", "hide and seek"},
    {"ピーターパン", "peter pan"},
    {"レオ", "leo"},
    {"ビリミリオン", "billimillion"},

    # 藤井風 (Fujii Kaze)
    {"死ぬのがいいわ", "shinunoga e-wa", "shinunoga iiwa"},
    {"きらり", "kirari"},
    {"まつり", "matsuri"},
    {"何なんw", "nan-nan", "what the hell"},
    {"もうええわ", "mo-eh-wa", "i'm over it"},
    {"優しさ", "yasashisa", "kindness"},
    {"旅路", "tabiji", "journey"},
    {"grace", "grace"},
    {"満ちてゆく", "overflowing", "michiteyuku"},

    # Vaundy
    {"怪獣の花唄", "kaiju no hanauta", "kaijunohanauta"},
    {"踊り子", "odoriko", "dancer"},
    {"不可幸力", "fukakouryoku"},
    {"napori", "napori"},
    {"花占い", "hanauranai"},
    {"CHAINSAW BLOOD", "chainsaw blood"},
    {"タイムパラドックス", "time paradox"},

    # Ado
    {"うっせぇわ", "usseewa"},
    {"踊", "odo", "dance"},
    {"ギラギラ", "gira gira"},
    {"新時代", "new genesis", "shin jidai", "shinjidai"},
    {"逆光", "backlight", "gyakkou"},
    {"私は最強", "i'm invincible", "watashi wa saikyou"},
    {"ウタカタララバイ", "fleeting lullaby", "utakata lullaby"},
    {"Tot Musica", "tot musica"},
    {"Show", "show", "唱"},
    {"Kura Kura", "kura kura", "クラクラ"},

    # back number
    {"水平線", "suiheisen", "horizon"},
    {"高嶺の花子さん", "takane no hanako-san"},
    {"クリスマスソング", "christmas song"},
    {"ハッピーエンド", "happy end"},
    {"ヒロイン", "heroine"},
    {"怪盗", "kaitou"},
    {"アイラブユー", "i love you"},

    # 美波 (Minami)
    {"カワキヲアメク", "crying for rain", "kawaki wo ameku", "kawakiwoameku"},
    {"ホロネス", "hollowness"},
    {"アメヲマツ、", "waiting for rain", "ame wo matsu"},

    # ヨルシカ (Yorushika)
    {"ただ君に晴れ", "just a sunny day for you", "tada kimi ni hare"},
    {"だから僕は音楽を辞めた", "that's why i gave up on music", "dakara boku wa ongaku wo yameta"},
    {"言って。", "say it.", "itte"},
    {"花に亡霊", "ghost in a flower", "hana ni bourei"},
    {"春泥棒", "spring thief", "haru dorobou"},
    {"夏の肖像", "portrait of summer", "natsu no shouzou", "natsu no shozo"},
    {"夜行", "night journey", "yakou", "yako"},
    {"老人と海", "the old man and the sea", "roujin to umi", "rojin to umi"},
    {"雨とカプチーノ", "rain with cappuccino"},
    {"藍二乗", "blur", "deep indigo"},
    {"ヒッチコック", "hitchcock"},
    {"ブレーメン", "bremen"},
    {"アルジャーノン", "algernon"},
    {"又三郎", "matasaburo"},
    {"左右盲", "left-right confusion", "sayuumou"},
    {"斜陽", "setting sun", "shayo", "shayou"},
    {"都落ち", "capital city fall"},
    {"月光浴", "moonbath"},

    # ずっと真夜中でいいのに。 (ZUTOMAYO)
    {"秒針を噛む", "bite the second hand", "byoushin wo kamu"},
    {"脳裏上のクラッカー", "cracker inside my head", "nouriue no cracker"},
    {"正義", "justice", "seigi"},
    {"お勉強しといてよ", "study me", "obenkyou shitoiteyo"},
    {"あいつら全員同窓会", "inside joke", "aitsura zenin dousoukai"},
    {"残機", "time left", "zanki"},

    # Eve
    {"廻廻奇譚", "kaikai kitan"},
    {"ドラマツルギー", "dramaturgy"},
    {"お気に召すまま", "as you like it", "okinimesumama"},
    {"ナンセンス文学", "nonsense bungaku"},
    {"いのちの食べ方", "how to eat life", "inochi no tabekata"},
    {"心海", "shinkai"},
    {"心予報", "heart forecast", "kokoro yoho"},
    {"ファイトソング", "fight song"},
    {"ぼくらの", "bokurano"},

    # AKASAKI / New J-Pop
    {"夏実", "kajitsu", "natsumi"},
    {"Bunny Girl", "bunny girl"},

    # supercell / EGOIST
    {"君の知らない物語", "kimi no shiranai monogatari", "the story you don't know"},
    {"The Everlasting Guilty Crown", "the everlasting guilty crown"},
    {"My Dearest", "my dearest"},
    {"名前のない怪物", "nameless monster", "namae no nai kaibutsu"},
    {"All Alone With You", "all alone with you"},
    {"英雄 運命の詩", "eiyuu unmei no uta"},

    # Classic Anime Themes
    {"God knows...", "god knows..."},
    {"secret base ～君がくれたもの～", "secret base ~kimi ga kureta mono~", "secret base"},
    {"恋愛サーキュレーション", "renai circulation"},
    {"白金ディスコ", "platinum disco", "purachina disuko"},
    {"コネクト", "connect"},
    {"only my railgun", "only my railgun"},
    {"LEVEL5-judgelight-", "level5 -judgelight-"},
    {"鳥の詩", "鸟之诗", "tori no uta"},
    {"青空", "aozora"},
    {"千本桜", "senbonzakura"},
    {"Butter-Fly", "butter-fly"},
    {"brave heart", "brave heart"},
    {"Blue Bird", "blue bird"},
    {"青い栞", "aoi shiori", "blue bookmark"},
    {"光るなら", "hikaru nara", "if it shines"},
    {"境界の彼方", "kyoukai no kanata", "beyond the boundary"},
    {"unravel", "unravel"},
    {"シルエット", "silhouette"},
    {"Sign", "sign"},
    {"Bling-Bang-Bang-Born", "bling-bang-bang-born"},
    {"Otonoke", "otonoke"},
    {"CHA-LA HEAD-CHA-LA", "cha-la head-cha-la"},
    {"DAN DAN 心魅かれてく", "dan dan kokoro hikareteku"},
    {"そばかす", "sobakasu", "freckles"},
    {"1/3の純情な感情", "1/3 no junjou na kanjou"},
    {"奏", "奏(かなで)", "kanade"},
    {"春を告げる", "haru wo tsugeru", "haru o tsugeru", "haruwotsugeru"},
    {"春を待つ", "wait for spring", "haru wo matsu", "haru o matsu", "haruwomatsu"},
    {"Thaw", "thaw"},
]

# Build lookup index from normalized title -> group_id
_TITLE_TO_GROUP: Dict[str, int] = {}
for gid, group in enumerate(TITLE_GROUPS):
    for name in group:
        norm = name.lower().strip()
        _TITLE_TO_GROUP[norm] = gid
        # Also clean punctuation: "god knows..." -> "god knows"
        p_clean = re.sub(r"[^\w\s\u4e00-\u9fa5\u3040-\u30ff]", "", norm).strip()
        if p_clean and p_clean != norm:
            _TITLE_TO_GROUP[p_clean] = gid


def are_titles_equivalent(t1: str, t2: str) -> bool:
    """
    Check if two song titles are identical or known cross-lingual/romanized aliases.
    """
    if not t1 or not t2:
        return False

    n1 = t1.lower().strip()
    n2 = t2.lower().strip()

    if n1 == n2:
        return True

    p1 = re.sub(r"[^\w\s\u4e00-\u9fa5\u3040-\u30ff]", "", n1).strip()
    p2 = re.sub(r"[^\w\s\u4e00-\u9fa5\u3040-\u30ff]", "", n2).strip()

    if p1 and p1 == p2:
        return True

    g1 = _TITLE_TO_GROUP.get(n1) or _TITLE_TO_GROUP.get(p1)
    g2 = _TITLE_TO_GROUP.get(n2) or _TITLE_TO_GROUP.get(p2)

    if g1 is not None and g2 is not None and g1 == g2:
        return True

    return False


# Curated Kanji-to-Romaji mappings for Japanese song titles.
# Sorted by length descending when applied to match longest compound words first.
KANJI_TO_ROMAJI_COMPOUNDS: List[tuple] = [
    # Multi-kanji phrases
    ("打上花火", "uchiage hanabi"),
    ("前前前世", "zenzenzense"),
    ("夜に駆ける", "yoru ni kakeru"),
    ("丸の内サディスティック", "marunouchi sadistic"),
    ("歌舞伎町の女王", "kabukichou no joou"),
    ("残酷な天使のテーゼ", "zankoku na tenshi no teze"),
    ("魂のルフラン", "tamashii no rufuran"),
    ("君の知らない物語", "kimi no shiranai monogatari"),
    ("怪獣の花唄", "kaiju no hanauta"),
    ("点描の唄", "tenbyou no uta"),
    ("死ぬのがいいわ", "shinunoga iiwa"),
    ("春を告げる", "haru wo tsugeru"),
    ("海の幽霊", "umi no yuurei"),
    ("灰色と青", "haiiro to ao"),
    ("青と夏", "ao to natsu"),
    ("水平線", "suiheisen"),
    ("残響散歌", "zankyosanka"),
    ("新時代", "shin jidai"),
    ("逆光", "gyakkou"),
    ("白日", "hakujitsu"),
    ("一途", "ichizu"),
    ("逆夢", "sakayume"),
    ("千本桜", "senbonzakura"),
    ("鳥の詩", "tori no uta"),
    ("花束を君に", "hanataba wo kimini"),
    ("真夏の通り雨", "manatsu no tooriame"),
    ("愛にできることはまだあるかい", "ai ni dekiru koto wa mada aru kai"),
    ("ただ君に晴れ", "tada kimi ni hare"),
    ("花に亡霊", "hana ni bourei"),
    ("春泥棒", "haru dorobou"),
    ("秒針を噛む", "byoushin wo kamu"),
    ("廻廻奇譚", "kaikai kitan"),
    ("高嶺の花子さん", "takane no hanako-san"),

    # Common Kanji words in Japanese music titles
    ("花火", "hanabi"),
    ("打上", "uchiage"),
    ("夜", "yoru"),
    ("駆ける", "kakeru"),
    ("青", "ao"),
    ("夏", "natsu"),
    ("春", "haru"),
    ("秋", "aki"),
    ("冬", "fuyu"),
    ("花", "hana"),
    ("雨", "ame"),
    ("雪", "yuki"),
    ("空", "sora"),
    ("風", "kaze"),
    ("光", "hikari"),
    ("夢", "yume"),
    ("星", "hoshi"),
    ("月", "tsuki"),
    ("太陽", "taiyou"),
    ("心", "kokoro"),
    ("愛", "ai"),
    ("恋", "koi"),
    ("君", "kimi"),
    ("僕", "boku"),
    ("私", "watashi"),
    ("世界", "sekai"),
    ("未来", "mirai"),
    ("過去", "kako"),
    ("道", "michi"),
    ("旅", "tabi"),
    ("鳥", "tori"),
    ("歌", "uta"),
    ("唄", "uta"),
    ("詩", "uta"),
    ("音", "oto"),
    ("声", "koe"),
    ("手", "te"),
    ("目", "me"),
    ("涙", "namida"),
    ("笑顔", "egao"),
    ("言葉", "kotoba"),
    ("想い", "omoi"),
    ("願い", "negai"),
    ("祈り", "inori"),
    ("約束", "yakusoku"),
    ("秘密", "himitsu"),
    ("記憶", "kioku"),
    ("絆", "kizuna"),
    ("命", "inochi"),
    ("炎", "homura"),
    ("紅蓮華", "gurenge"),
    ("感電", "kanden"),
    ("勇者", "yuusha"),
    ("祝福", "shukufuku"),
    ("怪物", "kaibutsu"),
    ("群青", "gunjou"),
    ("奏", "kanade"),
    ("本能", "honnou"),
    ("罪", "tsumi"),
    ("罰", "batsu"),
    ("落日", "rakujitsu"),
    ("修羅場", "shuraba"),
    ("桜", "sakura"),
]


# Comprehensive On'yomi and Kun'yomi multi-readings for common Japanese music kanji
KANJI_MULTI_READINGS: Dict[str, List[str]] = {
    # Seasons & Nature
    "夏": ["natsu", "ka", "ge"],
    "実": ["jitsu", "mi"],
    "春": ["haru", "shun"],
    "秋": ["aki", "shuu"],
    "冬": ["fuyu", "tou"],
    "雨": ["ame", "ama", "u"],
    "雪": ["yuki", "setsu"],
    "風": ["kaze", "fuu"],
    "空": ["sora", "kuu"],
    "海": ["umi", "kai"],
    "波": ["nami", "ha"],
    "雲": ["kumo", "un"],
    "月": ["tsuki", "getsu", "gatsu"],
    "日": ["hi", "nichi", "jitsu", "ka"],
    "星": ["hoshi", "sei", "shou"],
    "光": ["hikari", "kou"],
    "夜": ["yoru", "yo", "ya"],
    "花": ["hana", "ka", "ke"],
    "火": ["hi", "ka"],
    "水": ["mizu", "sui"],
    "木": ["ki", "ko", "moku", "boku"],
    "金": ["kane", "kin", "kon"],
    "土": ["tsuchi", "do", "to"],
    "天": ["ten", "ama"],
    "地": ["chi", "ji"],
    "山": ["yama", "san", "zan"],
    "川": ["kawa", "sen"],
    "森": ["mori", "shin"],
    "林": ["hayashi", "rin"],
    "石": ["ishi", "seki", "shaku"],
    "草": ["kusa", "sou"],

    # Time & Space
    "朝": ["asa", "chou"],
    "昼": ["hiru", "chuu"],
    "夕": ["yuu", "seki"],
    "今": ["ima", "kon", "kin"],
    "昔": ["mukashi", "seki", "shaku"],
    "時": ["toki", "ji"],
    "間": ["aida", "ma", "kan", "ken"],
    "年": ["toshi", "nen"],
    "世": ["yo", "sei", "se"],
    "界": ["kai"],
    "前": ["mae", "zen"],
    "後": ["ato", "ushiro", "nochi", "go", "kou"],
    "道": ["michi", "dou", "tou"],
    "路": ["ji", "michi", "ro"],
    "街": ["machi", "gai", "kai"],
    "町": ["machi", "chou"],
    "国": ["kuni", "koku"],
    "生": ["nama", "sei", "shou", "iku"],
    "死": ["shinu", "shi"],

    # Senses & Emotions
    "心": ["kokoro", "shin"],
    "愛": ["ai"],
    "恋": ["koi", "ren"],
    "夢": ["yume", "mu", "bou"],
    "音": ["oto", "ne", "on", "in"],
    "声": ["koe", "sei", "shou"],
    "歌": ["uta", "ka"],
    "唄": ["uta", "bai"],
    "詩": ["uta", "shi"],
    "想": ["omoi", "sou", "so"],
    "思": ["omou", "shi"],
    "願": ["negai", "gan"],
    "祈": ["inori", "ki"],
    "涙": ["namida", "rui"],
    "笑": ["warau", "emi", "shou"],
    "色": ["iro", "shiki", "shoku"],
    "青": ["ao", "sei", "shou"],
    "白": ["shiro", "haku", "byaku"],
    "黒": ["kuro", "koku"],
    "赤": ["aka", "seki", "shaku"],
    "紅": ["beni", "kurenai", "kou"],
    "黄": ["ki", "kou", "ou"],
    "紫": ["murasaki", "shi"],
    "緑": ["midori", "ryoku", "roku"],
    "藍": ["ai", "ran"],

    # Actions & States
    "行": ["kou", "gyou", "iku", "yuku"],
    "来": ["kuru", "ki", "rai"],
    "帰": ["kaeru", "ki"],
    "走": ["hashiru", "sou"],
    "歩": ["aruku", "ho", "bu"],
    "飛": ["tobu", "hi"],
    "舞": ["mau", "mai", "bu"],
    "泳": ["oyogu", "ei"],
    "落": ["ochiru", "raku"],
    "降": ["furu", "oriru", "kou"],
    "消": ["kieru", "shou"],
    "残": ["nokoru", "zan"],
    "見": ["miru", "ken"],
    "聞": ["kiku", "bun", "mon"],
    "言": ["koto", "iu", "gen", "gon"],
    "語": ["katari", "go"],
    "話": ["hanashi", "wa"],
    "知": ["shiru", "chi"],
    "忘": ["wasureru", "bou"],
    "覚": ["oboeru", "kaku"],
    "待": ["matsu", "tai"],
    "止": ["tomaru", "shi"],
    "開": ["hiraku", "akeru", "kai"],
    "閉": ["tojiru", "shimeru", "hei"],

    # Qualities
    "真": ["makoto", "shin"],
    "嘘": ["uso", "kyo"],
    "新": ["atarashii", "shin"],
    "古": ["furui", "ko"],
    "明": ["akarui", "mei", "myou"],
    "暗": ["kurai", "an"],
    "深": ["fukai", "shin"],
    "浅": ["asai", "sen"],
    "高": ["takai", "kou"],
    "低": ["hikui", "tei"],
    "長": ["nagai", "chou"],
    "短": ["mijikai", "tan"],
    "大": ["ookii", "dai", "tai"],
    "小": ["chiisai", "shou"],
    "多": ["ooi", "ta"],
    "少": ["sukunai", "shou"],
    "美": ["utsukushii", "bi", "mi"],
    "神": ["kami", "shin", "jin"],
    "人": ["hito", "jin", "nin"],
    "君": ["kimi", "kun"],
    "僕": ["boku"],
    "私": ["watashi", "shi"],
    "誰": ["dare", "sui"],
    "何": ["nani", "nan", "ka"],
    "物": ["mono", "butsu", "motsu"],
    "者": ["mono", "sha"],
}

