"""Data directory, config.json and the duration sidecar."""
import json
import os

from gi.repository import GLib

from .constants import APP_DIR, DEFAULT_VOLUME, SAVE_DEBOUNCE_MS
from .settings import SettingsStore, dump_settings, load_settings


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

    def load_config(self):
        """Read config.json into self.config + instance attrs. Every value is
        validated by the settings schema, so a hand-edited or truncated config
        can never prevent startup."""
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
        cfg = load_settings(data)
        self._expanded_size = list(cfg['expanded_size'])
        self._windowshade = False
        self._restore_shade = cfg['windowshade']
        self.config = cfg
        self.volume = cfg['volume']
        self.balance = cfg['balance']
        self.eq_values = list(cfg['eq_values'])
        self.shuffle = cfg['shuffle']
        self.repeat_mode = cfg['repeat']
        self.direct_mode = cfg['direct_mode']
        self.alsa_output = cfg['alsa_output']
        self._alsa_recovers = 0   # busy-DAC auto-recovery budget per session
        self._playlist_name = cfg['playlist_name']
        self.gapless = cfg['gapless']
        self._gapless_next = None   # (index, path, uri) preroll set by about-to-finish
        self._awaiting_own_start = False  # the next stream-start belongs to load_song
        self.preamp_value = cfg['preamp']
        self.eq_enabled = cfg['eq_enabled']
        self._time_remaining = cfg['time_remaining']
        self.replaygain = cfg['replaygain']
        self._loading = False       # True while load_song rebuilds the pipeline
        # Sleep timer (session-only)
        self._sleep_timer_id = None
        self._sleep_after_track = False
        self._sleep_deadline = None
        self._sleep_mode = None
        self._restore_index = cfg['last_index']
        self._restore_position_ns = cfg['last_position_ns']
        if cfg['window_pos'] is not None:
            self._win_pos = tuple(cfg['window_pos'])

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

    def _live_settings(self):
        """Settings held in attributes rather than self.config."""
        return {
            "volume": getattr(self, "volume", DEFAULT_VOLUME),
            "balance": getattr(self, "balance", 0.0),
            "eq_values": self.eq_values,
            "shuffle": self.shuffle,
            "repeat": self.repeat_mode,
            "last_index": self.current_index,
            "last_position_ns": self._current_position_ns(),
            "window_pos": self._win_pos,
            "direct_mode": self.direct_mode,
            "alsa_output": self.alsa_output,
            "playlist_name": self._playlist_name,
            "gapless": self.gapless,
            "preamp": self.preamp_value,
            "eq_enabled": self.eq_enabled,
            "time_remaining": self._time_remaining,
            "replaygain": self.replaygain,
            "windowshade": self._windowshade,
            "expanded_size": self._expanded_size,
            "panels": self.panels.snapshot() if hasattr(self, 'panels') else self.config['panels'],
        }

    def _write_config(self):
        try:
            data = dump_settings({**self.config, **self._live_settings()})
            serialized = json.dumps(data, indent=2)
            if serialized == self._last_config_json:
                return
            self._atomic_write(self.config_path(), serialized)
            self._last_config_json = serialized
        except Exception as e:
            self.log_debug(f"config save failed: {e}")
