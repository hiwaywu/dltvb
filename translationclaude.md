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
| 家佑 | Jiayou | errand boy in Guangzhou (林家佑, the fire victim in ep 1's trial) |
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

## Known limits

- The pass only checks cues the OCR found. A subtitle the OCR missed entirely stays missing.
- The red-calligraphy theme-song lyrics in the opening are not picked up (the glyph mask rejects them on purpose).
- Romanised names are pinyin, chosen in episode 2. If the user prefers Cantonese romanisation (Ma Hoi, …), change the glossary and say so.
