"""Synthetic rooms in the shape the Arcturus pipeline leaves them, for tests without real recordings.

Two rooms side by side with a door between them (Y points down like in the pipeline):

    z=3 +----------------+----------------+
        |                |                |
        |   room A       door   room B    |
        |   (x 0..4)     |      (x 4..8)  |
    z=0 +----------------+----------------+
       x=0              x=4              x=8

Every recording walks around its room; room B starts in room A next to the door and walks in
(like a real recording that starts in the hallway). Each room gets what the pipeline writes:
  dataset/main.aiscene            camera poses as tracked by the Frame (the truth, plus `drift`)
  dataset/sparse/0/images.bin     camera poses of the reconstruction
  dataset/sparse/0/points3D.bin   sparse points
  spirula/scene.ply               splats (one per point)
  scene.sog, scene.xrSceneTransform.json, <slug>.ui.json
The reconstruction is off by a known small rotation and shift (`error`), as a real one is before
it is fitted to the Frame's tracking.
"""
import base64
import json
import struct
import zlib
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

FLOOR, CEILING = 1.6, -0.9                 # y (down): floor below the cameras, ceiling above
DOOR = (1.2, 2.0)                          # door between the rooms, z range
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
PROPS = (["x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2"] + [f"f_rest_{i}" for i in range(45)]
         + ["opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"])


def walls(x0, x1, step=0.04):
    """Points on the walls, floor and ceiling of the room x0..x1 (z 0..3), with a door in the wall at x=4."""
    pts = []
    ys = np.arange(CEILING, FLOOR + 1e-9, step)
    for x in (x0, x1):
        for z in np.arange(0, 3 + 1e-9, step):
            if x == 4 and DOOR[0] < z < DOOR[1]:
                continue
            pts += [(x, y, z) for y in ys]
    for z in (0, 3):
        for x in np.arange(x0, x1 + 1e-9, step):
            pts += [(x, y, z) for y in ys]
    for x in np.arange(x0, x1 + 1e-9, step * 2):
        for z in np.arange(0, 3 + 1e-9, step * 2):
            pts += [(x, FLOOR, z), (x, CEILING, z)]
    return np.array(pts)


def path(room, walk_in_offset=0.3):
    """Camera positions over time (y = 0, head height) and the times in seconds."""
    t = np.linspace(0, 2 * np.pi, 140)
    if room == "A":
        # starts at the far wall, passes the door in the middle of the round
        loop = np.stack([2 + 1.3 * np.cos(t + np.pi), np.zeros_like(t), 1.5 + 0.9 * np.sin(t + np.pi)], 1)
        return loop, np.linspace(0, 40, len(loop))
    # starts in room A next to room A's round (like a recording that starts in the hallway), then through
    # the door. walk_in_offset: how far from room A's own path (towards room A's wall) it starts.
    tt = np.linspace(-1.0, 0, 18)
    along_a = np.stack([2 + 1.3 * np.cos(tt), np.zeros_like(tt), 1.5 + 0.9 * np.sin(tt) - walk_in_offset], 1)
    walk_in = np.vstack([along_a, np.stack([np.linspace(3.35, 4.6, 12), np.zeros(12), np.full(12, 1.6)], 1)])
    loop = np.stack([6 + 1.3 * np.cos(t + np.pi), np.zeros_like(t), 1.5 + 0.9 * np.sin(t + np.pi)], 1)
    pos = np.vstack([walk_in, loop])
    return pos, np.linspace(0, 45, len(pos))


def look_at(c, target):
    """cam_from_world rotation for a camera at c looking at target (x right, y down, z forward)."""
    z = target - c
    z[1] = 0
    z /= np.linalg.norm(z)
    y = np.array([0.0, 1.0, 0.0])
    x = np.cross(y, z)
    return np.stack([x, y, z])


def write_images(path_, names, R_list, C_list):
    with open(path_, "wb") as f:
        f.write(struct.pack("<Q", len(names)))
        for i, (name, R, C) in enumerate(zip(names, R_list, C_list)):
            qx, qy, qz, qw = Rotation.from_matrix(R).as_quat()
            t = -R @ C
            f.write(struct.pack("<i7di", i + 1, qw, qx, qy, qz, *t, 1))
            f.write(name.encode() + b"\0")
            f.write(struct.pack("<Q", 0))


def write_points(path_, pts):
    with open(path_, "wb") as f:
        f.write(struct.pack("<Q", len(pts)))
        for i, p in enumerate(pts):
            f.write(struct.pack("<Q3d3BdQ", i + 1, *p, 128, 128, 128, 0.5, 0))


def write_ply(path_, pts, colours, rotation=(1.0, 0.0, 0.0, 0.0), scale=0.02):
    """One splat per point; all with the same rotation (w, x, y, z) and size (metres)."""
    data = np.zeros(len(pts), dtype=np.dtype([(p, "<f4") for p in PROPS]))
    data["x"], data["y"], data["z"] = pts.T
    for k in range(3):
        data[f"f_dc_{k}"] = (colours[:, k] - 0.5) / 0.28209479177387814
    data["opacity"] = 3.0
    for k in range(3):
        data[f"scale_{k}"] = np.log(scale)
    for k in range(4):
        data[f"rot_{k}"] = rotation[k]
    header = "ply\nformat binary_little_endian 1.0\nelement vertex %d\n%send_header\n" % (
        len(pts), "".join(f"property float {p}\n" for p in PROPS))
    with open(path_, "wb") as f:
        f.write(header.encode())
        data.tofile(f)


MARKERS = (((1, 0, 0), (0.0, 0.5, 0.0)), ((0, 1, 0), (2.0, 0.5, 0.0)), ((0, 0, 1), (0.0, 0.5, 2.0)))
SPLAT_TURN = Rotation.from_euler("x", 20, degrees=True)     # how every splat is turned in the real world
SPLAT_SIZE = 0.02                                            # metres


