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
from collections import OrderedDict, deque
from contextlib import contextmanager
from pathlib import Path

# Application version
APP_VERSION = "1.1"

# Application name (Winamp-inspired, but an original name — "Winamp" is a trademark)
APP_NAME = "Llama Amp"

# Default display text
DEFAULT_SONG_TEXT = f"{APP_NAME} *** Please select a file ***"

# ---- Tunables / constants ----
WINDOW_W, WINDOW_H = 360, 500
POSITION_TIMER_MS = 100          # position/time UI refresh
SAVE_DEBOUNCE_MS = 1000          # debounce for writing config.json
DEFAULT_VOLUME = 0.7
EQ_GAIN_MIN, EQ_GAIN_MAX = -24.0, 12.0   # equalizer-10bands band range (dB)
SPECTRUM_BANDS = 10
SPECTRUM_INTERVAL_NS = 66_000_000        # ~66 ms (15 fps; indistinguishable, 25% fewer wakeups)
SPECTRUM_THRESHOLD = -80                 # dB floor for the analyzer
SPECTRUM_DECAY = 0.18                    # how fast bars fall between updates
EQ_FREQUENCIES = ['60', '170', '310', '600', '1K', '3K', '6K', '12K', '14K', '16K']
REPEAT_OFF, REPEAT_ALL, REPEAT_ONE = 0, 1, 2
SHUFFLE_OFF, SHUFFLE_TRACKS, SHUFFLE_ALBUMS = 0, 1, 2
RG_MODES = ('off', 'track', 'album')
LISTENBRAINZ_API = "https://api.listenbrainz.org"   # module-level: test-patchable
NOTIFY_MIN_INTERVAL_S = 5
ALBUM_ART_SIZE = 170                     # px, square art matching the info+time column height
FOLDER_ART_NAMES = ('cover', 'folder', 'front', 'album', 'albumart')
FOLDER_ART_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
META_CACHE_LIMIT = 64                    # LRU caps: probed metadata / embedded art
FOLDER_ART_CACHE_LIMIT = 32              # per-directory folder art
SEEK_STEP_SECONDS = 5                    # arrow-key seek step
VOLUME_STEP = 0.05                       # arrow-key / scroll volume step
URI_TARGET_INFO = 80                     # DnD info id for uri-list drops on the playlist

# Track-skip pipe for prev/next buttons: scaled + raised to sit at the same
# optical height as the small ◂/▸ triangles (which are ~55% of em, centered).
PIPE_MARKUP = '<span size="60%" rise="3000">❙</span>'

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

