# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy"]
# ///
"""Put several scenes of one recording session together into one big scene.

Usage: uv run combine.py <out folder> <run folder>=<label> [<run folder>=<label> ...]
                         [--align frame|images|shift|rigid] [--partition view|nearest] [--rooms JSON]
                       [--colour match|keep]

--rooms: the room of every recording, in the same order, e.g. '["Kitchen", "Kitchen", "Hallway"]' – a room
can be made of several recordings (default: every recording is a room of its own).

All recordings of one session share the Steam Frame's tracking frame, so the rooms are already
roughly in place. Steps:
  1. Alignment (--align):
       frame   as the pipeline delivers it (aligned on the first image of each recording)
       images  every room fitted to the Frame's tracking over all its images (default)
       shift   images + a joint shift of the rooms so the overlaps (doors, passages) match
       rigid   images + a joint shift and slight rotation
  2. Who owns which part (--partition):
       view     per 10 cm cube the recording that saw it from close by and looking at it; the first and
                last stretch of a recording along the round of another room's recording (walking in/out)
                does not count, so the borders between rooms run through doors (default). Recordings of
                the same room simply share it: every part from the recording that saw it best.
       nearest  the recording whose camera was closest
     Either way a cube only goes to a recording that really has something there: at least a quarter of
     what the richest one has (splat opacity summed per cube). A camera that "looked at" a place from
     behind the sofa does not leave a dark hole there.
  2b. Colour (--colour): every training settles on its own brightness, so the same wall can come out a
     third darker in one recording than in the next. `match` (default) brings all of them to one
     brightness per colour channel, from the cubes they share; `keep` leaves them as they are.
  3. Every room is transformed, cut to its own part and appended – one room in memory at a time.

Writes <out>/spirula/scene.ply (all splats), <out>/scene.xrSceneTransform.json (start view of the
room in the middle) and <out>/combine.json (settings and how well the overlaps match).
Lines starting with "@@" are progress markers for the web interface.
"""
import argparse
import itertools
import json
import shutil
import struct
import sys
import time
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation

VOXEL, RADIUS, K = 0.10, 1.5, 64           # ownership: cube size, camera reach, cameras looked at
SHARE = 0.25                               # a cube's owner needs at least this share of the richest one's splats
SOLID = 3.0                                # summed opacity of a cube something really sits in
GAIN = (0.7, 1.4)                          # colour matching never changes a recording more than this
C0 = 0.28209479177387814                   # spherical harmonics, band 0
BOTH = 1.5                                 # an overlap: places within this distance of both recordings' cameras


def progress(pct):
    print(f"@@ pct {int(pct)}", flush=True)


# ---------------------------------------------------------------- reading

def read_cameras(path: Path):
    """Image names, camera centres, viewing directions (COLMAP: the camera looks along +z)."""
    names, centres, fwd = [], [], []
    with open(path, "rb") as f:
        n, = struct.unpack("<Q", f.read(8))
        for _ in range(n):
            _, qw, qx, qy, qz, tx, ty, tz, _ = struct.unpack("<i7di", f.read(64))
            name = b""
            while (ch := f.read(1)) != b"\0":
                name += ch
            k, = struct.unpack("<Q", f.read(8))
            f.seek(24 * k, 1)
            R = Rotation.from_quat([qx, qy, qz, qw]).as_matrix()
            names.append(name.decode().replace("\\", "/"))
            centres.append(-R.T @ np.array([tx, ty, tz]))
            fwd.append(R.T[:, 2])
    return names, np.array(centres), np.array(fwd)


def read_points(path: Path):
    """Sparse points with a small reprojection error."""
    with open(path, "rb") as f:
        n, = struct.unpack("<Q", f.read(8))
        xyz, err = np.empty((n, 3)), np.empty(n)
        for i in range(n):
            _, x, y, z, _, _, _, e, k = struct.unpack("<Q3d3BdQ", f.read(51))
            f.seek(8 * k, 1)
            xyz[i], err[i] = (x, y, z), e
    return xyz[err < 2.0]


def read_ply(path: Path):
    with open(path, "rb") as f:
        header = b""
        while not header.endswith(b"end_header\n"):
            line = f.readline()
            if not line:
                sys.exit(f"Not a PLY file: {path}")
            header += line
        props = [l.split()[-1].decode() for l in header.splitlines() if l.startswith(b"property")]
        n = int(next(l for l in header.splitlines() if l.startswith(b"element vertex")).split()[-1])
        data = np.fromfile(f, dtype=np.dtype([(p, "<f4") for p in props]), count=n)
    return header, data


