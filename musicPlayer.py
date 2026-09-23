#!/usr/bin/env python3

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gst', '1.0')
gi.require_version('GstPbutils', '1.0')
gi.require_version('GdkPixbuf', '2.0')

from gi.repository import Gtk, Gdk, GLib, Gio, Gst, GstPbutils, Pango, GdkPixbuf
import cairo
import os
import re
import sys
import json
import time
import urllib.parse
import urllib.request
import queue
import random
import signal
import threading
import math
import uuid
from collections import OrderedDict, deque
from contextlib import contextmanager
from pathlib import Path

# Application version
APP_VERSION = "1.5"

# Application name (Winamp-inspired, but an original name — "Winamp" is a trademark)
APP_NAME = "Llama Amp"

# Default display text
DEFAULT_SONG_TEXT = f"{APP_NAME} *** Please select a file ***"

# Update check (GitHub releases)
UPDATE_API_URL = "https://api.github.com/repos/jeremyperson/llama-amp/releases/latest"
RELEASES_URL = "https://github.com/jeremyperson/llama-amp/releases"
UPDATE_CHECK_DELAY_S = 15        # let startup finish before touching the network
UPDATE_RECHECK_S = 86400         # long-running sessions re-check daily
# Inside Flatpak the store owns updates: no version checks, no update UI.
IS_FLATPAK = os.path.exists('/.flatpak-info')

# ---- Tunables / constants ----
WINDOW_W, WINDOW_H = 560, 740
POSITION_TIMER_MS = 100          # position/time UI refresh
SAVE_DEBOUNCE_MS = 1000          # debounce for writing config.json
DEFAULT_VOLUME = 0.7
EQ_GAIN_MIN, EQ_GAIN_MAX = -24.0, 12.0   # equalizer-10bands band range (dB)
EQ_BANDS = 10
SPECTRUM_BANDS = 512
DISPLAY_BANDS = 20
SPECTRUM_INTERVAL_NS = 33_000_000
SPECTRUM_THRESHOLD = -80                 # dB floor for the analyzer
EQ_FREQUENCIES = ['60', '170', '310', '600', '1K', '3K', '6K', '12K', '14K', '16K']
REPEAT_OFF, REPEAT_ALL, REPEAT_ONE = 0, 1, 2
SHUFFLE_OFF, SHUFFLE_TRACKS, SHUFFLE_ALBUMS = 0, 1, 2
RG_MODES = ('off', 'track', 'album')
LISTENBRAINZ_API = "https://api.listenbrainz.org"   # module-level: test-patchable
NOTIFY_MIN_INTERVAL_S = 5
ALBUM_ART_SIZE = 72
FOLDER_ART_NAMES = ('cover', 'folder', 'front', 'album', 'albumart')
FOLDER_ART_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
META_CACHE_LIMIT = 64                    # LRU caps: probed metadata / embedded art
FOLDER_ART_CACHE_LIMIT = 32              # per-directory folder art
SEEK_STEP_SECONDS = 5                    # arrow-key seek step
VOLUME_STEP = 0.05                       # arrow-key / scroll volume step
URI_TARGET_INFO = 80                     # DnD info id for uri-list drops on the playlist

# Preamp: master gain ahead of the EQ bands (headroom for boosts), Winamp-style
PREAMP_DB_RANGE = 12.0                   # slider spans ±12 dB, 0.5 = unity


# Marquee: long titles scroll through a fixed window of the monospace LCD
# (char-count is a faithful width proxy; 44 matches song_label's width cap)
MARQUEE_TICK_MS = 150
MARQUEE_HOLD_TICKS = 13          # ~2 s pause at the start of each loop
MARQUEE_SEP = "  ***  "
MARQUEE_WINDOW = 44

# Debug logging is opt-in: LLAMAAMP_DEBUG=1 (stdout is discarded by the .desktop launcher anyway)
DEBUG = os.environ.get('LLAMAAMP_DEBUG') not in (None, '', '0')

# Spectrum magnitude parsing (PyGObject can't convert GstValueList; see _parse_magnitudes)
_MAG_LIST_RE = re.compile(r"magnitude=\(float\)\s*\{([^}]*)\}")
_MAG_ONE_RE = re.compile(r"magnitude=\(float\)\s*([-\d.eE+]+)")

