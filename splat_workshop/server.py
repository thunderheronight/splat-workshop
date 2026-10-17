"""Splat Workshop: local web interface for scripts/splat.sh.

Starts splat.sh as its own process group (it keeps running when the browser – or this server –
is closed), follows its output and shows phase, progress and result. Python standard library only.

Listens on 127.0.0.1, or in the home network when [server] lan = true (only private addresses).
Without the tray icon (--no-idle-exit) it stops after idle_minutes with nothing running and no page open.

Start: ./splat-workshop  (or python3 -m splat_workshop.server)
"""
import argparse
import functools
import hashlib
import http.server
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

from . import check, config
from .i18n import T, detect

VERSION = "0.5.0"
CFG = config.load()
ROOT = config.ROOT
WEB = ROOT / "web"
SCRIPTS = ROOT / "scripts"
REC = CFG.recordings
RUNS = CFG.runs
STATE_DIR = CFG.state_dir
JOBS = STATE_DIR / "jobs"
CACHE = STATE_DIR / "cache"
STATE = STATE_DIR / "state.json"

FRAME = CFG.frame_host
FRAME_VIDEOS = "Videos/ArcturusVisionCamera"
FRAME_SCENES = "Documents/GaussianXR"         # Gaussian XR's personal scenes: Steam updates leave them alone
# The gallery that comes with Gaussian XR: its examples, and scenes copied by Splat Workshop 0.2.3 and older
FRAME_STEAM = ".local/share/Steam/steamapps/common/ArcturusVisionGaussianXR/Scenes"
FRAME_PLACES = {"own": FRAME_SCENES, "steam": FRAME_STEAM}
FRAME_BACKUP = "Documents/GaussianSplats"     # copies made by versions up to 0.2.3 – removed along with a scene
SSH = ["ssh", "-o", "ConnectTimeout=4", "-o", "BatchMode=yes", FRAME]
HOSTNAME = socket.gethostname().lower()
# Names the server may be reached under (protects against DNS rebinding); IP addresses always work
HOST_NAMES = {"localhost", HOSTNAME} | {f"{HOSTNAME}.{d}" for d in ("local", "lan", "home", "fritz.box")}
QUALITIES = ("fast", "high", "max")

t = T(detect(CFG.language))
lock = threading.Lock()
meta_lock = threading.Lock()         # read-modify-write of the *.ui.json files
plan_slots = threading.BoundedSemaphore(2)   # floor plans drawn at the same time
last_request = time.time()
idle_exit = True
proc = None                        # Popen of the running splat.sh (if started by this server)
download = None                    # file being fetched: {"name", "total", "done", "error", "finished"}
download_queue = []                # files waiting, fetched one after the other: [{"name", "size"}]
download_batch = {}                # since the queue was last empty: {"files", "bytes", "done_files", "done_bytes", "failed"}
download_proc = None               # ssh of the file being fetched (to cancel it)
uploads = {}                       # slug -> {"proc", "out", "started", "notified"}
frame_cache = {"t": 0, "data": None}
probe_cache = {}
picked = set()                     # recordings chosen in the file dialog outside the usual folders


# ---------------------------------------------------------------- helpers

def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))            # routing lookup only, nothing is sent
            return s.getsockname()[0]
    except OSError:
        return None


def host_allowed(host: str) -> bool:
    host = host.strip("[]").lower()
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or (CFG.lan and ip.is_private)
    except ValueError:
        return host == "localhost" or (CFG.lan and host in HOST_NAMES)


def pretty_date(stem: str) -> str:
    """ISO date and time from a recording name (the browser formats it)."""
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)_(\d\d)-(\d\d)", stem)
    return f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}" if m else ""


notes = []                         # notify-send processes still running (the tests wait for them)


def notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        notes[:] = [p for p in notes if p.poll() is None]
        notes.append(subprocess.Popen(["notify-send", "-a", t("app"), "-i", "applications-graphics", title, body],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))


