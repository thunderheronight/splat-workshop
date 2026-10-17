#!/bin/bash
# A splat file from another program as a scene (0.6) – then copied to the Frame, versions, mesh … like any
# other scene.
#
# Usage: scripts/import.sh <splat file> <run folder> "<display name>" [up] [scale]
#   <splat file>  .ply .compressed.ply .sog .spz .splat .ksplat (whatever splat-transform reads)
#   up            where "up" is in the file: y-down (Spirula Studio, COLMAP, the Frame – default), y-up, z-up
#   scale         factor for the size, e.g. 0.01 if the file is in centimetres (default 1)
# Writes <run folder>/ with spirula/scene.ply, scene.sog, scene.xrSceneTransform.json, a preview from the
# top view and a name plate. Lines starting with "@@" are progress markers.
set -euo pipefail
# Everything in one block: bash reads it completely before it starts, so updating this file
# (git pull, editing) does not affect a run that is already going.
{

HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
ROOT=$(dirname "$HERE")
eval "$(cd "$ROOT" && python3 -m splat_workshop.config --shell)"

src=$(realpath "${1:?Give a splat file}")
out=${2:?Give the run folder}
name=${3:?Give a name}
up=${4:-y-down}
scale=${5:-1}
[ -f "$src" ] || { echo "No such file: $src" >&2; exit 1; }
[ -n "$SPLAT_TRANSFORM" ] || { echo "splat-transform not found" >&2; exit 1; }
[ -e "$out" ] && { echo "Already there: $out" >&2; exit 1; }
case "$up" in
    y-down) turn=() ;;
    y-up) turn=(-r 180,0,0) ;;                  # +Y becomes -Y (checked: splat-transform -r 180,0,0)
    z-up) turn=(-r -90,0,0) ;;                  # +Z becomes -Y (splat-transform -r 90,0,0 turns +Z into +Y)
    *) echo "up must be y-down, y-up or z-up" >&2; exit 1 ;;
esac
[[ "$scale" =~ ^[0-9]+(\.[0-9]+)?$ ]] && awk -v s="$scale" 'BEGIN { exit !(s > 0) }' \
    || { echo "scale must be a number above 0" >&2; exit 1; }
size=()
[ "$scale" != 1 ] && size=(-s "$scale")
slug=$(basename "$out")
stem=$(printf '%s' "$slug" | tr 'A-Z' 'a-z')

tmp=$out.tmp
rm -rf "$tmp"
mkdir -p "$tmp/spirula"
trap 'rm -rf "$tmp"' EXIT
echo "@@ vstep 1 2 convert"
echo "Importing $(basename "$src") as \"$name\" (up: $up, scale: $scale) ..."
"$SPLAT_TRANSFORM" -w "$src" "${turn[@]}" "${size[@]}" -N "$tmp/spirula/scene.ply"
"$SPLAT_TRANSFORM" -w "$tmp/spirula/scene.ply" "$tmp/scene.sog"
# (the same line the pipeline writes: the scene list reads the number of splats from it)
python3 -c 'import sys
with open(sys.argv[1], "rb") as f:
    n = next(int(l.split()[-1]) for l in f if l.startswith(b"element vertex"))
print(f"spirula: {n:,} Gaussians in {sys.argv[1]}")' "$tmp/spirula/scene.ply"
# Gaussian XR starts at the scene's origin, as it is in the file
cat > "$tmp/scene.xrSceneTransform.json" <<'JSON'
{"transform": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], "worldScale": 1.0, "worldScaleOffset": [0, 0, 0]}
JSON

echo "@@ vstep 2 2 preview"
# Preview for the gallery: the top view as for the floor plan; if that finds no room, plain colour
if uv run -q "$HERE/floorplan.py" "$tmp/plan.png" "$tmp=$name" >/dev/null 2>&1; then
    uv run -q "$HERE/make_gallery.py" --plan "$tmp/plan.png" "$tmp" "$name" "$stem"
else
    uv run -q --no-project --with pillow python3 -c 'import sys
from PIL import Image
Image.new("RGBA", (512, 512), (40, 46, 60, 255)).save(sys.argv[1])' "$tmp/$stem.gallery.png"
    uv run -q "$HERE/make_gallery.py" --label "$tmp/$stem.sceneinfo.png" "$name"
fi
mv "$tmp" "$out"
trap - EXIT
echo "Gaussian XR initial view: (imported scene) Display rotation 0°"
echo "@@ done local"
echo "Done: $out"
exit
}
