"""A mesh of a scene (0.5, scripts/mesh.sh + scripts/upright.py): made by Spirula Studio (faked here, see
tests/fake_mesh.py), stood upright with the floor at height 0 – Z up for OBJ/PLY/STL, Y up for GLB, as 3D
programs expect – instead of lying on its side as the scene does (Y down, like the Frame's tracking)."""
import json
import shutil
import subprocess
import sys

import numpy as np
import pytest

from conftest import ROOT, RUNS, TRASH, server, tools_called, wait_job
import fake_mesh
import test_clean

UPRIGHT = ROOT / "scripts" / "upright.py"


@pytest.mark.parametrize("fmt,colour", [("obj", "none"), ("ply", "vertex"), ("ply", "none"), ("stl", "none"), ("glb", "vertex")])
def test_upright_puts_the_floor_at_the_bottom(tmp_path, fmt, colour):
    f = tmp_path / f"room.{fmt}"
    fake_mesh.write(f, fmt, colour)
    r = subprocess.run([sys.executable, str(UPRIGHT), "--floor", str(fake_mesh.FLOOR_Y), str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    p = fake_mesh.read_points(f)
    up = 1 if fmt in ("glb", "obj") else 2               # glTF and OBJ: Y up; PLY and STL: Z up (as Blender imports them)
    k = 6 if fmt == "stl" else 4                         # STL lists the corners of each triangle: 2 floor triangles
    floor, ceiling = p[:k, up], p[k:, up]
    assert np.allclose(floor, 0, atol=1e-5)              # the floor at height 0 ...
    assert np.allclose(ceiling, fake_mesh.FLOOR_Y - fake_mesh.CEILING_Y, atol=1e-5)   # ... the ceiling 2.5 m above
    if fmt in ("obj", "ply"):                            # a proper turn, not a mirror: x stays
        assert np.allclose(p[:, 0], fake_mesh.V[:, 0])
    if fmt == "ply":                                     # normals turn along: the floor faces up
        raw = f.read_bytes()
        end = raw.index(b"end_header\n") + len(b"end_header\n")
        dt = np.dtype([("p", "<f4", 3), ("n", "<f4", 3)] + ([("c", "u1", 3)] if colour == "vertex" else []))
        n = np.frombuffer(raw, dt, count=8, offset=end)["n"]
        assert np.allclose(n[:4], [0, 0, 1]) and np.allclose(n[4:], [0, 0, -1])
        assert raw.endswith(b"".join(np.array([3], np.uint8).tobytes() + fake_mesh.F[i].astype("<i4").tobytes()
                                     for i in range(len(fake_mesh.F))))       # faces untouched


def scene(slug="Living", images=False):
    run = RUNS / slug
    test_clean.room(run / "spirula" / "scene.ply")
    (run / "scene.sog").write_bytes(b"sog")
    if images:
        (run / "dataset" / "images").mkdir(parents=True)
        (run / "dataset" / "sparse").mkdir()
    server.write_meta(slug, name="Living room")
    return run


def mesh_calls():
    return [c for c in tools_called() if c[:2] == ["spirula", "mesh"]]


def test_a_mesh_for_blender_and_3d_printing(api):
    run = scene()
    m = server.mesh_of("Living")
    assert {k: m[k] for k in ("texture_possible", "files", "made")} == {"texture_possible": False, "files": [], "made": None}
    status, r = api.post("/api/mesh", {"slug": "Living", "formats": "glb,stl", "texture": True})
    assert status == 200, r
    js = wait_job()
    assert js["result"] == "ok" and js["kind"] == "mesh", js["tail"]
    m = server.mesh_of("Living")
    assert [f["name"] for f in m["files"]] == ["living.glb", "living.stl"]     # one file per format, nothing else
    assert m["made"]["formats"] == ["glb", "stl"] and m["made"]["texture"] is False and not m["made"]["stale"]
    call = mesh_calls()[0]
    assert "--no-data" in call                                               # no training images: no texture
    assert call[call.index("--color") + 1] == "none,vertex"                  # GLB with colours, STL without
    assert call[2].endswith("inside.ply")                                    # meshed from inside the room only
    assert np.allclose(fake_mesh.read_points(run / "mesh" / "living.stl")[:, 2].min(), 0, atol=0.05)
    assert np.allclose(fake_mesh.read_points(run / "mesh" / "living.glb")[:, 1].min(), 0, atol=0.05)
    assert not (run / "mesh.tmp").exists()
    assert [(s["slug"], s["mesh"]) for s in server.scenes()] == [("Living", True)]


def test_a_texture_when_the_training_images_are_there(api):
    run = scene(images=True)
    assert server.mesh_of("Living")["texture_possible"]
    api.post("/api/mesh", {"slug": "Living", "formats": "obj,ply", "texture": True})
    assert wait_job()["result"] == "ok"
    call = mesh_calls()[0]
    assert call[call.index("--data") + 1] == str(run / "dataset")
    assert call[call.index("--color") + 1] == "texture,vertex"
    names = [f["name"] for f in server.mesh_of("Living")["files"]]
    assert names == ["living.ply", "living_texture.obj", "living_texture.png"]    # a texture keeps its names


def test_a_new_mesh_replaces_the_old_and_can_be_deleted(api):
    run = scene()
    api.post("/api/mesh", {"slug": "Living", "formats": "glb,stl"})
    wait_job()
    api.post("/api/mesh", {"slug": "Living", "formats": "obj"})
    wait_job()
    assert [f["name"] for f in server.mesh_of("Living")["files"]] == ["living.obj"]
    assert api.post("/api/mesh/delete", {"slug": "Living"})[0] == 200
    assert not (run / "mesh").exists() and (TRASH / "mesh").exists()
    assert api.post("/api/mesh/delete", {"slug": "Living"})[1]["error"]["code"] == "err.no_mesh"


@pytest.mark.parametrize("body,code", [({"formats": "fbx"}, "err.bad_value"), ({"formats": ""}, "err.bad_value"),
                                       ({"slug": "Nope", "formats": "glb"}, "err.no_scene"),
                                       ({"slug": "../x", "formats": "glb"}, "err.no_scene")])
def test_odd_meshes_are_refused(api, body, code):
    scene()
    status, r = api.post("/api/mesh", {"slug": "Living", **body})
    assert status == 400 and r["error"]["code"] == code
    assert not mesh_calls()


def test_mesh_sh_refuses_unknown_formats():
    run = scene()
    r = subprocess.run([str(ROOT / "scripts" / "mesh.sh"), str(run), "glb,fbx"], capture_output=True, text=True)
    assert r.returncode == 1 and "Unknown mesh format: fbx" in r.stderr and not mesh_calls()


def test_mesh_files_can_be_downloaded(api):
    run = scene()
    api.post("/api/mesh", {"slug": "Living", "formats": "glb,stl"})
    wait_job()
    import urllib.parse
    status, body, headers = api.raw("/api/mesh/file?slug=Living&name=living.glb")
    assert status == 200 and body == (run / "mesh" / "living.glb").read_bytes()
    assert headers["Content-Type"] == "model/gltf-binary" and "attachment" in headers["Content-Disposition"]
    for bad in ("../scene.sog", "mesh.json", ".hidden", "living.sog", "", "/etc/passwd"):
        assert api.raw("/api/mesh/file?slug=Living&name=" + urllib.parse.quote(bad))[0] == 404, bad
    assert api.raw("/api/mesh/file?slug=..&name=living.glb")[0] == 404


def test_open_in_blender_imports_the_mesh(api, monkeypatch):
    run = scene()
    api.post("/api/mesh", {"slug": "Living", "formats": "stl,glb"})
    wait_job()
    started = []
    monkeypatch.setattr(server, "blender_cmd", lambda: ["flatpak", "run", "org.blender.Blender"])
    monkeypatch.setattr(server.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    assert api.post("/api/open", {"slug": "Living", "what": "blender"})[0] == 200
    cmd = started[-1]
    assert cmd[:3] == ["flatpak", "run", "org.blender.Blender"] and cmd[3] == "--python-expr"
    assert "import_scene.gltf" in cmd[4] and repr(str(run / "mesh" / "living.glb")) in cmd[4]   # GLB first
    assert "bpy.app.timers.register" in cmd[4]           # not right at start-up: Blender's window isn't ready then
    compile(cmd[4], "blender", "exec")                   # valid Python
    monkeypatch.setattr(server, "blender_cmd", lambda: None)
    status, r = api.post("/api/open", {"slug": "Living", "what": "blender"})
    assert status == 400 and r["error"]["code"] == "err.no_blender"
