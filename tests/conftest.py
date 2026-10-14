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
RUNS, REC, STATE, BIN, TRASH, PIPE, FRAME_HOME = (BASE / n for n in (
    "runs", "recordings", "state", "bin", "trash", "pipeline", "frame"))
for d in (RUNS, REC, STATE, BIN, TRASH, PIPE, FRAME_HOME):
    d.mkdir()
TOOL_LOG = BASE / "tools.log"              # what the fake tools were called with, one JSON line each


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
# like the real pipeline: an unfinished training blocks --resume ("Incomplete training retained")
run_json = out / "spirula" / "run.json"
if "--resume" in sys.argv and run_json.exists() and json.loads(run_json.read_text()).get("status") != "complete":
    sys.exit("error: Incomplete training retained; use a new output directory to restart")
# like the real one: an existing output folder is only continued with --resume, and only once the camera
# positions are complete ("Incomplete native SfM retained" / "already exists without a matching manifest")
dataset = out / "dataset" / "spirula-dataset.json"
if out.exists() and any(out.iterdir()):
    if "--resume" not in sys.argv:
        sys.exit("error: Spirula SfM output already exists without a matching resume manifest")
    if not dataset.exists() or json.loads(dataset.read_text()).get("status") != "complete":
        sys.exit("error: Incomplete native SfM retained for diagnosis; use a fresh output directory")
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
run_json.write_text(json.dumps({"status": "complete"}))
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
import json, shutil, sys
with open("{TOOL_LOG}", "a") as log:
    log.write(json.dumps(["splat-transform", *sys.argv[1:]]) + "\\n")
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

