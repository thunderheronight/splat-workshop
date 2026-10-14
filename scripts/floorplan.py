# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pillow"]
# ///
"""Draw a floor plan (top view) of one or more finished scenes.

Usage: uv run floorplan.py <out.png> <run folder>[=<label>] [<run folder>[=<label>] ...]

The Arcturus pipeline puts every scene into the Steam Frame's tracking frame (metric, Y down).
Recordings made in one session therefore share a frame, and their floor plans line up without
any manual alignment. Per scene the plan shows:
  - a colour top view (ceiling cut away, so you see floor and furniture from above)
  - walls as bright lines (splats in the wall height band, where they pile up)
  - the path the camera was carried along (registered camera positions)
  - name and approximate size
The plan is rotated so the walls are axis-aligned.
Seen from above, Y down means +x and +z form a mirrored pair on screen (rows count downwards):
x is drawn right to left, otherwise the plan comes out mirror-inverted.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = [
    "/usr/share/fonts/google-noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/google-noto/NotoSans-SemiBold.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]
COLORS = [(124, 156, 255), (255, 184, 107), (70, 211, 181), (255, 107, 129), (200, 160, 255), (240, 225, 120),
          (120, 220, 255), (255, 150, 210)]
BG = (15, 17, 21)
SH_C0 = 0.28209479177387814
MAX_POINTS = 1_500_000            # per scene; enough for a sharp plan, keeps memory low
SIZE = 1400                       # longest side of the output in pixels


def font(size):
    path = next((p for p in FONTS if Path(p).exists()), None)
    return ImageFont.truetype(path, size) if path else ImageFont.load_default()


def load_ply(path: Path, rng):
    """x, y, z, rgb of fairly solid splats (memory-mapped, only the needed columns)."""
    with open(path, "rb") as f:
        props, n = [], 0
        while (line := f.readline().decode(errors="replace").strip()) != "end_header":
            if line.startswith("element vertex"):
                n = int(line.split()[-1])
            elif line.startswith("property"):
                props.append(line.split()[-1])
        offset = f.tell()
    dtype = np.dtype([(p, "<f4") for p in props])
    data = np.memmap(path, dtype=dtype, mode="r", offset=offset, shape=(n,))
    idx = np.sort(rng.choice(n, MAX_POINTS, replace=False)) if n > MAX_POINTS else slice(None)
    d = data[idx]
    keep = 1 / (1 + np.exp(-d["opacity"].astype(np.float32))) > 0.5
    d = d[keep]
    xyz = np.stack([d["x"], d["y"], d["z"]], axis=1).astype(np.float32)
    rgb = np.clip(0.5 + SH_C0 * np.stack([d["f_dc_0"], d["f_dc_1"], d["f_dc_2"]], axis=1), 0, 1)
    return xyz, rgb.astype(np.float32)


def load_cameras(run: Path):
    """Registered camera centres from the aligned COLMAP model (dataset/sparse/0/images.bin), in capture order."""
    f = run / "dataset" / "sparse" / "0" / "images.bin"
    if not f.exists():
        return None
    out = []
    with open(f, "rb") as fh:
        (num,) = struct.unpack("<Q", fh.read(8))
        for _ in range(num):
            fh.read(4)
            qw, qx, qy, qz, tx, ty, tz = struct.unpack("<7d", fh.read(56))
            fh.read(4)
            name = b""
            while (c := fh.read(1)) != b"\0":
                name += c
            (npts,) = struct.unpack("<Q", fh.read(8))
            fh.seek(24 * npts, 1)
            if not name.startswith(b"cam0/"):
                continue
            r = np.array([[1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
                          [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
                          [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)]])
            out.append((name.decode(), -r.T @ np.array([tx, ty, tz])))
    out.sort(key=lambda c: c[0])
    return np.array([c[1] for c in out]) if out else None


def heights(y):
    """Floor and ceiling (Y points down): the floor has the largest y."""
    return np.percentile(y, 97), np.percentile(y, 3)


def wall_angle(points_xz, bin_size=0.03):
    """Yaw that makes the walls axis-aligned: maximise the peakiness of the x/z histograms."""
    best, best_score = 0.0, -1
    if len(points_xz) < 50:                                   # no walls found: leave it as it is
        return best
    for deg in np.arange(0, 90, 0.5):
        a = np.radians(deg)
        c, s = np.cos(a), np.sin(a)
        u = points_xz[:, 0] * c - points_xz[:, 1] * s
        v = points_xz[:, 0] * s + points_xz[:, 1] * c
        score = 0
        for w in (u, v):
            h, _ = np.histogram(w, bins=max(10, int((w.max() - w.min()) / bin_size)))
            score += float(np.sum(h.astype(np.float64) ** 2)) / len(w) ** 2
        if score > best_score:
            best, best_score = a, score
    return best


def room_box(w, cell=0.05):
    """Bounds of a room from its walls: real walls are dense, floaters (seen through windows/doors) sparse."""
    if len(w) < 50:
        return None
    ij = np.floor(w / cell).astype(np.int64)
    _, inv, counts = np.unique(ij, axis=0, return_inverse=True, return_counts=True)
    dense = counts[inv.ravel()] >= max(3, np.percentile(counts, 75))
    strong = w[dense]
    if len(strong) < 50:
        strong = w
    return np.percentile(strong, 1, axis=0), np.percentile(strong, 99, axis=0)


def rotate(xz, a):
    c, s = np.cos(a), np.sin(a)
    return np.stack([xz[:, 0] * c - xz[:, 1] * s, xz[:, 0] * s + xz[:, 1] * c], axis=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("runs", nargs="+", help="run folder, optionally =label")
    ap.add_argument("--title", default="")
    ap.add_argument("--comma", action="store_true", help="decimal comma in sizes")
    ap.add_argument("--ratio", type=float, default=1.0,
                    help="model scale, e.g. 87 for H0 (1:87): sizes and the scale bar show the original's size")
    ap.add_argument("--style", choices=("pretty", "classic"), default="pretty",
                    help="pretty: colour top view, glowing walls, camera path; classic: glowing point density only")
    args = ap.parse_args()
    out = Path(args.out)
    rng = np.random.default_rng(1)
    # All lengths below are in metres of the original: a model (--ratio 87) is measured in its own scale,
    # so its walls are looked for 1 to 2 cm above its floor instead of 0.9 to 1.9 m.
    m = 1 / args.ratio
    scenes = []
    for arg in args.runs:
        run, _, label = arg.partition("=")
        run = Path(run)
        xyz, rgb = load_ply(run / "spirula" / "scene.ply", rng)
        floor, ceil = heights(xyz[:, 1])
        # keep the room: below the ceiling, not below the floor, and drop far-away floaters
        lo, hi = np.percentile(xyz[:, [0, 2]], 0.5, axis=0), np.percentile(xyz[:, [0, 2]], 99.5, axis=0)
        inside = ((xyz[:, 1] > ceil + 0.25 * m) & (xyz[:, 1] < floor + 0.15 * m)
                  & np.all(xyz[:, [0, 2]] >= lo - 0.3 * m, axis=1) & np.all(xyz[:, [0, 2]] <= hi + 0.3 * m, axis=1))
        if inside.sum() < 100:                                   # e.g. a model drawn without its scale: keep all
            inside = np.ones(len(xyz), bool)
        up = floor - xyz[:, 1]                                   # height above the floor
        walls = inside & (up > 0.9 * m) & (up < 1.9 * m)
        scenes.append({"label": label or run.name, "xyz": xyz[inside], "rgb": rgb[inside], "up": up[inside],
                       "walls": xyz[walls][:, [0, 2]], "cams": load_cameras(run), "floor": floor})

    all_walls = np.vstack([s["walls"] for s in scenes])
    sub = all_walls[rng.choice(len(all_walls), min(len(all_walls), 150_000), replace=False)]
    angle = wall_angle(sub, 0.03 * m)
    for s in scenes:
        s["p"] = rotate(s["xyz"][:, [0, 2]], angle)
        s["w"] = rotate(s["walls"], angle)
        s["c"] = rotate(s["cams"][:, [0, 2]], angle) if s["cams"] is not None else None
        s["box"] = room_box(s["w"], 0.05 * m)
        if s["box"] is not None:                                   # keep only this room (plus a margin)
            b0, b1 = s["box"][0] - 0.35 * m, s["box"][1] + 0.35 * m
            ok = np.all((s["p"] >= b0) & (s["p"] <= b1), axis=1)
            s["p"], s["rgb"], s["up"] = s["p"][ok], s["rgb"][ok], s["up"][ok]
            okw = np.all((s["w"] >= b0) & (s["w"] <= b1), axis=1)
            s["w"] = s["w"][okw]

    boxes = [s["box"] for s in scenes if s["box"] is not None]
    if boxes:
        lo = np.min([b[0] for b in boxes], axis=0) - 0.8 * m
        hi = np.max([b[1] for b in boxes], axis=0) + 0.8 * m
    else:
        allp = np.vstack([s["p"] for s in scenes])
        lo, hi = np.percentile(allp, 1, axis=0) - 0.5 * m, np.percentile(allp, 99, axis=0) + 0.5 * m
    span = hi - lo
    ppm = (SIZE - 160) / span.max()                               # pixels per metre
    W, H = int(span[0] * ppm) + 160, int(span[1] * ppm) + 200

    def px(p):
        # x from right to left: a top view of a Y-down frame, not its mirror image
        return ((hi[0] - p[:, 0]) * ppm + 80).astype(int), ((p[:, 1] - lo[1]) * ppm + 120).astype(int)

    if args.style == "classic":
        img = classic(scenes, px, W, H)
    else:
        img = pretty(scenes, px, W, H)
    out_img = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))
    d = ImageDraw.Draw(out_img)

    # 3. camera paths, labels and sizes
    f_label, f_small, f_title = font(26), font(17), font(30)
    for i, s in enumerate(scenes):
        col = COLORS[i % len(COLORS)]
        if args.style == "pretty" and s["c"] is not None and len(s["c"]) > 1:
            x, z = px(s["c"])
            pts = list(zip(x.tolist(), z.tolist()))
            for k in range(0, len(pts) - 1, 2):                    # dotted line
                d.line([pts[k], pts[k + 1]], fill=col + (255,), width=2)
            d.ellipse((pts[0][0] - 6, pts[0][1] - 6, pts[0][0] + 6, pts[0][1] + 6), outline=col, width=3)
        if s["box"] is not None:
            (bx0, bz0), (bx1, bz1) = s["box"]
            cx, cz = px(np.array([[(bx0 + bx1) / 2, (bz0 + bz1) / 2]]))
            size = f"~ {(bx1 - bx0) * args.ratio:.1f} × {(bz1 - bz0) * args.ratio:.1f} m".replace(".", "," if args.comma else ".")
            for txt, fnt, dy in ((s["label"], f_label, -16), (size, f_small, 16)):
                tw = d.textlength(txt, font=fnt)
                d.text((cx[0] - tw / 2 + 1, cz[0] + dy - 13 + 1), txt, font=fnt, fill=(0, 0, 0))
                d.text((cx[0] - tw / 2, cz[0] + dy - 13), txt, font=fnt, fill=(255, 255, 255) if dy < 0 else (210, 214, 224))

    # scale bar: a round length of the original (for a model: of the real thing), about 150 px long
    real_ppm = ppm / args.ratio                                    # pixels per metre of the original
    length = min((0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000), key=lambda l: abs(l * real_ppm - 150))
    bar = length * real_ppm
    text = (f"{length * 100:g} cm" if length < 1 else f"{length:g} m")
    d.line((W - 60 - bar, H - 40, W - 60, H - 40), fill=(230, 230, 230), width=3)
    for xx in (W - 60 - bar, W - 60):
        d.line((xx, H - 47, xx, H - 33), fill=(230, 230, 230), width=3)
    tw = d.textlength(text, font=f_small)
    d.text((W - 60 - bar / 2 - tw / 2, H - 72), text, font=f_small, fill=(230, 230, 230))
    if args.ratio != 1:
        ratio = f"1:{args.ratio:g}".replace(".", "," if args.comma else ".")
        d.text((W - 60 - bar - 20 - d.textlength(ratio, font=f_small), H - 50), ratio, font=f_small, fill=(230, 230, 230))
    if args.title:
        d.text((40, 32), args.title, font=f_title, fill=(235, 238, 245))
    out_img.save(out)
    info = [{"label": s["label"], "size": [round(float(v) * args.ratio, 2) for v in (s["box"][1] - s["box"][0])]
             if s["box"] is not None else None} for s in scenes]
    print(json.dumps({"angle": round(float(np.degrees(angle)), 1), "scenes": info, "ratio": args.ratio,
                      "pixels_per_metre": round(real_ppm, 4)}))


def classic(scenes, px, W, H):
    """Glowing point density of everything between knee and head height, one colour per scene."""
    img = np.zeros((H, W, 3), np.float32) + np.array(BG, np.float32) / 255
    for i, s in enumerate(scenes):
        col = np.array(COLORS[i % len(COLORS)], np.float32) / 255
        lo_h, hi_h = np.percentile(s["up"], 15), np.percentile(s["up"], 85)
        q = s["p"][(s["up"] > lo_h) & (s["up"] < hi_h)]
        x, z = px(q)
        ok = (x >= 0) & (x < W) & (z >= 0) & (z < H)
        h = np.zeros((H, W), np.float32)
        np.add.at(h, (z[ok], x[ok]), 1)
        ref = np.percentile(h[h > 0], 98) if (h > 0).any() else 1
        h = np.clip(np.log1p(h) / np.log1p(ref), 0, 1) ** 0.75
        h = np.asarray(Image.fromarray((h * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3)), np.float32) / 255
        img = np.maximum(img, h[..., None] * col)
    return img


def pretty(scenes, px, W, H):
    # 1. colour top view: per pixel the highest splat (z-buffer on the height above floor)
    top = np.zeros((H, W, 3), np.float32)
    level = np.full((H, W), -9.0, np.float32)
    for s in scenes:
        x, z = px(s["p"])
        ok = (x >= 0) & (x < W) & (z >= 0) & (z < H)
        order = np.argsort(s["up"][ok])
        xs, zs, ups, cols = x[ok][order], z[ok][order], s["up"][ok][order], s["rgb"][ok][order]
        top[zs, xs] = cols                                        # later (= higher) points win
        level[zs, xs] = np.maximum(level[zs, xs], ups)
    covered = level > -9
    img = np.zeros((H, W, 3), np.float32) + np.array(BG, np.float32) / 255
    # soften: fill small gaps, slightly darken so the walls stand out
    top_img = Image.fromarray((top * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(1.2))
    mask = Image.fromarray((covered * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(1.5))
    top_arr = (np.asarray(top_img, np.float32) / 255) ** 0.7 * 0.85      # lift dark interiors a little
    m = np.asarray(mask, np.float32)[..., None] / 255
    img = img * (1 - m) + top_arr * m

    # 2. walls: density of splats in the wall band, drawn as glowing lines in the scene colour
    for i, s in enumerate(scenes):
        col = np.array(COLORS[i % len(COLORS)] if len(scenes) > 1 else (235, 240, 255), np.float32) / 255
        x, z = px(s["w"])
        ok = (x >= 0) & (x < W) & (z >= 0) & (z < H)
        h = np.zeros((H, W), np.float32)
        np.add.at(h, (z[ok], x[ok]), 1)
        h = np.clip(np.log1p(h) / np.log1p(np.percentile(h[h > 0], 97) if (h > 0).any() else 1), 0, 1)
        glow = np.asarray(Image.fromarray((h * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3)), np.float32) / 255
        core = h ** 0.8
        a = np.clip(core + glow * 0.8, 0, 1)[..., None]
        img = img * (1 - a * 0.9) + col * a * 0.9 + col * glow[..., None] * 0.25
    return img


if __name__ == "__main__":
    main()
