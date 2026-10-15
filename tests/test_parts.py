"""A long recording in parts (0.3): each part gets up to 500 image pairs, the parts become one room.

The pipeline matches its images to the poses by the time stamps inside the recording, so a part has to be
cut with the original time (ffmpeg -copyts) – checked here on the command splat.sh runs. The fake pipeline
(FAKE_PIPELINE=room) gives every part a synthetic round through one half of room A, so putting the parts
together really runs."""
import json
import os
import subprocess

import pytest

from conftest import REC, ROOT, RUNS, TRASH, recording, server, tools_called, wait_for_tool, wait_job

NAME = "2026-10-14_16-57-54_stereo.mp4"                  # the fake ffprobe makes it 60 s long


def cuts():
    return [c for c in tools_called() if c[0] == "ffmpeg"]


def run_chain(timeout=120):
    """Let the chain run on as the server's watcher does: a tick after every job – and one more, which
    must not do anything again."""
    was = True
    for _ in range(10):
        js = wait_job(timeout)
        running = server.watch_once(was)
        server.watch_once(running)
        nxt = server.job_status(tail=False)
        if not running and nxt["out"] == js["out"]:
            return js
        assert running and nxt["running"], "the watcher reports the next job as running"
        was = running
    raise AssertionError("the chain did not end")


def notes():
    for p in server.notes:                     # notifications are sent in the background
        p.wait(10)
    return [c for c in tools_called() if c[0] == "notify-send"]


def test_parts_are_equally_long_and_overlap_a_little():
    assert server.part_ranges(240, 3) == [(0.0, 82.0), (78.0, 162.0), (158.0, 240)]
    ranges = server.part_ranges(100, 2)
    assert ranges[0][0] == 0 and ranges[-1][1] == 100 and ranges[0][1] - ranges[1][0] == server.PART_OVERLAP


def test_a_recording_in_parts_becomes_one_room(api):
    os.environ["FAKE_PIPELINE"] = "room"
    rec = recording(NAME)
    status, r = api.post("/api/parts", {"path": str(rec), "name": "Living room", "quality": "high", "parts": 2,
                                        "upload": False})
    assert status == 200, r
    job = server.job_status(tail=False)
    assert (job["chain"]["i"], job["chain"]["n"], job["part"]) == (0, 2, "0.00-32.00")
    js = run_chain()
    assert js["kind"] == "combine" and js["result"] == "ok", js.get("tail")
    # cut losslessly, with the original time, from the original – which stays
    assert [c[1:] for c in cuts()] == [
        ["-v", "error", "-y", "-copyts", "-ss", "0.00", "-i", str(rec), "-to", "32.00", "-map", "0", "-c", "copy",
         "-copy_unknown", str(RUNS / ".parts" / "Living-room-part-1-of-2.mp4.tmp.mp4")],
        ["-v", "error", "-y", "-copyts", "-ss", "28.00", "-i", str(rec), "-to", "60.00", "-map", "0", "-c", "copy",
         "-copy_unknown", str(RUNS / ".parts" / "Living-room-part-2-of-2.mp4.tmp.mp4")]]
    assert rec.read_bytes() == b"not really a video" and not (RUNS / ".parts").exists()
    # the parts: the original is their recording, each knows its stretch
    parts = {s: server.read_meta(s) for s in ("Living-room-part-1-of-2", "Living-room-part-2-of-2")}
    assert {s: (m["recording"], m["part"]) for s, m in parts.items()} == {
        "Living-room-part-1-of-2": (NAME, "0.00-32.00"), "Living-room-part-2-of-2": (NAME, "28.00-60.00")}
    room = {g["slug"]: g for g in server.groups()["groups"]}["Living-room"]
    assert room["room"] and room["members"] == list(parts) and room["result"] and not room["result"]["stale"]
    info = json.loads((RUNS / "Living-room" / "combine.json").read_text())
    assert {x["room"] for x in info["rooms"].values()} == {"Living room"}
    assert [n[-1] for n in notes() if "part" in n[-1]] == [
        "“Living room”: 2 parts are one room now – it is being put together"]


