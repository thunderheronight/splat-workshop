"""Load the Splat Workshop configuration.

Order: built-in defaults < ~/.config/splat-workshop/config.toml (or $SPLAT_WORKSHOP_CONFIG)
< environment variables (SPLAT_PORT, SPLAT_LAN, FRAME_HOST).

`python3 -m splat_workshop.config --shell` prints the values as shell assignments for scripts/.
"""
import os
import shlex
import shutil
import sys
import tomllib
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "splat-workshop"
CONFIG_FILE = Path(os.environ.get("SPLAT_WORKSHOP_CONFIG", CONFIG_DIR / "config.toml"))

DEFAULTS = {
    "paths": {
        "pipeline": "~/arcturus/av1-3dgs-pipeline",
        "spirula": "~/arcturus/spirula-studio/build_vulkan/spirula",
        "splat_transform": "",
        "recordings": "~/SplatWorkshop/recordings",
        "runs": "~/SplatWorkshop/runs",
        "extra_recording_dirs": ["~/Downloads", "~/Videos"],
        "video_player": "",
    },
    "frame": {"host": ""},
    "server": {"port": 8771, "lan": False, "idle_minutes": 20, "language": "auto"},
}


class Config:
    def __init__(self, data: dict, source: Path | None):
        self.source = source
        base = source.parent if source else Path.home()

        def path(v: str) -> Path:
            p = Path(os.path.expanduser(v))
            return p if p.is_absolute() else (base / p).resolve()

        p, f, s = data["paths"], data["frame"], data["server"]
        self.pipeline = path(p["pipeline"])
        self.spirula = path(p["spirula"])
        st = p.get("splat_transform") or shutil.which("splat-transform") or ""
        self.splat_transform = path(st) if st else None
        self.recordings = path(p["recordings"])
        self.runs = path(p["runs"])
        self.extra_recording_dirs = [path(d) for d in p.get("extra_recording_dirs", [])]
        # Command for "Open in video player" (e.g. "mpv"); empty = system default via xdg-open
        self.video_player = shlex.split(p.get("video_player", "")) or ["xdg-open"]
        self.frame_host = os.environ.get("FRAME_HOST", f.get("host", "")).strip()
        self.port = int(os.environ.get("SPLAT_PORT", s["port"]))
        lan = os.environ.get("SPLAT_LAN")
        self.lan = (lan != "0") if lan is not None else bool(s["lan"])
        self.idle_minutes = int(s["idle_minutes"])
        self.language = s.get("language", "auto")
        # Internal state (jobs, caches) lives next to the runs, so it moves with the data
        self.state_dir = Path(os.environ.get("SPLAT_WORKSHOP_STATE") or self.runs.parent / ".splat-workshop")

    def problems(self) -> list[tuple[str, dict]]:
        """Missing prerequisites as (message key, params) for the setup hints in the UI."""
        out = []
        if not (self.pipeline / "av1_to_3dgs.py").is_file():
            out.append(("setup.pipeline", {"path": str(self.pipeline)}))
        if not os.access(self.spirula, os.X_OK):
            out.append(("setup.spirula", {"path": str(self.spirula)}))
        if not self.splat_transform:
            out.append(("setup.splat_transform", {}))
        for tool in ("ffmpeg", "ffprobe", "uv"):
            if not shutil.which(tool):
                out.append(("setup.tool", {"tool": tool}))
        return out


def slugify(name: str) -> str:
    """Folder/file name from a display name: ä→ae etc., accents dropped, only [A-Za-z0-9._-]."""
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ß", "ss")):
        name = name.replace(a, b)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    out, dash = [], False
    for ch in name:
        if ch.isalnum() or ch in "._-":
            out.append(ch)
            dash = False
        elif not dash:
            out.append("-")
            dash = True
    return "".join(out).strip("-")


def _merge(base: dict, over: dict) -> dict:
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in base.items()}
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def load() -> Config:
    data, source = DEFAULTS, None
    if CONFIG_FILE.is_file():
        with open(CONFIG_FILE, "rb") as fh:
            data = _merge(DEFAULTS, tomllib.load(fh))
        source = CONFIG_FILE
    return Config(data, source)


def shell(cfg: Config) -> str:
    values = {
        "PIPELINE_DIR": cfg.pipeline, "SPIRULA_BIN": cfg.spirula, "SPLAT_TRANSFORM": cfg.splat_transform or "",
        "RECORDINGS_DIR": cfg.recordings, "RUNS_DIR": cfg.runs, "FRAME_HOST": cfg.frame_host,
    }
    # Values already set in the environment win: KEY=${KEY:-'default'}
    return "\n".join(f"{k}=${{{k}:-{shlex.quote(str(v))}}}" for k, v in values.items())


if __name__ == "__main__":
    if sys.argv[1:] == ["--shell"]:
        print(shell(load()))
    elif sys.argv[1:2] == ["--slug"] and len(sys.argv) == 3:
        print(slugify(sys.argv[2]))
    else:
        c = load()
        print(f"config: {c.source or '(defaults, no config file)'}")
        for k in ("pipeline", "spirula", "splat_transform", "recordings", "runs", "frame_host", "port", "lan"):
            print(f"  {k} = {getattr(c, k)}")
        for key, params in c.problems():
            print(f"  ! {key} {params}")
