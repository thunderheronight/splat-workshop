"""Your own splats (0.6, scripts/import.sh): a splat file from another program becomes a scene like the
others, and goes to the Frame."""
import base64
import io
import json
import time

import numpy as np
import pytest
from PIL import Image

from conftest import RUNS, server, tools_called, wait_job
import test_clean


@pytest.fixture
def splat(tmp_path):
    f = tmp_path / "garden_dslr.ply"
    test_clean.room(f)
    return f


def transforms():
    return [c for c in tools_called() if c[0] == "splat-transform"]


def test_a_splat_becomes_a_scene(api, splat):
    status, r = api.post("/api/import", {"path": str(splat), "name": "Garden"})
    assert status == 200, r
    js = wait_job()
    assert js["result"] == "ok" and js["kind"] == "import", js["tail"]
    run = RUNS / "Garden"
    for f in ("spirula/scene.ply", "scene.sog", "scene.xrSceneTransform.json", "garden.gallery.png", "garden.sceneinfo.png"):
        assert (run / f).exists(), f
    assert json.loads((run / "scene.xrSceneTransform.json").read_text())["worldScale"] == 1.0
    s = {x["slug"]: x for x in server.scenes()}["Garden"]
    assert s["name"] == "Garden" and s["ply"] and s["gaussians"] == len(test_clean.read(run / "spirula" / "scene.ply"))
    assert server.read_meta("Garden")["imported"] == "garden_dslr.ply"
    assert splat.exists() and not (RUNS / "Garden.tmp").exists()            # the file itself stays
    first = transforms()[0]
    assert first[first.index("-w") + 1] == str(splat) and "-r" not in first and "-s" not in first


@pytest.mark.parametrize("up,turn", [("y-up", "180,0,0"), ("z-up", "-90,0,0")])
def test_up_and_size_are_set_right(api, splat, up, turn):
    assert api.post("/api/import", {"path": str(splat), "name": "Garden", "up": up, "scale": "0.01"})[0] == 200
    assert wait_job()["result"] == "ok"
    first = transforms()[0]
    assert first[first.index("-r") + 1] == turn and first[first.index("-s") + 1] == "0.01"


def test_an_imported_scene_goes_to_the_frame(api, splat, frame):
    api.post("/api/import", {"path": str(splat), "name": "Garden"})
    wait_job()
    assert api.post("/api/upload", {"slug": "Garden"})[0] == 200
    end = time.time() + 60
    while (u := server.upload_status("Garden")) and u["running"] and time.time() < end:
        time.sleep(0.2)
    assert server.upload_status("Garden")["ok"], server.upload_status("Garden")
    folder = next((frame / server.FRAME_SCENES).iterdir())
    assert sorted(p.name for p in folder.iterdir()) == [
        "garden.gallery.png", "garden.sceneinfo.png", "garden.sog", "garden.xrSceneTransform.json"]


def test_a_failed_import_frees_the_name(api, tmp_path):
    bad = tmp_path / "broken.ply"
    bad.write_bytes(b"not a splat")
    assert api.post("/api/import", {"path": str(bad), "name": "Broken"})[0] == 200
    assert wait_job()["result"] == "failed"
    server.watch_once(True)
    assert not (RUNS / "Broken").exists() and not server.meta_file("Broken").exists()


@pytest.mark.parametrize("body,code", [
    ({"path": "relative.ply"}, "err.import_file"), ({"path": "/nope/x.ply"}, "err.import_file"),
    ({"suffix": ".txt"}, "err.import_file"), ({"up": "x-up"}, "err.bad_value"), ({"scale": "0"}, "err.range"),
    ({"scale": "abc"}, "err.bad_value"), ({"name": "Taken"}, "err.scene_name_taken")])
def test_odd_imports_are_refused(api, splat, body, code):
    (RUNS / "Taken").mkdir(parents=True)
    path = splat
    if "suffix" in body:
        path = splat.with_suffix(body.pop("suffix"))
        path.write_bytes(b"x")
    status, r = api.post("/api/import", {"path": str(path), "name": "Garden", **body})
    assert status == 400 and r["error"]["code"] == code
    assert server.load_state().get("job") is None


# ---------------------------------------------------------------- which way is up (0.6.1)

def turned(src, dst, up):
    """The room as another program would have saved it: our scene (Y down) turned back into "up"."""
    v = test_clean.read(src).copy()
    x, y, z = v["x"].copy(), v["y"].copy(), v["z"].copy()
    if up == "y-up":
        v["y"], v["z"] = -y, -z                                       # 180° about X
    elif up == "z-up":
        v["y"], v["z"] = z, -y                                        # down (+Y) becomes -Z
    header = "ply\nformat binary_little_endian 1.0\nelement vertex %d\n%send_header\n" % (
        len(v), "".join(f"property float {p}\n" for p in v.dtype.names))
    dst.write_bytes(header.encode() + v.tobytes())
    return dst


def picture(url):
    assert url.startswith("data:image/png;base64,")
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB")


@pytest.mark.parametrize("up", ["y-down", "y-up", "z-up"])
def test_the_dialog_guesses_which_way_is_up(api, splat, tmp_path, up):
    f = splat if up == "y-down" else turned(splat, tmp_path / f"room_{up}.ply", up)
    status, r = api.post("/api/import/preview", {"path": str(f)})
    assert status == 200, r
    assert r["guess"] == up
    assert sorted(r["images"]) == ["y-down", "y-up", "z-up"]
    for u, url in r["images"].items():
        assert picture(url).size == (320, 200), u


def test_the_upright_picture_has_the_floor_below(api, splat, tmp_path):
    # the room's floor is brown, its ceiling nearly white: saved with Y up, the right choice shows brown at
    # the bottom, our usual Y down at the top (the scene on its head)
    f = turned(splat, tmp_path / "room_yup.ply", "y-up")
    imgs = {u: np.asarray(picture(url), np.float32) for u, url in api.post("/api/import/preview", {"path": str(f)})[1]["images"].items()}

    def brown(img):                                                   # floor colour: much more red than blue
        return (img[..., 0] - img[..., 2]) > 40

    up, head = brown(imgs["y-up"]), brown(imgs["y-down"])
    assert up[100:].sum() > 5 * up[:100].sum()
    assert head[:100].sum() > 5 * head[100:].sum()


def test_the_pictures_are_kept_per_file(api, splat, monkeypatch):
    assert api.post("/api/import/preview", {"path": str(splat)})[0] == 200
    calls = []
    real = server.subprocess.run
    monkeypatch.setattr(server.subprocess, "run", lambda *a, **k: calls.append(a) or real(*a, **k))
    assert api.post("/api/import/preview", {"path": str(splat)})[0] == 200
    assert calls == []                                                # the same file: from the cache


def test_a_broken_file_gives_no_pictures(api, tmp_path):
    bad = tmp_path / "broken.ply"
    bad.write_bytes(b"not a splat")
    status, r = api.post("/api/import/preview", {"path": str(bad)})
    assert status == 400 and r["error"]["code"] == "err.import_preview"
    status, r = api.post("/api/import/preview", {"path": "relative.ply"})
    assert status == 400 and r["error"]["code"] == "err.import_file"
