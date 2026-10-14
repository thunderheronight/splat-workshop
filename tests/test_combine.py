"""Putting rooms together (scripts/combine.py, scripts/combine.sh) on synthetic rooms with a known error.

The expectations come from how the synthetic rooms were built (tests/synth.py), not from the code:
each reconstruction is turned, shifted and scaled by a known amount, the Frame's tracking has 5 mm
noise and optionally a drift or a turn of its own."""
import json
import os
import subprocess
import sys

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from conftest import BASE, ROOT, RUNS, tools_called
import synth

COMBINE = ROOT / "scripts" / "combine.py"
DRIFT_B = 0.03                            # metres: the Frame's tracking of room B is off by this much


def two_rooms(drift_b=DRIFT_B, turn_b=0.0, b_far=False, walk_in_offset=0.3, scale_a=1.0, markers_b=()):
    a = synth.make_room(RUNS, "A", "A", error=(1.0, (0.03, 0.0, -0.02)), scale=scale_a,
                        markers=[((0, 1, 0), (1.0, 0.5, 0.3)), ((0, 1, 1), (1.0, 0.5, 2.7))])
    b = synth.make_room(RUNS, "B", "B", error=(-0.8, (-0.02, 0.01, 0.03)), turn=turn_b,
                        drift=(20.0 if b_far else drift_b, 0.0, 0.0), walk_in_offset=walk_in_offset,
                        markers=markers_b)
    return a, b


def combine(*runs, align="images", partition="view"):
    out = BASE / f"out-{align}-{partition}"
    r = subprocess.run([sys.executable, str(COMBINE), str(out), *[f"{p}={p.name}" for p in runs],
                        "--align", align, "--partition", partition], capture_output=True, text=True)
    assert r.returncode == 0 or "@@ error" in r.stdout, r.stderr
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


def blob(v, rgb):
    """Centre of the marker splats of one colour (markers are pure colours, rooms are not)."""
    c = 0.5 + 0.28209479177387814 * np.stack([v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], 1)
    m = np.all(np.abs(c - np.array(rgb)) < 0.02, axis=1)
    assert m.sum() >= 300, f"marker {rgb} not found"
    return np.stack([v["x"], v["y"], v["z"]], 1)[m].mean(0)


@pytest.mark.parametrize("align,lo,hi", [
    ("frame", 5.0, 12.0),                 # as delivered: both reconstructions' errors add up
    ("images", 2.0, 4.0),                 # rooms fitted, but room B's tracking drift (3 cm) is left
    ("shift", 0.0, 1.0),                  # shifting the rooms over the door removes the drift
    ("rigid", 0.0, 1.0),
])
def test_corrections_take_out_the_known_error(align, lo, hi):
    out, r = combine(*two_rooms(), align=align)
    seam = result(out)["seams"]["A|B"]["cm"]
    assert lo <= seam <= hi, f"{align}: overlap off by {seam} cm"


def test_every_room_is_fitted_to_the_frames_tracking():
    out, r = combine(*two_rooms(drift_b=0.0, scale_a=1.03))
    for room in result(out)["rooms"].values():
        assert room["tracking_cm"] > 3              # off before: turned, shifted (and room A 3 % too big)
        assert room["fitted_cm"] < 1.5              # fitted over all images: down to the 5 mm tracking noise


def test_splats_get_the_rotation_and_size_they_have_in_reality():
    """Room A's reconstruction is 3 % too big and turned by 1°; afterwards every splat must have its
    real-world orientation (20° about x) and size (2 cm) again – with the right sign."""
    out, r = combine(*two_rooms(drift_b=0.0, scale_a=1.03))
    v = read_ply(out / "spirula" / "scene.ply")
    q = np.stack([v["rot_1"], v["rot_2"], v["rot_3"], v["rot_0"]], 1).astype(np.float64)
    off = (Rotation.from_quat(q) * synth.SPLAT_TURN.inv()).magnitude()
    assert np.degrees(off).max() < 0.3, "splats are turned the wrong way"
    size = np.exp(np.stack([v["scale_0"], v["scale_1"], v["scale_2"]], 1).astype(np.float64))
    assert np.allclose(size, synth.SPLAT_SIZE, rtol=0.01), "splats have the wrong size"
    # and the room itself has its real size: the two markers in room A are 2.4 m apart
    assert abs(np.linalg.norm(blob(v, (0, 1, 1)) - blob(v, (0, 1, 0))) - 2.4) < 0.02


