"""Versions of a scene or tour (scripts/versions.sh): several sizes and scales side by side, each its own
scene – also on the Frame, where every version gets its own folder in the gallery."""
import json
import os
import time

import pytest

from conftest import RUNS, TRASH, recording, server, tools_called, wait_job
import synth


def room(slug="Room", **kw):
    run = synth.make_room(RUNS, slug, "A", name=slug, **kw)
    with open(run / "spirula" / "scene.ply", "rb") as f:
        total = next(int(l.split()[-1]) for l in f if l.startswith(b"element vertex"))
    return run, total


def make(slug, sizes, scales="1"):
    assert server.start_versions({"slug": slug, "sizes": sizes, "scales": scales}) is None
    js = wait_job()
    assert js["result"] == "ok", js["tail"]
    return {v["key"]: v for v in server.versions_of(slug)}


def test_versions_in_several_sizes():
    run, total = room()
    versions = make("Room", "10000,20000,all")
    assert set(versions) == {"0.01m", "0.02m", "all"}
    assert [versions[k]["splats"] for k in ("0.01m", "0.02m", "all")] == [10000, 20000, total]
    assert versions["0.01m"]["label"] == "Room · 0.01 M" and versions["all"]["label"] == "Room · all splats"
    reduced = [c for c in tools_called() if c[0] == "splat-transform" and "--decimate" in c]
    assert sorted(c[c.index("--decimate") + 1] for c in reduced) == ["10000", "20000"]     # "all" is not reduced
    for key in versions:
        v = run / "versions" / key
        assert (v / "scene.sog").exists() and (v / f"room-{key}.sceneinfo.png").exists()
        assert (v / f"room-{key}.gallery.png").exists()                           # the scene's preview
        assert json.loads((v / "scene.xrSceneTransform.json").read_text())["worldScale"] == 1.0
    assert server.scenes()[0]["versions"] == 3


def test_a_size_above_the_scene_is_all_of_it():
    run, total = room()
    versions = make("Room", str(total * 2))
    assert list(versions.values())[0]["splats"] == total


def test_versions_in_the_originals_size(api):
    run, _ = room()
    api.post("/api/scale", {"slug": "Room", "scale": "87"})
    versions = make("Room", "10000", "1,87")
    assert set(versions) == {"0.01m", "0.01m-1x87"}
    assert versions["0.01m-1x87"]["label"] == "Room · 0.01 M · 1:87"
    worlds = {k: json.loads((run / "versions" / k / "scene.xrSceneTransform.json").read_text())["worldScale"]
              for k in versions}
    assert worlds == {"0.01m": 1.0, "0.01m-1x87": 87.0}


def test_versions_side_by_side_on_the_frame(api, frame):
    room()
    make("Room", "10000,20000")
    for key in ("0.01m", "0.02m"):
        assert api.post("/api/upload", {"slug": "Room", "version": key})[0] == 200
        end = time.time() + 60
        while (u := server.upload_status(f"Room#{key}")) and u["running"] and time.time() < end:
            time.sleep(0.2)
        assert server.upload_status(f"Room#{key}")["ok"]
    scenes = frame / server.FRAME_SCENES
    assert sorted(p.name for p in scenes.iterdir()) == ["20_Room-0.01m", "21_Room-0.02m"]
    assert sorted(p.name for p in (scenes / "21_Room-0.02m").iterdir()) == [
        "room-0.02m.gallery.png", "room-0.02m.sceneinfo.png", "room-0.02m.sog", "room-0.02m.xrSceneTransform.json"]
    folders = json.loads((RUNS / "Room.ui.json").read_text())["version_folders"]
    assert folders == {"0.01m": "20_Room-0.01m", "0.02m": "21_Room-0.02m"}
    versions = {v["key"]: v for v in server.versions_of("Room")}
    assert versions["0.01m"]["on_frame"] and versions["0.02m"]["on_frame"]
    assert api.post("/api/upload", {"slug": "Room", "version": "9m"})[1]["error"]["code"] == "err.no_version"


def test_deleting_a_version(api):
    run, _ = room()
    make("Room", "10000,20000")
    assert api.post("/api/version/delete", {"slug": "Room", "version": "0.01m"})[0] == 200
    assert set(v["key"] for v in server.versions_of("Room")) == {"0.02m"}
    assert (TRASH / "0.01m").exists() and (run / "spirula" / "scene.ply").exists()
    assert api.post("/api/version/delete", {"slug": "Room", "version": "../spirula"})[0] == 400


