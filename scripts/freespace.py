# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy"]
# ///
"""EXPERIMENTAL, not wired into the interface yet (0.4): splats in space the cameras looked through.

For every (sampled) image: the SfM points it saw give a coarse depth map (per cell the nearest seen
surface). A splat that projects into such a cell clearly in front of that surface sat in the line of sight
of a camera that saw something behind it: free space. Removed when enough images say so.
Usage: freespace.py <scene.ply> <out.ply> <run>[,<run>...] [--every N] [--strength S] [--solid A]
  <run>     the recordings the scene was made of (their sparse model and the Frame's tracking); the
            scene must be in their fitted frame, as combine.py makes it (alignment "images")
  --solid A where the SfM points say nothing (white walls and ceilings), use the splats more opaque than A

First try on a real living room (15.10.2026): only 0.4 % of the splats found, mostly faint ones. The SfM
points – the only reliable "something is here" – are missing exactly on white surfaces, and the dark veil
under a white ceiling hangs closer to it (10–35 cm) than this can tell apart; scripts/clean.py --ceiling
is the tool for that.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parent))
import combine as C                                                   # noqa: E402

NP = {0: 3, 1: 4, 2: 4, 3: 5, 4: 8, 5: 8, 6: 12, 7: 5, 8: 4, 9: 5, 10: 12}
GRID = 48                                   # depth cells per image side


def read_cams(path):
    out = {}
    with open(path, "rb") as f:
        n, = struct.unpack("<Q", f.read(8))
        for _ in range(n):
            cid, model, w, h = struct.unpack("<iiQQ", f.read(24))
            out[cid] = (model, w, h, np.array(struct.unpack("<%dd" % NP[model], f.read(8 * NP[model]))))
    return out


def read_images(path):
    out = []
    with open(path, "rb") as f:
        n, = struct.unpack("<Q", f.read(8))
        for _ in range(n):
            _, qw, qx, qy, qz, tx, ty, tz, cid = struct.unpack("<i7di", f.read(64))
            name = b""
            while (ch := f.read(1)) != b"\0":
                name += ch
            k, = struct.unpack("<Q", f.read(8))
            obs = np.frombuffer(f.read(24 * k), dtype=np.dtype([("x", "<f8"), ("y", "<f8"), ("id", "<i8")]))
            R = Rotation.from_quat([qx, qy, qz, qw]).as_matrix()
            out.append((name.decode(), R, np.array([tx, ty, tz]), cid, obs[obs["id"] >= 0].copy()))
    return out


def read_points3d(path):
    ids, xyz = [], []
    with open(path, "rb") as f:
        n, = struct.unpack("<Q", f.read(8))
        for _ in range(n):
            pid, x, y, z, _, _, _, e, k = struct.unpack("<Q3d3BdQ", f.read(51))
            f.seek(8 * k, 1)
            ids.append(pid)
            xyz.append((x, y, z))
    ids = np.array(ids)
    table = np.full(ids.max() + 1, -1)
    table[ids] = np.arange(len(ids))
    return table, np.array(xyz)


def project(cam, pc):
    """Camera coordinates -> pixels (OPENCV_FISHEYE or pinhole); valid only for z > 0."""
    model, w, h, p = cam
    x, y = pc[:, 0] / pc[:, 2], pc[:, 1] / pc[:, 2]
    if model == 5:
        fx, fy, cx, cy, k1, k2, k3, k4 = p
        r = np.sqrt(x * x + y * y)
        th = np.arctan(r)
        thd = th * (1 + k1 * th**2 + k2 * th**4 + k3 * th**6 + k4 * th**8)
        s = np.where(r > 1e-9, thd / np.maximum(r, 1e-9), 1.0)
        return fx * x * s + cx, fy * y * s + cy
    fx, fy, cx, cy = (p[0], p[0], p[1], p[2]) if model in (0, 2, 3) else p[:4]
    return fx * x + cx, fy * y + cy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("runs")
    ap.add_argument("--every", type=int, default=4)
    ap.add_argument("--strength", type=float, default=0.5)
    ap.add_argument("--solid", type=float, default=0.0)
    args = ap.parse_args()
    header, v = C.read_ply(Path(args.src))
    P = np.stack([v["x"], v["y"], v["z"]], 1).astype(np.float64)
    A = 1 / (1 + np.exp(-v["opacity"].astype(np.float64)))
    free = np.zeros(len(v), np.int32)
    seen = np.zeros(len(v), np.int32)
    for run in args.runs.split(","):
        run = Path(run)
        sp = run / "dataset/sparse/0"
        cams, imgs = read_cams(sp / "cameras.bin"), read_images(sp / "images.bin")
        table, X = read_points3d(sp / "points3D.bin")
        # the room's frame: fitted to the Frame's tracking, as combine.py does (alignment "images")
        ai = json.loads((run / "dataset/main.aiscene").read_text())
        rec = {f["path"].removeprefix("images/"): np.array(f["worldFromCam"])[:3, 3] for f in ai["frames"]}
        names = [i[0].replace("\\", "/") for i in imgs]
        centres = np.array([-i[1].T @ i[2] for i in imgs])
        both = [k for k, nm in enumerate(names) if nm in rec]
        fit = C.umeyama(centres[both], np.array([rec[names[k]] for k in both]))
        # splats into this recording's own frame (inverse of the fit), cameras stay as they are
        Q = (fit.R.T @ ((P - fit.t) / fit.s).T).T
        for name, R, t, cid, obs in imgs[::args.every]:
            cam = cams[cid]
            _, w, h, _ = cam
            pts = X[table[obs["id"]]]
            pc = (R @ pts.T).T + t
            ok = pc[:, 2] > 0.05
            cx = np.clip((obs["x"][ok] / w * GRID).astype(int), 0, GRID - 1)
            cy = np.clip((obs["y"][ok] / h * GRID).astype(int), 0, GRID - 1)
            depth = np.full(GRID * GRID, np.inf)
            np.minimum.at(depth, cy * GRID + cx, pc[ok, 2])
            sc = (R @ Q.T).T + t
            front = sc[:, 2] > 0.1
            if args.solid:                      # where SfM saw nothing (a white ceiling): the solid splats
                so = front & (A > args.solid)
                us, vs = project(cam, sc[so])
                ins = (us >= 0) & (us < w) & (vs >= 0) & (vs < h)
                cs = (np.clip((vs[ins] / h * GRID).astype(int), 0, GRID - 1) * GRID
                      + np.clip((us[ins] / w * GRID).astype(int), 0, GRID - 1))
                sd = np.full(GRID * GRID, np.inf)
                np.minimum.at(sd, cs, sc[so][ins, 2])
                depth = np.where(np.isfinite(depth), depth, sd)
            u, vv = project(cam, sc[front])
            inside = (u >= 0) & (u < w) & (vv >= 0) & (vv < h)
            idx = np.flatnonzero(front)[inside]
            cell = (np.clip((vv[inside] / h * GRID).astype(int), 0, GRID - 1) * GRID
                    + np.clip((u[inside] / w * GRID).astype(int), 0, GRID - 1))
            d = depth[cell]
            known = np.isfinite(d)
            z = sc[idx, 2]
            seen[idx[known]] += 1
            margin = 0.15 + 0.1 * d[known]
            free[idx[known][z[known] < d[known] - margin]] += 1
        print(run.name, "images used:", len(imgs[::args.every]), flush=True)
    s = args.strength
    need = int(round(6 - 4 * s))                      # images that looked through it
    frac = 0.7 - 0.3 * s                              # of those that saw the place at all
    drop = (free >= need) & (free >= frac * np.maximum(seen, 1)) & (A < 0.9)
    print(f"free-space splats: {drop.sum():,} of {len(v):,} ({100 * drop.mean():.1f} %), "
          f"opacity median {np.median(A[drop]) if drop.any() else 0:.2f}")
    lines = [b"element vertex %d\n" % (~drop).sum() if l.startswith(b"element vertex") else l
             for l in header.splitlines(keepends=True)]
    with open(args.dst, "wb") as f:
        f.write(b"".join(lines))
        v[~drop].tofile(f)


if __name__ == "__main__":
    main()
