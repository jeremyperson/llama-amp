# Classic interface validation

Validated locally on 2026-09-12. The application remains an unreleased v1.4
checkout; no release, apt repository update, or system installation was performed.

## Automated checks

`NO_AT_BRIDGE=1 xvfb-run -a /usr/bin/python3 -m unittest discover -s tests -v`

All 34 tests pass. They cover queue precedence, repeat, actual playback history,
duplicate identities, reorder/Undo, clear/Undo, missing and corrupt tracks,
gapless handoff invalidation, real GStreamer gapless playback, local WAV/MP3/FLAC,
HTTP playback and buffering, Direct Mode, ReplayGain, unavailable ALSA output,
MPRIS Next dispatch and playing/paused/stopped state, stopped session restoration,
tagged titles with cached durations, late metadata across duplicate/reordered/
removed rows and active search, anchored metadata, 44.1 kHz/multichannel formatting,
configuration validation/restoration, a 10,000-entry playlist, themes,
windowshade, X11 snapping/group movement, and the Wayland attachment fallback.

Tests create temporary data directories and use GStreamer `fakesink`, so no
test audio reaches speakers. MPRIS registration is suppressed during the suite;
its dispatch handlers are tested directly. HTTP fixtures are served on localhost.

The layout/theme/windowshade checks were exercised with `GDK_DPI_SCALE=1.5`
and 200% display scaling (`GDK_SCALE=2`). The interface uses pixel-sized fonts;
its own text remains fixed under the DPI text override. Screenshots were inspected
for all themes, Direct Mode bypass, a long title at the 440-pixel minimum width,
and windowshade. Syntax compilation, shell syntax, and `git diff --check` pass.
CI runs the behavioral and GTK tests.

## Design polish checks

- At 560 × 740 logical pixels, the playlist fits seven complete 24-pixel rows.
  EQ scales are 68 pixels tall; the Volume and Balance scales measure 316 and
  208 pixels wide, respectively, with a 12-pixel gap between their groups.
- The wordmark and now-playing title both begin 20 pixels from the left edge.
  The badge reads `v1.4` from `APP_VERSION`; both labels forward title-bar drag
  gestures. Branding hides as a group in the 48-pixel windowshade.
- Panel controls have at least 28 × 28 targets. Clicks exercise collapse/expand
  and detach/reattach, including changing tooltips and working detached resize
  grips. Native window-manager drag behavior remains a desktop check.
- Direct Mode displays “Bypassed · Direct Mode,” disables the EQ power button,
  and dims only slider hardware. Stored gains survive bypass and become active
  when Direct Mode is disabled. Off keeps those gains while applying zero gain.
- New title tests include tagged FLAC, numbered untagged filenames, duplicate
  entries, cached durations, late results, and matching the displayed title.
  The current track stays ready after Stop with no pause marker or pending seek.

## Performance

During the initial interface work, a local Xvfb test played the same generated
44.1 kHz stereo WAV through a
synchronized silent output for both the previous checkout and this version.
CPU figures are percentages of **one core**, sampled over four visible seconds
and three hidden seconds; they are short local measurements, not a benchmark
guarantee.

| Version | Visible | Hidden |
| --- | ---: | ---: |
| Previous 10-band / ~15 fps display | 3.4% | 1.1% |
| Initial 20-column / ~30 fps implementation | 16.1% | 1.5% |
| Final cached rendering and frequency mapping | 9.0% | 1.2% |

The smoother, higher-resolution analyzer costs more CPU while visible. Its
background and illuminated LEDs are cached; frames only clip level heights and
draw peak positions. Hidden/disabled visualization removes the animation timer
and disables spectrum messages. Playback and position tracking continue.

## Packaging and practical limits

- The Debian preview was built in an isolated staging directory, inspected with
  `dpkg-deb`, and its extracted application compared byte-for-byte with the
  checkout. The artifact is `packaging/dist/preview/llama-amp_1.4_all.deb`.
- Flatpak build/run was not tested: this machine lacks `flatpak-builder` and the
  manifest's GNOME 48 SDK. The release-pinned manifest and install paths were
  reviewed; this change adds no runtime dependency or packaged theme asset.
- A native Wayland compositor was not available for testing. Explicit attachment
  and independent-window fallback were exercised under Xvfb with X11-specific
  positioning disabled. X11 snapping was exercised directly under Xvfb.
- DAC contention fallback was exercised through an unavailable sink. Physical
  DAC switching, audible transition quality, desktop media-key integration,
  compositor-specific drag behavior, and live public radio stations still need
  a normal desktop listening session. Playback tests verify pipeline behavior
  using local fixtures and silent output.

## Try the checkout

```bash
/usr/bin/python3 /home/jeremy/Apps/audioPlayer/musicPlayer.py
```

Use Settings → Theme for Classic Silver or Amber. Double-click the title bar
for windowshade; use the panel window/dock icons to detach/attach EQ and playlist.
Existing saved music and output settings are used normally. Set
`LLAMAAMP_DATA_DIR` to a separate writable directory for an isolated session.
