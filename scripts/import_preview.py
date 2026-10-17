# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pillow"]
# ///
"""Small pictures of a splat file for the import dialog (0.6.1): one per choice of "up", as the scene would
stand on the Frame, so the upright one can simply be picked – plus a guess which one it is.

Usage: python3 import_preview.py <splat file> <out dir> [splat-transform]
  Writes <out dir>/y-down.png, y-up.png, z-up.png and prints {"guess": "..."} as JSON.
  Files other than a plain PLY (.sog .spz .splat .ksplat .compressed.ply) are turned into one first with
  splat-transform.

Each view is a small picture as from the Frame at the scene's start, looking level, with Y pointing down as in
our scenes. The turns are the ones scripts/import.sh makes.
The guess (see guess()) only preselects one: the pictures decide.
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

TURNS = {                                                    # file -> our scene (Y down), as scripts/import.sh
    "y-down": np.eye(3),
    "y-up": np.array([[1, 0, 0], [0, -1, 0], [0, 0, -1]], dtype=np.float64),     # 180° about X
    "z-up": np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=np.float64),      # +Z becomes -Y
}
W, H = 320, 200
MAX_POINTS = 2_000_000
FOV = 100                                                    # degrees across, about what the Frame shows
SH_C0 = 0.28209479177387814
BG = (24, 27, 34)


def plain_ply(path: Path) -> bool:
    """A PLY with one float per property and the usual splat columns (what load() reads)."""
    if path.suffix.lower() != ".ply":
        return False
    with open(path, "rb") as f:
        head = f.read(64 * 1024)
    if b"end_header" not in head:
        return False
    lines = head.split(b"end_header")[0].decode(errors="replace").splitlines()
    props = [ln.split() for ln in lines if ln.startswith("property")]
    names = {p[-1] for p in props}
    return (sum(ln.startswith("element") for ln in lines) == 1 and all(p[1] == "float" for p in props)
            and {"x", "y", "z", "opacity", "f_dc_0", "f_dc_1", "f_dc_2"} <= names
            and "format binary_little_endian 1.0" in lines)


def load(path: Path):
    """x, y, z, rgb and a size per splat that is not nearly clear (memory-mapped, at most MAX_POINTS of them)."""
    with open(path, "rb") as f:
        props, n = [], 0
        while (raw := f.readline()) and (line := raw.decode(errors="replace").strip()) != "end_header":
            if line.startswith("element vertex"):
                n = int(line.split()[-1])
            elif line.startswith("property"):
                props.append(line.split()[-1])
        if not raw or not n:
            sys.exit(f"{path.name}: not a splat file")
        offset = f.tell()
    data = np.memmap(path, dtype=np.dtype([(p, "<f4") for p in props]), mode="r", offset=offset, shape=(n,))
    rng = np.random.default_rng(0)
    d = data[np.sort(rng.choice(n, MAX_POINTS, replace=False))] if n > MAX_POINTS else data[:]
    d = d[1 / (1 + np.exp(-d["opacity"].astype(np.float64))) > 0.1]
    xyz = np.stack([d["x"], d["y"], d["z"]], axis=1).astype(np.float64)
    rgb = np.clip(0.5 + SH_C0 * np.stack([d["f_dc_0"], d["f_dc_1"], d["f_dc_2"]], axis=1), 0, 1).astype(np.float32)
    if {"scale_0", "scale_1", "scale_2"} <= set(props):
        sc = np.stack([d["scale_0"], d["scale_1"], d["scale_2"]], axis=1).astype(np.float64)
        size = np.exp(np.clip(sc.max(axis=1), -20, 5))
    else:
        size = np.full(len(d), 0.01)
    ok = np.isfinite(xyz).all(axis=1) & np.isfinite(size)
    return xyz[ok], rgb[ok], size[ok]


def eye_of(xyz: np.ndarray) -> np.ndarray:
    """Where to look from: the file's origin if it lies in the scene (the Frame's start, the middle of the
    cameras for COLMAP-based tools) – otherwise the middle of the splats."""
    lo, hi = np.percentile(xyz, 5, axis=0), np.percentile(xyz, 95, axis=0)
    return np.zeros(3) if ((lo < 0) & (hi > 0)).all() else np.median(xyz, axis=0)


def guess(xyz: np.ndarray) -> str:
    """The vertical is the smaller of the Y and Z extents (a room is wider than it is high, so is a landscape;
    X is never up in the choices). Along Y, there is usually more down by the floor than up by the ceiling."""
    lo, hi = np.percentile(xyz, 2, axis=0), np.percentile(xyz, 98, axis=0)
    ext = hi - lo
    if ext[2] < ext[1]:
        return "z-up"
    return "y-down" if np.median(xyz[:, 1]) >= (lo[1] + hi[1]) / 2 else "y-up"      # Y down: the floor has the larger y


def view(xyz: np.ndarray, rgb: np.ndarray, size: np.ndarray, eye: np.ndarray) -> Image.Image:
    """A small picture as from the Frame at the scene's start: from eye, looking level along +z, x to the
    right and y (down) downwards; each splat a dot as big as it looks, the nearest one in each pixel wins."""
    p = xyz - eye
    z = p[:, 2]
    near = 0.04 * float((np.percentile(xyz, 95, axis=0) - np.percentile(xyz, 5, axis=0)).max())
    f = W / 2 / np.tan(np.radians(FOV / 2))
    m = z > near                                             # what floats right in front of the eye hides all
    p, z, c, sz = p[m], z[m], rgb[m], size[m]
    u = np.round(W / 2 + f * p[:, 0] / z).astype(int)
    v = np.round(H / 2 + f * p[:, 1] / z).astype(int)
    r = np.clip(np.round(f * sz / z * 0.5), 0, 2).astype(int)
    ok = (u >= -3) & (u < W + 3) & (v >= -3) & (v < H + 3)
    u, v, z, c, r = u[ok], v[ok], z[ok], c[ok], r[ok]
    pu, pv, pz, pc = [], [], [], []
    for rad in range(3):                                     # each dot as a small square of its size
        k = r == rad
        for dy in range(-rad, rad + 1):
            for dx in range(-rad, rad + 1):
                pu.append(u[k] + dx); pv.append(v[k] + dy); pz.append(z[k]); pc.append(c[k])
    u, v, z, c = np.concatenate(pu), np.concatenate(pv), np.concatenate(pz), np.concatenate(pc)
    ok = (u >= 0) & (u < W) & (v >= 0) & (v < H)
    u, v, z, c = u[ok], v[ok], z[ok], c[ok]
    img = np.zeros((H, W, 3), np.float32)
    img[:] = BG
    if len(z):
        order = np.argsort(-z)                               # far first: the near ones are written last
        c = c / max(float(np.percentile(c.max(axis=1), 95)), 0.05) * 0.9      # rooms come out dim otherwise
        img[v[order], u[order]] = np.clip(c[order], 0, 1) * 255
    return Image.fromarray(img.astype(np.uint8))


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    ply = src
    if not plain_ply(src):
        if len(sys.argv) < 4 or not sys.argv[3]:
            sys.exit("splat-transform is needed for this file")
        ply = out / "as.ply"
        if subprocess.run([sys.argv[3], "-w", str(src), str(ply)], stdout=subprocess.DEVNULL).returncode:
            sys.exit(f"{src.name}: splat-transform cannot read it")
    xyz, rgb, size = load(ply)
    if ply != src:
        ply.unlink()
    if not len(xyz):
        sys.exit("no splats in the file")
    eye = eye_of(xyz)
    for up, r in TURNS.items():
        view(xyz @ r.T, rgb, size, eye @ r.T).save(out / f"{up}.png")
    print(json.dumps({"guess": guess(xyz)}))


if __name__ == "__main__":
    main()
