# translationclaude.md: how Claude subtitles a hardsub episode

**For:** the next Claude session asked to subtitle an episode of a show that has Chinese subtitles burned into the picture (first done on 生死訟 / *The Intangible Truth*, 1990, episode 2).
**What the user wants:** finished, watchable Chinese and English subtitles that are **checked against the real frames**. They want Claude itself, running on their subscription, to read the frames and translate. Don't use an API key, and don't stop at Google Translate. They called the result of this method "really really good". Do it this way.

---

## Why not just OCR + Google

Measured on episode 2 (487 subtitle lines):

| Method | Result |
|---|---|
| OCR (PP-OCRv3) + cleanup rules | only **39%** of lines exactly right; most of the rest are missing a glyph (麼 氣 價 來) or have a look-alike (追/這, 實/買, 周題/問題) |
| Google Translate on that | translates the mistakes literally: 多少鰱 "how much silver carp", 排一次隧 "drain the tunnel" |
| **Claude reads the frames + translates in context** | every line matches the screen; meaning fixes like 插→捅 "beat them up" → **"stab them"**, 你懷姊姊 "when **you** were pregnant", 孫女 "granddaughter" |

The OCR is still useful: it finds **when** each subtitle appears (the timings) and gives a first guess at the text. Claude then reads every line off the actual frame.

---

## The method, step by step

Run everything from the tool folder:

```powershell
cd "C:\AI STUFF\Local\tvbaw"
$env:PYTHONIOENCODING='utf-8'
$ep = "downloads\The Intangible Truth - 生死訟 - 1990\003 Episode 03"
```

### 1. OCR for the timings only (about 1.5 minutes)

```powershell
python hardsub.py ocr "$ep.mp4" -o <scratchpad>\ep03\raw.zh.srt
```

**Use `ocr`, not `all`.** The user does not want Google Translate or any OCR file in the episode folder: the OCR is only there to find *when* each subtitle appears. Write it to the scratchpad. The episode folder ends up with exactly two subtitle files, `.en.srt` and `.zh.srt`.

### 2. Contact sheets (about 1 minute)

```powershell
python hardsub.py sheets "$ep.mp4" <scratchpad>\ep03\raw.zh.srt -o <scratchpad>\ep03
```

This takes one crop of the subtitle band at the **midpoint of every cue**, stacked 10 to a sheet with the cue number in a yellow margin. About 50 sheets per 46-minute episode. Ten per sheet is the limit; any more and the glyphs get too small to read reliably.

### 3. Read every sheet and write the review file (the real work)

Read `sheet01.png` … `sheetNN.png` in order, two or three at a time. For **every** cue number write one line in a scratchpad text file:

```
N|中文 exactly as on screen|English
```

Rules that produced the episode 2 result:

- **Chinese:** copy what the frame shows, character for character, in Traditional script. Put a **space** between the two subtitle lines, which becomes a line break in the SRT. Keep the subtitler's punctuation (`…`, `？`, `，`).
- **Drop duplicates and junk with an empty Chinese part: `N||`.** The OCR often splits one subtitle into 2–4 cues (one frame read half a line, the next the whole line). Keep the cue with the full text, and empty the others. An empty cue is removed and the cue before it stretches over its time.
- **No subtitle visible** (the midpoint fell between two lines): keep the OCR text if it's plausible, corrected from context.
- **English is translated from your corrected Chinese, as a scene, not line by line.** Know who is speaking to whom: 你懷姊姊的時候 is the son talking *to* his mother, so it's "when *you* were pregnant".
- **Two speakers in one cue** (`多少錢 一百三`): separate the English with ` / `, which also becomes a line break.
- **Style:** natural spoken English, short enough to read in the cue's time, with **British spelling** (Mum, colour, rubbish). Keep insults strong (死野種 "bastards", 狐狸精 "vixen") and idioms idiomatic (龍生龍，鳳生鳳 "like breeds like", 受氣包 "doormat"). Don't explain culture in the subtitle.
- **Names:** use the glossary below. Add new characters to it as you meet them.
- **Unsure?** Give your best reading, and list the cue numbers in your report to the user.

A line must exist for every cue number in `raw.zh.srt`, or `apply` refuses.

### 4. Build the SRTs

