"""Cleaning up a scene (0.4, scripts/clean.py): ceiling, floor, haze, outside – and as part of its versions.

The synthetic room is built so every kind of splat can be told apart afterwards by its colour: a white
ceiling that the reconstruction left thin, a dark veil under it, a dark cupboard top by the wall, a bird
cage of long thin bars, fat transparent blobs (haze), a floor with a hole, and things beyond the walls.
The expectations come from how it is built, not from the code."""
import json
import subprocess
import sys

import numpy as np
import pytest

from conftest import ROOT, RUNS, server, wait_job
import synth

CLEAN = ROOT / "scripts" / "clean.py"
FLOOR, CEILING = 1.6, -0.9                 # y points down: the room is 2.5 m high
W, D = 4.0, 5.0                            # x and z extent of the room
WALL, FLOORC, CEILC = (0.6, 0.6, 0.6), (0.45, 0.3, 0.2), (0.85, 0.85, 0.82)
VEIL, CUPBOARD, CAGE, HAZE, OUT = (0.12, 0.12, 0.13), (0.2, 0.1, 0.05), (0.3, 0.3, 0.35), (0.5, 0.5, 0.9), (0.9, 0.1, 0.9)
LOGIT = lambda a: float(np.log(a / (1 - a)))


def splats(pts, colour, opacity=0.95, scale=(0.02, 0.02, 0.02)):
    v = np.zeros(len(pts), dtype=np.dtype([(p, "<f4") for p in synth.PROPS]))
    v["x"], v["y"], v["z"] = np.asarray(pts).T
    for k in range(3):
        v[f"f_dc_{k}"] = (colour[k] - 0.5) / 0.28209479177387814
        v[f"scale_{k}"] = np.log(scale[k])
    v["opacity"] = LOGIT(opacity)
    v["rot_0"] = 1.0
    return v


def grid(a0, a1, b0, b1, step):
    a, b = np.meshgrid(np.arange(a0, a1 + 1e-9, step), np.arange(b0, b1 + 1e-9, step), indexing="ij")
    return a.ravel(), b.ravel()


def room(path):
    """The living room as a reconstruction of a white ceiling tends to come out."""
    rng = np.random.default_rng(1)
    parts = []
    ys = np.arange(CEILING, FLOOR + 1e-9, 0.04)
    for x in (0.0, W):                                         # walls
        z, y = grid(0, D, CEILING, FLOOR, 0.04)
        parts.append(splats(np.stack([np.full_like(z, x), y, z], 1), WALL))
    for z in (0.0, D):
        x, y = grid(0, W, CEILING, FLOOR, 0.04)
        parts.append(splats(np.stack([x, y, np.full_like(x, z)], 1), WALL))
    x, z = grid(0, W, 0, D, 0.03)                              # floor with a hole of 1 x 1 m
    hole = (x > 1.5) & (x < 2.5) & (z > 2.0) & (z < 3.0)
    parts.append(splats(np.stack([x[~hole], np.full((~hole).sum(), FLOOR), z[~hole]], 1), FLOORC))
    x, z = grid(0, W, 0, D, 0.12)                              # thin ceiling: little, half transparent ...
    parts.append(splats(np.stack([x, np.full_like(x, CEILING), z], 1), CEILC, opacity=0.4))
    x, z = grid(0, W, 0, D, 0.03)                              # ... but solid where it meets the walls
    edge = (x < 0.25) | (x > W - 0.25) | (z < 0.25) | (z > D - 0.25)
    parts.append(splats(np.stack([x[edge], np.full(edge.sum(), CEILING), z[edge]], 1), CEILC))
    n = 4000                                                   # the veil: dark, thin, 10–30 cm under it
    veil = np.stack([rng.uniform(0.8, W - 0.8, n), CEILING + rng.uniform(0.1, 0.3, n), rng.uniform(0.8, D - 0.8, n)], 1)
    parts.append(splats(veil, VEIL, opacity=0.2, scale=(0.03, 0.03, 0.03)))
    x, z = grid(0.05, 0.4, 1.0, 2.0, 0.03)                     # a dark cupboard top at the wall, 2.2 m high
    parts.append(splats(np.stack([x, np.full_like(x, FLOOR - 2.2), z], 1), CUPBOARD))
    bars = []                                                  # a bird cage: long thin bars, faint
    for bx in np.arange(1.8, 2.2, 0.02):
        for by in np.arange(FLOOR - 2.0, FLOOR - 1.2, 0.08):
            bars.append((bx, by, 2.5))
    parts.append(splats(bars, CAGE, opacity=0.15, scale=(0.001, 0.06, 0.001)))
    n = 300                                                    # haze: fat, transparent blobs in the room
    blobs = np.stack([rng.uniform(1, 3, n), rng.uniform(FLOOR - 1.6, FLOOR - 0.6, n), rng.uniform(1, 4, n)], 1)
    parts.append(splats(blobs, HAZE, opacity=0.06, scale=(0.06, 0.06, 0.06)))
    n = 500                                                    # beyond the walls, seen through the window
    out = np.stack([rng.uniform(-3, -1.5, n), rng.uniform(FLOOR - 2, FLOOR, n), rng.uniform(0, D, n)], 1)
    parts.append(splats(out, OUT))
    v = np.concatenate(parts)
    header = "ply\nformat binary_little_endian 1.0\nelement vertex %d\n%send_header\n" % (
        len(v), "".join(f"property float {p}\n" for p in synth.PROPS))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(header.encode())
        v.tofile(f)
    return v


