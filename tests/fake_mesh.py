"""Writers for the fake `spirula mesh` (tests/conftest.py) and readers for the tests: a tiny room as
Spirula would mesh it – in the scene's own frame, Y down: the floor at y = +1.6, the ceiling at y = -0.9,
both 4 x 4 m around x, z = 0. Each file holds two quads (four triangles)."""
import json
import struct

import numpy as np

FLOOR_Y, CEILING_Y = 1.6, -0.9
V = np.array([[x, y, z] for y in (FLOOR_Y, CEILING_Y) for x, z in ((-2, -2), (2, -2), (2, 2), (-2, 2))], np.float32)
F = np.array([[0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6]], np.int32)
N = np.array([[0, -1, 0]] * 4 + [[0, 1, 0]] * 4, np.float32)          # floor faces up (-Y), ceiling down


def write(path, fmt, colour):
    if fmt == "ply":
        props = "property float x\nproperty float y\nproperty float z\nproperty float nx\nproperty float ny\nproperty float nz\n"
        props += "property uchar red\nproperty uchar green\nproperty uchar blue\n" if colour == "vertex" else ""
        head = f"ply\nformat binary_little_endian 1.0\nelement vertex {len(V)}\n{props}element face {len(F)}\nproperty list uchar int vertex_indices\nend_header\n"
        body = b""
        for v, n in zip(V, N):
            body += struct.pack("<6f", *v, *n) + (bytes([200, 150, 100]) if colour == "vertex" else b"")
        for f in F:
            body += struct.pack("<B3i", 3, *f)
        path.write_bytes(head.encode() + body)
    elif fmt == "obj":
        lines = ["# fake"] + [f"v {x} {y} {z}" for x, y, z in V] + [f"vn {x} {y} {z}" for x, y, z in N]
        lines += [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in F]
        path.write_text("\n".join(lines) + "\n")
    elif fmt == "stl":
        body = b"".join(struct.pack("<12fH", *N[f[0]], *V[f[0]], *V[f[1]], *V[f[2]], 0) for f in F)
        path.write_bytes(b"\0" * 80 + struct.pack("<I", len(F)) + body)
    elif fmt == "glb":
        buf = V.tobytes() + F.astype(np.uint32).tobytes()
        gltf = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "room"}],
                "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
                "buffers": [{"byteLength": len(buf)}],
                "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": V.nbytes},
                                {"buffer": 0, "byteOffset": V.nbytes, "byteLength": F.size * 4}],
                "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(V), "type": "VEC3",
                               "min": V.min(0).tolist(), "max": V.max(0).tolist()},
                              {"bufferView": 1, "componentType": 5125, "count": F.size, "type": "SCALAR"}]}
        js = json.dumps(gltf).encode()
        js += b" " * (-len(js) % 4)
        buf += b"\0" * (-len(buf) % 4)
        path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(buf))
                         + struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(buf), b"BIN\0") + buf)


def read_points(path):
    """Vertex positions as a 3D program sees them (for GLB: with the node transforms applied)."""
    fmt = path.suffix[1:]
    raw = path.read_bytes()
    if fmt == "ply":
        end = raw.index(b"end_header\n") + len(b"end_header\n")
        colour = b"property uchar red" in raw[:end]
        dt = np.dtype([("p", "<f4", 3), ("n", "<f4", 3)] + ([("c", "u1", 3)] if colour else []))
        return np.frombuffer(raw, dt, count=len(V), offset=end)["p"].astype(float)
    if fmt == "obj":
        return np.array([[float(x) for x in l.split()[1:4]] for l in raw.decode().splitlines() if l.startswith("v ")])
    if fmt == "stl":
        n, = struct.unpack_from("<I", raw, 80)
        t = np.frombuffer(raw, np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]), count=n, offset=84)
        return t["v"].reshape(-1, 3).astype(float)
    if fmt == "glb":
        jl, = struct.unpack_from("<I", raw, 12)
        gltf = json.loads(raw[20:20 + jl])
        bin_ = raw[20 + jl + 8:]
        pts = np.frombuffer(bin_, np.float32, count=len(V) * 3).reshape(-1, 3).astype(float)

        def transform(i, p):
            node = gltf["nodes"][i]
            if "rotation" in node:
                x, y, z, w = node["rotation"]
                R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                              [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                              [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
                p = p @ R.T
            return p + np.array(node.get("translation", [0, 0, 0]))
        out = []
        for root in gltf["scenes"][gltf.get("scene", 0)]["nodes"]:
            def walk(i, chain):
                node = gltf["nodes"][i]
                if "mesh" in node:
                    p = pts
                    for j in reversed(chain + [i]):
                        p = transform(j, p)
                    out.append(p)
                for c in node.get("children", []):
                    walk(c, chain + [i])
            walk(root, [])
        return np.vstack(out)