```powershell
python hardsub.py apply <scratchpad>\ep03\raw.zh.srt <scratchpad>\ep03\review.txt --zh "$ep.zh.srt" --en "$ep.en.srt"
```

### 5. Second check (mandatory)

Print the finished pairs side by side and **read all of them**:

```powershell
python -c "import sys;sys.path.insert(0,'.');import hardsub as H;z=H.read_srt(r'$ep.zh.srt');e=H.read_srt(r'$ep.en.srt');print(len(z),len(e));[print(i,H.ts(a['start'])[3:8],a['text'],'|',b['text']) for i,(a,b) in enumerate(zip(z,e),1)]"
```

Check:
- the same number of cues in both files, with identical timings
- no empty lines, and no Chinese left in the English
- every English line says what its Chinese says, especially questions vs statements, who "you" is, and numbers and prices

On episode 2 this pass found 2 lines to change. Fix them, re-run the check, then report.

### 6. Report to the user

Give the cue count, the OCR accuracy (how many lines were already exactly right), the notable meaning fixes, and any cue numbers you are unsure of. Never call an episode finished before the second check.

---

## Files per episode

```
003 Episode 03.mp4
003 Episode 03.en.srt            <- Claude's English. The one to watch with (auto-loads)
003 Episode 03.zh.srt            <- Claude's proof-read Chinese (auto-loads)
```

Nothing else. No raw OCR, no Google translation: the user asked for only these two.

---

## Glossary: 生死訟 (keep consistent across episodes)

