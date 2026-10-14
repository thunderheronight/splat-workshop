"""Test setup: a temporary home for Splat Workshop with fake tools, and the server on a free port.

The real Arcturus pipeline, Spirula, splat-transform and the desktop trash are replaced by small fakes
(written below), so the tests run anywhere – no GPU, no recordings, no Steam Frame. The server reads
its configuration when it is imported, so everything is set up before that, once per test session.
"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(Path(__file__).parent)]

BASE = Path(tempfile.mkdtemp(prefix="splat-workshop-tests-"))
RUNS, REC, STATE, BIN, TRASH, PIPE = (BASE / n for n in ("runs", "recordings", "state", "bin", "trash", "pipeline"))
for d in (RUNS, REC, STATE, BIN, TRASH, PIPE):
    d.mkdir()


def script(path: Path, text: str):
    path.write_text(text)
    path.chmod(0o755)


# Fake pipeline: behaves like av1_to_3dgs.py as far as Splat Workshop can see (progress lines, result files).
# FAKE_PIPELINE=ok (default) | fail | slow (waits for 60 s, to be cancelled)
script(PIPE / "av1_to_3dgs.py", r'''
import base64, json, os, sys, time
from pathlib import Path
out = Path(sys.argv[2])
mode = os.environ.get("FAKE_PIPELINE", "ok")
(out / "dataset").mkdir(parents=True, exist_ok=True)
print("Extracting 20 images from 2 video streams", flush=True)
(out / "dataset" / "spirula-dataset.json").write_text(json.dumps({"status": "complete"}))
print("Running native Spirula SfM", flush=True)
print("Aligned 20 cameras using orientation anchor cam0/0000.jpg: RMS 0.0110 m, median 0.0090 m.", flush=True)
if mode == "fail":
    sys.exit("fake pipeline: failed on purpose")
for step in (1000, 2000, 3000):
    print(f"step {step:6d}/3000 ({step // 30:3d}%)  splats 1000  [elapsed 00:00:0{step // 1000}.000 | ETA 00:00:01.000]  rgb_loss=0.1  ssim=0.8  psnr=28.5", flush=True)
    if mode == "slow":
        time.sleep(20)
(out / "spirula").mkdir(exist_ok=True)
(out / "spirula" / "scene.ply").write_bytes(b"ply\n")
print("spirula: 1,000 Gaussians in " + str(out / "spirula" / "scene.ply"), flush=True)
(out / "scene.sog").write_bytes(b"fake sog")
(out / "scene.xrSceneTransform.json").write_text(json.dumps({"transform": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]}))
png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
(out / (out.name.lower() + ".gallery.png")).write_bytes(png)
print("Gaussian XR scene transform: " + str(out / "scene.xrSceneTransform.json"), flush=True)
''')
script(BIN / "spirula", "#!/bin/sh\nexit 0\n")
# splat-transform: copies the input (a .ply stays a .ply), writes a small file for .sog
script(BIN / "splat-transform", f'''#!{sys.executable}
import shutil, sys
args = [a for a in sys.argv[1:] if a not in ("-w", "--overwrite")]
if "--decimate" in args:
    i = args.index("--decimate"); del args[i:i + 2]
src, dst = args[0], args[-1]
if dst.endswith(".ply"):
    shutil.copy(src, dst)
else:
    open(dst, "wb").write(b"fake sog")
print("done")
''')
# gio trash: moves into a trash folder of the test (numbered if a name repeats)
script(BIN / "gio", f'''#!/bin/sh
[ "$1" = trash ] || exit 1
shift; [ "$1" = -- ] && shift
exec mv --backup=numbered -t "{TRASH}" -- "$@"
''')

(BASE / "config.toml").write_text(f'''
[paths]
pipeline = "{PIPE}"
spirula = "{BIN / 'spirula'}"
splat_transform = "{BIN / 'splat-transform'}"
recordings = "{REC}"
runs = "{RUNS}"
extra_recording_dirs = []
[server]
language = "en"
[frame]
host = ""
''')
os.environ.update(SPLAT_WORKSHOP_CONFIG=str(BASE / "config.toml"), SPLAT_WORKSHOP_STATE=str(STATE), FRAME_HOST="",
                  PATH=f"{BIN}:{os.environ['PATH']}")
os.environ.pop("SPLAT_LAN", None)

from splat_workshop import server  # noqa: E402  (after the environment is set up)


@pytest.fixture(autouse=True)
def clean():
    """Every test starts with empty runs, recordings, state and trash."""
    yield
    st = server.load_state().get("job") or {}
    if server.pid_alive(st.get("pid", 0)):
        server.cancel_job()
        wait_job(timeout=20)
    for d in (RUNS, REC, STATE, TRASH):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir()
    for cache in (server.usage_cache, server.probe_cache, server.uploads, server.checks):
        cache.clear()
    os.environ.pop("FAKE_PIPELINE", None)


@pytest.fixture
def ok_probe(monkeypatch):
    """Recordings are fake files: let the recording check (ffprobe) accept them."""
    monkeypatch.setattr(server, "probe", lambda path: {"ok": True, "errors": [], "warnings": [], "duration": 60})


def recording(name="2026-10-12_16-40-00_stereo.mp4"):
    p = REC / name
    p.write_bytes(b"not really a video")
    return p


def wait_job(timeout=120):
    """Wait until the current job has ended; returns its status."""
    end = time.time() + timeout
    while time.time() < end:
        js = server.job_status(tail=True)
        if js and not js["running"]:
            return js
        time.sleep(0.3)
    raise TimeoutError("job did not finish")


class Client:
    def __init__(self, port):
        self.port = port

    def request(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
        c.request(method, path, body=data, headers={"Content-Type": "application/json", **(headers or {})})
        r = c.getresponse()
        raw = r.read()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def get(self, path, headers=None):
        return self.request("GET", path, headers=headers)

    def post(self, path, body=None, headers=None):
        return self.request("POST", path, body if body is not None else {}, headers)


@pytest.fixture(scope="session")
def api():
    srv = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield Client(srv.server_address[1])
    srv.shutdown()


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(BASE, ignore_errors=True)
