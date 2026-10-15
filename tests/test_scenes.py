"""Finished scenes: renaming, cleaning up, deleting, and runs that did not finish."""
import json

from conftest import RUNS, TRASH, server
import synth


def scene_names():
    return {s["slug"]: s["name"] for s in server.scenes()}


def test_finished_scenes_are_listed(api):
    synth.make_room(RUNS, "Kitchen", "A", name="Kitchen")
    status, scenes = api.get("/api/scenes")
    assert status == 200
    assert [(s["slug"], s["name"], s["ply"]) for s in scenes] == [("Kitchen", "Kitchen", True)]
    assert scenes[0]["gaussians"] > 1000


def test_renaming_moves_folder_files_and_log(api):
    run = synth.make_room(RUNS, "Aufnahme-12.10.-16-40", "A", name="Aufnahme 12.10.")
    (run / "aufnahme-12.10.-16-40.sog").write_bytes(b"x")          # gallery files are named after the folder
    (run / "aufnahme-12.10.-16-40.gallery.png").write_bytes(b"x")
    status, r = api.post("/api/rename", {"slug": "Aufnahme-12.10.-16-40", "new": "Büro 12.10."})
    assert status == 200, r
    new = RUNS / "Buero-12.10"
    assert new.is_dir() and not run.exists()
    assert (new / "buero-12.10.sog").exists() and (new / "buero-12.10.gallery.png").exists()
    assert (RUNS / "Buero-12.10.log").exists() and not (RUNS / "Aufnahme-12.10.-16-40.log").exists()
    meta = json.loads((RUNS / "Buero-12.10.ui.json").read_text())
    assert meta["name"] == "Büro 12.10." and meta["folder_renamed"] is True
    assert server.no_resume("Buero-12.10") == "renamed"        # the pipeline remembers the old path


def test_renaming_to_a_taken_name_is_refused(api):
    synth.make_room(RUNS, "Kitchen", "A")
    synth.make_room(RUNS, "Office", "B")
    status, r = api.post("/api/rename", {"slug": "Office", "new": "Kitchen"})
    assert status == 400 and r["error"]["code"] == "err.scene_name_taken"
    assert (RUNS / "Office").is_dir()


def test_cleaning_up_removes_only_what_it_says(api):
    run = synth.make_room(RUNS, "Kitchen", "A")
    for rel in ("spirula/training/step-1.ckpt", "dataset/.spirula-sfm/x", "dataset/images/cam0/0000.jpg"):
        (run / rel).parent.mkdir(parents=True, exist_ok=True)
        (run / rel).write_bytes(b"x" * 1000)
    assert api.post("/api/clean", {"slug": "Kitchen", "what": "temp"})[0] == 200
    assert not (run / "spirula/training").exists() and not (run / "dataset/.spirula-sfm").exists()
    assert (run / "dataset/images/cam0/0000.jpg").exists()          # training images stay
    assert (run / "spirula/scene.ply").exists() and (run / "dataset/sparse/0/images.bin").exists()
    assert server.no_resume("Kitchen") is None
    assert api.post("/api/clean", {"slug": "Kitchen", "what": "images"})[0] == 200
    assert not (run / "dataset/images").exists()
    assert server.no_resume("Kitchen") == "images"                 # only a fresh start from now on
    assert api.post("/api/clean", {"slug": "Kitchen", "what": "everything"})[0] == 400


def test_deleting_moves_to_the_trash(api):
    synth.make_room(RUNS, "Kitchen", "A")
    assert api.post("/api/delete", {"slug": "Kitchen"})[0] == 200
    assert "Kitchen" not in scene_names()
    assert {p.name for p in TRASH.iterdir()} >= {"Kitchen", "Kitchen.log", "Kitchen.ui.json"}


def test_unfinished_runs_resume_or_start_over(api):
    # interrupted in the training: camera positions done -> it can continue
    (RUNS / "Hallway" / "dataset").mkdir(parents=True)
    (RUNS / "Hallway" / "dataset" / "spirula-dataset.json").write_text('{"status": "complete"}')
    (RUNS / "Hallway.log").write_text("Running native Spirula SfM\nstep  13801/30000 ( 46%)  splats 2000000  "
                                      "[elapsed 00:10:00.0 | ETA 00:12:00.0]  rgb_loss=0.1 ssim=0.7 psnr=25\n")
    rec = "2026-10-12_16-43-00_stereo.mp4"
    (RUNS / "Hallway.ui.json").write_text(json.dumps({"name": "Hallway", "recording": rec}))
    (server.REC / rec).write_bytes(b"x")
    # stopped while extracting images -> only from the start
    (RUNS / "Broken").mkdir()
    (RUNS / "Broken.log").write_text("Extraction progress: decoded 4% / saved 44 of 1000\n")
    (RUNS / "Broken.ui.json").write_text(json.dumps({"name": "Broken", "recording": rec}))
    (RUNS / "old-test.log").write_text("something\n")                # only a log file left
    synth.make_room(RUNS, "Kitchen", "A", name="Kitchen")            # finished: not in this list

    status, runs = api.get("/api/unfinished")
    assert status == 200
    by = {r["slug"]: r for r in runs}
    assert set(by) == {"Hallway", "Broken", "old-test"}
    assert (by["Hallway"]["phase"], by["Hallway"]["pct"], by["Hallway"]["resumable"]) == (2, 46, True)
    assert (by["Broken"]["phase"], by["Broken"]["resumable"], by["Broken"]["restartable"]) == (0, False, True)
    assert by["old-test"]["phase"] is None and not by["old-test"]["restartable"]
    assert api.post("/api/resume", {"slug": "Broken"})[1]["error"]["code"] == "err.not_resumable"
    assert api.post("/api/delete", {"slug": "old-test"})[0] == 200
    assert not (RUNS / "old-test.log").exists()
