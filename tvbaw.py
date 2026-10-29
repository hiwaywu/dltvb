#!/usr/bin/env python3
"""
tvbaw.py - TVBAnywhere NA (tvbanywherena.com) episode/series downloader.

Pipeline (all public, no login needed for UNRESTRICTED titles):
    series page  ->  bcovIds.tc (Brightcove playlist id)
    /api/playlist?bcov=<pid>  ->  full episode manifest (all episodes at once)
    edge.api.brightcove.com/playback/v1  ->  sources (needs Origin header)
    pmp4 source  ->  1080p progressive MP4, HTTP range, no DRM, no remux

Usage:
    python tvbaw.py series  2321                    # list episodes
    python tvbaw.py links   2321 --out links.json   # all direct URLs at once
    python tvbaw.py dl      2321                    # whole series
    python tvbaw.py dl      2321 -e 1-10,15 -c 8 -j 3
    python tvbaw.py dl      "https://tvbanywherena.com/en/watch/2321-Hong-Kong-86/1731551693726623908"
    python tvbaw.py browse  "https://tvbanywherena.com/en/browse?year=1980s&page=5"
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable
from urllib.parse import urljoin, urlparse, parse_qs

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

BASE = "https://tvbanywherena.com"
ACCOUNT = "5324042807001"
PLAYER_CFG = f"https://players.brightcove.net/{ACCOUNT}/t3oBfbU6IW_default/index.html"
PLAYBACK = "https://edge.api.brightcove.com/playback/v1/accounts/{acct}/videos/{vid}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".state")
PK_CACHE = os.path.join(STATE_DIR, "policykey.txt")

_local = threading.local()
_pk_lock = threading.Lock()
_policy_key: str | None = None


# --------------------------------------------------------------------------- http
def session() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({
            "User-Agent": UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": BASE,
            "Referer": BASE + "/",
        })
        _local.s = s
    return s


def get(url: str, *, tries: int = 4, timeout: int = 40, **kw) -> requests.Response:
    last = None
    for i in range(tries):
        try:
            r = session().get(url, timeout=timeout, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                time.sleep(1.5 * (i + 1))
                continue
            return r
        except requests.RequestException as exc:
            last = str(exc)
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET failed ({last}): {url[:140]}")


# ---------------------------------------------------------------- policy key
def policy_key(refresh: bool = False) -> str:
    global _policy_key
    with _pk_lock:
        if _policy_key and not refresh:
            return _policy_key
        if not refresh and os.path.exists(PK_CACHE):
            cached = open(PK_CACHE, "r", encoding="utf-8").read().strip()
            if cached.startswith("BCpk"):
                _policy_key = cached
                return cached
        html = get(PLAYER_CFG).text
        m = re.search(r"BCpk[A-Za-z0-9_\-\.]+", html)
        if not m:
            raise RuntimeError("policy key not found in player config")
        _policy_key = m.group(0)
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(PK_CACHE, "w", encoding="utf-8") as fh:
            fh.write(_policy_key)
        return _policy_key


# ------------------------------------------------------------- series resolve
def normalize_ref(ref: str) -> str:
    """Accept id, id-slug, slug, or any tvbanywherena URL -> canonical series page URL."""
    ref = ref.strip()
    if ref.startswith("http"):
        p = urlparse(ref)
        m = re.search(r"/(?:en/)?(series|watch)/([^/?#]+)", p.path)
        if not m:
            raise SystemExit(f"not a series/watch URL: {ref}")
        slug = m.group(2)
        if m.group(1) == "watch":
            # /watch/<id-slug>/<videoid>  -> keep only the slug part
            slug = slug
        return f"{BASE}/en/series/{slug}"
    if re.fullmatch(r"\d+", ref):
        return f"{BASE}/en/series/{ref}"
    return f"{BASE}/en/series/{ref}"


def resolve_series(ref: str) -> dict:
    url = normalize_ref(ref)
    r = get(url)
    final = r.url
    html = r.text

    slug = final.rstrip("/").split("/en/series/")[-1].split("?")[0]
    sid = slug.split("-", 1)[0]

    bcov_ids: dict[str, str] = {}
    m = re.search(r'bcovIds\\?":\{(.{0,600}?)\}', html, re.S)
    if m:
        for lang, pid in re.findall(r'\\?"([A-Za-z\-]+)\\?"\s*:\s*\\?"(\d+)\\?"', m.group(1)):
            bcov_ids[lang] = pid

    title = slug
    tm = re.search(r"<title>([^<|]+)", html)
    if tm:
        title = tm.group(1).strip()
    nm = re.search(r'program_name\\?":\\?"([^"\\]+)', html) or \
         re.search(r'"name"\s*:\s*"([^"]{2,80})"\s*,\s*"account_id"', html)
    series_name = nm.group(1) if nm else title

    return {
        "id": sid,
        "slug": slug,
        "url": final,
        "name": series_name,
        "title": title,
        "bcov_ids": bcov_ids,
        "default_bcov": next(iter(bcov_ids.values()), ""),
        "html_len": len(html),
    }


def playlist(pid: str) -> list[dict]:
    r = get(f"{BASE}/api/playlist?bcov={pid}")
    if r.status_code != 200:
        raise RuntimeError(f"playlist {pid}: HTTP {r.status_code}")
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError(f"playlist {pid}: unexpected payload")
    eps = [e for e in data if e.get("id")]
    eps.sort(key=lambda e: int((e.get("custom_fields") or {}).get("episode_number") or 0))
    return eps


def episodes(ref: str, lang: str | None = None) -> tuple[dict, list[dict]]:
    info = resolve_series(ref)
    if not info["bcov_ids"]:
        raise SystemExit(f"no Brightcove playlist found for {info['slug']}")
    pid = info["bcov_ids"].get(lang) if lang else info["default_bcov"]
    if not pid:
        raise SystemExit(f"lang '{lang}' not available; have {list(info['bcov_ids'])}")
    info["lang"] = lang or next(iter(info["bcov_ids"]))
    info["playlist_id"] = pid
    info["episodes"] = playlist(pid)
    return info, info["episodes"]


def ep_label(ep: dict, idx: int) -> str:
    cf = ep.get("custom_fields") or {}
    num = cf.get("episode_number") or str(idx)
    try:
        num = f"{int(num):03d}"
    except (TypeError, ValueError):
        pass
    name = (cf.get("title_chi") or ep.get("name") or f"ep{num}").strip()
    return f"{num} {name}"


def ep_title(ep: dict) -> str:
    cf = ep.get("custom_fields") or {}
    return (cf.get("title_chi") or ep.get("name") or ep["id"]).strip()


def duration_s(ep: dict) -> int:
    return int(round((ep.get("duration") or 0) / 1000))


# ---------------------------------------------------------------- playback
def playback(vid: str, refresh_key: bool = False) -> dict:
    pk = policy_key(refresh=refresh_key)
    url = PLAYBACK.format(acct=ACCOUNT, vid=vid)
    r = get(url, headers={"Accept": f"application/json;pk={pk}"})
    if r.status_code == 403:
        body = r.text[:120]
        if "ACCESS_DENIED" in body and not refresh_key:
            return playback(vid, refresh_key=True)
        raise RuntimeError(f"playback 403 for {vid}: {body}")
    if r.status_code != 200:
        raise RuntimeError(f"playback HTTP {r.status_code} for {vid}")
    data = r.json()
    if isinstance(data, list):
        raise RuntimeError(f"playback error for {vid}: {json.dumps(data)[:200]}")
    return data


def pick_sources(data: dict) -> dict:
    out = {"mp4": None, "hls": None, "hls_1080": None}
    for s in data.get("sources") or []:
        src = s.get("src") or ""
        if not src.startswith("https"):
            continue
        if s.get("container") == "MP4":
            out["mp4"] = src
        elif "master.m3u8" in src:
            out["hls"] = src
    return out


_hls_cache: dict[str, list[dict]] = {}
_mp4_dim: dict[str, tuple[int, int]] = {}


def hls_variants(master_url: str) -> list[dict]:
    """Parse the master playlist into [{w,h,bw,url}] (cached per master URL)."""
    if master_url in _hls_cache:
        return _hls_cache[master_url]
    out: list[dict] = []
    try:
        txt = get(master_url).text
        cur = None
        for line in (l.strip() for l in txt.splitlines()):
            if not line:
                continue
            if line.startswith("#EXT-X-STREAM-INF"):
                m = re.search(r"RESOLUTION=(\d+)x(\d+)", line)
                bw = re.search(r"BANDWIDTH=(\d+)", line)
                cur = {"w": int(m.group(1)) if m else 0,
                       "h": int(m.group(2)) if m else 0,
                       "bw": int(bw.group(1)) if bw else 0}
            elif cur is not None and not line.startswith("#"):
                cur["url"] = urljoin(master_url, line)
                out.append(cur)
                cur = None
    except Exception:
        pass
    _hls_cache[master_url] = out
    return out


def mp4_dims(url: str) -> tuple[int, int]:
    """Probe the progressive MP4's resolution (cached, keyed without the token)."""
    key = url.split("?")[0]
    if key in _mp4_dim:
        return _mp4_dim[key]
    dim = (0, 0)
    if shutil.which("ffprobe"):
        try:
            p = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0", url],
                capture_output=True, text=True, timeout=40)
            if p.returncode == 0 and p.stdout.strip():
                parts = p.stdout.strip().split(",")
                dim = (int(parts[0]), int(parts[1]))
        except Exception:
            pass
    _mp4_dim[key] = dim
    return dim


