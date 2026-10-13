# Setting up the Arcturus pipeline on Linux

[Deutsch](arcturus-setup.de.md)

Splat Workshop needs two things from Arcturus Vision: the **reconstruction scripts** and **Spirula Studio** (their fork, built with Vulkan). This is how it was set up on the machine Splat Workshop was developed on (Nobara 44 / Fedora, NVIDIA RTX 4070 SUPER). The [pipeline's own README](https://arcturus.vision/apps) is the reference – if it says something different for a newer version, follow it.

Plan for about 30 minutes, most of it compiling. Everything can live in one folder, e.g. `~/arcturus`:

```
~/arcturus/
├── av1-3dgs-pipeline/          reconstruction scripts (download)
└── spirula-studio/             Spirula Studio fork (git clone + build)
    └── build_vulkan/spirula    the binary Splat Workshop calls
```

## 1. Packages

A C++17 compiler, CMake, Ninja, git, the Vulkan headers and loader, and a GPU driver with Vulkan support. Plus the tools Splat Workshop itself needs.

**Fedora / Nobara**

```bash
sudo dnf install gcc-c++ cmake ninja-build git vulkan-headers vulkan-loader-devel vulkan-tools \
                 ffmpeg uv npm
```

**Ubuntu / Debian**

```bash
sudo apt install build-essential cmake ninja-build git libvulkan-dev vulkan-tools ffmpeg nodejs npm
curl -LsSf https://astral.sh/uv/install.sh | sh        # uv is not packaged on older releases
```

Check that Vulkan sees your graphics card (not only a software renderer like *llvmpipe*):

```bash
vulkaninfo --summary
```

If only llvmpipe shows up, install the Vulkan driver for your GPU first (NVIDIA: the proprietary driver; AMD/Intel: Mesa's Vulkan drivers). The CUDA toolkit is **not** needed.

## 2. Reconstruction scripts

Download the **AV1 → 3DGS pipeline** zip from [arcturus.vision/apps](https://arcturus.vision/apps) and unpack it:

```bash
mkdir -p ~/arcturus && cd ~/arcturus
unzip ~/Downloads/av1-3dgs-pipeline-*.zip -d av1-3dgs-pipeline
```

The folder must directly contain `av1_to_3dgs.py` (if the zip has an extra folder level, move its contents up). You don't need to install Python packages – `uv` fetches them on the first run.

## 3. Spirula Studio

The pipeline is tested against one specific Spirula revision, and its README names it (`git -C spirula-studio checkout <commit>`). Use exactly that one – Spirula is under active development, and a newer version can change the command line options the pipeline calls. When you download a newer pipeline, it names the matching Spirula revision, so this stays up to date by itself.

These commands read the revision from the pipeline README, so you don't have to copy it by hand:

```bash
cd ~/arcturus
REV=$(grep -oE 'spirula-studio checkout [0-9a-f]{40}' av1-3dgs-pipeline/README.md | awk '{print $3}')
echo "Spirula revision: $REV"          # must not be empty – otherwise look it up in the README
git clone https://github.com/arcturus-vision/spirula-studio.git
cd spirula-studio
git fetch origin "$REV"                # also works if the commit is no longer on a branch tip
git checkout "$REV"
```

Build (Vulkan backend, with Spirula's own structure-from-motion):

```bash
bash build_develop.bash -DSS_BACKEND=vulkan -DSS_BUILD_GUI=OFF -DSS_BUILD_SFM=ON -DSS_BUILD_SAM=OFF
```

The build downloads its own shader compiler and takes a while. Test it:

```bash
./build_vulkan/spirula sfm auto --help
```

**Optional:** with `-DSS_BUILD_GUI=ON` instead you also get Spirula's graphical viewer, which the *Spirula* button in Splat Workshop opens to look at a finished scene on the PC. The GUI build needs additional desktop development packages (X11/Wayland); CMake names the ones that are missing.

## 4. splat-transform

Converts the result into the compact SOG format that Gaussian XR loads:

```bash
npm install -g @playcanvas/splat-transform
```

If you can't or don't want to install globally: `npm install --prefix ~/arcturus/node @playcanvas/splat-transform` and set `splat_transform = "~/arcturus/node/bin/splat-transform"` in the config.

## 5. Tell Splat Workshop where everything is

In `~/.config/splat-workshop/config.toml`:

```toml
[paths]
pipeline = "~/arcturus/av1-3dgs-pipeline"
spirula  = "~/arcturus/spirula-studio/build_vulkan/spirula"
```

Restart Splat Workshop (tray → Quit, then start it again). If something is still missing, the page says so at the top.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `av.open() got an unexpected keyword argument 'metadata_errors'` | PyAV 19 removed that option. Pin the older version: in the `# /// script` header at the top of `av1_to_3dgs.py` and `av1_colmap.py`, change `"av>=18.1"` to `"av>=18.1,<19"`. (Was needed with the pipeline from autumn 2026; newer versions may have fixed it.) |
| `Could not select unique evenly spaced capture instants` | Recording too short. The pipeline needs at least 100 capture instants – record at least 10 seconds, better 30+. |
| Spirula finds no GPU / uses llvmpipe | Vulkan driver missing, see step 1. |
| The scene does not appear in Gaussian XR | It must be a real folder in Gaussian XR's `Scenes/` (no symlink) with the gallery images – Splat Workshop's *To the Frame* button does this for you. A Steam update or “verify files” of Gaussian XR can reset that folder; just copy the scene again. |
| Only very few images registered / blurry result | Usually the recording: too dark, too fast, or too little structure. Run *Check recording* in Splat Workshop first. |
