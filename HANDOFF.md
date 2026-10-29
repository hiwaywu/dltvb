# HANDOFF — TVBAnywhere series downloader

**Audience:** the next AI assistant taking over this session.
**Owner pattern:** the user pastes a `tvbanywherena.com` link and says "dl this" / "download this series".
**Your job:** download every episode at the highest quality available, verify it, tell them where it went.

> ⚠️ **ALWAYS the highest quality. No exceptions, no asking, no downgrading for speed.**
> The tool selects the best source automatically (§4) — you do not need to choose.
> Never substitute a smaller/faster file for a better one.
>
> ⚠️ **ALWAYS download subtitles.** They are fetched and embedded by default
> (`--no-subs` / `--no-mux` exist only if the user explicitly asks to skip them).

Everything needed already exists and is tested. Do **not** re-derive the API, do **not** scrape HTML, do **not** ask clarifying questions. Run the tool.

---

## 1. The one command that does the whole job

```powershell
cd "C:\AI STUFF\Local\tvbaw"
$env:PYTHONIOENCODING='utf-8'
python tvbaw.py dl "<PASTED_URL>" -c 8 -j 3
```

That is the default and correct answer to "dl this series, highest quality".

- **highest quality is automatic** — the tool compares the progressive MP4 against the best HLS variant and takes whichever has more pixels; ties go to the MP4 (already muxed, much faster). Do not pass `--source` unless the user explicitly asks for a specific one.
- **subtitles are automatic** — caption tracks are fetched and embedded as soft tracks by default. No flag needed.
- `-c 8` — 8 parallel HTTP range connections per episode (measured sweet spot; more stops helping)
- `-j 3` — 3 episodes at once (the CDN caps near **35 MB/s total**; raising `-j` does not go faster)
- `--subs` — also fetch caption tracks if the title has them
- `--mux` — embed those captions as soft subtitle tracks inside the mp4

Accepted `ref` forms — all equivalent, pass through whatever the user pasted:

```
https://tvbanywherena.com/en/series/3751-This-City-Never-Sleeps?lang=en
https://tvbanywherena.com/en/watch/3751-This-City-Never-Sleeps/1878611366079145628?lang=en
3751-This-City-Never-Sleeps
3751
```

---

## 2. Runbook (follow in order)

**Step 1 — inspect first (2 seconds).** Never start blind; report what you found.

```powershell
python tvbaw.py series "<URL>"
```

Output gives: series name, episode count, which language playlists exist, and every episode id.
Tell the user: *"`<name>` — N episodes, language `<tc>`"*.

**Step 2 — confirm the quality ceiling and subtitles** (only if they asked about quality, or the title looks old):

```powershell
python -c "import sys;sys.path.insert(0,'.');import tvbaw as T;info,eps=T.episodes('<REF>');L=T.episode_links(eps[0]);print('subs:',[(s['lang']) for s in L['subs']]);print(L['mp4'][:90])"
```

Then `ffprobe` the `mp4` URL for `width,height,display_aspect_ratio`. See §4 for what to expect.

**Step 3 — check free disk.** `Get-PSDrive C` → `Free`. Rule of thumb: episodes run **300 MB – 1.3 GB each**; 30 episodes ≈ 8–38 GB.
If free space < needed, tell the user *before* starting.

