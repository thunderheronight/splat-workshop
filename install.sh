#!/bin/bash
# Install Splat Workshop for the current user:
#   - config file ~/.config/splat-workshop/config.toml (only if there is none yet)
#   - menu entry and icon (and a desktop shortcut with --desktop)
#   - optional autostart of the tray icon with --autostart
# Nothing is copied: the program runs from this folder. Uninstall: ./install.sh --uninstall
set -euo pipefail
ROOT=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
APPS=${XDG_DATA_HOME:-$HOME/.local/share}/applications
ICONS=${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps
CONF=${XDG_CONFIG_HOME:-$HOME/.config}/splat-workshop
AUTOSTART=${XDG_CONFIG_HOME:-$HOME/.config}/autostart
DESKTOP_DIR=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")

if [ "${1:-}" = --uninstall ]; then
    rm -f "$APPS/splat-workshop.desktop" "$ICONS/splat-workshop.svg" "$AUTOSTART/splat-workshop.desktop" \
          "$DESKTOP_DIR/splat-workshop.desktop"
    echo "Removed menu entry, icon and autostart. Config ($CONF) and your scenes were kept."
    exit 0
fi

echo "Checking requirements ..."
missing=0
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null || { echo "  ✗ Python 3.11 or newer"; missing=1; }
for tool in ffmpeg ffprobe uv ssh curl; do
    command -v "$tool" >/dev/null && echo "  ✓ $tool" || { echo "  ✗ $tool"; missing=1; }
done
command -v splat-transform >/dev/null && echo "  ✓ splat-transform" ||
    echo "  ! splat-transform not on PATH (npm install -g --prefix ~/.local @playcanvas/splat-transform needs no root; or set its path in the config)"
python3 -c 'import PySide6' 2>/dev/null && echo "  ✓ PySide6 (tray icon)" ||
    echo "  · PySide6 not installed – uv will fetch it on first start for the tray icon"
[ $missing = 0 ] || echo "Install the missing tools first (see README)."

mkdir -p "$CONF" "$APPS" "$ICONS"
created=0
if [ ! -f "$CONF/config.toml" ]; then
    cp "$ROOT/config.example.toml" "$CONF/config.toml"
    created=1
    echo "Created $CONF/config.toml from config.example.toml – set the paths to the Arcturus pipeline and Spirula, and your Frame's address."
else
    echo "Keeping existing $CONF/config.toml (all settings with explanations: $ROOT/config.example.toml)"
fi

# Started from the desktop, Splat Workshop does not see a PATH set in .bashrc/.zshrc: give it the full path
st=$(command -v splat-transform || true)
conf_st=$(python3 -c 'import sys, tomllib
print(tomllib.load(open(sys.argv[1], "rb")).get("paths", {}).get("splat_transform", ""))' "$CONF/config.toml" 2>/dev/null || true)
if [ -n "$st" ] && [ -z "$conf_st" ]; then
    if [ $created = 1 ]; then
        sed -i "s|^splat_transform = \"\"|splat_transform = \"$st\"|" "$CONF/config.toml"
        echo "  ✓ splat_transform = \"$st\" written to the config"
    else
        echo "  ! Add this line under [paths] in $CONF/config.toml – started from the desktop,"
        echo "    Splat Workshop does not see the PATH of your shell:"
        echo "      splat_transform = \"$st\""
    fi
fi
# Settings Splat Workshop does not know (typos such as "splat-transform" instead of "splat_transform")
(cd "$ROOT" && python3 -c 'from splat_workshop import config
for key, hint in config.load().unknown:
    print(f"  ! Unknown setting \"{key}\" in the config" + (f" – did you mean \"{hint}\"?" if hint else " – it is ignored"))') || true

cp "$ROOT/web/icon.svg" "$ICONS/splat-workshop.svg"
entry() {
    cat <<EOF
[Desktop Entry]
Type=Application
Name=Splat Workshop
Name[de]=Splat-Werkstatt
Comment=GUI for the Arcturus Vision splat pipeline (Steam Frame)
Comment[de]=Oberfläche für die Splat-Pipeline von Arcturus Vision (Steam Frame)
Exec="$ROOT/splat-workshop" $1
Icon=splat-workshop
Terminal=false
Categories=Graphics;3DGraphics;
StartupNotify=false
EOF
}
entry "" > "$APPS/splat-workshop.desktop"
echo "Added menu entry \"Splat Workshop\""

for arg in "$@"; do
    case "$arg" in
        --desktop)
            entry "" > "$DESKTOP_DIR/splat-workshop.desktop"
            chmod +x "$DESKTOP_DIR/splat-workshop.desktop"
            gio set "$DESKTOP_DIR/splat-workshop.desktop" metadata::trusted true 2>/dev/null || true
            echo "Added desktop shortcut" ;;
        --autostart)
            mkdir -p "$AUTOSTART"
            entry "--background" > "$AUTOSTART/splat-workshop.desktop"
            echo "Tray icon starts automatically after login" ;;
    esac
done
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
echo "Done. Start it from the menu or with: $ROOT/splat-workshop"
