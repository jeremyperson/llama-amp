"""The Llama Amp window: composes the mixins and owns startup and shutdown."""
import os
import signal
import sys
import threading
from collections import OrderedDict, deque

from gi.repository import GLib, Gdk, Gst, Gtk

from .analyzer import AnalyzerState, AnalyzerViewMixin, ScopeState
from .config import ConfigMixin
from .constants import (
    APP_NAME,
    DEBUG,
    IS_FLATPAK,
    POSITION_TIMER_MS,
    REPEAT_OFF,
    SEEK_STEP_SECONDS,
    UPDATE_CHECK_DELAY_S,
    UPDATE_RECHECK_S,
    VOLUME_STEP,
    WINDOW_H,
    WINDOW_W,
)
from .desktop import DesktopMixin
from .engine import EngineMixin
from .metadata import MetadataMixin, MetadataWorker
from .mpris import MprisMixin
from .order import PlaybackOrder
from .playlist import PlaylistMixin
from .scrobble import ScrobbleMixin
from .tasks import MainLoopTasks
from .ui.controls import ControlsMixin
from .ui.menus import MenusMixin
from .ui.playlist_view import PlaylistViewMixin
from .ui.window import WindowMixin
from .updates import UpdatesMixin


class MusicPlayer(EngineMixin, ConfigMixin, MetadataMixin, PlaylistMixin, MprisMixin,
                  DesktopMixin, ScrobbleMixin, UpdatesMixin, AnalyzerViewMixin, WindowMixin,
                  MenusMixin, ControlsMixin, PlaylistViewMixin, Gtk.Window):
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
        self._scaled_widgets = []       # (widget, width, height) sized in 1x pixels
        self._scaled_columns = []
        self.scope_state = ScopeState()
        self._scope_active = False      # read by the streaming-thread probe
        self._scope_last = 0.0
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
        self._playlist_tags = {}    # path -> artist/album/disc/track for sorting
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

    def on_window_key_press(self, widget, event):
        """Global shortcuts: Space play/pause, Winamp's Z/X/C/V/B transport,
        arrows seek/volume, S shuffle, R repeat, J jump to file, Ctrl+J jump to
        time, Ctrl+T elapsed/remaining, Ctrl+D double size, Ctrl+O add files,
        Ctrl+L open URL.
        Delete/Backspace propagate to the playlist."""
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
        if ctrl and key in (Gdk.KEY_j, Gdk.KEY_J):
            self.show_jump_to_time_dialog()
            return True
        if ctrl and key in (Gdk.KEY_t, Gdk.KEY_T):
            self._toggle_time_mode()
            return True
        if ctrl and key in (Gdk.KEY_d, Gdk.KEY_D):
            self.toggle_double_size()
            return True
        if key == Gdk.KEY_space:
            self.toggle_play_pause(None)
            return True
        transport = {Gdk.KEY_z: self.previous_song, Gdk.KEY_x: self.play_from_start,
                     Gdk.KEY_c: self.pause_toggle, Gdk.KEY_v: self.stop_song,
                     Gdk.KEY_b: self.next_song}
        action = transport.get(Gdk.keyval_to_lower(key))
        if action is not None and not ctrl:
            action(None)
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

    def log_debug(self, message):
        """Debug logging, opt-in via LLAMAAMP_DEBUG=1"""
        if DEBUG:
            print(f"DEBUG: {message}")



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

