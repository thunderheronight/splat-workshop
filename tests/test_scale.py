"""Scale for models (e.g. H0 = 1:87): floor plan and measurements in the original's size, and the
original's size in Gaussian XR (worldScale).

The model is the synthetic 4 x 3 m room built 87 times smaller, so the expected numbers are simply
those of the full-size room."""
import json
import subprocess
import sys
import time

import pytest

from conftest import BASE, ROOT, RUNS, server
import synth

H0 = 87


def plan(*runs, ratio=None):
    out = runs[0].parent / "plan.png"
    cmd = [sys.executable, str(ROOT / "scripts" / "floorplan.py"), str(out), *[f"{r}={r.name}" for r in runs]]
    if ratio:
        cmd += ["--ratio", str(ratio)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_a_model_has_the_floor_plan_of_the_original():
    full = plan(synth.make_room(BASE / "full", "Room", "A", error=(0.0, (0, 0, 0))))
    model = plan(synth.make_room(BASE / "model", "Room", "A", error=(0.0, (0, 0, 0)), scale=1 / H0), ratio=H0)
    assert model["scenes"][0]["size"] == pytest.approx(full["scenes"][0]["size"], abs=0.05)     # 4 x 3 m
    assert model["pixels_per_metre"] == pytest.approx(full["pixels_per_metre"], rel=0.01)       # measuring in metres
    assert model["ratio"] == H0


def test_a_model_without_its_scale_still_draws():
    info = plan(synth.make_room(BASE / "model", "Room", "A", error=(0.0, (0, 0, 0)), scale=1 / H0))
    assert info["ratio"] == 1


@pytest.mark.parametrize("scale", ["0.5", "2000", "abc", "nan"])
def test_scales_out_of_range_are_refused(api, scale):
    synth.make_room(RUNS, "Layout", "A")
    status, r = api.post("/api/scale", {"slug": "Layout", "scale": scale})
    assert status == 400 and r["error"]["code"] in ("err.range", "err.bad_value")


def test_setting_and_clearing_the_scale_of_a_scene(api):
    synth.make_room(RUNS, "Layout", "A")
    assert api.post("/api/scale", {"slug": "Layout", "scale": "87", "xr": True})[0] == 200
    s = {x["slug"]: x for x in api.get("/api/scenes")[1]}["Layout"]
    assert (s["scale"], s["scale_xr"]) == (87, True)
    assert api.post("/api/scale", {"slug": "Layout", "scale": "1", "xr": True})[0] == 200
    s = {x["slug"]: x for x in api.get("/api/scenes")[1]}["Layout"]
    assert (s["scale"], s["scale_xr"]) == (1, False)                  # original size: nothing to enlarge in XR
    assert api.post("/api/scale", {"slug": "Nope", "scale": "87"})[0] == 400


def test_a_group_passes_its_scale_on(api):
    synth.make_room(RUNS, "Station", "A", recording="2026-10-12_16-40-00_stereo.mp4")
    synth.make_room(RUNS, "Yard", "B", recording="2026-10-12_16-43-00_stereo.mp4")
    synth.make_room(RUNS, "Other", "A", recording="2026-10-03_19-00-00_stereo.mp4")
    server.save_group("", "Layout", "Station,Yard")
    assert api.post("/api/scale", {"slug": "Layout", "scale": "160", "with_scenes": True})[0] == 200
    scales = {x["slug"]: x["scale"] for x in api.get("/api/scenes")[1]}
    assert scales == {"Station": 160, "Yard": 160, "Other": 1}
    assert api.get("/api/groups")[1]["groups"][0]["scale"] == 160
    api.post("/api/scale", {"slug": "Layout", "scale": "87"})         # without its scenes this time
    assert {x["slug"]: x["scale"] for x in api.get("/api/scenes")[1]}["Station"] == 160


def test_the_floor_plan_endpoint_measures_in_the_originals_metres(api):
    synth.make_room(RUNS, "Layout", "A", error=(0.0, (0, 0, 0)), scale=1 / H0)
    api.post("/api/scale", {"slug": "Layout", "scale": str(H0)})
    status, png = api.get("/api/floorplan?slugs=Layout&style=classic")
    assert status == 200 and png[:4] == b"\x89PNG"
    full = plan(synth.make_room(BASE / "full", "Room", "A", error=(0.0, (0, 0, 0))))
    info = server.floorplan(["Layout"], "classic", "en", "")[1]
    assert info["pixels_per_metre"] == pytest.approx(full["pixels_per_metre"], rel=0.01)


def wait_upload(slug):
    end = time.time() + 120
    while time.time() < end:
        u = server.upload_status(slug)
        if u and not u["running"]:
            return u
        time.sleep(0.2)
    raise TimeoutError


@pytest.mark.parametrize("xr,expected", [(True, H0), (False, 1.0)])
def test_world_scale_on_the_frame(api, frame, xr, expected):
    run = synth.make_room(RUNS, "Layout", "A")
    api.post("/api/scale", {"slug": "Layout", "scale": str(H0), "xr": xr})
    api.post("/api/upload", {"slug": "Layout"})
    assert wait_upload("Layout")["ok"]
    sent = json.loads((frame / server.FRAME_SCENES / "20_Layout" / "layout.xrSceneTransform.json").read_text())
    assert sent["worldScale"] == expected
    assert sent["transform"] == json.loads((run / "scene.xrSceneTransform.json").read_text())["transform"]
    assert json.loads((run / "scene.xrSceneTransform.json").read_text())["worldScale"] == 1.0   # the original stays


def test_changing_the_scale_updates_the_frame(api, frame):
    synth.make_room(RUNS, "Layout", "A")
    api.post("/api/upload", {"slug": "Layout"})
    wait_upload("Layout")
    assert api.post("/api/scale", {"slug": "Layout", "scale": "45", "xr": True, "update_frame": True})[0] == 200
    assert wait_upload("Layout")["ok"]
    sent = json.loads((frame / server.FRAME_SCENES / "20_Layout" / "layout.xrSceneTransform.json").read_text())
    assert sent["worldScale"] == 45


def test_a_tour_of_a_model_is_walkable_in_both_versions():
    synth.make_room(RUNS, "Station", "A", recording="2026-10-12_16-40-00_stereo.mp4")
    synth.make_room(RUNS, "Yard", "B", recording="2026-10-12_16-43-00_stereo.mp4")
    server.save_group("", "Layout", "Station,Yard")
    server.set_scale("Layout", H0, xr=True, members=True, update_frame=False)
    assert server.start_combine({"slug": "Layout", "full": True}) is None
    from conftest import wait_job
    assert wait_job()["result"] == "ok"
    tour = RUNS / "Layout"
    assert json.loads((tour / "layout.xrSceneTransform.json").read_text())["worldScale"] == H0
    assert json.loads((tour / "full" / "layout-full.xrSceneTransform.json").read_text())["worldScale"] == H0
