# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///
"""Create the preview image (512x512) and name plate (358x86) for the Gaussian XR gallery.

Usage: uv run make_gallery.py <run folder> <pipeline log> <display name> <file stem>
       uv run make_gallery.py --plan <floor plan.png> <run folder> <display name> <file stem>
       uv run make_gallery.py --label <name plate.png> <text>
Reads the initial view from the log ("Gaussian XR initial view: cam0/... Display rotation X°")
and writes <file stem>.gallery.png and <file stem>.sceneinfo.png into the run folder.
The look matches the example scenes that ship with Gaussian XR.
"""
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageStat

FONTS = [
    "/usr/share/fonts/google-noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
    "/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",          # Debian/Ubuntu
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",                   # Arch
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]


def initial_view(run: Path, log: Path) -> tuple[Path, float]:
    match = None
    for match in re.finditer(
        r"Gaussian XR initial view: (\S+\.jpg) .*?Display rotation (-?[\d.]+)°",
        log.read_text(errors="replace"),
    ):
        pass  # the last message counts
    if match:
        image = next(run.glob(f"dataset/images/{match.group(1)}"), None)
        if image:
            return image, float(match.group(2))
    # No message: an image from the middle of the recording, not rotated
    images = sorted(run.glob("dataset/images/cam0/*.jpg"))
    if not images:
        sys.exit(f"No images found in {run}/dataset/images/cam0")
    return images[len(images) // 2], 0.0


def gallery(image: Path, rotation_ccw: float, out: Path) -> None:
    # PIL rotates counter-clockwise for positive angles; the pipeline already reports the CCW rotation
    img = Image.open(image).convert("RGB").rotate(rotation_ccw, expand=True)
    w, h = img.size
    side = int(min(w, h) * 0.8)  # cut away the fisheye border
    img = img.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
    img = img.resize((512, 512), Image.LANCZOS)
    # Brighten dark indoor shots without blowing out bright ones
    brightness = ImageStat.Stat(img.convert("L")).mean[0]
    if brightness < 110:
        img = ImageEnhance.Brightness(img).enhance(min(2.2, 110 / max(brightness, 1)))
    img = ImageEnhance.Contrast(img).enhance(1.1)
    img.convert("RGBA").save(out)


def label(text: str, out: Path) -> None:
    width, height = 358, 86
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (10, 10, width - 10, height - 10), radius=16, fill=(40, 46, 60, 200))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(4)))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((14, 12, width - 14, height - 14), radius=14, fill=(64, 72, 90, 255))
    font_path = next((p for p in FONTS if Path(p).exists()), None)
    size = 34
    while True:  # shrink long names until they fit on the plate
        font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= width - 50 or size <= 14 or not font_path:
            break
        size -= 2
    draw.text(
        ((width - (box[2] - box[0])) / 2 - box[0], (height - (box[3] - box[1])) / 2 - box[1] - 1),
        text, font=font, fill=(255, 255, 255, 255))
    img.save(out)


def from_plan(plan: Path, out: Path) -> None:
    """Preview of a combined scene: its floor plan, centred on a square."""
    img = Image.open(plan).convert("RGB")
    side = max(img.size)
    square = Image.new("RGB", (side, side), img.getpixel((5, 5)))
    square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    square.resize((512, 512), Image.LANCZOS).convert("RGBA").save(out)


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == "--label":
        label(sys.argv[3], Path(sys.argv[2]))
        return
    if len(sys.argv) == 6 and sys.argv[1] == "--plan":
        plan, run, name, stem = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], sys.argv[5]
        from_plan(plan, run / f"{stem}.gallery.png")
        label(name, run / f"{stem}.sceneinfo.png")
        print(f"Preview image from the floor plan, name plate \"{name}\"")
        return
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    run, log, name, stem = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
    preview = run / f"{stem}.gallery.png"
    if preview.exists() and not any(run.glob("dataset/images/cam0/*.jpg")):
        # Training images were cleaned up: keep the existing preview, only renew the name plate
        label(name, run / f"{stem}.sceneinfo.png")
        print(f"Name plate \"{name}\" (preview image kept)")
        return
    image, rotation = initial_view(run, log)
    gallery(image, rotation, preview)
    label(name, run / f"{stem}.sceneinfo.png")
    print(f"Preview image from {image.name} (rotation {rotation:g}°), name plate \"{name}\"")


if __name__ == "__main__":
    main()
