"""Is there a newer version? (0.5.1, asked in issue #2): once a day the server asks GitHub for the newest
release – in the background, switched off with check_updates = false – and the page shows a note."""
import io
import json
import time

import pytest

from conftest import server


@pytest.fixture
def github(monkeypatch):
    """A fake api.github.com: answers with `reply`, counts the requests."""
    seen = {"n": 0, "reply": {"tag_name": "v9.1.0", "html_url": "https://github.com/thunderheronight/splat-workshop/releases/tag/v9.1.0"}}

    def urlopen(req, timeout=None):
        seen["n"] += 1
        seen["url"], seen["headers"] = req.full_url, dict(req.header_items())
        if seen["reply"] is None:
            raise OSError("offline")
        return io.BytesIO(json.dumps(seen["reply"]).encode())
    monkeypatch.setattr(server.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(server.CFG, "check_updates", True)
    server.UPDATE_FILE.unlink(missing_ok=True)
    yield seen
    server.UPDATE_FILE.unlink(missing_ok=True)


def ask():
    server.ask_for_update()
    end = time.time() + 5
    while server.update_lock.locked() and time.time() < end:
        time.sleep(0.02)


def test_a_newer_release_is_shown(github, api):
    ask()
    assert github["url"] == "https://api.github.com/repos/thunderheronight/splat-workshop/releases/latest"
    assert "Cookie" not in github["headers"] and "Authorization" not in github["headers"]
    info = api.get("/api/ping")[1]["update"]
    assert info == {"latest": "9.1.0", "current": server.VERSION,
                    "url": "https://github.com/thunderheronight/splat-workshop/releases/tag/v9.1.0"}


def test_asked_at_most_once_a_day(github):
    ask()
    ask()
    assert github["n"] == 1
    data = json.loads(server.UPDATE_FILE.read_text())
    data["checked"] -= server.UPDATE_EVERY + 1
    server.UPDATE_FILE.write_text(json.dumps(data))
    ask()
    assert github["n"] == 2


@pytest.mark.parametrize("tag", ["v" + server.VERSION, "v0.1.0", "nonsense", None])
def test_nothing_shown_for_the_same_an_older_or_an_odd_release(github, tag):
    github["reply"] = {"tag_name": tag, "html_url": "https://github.com/thunderheronight/splat-workshop/releases"}
    ask()
    assert server.update_info() is None


def test_offline_is_no_problem(github):
    github["reply"] = None
    ask()
    assert server.update_info() is None and github["n"] == 1        # and not asked again right away
    ask()
    assert github["n"] == 1


def test_a_link_elsewhere_is_not_shown(github):
    github["reply"] = {"tag_name": "v9.1.0", "html_url": "https://evil.example/download"}
    ask()
    assert server.update_info()["url"] == "https://github.com/thunderheronight/splat-workshop/releases/latest"


def test_switched_off_never_asks(github, monkeypatch):
    monkeypatch.setattr(server.CFG, "check_updates", False)
    ask()
    assert github["n"] == 0 and server.update_info() is None


def test_versions_compare_as_numbers():
    assert server.version_tuple("v0.10.0") > server.version_tuple("0.9.9")
    assert server.version_tuple("0.5.0-experimental") == (0, 5, 0) and server.version_tuple("x") == ()
