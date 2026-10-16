"""Stand a mesh upright: our scenes have Y pointing down (like the Steam Frame's tracking). 3D programs
expect Z up for PLY and STL (Blender, slicers for 3D printing) and Y up for glTF/GLB (by its standard) and
OBJ (by custom) – Blender turns those two into Z up itself on import. Without this a room lies on its side
or upside down. (Checked by importing each format in Blender 5.2.)

Usage: python3 upright.py [--floor Y] <file> [<file> ...]   (.ply .obj .stl .glb .gltf; changed in place)

  PLY, STL:      positions and normals turned so that up (-Y) becomes +Z: (x, y, z) -> (x, z, -y)
  OBJ:           turned 180° about X, up (-Y) becomes +Y: (x, y, z) -> (x, -y, -z)
  GLB/glTF:      the scene's root nodes go under one new node turned 180° about X: (x, y, z) -> (x, -y, -z)
                 – nothing else in the file is touched (textures, materials, accessors stay as they are)
  --floor Y      where the floor is in the scene (Y down): it is moved to height 0, so the room stands on
                 the ground plane (the scene's origin is where the Frame was at the start, at eye height)
  --floor-from S the same, found in the scene's splats S (spirula/scene.ply) as for the floor plan
"""
import json
import struct
import sys
from pathlib import Path

import numpy as np

Z_UP = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)       # proper rotation, det +1
Y_UP_QUAT = [1.0, 0.0, 0.0, 0.0]                                             # x, y, z, w: 180° about X
LIFT = 0.0                                                                    # the floor's height before (Y down)


def _replace(path: Path, data: bytes):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def ply(path: Path):
    raw = path.read_bytes()
    end = raw.index(b"end_header\n") + len(b"end_header\n")
    header = raw[:end].decode("ascii", "replace").splitlines()
    if "format binary_little_endian 1.0" not in header:
        sys.exit(f"{path}: only binary little-endian PLY is supported")
    sizes = {"float": "<f4", "double": "<f8", "uchar": "u1", "char": "i1", "ushort": "<u2", "short": "<i2",
             "uint": "<u4", "int": "<i4"}
    props, count, first = [], 0, True
    for line in header:
        parts = line.split()
        if parts[:2] == ["element", "vertex"]:
            count = int(parts[2])
        elif parts[:1] == ["element"]:
            first = False                                  # properties after this belong to faces etc.
        elif parts[:1] == ["property"] and first and count:
            if parts[1] == "list":
                sys.exit(f"{path}: list properties on vertices are not supported")
            props.append((parts[2], sizes[parts[1]]))
    dtype = np.dtype(props)
    v = np.frombuffer(raw, dtype=dtype, count=count, offset=end).copy()
    for a, b, c in (("x", "y", "z"), ("nx", "ny", "nz")):
        if a in dtype.names:
            xyz = np.stack([v[a], v[b], v[c]], 1).astype(np.float64) @ Z_UP.T
            if a == "x":
                xyz[:, 2] += LIFT
            v[a], v[b], v[c] = xyz.T
    _replace(path, raw[:end] + v.tobytes() + raw[end + v.nbytes:])


def obj(path: Path):
    out = []
    with open(path, "rb") as f:
        for line in f:
            if line.startswith((b"v ", b"vn ")):
                tag, rest = line.split(None, 1)
                nums = rest.split()
                x, y, z = (float(n) for n in nums[:3])
                x, y, z = x, -y, -z                        # Y up (180° about X)
                y += LIFT if tag == b"v" else 0.0
                line = b"%s %.7g %.7g %.7g%s\n" % (tag, x, y, z, (b" " + b" ".join(nums[3:])) if nums[3:] else b"")
            out.append(line)
    _replace(path, b"".join(out))


def stl(path: Path):
    raw = path.read_bytes()
    if raw[:5] == b"solid" and b"facet" in raw[:512]:
        sys.exit(f"{path}: only binary STL is supported")
    n, = struct.unpack_from("<I", raw, 80)
    tri = np.frombuffer(raw, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")]),
                        count=n, offset=84).copy()
    tri["n"] = tri["n"] @ Z_UP.T
    tri["v"] = tri["v"] @ Z_UP.T
    tri["v"][..., 2] += LIFT
    _replace(path, raw[:84] + tri.tobytes())


def _wrap(gltf: dict):
    for scene in gltf.get("scenes", []):
        root = {"name": "upright (Y up)", "rotation": Y_UP_QUAT, "translation": [0.0, LIFT, 0.0],
                "children": scene.get("nodes", [])}
        gltf.setdefault("nodes", []).append(root)
        scene["nodes"] = [len(gltf["nodes"]) - 1]


def glb(path: Path):
    raw = path.read_bytes()
    magic, version, _ = struct.unpack_from("<4sII", raw)
    if magic != b"glTF" or version != 2:
        sys.exit(f"{path}: not a glTF 2 binary")
    jlen, jtype = struct.unpack_from("<I4s", raw, 12)
    if jtype != b"JSON":
        sys.exit(f"{path}: first chunk is not JSON")
    gltf = json.loads(raw[20:20 + jlen])
    _wrap(gltf)
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)                         # chunks are 4-byte aligned, JSON padded with spaces
    rest = raw[20 + jlen:]
    total = 12 + 8 + len(js) + len(rest)
    _replace(path, struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js + rest)


def gltf(path: Path):
    data = json.loads(path.read_text())
    _wrap(data)
    _replace(path, json.dumps(data).encode())


def main():
    global LIFT
    args = sys.argv[1:]
    if args[:1] == ["--floor"]:
        LIFT, args = float(args[1]), args[2:]
    elif args[:1] == ["--floor-from"]:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import floorplan
        xyz, _ = floorplan.load_ply(Path(args[1]), np.random.default_rng(0))
        LIFT, args = (float(floorplan.heights(xyz[:, 1])[0]) if len(xyz) else 0.0), args[2:]
        print(f"floor: {LIFT:.2f}")
    for arg in args:
        p = Path(arg)
        kind = p.suffix.lower().lstrip(".")
        fn = {"ply": ply, "obj": obj, "stl": stl, "glb": glb, "gltf": gltf}.get(kind)
        if fn is None:
            sys.exit(f"{p}: unknown mesh format")
        fn(p)
        print(f"upright: {p.name}")


if __name__ == "__main__":
    main()
