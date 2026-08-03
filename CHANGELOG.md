# Changelog

## 1.4 — 2026-08-03

- Flathub-ready Flatpak packaging: the manifest
  (`packaging/flatpak/com.jeremyperson.LlamaAmp.yml`) now builds straight
  from the release tag with pinned font sources — no manual staging — and
  installs AppStream metadata, an app-id desktop entry, and the icon under
  the app id. Also adds the previously missing network permission
  (internet radio / scrobbling).
- Inside Flatpak the update checker disables itself entirely (no version
  checks, no update menu items): the store owns updates there.

## 1.3 — 2026-08-03

- In-app update notifications: the app checks GitHub Releases 15 s after
  startup and once a day while running, notifying (desktop popup + LCD
  flash) when a newer version exists. Silent on failure and when current;
  each version is announced only once. Opt out via
  ⚙ → "Check for Updates on Startup" (no network calls when disabled).
- Manual ⚙ → "Check for Updates…" with an explicit up-to-date / failed
  dialog, offering to install when an update exists.
- GUI update flow: once an update is known the menu item becomes
  "⬆ Update to vX…" — installed (.deb) copies download the new package to
  Downloads and hand it to the system installer (normal admin-password
  prompt, no in-app privilege escalation); portable checkouts open the
  releases page instead.

## 1.2 — 2026-08-03

- New original app icon: an LED spectrum ladder whose last bar rises into a
  llama, drawn from the app's own theme palette. Replaces the previous
  Winamp-derived bolt artwork.
- The icon asset is renamed `musicPlayer.svg` → `llama-amp.svg` and the tray
  indicator now registers under the `llama-amp` icon name, matching the name
  the .deb installs into the hicolor theme (and avoiding stale icon-theme
  caches from the old name).

## 1.1 — 2026-08-03

- Fixed: song title and album art could disagree during gapless playback.
  Tags from the prerolling next track were applied while the previous track
  was still on screen — flipping the title and bitrate readout early and
  caching the next track's cover art under the wrong file, which then showed
  the wrong cover whenever that track played again in the session. Preroll
  tags are now cached for the upcoming track and applied only at the
  stream-start handoff.

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
