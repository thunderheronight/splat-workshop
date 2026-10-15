"""Rooms made of several recordings (0.3): putting them together with the rooms of a home.

Several recordings of one room share it: every part comes from the recording that saw it best, and the
recordings of one room are never mistaken for walking in from somewhere else. The expectations come from
how the synthetic recordings were built (tests/synth.py): each has its own colour, so where a splat came
from can be seen in the result – with colour matching off (COLOUR=keep), which would otherwise bring them
closer together."""
import json
import subprocess
import sys

import numpy as np

from conftest import BASE, ROOT, RUNS
import synth

COMBINE = ROOT / "scripts" / "combine.py"
RED, GREEN, HALL = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.3, 0.3, 0.8)


def combine(runs, rooms=None, name="out", colour="keep"):
    out = BASE / name
    cmd = [sys.executable, str(COMBINE), str(out), *[f"{p}={p.name}" for p in runs]]
    if rooms is not None:
        cmd += ["--rooms", json.dumps(rooms)]
    cmd += ["--colour", colour]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return out, r


def splats(out):
    with open(out / "spirula" / "scene.ply", "rb") as f:
        header = b""
        while not header.endswith(b"end_header\n"):
            header += f.readline()
        props = [l.split()[-1].decode() for l in header.splitlines() if l.startswith(b"property")]
        n = int(next(l for l in header.splitlines() if l.startswith(b"element vertex")).split()[-1])
        v = np.fromfile(f, dtype=np.dtype([(p, "<f4") for p in props]), count=n)
    colour = 0.5 + 0.28209479177387814 * np.stack([v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], 1)
    return v, colour


def coloured(colour, rgb):
    return np.all(np.abs(colour - np.array(rgb)) < 0.02, axis=1)


def test_a_room_from_two_recordings_takes_each_part_from_the_one_that_saw_it():
    left = synth.make_room(RUNS, "Left", "A", loop=(1.1, 1.5, 0.6, 0.8), colour=RED)
    right = synth.make_room(RUNS, "Right", "A", loop=(2.9, 1.5, 0.6, 0.8), colour=GREEN)
    out, r = combine([left, right], rooms=["Kitchen", "Kitchen"])
    assert r.returncode == 0, r.stderr
    v, colour = splats(out)
    red, green = coloured(colour, RED), coloured(colour, GREEN)
    left_wall, right_wall = v["x"] < 0.3, (v["x"] > 3.7) & (v["x"] < 4.3)
    assert red[left_wall].mean() > 0.9 and green[right_wall].mean() > 0.9
    # each place once: both recordings saw the whole room, the result holds it once
    made = json.loads((left / "synth.json").read_text())
    assert len(v) <= made["own"] + made["seen_through_door"]
    info = json.loads((out / "combine.json").read_text())
    assert {l: x["room"] for l, x in info["rooms"].items()} == {"Left": "Kitchen", "Right": "Kitchen"}
    assert all(x["walk_in_out_cameras"] == 0 for x in info["rooms"].values())
    assert info["seams"]["Left|Right"]["cm"] < 1.0               # the two recordings match



def thin_out(run, keep, gain=1.0):
    """Change a synthetic recording's splats: keep only some (keep(v) -> mask), colours times `gain`."""
    path = run / "spirula" / "scene.ply"
    raw = path.read_bytes()
    end = raw.index(b"end_header\n") + len(b"end_header\n")
    props = [l.split()[-1].decode() for l in raw[:end].splitlines() if l.startswith(b"property")]
    v = np.frombuffer(raw[end:], dtype=np.dtype([(p, "<f4") for p in props])).copy()
    v = v[keep(v)]
    for k in range(3):
        v[f"f_dc_{k}"] = (gain * (0.5 + 0.28209479177387814 * v[f"f_dc_{k}"]) - 0.5) / 0.28209479177387814
    header = raw[:end].replace(b"element vertex %d" % int(raw[:end].split(b"element vertex ")[1].split()[0]),
                               b"element vertex %d" % len(v))
    path.write_bytes(header + v.tobytes())