def read(path):
    with open(path, "rb") as f:
        header = b""
        while not header.endswith(b"end_header\n"):
            header += f.readline()
        props = [l.split()[-1].decode() for l in header.splitlines() if l.startswith(b"property")]
        n = int(next(l for l in header.splitlines() if l.startswith(b"element vertex")).split()[-1])
        return np.fromfile(f, dtype=np.dtype([(p, "<f4") for p in props]), count=n)


def colour_of(v):
    return 0.5 + 0.28209479177387814 * np.stack([v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], 1)


def count(v, rgb):
    return int(np.all(np.abs(colour_of(v) - rgb) < 0.01, axis=1).sum())


def clean(tmp_path, *args):
    src, dst = tmp_path / "in.ply", tmp_path / "out.ply"
    before = room(src)
    r = subprocess.run([sys.executable, str(CLEAN), str(src), str(dst), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return before, read(dst), r.stdout


def test_ceiling_veil_out_ceiling_closed(tmp_path):
    before, after, out = clean(tmp_path, "--ceiling", "0.5")
    assert "Ceiling 2.5" in out                                    # found where walls and ceiling meet
    assert count(after, VEIL) == 0                                 # the veil is gone
    assert count(after, CUPBOARD) == count(before, CUPBOARD)       # the dark cupboard top by the wall stays
    assert count(after, CAGE) == count(before, CAGE)               # and the cage, far below the ceiling
    flat = after[np.isclose(after["y"], CEILING, atol=0.03) & (after["opacity"] > 3)]
    # closed: a solid layer over the whole room, in the ceiling's own colour
    assert len(flat) > (W / 0.02) * (D / 0.02) * 0.9
    assert np.allclose(np.median(colour_of(flat), 0), CEILC, atol=0.05)
    for k in ("x", "y", "z", "f_dc_0", "opacity", "scale_0"):
        assert np.isfinite(after[k]).all()


def test_floor_holes_filled_in_the_floors_colour(tmp_path):
    before, after, out = clean(tmp_path, "--floor", "0.5")
    added = after[len(before):]
    inside = (added["x"] > 1.6) & (added["x"] < 2.4) & (added["z"] > 2.1) & (added["z"] < 2.9)
    assert inside.sum() > 0.8 * (0.8 / 0.02) ** 2                  # the hole is closed ...
    assert len(added) < 1.3 * (1 / 0.02) ** 2                      # ... and little more than the hole
    assert np.allclose(np.median(colour_of(added), 0), FLOORC, atol=0.05)
    assert np.allclose(added["y"], FLOOR, atol=0.01)


def test_haze_goes_but_not_thin_bars(tmp_path):
    before, after, _ = clean(tmp_path, "--haze", "1")
    assert count(after, HAZE) == 0
    assert count(after, CAGE) == count(before, CAGE)               # long and thin is no haze
    assert count(after, VEIL) == 0                                 # at full strength the veil is haze, too
    assert count(after, WALL) == count(before, WALL) and count(after, CUPBOARD) == count(before, CUPBOARD)


def test_outside_goes(tmp_path):
    before, after, _ = clean(tmp_path, "--outside", "1")
    assert count(after, OUT) == 0 and count(after, WALL) == count(before, WALL)


def test_nothing_switched_on_changes_nothing(tmp_path):
    before, after, _ = clean(tmp_path)
    assert len(after) == len(before) and np.array_equal(after["x"], before["x"])


@pytest.mark.parametrize("args", [["--haze", "2"], ["--ceiling", "-0.1"]])
def test_strengths_from_0_to_1(tmp_path, args):
    src = tmp_path / "in.ply"
    room(src)
    r = subprocess.run([sys.executable, str(CLEAN), str(src), str(tmp_path / "o.ply"), *args], capture_output=True, text=True)
    assert r.returncode != 0 and "between 0 and 1" in r.stderr


# ---------------------------------------------------------------- as part of the versions

def scene(slug="Living"):
    run = RUNS / slug
    (run / "spirula").mkdir(parents=True, exist_ok=True)
    room(run / "spirula" / "scene.ply")
    (run / "scene.sog").write_bytes(b"sog")
    (run / "scene.xrSceneTransform.json").write_text(json.dumps({"worldScale": 1.0}))
    server.write_meta(slug, name="Living room")
    return run


def test_a_cleaned_version(api):
    run = scene()
    total = len(read(run / "spirula" / "scene.ply"))
    status, r = api.post("/api/versions", {"slug": "Living", "sizes": "all", "clean": "ceiling=0.5,haze=0.3"})
    assert status == 200, r
    js = wait_job()
    assert js["result"] == "ok", js["tail"]
    v = {x["key"]: x for x in server.versions_of("Living")}
    assert list(v) == ["all-c50h30"]
    x = v["all-c50h30"]
    assert x["clean"] == {"ceiling": 0.5, "haze": 0.3} and x["label"] == "Living room · all splats · ceiling 50 % · haze 30 %"
    assert x["purpose"] == "pc" and x["ply"]                                 # Spirula Studio can open it
    info = json.loads((run / "versions" / "all-c50h30" / "version.json").read_text())
    assert info["splats"] != total                                       # counted after cleaning
    assert not (run / "versions" / "all-c50h30" / "clean.ply").exists()
    assert len(read(run / "versions" / "all-c50h30" / "spirula" / "scene.ply")) == info["splats"]
    assert len(read(run / "spirula" / "scene.ply")) == total               # the scene itself is untouched


def test_a_cleaned_and_a_plain_version_side_by_side(api):
    scene()
    api.post("/api/versions", {"slug": "Living", "sizes": "all"})
    wait_job()
    api.post("/api/versions", {"slug": "Living", "sizes": "all", "clean": "floor=1"})
    wait_job()
    assert sorted(x["key"] for x in server.versions_of("Living")) == ["all", "all-f100"]


@pytest.mark.parametrize("clean,code", [("roof=0.5", "err.bad_value"), ("haze=2", "err.range"), ("haze=x", "err.bad_value"),
                                        ("haze=nan", "err.bad_value"), ({"haze": 1}, "err.bad_request")])
def test_odd_clean_ups_are_refused(api, clean, code):
    scene()
    status, r = api.post("/api/versions", {"slug": "Living", "sizes": "all", "clean": clean})
    assert status == 400 and r["error"]["code"] == code


def test_cleaned_and_plain_in_one_go(api):
    """For the Frame, with and without cleaning up, to compare: two versions, named by their strengths."""
    run = scene()
    status, r = api.post("/api/versions", {"slug": "Living", "sizes": "20000", "clean": "haze=0.4", "also_plain": True})
    assert status == 200, r
    assert wait_job()["result"] == "ok"
    v = {x["key"]: x for x in server.versions_of("Living")}
    assert set(v) == {"0.02m", "0.02m-h40"}
    assert v["0.02m-h40"]["label"] == "Living room · 0.02 M · haze 40 %" and v["0.02m"]["label"] == "Living room · 0.02 M"
    assert all(x["purpose"] == "frame" and not x["ply"] for x in v.values())   # Frame versions keep no ply
    assert not (run / "versions" / "0.02m-h40" / "spirula").exists()


def test_a_version_opens_in_spirula(api, monkeypatch):
    run = scene()
    api.post("/api/versions", {"slug": "Living", "sizes": "all", "clean": "ceiling=0.5"})
    wait_job()
    api.post("/api/versions", {"slug": "Living", "sizes": "20000"})
    wait_job()
    opened = []
    monkeypatch.setattr(server.subprocess, "Popen", lambda cmd, **kw: opened.append(cmd))
    assert api.post("/api/open", {"slug": "Living", "version": "all-c50", "what": "spirula"})[0] == 200
    assert opened[-1][1] == str(run / "versions" / "all-c50" / "spirula" / "scene.ply")
    status, r = api.post("/api/open", {"slug": "Living", "version": "0.02m", "what": "spirula"})   # no ply
    assert status == 400 and r["error"]["code"] == "err.no_version"
    status, r = api.post("/api/open", {"slug": "Living", "version": "../x", "what": "spirula"})
    assert status == 400 and len(opened) == 1
