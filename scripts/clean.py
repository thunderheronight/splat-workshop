# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "pillow"]
# ///
"""Clean up a finished scene or room: the things a reconstruction of a real room tends to get wrong.

Usage: uv run clean.py <in.ply> <out.ply> [--ceiling S] [--floor S] [--haze S] [--outside S]

Every strength S runs from 0 (off) to 1 (strongest). The room is found as for the floor plan (walls 0.9 to
1.9 m above the floor, turned to them; Y points down).
  --ceiling  A white ceiling has no texture: the reconstruction finds nothing to hold on to, leaves the
             ceiling thin and hangs a dark veil under it. Takes out the veil (dark or thin splats from
             10 cm to 60 cm under the ceiling – how deep grows with S; dark but solid things like a cupboard
             top or a bird cage stay if they are within 50 cm of a wall) and everything above the ceiling
             (found where walls and ceiling meet), and puts in a closed ceiling in the colour of what there
             is of it nearby.
  --floor    Fills holes in the floor (where the floor itself has too little: more with S) in the colour of
             the floor nearby, and takes out what lies under it.
  --haze     Takes out big, nearly transparent blobs floating in the room. Measured by their *smallest*
             axis: long thin splats (wires, bars of a cage, cables) are no haze. More with S.
  --outside  Takes out what lies well outside the room (beyond the walls, above the ceiling, under the
             floor): from 1 m beyond it (S near 0) to 30 cm (S = 1).
Prints what it did; the input is never changed.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import floorplan as F                                              # noqa: E402  (room box, wall angle)

C0 = 0.28209479177387814
STEP = 0.02                               # grid of a filled ceiling or floor (m)


def read_ply(path):
    with open(path, "rb") as f:
        header = b""
        while not header.endswith(b"end_header\n"):
            line = f.readline()
            if not line:
                sys.exit(f"Not a PLY file: {path}")
            header += line
        props = [l.split()[-1].decode() for l in header.splitlines() if l.startswith(b"property")]
        n = int(next(l for l in header.splitlines() if l.startswith(b"element vertex")).split()[-1])
        v = np.fromfile(f, dtype=np.dtype([(p, "<f4") for p in props]), count=n)
    return header, v


def write_ply(path, header, v):
    lines = [b"element vertex %d\n" % len(v) if l.startswith(b"element vertex") else l
             for l in header.splitlines(keepends=True)]
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "wb") as f:
        f.write(b"".join(lines))
        v.tofile(f)
    tmp.replace(path)


class Room:
    """Where floor, ceiling and walls are, from the solid splats."""
    def __init__(self, xyz, alpha):
        solid = xyz[alpha > 0.5]
        if len(solid) < 1000:
            sys.exit("Too few solid splats to find the room")
        self.floor, _ = F.heights(solid[:, 1])
        up = self.floor - solid[:, 1]
        walls = solid[(up > 0.9) & (up < 1.9)][:, [0, 2]]
        sub = walls[np.random.default_rng(0).choice(len(walls), min(len(walls), 150_000), replace=False)]
        self.angle = F.wall_angle(sub, 0.03)
        box = F.room_box(F.rotate(walls, self.angle), 0.05)
        if box is None:
            sys.exit("No walls found")
        self.lo, self.hi = box
        self.up = self.floor - xyz[:, 1]                       # height above the floor of every splat
        self.p = F.rotate(xyz[:, [0, 2]], self.angle)         # floor plan position, walls axis-aligned

    def within(self, margin):
        """Splats inside the walls, `margin` metres away from them (negative: beyond them)."""
        return np.all((self.p > self.lo + margin) & (self.p < self.hi - margin), axis=1)

    def grid(self):
        g0, g1 = np.meshgrid(np.arange(self.lo[0], self.hi[0], STEP), np.arange(self.lo[1], self.hi[1], STEP),
                             indexing="ij")
        return np.stack([g0.ravel(), g1.ravel()], 1)


def layer(room, v, at, cells, ref, rgb, alpha):
    """Flat splats (3 cm wide, 2 mm thick) at height `at` above the floor in the floor plan cells, in the
    colour of the reference splats `ref` nearby (half of it the overall colour: calm, no blotches)."""
    overall = np.average(rgb[ref], weights=alpha[ref], axis=0) if ref.any() else np.array([0.8, 0.8, 0.8])
    col = np.tile(overall, (len(cells), 1))
    if ref.sum() >= 10:
        d, j = cKDTree(room.p[ref]).query(cells, k=32, distance_upper_bound=0.5, workers=-1)
        ok = np.isfinite(d)
        j = np.where(ok, j, 0)
        w = np.where(ok, alpha[ref][j] / (0.05 + np.where(ok, d, 1)), 0)
        near = (w[:, :, None] * rgb[ref][j]).sum(1) / np.maximum(w.sum(1, keepdims=True), 1e-9)
        col = np.where(w.sum(1, keepdims=True) > 0, 0.5 * near + 0.5 * overall, overall)
    new = np.zeros(len(cells), dtype=v.dtype)
    xz = F.rotate(cells, -room.angle)
    new["x"], new["z"], new["y"] = xz[:, 0], xz[:, 1], room.floor - at
    for k in range(3):
        new[f"f_dc_{k}"] = (np.clip(col[:, k], 0, 1) - 0.5) / C0
    new["opacity"] = np.log(0.98 / 0.02)
    new["scale_0"] = new["scale_2"] = np.log(0.015)
    new["scale_1"] = np.log(0.002)                              # flat: y is up/down
    new["rot_0"] = 1.0
    return new


def ceiling(room, v, rgb, lum, alpha, s):
    """Height of the ceiling, what to take out (veil, above it), the new ceiling."""
    inside = room.within(0)
    # where walls and ceiling meet, solid splats pile up: the strongest line near the walls above 2 m
    # (the ceiling itself, white and thin, is no good to find it)
    corner = inside & ~room.within(0.3) & (alpha > 0.5) & (room.up > 2.0) & (room.up < 3.5)
    if corner.sum() < 100:
        print("Ceiling: not found, left as it is")
        return np.zeros(len(v), bool), v[:0]
    h, edges = np.histogram(room.up[corner], bins=np.arange(2.0, 3.5, 0.02))
    H = float(edges[np.argmax(np.convolve(h, [1, 2, 1], "same"))] + 0.01)
    bright = inside & (lum > 0.3) & (room.up > 2.0)
    depth = 0.1 + 0.5 * s
    under = (room.up > H - depth) & (room.up < H - 0.03)
    veil = under & ((room.within(0.15) & (alpha < 0.3)) | (room.within(0.5) & (lum < 0.3)))
    above = inside & (room.up > H + 0.05)
    new = layer(room, v, H, room.grid(), bright & (np.abs(room.up - H) < 0.08), rgb, alpha)
    print(f"Ceiling {H:.2f} m above the floor: veil ({depth:.2f} m deep) {veil.sum():,} splats and "
          f"{above.sum():,} above it out, closed with {len(new):,}")
    return veil | above, new


def floor(room, v, rgb, alpha, s):
    """Holes in the floor filled, what lies under it out."""
    inside = room.within(0)
    below = inside & (room.up < -0.05)
    near = inside & (np.abs(room.up) < 0.04)
    cells = room.grid()
    n = (len(np.arange(room.lo[0], room.hi[0], STEP)), len(np.arange(room.lo[1], room.hi[1], STEP)))
    idx = np.clip(np.floor((room.p[near] - room.lo) / STEP).astype(np.int64), 0, np.array(n) - 1)
    n0 = n[1]
    cover = np.bincount(idx[:, 0] * n0 + idx[:, 1], alpha[near], len(cells))
    # a cell and its neighbours: a floor a centimetre off still counts
    c2 = cover.reshape(-1, n0)
    pad = np.pad(c2, 1)
    around = sum(pad[1 + dx:1 + dx + c2.shape[0], 1 + dy:1 + dy + c2.shape[1]]
                 for dx in (-1, 0, 1) for dy in (-1, 0, 1)).ravel()
    hole = around < 1.5 * s
    new = layer(room, v, 0.0, cells[hole], near & (alpha > 0.3), rgb, alpha)
    print(f"Floor: {below.sum():,} splats under it out, {hole.sum():,} holes ({100 * hole.mean():.0f} %) filled")
    return below, new


def haze(v, alpha, s):
    smallest = np.exp(np.stack([v["scale_0"], v["scale_1"], v["scale_2"]], 1).astype(np.float64)).min(1)
    size, opacity = 0.04 - 0.025 * s, 0.05 + 0.15 * s
    out = (smallest > size) & (alpha < opacity)
    print(f"Haze: {out.sum():,} splats out (all axes over {size * 100:.1f} cm, under {opacity * 100:.0f} % opaque)")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    for k in ("ceiling", "floor", "haze", "outside"):
        ap.add_argument(f"--{k}", type=float, default=0.0)
    args = ap.parse_args()
    for k in ("ceiling", "floor", "haze", "outside"):
        if not 0 <= getattr(args, k) <= 1:
            sys.exit(f"--{k} must be between 0 and 1")
    header, v = read_ply(args.src)
    xyz = np.stack([v["x"], v["y"], v["z"]], 1).astype(np.float64)
    alpha = 1 / (1 + np.exp(-v["opacity"].astype(np.float64)))
    rgb = 0.5 + C0 * np.stack([v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], 1).astype(np.float64)
    lum = rgb @ [0.299, 0.587, 0.114]
    room = Room(xyz, alpha)
    print(f"Room {room.hi[0] - room.lo[0]:.1f} × {room.hi[1] - room.lo[1]:.1f} m, {len(v):,} splats")
    drop, added = np.zeros(len(v), bool), []
    if args.ceiling:
        d, new = ceiling(room, v, rgb, lum, alpha, args.ceiling)
        drop |= d
        added.append(new)
    if args.floor:
        d, new = floor(room, v, rgb, alpha, args.floor)
        drop |= d
        added.append(new)
    if args.haze:
        drop |= haze(v, alpha, args.haze)
    if args.outside:
        m = 1.0 - 0.7 * args.outside
        solid_in = room.up[room.within(0) & (alpha > 0.5)]
        top = max(np.percentile(solid_in, 99.5), 2.0) if len(solid_in) else 2.5
        out = ~room.within(-m) | (room.up < -m) | (room.up > top + m)
        print(f"Outside: {out.sum():,} splats more than {m:.2f} m outside the room out")
        drop |= out
    result = np.concatenate([v[~drop], *added])
    write_ply(args.dst, header, result)
    print(f"{len(result):,} splats ({drop.sum():,} out, {sum(len(a) for a in added):,} in)")


if __name__ == "__main__":
    main()