def best_source(data: dict, want: str = "auto") -> dict | None:
    """Pick the genuinely highest-quality source: MP4 vs best HLS variant.

    The progressive MP4 is usually the top rendition (already muxed, range-capable),
    but some titles only expose HLS, and a few have an HLS ladder above the MP4.
    Probing the MP4 resolution costs a round trip, so it only happens when the HLS
    ladder is actually high enough to compete.
    """
    srcs = pick_sources(data)
    mp4 = {"kind": "mp4", "url": srcs["mp4"], "w": 0, "h": 0, "bw": 0,
           "probed": False} if srcs["mp4"] else None

    hls = None
    if srcs["hls"]:
        vs = hls_variants(srcs["hls"])
        if vs:
            top = max(vs, key=lambda v: (v["w"] * v["h"], v["bw"]))
            hls = {"kind": "hls", "url": top["url"], "w": top["w"], "h": top["h"],
                   "bw": top["bw"]}
        else:
            hls = {"kind": "hls", "url": srcs["hls"], "w": 0, "h": 0, "bw": 0}

    def probe(rec: dict) -> dict:
        if rec and not rec.get("probed") and rec["kind"] == "mp4":
            rec["w"], rec["h"] = mp4_dims(rec["url"])
            rec["probed"] = True
        return rec

    if want == "mp4":
        return probe(mp4) if mp4 else None
    if want == "hls":
        return hls
    if mp4 and hls:
        # only worth measuring the MP4 if HLS could plausibly beat it
        if hls["w"] * hls["h"] >= 1280 * 720:
            probe(mp4)
            if mp4["w"] == 0:      # couldn't measure -> MP4 is the historical best
                return mp4
            return mp4 if mp4["w"] * mp4["h"] >= hls["w"] * hls["h"] else hls
        return mp4
    return probe(mp4) if mp4 else hls


