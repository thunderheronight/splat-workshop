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
    echo "  ! splat-transform not on PATH (npm install -g @playcanvas/splat-transform, or set it in the config)"
python3 -c 'import PySide6' 2>/dev/null && echo "  ✓ PySide6 (tray icon)" ||
    echo "  · PySide6 not installed – uv will fetch it on first start for the tray icon"
[ $missing = 0 ] || echo "Install the missing tools first (see README)."

mkdir -p "$CONF" "$APPS" "$ICONS"
if [ ! -f "$CONF/config.toml" ]; then
    cp "$ROOT/config.example.toml" "$CONF/config.toml"
    echo "Created $CONF/config.toml – set the paths to the Arcturus pipeline and Spirula, and your Frame's address."
else
    echo "Keeping existing $CONF/config.toml"
fi

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
