#!/bin/bash
# Prepare a finished scene for the Gaussian XR gallery and copy it to the Steam Frame.
# Called by splat.sh at the end; also works on its own, e.g. if the Frame was off.
#
# Usage: scripts/to-frame.sh <run folder> "<display name>"
#   <run folder>    runs/<name> or just <name>
#   GALLERY_ONLY=1  only create the gallery files (preview image, name plate) in the run folder
#   TARGET=NN_name  use this scene folder on the Frame (default: same name, or next free number from 20)
#   STEM=name       file stem (default: folder name in lower case)
#   LOG=file        pipeline log with the initial view (default: runs/<folder>.log)
set -euo pipefail
# Everything in one block: bash reads it completely before it starts, so updating this file
# (git pull, editing) does not affect a run that is already going.
{

HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
ROOT=$(dirname "$HERE")
eval "$(cd "$ROOT" && python3 -m splat_workshop.config --shell)"
FRAME_SCENES=.local/share/Steam/steamapps/common/ArcturusVisionGaussianXR/Scenes
FRAME_BACKUP=Documents/GaussianSplats

run=${1:?Give a run folder}
name=${2:?Give a display name}
[ -d "$run" ] || run=$RUNS_DIR/$run
out=$(realpath "$run")
slug=$(basename "$out")
[ -f "$out/scene.sog" ] || { echo "No scene.sog in $out" >&2; exit 1; }
log=${LOG:-$RUNS_DIR/$slug.log}
[ -f "$log" ] || log=$(grep -ls "Gaussian XR initial view" "$RUNS_DIR"/*.log 2>/dev/null | xargs -r grep -l "$out/" | head -1)
[ -f "$log" ] || { echo "No pipeline log found for $slug (set LOG=...)" >&2; exit 1; }

# Gallery files: <stem>.sog / .xrSceneTransform.json / .gallery.png / .sceneinfo.png
stem=${STEM:-$(printf '%s' "$slug" | tr 'A-Z' 'a-z')}
cp "$out/scene.sog" "$out/$stem.sog"
cp "$out/scene.xrSceneTransform.json" "$out/$stem.xrSceneTransform.json"
uv run -q "$HERE/make_gallery.py" "$out" "$log" "$name" "$stem"

[ "${GALLERY_ONLY:-0}" = 1 ] && exit 0
[ -n "$FRAME_HOST" ] || { echo "No Frame configured (config.toml: [frame] host)" >&2; exit 1; }

echo "@@ phase upload"
echo "Copying to the Frame ..."
# Reuse an existing folder of the same name, otherwise the next free number from 20
folder=${TARGET:-$(ssh -o ConnectTimeout=8 "$FRAME_HOST" "
    existing=\$(ls -d $FRAME_SCENES/[0-9][0-9]_$slug 2>/dev/null | head -1)
    if [ -n \"\$existing\" ]; then basename \"\$existing\"; exit; fi
    n=20; while ls -d $FRAME_SCENES/\${n}_* >/dev/null 2>&1; do n=\$((n+1)); done
    echo \${n}_$slug")}
ssh -o ConnectTimeout=8 "$FRAME_HOST" "mkdir -p $FRAME_BACKUP/$folder $FRAME_SCENES/$folder"
scp -q "$out/$stem".{sog,xrSceneTransform.json,gallery.png,sceneinfo.png} "$FRAME_HOST:$FRAME_BACKUP/$folder/"
ssh "$FRAME_HOST" "cp $FRAME_BACKUP/$folder/$stem.* $FRAME_SCENES/$folder/ && chmod 755 $FRAME_SCENES/$folder/*"
echo "@@ done frame $folder"
echo
echo "Done: \"$name\" is in the Gaussian XR gallery ($folder)."
echo "Restart Gaussian XR to see it."
exit
}
