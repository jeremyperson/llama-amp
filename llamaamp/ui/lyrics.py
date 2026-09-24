"""The Lyrics window (Alt+Y): synced lines highlight as the track plays."""
import os

from gi.repository import Gdk, Gst, Gtk, Pango

from ..i18n import _
from ..paths import displayable
from ..lyrics import find_lyrics


class LyricsMixin:
    def show_lyrics(self, *_args):
        if self._lyrics_window is None:
            self._lyrics_window = LyricsWindow(self)
            self._lyrics_window.connect('destroy', self._lyrics_closed)
            self._lyrics_window.load(self.current_song)
        self._lyrics_window.present()

    def _lyrics_closed(self, _window):
        self._lyrics_window = None

    def _lyrics_track_changed(self):
        if self._lyrics_window is not None:
            self._lyrics_window.load(self.current_song)

    def _lyrics_tick(self, position_ns):
        if self._lyrics_window is not None:
            self._lyrics_window.follow(position_ns / Gst.SECOND)


class LyricsWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title=_('Lyrics'))
        self.app = app
        self.lyrics = None
        self.line = None
        self.set_default_size(420, 520)
        self.set_icon_name('llama-amp')
        self.get_style_context().add_class('library-window')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.get_style_context().add_class('music-player-main')
        box.set_border_width(8)
        self.heading = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.heading.get_style_context().add_class('song-title')
        box.pack_start(self.heading, False, False, 0)
        self.view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.view.get_style_context().add_class('playlist')
        self.view.set_left_margin(10)
        self.view.set_right_margin(10)
        self.view.set_pixels_below_lines(4)
        buffer = self.view.get_buffer()
        buffer.create_tag('current', weight=Pango.Weight.BOLD, scale=1.15)
        buffer.create_tag('hint', style=Pango.Style.ITALIC)
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.add(self.view)
        box.pack_start(self.scroller, True, True, 0)
        self.source = Gtk.Label(xalign=0)
        self.source.get_style_context().add_class('muted')
        box.pack_start(self.source, False, False, 0)
        self.add(box)
        self.connect('key-press-event', self._key)
        self.show_all()

    def _key(self, _window, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def load(self, path):
        self.lyrics = find_lyrics(path) if path else None
        self.line = None
        name = self.app._display_name(path) if path else ''
        self.heading.set_text(getattr(self.app, '_title_full', '') or name)
        buffer = self.view.get_buffer()
        if self.lyrics is None:
            buffer.set_text('')
            hint = (_('No lyrics for this track.\n\nPut an .lrc file with the same name next to it '
                      '(for example "{name}.lrc"), or add lyrics to its tags.').format(
                        name=displayable(os.path.splitext(os.path.basename(path))[0]))
                    if path else _('Nothing is playing.'))
            buffer.insert_with_tags_by_name(buffer.get_start_iter(), hint, 'hint')
            self.source.set_text('')
            return
        buffer.set_text('\n'.join(text for _time, text in self.lyrics.lines))
        self.source.set_text((_('Synced lyrics') if self.lyrics.synced else _('Lyrics')) + ' · ' +
                             (_('from an .lrc file') if self.lyrics.source == 'file' else _('from the tags')))
        self.scroller.get_vadjustment().set_value(0)

    def follow(self, seconds):
        if self.lyrics is None or not self.lyrics.synced:
            return
        line = self.lyrics.current(seconds)
        if line == self.line:
            return
        buffer = self.view.get_buffer()
        buffer.remove_tag_by_name('current', buffer.get_start_iter(), buffer.get_end_iter())
        self.line = line
        if line < 0:
            return
        start = buffer.get_iter_at_line(line)
        end = start.copy()
        end.forward_to_line_end()
        buffer.apply_tag_by_name('current', start, end)
        self.view.scroll_to_iter(start, 0.0, True, 0.0, 0.4)
