"""Estimate the size/time of downloading every TVBAnywhere series in a decade category.
Metadata only: browse pages, playlists, and one HEAD per series for bitrate. No video bytes.
"""
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tvbaw as T

CATEGORY_URL = sys.argv[1] if len(sys.argv) > 1 else "https://tvbanywherena.com/en/browse?year=2000s"
OUT = sys.argv[2] if len(sys.argv) > 2 else "estimate_2000s.json"

lock = threading.Lock()


def enumerate_series():
    slugs, page = [], 1
    while page <= 40:
        url = f"{CATEGORY_URL}&page={page}"
        try:
            r = T.get(url)
        except Exception as exc:
            print(f"  page {page} ERR {exc}")
            break
        found = sorted(set(__import__("re").findall(r"/en/series/(\d+-[A-Za-z0-9\-]+)", r.text)))
        print(f"  browse page {page}: {len(found)} series")
        if not found:
            break
        for s in found:
            if s not in slugs:
                slugs.append(s)
        page += 1
    return slugs


def survey(slug):
    """Return dict with episodes, seconds, bytes-per-episode (from one HEAD)."""
    try:
        info = T.resolve_series(slug)
        pid = info["default_bcov"]
        if not pid:
            return {"slug": slug, "error": "no playlist"}
        eps = T.playlist(pid)
        if not eps:
            return {"slug": slug, "error": "empty playlist"}
        secs = sum(T.duration_s(e) for e in eps)
        # sample one episode (middle) for bitrate
        sample = eps[len(eps) // 2]
        data = T.playback(sample["id"])
        srcs = T.pick_sources(data)
        size = 0
        if srcs["mp4"]:
            try:
                size, _ = T.probe(srcs["mp4"])
            except Exception:
                size = 0
        per_ep = size if size else 0
        total = per_ep * len(eps) if per_ep else 0
        with lock:
            print(f"  {slug[:44]:<44} {len(eps):>3} ep  {secs/3600:>6.1f} h  "
                  f"{(per_ep or 0)/2**20:>6.0f} MB/ep")
        return {"slug": slug, "name": info["name"], "episodes": len(eps),
                "seconds": secs, "bytes_per_ep": per_ep, "bytes_total": total}
    except Exception as exc:  # noqa: BLE001
        with lock:
            print(f"  {slug[:44]:<44} FAIL {exc}")
        return {"slug": slug, "error": str(exc)}


def main():
    t0 = time.time()
    print(f"enumerating {CATEGORY_URL}")
    slugs = enumerate_series()
    print(f"\n{len(slugs)} series found; surveying (metadata only) ...\n")
    rows = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for r in ex.map(survey, slugs):
            rows.append(r)

    ok = [r for r in rows if not r.get("error")]
    eps = sum(r["episodes"] for r in ok)
    secs = sum(r["seconds"] for r in ok)
    total = sum(r["bytes_total"] for r in ok)
    worst = sorted(ok, key=lambda r: -r["bytes_total"])[:5]

    summary = {
        "category": CATEGORY_URL,
        "series_total": len(slugs),
        "series_ok": len(ok),
        "series_failed": len(rows) - len(ok),
        "episodes": eps,
        "hours": round(secs / 3600, 1),
        "total_bytes": total,
        "total_gb": round(total / 2**30, 1),
        "avg_bytes_per_ep": total // eps if eps else 0,
        "top5_by_size": [{"name": r.get("name"), "gb": round(r["bytes_total"] / 2**30, 1),
                          "episodes": r["episodes"]} for r in worst],
        "elapsed_s": round(time.time() - t0, 1),
    }
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nat 30 MB/s: {total / (30 * 2**20) / 3600:.1f} hours")
    print(f"at 35 MB/s: {total / (35 * 2**20) / 3600:.1f} hours")
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "rows": rows}, fh, ensure_ascii=False, indent=2)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
