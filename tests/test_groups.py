"""Groups of scenes: one group per scene, suggestions, renaming, cleaning up together."""
import json
import pytest

from conftest import RUNS, server
import synth


def make_flat():
    synth.make_room(RUNS, "Kitchen", "A", name="Kitchen", recording="2026-10-12_16-40-00_stereo.mp4")
    synth.make_room(RUNS, "Hallway", "B", name="Hallway", recording="2026-10-12_16-43-00_stereo.mp4")
    synth.make_room(RUNS, "Statue", "A", name="Statue", recording="2026-10-03_19-00-00_stereo.mp4")


def test_rooms_recorded_one_after_another_are_suggested(api):
    make_flat()
    # 25 minutes after the hallway: more than the 20 minutes that still count as one session
    synth.make_room(RUNS, "Garden", "A", name="Garden", recording="2026-10-12_17-08-00_stereo.mp4")
    status, r = api.get("/api/groups")
    assert status == 200 and r["groups"] == []
    assert sorted(r["suggestion"]["members"]) == ["Hallway", "Kitchen"]     # not the statue from another day
    assert r["suggestion"]["date"] == "2026-10-12"
    assert api.post("/api/group/dismiss", {"key": r["suggestion"]["key"]})[0] == 200
    assert api.get("/api/groups")[1]["suggestion"] is None


def test_a_group_with_its_scenes(api):
    make_flat()
    status, r = api.post("/api/group", {"name": "Example home", "members": "Kitchen,Hallway"})
    assert status == 200 and r["slug"] == "Example-home"
    g = api.get("/api/groups")[1]["groups"][0]
    assert (g["name"], g["members"], g["result"]) == ("Example home", ["Kitchen", "Hallway"], None)
    assert "Example-home" not in {s["slug"] for s in api.get("/api/scenes")[1]}   # a group is not a scene
    assert "Example-home" not in {u["slug"] for u in api.get("/api/unfinished")[1]}


def test_a_scene_can_only_be_in_one_group(api):
    make_flat()
    api.post("/api/group", {"name": "Home", "members": "Kitchen,Hallway"})
    status, r = api.post("/api/group", {"name": "Other", "members": "Kitchen,Statue"})
    assert status == 400 and r["error"]["code"] == "err.in_other_group"
    assert api.post("/api/group", {"name": "Empty", "members": ""})[0] == 400
    assert api.post("/api/group", {"name": "Ghost", "members": "Nope"})[0] == 400


def test_renaming_and_changing_a_group(api):
    make_flat()
    api.post("/api/group", {"name": "Home", "members": "Kitchen,Hallway"})
    status, r = api.post("/api/group", {"slug": "Home", "name": "Old flat", "members": "Kitchen"})
    assert status == 200 and r["slug"] == "Old-flat"
    assert not (RUNS / "Home.ui.json").exists()
    g = api.get("/api/groups")[1]["groups"][0]
    assert (g["slug"], g["name"], g["members"]) == ("Old-flat", "Old flat", ["Kitchen"])


def test_renamed_and_deleted_scenes_follow_their_group(api):
    make_flat()
    api.post("/api/group", {"name": "Home", "members": "Kitchen,Hallway"})
    api.post("/api/rename", {"slug": "Kitchen", "new": "Küche"})
    assert json.loads((RUNS / "Home.ui.json").read_text())["members"] == ["Kueche", "Hallway"]
    api.post("/api/delete", {"slug": "Hallway"})
    assert json.loads((RUNS / "Home.ui.json").read_text())["members"] == ["Kueche"]


def test_cleaning_up_a_group_cleans_only_its_scenes(api):
    make_flat()
    for slug in ("Kitchen", "Hallway", "Statue"):
        (RUNS / slug / "spirula" / "training").mkdir(parents=True)
        (RUNS / slug / "spirula" / "training" / "x").write_bytes(b"x" * 1000)
    api.post("/api/group", {"name": "Home", "members": "Kitchen,Hallway"})
    assert api.post("/api/clean", {"slug": "Home", "what": "temp"})[0] == 200
    assert not (RUNS / "Kitchen/spirula/training").exists() and not (RUNS / "Hallway/spirula/training").exists()
    assert (RUNS / "Statue/spirula/training/x").exists()


def test_dissolving_a_group_keeps_its_scenes(api):
    make_flat()
    api.post("/api/group", {"name": "Home", "members": "Kitchen,Hallway"})
    assert api.post("/api/delete", {"slug": "Home"})[0] == 200
    assert api.get("/api/groups")[1]["groups"] == []
    assert {"Kitchen", "Hallway", "Statue"} <= {s["slug"] for s in server.scenes()}


@pytest.mark.parametrize("name,slug", [("Rundgang 12.10.", "Rundgang-12.10"), ("Küche", "Kueche"),
                                       ("...", ""), (".hidden", "hidden"), ("Flur 2", "Flur-2")])
def test_folder_names_suit_every_file_system(name, slug):
    """No dot at the end: NTFS (e.g. runs on a Windows disk) refuses it, and the copy loses the tour."""
    from splat_workshop import config
    assert config.slugify(name) == slug


def test_a_tour_named_with_a_date_gets_a_folder_ntfs_accepts(api):
    synth.make_room(RUNS, "Kitchen", "A", name="Kitchen")
    synth.make_room(RUNS, "Hall", "B", name="Hall")
    status, r = api.post("/api/group", {"name": "Tour 12.10.", "members": "Kitchen,Hall"})
    assert status == 200 and r["slug"] == "Tour-12.10"
