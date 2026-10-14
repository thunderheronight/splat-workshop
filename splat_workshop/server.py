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
from pathlib import Path

from . import check, config
from .i18n import T, detect

VERSION = "0.2.0"
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
FRAME_SCENES = ".local/share/Steam/steamapps/common/ArcturusVisionGaussianXR/Scenes"
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
download = None                    # {"name", "total", "done", "error", "finished"}
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


def notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-a", t("app"), "-i", "applications-graphics", title, body],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
        return "splat.sh" in cmd or "combine.sh" in cmd
    except OSError:
        return False


def shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def err(code: str, **params) -> dict:
    """Error for the web interface: a key from web/i18n.js plus parameters."""
    return {"code": code, "params": params}


# ---------------------------------------------------------------- recordings

def frame_listing(force=False):
    """Recordings on the Frame (cached). None = no Frame configured or not reachable."""
    if not FRAME:
        return None
    if not force and time.time() - frame_cache["t"] < 20:
        return frame_cache["data"]
    cmd = (f'(cd {FRAME_VIDEOS} 2>/dev/null && stat -c "%n|%s|%Y" *_stereo.mp4 2>/dev/null); echo ---; '
           f'ls {FRAME_VIDEOS}/thumbnails 2>/dev/null; echo ---; ls {FRAME_SCENES} 2>/dev/null')
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

def start_download(name: str, size: int):
    global download
    with lock:
        if download and not download.get("finished"):
            return err("err.download_busy")
        if (REC / name).exists():
            return err("err.name_taken")
        download = {"name": name, "total": size, "done": 0, "error": None, "finished": False}

    def run():
        REC.mkdir(parents=True, exist_ok=True)
        part = REC / (name + ".part")
        try:
            with open(part, "wb") as fh:
                p = subprocess.Popen(keep_awake(SSH + [f"cat {FRAME_VIDEOS}/{shq(name)}"], t("inhibit.download")),
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                while chunk := p.stdout.read(1 << 20):
                    fh.write(chunk)
                    download["done"] += len(chunk)
                rc = p.wait()
            if rc != 0 or download["done"] != size:
                raise RuntimeError("transfer interrupted")
            part.rename(REC / name)
            frame_cache["t"] = 0
        except Exception as e:  # noqa: BLE001 – shown in the interface
            download["error"] = str(e)
            part.unlink(missing_ok=True)
        download["finished"] = True

    threading.Thread(target=run, daemon=True).start()
    return None


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


def parse_output(text: str) -> dict:
    text = text.replace("\r", "\n")
    if "@@ cphase" in text:
        return parse_combine(text)
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
    return {"phase": phase, "pct": pct, "eta": eta, "info": info}


def job_status(tail=True) -> dict | None:
    st = load_state().get("job")
    if not st:
        return None
    out = Path(st["out"])
    text = out.read_text(errors="replace") if out.exists() else ""
    running = (proc is not None and proc.poll() is None) or (proc is None and pid_alive(st["pid"]))
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


def start_job(p: dict):
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
        if not p.get("upload", True) or not FRAME:
            env["NO_UPLOAD"] = "1"
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
                     "overrides": {k: env[k] for k in ("FRAMES", "SCALE", "MAXG", "ITER") if k in env}}
        save_state(st)
        RUNS.mkdir(parents=True, exist_ok=True)
        write_meta(slug, name=name, recording=Path(path).name, quality=env["QUALITY"],
                   **({} if "--resume" in args else {"cleaned": [], "recording_renamed": False}),
                   **({"previous": previous} if previous is not None else {}))
    return None


def cancel_job():
    st = load_state()
    job = st.get("job")
    if not job or not pid_alive(job["pid"]):
        return err("err.no_job")
    job["cancelled"] = True
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
    dl = download if download and not download.get("finished") else None
    return {"job": job, "download": {"pct": round(100 * dl["done"] / max(dl["total"], 1))} if dl else None,
            "checking": check_busy(), "uploading": any(u["proc"].poll() is None for u in uploads.values())}


