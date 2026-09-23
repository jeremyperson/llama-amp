#!/bin/bash
# Build a Debian/Ubuntu package for Llama Amp.
# Output: packaging/dist/llama-amp_<version>_all.deb
# Install:  sudo apt install ./dist/llama-amp_*.deb   (or double-click in Files)
set -euo pipefail
cd "$(dirname "$0")"

VERSION=$(python3 -c "import re; print(re.search(r'APP_VERSION = \"([^\"]+)\"', open('../llamaamp/constants.py').read()).group(1))")
PKG="build/llama-amp_${VERSION}_all"

rm -rf build
mkdir -p dist \
  "$PKG/DEBIAN" \
  "$PKG/usr/bin" \
  "$PKG/usr/share/llama-amp" \
  "$PKG/usr/share/applications" \
  "$PKG/usr/share/icons/hicolor/scalable/apps" \
  "$PKG/usr/share/fonts/truetype/llama-amp" \
  "$PKG/usr/share/doc/llama-amp"

# App
cp ../musicPlayer.py "$PKG/usr/share/llama-amp/"
(cd .. && find llamaamp -name '*.py' -exec install -Dm644 {} "packaging/$PKG/usr/share/llama-amp/{}" \;)
# Translations (po/*.po), compiled beside the app where llamaamp/i18n.py looks
if ls ../po/*.po >/dev/null 2>&1; then
  python3 ../tools/translations.py compile "$PKG/usr/share/llama-amp/locale"
fi
cp ../llama-amp.svg "$PKG/usr/share/icons/hicolor/scalable/apps/llama-amp.svg"

# Launcher
cat > "$PKG/usr/bin/llama-amp" <<'EOF'
#!/bin/sh
exec python3 /usr/share/llama-amp/musicPlayer.py "$@"
EOF
chmod 755 "$PKG/usr/bin/llama-amp"

# Desktop entry
cat > "$PKG/usr/share/applications/llama-amp.desktop" <<'EOF'
[Desktop Entry]
Name=Llama Amp
Comment=Winamp-inspired music player
Exec=llama-amp %F
Icon=llama-amp
Terminal=false
Type=Application
Categories=AudioVideo;Audio;Player;GTK;
MimeType=audio/mpeg;audio/mp3;audio/flac;audio/x-flac;audio/wav;audio/x-wav;audio/ogg;audio/x-vorbis+ogg;audio/mp4;audio/aac;audio/x-m4a;
EOF

# Fonts (both SIL OFL 1.1 — license files included below).
# Self-contained: use the locally installed copies if present, otherwise
# download into a cached staging dir (gitignored).
CACHE="fonts-cache"
LOCAL="$HOME/.local/share/fonts"
if [ ! -f "$CACHE/DSEG7Classic-Regular.ttf" ]; then
  mkdir -p "$CACHE"
  if [ -f "$LOCAL/dseg/DSEG7Classic-Regular.ttf" ]; then
    cp "$LOCAL/dseg/DSEG7Classic-Regular.ttf" "$LOCAL/dseg/DSEG7Classic-Bold.ttf" \
       "$LOCAL/dseg/DSEG-LICENSE.txt" "$CACHE/"
    cp "$LOCAL/orbitron/Orbitron[wght].ttf" "$LOCAL/orbitron/OFL.txt" "$CACHE/"
  else
    echo "Downloading fonts (DSEG7 Classic + Orbitron, SIL OFL 1.1)..."
    curl -sL -o "$CACHE/dseg.zip" \
      https://github.com/keshikan/DSEG/releases/download/v0.46/fonts-DSEG_v046.zip
    unzip -j -o -q "$CACHE/dseg.zip" \
      "fonts-DSEG_v046/DSEG7-Classic/DSEG7Classic-Regular.ttf" \
      "fonts-DSEG_v046/DSEG7-Classic/DSEG7Classic-Bold.ttf" \
      "fonts-DSEG_v046/DSEG-LICENSE.txt" -d "$CACHE/"
    rm -f "$CACHE/dseg.zip"
    curl -sL -o "$CACHE/Orbitron[wght].ttf" \
      'https://github.com/google/fonts/raw/main/ofl/orbitron/Orbitron%5Bwght%5D.ttf'
    curl -sL -o "$CACHE/OFL.txt" \
      'https://raw.githubusercontent.com/google/fonts/main/ofl/orbitron/OFL.txt'
  fi
fi
cp "$CACHE/DSEG7Classic-Regular.ttf" "$CACHE/DSEG7Classic-Bold.ttf" \
   "$CACHE/Orbitron[wght].ttf" "$PKG/usr/share/fonts/truetype/llama-amp/"
cp "$CACHE/DSEG-LICENSE.txt" "$PKG/usr/share/doc/llama-amp/DSEG-LICENSE.txt"
cp "$CACHE/OFL.txt" "$PKG/usr/share/doc/llama-amp/Orbitron-OFL.txt"

# Docs
cat > "$PKG/usr/share/doc/llama-amp/copyright" <<'EOF'
Llama Amp — © 2026 Jeremy Person
Bundled fonts: DSEG7 Classic (© keshikan) and Orbitron (© The Orbitron
Project Authors), both under the SIL Open Font License 1.1 — see
DSEG-LICENSE.txt and Orbitron-OFL.txt in this directory.
Winamp 2 skin sprite coordinates (llamaamp/skin/sprites.py) are derived
from Webamp, © 2015 Jordan Eldredge, MIT License (full text in that file).
EOF

# Control
cat > "$PKG/DEBIAN/control" <<EOF
Package: llama-amp
Version: ${VERSION}
Section: sound
Priority: optional
Architecture: all
Depends: python3, python3-gi, python3-gi-cairo, gir1.2-gtk-3.0, gir1.2-gstreamer-1.0, gir1.2-gst-plugins-base-1.0, gir1.2-gdkpixbuf-2.0, gstreamer1.0-plugins-good, gstreamer1.0-alsa, gstreamer1.0-pulseaudio
Recommends: gstreamer1.0-plugins-bad, gstreamer1.0-libav
Maintainer: Jeremy Person <1409499+jeremyperson@users.noreply.github.com>
Description: Winamp-inspired music player
 Compact GTK3 music player with three retro themes, a 10-band equalizer,
 falling-peak spectrum display, detachable panels, windowshade mode,
 playlist undo, MPRIS2 media keys, and direct ALSA output.
EOF

# Refresh caches after install/remove
cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v fc-cache >/dev/null && fc-cache -f /usr/share/fonts/truetype/llama-amp || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q /usr/share/icons/hicolor || true
command -v update-desktop-database >/dev/null && update-desktop-database -q || true
exit 0
EOF
cat > "$PKG/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
command -v fc-cache >/dev/null && fc-cache -f || true
command -v update-desktop-database >/dev/null && update-desktop-database -q || true
exit 0
EOF
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"

dpkg-deb --build --root-owner-group "$PKG" dist/
echo
echo "Built:"
ls -lh dist/*.deb