**Step 4 — download in the background** (it takes minutes; don't block the turn on it):

Use the tool's background facility, then poll the folder — **do not** dump the progress stream into the conversation, it is enormous. Check progress like this instead:

```powershell
Get-ChildItem "downloads\<series name>" -Filter *.mp4 -File | Where-Object { $_.Name -notlike '*.part' } | Measure-Object Length -Sum
```

**Step 5 — verify with ffprobe before declaring success.** For every file: resolution must match the expected ceiling, duration must match the site's listed duration (±2 s), and there must be **no leftover `.part` files**.

**Step 6 — report.** Absolute folder path, file count, total GB, resolution, subtitle status. Keep it short.

---

## 3. Where files go — naming convention

```
C:\AI STUFF\Local\tvbaw\downloads\
    English Name - Chinese Name - Year\          <- one folder per series
        001 Episode 01.mp4
        001 Episode 01.en.vtt                      (sidecar, if the title has captions)
        001 Episode 01.zh-HK.vtt
```

**Folder format is exactly `English Name - Chinese Name - Year`**, e.g.

```
The Intangible Truth - 生死訟 - 1990
This City Never Sleeps - 璀璨之城 (EN) - 2026
Hong Kong 86 - 香港‘86 - 1980
```

- `English Name` comes from the URL slug (`2906-The-Intangible-Truth` → `The Intangible Truth`)
- `Chinese Name` is Brightcove's `program_name`
- `Year` comes from the series JSON-LD `datePublished` (falling back to the browse-year chip). It is the site's own value — occasionally that is the decade bucket rather than the exact air year (`香港‘86` → `1980`), so report it as "the year the site lists", not as an authoritative air date.
- `--name en|native|both` changes the title style; `both` (default) is the format above.
- Episode files are `NNN Episode NN.mp4` — `第01集` is transliterated to `Episode 01`, so they sort correctly and are readable without Chinese input.
- `NNN` is zero-padded, so a folder sorts in viewing order.
- Subtitle sidecars share the video's stem — every player (VLC, mpv, Plex, Jellyfin) auto-detects them. With muxing on (default) they are *also* inside the mp4, so the `.vtt` files are optional.

---

## 4. "Highest quality" — automatic, and here is the rule

The site exposes **two source types** per episode, and the tool always takes the better one:

| Source | What it is | Why it matters |
|---|---|---|
| `pmp4` progressive MP4 | one pre-muxed file, `Accept-Ranges: bytes` | usually the top rendition; supports 8-way ranged download |
| HLS ladder (`master.m3u8`) | 3–5 variants, AES-clear | sometimes the *only* source (~47 titles in the 2000s category); tops out at 720p even when the MP4 is 1080p |

**Selection rule implemented in `best_source()`:**

1. Read the HLS ladder, take the variant with the largest `RESOLUTION` (tie → higher `BANDWIDTH`).
2. If an MP4 exists **and** the HLS ladder reaches ≥ 1280×720, `ffprobe` the MP4 and compare pixel area.
3. Take the larger. **On a tie, take the MP4** (already muxed, no remux, far faster).
4. No MP4 at all → download the best HLS variant through `ffmpeg -c copy` (≈23 MB/s, single stream).

Verified behaviour:

| Series | Decision |
|---|---|
| 3751 This City Never Sleeps | `mp4 1920×1080` (HLS only reached 720p) |
| 2321 Hong Kong '86 | `mp4 1920×1080` |
| 2906 The Intangible Truth | `mp4` 640×480 4:3 (HLS identical — nothing better exists) |
| 3419 Pages of Treasures | `hls 640×480` (MP4 does not exist; fallback used) |

**Do not "optimise" this into a guess.** Never claim 1080p for a catalogue title without probing — many 1990s–2010s masters are **640×480 with `display_aspect_ratio=4:3`**, and that genuinely is the ceiling. Report what you measured.

`--source mp4|hls` exists only as a manual override, for when the user asks for a specific stream.

If the user asks for "the captcha/720p thing" — that's the **aidrama** site, not TVBAnywhere. See §7.

---

## 5. Subtitles — always on

- Caption tracks are fetched **by default** on every download, and embedded as `mov_text` soft tracks tagged `eng` / `zho`. `--no-subs` and `--no-mux` exist only for the rare case the user asks to skip them.
- **Newer titles have them**, usually two tracks: `en` and `zh-HK` (WebVTT).
- **Older catalogue titles have none** — only thumbnail metadata. Check ep 1, a middle ep, and the last ep before telling the user a title has no subs.
- Backfill for an already-downloaded series: `python tvbaw.py subs <REF>` (muxing is on by default; `--no-mux` for sidecars only).
- **Burn-in caveat:** some titles (e.g. `生死訟` / 1754 / 2906) have *hardcoded* Chinese subtitles in the picture and no tracks at all. A player cannot translate those. The fix is OCR → SRT → translate (see §7).

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `playback 403 ... ACCESS_DENIED / DOMAIN` | Missing `Origin` header | The tool always sends `Origin: https://tvbanywherena.com`; if hand-calling the API, you must add it |
| `VIDEO_NOT_PLAYABLE` | Title is geo/policy locked on Brightcove | Report it and move on; ~4 such series exist in the 2000s category alone |
| Download stalls or 403s mid-transfer | `fastly_token` in the media URL expired | The tool re-resolves and retries automatically; a manual retry is `python tvbaw.py dl <REF>` again |
| `no MP4 source` | Title is HLS-only | Handled automatically — the tool falls back to `ffmpeg` on the best HLS variant. If you see this error, the fallback itself failed: check `ffmpeg` is on PATH |
| `ffmpeg is required for HLS-only titles` | ffmpeg missing | Install ffmpeg; HLS-only titles cannot be fetched without it |
| Batch interrupted | — | Just rerun the same command: finished files are skipped, `.part` files resume |

---

## 7. Other context you may need

- **`aidrama.py`** — second tool in the same folder, for `aidrama.me`. AES-128 HLS with disguised `.html` playlists and `.png` segments. Ceiling **854×480 16:9**, ~170 MB/ep, ~19 MB/s, no gates. Its `Hydrax` server is a trap: **360p preview only**, full 720p is behind 5 ads + Cloudflare Turnstile — do not attempt it.
- **`estimate_category.py`** — sizes up a whole browse category before committing. Already run for the 2000s: 227 shows, 6,204 episodes, **≈4.2 TB, ≈35 h** at line speed.
- **`README.md`** — the full technical write-up of both sites (API chains, measured speeds).
- **`HANDOFF` facts already verified:** Brightcove account `5324042807001`, player `t3oBfbU6IW`, playlist endpoint `GET /api/playlist?bcov=<id>`, policy key scraped from the player config and cached in `.state/policykey.txt`, playlist ids read from `bcovIds` in the series page.

---

## 8. Hard rules

1. **ALWAYS highest quality.** The tool picks the best source automatically — never downgrade for speed, disk, or convenience.
2. **Don't ask which episodes or which quality** — default is all episodes at highest. The user will say if they want a subset.
3. **Don't re-implement the API** — the tool works. If it fails, fix the tool, don't hand-roll curl.
4. **Don't dump download progress** into the chat. Poll the folder.
5. **Always verify with ffprobe** before saying "done".
6. **Quote real numbers** you measured (resolution, size, duration, speed) — never assume.
7. If disk space is short, report it *before* starting rather than filling the drive.
8. Say where the files are, with the absolute path.

---

## 9. Reference timings (measured, this machine)

| Series | Episodes | Quality | Size | Wall clock |
|---|---|---|---|---|
| 璀璨之城 / This City Never Sleeps (3751) | 13 | 1920×1080 | 16.26 GB | ~11 min |
| 生死訟 / The Intangible Truth (2906) | 25 | 640×480 4:3 | 17.67 GB | ~14 min |

Throughput: **32 MB/s** single episode with `-c 8`; **~35 MB/s** aggregate ceiling. A 25-episode series at 1080p is ~30 GB, so budget disk accordingly.