def test_versions_follow_a_renamed_scene(api):
    room("Kitchen")
    make("Kitchen", "10000")
    assert api.post("/api/rename", {"slug": "Kitchen", "new": "Küche"})[0] == 200
    v = RUNS / "Kueche" / "versions" / "0.01m"
    assert (v / "kueche-0.01m.sceneinfo.png").exists() and not (v / "kitchen-0.01m.sceneinfo.png").exists()
    assert [x["key"] for x in server.versions_of("Kueche")] == ["0.01m"]


def test_a_version_older_than_its_scene():
    run, _ = room()
    make("Room", "10000")
    assert not server.versions_of("Room")[0]["stale"]
    time.sleep(0.05)
    os.utime(run / "spirula" / "scene.ply")                       # the scene was computed again
    assert server.versions_of("Room")[0]["stale"]


@pytest.mark.parametrize("body,code", [
    ({"sizes": "5"}, "err.range"), ({"sizes": "abc"}, "err.bad_value"), ({"sizes": ""}, "err.no_versions"),
    ({"sizes": "10000", "scales": "0.5"}, "err.range"), ({"slug": "Nope", "sizes": "10000"}, "err.no_scene"),
])
def test_invalid_versions_are_refused(body, code):
    room()
    assert server.start_versions({"slug": "Room", **body})["code"] == code


def test_versions_of_a_tour():
    synth.make_room(RUNS, "Kitchen", "A", name="Kitchen", recording="2026-10-12_16-40-00_stereo.mp4")
    synth.make_room(RUNS, "Hallway", "B", name="Hallway", recording="2026-10-12_16-43-00_stereo.mp4")
    server.save_group("", "Flat", "Kitchen,Hallway")
    assert server.start_combine({"slug": "Flat", "full": False}) is None
    assert wait_job()["result"] == "ok"
    versions = make("Flat", "10000,all")
    assert set(versions) == {"0.01m", "all"}
    assert versions["all"]["splats"] == server.groups()["groups"][0]["result"]["splats"]
    assert server.groups()["groups"][0]["versions"] == 2


def test_one_job_at_a_time():
    room()
    os.environ["FAKE_PIPELINE"] = "slow"
    rec = recording()
    assert server.start_job({"path": str(rec), "name": "Other", "quality": "fast", "upload": False}) is None
    time.sleep(1)
    assert server.start_versions({"slug": "Room", "sizes": "10000"})["code"] == "err.job_busy"


def upload(api, slug, key):
    assert api.post("/api/upload", {"slug": slug, "version": key})[0] == 200
    end = time.time() + 60
    while (u := server.upload_status(f"{slug}#{key}")) and u["running"] and time.time() < end:
        time.sleep(0.2)
    assert server.upload_status(f"{slug}#{key}")["ok"]


def test_taking_a_version_off_the_frame_removes_only_that(api, frame):
    room()
    make("Room", "10000,20000")
    upload(api, "Room", "0.01m")
    upload(api, "Room", "0.02m")
    scenes, backup = frame / server.FRAME_SCENES, frame / server.FRAME_BACKUP
    (scenes / "04_BurtLakeHouse").mkdir()                      # an example scene of Gaussian XR
    (scenes / "30_Room-0.01m-old").mkdir()                     # someone else's folder with a similar name
    assert api.post("/api/frame/remove", {"slug": "Room", "version": "0.01m"})[0] == 200
    assert sorted(p.name for p in scenes.iterdir()) == ["04_BurtLakeHouse", "21_Room-0.02m", "30_Room-0.01m-old"]
    assert not (backup / "20_Room-0.01m").exists() and (backup / "21_Room-0.02m").exists()
    assert "0.01m" not in json.loads((RUNS / "Room.ui.json").read_text())["version_folders"]
    v = {x["key"]: x for x in server.versions_of("Room")}
    assert v["0.01m"]["on_frame"] is False and v["0.02m"]["on_frame"]       # still on the PC, off the Frame
    status, r = api.post("/api/frame/remove", {"slug": "Room", "version": "0.01m"})
    assert status == 400 and r["error"]["code"] == "err.not_on_frame_version"


@pytest.mark.parametrize("version", ["", "..", "../0.02m", ".hidden"])
def test_taking_off_the_frame_only_with_a_proper_version(api, frame, version):
    room()
    assert api.post("/api/frame/remove", {"slug": "Room", "version": version})[0] == 400


def test_taking_off_a_frame_that_is_off(api, frame):
    room()
    make("Room", "10000")
    upload(api, "Room", "0.01m")
    os.environ["FAKE_FRAME_OFF"] = "1"
    status, r = api.post("/api/frame/remove", {"slug": "Room", "version": "0.01m"})
    assert status == 400 and r["error"]["code"] == "err.frame_offline"
    os.environ.pop("FAKE_FRAME_OFF")
    assert (frame / server.FRAME_SCENES / "20_Room-0.01m").exists()