@functools.cache
def can_inhibit() -> bool:
    """systemd-inhibit exists and logind accepts the lock (not the case in some containers)."""
    try:
        return subprocess.run(["systemd-inhibit", "--what=sleep", "--why=test", "true"], timeout=5,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def keep_awake(cmd: list, why: str) -> list:
    """Run cmd with a sleep inhibitor, so the PC does not suspend in the middle of it.
    The lock belongs to the child process: it lasts exactly as long as the job, even if this server stops."""
    if not can_inhibit():
        return cmd
    return ["systemd-inhibit", "--what=sleep", f"--who={t('app')}", f"--why={why}", "--mode=block", *cmd]


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def write_json(path: Path, data, indent=None) -> None:
    """Write via a temporary file, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent))
    tmp.replace(path)


def save_state(st: dict) -> None:
    write_json(STATE, st, indent=1)


def pid_alive(pid: int) -> bool:
    """Is the job started as `pid` still running? The job is its whole process group: the first process
    (systemd-inhibit, if the PC is kept awake) can end before splat.sh has finished cleaning up."""
    if proc is not None and proc.pid == pid:
        proc.poll()                             # reap it, so a finished first process does not count
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
        if cmd:
            return any(x in cmd for x in ("splat.sh", "combine.sh", "versions.sh", "mesh.sh"))
    except OSError:
        pass
    if not pid:
        return False
    try:
        os.killpg(pid, 0)                       # the first process is gone, are others of the job left?
        return True
    except (ProcessLookupError, PermissionError):
        return False


def shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def err(code: str, **params) -> dict:
    """Error for the web interface: a key from web/i18n.js plus parameters."""
    return {"code": code, "params": params}


# ---------------------------------------------------------------- recordings

def frame_listing(force=False):
    """Recordings on the Frame (cached). None = no Frame configured or not reachable.
    An expired listing is renewed in the background and the last one answered meanwhile: asking a Frame
    that is switched off takes seconds, and the scene list (which asks too) would hang that long."""
    if not FRAME:
        return None
    if not force and time.time() - frame_cache["t"] < 20:
        return frame_cache["data"]
    if not force and frame_cache["t"]:
        if not frame_cache.get("renewing"):
            frame_cache["renewing"] = True
            threading.Thread(target=renew_frame_listing, daemon=True).start()
        return frame_cache["data"]
    return read_frame_listing()


def renew_frame_listing():
    try:
        read_frame_listing()
    finally:
        frame_cache["renewing"] = False


def read_frame_listing():
    cmd = (f'(cd {FRAME_VIDEOS} 2>/dev/null && stat -c "%n|%s|%Y" *_stereo.mp4 2>/dev/null); echo ---; '
           f'ls {FRAME_VIDEOS}/thumbnails 2>/dev/null; echo ---; (ls {FRAME_SCENES}; ls {FRAME_STEAM}) 2>/dev/null')
    try:
        r = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        r = None
    if r is None or r.returncode == 255:
        data = None
    else:
        files, _, rest = r.stdout.partition("---\n")
        thumbs, _, scenes_ = rest.partition("---\n")
        data = {"files": [], "thumbs": thumbs.split(), "scenes": scenes_.split()}
        for line in files.splitlines():
            parts = line.rsplit("|", 2)
            if len(parts) == 3:
                data["files"].append({"name": parts[0], "size": int(parts[1]), "mtime": int(parts[2])})
    frame_cache.update(t=time.time(), data=data)
    return data


RECMETA = STATE_DIR / "recordings.json"   # renamed recordings: {current name: {"orig": name on the Frame}}


def rec_meta() -> dict:
    try:
        return json.loads(RECMETA.read_text())
    except (OSError, ValueError):
        return {}


def local_files():
    seen, out = set(), []
    renamed = rec_meta()
    cands = list(REC.glob("*.mp4")) if REC.is_dir() else []
    for d in CFG.extra_recording_dirs:
        if d.is_dir():
            cands += d.glob("*_stereo.mp4")
    for p in sorted(cands, key=lambda p: p.stat().st_mtime, reverse=True):
        if p.name in seen:
            continue
        seen.add(p.name)
        st = p.stat()
        cr = check_cached(p)
        orig = renamed.get(p.name, {}).get("orig", p.name)
        out.append({"name": p.name, "path": str(p), "size": st.st_size, "mtime": int(st.st_mtime),
                    "date": pretty_date(Path(orig).stem), "orig": orig,
                    "label": p.name.removesuffix(".mp4") if orig != p.name else None,
                    "renamable": p.parent.resolve() == REC.resolve(), "check": cr["level"] if cr else None})
    return out


def local_path(name: str):
    """Path of a recording on this PC – only from the known list, never taken from the browser."""
    return next((f["path"] for f in local_files() if f["name"] == name),
                next((p for p in picked if Path(p).name == name and Path(p).is_file()), None))


def recordings():
    fr = frame_listing()
    local = local_files()
    local_names = {f["orig"]: f["path"] for f in local}
    frame = None
    if fr is not None:
        frame = [{**f, "date": pretty_date(f["name"]), "local": local_names.get(f["name"])}
                 for f in sorted(fr["files"], key=lambda f: f["name"], reverse=True)]
    return {"frame_configured": bool(FRAME), "frame_online": fr is not None, "frame": frame, "local": local}


def probe(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "errors": [err("probe.missing")], "warnings": []}
    key = (path, p.stat().st_mtime)
    if key in probe_cache:
        return probe_cache[key]
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,codec_tag_string,width,height,avg_frame_rate,color_transfer:format=duration",
                        "-of", "json", path], capture_output=True, text=True)
    try:
        info = json.loads(r.stdout)
    except ValueError:
        return {"ok": False, "errors": [err("probe.unreadable")], "warnings": []}
    streams = info.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"]
    gpmd = any(s.get("codec_tag_string") == "gpmd" for s in streams)
    dur = float(info.get("format", {}).get("duration", 0) or 0)
    fps = 0.0
    if videos:
        n, _, d = videos[0].get("avg_frame_rate", "0/1").partition("/")
        fps = float(n) / float(d or 1) if float(d or 1) else 0.0
    hdr = bool(videos) and videos[0].get("color_transfer") in ("arib-std-b67", "smpte2084")
    errors, warnings = [], []
    if len(videos) < 2:
        errors.append(err("probe.not_stereo"))
    if not gpmd:
        errors.append(err("probe.no_gpmd"))
    if dur and dur < 8:
        errors.append(err("probe.too_short", secs=round(dur)))
    elif dur and dur < 20:
        warnings.append(err("probe.short", secs=round(dur)))
    res = {"ok": not errors, "errors": errors, "warnings": warnings, "duration": dur,
           "fps": round(fps, 1), "hdr": hdr,
           "size": f"{videos[0]['width']}×{videos[0]['height']}" if videos else ""}
    probe_cache[key] = res
    return res


def thumbnail(source: str, name: str):
    """Preview image as PNG bytes (cached) or None."""
    CACHE.mkdir(parents=True, exist_ok=True)
    stem = Path(name).stem
    cached = CACHE / f"{stem}.thumb"
    if cached.exists() and cached.stat().st_size:
        return cached.read_bytes()
    local = local_path(name)
    if local:
        dur = probe(local).get("duration") or 6
        try:
            r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{dur / 3:.2f}", "-i", local, "-map", "0:v:0",
                            "-frames:v", "1", "-vf", "scale=360:-1,eq=gamma=1.3,format=rgb24",
                            "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True, timeout=30)
        except subprocess.TimeoutExpired:
            return None
        if r.stdout:
            cached.write_bytes(r.stdout)       # don't cache failures
        return r.stdout or None
    if source != "frame":
        return None
    # No preview on the Frame: remember the empty result for 10 min only, then try again
    if cached.exists() and time.time() - cached.stat().st_mtime < 600:
        return None
    fr = frame_listing()
    if fr is None or not any(f["name"] == name for f in fr["files"]):
        return None
    data = b""
    match = next((x for x in fr["thumbs"] if stem in x or stem.removesuffix("_stereo") in x), None)
    if match:
        try:
            r = subprocess.run(SSH + [f"cat {FRAME_VIDEOS}/thumbnails/{shq(match)}"], capture_output=True, timeout=20)
        except subprocess.TimeoutExpired:
            return None                     # try again next time
        data = r.stdout if r.returncode == 0 else b""
    cached.write_bytes(data)
    return data or None


# ---------------------------------------------------------------- recording check (check.py)

checks = {}                        # name -> {"state": queued|running|done|error, "done", "total", "error"}
check_queue = []


CHECK_VERSION = 1                  # bump when the result format changes; older results are measured again


def check_file(path: Path) -> Path:
    st = path.stat()
    return CACHE / f"{path.stem}.{st.st_size}-{int(st.st_mtime)}.v{CHECK_VERSION}.check.json"


def check_cached(path: Path):
    f = check_file(path)
    try:
        return json.loads(f.read_text()) if f.exists() else None
    except ValueError:
        return None


def check_status(name: str) -> dict:
    local = local_path(name)
    if not local:
        return {"state": "missing"}
    res = check_cached(Path(local))
    if res:
        return {"state": "done", "result": res}
    with lock:
        c = checks.get(name)
        return dict(c) if c else {"state": "none"}


def check_busy() -> bool:
    return bool(check_queue) or any(c["state"] == "running" for c in checks.values())


def start_check(name: str):
    local = local_path(name)
    if not local:
        return err("err.not_local")
    with lock:
        if checks.get(name, {}).get("state") in ("queued", "running"):
            return None
        dur = probe(local).get("duration") or 0
        checks[name] = {"state": "queued", "done": 0, "total": round(dur), "error": None}
        check_queue.append((name, local, dur))
        if len(check_queue) == 1 and not any(c["state"] == "running" for c in checks.values()):
            threading.Thread(target=check_worker, daemon=True).start()
    return None


def check_worker():
    while True:
        with lock:
            if not check_queue:
                return
            name, local, dur = check_queue.pop(0)
            checks[name]["state"] = "running"

        def progress(i, name=name):
            checks[name]["done"] = i
        try:
            rows = check.measure(local, dur, progress)
            res = check.evaluate(rows, dur)
            res["measured"] = time.time()
            CACHE.mkdir(parents=True, exist_ok=True)
            for old in CACHE.glob(f"{Path(local).stem}.*.v{CHECK_VERSION}.check.json"):
                old.unlink()
            check_file(Path(local)).write_text(json.dumps(res, ensure_ascii=False))
            checks[name]["state"] = "done"
        except Exception as e:                  # noqa: BLE001 – shown in the interface
            checks[name].update(state="error", error=str(e))


# ---------------------------------------------------------------- download from the Frame

def download_busy() -> bool:
    return bool(download_queue) or bool(download and not download.get("finished"))


def start_download(files: list):
    """Fetch recordings from the Frame, one after the other ([{"name", "size"}]). Files already on this PC are
    skipped (a single one is refused). Adds to the queue when a download is already running."""
    with lock:
        waiting = {f["name"] for f in download_queue} | ({download["name"]} if download_busy() and download else set())
        new = [f for f in files if not (REC / f["name"]).exists() and f["name"] not in waiting]
        if not new:
            return err("err.name_taken") if len(files) == 1 and (REC / files[0]["name"]).exists() else (
                err("err.download_busy") if files else err("err.not_on_frame"))
        idle = not download_busy()
        if idle:
            download_batch.clear()
            download_batch.update(files=0, bytes=0, done_files=0, done_bytes=0, failed=[])
        download_batch["files"] += len(new)
        download_batch["bytes"] += sum(f["size"] for f in new)
        download_queue.extend(new)
        if idle:
            threading.Thread(target=download_worker, daemon=True).start()
    return None


def download_worker():
    global download
    while True:
        with lock:
            if not download_queue:
                break
            f = download_queue.pop(0)
            download = {"name": f["name"], "total": f["size"], "done": 0, "error": None, "finished": False}
        fetch(f["name"], f["size"])
        with lock:
            download_batch["done_files"] += 1
            download_batch["done_bytes"] += f["size"]
            if download["error"]:
                download_batch["failed"].append(f["name"])
            download["finished"] = True
    b = download_batch
    if b.get("done_files") and not b.get("cancelled"):
        ok = b["done_files"] - len(b["failed"])
        if b["failed"]:
            notify(t("notify.download_failed.title"), t("notify.download_failed", n=len(b["failed"]), ok=ok))
        else:
            notify(t("notify.download.title"), t("notify.download", n=ok))


def fetch(name: str, size: int):
    """One recording from the Frame, via a .part file (never a half file under the real name)."""
    global download_proc
    REC.mkdir(parents=True, exist_ok=True)
    part = REC / (name + ".part")
    try:
        with open(part, "wb") as fh:
            p = download_proc = subprocess.Popen(keep_awake(SSH + [f"cat {FRAME_VIDEOS}/{shq(name)}"],
                                                            t("inhibit.download")),
                                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            while chunk := p.stdout.read(1 << 20):
                fh.write(chunk)
                download["done"] += len(chunk)
            rc = p.wait()
        if download.get("cancelled"):
            raise RuntimeError("cancelled")
        if rc != 0 or download["done"] != size:
            raise RuntimeError("transfer interrupted")
        part.rename(REC / name)
        frame_cache["t"] = 0
    except Exception as e:  # noqa: BLE001 – shown in the interface
        download["error"] = str(e)
        part.unlink(missing_ok=True)
    finally:
        download_proc = None


def cancel_download():
    """Empty the queue and stop the file being fetched (its half is removed)."""
    with lock:
        if not download_busy():
            return err("err.no_download")
        download_queue.clear()
        download_batch["cancelled"] = True
        if download and not download.get("finished"):
            download["cancelled"] = True
            if download_proc and download_proc.poll() is None:
                download_proc.kill()
    return None


def download_status():
    return {**(download or {}), "queue": [f["name"] for f in download_queue], "batch": dict(download_batch),
            "busy": download_busy()} if download or download_queue else None


# ---------------------------------------------------------------- conversion

RX_EXTRACT = re.compile(r"Extraction progress: decoded (\d+)%")
RX_STEP = re.compile(r"(?:step|Schritt)\s+(\d+)/(\d+)\s+\(\s*(\d+)%\)\s+(?:splats|Splats)\s+(\d+)\s+"
                     r"\[(?:elapsed|Laufzeit) ([\d:.]+) \| (?:ETA|Restzeit) ([\d:.]+)\].*?psnr=([\d.]+)")
RX_ALIGN = re.compile(r"Aligned (\d+) cameras .*?RMS ([\d.]+) m")
RX_TOTAL = re.compile(r"Extracting (\d+) images")
RX_GAUSS = re.compile(r"spirula: ([\d,]+) Gaussians")
RX_DONE = re.compile(r"^@@ done (frame|local)(?: (\S+))?", re.M)


def hms(s: str) -> float:
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec)


RX_CPHASE = re.compile(r"^@@ cphase (\d)", re.M)
RX_CPCT = re.compile(r"^@@ pct (\d+)", re.M)
RX_CTOTAL = re.compile(r"^@@ total (\d+)", re.M)


def parse_combine(text: str) -> dict:
    """Progress of combine.sh: phases 0 align, 1 assemble, 2 reduce, 3 convert, 4 copy to the Frame."""
    last = list(RX_CPHASE.finditer(text))[-1]
    phase, rest = int(last[1]), text[last.end():]
    pcts = RX_CPCT.findall(rest)
    pct = int(pcts[-1]) if pcts else None
    info = {}
    if tot := RX_CTOTAL.findall(text):
        info["splats"] = int(tot[-1])
    if "@@ phase upload" in rest:
        phase, pct = 4, None
    if d := RX_DONE.search(text):
        info["done"] = True
        if d[1] == "frame" and d[2]:
            info["folder"] = d[2]
    return {"phase": phase, "pct": pct, "eta": None, "info": info}


RX_VSTEP = re.compile(r"^@@ vstep (\d+) (\d+) (\S+)", re.M)


def parse_versions(text: str) -> dict:
    """Progress of versions.sh: one phase per version."""
    steps = RX_VSTEP.findall(text)
    i, n, key = steps[-1]
    info = {"version": key, "versions": int(n)}
    if RX_DONE.search(text):
        info["done"] = True
    return {"phase": int(i) - 1, "pct": round(100 * (int(i) - 1) / int(n)), "eta": None, "info": info}


def parse_output(text: str) -> dict:
    text = text.replace("\r", "\n")
    if "@@ cphase" in text:
        return parse_combine(text)
    if "@@ vstep" in text:
        return parse_versions(text)
    phase, pct, eta = 0, None, None
    info = {}
    m = None
    for m in RX_EXTRACT.finditer(text):
        pass
    if m:
        pct = int(m[1])
    if "Running native Spirula SfM" in text:
        phase, pct = 1, None
    if a := RX_ALIGN.search(text):
        info["cameras"] = int(a[1])
        info["rms_cm"] = round(float(a[2]) * 100, 1)
        if tot := RX_TOTAL.search(text):
            info["images"] = int(tot[1])
    steps = RX_STEP.findall(text)
    if steps:
        phase = 2
        last = steps[-1]
        pct = int(last[2])
        eta = hms(last[5])
        info.update(step=int(last[0]), steps=int(last[1]), splats=int(last[3]), elapsed=hms(last[4]))
        psnr = [float(s[6]) for s in steps]
        k = max(1, len(psnr) // 120)           # smoothed curve, at most 120 points
        info["psnr_curve"] = [round(sum(psnr[i:i + k]) / len(psnr[i:i + k]), 2) for i in range(0, len(psnr), k)]
        tail = psnr[-10:]
        info["psnr"] = round(sum(tail) / len(tail), 1)
    if g := RX_GAUSS.search(text):
        info["gaussians"] = int(g[1].replace(",", ""))
    if "splat-transform" in text:
        phase, pct, eta = 3, None, None
    if "@@ phase upload" in text:
        phase, pct = 4, None
    if d := RX_DONE.search(text):
        info["done"] = True
        if d[1] == "frame" and d[2]:
            info["folder"] = d[2]
    for needle, hint in KNOWN_FAILURES:
        if needle in text:
            info["hint"] = hint
    return {"phase": phase, "pct": pct, "eta": eta, "info": info}


# Failures with a known cause: a line of the pipeline's output -> a hint in the interface (web/i18n.js)
KNOWN_FAILURES = (("unexpected keyword argument 'metadata_errors'", "hint.pyav"),)


def job_status(tail=True) -> dict | None:
    st = load_state().get("job")
    if not st:
        return None
    out = Path(st["out"])
    text = out.read_text(errors="replace") if out.exists() else ""
    running = pid_alive(st["pid"])
    parsed = parse_output(text)
    res = {**st, **parsed, "running": running}
    if tail:
        res["tail"] = "\n".join(text.replace("\r", "\n").splitlines()[-200:])
    if not running:
        if parsed["info"].get("done"):
            res["result"] = "ok"
        elif st.get("cancelled"):
            res["result"] = "cancelled"
        else:
            res["result"] = "failed"
    return res


def start_job(p: dict, part: str = "", chain: dict = None):
    """A conversion; `part` (e.g. "120.0-245.5") converts only that stretch of the recording."""
    global proc
    with lock:
        if (cur := load_state().get("job")) and pid_alive(cur["pid"]):
            return err("err.job_busy")
        path, name = p.get("path", ""), (p.get("name") or "").strip()
        if local_path(Path(path).name) != path:
            return err("err.unknown_recording")
        if not (pr := probe(path))["ok"]:
            return pr["errors"][0]
        slug = config.slugify(name)
        if not slug or slug.startswith("."):
            return err("err.bad_name")
        env = {**os.environ, "QUALITY": p.get("quality", "high")}
        if env["QUALITY"] not in QUALITIES:
            return err("err.bad_quality")
        for key, lo, hi, typ in (("FRAMES", 100, 500, int), ("SCALE", 0.25, 1, float),
                                 ("MAXG", 200_000, 6_000_000, int), ("ITER", 1000, 100_000, int)):
            v = p.get(key.lower())
            if v not in (None, ""):
                try:
                    v = typ(v)
                except ValueError:
                    return err("err.bad_value", key=key)
                if not lo <= v <= hi:
                    return err("err.range", key=key, lo=lo, hi=hi)
                env[key] = str(v)
        if not p.get("upload", True) or not FRAME or part:
            env["NO_UPLOAD"] = "1"                  # a part alone is no scene for the Frame
        if part:
            env["PART"] = part
        args = []
        previous = None
        if (RUNS / slug).exists():
            if p.get("existing") == "resume" and no_resume(slug):
                return "exists"                 # training images were deleted: only a fresh start works
            if p.get("existing") == "resume":
                args.append("--resume")
            elif p.get("existing") == "fresh":
                env["FRESH"] = "1"
                # splat.sh keeps the old result until the new one is done and puts it back otherwise,
                # including these settings
                if (RUNS / slug / "scene.sog").exists():
                    previous = {k: v for k, v in read_meta(slug).items() if k != "previous"}
            else:
                return "exists"
        JOBS.mkdir(parents=True, exist_ok=True)
        out = JOBS / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_{slug}.out"
        with open(out, "wb") as fh:
            proc = subprocess.Popen(keep_awake([str(SCRIPTS / "splat.sh"), path, name, *args],
                                               t("inhibit.job", name=name)), cwd=ROOT, env=env, stdout=fh,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        st = load_state()
        st["job"] = {"pid": proc.pid, "out": str(out), "name": name, "slug": slug, "recording": Path(path).name,
                     "quality": env["QUALITY"], "upload": "NO_UPLOAD" not in env, "started": time.time(),
                     "overrides": {k: env[k] for k in ("FRAMES", "SCALE", "MAXG", "ITER") if k in env},
                     **({"part": part} if part else {}), **({"chain": chain} if chain else {})}
        save_state(st)
        RUNS.mkdir(parents=True, exist_ok=True)
        write_meta(slug, name=name, recording=Path(path).name, quality=env["QUALITY"], part=part or None,
                   **({} if "--resume" in args else {"cleaned": [], "recording_renamed": False}),
                   **({"previous": previous} if previous is not None else {}))
    return None


# ---------------------------------------------------------------- a long recording in parts
# The Arcturus pipeline takes at most 500 image pairs per recording. web/index.html suggests as many parts
# as a recording needs for the full rate of its quality preset, all equally long (no short rest).

PARTS_MAX = 10
PART_OVERLAP = 4.0                         # seconds both neighbouring parts get, so they surely meet


def part_ranges(duration: float, n: int) -> list:
    step = duration / n
    return [(max(0.0, i * step - (PART_OVERLAP / 2 if i else 0)),
             min(duration, (i + 1) * step + (PART_OVERLAP / 2 if i < n - 1 else 0))) for i in range(n)]


def start_parts(p: dict):
    """Convert a long recording in n parts one after the other, then make them one room and put it
    together (each part gets up to 500 image pairs)."""
    try:
        n = int(p.get("parts"))
    except (TypeError, ValueError):
        return err("err.bad_value", key="parts")
    if not 2 <= n <= PARTS_MAX:
        return err("err.range", key="parts", lo=2, hi=PARTS_MAX)
    path, room = p.get("path", ""), clean_name(p.get("name") or "", file=False)
    if local_path(Path(path).name) != path:
        return err("err.unknown_recording")
    duration = probe(path).get("duration") or 0
    if duration / n < 20:
        return err("err.parts_too_short", secs=20)
    room_slug = config.slugify(room)
    if not room_slug or room_slug.startswith("."):
        return err("err.bad_name")
    names = [t("part.name", name=room, i=i + 1, n=n) for i in range(n)]
    slugs = [config.slugify(x) for x in names]
    taken = lambda x: (RUNS / x).exists() or meta_file(x).exists() or (RUNS / f"{x}.log").exists()
    if any(taken(x) for x in [room_slug, *slugs]) or len(set(slugs)) < n or room_slug in slugs:
        return err("err.scene_name_taken")
    ranges = part_ranges(duration, n)
    chain = {"room": room, "path": path, "quality": p.get("quality", "high"), "i": 0, "n": n,
             "parts": [{"name": x, "slug": s_, "part": f"{a:.2f}-{b:.2f}"} for x, s_, (a, b) in zip(names, slugs, ranges)],
             "settings": {k: p.get(k) for k in ("frames", "scale", "maxg", "iter") if p.get(k) not in (None, "")},
             "upload": bool(p.get("upload", True)) and bool(FRAME)}
    first = chain["parts"][0]
    return start_job({**chain["settings"], "path": path, "name": first["name"], "quality": chain["quality"],
                      "upload": False}, part=first["part"], chain={"i": 0, "n": n, "room": room, "parts": chain["parts"],
                                                                     "settings": chain["settings"], "upload": chain["upload"]})


def advance_parts():
    """After a part: the next one; after the last: the parts become one room, which is put together."""
    js = job_status(tail=False)
    ch = js and js.get("chain")
    if not ch or js["running"] or ch.get("done"):
        return
    st = load_state()
    st["job"]["chain"]["done"] = True             # only once, also after a restart of the server
    save_state(st)
    room = ch["room"]
    if js["result"] != "ok":
        if js["result"] == "failed":
            notify(t("notify.parts_failed.title"), t("notify.parts_failed", name=room, i=ch["i"] + 1, n=ch["n"]))
        return
    i = ch["i"] + 1
    if i < ch["n"]:
        nxt = ch["parts"][i]
        e = start_job({**ch["settings"], "path": js_path(js), "name": nxt["name"], "quality": js["quality"], "upload": False},
                      part=nxt["part"], chain={**ch, "i": i})
        if e:
            notify(t("notify.parts_failed.title"), t("notify.parts_failed", name=room, i=i + 1, n=ch["n"]))
        return
    slug = save_group("", room, ",".join(x["slug"] for x in ch["parts"]), room=True)
    if isinstance(slug, dict):
        notify(t("notify.parts_failed.title"), t("notify.parts_failed", name=room, i=ch["n"], n=ch["n"]))
        return
    e = start_combine({"slug": slug, "upload": ch["upload"], "full": True})
    notify(t("notify.parts_done.title"), t("notify.parts_done", name=room, n=ch["n"]) if not e else
           t("notify.parts_done_alone", name=room, n=ch["n"]))


def js_path(js: dict) -> str:
    return local_path(js["recording"]) or ""


def cancel_job():
    st = load_state()
    job = st.get("job")
    if not job or not pid_alive(job["pid"]):
        return err("err.no_job")
    job["cancelled"] = True
    st["standby"] = {}                          # someone is there: no standby afterwards
    save_state(st)
    try:
        os.killpg(job["pid"], signal.SIGTERM)
    except ProcessLookupError:
        pass

    def kill_later(pid):
        time.sleep(8)
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    threading.Thread(target=kill_later, args=(job["pid"],), daemon=True).start()
    return None


def activity() -> dict:
    """Compact summary for the tray icon."""
    js = job_status(tail=False)
    job = None
    if js and js["running"]:
        job = {k: js.get(k) for k in ("name", "phase", "pct", "eta", "upload")}
    b = download_batch
    pct = (round(100 * (b["done_bytes"] + (download["done"] if download and not download.get("finished") else 0))
                 / max(b["bytes"], 1)) if download_busy() else None)
    return {"job": job, "download": {"pct": pct} if pct is not None else None,
            "checking": check_busy(), "uploading": any(u["proc"].poll() is None for u in uploads.values())}


# ---------------------------------------------------------------- standby when everything is done

STANDBY_GRACE = 60                 # seconds between "all done" and the standby (time to change one's mind)
AWAKE_AFTER = 15                   # seconds the PC is still kept awake after the last work (gaps between jobs)
awake = {"proc": None, "idle_since": None}


def hold_awake(busy: bool) -> None:
    """While anything runs, and a little longer: one sleep inhibitor of this server for all of it. The
    jobs have their own, but between two of them (the parts of a long recording, then putting them
    together) there are a few seconds without – enough for the desktop to suspend a PC idle for hours."""
    p = awake["proc"]
    if busy:
        awake["idle_since"] = None
        if (p is None or p.poll() is not None) and can_inhibit():
            awake["proc"] = subprocess.Popen(keep_awake(["sleep", "infinity"], t("inhibit.busy")),
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return
    awake["idle_since"] = awake["idle_since"] or time.time()
    if p is not None and time.time() - awake["idle_since"] > AWAKE_AFTER:
        release_awake()


def release_awake() -> None:
    p, awake["proc"] = awake["proc"], None
    if p is not None and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        p.wait()


def standby_status() -> dict:
    sb = load_state().get("standby") or {}
    return {"on": bool(sb.get("on")), "at": sb.get("at"), "error": sb.get("error")}


def set_standby(on: bool) -> dict:
    """Switch "standby when everything is done" on or off (also stops a countdown)."""
    with lock:
        st = load_state()
        st["standby"] = {"on": True, "since": time.time()} if on else {}
        save_state(st)
    return standby_status()


def standby_tick(busy: bool) -> None:
    """Once nothing runs any more: a minute's notice, then standby. (Cancelling a job switches it off:
    someone is there.)"""
    st = load_state()
    sb = st.get("standby") or {}
    if not sb.get("on"):
        return
    if busy:
        if sb.get("at"):
            sb.pop("at")
            save_state(st)
        return
    if not sb.get("at"):
        sb["at"] = time.time() + STANDBY_GRACE
        save_state(st)
        notify(t("notify.standby.title"), t("notify.standby", s=STANDBY_GRACE))
        return
    if time.time() < sb["at"]:
        return
    st["standby"] = {}
    save_state(st)
    release_awake()                             # our own lock must not keep the PC up
    r = subprocess.run(["systemctl", "suspend"], capture_output=True, text=True)
    if r.returncode:
        st = load_state()
        st["standby"] = {"error": (r.stderr or r.stdout).strip()[-300:]}
        save_state(st)
        notify(t("notify.standby_failed.title"), t("notify.standby_failed", why=(r.stderr or r.stdout).strip()[-200:]))


def watch_once(was_running):
    """One look at the jobs: notification and tidying up when a run has ended. Returns whether one runs."""
    if proc is not None:
        proc.poll()                             # reap the zombie
    js = job_status(tail=False)
    running = bool(js and js["running"])
    if was_running and not running and js:
        mins = round((time.time() - js["started"]) / 60)
        if js.get("chain"):
            pass                                # advance_parts() reports at the end
        elif js["result"] == "ok":
            if js["info"].get("folder"):
                write_meta(js["slug"], frame_folder=js["info"]["folder"])
            with meta_lock:                     # the replaced scene's settings are not needed any more
                meta = read_meta(js["slug"])
                if meta.pop("previous", None) is not None:
                    write_json(meta_file(js["slug"]), meta)
            notify(t("notify.done.title"),
                   t("notify.done.frame" if js["upload"] else "notify.done.local", name=js["name"], mins=mins))
        elif js["result"] == "failed":
            notify(t("notify.failed.title"), t("notify.failed", name=js["name"]))
    if not running:
        restore_previous()
        advance_parts()
        js = job_status(tail=False)             # the next part (or putting them together) may run now
        running = bool(js and js["running"])
    for slug in list(uploads):
        upload_status(slug)                     # notify even without an open page
    return running


def watcher():
    """Every few seconds: notifications and tidying up; stop the server after a longer idle time."""
    was_running = None
    while True:
        time.sleep(3)
        was_running = running = watch_once(was_running)
        busy = (running or download_busy() or check_busy()
                or any(u["proc"].poll() is None for u in uploads.values()))
        hold_awake(busy)
        standby_tick(busy)
        if idle_exit and not busy and time.time() - last_request > CFG.idle_minutes * 60:
            os._exit(0)


# ---------------------------------------------------------------- copy to the Frame

# ---------------------------------------------------------------- scale (models, e.g. a model railway)

SCALE_MAX = 1000                           # 1:1000 – further than any model railway or building model


def world_scale_env(meta: dict) -> dict:
    """WORLD_SCALE for to-frame.sh/combine.sh: show a model in Gaussian XR in the size of the original."""
    scale = meta.get("scale") or 1
    return {"WORLD_SCALE": f"{scale:g}"} if meta.get("scale_xr") and scale != 1 else {}


def set_scale(slug: str, ratio, xr: bool, members: bool, update_frame: bool):
    """Scale 1:ratio of a scene or group (1 = original size). For a group optionally also for its scenes."""
    try:
        ratio = float(ratio)
    except (TypeError, ValueError):
        return err("err.bad_value", key="scale")
    if not 1 <= ratio <= SCALE_MAX or ratio != ratio:
        return err("err.range", key="scale", lo=1, hi=SCALE_MAX)
    meta = read_meta(slug) if slug and Path(slug).name == slug and not slug.startswith(".") else {}
    group = is_group(meta)
    if not group and not (scene_dir(slug) and (RUNS / slug / "scene.sog").exists()):
        return err("err.no_scene")
    inner_rooms = [m for m in meta.get("members", []) if is_room(read_meta(m))] if group and members else []
    targets = [slug] + inner_rooms + ([m for m, _ in group_scenes(slug, meta)] if group and members else [])
    if any(scene_busy(t_) for t_ in targets):
        return err("err.scene_busy")
    xr = bool(xr) and ratio != 1
    for t_ in targets:
        write_meta(t_, scale=ratio if ratio != 1 else None, scale_xr=xr)
    if update_frame and FRAME and (RUNS / slug / "scene.sog").exists():
        return start_upload(slug)                       # the Frame gets the new worldScale
    return None


# ---------------------------------------------------------------- versions (scripts/versions.sh)

VERSION_LIMITS = (10_000, 50_000_000)       # splats


def read_version(vdir: Path) -> dict:
    try:
        return json.loads((vdir / "version.json").read_text())
    except (OSError, ValueError):
        return {}


CLEANUPS = ("ceiling", "floor", "haze", "outside")        # scripts/clean.py, each 0 (off) to 1


def version_key(splats, scale: float, clean: dict = None) -> str:
    """Folder name of a version: 1m, 0.75m, all; with a scale e.g. 1m-1x87; cleaned up e.g. all-c50h30."""
    key = "all" if splats == "all" else f"{splats / 1e6:g}m"
    key += f"-1x{scale:g}" if scale != 1 else ""
    return key + ("-" + "".join(f"{k[0]}{round(v * 100)}" for k, v in clean.items()) if clean else "")


def parse_cleanup(raw: str):
    """"ceiling=0.5,haze=0.3" -> {"ceiling": 0.5, "haze": 0.3}, the ones switched on (0 < S <= 1); or an error."""
    if not raw:
        return {}
    pairs = dict(kv.partition("=")[::2] for kv in raw.split(","))
    if set(pairs) - set(CLEANUPS):
        return err("err.bad_value", key="clean")
    out = {}
    for k in CLEANUPS:
        try:
            v = float(pairs.get(k) or 0)
        except ValueError:
            return err("err.bad_value", key=k)
        if v != v:
            return err("err.bad_value", key=k)
        if not 0 <= v <= 1:
            return err("err.range", key=k, lo=0, hi=1)
        if v:
            out[k] = round(v, 2)
    return out


def versions_of(slug: str) -> list:
    d = scene_dir(slug)
    if not d or not (d / "versions").is_dir():
        return []
    meta = read_meta(slug)
    source = d / "spirula" / "scene.ply"
    built = source.stat().st_mtime if source.exists() else 0
    fr = frame_listing()
    folders = meta.get("version_folders") or {}
    out = []
    for vdir in sorted((d / "versions").iterdir()):
        info = read_version(vdir)
        if not info or not (vdir / "scene.sog").exists():
            continue
        key = vdir.name
        on_frame = None
        if fr is not None:
            folder = folders.get(key)
            on_frame = (folder in fr["scenes"]) if folder else any(
                re.fullmatch(r"\d\d_" + re.escape(f"{slug}-{key}"), x) for x in fr["scenes"])
        clean = info.get("clean") or {}
        target = info.get("target", "")
        out.append({"key": key, "label": info.get("label"), "splats": info.get("splats"), "scale": info.get("scale", 1),
                    "clean": clean, "purpose": "pc" if target == "all" else "frame",
                    # Spirula opens a ply: a cleaned PC version keeps its own, a plain one is the scene's own
                    "ply": (vdir / "spirula" / "scene.ply").exists() or (target == "all" and not clean and source.exists()),
                    "size": (vdir / "scene.sog").stat().st_size, "created": int(info.get("created", 0)),
                    "stale": info.get("created", 0) < built, "on_frame": on_frame,
                    "upload": upload_status(f"{slug}#{key}")})
    return sorted(out, key=lambda v: (v["scale"], v["splats"] or 0))


def start_versions(p: dict):
    """New versions of a scene or tour: sizes (splats, or "all") times scales."""
    global proc
    slug = p.get("slug", "")
    d = scene_dir(slug) if slug and not slug.startswith(".") else None
    if not d or not (d / "spirula" / "scene.ply").exists() or not (d / "scene.sog").exists():
        return err("err.no_scene")
    sizes, scales = [], []
    for x in (p.get("sizes") or "").split(","):
        x = x.strip()
        if not x:
            continue
        if x == "all":
            sizes.append("all")
            continue
        try:
            n = int(float(x))
        except ValueError:
            return err("err.bad_value", key="sizes")
        if not VERSION_LIMITS[0] <= n <= VERSION_LIMITS[1]:
            return err("err.range", key="sizes", lo=VERSION_LIMITS[0], hi=VERSION_LIMITS[1])
        sizes.append(n)
    for x in (p.get("scales") or "1").split(","):
        try:
            sc = float(x)
        except ValueError:
            return err("err.bad_value", key="scales")
        if not 1 <= sc <= SCALE_MAX or sc != sc:
            return err("err.range", key="scales", lo=1, hi=SCALE_MAX)
        scales.append(sc)
    if not sizes:
        return err("err.no_versions")
    clean = parse_cleanup(p.get("clean"))
    if "code" in clean:
        return clean
    meta = read_meta(slug)
    name = meta.get("name") or slug
    specs = []
    # with a clean-up, optionally the same without, to compare side by side
    variants = [clean, {}] if clean and p.get("also_plain") else [clean]
    for sc in dict.fromkeys(scales):
        for n in dict.fromkeys(sizes):
            for cl in variants:
                size = t("ver.all") if n == "all" else t("ver.size", n=f"{n / 1e6:g}".replace(".", "," if t.lang == "de" else "."))
                plate = (f"{name} · {size}" + (f" · 1:{sc:g}" if sc != 1 else "")
                         + "".join(f" · {t('ver.clean.' + k)} {round(v * 100)} %" for k, v in cl.items()))
                specs.append(f"{version_key(n, sc, cl)}|{n}|{sc:g}|{plate}"
                             + ("|" + ",".join(f"{k}={v:g}" for k, v in cl.items()) if cl else ""))
    with lock:
        if (cur := load_state().get("job")) and pid_alive(cur["pid"]):
            return err("err.job_busy")
        if scene_busy(slug):
            return err("err.scene_busy")
        JOBS.mkdir(parents=True, exist_ok=True)
        out = JOBS / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_versions_{slug}.out"
        with open(out, "wb") as fh:
            proc = subprocess.Popen(keep_awake([str(SCRIPTS / "versions.sh"), str(d), *specs],
                                               t("inhibit.versions", name=name)), cwd=ROOT, stdout=fh,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        st = load_state()
        st["job"] = {"kind": "versions", "pid": proc.pid, "out": str(out), "name": name, "slug": slug,
                     "recording": "", "quality": "", "upload": False, "started": time.time(), "overrides": {},
                     "steps": [s.split("|")[3].removeprefix(name + " · ") for s in specs]}
        save_state(st)
    return None


GALLERY_TTL = 20                           # seconds the list of the Frame's gallery is reused
gallery_cache = {"t": 0, "data": None}
RX_FOLDER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]*")


def frame_gallery(force=False):
    """Every scene in Gaussian XR's gallery on the Frame (a folder with a preview image), with what it is:
    a scene, tour or version made here, or something else (e.g. Gaussian XR's examples). None = no Frame."""
    if not FRAME:
        return None
    if not force and time.time() - gallery_cache["t"] < GALLERY_TTL:
        return gallery_cache["data"]
    cmd = "".join(f'(cd {path} 2>/dev/null && for d in */; do d=${{d%/}}; '
                  f'g=$(ls "$d"/*.gallery.png 2>/dev/null | head -1); [ -n "$g" ] || continue; '
                  f'echo "{place}|$d|$(du -sb "$d" | cut -f1)|${{g##*/}}"; done); ' for place, path in FRAME_PLACES.items())
    cmd += "true"
    try:
        r = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        r = None
    if r is None or r.returncode == 255:
        gallery_cache.update(t=time.time(), data=None)
        return None
    owners = {}                                 # folder -> (slug, version, kind, name)
    for f in RUNS.glob("*.ui.json"):
        slug = f.name.removesuffix(".ui.json")
        meta = read_meta(slug)
        kind, name = ("tour" if is_group(meta) else "scene"), meta.get("name") or slug
        if meta.get("frame_folder"):
            owners[meta["frame_folder"]] = (slug, "", kind, name)
        for key, folder in (meta.get("version_folders") or {}).items():
            label = read_version(RUNS / slug / "versions" / key).get("label") or f"{name} · {key}"
            owners[folder] = (slug, key, "version", label)
        owners.setdefault(("re", slug), (slug, "", kind, name))
    out = []
    for line in r.stdout.splitlines():
        place, _, line = line.partition("|")
        folder, _, rest = line.partition("|")
        size, _, preview = rest.partition("|")
        if place not in FRAME_PLACES or not RX_FOLDER.fullmatch(folder):
            continue
        owner = owners.get(folder)
        if not owner and (m := re.fullmatch(r"\d\d_(.+)", folder)):
            rest_ = m.group(1)
            owner = owners.get(("re", rest_))
            if not owner and "-" in rest_:              # <scene>-<version>
                slug, _, key = rest_.rpartition("-")
                if (RUNS / slug / "versions" / key / "version.json").exists():
                    owner = (slug, key, "version", read_version(RUNS / slug / "versions" / key).get("label") or rest_)
        slug, version, kind, name = owner or ("", "", "other", re.sub(r"^\d\d_", "", folder))
        out.append({"folder": folder, "place": place, "size": int(size or 0), "preview": preview, "slug": slug,
                    "version": version, "kind": kind, "name": name})
    gallery_cache.update(t=time.time(), data=out)
    return out


def gallery_entry(gallery, folder: str, place: str = ""):
    """A scene in the Frame's gallery by its folder (and place, when the same name is in both)."""
    return next((x for x in gallery or [] if x["folder"] == folder and place in ("", x["place"])), None)


def frame_preview(folder: str, place: str = ""):
    """The gallery image of a scene on the Frame (cached on the PC)."""
    g = gallery_entry(frame_gallery(), folder, place)
    if not g:
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"frame-{hashlib.sha1(f'{g['place']}|{folder}|{g['size']}'.encode()).hexdigest()[:16]}.png"
    if cached.exists():
        return cached.read_bytes()
    try:
        r = subprocess.run(SSH + [f"cat {FRAME_PLACES[g['place']]}/{shq(folder)}/{shq(g['preview'])}"],
                           capture_output=True, timeout=20)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    cached.write_bytes(r.stdout)
    return r.stdout


def remove_frame_folder(folder: str, place: str = ""):
    """Take a scene off the Frame's gallery – any scene in it (also Gaussian XR's examples; Steam brings
    those back with "verify files"). The PC is not touched."""
    if not FRAME:
        return err("err.no_frame")
    gallery = frame_gallery(force=True)
    if gallery is None:
        return err("err.frame_offline")
    g = gallery_entry(gallery, folder, place)
    if not g or not RX_FOLDER.fullmatch(folder):
        return err("err.not_on_frame_version")
    key = f"{g['slug']}#{g['version']}" if g["version"] else g["slug"]
    if g["slug"] and (u := uploads.get(key)) and u["proc"].poll() is None:
        return err("err.upload_busy")
    try:
        r = subprocess.run(SSH + [f"rm -rf -- {FRAME_PLACES[g['place']]}/{shq(folder)} {FRAME_BACKUP}/{shq(folder)}"],
                           capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return err("err.frame_offline")
    if r.returncode != 0:
        return err("err.frame_failed", detail=(r.stderr.strip().splitlines() or [""])[-1])
    if g["slug"]:                               # forget the folder, so copying again makes a new one
        meta = read_meta(g["slug"])
        if g["version"]:
            folders = meta.get("version_folders") or {}
            folders.pop(g["version"], None)
            write_meta(g["slug"], version_folders=folders)
        elif meta.get("frame_folder") == folder:
            write_meta(g["slug"], frame_folder=None)
        uploads.pop(key, None)
    gallery_cache["t"] = frame_cache["t"] = 0
    return None


def remove_from_frame(slug: str, version: str):
    """Take one version of a scene off the Frame."""
    if not FRAME:
        return err("err.no_frame")
    if not slug or slug.startswith(".") or not version or version.startswith(".") or Path(version).name != version:
        return err("err.no_version")
    gallery = frame_gallery(force=True)
    if gallery is None:
        return err("err.frame_offline")
    g = next((x for x in gallery if x["slug"] == slug and x["version"] == version), None)
    if not g:
        return err("err.not_on_frame_version")
    return remove_frame_folder(g["folder"], g["place"])


def move_to_own_folder():
    """Move the scenes made here out of Gaussian XR's own gallery folder (where 0.2.3 and older copied them,
    and where a Steam update may remove them) into the folder for personal scenes. Gaussian XR's examples stay."""
    if not FRAME:
        return err("err.no_frame")
    gallery = frame_gallery(force=True)
    if gallery is None:
        return err("err.frame_offline")
    if any(u["proc"].poll() is None for u in uploads.values()):    # to-frame.sh may be choosing or moving a folder
        return err("err.upload_busy")
    moving = [g for g in gallery if g["place"] == "steam" and g["kind"] != "other"]
    if not moving:
        return None
    # A folder of the same name in the personal folder is the newer copy: the old one goes
    cmd = f"mkdir -p {FRAME_SCENES} && " + " && ".join(
        f"{{ [ -e {FRAME_SCENES}/{shq(g['folder'])} ] && rm -rf -- {FRAME_STEAM}/{shq(g['folder'])} || "
        f"mv -- {FRAME_STEAM}/{shq(g['folder'])} {FRAME_SCENES}/; }}" for g in moving)
    try:
        r = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return err("err.frame_offline")
    gallery_cache["t"] = frame_cache["t"] = 0
    if r.returncode != 0:
        return err("err.frame_failed", detail=(r.stderr.strip().splitlines() or [""])[-1])
    return None


def delete_version(slug: str, key: str):
    d = scene_dir(slug) if slug and not slug.startswith(".") else None
    vdir = d / "versions" / Path(key).name if d and key and not key.startswith(".") else None
    if not vdir or not vdir.is_dir():
        return err("err.no_version")
    if scene_busy(slug) or ((u := uploads.get(f"{slug}#{key}")) and u["proc"].poll() is None):
        return err("err.scene_busy")
    try:
        r = subprocess.run(["gio", "trash", "--", str(vdir)], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return err("err.trash_failed", detail=str(e))
    if r.returncode != 0:
        return err("err.trash_failed", detail=(r.stderr.strip().splitlines() or [""])[-1])
    usage_cache.pop(d, None)
    return None


# ---------------------------------------------------------------- is there a newer version?

REPO = "thunderheronight/splat-workshop"
UPDATE_FILE = STATE_DIR / "update.json"        # {"checked": time, "tag": "v0.5.1", "url": release page}
UPDATE_EVERY = 24 * 3600
update_lock = threading.Lock()


def version_tuple(v: str) -> tuple:
    """"v0.5.1" / "0.5.1-experimental" -> (0, 5, 1); unparsable -> ()."""
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", v or "")
    return tuple(int(x) for x in m.groups()) if m else ()


def fetch_latest_release() -> dict | None:
    """The newest release on GitHub: one plain request, nothing about the user is sent."""
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": f"SplatWorkshop/{VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
    except (OSError, ValueError):
        return None
    tag, url = data.get("tag_name"), data.get("html_url")
    if not isinstance(tag, str) or not version_tuple(tag):
        return None
    if not (isinstance(url, str) and url.startswith(f"https://github.com/{REPO}/")):
        url = f"https://github.com/{REPO}/releases/latest"
    return {"tag": tag, "url": url}


def ask_for_update():
    """At most once a day, in the background (switched off with check_updates = false)."""
    if not CFG.check_updates or not update_lock.acquire(blocking=False):
        return
    try:
        try:
            last = json.loads(UPDATE_FILE.read_text())
        except (OSError, ValueError):
            last = {}
        if time.time() - last.get("checked", 0) < UPDATE_EVERY:
            update_lock.release()
            return
    except Exception:
        update_lock.release()
        raise

    def run():
        try:
            found = fetch_latest_release()
            write_json(UPDATE_FILE, {"checked": time.time(), **(found or {})})
        finally:
            update_lock.release()
    threading.Thread(target=run, daemon=True).start()


def update_info() -> dict | None:
    """A newer release than this one, or None."""
    if not CFG.check_updates:
        return None
    try:
        last = json.loads(UPDATE_FILE.read_text())
    except (OSError, ValueError):
        return None
    latest = version_tuple(last.get("tag", ""))
    if latest and latest > version_tuple(VERSION):
        return {"latest": last["tag"].lstrip("v"), "url": last.get("url"), "current": VERSION}
    return None


# ---------------------------------------------------------------- meshes (scripts/mesh.sh, 0.5)

MESH_FORMATS = ("glb", "ply", "obj", "stl")


MESH_TYPES = {".glb": "model/gltf-binary", ".gltf": "model/gltf+json", ".obj": "model/obj", ".mtl": "text/plain",
              ".ply": "application/octet-stream", ".stl": "model/stl", ".png": "image/png", ".jpg": "image/jpeg"}


@functools.cache
def blender_cmd() -> list | None:
    """How to start Blender here: installed as a program, or as the Flatpak from Flathub; None = not there."""
    if shutil.which("blender"):
        return ["blender"]
    if shutil.which("flatpak"):
        try:
            if subprocess.run(["flatpak", "info", "org.blender.Blender"], capture_output=True, timeout=10).returncode == 0:
                return ["flatpak", "run", "org.blender.Blender"]
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def mesh_file(slug: str, name: str) -> Path | None:
    """One file of a scene's mesh, by its plain name (nothing outside the mesh folder)."""
    d = scene_dir(slug) if slug and not slug.startswith(".") else None
    if not d or not name or Path(name).name != name or name.startswith(".") or name == "mesh.json":
        return None
    f = d / "mesh" / name
    return f if f.is_file() and f.suffix.lower() in MESH_TYPES else None


def open_in_blender(slug: str):
    """Blender with the mesh imported – GLB if there is one, else OBJ, PLY or STL."""
    cmd = blender_cmd()
    if not cmd:
        return err("err.no_blender")
    d = scene_dir(slug) if slug and not slug.startswith(".") else None
    m = d / "mesh" if d else None
    files = {f.suffix.lower(): f for f in m.iterdir()} if m and m.is_dir() else {}
    importers = {".glb": "bpy.ops.import_scene.gltf", ".obj": "bpy.ops.wm.obj_import",
                 ".ply": "bpy.ops.wm.ply_import", ".stl": "bpy.ops.wm.stl_import"}
    ext = next((e for e in importers if e in files), None)
    if not ext:
        return err("err.no_mesh")
    code = (f"import bpy; bpy.ops.wm.read_factory_settings(use_empty=True); "
            f"{importers[ext]}(filepath={str(files[ext])!r})")
    subprocess.Popen([*cmd, "--python-expr", code], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    return None


def mesh_of(slug: str) -> dict:
    """The mesh of a scene or tour: its files, and whether a texture is possible (training images still there)."""
    d = scene_dir(slug) if slug and not slug.startswith(".") else None
    if not d:
        return {}
    texture = (d / "dataset" / "images").is_dir() and (d / "dataset" / "sparse").is_dir()
    m = d / "mesh"
    info = {"texture_possible": texture, "files": [], "made": None, "blender": blender_cmd() is not None}
    if (m / "mesh.json").exists():
        try:
            made = json.loads((m / "mesh.json").read_text())
        except (OSError, ValueError):
            made = {}
        src = d / "spirula" / "scene.ply"
        info["made"] = {**made, "stale": src.exists() and made.get("created", 0) < src.stat().st_mtime}
        info["files"] = [{"name": f.name, "size": f.stat().st_size} for f in sorted(m.iterdir())
                         if f.is_file() and f.name != "mesh.json"]
    return info


def start_mesh(p: dict):
    """A mesh of a scene or tour in the chosen formats (glb, ply, obj, stl), optionally with a texture."""
    global proc
    slug = p.get("slug", "")
    d = scene_dir(slug) if slug and not slug.startswith(".") else None
    if not d or not (d / "spirula" / "scene.ply").exists():
        return err("err.no_scene")
    formats = [f for f in dict.fromkeys((p.get("formats") or "").split(",")) if f]
    if not formats or any(f not in MESH_FORMATS for f in formats):
        return err("err.bad_value", key="formats")
    texture = bool(p.get("texture")) and mesh_of(slug)["texture_possible"]
    name = read_meta(slug).get("name") or slug
    with lock:
        if (cur := load_state().get("job")) and pid_alive(cur["pid"]):
            return err("err.job_busy")
        if scene_busy(slug):
            return err("err.scene_busy")
        JOBS.mkdir(parents=True, exist_ok=True)
        out = JOBS / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_mesh_{slug}.out"
        with open(out, "wb") as fh:
            proc = subprocess.Popen(keep_awake([str(SCRIPTS / "mesh.sh"), str(d), ",".join(formats), "1" if texture else "0"],
                                               t("inhibit.mesh", name=name)), cwd=ROOT, stdout=fh,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        st = load_state()
        st["job"] = {"kind": "mesh", "pid": proc.pid, "out": str(out), "name": name, "slug": slug,
                     "recording": "", "quality": "", "upload": False, "started": time.time(), "overrides": {},
                     "steps": [t("mesh.step.mesh"), t("mesh.step.upright")]}
        save_state(st)
    return None


def delete_mesh(slug: str):
    d = scene_dir(slug) if slug and not slug.startswith(".") else None
    if not d or not (d / "mesh").is_dir():
        return err("err.no_mesh")
    if scene_busy(slug):
        return err("err.scene_busy")
    try:
        r = subprocess.run(["gio", "trash", "--", str(d / "mesh")], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return err("err.trash_failed", detail=str(e))
    if r.returncode != 0:
        return err("err.trash_failed", detail=(r.stderr.strip().splitlines() or [""])[-1])
    usage_cache.pop(d, None)
    return None


def start_upload(slug: str, version: str = ""):
    """Copy a scene – or one of its versions – to the Frame. Every version gets its own scene folder there."""
    if not FRAME:
        return err("err.no_frame")
    d = scene_dir(slug)
    if not d or not (d / "scene.sog").exists():
        return err("err.no_scene")
    vdir = None
    if version:
        vdir = d / "versions" / Path(version).name
        if version.startswith(".") or not (vdir / "scene.sog").exists():
            return err("err.no_version")
    key = f"{slug}#{version}" if version else slug
    with lock:
        u = uploads.get(key)
        if u and u["proc"].poll() is None:
            return err("err.upload_busy")
        meta = read_meta(slug)
        env = {**os.environ}
        name = meta.get("name") or slug
        if vdir:
            info = read_version(vdir)
            name = info.get("label") or name
            env.update(STEM=f"{slug}-{version}".lower(), SLUG=f"{slug}-{version}",
                       LOG=str(RUNS / Path(meta.get("log") or f"{slug}.log").name))
            if folder := (meta.get("version_folders") or {}).get(version):
                env["TARGET"] = folder
        else:
            for k, var in (("frame_folder", "TARGET"), ("stem", "STEM")):
                if meta.get(k):
                    env[var] = meta[k]
            env.update(world_scale_env(meta))
            if meta.get("log"):
                env["LOG"] = str(RUNS / Path(meta["log"]).name)
        JOBS.mkdir(parents=True, exist_ok=True)
        out = JOBS / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_to-frame_{slug}{'-' + version if version else ''}.out"
        with open(out, "wb") as fh:
            p = subprocess.Popen(keep_awake([str(SCRIPTS / "to-frame.sh"), str(vdir or d), name],
                                            t("inhibit.upload", name=name)), cwd=ROOT, env=env,
                                 stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 start_new_session=True)
        uploads[key] = {"proc": p, "out": out, "started": time.time(), "notified": False}
    return None


def upload_status(slug: str):
    u = uploads.get(slug)
    if not u:
        return None
    rc = u["proc"].poll()
    text = u["out"].read_text(errors="replace") if u["out"].exists() else ""
    res = {"running": rc is None, "ok": rc == 0}
    if rc is not None and not u["notified"]:
        u["notified"] = True
        name = read_meta(slug).get("name") or slug
        if rc == 0:
            if (d := RX_DONE.search(text)) and d[2]:
                scene, _, version = slug.partition("#")
                if version:
                    folders = read_meta(scene).get("version_folders") or {}
                    write_meta(scene, version_folders={**folders, version: d[2]})
                else:
                    write_meta(slug, frame_folder=d[2])
            frame_cache["t"] = 0
            notify(t("notify.upload.title"), t("notify.upload", name=name))
        else:
            notify(t("notify.upload_failed.title"), t("notify.upload_failed", name=name))
    if rc not in (None, 0):
        res["error"] = "\n".join(text.strip().splitlines()[-3:]) or "error"
    return res


# ---------------------------------------------------------------- scenes

def meta_file(slug: str) -> Path:
    return RUNS / f"{slug}.ui.json"


def read_meta(slug: str) -> dict:
    try:
        return json.loads(meta_file(slug).read_text())
    except (OSError, ValueError):
        return {}


def write_meta(slug: str, **kw) -> None:
    with meta_lock:
        meta = read_meta(slug)
        meta.update(kw)
        write_json(meta_file(slug), meta)


PREVIOUS = RUNS / ".previous"      # splat.sh keeps a scene here while it is computed again (FRESH)


def restore_previous():
    """splat.sh puts a replaced scene back itself when the new run fails. If it could not
    (killed hard after cancelling, power cut), do it here once no run of that scene is going."""
    if not PREVIOUS.is_dir():
        return
    with lock:                                  # start_job holds it until the new job is recorded
        job = load_state().get("job") or {}
        busy = job.get("slug") if pid_alive(job.get("pid", 0)) else None
        for prev in PREVIOUS.iterdir():
            slug, run, out = prev.name, prev / "run", RUNS / prev.name
            if slug == busy:
                continue
            if run.is_dir() and not (out / "scene.sog").exists():
                if out.exists():
                    shutil.rmtree(prev / "failed", ignore_errors=True)
                    out.rename(prev / "failed")
                run.rename(out)
                if (prev / "log").exists():
                    (prev / "log").replace(RUNS / f"{slug}.log")
                with meta_lock:
                    meta = read_meta(slug)
                    if isinstance(meta.get("previous"), dict):
                        write_json(meta_file(slug), meta["previous"])
            shutil.rmtree(prev, ignore_errors=True)
        try:
            PREVIOUS.rmdir()
        except OSError:
            pass


def scenes():
    fr = frame_listing()
    out = []
    if not RUNS.is_dir():
        return out
    found = [(d, False) for d in RUNS.glob("*/") if (d / "scene.sog").exists() and not is_group(read_meta(d.name))]
    # A scene that is being computed again stays in the list (without actions) until it is replaced
    found += [(d, True) for d in PREVIOUS.glob("*/run/")
              if (d / "scene.sog").exists() and not (RUNS / d.parent.name / "scene.sog").exists()]
    for d, replacing in sorted(found, key=lambda x: x[0].stat().st_mtime, reverse=True):
        sog = d / "scene.sog"
        slug = d.parent.name if replacing else d.name
        meta = read_meta(slug)
        if replacing and isinstance(meta.get("previous"), dict):
            meta = meta["previous"]
        on_frame = None
        if fr is not None:
            folder = meta.get("frame_folder")
            on_frame = (folder in fr["scenes"]) if folder else any(
                re.fullmatch(r"\d\d_" + re.escape(slug), s) for s in fr["scenes"])
        gallery = next(d.glob("*.gallery.png"), None)
        gauss = None
        for log in (RUNS / f"{slug}.log", RUNS / f"{slug}.out"):
            if log.exists() and (g := RX_GAUSS.search(log.read_text(errors="replace"))):
                gauss = int(g[1].replace(",", ""))
                break
        out.append({"slug": slug, "name": meta.get("name") or slug, "quality": meta.get("quality"),
                    "recording": meta.get("recording"), "size": sog.stat().st_size,
                    "mtime": int(sog.stat().st_mtime), "gaussians": gauss, "preview": bool(gallery),
                    "ply": (d / "spirula" / "scene.ply").exists(), "on_frame": on_frame, "disk": disk_usage(d),
                    "upload": upload_status(slug), "replacing": replacing,
                    "scale": meta.get("scale") or 1, "scale_xr": bool(meta.get("scale_xr")),
                    "versions": len(list(d.glob("versions/*/version.json"))), "mesh": (d / "mesh" / "mesh.json").exists()})
    return out


# ---------------------------------------------------------------- floor plans (scripts/floorplan.py)

plan_locks = {}                    # cache key -> Lock, so one plan is drawn only once at a time


def floorplan(slugs: list[str], style: str, lang: str, title: str, group: str = ""):
    """(PNG bytes, info dict with pixels_per_metre) of one or more scenes (cached), or an error dict.
    The plan of a group is drawn in the group's scale, if it has one; otherwise all scenes need the same."""
    if style not in ("pretty", "classic"):
        style = "pretty"
    gmeta = read_meta(group) if group and Path(group).name == group and not group.startswith(".") else {}
    if gmeta and not is_group(gmeta):
        return err("err.bad_request")
    room_of = dict(group_scenes(group, gmeta)) if gmeta else {}         # one label per room
    runs = []
    for slug in dict.fromkeys(slugs):
        d = scene_dir(slug)
        ply = d / "spirula" / "scene.ply" if d else None
        if not ply or not ply.exists():
            return err("err.no_scene")
        runs.append((d, room_of.get(slug) or read_meta(slug).get("name") or slug, ply.stat().st_mtime))
    if not runs or len(runs) > 12:
        return err("err.bad_request")
    if (gmeta.get("scale") or 1) != 1:
        ratio = gmeta["scale"]
    else:
        scales = {read_meta(slug).get("scale") or 1 for slug in dict.fromkeys(slugs)}
        if len(scales) > 1:
            return err("err.mixed_scale")
        ratio = scales.pop()
    title = title.strip()[:60]
    script = (SCRIPTS / "floorplan.py").stat().st_mtime        # a new version of the script draws anew
    key = hashlib.sha1(json.dumps([[str(r[0]), r[1], r[2]] for r in runs] + [style, lang, title, script, ratio]).encode()).hexdigest()[:16]
    out = CACHE / f"plan-{key}.png"
    meta = out.with_suffix(".json")
    with lock:
        plan_lock = plan_locks.setdefault(key, threading.Lock())
    with plan_lock:
        if not out.exists() or not meta.exists():
            CACHE.mkdir(parents=True, exist_ok=True)
            cmd = ["uv", "run", "-q", str(SCRIPTS / "floorplan.py"), str(out.with_suffix(".tmp.png")),
                   *[f"{d}={name}" for d, name, _ in runs], "--style", style]
            if title:
                cmd += ["--title", title]
            if lang == "de":
                cmd.append("--comma")
            if ratio != 1:
                cmd += ["--ratio", str(ratio)]
            if not plan_slots.acquire(timeout=1):
                return err("err.plan_busy")
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            except subprocess.TimeoutExpired:
                return err("err.plan_failed", detail="timeout")
            finally:
                plan_slots.release()
            if r.returncode != 0 or not out.with_suffix(".tmp.png").exists():
                return err("err.plan_failed", detail=(r.stderr or r.stdout).strip().splitlines()[-1:] or "")
            meta.write_text(r.stdout.strip().splitlines()[-1])
            out.with_suffix(".tmp.png").replace(out)
    try:
        info = json.loads(meta.read_text())
    except ValueError:
        info = {}
    return out.read_bytes(), info


def scene_dir(slug: str) -> Path | None:
    d = (RUNS / slug).resolve()
    return d if d.parent == RUNS.resolve() and d.is_dir() else None


# ---------------------------------------------------------------- renaming and cleaning up

# What a finished scene can do without: intermediate state (lossless) and the training images (a new
# training then has to start from the recording again). Scene, floor plan and Spirula viewer keep working.
CLEANUP = {"temp": ("spirula/training", "dataset/.spirula-sfm"), "images": ("dataset/images",)}
usage_cache = {}                   # run folder -> (time, sizes)


def dir_size(p: Path) -> int:
    total = 0
    try:
        for e in os.scandir(p):
            if e.is_dir(follow_symlinks=False):
                total += dir_size(Path(e.path))
            elif e.is_file(follow_symlinks=False):
                total += e.stat(follow_symlinks=False).st_blocks * 512
    except OSError:
        pass
    return total


def disk_usage(d: Path) -> dict:
    hit = usage_cache.get(d)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    sizes = {k: sum(dir_size(d / rel) for rel in rels) for k, rels in CLEANUP.items()}
    sizes["total"] = dir_size(d)
    usage_cache[d] = (time.time(), sizes)
    return sizes


def no_resume(slug: str):
    """Why an existing run can only be started over: its training images were deleted, or its recording
    was renamed (the pipeline remembers the recording's path and refuses to resume with another one)."""
    meta = read_meta(slug)
    if "images" in (meta.get("cleaned") or []):
        return "images"
    return "renamed" if meta.get("recording_renamed") or meta.get("folder_renamed") else None


def scene_busy(slug: str) -> bool:
    job = load_state().get("job") or {}
    u = uploads.get(slug)
    return (job.get("slug") == slug and pid_alive(job.get("pid", 0))) or bool(u and u["proc"].poll() is None)


def clean_scene(slug: str, what: str):
    d = scene_dir(slug)
    if not d or not (d / "scene.sog").exists():
        return err("err.no_scene")
    if what not in CLEANUP:
        return err("err.bad_request")
    if scene_busy(slug):
        return err("err.scene_busy")
    for rel in CLEANUP[what]:
        shutil.rmtree(d / rel, ignore_errors=True)
    meta = read_meta(slug)
    write_meta(slug, cleaned=sorted(set(meta.get("cleaned") or []) | {what}))
    usage_cache.pop(d, None)
    return None


QUALITY_ALIASES = {"schnell": "fast", "hoch": "high", "maximal": "max"}   # runs from the first version


def unfinished_runs():
    """Runs without a finished scene (cancelled, failed, interrupted), except the one computing now."""
    if not RUNS.is_dir():
        return []
    job = load_state().get("job") or {}
    busy = job.get("slug") if pid_alive(job.get("pid", 0)) else None
    metas = {f.name.removesuffix(".ui.json"): read_meta(f.name.removesuffix(".ui.json")) for f in RUNS.glob("*.ui.json")}
    used_logs = {Path(m["log"]).name for m in metas.values() if m.get("log")}
    slugs = {d.name for d in RUNS.iterdir() if d.is_dir()} | set(metas)
    slugs |= {f.name.removesuffix(".log") for f in RUNS.glob("*.log") if f.name not in used_logs}
    out = []
    for slug in sorted(slugs):
        d, log = RUNS / slug, RUNS / f"{slug}.log"
        meta = metas.get(slug, {})
        if slug.startswith(".") or slug == busy or (d / "scene.sog").exists() or (PREVIOUS / slug).exists() \
                or is_group(meta):
            continue
        parsed = parse_output(log.read_text(errors="replace")) if log.exists() and d.is_dir() else None
        parts = [p for p in (d, log, RUNS / f"{slug}.out", meta_file(slug)) if p.exists()]
        size = sum(dir_size(p) if p.is_dir() else p.stat().st_size for p in parts)
        rec = meta.get("recording")
        startable = bool(meta.get("name") and rec and local_path(rec) and config.slugify(meta["name"]) == slug)
        out.append({"slug": slug, "name": meta.get("name") or slug, "recording": rec,
                    "mtime": int(max(p.stat().st_mtime for p in parts)), "size": size,
                    "phase": parsed["phase"] if parsed else None, "pct": parsed["pct"] if parsed else None,
                    "resumable": startable and d.is_dir() and resumable_run(slug), "restartable": startable,
                    "folder": d.is_dir()})
    return sorted(out, key=lambda r: r["mtime"], reverse=True)


def resumable_run(slug: str) -> bool:
    """The pipeline continues only after the camera positions are complete (an interrupted training
    is restarted by splat.sh with them); an earlier interruption has to start over."""
    try:
        done = json.loads((RUNS / slug / "dataset" / "spirula-dataset.json").read_text()).get("status") == "complete"
    except (OSError, ValueError):
        return False
    return done and not no_resume(slug)


def resume_run(slug: str, fresh: bool):
    meta = read_meta(slug)
    rec = local_path(meta.get("recording") or "")
    if not rec or not meta.get("name") or config.slugify(meta["name"]) != slug:
        return err("err.not_resumable")
    if not fresh and not resumable_run(slug):
        return err("err.not_resumable")
    quality = QUALITY_ALIASES.get(meta.get("quality"), meta.get("quality") or "high")
    return start_job({"path": rec, "name": meta["name"], "quality": quality, "existing": "fresh" if fresh else "resume",
                      "upload": bool(FRAME) and frame_listing() is not None}, part=meta.get("part") or "")


def delete_scene(slug: str):
    """Scene or unfinished run: folder, log and settings into the desktop trash (recoverable);
    a copy on the Frame stays."""
    if not slug or Path(slug).name != slug or slug.startswith("."):
        return err("err.no_scene")
    if scene_busy(slug):
        return err("err.scene_busy")
    d = scene_dir(slug)
    files = ([d] if d else []) + [p for p in (RUNS / f"{slug}.log", RUNS / f"{slug}.out", meta_file(slug)) if p.exists()]
    if not files:
        return err("err.no_scene")
    try:
        r = subprocess.run(["gio", "trash", "--", *map(str, files)], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return err("err.trash_failed", detail=str(e))
    if r.returncode != 0:
        return err("err.trash_failed", detail=(r.stderr.strip().splitlines() or [""])[-1])
    usage_cache.pop(d, None)
    uploads.pop(slug, None)
    # a part's piece cut out of its recording (kept for resuming): not needed any more, and cut again
    # from the recording any time – deleted, not into the trash (it is as big as that stretch of video)
    for cut in (RUNS / ".parts" / f"{slug}.mp4", RUNS / ".parts" / f"{slug}.mp4.tmp.mp4"):
        cut.unlink(missing_ok=True)
    try:
        (RUNS / ".parts").rmdir()
    except OSError:
        pass
    for g, gm in all_groups().items():
        if slug in gm.get("members", []):
            write_meta(g, members=[m for m in gm["members"] if m != slug])
    return None


def clean_group(slug: str, what: str):
    """Clean up the same kind of data in every scene of a group (the tour itself needs none of it)."""
    meta = read_meta(slug) if slug and Path(slug).name == slug else {}
    if not is_group(meta):
        return err("err.no_scene")
    if what not in CLEANUP:
        return err("err.bad_request")
    members = [m for m, _ in group_scenes(slug, meta)]
    if scene_busy(slug) or any(scene_busy(m) for m in members):
        return err("err.scene_busy")
    for m in members:
        if e := clean_scene(m, what):
            return e
    return None


def clean_name(name: str, file: bool = True) -> str:
    """A name from user input without control characters; as a file name also without path separators."""
    name = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f]", "", name or "")).strip()
    if file:
        name = re.sub(r"\s*[/\\]\s*", " - ", name).strip(" .-")
    return name[:80]


def move_scene(slug: str, new: str) -> None:
    """Scene folder, its log/settings/output and the gallery files inside get the new folder name.
    The folder on the Frame stays (remembered in frame_folder), so no second copy appears there."""
    d, nd = RUNS / slug, RUNS / new
    meta = read_meta(slug)
    if not meta.get("frame_folder") and (fr := frame_listing()) is not None:
        meta["frame_folder"] = next((s for s in fr["scenes"] if re.fullmatch(r"\d\d_" + re.escape(slug), s)), None)
    old_stem, new_stem = meta.get("stem") or slug.lower(), new.lower()
    d.rename(nd)
    renames = [(nd, old_stem, new_stem), (nd / "full", f"{old_stem}-full", f"{new_stem}-full")]
    renames += [(v, f"{slug}-{v.name}".lower(), f"{new}-{v.name}".lower()) for v in (nd / "versions").glob("*/")]
    for folder, old, new_ in renames:
        for f in folder.glob(f"{glob_escape(old)}.*"):         # full/: the full version of a tour
            if f.name[len(old):] in (".sog", ".xrSceneTransform.json", ".gallery.png", ".sceneinfo.png"):
                f.rename(folder / (new_ + f.name[len(old):]))
    log = RUNS / Path(meta.get("log") or f"{slug}.log").name
    if log.exists():
        log.rename(RUNS / f"{new}.log")
    if (RUNS / f"{slug}.out").exists():
        (RUNS / f"{slug}.out").rename(RUNS / f"{new}.out")
    for k in ("stem", "log"):
        meta.pop(k, None)
    if not meta.get("frame_folder"):
        meta.pop("frame_folder", None)
    meta["folder_renamed"] = True               # the pipeline's own files still name the old folder
    with meta_lock:
        write_json(meta_file(new), meta)
        meta_file(slug).unlink(missing_ok=True)
    usage_cache.pop(d, None)
    uploads.pop(slug, None)
    with lock:
        st = load_state()
        if (st.get("job") or {}).get("slug") == slug:
            st["job"]["slug"] = new
            save_state(st)
    for g, gm in all_groups().items():
        if slug in gm.get("members", []):
            write_meta(g, members=[new if m == slug else m for m in gm["members"]])


def glob_escape(s: str) -> str:
    return re.sub(r"([*?\[])", r"[\1]", s)


def rename_scene(slug: str, name: str, update_frame: bool):
    d = scene_dir(slug)
    name = clean_name(name, file=False)
    if not d or not (d / "scene.sog").exists():
        return err("err.no_scene")
    if not name:
        return err("err.bad_name")
    if scene_busy(slug):
        return err("err.scene_busy")
    new = config.slugify(name)
    if not new or new.startswith("."):
        return err("err.bad_name")
    if new != slug:
        if (RUNS / new).exists() or meta_file(new).exists() or (RUNS / f"{new}.log").exists():
            return err("err.scene_name_taken")
        move_scene(slug, new)
        slug, d = new, RUNS / new
    meta = read_meta(slug)
    write_meta(slug, name=name)
    # New name plate for the gallery; the preview image stays if the training images are gone
    env = {**os.environ, "GALLERY_ONLY": "1"}
    if meta.get("stem"):
        env["STEM"] = meta["stem"]
    if meta.get("log"):
        env["LOG"] = str(RUNS / Path(meta["log"]).name)
    try:
        subprocess.run([str(SCRIPTS / "to-frame.sh"), str(d), name], cwd=ROOT, env=env, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        pass                                    # the name is saved; the plate is redone on the next upload
    return start_upload(slug) if update_frame else None


def rename_recording(name: str, new: str):
    src = REC / Path(name).name
    new = clean_name(new).removesuffix(".mp4")
    if not src.is_file() or src.parent.resolve() != REC.resolve():
        return err("err.not_local")
    if not new:
        return err("err.bad_name")
    dst = REC / f"{new}.mp4"
    if dst.exists():
        return err("err.name_taken")
    job = load_state().get("job") or {}
    if job.get("recording") == src.name and pid_alive(job.get("pid", 0)):
        return err("err.recording_busy")
    if checks.get(src.name, {}).get("state") in ("queued", "running"):
        return err("err.recording_busy")
    users = []
    for m in RUNS.glob("*.ui.json"):
        try:
            if json.loads(m.read_text()).get("recording") == src.name:
                users.append(m.name.removesuffix(".ui.json"))
        except (OSError, ValueError):
            pass
    unfinished = [s for s in users if (RUNS / s).is_dir() and not (RUNS / s / "scene.sog").exists()]
    if unfinished:
        # The pipeline would refuse to resume it with a renamed recording
        return err("err.recording_unfinished", name=read_meta(unfinished[0]).get("name") or unfinished[0])
    with lock:
        os.rename(src, dst)                     # keeps size and mtime, so cached results stay valid
        meta = rec_meta()
        orig = meta.pop(src.name, {}).get("orig", src.name)
        if orig != dst.name:
            meta[dst.name] = {"orig": orig}
        write_json(RECMETA, meta, indent=1)
        # Cached thumbnail and check result belong to the file name
        old_stem, new_stem = src.stem, dst.stem
        if (CACHE / f"{old_stem}.thumb").exists():
            os.replace(CACHE / f"{old_stem}.thumb", CACHE / f"{new_stem}.thumb")
        for f in CACHE.glob("*.check.json"):
            if f.name.startswith(old_stem + ".") and re.fullmatch(r"\.\d+-\d+\.v\d+\.check\.json",
                                                                 f.name[len(old_stem):]):
                os.replace(f, CACHE / (new_stem + f.name[len(old_stem):]))
        if src.name in checks:
            checks[dst.name] = checks.pop(src.name)
        for m in RUNS.glob("*.ui.json"):
            if read_meta(slug := m.name.removesuffix(".ui.json")).get("recording") == src.name:
                write_meta(slug, recording=dst.name, recording_renamed=True)
        st = load_state()
        if (st.get("job") or {}).get("recording") == src.name:
            st["job"]["recording"] = dst.name
            save_state(st)
    return None


def recording_in_use(name: str):
    """Why a recording can't be deleted right now (converted, checked or fetched), or None."""
    job = load_state().get("job") or {}
    if job.get("recording") == name and pid_alive(job.get("pid", 0)):
        return err("err.recording_busy")
    if checks.get(name, {}).get("state") in ("queued", "running"):
        return err("err.recording_busy")
    if download_busy() and (name in [f["name"] for f in download_queue] or (download and download["name"] == name)):
        return err("err.recording_fetching")
    return None


def delete_recording(name: str, force: bool = False):
    """A recording on this PC into the desktop trash (recoverable). Only from the recordings folder.
    If parts were cut from it, only when asked for once more (`force`): it is the original for cutting again."""
    src = REC / Path(name).name
    if not name or not src.is_file() or src.parent.resolve() != REC.resolve():
        return err("err.not_local")
    if e := recording_in_use(src.name):
        return e
    unfinished = [m.name.removesuffix(".ui.json") for m in RUNS.glob("*.ui.json")
                  if read_meta(m.name.removesuffix(".ui.json")).get("recording") == src.name]
    unfinished = [s for s in unfinished if (RUNS / s).is_dir() and not (RUNS / s / "scene.sog").exists()]
    if unfinished:
        return err("err.recording_needed", name=read_meta(unfinished[0]).get("name") or unfinished[0])
    # parts are cut from the original again when needed (start over, resume): ask once more
    parts = [m.name.removesuffix(".ui.json") for m in RUNS.glob("*.ui.json")
             if (pm := read_meta(m.name.removesuffix(".ui.json"))).get("recording") == src.name and pm.get("part")]
    if parts and not force:
        return err("err.recording_has_parts", name=read_meta(parts[0]).get("name") or parts[0], n=len(parts))
    try:
        r = subprocess.run(["gio", "trash", "--", str(src)], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return err("err.trash_failed", detail=str(e))
    if r.returncode != 0:
        return err("err.trash_failed", detail=(r.stderr.strip().splitlines() or [""])[-1])
    with lock:
        meta = rec_meta()
        if meta.pop(src.name, None) is not None:
            write_json(RECMETA, meta, indent=1)
        checks.pop(src.name, None)
    frame_cache["t"] = 0
    return None


def delete_frame_recording(name: str):
    """A recording on the Frame, for good (the Frame has no trash). Only from the Frame's recordings."""
    if not FRAME:
        return err("err.no_frame")
    fr = frame_listing(force=True)
    if fr is None:
        return err("err.frame_offline")
    if not any(f["name"] == name for f in fr["files"]) or Path(name).name != name:
        return err("err.not_on_frame")
    if e := recording_in_use(name):
        return e
    try:
        r = subprocess.run(SSH + [f"rm -f -- {FRAME_VIDEOS}/{shq(name)}"], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return err("err.frame_offline")
    if r.returncode != 0:
        return err("err.frame_failed", detail=(r.stderr.strip().splitlines() or [""])[-1])
    frame_cache["t"] = 0
    return None


# ---------------------------------------------------------------- groups and tours (scripts/combine.sh)

# A group is a <slug>.ui.json with kind "group" and its member scenes; once put together, the tour
# lives in <slug>/ like a scene (scene.sog = Frame version, full/ = all splats, spirula/scene.ply).
# A group marked "room" is one room made of several recordings (only scenes). Any other group may hold
# scenes and such rooms; putting it together always starts from the recordings, each in its room.
ALIGNS = ("frame", "images", "shift", "rigid")
PARTITIONS = ("view", "nearest")
SESSION_GAP = 20 * 60                      # recordings closer than this belong to one session (suggestion)


def is_group(meta: dict) -> bool:
    return meta.get("kind") == "group"


def is_room(meta: dict) -> bool:
    return is_group(meta) and bool(meta.get("room"))


def group_scenes(slug: str, meta: dict = None) -> list:
    """The scenes of a group, each with the name of its room: [(scene slug, room name)]. A scene on its own
    is a room of its own; the scenes of a room (also of a room inside the group) all share its name."""
    meta = meta if meta is not None else read_meta(slug)
    if not is_group(meta):
        return []
    scene = lambda m, mm: scene_dir(m) and not is_group(mm)
    if is_room(meta):
        return [(m, meta.get("name") or slug) for m in meta.get("members", []) if scene(m, read_meta(m))]
    out, names = [], {}
    for m in meta.get("members", []):
        mm = read_meta(m)
        if is_room(mm):
            found = [s_ for s_ in mm.get("members", []) if scene(s_, read_meta(s_))]
        elif scene(m, mm):
            found = [m]
        else:
            continue
        room = base = mm.get("name") or m
        k = 1
        while room in names and names[room] != m:          # two rooms of the same name stay two rooms
            k += 1
            room = f"{base} ({k})"
        names[room] = m
        out += [(s_, room) for s_ in found]
    return out


def parent_group(slug: str):
    return next((g for g, gm in all_groups().items() if slug in gm.get("members", [])), None)


def all_groups() -> dict:
    out = {}
    for f in RUNS.glob("*.ui.json"):
        slug = f.name.removesuffix(".ui.json")
        if is_group(meta := read_meta(slug)):
            out[slug] = meta
    return out


def recorded_at(meta: dict):
    """When the scene's recording was made (from the Frame's file name), or None."""
    rec = meta.get("recording") or ""
    orig = rec_meta().get(rec, {}).get("orig", rec)
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)_(\d\d)-(\d\d)-(\d\d)", orig)
    return time.mktime(time.strptime("-".join(m.groups()), "%Y-%m-%d-%H-%M-%S")) if m else None


def mem_available() -> float:
    try:
        kb = next(int(l.split()[1]) for l in Path("/proc/meminfo").read_text().splitlines() if l.startswith("MemAvailable"))
        return round(kb / 1048576, 1)
    except (OSError, StopIteration, ValueError):
        return 0.0


def groups():
    scene_list = {s["slug"]: s for s in scenes()}
    fr = frame_listing()
    out = []
    every = all_groups()
    parents = {m: g for g, gm in every.items() for m in gm.get("members", [])}
    for slug, meta in sorted(every.items(), key=lambda kv: (kv[1].get("name") or kv[0]).lower()):
        members = [m for m in meta.get("members", []) if m in scene_list
                   or (not is_room(meta) and m in every and is_room(every[m]))]
        inner = [(m, r) for m, r in group_scenes(slug, meta) if m in scene_list]
        d, res = RUNS / slug, None
        if (d / "scene.sog").exists():
            try:
                info = json.loads((d / "combine.json").read_text())
            except (OSError, ValueError):
                info = {}
            built = (d / "scene.sog").stat().st_mtime
            used = sorted(Path(r["run"]).name for r in info.get("rooms", {}).values())
            on_frame = None
            if fr is not None:
                folder = meta.get("frame_folder")
                on_frame = (folder in fr["scenes"]) if folder else any(
                    re.fullmatch(r"\d\d_" + re.escape(slug), x) for x in fr["scenes"])
            full = d / "full" / "scene.sog"
            res = {"size": (d / "scene.sog").stat().st_size, "full_size": full.stat().st_size if full.exists() else None,
                   "splats": info.get("splats"), "frame_splats": meta.get("frame_splats"),
                   "align": info.get("align"), "partition": info.get("partition"), "hub": info.get("hub"),
                   "seams": info.get("seams", {}), "mtime": int(built),
                   "stale": used != sorted(m for m, _ in inner)
                            or any((RUNS / m / "scene.sog").stat().st_mtime > built for m, _ in inner),
                   "preview": bool(next(d.glob("*.gallery.png"), None)), "ply": (d / "spirula" / "scene.ply").exists(),
                   "on_frame": on_frame, "upload": upload_status(slug), "disk": disk_usage(d)}
        out.append({"slug": slug, "name": meta.get("name") or slug, "members": members, "result": res,
                    "room": is_room(meta), "parent": parents.get(slug), "scenes": [m for m, _ in inner],
                    "rooms": len({r for _, r in inner}),
                    "busy": scene_busy(slug), "options": {k: meta.get(k) for k in ("align", "partition", "frame_splats", "full")},
                    "scale": meta.get("scale") or 1, "scale_xr": bool(meta.get("scale_xr")),
                    "versions": len(list(d.glob("versions/*/version.json"))), "mesh": (d / "mesh" / "mesh.json").exists(),
                    "splats": sum(scene_list[m].get("gaussians") or 0 for m, _ in inner)})
    return {"groups": out, "suggestion": suggestion(scene_list, out), "mem_available_gb": mem_available()}


def suggestion(scene_list: dict, group_list: list):
    """Scenes recorded right after each other and not in a group yet: offer them as one group."""
    grouped = {m for g in group_list for m in g["members"]}
    timed = sorted((t, slug) for slug in scene_list if slug not in grouped
                   and (t := recorded_at(read_meta(slug))) is not None)
    runs, cur = [], []
    for t, slug in timed:
        if cur and t - cur[-1][0] > SESSION_GAP:
            runs.append(cur)
            cur = []
        cur.append((t, slug))
    runs.append(cur)
    dismissed = set(load_state().get("dismissed_groups", []))
    for r in sorted(runs, key=len, reverse=True):
        key = ",".join(sorted(s for _, s in r))
        if len(r) >= 2 and key not in dismissed:
            return {"members": [s for _, s in r], "key": key, "date": time.strftime("%Y-%m-%d", time.localtime(r[0][0]))}
    return None


def save_group(slug: str, name: str, members: str, room: bool = False):
    """Create (no slug) or change a group. Returns its slug, or an error. A room (room=True) holds scenes
    only; any other group scenes and rooms."""
    name = clean_name(name, file=False)
    if not name:
        return err("err.bad_name")
    members = [m for m in dict.fromkeys(members.split(",")) if m]
    scene_slugs = {s["slug"] for s in scenes()}
    existing = all_groups()
    rooms = {g for g, gm in existing.items() if is_room(gm) and g != slug}
    allowed = scene_slugs if room else scene_slugs | rooms
    if not members or any(m not in allowed for m in members):
        return err("err.bad_request")
    if slug and not room and parent_group(slug):           # inside another group it has to stay a room
        return err("err.room_in_group", group=existing[parent_group(slug)].get("name") or parent_group(slug))
    for g, gm in existing.items():
        if g != slug and (both := set(gm.get("members", [])) & set(members)):
            return err("err.in_other_group", group=gm.get("name") or g)
    new = config.slugify(name)
    if not new or new.startswith("."):
        return err("err.bad_name")
    taken = lambda x: (RUNS / x).exists() or meta_file(x).exists() or (RUNS / f"{x}.log").exists()
    if not slug:
        if taken(new):
            return err("err.scene_name_taken")
        write_json(meta_file(new), {"kind": "group", "name": name, "members": members, "room": room})
        return new
    if slug not in existing:
        return err("err.no_scene")
    if scene_busy(slug):
        return err("err.scene_busy")
    renamed = name != existing[slug].get("name")
    if new != slug:
        if taken(new):
            return err("err.scene_name_taken")
        if (RUNS / slug).exists():
            move_scene(slug, new)
        else:
            meta_file(slug).rename(meta_file(new))
            if (RUNS / f"{slug}.log").exists():
                (RUNS / f"{slug}.log").rename(RUNS / f"{new}.log")
            for g, gm in all_groups().items():                  # a room inside another group
                if slug in gm.get("members", []):
                    write_meta(g, members=[new if m == slug else m for m in gm["members"]])
        slug = new
    write_meta(slug, name=name, members=members, room=room)
    d = RUNS / slug
    if renamed and (d / "scene.sog").exists():                  # new name plates on the tour
        stem = slug.lower()
        cmds = []
        runs = [m for m in members if (RUNS / m / "spirula" / "scene.ply").exists()]
        if room and (d / "plan.png").exists() and runs:
            # a room's floor plan carries its name: drawn again, and the preview images from it
            plan = d / "plan.new.png"
            cmds.append((["uv", "run", "-q", str(SCRIPTS / "floorplan.py"), str(plan),
                          *[f"{RUNS / m}={name}" for m in runs], *(["--comma"] if t.lang == "de" else [])], {}))
            cmds.append((["mv", str(plan), str(d / "plan.png")], {}))
            cmds.append((["uv", "run", "-q", str(SCRIPTS / "make_gallery.py"), "--plan", str(d / "plan.png"),
                          str(d), name, stem], {}))
        cmds.append(([str(SCRIPTS / "to-frame.sh"), str(d), name], {"GALLERY_ONLY": "1", "LOG": str(RUNS / f"{slug}.log")}))
        if (d / "full" / "scene.sog").exists() and (d / "plan.png").exists():
            cmds.append((["uv", "run", "-q", str(SCRIPTS / "make_gallery.py"), "--plan", str(d / "plan.png"),
                          str(d / "full"), t("tour.full_name", name=name), f"{stem}-full"], {}))
        for cmd, extra in cmds:
            try:
                subprocess.run(cmd, cwd=ROOT, timeout=120, env={**os.environ, **extra},
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                pass
    return slug


def start_combine(p: dict):
    global proc
    slug = p.get("slug", "")
    meta = read_meta(slug) if slug and Path(slug).name == slug else {}
    if not is_group(meta):
        return err("err.no_scene")
    align, partition = p.get("align") or "images", p.get("partition") or "view"
    if align not in ALIGNS or partition not in PARTITIONS:
        return err("err.bad_request")
    try:
        frame_splats = int(p.get("frame_splats") or 1_000_000)
    except (TypeError, ValueError):
        return err("err.bad_value", key="frame_splats")
    if not 500_000 <= frame_splats <= 10_000_000:
        return err("err.range", key="frame_splats", lo=500_000, hi=10_000_000)
    scene_list = {s["slug"]: s for s in scenes()}
    inner = [(m, r) for m, r in group_scenes(slug, meta) if m in scene_list
             and (RUNS / m / "spirula" / "scene.ply").exists() and (RUNS / m / "dataset/sparse/0/images.bin").exists()]
    members = [m for m, _ in inner]
    if len(members) < 2:
        return err("err.combine_too_few")
    full = bool(p.get("full", True))
    with lock:
        if (cur := load_state().get("job")) and pid_alive(cur["pid"]):
            return err("err.job_busy")
        if any(scene_busy(m) for m in members) or scene_busy(slug):
            return err("err.scene_busy")
        name = meta.get("name") or slug
        env = {**os.environ, "ALIGN": align, "PARTITION": partition, "FRAME_SPLATS": str(frame_splats),
               "FULL": "1" if full else "0", "FULL_NAME": t("tour.full_name", name=name)}
        if t.lang == "de":
            env["PLAN_COMMA"] = "1"
        if meta.get("frame_folder"):
            env["TARGET"] = meta["frame_folder"]
        env.update(world_scale_env(meta))
        upload = bool(p.get("upload")) and bool(FRAME)
        if not upload:
            env["NO_UPLOAD"] = "1"
        pairs = [f"{RUNS / m}={scene_list[m]['name']}" for m in members]
        if len({r for _, r in inner}) < len(inner):              # a room made of several recordings
            env["ROOMS"] = json.dumps([r for _, r in inner], ensure_ascii=False)
        JOBS.mkdir(parents=True, exist_ok=True)
        out = JOBS / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_{slug}.out"
        with open(out, "wb") as fh:
            proc = subprocess.Popen(keep_awake([str(SCRIPTS / "combine.sh"), slug, name, *pairs],
                                               t("inhibit.combine", name=name)), cwd=ROOT, env=env, stdout=fh,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        st = load_state()
        st["job"] = {"kind": "combine", "pid": proc.pid, "out": str(out), "name": name, "slug": slug,
                     "recording": "", "rooms": len({r for _, r in inner}), "recordings": len(inner), "quality": align, "partition": partition,
                     "upload": upload, "full": full, "started": time.time(), "overrides": {}}
        save_state(st)
        write_meta(slug, align=align, partition=partition, frame_splats=frame_splats, full=full)
    return None


def pick_file():
    start = str(REC if REC.exists() else Path.home())
    title = "Aufnahme wählen" if t.lang == "de" else "Choose a recording"
    for cmd in (["kdialog", "--title", title, "--getopenfilename", start, "Videos (*.mp4)"],
                ["zenity", "--file-selection", f"--title={title}", f"--filename={start}/", "--file-filter=*.mp4"]):
        if shutil.which(cmd[0]):
            r = subprocess.run(cmd, capture_output=True, text=True)
            return r.stdout.strip() or None
    return None


def shutdown(cancel: bool):
    if cancel:
        cancel_job()
    # answer the request first, then exit
    threading.Timer(0.3, lambda: os._exit(0)).start()


# ---------------------------------------------------------------- HTTP

STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"), "/icon.svg": ("icon.svg", "image/svg+xml")}


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = f"SplatWorkshop/{VERSION}"

    def log_message(self, *a):
        pass

    def _host_ok(self):
        """Host header (DNS rebinding), the client's real address, and no requests from other websites."""
        client = ipaddress.ip_address(self.client_address[0])
        if not (client.is_loopback or (CFG.lan and client.is_private)):
            return False
        # Browsers name the origin of every request; another site (even another port on localhost) is refused.
        # Only opening the page itself via a link is fine.
        site = self.headers.get("Sec-Fetch-Site", "same-origin")
        page = self.command == "GET" and urllib.parse.urlparse(self.path).path in STATIC \
            and self.headers.get("Sec-Fetch-Mode") == "navigate"
        if site not in ("same-origin", "none") and not page:
            return False
        return host_allowed((self.headers.get("Host") or "").rsplit(":", 1)[0])

    def _same_origin(self):
        """POST only from our own page (Origin must match Host) – protects against other websites."""
        origin = self.headers.get("Origin", "")
        return not origin or urllib.parse.urlparse(origin).netloc == self.headers.get("Host", "")

    def _from_this_pc(self):
        return ipaddress.ip_address(self.client_address[0]).is_loopback

    def send(self, code, body, ctype="application/json; charset=utf-8", cache=False):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _safely(self, handler):
        try:
            handler()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:                       # noqa: BLE001 – answer instead of dropping the connection
            traceback.print_exc()
            try:
                self.send(500, {"error": err("err.bad_request")})
            except OSError:
                pass

    def do_GET(self):
        self._safely(self._get)

    def do_POST(self):
        self._safely(self._post)

    def _get(self):
        global last_request
        if not self._host_ok():
            return self.send(403, {"error": err("err.forbidden")})
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path != "/api/activity":           # the tray polls this; it does not count as an open page
            last_request = time.time()
        if u.path in STATIC:
            f, ctype = STATIC[u.path]
            return self.send(200, (WEB / f).read_bytes(), ctype)
        if u.path == "/api/ping":
            here = self._from_this_pc()
            ask_for_update()
            ip = lan_ip() if CFG.lan else None
            return self.send(200, {
                "ok": True, "app": "splat-workshop", "version": VERSION, "local": here,
                "lan_url": f"http://{ip}:{CFG.port}/" if ip and here else None,
                "language": CFG.language, "frame_configured": bool(FRAME),
                "problems": [err(k, **p) for k, p in CFG.problems()],
                "config": str(CFG.source or config.CONFIG_FILE) if here else None, "update": update_info()})
        if u.path == "/api/activity":
            return self.send(200, activity())
        if u.path == "/api/recordings":
            if q.get("refresh"):
                frame_cache["t"] = 0
            return self.send(200, recordings())
        if u.path == "/api/probe":
            path = q.get("path", "")
            if local_path(Path(path).name) != path:
                return self.send(200, {"ok": False, "errors": [err("err.unknown_recording")], "warnings": []})
            return self.send(200, probe(path))
        if u.path == "/api/thumb":
            data = thumbnail(q.get("source", ""), Path(q.get("name", "")).name)
            return self.send(200, data, "image/png", cache=True) if data else self.send(404, b"", "image/png")
        if u.path == "/api/check":
            return self.send(200, check_status(Path(q.get("name", "")).name))
        if u.path == "/api/status":
            ups = {s: upload_status(s) for s in list(uploads)}
            return self.send(200, {"job": job_status(), "download": download_status(), "uploads": ups,
                                   "standby": standby_status()})
        if u.path == "/api/scenes":
            return self.send(200, scenes())
        if u.path == "/api/unfinished":
            return self.send(200, unfinished_runs())
        if u.path == "/api/groups":
            return self.send(200, groups())
        if u.path == "/api/frame/gallery":
            g = frame_gallery(force=bool(q.get("refresh")))
            return self.send(200, {"online": g is not None, "configured": bool(FRAME), "scenes": g or []})
        if u.path == "/api/frame/preview":
            data = frame_preview(q.get("folder", ""), q.get("place", ""))
            return self.send(200, data, "image/png", cache=True) if data else self.send(404, b"", "image/png")
        if u.path == "/api/versions":
            return self.send(200, {"versions": versions_of(q.get("slug", ""))})
        if u.path == "/api/mesh":
            return self.send(200, mesh_of(q.get("slug", "")))
        if u.path == "/api/mesh/file":
            f = mesh_file(q.get("slug", ""), q.get("name", ""))
            if not f:
                return self.send(404, {"error": err("err.no_mesh")})
            self.send_response(200)
            self.send_header("Content-Type", MESH_TYPES[f.suffix.lower()])
            self.send_header("Content-Length", str(f.stat().st_size))
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(f.name))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            with open(f, "rb") as fh:
                shutil.copyfileobj(fh, self.wfile, 1 << 20)
            return None
        if u.path == "/api/floorplan":
            data = floorplan([x for x in q.get("slugs", "").split(",") if x], q.get("style", "pretty"),
                             q.get("lang", "en"), q.get("title", ""), q.get("group", ""))
            if isinstance(data, dict):
                return self.send(400, {"error": data})
            data, info = data
            self.send_response(200)
            self.send_header("X-Pixels-Per-Metre", str(info.get("pixels_per_metre", "")))
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if q.get("download"):
                name = re.sub(r"[^A-Za-z0-9._-]+", "-", q.get("name") or "floorplan").strip("-") or "floorplan"
                self.send_header("Content-Disposition", f'attachment; filename="{name}.png"')
            self.end_headers()
            self.wfile.write(data)
            return
        if u.path == "/api/preview":
            d = scene_dir(q.get("slug", ""))
            img = next(d.glob("*.gallery.png"), None) if d else None
            prev = PREVIOUS / Path(q.get("slug", "")).name / "run"
            if not img and q.get("slug", "").strip(".") and prev.is_dir():
                img = next(prev.glob("*.gallery.png"), None)
            return self.send(200, img.read_bytes(), "image/png") if img else self.send(404, b"", "image/png")
        return self.send(404, {"error": err("err.not_found")})

    def _post(self):
        global last_request
        if not self._host_ok() or not self._same_origin():
            return self.send(403, {"error": err("err.forbidden")})
        last_request = time.time()
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 <= n <= 1 << 20:
                raise ValueError
            p = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            p = None
        # Only flat objects of simple values; text fields are text
        if not isinstance(p, dict) or any(not isinstance(v, (str, int, float, bool, type(None))) for v in p.values()) \
                or any(not isinstance(p.get(k, ""), str) for k in ("name", "new", "slug", "path", "what", "quality", "existing", "members", "key", "version", "sizes", "scales", "folder",
                                                           "names", "place", "where",
                                                           "align", "partition", "clean", "formats")):
            return self.send(400, {"error": err("err.bad_request")})
        path = urllib.parse.urlparse(self.path).path
        e = None
        if path in ("/api/pick", "/api/play", "/api/open", "/api/shutdown") and not self._from_this_pc():
            # These open windows on this PC's screen or stop the program – only allowed from the PC itself
            return self.send(403, {"error": err("err.pc_only")})
        if path == "/api/start":
            e = start_job(p)
            if e == "exists":
                slug = config.slugify((p.get("name") or "").strip())
                return self.send(409, {"error": err("exists"), "no_resume": no_resume(slug)})
        elif path == "/api/parts":
            e = start_parts(p)
            if e == "exists":
                e = err("err.scene_name_taken")
        elif path == "/api/cancel":
            e = cancel_job()
        elif path == "/api/download":
            fr = frame_listing()
            names = p["names"].split("\n") if p.get("names") else [p.get("name")]     # several: one per line
            on_frame = {f["name"]: f for f in (fr or {}).get("files", [])}
            files = [on_frame[n] for n in names if isinstance(n, str) and n in on_frame]
            e = start_download(files) if files else err("err.not_on_frame")
        elif path == "/api/download/cancel":
            e = cancel_download()
        elif path == "/api/upload":
            e = start_upload(p.get("slug", ""), p.get("version", ""))
        elif path == "/api/versions":
            e = start_versions(p)
        elif path == "/api/frame/remove":
            e = (remove_frame_folder(p["folder"], p.get("place", "")) if p.get("folder") else
                 remove_from_frame(p.get("slug", ""), p.get("version", "")))
        elif path == "/api/frame/move":
            e = move_to_own_folder()
        elif path == "/api/mesh":
            e = start_mesh(p)
        elif path == "/api/mesh/delete":
            e = delete_mesh(p.get("slug", ""))
        elif path == "/api/version/delete":
            e = delete_version(p.get("slug", ""), p.get("version", ""))
        elif path == "/api/pick":
            chosen = pick_file()
            if chosen:
                picked.add(chosen)
            return self.send(200, {"path": chosen})
        elif path == "/api/check":
            e = start_check(Path(p.get("name", "")).name)
        elif path == "/api/recording/delete":
            e = delete_frame_recording(p.get("name", "")) if p.get("where") == "frame" else (
                delete_recording(p.get("name", ""), bool(p.get("force"))) if p.get("where") == "pc" else err("err.bad_request"))
        elif path == "/api/rename":
            if p.get("slug"):
                e = rename_scene(p["slug"], p.get("new", ""), bool(p.get("update_frame")))
            else:
                e = rename_recording(p.get("name", ""), p.get("new", ""))
                if not e:
                    return self.send(200, {"ok": True, "name": clean_name(p.get("new", "")).removesuffix(".mp4") + ".mp4"})
        elif path == "/api/clean":
            slug = p.get("slug", "")
            e = (clean_group if slug and is_group(read_meta(slug)) else clean_scene)(slug, p.get("what", ""))
        elif path == "/api/delete":
            e = delete_scene(p.get("slug", ""))
        elif path == "/api/group":
            e = save_group(p.get("slug", ""), p.get("name", ""), p.get("members", ""), bool(p.get("room")))
            if isinstance(e, str):
                return self.send(200, {"ok": True, "slug": e})
        elif path == "/api/scale":
            e = set_scale(p.get("slug", ""), p.get("scale"), bool(p.get("xr")), bool(p.get("with_scenes")),
                          bool(p.get("update_frame")))
        elif path == "/api/group/dismiss":
            st = load_state()
            st["dismissed_groups"] = sorted(set(st.get("dismissed_groups", [])) | {p.get("key", "")})[-50:]
            save_state(st)
        elif path == "/api/standby":
            return self.send(200, set_standby(bool(p.get("on"))))
        elif path == "/api/combine":
            e = start_combine(p)
        elif path == "/api/resume":
            e = resume_run(p.get("slug", ""), bool(p.get("fresh")))
            if e == "exists":                   # e.g. the training images were cleaned up in the meantime
                e = err("err.not_resumable")
        elif path == "/api/play":
            local = local_path(Path(p.get("name", "")).name)
            if not local:
                e = err("err.not_local")
            else:
                subprocess.Popen([*CFG.video_player, local], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif path == "/api/open":
            d = scene_dir(p.get("slug", ""))
            if not d:
                e = err("err.no_scene")
            elif p.get("what") == "blender":
                e = open_in_blender(p.get("slug", ""))
            elif p.get("what") == "spirula":
                ply = d / "spirula" / "scene.ply"
                if p.get("version"):                    # a version: its own ply, or the scene's for a plain "all"
                    v = {x["key"]: x for x in versions_of(p.get("slug", ""))}.get(p["version"])
                    own = d / "versions" / p["version"] / "spirula" / "scene.ply"
                    ply = own if v and own.exists() else ply if v and v["ply"] else None
                if ply is None:
                    e = err("err.no_version")
                else:
                    subprocess.Popen([str(CFG.spirula), str(ply)], cwd=ply.parent.parent,
                                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                folder = d / "mesh" if p.get("what") == "mesh" and (d / "mesh").is_dir() else d
                subprocess.Popen(["xdg-open", str(folder)], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif path == "/api/shutdown":
            self.send(200, {"ok": True})
            return shutdown(bool(p.get("cancel_job")))
        else:
            return self.send(404, {"error": err("err.not_found")})
        return self.send(400 if e else 200, {"error": e} if e else {"ok": True})


def main():
    global idle_exit
    ap = argparse.ArgumentParser(description="Splat Workshop web server")
    ap.add_argument("--no-idle-exit", action="store_true", help="keep running when idle (used by the tray icon)")
    args = ap.parse_args()
    idle_exit = not args.no_idle_exit
    threading.Thread(target=watcher, daemon=True).start()
    srv = http.server.ThreadingHTTPServer(("0.0.0.0" if CFG.lan else "127.0.0.1", CFG.port), Handler)
    srv.daemon_threads = True
    msg = f"Splat Workshop {VERSION} on http://127.0.0.1:{CFG.port}/"
    if CFG.lan:
        msg += f" and in your network on http://{lan_ip()}:{CFG.port}/"
    print(msg, flush=True)
    for key, params in CFG.problems():
        print(f"  setup: {key} {params}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
