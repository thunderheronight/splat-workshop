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
#   PART=120-245  only this stretch of the recording (seconds): cut losslessly with the original
#                 timestamps (the pipeline matches the images to the poses by time) into
#                 runs/.parts/<name>.mp4, which goes again once the scene is done. A long recording in
#                 several parts gets up to 500 image pairs per part.
#   FRESH=1       start an existing run of the same name over; its result stays until the new one
#                 is done and comes back if the new run fails or is cancelled
#   Interrupted? Run the same command again with --resume.
# Paths and the Frame address come from ~/.config/splat-workshop/config.toml
# (or PIPELINE_DIR, SPIRULA_BIN, SPLAT_TRANSFORM, RECORDINGS_DIR, RUNS_DIR, FRAME_HOST).
#
# Lines starting with "@@" are machine-readable progress markers for the web interface.
set -euo pipefail
# Everything in one block: bash reads it completely before it starts, so updating this file
# (git pull, editing) does not affect a run that is already going.
{

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

# A stretch of the recording: cut it once (the same file again for --resume)
secs=
if [ -n "${PART:-}" ]; then
    start=${PART%-*}; end=${PART#*-}
    [[ "$start" =~ ^[0-9]+(\.[0-9]+)?$ && "$end" =~ ^[0-9]+(\.[0-9]+)?$ ]] \
        && awk -v a="$start" -v b="$end" 'BEGIN { exit !(b > a) }' \
        || { echo "PART must be <start>-<end> in seconds, e.g. 120-245" >&2; exit 1; }
    cut=$RUNS_DIR/.parts/$slug.mp4
    if [ ! -f "$cut" ]; then
        mkdir -p "$RUNS_DIR/.parts"
        echo "Cutting $start-$end s out of $(basename "$in") ..."
        ffmpeg -v error -y -copyts -ss "$start" -i "$in" -to "$end" -map 0 -c copy -copy_unknown "$cut.tmp.mp4"
        mv "$cut.tmp.mp4" "$cut"
    fi
    in=$cut
    secs=$(awk -v a="$start" -v b="$end" 'BEGIN { print b - a }')
fi

# FRESH: set the previous result aside, delete it only once the new one is there
prev=$RUNS_DIR/.previous/$slug
restore_previous() {
    [ -d "$prev/run" ] && [ ! -f "$out/scene.sog" ] || return 0
    if [ -e "$out" ]; then mv "$out" "$prev/failed"; fi
    mv "$prev/run" "$out"
    if [ -f "$prev/log" ]; then mv "$prev/log" "$log"; fi
    # The web interface keeps the settings of the previous run in "previous"
    python3 - "$RUNS_DIR/$slug.ui.json" <<'PY' || true
import json, sys
try:
    meta = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    sys.exit()
if isinstance(meta.get("previous"), dict):
    json.dump(meta["previous"], open(sys.argv[1], "w"), ensure_ascii=False)
PY
    echo "The previous result of \"$name\" was kept."
    rm -rf "$prev"
    rmdir "$RUNS_DIR/.previous" 2>/dev/null || true
}
if [ "${FRESH:-0}" = 1 ]; then
    restore_previous                    # left over from an earlier run that never finished (e.g. power cut)
    rm -rf "$prev"
    if [ -f "$out/scene.sog" ]; then
        mkdir -p "$prev"
        mv "$out" "$prev/run"
        if [ -f "$log" ]; then mv "$log" "$prev/log"; fi
        trap restore_previous EXIT
        trap 'exit 143' TERM; trap 'exit 130' INT; trap 'exit 129' HUP
    else
        rm -rf "$out" "$log"
    fi
fi

# The pipeline resumes finished stages only: an interrupted training run blocks --resume.
# Then start the training over; images and camera positions are reused.
manifest=$out/spirula/run.json
if [[ " $* " == *" --resume "* ]] && [ -f "$manifest" ] && ! grep -q '"status": *"complete"' "$manifest"; then
    ckpt=$(ls -d "$out"/spirula/training/step-*.ckpt 2>/dev/null | tail -1)
    echo "Training was interrupted${ckpt:+ (last checkpoint: ${ckpt##*/step-})}; the pipeline cannot continue it." | tee -a "$log"
    echo "Restarting the training, images and camera positions are reused." | tee -a "$log"
    rm -rf "$out/spirula"
fi

# Image pairs from the duration: <per second>, clamped to the pipeline limits 100..500
pairs() {
    local secs=$secs
    [ -n "$secs" ] || secs=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$in")
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
if [ -n "${PART:-}" ]; then
    rm -f "$cut"                        # cut again from the recording when needed
    rmdir "$RUNS_DIR/.parts" 2>/dev/null || true
fi
rm -rf "$prev"
rmdir "$RUNS_DIR/.previous" 2>/dev/null || true

# 3. Gallery files, then copy to the Frame (to-frame.sh also works on its own)
if [ "${NO_UPLOAD:-0}" = 1 ] || [ -z "$FRAME_HOST" ]; then
    GALLERY_ONLY=1 LOG="$log" "$HERE/to-frame.sh" "$out" "$name"
    echo "@@ done local"
    echo "Done (not copied to the Frame): $out"
    exit 0
fi
LOG="$log" "$HERE/to-frame.sh" "$out" "$name"
exit
}