def download_hls(url: str, dest: str) -> tuple[int, float]:
    """Fetch an HLS variant through ffmpeg (single stream, stream-copy remux)."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required for HLS-only titles")
    tmp = dest + ".part.mp4"
    t0 = time.time()
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", url,
           "-c", "copy", "-bsf:a", "aac_adtstoasc", "-f", "mp4", tmp]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0 or not os.path.exists(tmp):
        raise RuntimeError("ffmpeg: " + (p.stderr.strip().splitlines() or ["failed"])[-1][:180])
    os.replace(tmp, dest)
    return os.path.getsize(dest), time.time() - t0


def episode_links(ep: dict) -> dict:
    data = playback(ep["id"])
    srcs = pick_sources(data)
    tracks = [{"lang": t.get("srclang") or "", "label": t.get("label") or "",
               "url": t.get("src")}
              for t in (data.get("text_tracks") or [])
              if t.get("kind") == "captions" and t.get("src", "").startswith("http")]
    return {
        "id": ep["id"],
        "title": ep_title(ep),
        "episode": (ep.get("custom_fields") or {}).get("episode_number"),
        "duration_s": duration_s(ep),
        "mp4": srcs["mp4"],
        "hls": srcs["hls"],
        "poster": data.get("poster"),
        "subs": tracks,
    }


def fetch_subs(tracks: list[dict], dest_mp4: str) -> list[str]:
    """Save each caption track as a sidecar .vtt next to the video."""
    out = []
    stem = dest_mp4[:-4] if dest_mp4.lower().endswith(".mp4") else dest_mp4
    for t in tracks:
        lang = safe(t.get("lang") or "und", 12)
        path = f"{stem}.{lang}.vtt"
        if os.path.exists(path) and os.path.getsize(path) > 32:
            out.append(path)
            continue
        try:
            r = get(t["url"])
            body = r.text
            if "-->" not in body:
                continue
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
            out.append(path)
        except Exception:
            continue
    return out


def mux_subs(mp4: str, vtts: list[str]) -> None:
    """Embed sidecar VTTs as soft mov_text tracks (rewrites the file in place)."""
    if not vtts or not shutil.which("ffmpeg"):
        return
    tmp = mp4 + ".mux.mp4"
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", mp4]
    for v in vtts:
        cmd += ["-i", v]
    cmd += ["-map", "0"]
    for i in range(len(vtts)):
        cmd += ["-map", str(i + 1)]
    cmd += ["-c", "copy", "-c:s", "mov_text"]
    lang_map = {"en": "eng", "zh-HK": "zho", "zh-TW": "zho", "zh-CN": "zho", "tc": "zho"}
    for i, v in enumerate(vtts):
        tag = os.path.basename(v).rsplit(".", 2)[-2]
        cmd += [f"-metadata:s:s:{i}", f"language={lang_map.get(tag, 'und')}"]
    cmd += [tmp]
    if subprocess.run(cmd, capture_output=True).returncode == 0:
        os.replace(tmp, mp4)
    elif os.path.exists(tmp):
        os.remove(tmp)


# ---------------------------------------------------------------- downloader
def probe(url: str) -> tuple[int, bool]:
    r = session().get(url, headers={"Range": "bytes=0-0"}, stream=True, timeout=30)
    try:
        if r.status_code not in (200, 206):
            raise RuntimeError(f"probe HTTP {r.status_code}")
        ar = r.headers.get("Accept-Ranges", "").lower() == "bytes" or r.status_code == 206
        cr = r.headers.get("Content-Range")
        total = 0
        if cr and "/" in cr:
            total = int(cr.rsplit("/", 1)[1])
        else:
            total = int(r.headers.get("Content-Length") or 0)
        return total, ar
    finally:
        r.close()


def _fmt(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


class _Progress:
    def __init__(self, total: int, tag: str):
        self.total, self.tag, self.done = total, tag, 0
        self.lock, self.stop = threading.Lock(), False
        self.t0 = time.time()

    def add(self, n: int) -> None:
        with self.lock:
            self.done += n

    def render(self) -> None:
        while not self.stop:
            time.sleep(0.5)
            with self.lock:
                d = self.done
            el = max(time.time() - self.t0, 1e-6)
            pct = (d / self.total * 100) if self.total else 0
            sp = d / el
            eta = (self.total - d) / sp if sp > 0 and self.total else 0
            sys.stdout.write(
                f"\r  {self.tag} {_fmt(d)}/{_fmt(self.total)} {pct:5.1f}% "
                f"{_fmt(sp)}/s ETA {int(eta)//60:02d}:{int(eta)%60:02d}   ")
            sys.stdout.flush()

    def finish(self) -> None:
        self.stop = True
        time.sleep(0.6)


def _fetch_range(url: str, start: int, end: int, fh, prog: _Progress,
                 reurl: Callable[[], str] | None, stop: threading.Event) -> None:
    pos, attempt, cur = start, 0, url
    while pos <= end and not stop.is_set():
        try:
            r = session().get(cur, headers={"Range": f"bytes={pos}-{end}"},
                              stream=True, timeout=60)
            if r.status_code in (403, 410) and reurl:
                r.close()
                cur = reurl()
                attempt += 1
                if attempt > 6:
                    raise RuntimeError("token refresh loop")
                continue
            if r.status_code not in (200, 206):
                r.close()
                raise RuntimeError(f"HTTP {r.status_code}")
            for chunk in r.iter_content(262144):
                if stop.is_set():
                    r.close()
                    return
                if not chunk:
                    continue
                fh.seek(pos)
                fh.write(chunk)
                pos += len(chunk)
                prog.add(len(chunk))
                attempt = 0
            r.close()
        except (requests.RequestException, RuntimeError) as exc:
            attempt += 1
            if attempt > 8:
                raise RuntimeError(f"range {start}-{end} failed: {exc}")
            time.sleep(min(2 ** attempt * 0.4, 8))
            if reurl and attempt % 3 == 0:
                try:
                    cur = reurl()
                except Exception:
                    pass


def download(url_getter: Callable[[], str], dest: str, conns: int = 8) -> tuple[int, float]:
    """url_getter() returns a fresh signed URL each call (tokens expire)."""
    url = url_getter()
    total, ranges = probe(url)
    if total <= 0:
        raise RuntimeError("no content-length")
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    part = dest + ".part"

    if os.path.exists(part) and os.path.getsize(part) == total:
        os.replace(part, dest)
        return total, 0.0

    with open(part, "wb") as fh:
        fh.truncate(total)

    if not ranges or total < 8 << 20:
        conns = 1
    conns = max(1, min(conns, 16))
    seg = total // conns
    spans = []
    for i in range(conns):
        s = i * seg
        e = total - 1 if i == conns - 1 else (i + 1) * seg - 1
        spans.append((s, e))

    prog = _Progress(total, os.path.basename(dest)[:28])
    stop = threading.Event()
    t = threading.Thread(target=prog.render, daemon=True)
    t.start()
    t0 = time.time()
    errs: queue.Queue = queue.Queue()

    def worker(s: int, e: int) -> None:
        try:
            with open(part, "r+b") as fh:
                _fetch_range(url, s, e, fh, prog, url_getter, stop)
        except Exception as exc:  # noqa: BLE001
            errs.put(exc)
            stop.set()

    threads = [threading.Thread(target=worker, args=(s, e), daemon=True) for s, e in spans]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    prog.finish()
    elapsed = time.time() - t0
    sys.stdout.write("\r" + " " * 90 + "\r")
    if not errs.empty():
        raise errs.get()
    size = os.path.getsize(part)
    if size != total:
        raise RuntimeError(f"size mismatch {size} != {total}")
    os.replace(part, dest)
    return size, elapsed


# ---------------------------------------------------------------- misc utils
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}


def safe(name: str, maxlen: int = 90) -> str:
    name = _BAD.sub("_", name).strip(" .")
    name = re.sub(r"\s+", " ", name)
    if name.upper() in RESERVED:
        name = "_" + name
    return name[:maxlen] or "untitled"


def parse_sel(spec: str, n: int) -> list[int]:
    if not spec:
        return list(range(1, n + 1))
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted({i for i in out if 1 <= i <= n})


def hms(sec: int) -> str:
    return f"{sec // 60}:{sec % 60:02d}"


# ---------------------------------------------------------------- commands
def cmd_series(args) -> None:
    info, eps = episodes(args.ref, args.lang)
    print(f"{info['name']}  [{info['lang']}]  id={info['id']}")
    print(f"  url         {info['url']}")
    print(f"  playlist    {info['playlist_id']}   languages: {', '.join(info['bcov_ids'])}")
    print(f"  episodes    {len(eps)}")
    print()
    for i, ep in enumerate(eps, 1):
        print(f"  {ep_label(ep, i):<22} {hms(duration_s(ep)):>6}  {ep['id']}")


def cmd_links(args) -> None:
    info, eps = episodes(args.ref, args.lang)
    sel = parse_sel(args.episodes, len(eps))
    print(f"{info['name']}: resolving {len(sel)}/{len(eps)} episodes ...", file=sys.stderr)
    rows = []

    def work(i: int) -> None:
        ep = eps[i - 1]
        try:
            row = episode_links(ep)
            row["index"] = i
            rows.append(row)
            print(f"  [{i}/{len(eps)}] {row['title']}  ok", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}/{len(eps)}] FAIL {exc}", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        list(ex.map(work, sel))
    rows.sort(key=lambda r: r["index"])

    payload = {"series": {k: info[k] for k in
                          ("id", "slug", "name", "lang", "playlist_id", "url")},
               "count": len(rows), "episodes": rows}
    out = args.out or f"links-{info['id']}.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    if args.print_urls:
        for r in rows:
            print(f"{r['index']:03d} {r['title']}\t{r['mp4'] or r['hls']}")
    print(f"\nwrote {len(rows)} entries -> {out}", file=sys.stderr)


def cmd_dl(args) -> None:
    info, eps = episodes(args.ref, args.lang)
    sel = parse_sel(args.episodes, len(eps))
    root = os.path.join(args.outdir, safe(info["name"]))
    os.makedirs(root, exist_ok=True)

    print(f"{info['name']}  [{info['lang']}]")
    print(f"  {len(sel)} of {len(eps)} episodes -> {root}")
    print(f"  {args.conns} connections/episode, {args.jobs} episode(s) in parallel\n")

    got, failed = 0, []
    lock = threading.Lock()
    t_start = time.time()

    def one(i: int) -> None:
        nonlocal got
        ep = eps[i - 1]
        label = ep_label(ep, i)
        dest = os.path.join(root, safe(label) + ".mp4")

        def do_subs() -> None:
            if not args.subs:
                return
            try:
                tracks = episode_links(ep).get("subs") or []
                if not tracks:
                    return
                saved = fetch_subs(tracks, dest)
                if saved:
                    print(f"[{i}/{len(eps)}] subs {label}: "
                          + ", ".join(os.path.basename(s) for s in saved))
                if args.mux and saved:
                    mux_subs(dest, saved)
            except Exception as exc:  # noqa: BLE001
                print(f"[{i}/{len(eps)}] subs FAIL {label}: {exc}")

        if os.path.exists(dest) and os.path.getsize(dest) > 1 << 20 and not args.force:
            print(f"[{i}/{len(eps)}] skip (exists) {label}")
            with lock:
                got += 1
            do_subs()
            return
        try:
            def fresh(ep=ep) -> str:
                bs = best_source(playback(ep["id"]), want=args.source)
                if not bs or bs["kind"] != "mp4" or not bs["url"]:
                    raise RuntimeError("no MP4 source")
                return bs["url"]

            bs = best_source(playback(ep["id"]), want=args.source)
            if not bs:
                raise RuntimeError("no playable source")
            if bs["kind"] == "mp4":
                size, sec = download(fresh, dest, conns=args.conns)
            else:
                size, sec = download_hls(bs["url"], dest)
            rate = size / sec if sec else 0
            res = f" {bs['w']}x{bs['h']}" if bs.get("w") else ""
            print(f"[{i}/{len(eps)}] done {label}  {_fmt(size)} in {sec:.0f}s "
                  f"({_fmt(rate)}/s, {bs['kind']}{res})")
            do_subs()
            with lock:
                got += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[{i}/{len(eps)}] FAIL {label}: {exc}")
            with lock:
                failed.append((i, label, str(exc)))

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        list(ex.map(one, sel))

    el = time.time() - t_start
    print(f"\nfinished: {got}/{len(sel)} ok in {hms(int(el))}")
    if failed:
        print("failed:")
        for i, label, why in failed:
            print(f"  {i:03d} {label}  {why}")
        if args.retry_failed:
            print("\nre-running failures once ...")
            for i, _, _ in list(failed):
                one(i)


def cmd_subs(args) -> None:
    info, eps = episodes(args.ref, args.lang)
    sel = parse_sel(args.episodes, len(eps))
    root = os.path.join(args.outdir, safe(info["name"]))
    os.makedirs(root, exist_ok=True)
    print(f"{info['name']}  [{info['lang']}]  subtitles -> {root}")
    ok, none = 0, []
    for i in sel:
        ep = eps[i - 1]
        label = ep_label(ep, i)
        dest = os.path.join(root, safe(label) + ".mp4")
        try:
            tracks = episode_links(ep).get("subs") or []
        except Exception as exc:  # noqa: BLE001
            print(f"[{i}] {label}: FAIL {exc}")
            continue
        if not tracks:
            none.append(i)
            print(f"[{i}] {label}: no caption track")
            continue
        saved = fetch_subs(tracks, dest)
        if args.mux and saved and os.path.exists(dest):
            mux_subs(dest, saved)
        print(f"[{i}] {label}: " + ", ".join(os.path.basename(s) for s in saved))
        ok += 1
    print(f"\n{ok} episode(s) with subtitles"
          + (f", {len(none)} without: {none}" if none else ""))


def cmd_browse(args) -> None:
    r = get(args.url)
    slugs = sorted(set(re.findall(r"/en/series/(\d+-[A-Za-z0-9\-]+)", r.text)))
    print(f"{len(slugs)} series on page")
    for s in slugs:
        print(f"  {BASE}/en/series/{s}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(slugs))
        print(f"\nwrote -> {args.out}")


def main() -> None:
    p = argparse.ArgumentParser(description="TVBAnywhere NA downloader")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, ep=True):
        sp.add_argument("ref", help="series id, id-slug, or any site URL")
        sp.add_argument("--lang", help="language playlist key (e.g. tc)")
        if ep:
            sp.add_argument("-e", "--episodes", default="",
                            help="episode selection, e.g. 1-10,15 (default all)")

    sp = sub.add_parser("series", help="list episodes")
    common(sp, ep=False)
    sp.set_defaults(func=cmd_series)

    sp = sub.add_parser("links", help="dump every direct URL at once")
    common(sp)
    sp.add_argument("--out", help="output json")
    sp.add_argument("-j", "--jobs", type=int, default=6)
    sp.add_argument("--print-urls", action="store_true")
    sp.set_defaults(func=cmd_links)

    sp = sub.add_parser("dl", help="download")
    common(sp)
    sp.add_argument("-o", "--outdir", default="downloads")
    sp.add_argument("-c", "--conns", type=int, default=8, help="connections per episode")
    sp.add_argument("-j", "--jobs", type=int, default=3, help="episodes in parallel")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--retry-failed", action="store_true")
    sp.add_argument("--subs", action="store_true", help="also fetch caption tracks (.vtt)")
    sp.add_argument("--mux", action="store_true", help="embed subs as soft tracks")
    sp.add_argument("--source", choices=("auto", "mp4", "hls"), default="auto",
                    help="source selection (default auto = highest resolution)")
    sp.set_defaults(func=cmd_dl)

    sp = sub.add_parser("subs", help="fetch subtitle tracks for a series")
    common(sp)
    sp.add_argument("-o", "--outdir", default="downloads")
    sp.add_argument("--mux", action="store_true", help="embed into existing mp4")
    sp.set_defaults(func=cmd_subs)

    sp = sub.add_parser("browse", help="list series on a browse page")
    sp.add_argument("url")
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_browse)

    args = p.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)


if __name__ == "__main__":
    main()
