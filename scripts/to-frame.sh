#!/bin/bash
# Prepare a finished scene for the Gaussian XR gallery and copy it to the Steam Frame.
# Called by splat.sh at the end; also works on its own, e.g. if the Frame was off.
#
# Usage: scripts/to-frame.sh <run folder> "<display name>"
#   <run folder>    runs/<name> or just <name>
#   GALLERY_ONLY=1  only create the gallery files (preview image, name plate) in the run folder
#   TARGET=NN_name  use this scene folder on the Frame (default: same name, or next free number from 20)
#   STEM=name       file stem (default: folder name in lower case)
#   SLUG=name       name for a new scene folder on the Frame (default: the run folder's name)
#   LOG=file        pipeline log with the initial view (default: runs/<folder>.log)
set -euo pipefail
# Everything in one block: bash reads it completely before it starts, so updating this file
# (git pull, editing) does not affect a run that is already going.
{

HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
ROOT=$(dirname "$HERE")
eval "$(cd "$ROOT" && python3 -m splat_workshop.config --shell)"
FRAME_SCENES=.local/share/Steam/steamapps/common/ArcturusVisionGaussianXR/Scenes

run=${1:?Give a run folder}
name=${2:?Give a display name}
[ -d "$run" ] || run=$RUNS_DIR/$run
out=$(realpath "$run")
slug=${SLUG:-$(basename "$out")}
[ -f "$out/scene.sog" ] || { echo "No scene.sog in $out" >&2; exit 1; }
log=${LOG:-$RUNS_DIR/$slug.log}
[ -f "$log" ] || log=$(grep -ls "Gaussian XR initial view" "$RUNS_DIR"/*.log 2>/dev/null | xargs -r grep -l "$out/" | head -1)
[ -f "$log" ] || { echo "No pipeline log found for $slug (set LOG=...)" >&2; exit 1; }

# Gaussian XR's worldScale in a scene's transform file, when WORLD_SCALE is set (a model shown in the
# size of the original, e.g. 87 for H0)
world_scale() {
    [ -n "${WORLD_SCALE:-}" ] || return 0
    python3 - "$1" "$WORLD_SCALE" <<'PY'
import json, sys
path, scale = sys.argv[1], float(sys.argv[2])
data = json.load(open(path))
data["worldScale"] = scale
json.dump(data, open(path, "w"), indent=2)
PY
}

# Gallery files: <stem>.sog / .xrSceneTransform.json / .gallery.png / .sceneinfo.png
stem=${STEM:-$(printf '%s' "$slug" | tr 'A-Z' 'a-z')}
cp "$out/scene.sog" "$out/$stem.sog"
cp "$out/scene.xrSceneTransform.json" "$out/$stem.xrSceneTransform.json"
world_scale "$out/$stem.xrSceneTransform.json"
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
# Straight into the gallery – everything is on the PC, copying again restores a scene.
# Files of this scene under an earlier name (renamed in Splat Workshop) go first, so the folder holds one scene.
ssh -o ConnectTimeout=8 "$FRAME_HOST" "mkdir -p $FRAME_SCENES/$folder && cd $FRAME_SCENES/$folder && \
    for f in *.sog *.xrSceneTransform.json *.gallery.png *.sceneinfo.png; do
        case \$f in $stem.*) ;; *) rm -f -- \"\$f\" ;; esac
    done"
scp -q "$out/$stem".{sog,xrSceneTransform.json,gallery.png,sceneinfo.png} "$FRAME_HOST:$FRAME_SCENES/$folder/"
ssh "$FRAME_HOST" "chmod 755 $FRAME_SCENES/$folder/*"
echo "@@ done frame $folder"
echo
echo "Done: \"$name\" is in the Gaussian XR gallery ($folder)."
echo "Restart Gaussian XR to see it."
exit
}
