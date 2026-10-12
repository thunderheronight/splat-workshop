"""Basic quality check of an Arcturus recording – plain measurements, no AI / image recognition.

Decodes only the keyframes (the Frame writes about one per second) of both cameras at a small
size and measures per second:
  bright  mean brightness (0-255) of the inner image area (without the black fisheye border)
  over    share of almost white pixels (blown-out windows, lamps)
  detail  mean edge strength (Sobel) – low for bare walls, ceilings, tiles and blur
  cast    colour cast: distance of the mean colour from grey
  motion  difference to the previous second – high for fast pans
  right   brightness/detail of the right camera (covered or smudged lens)

Findings are returned as codes with parameters; the web interface turns them into text.
Try it: python3 -m splat_workshop.check <video.mp4> [...]
"""
import json
import os
import subprocess
import sys

SIZE = 96                     # edge length of the analysis images
CROP = "crop=iw*0.7:ih*0.7"   # square inside the fisheye image circle

# Thresholds, calibrated on HLG recordings of the Arcturus camera (values without tone mapping,
# so they look dark): rooms with good structure have a median detail of 5.5-8, a tiled bathroom 3.5.
DARK = 35          # brightness per second
VERY_DARK = 35     # mean brightness of the whole recording
OVER = 0.05        # share of almost white pixels
FLAT = 3.0         # edge strength
CAST = 19          # colour cast (distance from grey)
FAST = 50.0        # difference to the previous second
EYE_BRIGHT = 0.45  # relative brightness difference left/right
EYE_DETAIL = 0.65  # relative detail difference left/right


def _frames(path: str, keyframes_only: bool, progress=None):
    """Yields (index, left_rgb, right_rgb) as bytes, one per second."""
    vf = f"{CROP},scale={SIZE}:{SIZE}:flags=area,format=rgb24"
    pre = ["-skip_frame", "nokey"] if keyframes_only else []
    if not keyframes_only:
        vf = "fps=1," + vf
    cmd = ["ffmpeg", "-v", "error", *pre, "-i", path,
           "-filter_complex", f"[0:v:0]{vf}[l];[0:v:1]{vf}[r];[l][r]hstack",
           "-fps_mode", "passthrough", "-f", "rawvideo", "-"]
    # low priority: may run next to a conversion
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, preexec_fn=lambda: os.nice(10))
    n = SIZE * SIZE * 3
    row = SIZE * 3
    i = 0
    try:
        while True:
            buf = p.stdout.read(2 * n)
            if len(buf) < 2 * n:
                break
            left = b"".join(buf[y * 2 * row: y * 2 * row + row] for y in range(SIZE))
            right = b"".join(buf[y * 2 * row + row: (y + 1) * 2 * row] for y in range(SIZE))
            yield i, left, right
            i += 1
            if progress:
                progress(i)
    finally:
        p.stdout.close()
        p.wait()


def _gray(rgb: bytes):
    return [(77 * rgb[k] + 150 * rgb[k + 1] + 29 * rgb[k + 2]) >> 8 for k in range(0, len(rgb), 3)]


def _stats(rgb: bytes):
    g = _gray(rgb)
    npx = len(g)
    bright = sum(g) / npx
    over = sum(1 for v in g if v >= 245) / npx
    r = sum(rgb[0::3]) / npx
    gr = sum(rgb[1::3]) / npx
    b = sum(rgb[2::3]) / npx
    m = (r + gr + b) / 3
    cast = ((r - m) ** 2 + (gr - m) ** 2 + (b - m) ** 2) ** 0.5
    # Sobel magnitude on every second pixel (enough, and saves time)
    s, cnt, w = 0, 0, SIZE
    for y in range(1, w - 1, 2):
        o = y * w
        for x in range(1, w - 1, 2):
            a, c = g[o - w + x - 1], g[o - w + x + 1]
            d, f = g[o + w + x - 1], g[o + w + x + 1]
            gx = (c + 2 * g[o + x + 1] + f) - (a + 2 * g[o + x - 1] + d)
            gy = (d + 2 * g[o + w + x] + f) - (a + 2 * g[o - w + x] + c)
            s += abs(gx) + abs(gy)
            cnt += 1
    return g, {"bright": round(bright, 1), "over": round(over, 3), "cast": round(cast, 1),
               "detail": round(s / cnt / 8, 2), "rgb": [round(r), round(gr), round(b)]}


