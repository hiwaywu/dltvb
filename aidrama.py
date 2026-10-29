#!/usr/bin/env python3
"""
aidrama.py - aidrama.me episode/series downloader.

Chain (measured 2026-10-28):

    aidrama.me episode post  ->  cc.videoss.online/video/<b64>.html
    POST kk.videoss.online/api/sources  {video}          -> servers[hls, hydrax]
    POST kk.videoss.online/api/playback {video, server}  -> iframe url
    hls.techxzen.com/file/<hash>/playlist.m3u8           -> AES-128 HLS
        key:        /file/<hash>/key.html      (16 bytes)
        segments:   image_N.png on 5 rotating hosts (123dad.fit/.ink/.store/.blog/.online)
    -> download + AES-128-CBC decrypt + concat -> ffmpeg -c copy -> mp4

Episode index comes from the WordPress REST API (open):
    /wp-json/wp/v2/posts?slug=<slug>          -> content holds the player iframe
    /wp-json/wp/v2/posts?categories=<cat>     -> whole series in one call

Usage:
    python aidrama.py series "https://aidrama.me/the-intangible-truth-.../"
    python aidrama.py links  "https://aidrama.me/.../"
    python aidrama.py dl     "https://aidrama.me/..." -e 1-5 -c 16
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urlparse

import requests

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:
    sys.exit("pip install cryptography")

SITE = "https://aidrama.me"
API = "https://kk.videoss.online"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_local = threading.local()
_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def session() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        _local.s = s
    return s


def req(method: str, url: str, *, tries: int = 4, timeout: int = 60, **kw):
    last = None
    for i in range(tries):
        try:
            r = session().request(method, url, timeout=timeout, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                time.sleep(1.2 * (i + 1))
                continue
            return r
        except requests.RequestException as exc:
            last = str(exc)
            time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"{method} failed ({last}): {url[:120]}")


def api_post(path: str, payload: dict) -> dict:
    r = req("POST", API + path,
            json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json",
                     "Origin": API, "Referer": API + "/"})
    if r.status_code != 200:
        raise RuntimeError(f"{path} -> HTTP {r.status_code}: {r.text[:120]}")
    return r.json()


# --------------------------------------------------------------- series index
def series_slug(ref: str) -> str:
    p = urlparse(ref if ref.startswith("http") else SITE + "/" + ref.strip("/"))
    seg = p.path.rstrip("/").split("/")[-1]
    seg = re.sub(r"-episode-\d+$", "", seg)
    return seg


def wp_get(params: str) -> list | dict:
    r = req("GET", f"{SITE}/wp-json/wp/v2/posts?{params}&_fields=id,slug,title,date,content,categories")
    if r.status_code != 200:
        raise RuntimeError(f"wp-json {params[:60]} -> HTTP {r.status_code}")
    return r.json()


def strip_tags(html: str) -> str:
    txt = re.sub(r"<[^>]+>", "", html)
    txt = txt.replace("&#8211;", "-").replace("&#8212;", "-").replace("&amp;", "&")
    txt = txt.replace("&#039;", "'").replace("&quot;", '"').replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", txt).strip()


PLAYER_RE = re.compile(r"([a-z0-9]+\.videoss\.online)/video/([A-Za-z0-9+/=]+)\.html", re.I)


def episode_from_post(post: dict) -> dict | None:
    m = PLAYER_RE.search(post.get("content", {}).get("rendered", ""))
    if not m:
        return None
    slug = post["slug"]
    em = re.search(r"-episode-(\d+)$", slug)
    idx = int(em.group(1)) if em else 0
    title = strip_tags(post.get("title", {}).get("rendered", "")) or f"Episode {idx}"
    return {"index": idx, "title": title, "vid": m.group(2), "host": m.group(1),
            "post_id": post["id"], "slug": slug,
            "date": (post.get("date") or "")[:10]}


def series(ref: str) -> dict:
    slug = series_slug(ref)
    landing = wp_get(f"slug={slug}")
    if not landing:
        raise SystemExit(f"series not found: {slug}")
    land = landing[0]
    cats = land.get("categories") or []

    best: list[dict] = []
    for cat in cats:
        try:
            posts = wp_get(f"categories={cat}&per_page=100")
        except RuntimeError:
            continue
        if not isinstance(posts, list):
            continue
        eps = [e for e in (episode_from_post(p) for p in posts) if e]
        mine = [e for e in eps if e["slug"].startswith(slug[:40])]
        pool = mine or eps
        if len(pool) > len(best):
            best = pool
        if mine:
            break

    if not best:  # fallback: pull episode links straight out of the landing content
        links = sorted(set(re.findall(r'href="(https://aidrama\.me/[^"]*?-episode-\d+/?)"',
                                      land.get("content", {}).get("rendered", ""))))
        for link in links:
            s = link.rstrip("/").split("/")[-1]
            got = wp_get(f"slug={s}")
            if got and (e := episode_from_post(got[0])):
                best.append(e)

    best.sort(key=lambda e: e["index"])
    name = strip_tags(land.get("title", {}).get("rendered", "")) or slug
    return {"slug": slug, "name": name, "post_id": land.get("id"),
            "episodes": best, "url": f"{SITE}/{slug}/"}


# --------------------------------------------------------------- media resolve
def playlist_url(vid: str, server: str = "hls") -> str:
    src = api_post("/api/sources", {"video": vid})
    servers = [s["key"] for s in src.get("servers") or []]
    if server not in servers:
        if not servers:
            raise RuntimeError("no servers offered for this video")
        server = servers[0]
    pb = api_post("/api/playback", {"video": vid, "server": server})
    url = pb.get("url") or ""
    if pb.get("type") == "iframe":
        m = re.search(r"/embed/([A-Za-z0-9]+)\.html", url)
        if not m:
            raise RuntimeError(f"unexpected iframe: {url}")
        h = m.group(1)
        return f"https://hls.techxzen.com/file/{h}/playlist.m3u8"
    return url


# --------------------------------------------------------------- hls download
def parse_playlist(text: str, base: str) -> dict:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if any(l.startswith("#EXT-X-STREAM-INF") for l in lines):
        best, best_bw = None, -1
        for i, l in enumerate(lines):
            if l.startswith("#EXT-X-STREAM-INF"):
                bw = int(re.search(r"BANDWIDTH=(\d+)", l).group(1)) if "BANDWIDTH=" in l else 0
                if bw > best_bw and i + 1 < len(lines):
                    best_bw, best = bw, lines[i + 1]
        if best:
            return {"master": best}

    key = None
    for l in lines:
        if l.startswith("#EXT-X-KEY"):
            method = re.search(r'METHOD=([A-Z0-9\-]+)', l)
            uri = re.search(r'URI="([^"]+)"', l)
            iv = re.search(r'IV=0x([0-9A-Fa-f]+)', l)
            key = {
                "method": method.group(1) if method else "NONE",
                "uri": uri.group(1) if uri else None,
                "iv": bytes.fromhex(iv.group(1)) if iv else None,
            }
            break
    segs = [l for l in lines if not l.startswith("#")]
    return {"key": key, "segments": segs}


def fetch_key(url: str, referer: str) -> bytes:
    r = req("GET", url, headers={"Referer": referer})
    if r.status_code != 200:
        raise RuntimeError(f"key HTTP {r.status_code}")
    return r.content


def _get_bytes(url: str, referer: str, iv: bytes | None, key: bytes | None) -> bytes:
    r = req("GET", url, headers={"Referer": referer}, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"segment HTTP {r.status_code}")
    data = r.content
    if key:
        dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        data = dec.update(data) + dec.finalize()
        pad = data[-1] if data else 0
        if 0 < pad < 16 and data.endswith(bytes([pad]) * pad):
            data = data[:-pad]
    return data


def download_hls(m3u8: str, out_mp4: str, conns: int = 16, keep_ts: bool = False) -> tuple[int, float]:
    referer = "https://hls.techxzen.com/"
    r = req("GET", m3u8, headers={"Referer": referer})
    if r.status_code != 200:
        raise RuntimeError(f"playlist HTTP {r.status_code}")
    pl = parse_playlist(r.text, m3u8)

    if "master" in pl:  # one level of indirection
        from urllib.parse import urljoin
        r2 = req("GET", urljoin(m3u8, pl["master"]), headers={"Referer": referer})
        pl = parse_playlist(r2.text, m3u8)

    segs, kinfo = pl["segments"], pl.get("key") or {}
    if not segs:
        raise RuntimeError("empty playlist")

    key = iv = None
    if kinfo.get("method") == "AES-128":
        kv = kinfo["uri"]
        if kv and not kv.startswith("http"):
            from urllib.parse import urljoin
            kv = urljoin(m3u8, kv)
        key = fetch_key(kv, referer)
        iv = kinfo.get("iv") or (0).to_bytes(16, "big")

    tmp_ts = out_mp4 + ".ts"
    os.makedirs(os.path.dirname(out_mp4) or ".", exist_ok=True)
    total = len(segs)
    done = {"n": 0, "bytes": 0, "last": 0.0}
    lock = threading.Lock()
    t0 = time.time()

    def tick(nbytes: int) -> None:
        now = time.time()
        with lock:
            done["n"] += 1
            done["bytes"] += nbytes
            n, b = done["n"], done["bytes"]
            if n < total and now - done["last"] < 0.3:
                return
            done["last"] = now
        el = max(now - t0, 1e-6)
        eta = (total - n) / (n / el) if n else 0
        sys.stdout.write(f"\r    {n}/{total} segs  {b/1048576:.1f}MB  "
                         f"{b/1048576/el:.1f}MB/s  ETA {int(eta)//60:02d}:{int(eta)%60:02d}   ")
        sys.stdout.flush()

    with open(tmp_ts, "wb") as fh, ThreadPoolExecutor(max_workers=max(1, conns)) as ex:
        futs = {i: ex.submit(_get_bytes, u, referer, iv, key) for i, u in enumerate(segs)}
        for i in range(total):
            data = futs[i].result()
            fh.write(data)
            tick(len(data))
    sys.stdout.write("\r" + " " * 70 + "\r")
    elapsed = time.time() - t0

    size = os.path.getsize(tmp_ts)
    if keep_ts or not shutil.which("ffmpeg"):
        os.replace(tmp_ts, out_mp4.rsplit(".", 1)[0] + ".ts")
        return size, elapsed

    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", tmp_ts,
         "-c", "copy", "-bsf:a", "aac_adtstoasc", out_mp4],
        capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(out_mp4):
        raise RuntimeError(f"ffmpeg: {proc.stderr.strip()[:200]}")
    os.remove(tmp_ts)
    return os.path.getsize(out_mp4), elapsed


# --------------------------------------------------------------- utilities
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe(name: str, maxlen: int = 90) -> str:
    name = _BAD.sub("_", name).strip(" .")
    name = re.sub(r"\s+", " ", name)
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


def hms(s: int) -> str:
    return f"{s//60}:{s%60:02d}"


# --------------------------------------------------------------- commands
def cmd_series(args) -> None:
    s = series(args.ref)
    print(f"{s['name']}   ({len(s['episodes'])} episodes)")
    print(f"  {s['url']}")
    print()
    for e in s["episodes"]:
        print(f"  {e['index']:03d}  {e['title'][:58]:<58} {e['date']}  {e['vid']}")


def cmd_links(args) -> None:
    s = series(args.ref)
    sel = parse_sel(args.episodes, len(s["episodes"]))
    rows = []

    def one(i: int) -> None:
        e = s["episodes"][i - 1]
        try:
            m3u8 = playlist_url(e["vid"], server=args.server)
            rows.append({**e, "m3u8": m3u8})
            log(f"  [{i}/{len(sel)}] {e['index']:03d} ok")
        except Exception as exc:  # noqa: BLE001
            log(f"  [{i}/{len(sel)}] {e['index']:03d} FAIL {exc}")

    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        list(ex.map(one, sel))
    rows.sort(key=lambda r: r["index"])

    out = args.out or f"links-{safe(s['slug'])[:40]}.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"series": s["name"], "slug": s["slug"], "count": len(rows),
                   "episodes": rows}, fh, ensure_ascii=False, indent=2)
    if args.print_urls:
        for r in rows:
            print(f"{r['index']:03d} {r['title']}\t{r['m3u8']}")
    print(f"wrote {len(rows)} entries -> {out}")


def ep_filename(e: dict, series_name: str) -> str:
    t = e["title"]
    for sep in (" - ", " – ", " — "):
        if t.startswith(series_name + sep):
            t = t[len(series_name) + len(sep):]
            break
    t = t or f"Episode {e['index']}"
    return f"{e['index']:03d} {safe(t)}.mp4"


def cmd_dl(args) -> None:
    s = series(args.ref)
    sel = parse_sel(args.episodes, len(s["episodes"]))
    root = os.path.join(args.outdir, safe(s["name"]))
    os.makedirs(root, exist_ok=True)
    print(f"{s['name']} — {len(sel)}/{len(s['episodes'])} episodes -> {root}")
    print(f"{args.conns} segment connections, {args.jobs} episode(s) parallel\n")

    ok, failed = 0, []
    lock = threading.Lock()
    t0 = time.time()

    def one(i: int) -> None:
        nonlocal ok
        e = s["episodes"][i - 1]
        dest = os.path.join(root, ep_filename(e, s["name"]))
        if os.path.exists(dest) and os.path.getsize(dest) > 1 << 20 and not args.force:
            log(f"[{e['index']:03d}] skip (exists)")
            with lock:
                ok += 1
            return
        try:
            m3u8 = playlist_url(e["vid"], server=args.server)
            log(f"[{e['index']:03d}] {e['title'][:50]}")
            size, sec = download_hls(m3u8, dest, conns=args.conns, keep_ts=args.ts)
            log(f"[{e['index']:03d}] done {size/1048576:.0f}MB in {sec:.0f}s "
                f"({size/1048576/max(sec,1e-6):.1f}MB/s) -> {os.path.basename(dest)}")
            with lock:
                ok += 1
        except Exception as exc:  # noqa: BLE001
            log(f"[{e['index']:03d}] FAIL {exc}")
            with lock:
                failed.append((e["index"], str(exc)))

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        list(ex.map(one, sel))

    print(f"\nfinished {ok}/{len(sel)} in {hms(int(time.time()-t0))}")
    if failed:
        print("failed:")
        for idx, why in failed:
            print(f"  {idx:03d} {why}")


def main() -> None:
    p = argparse.ArgumentParser(description="aidrama.me downloader")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("series", help="list episodes")
    sp.add_argument("ref")
    sp.set_defaults(func=cmd_series)

    sp = sub.add_parser("links", help="resolve m3u8 for each episode")
    sp.add_argument("ref")
    sp.add_argument("-e", "--episodes", default="")
    sp.add_argument("-j", "--jobs", type=int, default=5)
    sp.add_argument("--out")
    sp.add_argument("--print-urls", action="store_true")
    sp.add_argument("--server", default="hls", help="source server (default: hls)")
    sp.set_defaults(func=cmd_links)

    sp = sub.add_parser("dl", help="download + decrypt + remux")
    sp.add_argument("ref")
    sp.add_argument("-e", "--episodes", default="")
    sp.add_argument("-o", "--outdir", default="downloads")
    sp.add_argument("-c", "--conns", type=int, default=16, help="segment connections")
    sp.add_argument("-j", "--jobs", type=int, default=2, help="episodes in parallel")
    sp.add_argument("--ts", action="store_true", help="keep raw .ts, skip ffmpeg")
    sp.add_argument("--server", default="hls", help="source server (default: hls)")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_dl)

    args = p.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)


if __name__ == "__main__":
    main()
