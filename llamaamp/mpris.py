"""MPRIS2 D-Bus interface: media keys and desktop playback controls."""
import os

from gi.repository import GLib, Gio, Gst

from .constants import APP_NAME, REPEAT_ALL, REPEAT_OFF, REPEAT_ONE, SHUFFLE_OFF, SHUFFLE_TRACKS


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


class MprisMixin:
    def _mpris_notify_playback(self):
        self._mpris_emit({'PlaybackStatus': GLib.Variant('s', self._mpris_status())})

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
                    self._seek_ns(pos_us * 1000)
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

    def _mpris_notify_track(self):
        self._mpris_emit({
            'Metadata': self._mpris_metadata(),
            'PlaybackStatus': GLib.Variant('s', self._mpris_status()),
        })

