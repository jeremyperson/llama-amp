# Installing Llama Amp

Llama Amp is a single-file Python/GTK3/GStreamer app. Two install paths are
provided, covering effectively every Linux desktop. (Windows/macOS: see the
honest note at the bottom.)

## Debian / Ubuntu / Mint — .deb package

Build once (DSEG7/Orbitron fonts are used from `~/.local/share/fonts` if
installed, otherwise downloaded automatically into `fonts-cache/`):

    ./build-deb.sh

Install (either):
- Double-click `dist/llama-amp_*.deb` in your file manager → opens App Center → Install
- Or: `sudo apt install ./dist/llama-amp_2.0_all.deb`

You get: `llama-amp` on the PATH, a launcher in your app grid ("Llama Amp"),
"Open With → Llama Amp" for audio files, bundled LED/display fonts, and all
GStreamer dependencies pulled in automatically. Settings live in
`~/.config/llamaamp/` (the installed app detects that /usr is read-only and
switches to XDG config paths automatically).

Uninstall: `sudo apt remove llama-amp`

## Any other Linux distro — Flatpak

`flatpak/com.jeremyperson.LlamaAmp.yml` is a self-contained flatpak-builder
manifest (GNOME 48 runtime; includes MPRIS, PulseAudio, network for radio
streams, and raw ALSA device access for the bit-perfect output mode). It
builds straight from the pinned release tag — no staging step:

    # one-time setup
    flatpak install flathub org.gnome.Platform//48 org.gnome.Sdk//48

    flatpak-builder --user --install --force-clean build-flatpak \
      flatpak/com.jeremyperson.LlamaAmp.yml

The same manifest is submitted to Flathub; once accepted the app is a
software-center install (`flatpak install flathub com.jeremyperson.LlamaAmp`)
on Fedora, Arch, openSUSE, Steam Deck, etc. Flatpak builds receive updates
through the store, so the in-app update checker stays out of the way there.

## Windows / macOS — the honest note

The app is built on GTK3 + GStreamer + PyGObject. Those stacks *do* exist on
Windows (via MSYS2) and macOS (via Homebrew), but shipping a double-clickable
installer means bundling the entire GTK/GStreamer runtime (~150MB+), theming
breaks, and the ALSA/MPRIS features are Linux-only. It's a real porting
project, not a packaging step. If cross-platform ever matters, the pragmatic
path is a rewrite of the UI layer, not a package of this one.

## Notes

- A portable checkout (running `musicPlayer.py` from a writable directory)
  stores config/playlist next to the script. Installed copies use
  `~/.config/llamaamp/`. The two don't share state.
- Bundled fonts (DSEG7 Classic, Orbitron) are SIL OFL 1.1; licenses ship in
  `/usr/share/doc/llama-amp/`.