class MusicPlayer(Gtk.Window):
    def __init__(self):
        super().__init__(title=APP_NAME)
        
        # Window setup
        self.set_default_size(WINDOW_W, WINDOW_H)
        self.set_resizable(False)
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
        self.spectrum_levels = [0.0] * SPECTRUM_BANDS

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
        self._play_next = deque()       # paths queued via "Play Next" (session-only)
        self._search_pos = -1           # last playlist-search hit index

        # Single background worker drains all metadata probes + folder-art scans:
        # bounded concurrency no matter how fast the user scrolls the playlist.
        self._probe_queue = queue.Queue()
        self._probe_inflight = set()    # paths queued/probing (main thread only)
        threading.Thread(target=self._probe_loop, daemon=True).start()

        # Audio properties for dynamic display
        self.audio_properties = {
            'sample_rate': 44100,
            'bitrate': 128,
            'channels': 2
        }

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
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._on_unix_signal)

        # Set up drag and drop
        self.setup_drag_and_drop()

        # Timer for updating position
        self._timeout_ids.append(GLib.timeout_add(POSITION_TIMER_MS, self.update_position))

        # Track-length sidecar, then the saved playlist (rows read the cache)
        self._load_durations()
        self.load_playlist()

        # Apply restored settings to widgets and re-arm the last-played track
        self.apply_config()

        # Desktop integration: media keys, panel / lockscreen controls
        self._mpris_setup()

        # System tray / app indicator
        self._init_tray()

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
        if not self.direct_mode and self.equalizer is not None:
            self.apply_all_eq()
            if self.panorama is not None:
                self.panorama.set_property("panorama",
                                           max(-1.0, min(1.0, self.balance)))

    def _rebuild_pipeline(self):
        """Rebuild the audio-filter chain and resume the current track at the
        same position. playbin's audio-filter only changes in the NULL state,
        so this is the shared machinery for Direct Mode and ReplayGain toggles."""
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
        GLib.timeout_add(400, self._alsa_try_start)

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
                    GLib.timeout_add(1800, self._alsa_try_start)
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
        """Drive EQ bar heights from real analyzer magnitudes (rise fast, decay smooth)."""
        mags = self._parse_magnitudes(structure)
        if not mags:
            return
        n = min(len(mags), len(self.spectrum_levels))
        for i in range(n):
            norm = (float(mags[i]) - SPECTRUM_THRESHOLD) / (0.0 - SPECTRUM_THRESHOLD)
            norm = max(0.0, min(1.0, norm))
            old = self.spectrum_levels[i]
            if norm >= old:
                self.spectrum_levels[i] = norm
            else:
                self.spectrum_levels[i] = max(norm, old - SPECTRUM_DECAY)
            # Only repaint bars that moved visibly
            if abs(self.spectrum_levels[i] - old) > 0.01 and i < len(self.eq_bars):
                self.eq_bars[i].queue_draw()

    # ==================== GStreamer bus ====================
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
            GLib.timeout_add(1500, self._alsa_try_start)
            return
        err, dbg = message.parse_error()
        self.log_debug(f"GStreamer error: {err} ({dbg})")
        self.player.set_state(Gst.State.NULL)
        self._sync_play_ui(False)
        self.info_label.set_text(f"⚠ {err.message}")
        # Skip past a bad track if we were playing, guarding against an all-bad loop.
        self._error_streak += 1
        if self.playlist and self._error_streak < len(self.playlist):
            # after_error: never let REPEAT_ONE replay a broken track in a loop
            GLib.idle_add(lambda: (self.advance_track(auto=True, after_error=True), False)[1])

    def on_bus_eos(self, bus, message):
        # Only fires when the gapless handoff declined (repeat-one, sleep,
        # gapless off, end of playlist) — advance_track handles those cases.
        self.advance_track(auto=True)

    def _on_about_to_finish(self, playbin):
        """GStreamer streaming thread: decide the next track and hand its uri
        to playbin for gapless playback. ONLY reads state + sets the uri —
        all bookkeeping happens at handoff (on_bus_stream_start, main loop)."""
        if (not self.gapless or self.repeat_mode == REPEAT_ONE
                or self._sleep_after_track):
            return  # decline: let EOS fire and advance_track do its thing
        if self._loading or getattr(self, '_switching_output', False):
            # The main thread is rebuilding the pipeline (manual track load or
            # output/filter swap); setting the uri now would race that setup
            # and can wedge the preroll in READY.
            return
        nxt = self._peek_next_index(auto=True)
        if nxt is None:
            return  # end of playlist: normal EOS/stop
        path = self.playlist[nxt]
        uri = (path if self._is_stream_url(path)
               else Gst.filename_to_uri(os.path.abspath(path)))
        self._gapless_next = (nxt, path, uri)
        playbin.set_property("uri", uri)

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
        if self._gapless_next is None:
            return  # ordinary load_song start
        idx, path, uri = self._gapless_next
        self._gapless_next = None
        if self._play_next and self._play_next[0] == path:
            self._play_next.popleft()
            self._update_queue_markers()
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
            def requery():
                ok2, d2 = self.player.query_duration(Gst.Format.TIME)
                if ok2 and d2 > 0:
                    self.duration = d2
                    self._note_duration(path, d2 // Gst.SECOND)
                return False
            GLib.timeout_add(200, requery)
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

    def _sync_play_ui(self, playing):
        self.is_playing = playing
        if playing:
            self.play_btn.set_label("❚❚")
            self.play_btn.set_tooltip_text("Pause")
            self.play_btn.get_style_context().add_class('active')
        else:
            self.play_btn.set_label("▸")
            self.play_btn.set_tooltip_text("Play")
            self.play_btn.get_style_context().remove_class('active')
            self._start_decay()  # let the bars fall to rest, then stop ticking
        if getattr(self, '_tray_play_item', None) is not None:
            self._tray_play_item.set_label("Pause" if playing else "Play")
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
        """Arm/disarm the sleep timer. mode: None (off), 'track', or minutes."""
        if self._sleep_timer_id is not None:
            GLib.source_remove(self._sleep_timer_id)
            self._sleep_timer_id = None
        self._sleep_after_track = False
        self._sleep_deadline = None
        self._sleep_mode = mode
        if mode == 'track':
            self._sleep_after_track = True
            self.show_drop_feedback("Sleeping after this track")
        elif isinstance(mode, int) and mode > 0:
            self._sleep_timer_id = GLib.timeout_add_seconds(mode * 60, self._sleep_fire)
            self._sleep_deadline = GLib.get_monotonic_time() + mode * 60 * 1_000_000
            self.show_drop_feedback(f"Sleeping in {mode} min")
        else:
            self.show_drop_feedback("Sleep timer off")
        self._update_stop_btn_cue()

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
            self.player.set_state(Gst.State.PAUSED)
            self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
            self._sync_play_ui(False)
            self.show_drop_feedback("Sleep timer — stopped after track")
            return
        if auto and self.repeat_mode == REPEAT_ONE and not after_error:
            self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
            self.player.set_state(Gst.State.PLAYING)
            return

        nxt = self._peek_next_index(auto=auto)
        if nxt is None:
            playable = [i for i, p in enumerate(self.playlist)
                        if self._playable(p)]
            if not playable:
                self.current_song = None
                self._set_title_text("❌ No available files in playlist")
                self.info_label.set_text("All files are missing - please re-add music files")
            self.stop_song(None)
            return
        # Consume the queue head if that's what the resolver picked
        if self._play_next and self._play_next[0] == self.playlist[nxt]:
            self._play_next.popleft()
            self._update_queue_markers()
        self.load_song(nxt)
        self._play_current()

    def _peek_next_index(self, auto=True):
        """Resolve the next track index: Play-Next queue first (pruning dead
        entries without popping the live head), then shuffle/repeat order.
        Returns None when playback should stop."""
        while self._play_next:
            p = self._play_next[0]
            if p in self.playlist and (self._is_stream_url(p) or os.path.exists(p)):
                return self.playlist.index(p)
            self._play_next.popleft()  # gone from playlist or disk: prune

        playable = [i for i, p in enumerate(self.playlist)
                    if self._playable(p)]
        if not playable:
            return None
        if self.shuffle == SHUFFLE_TRACKS:
            choices = [i for i in playable if i != self.current_index] or playable
            return random.choice(choices)
        if self.shuffle == SHUFFLE_ALBUMS:
            return self._album_next_index(playable)
        after = [i for i in playable if i > self.current_index]
        if after:
            return after[0]
        if self.repeat_mode == REPEAT_ALL or not auto:
            return playable[0]
        return None

    def _album_key(self, path):
        """Album identity for shuffle-by-album: the containing directory
        (each stream URL counts as its own album)."""
        if self._is_stream_url(path):
            return path
        return os.path.dirname(os.path.abspath(path))

    def _album_runs(self, playable):
        """Group playable indices into runs of consecutive same-album tracks."""
        runs = []
        for i in playable:
            key = self._album_key(self.playlist[i])
            if runs and runs[-1][0] == key and runs[-1][1][-1] == i - 1:
                runs[-1][1].append(i)
            else:
                runs.append((key, [i]))
        return runs

    def _album_next_index(self, playable):
        """Album shuffle: play sequentially within the current album run; at
        its end jump to a random other album's first track."""
        cur_key = (self._album_key(self.playlist[self.current_index])
                   if 0 <= self.current_index < len(self.playlist) else None)
        nxt = self.current_index + 1
        if nxt in playable and self._album_key(self.playlist[nxt]) == cur_key:
            return nxt
        runs = self._album_runs(playable)
        others = [r for r in runs if r[0] != cur_key] or runs
        return random.choice(others)[1][0]

    def _album_prev_index(self, playable):
        """Album shuffle, backwards: sequential-previous within the album; at
        the album's first track jump to a random other album's first track."""
        cur_key = (self._album_key(self.playlist[self.current_index])
                   if 0 <= self.current_index < len(self.playlist) else None)
        prv = self.current_index - 1
        if prv in playable and self._album_key(self.playlist[prv]) == cur_key:
            return prv
        runs = self._album_runs(playable)
        others = [r for r in runs if r[0] != cur_key] or runs
        return random.choice(others)[1][0]

    # ==================== Persistence (config.json) ====================
    def _data_dir(self):
        """Where config.json/playlist.txt live. Portable mode: next to the
        script when its directory is writable (the classic ~/Apps setup).
        Installed mode (/usr is read-only): ~/.config/llamaamp/."""
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
            self._durations_save_id = GLib.timeout_add(2000, self._save_durations)

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

    def _queue_missing_durations(self):
        """Background-probe playlist entries with no cached duration."""
        for p in self.playlist:
            if (self._duration_seconds(p) is None and os.path.exists(p)
                    and self.is_audio_file(p) and p not in self._probe_inflight):
                self._probe_inflight.add(p)
                self._probe_queue.put(('meta', p, dict(self.audio_properties)))

    @contextmanager
    def _store_guard(self):
        """Suppress store-changed resyncs during programmatic edits, exception-safe."""
        self._suppress_store = True
        try:
            yield
        finally:
            self._suppress_store = False

    def _atomic_write(self, path, data, binary=False):
        """Write via tmp + os.replace so a crash mid-write can't truncate the file."""
        tmp = path + ".tmp"
        with open(tmp, "wb" if binary else "w") as f:
            f.write(data)
        os.replace(tmp, path)

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
            "eq_values": [0.5] * SPECTRUM_BANDS,
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
        self.config = cfg
        self.volume = self._cfg(cfg, "volume", float, DEFAULT_VOLUME, 0.0, 1.0)
        self.balance = self._cfg(cfg, "balance", float, 0.0, -1.0, 1.0)
        ev = cfg.get("eq_values")
        if isinstance(ev, list) and len(ev) == SPECTRUM_BANDS:
            try:
                self.eq_values = [max(0.0, min(1.0, float(x))) for x in ev]
            except Exception:
                self.eq_values = [0.5] * SPECTRUM_BANDS
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
        if (isinstance(wp, (list, tuple)) and len(wp) == 2
                and all(isinstance(x, (int, float)) for x in wp)):
            self._win_pos = (int(wp[0]), int(wp[1]))

    def apply_config(self):
        """Push restored state into widgets after the UI + playlist exist."""
        # Restore window position (undecorated window: the WM won't do it for us),
        # clamped so a disconnected monitor can't strand it off-screen.
        if self._win_pos is not None:
            screen = Gdk.Screen.get_default()
            if screen is not None:
                x = max(0, min(self._win_pos[0], screen.get_width() - 100))
                y = max(0, min(self._win_pos[1], screen.get_height() - 100))
                self.move(x, y)
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

    def schedule_save_config(self):
        if self._save_timeout_id is not None:
            return
        self._save_timeout_id = GLib.timeout_add(SAVE_DEBOUNCE_MS, self._flush_save_config)

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
            }
            serialized = json.dumps(data, indent=2)
            if serialized == self._last_config_json:
                return
            self._atomic_write(self.config_path(), serialized)
            self._last_config_json = serialized
        except Exception as e:
            self.log_debug(f"config save failed: {e}")

    def on_configure_event(self, widget, event):
        self._win_pos = self.get_position()
        return False

    def on_window_state_event(self, widget, event):
        """Stop the spectrum analyzer while minimized: nobody can see the bars,
        so skip the whole parse -> decay -> redraw chain."""
        if self.spectrum is None:
            return False
        iconified = bool(event.new_window_state & Gdk.WindowState.ICONIFIED)
        try:
            self.spectrum.set_property("post-messages", not iconified)
        except Exception as e:
            self.log_debug(f"spectrum toggle failed: {e}")
        if iconified:
            self._start_decay()
            self._marquee_stop()
        else:
            # Re-evaluate the title: restarts the marquee only if it's long
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
        focus = self.get_focus()
        if isinstance(focus, Gtk.Entry):
            return False
        key = event.keyval
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
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
        if self._save_timeout_id is not None:
            GLib.source_remove(self._save_timeout_id)
            self._save_timeout_id = None
        self._write_config()
        self.save_playlist()
        if self._durations_save_id is not None:
            GLib.source_remove(self._durations_save_id)
        self._save_durations()
        if self._sleep_timer_id is not None:
            GLib.source_remove(self._sleep_timer_id)
            self._sleep_timer_id = None
        self._marquee_stop()
        self._hide_tray()
        self._mpris_teardown()
        if self._drop_feedback_id is not None:
            GLib.source_remove(self._drop_feedback_id)
            self._drop_feedback_id = None
        for tid in self._timeout_ids:
            try:
                GLib.source_remove(tid)
            except Exception:
                pass
        try:
            self.player.set_state(Gst.State.NULL)
        except Exception:
            pass
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
        svg = os.path.join(app_dir, "musicPlayer.svg")
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
                    ind.set_icon_full('musicPlayer', APP_NAME)
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
                               "musicPlayer.svg")
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
        if self.is_playing:
            return 'Playing'
        return 'Paused' if self.current_song else 'Stopped'

    def _mpris_trackid(self):
        return f"{MPRIS_OBJECT_PATH}/llamaamp/track/{self.current_index}"

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
                self.shuffle = want
                self.update_shuffle_button()
                self.schedule_save_config()
        elif prop == "LoopStatus":
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
        added = 0
        first_new = len(self.playlist)
        with self._store_guard():
            for p in paths:
                self.playlist.append(p)
                name = self._display_name(p)
                display = (name if self._is_stream_url(p) or os.path.exists(p)
                           else f"❌ {name} [MISSING]")
                self.playlist_store.append([p, display, len(self.playlist), self._duration_str(p)])
                added += 1
        if not added:
            return 0
        self.update_playlist_info()
        self.save_playlist()
        self._queue_missing_durations()
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
        return os.path.splitext(os.path.basename(path))[0]

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
        """Show a brief feedback message when files are dropped"""
        self._marquee_stop()  # a tick must not overwrite the transient message
        self.song_label.set_text(f"▸ {message}")

        def restore_text():
            self._drop_feedback_id = None
            self._refresh_song_label()  # keeps tag-derived "Artist - Title"
            return False

        # Re-arm rather than stack timers on rapid drops
        if self._drop_feedback_id is not None:
            GLib.source_remove(self._drop_feedback_id)
        self._drop_feedback_id = GLib.timeout_add(2000, restore_text)
        
    def setup_styling(self):
        css_provider = Gtk.CssProvider()
        css = """

        /* ============================================================
           Llama Amp — modern flat Winamp theme
           Tokens: chassis #101210 · panel #161917 · control #1d211e
                   hover #262b27 · pressed #0e100e · hairline #2b302c
                   text #d8dcd8 · accent #1ae000 · accent-bright #52ff33
                   accent-dim #18b40a · LCD #00ff00 on #000000
           ============================================================ */

        window.llama-window {
            background-color: transparent;
        }

        .music-player-main {
            background: #101210;
            border: 1px solid #2b302c;
            border-radius: 12px;
        }

        .music-player-main.drag-over {
            background: #0f1a0c;
            border: 1px solid #1ae000;
            box-shadow: inset 0 0 12px rgba(26, 224, 0, 0.18);
        }

        .music-player-titlebar {
            background: #101210;
            color: #1ae000;
            font-family: "Orbitron", "DejaVu Sans", sans-serif;
            font-size: 15px;
            font-weight: bold;
            letter-spacing: 2px;
            padding: 10px 14px;
            border-bottom: 1px solid #2b302c;
            border-radius: 12px 12px 0 0;
            text-shadow: 0 0 6px rgba(26, 224, 0, 0.35);
        }

        .music-player-display {
            background: #000000;
            color: #00ff00;
            font-family: "Courier New", "Liberation Mono", monospace;
            font-size: 15px;
            font-weight: bold;
            border: 1px solid #2b302c;
            border-radius: 6px;
            margin: 4px 0;
            padding: 12px;
        }

        .music-player-art {
            border: 1px solid #2b302c;
            border-radius: 4px;
        }

        .music-player-time {
            background: #000000;
            color: #00ff00;
            font-family: "DSEG7 Classic", "Courier New", "Liberation Mono", monospace;
            font-size: 30px;
            font-weight: normal;
            border: 1px solid #2b302c;
            border-radius: 4px;
            padding: 10px 14px;
            min-width: 120px;
        }

        .music-player-button {
            background: #1d211e;
            border: 1px solid #2b302c;
            border-radius: 6px;
            color: #d8dcd8;
            font-size: 20px;
            min-width: 44px;
            min-height: 36px;
            margin: 2px;
            padding: 6px 12px;
            transition: background 120ms ease, border-color 120ms ease, color 120ms ease, box-shadow 120ms ease;
        }

        .music-player-button:hover {
            background: #262b27;
            border-color: #3c433e;
            color: #52ff33;
        }

        .music-player-button:active,
        .music-player-button.pressed {
            background: #0e100e;
            color: #1ae000;
        }

        .music-player-button.active {
            background: rgba(26, 224, 0, 0.12);
            border: 1px solid #1ae000;
            color: #1ae000;
            box-shadow: 0 0 8px rgba(26, 224, 0, 0.25);
        }

        .music-player-button.active:hover {
            background: rgba(26, 224, 0, 0.18);
            color: #52ff33;
        }

        .music-player-button.active:active,
        .music-player-button.active.pressed {
            background: rgba(26, 224, 0, 0.08);
        }

        .music-player-text-button {
            background: #1d211e;
            border: 1px solid #2b302c;
            border-radius: 6px;
            color: #d8dcd8;
            font-size: 12px;
            font-weight: bold;
            letter-spacing: 1px;
            min-height: 32px;
            margin: 2px;
            padding: 8px 10px;
            transition: background 120ms ease, border-color 120ms ease, color 120ms ease, box-shadow 120ms ease;
        }

        .music-player-text-button:hover {
            background: #262b27;
            border-color: #3c433e;
            color: #52ff33;
        }

        .music-player-text-button:active,
        .music-player-text-button.pressed {
            background: #0e100e;
            color: #1ae000;
        }

        .music-player-text-button.active {
            background: rgba(26, 224, 0, 0.12);
            border: 1px solid #1ae000;
            color: #1ae000;
            box-shadow: 0 0 8px rgba(26, 224, 0, 0.25);
        }

        .music-player-text-button.active:hover {
            background: rgba(26, 224, 0, 0.18);
            color: #52ff33;
        }

        .music-player-text-button.active:active,
        .music-player-text-button.active.pressed {
            background: rgba(26, 224, 0, 0.08);
        }

        .titlebar-btn {
            font-size: 16px;
            padding: 6px;
        }

        .eq-on-button {
            min-height: 24px;
            padding: 2px 10px;
            font-size: 11px;
        }

        .titlebar-close:hover {
            color: #ff5544;
            border-color: #ff5544;
        }

        .music-player-slider {
            background: transparent;
        }

        .music-player-slider trough {
            background: #0b0d0b;
            border: 1px solid #2b302c;
            border-radius: 3px;
            min-height: 6px;
        }

        .music-player-slider highlight {
            background: #1ae000;
            border: none;
            border-radius: 3px;
        }

        .music-player-slider slider {
            background: #cfd4cf;
            border: none;
            border-radius: 2px;
            min-width: 6px;
            min-height: 16px;
        }

        .music-player-slider slider:hover {
            background: #52ff33;
        }

        .music-player-slider value {
            color: #18b40a;
            font-size: 11px;
            font-weight: bold;
        }

        .volume-slider {
            background: transparent;
        }

        .volume-slider trough {
            background: #0b0d0b;
            border: 1px solid #2b302c;
            border-radius: 3px;
            min-height: 6px;
        }

        .volume-slider highlight {
            background: #1ae000;
            border: none;
            border-radius: 3px;
        }

        .volume-slider slider {
            background: #cfd4cf;
            border: none;
            border-radius: 2px;
            min-width: 6px;
            min-height: 16px;
        }

        .volume-slider slider:hover {
            background: #52ff33;
        }

        .volume-slider value {
            color: #18b40a;
            font-size: 11px;
            font-weight: bold;
        }

        .balance-slider highlight {
            background: transparent;
            border: none;
        }

        /* Focus rings: Yaru paints these orange; keep them in the family */
        *:focus {
            outline-color: rgba(26, 224, 0, 0.45);
        }

        .eq-bar {
            min-width: 30px;
        }

        .playlist {
            background: #000000;
            color: #00ff00;
            font-family: "Courier New", "Liberation Mono", monospace;
            font-size: 14px;
            font-weight: bold;
        }

        .playlist-search {
            background: #0b0d0b;
            color: #52ff33;
            border: 1px solid #2b302c;
            border-radius: 4px;
            font-size: 13px;
            padding: 4px 8px;
        }

        .playlist-search.search-miss {
            border-color: #ff5544;
            color: #ff5544;
        }

        .playlist:selected {
            background: #0d3a06;
            color: #eaffea;
        }

        .status-indicator {
            background: #000000;
            color: #00d400;
            font-family: "Courier New", "Liberation Mono", monospace;
            font-size: 10px;
            font-weight: bold;
            padding: 2px 6px;
            border: 1px solid #2b302c;
            border-radius: 3px;
        }

        .music-player-frame {
            background: #161917;
            border: 1px solid #2b302c;
            border-radius: 8px;
            margin: 8px 12px;
            padding: 12px;
        }

        .music-player-label {
            font-size: 11px;
            font-weight: bold;
            letter-spacing: 2px;
            color: #18b40a;
        }

        scrollbar {
            background: transparent;
        }

        scrollbar trough {
            background: #0b0d0b;
        }

        scrollbar slider {
            background: #343a35;
            border-radius: 4px;
            min-width: 6px;
        }

        scrollbar slider:hover {
            background: #18b40a;
        }

        menu {
            background-color: #141614;
            border: 1px solid #2b302c;
        }

        menuitem {
            padding: 8px 14px;
            color: #d8dcd8;
            font-size: 15px;
        }

        menuitem:hover {
            background-color: rgba(26, 224, 0, 0.12);
            color: #52ff33;
        }

        menuitem:disabled {
            color: #6a716a;
        }

        menu separator {
            background-color: #2b302c;
            min-height: 1px;
        }

        tooltip {
            background-color: #101210;
            color: #d8dcd8;
            border: 1px solid #2b302c;
        }
        """
        
        css_provider.load_from_data(css.encode())
        screen = Gdk.Screen.get_default()
        style_context = Gtk.StyleContext()
        style_context.add_provider_for_screen(screen, css_provider, 
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    
    def create_interface(self):
        main_box = Gtk.VBox(spacing=0)
        main_box.get_style_context().add_class('music-player-main')
        self.add(main_box)
        
        # Custom title bar
        title_bar = self.create_title_bar()
        main_box.pack_start(title_bar, False, False, 0)
        
        # Main player area
        player_frame = Gtk.Frame()
        player_frame.get_style_context().add_class('music-player-frame')
        
        player_box = Gtk.VBox(spacing=25)
        
        # LED Display
        display_area = self.create_display_area()
        player_box.pack_start(display_area, False, False, 0)
        
        # Control buttons row (position slider lives inside the display panel)
        controls = self.create_controls()
        player_box.pack_start(controls, False, False, 0)

        # Volume and balance
        vol_controls = self.create_volume_controls()
        player_box.pack_start(vol_controls, False, False, 0)
        
        player_frame.add(player_box)
        main_box.pack_start(player_frame, False, False, 0)
        
        # Equalizer
        eq_frame = self.create_equalizer()
        main_box.pack_start(eq_frame, False, False, 0)
        
        # Playlist
        playlist_frame = self.create_playlist()
        main_box.pack_start(playlist_frame, True, True, 0)
        
    def create_title_bar(self):
        title_event_box = Gtk.EventBox()
        title_event_box.get_style_context().add_class('music-player-titlebar')
        
        title_box = Gtk.HBox()
        
        # Music Player title
        title_label = Gtk.Label(label=f" {APP_NAME} v{APP_VERSION} ")
        title_label.set_halign(Gtk.Align.START)
        title_label.set_margin_start(20)
        title_box.pack_start(title_label, True, True, 0)
        
        # Window controls
        controls_box = Gtk.HBox(spacing=2)

        settings_btn = Gtk.Button(label="⚙")
        settings_btn.set_size_request(48, 40)
        settings_btn.get_style_context().add_class('music-player-text-button')
        settings_btn.get_style_context().add_class('titlebar-btn')
        settings_btn.set_tooltip_text("Settings")
        settings_btn.connect("clicked", self.on_settings_clicked)

        min_btn = Gtk.Button(label="─")
        min_btn.set_size_request(48, 40)
        min_btn.get_style_context().add_class('music-player-text-button')
        min_btn.get_style_context().add_class('titlebar-btn')
        min_btn.set_tooltip_text("Minimize")
        min_btn.connect("clicked", lambda x: self.iconify())
        # No press-swallowing handler: GtkButton consumes the press itself, so
        # titlebar drag can't interfere — and a True-returning handler would
        # block the button's own activation (the old minimize bug).
        
        close_btn = Gtk.Button(label="✕")
        close_btn.set_size_request(48, 40)
        close_btn.get_style_context().add_class('music-player-text-button')
        close_btn.get_style_context().add_class('titlebar-btn')
        close_btn.get_style_context().add_class('titlebar-close')
        close_btn.set_tooltip_text("Close")
        close_btn.connect("clicked", self.on_close_clicked)
        # Prevent title bar drag events from interfering with close button
        close_btn.connect("button-press-event", self.on_button_press)
        close_btn.connect("button-release-event", self.on_button_release)
        
        controls_box.pack_start(settings_btn, False, False, 0)
        controls_box.pack_start(min_btn, False, False, 0)
        controls_box.pack_start(close_btn, False, False, 0)
        
        title_box.pack_end(controls_box, False, False, 0)
        title_event_box.add(title_box)
        
        # Make title bar draggable and add context menu
        title_event_box.connect("button-press-event", self.on_title_press)
        title_event_box.connect("button-release-event", self.on_title_release)
        title_event_box.connect("motion-notify-event", self.on_title_motion)
        
        return title_event_box
        
    def on_title_press(self, widget, event):
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 1:  # Left click
            self.drag_start_x = event.x_root - self.get_position()[0]
            self.drag_start_y = event.y_root - self.get_position()[1]
            self.is_dragging = True
        elif event.type == Gdk.EventType.BUTTON_PRESS and event.button == 3:  # Right click
            self.show_title_context_menu(widget, event)
    
    def on_title_motion(self, widget, event):
        if self.is_dragging:
            self.move(int(event.x_root - self.drag_start_x),
                     int(event.y_root - self.drag_start_y))
    
    def on_title_release(self, widget, event):
        """Handle title bar button release to stop dragging"""
        if event.type == Gdk.EventType.BUTTON_RELEASE and event.button == 1:  # Left click release
            self.is_dragging = False
    
    def build_settings_menu(self):
        """The settings menu: audio fidelity toggles, EQ presets, window actions.
        Served by both the titlebar ⚙ button and the titlebar right-click."""
        menu = Gtk.Menu()

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
        display_frame = Gtk.Frame()
        display_frame.get_style_context().add_class('music-player-display')
        display_events = Gtk.EventBox()
        display_events.set_visible_window(False)  # draw-through: keep the frame's styling
        display_events.add_events(Gdk.EventMask.SCROLL_MASK
                                  | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        display_events.connect("scroll-event", self.on_display_scroll)
        
        # Art on the left spans the full panel height; info + time stack on the right
        outer_box = Gtk.HBox(spacing=12)
        outer_box.set_margin_top(10)
        outer_box.set_margin_bottom(10)
        outer_box.set_margin_start(10)
        outer_box.set_margin_end(10)

        # Album art (hidden until a track provides art)
        self.album_art = Gtk.Image()
        self.album_art.get_style_context().add_class('music-player-art')
        self.album_art.set_valign(Gtk.Align.CENTER)
        self.album_art.set_no_show_all(True)
        outer_box.pack_start(self.album_art, False, False, 0)

        display_box = Gtk.VBox(spacing=15)

        # Main info display
        info_box = Gtk.HBox(spacing=10)

        # Song info
        song_box = Gtk.VBox(spacing=8)
        self.song_label = Gtk.Label(label=DEFAULT_SONG_TEXT)
        self.song_label.set_halign(Gtk.Align.START)
        self.song_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.song_label.set_max_width_chars(44)  # panel is ~440px of text room beside the art
        song_box.pack_start(self.song_label, False, False, 0)
        
        # Bitrate, frequency info
        self.info_label = Gtk.Label(label="")
        self.info_label.set_halign(Gtk.Align.START)
        song_box.pack_start(self.info_label, False, False, 0)
        
        info_box.pack_start(song_box, True, True, 0)
        display_box.pack_start(info_box, False, False, 0)
        
        # Time display
        time_box = Gtk.HBox(spacing=15)

        self.time_display = Gtk.Label(label="00:00")
        self.time_display.get_style_context().add_class('music-player-time')
        # Winamp-style: clicking the clock flips elapsed <-> remaining
        time_events = Gtk.EventBox()
        time_events.set_visible_window(False)
        time_events.add(self.time_display)
        time_events.set_tooltip_text("Click: elapsed / remaining")
        time_events.connect("button-press-event", self._toggle_time_mode)
        time_box.pack_start(time_events, False, False, 0)


        display_box.pack_start(time_box, False, False, 0)

        outer_box.pack_start(display_box, True, True, 0)

        # Stack the art/info row above the position slider inside the LCD panel
        panel_box = Gtk.VBox(spacing=10)
        panel_box.pack_start(outer_box, True, True, 0)
        panel_box.pack_start(self.create_position_slider(), False, False, 0)

        display_frame.add(panel_box)
        display_events.add(display_frame)
        return display_events
    
    def create_controls(self):
        # Create a container with internal padding
        controls_container = Gtk.VBox()
        controls_container.set_margin_top(8)
        controls_container.set_margin_bottom(8)
        controls_container.set_margin_start(8)
        controls_container.set_margin_end(8)
        
        controls_box = Gtk.HBox(spacing=15, homogeneous=False)
        
        # Transport controls with exact music player button layout
        self.prev_btn = Gtk.Button(label="❙◂◂")
        # Scale the track-stop pipe down to the small triangles' optical height
        self.prev_btn.get_child().set_markup(f'{PIPE_MARKUP}◂◂')
        self.prev_btn.get_style_context().add_class('music-player-button')
        self.prev_btn.set_tooltip_text("Previous Track")
        self.prev_btn.set_halign(Gtk.Align.CENTER)
        self.prev_btn.set_valign(Gtk.Align.CENTER)
        self.prev_btn.connect("clicked", self.previous_song)
        self.prev_btn.connect("button-press-event", self.on_button_press)
        self.prev_btn.connect("button-release-event", self.on_button_release)
        
        self.play_btn = Gtk.Button(label="▸")
        self.play_btn.get_style_context().add_class('music-player-button')
        self.play_btn.set_tooltip_text("Play")
        self.play_btn.set_halign(Gtk.Align.CENTER)
        self.play_btn.set_valign(Gtk.Align.CENTER)
        self.play_btn.connect("clicked", self.toggle_play_pause)
        self.play_btn.connect("button-press-event", self.on_button_press)
        self.play_btn.connect("button-release-event", self.on_button_release)
        
        self.stop_btn = Gtk.Button(label="■")
        self.stop_btn.get_style_context().add_class('music-player-button')
        self.stop_btn.set_tooltip_text("Stop  ·  right-click: stop after current track")
        self.stop_btn.connect("button-press-event", self.on_stop_button_press)
        self.stop_btn.set_halign(Gtk.Align.CENTER)
        self.stop_btn.set_valign(Gtk.Align.CENTER)
        self.stop_btn.connect("clicked", self.stop_song)
        self.stop_btn.connect("button-press-event", self.on_button_press)
        self.stop_btn.connect("button-release-event", self.on_button_release)
        
        self.next_btn = Gtk.Button(label="▸▸❙")
        self.next_btn.get_child().set_markup(f'▸▸{PIPE_MARKUP}')
        self.next_btn.get_style_context().add_class('music-player-button')
        self.next_btn.set_tooltip_text("Next Track")
        self.next_btn.set_halign(Gtk.Align.CENTER)
        self.next_btn.set_valign(Gtk.Align.CENTER)
        self.next_btn.connect("clicked", self.next_song)
        self.next_btn.connect("button-press-event", self.on_button_press)
        self.next_btn.connect("button-release-event", self.on_button_release)
        
        # File operations
        self.eject_btn = Gtk.Button(label="▴")
        self.eject_btn.get_style_context().add_class('music-player-button')
        self.eject_btn.set_tooltip_text("Add Files")
        self.eject_btn.connect("clicked", self.add_files)
        self.eject_btn.connect("button-press-event", self.on_button_press)
        self.eject_btn.connect("button-release-event", self.on_button_release)
        
        # Mode buttons
        self.shuffle_btn = Gtk.Button(label="⇄ SHUFFLE")
        self.shuffle_btn.get_style_context().add_class('music-player-text-button')
        self.shuffle_btn.set_tooltip_text("Toggle Shuffle")
        self.shuffle_btn.connect("clicked", self.toggle_shuffle)
        self.shuffle_btn.connect("button-press-event", self.on_button_press)
        self.shuffle_btn.connect("button-release-event", self.on_button_release)
        
        self.repeat_btn = Gtk.Button(label="↻ REPEAT")
        self.repeat_btn.get_style_context().add_class('music-player-text-button')
        self.repeat_btn.set_tooltip_text("Toggle Repeat")
        self.repeat_btn.connect("clicked", self.toggle_repeat)
        self.repeat_btn.connect("button-press-event", self.on_button_press)
        self.repeat_btn.connect("button-release-event", self.on_button_release)
        
        controls_box.pack_start(self.prev_btn, False, False, 0)
        controls_box.pack_start(self.play_btn, False, False, 0)
        controls_box.pack_start(self.stop_btn, False, False, 0)
        controls_box.pack_start(self.next_btn, False, False, 0)
        controls_box.pack_start(self.eject_btn, False, False, 0)
        
        # Spacer
        spacer = Gtk.Label(label="")
        controls_box.pack_start(spacer, True, True, 0)
        
        controls_box.pack_end(self.repeat_btn, False, False, 0)
        controls_box.pack_end(self.shuffle_btn, False, False, 0)
        
        controls_container.pack_start(controls_box, True, True, 0)
        return controls_container
    
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
        vol_container = Gtk.VBox()
        vol_container.set_margin_top(8)
        vol_container.set_margin_bottom(8)
        vol_container.set_margin_start(8)
        vol_container.set_margin_end(8)
        
        vol_box = Gtk.HBox(spacing=20)
        
        vol_label = Gtk.Label(label="VOLUME")
        vol_label.get_style_context().add_class('music-player-label')
        vol_label.set_size_request(80, -1)
        vol_box.pack_start(vol_label, False, False, 0)
        
        self.volume_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        self.volume_scale.set_can_focus(False)
        self.volume_scale.get_style_context().add_class('volume-slider')
        self.volume_scale.set_range(0, 1)
        self.volume_scale.set_value(0.7)
        self.volume_scale.set_draw_value(True)
        self.volume_scale.set_tooltip_text("Volume")
        self.volume_scale.connect("value-changed", self.on_volume_changed)
        self.volume_scale.connect("format-value", self.format_volume_value)
        vol_box.pack_start(self.volume_scale, True, True, 0)
        
        bal_label = Gtk.Label(label="BALANCE")
        bal_label.get_style_context().add_class('music-player-label')
        bal_label.set_size_request(80, -1)
        vol_box.pack_start(bal_label, False, False, 0)
        
        self.balance_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        self.balance_scale.set_can_focus(False)
        self.balance_scale.get_style_context().add_class('music-player-slider')
        self.balance_scale.get_style_context().add_class('balance-slider')
        self.balance_scale.set_range(-1, 1)
        self.balance_scale.set_value(0)
        self.balance_scale.set_draw_value(True)  # Enable value display
        self.balance_scale.connect("format-value", self.format_balance_value)  # Connect format function
        self.balance_scale.connect("value-changed", self.on_balance_changed)
        self.balance_scale.set_tooltip_text("Balance")
        vol_box.pack_start(self.balance_scale, True, True, 0)
        
        vol_container.pack_start(vol_box, True, True, 0)
        return vol_container
    
    def create_equalizer(self):
        eq_frame = Gtk.Frame()
        eq_frame.get_style_context().add_class('music-player-frame')
        
        eq_box = Gtk.VBox(spacing=20)
        eq_box.set_vexpand(True)  # Expand vertically
        eq_box.set_hexpand(True)  # Expand horizontally
        eq_box.set_margin_top(10)
        eq_box.set_margin_bottom(10)
        eq_box.set_margin_start(10)
        eq_box.set_margin_end(10)
        
        # EQ header: label + Winamp-style ON toggle
        eq_header = Gtk.HBox()
        eq_label = Gtk.Label(label="EQUALIZER")
        eq_label.get_style_context().add_class('music-player-label')
        eq_label.set_halign(Gtk.Align.START)
        eq_header.pack_start(eq_label, False, False, 0)

        self.eq_on_btn = Gtk.Button(label="ON")
        self.eq_on_btn.get_style_context().add_class('music-player-text-button')
        self.eq_on_btn.get_style_context().add_class('eq-on-button')
        if self.eq_enabled:
            self.eq_on_btn.get_style_context().add_class('active')
        self.eq_on_btn.set_tooltip_text("Enable/bypass the equalizer and preamp")
        self.eq_on_btn.connect("clicked", self.toggle_eq_enabled)
        eq_header.pack_end(self.eq_on_btn, False, False, 0)
        eq_box.pack_start(eq_header, False, False, 0)

        # Frequency bars - traditional equalizer bars
        freq_box = Gtk.HBox(spacing=15, homogeneous=True)
        freq_box.set_vexpand(True)  # Expand vertically
        freq_box.set_hexpand(True)  # Expand horizontally
        
        frequencies = EQ_FREQUENCIES
        self.eq_bars = []
        # EQ gain settings, 0..1 with 0.5 = flat (0 dB). May be overridden by config.
        if not getattr(self, 'eq_values', None):
            self.eq_values = [0.5] * len(frequencies)

        # Preamp bar (master gain), Winamp-style, ahead of the bands
        pre_box = Gtk.VBox(spacing=8)
        pre_box.set_vexpand(True)
        pre_box.set_hexpand(True)
        self.preamp_bar = Gtk.DrawingArea()
        self.preamp_bar.get_style_context().add_class('eq-bar')
        self.preamp_bar.set_vexpand(True)
        self.preamp_bar.set_hexpand(True)
        self.preamp_bar.set_tooltip_text(f"Preamp (±{int(PREAMP_DB_RANGE)} dB)")
        self.preamp_bar.set_size_request(-1, 100)
        self.preamp_bar.connect("draw", self.draw_preamp_bar)
        pre_events = Gtk.EventBox()
        pre_events.add(self.preamp_bar)
        pre_events.set_vexpand(True)
        pre_events.set_hexpand(True)
        pre_events.set_size_request(-1, 100)
        pre_events.connect("button-press-event", self.on_preamp_clicked)
        pre_events.connect("motion-notify-event", self.on_preamp_drag)
        pre_events.set_events(Gdk.EventMask.BUTTON_PRESS_MASK
                              | Gdk.EventMask.POINTER_MOTION_MASK)
        pre_box.pack_start(pre_events, True, True, 0)
        pre_label = Gtk.Label(label="PRE")
        pre_label.get_style_context().add_class('status-indicator')
        pre_label.set_size_request(-1, 20)
        pre_label.set_halign(Gtk.Align.CENTER)
        pre_box.pack_start(pre_label, False, False, 0)
        freq_box.pack_start(pre_box, True, True, 0)

        for i, freq in enumerate(frequencies):
            bar_box = Gtk.VBox(spacing=8)
            bar_box.set_vexpand(True)  # Expand vertically
            bar_box.set_hexpand(True)  # Expand horizontally
            
            # Traditional equalizer bar using DrawingArea for full control
            bar = Gtk.DrawingArea()
            bar.get_style_context().add_class('eq-bar')
            bar.set_vexpand(True)  # Expand vertically
            bar.set_hexpand(True)  # Expand horizontally
            bar.set_halign(Gtk.Align.FILL)  # Fill horizontally
            bar.set_valign(Gtk.Align.FILL)  # Fill vertically
            bar.set_tooltip_text(f"{freq} Hz")
            # Only set height, let width expand to fill available space
            bar.set_size_request(-1, 100)  # -1 means no width constraint, 100 height
            bar.connect("draw", self.draw_eq_bar, i)
            self.eq_bars.append(bar)
            
            # Make bar clickable for adjustment
            event_box = Gtk.EventBox()
            event_box.add(bar)
            event_box.set_vexpand(True)  # Expand vertically
            event_box.set_hexpand(True)  # Expand horizontally
            event_box.set_halign(Gtk.Align.FILL)  # Fill horizontally
            event_box.set_valign(Gtk.Align.FILL)  # Fill vertically
            # Only set height, let width expand to fill available space
            event_box.set_size_request(-1, 100)  # -1 means no width constraint, 100 height
            event_box.connect("button-press-event", self.on_eq_bar_clicked, i)
            event_box.connect("motion-notify-event", self.on_eq_bar_drag, i)
            event_box.set_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
            
            # Pack the event box to fill the full width
            bar_box.pack_start(event_box, True, True, 0)
            
            # Frequency label
            freq_label = Gtk.Label(label=freq)
            freq_label.get_style_context().add_class('status-indicator')
            freq_label.set_size_request(-1, 20)
            freq_label.set_halign(Gtk.Align.CENTER)  # Center the frequency label
            bar_box.pack_start(freq_label, False, False, 0)
            
            # Pack the bar box to fill the full width in the frequency box
            freq_box.pack_start(bar_box, True, True, 0)
        
        eq_box.pack_start(freq_box, False, False, 0)
        eq_frame.add(eq_box)
        
        return eq_frame

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
    
    def on_eq_bar_clicked(self, widget, event, index):
        """Handle equalizer bar click"""
        if event.button == 1:  # Left mouse button
            # Calculate value based on click position
            allocation = widget.get_allocation()
            height = allocation.height
            if height <= 0:
                return True
            value = max(0.0, min(1.0, 1.0 - (event.y / height)))  # Inverted bar
            self.eq_values[index] = value
            self._apply_eq_band(index)
            widget.queue_draw()  # move the gain marker
            self.schedule_save_config()
        elif event.button == 3:  # Right mouse button: preset menu
            self.show_eq_preset_menu(event)
        return True

    def on_eq_bar_drag(self, widget, event, index):
        """Handle equalizer bar drag"""
        if event.state & Gdk.ModifierType.BUTTON1_MASK:  # Left mouse button pressed
            allocation = widget.get_allocation()
            height = allocation.height
            if height <= 0:
                return True
            value = max(0.0, min(1.0, 1.0 - (event.y / height)))  # Inverted bar
            self.eq_values[index] = value
            self._apply_eq_band(index)
            widget.queue_draw()  # move the gain marker
            self.schedule_save_config()
        return True
    
    def draw_eq_bar(self, widget, cr, index):
        """Flat LED-ladder bar: dark well, hairline border, segmented solid-green
        level fill, faint center tick, near-white gain marker."""
        allocation = widget.get_allocation()
        width = allocation.width
        height = allocation.height

        # Well background
        cr.set_source_rgb(0.020, 0.024, 0.020)  # #050605
        cr.rectangle(0, 0, width, height)
        cr.fill()

        # Crisp 1px hairline border (#2b302c)
        cr.set_source_rgb(0.169, 0.188, 0.173)
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, width - 1, height - 1)
        cr.stroke()

        # Faint 0 dB center tick
        cr.set_source_rgba(1, 1, 1, 0.10)
        cr.rectangle(2, height / 2 - 0.5, width - 4, 1)
        cr.fill()

        # Level fill: segmented LED ladder, solid accent green, bottom-up
        value = self.spectrum_levels[index] if index < len(self.spectrum_levels) else 0.0
        if value > 0:
            lit = int(value * (height - 4))
            top_limit = (height - 2) - lit
            cr.set_source_rgb(0.102, 0.878, 0.0)  # #1ae000
            y = height - 2
            while y - 3 >= top_limit:
                cr.rectangle(2, y - 3, width - 4, 3)
                y -= 5  # 3px segment + 2px gap
            cr.fill()

        # Gain marker: near-white (never vanishes against the green fill), kept
        # translucent so ten flat bars don't read as a wall of white lines.
        # Dimmed further while the EQ is bypassed.
        gain = self.eq_values[index] if index < len(self.eq_values) else 0.5
        marker_y = 2 + (1.0 - gain) * (height - 4)
        cr.set_source_rgba(0.941, 1.0, 0.941, 0.55 if self.eq_enabled else 0.2)
        cr.rectangle(2, marker_y - 1, width - 4, 2)
        cr.fill()

        return False

    def draw_preamp_bar(self, widget, cr):
        """Preamp: same visual language as the band bars — well, hairline,
        center tick, marker — but no spectrum fill (it isn't a band)."""
        allocation = widget.get_allocation()
        width = allocation.width
        height = allocation.height

        cr.set_source_rgb(0.020, 0.024, 0.020)  # well #050605
        cr.rectangle(0, 0, width, height)
        cr.fill()
        cr.set_source_rgb(0.169, 0.188, 0.173)  # hairline #2b302c
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, width - 1, height - 1)
        cr.stroke()
        cr.set_source_rgba(1, 1, 1, 0.10)       # unity (0 dB) tick
        cr.rectangle(2, height / 2 - 0.5, width - 4, 1)
        cr.fill()

        # Gain fill from center to the marker so the amount of boost/cut reads
        # at a glance (accent green up, dimmer green down)
        marker_y = 2 + (1.0 - self.preamp_value) * (height - 4)
        mid = height / 2
        if self.eq_enabled and abs(marker_y - mid) > 1:
            cr.set_source_rgba(0.102, 0.878, 0.0, 0.35)
            top = min(marker_y, mid)
            cr.rectangle(2, top, width - 4, abs(marker_y - mid))
            cr.fill()

        cr.set_source_rgba(0.941, 1.0, 0.941, 0.55 if self.eq_enabled else 0.2)
        cr.rectangle(2, marker_y - 1, width - 4, 2)
        cr.fill()
        return False

    def _set_preamp_from_y(self, widget, y):
        height = widget.get_allocation().height
        if height <= 0:
            return
        self.preamp_value = max(0.0, min(1.0, 1.0 - (y / height)))
        self._apply_preamp()
        self.preamp_bar.queue_draw()
        self.schedule_save_config()

    def on_preamp_clicked(self, widget, event):
        if event.button == 1:
            self._set_preamp_from_y(widget, event.y)
        elif event.button == 3:
            self.show_eq_preset_menu(event)
        return True

    def on_preamp_drag(self, widget, event):
        if event.state & Gdk.ModifierType.BUTTON1_MASK:
            self._set_preamp_from_y(widget, event.y)
        return True
    
    
    def log_debug(self, message):
        """Debug logging, opt-in via LLAMAAMP_DEBUG=1"""
        if DEBUG:
            print(f"DEBUG: {message}")
    
    
    def create_playlist(self):
        playlist_frame = Gtk.Frame()
        playlist_frame.get_style_context().add_class('music-player-frame')
        
        playlist_box = Gtk.VBox(spacing=20)
        playlist_box.set_margin_top(10)
        playlist_box.set_margin_bottom(10)
        playlist_box.set_margin_start(10)
        playlist_box.set_margin_end(10)
        
        # Playlist header
        header_box = Gtk.HBox()
        playlist_label = Gtk.Label(label="PLAYLIST")
        playlist_label.get_style_context().add_class('music-player-label')
        playlist_label.set_halign(Gtk.Align.START)
        header_box.pack_start(playlist_label, False, False, 0)
        
        self.playlist_info = Gtk.Label(label="0 files")
        self.playlist_info.get_style_context().add_class('music-player-label')
        header_box.pack_end(self.playlist_info, False, False, 0)
        
        playlist_box.pack_start(header_box, False, False, 0)

        # Type-to-find: scrolls to matches without filtering the model
        # (a TreeModelFilter would break drag-reorder and index arithmetic)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search playlist…")
        self.search_entry.get_style_context().add_class('playlist-search')
        self.search_entry.connect("search-changed",
                                  lambda e: self._search_step(restart=True))
        self.search_entry.connect("activate",
                                  lambda e: self._search_step(restart=False))
        self.search_entry.connect("stop-search", self._search_escape)
        playlist_box.pack_start(self.search_entry, False, False, 0)

        # Scrollable playlist
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_size_request(-1, 200)  # Reduced height to make room for debug area
        
        self.playlist_store = Gtk.ListStore(str, str, int, str)  # path, display, number, duration
        self.playlist_view = Gtk.TreeView(model=self.playlist_store)
        self.playlist_view.get_style_context().add_class('playlist')
        self.playlist_view.set_can_focus(True)  # Allow keyboard focus
        
        # Track number column
        track_renderer = Gtk.CellRendererText()
        track_column = Gtk.TreeViewColumn("", track_renderer, text=2)
        track_column.set_cell_data_func(track_renderer, self._zebra_bg)
        track_column.set_min_width(60)
        self.playlist_view.append_column(track_column)
        
        # Song name column
        song_renderer = Gtk.CellRendererText()
        song_column = Gtk.TreeViewColumn("", song_renderer, text=1)
        song_column.set_cell_data_func(song_renderer, self._zebra_bg)
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
        buttons_box = Gtk.HBox(spacing=15, homogeneous=True)
        
        add_btn = Gtk.Button(label="ADD FILES")
        add_btn.get_style_context().add_class('music-player-text-button')
        add_btn.set_tooltip_text("Add Files to Playlist")
        add_btn.connect("clicked", self.add_files)
        
        remove_btn = Gtk.Button(label="REMOVE")
        remove_btn.get_style_context().add_class('music-player-text-button')
        remove_btn.set_tooltip_text("Remove Selected from Playlist")
        remove_btn.connect("clicked", self.remove_selected)
        
        clear_btn = Gtk.Button(label="CLEAR")
        clear_btn.get_style_context().add_class('music-player-text-button')
        clear_btn.set_tooltip_text("Clear Playlist")
        clear_btn.connect("clicked", self.clear_playlist)
        
        buttons_box.pack_start(add_btn, True, True, 0)
        buttons_box.pack_start(remove_btn, True, True, 0)
        buttons_box.pack_start(clear_btn, True, True, 0)

        playlist_box.pack_start(buttons_box, False, False, 0)

        # Second row: folder add, missing-file cleanup, M3U export
        tools_box = Gtk.HBox(spacing=15, homogeneous=True)

        folder_btn = Gtk.Button(label="ADD DIR")
        folder_btn.get_style_context().add_class('music-player-text-button')
        folder_btn.set_tooltip_text("Add a folder (recursive)")
        folder_btn.connect("clicked", self.add_folder)

        missing_btn = Gtk.Button(label="FIX LIST")
        missing_btn.get_style_context().add_class('music-player-text-button')
        missing_btn.set_tooltip_text("Remove missing files from the playlist")
        missing_btn.connect("clicked", self.remove_missing)

        export_btn = Gtk.Button(label="EXPORT")
        export_btn.get_style_context().add_class('music-player-text-button')
        export_btn.set_tooltip_text("Export playlist as M3U")
        export_btn.connect("clicked", self.export_m3u)

        tools_box.pack_start(folder_btn, True, True, 0)
        tools_box.pack_start(missing_btn, True, True, 0)
        tools_box.pack_start(export_btn, True, True, 0)

        playlist_box.pack_start(tools_box, False, False, 0)

        playlist_frame.add(playlist_box)
        return playlist_frame
    
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
        self.current_song = None
        self._set_title_text("❌ No available files in playlist")
        self.info_label.set_text("All files are missing - please re-add music files")
    
    def stop_song(self, button):
        self._gapless_next = None
        self.player.set_state(Gst.State.NULL)
        self._sync_play_ui(False)
        self.position = 0
        self.position_scale.set_value(0)
        self.time_display.set_text("00:00")
    
    def next_song(self, button):
        if not self.playlist:
            return
        self._pending_seek_ns = None  # manual nav cancels resume-seek
        playable = [i for i, p in enumerate(self.playlist) if self._playable(p)]
        if self.shuffle == SHUFFLE_ALBUMS and playable:
            nxt = self._album_next_index(playable)
        elif self.shuffle == SHUFFLE_TRACKS and len(self.playlist) > 1:
            nxt = self.current_index
            while nxt == self.current_index:
                nxt = random.randint(0, len(self.playlist) - 1)
        elif self.shuffle != SHUFFLE_OFF:
            nxt = self.current_index
        else:
            nxt = self.current_index + 1
            if nxt >= len(self.playlist):
                if self.repeat_mode == REPEAT_ALL:
                    nxt = 0
                else:
                    self.stop_song(None)
                    return
        self.current_index = nxt
        self.load_song(self.current_index)
        if self.is_playing:
            self.player.set_state(Gst.State.PLAYING)
            self._sync_play_ui(True)

    def previous_song(self, button):
        if not self.playlist:
            return
        self._pending_seek_ns = None  # manual nav cancels resume-seek
        playable = [i for i, p in enumerate(self.playlist) if self._playable(p)]
        if self.shuffle == SHUFFLE_ALBUMS and playable:
            prv = self._album_prev_index(playable)
        elif self.shuffle == SHUFFLE_TRACKS and len(self.playlist) > 1:
            prv = self.current_index
            while prv == self.current_index:
                prv = random.randint(0, len(self.playlist) - 1)
        elif self.shuffle != SHUFFLE_OFF:
            prv = self.current_index
        else:
            prv = self.current_index - 1
            if prv < 0:
                prv = len(self.playlist) - 1 if self.repeat_mode == REPEAT_ALL else 0
        self.current_index = prv
        self.load_song(self.current_index)
        if self.is_playing:
            self.player.set_state(Gst.State.PLAYING)
            self._sync_play_ui(True)
    
    # ---- Marquee title ----

    def _set_title_text(self, text):
        """Central song-title setter: short titles display plainly; long ones
        scroll Winamp-style through a fixed 44-char window."""
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
            self._marquee_id = GLib.timeout_add(MARQUEE_TICK_MS, self._marquee_tick)

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
            GLib.source_remove(self._marquee_id)
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
                self._sync_play_ui(False)
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
            self.player.set_property("uri", uri)
            self.current_song = file_path
            self._loaded_uri = uri
            self._post_load_ui(index, file_path)
        finally:
            self._loading = was_loading

    def _post_load_ui(self, index, file_path):
        """Refresh everything that presents the current track (shared between
        load_song and the gapless handoff commit)."""
        self._refresh_song_label()
        self._update_album_art(file_path)
        self.get_audio_properties(file_path)
        self.update_audio_display(file_path)
        self._select_row(index)
        self.schedule_save_config()
        self._scrobble_reset()
        self._mpris_notify_track()
        self._notify_track(getattr(self, '_title_full', '') or self._display_name(file_path))

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
        # Cycle OFF -> TRACKS -> ALBUMS -> OFF
        self.shuffle = (self.shuffle + 1) % 3
        self.update_shuffle_button()
        self.schedule_save_config()
        if self.current_song:
            self.update_audio_display()
        self._mpris_emit({'Shuffle': GLib.Variant('b', self.shuffle != SHUFFLE_OFF)})

    def toggle_repeat(self, button):
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
            GLib.idle_add(self._folder_walk_done, folder, found)

        threading.Thread(target=walk, daemon=True).start()

    def _folder_walk_done(self, folder, found):
        if found:
            self._add_paths(found, feedback=f"Added {len(found)} file(s)")
        else:
            self.show_drop_feedback(f"No audio files in {os.path.basename(folder)}")
        return False

    def remove_missing(self, button):
        """Purge playlist entries whose files no longer exist."""
        keep = [p for p in self.playlist
                if self._is_stream_url(p) or os.path.exists(p)]
        removed = len(self.playlist) - len(keep)
        if removed == 0:
            self.show_drop_feedback("No missing files")
            return
        current = self.current_song
        self.playlist = keep
        with self._store_guard():
            self.playlist_store.clear()
            for i, p in enumerate(keep):
                name = self._display_name(p)
                display = name if self.is_audio_file(p) else f"⚠ {name} [UNSUPPORTED]"
                self.playlist_store.append([p, display, i + 1, self._duration_str(p)])
        self.update_playlist_info()
        self.save_playlist()
        if current in self.playlist:
            self.current_index = self.playlist.index(current)
            self._select_row(self.current_index)
        elif current is not None:
            # The playing track itself was purged
            self.stop_song(None)
            self.current_song = None
            self.current_index = 0
            self._set_title_text(DEFAULT_SONG_TEXT)
            self.info_label.set_text("")
            self._set_album_art(None)
        self.schedule_save_config()
        self.show_drop_feedback(f"Removed {removed} missing file(s)")

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
        path = os.path.join(self.playlists_dir(), f"{name}.m3u")
        entries = self._parse_m3u(path)
        if not entries:
            self.show_drop_feedback(f"Playlist '{name}' is empty/unreadable")
            return
        self.stop_song(None)
        self.current_song = None
        self.current_index = 0
        self.playlist.clear()
        self._play_next.clear()
        with self._store_guard():
            self.playlist_store.clear()
        self._add_paths(entries, feedback=f"Loaded '{name}'")
        self._playlist_name = name
        self.schedule_save_config()
    
    def remove_selected(self, button):
        """Remove all selected rows. Playback is only touched if the playing
        track itself is removed; deleting other rows just adjusts bookkeeping."""
        selection = self.playlist_view.get_selection()
        model, paths = selection.get_selected_rows()
        if not paths:
            return
        indices = sorted((p.get_indices()[0] for p in paths), reverse=True)
        removing_current = self.current_index in indices
        removed_below = sum(1 for i in indices if i < self.current_index)
        first_removed = indices[-1]

        with self._store_guard():
            for i in indices:
                del self.playlist[i]
                it = model.get_iter(Gtk.TreePath(i))
                model.remove(it)

        self._renumber_rows()
        self._update_queue_markers()
        self.update_playlist_info()
        self.save_playlist()

        if not self.playlist:
            self.stop_song(None)
            self.current_song = None
            self.current_index = 0
            self._set_title_text(DEFAULT_SONG_TEXT)
            self.info_label.set_text("")
            self._set_album_art(None)
        elif removing_current:
            self.stop_song(None)
            new_index = min(first_removed, len(self.playlist) - 1)
            self.load_song(new_index)
        else:
            # Keep pointing at the same (still-present) track; just fix the index
            self.current_index -= removed_below
            self._select_row(self.current_index)
        self.schedule_save_config()

    def clear_playlist(self, button):
        self.playlist.clear()
        self._play_next.clear()
        with self._store_guard():
            self.playlist_store.clear()
        self.stop_song(None)
        self.current_song = None
        self.current_index = 0
        self._set_title_text(DEFAULT_SONG_TEXT)
        self.info_label.set_text("")
        self._set_album_art(None)
        self.update_playlist_info()
        self.save_playlist()  # Save empty playlist
    
    def update_playlist_info(self):
        count = len(self.playlist)
        base = "1 file" if count == 1 else f"{count} files"
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
        GLib.idle_add(self._resync_playlist_from_store)

    def _zebra_bg(self, column, cell, model, it, data):
        """Zebra-stripe odd playlist rows. Cell data funcs run at draw time by
        path, so reorder/insert/delete restripe automatically. Selected rows are
        left alone so the CSS :selected color wins."""
        path = model.get_path(it)
        if self.playlist_view.get_selection().path_is_selected(path):
            cell.set_property('cell-background-set', False)
        elif path.get_indices()[0] % 2:
            cell.set_property('cell-background', '#071007')
        else:
            cell.set_property('cell-background-set', False)

    def _renumber_rows(self):
        """Rewrite the track-number column (col 2) to match current row order."""
        it = self.playlist_store.get_iter_first()
        n = 0
        while it is not None:
            n += 1
            self.playlist_store.set_value(it, 2, n)
            it = self.playlist_store.iter_next(it)

    def _resync_playlist_from_store(self):
        """Rebuild self.playlist from the store after a drag-reorder, keeping the
        playing track and renumbering rows, then persist the new order."""
        self._reordering = False
        new_playlist = []
        it = self.playlist_store.get_iter_first()
        while it is not None:
            new_playlist.append(self.playlist_store.get_value(it, 0))
            it = self.playlist_store.iter_next(it)
        self.playlist = new_playlist
        self._renumber_rows()
        self._update_queue_markers()
        if self.current_song in self.playlist:
            self.current_index = self.playlist.index(self.current_song)
        self.update_playlist_info()
        self.save_playlist()
        self.schedule_save_config()
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

    def _search_step(self, restart):
        """Scroll to the next playlist row matching the search text."""
        query = self.search_entry.get_text().strip().lower()
        ctx = self.search_entry.get_style_context()
        ctx.remove_class('search-miss')
        if not query:
            return
        n = len(self.playlist_store)
        if n == 0:
            return
        start = 0 if restart else (self._search_pos + 1) % n
        for off in range(n):
            i = (start + off) % n
            row = self.playlist_store[i]
            if query in row[1].lower():
                self._search_pos = i
                self._select_row(i)
                return
        ctx.add_class('search-miss')

    def _search_escape(self, entry):
        entry.set_text("")
        self._search_pos = -1
        self.playlist_view.grab_focus()

    def _update_queue_markers(self):
        """Prefix queued rows with '»' so Play Next targets are visible."""
        queued = set(self._play_next)
        it = self.playlist_store.get_iter_first()
        while it is not None:
            path = self.playlist_store.get_value(it, 0)
            display = self.playlist_store.get_value(it, 1)
            base = display[2:] if display.startswith("» ") else display
            want = f"» {base}" if path in queued else base
            if want != display:
                self.playlist_store.set_value(it, 1, want)
            it = self.playlist_store.iter_next(it)

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
        sel_paths = [model.get_value(model.get_iter(p), 0) for p in paths]

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
        ret = self.player.set_state(Gst.State.PLAYING)
        self._pipeline_live = (ret == Gst.StateChangeReturn.NO_PREROLL)
        self._sync_play_ui(True)

    def _play_index(self, index):
        self.load_song(index)
        self._play_current()

    def _queue_paths(self, paths):
        for p in paths:
            if p not in self._play_next:
                self._play_next.append(p)
        self._update_queue_markers()
        self.show_drop_feedback(f"Queued {len(paths)} track(s)")

    def _unqueue_paths(self, paths):
        for p in paths:
            try:
                self._play_next.remove(p)
            except ValueError:
                pass
        self._update_queue_markers()

    def on_playlist_activated(self, treeview, path, column):
        self._play_index(path.get_indices()[0])

    def on_playlist_selection_changed(self, selection):
        """Update the metadata display — only for a single-row selection."""
        model, paths = selection.get_selected_rows()
        if len(paths) != 1:
            return
        index = paths[0].get_indices()[0]
        if 0 <= index < len(self.playlist):
            file_path = self.playlist[index]
            song_name = self._display_name(file_path)
            self._set_title_text(f"{song_name}")

            # Get audio properties for the selected file
            self.get_audio_properties(file_path)
            self.update_audio_display(file_path)
    
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
        """Arm the bar-decay timer (idempotent). It removes itself at rest, so
        there is no permanent wakeup while idle."""
        if getattr(self, '_decay_id', None) is None:
            self._decay_id = GLib.timeout_add(60, self.animate_equalizer)

    def animate_equalizer(self):
        """When stopped/paused the spectrum stops posting, so decay the bars to
        rest — then stop ticking entirely."""
        if self.is_playing:
            self._decay_id = None
            return False
        changed = False
        for i in range(len(self.spectrum_levels)):
            if self.spectrum_levels[i] > 0.0:
                self.spectrum_levels[i] = max(0.0, self.spectrum_levels[i] - SPECTRUM_DECAY)
                changed = True
        if changed:
            for bar in self.eq_bars:
                bar.queue_draw()
            return True
        self._decay_id = None
        return False
    
    def get_audio_properties(self, file_path):
        """Populate audio_properties for file_path. Uses a cached value if present,
        otherwise shows sensible defaults now and queues an off-thread probe."""
        if self._is_stream_url(file_path):
            self.audio_properties = {'sample_rate': 44100, 'bitrate': 128,
                                     'channels': 2}
            return  # never enqueue a Discoverer probe for a live stream
        cached = self._cache_get(self._meta_cache, file_path)
        if isinstance(cached, dict):
            self.audio_properties = dict(cached)
            return

        # Immediate extension-based defaults so the UI isn't blank while we probe.
        file_ext = os.path.splitext(file_path)[1].lower()
        defaults = {
            '.flac': {'sample_rate': 44100, 'bitrate': 1000, 'channels': 2},
            '.wav':  {'sample_rate': 44100, 'bitrate': 1411, 'channels': 2},
            '.ogg':  {'sample_rate': 44100, 'bitrate': 192, 'channels': 2},
        }
        self.audio_properties = dict(defaults.get(file_ext, {'sample_rate': 44100, 'bitrate': 128, 'channels': 2}))

        # cached is False when a previous probe failed: don't probe again
        if cached is False or file_path in self._probe_inflight:
            return
        self._probe_inflight.add(file_path)
        # Snapshot rides with the job so the worker never reads live main-thread state
        self._probe_queue.put(('meta', file_path, dict(self.audio_properties)))

    def _probe_loop(self):
        """Single worker draining metadata probes and folder-art scans."""
        while True:
            job = self._probe_queue.get()
            try:
                if job[0] == 'meta':
                    self._probe_one(job[1], job[2])
                elif job[0] == 'folderart':
                    self._folder_art_scan(job[1])
            except Exception as e:
                self.log_debug(f"probe worker error: {e}")

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
                props['sample_rate'] = a.get_sample_rate() or 44100
                props['channels'] = a.get_channels() or 2
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
        GLib.idle_add(self._probe_done, file_path, result, art_bytes)

    def _probe_done(self, file_path, result, art_bytes):
        """Store a probe result on the UI thread; apply if still relevant.
        result is a props dict, or False to negative-cache a failed probe."""
        self._probe_inflight.discard(file_path)
        self._cache_put(self._meta_cache, file_path, result)
        if isinstance(result, dict) and result.get('duration'):
            self._note_duration(file_path, result['duration'])
        if art_bytes and self._cache_get(self._art_cache, file_path) is None:
            self._apply_art_bytes(file_path, art_bytes)
        if not isinstance(result, dict):
            return False
        if file_path != self.current_song and file_path != self._selected_path():
            return False
        for k in ('sample_rate', 'bitrate', 'channels'):
            if k in result:
                self.audio_properties[k] = result[k]
        if result.get('title') and file_path == self.current_song:
            self._refresh_song_label()
            self._mpris_notify_track()
        self.update_audio_display()
        return False

    def _selected_path(self):
        try:
            model, paths = self.playlist_view.get_selection().get_selected_rows()
            if len(paths) == 1:
                return model.get_value(model.get_iter(paths[0]), 0)
        except Exception:
            pass
        return None
    
    # ---- Album art ----

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
        GLib.idle_add(self._folder_art_done, directory, pixbuf)

    def _folder_art_done(self, directory, pixbuf):
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
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "musicPlayer.svg"),
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
        sample_rate_khz = sample_rate // 1000
        
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
        channel_text = "Mono" if channels == 1 else "Stereo"
        status_parts = [f"{file_type} • {sample_rate_khz}kHz • {bitrate}kbps • {channel_text}"]
        
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
                    display_name = self._display_name(file_path)
                    self.playlist_store.append([file_path, display_name, i + 1, self._duration_str(file_path)])
                self.playlist_view.set_model(self.playlist_store)

            self.update_playlist_info()
            GLib.idle_add(self._check_missing_chunk, 0)
            self._queue_missing_durations()

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
            GLib.idle_add(self._check_missing_chunk, end)
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