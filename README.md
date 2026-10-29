# tvbaw — TVBAnywhere North America downloader

Scratch-built tool for `tvbanywherena.com`. Resolves a series to **all** episode
links in one shot and downloads them at line speed. No DRM, no decrypt, no remux.

## What the site actually does (measured 2026-10-28)

| Layer | Finding |
|---|---|
| Frontend | Next.js App Router at `tvbanywherena.com` |
| Video platform | **Brightcove** — account `5324042807001`, player `t3oBfbU6IW` |
| Playlist | `GET /api/playlist?bcov=<playlistId>` → **every episode's metadata in one JSON** (132 eps for show 2321) |
| Playlist id | embedded in the series page as `bcovIds:{"tc":"...","vn":"..."}` (per-language dub tracks) |
| Playback | `edge.api.brightcove.com/playback/v1/accounts/5324042807001/videos/<id>` — requires header `Origin: https://tvbanywherena.com`, plus a public policy key scraped from the player config |
| DRM | **none** — sources are `clear` |
| Best source | `pmp4` progressive MP4: **1920×1080 h264 + aac stereo**, ~314 MB / 20 min, `Accept-Ranges: bytes` |
| HLS ladder | 480x270 → 1280x720 only (top ≈ 2.0 Mbps) — *worse* than the MP4 |
| Auth | not needed for `UNRESTRICTED` titles; playback is IP/Origin-gated only |

Because the MP4 is already a complete 1080p file, the fastest strategy is
plain parallel HTTP range requests — no segment fetching, no ffmpeg.

Measured on this machine: **~32 MB/s** single episode with 8 connections;
~35 MB/s aggregate ceiling with 4 episodes in parallel (≈280 Mbit line cap).
A 132-episode series (~41 GB) lands in roughly 20 minutes.

## Usage

```powershell
cd "C:\AI STUFF\Local\tvbaw"
$env:PYTHONIOENCODING='utf-8'      # Chinese episode titles in console

# list a series (accepts id, id-slug, or any site URL)
python tvbaw.py series 2321
python tvbaw.py series "https://tvbanywherena.com/en/watch/2321-Hong-Kong-86/1731551693726623908"

# dump every direct URL at once (mp4 + hls per episode) -> links-2321.json
python tvbaw.py links 2321 --out links-2321.json
python tvbaw.py links 2321 --print-urls        # pipe into curl/aria2/IDM

# download — best quality and subtitles are automatic
python tvbaw.py dl 2321                        # whole series, 8 conns/ep, 3 eps parallel
python tvbaw.py dl 2321 -e 1-10,15             # selected episodes
python tvbaw.py dl 2321 -c 12 -j 2             # tuning: conns per ep / eps in parallel
python tvbaw.py dl 1407 --lang vn              # Vietnamese dub playlist
python tvbaw.py dl 2321 --no-subs              # explicit opt-out (default: always fetch)
python tvbaw.py dl 2321 --retry-failed --force

# list series on a browse page
python tvbaw.py browse "https://tvbanywherena.com/en/browse?year=1980s&page=5" --out p5.txt
```

Output layout:

```
downloads/English Name - Chinese Name - Year/001 Episode 01.mp4
                                            001 Episode 01.en.vtt
                                            001 Episode 01.zh-HK.vtt
```

e.g. `The Intangible Truth - 生死訟 - 1990/`, `This City Never Sleeps - 璀璨之城 (EN) - 2026/`.
English name comes from the URL slug, Chinese from Brightcove's `program_name`,
year from the series JSON-LD `datePublished`. Episode titles are transliterated
(`第01集` → `Episode 01`) so folders sort naturally.

## Behaviour

- **Auto key refresh** — policy key is scraped from the Brightcove player and cached
  in `.state/policykey.txt`; re-fetched automatically on a 403.
- **Token-aware** — Brightcove's `fastly_token` in each media URL is short-lived, so
  the URL is re-resolved right before each download and again mid-transfer if a
  range request returns 403/410.
- **Resumable** — preallocated `.part` file; re-running skips completed files
  (`--force` to redownload).
- **Failures isolated** — one bad episode doesn't kill the batch; `--retry-failed`
  re-queues them.

## Speed notes

- `-c 8` is the sweet spot per episode; more connections stop helping above ~8.
- `-j 3` for whole-series batches. Raising `-j` does not raise total throughput —
  the CDN path caps near 35 MB/s.
- `links` output URLs expire (signed token). Use them promptly or re-run `links`;
  `dl` never has this problem because it re-resolves per episode.

---
---

# aidrama.py — aidrama.me (WordPress + obfuscated HLS)

Different site, completely different pipeline. `aidrama.me` is a WordPress site
whose "player" is a three-hop iframe chain ending in an AES-128 HLS stream with
every anti-scrape trick in the book: disguised extensions, rotating segment
hosts, and a playlist served as `.html`.

## The chain (measured 2026-10-28)