def make_room(runs: Path, slug: str, room: str, error=(1.0, (0.03, 0.0, -0.02)), scale=1.0,
              drift=(0.0, 0.0, 0.0), turn=0.0, noise=0.005, walk_in_offset=0.3,
              name=None, recording=None, markers=(), colour=None, loop=None):
    """Write one synthetic room into runs/<slug>. Returns the run folder.

    The reconstruction differs from the real world by `error` = (yaw in degrees, shift in m) and `scale`.
    The Frame's tracking of this recording is off by `drift` (m) and turned by `turn` (degrees, about the
    room's middle), plus `noise` (m) per pose. markers: True for red/green/blue at the corner, along +x and
    along +z, or a list of ((r, g, b), (x, y, z)) – blobs of 400 splats at real-world positions.
    loop: (x, z, radius x, radius z) – a round of its own in the room instead of the usual one, looking at the
    walls (outwards): several recordings of one room, e.g. one for each half.
    runs/<slug>/synth.json records how many splats are the room's own (of these, how many on the wall it
    shares with the other room – zero thickness, so it belongs to both) and how many were seen through the door."""
    rng = np.random.default_rng(zlib.crc32(slug.encode()))
    run = runs / slug
    sparse = run / "dataset" / "sparse" / "0"
    sparse.mkdir(parents=True, exist_ok=True)
    (run / "spirula").mkdir(exist_ok=True)
    x0, x1 = (0.0, 4.0) if room == "A" else (4.0, 8.0)
    pts = walls(x0, x1)
    # every recording its own colour, so the origin of a splat can be seen after putting rooms together
    colours = np.tile(colour or ((0.8, 0.3, 0.3) if room == "A" else (0.3, 0.3, 0.8)), (len(pts), 1))
    for c, at in (MARKERS if markers is True else markers):
        blob = np.array(at) + rng.normal(0, 0.05, (400, 3))
        pts, colours = np.vstack([pts, blob]), np.vstack([colours, np.tile(c, (400, 1))])
    own = len(pts)
    shared = int((np.abs(pts[:, 0] - 4) < 1e-9).sum())          # the wall between the rooms belongs to both
    # seen through the door: a little of the neighbouring room close to the door
    other = walls(4.0, 8.0) if room == "A" else walls(0.0, 4.0)
    near_door = other[(np.abs(other[:, 0] - 4) < 1.0) & (other[:, 2] > 0.6) & (other[:, 2] < 2.6)]
    pts = np.vstack([pts, near_door])
    colours = np.vstack([colours, np.tile(colours[0], (len(near_door), 1))])

    pos, times = path(room, walk_in_offset)
    centre = np.array([(x0 + x1) / 2, 0.0, 1.5])
    target = lambda c: centre
    if loop:
        lx, lz, rx, rz = loop
        t = np.linspace(0, 2 * np.pi, 140)
        pos = np.stack([lx + rx * np.cos(t), np.zeros_like(t), lz + rz * np.sin(t)], 1)
        times = np.linspace(0, 40, len(pos))
        target = lambda c: 2 * c - np.array([lx, 0.0, lz])          # outwards, at the walls
    yaw, shift = error
    E = Rotation.from_euler("y", yaw, degrees=True)                 # world -> reconstruction
    to_sfm = lambda p: scale * E.apply(p) + np.array(shift)
    T = Rotation.from_euler("y", turn, degrees=True)                # the tracking's own turn
    names, Rs, Cs, frames = [], [], [], []
    for k, (c, t) in enumerate(zip(pos, times)):
        R_world = look_at(c.copy(), target(c))
        image = f"cam0/{k:04d}-{t:.6f}.jpg"
        names.append(image)
        Rs.append(R_world @ E.as_matrix().T)
        Cs.append(to_sfm(c))
        world_from_cam = np.eye(4)
        world_from_cam[:3, :3] = T.as_matrix() @ R_world.T
        world_from_cam[:3, 3] = T.apply(c - centre) + centre + np.array(drift) + rng.normal(0, noise, 3)
        frames.append({"cam": 0, "path": "images/" + image, "worldFromCam": world_from_cam.tolist(),
                       "playbackTimestampSec": float(t)})
    write_images(sparse / "images.bin", names, Rs, Cs)
    write_points(sparse / "points3D.bin", to_sfm(pts))
    q = (E * SPLAT_TURN).as_quat()                                   # (x, y, z, w) in the reconstruction
    write_ply(run / "spirula" / "scene.ply", to_sfm(pts), colours, (q[3], q[0], q[1], q[2]), SPLAT_SIZE * scale)
    (run / "dataset" / "main.aiscene").write_text(json.dumps({"format": "synthetic", "cameras": [], "frames": frames}))
    (run / "scene.sog").write_bytes(b"synthetic")
    (run / "scene.xrSceneTransform.json").write_text(json.dumps({"transform": np.eye(4).tolist(), "worldScale": 1.0}))
    (run / f"{slug.lower()}.gallery.png").write_bytes(PNG)
    (run / "synth.json").write_text(json.dumps({"own": own, "shared_wall": shared, "seen_through_door": len(near_door),
                                                 "splats": len(pts)}))
    (runs / f"{slug}.log").write_text(f"spirula: {len(pts):,} Gaussians in {run}/spirula/scene.ply\n")
    meta = {"name": name or slug, "recording": recording or f"2026-10-12_16-{40 + (room == 'B') * 3:02d}-00_stereo.mp4",
            "quality": "high"}
    (runs / f"{slug}.ui.json").write_text(json.dumps(meta))
    return run
