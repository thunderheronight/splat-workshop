"""The "On the Frame" tab: everything in Gaussian XR's gallery, what it is, and taking it off the Frame."""
import json
import time

from conftest import RUNS, server
import synth


def upload(api, slug, version=""):
    body = {"slug": slug, **({"version": version} if version else {})}
    assert api.post("/api/upload", body)[0] == 200
    key = f"{slug}#{version}" if version else slug
    end = time.time() + 60
    while (u := server.upload_status(key)) and u["running"] and time.time() < end:
        time.sleep(0.2)
    assert server.upload_status(key)["ok"]


def example(frame, name="04_BurtLakeHouse"):
    d = frame / server.FRAME_STEAM / name                           # Gaussian XR's own gallery folder
    d.mkdir()
    (d / "trained-572k.htgs.gallery.png").write_bytes(b"example preview")
    (d / "trained-572k.htgs.ply").write_bytes(b"x" * 5000)
    return d


def test_the_gallery_on_the_frame(api, frame):
    synth.make_room(RUNS, "Kitchen", "A", name="Kitchen")
    upload(api, "Kitchen")
    assert server.start_versions({"slug": "Kitchen", "sizes": "10000"}) is None
    from conftest import wait_job
    wait_job()
    upload(api, "Kitchen", "0.01m")
    example(frame)
    (frame / server.FRAME_STEAM / "common_audio").mkdir()           # not a scene: no preview image
    status, r = api.get("/api/frame/gallery?refresh=1")
    assert status == 200 and r["online"]
    by = {x["folder"]: x for x in r["scenes"]}
    assert set(by) == {"04_BurtLakeHouse", "20_Kitchen", "21_Kitchen-0.01m"}
    assert (by["20_Kitchen"]["kind"], by["20_Kitchen"]["name"], by["20_Kitchen"]["slug"]) == ("scene", "Kitchen", "Kitchen")
    assert (by["21_Kitchen-0.01m"]["kind"], by["21_Kitchen-0.01m"]["version"]) == ("version", "0.01m")
    assert (by["04_BurtLakeHouse"]["kind"], by["04_BurtLakeHouse"]["name"]) == ("other", "BurtLakeHouse")
    assert by["04_BurtLakeHouse"]["size"] >= 5000
    assert (by["04_BurtLakeHouse"]["place"], by["20_Kitchen"]["place"]) == ("steam", "own")
    status, png = api.get("/api/frame/preview?folder=04_BurtLakeHouse")
    assert status == 200 and png == b"example preview"
    assert api.get("/api/frame/preview?folder=../../.ssh")[0] == 404


def test_taking_a_scene_off_the_frame_and_copying_it_again(api, frame):
    synth.make_room(RUNS, "Kitchen", "A", name="Kitchen")
    upload(api, "Kitchen")
    assert api.post("/api/frame/remove", {"folder": "20_Kitchen"})[0] == 200
    assert not (frame / server.FRAME_SCENES / "20_Kitchen").exists()
    assert (RUNS / "Kitchen" / "scene.sog").exists()                    # the PC is not touched
    assert json.loads((RUNS / "Kitchen.ui.json").read_text()).get("frame_folder") is None
    upload(api, "Kitchen")                                              # copying again works
    assert (frame / server.FRAME_SCENES / "20_Kitchen" / "kitchen.sog").exists()


def test_an_example_scene_can_be_deleted_too(api, frame):
    d = example(frame)
    assert api.post("/api/frame/remove", {"folder": "04_BurtLakeHouse"})[0] == 200
    assert not d.exists()


def test_only_scenes_of_the_gallery_can_be_removed(api, frame):
    (frame / server.FRAME_STEAM / "common_audio").mkdir()
    (frame / "Videos" / "keep.txt").write_text("x")
    for folder in ("common_audio", "..", "../..", "../../Videos", "nope"):
        status, r = api.post("/api/frame/remove", {"folder": folder})
        assert status == 400, folder
    assert (frame / server.FRAME_STEAM / "common_audio").exists() and (frame / "Videos" / "keep.txt").exists()