def image_time(name: str) -> float:
    """cam0/0123-45.678901.jpg -> 45.678901 (seconds into the recording)."""
    return float(name.rsplit("/", 1)[-1].split("-", 1)[1].removesuffix(".jpg"))


# ---------------------------------------------------------------- transforms

class Pose:
    """x -> s R x + t"""
    def __init__(self, s=1.0, R=None, t=None):
        self.s, self.R, self.t = s, np.eye(3) if R is None else R, np.zeros(3) if t is None else t

    def __call__(self, p):
        return (self.s * (self.R @ p.T)).T + self.t

    def then(self, R, t, centre):
        """Followed by a rigid motion about `centre`."""
        return Pose(self.s, R @ self.R, R @ (self.t - centre) + centre + t)


def umeyama(A, B):
    """Similarity that maps the points A onto B (least squares)."""
    ma, mb = A.mean(0), B.mean(0)
    a, b = A - ma, B - mb
    U, S, Vt = np.linalg.svd(b.T @ a / len(A))
    D = np.eye(3)
    if np.linalg.det(U @ Vt) < 0:
        D[2, 2] = -1
    R = U @ D @ Vt
    s = np.trace(np.diag(S) @ D) / a.var(0).sum()
    return Pose(s, R, mb - s * R @ ma)


def quat_mul(a, b):
    """a (w, x, y, z) times every row of b."""
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b.T
    return np.stack([w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                     w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2], axis=1)


# ---------------------------------------------------------------- overlaps

def overlap_zones(rooms):
    """Sparse points of each pair where both recordings were (doors, passages). The two sides of a
    wall are never both within reach of both, so they are not mistaken for the same surface."""
    zones = {}
    for a, b in itertools.combinations(rooms, 2):
        ta, tb = cKDTree(rooms[a]["cams"]), cKDTree(rooms[b]["cams"])
        both = lambda p: p[(ta.query(p)[0] < BOTH) & (tb.query(p)[0] < BOTH)]
        za, zb = both(rooms[a]["pts"]), both(rooms[b]["pts"])
        if len(za) >= 1000 and len(zb) >= 1000:
            zones[(a, b)] = (za, zb)
    return zones


def offset(pa, pb):
    """Shift that brings the overlap pa onto pb (robust, a few ICP steps) and how many points matched."""
    tree = cKDTree(pb)
    d, i = tree.query(pa, distance_upper_bound=0.08)
    ok = np.isfinite(d)
    if ok.sum() < 100:
        return None, 0
    P, Q, tot = pa[ok], pb[i[ok]], np.zeros(3)
    for _ in range(15):
        delta = np.median(Q - P, axis=0)
        P, tot = P + delta, tot + delta
        d2, i2 = tree.query(P, distance_upper_bound=0.05)
        ok2 = np.isfinite(d2)
        if ok2.sum() < 50:
            break
        P, Q = P[ok2], pb[i2[ok2]]
    return tot, int(ok.sum())


def seams(rooms, zones, poses):
    out = {}
    for (a, b), (za, zb) in zones.items():
        tot, n = offset(poses[a](za), poses[b](zb))
        if tot is not None:
            out[f"{a}|{b}"] = {"cm": round(float(np.linalg.norm(tot)) * 100, 1), "points": n}
    return out


def joint_shift(rooms, zones, poses, fixed):
    """Shift every room (except `fixed`) so all overlaps match best together (least squares)."""
    free = [l for l in rooms if l != fixed]
    rows, rhs = [], []
    for (a, b), (za, zb) in zones.items():
        tot, n = offset(poses[a](za), poses[b](zb))
        if tot is None:
            continue
        row = np.zeros(len(free))
        if a in free:
            row[free.index(a)] += 1             # (x_a + t_a) meets (x_b + t_b) when t_a - t_b = tot
        if b in free:
            row[free.index(b)] -= 1
        rows.append(row * np.sqrt(n))
        rhs.append(tot * np.sqrt(n))
    if not rows:
        return poses
    sol, *_ = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)
    return {l: poses[l] if l == fixed else poses[l].then(np.eye(3), sol[free.index(l)], np.zeros(3)) for l in rooms}


