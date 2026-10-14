#!/bin/bash
# Put the scenes of one recording session together into one big scene ("tour"):
# all splats for the PC, a smaller version for the Steam Frame.
#
# Usage: scripts/combine.sh <folder name> "<display name>" <run folder>=<label> <run folder>=<label> ...
#
# Environment:
#   ALIGN=frame|images|shift|rigid  how the rooms are fitted together (default: images, see combine.py)
#   PARTITION=view|nearest          where the borders between the recordings run (default: view)
#   FRAME_SPLATS=3500000            splats in the Frame version (the full version keeps all)
#   FULL=0                          skip the full SOG for the PC (the step that needs the most memory)
#   FULL_NAME="..."                 name plate of the full version (default: "<name> (full)")
#   PLAN_COMMA=1                    decimal comma on the floor plan used as preview
#   NO_UPLOAD=1                     only compute, don't copy to the Frame
# A previous result of the same name stays until the new one is done.
#
# Lines starting with "@@" are machine-readable progress markers for the web interface.
set -euo pipefail
# Everything in one block: bash reads it completely before it starts, so updating this file
# (git pull, editing) does not affect a run that is already going.
{

HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
ROOT=$(dirname "$HERE")
eval "$(cd "$ROOT" && python3 -m splat_workshop.config --shell)"

slug=${1:?Give a folder name}
name=${2:?Give a display name}
shift 2
[ $# -ge 2 ] || { echo "Give at least two scenes (run folder=label)" >&2; exit 1; }
case "$slug" in ''|.*|*/*) echo "Unusable folder name: $slug" >&2; exit 1 ;; esac
[ -n "$SPLAT_TRANSFORM" ] || { echo "splat-transform not found (npm install -g @playcanvas/splat-transform)" >&2; exit 1; }

out=$RUNS_DIR/$slug
log=$RUNS_DIR/$slug.log
work=$RUNS_DIR/.building
tmp=$work/$slug
stem=$(printf '%s' "$slug" | tr 'A-Z' 'a-z')
frame_splats=${FRAME_SPLATS:-3500000}

main() {
    rm -rf "$tmp"
    mkdir -p "$tmp"
    trap 'rm -rf "$tmp" "$tmp.old"; rmdir "$work" 2>/dev/null || true' EXIT
    trap 'exit 143' TERM; trap 'exit 130' INT; trap 'exit 129' HUP

    echo "Putting $# scenes together as \"$name\" (alignment ${ALIGN:-images}, borders ${PARTITION:-view})"
    uv run -q "$HERE/combine.py" "$tmp" "$@" --align "${ALIGN:-images}" --partition "${PARTITION:-view}"
    total=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["splats"])' "$tmp/combine.json")

    # Frame version: fewer splats
    echo "@@ cphase 2"
    src=$tmp/spirula/scene.ply
    if [ "$total" -gt "$frame_splats" ]; then
        echo "Reducing $total splats to $frame_splats for the Frame ..."
        "$SPLAT_TRANSFORM" -w "$src" --decimate "$frame_splats" "$tmp/frame.ply"
        src=$tmp/frame.ply
    fi

    echo "@@ cphase 3"
    "$SPLAT_TRANSFORM" -w "$src" "$tmp/scene.sog"
    rm -f "$tmp/frame.ply"
    if [ "${FULL:-1}" = 1 ]; then
        echo "Full version ($total splats) ..."
        mkdir -p "$tmp/full"
        "$SPLAT_TRANSFORM" -w "$tmp/spirula/scene.ply" "$tmp/full/scene.sog"
        cp "$tmp/scene.xrSceneTransform.json" "$tmp/full/scene.xrSceneTransform.json"
    fi

    # Preview for the gallery: the floor plan of all rooms
    uv run -q "$HERE/floorplan.py" "$tmp/plan.png" "$@" ${PLAN_COMMA:+--comma} >/dev/null
    uv run -q "$HERE/make_gallery.py" --plan "$tmp/plan.png" "$tmp" "$name" "$stem"
    if [ -d "$tmp/full" ]; then
        uv run -q "$HERE/make_gallery.py" --plan "$tmp/plan.png" "$tmp/full" "${FULL_NAME:-$name (full)}" "$stem-full"
        cp "$tmp/full/scene.sog" "$tmp/full/$stem-full.sog"
        cp "$tmp/full/scene.xrSceneTransform.json" "$tmp/full/$stem-full.xrSceneTransform.json"
    fi

    # Replace the previous result only now
    if [ -e "$out" ]; then mv "$out" "$tmp.old"; fi
    mv "$tmp" "$out"
    rm -rf "$tmp.old"

    if [ "${NO_UPLOAD:-0}" = 1 ] || [ -z "$FRAME_HOST" ]; then
        GALLERY_ONLY=1 LOG="$log" "$HERE/to-frame.sh" "$out" "$name"
        echo "@@ done local"
        echo "Done: $out"
    else
        LOG="$log" "$HERE/to-frame.sh" "$out" "$name"
    fi
}

mkdir -p "$RUNS_DIR"
main "$@" 2>&1 | tee -a "$log"
exit
}
