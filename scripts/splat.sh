#!/bin/bash
# Arcturus Vision recording -> Gaussian splat -> scene in the Gaussian XR gallery on the Steam Frame.
#
# Usage: scripts/splat.sh <recording> "<display name>" [extra av1_to_3dgs.py options]
#   <recording>     a local .mp4, or part of a file name on the Frame
#                   (e.g. "19-00" or "2026-10-03_19-00-53"); it is downloaded first
#   <display name>  shown on the name plate in the gallery, e.g. "Living room"
#
# Environment:
#   QUALITY=fast|high|max  preset (default: high)
#       fast  100 image pairs, half resolution, up to 1.5 M splats (~10 min, for trying things out)
#       high  ~5 image pairs per second of video (100-500), full resolution, up to 2 M splats
#       max   ~8 image pairs per second of video (100-500), full resolution, up to 3 M splats, 50k steps
#   FRAMES, SCALE, MAXG, ITER  override single preset values
#   NO_UPLOAD=1   only compute, don't copy to the Frame
#   FRESH=1       discard an existing run of the same name first
#   Interrupted? Run the same command again with --resume.
# Paths and the Frame address come from ~/.config/splat-workshop/config.toml
# (or PIPELINE_DIR, SPIRULA_BIN, SPLAT_TRANSFORM, RECORDINGS_DIR, RUNS_DIR, FRAME_HOST).
#
# Lines starting with "@@" are machine-readable progress markers for the web interface.
set -euo pipefail

HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
ROOT=$(dirname "$HERE")
eval "$(cd "$ROOT" && python3 -m splat_workshop.config --shell)"
FRAME_VIDEOS=Videos/ArcturusVisionCamera

src=${1:?Give a recording (.mp4 or part of a file name on the Frame)}
name=${2:?Give a display name, e.g. "Living room"}
shift 2

# Folder name from the display name: transliterate, keep only safe characters
slug=$(cd "$ROOT" && python3 -m splat_workshop.config --slug "$name")
case "$slug" in ''|.*) echo "The display name does not give a usable folder name" >&2; exit 1 ;; esac

[ -f "$PIPELINE_DIR/av1_to_3dgs.py" ] || { echo "Arcturus pipeline not found in $PIPELINE_DIR (see README)" >&2; exit 1; }
[ -x "$SPIRULA_BIN" ] || { echo "Spirula Studio not found at $SPIRULA_BIN (see README)" >&2; exit 1; }
[ -n "$SPLAT_TRANSFORM" ] || { echo "splat-transform not found (npm install -g @playcanvas/splat-transform)" >&2; exit 1; }

# 1. Recording
if [ -f "$src" ]; then
    in=$(realpath "$src")
else
    [ -n "$FRAME_HOST" ] || { echo "No such file, and no Frame configured: $src" >&2; exit 1; }
    remote=$(ssh -o ConnectTimeout=8 "$FRAME_HOST" "ls $FRAME_VIDEOS/*${src}*_stereo.mp4 2>/dev/null" || true)
    count=$(printf '%s\n' "$remote" | grep -c . || true)
    [ "$count" = 1 ] || { echo "$count recordings on the Frame match \"$src\":" >&2; printf '%s\n' "$remote" >&2; exit 1; }
    mkdir -p "$RECORDINGS_DIR"
    in=$RECORDINGS_DIR/$(basename "$remote")
    if [ ! -f "$in" ]; then
        echo "Downloading $(basename "$remote") from the Frame ..."
        scp -q "$FRAME_HOST:$remote" "$in.part" && mv "$in.part" "$in"
    fi
fi

out=$RUNS_DIR/$slug
log=$RUNS_DIR/$slug.log
mkdir -p "$RUNS_DIR"
if [ "${FRESH:-0}" = 1 ]; then rm -rf "$out" "$log"; fi

# Image pairs from the duration: <per second>, clamped to the pipeline limits 100..500
pairs() {
    local secs
    secs=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$in")
    awk -v s="$secs" -v r="$1" 'BEGIN { f = int(s * r + 0.5); if (f < 100) f = 100; if (f > 500) f = 500; print f }'
}

case "${QUALITY:-high}" in
    fast) frames=100;        scale=0.5; maxg=1500000; iter=30000 ;;
    high) frames=$(pairs 5); scale=1;   maxg=2000000; iter=30000 ;;
    max)  frames=$(pairs 8); scale=1;   maxg=3000000; iter=50000 ;;
    *) echo "QUALITY must be fast, high or max" >&2; exit 1 ;;
esac
quality=(--frames "${FRAMES:-$frames}" --training-image-scale "${SCALE:-$scale}"
         --max-gaussians "${MAXG:-$maxg}" --iterations "${ITER:-$iter}")
echo "Quality ${QUALITY:-high}: ${quality[*]}" | tee -a "$log"

# 2. Compute the Gaussian splat. Spirula in English, so the progress lines can be parsed.
cd "$PIPELINE_DIR"
SS_LANG=en uv run av1_to_3dgs.py "$in" "$out" \
  --backend spirula --sfm-backend spirula --no-undistort \
  --spirula "$SPIRULA_BIN" \
  --splat-transform "$SPLAT_TRANSFORM" \
  "${quality[@]}" "$@" 2>&1 | tee -a "$log"
[ -f "$out/scene.sog" ] || { echo "No scene.sog was produced, see $log" >&2; exit 1; }

# 3. Gallery files, then copy to the Frame (to-frame.sh also works on its own)
if [ "${NO_UPLOAD:-0}" = 1 ] || [ -z "$FRAME_HOST" ]; then
    GALLERY_ONLY=1 LOG="$log" "$HERE/to-frame.sh" "$out" "$name"
    echo "@@ done local"
    echo "Done (not copied to the Frame): $out"
    exit 0
fi
LOG="$log" "$HERE/to-frame.sh" "$out" "$name"
