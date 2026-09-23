"""Deterministic classic-mode scenes for golden-image tests.

Only sprite-drawn content is covered (built-in skin, bitmap font), so renders
are identical across machines; playlist rows are left empty because their
text uses system fonts. Regenerate with tools/update_golden.py after an
intended visual change, and inspect the new images before committing.
"""
from pathlib import Path
from unittest.mock import patch

import cairo

from llamaamp.ui.classic import playlist_window

GOLDEN_DIR = Path(__file__).resolve().parent / 'golden'
SECOND = 1_000_000_000


def _baseline(app):
    """The same starting state for every scene, so no scene depends on another."""
    app.playback_state, app.is_playing = 'Stopped', False
    app.current_song, app.current_index, app._title_full = None, 0, ''
    app.duration, app.audio_properties = 0, {}
    app.volume, app.balance = .7, 0.0
    app.eq_values, app.preamp_value = [.5] * 10, .5
    app.analyzer_state.reset()


def _playing(app):
    app.playback_state, app.is_playing = 'Playing', True
    app.current_song, app.current_index = '/music/demo.flac', 0
    app._title_full = 'Llamas - Whip It Good'
    app.duration = 225 * SECOND
    app.audio_properties = dict(sample_rate=44100, bitrate=1411, channels=2)
    app.volume, app.balance = .7, -.25
    levels = [abs(((band * 7) % 20) - 10) / 10 for band in range(20)]
    app.analyzer_state.levels = levels
    app.analyzer_state.peaks = [min(1.0, level + .15) for level in levels]


def _eq_curve(app):
    app.eq_values = [.9, .8, .65, .5, .4, .45, .55, .7, .8, .85]
    app.preamp_value = .6


SCENES = {
    'main-stopped': ('main', False, lambda app: None),
    'main-playing': ('main', False, _playing),
    'main-shaded': ('main', True, _playing),
    'eq': ('eq', False, _eq_curve),
    'eq-shaded': ('eq', True, _playing),
    'playlist-empty': ('playlist', False, lambda app: None),
}


def render(app, scene):
    """A cairo surface of the scene. app must be in classic mode with the built-in skin."""
    name, shaded, setup = SCENES[scene]
    _baseline(app)
    setup(app)
    window = getattr(app._classic, name)
    window.shaded, window.focused = shaded, True
    width, height = window.skin_size()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    # System-font text (the empty-playlist hint) differs between machines
    with patch.object(type(app), '_current_position_ns', lambda self: 83 * SECOND), \
            patch.object(playlist_window, 'EMPTY_PLAYLIST_HINT', ''):
        window.paint(cairo.Context(surface), app.skin)
    window.shaded = False
    return surface


def difference(surface, path):
    """Fraction of pixels that differ from the stored PNG (1.0 if missing)."""
    if not path.exists():
        return 1.0
    stored = cairo.ImageSurface.create_from_png(str(path))
    if (stored.get_width(), stored.get_height()) != (surface.get_width(), surface.get_height()):
        return 1.0
    surface.flush()
    ours, theirs = bytes(surface.get_data()), bytes(stored.get_data())
    if ours == theirs:
        return 0.0
    pixels = len(ours) // 4
    differing = sum(ours[i:i + 4] != theirs[i:i + 4] for i in range(0, len(ours), 4))
    return differing / pixels