def joint_rigid(rooms, zones, poses, fixed, centre):
    """Small rotation + shift per room (except `fixed`) over the point pairs of all overlaps."""
    free = [l for l in rooms if l != fixed]
    rng = np.random.default_rng(1)
    sub = {k: (za[rng.choice(len(za), min(len(za), 20000), replace=False)], zb) for k, (za, zb) in zones.items()}
    for cut in (0.08, 0.06, 0.05, 0.04, 0.03, 0.03):
        rows, rhs = [], []
        for (a, b), (za, zb) in sub.items():
            pa, pbz = poses[a](za), poses[b](zb)
            d, i = cKDTree(pbz).query(pa, distance_upper_bound=cut)
            ok = np.isfinite(d)
            if ok.sum() < 200:
                continue
            P, Q = pa[ok] - centre, pbz[i[ok]] - centre
            n, w = len(P), 1 / np.sqrt(ok.sum())        # every overlap counts about equally

            def cross(X):                                # rows of -[X]x: (w x X) = cross(X) @ w
                z = np.zeros(n)
                return -np.stack([np.stack([z, -X[:, 2], X[:, 1]], 1), np.stack([X[:, 2], z, -X[:, 0]], 1),
                                  np.stack([-X[:, 1], X[:, 0], z], 1)], 1)
            A = np.zeros((n, 3, 6 * len(free)))
            if a in free:
                j = free.index(a) * 6
                A[:, :, j:j + 3], A[:, :, j + 3:j + 6] = cross(P), np.eye(3)
            if b in free:
                j = free.index(b) * 6
                A[:, :, j:j + 3], A[:, :, j + 3:j + 6] = -cross(Q), -np.eye(3)
            rows.append(A.reshape(-1, 6 * len(free)) * w)
            rhs.append(-(P - Q).reshape(-1) * w)
        if not rows:
            break
        x, *_ = np.linalg.lstsq(np.vstack(rows), np.concatenate(rhs), rcond=None)
        for l in free:
            j = free.index(l) * 6
            poses[l] = poses[l].then(Rotation.from_rotvec(x[j:j + 3]).as_matrix(), x[j + 3:j + 6], centre)
    return poses


# ---------------------------------------------------------------- what every recording has where

def cube_codes(points):
    """10 cm cube of every point as one number (±100 km is plenty)."""
    k = np.floor(points / VOXEL).astype(np.int64) + (1 << 20)
    return (k[:, 0] << 42) | (k[:, 1] << 21) | k[:, 2]


def contents(clouds):
    """clouds: per recording (cube codes, opacity, rgb) of its splats. Per cube: summed opacity and
    opacity-weighted colour of every recording – the cube codes (sorted), cover [cubes, n], colour [cubes, n, 3]."""
    codes = [c for c, _, _ in clouds]
    table = np.unique(np.concatenate(codes))
    cover = np.zeros((len(table), len(clouds)))
    colour = np.zeros((len(table), len(clouds), 3))
    for i, (c, (_, a, rgb)) in enumerate(zip(codes, clouds)):
        j = np.searchsorted(table, c)
        cover[:, i] = np.bincount(j, a, len(table))
        for ch in range(3):
            colour[:, i, ch] = np.bincount(j, a * rgb[:, ch], len(table))
    colour /= np.maximum(cover, 1e-9)[:, :, None]
    return table, cover, colour


def around(table, cover):
    """Summed over each cube and its 26 neighbours: two recordings' copies of one wall, a centimetre apart,
    often fall into neighbouring cubes – compared cube by cube, each would seem to have the wall alone."""
    out = np.zeros_like(cover)
    for dx, dy, dz in itertools.product((-1, 0, 1), repeat=3):
        n = table + ((dx << 42) + (dy << 21) + dz)
        j = np.clip(np.searchsorted(table, n), 0, len(table) - 1)
        hit = table[j] == n
        out[hit] += cover[j[hit]]
    return out


def colour_gains(cover, colour, min_cubes=200):
    """Per recording and colour channel a factor that brings all recordings to one brightness: from the
    cubes two of them both cover well, least squares on the log ratios, all factors together neutral."""
    n = cover.shape[1]
    gains = np.ones((n, 3))
    if n < 2:
        return gains
    for ch in range(3):
        rows, rhs = [], []
        for a, b in itertools.combinations(range(n), 2):
            m = (cover[:, a] > SOLID) & (cover[:, b] > SOLID) & (colour[:, a, ch] > 0.02) & (colour[:, b, ch] > 0.02)
            if m.sum() < min_cubes:
                continue
            row = np.zeros(n)                    # log g_a - log g_b = log(c_b / c_a)
            row[a], row[b] = 1, -1
            w = np.sqrt(m.sum())
            rows.append(row * w)
            rhs.append(np.median(np.log(colour[m, b, ch] / colour[m, a, ch])) * w)
        if not rows:
            continue
        rows.append(np.ones(n) * 100)            # together neutral: the room keeps its overall brightness
        rhs.append(0.0)
        x, *_ = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)
        gains[:, ch] = np.clip(np.exp(x), *GAIN)
    return gains


