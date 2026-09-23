"""Data directory, config.json and the duration sidecar."""
import json
import math
import os

from gi.repository import GLib

from .constants import (
    APP_DIR,
    DEFAULT_VOLUME,
    EQ_BANDS,
    REPEAT_OFF,
    RG_MODES,
    SAVE_DEBOUNCE_MS,
    SHUFFLE_OFF,
    SHUFFLE_TRACKS,
    WINDOW_H,
    WINDOW_W,
)
from .settings import SettingsStore
from .ui.themes import THEMES


class ConfigMixin:
    def _data_dir(self):
        """Where config.json/playlist.txt live. Portable mode: next to the
        script when its directory is writable (the classic ~/Apps setup).
        Installed mode (/usr is read-only): ~/.config/llamaamp/."""
        override = os.environ.get('LLAMAAMP_DATA_DIR')
        if override:
            os.makedirs(override, exist_ok=True)
            return override
        app_dir = APP_DIR
        if os.access(app_dir, os.W_OK):
            return app_dir
        d = os.path.join(GLib.get_user_config_dir(), 'llamaamp')
        os.makedirs(d, exist_ok=True)
        return d

    def config_path(self):
        return os.path.join(self._data_dir(), "config.json")

    def playlist_path(self):
        return os.path.join(self._data_dir(), "playlist.txt")

    def durations_path(self):
        return os.path.join(self._data_dir(), "durations.json")

    def _load_durations(self):
        """Sidecar cache of track lengths: path -> [mtime, seconds]."""
        self._durations = {}
        self._durations_save_id = None
        try:
            with open(self.durations_path()) as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._durations = {
                    k: v for k, v in data.items()
                    if isinstance(v, list) and len(v) == 2
                    and all(isinstance(x, (int, float)) for x in v)
                }
        except FileNotFoundError:
            pass
        except Exception as e:
            self.log_debug(f"durations load failed: {e}")

    def _save_durations(self):
        self._durations_save_id = None
        try:
            self._atomic_write(self.durations_path(),
                               json.dumps(self._durations))
        except Exception as e:
            self.log_debug(f"durations save failed: {e}")
        return False

    def _note_duration(self, path, seconds):
        """Record a track's length; update its row and the total-time label."""
        if not path or seconds <= 0:
            return
        try:
            mtime = int(os.path.getmtime(path))
        except OSError:
            return
        entry = [mtime, int(seconds)]
        if self._durations.get(path) == entry:
            return
        self._durations[path] = entry
        text = self._fmt_duration(int(seconds))
        it = self.playlist_store.get_iter_first()
        while it is not None:
            if self.playlist_store.get_value(it, 0) == path:
                self.playlist_store.set_value(it, 3, text)
            it = self.playlist_store.iter_next(it)
        self.update_playlist_info()
        if self._durations_save_id is None:
            self._durations_save_id = self.tasks.timeout_add(2000, self._save_durations)

    def _duration_seconds(self, path):
        """Cached duration in seconds, or None (mtime-validated)."""
        entry = self._durations.get(path)
        if not entry:
            return None
        try:
            if int(os.path.getmtime(path)) != int(entry[0]):
                return None
        except OSError:
            return None
        return int(entry[1])

    def _duration_str(self, path):
        secs = self._duration_seconds(path)
        return self._fmt_duration(secs) if secs else ""

    @staticmethod
    def _fmt_duration(secs):
        if secs >= 3600:
            return f"{secs // 3600}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"
        return f"{secs // 60}:{secs % 60:02d}"

    @staticmethod
    def _fmt_total(secs):
        h, m = secs // 3600, (secs % 3600) // 60
        return f"{h}h {m:02d}m" if h else f"{m}m"

    def _atomic_write(self, path, data, binary=False):
        SettingsStore.write(path, data, binary)

    @staticmethod
    def _cfg(cfg, key, cast, default, lo=None, hi=None):
        """Coerce one config value; fall back to default on any bad type/value."""
        try:
            v = cast(cfg[key])
            if lo is not None and v < lo:
                v = lo
            if hi is not None and v > hi:
                v = hi
            return v
        except Exception:
            return default

    def load_config(self):
        """Read config.json into self.config + instance attrs (with safe defaults).
        Every value is type-checked and clamped so a hand-edited or truncated
        config can never prevent startup."""
        defaults = {
            "volume": DEFAULT_VOLUME, "balance": 0.0,
            "eq_values": [0.5] * EQ_BANDS,
            "shuffle": False, "repeat": REPEAT_OFF,
            "last_index": 0, "last_position_ns": 0,
            "window_pos": None,
            # Native-first defaults: bit-transparent DSP bypass + direct DAC
            # output. Both degrade gracefully (DSP reattaches on demand; ALSA
            # falls back to the mixer if the device can't be acquired).
            "direct_mode": True,
            "alsa_output": True,
            "alsa_device": None,
            "playlist_name": None,
            "tray_icon": True,
            "gapless": True,
            "preamp": 0.5,
            "eq_enabled": True,
            "time_remaining": False,
            "replaygain": "off",
            "listenbrainz_token": None,
            "scrobble_enabled": False,
            "notifications": True,
            "update_check": True,
            "theme": "green", "palette": None, "visualization": True,
            "peaks": True, "falloff": "normal", "show_art": True,
            "windowshade": False, "expanded_size": [WINDOW_W, WINDOW_H],
            "panels": {},
        }
        data = {}
        try:
            with open(self.config_path()) as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except FileNotFoundError:
            pass
        except Exception as e:
            self.log_debug(f"config load failed, using defaults: {e}")
        cfg = {**defaults, **data}
        for key, choices, default in [('theme', THEMES, 'green'),
                                      ('palette', (None, 'green', 'classic', 'amber'), None),
                                      ('falloff', ('slow', 'normal', 'fast'), 'normal')]:
            if not isinstance(cfg.get(key), (str, type(None))) or cfg.get(key) not in choices:
                cfg[key] = default
        for key in ('visualization', 'peaks', 'show_art'):
            cfg[key] = cfg.get(key) is not False
        if not isinstance(cfg.get('panels'), dict):
            cfg['panels'] = {}
        size = cfg.get('expanded_size')
        if not (isinstance(size, list) and len(size) == 2 and
                all(isinstance(n, (int, float)) and math.isfinite(n) for n in size)):
            size = [WINDOW_W, WINDOW_H]
        self._expanded_size = [max(440, min(4000, int(size[0]))), max(220, min(4000, int(size[1])))]
        self._windowshade = False
        self._restore_shade = cfg.get('windowshade') is True
        self.config = cfg
        self.volume = self._cfg(cfg, "volume", float, DEFAULT_VOLUME, 0.0, 1.0)
        self.balance = self._cfg(cfg, "balance", float, 0.0, -1.0, 1.0)
        ev = cfg.get("eq_values")
        if isinstance(ev, list) and len(ev) == EQ_BANDS:
            try:
                self.eq_values = [max(0.0, min(1.0, float(x))) for x in ev]
            except Exception:
                self.eq_values = [0.5] * EQ_BANDS
        sh = cfg.get("shuffle", SHUFFLE_OFF)
        if isinstance(sh, bool):        # migrate pre-1.x boolean (bool IS int — check first)
            sh = SHUFFLE_TRACKS if sh else SHUFFLE_OFF
        self.shuffle = self._cfg({"shuffle": sh}, "shuffle", int, SHUFFLE_OFF, 0, 2)
        self.repeat_mode = self._cfg(cfg, "repeat", int, REPEAT_OFF) % 3
        self.direct_mode = cfg.get("direct_mode") is True
        self.alsa_output = cfg.get("alsa_output") is True
        self._alsa_recovers = 0   # busy-DAC auto-recovery budget per session
        pn = cfg.get("playlist_name")
        self._playlist_name = pn if isinstance(pn, str) and pn else None
        self.gapless = cfg.get("gapless", True) is not False
        self._gapless_next = None   # (index, path, uri) preroll set by about-to-finish
        self._awaiting_own_start = False  # the next stream-start belongs to load_song
        self.preamp_value = self._cfg(cfg, "preamp", float, 0.5, 0.0, 1.0)
        self.eq_enabled = cfg.get("eq_enabled", True) is not False
        self._time_remaining = cfg.get("time_remaining") is True
        rg = cfg.get("replaygain")
        self.replaygain = rg if rg in RG_MODES else "off"
        self._loading = False       # True while load_song rebuilds the pipeline
        # Sleep timer (session-only)
        self._sleep_timer_id = None
        self._sleep_after_track = False
        self._sleep_deadline = None
        self._sleep_mode = None
        self._restore_index = self._cfg(cfg, "last_index", int, 0, 0)
        self._restore_position_ns = self._cfg(cfg, "last_position_ns", int, 0, 0)
        wp = cfg.get("window_pos")
        if self._valid_pair(wp):
            self._win_pos = (int(wp[0]), int(wp[1]))

    def apply_config(self):
        """Push restored state into widgets after the UI + playlist exist."""
        if self.panels.x11 and self._win_pos is not None:
            self.move(*self._clamp_position(self._win_pos))
        # Set the player properties directly (the scale's value-changed signal does
        # NOT fire when the value is unchanged, so don't rely on it for syncing).
        self.player.set_property("volume", self.volume)
        if self.panorama is not None:
            self.panorama.set_property("panorama", max(-1.0, min(1.0, self.balance)))
        self.volume_scale.set_value(self.volume)
        self.balance_scale.set_value(self.balance)
        self.apply_all_eq()
        self.update_shuffle_button()
        self.update_repeat_button()
        for bar in self.eq_bars:
            bar.queue_draw()
        # Re-arm last-played track (paused, ready to resume at saved position)
        if self.playlist and 0 <= self._restore_index < len(self.playlist):
            rp = self.playlist[self._restore_index]
            if self._playable(rp):
                self.load_song(self._restore_index)
                if self._restore_position_ns > 0 and not self._is_stream_url(rp):
                    self._pending_seek_ns = self._restore_position_ns
                    self.position = self._restore_position_ns
        self.update_audio_display()
        self._sync_eq_controls()

    def schedule_save_config(self):
        self._sync_eq_controls()
        if hasattr(self, 'playlist_store'):
            self._prepare_next()
        if self._save_timeout_id is not None:
            return
        self._save_timeout_id = self.tasks.timeout_add(SAVE_DEBOUNCE_MS, self._flush_save_config)

    def _flush_save_config(self):
        self._save_timeout_id = None
        self._write_config()
        return False

    def _write_config(self):
        try:
            data = {
                "volume": float(getattr(self, "volume", DEFAULT_VOLUME)),
                "balance": float(getattr(self, "balance", 0.0)),
                "eq_values": [float(x) for x in self.eq_values],
                "shuffle": int(self.shuffle),
                "repeat": int(self.repeat_mode),
                "last_index": int(self.current_index),
                "last_position_ns": self._current_position_ns(),
                "window_pos": list(self._win_pos) if self._win_pos else None,
                "direct_mode": bool(self.direct_mode),
                "alsa_output": bool(self.alsa_output),
                "alsa_device": self.config.get("alsa_device"),
                "playlist_name": self._playlist_name,
                "tray_icon": self.config.get("tray_icon", True) is not False,
                "gapless": bool(self.gapless),
                "preamp": float(self.preamp_value),
                "eq_enabled": bool(self.eq_enabled),
                "time_remaining": bool(self._time_remaining),
                "replaygain": self.replaygain,
                "listenbrainz_token": self.config.get("listenbrainz_token"),
                "scrobble_enabled": self.config.get("scrobble_enabled") is True,
                "notifications": self.config.get("notifications", True) is not False,
                "update_check": self.config.get("update_check", True) is not False,
            }
            data.update({key: self.config[key] for key in
                         ('theme', 'palette', 'visualization', 'peaks', 'falloff', 'show_art')})
            data['windowshade'] = self._windowshade
            data['expanded_size'] = self._expanded_size
            data['panels'] = self.panels.snapshot() if hasattr(self, 'panels') else self.config['panels']
            serialized = json.dumps(data, indent=2)
            if serialized == self._last_config_json:
                return
            self._atomic_write(self.config_path(), serialized)
            self._last_config_json = serialized
        except Exception as e:
            self.log_debug(f"config save failed: {e}")

