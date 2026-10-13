# Die Arcturus-Pipeline unter Linux einrichten

[English](arcturus-setup.md)

Die Splat-Werkstatt braucht zwei Dinge von Arcturus Vision: die **Rekonstruktions-Skripte** und **Spirula Studio** (deren Fork, mit Vulkan gebaut). So wurde es auf dem Rechner eingerichtet, auf dem die Splat-Werkstatt entstanden ist (Nobara 44 / Fedora, NVIDIA RTX 4070 SUPER). Maßgeblich ist die [README der Pipeline](https://arcturus.vision/apps) – wenn dort für eine neuere Version etwas anderes steht, gilt das.

Rechne mit etwa 30 Minuten, das meiste davon Kompilieren. Alles kann in einem Ordner liegen, z. B. `~/arcturus`:

```
~/arcturus/
├── av1-3dgs-pipeline/          Rekonstruktions-Skripte (Download)
└── spirula-studio/             Spirula-Studio-Fork (git clone + bauen)
    └── build_vulkan/spirula    das Programm, das die Splat-Werkstatt aufruft
```

## 1. Pakete

Ein C++17-Compiler, CMake, Ninja, git, Vulkan-Header und -Loader und ein Grafiktreiber mit Vulkan. Dazu die Werkzeuge, die die Splat-Werkstatt selbst braucht.

**Fedora / Nobara**

```bash
sudo dnf install gcc-c++ cmake ninja-build git vulkan-headers vulkan-loader-devel vulkan-tools \
                 ffmpeg uv npm
```

**Ubuntu / Debian**

```bash
sudo apt install build-essential cmake ninja-build git libvulkan-dev vulkan-tools ffmpeg nodejs npm
curl -LsSf https://astral.sh/uv/install.sh | sh        # uv gibt es bei älteren Versionen nicht als Paket
```

Prüfen, ob Vulkan deine Grafikkarte sieht (und nicht nur einen Software-Renderer wie *llvmpipe*):

```bash
vulkaninfo --summary
```

Taucht nur llvmpipe auf, zuerst den Vulkan-Treiber für deine GPU installieren (NVIDIA: proprietärer Treiber; AMD/Intel: Mesa-Vulkan-Treiber). Das CUDA-Toolkit wird **nicht** gebraucht.

## 2. Rekonstruktions-Skripte

Das ZIP der **AV1 → 3DGS Pipeline** von [arcturus.vision/apps](https://arcturus.vision/apps) laden und entpacken:

```bash
mkdir -p ~/arcturus && cd ~/arcturus
unzip ~/Downloads/av1-3dgs-pipeline-*.zip -d av1-3dgs-pipeline
```

Im Ordner muss direkt `av1_to_3dgs.py` liegen (hat das ZIP eine zusätzliche Ordnerebene, den Inhalt eine Ebene hochschieben). Python-Pakete musst du nicht installieren – `uv` holt sie beim ersten Lauf.

## 3. Spirula Studio

Die Pipeline ist gegen genau einen Spirula-Stand getestet, und ihre README nennt ihn (`git -C spirula-studio checkout <commit>`). Genau den nehmen – Spirula wird aktiv weiterentwickelt, und eine neuere Version kann die Kommandozeilen-Optionen ändern, die die Pipeline aufruft. Lädst du eine neuere Pipeline, nennt sie den passenden Spirula-Stand, das bleibt also von selbst aktuell.

Diese Befehle lesen den Stand aus der Pipeline-README, du musst ihn nicht abtippen:

```bash
cd ~/arcturus
REV=$(grep -oE 'spirula-studio checkout [0-9a-f]{40}' av1-3dgs-pipeline/README.md | awk '{print $3}')
echo "Spirula-Stand: $REV"             # darf nicht leer sein – sonst in der README nachschauen
git clone https://github.com/arcturus-vision/spirula-studio.git
cd spirula-studio
git fetch origin "$REV"                # klappt auch, wenn der Commit nicht mehr an einer Branch-Spitze liegt
git checkout "$REV"
```

Bauen (Vulkan, mit Spirulas eigenem Structure-from-Motion):

```bash
bash build_develop.bash -DSS_BACKEND=vulkan -DSS_BUILD_GUI=OFF -DSS_BUILD_SFM=ON -DSS_BUILD_SAM=OFF
```

Der Build lädt seinen eigenen Shader-Compiler und dauert eine Weile. Testen:

```bash
./build_vulkan/spirula sfm auto --help
```

**Optional:** Mit `-DSS_BUILD_GUI=ON` bekommst du zusätzlich Spirulas grafischen Viewer, den der Knopf *Spirula* in der Splat-Werkstatt öffnet, um eine fertige Szene am PC anzuschauen. Dafür braucht der Build weitere Desktop-Entwicklerpakete (X11/Wayland); CMake sagt, welche fehlen.

## 4. splat-transform

Wandelt das Ergebnis in das kompakte SOG-Format, das Gaussian XR lädt:

```bash
npm install -g @playcanvas/splat-transform
```

Ohne globale Installation: `npm install --prefix ~/arcturus/node @playcanvas/splat-transform` und in der Config `splat_transform = "~/arcturus/node/bin/splat-transform"` eintragen.

## 5. Der Splat-Werkstatt sagen, wo alles liegt

In `~/.config/splat-workshop/config.toml`:

```toml
[paths]
pipeline = "~/arcturus/av1-3dgs-pipeline"
spirula  = "~/arcturus/spirula-studio/build_vulkan/spirula"
```

Splat-Werkstatt neu starten (Tray → Beenden, dann wieder starten). Fehlt noch etwas, steht es oben auf der Seite.

## Wenn etwas nicht klappt

| Meldung / Problem | Ursache / Lösung |
|---|---|
| `av.open() got an unexpected keyword argument 'metadata_errors'` | PyAV 19 hat diese Option entfernt. Ältere Version festlegen: im `# /// script`-Kopf oben in `av1_to_3dgs.py` und `av1_colmap.py` `"av>=18.1"` in `"av>=18.1,<19"` ändern. (Mit der Pipeline vom Herbst 2026 nötig; neuere Versionen haben es evtl. behoben.) |
| `Could not select unique evenly spaced capture instants` | Aufnahme zu kurz. Die Pipeline braucht mindestens 100 Aufnahmezeitpunkte – mindestens 10 Sekunden aufnehmen, besser 30+. |
| Spirula findet keine GPU / nutzt llvmpipe | Vulkan-Treiber fehlt, siehe Schritt 1. |
| Die Szene taucht in Gaussian XR nicht auf | Sie muss als echter Ordner (kein Symlink) mit den Galeriebildern in `Scenes/` von Gaussian XR liegen – das macht der Knopf *Auf die Frame*. Ein Steam-Update oder „Dateien überprüfen“ von Gaussian XR kann den Ordner zurücksetzen; dann die Szene einfach neu übertragen. |
| Nur wenige Bilder registriert / unscharfes Ergebnis | Meist liegt es an der Aufnahme: zu dunkel, zu schnell oder zu wenig Struktur. Vorher *Aufnahme prüfen* laufen lassen. |