def test_a_place_goes_to_a_recording_that_really_has_something_there():
    """The right recording looked at the right wall, but its training left nothing there (behind the sofa,
    too dark, ...): the wall comes from the left recording instead of being a dark hole."""
    left = synth.make_room(RUNS, "Left", "A", loop=(1.1, 1.5, 0.6, 0.8), colour=RED)
    right = synth.make_room(RUNS, "Right", "A", loop=(2.9, 1.5, 0.6, 0.8), colour=GREEN)
    thin_out(right, lambda v: v["x"] < 3.0)
    out, r = combine([left, right], rooms=["Kitchen", "Kitchen"])
    assert r.returncode == 0, r.stderr
    v, colour = splats(out)
    right_wall = (v["x"] > 3.7) & (v["x"] < 4.3)
    assert right_wall.sum() > 1000 and coloured(colour, RED)[right_wall].mean() > 0.9
    assert coloured(colour, GREEN)[v["x"] < 0.3].mean() < 0.1            # the left wall still from the left


def test_recordings_of_one_room_are_brought_to_one_brightness():
    grey = (0.6, 0.6, 0.6)
    left = synth.make_room(RUNS, "Left", "A", loop=(1.1, 1.5, 0.6, 0.8), colour=grey)
    right = synth.make_room(RUNS, "Right", "A", loop=(2.9, 1.5, 0.6, 0.8), colour=grey)
    thin_out(right, lambda v: v["x"] > -1, gain=0.7)                     # its training came out darker
    out, r = combine([left, right], rooms=["Kitchen", "Kitchen"], colour="match")
    assert r.returncode == 0, r.stderr
    v, colour = splats(out)
    lw, rw = colour[v["x"] < 0.3, 0], colour[(v["x"] > 3.7) & (v["x"] < 4.3), 0]
    assert abs(np.median(lw) - np.median(rw)) < 0.01
    assert abs(np.median(lw) - np.sqrt(0.6 * 0.42)) < 0.01               # in between: the room keeps its brightness
    info = json.loads((out / "combine.json").read_text())
    assert info["colour"] == "match" and info["rooms"]["Right"]["colour"][0] > 1.1
    out, r = combine([left, right], rooms=["Kitchen", "Kitchen"], name="kept", colour="keep")
    v, colour = splats(out)
    assert abs(np.median(colour[v["x"] < 0.3, 0]) - 0.6) < 0.01


def test_colour_matching_leaves_alone_what_it_cannot_compare():
    sys.path.insert(0, str(ROOT / "scripts"))
    import combine as C
    cover = np.zeros((10, 2)); cover[:, 0] = 5
    colour = np.full((10, 2, 3), 0.5)
    assert np.all(C.colour_gains(cover, colour) == 1)                    # nothing in common: unchanged
    cover[:] = 5; colour[:, 1] = 0.05                                    # wildly different: limited
    g = C.colour_gains(cover, colour, min_cubes=5)
    assert g.max() <= C.GAIN[1] and g.min() >= C.GAIN[0]

def test_recordings_of_one_room_do_not_hide_a_walk_in():
    """Two rounds through the kitchen and the hallway's recording, which starts in the kitchen. Without
    rooms the two kitchen rounds look like walking in to each other, and the hallway's real walk-in is not
    recognised any more – it then claims part of the kitchen."""
    k1 = synth.make_room(RUNS, "K1", "A", colour=RED)
    k2 = synth.make_room(RUNS, "K2", "A", loop=(2.0, 1.5, 1.2, 0.8), colour=GREEN)
    hall = synth.make_room(RUNS, "Hall", "B")

    def hall_in_kitchen(rooms, name):
        out, r = combine([k1, k2, hall], rooms=rooms, name=name)
        assert r.returncode == 0, r.stderr
        v, colour = splats(out)
        inside = (v["x"] > 0.2) & (v["x"] < 3.6)
        return int((inside & coloured(colour, HALL)).sum()), json.loads((out / "combine.json").read_text())
    with_rooms, info = hall_in_kitchen(["Kitchen", "Kitchen", "Hallway"], "rooms")
    assert info["rooms"]["Hall"]["walk_in_out_cameras"] > 10
    assert with_rooms < 50
    assert info["rooms"]["K1"]["walk_in_out_cameras"] == info["rooms"]["K2"]["walk_in_out_cameras"] == 0
    without, info = hall_in_kitchen(None, "plain")
    # the walk-in is not recognised; the hallway claims part of the kitchen – less than before 0.3, since a
    # cube only goes to a recording that really has something there
    assert info["rooms"]["Hall"]["walk_in_out_cameras"] == 0 and without > 2 * max(with_rooms, 5)


