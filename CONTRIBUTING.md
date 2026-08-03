# Contributing to Llama Amp

Thanks for wanting to help! Llama Amp is deliberately a **single-file**
Python/GTK3/GStreamer app — that constraint is a feature. Please keep it in
mind when proposing changes.

## Getting a dev setup

```bash
git clone https://github.com/jeremyperson/llama-amp.git && cd llama-amp
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
  gstreamer1.0-plugins-good gstreamer1.0-alsa gstreamer1.0-pulseaudio
python3 musicPlayer.py
```

Run from a writable checkout and settings live next to the script
(`config.json`, `playlist.txt`) — delete them to reset. No build step:
edit, relaunch, done.

## Ground rules

- **One file.** App code goes in `musicPlayer.py`. If your change seems to
  need a second module, open an issue first so we can talk about it.
- **No new runtime dependencies** without prior discussion — the app runs on
  a stock GNOME/GTK3/GStreamer stack on purpose (it's what makes the .deb
  and Flatpak trivially installable).
- **UI threads**: GTK calls only on the main loop. Background work uses a
  worker thread + `GLib.idle_add` (see the metadata probe queue and the
  update checker for the pattern).
- **Style**: match the surrounding code. Section banners
  (`# ==================== X ====================`) group related methods;
  keep methods small and comment the *why*, not the *what*.

## Before you open a PR

1. `python3 -m py_compile musicPlayer.py` (this is what CI runs)
2. `bash -n packaging/build-deb.sh` if you touched packaging
3. Actually play music: local files *and* — if your change goes anywhere
   near the pipeline — an internet radio URL (`Ctrl+L`), gapless track
   transitions, and Direct Mode on/off.
4. If you changed behavior, add a line to `CHANGELOG.md` under an
   "Unreleased" heading (the maintainer folds it into the next version).

Don't bump `APP_VERSION` in PRs — versioning and releases are handled by
the maintainer at release time.

## Reporting bugs

Use the bug-report issue template. The three details that matter most:
how you installed (portable / .deb / Flatpak), your distro, and exact
steps to reproduce. `python3 musicPlayer.py` from a terminal prints debug
logging that is usually the fastest route to a diagnosis.

## Releases (maintainer notes)

`APP_VERSION` in `musicPlayer.py` is the single source of truth — the .deb
build stamps itself from it. Tag `vX.Y`, build with
`packaging/build-deb.sh`, attach the .deb to the GitHub release; the
Flathub package updates from the pinned tag in its manifest.
