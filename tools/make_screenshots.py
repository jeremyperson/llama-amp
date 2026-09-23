#!/usr/bin/env python3
"""Regenerate screenshots/*.png (README) and docs/assets (site) from the
current build: the three themes, windowshade, and classic mode.

    xvfb-run -a -s "-screen 0 1600x1400x24" python3 tools/make_screenshots.py

Uses demonstration track labels and generated audio into a fakesink.
"""
import json
import math
import os
import shutil
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
work = Path(tempfile.mkdtemp(prefix='llama-shots-'))
os.environ['LLAMAAMP_DATA_DIR'] = str(work)

from llamaamp.app import MusicPlayer  # noqa: E402  (pins the GTK 3 versions first)
from llamaamp.library.scanner import Scanner  # noqa: E402
from gi.repository import Gdk, GdkPixbuf, GLib, Gst  # noqa: E402

TRACKS = [('Llamas - Whip It Good', 196), ('The Alpacas - Night Drive', 262),
          ('Vicuña Club - Andes Sunrise', 330), ('Guanaco Groove - Slow Burn', 220)]


def tone(path, pitch, seconds=12):
    rate = 44100
    data = bytearray()
    for i in range(rate * seconds):
        pulse = .55 + .45 * math.sin(2 * math.pi * 1.5 * i / rate)
        data += struct.pack('<hh', int(14000 * pulse * math.sin(2 * math.pi * pitch * i / rate)),
                            int(9000 * math.sin(2 * math.pi * pitch * 2.01 * i / rate)))
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((2, 2, rate, 0, 'NONE', 'not compressed'))
        audio.writeframes(bytes(data))


def pump(seconds):
    end = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(.002)


def grab(windows, path, zoom=1):
    """Stack the given windows vertically into one PNG."""
    shots = []
    for window in windows:
        gdk = window.get_window()
        shots.append(Gdk.pixbuf_get_from_window(gdk, 0, 0, gdk.get_width(), gdk.get_height()))
    width, height = max(s.get_width() for s in shots), sum(s.get_height() for s in shots)
    canvas = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, width, height)
    canvas.fill(0)
    y = 0
    for shot in shots:
        shot.copy_area(0, 0, shot.get_width(), shot.get_height(), canvas, 0, y)
        y += shot.get_height()
    if zoom != 1:
        canvas = canvas.scale_simple(width * zoom, height * zoom, GdkPixbuf.InterpType.NEAREST)
    canvas.savev(str(path), 'png', [], [])
    print(f'{path.relative_to(ROOT)} {canvas.get_width()}x{canvas.get_height()}')


def session(theme):
    for saved in work.glob('*.txt'):
        saved.unlink()            # each session starts with an empty saved playlist
    (work / 'config.json').write_text(json.dumps(
        {'alsa_output': False, 'direct_mode': False, 'tray_icon': False, 'notifications': False,
         'update_check': False, 'theme': theme, 'expanded_size': [560, 740], 'shuffle': 1}))
    with patch.object(MusicPlayer, '_mpris_setup', lambda self: None):
        app = MusicPlayer()
    sink = Gst.ElementFactory.make('fakesink')
    sink.set_property('sync', True)
    app.player.set_property('audio-sink', sink)
    app.show_all()
    display = Gdk.Display.get_default()
    display.get_default_seat().get_pointer().warp(display.get_default_screen(), 0, 0)   # no tooltips
    pump(.3)
    app._add_paths(paths)
    app._play_index(0)
    pump(1.6)
    return app


paths = []
audio_dir = Path(tempfile.mkdtemp(prefix='llama-shots-audio-'))
for name, pitch in TRACKS:
    path = audio_dir / f'{name}.wav'
    tone(path, pitch)
    paths.append(str(path))

LIBRARY = [('Rock', 'Llamas', 'Whip It Good', ['Opening', 'Whip It Good', 'Andes Highway', 'Spit Take', 'Closing Time']),
           ('Rock', 'Llamas', 'Second Wind', ['Second Wind', 'Pack Animal', 'Cria', 'Summit']),
           ('Jazz', 'Vicuña Club', 'Andes Sunrise', ['Andes Sunrise', 'Blue Altiplano', 'Fleece', 'Cusco Nights']),
           ('Jazz', 'The Alpacas', 'Night Drive', ['Night Drive', 'Headlights', 'Motel Pool', 'Dawn']),
           ('Electronic', 'Guanaco Groove', 'Slow Burn', ['Slow Burn', 'Pulse', 'Afterglow', 'Low Tide'])]


def library_shot(path):
    """The Media Library over a small tagged demo collection (needs ffmpeg)."""
    music = Path(tempfile.mkdtemp(prefix='llama-shots-music-'))
    for genre, artist, album, titles in LIBRARY:
        (music / artist / album).mkdir(parents=True)
        for number, title in enumerate(titles, 1):
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', paths[0], '-t', '1', '-metadata', f'title={title}',
                            '-metadata', f'artist={artist}', '-metadata', f'album={album}', '-metadata',
                            f'track={number}', '-metadata', f'genre={genre}',
                            str(music / artist / album / f'{number:02d} {title}.flac')], check=True)
    app = session('green')
    app.library.add_folder(str(music))
    Scanner(app.library, app.library.folders()).run()
    app.show_library()
    window = app._library_window
    window.resize(980, 600)
    pump(.4)
    view = window.facet_views['genre']
    view.get_selection().select_iter(next(row.iter for row in view.get_model() if row[1] == 'Jazz'))
    pump(.4)
    grab([window], path)
    app.destroy()
    pump(.2)


shots = ROOT / 'screenshots'
for theme, filename in (('green', 'llama-amp.png'), ('silver', 'llama-amp-silver.png'), ('amber', 'llama-amp-amber.png')):
    app = session(theme)
    grab([app], shots / filename)
    if theme == 'green':
        app.toggle_windowshade()
        pump(.4)
        grab([app], shots / 'llama-amp-windowshade.png')
        app.toggle_windowshade()
        app.set_skin('builtin')
        pump(1.2)
        classic = app._classic
        grab([classic.main, classic.eq, classic.playlist], shots / 'llama-amp-classic.png', zoom=2)
        app.set_skin(None)
    app.destroy()
    pump(.2)

if shutil.which('ffmpeg'):
    library_shot(shots / 'llama-amp-library.png')

assets = ROOT / 'docs' / 'assets'
assets.mkdir(parents=True, exist_ok=True)
for source, target in (('llama-amp.png', 'modern.png'), ('llama-amp-silver.png', 'silver.png'),
                       ('llama-amp-amber.png', 'amber.png'), ('llama-amp-classic.png', 'classic.png'),
                       ('llama-amp-library.png', 'library.png')):
    shutil.copyfile(shots / source, assets / target)
shutil.copyfile(ROOT / 'llama-amp.svg', assets / 'llama-amp.svg')
print('docs/assets updated')
