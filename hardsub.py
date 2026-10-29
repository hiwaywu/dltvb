#!/usr/bin/env python3
"""
hardsub.py - recover subtitles from video with BURNED-IN (hardsub) text, then translate.

For titles that have no caption tracks at all: the Chinese is painted into the
picture, so it has to be read off the frames.

    python hardsub.py ocr <video> [-o out.zh.srt] [--fps 1] [--start S --end S]
    python hardsub.py translate <in.zh.srt> [-o out.en.srt]
    python hardsub.py all <video> [--fps 1]

Method (fast path):
  1. ffmpeg crops the subtitle band, emits frames at N fps
  2. per frame, a row-ink profile splits the band into text lines (no CNN detector
     needed — this is what made the first version ~1 s/frame; now ~30-60 ms)
  3. the recogniser reads each line crop
  4. consecutive frames with near-identical text collapse into one cue; the most
     frequent reading inside the cue wins (temporal voting kills random slips)
  5. optional: machine-translate the SRT via the public Google endpoint
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections import Counter
from difflib import SequenceMatcher

import numpy as np
from PIL import Image

try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    sys.exit("pip install rapidocr-onnxruntime")

BAND_TOP = 0.68            # subtitle band starts at 68% of frame height
BAND_H = 0.30              # and is 30% tall
INK = 185                  # grey level counted as glyph ink
ROW_MIN = 3                # ink pixels in a row for it to count
MIN_LINE_H = 9             # ignore bands shorter than this (noise)
MIN_CONF = 0.30
MIN_DUR = 0.30             # cue shorter than this is dropped
SAME = 0.72                # similarity to treat two frames as the same cue

CJK = re.compile(r"[\u4e00-\u9fff]")


def is_junk(cue: dict) -> bool:
    """Reject on-screen signage / credits noise that is not dialogue."""
    t = cue["text"].strip()
    dur = cue["end"] - cue["start"]
    if not t:
        return True
    cjk = len(CJK.findall(t))
    # pure latin/numeric fragments ("set", "0", "Guoji") — never dialogue here
    if cjk == 0 and len(t) <= 10:
        return True
    # one or two glyphs that flash by: signage, not speech
    if cjk <= 2 and len(t) <= 3 and dur < 1.5:
        return True
    return False


# ------------------------------------------------------------------------ engine
_engine: RapidOCR | None = None


def engine() -> RapidOCR:
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    return _engine


def line_bands(band: np.ndarray) -> list[tuple[int, int]]:
    """Split the band into horizontal text lines using a row-ink profile."""
    grey = band.mean(axis=2)
    ink = (grey > INK).sum(axis=1) > ROW_MIN
    bands, start = [], None
    for y, on in enumerate(ink):
        if on and start is None:
            start = y
        elif not on and start is not None:
            bands.append((start, y))
            start = None
    if start is not None:
        bands.append((start, len(ink)))

    # merge bands separated by a thin gap, drop noise
    merged: list[list[int]] = []
    for a, b in bands:
        if merged and a - merged[-1][1] <= 3:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged if b - a >= MIN_LINE_H]


def read_frame(band: np.ndarray) -> tuple[str, float]:
    """Recognise the text lines in one frame band. Returns (text, confidence)."""
    parts, confs = [], []
    h = band.shape[0]
    for a, b in line_bands(band)[:2]:          # subtitles are 1-2 lines
        a, b = max(0, a - 4), min(h, b + 4)
        crop = band[a:b, :]
        if crop.shape[0] < 8 or crop.shape[1] < 8:
            continue
        out = engine().text_recognizer([crop])
        if not out or not out[0]:
            continue
        txt, conf = out[0][0]
        if float(conf) >= MIN_CONF and txt.strip():
            parts.append(txt)
            confs.append(float(conf))
    if not parts:
        return "", 0.0
    return clean(" ".join(parts)), sum(confs) / len(confs)


def clean(t: str) -> str:
    t = t.replace("|", " ").replace("／", "/")
    return re.sub(r"\s+", "", t).strip()


def similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


# ----------------------------------------------------------------------- extract
def extract(video: str, fps: float, start: float, end: float) -> list[dict]:
    tmp = tempfile.mkdtemp(prefix="hs_")
    try:
        ss = ["-ss", str(start)] if start else []
        to = ["-to", str(end)] if end else []
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", *ss, "-i", video, *to,
             "-vf", f"fps={fps},crop=iw:ih*{BAND_H}:0:ih*{BAND_TOP}",
             os.path.join(tmp, "%06d.png")],
            capture_output=True, check=False)

        frames = sorted(f for f in os.listdir(tmp) if f.endswith(".png"))
        if not frames:
            raise RuntimeError("ffmpeg produced no frames")
        print(f"  frames: {len(frames)} @ {fps} fps")

        rows, t0 = [], time.time()
        for i, f in enumerate(frames):
            band = np.asarray(Image.open(os.path.join(tmp, f)).convert("RGB"))
            txt, conf = read_frame(band)
            rows.append({"t": start + i / fps, "text": txt, "conf": conf})
            if (i + 1) % 500 == 0:
                el = time.time() - t0
                print(f"    {i+1}/{len(frames)}  {el:.0f}s  "
                      f"({el/(i+1)*1000:.0f} ms/frame)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return group(rows, fps)


def group(rows: list[dict], fps: float) -> list[dict]:
    cues: list[dict] = []
    cur: list[dict] = []

    def flush() -> None:
        if not cur:
            return
        texts = [r["text"] for r in cur if r["text"]]
        if not texts:
            cur.clear()
            return
        # temporal voting: the most frequent reading wins
        text, hits = Counter(texts).most_common(1)[0]
        span = cur[-1]["t"] + 1 / fps - cur[0]["t"]
        if span >= MIN_DUR:
            conf = max(r["conf"] for r in cur)
            cue = {"start": cur[0]["t"], "end": cur[-1]["t"] + 1 / fps,
                   "text": text, "conf": round(conf, 2)}
            if cues and similar(cues[-1]["text"], text) > 0.9 \
                    and cue["start"] - cues[-1]["end"] < 0.4:
                cues[-1]["end"] = cue["end"]
            else:
                cues.append(cue)
        cur.clear()

    for r in rows:
        if not r["text"]:
            flush()
            continue
        if cur and similar(cur[-1]["text"], r["text"]) < SAME:
            flush()
        cur.append(r)
    flush()
    kept = [c for c in cues if not is_junk(c)]
    print(f"  junk filtered: {len(cues) - len(kept)} of {len(cues)} cues dropped")
    return kept


# ---------------------------------------------------------------- cleanup pass
# The PP-OCR model confuses a handful of characters with the same shape. These
# are systematic, not random, so they can be repaired with rules. Learned from
# real output on 生死訟 (1990) — the same font/engine hits these every episode.
CONFUSIONS: list[tuple[str, str]] = [
    # 麼 is constantly read as 磨 / 擎
    (r"基磨", "甚麼"), (r"怎磨", "怎麼"), (r"甚磨", "甚麼"), (r"什磨", "甚麼"),
    (r"那磨", "那麼"), (r"這磨", "這麼"), (r"多磨", "多麼"), (r"甚擎", "甚麼"),
    (r"基擎", "甚麼"), (r"怎擎", "怎麼"),
    # 還 read as 遗
    (r"遗(?=[是會要肯能有在沒])", "還"),
    # 兩 read as 雨
    (r"雨个", "兩個"), (r"雨個", "兩個"),
    # 這 read as 道 (only in固定 bigrams, 道 is a real character)
    (r"道个", "這個"), (r"道是", "這是"), (r"道裡", "這裡"), (r"道種", "這種"),
    (r"道樣", "這樣"), (r"道麼", "這麼"), (r"道边", "這邊"), (r"道追个", "這個"),
    (r"雞道", "難道"),
    # legal vocabulary — the most consequential misses
    (r"重骞", "重審"), (r"好赖", "好報"), (r"洋赢", "贏"), (r"洋贏", "贏"),
    (r"阴庭", "休庭"), (r"陰庭", "休庭"),
    (r"害面", "書面"), (r"害判", "審判"),
    (r"判活[助勤]", "審判活動"), (r"活助", "活動"), (r"活勤", "活動"),
    (r"毅人", "殺人"),
    (r"检查(?=判|官|員)", "檢察官"),
    (r"判及護人", "法官及辯護人"),
    (r"抗诉", "抗訴"),
    # assorted
    (r"^国中國", "中國"),
    (r"魏之一", "總之一"), (r"魏之你", "總之你"),
    (r"横屁", "橫屍"), (r"德知道", "都知道"), (r"宝在", "實在"),
    (r"精誠所至，金石為$", "精誠所至，金石為開"),
    (r"大聲一$", "大聲一點"),
    (r"現在有請檢查$", "現在有請檢察官"),
]

_cc = None


def to_traditional(text: str) -> str:
    """OCR mixes simplified into traditional output; normalise to Traditional."""
    global _cc
    if _cc is None:
        try:
            from opencc import OpenCC
            _cc = OpenCC("s2t")
        except Exception:
            _cc = False
    return _cc.convert(text) if _cc else text


def cleanup_text(t: str) -> str:
    for pat, rep in CONFUSIONS:
        t = re.sub(pat, rep, t)
    return to_traditional(t)


def cleanup_srt(src: str, dst: str) -> list[dict]:
    cues = read_srt(src)
    changed = 0
    for c in cues:
        new = cleanup_text(c["text"])
        if new != c["text"]:
            changed += 1
        c["text"] = new
    write_srt(cues, dst)
    print(f"  repaired {changed}/{len(cues)} lines")
    return cues


# --------------------------------------------------------------------------- srt
def ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for i, c in enumerate(cues, 1):
            fh.write(f"{i}\n{ts(c['start'])} --> {ts(c['end'])}\n{c['text']}\n\n")


def read_srt(path: str) -> list[dict]:
    raw = open(path, encoding="utf-8").read().replace("\r\n", "\n")
    out = []
    for block in re.split(r"\n\n+", raw.strip()):
        lines = block.split("\n")
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        a, b = lines[1].split("-->")
        out.append({"start": parse_ts(a), "end": parse_ts(b),
                    "text": " ".join(lines[2:]).strip()})
    return out


def parse_ts(s: str) -> float:
    h, m, rest = s.strip().replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


# --------------------------------------------------------------------- translate
def translate(text: str) -> str:
    q = urllib.parse.urlencode({"client": "gtx", "sl": "auto", "tl": "en",
                                "dt": "t", "q": text})
    req = urllib.request.Request(
        "https://translate.googleapis.com/translate_a/single?" + q,
        headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    return "".join(seg[0] for seg in data[0] if seg and seg[0]).strip()


def translate_srt(src: str, dst: str, delay: float = 0.03) -> None:
    cues = read_srt(src)
    t0 = time.time()
    for i, c in enumerate(cues, 1):
        for attempt in range(3):
            try:
                c["text"] = translate(c["text"])
                break
            except Exception:
                time.sleep(1.0 + attempt)
        if i % 50 == 0:
            print(f"    {i}/{len(cues)}  {time.time()-t0:.0f}s")
        time.sleep(delay)
    write_srt(cues, dst)


# -------------------------------------------------------------------------- main
def main() -> None:
    p = argparse.ArgumentParser(description="recover + translate burned-in subtitles")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("ocr")
    sp.add_argument("video")
    sp.add_argument("-o", "--out")
    sp.add_argument("--fps", type=float, default=1.0)
    sp.add_argument("--start", type=float, default=0.0)
    sp.add_argument("--end", type=float, default=0.0)

    sp = sub.add_parser("translate")
    sp.add_argument("srt")
    sp.add_argument("-o", "--out")

    sp = sub.add_parser("cleanup")
    sp.add_argument("srt")
    sp.add_argument("-o", "--out")
    sp.add_argument("--translate", action="store_true",
                    help="also produce the English SRT")

    sp = sub.add_parser("all")
    sp.add_argument("video")
    sp.add_argument("--fps", type=float, default=1.0)
    sp.add_argument("--start", type=float, default=0.0)
    sp.add_argument("--end", type=float, default=0.0)

    args = p.parse_args()

    if args.cmd == "ocr":
        print(f"OCR {os.path.basename(args.video)}")
        cues = extract(args.video, args.fps, args.start, args.end)
        out = args.out or os.path.splitext(args.video)[0] + ".zh.srt"
        write_srt(cues, out)
        print(f"  {len(cues)} cues -> {out}")

    elif args.cmd == "translate":
        out = args.out or re.sub(r"\.zh\.srt$", ".en.srt", args.srt)
        print(f"translate {os.path.basename(args.srt)} -> {os.path.basename(out)}")
        translate_srt(args.srt, out)
        print("  done")

    elif args.cmd == "cleanup":
        out = args.out or args.srt.replace(".zh.srt", ".clean.zh.srt")
        print(f"cleanup {os.path.basename(args.srt)}")
        cleanup_srt(args.srt, out)
        print(f"  -> {os.path.basename(out)}")
        if args.translate:
            en = re.sub(r"\.zh\.srt$", ".en.srt", out)
            print("translate")
            translate_srt(out, en)
            print(f"  -> {os.path.basename(en)}")

    else:
        base = os.path.splitext(args.video)[0]
        zh, en = base + ".zh.srt", base + ".en.srt"
        print(f"OCR {os.path.basename(args.video)}")
        cues = extract(args.video, args.fps, args.start, args.end)
        write_srt(cues, zh)
        print(f"  {len(cues)} cues -> {os.path.basename(zh)}")
        print("translate")
        translate_srt(zh, en)
        print(f"  -> {os.path.basename(en)}")


if __name__ == "__main__":
    main()
