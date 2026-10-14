"""Recordings on the PC and on the Frame: renaming, the Frame's list, download, copying scenes over."""
import json
import os
import time

import pytest

from conftest import REC, RUNS, TRASH, recording, server, tools_called, wait_for_tool
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
    assert not (frame / "Documents" / "GaussianSplats").exists()        # no second copy on the Frame
    assert json.loads((RUNS / "Kitchen.ui.json").read_text())["frame_folder"] == "20_Kitchen"
    assert wait_for_tool("notify-send")                             # sent in the background
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


def test_a_broken_download_leaves_nothing_behind(api, frame):
    name, data = put_on_frame(frame)
    server.frame_listing(force=True)
    os.environ["FAKE_FRAME_CUT"] = "1"                  # the connection breaks halfway
    assert api.post("/api/download", {"name": name})[0] == 200
    end = time.time() + 20
    while not (server.download or {}).get("finished") and time.time() < end:
        time.sleep(0.1)
    assert server.download["error"]
    assert not (REC / name).exists() and not (REC / (name + ".part")).exists()


def test_a_scene_renamed_before_its_frame_folder_was_remembered(api, frame):
    """Copied to the Frame by an older version (no frame_folder in its settings): renaming finds the
    folder on the Frame, so copying again does not create a second one."""
    synth.make_room(RUNS, "Kitchen", "A", name="Kitchen")
    (frame / SCENES / "20_Kitchen").mkdir()
    (frame / SCENES / "20_Kitchen" / "kitchen.sog").write_bytes(b"old")
    server.frame_listing(force=True)
    assert api.post("/api/rename", {"slug": "Kitchen", "new": "Küche"})[0] == 200
    assert json.loads((RUNS / "Kueche.ui.json").read_text())["frame_folder"] == "20_Kitchen"
    api.post("/api/upload", {"slug": "Kueche"})
    assert wait_upload("Kueche")["ok"]
    assert sorted(p.name for p in (frame / SCENES).iterdir()) == ["20_Kitchen"]


def wait_fetched(timeout=30):
    end = time.time() + timeout
    while server.download_busy() and time.time() < end:
        time.sleep(0.05)
    assert not server.download_busy()
    return server.download_status()


def fetch_notes(wait=0):
    """Notifications about fetching (others of an earlier test may still arrive in the background)."""
    end = time.time() + wait
    while True:
        notes = [c for c in tools_called() if c[0] == "notify-send" and "recording(s)" in c[-1]]
        if notes or time.time() >= end:
            return notes
        time.sleep(0.05)


NAMES = [f"2026-10-14_1{i}-00-00_stereo.mp4" for i in range(4)]


def test_fetching_several_recordings_one_after_the_other(api, frame):
    for i, n in enumerate(NAMES):
        put_on_frame(frame, n, bytes([65 + i]) * 3000)
    (REC / NAMES[0]).write_bytes(b"already here")                    # skipped, never overwritten
    server.frame_listing(force=True)
    os.environ["FAKE_FRAME_SLOW"] = "1"
    assert api.post("/api/download", {"names": "\n".join(NAMES[:3])})[0] == 200
    st = api.get("/api/status")[1]["download"]
    assert st["busy"] and st["batch"]["files"] == 2
    assert [st["name"], *st["queue"]] == NAMES[1:3] or st["queue"] == NAMES[1:3]
    assert api.post("/api/download", {"names": f"{NAMES[3]}\n{NAMES[2]}"})[0] == 200   # added while running
    assert api.post("/api/download", {"names": NAMES[2]})[1]["error"]["code"] == "err.download_busy"
    st = wait_fetched()
    assert st["batch"]["files"] == st["batch"]["done_files"] == 3 and st["batch"]["failed"] == []
    assert (REC / NAMES[0]).read_bytes() == b"already here"
    for i, n in enumerate(NAMES[1:], 1):
        assert (REC / n).read_bytes() == bytes([65 + i]) * 3000
    assert not list(REC.glob("*.part"))
    note = fetch_notes(wait=5)
    assert len(note) == 1 and "3 recording(s)" in note[0][-1]           # one notification at the end


def test_a_failed_one_does_not_stop_the_others(api, frame):
    for n in NAMES[:2]:
        put_on_frame(frame, n)
    server.frame_listing(force=True)
    (frame / VIDEOS / NAMES[0]).unlink()                               # gone from the Frame in the meantime
    assert api.post("/api/download", {"names": "\n".join(NAMES[:2])})[0] == 200
    st = wait_fetched()
    assert st["batch"]["failed"] == [NAMES[0]] and (REC / NAMES[1]).exists() and not (REC / NAMES[0]).exists()
    assert not list(REC.glob("*.part"))
    assert [n[-1] for n in fetch_notes(wait=5)] == ["1 recording(s) could not be fetched (1 did) – is the Frame switched on?"]