def test_rooms_need_one_name_per_recording():
    left = synth.make_room(RUNS, "Left", "A", loop=(1.1, 1.5, 0.6, 0.8))
    right = synth.make_room(RUNS, "Right", "A", loop=(2.9, 1.5, 0.6, 0.8))
    for rooms in (["Kitchen"], ["Kitchen", ""], "Kitchen", ["Kitchen", 3]):
        out, r = combine([left, right], rooms=rooms)
        assert r.returncode != 0 and "--rooms" in r.stderr, rooms


def test_the_floor_plan_shows_a_room_of_several_recordings_once():
    left = synth.make_room(RUNS, "Left", "A", loop=(1.1, 1.5, 0.6, 0.8), error=(0.0, (0, 0, 0)))
    right = synth.make_room(RUNS, "Right", "A", loop=(2.9, 1.5, 0.6, 0.8), error=(0.0, (0, 0, 0)))
    hall = synth.make_room(RUNS, "Hall", "B", error=(0.0, (0, 0, 0)))
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "floorplan.py"), str(BASE / "plan.png"),
                        f"{left}=Kitchen", f"{right}=Kitchen", f"{hall}=Hallway"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    info = json.loads(r.stdout.strip().splitlines()[-1])
    sizes = {s["label"]: s["size"] for s in info["scenes"]}
    assert set(sizes) == {"Kitchen", "Hallway"}
    assert abs(max(sizes["Kitchen"]) - 4) < 0.3 and abs(min(sizes["Kitchen"]) - 3) < 0.3     # the whole 4 x 3 m room


# ---------------------------------------------------------------- rooms in the web interface (server)

from conftest import server, wait_job                     # noqa: E402


def make_home():
    """The kitchen in two recordings (left and right half), the hallway in one – one session."""
    synth.make_room(RUNS, "Left", "A", name="Kitchen left", loop=(1.1, 1.5, 0.6, 0.8), colour=RED,
                    recording="2026-10-12_16-40-00_stereo.mp4")
    synth.make_room(RUNS, "Right", "A", name="Kitchen right", loop=(2.9, 1.5, 0.6, 0.8), colour=GREEN,
                    recording="2026-10-12_16-41-00_stereo.mp4")
    synth.make_room(RUNS, "Hall", "B", name="Hallway", recording="2026-10-12_16-43-00_stereo.mp4")


def test_a_room_and_a_home_made_of_it(api):
    make_home()
    status, r = api.post("/api/group", {"name": "Kitchen", "members": "Left,Right", "room": True})
    assert status == 200 and r["slug"] == "Kitchen"
    status, r = api.post("/api/group", {"name": "Home", "members": "Kitchen,Hall"})
    assert status == 200
    by = {g["slug"]: g for g in api.get("/api/groups")[1]["groups"]}
    assert (by["Kitchen"]["room"], by["Kitchen"]["parent"], by["Kitchen"]["rooms"]) == (True, "Home", 1)
    assert (by["Home"]["room"], by["Home"]["members"], by["Home"]["rooms"]) == (False, ["Kitchen", "Hall"], 2)
    assert by["Home"]["scenes"] == ["Left", "Right", "Hall"]
    assert api.get("/api/groups")[1]["suggestion"] is None                    # everything is grouped


def test_what_a_room_and_a_home_may_hold(api):
    make_home()
    synth.make_room(RUNS, "Other", "A", recording="2026-10-03_19-00-00_stereo.mp4")
    assert api.post("/api/group", {"name": "Kitchen", "members": "Left,Right", "room": True})[0] == 200
    assert api.post("/api/group", {"name": "Flat", "members": "Other"})[0] == 200
    for body in ({"name": "Room2", "members": "Kitchen,Hall", "room": True},     # a room holds recordings only
                 {"name": "Home", "members": "Flat,Hall"},                       # no group of groups of groups
                 {"name": "Self", "members": "Self"}):
        status, r = api.post("/api/group", body)
        assert status == 400 and r["error"]["code"] == "err.bad_request", body
    assert api.post("/api/group", {"name": "Home", "members": "Kitchen,Hall"})[0] == 200
    status, r = api.post("/api/group", {"name": "Home 2", "members": "Kitchen"})   # a room in one home only
    assert status == 400 and r["error"]["code"] == "err.in_other_group"
    status, r = api.post("/api/group", {"slug": "Kitchen", "name": "Kitchen", "members": "Left,Right"})
    assert status == 400 and r["error"]["code"] == "err.room_in_group"            # inside a home it stays a room