def watcher():
    """Notification when a run ends; stop the server after a longer idle time."""
    was_running = None
    while True:
        time.sleep(3)
        if proc is not None:
            proc.poll()                         # reap the zombie
        js = job_status(tail=False)
        running = bool(js and js["running"])
        if was_running and not running and js:
            mins = round((time.time() - js["started"]) / 60)
            if js["result"] == "ok":
                if js["info"].get("folder"):
                    write_meta(js["slug"], frame_folder=js["info"]["folder"])
                with meta_lock:                 # the replaced scene's settings are not needed any more
                    meta = read_meta(js["slug"])
                    if meta.pop("previous", None) is not None:
                        write_json(meta_file(js["slug"]), meta)
                notify(t("notify.done.title"),
                       t("notify.done.frame" if js["upload"] else "notify.done.local", name=js["name"], mins=mins))
            elif js["result"] == "failed":
                notify(t("notify.failed.title"), t("notify.failed", name=js["name"]))
        was_running = running
        if not running:
            restore_previous()
        for slug in list(uploads):
            upload_status(slug)                 # notify even without an open page
        busy = (running or (download and not download.get("finished")) or check_busy()
                or any(u["proc"].poll() is None for u in uploads.values()))
        if idle_exit and not busy and time.time() - last_request > CFG.idle_minutes * 60:
            os._exit(0)


# ---------------------------------------------------------------- copy to the Frame

def start_upload(slug: str):
    if not FRAME:
        return err("err.no_frame")
    d = scene_dir(slug)
    if not d or not (d / "scene.sog").exists():
        return err("err.no_scene")
    with lock:
        u = uploads.get(slug)
        if u and u["proc"].poll() is None:
            return err("err.upload_busy")
        meta = read_meta(slug)
        env = {**os.environ}
        for key, var in (("frame_folder", "TARGET"), ("stem", "STEM")):
            if meta.get(key):
                env[var] = meta[key]
        if meta.get("log"):
            env["LOG"] = str(RUNS / Path(meta["log"]).name)
        JOBS.mkdir(parents=True, exist_ok=True)
        out = JOBS / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_to-frame_{slug}.out"
        with open(out, "wb") as fh:
            p = subprocess.Popen(keep_awake([str(SCRIPTS / "to-frame.sh"), str(d), meta.get("name") or slug],
                                            t("inhibit.upload", name=meta.get("name") or slug)), cwd=ROOT, env=env,
                                 stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 start_new_session=True)
        uploads[slug] = {"proc": p, "out": out, "started": time.time(), "notified": False}
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
                    "upload": upload_status(slug), "replacing": replacing})
    return out


# ---------------------------------------------------------------- floor plans (scripts/floorplan.py)

plan_locks = {}                    # cache key -> Lock, so one plan is drawn only once at a time


def floorplan(slugs: list[str], style: str, lang: str, title: str):
    """(PNG bytes, info dict with pixels_per_metre) of one or more scenes (cached), or an error dict."""
    if style not in ("pretty", "classic"):
        style = "pretty"
    runs = []
    for slug in dict.fromkeys(slugs):
        d = scene_dir(slug)
        ply = d / "spirula" / "scene.ply" if d else None
        if not ply or not ply.exists():
            return err("err.no_scene")
        runs.append((d, read_meta(slug).get("name") or slug, ply.stat().st_mtime))
    if not runs or len(runs) > 12:
        return err("err.bad_request")
    title = title.strip()[:60]
    script = (SCRIPTS / "floorplan.py").stat().st_mtime        # a new version of the script draws anew
    key = hashlib.sha1(json.dumps([[str(r[0]), r[1], r[2]] for r in runs] + [style, lang, title, script]).encode()).hexdigest()[:16]
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
                      "upload": bool(FRAME) and frame_listing() is not None})


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
    members = [m for m in meta.get("members", []) if scene_dir(m)]
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
    for folder, old, new_ in ((nd, old_stem, new_stem), (nd / "full", f"{old_stem}-full", f"{new_stem}-full")):
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


# ---------------------------------------------------------------- groups and tours (scripts/combine.sh)

# A group is a <slug>.ui.json with kind "group" and its member scenes; once put together, the tour
# lives in <slug>/ like a scene (scene.sog = Frame version, full/ = all splats, spirula/scene.ply).
ALIGNS = ("frame", "images", "shift", "rigid")
PARTITIONS = ("view", "nearest")
SESSION_GAP = 20 * 60                      # recordings closer than this belong to one session (suggestion)


def is_group(meta: dict) -> bool:
    return meta.get("kind") == "group"


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
    for slug, meta in sorted(all_groups().items(), key=lambda kv: (kv[1].get("name") or kv[0]).lower()):
        members = [m for m in meta.get("members", []) if m in scene_list]
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
                   "stale": used != sorted(members) or any((RUNS / m / "scene.sog").stat().st_mtime > built for m in members),
                   "preview": bool(next(d.glob("*.gallery.png"), None)), "ply": (d / "spirula" / "scene.ply").exists(),
                   "on_frame": on_frame, "upload": upload_status(slug), "disk": disk_usage(d)}
        out.append({"slug": slug, "name": meta.get("name") or slug, "members": members, "result": res,
                    "busy": scene_busy(slug), "options": {k: meta.get(k) for k in ("align", "partition", "frame_splats", "full")},
                    "splats": sum(scene_list[m].get("gaussians") or 0 for m in members)})
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


