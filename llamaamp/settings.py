"""Settings schema (defaults and validation for config.json) and atomic file writes."""
import copy
import math
import os
from collections import namedtuple

from .constants import (
    DEFAULT_VOLUME,
    EQ_BANDS,
    REPEAT_OFF,
    RG_MODES,
    SHUFFLE_OFF,
    SHUFFLE_TRACKS,
    WINDOW_H,
    WINDOW_W,
)
from .ui.themes import THEMES


class SettingsStore:
    """Atomic persistence shared by settings, playlists and duration caches."""
    @staticmethod
    def write(path, data, binary=False):
        tmp = path + '.tmp'
        try:
            with open(tmp, 'wb' if binary else 'w', **({} if binary else {'encoding': 'utf-8'})) as stream:
                stream.write(data)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


# A coercer returns the cleaned value or raises; any exception means "use the default".
Setting = namedtuple('Setting', 'default coerce')


def _clamped(cast, lo=None, hi=None):
    def coerce(value):
        value = cast(value)
        if lo is not None and value < lo:
            value = lo
        if hi is not None and value > hi:
            value = hi
        return value
    return coerce


def _choice(choices):
    def coerce(value):
        if isinstance(value, (str, type(None))) and value in choices:
            return value
        raise ValueError(value)
    return coerce


def _is_true(value):
    return value is True


def _not_false(value):
    return value is not False


def _str_or_none(value):
    if isinstance(value, (str, type(None))):
        return value
    raise ValueError(value)


def _name_or_none(value):
    return value if isinstance(value, str) and value else None


def _finite_pair(value):
    if not (isinstance(value, (list, tuple)) and len(value) == 2 and
            all(isinstance(n, (int, float)) and math.isfinite(n) for n in value)):
        raise ValueError(value)
    return value


def _window_pos(value):
    return None if value is None else [int(n) for n in _finite_pair(value)]


def _window_size(value):
    width, height = _finite_pair(value)
    return [max(440, min(4000, int(width))), max(220, min(4000, int(height)))]


def _eq_values(value):
    if not (isinstance(value, list) and len(value) == EQ_BANDS):
        raise ValueError(value)
    return [max(0.0, min(1.0, float(x))) for x in value]


def _shuffle(value):
    if isinstance(value, bool):     # migrate pre-1.x boolean (bool IS int — check first)
        value = SHUFFLE_TRACKS if value else SHUFFLE_OFF
    return _clamped(int, 0, 2)(value)


def _dict(value):
    if isinstance(value, dict):
        return value
    raise ValueError(value)


# Every persisted key, in config.json order. A key that lives in self.config
# needs only an entry here; keys mirrored into attributes are also mapped in
# ConfigMixin.load_config and ConfigMixin._live_settings.
SCHEMA = {
    'volume': Setting(DEFAULT_VOLUME, _clamped(float, 0.0, 1.0)),
    'balance': Setting(0.0, _clamped(float, -1.0, 1.0)),
    'eq_values': Setting([0.5] * EQ_BANDS, _eq_values),
    'shuffle': Setting(SHUFFLE_OFF, _shuffle),
    'repeat': Setting(REPEAT_OFF, lambda value: int(value) % 3),
    'last_index': Setting(0, _clamped(int, 0)),
    'last_position_ns': Setting(0, _clamped(int, 0)),
    'window_pos': Setting(None, _window_pos),
    # Native-first defaults: bit-transparent DSP bypass + direct DAC
    # output. Both degrade gracefully (DSP reattaches on demand; ALSA
    # falls back to the mixer if the device can't be acquired).
    'direct_mode': Setting(True, _is_true),
    'alsa_output': Setting(True, _is_true),
    'alsa_device': Setting(None, _str_or_none),
    'playlist_name': Setting(None, _name_or_none),
    'tray_icon': Setting(True, _not_false),
    'gapless': Setting(True, _not_false),
    'preamp': Setting(0.5, _clamped(float, 0.0, 1.0)),
    'eq_enabled': Setting(True, _not_false),
    'time_remaining': Setting(False, _is_true),
    'replaygain': Setting('off', _choice(RG_MODES)),
    'listenbrainz_token': Setting(None, _str_or_none),
    'scrobble_enabled': Setting(False, _is_true),
    'notifications': Setting(True, _not_false),
    'update_check': Setting(True, _not_false),
    'theme': Setting('green', _choice(THEMES)),
    'palette': Setting(None, _choice((None, 'green', 'classic', 'amber'))),
    'visualization': Setting(True, _not_false),
    'peaks': Setting(True, _not_false),
    'falloff': Setting('normal', _choice(('slow', 'normal', 'fast'))),
    'show_art': Setting(True, _not_false),
    'windowshade': Setting(False, _is_true),
    'expanded_size': Setting([WINDOW_W, WINDOW_H], _window_size),
    'panels': Setting({}, _dict),
}


def _coerce(key, value):
    try:
        return SCHEMA[key].coerce(value)
    except Exception:
        return copy.deepcopy(SCHEMA[key].default)


def load_settings(data):
    """Validated settings for every schema key. Unknown keys are kept so a newer
    config survives being opened, but they are not written back."""
    loaded = dict(data)
    for key, setting in SCHEMA.items():
        loaded[key] = _coerce(key, data[key]) if key in data else copy.deepcopy(setting.default)
    return loaded


def dump_settings(values):
    """The JSON-ready settings to persist, in schema order."""
    return {key: _coerce(key, values[key]) if key in values else copy.deepcopy(setting.default)
            for key, setting in SCHEMA.items()}
