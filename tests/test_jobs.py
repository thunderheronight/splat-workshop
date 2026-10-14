"""Conversions and tours as real background jobs (scripts/splat.sh, scripts/combine.sh) with a fake pipeline."""
import json
import os
import time

import pytest

from conftest import RUNS, recording, server, wait_job
import synth


def convert(name="Kitchen", existing=None, quality="fast", rec=None):
    rec = rec or recording()
    e = server.start_job({"path": str(rec), "name": name, "quality": quality, "upload": False,
                          **({"existing": existing} if existing else {})})
    assert e is None, e
    return wait_job()


def test_conversion_makes_a_scene():
    js = convert()
    assert js["result"] == "ok", js["tail"]
    assert js["info"]["cameras"] == 20 and js["info"]["rms_cm"] == 1.1
    s = {x["slug"]: x for x in server.scenes()}["Kitchen"]
    assert s["name"] == "Kitchen" and s["gaussians"] == 1000 and s["preview"]


def test_a_second_run_of_the_same_name_asks_first():
    convert()
    assert server.start_job({"path": str(recording()), "name": "Kitchen", "quality": "fast", "upload": False}) == "exists"


def test_start_over_that_fails_keeps_the_old_scene():
    convert(quality="fast")
    before = (RUNS / "Kitchen" / "scene.sog").stat().st_mtime_ns
    os.environ["FAKE_PIPELINE"] = "fail"
    js = convert(existing="fresh", quality="high")
    assert js["result"] == "failed"
    assert (RUNS / "Kitchen" / "scene.sog").stat().st_mtime_ns == before          # the old result is back
    assert json.loads((RUNS / "Kitchen.ui.json").read_text())["quality"] == "fast"  # with its settings
    assert not (RUNS / ".previous").exists()


def test_cancelled_start_over_keeps_the_old_scene():
    convert()
    before = (RUNS / "Kitchen" / "scene.sog").stat().st_mtime_ns
    os.environ["FAKE_PIPELINE"] = "slow"
    assert server.start_job({"path": str(recording()), "name": "Kitchen", "quality": "fast", "upload": False,
                             "existing": "fresh"}) is None
    time.sleep(3)
    # while it computes, the old scene stays in the list, marked
    assert {s["slug"]: s["replacing"] for s in server.scenes()}.get("Kitchen") is True
    assert server.cancel_job() is None
    js = wait_job(timeout=30)
    assert js["result"] == "cancelled"
    assert (RUNS / "Kitchen" / "scene.sog").stat().st_mtime_ns == before


def test_start_over_that_works_replaces_the_scene():
    convert()
    before = (RUNS / "Kitchen" / "scene.sog").stat().st_mtime_ns
    time.sleep(0.05)
    assert convert(existing="fresh")["result"] == "ok"
    assert (RUNS / "Kitchen" / "scene.sog").stat().st_mtime_ns != before
    assert not (RUNS / ".previous").exists()


def test_resume_after_an_interrupted_training():
    rec = recording()
    (RUNS / "Kitchen" / "dataset").mkdir(parents=True)
    (RUNS / "Kitchen" / "dataset" / "spirula-dataset.json").write_text('{"status": "complete"}')
    (RUNS / "Kitchen" / "spirula" / "training" / "step-18000.ckpt").mkdir(parents=True)
    (RUNS / "Kitchen" / "spirula" / "run.json").write_text('{"status": "running"}')
    (RUNS / "Kitchen.ui.json").write_text(json.dumps({"name": "Kitchen", "recording": rec.name, "quality": "fast"}))
    assert server.resume_run("Kitchen", fresh=False) is None
    js = wait_job()
    assert js["result"] == "ok", js["tail"]
    # the (fake) pipeline refuses --resume with an unfinished training, like the real one: it only
    # worked because splat.sh started the training over
    assert "Training was interrupted (last checkpoint: 18000.ckpt)" in js["tail"]
    assert "Incomplete training retained" not in js["tail"]
    assert not (RUNS / "Kitchen" / "spirula" / "training").exists()


def test_putting_a_tour_together():
    synth.make_room(RUNS, "Kitchen", "A", name="Kitchen", recording="2026-10-12_16-40-00_stereo.mp4")
    synth.make_room(RUNS, "Hallway", "B", name="Hallway", recording="2026-10-12_16-43-00_stereo.mp4", drift=(0.03, 0, 0))
    assert server.save_group("", "Example home", "Kitchen,Hallway") == "Example-home"
    assert server.start_combine({"slug": "Example-home", "align": "shift", "partition": "view",
                                 "frame_splats": "500000", "full": True}) is None
    js = wait_job()
    assert js["result"] == "ok", js["tail"]
    g = server.groups()["groups"][0]
    r = g["result"]
    assert r and r["splats"] > 10000 and r["align"] == "shift" and not r["stale"] and r["preview"]
    assert r["seams"]["Kitchen|Hallway"]["cm"] < 0.5
    tour = RUNS / "Example-home"
    assert (tour / "example-home.sog").exists() and (tour / "example-home.sceneinfo.png").exists()
    assert (tour / "full" / "example-home-full.sog").exists()
    # a scene that changes afterwards makes the tour stale
    time.sleep(0.05)
    (RUNS / "Kitchen" / "scene.sog").write_bytes(b"newer")
    assert server.groups()["groups"][0]["result"]["stale"]