def old_copy(frame, folder, stem, content=b"old"):
    """A scene copied by Splat Workshop 0.2.3 or older: in Gaussian XR's own gallery folder."""
    d = frame / server.FRAME_STEAM / folder
    d.mkdir()
    for ext in ("sog", "xrSceneTransform.json", "sceneinfo.png"):
        (d / f"{stem}.{ext}").write_bytes(content)
    (d / f"{stem}.gallery.png").write_bytes(b"preview")
    return d


def test_scenes_go_to_the_personal_folder_and_numbers_count_both(api, frame):
    old_copy(frame, "20_Other", "other")                            # e.g. a scene of someone else
    synth.make_room(RUNS, "Kitchen", "A", name="Kitchen")
    upload(api, "Kitchen")
    assert sorted(p.name for p in (frame / server.FRAME_SCENES).iterdir()) == ["21_Kitchen"]
    assert (frame / server.FRAME_STEAM / "20_Other").exists()


def test_copying_again_moves_an_old_copy_over(api, frame):
    old = old_copy(frame, "22_Kitchen", "kitchen")
    synth.make_room(RUNS, "Kitchen", "A", name="Kitchen")
    upload(api, "Kitchen")
    assert not old.exists()
    new = frame / server.FRAME_SCENES / "22_Kitchen"
    assert (new / "kitchen.sog").read_bytes() != b"old"
    assert json.loads((RUNS / "Kitchen.ui.json").read_text())["frame_folder"] == "22_Kitchen"


def test_moving_the_old_copies_to_the_personal_folder(api, frame):
    synth.make_room(RUNS, "Kitchen", "A", name="Kitchen")
    synth.make_room(RUNS, "Hallway", "B", name="Hallway")
    old_copy(frame, "20_Kitchen", "kitchen")
    old_copy(frame, "21_Hallway", "hallway")
    example(frame)
    newer = frame / server.FRAME_SCENES / "21_Hallway"               # already copied again: the newer one stays
    newer.mkdir(parents=True)
    (newer / "hallway.sog").write_bytes(b"new")
    (newer / "hallway.gallery.png").write_bytes(b"preview")
    assert api.post("/api/frame/move", {})[0] == 200
    assert sorted(p.name for p in (frame / server.FRAME_STEAM).iterdir()) == ["04_BurtLakeHouse"]   # the example stays
    assert sorted(p.name for p in (frame / server.FRAME_SCENES).iterdir()) == ["20_Kitchen", "21_Hallway"]
    assert (frame / server.FRAME_SCENES / "20_Kitchen" / "kitchen.sog").read_bytes() == b"old"
    assert (newer / "hallway.sog").read_bytes() == b"new"
    kinds = {(x["folder"], x["place"]) for x in api.get("/api/frame/gallery?refresh=1")[1]["scenes"]}
    assert kinds == {("04_BurtLakeHouse", "steam"), ("20_Kitchen", "own"), ("21_Hallway", "own")}
    assert {s["slug"]: s["on_frame"] for s in server.scenes()} == {"Kitchen": True, "Hallway": True}


def test_the_same_folder_in_both_places(api, frame):
    old_copy(frame, "20_Kitchen", "kitchen")
    d = frame / server.FRAME_SCENES / "20_Kitchen"
    d.mkdir()
    (d / "kitchen.gallery.png").write_bytes(b"own preview")
    assert api.get("/api/frame/preview?folder=20_Kitchen&place=own") == (200, b"own preview")
    assert api.post("/api/frame/remove", {"folder": "20_Kitchen", "place": "steam"})[0] == 200
    assert d.exists() and not (frame / server.FRAME_STEAM / "20_Kitchen").exists()
    assert api.post("/api/frame/remove", {"folder": "20_Kitchen", "place": "nowhere"})[0] == 400
