#!/bin/bash
# Versions of a finished scene or tour: fewer splats for the Frame, all (or a limit) for the PC,
# optionally shown in the original's size (models). Each version is a complete scene in
# <run folder>/versions/<key>/ and can be copied to the Frame next to the others.
#
# Usage: scripts/versions.sh <run folder> "<key>|<splats>|<scale>|<name plate>" ...
#   <key>         folder name of the version, e.g. 1m, 0.75m, all, 1m-1x87
#   <splats>      number of splats, or "all"
#   <scale>       1, or e.g. 87: Gaussian XR shows it in the original's size (worldScale 87)
#   <name plate>  text on the name plate in the gallery, e.g. "Flat · 1 M"
# The splats come from <run folder>/spirula/scene.ply. Lines starting with "@@" are progress markers.
set -euo pipefail
# Everything in one block: bash reads it completely before it starts, so updating this file
# (git pull, editing) does not affect a run that is already going.
{

HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
ROOT=$(dirname "$HERE")
eval "$(cd "$ROOT" && python3 -m splat_workshop.config --shell)"

out=$(realpath "${1:?Give a run folder}")
shift
[ $# -ge 1 ] || { echo "Give at least one version" >&2; exit 1; }
src=$out/spirula/scene.ply
[ -f "$src" ] || { echo "No spirula/scene.ply in $out" >&2; exit 1; }
[ -n "$SPLAT_TRANSFORM" ] || { echo "splat-transform not found" >&2; exit 1; }
slug=$(basename "$out")
preview=$(ls "$out"/*.gallery.png 2>/dev/null | head -1 || true)
total=$(python3 - "$src" <<'PY'
import sys
with open(sys.argv[1], "rb") as f:
    for line in f:
        if line.startswith(b"element vertex"):
            print(int(line.split()[-1])); break
PY
)
echo "Making $# version(s) of $slug ($total splats)"

i=0
for spec in "$@"; do
    i=$((i + 1))
    IFS='|' read -r key splats scale plate <<< "$spec"
    case "$key" in ''|.*|*/*) echo "Unusable version name: $key" >&2; exit 1 ;; esac
    echo "@@ vstep $i $# $key"
    dir=$out/versions/$key
    tmp=$dir.tmp
    rm -rf "$tmp"
    mkdir -p "$tmp"
    trap 'rm -rf "$tmp"' EXIT
    if [ "$splats" = all ] || [ "$splats" -ge "$total" ]; then
        echo "$key: all $total splats"
        "$SPLAT_TRANSFORM" -w "$src" "$tmp/scene.sog"
        made=$total
    else
        echo "$key: reducing $total to $splats splats"
        "$SPLAT_TRANSFORM" -w "$src" --decimate "$splats" "$tmp/reduced.ply"
        "$SPLAT_TRANSFORM" -w "$tmp/reduced.ply" "$tmp/scene.sog"
        rm -f "$tmp/reduced.ply"
        made=$splats
    fi
    stem=$(printf '%s-%s' "$slug" "$key" | tr 'A-Z' 'a-z')
    python3 - "$out/scene.xrSceneTransform.json" "$tmp/scene.xrSceneTransform.json" "$scale" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
data["worldScale"] = float(sys.argv[3])          # 1 = original size of the scene
json.dump(data, open(sys.argv[2], "w"), indent=2)
PY
    if [ -n "$preview" ]; then
        cp "$preview" "$tmp/$stem.gallery.png"
    fi
    uv run -q "$HERE/make_gallery.py" --label "$tmp/$stem.sceneinfo.png" "$plate"
    python3 - "$tmp/version.json" "$key" "$made" "$splats" "$scale" "$plate" <<'PY'
import json, sys, time
path, key, made, target, scale, plate = sys.argv[1:]
json.dump({"key": key, "splats": int(made), "target": target, "scale": float(scale), "label": plate,
           "created": time.time()}, open(path, "w"), indent=1)
PY
    rm -rf "$dir"
    mv "$tmp" "$dir"
    trap - EXIT
done
echo "@@ done local"
echo "Done: $out/versions"
exit
}