def test_cancelling_empties_the_queue(api, frame):
    for n in NAMES[:3]:
        put_on_frame(frame, n, b"x" * 20000)
    server.frame_listing(force=True)
    os.environ["FAKE_FRAME_SLOW"] = "1"
    assert api.post("/api/download", {"names": "\n".join(NAMES[:3])})[0] == 200
    time.sleep(0.3)
    assert api.post("/api/download/cancel", {})[0] == 200
    st = wait_fetched()
    assert st["queue"] == [] and st["batch"]["cancelled"]
    assert not list(REC.iterdir())                                     # no file and no half of one
    assert not fetch_notes(wait=0.5)
    assert api.post("/api/download/cancel", {})[1]["error"]["code"] == "err.no_download"


def test_fetching_only_what_is_on_the_frame(api, frame):
    put_on_frame(frame, NAMES[0])
    server.frame_listing(force=True)
    for body in ({"names": "../../etc/passwd"}, {"names": "x\n.."}, {"names": "\n"}):
        status, r = api.post("/api/download", body)
        assert status == 400 and r["error"]["code"] == "err.not_on_frame", body
    assert api.post("/api/download", {"names": [NAMES[0]]})[1]["error"]["code"] == "err.bad_request"


def test_deleting_a_recording_on_this_pc_moves_it_to_the_trash(api):
    rec = recording()
    assert api.post("/api/rename", {"name": rec.name, "new": "Kitchen"})[0] == 200
    assert api.post("/api/recording/delete", {"name": "Kitchen.mp4", "where": "pc"})[0] == 200
    assert not (REC / "Kitchen.mp4").exists() and (TRASH / "Kitchen.mp4").exists()
    assert "Kitchen.mp4" not in json.loads((server.RECMETA).read_text())          # its old name is forgotten
    assert api.get("/api/recordings")[1]["local"] == []


@pytest.mark.parametrize("name", ["", "..", "../state/state.json", "nope.mp4"])
def test_deleting_only_recordings_of_the_recordings_folder(api, name):
    recording()
    status, r = api.post("/api/recording/delete", {"name": name, "where": "pc"})
    assert status == 400 and r["error"]["code"] == "err.not_local"
    assert len(list(REC.iterdir())) == 1 and not list(TRASH.iterdir())
    assert api.post("/api/recording/delete", {"name": "x", "where": "elsewhere"})[0] == 400


def test_a_recording_an_unfinished_scene_needs_stays(api):
    rec = recording()
    synth.make_room(RUNS, "Kitchen", "A", recording=rec.name)
    (RUNS / "Kitchen" / "scene.sog").unlink()                                       # not finished yet
    status, r = api.post("/api/recording/delete", {"name": rec.name, "where": "pc"})
    assert status == 400 and r["error"]["code"] == "err.recording_needed"
    assert rec.exists()


def test_deleting_a_recording_on_the_frame(api, frame):
    name, _ = put_on_frame(frame)
    other, _ = put_on_frame(frame, NAMES[1])
    (REC / name).write_bytes(b"the copy on this PC")
    assert api.post("/api/recording/delete", {"name": name, "where": "frame"})[0] == 200
    assert not (frame / VIDEOS / name).exists() and (frame / VIDEOS / other).exists()
    assert (REC / name).read_bytes() == b"the copy on this PC"                     # the PC is not touched
    assert [f["name"] for f in api.get("/api/recordings")[1]["frame"]] == [other]
    for bad in (name, "../" + other, "thumbnails", ""):
        status, r = api.post("/api/recording/delete", {"name": bad, "where": "frame"})
        assert status == 400 and r["error"]["code"] == "err.not_on_frame", bad
    assert (frame / VIDEOS / other).exists()


def test_a_recording_being_fetched_is_not_deleted(api, frame):
    for n in NAMES[:2]:
        put_on_frame(frame, n, b"x" * 20000)
    server.frame_listing(force=True)
    os.environ["FAKE_FRAME_SLOW"] = "1"
    assert api.post("/api/download", {"names": "\n".join(NAMES[:2])})[0] == 200
    for n in NAMES[:2]:                                     # fetching right now, and waiting
        status, r = api.post("/api/recording/delete", {"name": n, "where": "frame"})
        assert status == 400 and r["error"]["code"] == "err.recording_fetching"
    assert all((frame / VIDEOS / n).exists() for n in NAMES[:2])


def test_deleting_on_a_frame_that_is_off(api, frame):
    name, _ = put_on_frame(frame)
    os.environ["FAKE_FRAME_OFF"] = "1"
    status, r = api.post("/api/recording/delete", {"name": name, "where": "frame"})
    assert status == 400 and r["error"]["code"] == "err.frame_offline"