def test_a_failed_part_stops_the_chain():
    os.environ["FAKE_PIPELINE"] = "fail"
    rec = recording(NAME)
    assert server.start_parts({"path": str(rec), "name": "Living room", "quality": "high", "parts": 3}) is None
    js = run_chain()
    assert js["result"] == "failed" and js["chain"]["i"] == 0
    assert len(cuts()) == 1 and "Living-room" not in {g["slug"] for g in server.groups()["groups"]}
    assert any("part 1 of 3 failed" in n[-1] for n in wait_for_tool("notify-send"))


def test_a_cancelled_part_stops_the_chain_quietly():
    os.environ["FAKE_PIPELINE"] = "slow"
    rec = recording(NAME)
    assert server.start_parts({"path": str(rec), "name": "Living room", "quality": "high", "parts": 2}) is None
    import time
    time.sleep(1)
    server.cancel_job()
    js = run_chain(timeout=30)
    assert js["result"] == "cancelled" and len(cuts()) == 1
    assert not [n for n in notes() if "part" in n[-1]]


@pytest.mark.parametrize("body,code", [
    ({"parts": 1}, "err.range"), ({"parts": 11}, "err.range"), ({"parts": "x"}, "err.bad_value"),
    ({"parts": 4}, "err.parts_too_short"),                  # 15 s each
    ({"parts": 2, "path": "/etc/passwd"}, "err.unknown_recording"),
    ({"parts": 2, "name": "Taken"}, "err.scene_name_taken"),
])
def test_parts_that_cannot_work_are_refused(api, body, code):
    rec = recording(NAME)
    (RUNS / "Taken").mkdir()
    full = {"path": str(rec), "name": "Living room", "quality": "high", **body}
    status, r = api.post("/api/parts", full)
    assert status == 400 and r["error"]["code"] == code
    assert server.load_state().get("job") is None


def test_resuming_a_part_cuts_the_same_stretch():
    os.environ["FAKE_PIPELINE"] = "fail"
    rec = recording(NAME)
    server.start_parts({"path": str(rec), "name": "Living room", "quality": "high", "parts": 2})
    run_chain()
    os.environ["FAKE_PIPELINE"] = "ok"
    assert server.resume_run("Living-room-part-1-of-2", fresh=True) is None
    js = wait_job()
    assert js["part"] == "0.00-32.00" and js["result"] == "ok", js.get("tail")


def test_the_original_of_parts_is_only_deleted_when_asked_twice(api):
    os.environ["FAKE_PIPELINE"] = "room"
    rec = recording(NAME)
    api.post("/api/parts", {"path": str(rec), "name": "Living room", "quality": "high", "parts": 2, "upload": False})
    run_chain()
    status, r = api.post("/api/recording/delete", {"name": NAME, "where": "pc"})
    assert status == 400 and r["error"]["code"] == "err.recording_has_parts" and r["error"]["params"]["n"] == 2
    assert rec.exists()
    assert api.post("/api/recording/delete", {"name": NAME, "where": "pc", "force": True})[0] == 200
    assert not rec.exists() and (TRASH / NAME).exists()
    assert (RUNS / "Living-room-part-1-of-2" / "scene.sog").exists()        # the scenes stay


def test_splat_sh_refuses_odd_stretches():
    rec = recording(NAME)
    for part in ("abc", "30-10", "5", "-3-4", "1-2;rm"):
        r = subprocess.run([str(ROOT / "scripts" / "splat.sh"), str(rec), "Odd"], capture_output=True, text=True,
                           env={**os.environ, "PART": part, "NO_UPLOAD": "1"})
        assert r.returncode == 1 and "PART must be" in r.stderr, part
    assert not cuts() and not (RUNS / "Odd").exists()
    assert list(REC.iterdir()) == [rec]


def test_deleting_a_part_that_did_not_finish_removes_its_cut():
    """A failed or cancelled part keeps its piece of video (for resuming); deleting the run deletes it too
    – it is as big as that stretch of the recording, hidden and in no list."""
    os.environ["FAKE_PIPELINE"] = "fail"
    rec = recording(NAME)
    server.start_parts({"path": str(rec), "name": "Living room", "quality": "high", "parts": 2})
    run_chain()
    cut = RUNS / ".parts" / "Living-room-part-1-of-2.mp4"
    assert cut.exists()
    assert server.delete_scene("Living-room-part-1-of-2") is None
    assert not cut.exists() and not (RUNS / ".parts").exists() and not (TRASH / cut.name).exists()
    assert rec.exists()
