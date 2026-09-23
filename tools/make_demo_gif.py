#!/usr/bin/env python3
"""Record docs/assets/demo.gif: the modern window playing with a live
visualizer, then classic mode (built-in skin, double size) with the
oscilloscope and the spectrum.

    xvfb-run -a -s "-screen 0 1600x1200x24" python3 tools/make_demo_gif.py

Audio is a generated tone played into a fakesink (silent). Needs ffmpeg.
"""
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FPS = 12
OUT = ROOT / 'docs' / 'assets' / 'demo.gif'

work = Path(tempfile.mkdtemp(prefix='llama-demo-'))
os.environ['LLAMAAMP_DATA_DIR'] = str(work)
(work / 'config.json').write_text(json.dumps(
    {'alsa_output': False, 'direct_mode': False, 'tray_icon': False, 'notifications': False,
     'update_check': False, 'expanded_size': [560, 740]}))

from llamaamp.app import MusicPlayer  # noqa: E402  (pins the GTK 3 versions first)
from gi.repository import Gdk, GdkPixbuf, GLib, Gst  # noqa: E402


def tone(path, seconds, pitches):
    """A stereo demo track whose melody moves through the given pitches."""
    rate = 44100
    frames = bytearray()
    for i in range(rate * seconds):
        pitch = pitches[(i * len(pitches)) // (rate * seconds)]
        pulse = .55 + .45 * math.sin(2 * math.pi * 2 * i / rate)
        left = 15000 * pulse * math.sin(2 * math.pi * pitch * i / rate)
        right = 9000 * math.sin(2 * math.pi * pitch * 1.5 * i / rate)
        frames += struct.pack('<hh', int(left), int(right))
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((2, 2, rate, 0, 'NONE', 'not compressed'))
        audio.writeframes(bytes(frames))


def pump(seconds):
    end = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(.002)


frames_dir = work / 'frames'
frames_dir.mkdir()
count = 0


def capture(windows, seconds, size):
    """Grab frames of the given windows (stacked vertically) onto a fixed canvas."""
    global count
    for _ in range(int(seconds * FPS)):
        pump(1 / FPS)
        canvas = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, *size)
        canvas.fill(0x16181600)
        y = 0
        for window in windows:
            gdk = window.get_window()
            shot = Gdk.pixbuf_get_from_window(gdk, 0, 0, gdk.get_width(), gdk.get_height())
            x = (size[0] - shot.get_width()) // 2
            shot.copy_area(0, 0, min(shot.get_width(), size[0]), min(shot.get_height(), size[1] - y),
                           canvas, max(0, x), y)
            y += shot.get_height()
        canvas.savev(str(frames_dir / f'{count:04d}.png'), 'png', [], [])
        count += 1


tracks = []
for name, pitches in (('Llamas - Whip It Good', [196, 247, 294, 392]),
                      ('The Alpacas - Night Drive', [330, 262, 220, 165])):
    path = work / f'{name}.wav'
    tone(path, 20, pitches)
    tracks.append(str(path))

with patch.object(MusicPlayer, '_mpris_setup', lambda self: None):
    app = MusicPlayer()
sink = Gst.ElementFactory.make('fakesink')
sink.set_property('sync', True)
app.player.set_property('audio-sink', sink)
app.show_all()
# Park the pointer in a corner, away from anything with a tooltip
display = Gdk.Display.get_default()
display.get_default_seat().get_pointer().warp(display.get_default_screen(), 0, 0)
pump(.3)
app._add_paths(tracks)
app._play_index(0)
pump(.6)
size = (560, 740)
capture([app], 3.5, size)                       # modern: spectrum
app.set_skin('builtin')
app.toggle_double_size()                        # classic at 2x fills the frame
pump(.4)
classic = app._classic
app._set_appearance('vis_mode', 'scope')
capture([classic.main, classic.eq, classic.playlist], 3.0, size)
app._set_appearance('vis_mode', 'spectrum')
capture([classic.main, classic.eq, classic.playlist], 2.0, size)
app.destroy()

OUT.parent.mkdir(parents=True, exist_ok=True)
palette = work / 'palette.png'
subprocess.run(['ffmpeg', '-v', 'error', '-y', '-framerate', str(FPS), '-i', str(frames_dir / '%04d.png'),
                '-vf', 'palettegen=max_colors=96:stats_mode=diff', str(palette)], check=True)
subprocess.run(['ffmpeg', '-v', 'error', '-y', '-framerate', str(FPS), '-i', str(frames_dir / '%04d.png'),
                '-i', str(palette), '-lavfi', 'paletteuse=dither=none:diff_mode=rectangle',
                '-loop', '0', str(OUT)], check=True)
print(f'{OUT} ({OUT.stat().st_size // 1024} KB, {count} frames)')
