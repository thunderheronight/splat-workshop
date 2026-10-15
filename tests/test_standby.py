"""Standby when everything is done (0.3): after the last job – also after the parts of a long recording
and putting them together – a minute's notice, then the PC suspends. The fake systemctl only notes it."""
import os
import time

import pytest

from conftest import recording, server, tools_called, wait_for_tool, wait_job


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(server, "STANDBY_GRACE", 0)
    yield
    server.release_awake()


def suspends():
    return [c for c in tools_called() if c[0] == "systemctl"]


def test_standby_after_the_job(api):
    assert server.start_job({"path": str(recording()), "name": "Room", "quality": "fast", "upload": False}) is None
    assert api.post("/api/standby", {"on": True})[1]["on"]
    server.standby_tick(busy=True)
    assert not suspends() and server.standby_status()["at"] is None      # nothing while it runs
    wait_job()
    server.standby_tick(busy=False)                                      # all done: the notice
    assert server.standby_status()["at"] and not suspends()
    assert any("standby" in n[-1].lower() for n in wait_for_tool("notify-send"))
    server.standby_tick(busy=False)                                      # the minute is up
    assert suspends() == [["systemctl", "suspend"]]
    assert server.standby_status() == {"on": False, "at": None, "error": None}   # once only
    server.standby_tick(busy=False)
    assert len(suspends()) == 1


def test_work_starting_again_stops_the_countdown():
    server.set_standby(True)
    server.standby_tick(busy=False)
    assert server.standby_status()["at"]
    server.standby_tick(busy=True)                                       # e.g. the next part started
    assert server.standby_status()["at"] is None and server.standby_status()["on"]
    assert not suspends()


def test_switched_off_in_time():
    server.set_standby(True)
    server.standby_tick(busy=False)
    assert server.set_standby(False)["on"] is False
    server.standby_tick(busy=False)
    assert not suspends()


def test_cancelling_a_job_means_someone_is_there():
    os.environ["FAKE_PIPELINE"] = "slow"
    server.start_job({"path": str(recording()), "name": "Room", "quality": "fast", "upload": False})
    server.set_standby(True)
    time.sleep(1)
    server.cancel_job()
    wait_job(30)
    server.standby_tick(busy=False)
    server.standby_tick(busy=False)
    assert not suspends() and not server.standby_status()["on"]


def test_a_refused_standby_is_reported():
    os.environ["FAKE_SUSPEND_FAIL"] = "1"
    server.set_standby(True)
    server.standby_tick(busy=False)
    server.standby_tick(busy=False)
    assert "Steam" in server.standby_status()["error"]
    end = time.time() + 5                       # notifications are sent in the background
    while not any("Steam" in c[-1] for c in tools_called() if c[0] == "notify-send") and time.time() < end:
        time.sleep(0.1)
    assert any("Steam" in c[-1] for c in tools_called() if c[0] == "notify-send")


def test_kept_awake_between_jobs_and_let_go_after(monkeypatch):
    """One lock of the server spans the few seconds between two jobs; it goes a little after the last."""
    monkeypatch.setattr(server, "can_inhibit", lambda: True)
    monkeypatch.setattr(server, "keep_awake", lambda cmd, why: cmd)      # the lock is a process: here `sleep`
    monkeypatch.setattr(server, "AWAKE_AFTER", 0.5)
    server.hold_awake(True)
    p = server.awake["proc"]
    assert p and p.poll() is None
    server.hold_awake(False)                                             # between two jobs: still held
    assert p.poll() is None
    server.hold_awake(True)
    assert server.awake["proc"] is p                                     # the same lock, not a new one
    server.hold_awake(False)
    time.sleep(0.6)
    server.hold_awake(False)
    assert p.poll() is not None and server.awake["proc"] is None


def test_one_notice_after_a_whole_chain_of_parts():
    """Between two parts, and between the last part and putting them together, nothing announces the
    standby: the watcher's tick that starts the next part reports it as running."""
    os.environ["FAKE_PIPELINE"] = "room"
    assert server.start_parts({"path": str(recording("2026-10-14_16-57-54_stereo.mp4")), "name": "Living room",
                               "quality": "high", "parts": 2, "upload": False}) is None
    server.set_standby(True)
    was, ticks = True, 0
    while ticks < 10:
        wait_job()
        running = server.watch_once(was)
        server.standby_tick(running)
        ticks += 1
        if not running:
            break
        assert server.standby_status()["at"] is None
        was = running
    assert server.job_status(tail=False)["kind"] == "combine"
    server.standby_tick(False)
    for p in server.notes:
        p.wait(10)
    notices = [c for c in tools_called() if c[0] == "notify-send" and "standby" in c[-1].lower()]
    assert len(notices) == 1 and suspends() == [["systemctl", "suspend"]]


def test_the_own_lock_goes_before_the_standby(monkeypatch):
    monkeypatch.setattr(server, "can_inhibit", lambda: True)
    monkeypatch.setattr(server, "keep_awake", lambda cmd, why: cmd)
    server.hold_awake(True)
    lock = server.awake["proc"]
    held = []
    real = server.subprocess.run

    def run(cmd, *a, **kw):
        if cmd[:2] == ["systemctl", "suspend"]:
            held.append(lock.poll() is None)
        return real(cmd, *a, **kw)
    monkeypatch.setattr(server.subprocess, "run", run)
    server.set_standby(True)
    server.standby_tick(False)
    server.standby_tick(False)
    assert held == [False] and server.awake["proc"] is None
