# Changelog

## 1.0 — 2026-08-01

First public release.

- ReplayGain normalization (track/album; inactive in Direct Mode by design)
- Internet radio (SHOUTcast/Icecast URLs, ICY titles, buffering)
- ListenBrainz scrobbling; desktop notifications on track change
- ALSA output device picker; album shuffle; stop-after-current;
  jump-to-file dialog (J)

- Winamp-inspired GTK3 player with a modern flat theme: hairline chrome,
  rounded corners, DSEG7 LED clock, Orbitron wordmark, LED-ladder spectrum
  meters with per-band gain markers
- Gapless playback
- Direct Mode: bit-transparent DSP bypass with the spectrum analyzer kept
  alive as a passthrough tap
- ALSA direct output (exclusive DAC access, bypasses the desktop mixer) with
  automatic busy-device retry and fallback
- 10-band equalizer with presets; balance control
- Album art: embedded tags, folder art, placeholder fallback
- Playlist: durations + total time, type-to-search, Play Next queue, named
  playlists, M3U import/export, recursive folder add, missing-file cleanup,
  drag-reorder, multi-select, zebra striping
- Scrolling marquee titles; sleep timer; tray icon
- MPRIS2 media keys and desktop integration; keyboard shortcuts
- Atomic config/playlist persistence, portable and installed (XDG) modes
- Debian packaging + Flatpak manifest
