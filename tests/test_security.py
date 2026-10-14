"""Who may talk to the server: only this PC (or the home network in LAN mode), never other websites."""
import pytest

from conftest import RUNS, server
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
])
def test_invalid_requests_get_400_not_a_crash(api, body, headers):
    assert api.post("/api/rename", body, headers)[0] == 400
    assert api.get("/api/ping")[0] == 200            # still alive


@pytest.mark.parametrize("slug", ["../runs", "..", "/etc", ".previous", "a/../../b"])
def test_no_paths_outside_the_runs_folder(api, slug):
    synth.make_room(RUNS, "Room", "A")
    assert api.get(f"/api/preview?slug={slug}")[0] == 404
    assert api.post("/api/delete", {"slug": slug})[0] == 400
    assert api.post("/api/clean", {"slug": slug, "what": "temp"})[0] == 400
    assert (RUNS / "Room" / "scene.sog").exists()


def test_actions_on_the_screen_only_from_this_pc():
    # video player, file dialog, folder, Spirula and quitting: not from another device in the LAN
    class Remote(server.Handler):
        def __init__(self):
            self.client_address = ("192.168.1.20", 50000)

    class Local(server.Handler):
        def __init__(self):
            self.client_address = ("127.0.0.1", 50000)
    assert not Remote()._from_this_pc() and Local()._from_this_pc()


def test_unknown_thumbnail_names_leave_no_cache_files(api):
    status, _ = api.get("/api/thumb?source=frame&name=whatever.mp4")
    assert status == 404
    assert not list(server.CACHE.glob("whatever*"))
