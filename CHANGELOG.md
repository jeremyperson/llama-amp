# Changelog

## Unreleased

- Lyrics (Alt+Y): shows the lyrics of the playing track from an .lrc file
  with the same name beside it, or from its tags. Synced lyrics highlight
  the current line and scroll along; nothing is fetched online.

## 1.8 — 2026-09-23

- Fixed: files whose names aren't valid UTF-8 (common in older collections)
  couldn't be added to the playlist, and one in the playlist stopped it from
  being saved at all. They now play, save, reorder, show with a replacement
  character, and are indexed and searchable in the library.
- Media Library (Alt+L, or Settings → Media Library): add your music folders
  and browse by genre, artist and album, search as you type (accents and
  word prefixes), and use smart views for recently added, most played and
  never played tracks. Play replaces the playlist (one Ctrl+Z undo step),
  Enqueue and Play next add to it, and tracks drag onto the playlist. The
  index updates incrementally at startup and counts completed listens.

## 1.7 — 2026-09-23

- Classic windows dock reliably under real window managers (GNOME tested):
  they start stacked, only a user drag undocks a window, and dropping one
  near its slot snaps it back precisely. The equalizer and playlist editor
  no longer get their own taskbar entries and minimize with the player.
- Ready for translation: every visible string goes through gettext, with
  proper plural forms. See CONTRIBUTING.md → Translating to add a language.
- Fixed: after double size or shading, a docked classic window could overlap
  the one above it (the playlist covered half the equalizer).
- The empty playlist now says "Drop audio files or folders here or use Add ▾";
  the window-wide tooltip that popped up over the equalizer is gone.
- Drag selected rows up or down in the classic playlist editor to move them
  as a block (one Ctrl+Z undo step), as in Winamp.

## 1.6 — 2026-09-22

- Classic mode with real Winamp 2 skins (Settings → Skin): the main window,
  equalizer and playlist editor drawn from any `.wsz` skin, including shaped
  skins (`region.txt`), skin fonts, visualizer colors and playlist colors.
  Drop a `.wsz` onto the player to install it, or browse the Winamp Skin
  Museum from the menu. Windows dock magnetically, shade to 14px strips and
  follow double size. Includes an original built-in "Llama" classic skin.
- Crossfade (Settings → Crossfade, 2–10 s, off by default): the next track
  starts under the end of the current one with an equal-power fade. It
  uses the sound server's mixer, so it's unavailable with ALSA Output
  (bit-perfect) and for internet radio, where gapless playback continues.
  Skipping, seeking, pausing or stopping mid-fade ends the fade at once.
- File Info (Alt+3, or right-click a playlist row): tags, ReplayGain
  values, format, sample rate, bit depth, bitrate, length, location, size
  and cover art for the selected or current track. Read-only; files are
  probed in the background so slow drives never freeze the player.
- Playlist ▾ → Sort: by title, artist, album (disc and track order),
  filename, path or length, plus Reverse and Randomize. Each is one Ctrl+Z
  undo step and keeps the playing track, the queue and duplicate entries.
- Winamp-style readouts on the display: inset kbps and kHz digits and
  mono/stereo indicator lights. The info line keeps the format, channel
  count beyond stereo, and shuffle/repeat state.
- Double size (Ctrl+D, or Settings → View): the whole interface at 2×,
  pixel for pixel, including fonts, bevels, LED meters, icons and album
  art. The setting and the enlarged window size are remembered.
- Winamp's keyboard transport: Z previous, X play (restarts when already
  playing), C pause, V stop, B next. Ctrl+J jumps to a time (ss, m:ss or
  h:mm:ss) and Ctrl+T toggles elapsed/remaining time.
- Fixed: seeking in the last seconds of a track (arrow keys, the position
  slider or media controls) could skip to the next track instead, and a
  seek right after a track started could leave playback stuck paused.
- Oscilloscope visualization. Click the analyzer to cycle spectrum,
  oscilloscope and off, or pick the mode in Settings → Visualization. The
  trace reads the audio without modifying it, so Direct Mode stays bit-exact;
  the Green / yellow / red colors shade it by amplitude.
- Fixed: a short track followed by a duplicate of itself could jump the
  playlist ahead one entry when playback started.
- Fixed: a config.json with the wrong number of equalizer values prevented
  startup. Each setting now falls back to its default on its own.
- The code is now the `llamaamp` package (`musicPlayer.py` remains the
  launcher), with one declarative schema for every saved setting.

## 1.5 — 2026-09-22

- Inset LLAMA AMP wordmark with a recessed badge sourced from the application
  version. Larger now-playing title, separate Volume/Balance groups, shorter EQ,
  and track totals in the playlist header; seven complete rows fit at 560 × 740.
- Distinct collapse, expand, detach and reattach icons with descriptive tooltips
  and accessible names. EQ status distinguishes active presets/Custom, Off and
  Direct Mode bypass; bypassed sliders remain editable with dimmed handles.
- Playlist titles load from embedded metadata even when durations are already
  cached. Filename fallback and full-path tooltips preserve file identification;
  late results update duplicate rows without changing selection or queue order.
- Playing, paused and stopped states now have distinct playlist/MPRIS behavior.
  Stop clears the playback marker and pending seek; restored tracks start ready
  and stopped.
- Visible beveled resize grips with diagonal cursors on the player and detached
  panels; the grip hides in windowshade mode.
- Compact, resizable interface with beveled transport controls, collapsible and
  detachable EQ/playlist panels, X11 panel snapping, and 48-pixel windowshade mode.
- Dedicated 20-column spectrum with falling peak caps, time-based decay,
  cached LED drawing, and automatic suspension when hidden or disabled.
- Llama Green, Classic Silver and Amber themes, plus configurable analyzer colors,
  peak visibility and falloff speed.
- Accessible EQ/preamp sliders with zero marks, dB feedback, visible presets and
  Reset. Direct Mode clearly indicates that stored EQ settings are bypassed.
- One queue/navigation policy for Next, MPRIS and gapless playback, actual-playback
  shuffle history, and stable session identities for duplicate playlist entries.
- Now-playing title/art/metadata remain anchored while selecting playlist rows.
  Correct 44.1 kHz and multichannel metadata; unknown properties remain unknown.
- Compact playlist menus, queue positions, persistent playing marker, find counts,
  and 20-level session Undo for playlist edits, including clear and replacement.
- Focus-aware keyboard controls, layout/theme persistence, isolated data directory
  support, and centralized worker/timer cleanup.
- Behavioral and GTK/GStreamer integration tests in CI; updated screenshots.

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