def apply_gain(v, gain):
    """Colour times `gain` (per channel), the view-dependent part included."""
    rest = sorted((p for p in v.dtype.names if p.startswith("f_rest_")), key=lambda p: int(p[7:]))
    per = len(rest) // 3                         # f_rest: all of red, then green, then blue
    for ch in range(3):
        g = np.float32(gain[ch])
        v[f"f_dc_{ch}"] = (g * (0.5 + C0 * v[f"f_dc_{ch}"]) - 0.5) / C0
        for p in rest[ch * per:(ch + 1) * per]:
            v[p] *= g


# ---------------------------------------------------------------- ownership

def owners(rooms, poses, partition, have=None):
    """Function: points -> index of the owning recording."""
    labels = list(rooms)
    cams = [poses[l](rooms[l]["cams"]) for l in labels]
    fwd = [(poses[l].R @ rooms[l]["fwd"].T).T for l in labels]
    owner = np.concatenate([np.full(len(c), i) for i, c in enumerate(cams)])

    def richest(lab, codes):
        """Only a recording with at least SHARE of the richest one's splats in a cube may own it."""
        if have is None:
            return lab
        table, cover = have
        j = np.clip(np.searchsorted(table, codes), 0, len(table) - 1)
        c = np.where((table[j] == codes)[:, None], cover[j], 0.0)
        able = c >= SHARE * c.max(1, keepdims=True)
        thin = ~able[np.arange(len(lab)), lab]
        lab = lab.copy()
        lab[thin] = c[thin].argmax(1)
        return lab

    if partition == "nearest":
        tree = cKDTree(np.vstack(cams))
        return lambda p: richest(owner[tree.query(p, workers=-1)[1]], cube_codes(p)), {}

    # walking in and out: the first and last stretch of a recording along the path of another room's
    # recording ... (recordings of the same room cover the same place on purpose: that is no walking in)
    n = len(cams)
    room = [rooms[l]["room"] for l in labels]
    elsewhere = lambda i, arrays: [a for j, a in enumerate(arrays) if room[j] != room[i]]

    def edges(i, others):
        if not others:
            return np.zeros(len(cams[i]), bool)
        inside = cKDTree(np.vstack(others)).query(cams[i])[0] < 0.4
        order = np.argsort(rooms[labels[i]]["times"])
        flag = np.zeros(len(order), bool)
        for seq in (order, order[::-1]):
            for k in seq:
                if not inside[k]:
                    break
                flag[k] = True
        return flag
    cand = [edges(i, elsewhere(i, cams)) for i in range(n)]
    # ... counts only if another recording covered that place during its own round; otherwise two
    # recordings that hand over to each other (one ends where the next starts) would cancel out
    transit = []
    for i in range(n):
        core = [c for c in elsewhere(i, [cams[j][~cand[j]] for j in range(n)]) if len(c)]
        transit.append(cand[i] & (cKDTree(np.vstack(core)).query(cams[i])[0] < 0.4) if core
                       else np.zeros(len(cams[i]), bool))
    transit = np.concatenate(transit)
    C, F, O = np.vstack(cams)[~transit], np.vstack(fwd)[~transit], owner[~transit]
    tree = cKDTree(C)

    def own(points):
        keys = np.floor(points / VOXEL).astype(np.int64)
        uniq, inv = np.unique(keys, axis=0, return_inverse=True)
        centre = (uniq + 0.5) * VOXEL
        lab = np.empty(len(uniq), dtype=np.int64)
        for s in range(0, len(uniq), 200_000):
            c = centre[s:s + 200_000]
            d, j = tree.query(c, k=K, distance_upper_bound=RADIUS, workers=-1)
            ok = np.isfinite(d)
            j = np.where(ok, j, 0)
            to = c[:, None, :] - C[j]
            cos = np.einsum("nkd,nkd->nk", to, F[j]) / np.maximum(np.linalg.norm(to, axis=2), 1e-6)
            w = np.where(ok & (cos > 0.3), (RADIUS - np.where(ok, d, RADIUS)) * cos, 0.0)
            score = np.stack([(w * (O[j] == r)).sum(1) for r in range(n)], axis=1)
            codes = cube_codes(c)
            if have is not None:                 # among the recordings that really have something there
                table, cover = have
                k = np.clip(np.searchsorted(table, codes), 0, len(table) - 1)
                cv = np.where((table[k] == codes)[:, None], cover[k], 0.0)
                score = np.where(cv >= SHARE * cv.max(1, keepdims=True), score, -1.0)
            best = score.argmax(1)
            none = score.max(1) <= 0
            if none.any():
                best[none] = O[tree.query(c[none], workers=-1)[1]]
            lab[s:s + 200_000] = richest(best, codes)
        return lab[inv.ravel()]
    return own, {labels[i]: int(t.sum()) for i, t in enumerate(np.split(transit, np.cumsum([len(c) for c in cams])[:-1]))}


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("runs", nargs="+", help="run folder=label")
    ap.add_argument("--align", choices=("frame", "images", "shift", "rigid"), default="images")
    ap.add_argument("--partition", choices=("view", "nearest"), default="view")
    ap.add_argument("--rooms", default="", help="JSON list: the room of every recording (default: one each)")
    ap.add_argument("--colour", choices=("match", "keep"), default="match")
    args = ap.parse_args()
    room_of = json.loads(args.rooms) if args.rooms else []
    if room_of and (not isinstance(room_of, list) or len(room_of) != len(args.runs)
                    or not all(isinstance(r, str) and r for r in room_of)):
        sys.exit("--rooms needs one room name per recording")
    out = Path(args.out)
    (out / "spirula").mkdir(parents=True, exist_ok=True)
    started = time.time()

    print("@@ cphase 0", flush=True)
    rooms = {}
    for i, arg in enumerate(args.runs):
        run, _, label = arg.partition("=")
        run = Path(run)
        label = label or run.name
        if label in rooms:
            label = f"{label} ({i + 1})"
        aiscene = json.loads((run / "dataset/main.aiscene").read_text())
        recorded = {f["path"].removeprefix("images/"): np.array(f["worldFromCam"])[:3, 3] for f in aiscene["frames"]}
        names, centres, fwd = read_cameras(run / "dataset/sparse/0/images.bin")
        both = [k for k, n in enumerate(names) if n in recorded]
        if len(both) < 10:
            sys.exit(f"{label}: too few cameras with Frame tracking – was it made with the Arcturus pipeline?")
        rooms[label] = {"run": run, "room": room_of[i] if room_of else label, "cams": centres[both], "fwd": fwd[both],
                        "times": np.array([image_time(names[k]) for k in both]),
                        "tracked": np.array([recorded[names[k]] for k in both]),
                        "pts": read_points(run / "dataset/sparse/0/points3D.bin")}
        progress(30 * (i + 1) / len(args.runs))

    # how far each room's reconstruction is from the Frame's tracking, as delivered and fitted
    fit = {l: umeyama(r["cams"], r["tracked"]) for l, r in rooms.items()}
    rms = lambda p, r: float(np.sqrt((np.linalg.norm(p(r["cams"]) - r["tracked"], axis=1) ** 2).mean()))
    room_info = {l: {"run": str(r["run"]), "room": r["room"], "cameras": len(r["cams"]), "tracking_cm": round(rms(Pose(), r) * 100, 1),
                     "fitted_cm": round(rms(fit[l], r) * 100, 1)} for l, r in rooms.items()}
    # From here on everything is in the fitted frame; `poses` are applied on top of it.
    for l, r in rooms.items():
        r["cams"], r["pts"] = fit[l](r["cams"]), fit[l](r["pts"])
        r["fwd"] = (fit[l].R @ r["fwd"].T).T
    poses = {l: Pose() for l in rooms}
    if args.align == "frame":                   # undo the fit: as the pipeline delivered it
        poses = {l: Pose(1 / fit[l].s, fit[l].R.T, -fit[l].R.T @ fit[l].t / fit[l].s) for l in rooms}
    zones = overlap_zones(rooms)
    progress(55)

    # one recording session: every room has to overlap with the others (one connected group)
    links = {l: set() for l in rooms}
    for a, b in zones:
        links[a].add(b)
        links[b].add(a)
    seen, todo = set(), [next(iter(rooms))]
    while todo:
        l = todo.pop()
        if l not in seen:
            seen.add(l)
            todo += list(links[l])
    if len(rooms) > 1 and seen != set(rooms):
        lone = ", ".join(sorted(set(rooms) - seen))
        print(f"@@ error not_connected {lone}", flush=True)
        sys.exit(f"These scenes do not touch the others: {lone}. Only rooms of one recording session "
                 "(without restarting the Frame in between) share one frame and can be put together.")
    hub = max(rooms, key=lambda l: (len(links[l]), sum(len(zones[k][0]) for k in zones if l in k)))
    before = seams(rooms, zones, poses)
    centre = np.vstack([r["cams"] for r in rooms.values()]).mean(0)
    if args.align == "shift":
        poses = joint_shift(rooms, zones, poses, hub)
    elif args.align == "rigid":
        poses = joint_rigid(rooms, zones, poses, hub, centre)
    after = seams(rooms, zones, poses)
    # from the room's own frame: first the fit, then the poses
    final = {l: Pose(poses[l].s * fit[l].s, poses[l].R @ fit[l].R, poses[l](fit[l].t)) for l in rooms}
    progress(100)

    print("@@ cphase 1", flush=True)
    clouds = []                                 # what every recording has where (one at a time in memory)
    for l, r in rooms.items():
        _, v = read_ply(r["run"] / "spirula" / "scene.ply")
        xyz = final[l](np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float64))
        rgb = 0.5 + C0 * np.stack([v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], axis=1).astype(np.float64)
        clouds.append((cube_codes(xyz), 1 / (1 + np.exp(-v["opacity"].astype(np.float64))), rgb.astype(np.float32)))
        del v, xyz, rgb
    table, cover, colour = contents(clouds)
    del clouds
    gains = colour_gains(cover, colour) if args.colour == "match" else np.ones((len(rooms), 3))
    for l, g in zip(rooms, gains):
        if np.abs(g - 1).max() > 0.005:
            print(f"{l}: colour × {g[0]:.2f} {g[1]:.2f} {g[2]:.2f}", flush=True)
    own, transit = owners(rooms, poses, args.partition, (table, around(table, cover)))
    body = out / "spirula" / "scene.body.tmp"
    total, kept, header = 0, {}, None
    with open(body, "wb") as fb:
        for i, (l, r) in enumerate(rooms.items()):
            header, v = read_ply(r["run"] / "spirula" / "scene.ply")
            xyz = final[l](np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float64))
            keep = own(xyz) == i
            v, xyz = v[keep], xyz[keep]
            v["x"], v["y"], v["z"] = xyz.T.astype(np.float32)
            apply_gain(v, gains[i])
            q = Rotation.from_matrix(final[l].R).as_quat()           # (x, y, z, w)
            rot = quat_mul(np.array([q[3], q[0], q[1], q[2]]),
                           np.stack([v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]], axis=1).astype(np.float64))
            v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"] = rot.T.astype(np.float32)
            for k in ("scale_0", "scale_1", "scale_2"):
                v[k] += np.float32(np.log(final[l].s))
            v.tofile(fb)
            total += len(v)
            kept[l] = {"splats": int(keep.size), "kept": int(keep.sum())}
            print(f"{l}: {keep.sum():,} of {keep.size:,} splats kept", flush=True)
            del v, xyz, rot, keep
            progress(100 * (i + 1) / len(rooms))
    lines = [b"element vertex %d\n" % total if l.startswith(b"element vertex") else l
             for l in header.splitlines(keepends=True)]
    with open(out / "spirula" / "scene.ply", "wb") as fo:
        fo.write(b"".join(lines))
        with open(body, "rb") as fb:
            shutil.copyfileobj(fb, fo, 16 << 20)
    body.unlink()

    # start view: the one of the room in the middle
    shutil.copy(rooms[hub]["run"] / "scene.xrSceneTransform.json", out / "scene.xrSceneTransform.json")
    for l, g in zip(rooms, gains):
        room_info[l].update(kept[l], walk_in_out_cameras=transit.get(l, 0), colour=[round(float(x), 3) for x in g])
    result = {"align": args.align, "partition": args.partition, "colour": args.colour, "hub": hub, "splats": total,
              "rooms": room_info, "seams_before": before, "seams": after,
              "seconds": round(time.time() - started)}
    (out / "combine.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    worst = max(after.values(), key=lambda s: s["cm"]) if after else None
    print(f"@@ total {total}", flush=True)
    print(f"{total:,} splats; overlaps match within "
          + (f"{np.median([s['cm'] for s in after.values()]):.1f} cm (median), worst {worst['cm']} cm" if worst else "–"),
          flush=True)


if __name__ == "__main__":
    main()
