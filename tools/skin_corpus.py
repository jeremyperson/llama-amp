#!/usr/bin/env python3
"""Crash-test the classic skin engine against real skins from the Winamp Skin
Museum (https://skins.webamp.org). Not part of the unit tests: it downloads.

    xvfb-run -a python3 tools/skin_corpus.py [--count 300] [--seed 7]

Skins are cached in ~/.cache/llamaamp-skin-corpus. Every skin is loaded, and
every classic window is shaped and painted in its normal and shade states,
stopped and playing, in both visualizer modes. Failures and slow skins are
written to report.json in the cache directory; the exit status is the number
of failing skins (capped at 100).
"""
import argparse
import json
import os
import random
import sys
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

API = 'https://api.webamp.org/graphql'
AGENT = 'LlamaAmp-skin-corpus (https://github.com/jeremyperson/llama-amp)'
CACHE = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'llamaamp-skin-corpus'


def request(url, data=None):
    headers = {'User-Agent': AGENT}
    if data is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(data).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data, headers), timeout=30) as response:
        return response.read()


def sample(count, seed):
    """Skins spread across the whole museum, reproducibly."""
    total = json.loads(request(API, {'query': '{ skins(first: 1) { count } }'}))['data']['skins']['count']
    rng = random.Random(seed)
    picked = {}
    while len(picked) < count:
        offset = rng.randrange(0, max(1, total - 20))
        query = '{ skins(first: 20, offset: %d) { nodes { md5 filename download_url } } }' % offset
        for node in json.loads(request(API, {'query': query}))['data']['skins']['nodes']:
            picked.setdefault(node['md5'], node)
            if len(picked) >= count:
                break
    return list(picked.values())


def download(skins):
    CACHE.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, skin in enumerate(skins):
        path = CACHE / f"{skin['md5']}.wsz"
        if not path.exists():
            try:
                path.write_bytes(request(skin['download_url']))
                time.sleep(.2)          # be gentle with the museum
            except Exception as error:
                print(f'  download failed {skin["filename"]}: {error}')
                continue
        paths.append((path, skin['filename']))
        if index % 50 == 49:
            print(f'  {index + 1}/{len(skins)} downloaded')
    return paths


def exercise(paths):
    import cairo
    os.environ['LLAMAAMP_DATA_DIR'] = tempfile.mkdtemp(prefix='llama-corpus-')
    Path(os.environ['LLAMAAMP_DATA_DIR'], 'config.json').write_text(json.dumps(
        {'alsa_output': False, 'tray_icon': False, 'notifications': False, 'update_check': False}))
    from gi.repository import GLib
    from llamaamp.app import MusicPlayer
    from llamaamp.skin.loader import Skin

    with patch.object(MusicPlayer, '_mpris_setup', lambda self: None):
        app = MusicPlayer()
    app.show_all()
    app.set_skin('builtin')
    classic = app._classic
    default = app._builtin_skin()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 700, 700)
    failures, timings, fallbacks = [], [], []
    for path, name in paths:
        started = time.monotonic()
        try:
            app.skin = Skin.load(str(path), default)
            if app.skin.undecodable or 'MAIN' in app.skin.missing:
                fallbacks.append({'skin': name, 'file': str(path), 'undecodable': app.skin.undecodable,
                                  'missing': app.skin.missing})
            for window in classic.windows():
                window.apply_shape()
            for state in ('Stopped', 'Playing'):
                app.playback_state = state
                for mode in ('spectrum', 'scope'):
                    app.config['vis_mode'] = mode
                    for shaded in (False, True):
                        for window in classic.windows():
                            window.shaded = shaded
                            window.paint(cairo.Context(surface), app.skin)
            for window in classic.windows():
                window.shaded = False
        except Exception:
            failures.append({'skin': name, 'file': str(path), 'error': traceback.format_exc(limit=4)})
        timings.append((time.monotonic() - started, name))
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
    app.destroy()
    return failures, timings, fallbacks


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--count', type=int, default=300)
    parser.add_argument('--seed', type=int, default=7)
    args = parser.parse_args()
    print(f'Sampling {args.count} skins (seed {args.seed})…')
    paths = download(sample(args.count, args.seed))
    print(f'Exercising {len(paths)} skins…')
    failures, timings, fallbacks = exercise(paths)
    slow = sorted(timings, reverse=True)[:10]
    report = {'skins': len(paths), 'failures': failures, 'fallbacks': fallbacks,
              'slowest': [{'seconds': round(seconds, 3), 'skin': name} for seconds, name in slow]}
    print(f'{len(fallbacks)} skins had sheets that failed to decode or lacked main.bmp')
    for fallback in fallbacks[:15]:
        print(f"  {fallback['skin']}: undecodable {fallback['undecodable']} missing {fallback['missing']}")
    (CACHE / 'report.json').write_text(json.dumps(report, indent=2))
    print(f'{len(failures)} failing of {len(paths)}; slowest {slow[0][0]:.2f}s ({slow[0][1]})' if slow else 'no skins')
    for failure in failures[:15]:
        print('---', failure['skin'])
        print(failure['error'].rstrip().splitlines()[-1])
    print(f'Report: {CACHE / "report.json"}')
    sys.exit(min(100, len(failures)))


if __name__ == '__main__':
    main()