def test_a_renamed_room_stays_in_its_home(api):
    make_home()
    api.post("/api/group", {"name": "Kitchen", "members": "Left,Right", "room": True})
    api.post("/api/group", {"name": "Home", "members": "Kitchen,Hall"})
    assert api.post("/api/group", {"slug": "Kitchen", "name": "Cooking", "members": "Left,Right", "room": True})[0] == 200
    by = {g["slug"]: g for g in api.get("/api/groups")[1]["groups"]}
    assert by["Home"]["members"] == ["Cooking", "Hall"] and by["Cooking"]["parent"] == "Home"
    assert api.post("/api/delete", {"slug": "Cooking"})[0] == 200                # dissolving the room
    assert {g["slug"]: g for g in api.get("/api/groups")[1]["groups"]}["Home"]["members"] == ["Hall"]


def test_putting_a_home_together_from_its_recordings(api, monkeypatch):
    monkeypatch.setenv("COLOUR", "keep")
    make_home()
    api.post("/api/group", {"name": "Kitchen", "members": "Left,Right", "room": True})
    api.post("/api/group", {"name": "Home", "members": "Kitchen,Hall"})
    assert server.start_combine({"slug": "Home", "full": False}) is None
    js = wait_job()
    assert js["result"] == "ok", js["tail"]
    info = json.loads((RUNS / "Home" / "combine.json").read_text())
    assert {l: x["room"] for l, x in info["rooms"].items()} == {
        "Kitchen left": "Kitchen", "Kitchen right": "Kitchen", "Hallway": "Hallway"}
    v, colour = splats(RUNS / "Home")
    assert coloured(colour, RED)[v["x"] < 0.3].mean() > 0.9 and coloured(colour, GREEN)[(v["x"] > 3.7) & (v["x"] < 3.95)].mean() > 0.9
    home = {g["slug"]: g for g in api.get("/api/groups")[1]["groups"]}["Home"]
    assert home["result"] and not home["result"]["stale"]
    # the room alone, too
    assert server.start_combine({"slug": "Kitchen", "full": False}) is None
    assert wait_job()["result"] == "ok"
    info = json.loads((RUNS / "Kitchen" / "combine.json").read_text())
    assert {x["room"] for x in info["rooms"].values()} == {"Kitchen"}


def test_a_home_without_rooms_is_put_together_as_before(api, monkeypatch):
    make_home()
    api.post("/api/group", {"name": "Home", "members": "Right,Hall"})   # (the left half does not reach the door)
    seen = {}
    real = server.subprocess.Popen

    def popen(cmd, *a, **kw):
        if any("combine.sh" in str(c) for c in cmd):
            seen["rooms"] = kw["env"].get("ROOMS")
        return real(cmd, *a, **kw)
    monkeypatch.setattr(server.subprocess, "Popen", popen)
    assert server.start_combine({"slug": "Home", "full": False}) is None
    assert wait_job()["result"] == "ok"
    assert seen == {"rooms": None}


def test_the_floor_plan_of_a_home_names_rooms(api):
    make_home()
    api.post("/api/group", {"name": "Kitchen", "members": "Left,Right", "room": True})
    api.post("/api/group", {"name": "Home", "members": "Kitchen,Hall"})
    info = server.floorplan(["Left", "Right", "Hall"], "classic", "en", "", "Home")[1]
    assert sorted(s["label"] for s in info["scenes"]) == ["Hallway", "Kitchen"]


def test_a_homes_scale_reaches_the_rooms_and_their_recordings(api):
    make_home()
    api.post("/api/group", {"name": "Kitchen", "members": "Left,Right", "room": True})
    api.post("/api/group", {"name": "Home", "members": "Kitchen,Hall"})
    assert api.post("/api/scale", {"slug": "Home", "scale": "87", "with_scenes": True})[0] == 200
    scales = {x["slug"]: x["scale"] for x in api.get("/api/scenes")[1]}
    assert scales == {"Left": 87, "Right": 87, "Hall": 87}
    assert {g["slug"]: g["scale"] for g in api.get("/api/groups")[1]["groups"]} == {"Home": 87, "Kitchen": 87}
