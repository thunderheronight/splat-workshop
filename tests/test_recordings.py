"""Recordings on the PC and on the Frame: renaming, the Frame's list, download, copying scenes over."""
import json
import os
import time

from conftest import REC, RUNS, recording, server, tools_called
import synth

VIDEOS = server.FRAME_VIDEOS
SCENES = server.FRAME_SCENES


# ---------------------------------------------------------------- renaming a recording on the PC

def test_renaming_a_recording_keeps_date_check_result_and_thumbnail(api):
    rec = recording("2026-10-12_16-40-00_stereo.mp4")
    server.CACHE.mkdir(parents=True, exist_ok=True)
    check = server.check_file(rec)
    check.write_text(json.dumps({"level": "warn"}))
    (server.CACHE / "2026-10-12_16-40-00_stereo.thumb").write_bytes(b"thumb")
    status, r = api.post("/api/rename", {"name": rec.name, "new": "Kitchen / evening"})
    assert status == 200 and r["name"] == "Kitchen - evening.mp4"
    new = REC / "Kitchen - evening.mp4"
    assert new.exists() and not rec.exists()
    f = next(x for x in server.local_files() if x["name"] == new.name)
    assert (f["date"], f["label"], f["orig"]) == ("2026-10-12T16:40", "Kitchen - evening", rec.name)
    assert f["check"] == "warn"                                     # the check result moved along
    assert (server.CACHE / "Kitchen - evening.thumb").read_bytes() == b"thumb"


def test_runs_follow_a_renamed_recording(api):
    rec = recording()
    synth.make_room(RUNS, "Kitchen", "A", recording=rec.name)        # a finished scene made from it
    api.post("/api/rename", {"name": rec.name, "new": "Kitchen take 1"})
    meta = json.loads((RUNS / "Kitchen.ui.json").read_text())
    assert meta["recording"] == "Kitchen take 1.mp4" and meta["recording_renamed"] is True
    assert server.no_resume("Kitchen") == "renamed"


def test_a_recording_with_an_unfinished_run_cannot_be_renamed(api):
    rec = recording()
    (RUNS / "Kitchen").mkdir()                                       # interrupted, no scene.sog
    (RUNS / "Kitchen.ui.json").write_text(json.dumps({"name": "Kitchen", "recording": rec.name}))
    status, r = api.post("/api/rename", {"name": rec.name, "new": "Other"})
    assert status == 400 and r["error"]["code"] == "err.recording_unfinished"   # the pipeline could not resume it
    assert rec.exists()


def test_renaming_a_recording_to_a_taken_name_is_refused(api):
    a, b = recording("2026-10-12_16-40-00_stereo.mp4"), recording("2026-10-12_16-43-00_stereo.mp4")
    status, r = api.post("/api/rename", {"name": a.name, "new": b.stem})
    assert status == 400 and r["error"]["code"] == "err.name_taken"
    assert api.post("/api/rename", {"name": "../../etc/passwd", "new": "x"})[0] == 400


# ---------------------------------------------------------------- the Frame

def put_on_frame(home, name="2026-10-14_10-00-00_stereo.mp4", data=b"recording" * 1000):
    (home / VIDEOS / name).write_bytes(data)
    (home / VIDEOS / "thumbnails" / name.replace("_stereo.mp4", ".jpg")).write_bytes(b"jpegdata")
    return name, data


def test_the_frames_recordings_are_listed(api, frame):
    name, data = put_on_frame(frame)
    (frame / SCENES / "20_Kitchen").mkdir()
    status, r = api.get("/api/recordings?refresh=1")
    assert status == 200 and r["frame_online"]
    f = r["frame"][0]
    assert (f["name"], f["size"], f["date"], f["local"]) == (name, len(data), "2026-10-14T10:00", None)
    assert server.frame_listing()["scenes"] == ["20_Kitchen"]


def test_a_frame_that_is_off(api, frame):
    os.environ["FAKE_FRAME_OFF"] = "1"
    r = api.get("/api/recordings?refresh=1")[1]
    assert r["frame_configured"] and r["frame_online"] is False and r["frame"] is None


def test_the_frames_preview_image(api, frame):
    name, _ = put_on_frame(frame)
    status, body = api.get(f"/api/thumb?source=frame&name={name}")
    assert status == 200 and body == b"jpegdata"


def test_downloading_a_recording(api, frame):
    name, data = put_on_frame(frame)
    server.frame_listing(force=True)
    assert api.post("/api/download", {"name": name})[0] == 200
    end = time.time() + 20
    while not (server.download or {}).get("finished") and time.time() < end:
        time.sleep(0.1)
    assert server.download["error"] is None
    assert (REC / name).read_bytes() == data and not (REC / (name + ".part")).exists()
    status, r = api.post("/api/download", {"name": name})               # never overwrites
    assert status == 400 and r["error"]["code"] == "err.name_taken"
    assert api.post("/api/download", {"name": "not-there.mp4"})[1]["error"]["code"] == "err.not_on_frame"


def wait_upload(slug):
    end = time.time() + 120
    while time.time() < end:
        u = server.upload_status(slug)
        if u and not u["running"]:
            return u
        time.sleep(0.2)
    raise TimeoutError


def test_copying_a_scene_to_the_frame(api, frame):
    synth.make_room(RUNS, "Kitchen", "A", name="Kitchen")
    assert api.post("/api/upload", {"slug": "Kitchen"})[0] == 200
    u = wait_upload("Kitchen")
    assert u["ok"], u.get("error")
    folder = frame / SCENES / "20_Kitchen"
    assert sorted(p.name for p in folder.iterdir()) == [
        "kitchen.gallery.png", "kitchen.sceneinfo.png", "kitchen.sog", "kitchen.xrSceneTransform.json"]
    assert (frame / "Documents" / "GaussianSplats" / "20_Kitchen" / "kitchen.sog").exists()   # backup copy
    assert json.loads((RUNS / "Kitchen.ui.json").read_text())["frame_folder"] == "20_Kitchen"
    end = time.time() + 5                                             # the notification is sent in the background
    while not any(c[0] == "notify-send" for c in tools_called()) and time.time() < end:
        time.sleep(0.1)
    assert any(c[0] == "notify-send" for c in tools_called())
    assert {s["slug"]: s["on_frame"] for s in server.scenes()}["Kitchen"] is True


def test_a_renamed_scene_stays_in_its_frame_folder(api, frame):
    synth.make_room(RUNS, "Kitchen", "A", name="Kitchen")
    api.post("/api/upload", {"slug": "Kitchen"})
    wait_upload("Kitchen")
    api.post("/api/rename", {"slug": "Kitchen", "new": "Küche"})
    assert api.post("/api/upload", {"slug": "Kueche"})[0] == 200
    assert wait_upload("Kueche")["ok"]
    scenes = sorted(p.name for p in (frame / SCENES).iterdir())
    assert scenes == ["20_Kitchen"]                                    # no second copy
    assert sorted(p.name for p in (frame / SCENES / "20_Kitchen").iterdir()) == [
        "kueche.gallery.png", "kueche.sceneinfo.png", "kueche.sog", "kueche.xrSceneTransform.json"]