def test_shift_and_turn_also_takes_out_a_turned_tracking():
    """Room B's tracking is turned by 2° about the room's middle: shifting (C) cannot repair that,
    shifting and turning (D) can reduce it. Measured independently of the alignment: the distances from
    the markers in room A to a marker in the far corner of room B (rigid motions keep distances).
    Known limit: the turn can only be read from the overlap at the door, so D does not take it out
    completely (here about 2 cm are left of about 6 cm)."""
    far_b = ((1, 0, 1), (7.5, 0.5, 2.7))
    truth = [np.linalg.norm(np.array(far_b[1]) - np.array(p)) for p in ((1.0, 0.5, 0.3), (1.0, 0.5, 2.7))]

    def error(align):
        out, r = combine(*two_rooms(drift_b=0.0, turn_b=2.0, markers_b=[far_b]), align=align)
        v = read_ply(out / "spirula" / "scene.ply")
        got = [np.linalg.norm(blob(v, far_b[0]) - blob(v, c)) for c in ((0, 1, 0), (0, 1, 1))]
        return max(abs(g - t) for g, t in zip(got, truth)) * 100

    shift, rigid = error("shift"), error("rigid")
    assert shift > 3, f"the test needs a turn that shifting cannot repair ({shift:.1f} cm)"
    assert rigid < shift / 2, f"shift {shift:.1f} cm, shift and turn {rigid:.1f} cm"


def test_every_splat_is_written_once():
    a, b = two_rooms()
    out, r = combine(a, b)
    info, v = result(out), read_ply(out / "spirula" / "scene.ply")
    assert len(v) == info["splats"]
    for run in (a, b):
        made = json.loads((run / "synth.json").read_text())
        room = info["rooms"][run.name]
        assert room["splats"] == made["splats"]            # read completely
        # a room keeps its own splats (the shared wall may go either way); of what it saw of the neighbour
        # through the door it keeps at most the part right behind the door, where its own cameras were closer
        assert 0.95 * (made["own"] - made["shared_wall"]) <= room["kept"] <= made["own"] + made["seen_through_door"]
    # each place once: together the rooms keep no more than they are made of
    total_own = sum(json.loads((run / "synth.json").read_text())["own"] for run in (a, b))
    assert len(v) <= total_own


@pytest.mark.parametrize("offset", [0.1, 0.3])
def test_borders_run_through_the_door(offset):
    """Room B starts its recording in room A (walking in). With borders 'through doors' that stretch
    claims nothing, so room A comes from room A's own recording; 'nearest camera' cuts into room A."""
    def from_b_in_room_a(partition):
        out, r = combine(*two_rooms(drift_b=0.0, walk_in_offset=offset), partition=partition)
        v = read_ply(out / "spirula" / "scene.ply")
        inside_a = (v["x"] > 0.2) & (v["x"] < 3.6)
        c = 0.5 + 0.28209479177387814 * np.stack([v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], 1)
        from_b = np.all(np.abs(c - (0.3, 0.3, 0.8)) < 0.02, axis=1)        # room B's own colour
        return int((inside_a & from_b).sum()), result(out)["rooms"]["B"]["walk_in_out_cameras"]
    view, walk_in = from_b_in_room_a("view")
    assert walk_in > 10                                    # recognised as walking in
    assert view < 50                                       # practically nothing of room B inside room A
    if offset >= 0.3:                                      # close to the round, room A's cameras win anyway
        nearest, _ = from_b_in_room_a("nearest")
        assert nearest > 10 * max(view, 5)


@pytest.mark.xfail(strict=True, reason="known limit: walking in is only recognised up to 0.4 m from the other "
                                       "recording's round; further away the walk-in stretch claims room A's wall")
def test_borders_with_a_walk_in_far_from_the_other_round():
    out, r = combine(*two_rooms(drift_b=0.0, walk_in_offset=0.6))
    assert result(out)["rooms"]["B"]["walk_in_out_cameras"] > 10


def test_rooms_from_different_sessions_are_refused():
    out, r = combine(*two_rooms(b_far=True))
    assert r.returncode != 0
    assert "@@ error not_connected" in r.stdout


def run_combine_sh(env, *runs):
    return subprocess.run([str(ROOT / "scripts" / "combine.sh"), "Tour", "Tour", *[f"{p}={p.name}" for p in runs]],
                          env={**os.environ, "NO_UPLOAD": "1", **env}, capture_output=True, text=True)


def test_combine_sh_reduces_for_the_frame_and_can_skip_the_full_version():
    a, b = two_rooms()
    r = run_combine_sh({"FRAME_SPLATS": "10000", "FULL": "0"}, a, b)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    calls = [c[1:] for c in tools_called() if c[0] == "splat-transform"]
    tmp = str(RUNS / ".building" / "Tour")
    assert calls[0] == ["-w", f"{tmp}/spirula/scene.ply", "--decimate", "10000", f"{tmp}/frame.ply"]
    assert calls[1] == ["-w", f"{tmp}/frame.ply", f"{tmp}/scene.sog"]
    assert len(calls) == 2                                 # no full version
    tour = RUNS / "Tour"
    assert (tour / "scene.sog").exists() and not (tour / "full").exists() and not (tour / "frame.ply").exists()
    assert not (RUNS / ".building").exists()


def test_combine_sh_full_version_without_reducing_a_small_tour():
    a, b = two_rooms()
    r = run_combine_sh({"FRAME_SPLATS": "3500000"}, a, b)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    calls = [c[1:] for c in tools_called() if c[0] == "splat-transform"]
    assert not any("--decimate" in c for c in calls)        # fewer splats than the Frame version allows
    assert (RUNS / "Tour" / "full" / "tour-full.sog").exists()
    assert (RUNS / "Tour" / "full" / "tour-full.sceneinfo.png").exists()