def _diff(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def measure(path: str, duration: float, progress=None) -> list:
    """Measurements per second. Fast via the keyframes; if the file has fewer keyframes than
    that, everything is decoded and one image per second is taken."""
    rows = _measure(path, True, progress)
    if len(rows) < 0.7 * duration:
        rows = _measure(path, False, progress)
    return rows


def _measure(path: str, keyframes_only: bool, progress=None) -> list:
    rows, prev = [], None
    for t, left, right in _frames(path, keyframes_only, progress):
        g, st = _stats(left)
        _, rs = _stats(right)
        st["motion"] = round(_diff(g, prev), 1) if prev is not None else None
        st["right"] = {"bright": rs["bright"], "detail": rs["detail"]}
        st["t"] = t
        rows.append(st)
        prev = g
    return rows


def _spans(flags, min_len=1):
    """[(start, end)] of consecutive seconds where flags is true."""
    out, start = [], None
    for i, f in enumerate(flags + [False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            if i - start >= min_len:
                out.append((start, i - 1))
            start = None
    return out


def _where(spans, total):
    secs = sum(b - a + 1 for a, b in spans)
    return {"secs": secs, "pct": round(100 * secs / max(total, 1)), "spans": spans[:4], "more": max(0, len(spans) - 4)}


def evaluate(rows: list, duration: float) -> dict:
    """Rates the measurements: findings with level ok/info/warn/bad and a per-second timeline."""
    n = len(rows)
    if not n:
        return {"level": "bad", "findings": [{"level": "bad", "code": "unreadable"}], "timeline": [], "summary": {}}
    findings = []

    def add(level, code, **params):
        findings.append({"level": level, "code": code, **params})

    dark = [r["bright"] < DARK for r in rows]
    flat = [r["detail"] < FLAT and r["bright"] >= DARK for r in rows]
    fast = [(r["motion"] or 0) > FAST for r in rows]
    over = [r["over"] > OVER for r in rows]
    eye = [abs(r["bright"] - r["right"]["bright"]) > EYE_BRIGHT * max(r["bright"], r["right"]["bright"], 1)
           or abs(r["detail"] - r["right"]["detail"]) > EYE_DETAIL * max(r["detail"], r["right"]["detail"], 1)
           for r in rows]

    avg = sum(r["bright"] for r in rows) / n
    if avg < VERY_DARK:
        add("bad", "too_dark", bright=round(avg))
    elif any(dark):
        w = _where(_spans(dark), n)
        add("bad" if w["pct"] > 40 else "warn", "dark", **w)

    sp = _spans(flat, 2)
    if sp:
        w = _where(sp, n)
        add("bad" if w["pct"] > 40 else "warn" if w["pct"] >= 8 else "info", "flat", **w)

    sp = _spans(fast)
    if sp:
        w = _where(sp, n)
        add("warn" if w["pct"] >= 20 else "info", "fast", **w)

    sp = _spans(over, 2)
    if sp:
        add("info", "overexposed", **_where(sp, n))

    sp = _spans(eye, 3)
    if sp:
        add("warn", "cameras_differ", **_where(sp, n))

    cast = sum(r["cast"] for r in rows) / n
    if cast > CAST:
        r, g, b = (sum(x["rgb"][k] for x in rows) / n for k in range(3))
        tone = "yellow" if r > b and g > b else "blue" if b > r else "green" if g > r and g > b else "red"
        add("info", "color_cast", tone=tone)

    if duration < 20:
        add("warn", "short", secs=round(duration))
    elif duration > 170:
        add("info", "long", secs=round(duration))

    order = {"bad": 3, "warn": 2, "info": 1}
    level = max((f["level"] for f in findings), key=lambda l: order[l], default="ok")
    if level == "info":
        level = "ok"
    timeline = ["dark" if dark[i] else "flat" if flat[i] else "fast" if fast[i] else
                "eye" if eye[i] else "over" if over[i] else "ok" for i in range(n)]
    return {"version": 1, "level": level, "findings": findings, "timeline": timeline,
            "summary": {"bright": round(avg), "detail": round(sum(r["detail"] for r in rows) / n, 1), "seconds": n}}


if __name__ == "__main__":
    for p in [a for a in sys.argv[1:] if not a.startswith("--")]:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                                   capture_output=True, text=True).stdout or 0)
        rows = measure(p, dur)
        if "--raw" in sys.argv:
            print(json.dumps(rows))
        res = evaluate(rows, dur)
        print(f"== {p.rsplit('/', 1)[-1]}  {res['level']}  {res['summary']}")
        print("   " + "".join({"ok": ".", "dark": "D", "flat": "F", "fast": ">", "eye": "E", "over": "o"}[x]
                              for x in res["timeline"]))
        for f in res["findings"]:
            print(f"   [{f['level']}] {f['code']} " + json.dumps({k: v for k, v in f.items() if k not in ("level", "code")}))