def test_a_tour_needs_two_scenes_and_no_other_job():
    synth.make_room(RUNS, "Kitchen", "A", name="Kitchen")
    server.save_group("", "Lonely", "Kitchen")
    assert server.start_combine({"slug": "Lonely"})["code"] == "err.combine_too_few"
    assert server.start_combine({"slug": "Lonely", "align": "magic"})["code"] == "err.bad_request"


# ---------------------------------------------------------------- checks before a conversion starts

@pytest.mark.parametrize("file,code", [
    ("2026-10-12_16-40-00_mono.mp4", "probe.not_stereo"),
    ("2026-10-12_16-40-00_nogpmd.mp4", "probe.no_gpmd"),
    ("2026-10-12_16-40-00_short.mp4", "probe.too_short"),
])
def test_unsuitable_recordings_are_refused(api, file, code):
    rec = recording(file)
    status, r = api.post("/api/start", {"path": str(rec), "name": "Kitchen", "quality": "fast", "upload": False})
    assert status == 400 and r["error"]["code"] == code
    assert not (RUNS / "Kitchen").exists()


def test_a_short_recording_only_warns():
    p = server.probe(str(recording("2026-10-12_16-40-00_brief.mp4")))
    assert p["ok"] and p["warnings"][0]["code"] == "probe.short"


@pytest.mark.parametrize("body,code", [
    ({"path": "/etc/passwd"}, "err.unknown_recording"),              # only recordings from the known folders
    ({"quality": "bogus"}, "err.bad_quality"),
    ({"frames": "9999"}, "err.range"),
    ({"scale": "abc"}, "err.bad_value"),
    ({"name": "///"}, "err.bad_name"),
])
def test_invalid_conversions_are_refused(body, code):
    full = {"path": str(recording()), "name": "Kitchen", "quality": "fast", "upload": False, **body}
    assert server.start_job(full)["code"] == code
    assert server.load_state().get("job") is None


# ---------------------------------------------------------------- a scene that is being computed is left alone

def test_a_scene_being_computed_cannot_be_changed(api):
    convert()                                              # a finished scene ...
    os.environ["FAKE_PIPELINE"] = "slow"                   # ... computed again (resume keeps scene.sog meanwhile)
    assert server.start_job({"path": str(recording()), "name": "Kitchen", "quality": "fast", "upload": False,
                             "existing": "resume"}) is None
    time.sleep(2)
    for path, body in (("/api/delete", {"slug": "Kitchen"}), ("/api/rename", {"slug": "Kitchen", "new": "Other"}),
                       ("/api/clean", {"slug": "Kitchen", "what": "temp"})):
        status, r = api.post(path, body)
        assert status == 400 and r["error"]["code"] == "err.scene_busy", path
    # and no tour is put together meanwhile (one job at a time)
    synth.make_room(RUNS, "Office", "A")
    synth.make_room(RUNS, "Hallway", "B")
    assert server.save_group("", "Home", "Office,Hallway") == "Home"
    assert server.start_combine({"slug": "Home"})["code"] == "err.job_busy"
    assert (RUNS / "Kitchen" / "scene.sog").exists() and (RUNS / "Kitchen.log").exists()
    server.cancel_job()
    wait_job(timeout=30)


# ---------------------------------------------------------------- after a power cut

def leftover_from_a_power_cut():
    """A start over that never finished: the old scene sits in .previous, a half new run in its place."""
    convert(quality="fast")
    old = (RUNS / "Kitchen" / "scene.sog").read_bytes()
    prev = RUNS / ".previous" / "Kitchen"
    prev.mkdir(parents=True)
    (RUNS / "Kitchen").rename(prev / "run")
    (RUNS / "Kitchen.log").rename(prev / "log")
    (RUNS / "Kitchen").mkdir()
    (RUNS / "Kitchen" / "half.txt").write_text("half")
    meta = json.loads((RUNS / "Kitchen.ui.json").read_text())
    (RUNS / "Kitchen.ui.json").write_text(json.dumps({**meta, "quality": "high", "previous": meta}))
    return old


def test_the_server_puts_back_a_scene_left_after_a_power_cut():
    old = leftover_from_a_power_cut()
    server.restore_previous()
    assert (RUNS / "Kitchen" / "scene.sog").read_bytes() == old
    assert not (RUNS / "Kitchen" / "half.txt").exists()
    assert (RUNS / "Kitchen.log").exists()
    assert json.loads((RUNS / "Kitchen.ui.json").read_text())["quality"] == "fast"
    assert not (RUNS / ".previous").exists()


def test_start_over_after_a_power_cut_keeps_the_old_scene_if_it_fails():
    old = leftover_from_a_power_cut()
    os.environ["FAKE_PIPELINE"] = "fail"
    js = convert(existing="fresh")
    assert js["result"] == "failed"
    assert (RUNS / "Kitchen" / "scene.sog").read_bytes() == old    # splat.sh first put the left-over back
