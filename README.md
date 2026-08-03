# Llama Amp 🦙

[![Latest release](https://img.shields.io/github/v/release/jeremyperson/llama-amp)](https://github.com/jeremyperson/llama-amp/releases/latest)
[![CI](https://github.com/jeremyperson/llama-amp/actions/workflows/ci.yml/badge.svg)](https://github.com/jeremyperson/llama-amp/actions)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A Winamp-inspired music player for Linux with a modern flat theme. Single-file
Python/GTK3/GStreamer — no build step, no framework, ~5000 lines that whip.

![Llama Amp](screenshots/llama-amp.png)

## Features

**Playback**
- Gapless playback (GStreamer `about-to-finish` handoff)
- ReplayGain volume normalization (track/album modes, rgvolume + limiter)
- Internet radio: open SHOUTcast/Icecast URLs (`Ctrl+L`) with live ICY
  now-playing titles and buffering handling
- **Direct Mode**: bit-transparent output — the EQ/balance DSP chain is fully
  detached while the spectrum analyzer keeps running (pure passthrough analysis)
- **ALSA direct output**: exclusive `hw:` access to your DAC, bypassing the
  PulseAudio/PipeWire mixer and its resampling — with automatic busy-device
  retry and graceful fallback
- 10-band equalizer with presets (right-click the bars), live spectrum
  analyzer with LED-ladder meters and per-band gain markers
- Shuffle by tracks or whole albums, tri-state repeat (off / all / one),
  sleep timer (after track or 15/30/60 min), stop-after-current
  (right-click the stop button)

**Library & playlists**
- Album art: embedded tags → folder art (`cover.jpg` etc.) → placeholder
- Playlist with durations + total time, zebra striping, drag-reorder,
  multi-select, type-to-search, and a Play Next queue (right-click)
- Named playlists, M3U import/export, recursive folder add,
  missing-file detection and cleanup
- Drag & drop files or folders straight onto the window
- Scrolling marquee for long titles on the LCD panel

**Desktop integration**
- MPRIS2: media keys, lock-screen and panel controls with title + cover art
- Tray icon (AppIndicator with StatusIcon fallback) and track-change
  desktop notifications
- ListenBrainz scrobbling (paste your user token in ⚙ → Scrobbling)
- ALSA output device picker for the bit-perfect direct mode
- Open audio files from your file manager ("Open With → Llama Amp")
- Update notifications: checks GitHub Releases at startup and daily, with
  one-click install from the ⚙ menu (installed copies download the new .deb
  and hand it to the system installer; opt out via
  ⚙ → "Check for Updates on Startup")
- Keyboard shortcuts: `Space` play/pause · `←/→` seek ±5s · `↑/↓` volume ·
  `S` shuffle · `R` repeat · `J` jump-to-file · `Ctrl+O` add files ·
  `Ctrl+L` open URL · `Del` remove from playlist

## Running

Portable (no install):

```bash
git clone https://github.com/jeremyperson/llama-amp.git && cd llama-amp
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
  gstreamer1.0-plugins-good gstreamer1.0-alsa gstreamer1.0-pulseaudio
python3 musicPlayer.py [files...]
```

In portable mode, settings live next to the script. Optional: install the
[DSEG7 Classic](https://github.com/keshikan/DSEG) and
[Orbitron](https://fonts.google.com/specimen/Orbitron) fonts (both OFL) for
the authentic LED clock and wordmark — the app degrades gracefully without.

## Installing

**Debian/Ubuntu/Mint** — build the package (fonts are fetched automatically):

```bash
./packaging/build-deb.sh
sudo apt install ./packaging/dist/llama-amp_*.deb
```

**Other distros** — a Flatpak manifest is provided; see
[`packaging/README.md`](packaging/README.md).

Installed copies keep settings in `~/.config/llamaamp/` and notify you
in-app when a new release is available (update straight from the ⚙ menu).

## License

MIT (see [LICENSE](LICENSE)). Bundled at package-build time: DSEG7 Classic
and Orbitron fonts, both under the SIL Open Font License 1.1.
