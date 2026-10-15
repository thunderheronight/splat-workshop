#!/bin/bash
# Versions of a finished scene or tour: fewer splats for the Frame, all (or a limit) for the PC,
# optionally shown in the original's size (models). Each version is a complete scene in
# <run folder>/versions/<key>/ and can be copied to the Frame next to the others.
#
# Usage: scripts/versions.sh <run folder> "<key>|<splats>|<scale>|<name plate>[|<clean-up>]" ...
#   <key>         folder name of the version, e.g. 1m, 0.75m, all, 1m-1x87
#   <splats>      number of splats, or "all"
#   <scale>       1, or e.g. 87: Gaussian XR shows it in the original's size (worldScale 87)
#   <name plate>  text on the name plate in the gallery, e.g. "Flat · 1 M"
#   <clean-up>    optional, e.g. "ceiling=0.5,haze=0.3": cleaned up first (scripts/clean.py; ceiling,
#                 floor, haze, outside, each 0 to 1), then reduced
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
    IFS='|' read -r key splats scale plate clean <<< "$spec"
    case "$key" in ''|.*|*/*) echo "Unusable version name: $key" >&2; exit 1 ;; esac
    echo "@@ vstep $i $# $key"
    dir=$out/versions/$key
    tmp=$dir.tmp
    rm -rf "$tmp"
    mkdir -p "$tmp"
    trap 'rm -rf "$tmp"' EXIT
    from=$src
    have=$total
    if [ -n "${clean:-}" ]; then
        args=()
        IFS=',' read -ra parts <<< "$clean"
        for kv in "${parts[@]}"; do
            k=${kv%%=*} v=${kv#*=}
            case "$k" in ceiling|floor|haze|outside) ;; *) echo "Unknown clean-up: $k" >&2; exit 1 ;; esac
            [[ "$v" =~ ^(0(\.[0-9]+)?|1(\.0+)?)$ ]] || { echo "Clean-up strength must be 0 to 1: $kv" >&2; exit 1; }
            args+=("--$k" "$v")
        done
        echo "$key: cleaning up ($clean)"
        uv run -q "$HERE/clean.py" "$src" "$tmp/clean.ply" "${args[@]}"
        from=$tmp/clean.ply
        have=$(python3 -c 'import sys
with open(sys.argv[1], "rb") as f:
    print(next(int(l.split()[-1]) for l in f if l.startswith(b"element vertex")))' "$from")
    fi
    if [ "$splats" = all ] || [ "$splats" -ge "$have" ]; then
        echo "$key: all $have splats"
        "$SPLAT_TRANSFORM" -w "$from" "$tmp/scene.sog"
        made=$have
    else
        echo "$key: reducing $have to $splats splats"
        "$SPLAT_TRANSFORM" -w "$from" --decimate "$splats" "$tmp/reduced.ply"
        "$SPLAT_TRANSFORM" -w "$tmp/reduced.ply" "$tmp/scene.sog"
        rm -f "$tmp/reduced.ply"
        made=$splats
    fi
    if [ -f "$tmp/clean.ply" ] && [ "$splats" = all ]; then
        mkdir -p "$tmp/spirula"                 # for the PC: Spirula Studio opens the ply
        mv "$tmp/clean.ply" "$tmp/spirula/scene.ply"
    fi
    rm -f "$tmp/clean.ply"
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
    python3 - "$tmp/version.json" "$key" "$made" "$splats" "$scale" "$plate" "${clean:-}" <<'PY'
import json, sys, time
path, key, made, target, scale, plate, clean = sys.argv[1:]
info = {"key": key, "splats": int(made), "target": target, "scale": float(scale), "label": plate,
        "created": time.time()}
if clean:
    info["clean"] = {k: float(v) for k, v in (kv.split("=") for kv in clean.split(","))}
json.dump(info, open(path, "w"), indent=1)
PY
    rm -rf "$dir"
    mv "$tmp" "$dir"
    trap - EXIT
done
echo "@@ done local"
echo "Done: $out/versions"
exit
}
