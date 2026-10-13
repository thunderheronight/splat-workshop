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
import urllib.parse
from pathlib import Path

from . import check, config
from .i18n import T, detect

VERSION = "0.1.0"
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


def save_state(st: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1))
    tmp.replace(STATE)


def pid_alive(pid: int) -> bool:
    try:
        return "splat.sh" in Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
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


def local_files():
    seen, out = set(), []
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
        out.append({"name": p.name, "path": str(p), "size": st.st_size, "mtime": int(st.st_mtime),
                    "date": pretty_date(p.stem), "check": cr["level"] if cr else None})
    return out


def local_path(name: str):
    """Path of a recording on this PC – only from the known list, never taken from the browser."""
    return next((f["path"] for f in local_files() if f["name"] == name),
                next((p for p in picked if Path(p).name == name and Path(p).is_file()), None))


def recordings():
    fr = frame_listing()
    local = local_files()
    local_names = {f["name"]: f["path"] for f in local}
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
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{dur / 3:.2f}", "-i", local, "-map", "0:v:0",
                            "-frames:v", "1", "-vf", "scale=360:-1,eq=gamma=1.3,format=rgb24",
                            "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True)
        if r.stdout:
            cached.write_bytes(r.stdout)       # don't cache failures
        return r.stdout or None
    if source != "frame":
        return None
    # No preview on the Frame: remember the empty result for 10 min only, then try again
    if cached.exists() and time.time() - cached.stat().st_mtime < 600:
        return None
    fr = frame_listing()
    if fr is None:
        return None
    data = b""
    match = next((x for x in fr["thumbs"] if stem in x or stem.removesuffix("_stereo") in x), None)
    if match:
        r = subprocess.run(SSH + [f"cat {FRAME_VIDEOS}/thumbnails/{shq(match)}"], capture_output=True, timeout=20)
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


def parse_output(text: str) -> dict:
    text = text.replace("\r", "\n")
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
        if (RUNS / slug).exists():
            if p.get("existing") == "resume":
                args.append("--resume")
            elif p.get("existing") == "fresh":
                env["FRESH"] = "1"
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
        write_meta(slug, name=name, recording=Path(path).name, quality=env["QUALITY"])
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
                notify(t("notify.done.title"),
                       t("notify.done.frame" if js["upload"] else "notify.done.local", name=js["name"], mins=mins))
            elif js["result"] == "failed":
                notify(t("notify.failed.title"), t("notify.failed", name=js["name"]))
        was_running = running
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
    meta = read_meta(slug)
    meta.update(kw)
    meta_file(slug).write_text(json.dumps(meta, ensure_ascii=False))


def scenes():
    fr = frame_listing()
    out = []
    if not RUNS.is_dir():
        return out
    for d in sorted(RUNS.glob("*/"), key=lambda d: d.stat().st_mtime, reverse=True):
        sog = d / "scene.sog"
        if not sog.exists():
            continue
        slug = d.name
        meta = read_meta(slug)
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
                    "ply": (d / "spirula" / "scene.ply").exists(), "on_frame": on_frame,
                    "upload": upload_status(slug)})
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
    key = hashlib.sha1(json.dumps([[str(r[0]), r[1], r[2]] for r in runs] + [style, lang, title]).encode()).hexdigest()[:16]
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
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
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

    def do_GET(self):
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
            return self.send(200, img.read_bytes(), "image/png") if img else self.send(404, b"", "image/png")
        return self.send(404, {"error": err("err.not_found")})

    def do_POST(self):
        global last_request
        if not self._host_ok() or not self._same_origin():
            return self.send(403, {"error": err("err.forbidden")})
        last_request = time.time()
        try:
            n = int(self.headers.get("Content-Length") or 0)
            p = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self.send(400, {"error": err("err.bad_request")})
        path = urllib.parse.urlparse(self.path).path
        e = None
        if path in ("/api/pick", "/api/play", "/api/open", "/api/shutdown") and not self._from_this_pc():
            # These open windows on this PC's screen or stop the program – only allowed from the PC itself
            return self.send(403, {"error": err("err.pc_only")})
        if path == "/api/start":
            e = start_job(p)
            if e == "exists":
                return self.send(409, {"error": err("exists")})
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
