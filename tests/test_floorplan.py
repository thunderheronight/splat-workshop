"""The floor plan is a top view, not its mirror image (scripts/floorplan.py).

A plan may be turned (so the walls are straight), but never mirrored. Three coloured markers tell
the two apart: red at the corner, green along +x, blue along +z. Turning keeps the sense of
red -> green -> blue, mirroring reverses it. Seen from above with the y axis pointing down
(like the pipeline's frame), +x to +z turns counter-clockwise on screen.
"""
import subprocess
import sys

import numpy as np
from PIL import Image

from conftest import BASE, ROOT
import synth


def draw(style):
    runs = BASE / f"plan-{style}"
    synth.make_room(runs, "Room", "A", error=(0.0, (0, 0, 0)), markers=True)
    out = runs / "plan.png"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "floorplan.py"), str(out), f"{runs / 'Room'}=Room",
                        "--style", style], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return np.asarray(Image.open(out).convert("RGB")).astype(int)


def test_floor_plan_is_not_mirrored():
    img = draw("pretty")                   # the pretty style shows the splats' own colours
    rr, gg, bb = img[..., 0], img[..., 1], img[..., 2]

    def centre(mask):
        ys, xs = np.nonzero(mask)
        assert len(xs) > 50, "marker not found"
        return np.array([xs.mean(), ys.mean()])
    red = centre((rr > 150) & (gg < 90) & (bb < 90))
    green = centre((gg > 150) & (rr < 90) & (bb < 90))
    blue = centre((bb > 150) & (rr < 90) & (gg < 90))
    gx, gz = green - red, blue - red                 # screen directions of +x and +z (rows count downwards)
    cross = gx[0] * gz[1] - gx[1] * gz[0]
    assert cross < 0, "the floor plan is mirrored"


def test_classic_style_smoke():
    """Smoke test: the classic style draws something (its colours are per room, not checkable here)."""
    img = draw("classic")
    assert img.shape[0] > 500 and img.std() > 5    # not an empty picture
