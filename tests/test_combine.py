"""Putting rooms together (scripts/combine.py) on synthetic rooms with a known error."""
import json
import subprocess
import sys

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from conftest import ROOT, RUNS, BASE
import synth

COMBINE = ROOT / "scripts" / "combine.py"
YAW_A, YAW_B = 1.0, -0.8                  # degrees: how far each reconstruction is turned
DRIFT_B = 0.03                            # metres: the Frame's tracking of room B is off by this much


def two_rooms(drift_b=DRIFT_B, b_far=False):
    a = synth.make_room(RUNS, "A", "A", error=(YAW_A, (0.03, 0.0, -0.02)))
    b = synth.make_room(RUNS, "B", "B", error=(YAW_B, (-0.02, 0.01, 0.03)),
                        drift=(20.0 if b_far else drift_b, 0.0, 0.0))
    return a, b


def combine(*args, align="images", partition="view"):
    out = BASE / f"out-{align}-{partition}"
    r = subprocess.run([sys.executable, str(COMBINE), str(out), *[f"{p}={p.name}" for p in args],
                        "--align", align, "--partition", partition], capture_output=True, text=True)
    return out, r


def result(out):
    return json.loads((out / "combine.json").read_text())


def read_ply(path):
    with open(path, "rb") as f:
        header = b""
        while not header.endswith(b"end_header\n"):
            header += f.readline()
        props = [l.split()[-1].decode() for l in header.splitlines() if l.startswith(b"property")]
        n = int(next(l for l in header.splitlines() if l.startswith(b"element vertex")).split()[-1])
        return np.fromfile(f, dtype=np.dtype([(p, "<f4") for p in props]), count=n)


@pytest.mark.parametrize("align,lo,hi", [
    ("frame", 5.0, 12.0),                 # as delivered: both reconstructions' errors add up
    ("images", 2.0, 4.0),                 # rooms fitted, but room B's tracking drift (3 cm) is left
    ("shift", 0.0, 0.5),                  # shifting the rooms over the door removes the drift
    ("rigid", 0.0, 0.5),
])
def test_corrections_take_out_the_known_error(align, lo, hi):
    out, r = combine(*two_rooms(), align=align)
    assert r.returncode == 0, r.stderr
    seam = result(out)["seams"]["A|B"]["cm"]
    assert lo <= seam <= hi, f"{align}: overlap off by {seam} cm"


def test_every_room_is_fitted_to_the_frames_tracking():
    out, r = combine(*two_rooms(drift_b=0.0))
    rooms = result(out)["rooms"]
    for room in rooms.values():
        assert room["tracking_cm"] > 3              # off before: turned and shifted
        assert room["fitted_cm"] < 0.1              # fitted over all images: exact
    # the splats are turned back as well (they start without rotation in the synthetic rooms)
    v = read_ply(out / "spirula" / "scene.ply")
    q = np.stack([v["rot_1"], v["rot_2"], v["rot_3"], v["rot_0"]], 1)
    yaws = np.abs(Rotation.from_quat(q).as_euler("yxz", degrees=True)[:, 0])
    assert np.isclose(sorted(set(np.round(yaws, 1))), [abs(YAW_B), YAW_A], atol=0.05).all()


def test_all_splats_are_written_once():
    out, r = combine(*two_rooms())
    info = result(out)
    v = read_ply(out / "spirula" / "scene.ply")
    assert len(v) == info["splats"] == sum(room["kept"] for room in info["rooms"].values())
    assert (out / "scene.xrSceneTransform.json").exists()


def test_borders_run_through_the_door():
    """Room B starts its recording in room A (walking in). With borders 'through doors' that stretch
    claims nothing, so room A comes completely from room A's own recording."""
    def colours_in_room_a(partition):
        out, r = combine(*two_rooms(drift_b=0.0), partition=partition)
        assert r.returncode == 0, r.stderr
        v = read_ply(out / "spirula" / "scene.ply")
        inside_a = (v["x"] > 0.2) & (v["x"] < 3.6)
        blue = v["f_dc_2"] > v["f_dc_0"]               # room B's splats are blue, room A's red
        return int((inside_a & blue).sum()), result(out)["rooms"]["B"]["walk_in_out_cameras"]
    from_b_view, walk_in = colours_in_room_a("view")
    from_b_nearest, _ = colours_in_room_a("nearest")
    assert walk_in > 10                                 # recognised as walking in
    assert from_b_view < 50                             # practically nothing of room B inside room A
    # nearest camera: room B's walk-in cuts into room A's wall
    assert from_b_nearest > 50 and from_b_nearest > 10 * max(from_b_view, 1)


def test_rooms_from_different_sessions_are_refused():
    out, r = combine(*two_rooms(b_far=True))
    assert r.returncode != 0
    assert "@@ error not_connected" in r.stdout
