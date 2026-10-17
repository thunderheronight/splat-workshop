"""Who may talk to the server: only this PC (or the home network in LAN mode), never other websites."""
import pytest

from conftest import RUNS, SHUTDOWNS, server
import synth


def test_page_and_api_answer_locally(api):
    assert api.get("/")[0] == 200
    status, ping = api.get("/api/ping")
    assert status == 200 and ping["app"] == "splat-workshop" and ping["version"] == server.VERSION


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_other_websites_are_refused(api, site):
    # e.g. <img src="http://127.0.0.1:8771/api/floorplan?..."> on any website, or another port on localhost
    assert api.get("/api/scenes", {"Sec-Fetch-Site": site})[0] == 403
    assert api.post("/api/cancel", {}, {"Sec-Fetch-Site": site})[0] == 403


def test_own_page_and_typed_address_work(api):
    assert api.get("/api/scenes", {"Sec-Fetch-Site": "same-origin"})[0] == 200
    assert api.get("/api/scenes", {"Sec-Fetch-Site": "none"})[0] == 200


def test_a_link_may_open_the_page_but_not_the_api(api):
    nav = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}
    assert api.get("/", nav)[0] == 200
    assert api.get("/api/scenes", nav)[0] == 403


def test_post_from_another_origin_is_refused(api):
    assert api.post("/api/cancel", {}, {"Origin": "http://evil.example"})[0] == 403


def test_dns_rebinding_is_refused(api):
    # a foreign host name that resolves to 127.0.0.1
    assert api.get("/api/scenes", {"Host": "evil.example:8771"})[0] == 403


@pytest.mark.parametrize("lan,client,ok", [
    (False, "127.0.0.1", True), (False, "192.168.1.20", False),
    (True, "192.168.1.20", True), (True, "10.8.0.2", True),
    (True, "100.100.1.1", False),          # carrier-grade NAT / Tailscale: not a home network
    (True, "84.150.3.3", False),           # the internet, e.g. through a port forwarding
])
def test_lan_mode_checks_the_real_client_address(monkeypatch, lan, client, ok):
    monkeypatch.setattr(server.CFG, "lan", lan)

    class H(server.Handler):
        def __init__(self):                 # no socket: only what _host_ok looks at
            self.client_address = (client, 50000)
            self.headers = {"Host": "192.168.1.5:8771" if lan else "127.0.0.1:8771"}
            self.command, self.path = "GET", "/api/scenes"
    assert H()._host_ok() is ok


@pytest.mark.parametrize("body,headers", [
    (b"[1, 2]", {}),                                   # a list instead of an object
    (b'{"name": 5, "new": "x"}', {}),                  # a number instead of text
    (b'{"slug": {"a": 1}}', {}),                       # nested objects
    (b"{not json", {}),
    (b"", {"Content-Length": "-5"}),
    (b"", {"Content-Length": str(10 ** 9)}),
    (b'{"slug": "x", "full": {"a": 1}}', {}),          # nested value in a field that is not text
])
def test_invalid_requests_get_400_not_a_crash(api, body, headers):
    assert api.post("/api/rename", body, headers)[0] == 400
    assert api.get("/api/ping")[0] == 200            # still alive


@pytest.fixture
def leak():
    """Something that looks like a scene one level above the runs folder – it must never be reachable."""
    above = RUNS.parent
    (above / "spirula").mkdir(exist_ok=True)
    (above / "spirula" / "scene.ply").write_bytes(b"ply\n")
    (above / "leak.gallery.png").write_bytes(b"secret")
    (above / "scene.sog").write_bytes(b"secret")
    yield
    for f in ("leak.gallery.png", "scene.sog"):
        (above / f).unlink(missing_ok=True)
    (above / "spirula" / "scene.ply").unlink(missing_ok=True)
    (above / "spirula").rmdir()


@pytest.mark.parametrize("slug", ["..", "../runs", "/etc", ".previous", "a/../../b", "."])
def test_no_paths_outside_the_runs_folder(api, leak, slug):
    synth.make_room(RUNS, "Room", "A")
    assert server.scene_dir(slug) is None
    status, body = api.get(f"/api/preview?slug={slug}")
    assert status == 404 and body != b"secret"
    assert api.get(f"/api/floorplan?slugs={slug}")[0] == 400
    for path, body in (("/api/delete", {"slug": slug}), ("/api/clean", {"slug": slug, "what": "temp"}),
                       ("/api/rename", {"slug": slug, "new": "x"}), ("/api/upload", {"slug": slug}),
                       ("/api/combine", {"slug": slug})):
        assert api.post(path, body)[0] == 400, path
    assert (RUNS / "Room" / "scene.sog").exists() and (RUNS.parent / "leak.gallery.png").exists()


@pytest.mark.parametrize("path,body", [
    ("/api/open", {"slug": "Room", "what": "folder"}), ("/api/play", {"name": "x.mp4"}),
    ("/api/pick", {}), ("/api/shutdown", {}), ("/api/import", {"path": "/x.ply"}), ("/api/import/preview", {"path": "/x.ply"}),
])
def test_actions_on_the_screen_only_from_this_pc(api, monkeypatch, path, body):
    # video player, file dialog, folder, Spirula and quitting: not from another device in the LAN
    monkeypatch.setattr(server.Handler, "_from_this_pc", lambda self: False)
    started = []
    monkeypatch.setattr(server.subprocess, "Popen", lambda *a, **k: started.append(a))
    monkeypatch.setattr(server, "pick_file", lambda: started.append("file dialog"))
    synth.make_room(RUNS, "Room", "A")
    status, r = api.post(path, body)
    assert status == 403 and r["error"]["code"] == "err.pc_only"
    assert started == [] and SHUTDOWNS == []                       # and nothing happened


def test_from_this_pc_means_loopback():
    class Remote(server.Handler):
        def __init__(self):
            self.client_address = ("192.168.1.20", 50000)

    class Local(server.Handler):
        def __init__(self):
            self.client_address = ("127.0.0.1", 50000)
    assert not Remote()._from_this_pc() and Local()._from_this_pc()


ENDPOINTS = ["/api/start", "/api/rename", "/api/clean", "/api/delete", "/api/upload", "/api/download", "/api/check",
             "/api/group", "/api/group/dismiss", "/api/combine", "/api/resume", "/api/versions", "/api/scale",
             "/api/frame/remove", "/api/frame/move", "/api/recording/delete", "/api/version/delete",
             "/api/download/cancel"]


@pytest.mark.parametrize("path", ENDPOINTS)
@pytest.mark.parametrize("field", ["name", "new", "slug", "path", "what", "members", "key", "align", "quality",
                                   "existing", "partition", "version", "sizes", "scales", "folder", "names", "place",
                                   "where"])
def test_wrongly_typed_fields_get_400_everywhere(api, path, field):
    for bad in (5, ["a"], True):
        assert api.post(path, {field: bad})[0] == 400, (path, field, bad)


def test_unknown_thumbnail_names_leave_no_cache_files(api):
    status, _ = api.get("/api/thumb?source=frame&name=whatever.mp4")
    assert status == 404
    assert not list(server.CACHE.glob("whatever*"))


def test_nested_values_are_refused_in_any_field(api):
    # a request that would work, apart from one nested value in a field that is not text
    synth.make_room(RUNS, "Room", "A")
    assert api.post("/api/rename", {"slug": "Room", "new": "Other", "update_frame": {"a": 1}})[0] == 400
    assert (RUNS / "Room").is_dir() and not (RUNS / "Other").exists()