| Chinese | English | Who / what |
|---|---|---|
| 馬海 | Ma Hai | son; car mechanic, about to go to sea (跑船) |
| 馬潔 | Ma Jie | elder sister; runs the vegetable stall |
| 馬泳 | Ma Yong | youngest sister, schoolgirl |
| 老媽 / 媽 | Mum | their mother, the first wife |
| 馬洋 | Ma Yang | son of the father's other, wealthy family |
| 媽咪 | Mummy | how Ma Yang's side address their mother (the second wife) |
| 葉蔚 / 火柴頭 | Ye Wei / "Matchstick" | Ma Hai's tomboy friend |
| 葉榮生 | Ye Rongsheng | Ye Wei's father, dying in hospital |
| 馬陳玉卿 / 玉卿 | Ma Chen Yuqing / Yuqing | the mother (first wife), retried in ep 1 for arson-murder she was framed for |
| 馬錦超 / 錦超 | Ma Jinchao / Jinchao | the father; gambler, keeps two families |
| 伊媚 | Yimei | the second wife; owns the company |
| 馬江 | Ma Jiang | a son of the second family (Ma Yang's brother) |
| 鬼王深 / 深哥 | Ghost King Shen / Brother Shen | Tsim Sha Tsui triad boss the father owes |
| 珍姊 | Sister Zhen | vegetable-market neighbour |
| 佳叔 | Uncle Jia | runs the garage where Ma Hai works |
| 阿立 / 立哥 | Ah Li | Ma Jie's policeman boyfriend; Mum's favourite |
| 張志雄 / 志雄 | Zhang Zhixiong / Zhixiong | market stallholder, secretly fond of Ma Jie |
| 葉螢影 / 螢影 | Ye Yingying / Yingying | Ye Wei's half-sister from the mainland (ep 3) |
| 銀婆婆 | Granny Yin | Ye Wei's grandmother |
| 先鋒 | Xianfeng | the father's partner in Guangzhou; pushes him toward drug smuggling |
| 家佑 | Jiayou | Yimei's simple-minded younger brother, so Jinchao's 小舅子 (brother-in-law); likely 林家佑, the fire victim in ep 1's trial |
| 鬼王森 | Ghost King Shen | the screen also writes 森 for 深; keep the Chinese as shown, English always "Ghost King Shen" |
| 豬肉榮 | Pork Rong | Zhixiong's football mate |
| 玲玲 | Lingling | friend Ma Jie tries to set up with Zhixiong |
| 玻璃球 | marbles | Yingying buys herself one whenever she's unhappy |
| 撈偏門 | the shady side | people in illegal trades |
| 玉姨 | Auntie Yu | what Zhixiong calls Mum (Yuqing) |
| 伯母 | Auntie | what Ah Li and Zhixiong call Mum |
| 立SIR | Sergeant Li | Ah Li after his promotion (升為警長) |
| 條子 | cop | slang for a policeman |
| 姨父 / 姨丈 | my uncle | Yingying's abusive uncle on the mainland, who raised her |
| 貴人 | benefactor | key word in the father's fortune (ep 6) |
| 飆車 | street race | street racing; Ma Hai's racing circle |
| 北帝爺 / 北帝 | Lord Pak Tai | the deity the father prays to |
| 求籤 / 籤 | draw a fortune stick / fortune slip | temple divination |
| 白粉 | heroin | |
| 貨版 | samples | cover story for the drug run (heroin hidden in stone tiles) |
| 堅叔 | Uncle Jian | |
| 團年 / 團年飯 | New Year dinner | |
| 謝灶 | the Kitchen God offering | |
| 馬子 | bird (girlfriend) | slang |
| 馮燦 / 馮哥 | Feng Can / Brother Feng | Hunan-gang mainland buyer the father double-sold Shen's shipment to |
| 湖南幫 | the Hunan gang | Feng Can's gang |
| 森哥 | Brother Shen | same man as 深哥 / Ghost King Shen |
| 阿九 | Ah Jiu | mainland relative whose son is marrying (the New Year trip) |
| 馬太太 | Mrs Ma | how Ye Wei addresses Mum |
| 郭先鋒 | Guo Xianfeng | Xianfeng's full name |
| 錦叔 | Uncle Jin | what Xianfeng calls Jinchao |
| 程進 | Cheng Jin | Guangzhou police officer |
| 李捷 | Li Jie | his colleague, "Comrade Li Jie" |
| 盲炳 | Blind Bing | detainee at the station |
| 白粉雄 | Heroin Xiong | dealer Yimei worked for when young |
| 公安 / 公安同志 | the police / comrade officer | mainland police |
| 黃大仙 | Wong Tai Sin | temple |
| 寺前大街 | Siqian Street | Guangzhou street (the fire site) |
| 燒鵝 | roast goose | also used to hide heroin (ep 8) |
| 林記 / 林記運輸公司 | Lin's / Lin's Transport | the company on Siqian Street (Yimei's family firm; Jinchao is boss, Xianfeng legal representative) |
| 林家佑 | Lin Jiayou | Jiayou's full name; the owner's son (少東) |
| 何光復 / 阿Q | He Guangfu / Ah Q | company electrician who saw the killers (ep 9) |
| 211林記專案小組 | the "211 Lin's" task force | joint police task force |
| 地磚 | floor tiles | the crates the heroin came through in |
| 法人代表 | legal representative | |
| 馮顯平 | Feng Xianping | Guangzhou lawyer who agrees to help Ma Hai (ep 10) |
| 光州律師事務所 | the Guangzhou Law Office | his firm |
| 檢察院 / 檢查院 | the procuratorate | prosecutes and issues warrants; screen also writes 檢查院 |
| 看守所 | the detention centre | |
| 和合石 | Wo Hop Shek | Hong Kong cemetery |
| 起訴書 / 逮捕證 | indictment / arrest warrant | |
| 貨辦 | samples | screen spelling of 貨版 |
| 審判員 / 審判長 | adjudicator / presiding adjudicator | mainland trial judges; ep 11 dialogue contrasts them with HK 法官 "judge", so keep "adjudicator" |
| 合議庭 | collegiate bench | panel of three adjudicators |
| 辯護人 | defence counsel / defender | |
| 法警 | court police | |
| 公、檢、法 | the police, the procuratorate and the court | |
| 彭建希 | Peng Jianxi | prosecutor, Guangzhou Intermediate People's Procuratorate |
| 何英 | He Ying | adjudicator on the bench |
| 陳天雄 | Chen Tianxiong | adjudicator (presiding) |
| 老楊 | Old Yang | court staffer |
| 年三十 | New Year's Eve | Ma Jie's trial date, 16 Feb 1988 |
| 李國源 | Li Guoyuan | police officer who found the heroin in the goose |
| 旁聽證 | public gallery pass | |
| 公訴人 / 公訴詞 | public prosecutor / prosecutor's statement | |
| 最高法院 / 覆核 | Supreme Court / review | death sentences are reviewed in Beijing |
| 書記 | Party secretary | |
| 初七 | the seventh day of the New Year | Ma Jie's execution date |
| 阿媚 | Yimei | Jinchao's pet name for Yimei |
| 富安大廈 | Fu On Building | the family's Sham Shui Po address |
| 槍決 / 槍斃 | execution by shooting | |
| 梁SIR | Mr Leung | Ma Jie's Form One teacher |
| 占士甸 | James Dean | |
| 碗仔糕 | bowl pudding | Ma Jie's favourite, from Fat Kee (發記) |
| 秀垛 (秀逗) | out of your mind | screen spelling |
| 檢察員 / 檢查員 | prosecutor | always "prosecutor" (not "procurator"); the institution 檢察院 is "the procuratorate" |
| 王尚東 | Wang Shangdong | prosecutor who reopens Yuqing's case (ep 14) |
| 煤油燈 | kerosene lamp | the detail that exposed the coached confession |
| 誤殺 | manslaughter | |
| 自首 | turn oneself in | |
| 阿嬌 | Ah Jiao | Wang Shangdong's wife, Xianfeng's elder sister (so Wang is Xianfeng's 姊夫) |
| 源哥 | Brother Yuan | the indebted officer they bribe; probably Li Guoyuan |
| 老張同志 | Comrade Zhang | father of the pregnant woman |
| 張瓊 | Zhang Qiong | the pregnant woman (Yuqing's alibi); died in childbirth |
| 李紅 | Li Hong | prosecution witness |
| 光州市中級人民法院 | the Guangzhou Intermediate People's Court | |
| 死緩 | death sentence suspended for two years | Yuqing's sentence (ep 15) |
| 剝奪政治權利終身 | deprived of political rights for life | |
| 金豬 / 還神 | roast pig / thank-you offering to the gods | |
| 舅舅 | Uncle | how Ma Yang's side refer to Jiayou |
| 申訴 | file a petition | asking a higher court to review a case |
| 高級人民法院 | the Higher People's Court | |
| 絕食 | hunger strike | |
| 全家福 | family photo | |
| 遺書 | last letter | |
| 飛碟 | frisbee | |
| 女侍應 | waitress | Yingying's job |
| 碼頭 | the pier | |
| 申訴信 / 申訴書 | petition letters / petitions | |
| CALL / 覆CALL / 查CALL | page / return a page / check pager messages | paging service (九九一台 = operator 991) |
| 送花使者 | flower messenger | Ma Yang, courting Ye Wei |
| 墳場 | the cemetery | |
| 便當 | boxed meal | |
| 貧血 / 血壓低 | anaemic / low blood pressure | Yuqing's illness in prison |
| 姨媽 | Auntie | Mum's sister; supports Ma Hai |
| 李先生 | Mr Lee | buyer of Ma Jie's vegetable stall |
| 李大叔 | Uncle Lee | at the herbal shop near Granny Yin's |
| 銀婆 | Granny Yin | short form of 銀婆婆 |
| 聘金 | betrothal money | |
| 專案小組 | task force | |
| 黃太太 | Mrs Wong | name used for the pregnant Yingying at the clinic |
| 權哥 | Brother Quan | Ma Hai's boss on a decorating job |
| 李太太 | Mrs Lee | Ma Hai and Yingying's landlady (not the stall buyer 李先生) |
| 馬承志 / 承志 | Ma Chengzhi / Chengzhi | Ma Hai and Yingying's son, born ep 20 |
| 裙褂 | wedding gown | traditional red bridal dress |
| 龍鳳被 | dragon-and-phoenix quilt | wedding bedding |
| 新華社 | the Xinhua news agency | |
| 特別小組 | special task force | reinvestigating Yuqing's case |
| 平反 / 覆查 | overturn the conviction / review | |
| 拍拖 | go out (dating) | |
| 港澳同胞 | compatriot from Hong Kong and Macau | |
| 阿同 / 李同 | Ah Tong / Li Tong | mainland con man taking Ma Hai's money; secretly working for Ma Jiang and Jinchao (ep 22) |
| 程先生 / PETER | Mr Cheng / Peter | Principal Cheng, courting Yingying |
| 阿儀 | Ah Yi | Wanyi's pet name |
| 太古城 | Taikoo Shing | where the second family has a flat |
| 福臨門 | Fook Lam Moon | restaurant |
| 翻版CD | pirate CDs | Ma Jiang's smuggling scheme |
| 木屋 | squatter hut | where Ma Hai lives in 1994 |
| 老幹部 | veteran cadre | |
| 疏通 | pull strings | |
| 美容院 | beauty salon | Ye Wei's workplace |
| 丁鍊鋼 / 丁檢 | Ding Liangang / Prosecutor Ding | Supreme People's Procuratorate prosecutor who reopens Yuqing's case (ep 23); also the prosecutor in ep 1's retrial |
| 最高人民檢查院 | the Supreme People's Procuratorate | screen spelling (檢查 for 檢察) |
| 港澳台辦公室 | the Hong Kong, Macau and Taiwan Affairs Office | |
| 電機房 | generator room | where Ah Q was on duty |
| 官倒 | official profiteering | |
| 黑龍江 | Heilongjiang | where Ah Q's letter came from |
| 馮副局長 | Deputy Director Feng | Guangzhou police deputy chief, arrested ep 24 for the cover-up |
| 雜貨舖 | the grocer's | its sales record shows Xianfeng bought petrol the night of the fire |
| 徇私舞弊 | bend the law for personal reasons | Wang Shangdong's admission |
| 大義滅親 | turn in one's own family | |
| 婉儀 | Wanyi | Chengzhi's little classmate |
| 蔚姨 | Auntie Wei | what Chengzhi calls Ye Wei |
| 海叔叔 / 海叔 | Uncle Hai | what Wanyi calls Ma Hai |
| 程校長 | Principal Cheng | head of the dance school that hires Yingying |
| 強哥 | Brother Qiang | gambling-den boss Jinchao owes 800,000-odd |
| 見習生 | trainee | |
| 總督杯 | the Governor's Cup | horse race |
| 馬經 | the racing form | |
| TINA | Tina | the second family's maid |
| 大會堂 | City Hall | |
| 一九九四年 | 1994 | the story jumps to Hong Kong, 1994, at the end of ep 20; Ma Hai now drives a taxi |
| 玉青 | Yuqing | the screen sometimes writes 玉青 for 玉卿; keep the Chinese as shown |
| 馮哥 | Brother Feng | Guangzhou loan shark |
| 光州市 | Guangzhou | the show's spelling (光 not 廣) for the mainland city; kept as Guangzhou |
| 狐狸精 | vixen | what each wife calls the other |
| 冬節 / 過冬節 | the Winter Solstice | family festival; 冬比年大 "the Solstice matters more than New Year" |
| 大陸 | the mainland | |
| 高利貸 / 放高利貸的 | loan sharks | |
| 跑船 / 海員 | go to sea / sailor | |

---

## Cost and time

Per 46-minute episode: OCR about 2 min, sheets about 1 min, then reading about 50 sheets and writing about 500 review lines, plus the second check. That last part is most of the time, roughly **30–45 minutes of Claude working**. It's also a real share of the user's subscription usage, so expect to hit usage limits after a few episodes and continue in the next window. Work **one episode at a time**, finishing each, second check included, before starting the next.

### Measured on the full 生死訟 run (25 episodes, 10,806 cues)

- Per episode: 5–13 min of agent time (OCR ~2–3 min, the rest is reading ~50 sheets and writing ~500 lines), plus ~1 min to verify. Eps 1–3, done by hand while the method was being built, took ~1 h each.
- OCR + rules was exactly right on only **32–44%** of lines in every episode; the frame reading is what makes the result good.
- **Parallel runs:** only the OCR step is heavy on the user's PC. Running 2–3 episodes' OCR at once made the machine lag, so the user asked for one at a time. If running several, **stagger starts ~3 min apart so only one is in OCR**; up to 4 in flight is then fine. Total usage is the same; it just finishes sooner.
- Delegating an episode: give a fresh subagent the brief (method + this glossary + "read the previous episode's .en.srt for context"); after it reports, verify counts/timings, spot-read 8 random pairs, check long subtitle gaps (>90 s) against frames (they were always silent montage), and merge its new glossary rows before starting the next.
- Low cue counts (ep 13: 241, ep 25: 228) were genuine: long wordless scenes, checked against the frames.

## Known limits

- The pass only checks cues the OCR found. A subtitle the OCR missed entirely stays missing.
- The red-calligraphy theme-song lyrics in the opening are not picked up (the glyph mask rejects them on purpose).
- Romanised names are pinyin, chosen in episode 2. If the user prefers Cantonese romanisation (Ma Hoi, …), change the glossary and say so.