# EQ presets in dB per band (60..16K); applied via db_to_eq_value
EQ_PRESETS = {
    "Flat":         [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    "Rock":         [5, 4, 3, 1, -1, -1, 1, 3, 4, 4],
    "Pop":          [-1, 2, 4, 4, 2, 0, -1, -1, 1, 2],
    "Jazz":         [3, 2, 1, 2, -1, -1, 0, 1, 2, 3],
    "Classical":    [4, 3, 2, 0, -1, -1, 0, 2, 3, 4],
    "Bass Boost":   [7, 6, 5, 3, 1, 0, 0, 0, 0, 0],
    "Treble Boost": [0, 0, 0, 0, 0, 2, 4, 6, 7, 7],
}

MPRIS_BUS_NAME = "org.mpris.MediaPlayer2.llamaamp"
MPRIS_OBJECT_PATH = "/org/mpris/MediaPlayer2"
MPRIS_XML = """
<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/>
    <method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Stop"/>
    <method name="Play"/>
    <method name="Seek"><arg direction="in" name="Offset" type="x"/></method>
    <method name="SetPosition">
      <arg direction="in" name="TrackId" type="o"/>
      <arg direction="in" name="Position" type="x"/>
    </method>
    <method name="OpenUri"><arg direction="in" name="Uri" type="s"/></method>
    <signal name="Seeked"><arg name="Position" type="x"/></signal>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="LoopStatus" type="s" access="readwrite"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Shuffle" type="b" access="readwrite"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>
"""

# Initialize GStreamer
Gst.init(None)

class PlaybackOrder:
    """Session identities, queue and actual playback history, independent of GTK."""
    def __init__(self):
        self.entries = []  # (session ID, path); duplicate paths remain distinct
        self.queue = deque()
        self.history = []
        self.cursor = -1
        self.current = None
        self.failed = set()

    def reconcile(self, entries):
        self.entries = list(entries)
        valid = {key for key, _ in self.entries}
        self.queue = deque(key for key in self.queue if key in valid)

    def next_id(self, playable, shuffle, repeat, auto=True, rng=random):
        available = [(key, path) for key, path in self.entries
                     if key not in self.failed and playable(path)]
        valid = {key for key, _ in available}
        if not valid:
            return None
        if auto and repeat == REPEAT_ONE and self.current in valid:
            return self.current
        for key in self.queue:
            if key in valid:
                return key
        for key in self.history[self.cursor + 1:]:
            if key in valid:
                return key
        if shuffle == SHUFFLE_TRACKS:
            return rng.choice([key for key, _ in available if key != self.current]
                              or list(valid))
        indices = {key: i for i, (key, _) in enumerate(self.entries)}
        index = indices.get(self.current, -1)
        if shuffle == SHUFFLE_ALBUMS:
            def album(path):
                return path if path.startswith(('http://', 'https://')) else os.path.dirname(path)
            current_album = album(self.entries[index][1]) if index >= 0 else None
            after = [(key, path) for key, path in available if indices[key] > index]
            if after and album(after[0][1]) == current_album:
                return after[0][0]
            runs = []
            previous = None
            for key, path in available:
                group = album(path)
                if group != previous:
                    runs.append((key, group))
                previous = group
            return rng.choice([r for r in runs if r[1] != current_album] or runs)[0]
        after = [key for key, _ in available if indices[key] > index]
        return after[0] if after else (available[0][0] if repeat == REPEAT_ALL else None)

    def previous_id(self, playable):
        paths = dict(self.entries)
        start = self.cursor
        if start >= 0 and self.history[start] == self.current:
            start -= 1
        for index in range(start, -1, -1):
            key = self.history[index]
            if key in paths and key not in self.failed and playable(paths[key]):
                self.cursor = index
                self.current = key
                return key
        return self.current

    def select(self, key):
        # Navigation consumes queue entries even while paused; history records
        # only tracks for which playback has actually been requested.
        if key in self.queue:
            self.queue.remove(key)
            self.history = self.history[:self.cursor + 1]
        self.current = key

    def commit(self, key):
        valid = dict(self.entries)
        if key not in valid:
            return
        queued = key in self.queue
        if queued:
            self.queue.remove(key)
        if self.cursor < 0 or self.history[self.cursor] != key:
            forward = self.history[self.cursor + 1:]
            if not queued and key in forward:
                self.cursor += forward.index(key) + 1
            else:
                self.history = self.history[:self.cursor + 1] + [key]
                self.history = self.history[-1000:]
                self.cursor = len(self.history) - 1
        self.current = key


class AnalyzerState:
    """Time-based ballistics. All levels are normalized to the display height."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.targets = [0.0] * DISPLAY_BANDS
        self.levels = [0.0] * DISPLAY_BANDS
        self.peaks = [0.0] * DISPLAY_BANDS
        self.hold_until = [0.0] * DISPLAY_BANDS
        self.last_input = None
        self.last_tick = None

    def feed(self, magnitudes, sample_rate, now):
        if not magnitudes or not sample_rate or sample_rate <= 0:
            return
        nyquist = sample_rate / 2
        top = min(20000.0, nyquist)
        if top <= 40:
            return
        mapping_key = (len(magnitudes), sample_rate)
        if getattr(self, '_mapping_key', None) != mapping_key:
            step = nyquist / len(magnitudes)
            edges = [40 * (top / 40) ** (i / DISPLAY_BANDS)
                     for i in range(DISPLAY_BANDS + 1)]
            self._ranges = []
            for low, high in zip(edges, edges[1:]):
                start = min(len(magnitudes) - 1, max(0, int(low / step)))
                end = min(len(magnitudes), max(start + 1, int(math.ceil(high / step))))
                self._ranges.append((start, end))
            self._mapping_key = mapping_key
        floor = 10 ** (SPECTRUM_THRESHOLD / 10)
        powers = [floor if not math.isfinite(v) or v <= SPECTRUM_THRESHOLD
                  else 1.0 if v >= 0 else 10 ** (v * .1) for v in magnitudes]
        self.targets = [max(0.0, min(1.0,
                            (10 * math.log10(sum(powers[start:end]) / (end - start))
                             - SPECTRUM_THRESHOLD) / -SPECTRUM_THRESHOLD))
                        for start, end in self._ranges]
        self.last_input = now

    def tick(self, now, playing=True, speed=1.0):
        dt = 0 if self.last_tick is None else max(0, now - self.last_tick)
        self.last_tick = now
        active = playing and self.last_input is not None and now - self.last_input < .25
        changed = False
        for i, target in enumerate(self.targets):
            target = target if active else 0.0
            old, peak = self.levels[i], self.peaks[i]
            level = max(target, old - 1.8 * speed * dt)
            if level > peak or (level > 0 and target >= peak):
                peak = level
                self.hold_until[i] = now + .25
            else:
                fall_time = min(dt, max(0, now - self.hold_until[i]))
                peak = max(level, peak - .65 * speed * fall_time)
            changed |= abs(level - old) > 1e-6 or abs(peak - self.peaks[i]) > 1e-6
            self.levels[i], self.peaks[i] = level, peak
        return changed


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


class MainLoopTasks:
    """One lifetime for timers and cross-thread callbacks owned by the player."""
    def __init__(self):
        self.ids = set()
        self.closed = False
        self.lock = threading.RLock()

    def _add(self, factory, prefix, callback, args):
        with self.lock:
            if self.closed:
                return None
            handle = [None]
            def invoke():
                with self.lock:
                    if self.closed:
                        return False
                keep = False
                try:
                    keep = bool(callback(*args))
                    return keep
                finally:
                    if not keep:
                        with self.lock:
                            self.ids.discard(handle[0])
            handle[0] = factory(*prefix, invoke)
            self.ids.add(handle[0])
            return handle[0]

    def idle_add(self, callback, *args):
        return self._add(GLib.idle_add, (), callback, args)

    def timeout_add(self, interval, callback, *args):
        return self._add(GLib.timeout_add, (interval,), callback, args)

    def timeout_add_seconds(self, interval, callback, *args):
        return self._add(GLib.timeout_add_seconds, (interval,), callback, args)

    def unix_signal_add(self, priority, sig, callback):
        return self._add(GLib.unix_signal_add, (priority, sig), callback, ())

    def source_remove(self, handle):
        with self.lock:
            if handle in self.ids:
                GLib.source_remove(handle)
                self.ids.discard(handle)

    def close(self):
        with self.lock:
            self.closed = True
            for handle in list(self.ids):
                self.source_remove(handle)


class MetadataWorker:
    """One worker and one queue; no GTK state is accessed by dispatch itself."""
    def __init__(self, handlers, on_error):
        self.queue = queue.Queue()
        self.handlers = handlers
        self.on_error = on_error
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._run, name='llama-metadata', daemon=True)
        self.thread.start()

    def _run(self):
        while not self.closed.is_set():
            job = self.queue.get()
            if job is None or self.closed.is_set():
                break
            try:
                self.handlers[job[0]](*job[1:])
            except Exception as exc:
                self.on_error(f'Metadata worker: {type(exc).__name__}')

    def close(self):
        self.closed.set()
        self.queue.put(None)


THEMES = {
    'green': dict(name='Llama Green', chassis='#101510', panel='#1b231c', control='#303b31',
                  text='#d8e5d7', accent='#52ef34', lcd='#081007', stripe='#111d12', palette='green'),
    'silver': dict(name='Classic Silver', chassis='#272936', panel='#36394a', control='#adb3bd',
                   text='#eeeeef', accent='#8cfa65', lcd='#060b08', stripe='#171d1a', palette='classic'),
    'amber': dict(name='Amber', chassis='#191510', panel='#292219', control='#463b2d',
                  text='#f0e0c5', accent='#ffba45', lcd='#100c04', stripe='#211a10', palette='amber'),
}

class PanelManager:
    """Owns auxiliary windows and reparents their live controls when attached."""
    def __init__(self, app, host):
        self.app, self.host = app, host
        self.items = {}
        self.x11 = 'X11' in Gdk.Display.get_default().__gtype__.name
        self.drag = None
        self.settle_id = None

    def add(self, name, title, content, expand=False):
        saved = self.app.config['panels'].get(name, {})
        if not isinstance(saved, dict):
            saved = {}
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        frame.get_style_context().add_class('music-player-frame')
        frame.set_no_show_all(True)
        header = Gtk.EventBox()
        header.get_style_context().add_class('panel-header')
        row = Gtk.Box(spacing=4)
        caption = Gtk.Label(label=title, xalign=0)
        caption.get_style_context().add_class('music-player-label')
        caption.set_ellipsize(Pango.EllipsizeMode.END)
        row.pack_start(caption, True, True, 0)
        if name == 'playlist':
            row.pack_start(self.app.playlist_info, False, False, 6)
        collapse = self.app._panel_button(name, 'collapse')
        collapse.set_tooltip_text('Collapse / expand ' + title.lower())
        collapse.connect('clicked', lambda *_: self.collapse(name))
        row.pack_start(collapse, False, False, 0)
        detach = self.app._panel_button(name, 'detach')
        detach.set_tooltip_text('Detach / attach ' + title.lower())
        detach.connect('clicked', lambda *_: self.toggle_attach(name))
        row.pack_start(detach, False, False, 0)
        header.add(row)
        header.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        header.connect('button-press-event', self._header_press, name)
        frame.pack_start(header, False, False, 0)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.set_no_show_all(True)
        body.pack_start(content, True, True, 0)
        frame.pack_start(body, True, True, 0)
        slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        slot.set_no_show_all(True)
        self.host.pack_start(slot, expand, expand, 0)
        slot.pack_start(frame, True, True, 0)
        item = dict(frame=frame, body=body, slot=slot, window=None,
                    collapse=collapse, detach=detach, title=title, expand=expand,
                    visible=saved.get('visible') is not False,
                    collapsed=saved.get('collapsed') is True,
                    attached=saved.get('attached') is not False,
                    size=saved.get('size', [560, 240]), position=saved.get('position'),
                    snap_to=saved.get('snap_to') if self.x11 and saved.get('snap_to') in ('main', 'eq', 'playlist') and saved.get('snap_to') != name else None)
        self.items[name] = item
        content.show_all()
        header.show_all()

    def restore(self):
        for name, item in self.items.items():
            if not item['attached']:
                self._detach(name)
            self._show(name)

    def _show(self, name):
        item = self.items[name]
        visible = item['visible'] and not self.app._windowshade
        self._update_controls(name)
        item['body'].set_visible(not item['collapsed'])
        self.host.set_child_packing(item['slot'], item['expand'] and not item['collapsed'],
                                    item['expand'] and not item['collapsed'], 0, Gtk.PackType.START)
        item['frame'].set_visible(visible)
        item['slot'].set_visible(visible and item['attached'])
        if item['window']:
            item['window'].set_visible(visible and not item['attached'])

    def _update_controls(self, name):
        item = self.items[name]
        for key, verb in [('collapse', 'Expand' if item['collapsed'] else 'Collapse'),
                          ('detach', 'Detach' if item['attached'] else 'Reattach')]:
            text = f"{verb} {item['title'].lower()}"
            item[key].set_tooltip_text(text)
            item[key].get_accessible().set_name(text)
            item[key].queue_draw()

    def set_visible(self, name, visible):
        self.items[name]['visible'] = visible
        self._show(name)
        self.app.schedule_save_config()

    def collapse(self, name):
        item = self.items[name]
        item['collapsed'] = not item['collapsed']
        self._show(name)
        self.app.schedule_save_config()

    def toggle_attach(self, name):
        item = self.items[name]
        if item['attached']:
            self._detach(name)
        else:
            frame = item['frame']
            frame.get_parent().remove(frame)
            item['slot'].pack_start(frame, True, True, 0)
            item['window'].hide()
            item['attached'] = True
            item['snap_to'] = None
            self._update_controls(name)
        self._show(name)
        self.app.schedule_save_config()

    def _detach(self, name):
        item = self.items[name]
        if item['window'] is None:
            window = Gtk.Window(title=f"{self.app.get_title()} — {item['title']}")
            window.set_decorated(False)
            window.set_transient_for(self.app)
            window.set_destroy_with_parent(True)
            window.get_style_context().add_class('llama-window')
            shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            shell.get_style_context().add_class('music-player-main')
            self.app._add_resize_grip(window, shell)
            shell.show()
            window.connect('delete-event', lambda *_: (self.set_visible(name, False), True)[1])
            window.connect('key-press-event', self.app.on_window_key_press)
            window.connect('configure-event', self._configured, name)
            window.connect('button-press-event', self.app._resize_press)
            window.set_geometry_hints(None, self.app._minimum_geometry(80), Gdk.WindowHints.MIN_SIZE)
            width, height = self.app._clamp_size(item['size'], minimum_height=80)
            window.set_default_size(width, height)
            if self.x11 and self.app._valid_pair(item['position']):
                window.move(*self.app._clamp_position(item['position']))
            item['window'] = window
            item['shell'] = shell
        frame = item['frame']
        frame.get_parent().remove(frame)
        item['shell'].pack_start(frame, True, True, 0)
        item['attached'] = False
        self._update_controls(name)

    def _header_press(self, widget, event, name):
        item = self.items[name]
        if event.button == 1 and event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            self.collapse(name)
            return True
        if event.button == 1 and not item['attached']:
            window = item['window']
            self.start_drag(window, event)
            window.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
            return True
        return False

    def windows(self):
        return {'main': self.app, **{name: item['window'] for name, item in self.items.items()
                                    if item['window'] and not item['attached'] and item['visible']}}

    def start_drag(self, window, event):
        if not self.x11:
            return
        windows = self.windows()
        root = next((name for name, win in windows.items() if win == window), 'main')
        if event.state & Gdk.ModifierType.MOD1_MASK:
            if root != 'main':
                self.items[root]['snap_to'] = None
            for item in self.items.values():
                if item['snap_to'] == root:
                    item['snap_to'] = None
        members = {root}
        for _ in self.items:
            for name, item in self.items.items():
                if item['snap_to'] in members and name in windows:
                    members.add(name)
        self.drag = (root, {name: windows[name].get_position() for name in members})
        self._drag_alt = bool(event.state & Gdk.ModifierType.MOD1_MASK)

    def _configured(self, window, event, name):
        item = self.items[name]
        item['size'] = [event.width, event.height]
        if self.x11:
            item['position'] = list(window.get_position())
        self.configured(name)
        return False

    def configured(self, name):
        if not self.x11 or not self.drag or self.drag[0] != name:
            return
        root, origins = self.drag
        windows = self.windows()
        if root not in windows:
            return
        x, y = windows[root].get_position()
        ox, oy = origins[root]
        for member, (mx, my) in origins.items():
            if member != root and member in windows:
                windows[member].move(mx + x - ox, my + y - oy)
        if self.settle_id is not None:
            self.app.tasks.source_remove(self.settle_id)
        self.settle_id = self.app.tasks.timeout_add(180, self._settle)

    def _settle(self):
        if self.drag and not self.app._destroyed:
            pointer = Gdk.Display.get_default().get_default_seat().get_pointer()
            state = self.app.get_window().get_device_position(pointer)[-1]
            if state & Gdk.ModifierType.BUTTON1_MASK:
                return True  # keep moving the group through pauses in a drag
        self.settle_id = None
        drag, self.drag = self.drag, None
        if not drag or self.app._destroyed:
            return False
        root, origins = drag
        windows = self.windows()
        if root != 'main' and root in windows and not self._drag_alt:
            moving = windows[root]
            x, y = moving.get_position()
            w, h = moving.get_size()
            best = None
            for target, window in windows.items():
                if target in origins:
                    continue
                tx, ty = window.get_position()
                tw, th = window.get_size()
                candidates = []
                if x < tx + tw and x + w > tx:
                    candidates.extend([(abs(y - ty - th), x, ty + th),
                                       (abs(y + h - ty), x, ty - h)])
                if y < ty + th and y + h > ty:
                    candidates.extend([(abs(x - tx - tw), tx + tw, y),
                                       (abs(x + w - tx), tx - w, y)])
                for distance, nx, ny in candidates:
                    if distance <= 12 and (best is None or distance < best[0]):
                        best = (distance, nx, ny, target)
            self.items[root]['snap_to'] = best[3] if best else None
            if best:
                moving.move(best[1], best[2])
        self.app.schedule_save_config()
        return False

    def snapshot(self):
        return {name: {key: item[key] for key in ('visible', 'collapsed', 'attached', 'size', 'position', 'snap_to')}
                for name, item in self.items.items()}

    def reset(self):
        for name, item in self.items.items():
            if not item['attached']:
                self.toggle_attach(name)
            item.update(visible=True, collapsed=False, snap_to=None)
            self._show(name)

    def close(self):
        if self.settle_id is not None:
            self.app.tasks.source_remove(self.settle_id)
        for item in self.items.values():
            if item['window']:
                item['window'].destroy()


class MusicPlayer(Gtk.Window):
    def __init__(self):
        super().__init__(title=APP_NAME)
        self.tasks = MainLoopTasks()
        
        # Window setup
        self.set_default_size(WINDOW_W, WINDOW_H)
        self.set_resizable(True)
        self.set_position(Gtk.WindowPosition.CENTER)
        
        # Remove window decorations to create custom title bar
        self.set_decorated(False)

        # Real rounded corners: transparent window background (scoped by class)
        # + RGBA visual so the compositor blends the radius. Degrades to square
        # corners without a compositor.
        self.get_style_context().add_class('llama-window')
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.set_visual(visual)
        
        # Add helpful tooltip for drag and drop
        self.set_tooltip_text("Drag and drop audio files to add them to the playlist")
        
        # Audio setup
        self.player = Gst.ElementFactory.make("playbin", "player")
        self.current_song = None
        self.is_playing = False
        self.playback_state = 'Stopped'
        self.playlist = []
        self.current_index = 0
        self.position = 0
        self.duration = 0
        self.shuffle = False
        self.repeat_mode = REPEAT_OFF   # 0 OFF / 1 ALL / 2 ONE
        self.seeking = False

        # DSP element handles (set by build_audio_filter; may stay None)
        self.equalizer = None
        self.panorama = None
        self.spectrum = None
        self.preamp = None
        self.rgvolume = None

        # Live analyzer levels (drive the EQ bar heights; separate from eq gains)
        self.analyzer_state = AnalyzerState()
        self._analyzer_id = None

        # Title-bar drag state (initialised so motion before press can't AttributeError)
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.is_dragging = False

        # Bookkeeping for timers, persistence, reorder, metadata cache
        self._timeout_ids = []
        self._save_timeout_id = None
        self._pending_seek_ns = None
        self._reordering = False        # a resync is scheduled (debounce)
        self._suppress_store = False     # programmatic store edits (don't resync)
        self._playlist_titles = {}  # path -> tagged title, or None after probing
        self._title_rows = {}       # path -> persistent GTK row references
        self._meta_cache = OrderedDict()         # path -> props dict, or False (probe failed)
        self._art_cache = OrderedDict()          # path -> embedded-art GdkPixbuf
        self._folder_art_cache = OrderedDict()   # dir  -> folder-art GdkPixbuf or None
        self.config = {}
        self._destroyed = False
        self._last_config_json = None
        self._win_pos = None            # tracked via configure-event, persisted
        self._load_gen = 0              # bumped per load_song; guards stale async results
        self._loaded_uri = None         # guards stale bus tag messages
        self._drop_feedback_id = None
        self._default_art = None
        self._default_art_loaded = False
        self.order = PlaybackOrder()
        self.entry_ids = []
        self._play_next = self.order.queue
        self._undo = deque(maxlen=20)
        self._undoing = False
        self._gapless_lock = threading.RLock()
        self._next_snapshot = None
        self._next_generation = 0
        self._search_pos = -1           # last playlist-search hit index

        # Single background worker drains all metadata probes + folder-art scans:
        # bounded concurrency no matter how fast the user scrolls the playlist.
        self._probe_inflight = set()
        self.metadata_worker = MetadataWorker({'meta': self._probe_one, 'folderart': self._folder_art_scan}, self.log_debug)
        self._probe_queue = self.metadata_worker.queue

        # Audio properties for dynamic display
        self.audio_properties = {'sample_rate': 0, 'bitrate': 0, 'channels': 0}

        # Restore saved settings (volume/balance/eq/shuffle/repeat/last track)
        self.load_config()

        # Attach the audio filter: full DSP chain, or analyzer-only tap in
        # direct mode (spectrum bars stay alive either way)
        self._attach_filter()

        # Restore direct-to-DAC output if it was enabled last session
        if self.alsa_output:
            sink = self._make_alsa_sink()
            if sink is not None:
                self.player.set_property("audio-sink", sink)
            else:
                self.alsa_output = False

        # Listen to the GStreamer bus (errors, EOS, tags, spectrum, state)
        self.setup_bus()

        # Set up CSS styling for authentic music player look
        self.setup_styling()
        
        
        # Create the interface
        self.create_interface()

        # Connect signals
        self.connect("destroy", self.on_destroy)
        self.connect("configure-event", self.on_configure_event)
        self.connect("window-state-event", self.on_window_state_event)
        self.connect("key-press-event", self.on_window_key_press)

        # Clean shutdown (config + playlist save) on SIGTERM/SIGINT too
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.tasks.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._on_unix_signal)

        # Set up drag and drop
        self.setup_drag_and_drop()

        # Timer for updating position
        self._timeout_ids.append(self.tasks.timeout_add(POSITION_TIMER_MS, self.update_position))

        # Track-length sidecar, then the saved playlist (rows read the cache)
        self._load_durations()
        self.load_playlist()

        # Apply restored settings to widgets and re-arm the last-played track
        self.apply_config()

        # Desktop integration: media keys, panel / lockscreen controls
        self._mpris_setup()

        # System tray / app indicator
        self._init_tray()

        # New-release check (GitHub), delayed so startup never waits on it;
        # long-running sessions re-check daily. Both respect the ⚙ toggle
        # at fire time, so disabling it needs no restart.
        self._update_info = None
        if not IS_FLATPAK:
            if self.config.get("update_check", True) is not False:
                self.tasks.timeout_add_seconds(UPDATE_CHECK_DELAY_S,
                                         self._startup_update_check)
            self._timeout_ids.append(self.tasks.timeout_add_seconds(
                UPDATE_RECHECK_S, self._periodic_update_check))

    # ==================== Real audio DSP (EQ / balance / spectrum) ====================
    def build_audio_filter(self, analyzer_only=False):
        """Build (and return) a playbin audio-filter bin.

        Full chain: audioconvert -> equalizer -> panorama -> spectrum -> audioconvert.
        analyzer_only (Direct Mode): audioconvert -> spectrum. The spectrum
        element is passthrough analysis — it never modifies samples — and its
        audioconvert stays passthrough for every common decoder format (at most
        it does lossless integer widening for exotic packed layouts), so the
        bars stay alive while playback remains bit-exact.

        Returns None if elements are missing (degrade to no filter)."""
        bin_ = Gst.Bin.new("audio-filter-bin")
        conv_in = Gst.ElementFactory.make("audioconvert", "conv_in")
        self.spectrum = Gst.ElementFactory.make("spectrum", "spectrum")

        if analyzer_only:
            self.equalizer = None
            self.panorama = None
            self.preamp = None
            self.rgvolume = None
            chain = (conv_in, self.spectrum)
        else:
            self.preamp = Gst.ElementFactory.make("volume", "preamp")
            self.equalizer = Gst.ElementFactory.make("equalizer-10bands", "equalizer")
            self.panorama = Gst.ElementFactory.make("audiopanorama", "panorama")
            conv_out = Gst.ElementFactory.make("audioconvert", "conv_out")
            chain = (conv_in, self.preamp, self.equalizer,
                     self.panorama, self.spectrum, conv_out)
            # ReplayGain normalization sits ahead of the user preamp so the two
            # compose; the limiter soft-clips only RG-boosted signal. Elements
            # exist only when enabled — 'off' keeps the chain byte-identical.
            if self.replaygain != 'off':
                self.rgvolume = Gst.ElementFactory.make("rgvolume", "rgvolume")
                rglimiter = Gst.ElementFactory.make("rglimiter", "rglimiter")
                if self.rgvolume is not None and rglimiter is not None:
                    self.rgvolume.set_property("album-mode",
                                               self.replaygain == 'album')
                    self.rgvolume.set_property("pre-amp", 0.0)
                    self.rgvolume.set_property("fallback-gain", 0.0)
                    chain = (conv_in, self.rgvolume, rglimiter, self.preamp,
                             self.equalizer, self.panorama, self.spectrum, conv_out)
                else:
                    self.log_debug("rgvolume/rglimiter unavailable; ReplayGain off")
                    self.rgvolume = None
            else:
                self.rgvolume = None

        if bin_ is None or not all(chain):
            self.log_debug("DSP elements unavailable; playing without EQ/balance/spectrum")
            self.equalizer = self.panorama = self.spectrum = self.preamp = None
            return None

        self.spectrum.set_property("bands", SPECTRUM_BANDS)
        self.spectrum.set_property("interval", SPECTRUM_INTERVAL_NS)
        self.spectrum.set_property("threshold", SPECTRUM_THRESHOLD)
        self.spectrum.set_property("post-messages", True)
        self.spectrum.set_property("message-magnitude", True)
        if self.panorama is not None:
            try:
                self.panorama.set_property("method", 0)  # psychoacoustic (preserve loudness)
            except Exception:
                pass

        for e in chain:
            bin_.add(e)
        for a, b in zip(chain, chain[1:]):
            if not a.link(b):
                self.log_debug(f"DSP link failed: {a.get_name()} -> {b.get_name()}; "
                               "playing without EQ/balance/spectrum")
                self.equalizer = self.panorama = self.spectrum = self.preamp = None
                return None

        bin_.add_pad(Gst.GhostPad.new("sink", conv_in.get_static_pad("sink")))
        bin_.add_pad(Gst.GhostPad.new("src", chain[-1].get_static_pad("src")))
        return bin_

    def _attach_filter(self):
        """Attach the filter bin matching the current direct_mode (NULL state only)."""
        bin_ = self.build_audio_filter(analyzer_only=self.direct_mode)
        self.player.set_property("audio-filter", bin_)
        self._update_analyzer_visibility()
        if not self.direct_mode and self.equalizer is not None:
            self.apply_all_eq()
            if self.panorama is not None:
                self.panorama.set_property("panorama",
                                           max(-1.0, min(1.0, self.balance)))

    def _rebuild_pipeline(self):
        """Rebuild the audio-filter chain and resume the current track at the
        same position. playbin's audio-filter only changes in the NULL state,
        so this is the shared machinery for Direct Mode and ReplayGain toggles."""
        self._invalidate_next()
        was_playing = self.is_playing
        resume_ns = self._current_position_ns()

        self._loading = True  # freeze about-to-finish during the rebuild
        try:
            self.player.set_state(Gst.State.NULL)
            self._attach_filter()

            if self.current_song:
                self.load_song(self.current_index)
                if resume_ns > 0 and not self._is_stream_url(self.current_song):
                    self._pending_seek_ns = resume_ns
                if was_playing:
                    self.player.set_state(Gst.State.PLAYING)
                    self._sync_play_ui(True)
                else:
                    self.player.set_state(Gst.State.PAUSED)
        finally:
            self._loading = False

    def toggle_direct_mode(self, *_args):
        """Toggle bit-transparent playback: swap between the full DSP chain and
        the analyzer-only tap (spectrum keeps running either way)."""
        self.direct_mode = not self.direct_mode
        self._rebuild_pipeline()
        self.schedule_save_config()
        state = ("ON — EQ/balance bypassed, analyzer on" if self.direct_mode
                 else "OFF — DSP active")
        if self.direct_mode and self.replaygain != 'off':
            state += " (ReplayGain inactive in this mode)"
        self.show_drop_feedback(f"Direct mode {state}")

    def set_replaygain(self, mode):
        """Switch ReplayGain mode. track<->album is a live property flip;
        off<->on rebuilds the filter chain (audio-filter swaps only at NULL)."""
        if mode not in RG_MODES or mode == self.replaygain:
            return
        prev = self.replaygain
        self.replaygain = mode
        if 'off' not in (prev, mode) and self.rgvolume is not None:
            self.rgvolume.set_property("album-mode", mode == 'album')
        elif not self.direct_mode:
            self._rebuild_pipeline()
        self.schedule_save_config()
        if self.direct_mode and mode != 'off':
            self.show_drop_feedback(
                f"ReplayGain {mode} armed — no effect while Direct Mode is ON")
        else:
            self.show_drop_feedback(f"ReplayGain: {mode}")

    def _list_alsa_devices(self):
        """[(hw:X,Y, 'CardName — PcmName'), ...] for all playback PCMs."""
        cards = {}
        try:
            with open('/proc/asound/cards') as f:
                for line in f:
                    m = re.match(r'\s*(\d+)\s+\[', line)
                    if m and ':' in line:
                        cards[int(m.group(1))] = line.split(':', 1)[1].strip().split(' at ')[0]
        except Exception:
            pass
        devices = []
        try:
            with open('/proc/asound/pcm') as f:
                for line in f:
                    if 'playback' not in line.lower():
                        continue
                    parts = line.split(':')
                    c, d = parts[0].strip().split('-')
                    name = parts[2].strip() if len(parts) > 2 else parts[1].strip()
                    card_name = cards.get(int(c), f"card {int(c)}")
                    devices.append((f"hw:{int(c)},{int(d)}", f"{card_name} — {name}"))
        except Exception as e:
            self.log_debug(f"alsa device list failed: {e}")
        return devices

    def _select_alsa_device(self, device):
        """Persist an ALSA device choice (None = auto) and reacquire if active."""
        if self.config.get('alsa_device') == device:
            return
        self.config['alsa_device'] = device
        self.schedule_save_config()
        if self.alsa_output:
            self._alsa_reacquire()
        else:
            self.show_drop_feedback("Device saved — enable ALSA Output to use it")

    def _detect_alsa_device(self):
        """Pick the hw: device for direct DAC output. Config 'alsa_device'
        overrides; otherwise prefer the first *Analog* playback PCM (GPU HDMI
        ports often enumerate as card 0 ahead of the analog codec), falling
        back to the first playback PCM of any kind."""
        dev = self.config.get('alsa_device')
        if isinstance(dev, str) and dev:
            return dev
        first = None
        try:
            with open('/proc/asound/pcm') as f:
                for line in f:
                    if 'playback' not in line.lower():
                        continue
                    card_dev = line.split(':', 1)[0].strip()
                    c, d = card_dev.split('-')
                    hw = f"hw:{int(c)},{int(d)}"
                    if first is None:
                        first = hw
                    if 'analog' in line.lower():
                        return hw
        except Exception as e:
            self.log_debug(f"alsa detect failed: {e}")
        return first or "hw:0,0"

    def _make_alsa_sink(self):
        sink = Gst.ElementFactory.make("alsasink", "alsa_direct")
        if sink is None:
            return None
        self._alsa_device = self._detect_alsa_device()
        sink.set_property("device", self._alsa_device)
        return sink

    def toggle_alsa_output(self, *_args):
        """Toggle direct ALSA output: hand samples straight to the DAC's hw:
        device, skipping the PulseAudio/PipeWire mixer (and its resampling).
        The sound server only releases the device a few seconds after all
        streams stop (WirePlumber suspend-on-idle), so enabling retries for
        ~10s before falling back to the mixer. While active, the device is
        exclusive to Llama Amp."""
        if self.alsa_output:
            # Turning OFF: hand the device back to the mixer (synchronous)
            self.alsa_output = False
            was_playing = self.is_playing
            resume_ns = self._current_position_ns()
            self._switching_output = True
            try:
                self.player.set_state(Gst.State.NULL)
                self.player.set_property("audio-sink", None)
                if self.current_song:
                    self.load_song(self.current_index)
                    if resume_ns > 0:
                        self._pending_seek_ns = resume_ns
                    self.player.set_state(
                        Gst.State.PLAYING if was_playing else Gst.State.PAUSED)
                    if was_playing:
                        self._sync_play_ui(True)
            finally:
                self._switching_output = False
            self.schedule_save_config()
            self.show_drop_feedback("Output: system mixer")
            return

        # Turning ON: async retry while the server releases the device
        self._alsa_reacquire()

    def _alsa_reacquire(self):
        """(Re)build the alsasink from config and start the async acquire flow.
        Used by the ALSA toggle and by device-picker changes."""
        self._invalidate_next()
        sink = self._make_alsa_sink()
        if sink is None:
            self.alsa_output = False
            self.show_drop_feedback("alsasink not available")
            return
        self.alsa_output = True
        self._alsa_ctx = {
            'was_playing': self.is_playing,
            'resume_ns': self._current_position_ns(),
            'tries': 0,
        }
        self._switching_output = True
        self.player.set_state(Gst.State.NULL)
        self.player.set_property("audio-sink", sink)
        self.show_drop_feedback(f"Acquiring {self._alsa_device}…")
        self.tasks.timeout_add(400, self._alsa_try_start)

    def _alsa_try_start(self):
        ctx = self._alsa_ctx
        ctx['tries'] += 1
        target = Gst.State.PLAYING if ctx['was_playing'] else Gst.State.PAUSED

        if self.current_song:
            self.load_song(self.current_index)
            if ctx['resume_ns'] > 0:
                self._pending_seek_ns = ctx['resume_ns']
            self.player.set_state(target)
            result = self.player.get_state(2 * Gst.SECOND)[0]
            if result == Gst.StateChangeReturn.FAILURE:
                self.player.set_state(Gst.State.NULL)
                if ctx['tries'] < 5:
                    self.show_drop_feedback(
                        f"DAC busy — waiting for release ({ctx['tries']}/5)…")
                    self.tasks.timeout_add(1800, self._alsa_try_start)
                    return False
                # Give up: revert to the mixer
                dev = self._alsa_device
                self.alsa_output = False
                self.player.set_property("audio-sink", None)
                self.load_song(self.current_index)
                if ctx['resume_ns'] > 0:
                    self._pending_seek_ns = ctx['resume_ns']
                self.player.set_state(target)
                if ctx['was_playing']:
                    self._sync_play_ui(True)
                self._switching_output = False
                self.schedule_save_config()
                self.show_drop_feedback(f"ALSA {dev} unavailable — using mixer")
                return False
            if ctx['was_playing']:
                self._sync_play_ui(True)

        self._switching_output = False
        self.schedule_save_config()
        self.show_drop_feedback(f"ALSA direct → {self._alsa_device}")
        return False

    def eq_value_to_db(self, v):
        """Map a 0..1 slider value to dB, with 0.5 = flat (0 dB)."""
        v = max(0.0, min(1.0, v))
        if v >= 0.5:
            return (v - 0.5) / 0.5 * EQ_GAIN_MAX
        return (0.5 - v) / 0.5 * EQ_GAIN_MIN  # EQ_GAIN_MIN is negative

    def db_to_eq_value(self, db):
        """Inverse of eq_value_to_db: map dB to the 0..1 bar value."""
        if db >= 0:
            return 0.5 + 0.5 * min(db, EQ_GAIN_MAX) / EQ_GAIN_MAX
        return 0.5 - 0.5 * max(db, EQ_GAIN_MIN) / EQ_GAIN_MIN

    def _apply_eq_band(self, index):
        if self.equalizer is None:
            return
        try:
            db = self.eq_value_to_db(self.eq_values[index]) if self.eq_enabled else 0.0
            self.equalizer.set_property(f"band{index}", db)
        except Exception as e:
            self.log_debug(f"EQ band {index} failed: {e}")

    def _apply_preamp(self):
        """Master gain ahead of the bands: 0.5 = unity, edges = ±PREAMP_DB_RANGE."""
        if self.preamp is None:
            return
        if self.eq_enabled:
            db = (self.preamp_value - 0.5) * 2 * PREAMP_DB_RANGE
            self.preamp.set_property("volume", 10 ** (db / 20))
        else:
            self.preamp.set_property("volume", 1.0)

    def apply_all_eq(self):
        for i in range(len(self.eq_values)):
            self._apply_eq_band(i)
        self._apply_preamp()

    def toggle_eq_enabled(self, *_args):
        """Winamp-style EQ ON/OFF: bypass bands + preamp by zeroing the live
        element properties — no pipeline rebuild, stored curve untouched."""
        self.eq_enabled = not self.eq_enabled
        self.apply_all_eq()
        for bar in self.eq_bars:
            bar.queue_draw()
        if getattr(self, 'preamp_bar', None) is not None:
            self.preamp_bar.queue_draw()
        if getattr(self, 'eq_on_btn', None) is not None:
            ctx = self.eq_on_btn.get_style_context()
            (ctx.add_class if self.eq_enabled else ctx.remove_class)('active')
        self.schedule_save_config()
        self.show_drop_feedback("EQ on" if self.eq_enabled else "EQ off (bypassed)")

    def on_balance_changed(self, scale):
        self.balance = scale.get_value()
        if self.panorama is not None:
            try:
                self.panorama.set_property("panorama", max(-1.0, min(1.0, self.balance)))
            except Exception as e:
                self.log_debug(f"balance failed: {e}")
        self.schedule_save_config()

    def _parse_magnitudes(self, structure):
        """Read the spectrum 'magnitude' field as a list of dB floats.

        PyGObject can't convert the GstValueList via get_value() (raises
        'unknown type GstValueList'), so fall back to parsing the structure text."""
        try:
            val = structure.get_value("magnitude")
            if isinstance(val, (list, tuple)):
                return [float(x) for x in val]
        except Exception:
            pass
        try:
            text = structure.to_string()
            m = _MAG_LIST_RE.search(text)
            if m:
                return [float(x) for x in m.group(1).split(",")]
            m = _MAG_ONE_RE.search(text)
            if m:
                return [float(m.group(1))]
        except Exception:
            pass
        return None

    def on_spectrum_message(self, structure):
        if not self._analyzer_visible():
            return
        mags = self._parse_magnitudes(structure)
        if not mags:
            return
        rate = self.audio_properties.get('sample_rate') or 0
        if self.spectrum:
            caps = self.spectrum.get_static_pad('sink').get_current_caps()
            if caps and caps.get_size():
                ok, negotiated = caps.get_structure(0).get_int('rate')
                if ok:
                    rate = negotiated
        self.analyzer_state.feed(mags, rate, time.monotonic())
        self._start_decay()

    def setup_bus(self):
        self._error_streak = 0
        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self.on_bus_error)
        bus.connect("message::eos", self.on_bus_eos)
        bus.connect("message::state-changed", self.on_bus_state_changed)
        bus.connect("message::tag", self.on_bus_tag)
        bus.connect("message::element", self.on_bus_element)
        # Gapless: commit the prerolled next track when its stream starts.
        # (Bus signal-watch handlers run on the GLib main loop.)
        bus.connect("message::stream-start", self.on_bus_stream_start)
        bus.connect("message::buffering", self.on_bus_buffering)
        self.player.connect("about-to-finish", self._on_about_to_finish)

    def on_bus_error(self, bus, message):
        # Errors raised while deliberately swapping output sinks are handled by
        # the toggle's own revert logic — don't skip tracks over them.
        if getattr(self, '_switching_output', False):
            err, _dbg = message.parse_error()
            self.log_debug(f"suppressed during output switch: {err}")
            return
        # ALSA-direct is enabled but the DAC was busy (e.g. another app had it
        # at startup): re-run the acquire/retry/fallback flow instead of
        # treating it as a broken track.
        if self.alsa_output and self._alsa_recovers < 2:
            self._alsa_recovers += 1
            err, _dbg = message.parse_error()
            self.log_debug(f"ALSA error, attempting recovery: {err}")
            self._alsa_ctx = {
                'was_playing': self.is_playing,
                'resume_ns': self._current_position_ns(),
                'tries': 0,
            }
            self._switching_output = True
            self.player.set_state(Gst.State.NULL)
            self.show_drop_feedback("DAC busy — waiting for release…")
            self.tasks.timeout_add(1500, self._alsa_try_start)
            return
        err, dbg = message.parse_error()
        self.log_debug(f"GStreamer error: {err} ({dbg})")
        was_playing = self.is_playing
        self.order.failed.add(self.order.current)
        self._invalidate_next()
        self.player.set_state(Gst.State.NULL)
        self._sync_play_ui(False, stopped=True)
        self.info_label.set_text(f"⚠ {err.message}")
        # Skip past a bad track if we were playing, guarding against an all-bad loop.
        self._error_streak += 1
        if was_playing and self.playlist and self._error_streak < len(self.playlist):
            # after_error: never let REPEAT_ONE replay a broken track in a loop
            self.tasks.idle_add(lambda: (self.advance_track(auto=True, after_error=True), False)[1])

    def on_bus_eos(self, bus, message):
        # Only fires when the gapless handoff declined (repeat-one, sleep,
        # gapless off, end of playlist) — advance_track handles those cases.
        self.advance_track(auto=True)

    def _on_about_to_finish(self, playbin):
        # Streaming thread: only a protected, precomputed decision is accessed.
        with self._gapless_lock:
            snapshot = self._next_snapshot
            if snapshot is None or self._gapless_next is not None:
                return
            self._gapless_next = snapshot
            self._next_snapshot = None
            playbin.set_property('uri', snapshot[2])

    def on_bus_buffering(self, bus, message):
        """Network-stream buffering: pause below 100% and resume at 100%,
        without flipping the user-facing play state. Live pipelines
        (NO_PREROLL) must not be paused for buffering."""
        if not (self.current_song and self._is_stream_url(self.current_song)):
            return
        percent = message.parse_buffering()
        if percent < 100:
            if not getattr(self, '_buffering', False) and not getattr(self, '_pipeline_live', False):
                self._buffering = True
                self.player.set_state(Gst.State.PAUSED)
            self.info_label.set_text(f"Buffering {percent}%")
        else:
            if getattr(self, '_buffering', False):
                self._buffering = False
                if self.is_playing:
                    self.player.set_state(Gst.State.PLAYING)
            self.update_audio_display()

    def on_bus_stream_start(self, bus, message):
        """Gapless handoff commit: the prerolled track is now the live stream."""
        if self._awaiting_own_start:
            # The bus was flushed on load, so the first start is the loaded
            # track's own. A short file can arm the handoff before this start is
            # dispatched, and a duplicate next entry has the same URI.
            self._awaiting_own_start = False
            return
        if self._gapless_next is None:
            return  # ordinary load_song start
        key, path, uri, generation = self._gapless_next
        if self.player.get_property('current-uri') != uri:
            return  # an earlier stream-start was still queued on the main loop
        self._gapless_next = None
        if generation != self._next_generation or key not in self.entry_ids:
            return
        idx = self.entry_ids.index(key)
        self._load_gen += 1
        self.order.commit(key)
        self.current_index = idx
        self.current_song = path
        self._loaded_uri = uri          # re-arms the stale-tag guard
        self._pending_seek_ns = None
        self._stream_meta = {}
        self.position_scale.set_sensitive(not self._is_stream_url(path))
        # Per-track UI reset (mirrors load_song; no state change happens in a
        # gapless handoff, so on_bus_state_changed never re-caches these)
        self.duration = 0
        self.position = 0
        self.position_scale.set_value(0)
        self.time_display.set_text("00:00")
        self._last_pos_sec = None
        self._last_progress = -1
        ok, dur = self.player.query_duration(Gst.Format.TIME)
        if ok and dur > 0:
            self.duration = dur
            self._note_duration(path, dur // Gst.SECOND)
        else:
            # Sidecar fallback now; some demuxers need a beat — retry once
            secs = self._duration_seconds(path)
            if secs:
                self.duration = secs * Gst.SECOND
            generation = self._load_gen
            def requery():
                if self._destroyed or generation != self._load_gen:
                    return False
                ok2, d2 = self.player.query_duration(Gst.Format.TIME)
                if ok2 and d2 > 0:
                    self.duration = d2
                    self._note_duration(path, d2 // Gst.SECOND)
                return False
            self.tasks.timeout_add(200, requery)
        self._read_caps_props()         # caps may renegotiate (rate change)
        self._post_load_ui(idx, path)

    def on_bus_state_changed(self, bus, message):
        if message.src is not self.player:
            return
        _old, new, _pending = message.parse_state_changed()
        if new == Gst.State.PLAYING:
            self._error_streak = 0
            self._read_caps_props()
            # Cache duration once per track instead of re-querying every UI tick
            ok, dur = self.player.query_duration(Gst.Format.TIME)
            if ok and dur > 0:
                self.duration = dur
                # Also feeds the playlist duration column (covers files the
                # background Discoverer chokes on)
                self._note_duration(self.current_song, dur // Gst.SECOND)
            if self._pending_seek_ns is not None:
                self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, self._pending_seek_ns)
                self._pending_seek_ns = None

    def on_bus_tag(self, bus, message):
        # Ignore tag messages queued before a track switch: playbin's current-uri
        # is authoritative for which stream posted them.
        if self.player.get_property("current-uri") != self._loaded_uri:
            return
        taglist = message.parse_tag()
        title = self._tag_str(taglist, Gst.TAG_TITLE)
        artist = self._tag_str(taglist, Gst.TAG_ARTIST)
        # Tags arriving while a gapless next track prerolls belong to THAT
        # track (current-uri still reports the old one until stream-start).
        # Applying them here flips the title/bitrate early and caches the next
        # track's cover under the current track's path — the title/art
        # mismatch. Cache them for the pending track and leave the display
        # alone; the stream-start handoff applies them via _post_load_ui.
        pending = self._gapless_next
        if pending is not None:
            path = pending[1]
            if title:
                cached = self._cache_get(self._meta_cache, path)
                if isinstance(cached, dict):
                    merged = dict(cached)
                    merged['title'] = title
                    if artist:
                        merged['artist'] = artist
                    self._cache_put(self._meta_cache, path, merged)
            if self._cache_get(self._art_cache, path) is None:
                ok, sample = taglist.get_sample(Gst.TAG_IMAGE)
                if not ok:
                    ok, sample = taglist.get_sample(Gst.TAG_PREVIEW_IMAGE)
                if ok and sample:
                    data = self._sample_to_bytes(sample)
                    if data:
                        pixbuf = self._decode_art_pixbuf(data)
                        if pixbuf:
                            self._cache_put(self._art_cache, path, pixbuf)
            return
        ok, br = taglist.get_uint(Gst.TAG_BITRATE)
        if not ok:
            ok, br = taglist.get_uint(Gst.TAG_NOMINAL_BITRATE)
        if ok and br > 0:
            self.audio_properties['bitrate'] = br // 1000
        if title:
            self._set_title_text(f"{artist} - {title}" if artist else title)
        # ICY now-playing: souphttpsrc posts stream metadata as ordinary tags
        if self.current_song and self._is_stream_url(self.current_song):
            station = self._tag_str(taglist, Gst.TAG_ORGANIZATION)
            meta = getattr(self, '_stream_meta', {})
            changed = title and title != meta.get('title')
            if title:
                meta['title'] = title
            if artist or station:
                meta['artist'] = artist or station
            self._stream_meta = meta
            if changed:
                self._mpris_notify_track()
                self._notify_track(title, meta.get('artist') or '')
        if self.current_song and self._cache_get(self._art_cache, self.current_song) is None:
            ok, sample = taglist.get_sample(Gst.TAG_IMAGE)
            if not ok:
                ok, sample = taglist.get_sample(Gst.TAG_PREVIEW_IMAGE)
            if ok and sample:
                data = self._sample_to_bytes(sample)
                if data:
                    self._apply_art_bytes(self.current_song, data)
        self.update_audio_display()

    def _tag_str(self, taglist, tag):
        ok, val = taglist.get_string(tag)
        return val if ok and val else None

    def on_bus_element(self, bus, message):
        s = message.get_structure()
        if s and s.get_name() == "spectrum":
            self.on_spectrum_message(s)

    def _read_caps_props(self):
        """Pull real sample rate / channels from the negotiated audio pad."""
        try:
            pad = self.player.emit("get-audio-pad", 0)
            if not pad:
                return
            caps = pad.get_current_caps()
            if not caps or caps.get_size() == 0:
                return
            s = caps.get_structure(0)
            ok, rate = s.get_int("rate")
            if ok:
                self.audio_properties['sample_rate'] = rate
            ok, ch = s.get_int("channels")
            if ok:
                self.audio_properties['channels'] = ch
            self.update_audio_display()
        except Exception as e:
            self.log_debug(f"caps read failed: {e}")

    def _sync_play_ui(self, playing, *, stopped=False):
        self.playback_state = 'Playing' if playing else 'Stopped' if stopped else 'Paused'
        self.is_playing = bool(playing)
        self.play_btn.queue_draw()
        self.shade_controls.queue_draw()
        context = self.play_btn.get_style_context()
        (context.add_class if playing else context.remove_class)('active')
        self.play_btn.set_tooltip_text('Pause (Space)' if playing else 'Play (Space)')
        self._update_queue_markers()
        self._start_decay()
        if getattr(self, '_tray_play_item', None) is not None:
            self._tray_play_item.set_label('Pause' if playing else 'Play')
        self._mpris_notify_playback()

    def _mpris_notify_playback(self):
        self._mpris_emit({'PlaybackStatus': GLib.Variant('s', self._mpris_status())})

    def on_stop_button_press(self, widget, event):
        """Right-click on ■ toggles stop-after-current-track (classic Winamp)."""
        if event.button != 3:
            return False  # left-click: normal pressed-visual + clicked handlers
        self._set_sleep(None if self._sleep_after_track else 'track')
        return True

    def _update_stop_btn_cue(self):
        """Latch the stop button while stop-after-current is armed."""
        if getattr(self, 'stop_btn', None) is None:
            return
        ctx = self.stop_btn.get_style_context()
        (ctx.add_class if self._sleep_after_track else ctx.remove_class)('active')

    def _set_sleep(self, mode):
        self._invalidate_next()
        """Arm/disarm the sleep timer. mode: None (off), 'track', or minutes."""
        if self._sleep_timer_id is not None:
            self.tasks.source_remove(self._sleep_timer_id)
            self._sleep_timer_id = None
        self._sleep_after_track = False
        self._sleep_deadline = None
        self._sleep_mode = mode
        if mode == 'track':
            self._sleep_after_track = True
            self.show_drop_feedback("Sleeping after this track")
        elif isinstance(mode, int) and mode > 0:
            self._sleep_timer_id = self.tasks.timeout_add_seconds(mode * 60, self._sleep_fire)
            self._sleep_deadline = GLib.get_monotonic_time() + mode * 60 * 1_000_000
            self.show_drop_feedback(f"Sleeping in {mode} min")
        else:
            self.show_drop_feedback("Sleep timer off")
        self._update_stop_btn_cue()
        self._prepare_next()

    def _sleep_fire(self):
        self._sleep_timer_id = None
        self._sleep_deadline = None
        self._sleep_mode = None
        if self.is_playing:
            self.player.set_state(Gst.State.PAUSED)
            self._sync_play_ui(False)
        self.show_drop_feedback("Sleep timer — paused")
        return False

    def advance_track(self, auto=False, after_error=False):
        """Move to the next track honoring shuffle + tri-state repeat, skipping
        missing files. auto=True means triggered by end-of-song (so REPEAT_ONE
        replays); after_error=True suppresses the replay so a broken track
        can't loop forever."""
        if not self.playlist:
            return
        if auto and self._sleep_after_track:
            # Sleep timer: stop at the end of this track (beats repeat-one)
            self._sleep_after_track = False
            self._sleep_mode = None
            self._update_stop_btn_cue()
            self.stop_song(None)
            self.show_drop_feedback("Sleep timer — stopped after track")
            return
        if auto and self.repeat_mode == REPEAT_ONE and not after_error:
            self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
            self.player.set_state(Gst.State.PLAYING)
            return

        self._navigate(auto=auto, after_error=after_error)

    def _data_dir(self):
        """Where config.json/playlist.txt live. Portable mode: next to the
        script when its directory is writable (the classic ~/Apps setup).
        Installed mode (/usr is read-only): ~/.config/llamaamp/."""
        override = os.environ.get('LLAMAAMP_DATA_DIR')
        if override:
            os.makedirs(override, exist_ok=True)
            return override
        app_dir = os.path.dirname(os.path.abspath(__file__))
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

    def _queue_playlist_metadata(self):
        """Probe titles even when an earlier session already cached durations."""
        for path in self.playlist:
            if (path not in self._playlist_titles and path not in self._probe_inflight
                    and not self._is_stream_url(path) and self._playable(path)):
                self._probe_inflight.add(path)
                self._probe_queue.put(('meta', path, {'sample_rate': 0, 'bitrate': 0, 'channels': 0}))

    @contextmanager
    def _store_guard(self):
        """Suppress store-changed resyncs during programmatic edits, exception-safe."""
        self._suppress_store = True
        try:
            yield
        finally:
            self._suppress_store = False

    def _atomic_write(self, path, data, binary=False):
        SettingsStore.write(path, data, binary)

    @staticmethod
    def _cache_get(cache, key):
        """LRU read: refresh recency on hit, None on miss."""
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        return None

    @staticmethod
    def _cache_put(cache, key, val, limit=META_CACHE_LIMIT):
        """LRU write: insert and evict oldest entries beyond limit."""
        cache[key] = val
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)

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

    def _current_position_ns(self):
        try:
            ok, pos = self.player.query_position(Gst.Format.TIME)
            if ok and pos > 0:
                return int(pos)
        except Exception:
            pass
        return int(getattr(self, "position", 0) or 0)

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

    def on_configure_event(self, widget, event):
        self._win_pos = self.get_position()
        if hasattr(self, 'panels'):
            self.panels.configured('main')
        if getattr(self, '_layout_restored', False) and not self._windowshade and not getattr(self, '_layout_switching', False):
            self._expanded_size = [event.width, event.height]
        return False

    def on_window_state_event(self, widget, event):
        self._iconified = bool(event.new_window_state & Gdk.WindowState.ICONIFIED)
        self._update_analyzer_visibility()
        if self._iconified:
            self._marquee_stop()
        else:
            self._set_title_text(getattr(self, '_title_full', '') or DEFAULT_SONG_TEXT)
        return False

    def _seek_relative(self, seconds):
        """Seek by +/- seconds from the current position, clamped to the track."""
        ok, pos = self.player.query_position(Gst.Format.TIME)
        if not ok:
            return
        ok, dur = self.player.query_duration(Gst.Format.TIME)
        if not ok or dur <= 0:
            dur = self.duration
        target = pos + int(seconds * Gst.SECOND)
        target = max(0, min(target, dur if dur > 0 else target))
        self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, target)
        self._mpris_notify_seeked(target)

    def _mpris_notify_seeked(self, position_ns):
        conn = getattr(self, '_mpris_conn', None)
        if not conn:
            return
        try:
            conn.emit_signal(None, MPRIS_OBJECT_PATH,
                             'org.mpris.MediaPlayer2.Player', 'Seeked',
                             GLib.Variant('(x)', (position_ns // 1000,)))
        except Exception:
            pass

    def _step_volume(self, delta):
        self.volume_scale.set_value(max(0.0, min(1.0, self.volume + delta)))

    def on_window_key_press(self, widget, event):
        """Global shortcuts: Space play/pause, arrows seek/volume, S shuffle,
        R repeat, Ctrl+O add files. Delete/Backspace propagate to the playlist."""
        # Typing in an entry (playlist search, dialogs) must never trigger
        # shortcuts — let the widget consume every key.
        focus = widget.get_focus() if isinstance(widget, Gtk.Window) else self.get_focus()
        if isinstance(focus, Gtk.Entry):
            return False
        key = event.keyval
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if ctrl and key in (Gdk.KEY_z, Gdk.KEY_Z):
            self.undo_playlist()
            return True
        if isinstance(focus, (Gtk.Range, Gtk.TreeView)) and key in (
                Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down):
            return False
        if key in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
            return False  # handled by on_playlist_key_press
        if ctrl and key in (Gdk.KEY_o, Gdk.KEY_O):
            self.add_files(None)
            return True
        if ctrl and key in (Gdk.KEY_l, Gdk.KEY_L):
            self.open_url_dialog()
            return True
        if key == Gdk.KEY_space:
            self.toggle_play_pause(None)
            return True
        if key == Gdk.KEY_Left:
            self._seek_relative(-SEEK_STEP_SECONDS)
            return True
        if key == Gdk.KEY_Right:
            self._seek_relative(SEEK_STEP_SECONDS)
            return True
        if key == Gdk.KEY_Up:
            self._step_volume(VOLUME_STEP)
            return True
        if key == Gdk.KEY_Down:
            self._step_volume(-VOLUME_STEP)
            return True
        if key in (Gdk.KEY_s, Gdk.KEY_S) and not ctrl:
            self.toggle_shuffle(None)
            return True
        if key in (Gdk.KEY_r, Gdk.KEY_R) and not ctrl:
            self.toggle_repeat(None)
            return True
        if key in (Gdk.KEY_j, Gdk.KEY_J) and not ctrl:
            self.show_jump_dialog()
            return True
        return False

    def _on_unix_signal(self):
        self.destroy()
        return False

    def on_destroy(self, *args):
        if self._destroyed:
            return
        self._destroyed = True
        self.metadata_worker.close()
        self._invalidate_next()
        if self._analyzer_id is not None:
            self.tasks.source_remove(self._analyzer_id)
            self._analyzer_id = None
        if self._save_timeout_id is not None:
            self.tasks.source_remove(self._save_timeout_id)
            self._save_timeout_id = None
        self._write_config()
        self.save_playlist()
        if self._durations_save_id is not None:
            self.tasks.source_remove(self._durations_save_id)
        self._save_durations()
        if self._sleep_timer_id is not None:
            self.tasks.source_remove(self._sleep_timer_id)
            self._sleep_timer_id = None
        self._marquee_stop()
        self._hide_tray()
        self._mpris_teardown()
        self.panels.close()
        if self._drop_feedback_id is not None:
            self.tasks.source_remove(self._drop_feedback_id)
            self._drop_feedback_id = None
        for tid in self._timeout_ids:
            try:
                self.tasks.source_remove(tid)
            except Exception:
                pass
        try:
            self.player.set_state(Gst.State.NULL)
        except Exception:
            pass
        self.player.get_bus().remove_signal_watch()
        self.tasks.close()
        if Gtk.main_level():
            Gtk.main_quit()

    # ==================== System tray ====================

    def _init_tray(self):
        """AppIndicator (Ayatana preferred) with Gtk.StatusIcon fallback.
        Degrades to no tray if neither backend exists."""
        self._tray = None
        self._tray_play_item = None
        if self.config.get("tray_icon") is False:
            return
        app_dir = os.path.dirname(os.path.abspath(__file__))
        svg = os.path.join(app_dir, "llama-amp.svg")
        AI = None
        for module, version in (('AyatanaAppIndicator3', '0.1'), ('AppIndicator3', '0.1')):
            try:
                gi.require_version(module, version)
                AI = getattr(__import__('gi.repository', fromlist=[module]), module)
                break
            except Exception:
                continue
        try:
            if AI is not None:
                ind = AI.Indicator.new('llamaamp', 'llama-amp',
                                       AI.IndicatorCategory.APPLICATION_STATUS)
                if os.path.exists(svg):
                    ind.set_icon_theme_path(app_dir)
                    ind.set_icon_full('llama-amp', APP_NAME)
                ind.set_status(AI.IndicatorStatus.ACTIVE)
                ind.set_menu(self._build_tray_menu())
                self._tray = ind
                self._tray_ai = AI
                self._tray_backend = 'appindicator'
            else:
                icon = (Gtk.StatusIcon.new_from_file(svg) if os.path.exists(svg)
                        else Gtk.StatusIcon.new_from_icon_name('audio-x-generic'))
                icon.set_tooltip_text(APP_NAME)
                icon.connect('activate', lambda *_: self._toggle_window_visible())
                icon.connect('popup-menu', self._on_statusicon_popup)
                self._tray = icon
                self._tray_backend = 'statusicon'
        except Exception as e:
            self.log_debug(f"tray unavailable: {e}")
            self._tray = None

    def _build_tray_menu(self):
        menu = Gtk.Menu()
        show_item = Gtk.MenuItem(label="Show/Hide")
        show_item.connect("activate", lambda *_: self._toggle_window_visible())
        menu.append(show_item)
        menu.append(Gtk.SeparatorMenuItem())
        self._tray_play_item = Gtk.MenuItem(label="Play")
        self._tray_play_item.connect("activate", lambda *_: self.toggle_play_pause(None))
        menu.append(self._tray_play_item)
        next_item = Gtk.MenuItem(label="Next")
        next_item.connect("activate", lambda *_: self.next_song(None))
        menu.append(next_item)
        prev_item = Gtk.MenuItem(label="Previous")
        prev_item.connect("activate", lambda *_: self.previous_song(None))
        menu.append(prev_item)
        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda *_: self.destroy())
        menu.append(quit_item)
        menu.show_all()
        self._tray_menu = menu
        return menu

    def _on_statusicon_popup(self, icon, button, time):
        self._build_tray_menu().popup(None, None, None, None, button, time)

    def _toggle_window_visible(self):
        if self.get_visible():
            self.hide()
        else:
            self.present()

    def toggle_gapless(self, *_args):
        """Toggle gapless handoff. Note: a next-uri already prerolled before
        turning it off still plays gaplessly once — acceptable."""
        self._invalidate_next()
        self.gapless = not self.gapless
        self.schedule_save_config()
        self.show_drop_feedback(
            "Gapless playback on" if self.gapless else "Gapless playback off")

    def toggle_tray_icon(self, *_args):
        """Live enable/disable of the tray icon (persisted)."""
        if self._tray is not None:
            self._hide_tray()
            self.config['tray_icon'] = False
        else:
            self.config['tray_icon'] = True
            self._init_tray()
        self.schedule_save_config()

    def _hide_tray(self):
        if self._tray is None:
            return
        try:
            if getattr(self, '_tray_backend', '') == 'appindicator':
                # PASSIVE removes it from the panel
                self._tray.set_status(self._tray_ai.IndicatorStatus.PASSIVE)
            else:
                self._tray.set_visible(False)
        except Exception:
            pass
        self._tray = None
        self._tray_play_item = None

    # ==================== ListenBrainz scrobbling ====================

    def _scrobble_reset(self):
        """New listen for the current track (called from _post_load_ui)."""
        self._listen = {
            'path': self.current_song,
            'start_ts': int(time.time()),
            'accum': 0.0,
            'last_wall': time.monotonic(),
            'now_sent': False,
            'submitted': False,
        }

    def _scrobble_enabled(self):
        return (self.config.get('scrobble_enabled') is True
                and bool(self.config.get('listenbrainz_token')))

    def _scrobble_tick(self):
        """Accumulate listened time (seek-proof: wall-clock while playing) and
        submit per the standard 50%-or-4-minutes rule. Runs on the 100ms
        position heartbeat; cheap early-outs keep it free."""
        listen = getattr(self, '_listen', None)
        if listen is None or listen['submitted'] or not self._scrobble_enabled():
            return
        if listen['path'] != self.current_song or not self.current_song:
            return
        if self._is_stream_url(listen['path']):
            return  # radio is not a listen
        now = time.monotonic()
        if self.is_playing and not self.seeking:
            listen['accum'] += now - listen['last_wall']
        listen['last_wall'] = now

        cached = self._cache_get(self._meta_cache, listen['path'])
        artist = cached.get('artist') if isinstance(cached, dict) else None
        title = cached.get('title') if isinstance(cached, dict) else None
        if not artist or not title:
            return  # conservatively skip artist-less files (usually mistagged)

        if not listen['now_sent']:
            listen['now_sent'] = True
            self._scrobble_send('playing_now', artist, title, None)
        duration_s = self.duration // Gst.SECOND if self.duration > 0 else 0
        if duration_s > 0 and listen['accum'] >= min(240, duration_s / 2):
            listen['submitted'] = True
            self._scrobble_send('single', artist, title, listen['start_ts'])

    def _scrobble_send(self, listen_type, artist, title, listened_at):
        """POST to ListenBrainz on a daemon thread; failures are silent
        (log_debug only, no retry queue — a dropped listen is acceptable)."""
        token = self.config.get('listenbrainz_token')
        payload = {'listen_type': listen_type, 'payload': [{
            'track_metadata': {'artist_name': artist, 'track_name': title}}]}
        if listened_at is not None:
            payload['payload'][0]['listened_at'] = listened_at

        def worker():
            try:
                req = urllib.request.Request(
                    f"{LISTENBRAINZ_API}/1/submit-listens",
                    data=json.dumps(payload).encode(),
                    headers={'Authorization': f'Token {token}',
                             'Content-Type': 'application/json'})
                urllib.request.urlopen(req, timeout=10).read()
            except Exception as e:
                self.log_debug(f"scrobble {listen_type} failed: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def show_scrobble_dialog(self, *_args):
        dialog = Gtk.Dialog(title="Scrobbling", transient_for=self, modal=True)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OK, Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_margin_top(10); box.set_margin_bottom(10)
        box.set_margin_start(10); box.set_margin_end(10)
        box.set_spacing(6)
        box.add(Gtk.Label(label="ListenBrainz user token\n(listenbrainz.org → Settings)"))
        entry = Gtk.Entry()
        entry.set_visibility(False)
        entry.set_width_chars(40)
        entry.set_text(self.config.get('listenbrainz_token') or "")
        box.add(entry)
        check = Gtk.CheckButton(label="Enable scrobbling")
        check.set_active(self.config.get('scrobble_enabled') is True)
        box.add(check)
        dialog.show_all()
        response = dialog.run()
        token = entry.get_text().strip()
        enabled = check.get_active()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        self.config['listenbrainz_token'] = token or None
        self.config['scrobble_enabled'] = bool(enabled and token)
        self.schedule_save_config()
        self.show_drop_feedback(
            "Scrobbling enabled" if self.config['scrobble_enabled']
            else "Scrobbling off")

    # ==================== Desktop notifications ====================

    def _notify_track(self, summary, body=""):
        """Track-change popup via org.freedesktop.Notifications (raw GDBus — the
        app has no Gtk.Application, so Gio.Notification isn't available).
        Suppressed while the window is focused; rate-limited; replaces the
        previous popup instead of stacking."""
        if self.config.get("notifications", True) is False:
            return
        conn = getattr(self, '_mpris_conn', None)
        if conn is None or self.is_active():
            return
        now = time.monotonic()
        if now - getattr(self, '_notify_last', 0.0) < NOTIFY_MIN_INTERVAL_S:
            return
        self._notify_last = now

        icon = ""
        art = self._mpris_art_url()
        if art and art.startswith('file://'):
            icon = art[7:]
        else:
            svg = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "llama-amp.svg")
            if os.path.exists(svg):
                icon = svg

        def done(c, res):
            try:
                self._notify_id = c.call_finish(res).unpack()[0]
            except Exception as e:
                self.log_debug(f"notify failed: {e}")

        try:
            conn.call('org.freedesktop.Notifications',
                      '/org/freedesktop/Notifications',
                      'org.freedesktop.Notifications', 'Notify',
                      GLib.Variant('(susssasa{sv}i)',
                                   (APP_NAME, getattr(self, '_notify_id', 0),
                                    icon, summary, body, [], {}, 4000)),
                      GLib.VariantType('(u)'),
                      Gio.DBusCallFlags.NONE, 2000, None, done)
        except Exception as e:
            self.log_debug(f"notify call failed: {e}")

    def toggle_notifications(self, *_args):
        self.config['notifications'] = self.config.get('notifications', True) is False
        self.schedule_save_config()
        self.show_drop_feedback(
            "Notifications on" if self.config['notifications'] else "Notifications off")

    # ==================== Update check ====================

    def _startup_update_check(self):
        self._check_updates(manual=False)
        return False  # one-shot timer

    def _periodic_update_check(self):
        if self.config.get("update_check", True) is not False:
            self._check_updates(manual=False)
        return True  # keep the daily timer alive

    def _check_updates(self, manual=False):
        """Ask GitHub for the latest release (worker thread; UI via idle_add)."""
        def worker():
            info, err = None, None
            try:
                req = urllib.request.Request(
                    UPDATE_API_URL,
                    headers={'User-Agent': f'llama-amp/{APP_VERSION}',
                             'Accept': 'application/vnd.github+json'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    rel = json.load(resp)
                deb = next((a.get('browser_download_url')
                            for a in rel.get('assets', [])
                            if (a.get('name') or '').endswith('.deb')), None)
                info = {'version': (rel.get('tag_name') or '').lstrip('v'),
                        'url': rel.get('html_url') or RELEASES_URL,
                        'deb_url': deb}
            except Exception as e:
                err = str(e)
            self.tasks.idle_add(self._check_updates_done, info, err, manual)
        threading.Thread(target=worker, daemon=True, name='update-check').start()

    @staticmethod
    def _version_tuple(v):
        return tuple(int(x) for x in re.findall(r'\d+', v or ''))

    def _check_updates_done(self, info, err, manual):
        """UI-thread result: remember a newer release and surface it."""
        newer = (info and info['version'] and
                 self._version_tuple(info['version']) > self._version_tuple(APP_VERSION))
        if newer:
            # The daily re-check shouldn't re-announce a version it already
            # surfaced — notify only the first time each version is seen.
            already_known = (self._update_info or {}).get('version') == info['version']
            self._update_info = info
            if manual:
                self._offer_update_dialog(info)
            elif not already_known:
                self.show_drop_feedback(f"v{info['version']} available — see ⚙ menu")
                self._notify_track(f"{APP_NAME} {info['version']} is available",
                                   "Update from the ⚙ menu")
        elif manual:
            dialog = Gtk.MessageDialog(
                transient_for=self, modal=True, message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text=("Could not check for updates" if err
                      else f"{APP_NAME} {APP_VERSION} is up to date"))
            if err:
                dialog.format_secondary_text(err)
            dialog.run()
            dialog.destroy()
        if err:
            self.log_debug(f"update check failed: {err}")
        return False

    def _offer_update_dialog(self, info):
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"{APP_NAME} {info['version']} is available (you have {APP_VERSION})")
        dialog.format_secondary_text("Download and install it now?")
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            self._start_update()

    def _start_update(self, *_args):
        """GUI update path. Installed (.deb) copies download the new package and
        hand it to the system installer (which prompts for the admin password);
        portable checkouts just get the release page."""
        info = self._update_info
        if not info:
            return
        installed = os.path.abspath(__file__).startswith('/usr/')
        if not installed or not info.get('deb_url'):
            try:
                Gtk.show_uri_on_window(self, info['url'], Gdk.CURRENT_TIME)
            except Exception as e:
                self.log_debug(f"open releases page failed: {e}")
            return
        dest_dir = (GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
                    or GLib.get_home_dir())
        dest = os.path.join(dest_dir, os.path.basename(info['deb_url']))
        self.show_drop_feedback(f"Downloading v{info['version']}…")

        def worker():
            err = None
            try:
                req = urllib.request.Request(
                    info['deb_url'],
                    headers={'User-Agent': f'llama-amp/{APP_VERSION}'})
                with urllib.request.urlopen(req, timeout=60) as r, \
                        open(dest + '.part', 'wb') as f:
                    while True:
                        chunk = r.read(1 << 16)
                        if not chunk:
                            break
                        f.write(chunk)
                os.replace(dest + '.part', dest)
            except Exception as e:
                err = str(e)
            self.tasks.idle_add(self._update_downloaded, dest, err)
        threading.Thread(target=worker, daemon=True, name='update-download').start()

    def _update_downloaded(self, dest, err):
        if err:
            self.log_debug(f"update download failed: {err}")
            self.show_drop_feedback("Download failed — opening releases page")
            try:
                Gtk.show_uri_on_window(self, self._update_info['url'],
                                       Gdk.CURRENT_TIME)
            except Exception:
                pass
            return False
        self.show_drop_feedback("Opening installer…")
        try:
            # Hands the .deb to the software installer; it prompts for the
            # admin password and replaces the running version in place.
            Gio.AppInfo.launch_default_for_uri(f'file://{dest}', None)
        except Exception as e:
            self.log_debug(f"launch installer failed: {e}")
            self.show_drop_feedback(f"Saved to {dest}")
        return False

    def _toggle_update_check(self, *_args):
        enabled = self.config.get('update_check', True) is False
        self.config['update_check'] = enabled
        self.schedule_save_config()
        self.show_drop_feedback(
            "Startup update check on" if enabled else "Startup update check off")

    # ==================== MPRIS2 (media keys / desktop integration) ====================

    def _mpris_setup(self):
        """Register org.mpris.MediaPlayer2.llamaamp on the session bus so media
        keys and desktop panel/lockscreen controls work. Degrades to a no-op if
        the bus is unavailable (headless runs)."""
        self._mpris_conn = None
        self._mpris_reg_ids = []
        self._mpris_owner_id = None
        try:
            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            node = Gio.DBusNodeInfo.new_for_xml(MPRIS_XML)
            for iface in node.interfaces:
                self._mpris_reg_ids.append(conn.register_object(
                    MPRIS_OBJECT_PATH, iface,
                    self._mpris_method_call, self._mpris_get_prop, self._mpris_set_prop))
            self._mpris_owner_id = Gio.bus_own_name_on_connection(
                conn, MPRIS_BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, None)
            self._mpris_conn = conn
        except Exception as e:
            self.log_debug(f"MPRIS unavailable: {e}")

    def _mpris_teardown(self):
        try:
            if getattr(self, '_mpris_owner_id', None) is not None:
                Gio.bus_unown_name(self._mpris_owner_id)
                self._mpris_owner_id = None
            conn = getattr(self, '_mpris_conn', None)
            if conn:
                for rid in self._mpris_reg_ids:
                    conn.unregister_object(rid)
                self._mpris_reg_ids = []
                self._mpris_conn = None
        except Exception:
            pass

    def _mpris_method_call(self, conn, sender, path, iface, method, params, invocation):
        try:
            if method == "Raise":
                self.present()
            elif method == "Quit":
                self.destroy()
            elif method == "Next":
                self.next_song(None)
            elif method == "Previous":
                self.previous_song(None)
            elif method == "PlayPause":
                self.toggle_play_pause(None)
            elif method == "Play":
                if not self.is_playing:
                    self.toggle_play_pause(None)
            elif method == "Pause":
                if self.is_playing:
                    self.toggle_play_pause(None)
            elif method == "Stop":
                self.stop_song(None)
            elif method == "Seek":
                self._seek_relative(params.unpack()[0] / 1_000_000)
            elif method == "SetPosition":
                trackid, pos_us = params.unpack()
                if trackid == self._mpris_trackid():
                    self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH,
                                            pos_us * 1000)
                    self._mpris_notify_seeked(pos_us * 1000)
            elif method == "OpenUri":
                files = self._uris_to_audio_paths([params.unpack()[0]])
                if files:
                    self._add_paths(files)
            invocation.return_value(None)
        except Exception as e:
            invocation.return_error_literal(
                Gio.dbus_error_quark(), Gio.DBusError.FAILED, str(e))

    def _mpris_status(self):
        return self.playback_state

    def _mpris_trackid(self):
        return f"{MPRIS_OBJECT_PATH}/llamaamp/track/{self.order.current or 'none'}"

    def _mpris_art_url(self):
        """File URL for the current track's art: embedded (cached pixbuf saved
        once to ~/.cache/llamaamp) or the cached folder art."""
        if not self.current_song:
            return None
        pixbuf = self._cache_get(self._art_cache, self.current_song)
        if pixbuf is None:
            directory = os.path.dirname(os.path.abspath(self.current_song))
            pixbuf = self._cache_get(self._folder_art_cache, directory)
        if not pixbuf:
            return None
        try:
            cache_dir = os.path.join(GLib.get_user_cache_dir(), 'llamaamp')
            os.makedirs(cache_dir, exist_ok=True)
            art_path = os.path.join(cache_dir, 'cover.png')
            if getattr(self, '_mpris_art_for', None) != self.current_song:
                pixbuf.savev(art_path, 'png', [], [])
                self._mpris_art_for = self.current_song
            return 'file://' + art_path
        except Exception:
            return None

    def _mpris_metadata(self):
        md = {'mpris:trackid': GLib.Variant('o', self._mpris_trackid())}
        if self.current_song:
            title = artist = None
            if self._is_stream_url(self.current_song):
                meta = getattr(self, '_stream_meta', {})
                title = meta.get('title')
                artist = meta.get('artist')
            else:
                cached = self._cache_get(self._meta_cache, self.current_song)
                if isinstance(cached, dict):
                    title = cached.get('title')
                    artist = cached.get('artist')
            if not title:
                title = self._display_name(self.current_song)
            md['xesam:title'] = GLib.Variant('s', title)
            if artist:
                md['xesam:artist'] = GLib.Variant('as', [artist])
            md['xesam:url'] = GLib.Variant(
                's', self.current_song if self._is_stream_url(self.current_song)
                else Gst.filename_to_uri(os.path.abspath(self.current_song)))
            if self.duration > 0:
                md['mpris:length'] = GLib.Variant('x', self.duration // 1000)
            art = self._mpris_art_url()
            if art:
                md['mpris:artUrl'] = GLib.Variant('s', art)
        return GLib.Variant('a{sv}', md)

    def _mpris_get_prop(self, conn, sender, path, iface, prop):
        if iface == "org.mpris.MediaPlayer2":
            return {
                "CanQuit": GLib.Variant('b', True),
                "CanRaise": GLib.Variant('b', True),
                "HasTrackList": GLib.Variant('b', False),
                "Identity": GLib.Variant('s', APP_NAME),
                "SupportedUriSchemes": GLib.Variant('as', ['file', 'http', 'https']),
                "SupportedMimeTypes": GLib.Variant('as', [
                    'audio/mpeg', 'audio/flac', 'audio/ogg', 'audio/x-wav', 'audio/mp4']),
            }.get(prop)
        if prop == "PlaybackStatus":
            return GLib.Variant('s', self._mpris_status())
        if prop == "LoopStatus":
            loop = {REPEAT_OFF: 'None', REPEAT_ALL: 'Playlist', REPEAT_ONE: 'Track'}
            return GLib.Variant('s', loop[self.repeat_mode])
        if prop in ("Rate", "MinimumRate", "MaximumRate"):
            return GLib.Variant('d', 1.0)
        if prop == "Shuffle":
            return GLib.Variant('b', self.shuffle != SHUFFLE_OFF)
        if prop == "Metadata":
            return self._mpris_metadata()
        if prop == "Volume":
            return GLib.Variant('d', float(self.volume))
        if prop == "Position":
            return GLib.Variant('x', self._current_position_ns() // 1000)
        if prop.startswith("Can"):
            return GLib.Variant('b', True)
        return None

    def _mpris_set_prop(self, conn, sender, path, iface, prop, value):
        if prop == "Volume":
            self.volume_scale.set_value(max(0.0, min(1.0, value.get_double())))
        elif prop == "Shuffle":
            want = SHUFFLE_TRACKS if value.get_boolean() else SHUFFLE_OFF
            if want != self.shuffle:
                self._invalidate_next()
                self.shuffle = want
                self.update_shuffle_button()
                self.schedule_save_config()
        elif prop == "LoopStatus":
            self._invalidate_next()
            loop = {'None': REPEAT_OFF, 'Playlist': REPEAT_ALL, 'Track': REPEAT_ONE}
            self.repeat_mode = loop.get(value.get_string(), REPEAT_OFF)
            self.update_repeat_button()
            self.schedule_save_config()
            self._mpris_emit({'LoopStatus': value})
        return True

    def _mpris_emit(self, props):
        conn = getattr(self, '_mpris_conn', None)
        if not conn:
            return
        try:
            conn.emit_signal(
                None, MPRIS_OBJECT_PATH, 'org.freedesktop.DBus.Properties',
                'PropertiesChanged',
                GLib.Variant('(sa{sv}as)', ('org.mpris.MediaPlayer2.Player', props, [])))
        except Exception as e:
            self.log_debug(f"mpris emit failed: {e}")

    def update_shuffle_button(self):
        ctx = self.shuffle_btn.get_style_context()
        if self.shuffle == SHUFFLE_ALBUMS:
            self.shuffle_btn.set_label("⇄ ALBUMS")
            self.shuffle_btn.set_tooltip_text("Shuffle: ALBUMS")
            ctx.add_class('active')
        elif self.shuffle == SHUFFLE_TRACKS:
            self.shuffle_btn.set_label("⇄ SHUFFLE")
            self.shuffle_btn.set_tooltip_text("Shuffle: TRACKS")
            ctx.add_class('active')
        else:
            self.shuffle_btn.set_label("⇄ SHUFFLE")
            self.shuffle_btn.set_tooltip_text("Shuffle: OFF")
            ctx.remove_class('active')

    def update_repeat_button(self):
        ctx = self.repeat_btn.get_style_context()
        if self.repeat_mode == REPEAT_ALL:
            self.repeat_btn.set_label("↻ REPEAT")
            self.repeat_btn.set_tooltip_text("Repeat: ALL")
            ctx.add_class('active')
        elif self.repeat_mode == REPEAT_ONE:
            self.repeat_btn.set_label("↻ REPEAT 1")
            self.repeat_btn.set_tooltip_text("Repeat: ONE")
            ctx.add_class('active')
        else:
            self.repeat_btn.set_label("↻ REPEAT")
            self.repeat_btn.set_tooltip_text("Repeat: OFF")
            ctx.remove_class('active')

    def setup_drag_and_drop(self):
        """Set up drag and drop functionality for adding music files"""
        self.log_debug("Setting up drag and drop...")
        
        # Define what we can accept
        target_entries = [
            Gtk.TargetEntry.new("text/uri-list", 0, 0),
            Gtk.TargetEntry.new("application/x-kde4-urilist", 0, 1)
        ]
        
        self.log_debug(f"Target entries: {[entry.target for entry in target_entries]}")
        
        # Set this window as a drag destination
        self.drag_dest_set(
            Gtk.DestDefaults.ALL,
            target_entries,
            Gdk.DragAction.COPY
        )
        
        self.log_debug("Window set as drag destination")
        
        # Connect the drag and drop signals
        self.connect("drag-data-received", self.on_drag_data_received)
        self.connect("drag-motion", self.on_drag_motion)
        self.connect("drag-leave", self.on_drag_leave)
        
        self.log_debug("Drag and drop signals connected")
        
    def _uris_to_audio_paths(self, uris):
        """Convert dropped URIs to playable entries: local audio files, or
        http(s) stream URLs passed through verbatim."""
        files = []
        for uri in uris:
            uri = uri.strip()
            if not uri:
                continue
            if self._is_stream_url(uri):
                files.append(uri)
                continue
            try:
                path = GLib.filename_from_uri(uri)[0]
            except Exception:
                continue
            if self.is_audio_file(path) and os.path.exists(path):
                files.append(path)
        return files

    def _add_paths(self, paths, feedback=None):
        """Append paths to playlist + store (missing entries marked), persist,
        and auto-load the first added track if nothing is loaded. Returns count."""
        if paths:
            self._remember_playlist()
        added = 0
        first_new = len(self.playlist)
        with self._store_guard():
            for p in paths:
                self.playlist.append(p)
                self.entry_ids.append(uuid.uuid4().hex)
                name = self._display_name(p)
                display = (name if self._is_stream_url(p) or os.path.exists(p)
                           else f"❌ {name} [MISSING]")
                self.playlist_store.append([p, display, len(self.playlist), self._duration_str(p), self.entry_ids[-1], ""])
                added += 1
        if not added:
            return 0
        self.update_playlist_info()
        self.save_playlist()
        self._queue_playlist_metadata()
        self._refresh_order()
        if self.current_song is None:
            self.load_song(first_new)
        if feedback:
            self.show_drop_feedback(feedback)
        return added

    def on_drag_data_received(self, widget, drag_context, x, y, data, info, time):
        """Handle files dropped on the window. Registered with DestDefaults.ALL,
        so GTK finishes the drag itself — no manual drag_finish here (a second
        call would be a double-finish)."""
        self.get_style_context().remove_class('drag-over')
        try:
            uris = list(data.get_uris() or [])
            if not uris:
                text_data = data.get_text()
                if text_data:
                    uris = text_data.strip().split('\n')
            files = self._uris_to_audio_paths(uris)
            if files:
                self._add_paths(files, feedback=f"Added {len(files)} file(s)")
        except Exception as e:
            self.log_debug(f"Error handling dropped files: {e}")
    
    def on_view_drag_data_received(self, widget, drag_context, x, y, data, info, time):
        """File drops landing on the playlist TreeView. Row reorders (the
        built-in TREE_MODEL_ROW target) pass through to the default handler."""
        if info != URI_TARGET_INFO:
            return  # let the TreeView's own reorder machinery run
        files = self._uris_to_audio_paths(data.get_uris() or [])
        added = self._add_paths(files, feedback=f"Added {len(files)} file(s)" if files else None)
        drag_context.finish(bool(added), False, time)
        widget.stop_emission_by_name("drag-data-received")

    def on_drag_motion(self, widget, drag_context, x, y, time):
        """Handle drag motion over the window"""
        # Show visual feedback that we can accept the drop
        self.get_style_context().add_class('drag-over')
        Gdk.drag_status(drag_context, Gdk.DragAction.COPY, time)
        return True
    
    def on_drag_leave(self, widget, drag_context, time):
        """Handle when drag leaves the window"""
        self.get_style_context().remove_class('drag-over')
    
    @staticmethod
    def _is_stream_url(path):
        """True for internet-radio / remote stream playlist entries."""
        return path.startswith(('http://', 'https://'))

    def _display_name(self, path):
        """Human name for a playlist entry: hostname for streams, stem for files."""
        if self._is_stream_url(path):
            return urllib.parse.urlparse(path).hostname or path
        title = self._playlist_titles.get(path)
        if not title:
            cached = self._meta_cache.get(path)
            title = cached.get('title') if isinstance(cached, dict) else None
        return title if title else os.path.splitext(os.path.basename(path))[0]

    def _playable(self, path):
        """Can this playlist entry be played right now?"""
        return self._is_stream_url(path) or (
            self.is_audio_file(path) and os.path.exists(path))

    def is_audio_file(self, file_path):
        """Check if a file is an audio file based on its extension"""
        audio_extensions = {
            '.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.wma', 
            '.mp4', '.m4p', '.opus', '.webm', '.3gp', '.amr'
        }
        _, ext = os.path.splitext(file_path.lower())
        return ext in audio_extensions
    
    def show_drop_feedback(self, message):
        if self._destroyed:
            return
        self.feedback_label.set_text(message)
        self.feedback_label.set_tooltip_text(message)
        if self._drop_feedback_id is not None:
            self.tasks.source_remove(self._drop_feedback_id)
        def clear():
            self._drop_feedback_id = None
            if not self._destroyed:
                self.feedback_label.set_text('')
            return False
        self._drop_feedback_id = self.tasks.timeout_add_seconds(5, clear)

    def setup_styling(self):
        self.theme = THEMES[self.config.get('theme', 'green')]
        t = self.theme
        css = """
        window.llama-window { background-color: transparent; }
        .music-player-main { background: CHASSIS; color: TEXT; border: 1px solid CONTROL;
                             border-radius: 5px; font: 12px "DejaVu Sans"; }
        .music-player-titlebar { background: linear-gradient(to bottom, CONTROL, PANEL);
            color: TEXT; padding: 3px 5px; border-bottom: 1px solid #080a08; }
        .brand { font: bold 11px "Orbitron", sans-serif; letter-spacing: 1px; color: ACCENT; }
        .music-player-frame { background: PANEL; border: 1px solid CONTROL;
            border-radius: 3px; margin: 2px 5px; padding: 5px; }
        frame.music-player-frame > border, frame.music-player-display > border { border: none; }
        .version-badge { font: 10px "Liberation Mono", monospace; color: TEXT;
            background: LCD; border: 1px solid CONTROL; border-top-color: #080b08;
            border-radius: 3px; padding: 2px 5px; opacity: .8; }
        .song-title { font-size: 14px; font-weight: bold; }
        .music-player-display { background: LCD; color: ACCENT; padding: 7px;
            border: 1px solid #050705; border-bottom-color: CONTROL;
            border-radius: 2px; font: bold 12px "Liberation Mono"; }
        .music-player-time { font: 30px "DSEG7 Classic", monospace; color: ACCENT;
            background: LCD; padding: 4px; }
        .music-player-label { font: 10px "DejaVu Sans"; color: TEXT; }
        .music-player-art { border: 1px solid CONTROL; }
        .music-player-main button { background: linear-gradient(to bottom, CONTROL, PANEL);
            color: TEXT; border: 1px solid CONTROL; border-top-color: #667067;
            border-bottom-color: #090c09; border-radius: 2px; padding: 4px 7px;
            min-width: 16px; min-height: 18px; font: bold 10px "DejaVu Sans"; }
        .music-player-main .panel-control { min-width: 24px; min-height: 24px; padding: 1px; }
        .music-player-main button:hover { border-color: ACCENT; }
        .music-player-main button:active, .music-player-main button.active,
        .music-player-main button:checked { background: LCD; color: ACCENT;
            border-top-color: #030503; border-bottom-color: CONTROL; }
        .music-player-main button:disabled { opacity: .45; }
        .music-player-main :focus { outline: 1px solid ACCENT; outline-offset: -2px; }
        .music-player-main scale { padding: 3px; color: TEXT; font-size: 10px; }
        .music-player-main scale trough { background: LCD; min-height: 4px; min-width: 4px;
            border: 1px solid #080b08; border-bottom-color: CONTROL; }
        .music-player-main scale highlight { background: ACCENT; border: none; box-shadow: none; }
        .music-player-main scale.vertical highlight { background: transparent; }
        .music-player-main scale slider { background: linear-gradient(to bottom, #c7ccc6, #778176);
            border: 1px solid #242c24; border-top-color: #edf3e9;
            box-shadow: none; border-radius: 1px; min-width: 9px; min-height: 12px; margin: -4px 0; }
        .music-player-main scale.vertical slider { min-width: 19px; min-height: 7px; margin: 0 -8px; }
        .music-player-main scale.eq-bypassed trough { background: alpha(LCD, .6);
            border-color: alpha(#080b08, .6); border-bottom-color: alpha(CONTROL, .6); }
        .music-player-main scale.eq-bypassed slider {
            background: linear-gradient(to bottom, alpha(#c7ccc6, .45), alpha(#778176, .45));
            border-color: alpha(#242c24, .45); border-top-color: alpha(#edf3e9, .45); }
        .music-player-main scale mark { color: TEXT; font-size: 8px; }
        .playlist { background: LCD; color: ACCENT; font: 12px "Liberation Mono";
                    -GtkTreeView-vertical-separator: 0; }
        .playlist:selected { background: CONTROL; color: TEXT; }
        .playlist-search { background: LCD; color: TEXT; border: 1px solid CONTROL;
                           border-radius: 2px; padding: 3px; min-height: 20px; font-size: 11px; }
        .search-miss { border-color: #e78d55; }
        .muted { color: TEXT; font-size: 10px; opacity: .8; }
        .panel-header { padding: 2px; }
        .music-player-main.drag-over { border-color: ACCENT; }
        """
        for token in ('CHASSIS', 'PANEL', 'CONTROL', 'TEXT', 'ACCENT', 'LCD'):
            css = css.replace(token, t[token.lower()])
        if t is THEMES['silver']:
            css += '.music-player-main button { color: #10151b; background: linear-gradient(to bottom, #d3d7df, #929aaa); }'
            css += '.music-player-main button:active, .music-player-main button.active { color: #8cfa65; background: #131a15; }'
            css += '.playlist:selected { color: #10151b; }'
        if hasattr(self, '_css_provider'):
            Gtk.StyleContext.remove_provider_for_screen(Gdk.Screen.get_default(), self._css_provider)
        self._css_provider = Gtk.CssProvider()
        self._css_provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), self._css_provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        if hasattr(self, 'analyzer'):
            self.analyzer.queue_draw()
            self.playlist_view.queue_draw()

    @staticmethod
    def _minimum_geometry(height):
        geometry = Gdk.Geometry()
        geometry.min_width = 440
        geometry.min_height = height
        return geometry

    @staticmethod
    def _valid_pair(value):
        return (isinstance(value, (list, tuple)) and len(value) == 2 and
                all(isinstance(n, (int, float)) and math.isfinite(n) for n in value))

    def _workarea(self):
        display = Gdk.Display.get_default()
        monitor = display.get_monitor_at_window(self.get_window()) if self.get_window() else display.get_primary_monitor()
        if monitor is None:
            monitor = display.get_monitor(0)
        return monitor.get_workarea()

    def _clamp_size(self, size, minimum_height=220):
        if not self._valid_pair(size):
            size = [WINDOW_W, WINDOW_H]
        area = self._workarea()
        return (max(440, min(int(size[0]), area.width)),
                max(minimum_height, min(int(size[1]), area.height)))

    def _clamp_position(self, position):
        x, y = map(int, position)
        display = Gdk.Display.get_default()
        monitor = display.get_monitor_at_point(x, y) or display.get_monitor(0)
        area = monitor.get_workarea()
        return max(area.x, min(x, area.x + area.width - 100)), max(area.y, min(y, area.y + area.height - 60))

    def _window_mapped(self, *_args):
        if not getattr(self, '_layout_restored', False):
            self._layout_restored = True
            self.resize(*self._clamp_size(self._expanded_size))
            self.panels.restore()
            if self._restore_shade:
                self.tasks.idle_add(self.toggle_windowshade)
        self._update_analyzer_visibility()
        return False

    def toggle_windowshade(self, *_args):
        if self._destroyed:
            return False
        self._layout_switching = True
        if not self._windowshade:
            self._expanded_size = list(self.get_size())
        self._windowshade = not self._windowshade
        self.resize_grip.set_visible(not self._windowshade)
        self.player_frame.set_visible(not self._windowshade)
        self.brand_group.set_visible(not self._windowshade)
        self._title_spacer.set_visible(not self._windowshade)
        for widget in (self.shade_title, self.shade_time, self.shade_controls):
            if self._windowshade:
                widget.show()
                if widget is self.shade_controls:
                    for child in widget.get_children():
                        child.show_all()
            else:
                widget.hide()
        for name in self.panels.items:
            self.panels._show(name)
        self.set_geometry_hints(None, self._minimum_geometry(48 if self._windowshade else 220),
                                Gdk.WindowHints.MIN_SIZE)
        self.resize(self._expanded_size[0], 48 if self._windowshade else self._expanded_size[1])
        self._update_analyzer_visibility()
        self._marquee_stop()
        if not self._windowshade:
            self._set_title_text(getattr(self, '_title_full', '') or DEFAULT_SONG_TEXT)
        def settled():
            self._layout_switching = False
            return False
        self.tasks.idle_add(settled)
        self.schedule_save_config()
        return False

    def _add_resize_grip(self, window, content):
        """Overlay a visible hit target without adding a footer to the layout."""
        overlay = Gtk.Overlay()
        overlay.add(content)
        window.add(overlay)
        grip = Gtk.DrawingArea()
        grip.set_size_request(24, 24)
        grip.set_halign(Gtk.Align.END)
        grip.set_valign(Gtk.Align.END)
        grip.set_margin_end(2)
        grip.set_margin_bottom(2)
        grip.set_tooltip_text('Drag to resize')
        grip.get_accessible().set_name('Resize window')
        grip.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK
                        | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        grip.connect('draw', self._draw_resize_grip)
        grip.connect('button-press-event',
                     lambda _widget, event: self._resize_press(window, event, from_grip=True))
        def realize(widget):
            cursor = Gdk.Cursor.new_from_name(widget.get_display(), 'se-resize')
            if cursor is None:
                cursor = Gdk.Cursor.new_for_display(widget.get_display(), Gdk.CursorType.BOTTOM_RIGHT_CORNER)
            widget.get_window().set_cursor(cursor)
        def hover(widget, event, entering):
            if entering:
                widget.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
            else:
                widget.unset_state_flags(Gtk.StateFlags.PRELIGHT)
            widget.queue_draw()
            return False
        grip.connect('realize', realize)
        grip.connect('enter-notify-event', hover, True)
        grip.connect('leave-notify-event', hover, False)
        overlay.add_overlay(grip)
        overlay.set_overlay_pass_through(grip, False)
        window.resize_grip = grip
        grip.show()
        grip.set_no_show_all(True)
        overlay.show()

    def _draw_resize_grip(self, widget, cr):
        hovered = bool(widget.get_state_flags() & Gtk.StateFlags.PRELIGHT)
        x, y = widget.get_allocated_width() - 4.5, widget.get_allocated_height() - 4.5
        # Paired dark/light diagonals give the grip the same bevel as the buttons.
        for offset, color, alpha in ((1, self.theme['lcd'], 1),
                                     (0, self.theme['accent'] if hovered else self.theme['text'],
                                      1 if hovered else .65)):
            self._cairo_color(cr, color, alpha)
            cr.set_line_width(1)
            for length in (4, 8, 12):
                cr.move_to(x - length, y + offset)
                cr.line_to(x, y - length + offset)
            cr.stroke()
        return False

    def _resize_press(self, window, event, from_grip=False):
        if event.button != 1 or (window is self and self._windowshade):
            return False
        if from_grip or (event.x >= window.get_allocated_width() - 12
                         and event.y >= window.get_allocated_height() - 12):
            window.begin_resize_drag(Gdk.WindowEdge.SOUTH_EAST, event.button,
                                     int(event.x_root), int(event.y_root), event.time)
            return True
        return False

    def _reset_layout(self, *_args):
        if self._windowshade:
            self.toggle_windowshade()
        self.panels.reset()
        self._expanded_size = [WINDOW_W, WINDOW_H]
        self.resize(*self._clamp_size(self._expanded_size))
        if self.panels.x11:
            area = self._workarea()
            self.move(area.x + max(0, (area.width - WINDOW_W) // 2), area.y + 30)
        self.schedule_save_config()

    def _set_appearance(self, key, value):
        self.config[key] = value
        if key == 'theme':
            self.config['palette'] = None
            self.setup_styling()
        if key == 'show_art':
            self._update_album_art(self.current_song)
        self.analyzer.queue_draw()
        self._update_analyzer_visibility()
        self.schedule_save_config()

    def _choice_menu(self, parent, label, key, choices):
        item, submenu = Gtk.MenuItem(label=label), Gtk.Menu()
        for value, caption in choices:
            choice = Gtk.CheckMenuItem(label=caption)
            choice.set_draw_as_radio(True)
            choice.set_active(self.config.get(key) == value)
            choice.connect('activate', lambda _w, k=key, v=value: self._set_appearance(k, v))
            submenu.append(choice)
        item.set_submenu(submenu)
        parent.append(item)

    def _appearance_menus(self, menu):
        self._choice_menu(menu, 'Theme', 'theme', [(key, value['name']) for key, value in THEMES.items()])
        visualization, sub = Gtk.MenuItem(label='Visualization'), Gtk.Menu()
        for key, caption in [('visualization', 'Spectrum enabled'), ('peaks', 'Falling peak caps')]:
            item = Gtk.CheckMenuItem(label=caption)
            item.set_active(self.config[key])
            item.connect('toggled', lambda w, k=key: self._set_appearance(k, w.get_active()))
            sub.append(item)
        self._choice_menu(sub, 'Colors', 'palette', [(None, 'Follow theme'), ('green', 'Green'),
                                                     ('classic', 'Green / yellow / red'), ('amber', 'Amber')])
        self._choice_menu(sub, 'Falloff', 'falloff', [('slow', 'Slow'), ('normal', 'Normal'), ('fast', 'Fast')])
        visualization.set_submenu(sub)
        menu.append(visualization)
        view, sub = Gtk.MenuItem(label='View'), Gtk.Menu()
        shade = Gtk.CheckMenuItem(label='Windowshade')
        shade.set_active(self._windowshade)
        shade.connect('activate', self.toggle_windowshade)
        sub.append(shade)
        art = Gtk.CheckMenuItem(label='Album artwork')
        art.set_active(self.config['show_art'])
        art.connect('toggled', lambda w: self._set_appearance('show_art', w.get_active()))
        sub.append(art)
        for name, panel in self.panels.items.items():
            item = Gtk.CheckMenuItem(label=panel['title'].title())
            item.set_active(panel['visible'])
            item.connect('toggled', lambda w, n=name: self.panels.set_visible(n, w.get_active()))
            sub.append(item)
        reset = Gtk.MenuItem(label='Reset layout')
        reset.connect('activate', self._reset_layout)
        sub.append(reset)
        view.set_submenu(sub)
        menu.append(view)

    def _popup_actions(self, button, actions):
        menu = Gtk.Menu()
        for caption, callback in actions:
            item = Gtk.MenuItem(label=caption)
            item.connect('activate', callback)
            menu.append(item)
        menu.show_all()
        self._actions_menu = menu
        menu.popup_at_widget(button, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, None)
        return menu

    def _add_popup(self, button):
        self._popup_actions(button, [('Files… (Ctrl+O)', self.add_files),
                                     ('Folder…', self.add_folder), ('Stream URL… (Ctrl+L)', self.open_url_dialog)])

    def _playlist_popup(self, button):
        actions = [('Undo edit (Ctrl+Z)', self.undo_playlist),
                   ('Save playlist as…', self._save_playlist_as), ('Save playlist', self._save_playlist_named)]
        for name in self._saved_playlist_names():
            actions.append((f'Load: {name}', lambda _w, n=name: self._load_named_playlist(n)))
        actions.extend([('Export M3U…', self.export_m3u), ('Remove missing files', self.remove_missing),
                        ('Clear playlist', self.clear_playlist)])
        menu = self._popup_actions(button, actions)
        menu.get_children()[0].set_sensitive(bool(self._undo))

    def create_interface(self):
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.main_box.get_style_context().add_class('music-player-main')
        self._add_resize_grip(self, self.main_box)
        self.main_box.pack_start(self.create_title_bar(), False, False, 0)
        self.player_frame = Gtk.Frame()
        self.player_frame.get_style_context().add_class('music-player-frame')
        player_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        player_box.pack_start(self.create_display_area(), False, False, 0)
        player_box.pack_start(self.create_controls(), False, False, 0)
        player_box.pack_start(self.create_volume_controls(), False, False, 0)
        self.player_frame.add(player_box)
        self.main_box.pack_start(self.player_frame, False, False, 0)
        self.panels = PanelManager(self, self.main_box)
        self.panels.add('eq', 'EQUALIZER', self.create_equalizer())
        self.panels.add('playlist', 'PLAYLIST', self.create_playlist(), expand=True)
        self.set_geometry_hints(None, self._minimum_geometry(220),
                                Gdk.WindowHints.MIN_SIZE)
        self.time_display.connect('notify::label', lambda *_: self.shade_time.set_text(self.time_display.get_text()))
        self.connect('map-event', self._window_mapped)
        self.connect('unmap-event', lambda *_: self._update_analyzer_visibility())
        self.connect('button-press-event', self._resize_press)

    def create_title_bar(self):
        events = Gtk.EventBox()
        events.get_style_context().add_class('music-player-titlebar')
        box = Gtk.Box(spacing=3)
        self.brand_group = Gtk.Box(spacing=8)
        self.brand_group.set_margin_start(19)  # 20 px including the chassis border
        self.brand_label = Gtk.Label(label='LLAMA AMP')
        self.brand_label.get_style_context().add_class('brand')
        self.brand_group.pack_start(self.brand_label, False, False, 0)
        self.version_badge = Gtk.Label(label=f'v{APP_VERSION}')
        self.version_badge.get_style_context().add_class('version-badge')
        self.version_badge.set_valign(Gtk.Align.CENTER)
        self.version_badge.get_accessible().set_name(f'Version {APP_VERSION}')
        self.brand_group.pack_start(self.version_badge, False, False, 0)
        box.pack_start(self.brand_group, False, False, 0)
        self.shade_title = Gtk.Label()
        self.shade_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.shade_title.set_width_chars(8)
        self.shade_title.set_max_width_chars(35)
        self.shade_title.set_no_show_all(True)
        box.pack_start(self.shade_title, True, True, 3)
        self.shade_time = Gtk.Label(label='00:00')
        self.shade_time.set_no_show_all(True)
        box.pack_start(self.shade_time, False, False, 2)
        self.shade_controls = Gtk.Box(spacing=1)
        self.shade_controls.set_no_show_all(True)
        for icon, tip, callback in [('previous', 'Previous', self.previous_song),
                                     ('play', 'Play / Pause (Space)', self.toggle_play_pause),
                                     ('next', 'Next', self.next_song)]:
            button = self._transport_button(icon, tip, callback)
            self.shade_controls.pack_start(button, False, False, 0)
        box.pack_start(self.shade_controls, False, False, 0)
        spacer = Gtk.Label()
        box.pack_start(spacer, True, True, 0)
        self._title_spacer = spacer
        for label, tip, callback in [('⚙', 'Settings', self.on_settings_clicked),
                                     ('▱', 'Windowshade / restore (double-click title bar)', self.toggle_windowshade),
                                     ('−', 'Minimize', lambda *_: self.iconify()),
                                     ('×', 'Close', self.on_close_clicked)]:
            button = Gtk.Button(label=label)
            button.set_tooltip_text(tip)
            button.get_accessible().set_name(tip)
            button.connect('clicked', callback)
            box.pack_start(button, False, False, 0)
        events.add(box)
        events.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        events.connect('button-press-event', self.on_title_press)
        return events

    def on_title_press(self, widget, event):
        if event.button == 3:
            self.show_title_context_menu(widget, event)
            return True
        if event.button != 1:
            return False
        if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            self.toggle_windowshade()
        else:
            self.panels.start_drag(self, event) if hasattr(self, 'panels') else None
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
        return True

    def build_settings_menu(self):
        """The settings menu: audio fidelity toggles, EQ presets, window actions.
        Served by both the titlebar ⚙ button and the titlebar right-click."""
        menu = Gtk.Menu()
        self._appearance_menus(menu)
        menu.append(Gtk.SeparatorMenuItem())
        header = Gtk.MenuItem(label="Audio Output")
        header.set_sensitive(False)
        menu.append(header)

        # Direct mode: bit-transparent playback (bypass EQ/balance/spectrum)
        direct_item = Gtk.CheckMenuItem(label="Direct Mode (bypass EQ/DSP)")
        direct_item.set_active(self.direct_mode)
        direct_item.connect("toggled", self.toggle_direct_mode)
        menu.append(direct_item)

        # ALSA direct: skip the system mixer, exclusive DAC access
        alsa_item = Gtk.CheckMenuItem(label="ALSA Output (bit-perfect to DAC)")
        alsa_item.set_active(self.alsa_output)
        alsa_item.connect("toggled", self.toggle_alsa_output)
        menu.append(alsa_item)

        # ALSA device picker (rebuilt each open = hotplug-aware)
        dev_item = Gtk.MenuItem(label="ALSA Device")
        dev_sub = Gtk.Menu()
        current_dev = self.config.get('alsa_device') or None
        auto_item = Gtk.CheckMenuItem(label="Auto (Analog)")
        auto_item.set_draw_as_radio(True)
        auto_item.set_active(current_dev is None)
        auto_item.connect("activate", lambda _w: self._select_alsa_device(None))
        dev_sub.append(auto_item)
        for dev, label in self._list_alsa_devices():
            item = Gtk.CheckMenuItem(label=f"{label}  ({dev})")
            item.set_draw_as_radio(True)
            item.set_active(dev == current_dev)
            item.connect("activate", lambda _w, d=dev: self._select_alsa_device(d))
            dev_sub.append(item)
        dev_item.set_submenu(dev_sub)
        menu.append(dev_item)

        gapless_item = Gtk.CheckMenuItem(label="Gapless Playback")
        gapless_item.set_active(self.gapless)
        gapless_item.connect("toggled", self.toggle_gapless)
        menu.append(gapless_item)

        # ReplayGain volume normalization (inactive in Direct Mode)
        rg_item = Gtk.MenuItem(label="ReplayGain")
        rg_sub = Gtk.Menu()
        for mode, label in (('off', 'Off'), ('track', 'Track gain'),
                            ('album', 'Album gain')):
            item = Gtk.CheckMenuItem(label=label)
            item.set_draw_as_radio(True)
            item.set_active(self.replaygain == mode)
            item.connect("activate", lambda _w, m=mode: self.set_replaygain(m))
            rg_sub.append(item)
        rg_item.set_submenu(rg_sub)
        menu.append(rg_item)

        tray_item = Gtk.CheckMenuItem(label="Tray Icon")
        tray_item.set_active(self._tray is not None)
        tray_item.connect("toggled", self.toggle_tray_icon)
        menu.append(tray_item)

        scrobble_item = Gtk.MenuItem(label="Scrobbling…")
        scrobble_item.connect("activate", self.show_scrobble_dialog)
        menu.append(scrobble_item)

        notif_item = Gtk.CheckMenuItem(label="Notifications")
        notif_item.set_active(self.config.get('notifications', True) is not False)
        notif_item.connect("toggled", self.toggle_notifications)
        menu.append(notif_item)

        menu.append(Gtk.SeparatorMenuItem())

        # EQ presets as a submenu (they otherwise hide behind the EQ right-click)
        eq_item = Gtk.MenuItem(label="EQ Preset")
        eq_sub = Gtk.Menu()
        self._append_eq_preset_items(eq_sub)
        eq_item.set_submenu(eq_sub)
        menu.append(eq_item)

        menu.append(Gtk.SeparatorMenuItem())

        url_item = Gtk.MenuItem(label="Open URL…")
        url_item.connect("activate", self.open_url_dialog)
        menu.append(url_item)

        # Named playlists
        pl_item = Gtk.MenuItem(label="Playlists")
        pl_sub = Gtk.Menu()
        save_label = (f"Save ({self._playlist_name})" if self._playlist_name else "Save")
        save_item = Gtk.MenuItem(label=save_label)
        save_item.set_sensitive(bool(self._playlist_name and self.playlist))
        save_item.connect("activate", self._save_playlist_named)
        pl_sub.append(save_item)
        save_as_item = Gtk.MenuItem(label="Save As…")
        save_as_item.set_sensitive(bool(self.playlist))
        save_as_item.connect("activate", self._save_playlist_as)
        pl_sub.append(save_as_item)
        names = self._saved_playlist_names()
        if names:
            pl_sub.append(Gtk.SeparatorMenuItem())
            for name in names:
                item = Gtk.CheckMenuItem(label=name)
                item.set_draw_as_radio(True)
                item.set_active(name == self._playlist_name)
                item.connect("activate",
                             lambda _w, n=name: self._load_named_playlist(n))
                pl_sub.append(item)
        pl_item.set_submenu(pl_sub)
        menu.append(pl_item)

        # Sleep timer
        sleep_item = Gtk.MenuItem(label="Sleep Timer")
        sleep_sub = Gtk.Menu()
        armed_min = None
        if self._sleep_deadline is not None:
            armed_min = max(0, (self._sleep_deadline - GLib.get_monotonic_time())
                            // 60_000_000) + 1
        choices = [("Off", None), ("After current track", 'track'),
                   ("15 min", 15), ("30 min", 30), ("60 min", 60)]
        for label, mode in choices:
            active = self._sleep_mode == mode
            if isinstance(mode, int) and active and armed_min is not None:
                label = f"{label} ({armed_min} min left)"
            item = Gtk.CheckMenuItem(label=label)
            item.set_draw_as_radio(True)
            item.set_active(active)
            item.connect("activate", lambda _w, m=mode: self._set_sleep(m))
            sleep_sub.append(item)
        sleep_item.set_submenu(sleep_sub)
        menu.append(sleep_item)

        menu.append(Gtk.SeparatorMenuItem())

        # Updates: one action item (check, or install if one is known) plus
        # the startup-check toggle. Hidden in Flatpak — the store updates it.
        if not IS_FLATPAK:
            if getattr(self, '_update_info', None):
                update_item = Gtk.MenuItem(
                    label=f"⬆ Update to v{self._update_info['version']}…")
                update_item.connect("activate", self._start_update)
            else:
                update_item = Gtk.MenuItem(label="Check for Updates…")
                update_item.connect("activate",
                                    lambda _w: self._check_updates(manual=True))
            menu.append(update_item)

            autoupd_item = Gtk.CheckMenuItem(label="Check for Updates on Startup")
            autoupd_item.set_active(
                self.config.get('update_check', True) is not False)
            autoupd_item.connect("toggled", self._toggle_update_check)
            menu.append(autoupd_item)

        about_item = Gtk.MenuItem(label=f"About {APP_NAME}")
        about_item.connect("activate", self.show_about_dialog)
        menu.append(about_item)

        menu.show_all()
        return menu

    def on_settings_clicked(self, button):
        """Open the settings menu from the titlebar gear button. The menu is
        kept as an attribute — a local would be garbage-collected while open."""
        self._settings_menu = self.build_settings_menu()
        self._settings_menu.popup_at_widget(button, Gdk.Gravity.SOUTH_EAST,
                                            Gdk.Gravity.NORTH_EAST, None)

    def show_title_context_menu(self, widget, event):
        """Show the settings menu on title bar right-click"""
        menu = self.build_settings_menu()
        menu.popup(None, None, None, None, event.button, event.time)
    
    def show_about_dialog(self, widget):
        """Show about dialog"""
        dialog = Gtk.AboutDialog()
        dialog.set_transient_for(self)
        dialog.set_modal(True)
        dialog.set_title(f"About {APP_NAME}")
        dialog.set_program_name(APP_NAME)
        dialog.set_version(APP_VERSION)
        dialog.set_comments("A Python GTK music player with a modern, Winamp-inspired interface")
        dialog.set_copyright("© 2026 Jeremy Person")
        dialog.set_website("https://github.com/jeremyperson/llama-amp")
        dialog.set_website_label("GitHub")
        dialog.set_authors(["Jeremy Person"])
        dialog.run()
        dialog.destroy()
    
    def on_button_press(self, widget, event):
        """Handle button press for visual feedback"""
        if event.button == 1:  # Left mouse button
            widget.get_style_context().add_class('pressed')
    
    def on_button_release(self, widget, event):
        """Handle button release for visual feedback"""
        if event.button == 1:  # Left mouse button
            widget.get_style_context().remove_class('pressed')
    
    def on_close_clicked(self, button):
        """Handle close button click — route through destroy so on_destroy
        (config write, playlist save, MPRIS teardown) always runs."""
        self.destroy()
    
    def on_display_scroll(self, widget, event):
        """Scroll wheel over the display panel adjusts volume."""
        if event.direction == Gdk.ScrollDirection.UP:
            self._step_volume(VOLUME_STEP)
        elif event.direction == Gdk.ScrollDirection.DOWN:
            self._step_volume(-VOLUME_STEP)
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            ok, _dx, dy = event.get_scroll_deltas()
            if ok and dy:
                self._step_volume(-dy * VOLUME_STEP)
        return True

    def create_display_area(self):
        frame = Gtk.Frame()
        frame.get_style_context().add_class('music-player-display')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.song_label = Gtk.Label(label=DEFAULT_SONG_TEXT, xalign=0)
        self.song_label.get_style_context().add_class('song-title')
        self.song_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.song_label.set_width_chars(20)
        self.song_label.set_max_width_chars(44)
        box.pack_start(self.song_label, False, False, 0)
        middle = Gtk.Box(spacing=8)
        self.album_art = Gtk.Image()
        self.album_art.set_no_show_all(True)
        self.album_art.get_style_context().add_class('music-player-art')
        middle.pack_start(self.album_art, False, False, 0)
        self.time_display = Gtk.Label(label='00:00')
        self.time_display.get_style_context().add_class('music-player-time')
        time_events = Gtk.EventBox()
        time_events.set_visible_window(False)
        time_events.add(self.time_display)
        time_events.set_tooltip_text('Click: elapsed / remaining')
        time_events.connect('button-press-event', self._clock_press)
        middle.pack_start(time_events, False, False, 0)
        self.analyzer = Gtk.DrawingArea()
        self.analyzer.set_size_request(100, 62)
        self.analyzer.set_hexpand(True)
        self.analyzer.get_accessible().set_name('Audio spectrum with falling peak indicators')
        self.analyzer.connect('draw', self._draw_analyzer)
        self.analyzer.connect('map', lambda *_: self._update_analyzer_visibility())
        self.analyzer.connect('unmap', lambda *_: self._update_analyzer_visibility())
        middle.pack_start(self.analyzer, True, True, 0)
        box.pack_start(middle, False, False, 0)
        self.info_label = Gtk.Label(xalign=0)
        self.info_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.info_label.get_style_context().add_class('muted')
        box.pack_start(self.info_label, False, False, 0)
        box.pack_start(self.create_position_slider(), False, False, 0)
        frame.add(box)
        return frame

    def create_controls(self):
        box = Gtk.Box(spacing=2)
        for attr, icon, tip, callback in [
                ('prev_btn', 'previous', 'Previous', self.previous_song),
                ('play_btn', 'play', 'Play / Pause (Space)', self.toggle_play_pause),
                ('stop_btn', 'stop', 'Stop (right-click: stop after track)', self.stop_song),
                ('next_btn', 'next', 'Next', self.next_song),
                ('eject_btn', 'eject', 'Add files (Ctrl+O)', self.add_files)]:
            button = self._transport_button(icon, tip, callback)
            setattr(self, attr, button)
            box.pack_start(button, False, False, 0)
        self.stop_btn.connect('button-press-event', self.on_stop_button_press)
        box.pack_start(Gtk.Label(), True, True, 0)
        self.shuffle_btn = Gtk.Button(label='SHUFFLE')
        self.shuffle_btn.connect('clicked', self.toggle_shuffle)
        self.shuffle_btn.set_tooltip_text('Shuffle: off / tracks / albums (S)')
        box.pack_start(self.shuffle_btn, False, False, 1)
        self.repeat_btn = Gtk.Button(label='REPEAT')
        self.repeat_btn.connect('clicked', self.toggle_repeat)
        self.repeat_btn.set_tooltip_text('Repeat: off / all / one (R)')
        box.pack_start(self.repeat_btn, False, False, 1)
        return box

    def _panel_button(self, name, action):
        button = Gtk.Button()
        button.get_style_context().add_class('panel-control')
        button.set_size_request(28, 28)
        drawing = Gtk.DrawingArea()
        drawing.set_size_request(16, 16)
        drawing.connect('draw', self._draw_panel_control, name, action)
        button.add(drawing)
        return button

    def _draw_panel_control(self, widget, cr, name, action):
        item = self.panels.items.get(name)
        if not item:
            return False
        color = '#152019' if self.theme is THEMES['silver'] else self.theme['text']
        self._cairo_color(cr, color)
        cr.set_line_width(1.5)
        cr.translate((widget.get_allocated_width() - 16) / 2,
                     (widget.get_allocated_height() - 16) / 2)
        if action == 'collapse':
            cr.move_to(3, 8); cr.line_to(13, 8)
            if item['collapsed']:
                cr.move_to(8, 3); cr.line_to(8, 13)
        elif item['attached']:
            # An arrow leaving a window: detach.
            cr.move_to(8, 4); cr.line_to(3, 4); cr.line_to(3, 13)
            cr.line_to(12, 13); cr.line_to(12, 8)
            cr.move_to(7, 9); cr.line_to(14, 2)
            cr.move_to(9, 2); cr.line_to(14, 2); cr.line_to(14, 7)
        else:
            # A downward arrow into a dock: reattach.
            cr.move_to(3, 10); cr.line_to(3, 14); cr.line_to(13, 14); cr.line_to(13, 10)
            cr.move_to(8, 2); cr.line_to(8, 11)
            cr.move_to(4, 7); cr.line_to(8, 11); cr.line_to(12, 7)
        cr.stroke()
        return False

    def _transport_button(self, icon, tooltip, callback):
        button = Gtk.Button()
        drawing = Gtk.DrawingArea()
        drawing.set_size_request(18, 16)
        drawing.connect('draw', self._draw_transport, icon)
        button.add(drawing)
        button.set_tooltip_text(tooltip)
        button.get_accessible().set_name(tooltip)
        button.connect('clicked', callback)
        return button

    def _draw_transport(self, widget, cr, icon):
        if icon == 'play' and self.is_playing:
            icon = 'pause'
        cr.translate(widget.get_allocated_width() / 2 - 8, widget.get_allocated_height() / 2 - 7)
        color = self.theme['accent'] if icon in ('play', 'pause') else ('#152019' if self.theme is THEMES['silver'] else self.theme['text'])
        self._cairo_color(cr, color)
        if icon == 'pause':
            cr.rectangle(3, 2, 4, 10); cr.rectangle(10, 2, 4, 10)
        elif icon == 'stop':
            cr.rectangle(3, 2, 11, 11)
        elif icon == 'eject':
            cr.move_to(2, 9); cr.line_to(8, 2); cr.line_to(14, 9); cr.close_path()
            cr.rectangle(2, 11, 12, 2)
        else:
            if icon == 'previous':
                cr.translate(16, 0); cr.scale(-1, 1)
            cr.move_to(3, 1); cr.line_to(13, 7); cr.line_to(3, 13); cr.close_path()
            if icon in ('next', 'previous'):
                cr.rectangle(13, 1, 2, 12)
        cr.fill()
        return False

    @staticmethod
    def _cairo_color(cr, color, alpha=1):
        cr.set_source_rgba(*(int(color[i:i+2], 16) / 255 for i in (1, 3, 5)), alpha)

    def create_position_slider(self):
        """Bare position scale — lives at the bottom of the LCD display panel."""
        self.position_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        self.position_scale.set_can_focus(False)  # arrows are global shortcuts; no focus ring
        self.position_scale.get_style_context().add_class('music-player-slider')
        self.position_scale.set_range(0, 100)
        self.position_scale.set_value(0)
        self.position_scale.set_draw_value(False)
        self.position_scale.set_tooltip_text("Song Position")
        self.position_scale.connect("button-press-event", self.on_position_pressed)
        self.position_scale.connect("button-release-event", self.on_position_released)
        # Swallow scroll: GTK's default scroll-to-change-value moves the slider
        # without seeking, then the position timer snaps it back — confusing.
        self.position_scale.connect("scroll-event", lambda *a: True)
        return self.position_scale
    
    def create_volume_controls(self):
        container = Gtk.Grid()
        container.set_column_homogeneous(True)
        container.set_column_spacing(0)
        self.level_controls = container
        for column, width, label, attr, limits, callback, formatter in (
                (0, 3, 'VOLUME', 'volume_scale', (0, 1), self.on_volume_changed, self.format_volume_value),
                (3, 2, 'BALANCE', 'balance_scale', (-1, 1), self.on_balance_changed, self.format_balance_value)):
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            group.set_hexpand(True)
            group.set_margin_start(6 if column else 0)
            group.set_margin_end(0 if column else 6)
            header = Gtk.Box()
            caption = Gtk.Label(label=label, xalign=0)
            caption.get_style_context().add_class('music-player-label')
            header.pack_start(caption, True, True, 0)
            readout = Gtk.Label(xalign=1)
            readout.get_style_context().add_class('muted')
            header.pack_end(readout, False, False, 0)
            group.pack_start(header, False, False, 0)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, *limits, .01)
            scale.set_can_focus(True)
            scale.set_draw_value(False)
            scale.get_accessible().set_name(label.title())
            scale.set_tooltip_text('Volume' if column == 0 else 'Balance · double-click to center')
            setattr(self, attr, scale)
            scale.connect('value-changed', callback)
            scale.connect('value-changed', lambda widget, r=readout, f=formatter:
                          r.set_text(f(widget, widget.get_value())))
            readout.set_text(formatter(scale, scale.get_value()))
            if column:
                scale.connect('button-press-event', self._eq_scale_press)
            group.pack_start(scale, False, False, 0)
            container.attach(group, column, 0, width, 1)
        return container

    def create_equalizer(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        controls = Gtk.Box(spacing=4)
        self.eq_on_btn = Gtk.Button(label='ON')
        self.eq_on_btn.connect('clicked', self.toggle_eq_enabled)
        controls.pack_start(self.eq_on_btn, False, False, 0)
        self.preset_button = Gtk.Button(label='Presets ▾')
        self.preset_button.connect('clicked', self._preset_popup)
        controls.pack_start(self.preset_button, False, False, 0)
        reset = Gtk.Button(label='Reset')
        reset.set_tooltip_text('Reset all EQ bands and preamp to 0 dB')
        reset.connect('clicked', self._reset_eq)
        controls.pack_start(reset, False, False, 0)
        self.eq_status = Gtk.Label(xalign=1)
        self.eq_status.set_ellipsize(Pango.EllipsizeMode.END)
        self.eq_status.get_style_context().add_class('muted')
        controls.pack_end(self.eq_status, True, True, 0)
        box.pack_start(controls, False, False, 0)
        bands = Gtk.Box(spacing=1, homogeneous=True)
        self.eq_bars = []
        self._syncing_eq = True
        for index, label in enumerate(['PRE'] + EQ_FREQUENCIES):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            preamp = index == 0
            lo, hi = (-PREAMP_DB_RANGE, PREAMP_DB_RANGE) if preamp else (EQ_GAIN_MIN, EQ_GAIN_MAX)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, lo, hi, .5)
            scale.set_inverted(True)
            scale.set_size_request(24, 68)
            scale.set_draw_value(False)
            scale.add_mark(0, Gtk.PositionType.LEFT, None)
            scale.get_accessible().set_name('Preamp' if preamp else label + ' Hz equalizer gain')
            scale.connect('value-changed', self._eq_scale_changed, index - 1)
            scale.connect('button-press-event', self._eq_scale_press)
            column.pack_start(scale, True, True, 0)
            value = Gtk.Label()
            value.get_style_context().add_class('muted')
            scale._gain_label = value
            column.pack_start(value, False, False, 0)
            caption = Gtk.Label(label=label)
            caption.get_style_context().add_class('muted')
            column.pack_start(caption, False, False, 0)
            bands.pack_start(column, True, True, 0)
            if preamp:
                self.preamp_bar = scale
            else:
                self.eq_bars.append(scale)
        box.pack_start(bands, False, False, 0)
        self._syncing_eq = False
        self._sync_eq_controls()
        return box

    def _eq_scale_changed(self, scale, index):
        if self._syncing_eq:
            return
        db = scale.get_value()
        if index < 0:
            self.preamp_value = (db / PREAMP_DB_RANGE + 1) / 2
            self._apply_preamp()
        else:
            self.eq_values[index] = self.db_to_eq_value(db)
            self._apply_eq_band(index)
        self._sync_eq_controls()
        self.schedule_save_config()

    def _eq_scale_press(self, scale, event):
        if event.button == 1 and event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            scale.set_value(0)
            return True
        if event.button == 3:
            self.show_eq_preset_menu(event)
            return True
        return False

    def _sync_eq_controls(self):
        if not hasattr(self, 'eq_status'):
            return
        self._syncing_eq = True
        try:
            scales = [self.preamp_bar] + self.eq_bars
            gains = [(self.preamp_value * 2 - 1) * PREAMP_DB_RANGE] + [self.eq_value_to_db(v) for v in self.eq_values]
            for scale, gain in zip(scales, gains):
                scale.set_value(gain)
                scale._gain_label.set_text(f'{gain:+g}')
                tip = f'{gain:+g} dB · double-click to reset · arrows adjust'
                if self.direct_mode:
                    tip += ' · stored for later; Direct Mode bypasses EQ'
                elif not self.eq_enabled:
                    tip += ' · stored for later; equalizer is off'
                scale.set_tooltip_text(tip)
                context = scale.get_style_context()
                (context.add_class if self.direct_mode or not self.eq_enabled else context.remove_class)('eq-bypassed')
            self.eq_on_btn.set_label('Bypassed' if self.direct_mode else 'ON' if self.eq_enabled else 'OFF')
            self.eq_on_btn.set_sensitive(not self.direct_mode)
            self.eq_on_btn.set_tooltip_text('Direct Mode bypasses EQ; disable it in Audio Output to use the equalizer'
                                            if self.direct_mode else 'Enable / disable the equalizer')
            context = self.eq_on_btn.get_style_context()
            (context.add_class if self.eq_enabled and not self.direct_mode else context.remove_class)('active')
            self.eq_status.set_text('Bypassed · Direct Mode' if self.direct_mode
                                    else 'Equalizer off' if not self.eq_enabled else self._current_preset_name() or 'Custom')
        finally:
            self._syncing_eq = False

    def _reset_eq(self, *_args):
        self.preamp_value = .5
        self.apply_eq_preset('Flat')
        self._sync_eq_controls()

    def _preset_popup(self, button):
        menu = Gtk.Menu()
        self._append_eq_preset_items(menu)
        menu.show_all()
        self._preset_menu = menu
        menu.popup_at_widget(button, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, None)

    def apply_eq_preset(self, name):
        """Apply a named preset from EQ_PRESETS to all bands."""
        dbs = EQ_PRESETS.get(name)
        if not dbs or len(dbs) != len(self.eq_values):
            return
        self.eq_values = [self.db_to_eq_value(db) for db in dbs]
        self.apply_all_eq()
        for bar in self.eq_bars:
            bar.queue_draw()
        self.schedule_save_config()

    def _current_preset_name(self):
        """Which preset the current gains match (None = custom curve)."""
        for name, dbs in EQ_PRESETS.items():
            values = [self.db_to_eq_value(db) for db in dbs]
            if all(abs(a - b) < 0.02 for a, b in zip(self.eq_values, values)):
                return name
        return None

    def _append_eq_preset_items(self, menu):
        """Radio-style preset entries; the active curve is checked."""
        current = self._current_preset_name()
        for name in EQ_PRESETS:
            label = "Flat (reset)" if name == "Flat" else name
            item = Gtk.CheckMenuItem(label=label)
            item.set_draw_as_radio(True)
            item.set_active(name == current)
            item.connect("activate", lambda _w, n=name: self.apply_eq_preset(n))
            menu.append(item)
        if current is None:
            custom = Gtk.CheckMenuItem(label="Custom")
            custom.set_draw_as_radio(True)
            custom.set_active(True)
            custom.set_sensitive(False)
            menu.append(custom)

    def show_eq_preset_menu(self, event):
        """Right-click menu on the EQ bars: apply a preset."""
        menu = Gtk.Menu()
        header = Gtk.MenuItem(label="EQ Presets")
        header.set_sensitive(False)
        menu.append(header)
        menu.append(Gtk.SeparatorMenuItem())
        self._append_eq_preset_items(menu)
        menu.show_all()
        self._eq_menu = menu  # keep referenced while open
        menu.popup_at_pointer(event)
    
    def log_debug(self, message):
        """Debug logging, opt-in via LLAMAAMP_DEBUG=1"""
        if DEBUG:
            print(f"DEBUG: {message}")
    
    
    def create_playlist(self):
        playlist_box = Gtk.VBox(spacing=5)
        playlist_box.set_margin_top(0)
        playlist_box.set_margin_bottom(0)
        playlist_box.set_margin_start(0)
        playlist_box.set_margin_end(0)
        
        self.playlist_info = Gtk.Label(label='0 tracks')
        self.playlist_info.get_style_context().add_class('muted')

        # Type-to-find: scrolls to matches without filtering the model
        # (a TreeModelFilter would break drag-reorder and index arithmetic)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Find in playlist…")
        self.search_entry.get_style_context().add_class('playlist-search')
        self.search_entry.connect("search-changed",
                                  lambda e: self._search_step(restart=True))
        self.search_entry.connect("activate",
                                  lambda e: self._search_step(restart=False))
        self.search_entry.connect("stop-search", self._search_escape)
        find_box = Gtk.Box(spacing=5)
        find_box.pack_start(self.search_entry, True, True, 0)
        self.search_count = Gtk.Label()
        self.search_count.get_style_context().add_class('muted')
        find_box.pack_start(self.search_count, False, False, 0)
        playlist_box.pack_start(find_box, False, False, 0)

        # Scrollable playlist
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_size_request(-1, 96)  # Four rows at the minimum window height
        
        self.playlist_store = Gtk.ListStore(str, str, int, str, str, str)  # path, display, number, duration
        self.playlist_view = Gtk.TreeView(model=self.playlist_store)
        self.playlist_view.get_style_context().add_class('playlist')
        self.playlist_view.set_has_tooltip(True)
        self.playlist_view.connect('query-tooltip', self._playlist_tooltip)
        self.playlist_view.set_can_focus(True)  # Allow keyboard focus
        
        marker = Gtk.CellRendererText()
        marker_col = Gtk.TreeViewColumn('', marker, text=5)
        marker_col.set_cell_data_func(marker, self._zebra_bg)
        marker_col.set_min_width(42)
        self.playlist_view.append_column(marker_col)
        # Track number column
        track_renderer = Gtk.CellRendererText()
        track_column = Gtk.TreeViewColumn("", track_renderer, text=2)
        track_column.set_cell_data_func(track_renderer, self._zebra_bg)
        track_column.set_min_width(30)
        self.playlist_view.append_column(track_column)
        
        # Song name column
        song_renderer = Gtk.CellRendererText()
        song_renderer.set_property('ellipsize', Pango.EllipsizeMode.END)
        song_renderer.set_property('height', 24)
        song_column = Gtk.TreeViewColumn("", song_renderer, text=1)
        song_column.set_cell_data_func(song_renderer, self._zebra_bg)
        song_column.set_expand(True)
        song_column.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
        song_column.set_min_width(60)
        self.playlist_view.append_column(song_column)

        # Track duration, right-aligned
        dur_renderer = Gtk.CellRendererText()
        dur_renderer.set_property('xalign', 1.0)
        dur_column = Gtk.TreeViewColumn("", dur_renderer, text=3)
        dur_column.set_cell_data_func(dur_renderer, self._zebra_bg)
        dur_column.set_min_width(64)
        self.playlist_view.append_column(dur_column)
        
        self.playlist_view.set_headers_visible(False)
        self.playlist_view.connect("row-activated", self.on_playlist_activated)

        # Drag-to-reorder rows. Typeahead search is off: it would swallow the
        # global letter shortcuts (S/R) whenever the list has focus.
        self.playlist_view.set_enable_search(False)
        self.playlist_view.set_reorderable(True)
        # The reorder dest swallows external file drops; add uri-list to its
        # accepted targets so dropping files onto the playlist works too.
        targets = self.playlist_view.drag_dest_get_target_list()
        if targets is None:
            targets = Gtk.TargetList.new([])
        targets.add_uri_targets(URI_TARGET_INFO)
        self.playlist_view.drag_dest_set_target_list(targets)
        self.playlist_view.connect("drag-data-received", self.on_view_drag_data_received)
        self.playlist_store.connect("row-inserted", self.on_store_rows_changed)
        self.playlist_store.connect("row-deleted", self.on_store_rows_changed)
        self.playlist_store.connect("rows-reordered", self.on_store_rows_changed)

        # Connect keyboard events for deletion
        self.playlist_view.connect("key-press-event", self.on_playlist_key_press)
        # Right-click context menu (queue actions)
        self.playlist_view.connect("button-press-event", self.on_playlist_button_press)

        # Multi-select (Ctrl/Shift-click) for bulk removal
        selection = self.playlist_view.get_selection()
        selection.set_mode(Gtk.SelectionMode.MULTIPLE)
        selection.connect("changed", self.on_playlist_selection_changed)
        
        scrolled.add(self.playlist_view)
        playlist_box.pack_start(scrolled, True, True, 0)
        
        # Playlist buttons
        tools = Gtk.Box(spacing=4)
        add = Gtk.Button(label='Add ▾')
        add.connect('clicked', self._add_popup)
        tools.pack_start(add, False, False, 0)
        playlist = Gtk.Button(label='Playlist ▾')
        playlist.connect('clicked', self._playlist_popup)
        tools.pack_start(playlist, False, False, 0)
        remove = Gtk.Button(label='Remove')
        remove.set_tooltip_text('Remove selected tracks (Delete) · Ctrl+Z to undo')
        remove.connect('clicked', self.remove_selected)
        tools.pack_start(remove, False, False, 0)
        self.feedback_label = Gtk.Label(xalign=1)
        self.feedback_label.set_margin_end(20)  # leave room for the corner grip
        self.feedback_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.feedback_label.get_style_context().add_class('muted')
        tools.pack_end(self.feedback_label, True, True, 0)
        playlist_box.pack_start(tools, False, False, 0)

        return playlist_box
    
    # Audio control methods
    def toggle_play_pause(self, button):
        if not self.playlist:
            self.add_files(None)
            return
            
        if self.is_playing:
            self.player.set_state(Gst.State.PAUSED)
            self._sync_play_ui(False)
        else:
            if self.current_song is None and self.playlist:
                self.load_song(self.current_index)

            # Check if current song file exists before trying to play
            if (self.current_song and not self._is_stream_url(self.current_song)
                    and not os.path.exists(self.current_song)):
                # Try to find the next available song
                self.find_and_load_next_available_song()
                if not self.current_song:
                    return  # No available songs found

            self._play_current()

    def find_and_load_next_available_song(self):
        """Find and load the next available (existing) song in the playlist"""
        if not self.playlist:
            return
        
        # Try all songs starting from current index
        for i in range(len(self.playlist)):
            index = (self.current_index + i) % len(self.playlist)
            if self._playable(self.playlist[index]):
                self.load_song(index)
                return
        
        # No available songs found
        self.stop_song(None)
        self.current_song = None
        self._set_title_text("❌ No available files in playlist")
        self.info_label.set_text("All files are missing - please re-add music files")
    
    def stop_song(self, button):
        self._invalidate_next()
        self._pending_seek_ns = None
        self.player.set_state(Gst.State.NULL)
        self._sync_play_ui(False, stopped=True)
        self.position = 0
        self.position_scale.set_value(0)
        self.time_display.set_text("00:00")
    
    def _invalidate_next(self):
        with self._gapless_lock:
            self._next_generation += 1
            self._next_snapshot = None
            pending = self._gapless_next
            self._gapless_next = None
        # A handed-off URI cannot simply be forgotten: reset the preroll to
        # the current track when an edit supersedes it.
        if pending and self.current_song and not self._destroyed:
            position = self._current_position_ns()
            self.player.set_state(Gst.State.NULL)
            uri = self.current_song if self._is_stream_url(self.current_song) else Gst.filename_to_uri(self.current_song)
            self.player.set_property('uri', uri)
            self._pending_seek_ns = position
            if self.is_playing:
                self.player.set_state(Gst.State.PLAYING)

    def _prepare_next(self):
        if (self._destroyed or not self.gapless or self.repeat_mode == REPEAT_ONE
                or self._sleep_after_track or self._loading or getattr(self, '_switching_output', False)):
            return
        with self._gapless_lock:
            if self._next_snapshot or self._gapless_next:
                return
        key = self.order.next_id(self._playable, self.shuffle, self.repeat_mode)
        if key is None:
            return
        path = dict(self.order.entries)[key]
        uri = path if self._is_stream_url(path) else Gst.filename_to_uri(os.path.abspath(path))
        with self._gapless_lock:
            self._next_snapshot = (key, path, uri, self._next_generation)

    def _refresh_order(self):
        self._invalidate_next()
        self.order.reconcile(zip(self.entry_ids, self.playlist))
        self._title_rows = {}
        for row in self.playlist_store:
            reference = Gtk.TreeRowReference.new(self.playlist_store, row.path)
            self._title_rows.setdefault(row[0], []).append(reference)
        self._playlist_titles = {path: title for path, title in self._playlist_titles.items()
                                 if path in self._title_rows}
        self._play_next = self.order.queue
        self._update_queue_markers()
        self._prepare_next()

    def _remember_playlist(self):
        if self._undoing:
            return
        self._invalidate_next()
        self._undo.append((list(self.playlist), list(self.entry_ids), list(self.order.queue),
                           self._playlist_name))

    def _playlist_edited(self, rebuild=True):
        current = self.order.current
        if rebuild:
            with self._store_guard():
                self.playlist_store.clear()
                for i, (key, path) in enumerate(zip(self.entry_ids, self.playlist)):
                    name = self._display_name(path)
                    if not self._playable(path):
                        name += ' [MISSING]'
                    self.playlist_store.append([path, name, i + 1, self._duration_str(path), key, ''])
        if current in self.entry_ids:
            self.current_index = self.entry_ids.index(current)
        else:
            self.stop_song(None)
            self.current_song = None
            self.order.current = None
            self.current_index = 0
            self._set_title_text(DEFAULT_SONG_TEXT)
            self.info_label.set_text('')
            self._set_album_art(None)
        self._renumber_rows()
        self._refresh_order()
        self.update_playlist_info()
        self.save_playlist()
        self.schedule_save_config()
        self._queue_playlist_metadata()
        self._search_step(True)

    def undo_playlist(self, *_args):
        if not self._undo:
            self.show_drop_feedback('Nothing to undo')
            return
        self._undoing = True
        try:
            self._invalidate_next()
            paths, keys, queued, name = self._undo.pop()
            self.playlist, self.entry_ids = paths, keys
            self.order.queue = deque(queued)
            self._playlist_name = name
            self._playlist_edited()
        finally:
            self._undoing = False
        self.show_drop_feedback('Playlist edit undone')

    def next_song(self, button):
        self._navigate(auto=False)

    def _navigate(self, auto=False, after_error=False):
        was_playing = self.is_playing
        mode = REPEAT_OFF if after_error and self.repeat_mode == REPEAT_ONE else self.repeat_mode
        key = self.order.next_id(self._playable, self.shuffle, mode, auto)
        if key is None:
            self.stop_song(None)
            return
        self.load_song(self.entry_ids.index(key))
        if auto or was_playing:
            self._play_current()

    def previous_song(self, button):
        if not self.playlist:
            return
        was_playing = self.is_playing
        if self.shuffle != SHUFFLE_OFF:
            key = self.order.previous_id(self._playable)
            index = self.entry_ids.index(key) if key in self.entry_ids else None
        else:
            earlier = [i for i in range(self.current_index) if self._playable(self.playlist[i])]
            index = earlier[-1] if earlier else self.current_index
            if not earlier and self.repeat_mode == REPEAT_ALL:
                available = [i for i, path in enumerate(self.playlist) if self._playable(path)]
                index = available[-1] if available else None
        if index is not None:
            self.load_song(index)
            if was_playing:
                self._play_current()

    def _set_title_text(self, text):
        """Central song-title setter: short titles display plainly; long ones
        scroll Winamp-style through a fixed 44-char window."""
        if hasattr(self, 'shade_title'):
            self.shade_title.set_text(text)
            self.shade_title.set_tooltip_text(text)
        self._title_full = text
        if len(text) <= MARQUEE_WINDOW:
            self._marquee_stop()
            self.song_label.set_ellipsize(Pango.EllipsizeMode.END)
            self.song_label.set_text(text)
            return
        # Ellipsize must be off or GTK re-truncates every rotated frame
        self.song_label.set_ellipsize(Pango.EllipsizeMode.NONE)
        self._marquee_circ = text + MARQUEE_SEP
        self._marquee_offset = 0
        self._marquee_hold = MARQUEE_HOLD_TICKS
        self.song_label.set_text(text[:MARQUEE_WINDOW])
        if getattr(self, '_marquee_id', None) is None:
            self._marquee_id = self.tasks.timeout_add(MARQUEE_TICK_MS, self._marquee_tick)

    def _marquee_tick(self):
        if self._marquee_hold > 0:
            self._marquee_hold -= 1
            return True
        circ = self._marquee_circ
        self._marquee_offset = (self._marquee_offset + 1) % len(circ)
        if self._marquee_offset == 0:
            self._marquee_hold = MARQUEE_HOLD_TICKS
        window = (circ + circ)[self._marquee_offset:self._marquee_offset + MARQUEE_WINDOW]
        self.song_label.set_text(window)
        return True

    def _marquee_stop(self):
        if getattr(self, '_marquee_id', None) is not None:
            self.tasks.source_remove(self._marquee_id)
            self._marquee_id = None

    def _refresh_song_label(self):
        """Set the song label from cached tag metadata, else the filename stem."""
        if not self.current_song:
            self._set_title_text(DEFAULT_SONG_TEXT)
            return
        cached = self._cache_get(self._meta_cache, self.current_song)
        if isinstance(cached, dict) and cached.get('title'):
            artist = cached.get('artist')
            title = cached['title']
            self._set_title_text(f"{artist} - {title}" if artist else title)
        else:
            self._set_title_text(self._display_name(self.current_song))

    def _select_row(self, index):
        selection = self.playlist_view.get_selection()
        selection.unselect_all()
        selection.select_path(Gtk.TreePath(index))
        self.playlist_view.scroll_to_cell(Gtk.TreePath(index), None, True, 0.5, 0.0)

    def load_song(self, index):
        if not (0 <= index < len(self.playlist)):
            return
        # New load generation: stale async results (probes, art, bus tags) are ignored
        self._load_gen += 1
        self._invalidate_next()
        self.analyzer_state.reset()
        self._pending_seek_ns = None
        self._gapless_next = None   # manual load supersedes any prerolled next track
        was_loading = self._loading
        self._loading = True        # freeze about-to-finish while we rebuild
        try:
            file_path = self.playlist[index]
            self.current_index = index
            # Reset per-track state so nothing stale leaks across tracks
            self.duration = 0
            self.position = 0
            self.position_scale.set_value(0)
            self.time_display.set_text("00:00")

            if self._is_stream_url(file_path):
                # Internet radio: the URL is the URI; no existence check, no
                # seeking, duration unknown until (never, for live streams)
                self.player.set_state(Gst.State.NULL)
                bus = self.player.get_bus()
                bus.set_flushing(True)
                bus.set_flushing(False)
                self._awaiting_own_start = True
                self.player.set_property("uri", file_path)
                self.current_song = file_path
                self._loaded_uri = file_path
                self._stream_meta = {}
                self.position_scale.set_sensitive(False)
                self._post_load_ui(index, file_path)
                return

            self.position_scale.set_sensitive(True)
            if not os.path.exists(file_path):
                self.current_song = None
                self._loaded_uri = None
                self.player.set_state(Gst.State.NULL)
                self._sync_play_ui(False, stopped=True)
                song_name = self._display_name(file_path)
                self._set_title_text(f"❌ File not found: {song_name}")
                self.info_label.set_text("File missing - please re-add to playlist")
                self._set_album_art(None)
                self.update_missing_file_in_playlist(index)
                self._select_row(index)
                self.schedule_save_config()
                return

            uri = Gst.filename_to_uri(os.path.abspath(file_path))  # encodes spaces/#/unicode
            self.player.set_state(Gst.State.NULL)  # Reset player state
            # Drop bus messages queued by the previous track so its late tags/art
            # can't be attributed to this one.
            bus = self.player.get_bus()
            bus.set_flushing(True)
            bus.set_flushing(False)
            self._awaiting_own_start = True
            self.player.set_property("uri", uri)
            self.current_song = file_path
            self._loaded_uri = uri
            self._post_load_ui(index, file_path)
        finally:
            self._loading = was_loading
            self._prepare_next()

    def _post_load_ui(self, index, file_path):
        """Refresh everything that presents the current track (shared between
        load_song and the gapless handoff commit)."""
        self.order.select(self.entry_ids[index])
        if self.is_playing:
            self.order.commit(self.order.current)
        self._refresh_song_label()
        self._update_album_art(file_path)
        self.get_audio_properties(file_path)
        self.update_audio_display(file_path)
        self._select_row(index)
        self.schedule_save_config()
        self._scrobble_reset()
        self._mpris_notify_track()
        self._notify_track(getattr(self, '_title_full', '') or self._display_name(file_path))
        self._update_queue_markers()
        self._prepare_next()

    def _mpris_notify_track(self):
        self._mpris_emit({
            'Metadata': self._mpris_metadata(),
            'PlaybackStatus': GLib.Variant('s', self._mpris_status()),
        })
    
    def update_missing_file_in_playlist(self, index):
        """Update the playlist display to show a file as missing"""
        if 0 <= index < len(self.playlist):
            file_path = self.playlist[index]
            song_name = self._display_name(file_path)
            missing_display = f"❌ {song_name} [MISSING]"
            
            # Update the playlist store display
            tree_iter = self.playlist_store.get_iter(Gtk.TreePath(index))
            self.playlist_store.set_value(tree_iter, 1, missing_display)
    
    def toggle_shuffle(self, button):
        self._invalidate_next()
        # Cycle OFF -> TRACKS -> ALBUMS -> OFF
        self.shuffle = (self.shuffle + 1) % 3
        self.update_shuffle_button()
        self.schedule_save_config()
        if self.current_song:
            self.update_audio_display()
        self._mpris_emit({'Shuffle': GLib.Variant('b', self.shuffle != SHUFFLE_OFF)})

    def toggle_repeat(self, button):
        self._invalidate_next()
        # Cycle OFF -> ALL -> ONE -> OFF
        self.repeat_mode = (self.repeat_mode + 1) % 3
        self.update_repeat_button()
        self.schedule_save_config()
        if self.current_song:
            self.update_audio_display()
        loop = {REPEAT_OFF: 'None', REPEAT_ALL: 'Playlist', REPEAT_ONE: 'Track'}
        self._mpris_emit({'LoopStatus': GLib.Variant('s', loop[self.repeat_mode])})
    
    # File management
    def _parse_m3u(self, m3u_path):
        """Read an M3U/M3U8 playlist: skip comments, resolve relative entries
        against the playlist's own directory."""
        base = os.path.dirname(os.path.abspath(m3u_path))
        entries = []
        try:
            with open(m3u_path, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if self._is_stream_url(line):
                        entries.append(line)
                        continue
                    if line.startswith('file://'):
                        try:
                            line = GLib.filename_from_uri(line)[0]
                        except Exception:
                            continue
                    if not os.path.isabs(line):
                        line = os.path.normpath(os.path.join(base, line))
                    entries.append(line)
        except Exception as e:
            self.log_debug(f"m3u parse failed: {e}")
        return entries

    def add_files(self, button):
        dialog = Gtk.FileChooserDialog(
            title="Add Music Files",
            parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        dialog.set_select_multiple(True)

        # Audio file filter
        filter_audio = Gtk.FileFilter()
        filter_audio.set_name("Audio files")
        filter_audio.add_mime_type("audio/*")
        dialog.add_filter(filter_audio)

        # Playlist import filter
        filter_m3u = Gtk.FileFilter()
        filter_m3u.set_name("Playlists (*.m3u, *.m3u8)")
        filter_m3u.add_pattern("*.m3u")
        filter_m3u.add_pattern("*.m3u8")
        dialog.add_filter(filter_m3u)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            paths = []
            for filename in dialog.get_filenames():
                if filename.lower().endswith(('.m3u', '.m3u8')):
                    paths.extend(self._parse_m3u(filename))
                else:
                    paths.append(filename)
            self._add_paths(paths)
        dialog.destroy()

    def add_folder(self, button):
        """Add every audio file under a chosen folder (recursive). The walk runs
        off-thread so a huge or slow tree can't freeze the UI."""
        dialog = Gtk.FileChooserDialog(
            title="Add Music Folder",
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        response = dialog.run()
        folder = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        if not folder:
            return

        def walk():
            found = []
            for root, dirs, files in os.walk(folder):
                dirs.sort()
                for f in sorted(files):
                    p = os.path.join(root, f)
                    if self.is_audio_file(p):
                        found.append(p)
            self.tasks.idle_add(self._folder_walk_done, folder, found)

        threading.Thread(target=walk, daemon=True).start()

    def _folder_walk_done(self, folder, found):
        if found:
            self._add_paths(found, feedback=f"Added {len(found)} file(s)")
        else:
            self.show_drop_feedback(f"No audio files in {os.path.basename(folder)}")
        return False

    def remove_missing(self, button):
        keep = [(key, path) for key, path in zip(self.entry_ids, self.playlist) if self._playable(path)]
        removed = len(self.playlist) - len(keep)
        if not removed:
            self.show_drop_feedback('No missing files')
            return
        self._remember_playlist()
        self.entry_ids = [key for key, _ in keep]
        self.playlist = [path for _, path in keep]
        self._playlist_edited()
        self.show_drop_feedback(f'Removed {removed} missing file(s) — Ctrl+Z to undo')

    def export_m3u(self, button):
        """Export the playlist as an extended M3U file."""
        if not self.playlist:
            return
        dialog = Gtk.FileChooserDialog(
            title="Export Playlist",
            parent=self,
            action=Gtk.FileChooserAction.SAVE
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK
        )
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name("playlist.m3u")
        response = dialog.run()
        target = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        if not target:
            return
        try:
            self._write_m3u(target)
            self.show_drop_feedback(f"Exported {len(self.playlist)} track(s)")
        except Exception as e:
            self.log_debug(f"m3u export failed: {e}")
            self.show_drop_feedback("Export failed")

    def _write_m3u(self, target):
        lines = ["#EXTM3U"]
        for p in self.playlist:
            secs = self._duration_seconds(p) or -1
            lines.append(f"#EXTINF:{secs},{self._display_name(p)}")
            lines.append(p)
        self._atomic_write(target, "\n".join(lines) + "\n")

    # ---- Named playlists ----

    def playlists_dir(self):
        d = os.path.join(self._data_dir(), "playlists")
        os.makedirs(d, exist_ok=True)
        return d

    def _saved_playlist_names(self):
        try:
            return sorted(os.path.splitext(f)[0] for f in os.listdir(self.playlists_dir())
                          if f.lower().endswith('.m3u'))
        except OSError:
            return []

    def _do_save_playlist_as(self, name):
        name = re.sub(r'[/\\\0]', '', name).strip()
        if not name or not self.playlist:
            return False
        self._write_m3u(os.path.join(self.playlists_dir(), f"{name}.m3u"))
        self._playlist_name = name
        self.schedule_save_config()
        self.show_drop_feedback(f"Saved playlist '{name}'")
        return True

    def open_url_dialog(self, *_args):
        """Open an internet-radio / stream URL (Ctrl+L)."""
        dialog = Gtk.Dialog(title="Open URL", transient_for=self, modal=True)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_placeholder_text("https://…")
        entry.set_width_chars(46)
        entry.set_activates_default(True)
        dialog.set_default_response(Gtk.ResponseType.OK)
        # Convenience: prefill from clipboard when it looks like a URL
        try:
            clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text()
            if clip and self._is_stream_url(clip.strip()):
                entry.set_text(clip.strip())
        except Exception:
            pass
        box = dialog.get_content_area()
        box.set_margin_top(10); box.set_margin_bottom(10)
        box.set_margin_start(10); box.set_margin_end(10)
        box.add(Gtk.Label(label="Stream URL (SHOUTcast / Icecast / direct):"))
        box.add(entry)
        dialog.show_all()
        response = dialog.run()
        url = entry.get_text().strip()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not url:
            return
        if not self._is_stream_url(url):
            self.show_drop_feedback("Not an http(s) URL")
            return
        self._add_paths([url])
        self._play_index(len(self.playlist) - 1)

    def _save_playlist_as(self, *_args):
        dialog = Gtk.Dialog(title="Save Playlist As", transient_for=self, modal=True)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_text(self._playlist_name or "")
        entry.set_activates_default(True)
        dialog.set_default_response(Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_margin_top(10); box.set_margin_bottom(10)
        box.set_margin_start(10); box.set_margin_end(10)
        box.add(Gtk.Label(label="Playlist name:"))
        box.add(entry)
        dialog.show_all()
        response = dialog.run()
        name = entry.get_text()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            self._do_save_playlist_as(name)

    def _save_playlist_named(self, *_args):
        if self._playlist_name:
            self._do_save_playlist_as(self._playlist_name)

    def _load_named_playlist(self, name):
        entries = self._parse_m3u(os.path.join(self.playlists_dir(), f'{name}.m3u'))
        if not entries:
            self.show_drop_feedback(f"Playlist '{name}' is empty/unreadable")
            return
        self._remember_playlist()
        self.playlist = entries
        self.entry_ids = [uuid.uuid4().hex for _ in entries]
        self.order.queue.clear()
        self._playlist_name = name
        self._playlist_edited()

    def remove_selected(self, button):
        model, paths = self.playlist_view.get_selection().get_selected_rows()
        if not paths:
            return
        self._remember_playlist()
        for index in sorted((p.get_indices()[0] for p in paths), reverse=True):
            del self.playlist[index]
            del self.entry_ids[index]
        self._playlist_edited()
        self.show_drop_feedback('Removed from playlist — Ctrl+Z to undo')

    def clear_playlist(self, button):
        if not self.playlist:
            return
        self._remember_playlist()
        self.playlist.clear()
        self.entry_ids.clear()
        self.order.queue.clear()
        self._playlist_edited()
        self.show_drop_feedback('Playlist cleared — Ctrl+Z to undo')

    def update_playlist_info(self):
        count = len(self.playlist)
        base = "1 track" if count == 1 else f"{count} tracks"
        known = [self._duration_seconds(p) for p in self.playlist]
        known = [s for s in known if s]
        if count > 0 and len(known) >= max(1, int(count * 0.9)):
            self.playlist_info.set_text(f"{base} · {self._fmt_total(sum(known))}")
        else:
            self.playlist_info.set_text(base)

    def on_store_rows_changed(self, *args):
        """Fires on user drag-reorder (and programmatic edits we suppress)."""
        if self._suppress_store or self._reordering:
            return
        self._reordering = True
        self.tasks.idle_add(self._resync_playlist_from_store)

    def _zebra_bg(self, column, cell, model, it, data):
        path = model.get_path(it)
        if self.playlist_view.get_selection().path_is_selected(path):
            cell.set_property('cell-background-set', False)
        else:
            cell.set_property('cell-background', self.theme['stripe'] if path.get_indices()[0] % 2 else self.theme['lcd'])
        cell.set_property('weight', 700 if model.get_value(it, 4) == self.order.current else 400)

    def _renumber_rows(self):
        """Rewrite the track-number column (col 2) to match current row order."""
        it = self.playlist_store.get_iter_first()
        n = 0
        while it is not None:
            n += 1
            self.playlist_store.set_value(it, 2, n)
            it = self.playlist_store.iter_next(it)

    def _resync_playlist_from_store(self):
        self._reordering = False
        if self._destroyed:
            return False
        self._remember_playlist()
        self.playlist = [row[0] for row in self.playlist_store]
        self.entry_ids = [row[4] for row in self.playlist_store]
        self._playlist_edited(rebuild=False)
        return False

    def show_jump_dialog(self, *_args):
        """Winamp 'J' jump-to-file: type to filter, Enter plays,
        Shift+Enter queues (Play Next), Esc closes."""
        dialog = Gtk.Dialog(title="Jump to File", transient_for=self, modal=True)
        dialog.set_default_size(420, 320)
        box = dialog.get_content_area()
        box.set_margin_top(8); box.set_margin_bottom(8)
        box.set_margin_start(8); box.set_margin_end(8)
        box.set_spacing(6)

        entry = Gtk.SearchEntry()
        entry.set_placeholder_text("Type to filter…  (Enter: play · Shift+Enter: queue)")
        box.pack_start(entry, False, False, 0)

        store = Gtk.ListStore(int, str)   # playlist index, display
        view = Gtk.TreeView(model=store)
        view.get_style_context().add_class('playlist')
        view.set_headers_visible(False)
        view.append_column(Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=1))
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(view)
        box.pack_start(scroller, True, True, 0)

        def refilter(*_a):
            query = entry.get_text().strip().lower()
            store.clear()
            for i, row in enumerate(self.playlist_store):
                if not query or query in row[1].lower():
                    store.append([i, row[1]])
            if len(store):
                view.get_selection().select_path(Gtk.TreePath(0))

        def selected_index():
            model, it = view.get_selection().get_selected()
            return model.get_value(it, 0) if it else None

        def move_selection(delta):
            model, it = view.get_selection().get_selected()
            if it is None:
                return
            pos = model.get_path(it).get_indices()[0] + delta
            pos = max(0, min(len(store) - 1, pos))
            view.get_selection().select_path(Gtk.TreePath(pos))
            view.scroll_to_cell(Gtk.TreePath(pos), None, False, 0, 0)

        def on_key(_w, event):
            if event.keyval == Gdk.KEY_Escape:
                dialog.destroy()
                return True
            if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                idx = selected_index()
                if idx is not None:
                    if event.state & Gdk.ModifierType.SHIFT_MASK:
                        self._queue_paths([self.playlist[idx]])
                    else:
                        self._play_index(idx)
                dialog.destroy()
                return True
            if event.keyval == Gdk.KEY_Up:
                move_selection(-1)
                return True
            if event.keyval == Gdk.KEY_Down:
                move_selection(1)
                return True
            return False

        entry.connect("changed", refilter)
        dialog.connect("key-press-event", on_key)
        view.connect("row-activated",
                     lambda *_a: (self._play_index(selected_index()), dialog.destroy()))
        refilter()
        dialog.show_all()
        entry.grab_focus()
        self._jump_dialog = dialog  # keep referenced while open

    def _search_step(self, restart, select=True):
        query = self.search_entry.get_text().strip().casefold()
        context = self.search_entry.get_style_context()
        context.remove_class('search-miss')
        matches = [i for i, row in enumerate(self.playlist_store) if query in row[1].casefold()] if query else []
        if not query:
            self.search_count.set_text('')
            return
        if not matches:
            self.search_count.set_text('No matches')
            context.add_class('search-miss')
            return
        if not select:
            self.search_count.set_text(f'{matches.index(self._search_pos) + 1}/{len(matches)}'
                                       if self._search_pos in matches else f'{len(matches)} matches')
            return
        index = matches[0] if restart else next((i for i in matches if i > self._search_pos), matches[0])
        self._search_pos = index
        self.search_count.set_text(f'{matches.index(index) + 1}/{len(matches)}')
        self._select_row(index)

    def _search_escape(self, entry):
        entry.set_text("")
        self._search_pos = -1
        self.playlist_view.grab_focus()

    def _update_queue_markers(self):
        if not hasattr(self, 'playlist_store'):
            return
        queued = {key: i + 1 for i, key in enumerate(self.order.queue)}
        for row in self.playlist_store:
            playing = {'Playing': '▶', 'Paused': 'Ⅱ', 'Stopped': ''}[self.playback_state]
            marker = playing if row[4] == self.order.current and self.current_song else ''
            if row[4] in queued:
                marker += f' [{queued[row[4]]}]'
            row[5] = marker

    def _playlist_tooltip(self, view, x, y, keyboard, tooltip):
        if keyboard:
            model, paths = view.get_selection().get_selected_rows()
            path = paths[0] if paths else None
        else:
            x, y = view.convert_widget_to_bin_window_coords(x, y)
            hit = view.get_path_at_pos(x, y)
            path = hit[0] if hit else None
            model = view.get_model()
        if path is None:
            return False
        tooltip.set_text(model[path][0])
        view.set_tooltip_row(tooltip, path)
        return True

    def on_playlist_button_press(self, view, event):
        """Right-click context menu with queue actions."""
        if event.button != 3:
            return False
        hit = view.get_path_at_pos(int(event.x), int(event.y))
        if hit is None:
            return True
        path = hit[0]
        selection = view.get_selection()
        if not selection.path_is_selected(path):
            selection.unselect_all()
            selection.select_path(path)
        model, paths = selection.get_selected_rows()
        sel_paths = [model.get_value(model.get_iter(p), 4) for p in paths]

        menu = Gtk.Menu()
        play_item = Gtk.MenuItem(label="Play Now")
        play_item.connect("activate",
                          lambda _w, i=paths[0].get_indices()[0]: self._play_index(i))
        menu.append(play_item)

        next_item = Gtk.MenuItem(label="Play Next")
        next_item.connect("activate",
                          lambda _w, ps=sel_paths: self._queue_paths(ps))
        menu.append(next_item)

        unq = [p for p in sel_paths if p in self._play_next]
        unq_item = Gtk.MenuItem(label="Remove from Queue")
        unq_item.set_sensitive(bool(unq))
        unq_item.connect("activate",
                         lambda _w, ps=unq: self._unqueue_paths(ps))
        menu.append(unq_item)

        menu.append(Gtk.SeparatorMenuItem())
        rm_item = Gtk.MenuItem(label="Remove")
        rm_item.connect("activate", lambda _w: self.remove_selected(None))
        menu.append(rm_item)

        menu.show_all()
        self._playlist_menu = menu  # keep referenced while open
        menu.popup_at_pointer(event)
        return True

    def _play_current(self):
        """Set PLAYING and record whether the pipeline is live (NO_PREROLL) —
        live streams must not be paused by the buffering handler."""
        if not self.current_song:
            return
        with self._gapless_lock:
            self._next_snapshot = None
        self.order.commit(self.entry_ids[self.current_index])
        ret = self.player.set_state(Gst.State.PLAYING)
        self._pipeline_live = (ret == Gst.StateChangeReturn.NO_PREROLL)
        self._sync_play_ui(True)
        self._prepare_next()

    def _play_index(self, index):
        if 0 <= index < len(self.entry_ids):
            self.order.failed.discard(self.entry_ids[index])
        self.load_song(index)
        self._play_current()

    def _queue_paths(self, entry_ids):
        for key in entry_ids:
            if key in self.entry_ids and key not in self.order.queue:
                self.order.queue.append(key)
        self._refresh_order()
        self.show_drop_feedback(f'Queued {len(entry_ids)} track(s)')

    def _unqueue_paths(self, entry_ids):
        self.order.queue = deque(key for key in self.order.queue if key not in entry_ids)
        self._refresh_order()

    def on_playlist_activated(self, treeview, path, column):
        self._play_index(path.get_indices()[0])

    def on_playlist_selection_changed(self, selection):
        # Selection belongs to playlist editing; the LCD belongs to playback.
        return

    def on_playlist_key_press(self, widget, event):
        """Handle keyboard events in the playlist"""
        # Check for Delete or Backspace keys
        if event.keyval == Gdk.KEY_Delete or event.keyval == Gdk.KEY_BackSpace:
            self.remove_selected(None)  # Pass None for button parameter
            return True  # Event handled
        return False  # Event not handled
    
    # Slider callbacks
    def format_volume_value(self, scale, value):
        """Format volume value as percentage"""
        return f"{int(value * 100)}%"
    
    def format_balance_value(self, scale, value):
        """Format balance value as percentage with L/R indication"""
        if value < 0:
            return f"L{int(abs(value) * 100)}%"
        elif value > 0:
            return f"R{int(value * 100)}%"
        else:
            return "CENTER"
    
    def on_volume_changed(self, scale):
        self.volume = scale.get_value()
        self.player.set_property("volume", self.volume)
        self.schedule_save_config()
        self._mpris_emit({'Volume': GLib.Variant('d', float(self.volume))})
    
    def on_position_pressed(self, scale, event):
        self.seeking = True
    
    def on_position_released(self, scale, event):
        position = scale.get_value()
        # Query fresh: self.duration may be stale/zero when paused on a new track
        ok, duration = self.player.query_duration(Gst.Format.TIME)
        if not ok or duration <= 0:
            duration = self.duration
        if duration > 0:
            seek_time = (position / 100.0) * duration
            self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, seek_time)
            self._mpris_notify_seeked(int(seek_time))
        self.seeking = False

    # Update methods
    def update_position(self):
        if self.is_playing and not self.seeking:
            ok_pos, position = self.player.query_position(Gst.Format.TIME)
            duration = self.duration  # cached on the PLAYING transition
            if (ok_pos and position >= 0 and duration <= 0
                    and self.current_song and self._is_stream_url(self.current_song)):
                # Live stream: show elapsed listen time; slider stays put
                pos_sec = position // Gst.SECOND
                if pos_sec != getattr(self, '_last_pos_sec', None):
                    self._last_pos_sec = pos_sec
                    self.time_display.set_text(f"{pos_sec // 60:01d}:{pos_sec % 60:02d}")
            elif ok_pos and position >= 0 and duration > 0:
                self.position = position

                pos_sec = position // Gst.SECOND
                if pos_sec != getattr(self, '_last_pos_sec', None):
                    self._last_pos_sec = pos_sec
                    self.time_display.set_text(self._fmt_clock(position, duration))

                progress = (position / duration) * 100
                # Skip sub-pixel slider updates (~340px trough → 0.25% steps)
                if abs(progress - getattr(self, '_last_progress', -1)) >= 0.25:
                    self._last_progress = progress
                    self.position_scale.set_value(progress)
        self._scrobble_tick()
        # Song-end is handled by the real EOS bus message (on_bus_eos), not polling.
        return True

    def _toggle_time_mode(self, *_args):
        """Flip the clock between elapsed and -remaining (click, Winamp-style)."""
        self._time_remaining = not self._time_remaining
        self._last_pos_sec = None   # force an immediate re-render next tick
        # Re-render right away, even while paused
        if self.duration > 0 and self.position >= 0:
            self.time_display.set_text(self._fmt_clock(self.position, self.duration))
        self.schedule_save_config()
        return True

    def _fmt_clock(self, position_ns, duration_ns):
        if self._time_remaining and duration_ns > 0:
            sec = max(0, (duration_ns - position_ns) // Gst.SECOND)
            return f"-{sec // 60:01d}:{sec % 60:02d}"
        sec = position_ns // Gst.SECOND
        return f"{sec // 60:01d}:{sec % 60:02d}"

    def _start_decay(self):
        if self._analyzer_id is None and self._analyzer_visible():
            self._analyzer_id = self.tasks.timeout_add(33, self.animate_equalizer)

    def animate_equalizer(self):
        if self._destroyed or not self._analyzer_visible():
            self._analyzer_id = None
            return False
        speed = {'slow': .5, 'normal': 1, 'fast': 2}[self.config['falloff']]
        if self.analyzer_state.tick(time.monotonic(), self.is_playing, speed):
            self.analyzer.queue_draw()
        alive = any(self.analyzer_state.levels) or any(self.analyzer_state.peaks)
        if not alive:
            self._analyzer_id = None
        return alive

    def _analyzer_visible(self):
        return (not self._destroyed and hasattr(self, 'analyzer') and self.analyzer.get_mapped()
                and self.config.get('visualization', True) and not self._windowshade
                and not getattr(self, '_iconified', False))

    def _update_analyzer_visibility(self):
        visible = self._analyzer_visible()
        if self.spectrum:
            self.spectrum.set_property('post-messages', visible)
        if not visible:
            if self._analyzer_id is not None:
                self.tasks.source_remove(self._analyzer_id)
                self._analyzer_id = None
            self.analyzer_state.reset()
            if hasattr(self, 'analyzer'):
                self.analyzer.queue_draw()
        return False

    def _draw_analyzer(self, widget, cr):
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        palette = self.config.get('palette') or self.theme['palette']
        scale = widget.get_scale_factor()
        key = (width, height, palette, self.theme['lcd'], scale)
        if getattr(self, '_meter_cache_key', None) != key:
            # Two tiny cached surfaces replace hundreds of Cairo fills per
            # frame. Only level clipping and peak positions change with audio.
            surfaces = []
            for lit in (False, True):
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width * scale, height * scale)
                surface.set_device_scale(scale, scale)
                ctx = cairo.Context(surface)
                self._cairo_color(ctx, self.theme['lcd'])
                ctx.paint()
                for y in range(height - 3, 0, -4):
                    fraction = (height - y) / height
                    if palette == 'classic':
                        color = '#ed644d' if fraction > .82 else '#eee85b' if fraction > .6 else '#65ed48'
                    else:
                        color = '#ffba45' if palette == 'amber' else '#52ef34'
                    self._cairo_color(ctx, color, 1 if lit else .08)
                    for index in range(DISPLAY_BANDS):
                        ctx.rectangle(int(index * width / DISPLAY_BANDS) + 1, y,
                                      max(1, int(width / DISPLAY_BANDS) - 2), 2)
                    ctx.fill()
                surfaces.append(surface)
            self._meter_cache_key, self._meter_surfaces = key, surfaces
        if not self.config.get('visualization', True):
            self._cairo_color(cr, self.theme['lcd'])
            cr.paint()
            return False
        background, lit = self._meter_surfaces
        cr.set_source_surface(background)
        cr.paint()
        step = width / DISPLAY_BANDS
        cr.save()
        for index, level in enumerate(self.analyzer_state.levels):
            if level > 0:
                top = height - int(level * height)
                cr.rectangle(int(index * step), top, int(step) + 1, height - top)
        cr.clip()
        cr.set_source_surface(lit)
        cr.paint()
        cr.restore()
        if self.config.get('peaks', True):
            self._cairo_color(cr, '#fff2d0' if palette == 'amber' else '#e8ffdc')
            for index, peak in enumerate(self.analyzer_state.peaks):
                if peak > 0:
                    cr.rectangle(int(index * step) + 1, max(0, int((1 - peak) * (height - 2))),
                                 max(1, int(step) - 2), 2)
            cr.fill()
        return False

    def _clock_press(self, widget, event):
        if event.button == 1 and event.type == Gdk.EventType.BUTTON_PRESS:
            # GDK sends a second ordinary press before DOUBLE_BUTTON_PRESS.
            last = getattr(self, '_clock_last_click', None)
            interval = Gtk.Settings.get_default().get_property('gtk-double-click-time')
            if last is None or event.time - last > interval:
                self._toggle_time_mode()
            self._clock_last_click = event.time
            return True
        return False

    def get_audio_properties(self, file_path):
        self.audio_properties = {'sample_rate': 0, 'bitrate': 0, 'channels': 0}
        if self._is_stream_url(file_path):
            return
        cached = self._cache_get(self._meta_cache, file_path)
        if isinstance(cached, dict):
            self.audio_properties.update(cached)
            return
        if cached is False or file_path in self._probe_inflight:
            return
        self._probe_inflight.add(file_path)
        self._probe_queue.put(('meta', file_path, dict(self.audio_properties)))

    def _probe_one(self, file_path, base_props):
        """Probe one file with GstDiscoverer (worker thread; no GTK calls)."""
        props = {}
        art_bytes = None
        try:
            disc = GstPbutils.Discoverer.new(3 * Gst.SECOND)
            info = disc.discover_uri(Gst.filename_to_uri(os.path.abspath(file_path)))
            streams = info.get_audio_streams()
            if streams:
                a = streams[0]
                props['sample_rate'] = a.get_sample_rate() or 0
                props['channels'] = a.get_channels() or 0
                br = a.get_bitrate() or 0
                if br > 0:
                    props['bitrate'] = br // 1000
            dur = info.get_duration()
            if dur and dur > 0:
                props['duration'] = dur // Gst.SECOND
            tags = info.get_tags()
            if tags:
                ok, title = tags.get_string(Gst.TAG_TITLE)
                ok2, artist = tags.get_string(Gst.TAG_ARTIST)
                if ok and title:
                    props['title'] = title
                if ok2 and artist:
                    props['artist'] = artist
                ok, sample = tags.get_sample(Gst.TAG_IMAGE)
                if not ok:
                    ok, sample = tags.get_sample(Gst.TAG_PREVIEW_IMAGE)
                if ok and sample:
                    art_bytes = self._sample_to_bytes(sample)
        except Exception as e:
            self.log_debug(f"discover failed for {os.path.basename(file_path)}: {e}")
        result = {**base_props, **props} if props else False
        self.tasks.idle_add(self._probe_done, file_path, result, art_bytes)

    def _update_playlist_title(self, file_path, title):
        references = self._title_rows.get(file_path)
        if not references:
            return
        self._playlist_titles[file_path] = title.strip() if isinstance(title, str) and title.strip() else None
        display = self._display_name(file_path)
        if not self._playable(file_path):
            display += ' [MISSING]'
        changed = False
        for reference in references:
            path = reference.get_path()
            if path is not None and self.playlist_store[path][0] == file_path:
                if self.playlist_store[path][1] != display:
                    self.playlist_store[path][1] = display
                    changed = True
        if changed and self.search_entry.get_text():
            self._search_step(False, select=False)

    def _probe_done(self, file_path, result, art_bytes):
        """Store a probe result on the UI thread; apply if still relevant.
        result is a props dict, or False to negative-cache a failed probe."""
        if self._destroyed:
            return False
        self._probe_inflight.discard(file_path)
        self._cache_put(self._meta_cache, file_path, result)
        title = result.get('title') if isinstance(result, dict) else None
        self._update_playlist_title(file_path, title)
        if isinstance(result, dict) and result.get('duration'):
            self._note_duration(file_path, result['duration'])
        if art_bytes and self._cache_get(self._art_cache, file_path) is None:
            self._apply_art_bytes(file_path, art_bytes)
        if not isinstance(result, dict):
            return False
        if file_path != self.current_song:
            return False
        for k in ('sample_rate', 'bitrate', 'channels'):
            if k in result:
                self.audio_properties[k] = result[k]
        if result.get('title') and file_path == self.current_song:
            self._refresh_song_label()
            self._mpris_notify_track()
        self.update_audio_display()
        return False

    def _sample_to_bytes(self, sample):
        """Extract raw image bytes from a Gst.Sample (embedded cover art tag)."""
        try:
            buf = sample.get_buffer()
            ok, mapinfo = buf.map(Gst.MapFlags.READ)
            if not ok:
                return None
            try:
                return bytes(mapinfo.data)
            finally:
                buf.unmap(mapinfo)
        except Exception as e:
            self.log_debug(f"art sample read failed: {e}")
            return None

    def _decode_art_pixbuf(self, data):
        """Decode image bytes into a pixbuf scaled to fit ALBUM_ART_SIZE."""
        try:
            loader = GdkPixbuf.PixbufLoader()
            try:
                loader.write(data)
            finally:
                loader.close()
            pixbuf = loader.get_pixbuf()
            if not pixbuf:
                return None
            w, h = pixbuf.get_width(), pixbuf.get_height()
            scale = ALBUM_ART_SIZE / max(w, h)
            if scale < 1:
                pixbuf = pixbuf.scale_simple(max(1, round(w * scale)),
                                             max(1, round(h * scale)),
                                             GdkPixbuf.InterpType.BILINEAR)
            return pixbuf
        except Exception as e:
            self.log_debug(f"art decode failed: {e}")
            return None

    def _apply_art_bytes(self, file_path, data):
        """Decode embedded art on the UI thread, cache it, show if still current."""
        pixbuf = self._decode_art_pixbuf(data)
        if pixbuf:
            self._cache_put(self._art_cache, file_path, pixbuf)
            if file_path == self.current_song:
                self._set_album_art(pixbuf)
        return False

    def _folder_art_scan(self, directory):
        """Scan a directory for cover art (worker thread; pure I/O + decode)."""
        pixbuf = None
        try:
            for entry in sorted(os.listdir(directory)):
                stem, ext = os.path.splitext(entry)
                if stem.lower() in FOLDER_ART_NAMES and ext.lower() in FOLDER_ART_EXTS:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        os.path.join(directory, entry),
                        ALBUM_ART_SIZE, ALBUM_ART_SIZE, True)
                    break
        except Exception as e:
            # Transient failure (unmounted volume, permissions): don't cache,
            # so a later attempt can succeed.
            self.log_debug(f"folder art scan failed: {e}")
            return
        self.tasks.idle_add(self._folder_art_done, directory, pixbuf)

    def _folder_art_done(self, directory, pixbuf):
        if self._destroyed:
            return False
        """Cache a completed folder scan; show it if still relevant (UI thread)."""
        self._cache_put(self._folder_art_cache, directory, pixbuf,
                        FOLDER_ART_CACHE_LIMIT)
        if (pixbuf is not None and self.current_song
                and os.path.dirname(os.path.abspath(self.current_song)) == directory
                and self._cache_get(self._art_cache, self.current_song) is None):
            self._set_album_art(pixbuf)
        return False

    def _update_album_art(self, file_path):
        """Show the best art known right now: embedded (cached) else cached folder
        art. On a folder-cache miss, show nothing and queue an off-thread scan —
        never block the UI on directory I/O (network mounts)."""
        if not file_path:
            self._set_album_art(None)
            return
        if self._is_stream_url(file_path):
            self._set_album_art(None)   # placeholder
            return
        pixbuf = self._cache_get(self._art_cache, file_path)
        if pixbuf is None:
            directory = os.path.dirname(os.path.abspath(file_path))
            if directory in self._folder_art_cache:
                pixbuf = self._cache_get(self._folder_art_cache, directory)
            else:
                self._probe_queue.put(('folderart', directory))
        self._set_album_art(pixbuf)

    def _get_default_art(self):
        """Placeholder art (the llama app icon, desaturated) for tracks with
        no embedded or folder art. Loaded once; None if the icon is missing."""
        if self._default_art_loaded:
            return self._default_art
        self._default_art_loaded = True
        candidates = (
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "llama-amp.svg"),
            "/usr/share/icons/hicolor/scalable/apps/llama-amp.svg",  # installed
        )
        for p in candidates:
            if not os.path.exists(p):
                continue
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    p, ALBUM_ART_SIZE, ALBUM_ART_SIZE, True)
                dim = pb.copy()
                pb.saturate_and_pixelate(dim, 0.25, False)  # muted = "placeholder"
                self._default_art = dim
                break
            except Exception as e:
                self.log_debug(f"default art load failed: {e}")
        return self._default_art

    def _set_album_art(self, pixbuf):
        if not self.config.get('show_art', True):
            self.album_art.hide()
            return
        if pixbuf is None:
            pixbuf = self._get_default_art()
        if pixbuf:
            self.album_art.set_from_pixbuf(pixbuf)
            self.album_art.show()
        else:
            self.album_art.clear()
            self.album_art.hide()

    def update_audio_display(self, file_path=None):
        """Update the audio information display with current properties"""
        if not self.audio_properties:
            return
            
        sample_rate = self.audio_properties['sample_rate']
        bitrate = self.audio_properties['bitrate']
        channels = self.audio_properties['channels']
        
        # Convert sample rate to kHz
        sample_rate_khz = f'{sample_rate / 1000:g}' if sample_rate else '—'
        
        # Get dynamic file type from current song or provided file path
        file_type = "AUDIO"  # Default fallback
        target_file = file_path or self.current_song
        if target_file and self._is_stream_url(target_file):
            file_type = "NET"
        elif target_file:
            file_ext = os.path.splitext(target_file)[1].lower()
            if file_ext == '.mp3':
                file_type = "MP3"
            elif file_ext == '.flac':
                file_type = "FLAC"
            elif file_ext == '.wav':
                file_type = "WAV"
            elif file_ext == '.ogg':
                file_type = "OGG"
            elif file_ext == '.m4a':
                file_type = "M4A"
            elif file_ext == '.aac':
                file_type = "AAC"
            elif file_ext == '.wma':
                file_type = "WMA"
            else:
                file_type = file_ext[1:].upper() if file_ext else "AUDIO"
        
        # Update info label with shuffle/repeat status
        channel_text = {0: '—', 1: 'Mono', 2: 'Stereo'}.get(channels, f'{channels} channels')
        status_parts = [f"{file_type} · {sample_rate_khz} kHz · {bitrate or '—'} kbps · {channel_text}"]
        
        if self.shuffle == SHUFFLE_ALBUMS:
            status_parts.append("ALBUMS")
        elif self.shuffle == SHUFFLE_TRACKS:
            status_parts.append("SHUFFLE")
        if self.repeat_mode == REPEAT_ALL:
            status_parts.append("REPEAT")
        elif self.repeat_mode == REPEAT_ONE:
            status_parts.append("REPEAT 1")

        self.info_label.set_text(" • ".join(status_parts))
    
    def save_playlist(self):
        """Save the current playlist to a file (atomically)"""
        try:
            self._atomic_write(self.playlist_path(), "".join(f"{p}\n" for p in self.playlist))
        except Exception as e:
            print(f"Error saving playlist: {e}")
    
    def load_playlist(self):
        """Load the saved playlist. Every line is kept — unknown or missing
        entries are only *marked*, never silently dropped (a later save would
        otherwise erase them from disk). Rows are bulk-inserted with the model
        detached, and existence checks run afterwards in idle chunks so startup
        never blocks on a slow or unmounted media drive."""
        try:
            playlist_file = self.playlist_path()
            if not os.path.exists(playlist_file):
                return
            with open(playlist_file, 'r') as f:
                lines = [line.strip() for line in f if line.strip()]
            if not lines:
                return
            with self._store_guard():
                self.playlist_view.set_model(None)  # bulk-insert speedup
                for i, file_path in enumerate(lines):
                    self.playlist.append(file_path)
                    self.entry_ids.append(uuid.uuid4().hex)
                    display_name = self._display_name(file_path)
                    self.playlist_store.append([file_path, display_name, i + 1, self._duration_str(file_path), self.entry_ids[-1], ""])
                self.playlist_view.set_model(self.playlist_store)

            self._refresh_order()
            self.update_playlist_info()
            self.tasks.idle_add(self._check_missing_chunk, 0)
            self._queue_playlist_metadata()

            # If we have a playlist, try to load the first available song
            if self.playlist and self.current_song is None:
                self.load_first_available_song()
        except Exception as e:
            print(f"Error loading playlist: {e}")

    def _check_missing_chunk(self, start, chunk=50):
        """Mark missing/unsupported rows in idle-time chunks after startup."""
        end = min(start + chunk, len(self.playlist))
        for i in range(start, end):
            p = self.playlist[i]
            if self._is_stream_url(p):
                continue
            try:
                it = self.playlist_store.get_iter(Gtk.TreePath(i))
            except Exception:
                return False  # store changed under us; a later edit re-marks
            name = self._display_name(p)
            if not os.path.exists(p):
                self.playlist_store.set_value(it, 1, f"❌ {name} [MISSING]")
            elif not self.is_audio_file(p):
                self.playlist_store.set_value(it, 1, f"⚠ {name} [UNSUPPORTED]")
        if end < len(self.playlist):
            self.tasks.idle_add(self._check_missing_chunk, end)
        return False
    
    def load_first_available_song(self):
        """Load the first available (existing) song from the playlist"""
        for i, file_path in enumerate(self.playlist):
            if self._playable(file_path):
                self.load_song(i)
                return
        # If no files exist, just set to first index but don't load
        if self.playlist:
            self.current_index = 0
    
def main():
    app = MusicPlayer()
    # Files passed on the command line (file manager "Open With", desktop %F):
    # append them and play the first one.
    args = [a if app._is_stream_url(a) else os.path.abspath(a)
            for a in sys.argv[1:] if app._playable(a)]
    if args:
        first_new = len(app.playlist)
        app._add_paths(args)
        app.load_song(first_new)
        app.player.set_state(Gst.State.PLAYING)
        app._sync_play_ui(True)
    app.show_all()
    Gtk.main()

if __name__ == "__main__":
    main()