| Layer | Finding |
|---|---|
| Index | WordPress REST API is **open**: `/wp-json/wp/v2/posts?categories=<cat>&per_page=100` returns every episode post *with content* in one call |
| Episode id | post content holds `<IFRAME SRC="http://cc.videoss.online/video/<b64>.html">` |
| Player API | `POST kk.videoss.online/api/sources {video}` → `servers[{hls},{hydrax}]` |
| | `POST kk.videoss.online/api/playback {video, server:"hls"}` → iframe url |
| **Guard** | API rejects requests unless `Origin: https://kk.videoss.online` (anything else → `403 {"message":""}`; missing field → `422`) |
| Embed | `hls.techxzen.com/embed/<md5>.html` → `player.js` builds `/file/<md5>/playlist.m3u8` |
| Disguise | playlist is also served as **`playlist.html`** — the desktop player fetches it as a blob and feeds JW Player, so it never appears as an m3u8 in DevTools |
| Encryption | `#EXT-X-KEY:METHOD=AES-128,URI=".../key.html",IV=0x…` — key is 16 bytes at `key.html` |
| Segments | 559 chunks named **`image_N.png`** spread across 5 rotating hosts: `123dad.fit / .ink / .store / .blog / .online` |
| Quality | single variant, **854×480 h264 + aac**, ~46 min/ep, ~170 MB after remux |

`player.js` detail that matters: on desktop it does
`URL.createObjectURL(await (await fetch('/file/<hash>/playlist.html')).blob())`.
That `.html` payload *is* the m3u8, key line and all.

## Usage

```powershell
cd "C:\AI STUFF\Local\tvbaw"
$env:PYTHONIOENCODING='utf-8'

python aidrama.py series "https://aidrama.me/the-intangible-truth-%e7%94%9f%e6%ad%bb%e8%a8%9f/"
python aidrama.py links  "https://aidrama.me/.../" --out links-x.json
python aidrama.py dl     "https://aidrama.me/.../"                    # all 25 eps
python aidrama.py dl     "https://aidrama.me/.../" -e 1-5 -c 16 -j 2
python aidrama.py dl     "https://aidrama.me/.../" --ts                # keep raw .ts
```

An episode URL works as input too — the `-episode-NN` suffix is stripped
automatically.

Output: `downloads/<series>/<NNN> <Episode title>.mp4`

## Behaviour

- Segments download in parallel (`-c`), are **decrypted per-segment with the
  playlist IV** (HLS restarts CBC at each segment), assembled in order through a
  single writer, then remuxed `ffmpeg -c copy -bsf:a aac_adtstoasc`.
- Episode index is taken from the series' WordPress category, with a fallback
  that scrapes `-episode-NN` links out of the landing post and resolves each slug.
- `hydrax` (the site's second server) is left unimplemented: its page is packed
  behind a `SoTrym`/`atob` obfuscated loader with an encrypted `media` blob. The
  `hls` server is the default and works for every episode tested.

## Speed notes

- ~19 MB/s per episode at `-c 16`; 2 parallel episodes ≈ 25 MB/s aggregate.
- Whole 25-episode series ≈ 4.3 GB ≈ 4 minutes.
- Nothing expires mid-flight (no signed tokens), so long batches are safe.

## The `Hydrax` server — investigated, not worth it

The player offers two buttons, `HLS` and `Hydrax`. Hydrax was traced end to end
(2026-10-28) and is a deliberately gated dead end for archiving:

| Path | What it actually yields |
|---|---|
| `playhydrax.com/?v=<slug>` → `abyss.to` → `player.abyssplayer.com/<id>` | **634 s preview only**, 640×360, 77.6 MB |
| `content.openx.xyz/api/get-video-info?v=<slug>` | open; lists real sources: 360p = 129.9 MB, **720p = 419.9 MB** (full 46 min) |
| `content.openx.xyz/api/export?videoId&hash&token&filename` | the full 720p — but requires **watching an ad to unlock** *and* a **Cloudflare Turnstile** token. Empty token → 403, junk token → 400 |

Details worth recording:

- Hydrax plays from `storage.googleapis.com/mediastorage/<ms>/<11-char>/<id>.mp4`
  where `<ms>` is a creation timestamp and the middle segment is a per-attempt
  random token. The URL is short-lived and effectively single-use — re-fetching
  it even 15 s later, or through the browser's own request context, returns 403
  `UserProjectAccountProblem`. Requests must be taken at creation time.
- The media request is issued by a **service worker**, so `page.route` misses it
  unless the context is created with `serviceWorkers: 'block'`.
- The player bundles (`iamcdn.net/player-v2/lite.bundle.js`) are obfuscator.io
  output. `window.SoTrym` is only a wrapper — it refuses to run unless
  `top !== self` (i.e. actually framed) or the host matches `*.abyss.to`, then
  forwards to `_0xf90789`, which loads hls.js and builds the JW Player.
- `config.preview === true` in the page's `datas` blob is what shortens playback;
  `api/export` returns the full-length file, which is why the advertised sizes
  (419.9 MB / 2790 s ≈ 1.2 Mbps) are full-episode and the player's 77.6 MB is not.

**Conclusion:** for downloading, `hls` is strictly better — full episode, no ad,
no captcha, AES key served openly. Hydrax only wins on resolution (720p vs 480p)
and that 720p is unreachable without watching the ad per episode.
`aidrama.py` therefore keeps `hls` as the only implemented server.
