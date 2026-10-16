#!/bin/bash
# A mesh (surface) of a finished scene or tour, for 3D programs and 3D printing – made by Spirula Studio
# (`spirula mesh`) from the splats inside the room (scripts/clean.py --outside), then stood upright with
# the floor at height 0 (scripts/upright.py).
#
# Usage: scripts/mesh.sh <run folder> <formats> [texture]
#   <formats>   comma-separated: glb, ply, obj, stl
#                 glb  colours (texture or per corner); Y up as glTF wants it – Blender, Windows 3D viewer, web
#                 ply  colours per corner; Z up – MeshLab, CloudCompare, Blender
#                 obj  with a texture, or without colours; Y up as is custom for OBJ – nearly every 3D program
#                 stl  no colours; Z up – slicers for 3D printing
#   texture     1: a texture from the training images (only if they are still there, see "clean up"),
#               otherwise colours per corner from the splats
# Writes <run folder>/mesh/ (replaced as a whole when done). Lines starting with "@@" are progress markers.
set -euo pipefail
# Everything in one block: bash reads it completely before it starts, so updating this file
# (git pull, editing) does not affect a run that is already going.
{

HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
ROOT=$(dirname "$HERE")
eval "$(cd "$ROOT" && python3 -m splat_workshop.config --shell)"

out=$(realpath "${1:?Give a run folder}")
formats=${2:?Give the formats, e.g. glb,stl}
texture=${3:-0}
src=$out/spirula/scene.ply
[ -f "$src" ] || { echo "No spirula/scene.ply in $out" >&2; exit 1; }
[ -x "$SPIRULA_BIN" ] || { echo "Spirula Studio not found: $SPIRULA_BIN" >&2; exit 1; }
IFS=',' read -ra want <<< "$formats"
for f in "${want[@]}"; do
    case "$f" in glb|ply|obj|stl) ;; *) echo "Unknown mesh format: $f (glb, ply, obj, stl)" >&2; exit 1 ;; esac
done
slug=$(basename "$out")
stem=$(printf '%s' "$slug" | tr 'A-Z' 'a-z')

# Colours: a texture needs the cameras and the training images; without them colours per corner
data=(--no-data)
if [ "$texture" = 1 ]; then
    if [ -d "$out/dataset/images" ] && [ -d "$out/dataset/sparse" ]; then
        data=(--data "$out/dataset")
    else
        echo "No training images any more – colours per corner instead of a texture"
        texture=0
    fi
fi
colour_of() {                                   # the colour each format gets
    case "$1" in
        glb) [ "$texture" = 1 ] && echo texture || echo vertex ;;
        ply) echo vertex ;;
        obj) [ "$texture" = 1 ] && echo texture || echo none ;;
        stl) echo none ;;
    esac
}
colours=$(for f in "${want[@]}"; do colour_of "$f"; done | sort -u | paste -sd,)

tmp=$out/mesh.tmp
rm -rf "$tmp"
mkdir -p "$tmp"
trap 'rm -rf "$tmp"' EXIT
echo "@@ vstep 1 2 mesh"
# what lies well outside the room (big blobs explaining the view through a window or beyond the walls)
# would become lumps floating around the room: out first, as the "Edges" clean-up does
from=$src
if uv run -q "$HERE/clean.py" "$src" "$tmp/inside.ply" --outside 1 >/dev/null 2>&1; then
    from=$tmp/inside.ply
else
    echo "No room found (walls) – meshing everything"
fi
echo "Meshing $slug ($formats; colours: $colours) ..."
"$SPIRULA_BIN" mesh "$from" "${data[@]}" --format "$formats" --color "$colours" --output "$tmp/$stem"
rm -f "$tmp/inside.ply"

# Spirula names the files <stem>.<ext>, or <stem>_<colour>.<ext> when it made several colours:
# keep the one colour every format was meant to have, under <stem>.<ext>
echo "@@ vstep 2 2 upright"
keep=()
for f in "${want[@]}"; do
    c=$(colour_of "$f")
    case "$c" in vertex) tag=vertexcolor ;; none) tag=nocolor ;; texture) tag=texture ;; esac
    file=""
    for cand in "$tmp/${stem}_${tag}.$f" "$tmp/${stem}_${tag}"*".$f" "$tmp/$stem.$f"; do
        [ -f "$cand" ] && { file=$cand; break; }
    done
    [ -n "$file" ] || { echo "Spirula made no $f file" >&2; exit 1; }
    if [ "$c" != texture ] && [ "$file" != "$tmp/$stem.$f" ]; then
        mv "$file" "$tmp/$stem.$f"              # (a texture's file names are referenced inside: they stay)
        file=$tmp/$stem.$f
    fi
    keep+=("$file")
done
for f in "$tmp"/*; do                           # the other colour pairs Spirula made along the way
    case " ${keep[*]} " in *" $f "*) continue ;; esac
    case "$f" in *.mtl|*.png|*.jpg|*.jpeg) continue ;; esac
    rm -f "$f"
done
# upright, the floor (found as for the floor plan) at height 0: the room stands on the ground plane
uv run -q --no-project --with numpy --with pillow python3 "$HERE/upright.py" --floor-from "$src" "${keep[@]}"

python3 - "$tmp/mesh.json" "$formats" "$texture" <<'PY'
import json, sys, time
path, formats, texture = sys.argv[1:]
json.dump({"formats": formats.split(","), "texture": texture == "1", "created": time.time()}, open(path, "w"), indent=1)
PY
rm -rf "$out/mesh"
mv "$tmp" "$out/mesh"
trap - EXIT
echo "@@ done local"
echo "Done: $out/mesh"
exit
}