# Nothing may open on the desktop, even if the code under test is broken: file dialogs, xdg-open (folder,
# video player) only leave a note in the tool log
for tool in ("kdialog", "zenity", "xdg-open"):
    script(BIN / tool, f'''#!{sys.executable}
import json, sys
with open("{TOOL_LOG}", "a") as log:
    log.write(json.dumps(["{tool}", *sys.argv[1:]]) + "\\n")
''')
# notify-send: no desktop notifications from the tests, only a note in the tool log
script(BIN / "notify-send", f'''#!{sys.executable}
import json, sys
with open("{TOOL_LOG}", "a") as log:
    log.write(json.dumps(["notify-send", *sys.argv[1:]]) + "\\n")
''')
# ffprobe: a good Arcturus recording (two video streams, gpmd, 60 s) unless the file name says otherwise:
# "mono" one video stream, "nogpmd" no metadata stream, "short" 5 s, "brief" 15 s
script(BIN / "ffprobe", f'''#!{sys.executable}
import json, sys
name = sys.argv[-1]
video = {{"codec_type": "video", "codec_tag_string": "hvc1", "width": 2464, "height": 2464, "avg_frame_rate": "30/1"}}
streams = [video] if "mono" in name else [video, dict(video)]
if "nogpmd" not in name:
    streams.append({{"codec_type": "data", "codec_tag_string": "gpmd"}})
dur = 5 if "short" in name else 15 if "brief" in name else 60
print(json.dumps({{"streams": streams, "format": {{"duration": str(dur)}}}}))
''')
# ssh / scp: the Frame is a folder (its home directory); FAKE_FRAME_OFF=1 makes it unreachable
script(BIN / "ssh", f'''#!{sys.executable}
import os, subprocess, sys
if os.environ.get("FAKE_FRAME_OFF"):
    sys.exit(255)
args = sys.argv[1:]
while args and args[0].startswith("-"):
    args = args[2:] if args[0] in ("-o", "-p", "-i") else args[1:]
cmd = " ".join(args[1:])
env = {{**os.environ, "HOME": "{FRAME_HOME}"}}
if os.environ.get("FAKE_FRAME_CUT") and cmd.startswith("cat "):   # the connection breaks halfway
    out = subprocess.run(["bash", "-c", cmd], cwd="{FRAME_HOME}", env=env, capture_output=True).stdout
    sys.stdout.buffer.write(out[:len(out) // 2])
    sys.exit(255)
if os.environ.get("FAKE_FRAME_SHORT") and cmd.startswith("cat "):  # ends early, but without an error
    out = subprocess.run(["bash", "-c", cmd], cwd="{FRAME_HOME}", env=env, capture_output=True).stdout
    sys.stdout.buffer.write(out[:len(out) // 2])
    sys.exit(0)
if os.environ.get("FAKE_FRAME_SLOW") and cmd.startswith("cat "):  # a slow connection: 1 kB every 0.1 s
    import time
    out = subprocess.run(["bash", "-c", cmd], cwd="{FRAME_HOME}", env=env, capture_output=True).stdout
    for i in range(0, len(out), 1000):
        sys.stdout.buffer.write(out[i:i + 1000]); sys.stdout.buffer.flush(); time.sleep(0.1)
    sys.exit(0)
sys.exit(subprocess.run(["bash", "-c", cmd or "true"], cwd="{FRAME_HOME}", env=env).returncode)
''')
script(BIN / "scp", f'''#!{sys.executable}
import os, shutil, sys
from pathlib import Path
if os.environ.get("FAKE_FRAME_OFF"):
    sys.exit(255)
args = [a for a in sys.argv[1:] if a not in ("-q",)]
def local(a):
    if ":" in a.split("/", 1)[0]:
        return Path("{FRAME_HOME}") / a.split(":", 1)[1]
    return Path(a)
*src, dst = map(local, args)
if os.environ.get("FAKE_SCP_SLOW"):                       # a slow connection: copying takes 3 s
    import time
    time.sleep(3)
for s in src:
    shutil.copy(s, dst / s.name if dst.is_dir() else dst)
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
    for d in (RUNS, REC, STATE, TRASH, FRAME_HOME):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir()
    for cache in (server.usage_cache, server.probe_cache, server.uploads, server.checks):
        cache.clear()
    if server.download_busy():
        server.cancel_download()
        end = time.time() + 10
        while server.download_busy() and time.time() < end:
            time.sleep(0.05)
    server.download, server.download_queue[:], server.download_batch = None, [], {}
    for k in ("FAKE_PIPELINE", "FAKE_FRAME_OFF", "FAKE_FRAME_CUT", "FAKE_FRAME_SLOW", "FAKE_FRAME_SHORT", "FAKE_SCP_SLOW",
              "FRAME_HOST"):
        os.environ.pop(k, None)
    os.environ["FRAME_HOST"] = ""
    TOOL_LOG.unlink(missing_ok=True)
    server.frame_cache.update(t=0, data=None)


SHUTDOWNS = []                             # calls of server.shutdown during the current test


@pytest.fixture(autouse=True)
def never_exit(monkeypatch):
    """Quitting must never end the test run – a broken guard would otherwise make it stop silently (green)."""
    SHUTDOWNS.clear()
    monkeypatch.setattr(server, "shutdown", lambda cancel: SHUTDOWNS.append(cancel))

    def no_exit(code=0):
        raise RuntimeError(f"os._exit({code}) called during a test")
    monkeypatch.setattr(server.os, "_exit", no_exit)


@pytest.fixture
def frame(monkeypatch):
    """A Steam Frame (the fake one: a folder) is configured and reachable. Returns its home directory."""
    host = "steamos@frame.test"
    monkeypatch.setattr(server, "FRAME", host)
    monkeypatch.setattr(server, "SSH", [*server.SSH[:-1], host])
    os.environ["FRAME_HOST"] = host                  # for the scripts (splat.sh, to-frame.sh)
    for d in (server.FRAME_VIDEOS + "/thumbnails", server.FRAME_SCENES, server.FRAME_STEAM):
        (FRAME_HOME / d).mkdir(parents=True, exist_ok=True)
    return FRAME_HOME


def tools_called():
    """Calls of the fake tools so far, e.g. [["splat-transform", "-w", ...], ...]."""
    return [json.loads(line) for line in TOOL_LOG.read_text().splitlines()] if TOOL_LOG.exists() else []


def wait_for_tool(name, timeout=5):
    """Calls of one fake tool, waiting a little for ones started in the background (notifications)."""
    end = time.time() + timeout
    while time.time() < end:
        calls = [c for c in tools_called() if c[0] == name]
        if calls:
            return calls
        time.sleep(0.1)
    return []


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