def save_group(slug: str, name: str, members: str):
    """Create (no slug) or change a group. Returns its slug, or an error."""
    name = clean_name(name, file=False)
    if not name:
        return err("err.bad_name")
    members = [m for m in dict.fromkeys(members.split(",")) if m]
    scene_slugs = {s["slug"] for s in scenes()}
    if not members or any(m not in scene_slugs for m in members):
        return err("err.bad_request")
    existing = all_groups()
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
        write_json(meta_file(new), {"kind": "group", "name": name, "members": members})
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
        slug = new
    write_meta(slug, name=name, members=members)
    d = RUNS / slug
    if renamed and (d / "scene.sog").exists():                  # new name plates on the tour
        stem = slug.lower()
        cmds = [([str(SCRIPTS / "to-frame.sh"), str(d), name], {"GALLERY_ONLY": "1", "LOG": str(RUNS / f"{slug}.log")})]
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
        frame_splats = int(p.get("frame_splats") or 3_500_000)
    except (TypeError, ValueError):
        return err("err.bad_value", key="frame_splats")
    if not 500_000 <= frame_splats <= 10_000_000:
        return err("err.range", key="frame_splats", lo=500_000, hi=10_000_000)
    scene_list = {s["slug"]: s for s in scenes()}
    members = [m for m in meta.get("members", []) if m in scene_list
               and (RUNS / m / "spirula" / "scene.ply").exists() and (RUNS / m / "dataset/sparse/0/images.bin").exists()]
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
        upload = bool(p.get("upload")) and bool(FRAME)
        if not upload:
            env["NO_UPLOAD"] = "1"
        pairs = [f"{RUNS / m}={scene_list[m]['name']}" for m in members]
        JOBS.mkdir(parents=True, exist_ok=True)
        out = JOBS / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_{slug}.out"
        with open(out, "wb") as fh:
            proc = subprocess.Popen(keep_awake([str(SCRIPTS / "combine.sh"), slug, name, *pairs],
                                               t("inhibit.combine", name=name)), cwd=ROOT, env=env, stdout=fh,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        st = load_state()
        st["job"] = {"kind": "combine", "pid": proc.pid, "out": str(out), "name": name, "slug": slug,
                     "recording": "", "rooms": len(members), "quality": align, "partition": partition,
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
            ip = lan_ip() if CFG.lan else None
            return self.send(200, {
                "ok": True, "app": "splat-workshop", "version": VERSION, "local": here,
                "lan_url": f"http://{ip}:{CFG.port}/" if ip and here else None,
                "language": CFG.language, "frame_configured": bool(FRAME),
                "problems": [err(k, **p) for k, p in CFG.problems()],
                "config": str(CFG.source or config.CONFIG_FILE) if here else None})
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
            return self.send(200, {"job": job_status(), "download": download, "uploads": ups})
        if u.path == "/api/scenes":
            return self.send(200, scenes())
        if u.path == "/api/unfinished":
            return self.send(200, unfinished_runs())
        if u.path == "/api/groups":
            return self.send(200, groups())
        if u.path == "/api/floorplan":
            data = floorplan([x for x in q.get("slugs", "").split(",") if x], q.get("style", "pretty"),
                             q.get("lang", "en"), q.get("title", ""))
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
                or any(not isinstance(p.get(k, ""), str) for k in ("name", "new", "slug", "path", "what", "quality", "existing")):
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
        elif path == "/api/cancel":
            e = cancel_job()
        elif path == "/api/download":
            fr = frame_listing()
            f = next((f for f in (fr or {}).get("files", []) if f["name"] == p.get("name")), None)
            e = start_download(f["name"], f["size"]) if f else err("err.not_on_frame")
        elif path == "/api/upload":
            e = start_upload(p.get("slug", ""))
        elif path == "/api/pick":
            chosen = pick_file()
            if chosen:
                picked.add(chosen)
            return self.send(200, {"path": chosen})
        elif path == "/api/check":
            e = start_check(Path(p.get("name", "")).name)
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
            e = save_group(p.get("slug", ""), p.get("name", ""), p.get("members", ""))
            if isinstance(e, str):
                return self.send(200, {"ok": True, "slug": e})
        elif path == "/api/group/dismiss":
            st = load_state()
            st["dismissed_groups"] = sorted(set(st.get("dismissed_groups", [])) | {p.get("key", "")})[-50:]
            save_state(st)
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
            elif p.get("what") == "spirula":
                subprocess.Popen([str(CFG.spirula), str(d / "spirula" / "scene.ply")], cwd=d,
                                 start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", str(d)], start_new_session=True,
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
