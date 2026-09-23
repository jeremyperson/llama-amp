"""Winamp's File Info window (Alt+3): read-only details for one entry."""
import os
import threading

from gi.repository import Gtk, Pango

from ..fileinfo import read_file_info


class FileInfoMixin:
    def _file_info_target(self):
        """The selected playlist entry, else the current track."""
        model, paths = self.playlist_view.get_selection().get_selected_rows()
        return model[paths[0]][0] if paths else self.current_song

    def show_file_info(self, *_args, path=None):
        path = path or self._file_info_target()
        if not path:
            self.show_drop_feedback("Select a track for File Info")
            return
        if self._file_info_dialog is not None:
            self._file_info_dialog.destroy()
        name = path if self._is_stream_url(path) else os.path.basename(path)
        dialog = Gtk.Dialog(title=f"File Info — {name}", transient_for=self)
        dialog.set_destroy_with_parent(True)
        dialog.add_button("_Close", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(440, -1)
        dialog.fields = {}
        box = dialog.get_content_area()
        box.set_margin_top(10); box.set_margin_bottom(10)
        box.set_margin_start(12); box.set_margin_end(12)
        box.set_spacing(10)
        header = Gtk.Box(spacing=12)
        dialog.art = Gtk.Image()
        header.pack_start(dialog.art, False, False, 0)
        title = Gtk.Label(label=self._display_name(path), xalign=0)
        title.set_line_wrap(True)
        title.get_style_context().add_class('song-title')
        header.pack_start(title, True, True, 0)
        box.pack_start(header, False, False, 0)
        dialog.grid = Gtk.Grid(column_spacing=12, row_spacing=3)
        dialog.grid.attach(Gtk.Label(label="Reading…", xalign=0), 0, 0, 2, 1)
        box.pack_start(dialog.grid, False, False, 0)
        self._set_file_info_art(dialog, path, None)
        dialog.connect('response', lambda d, _response: d.destroy())
        dialog.connect('destroy', self._file_info_closed)
        dialog.show_all()
        self._file_info_dialog = dialog
        threading.Thread(target=lambda: self.tasks.idle_add(self._file_info_ready, dialog, path,
                                                            read_file_info(path)),
                         daemon=True).start()

    def _file_info_closed(self, dialog):
        if self._file_info_dialog is dialog:
            self._file_info_dialog = None

    def _set_file_info_art(self, dialog, path, data):
        pixbuf = self._decode_art_pixbuf(data) if data else None
        if pixbuf is None and not self._is_stream_url(path):
            pixbuf = self._cache_get(self._art_cache, path)
        dialog.art.set_from_pixbuf(pixbuf or self._get_default_art())

    def _file_info_ready(self, dialog, path, info):
        if dialog is not self._file_info_dialog:
            return False    # closed or replaced while reading
        for child in dialog.grid.get_children():
            dialog.grid.remove(child)
        row = 0
        for section, rows in info['sections']:
            heading = Gtk.Label(xalign=0)
            heading.set_markup(f"<b>{section}</b>")
            heading.set_margin_top(6 if row else 0)
            dialog.grid.attach(heading, 0, row, 2, 1)
            row += 1
            for label, value in rows:
                name = Gtk.Label(label=label, xalign=1, yalign=0)
                name.get_style_context().add_class('dim-label')
                text = Gtk.Label(label=value, xalign=0, selectable=True)
                text.set_line_wrap(True)
                text.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                text.set_max_width_chars(48)
                dialog.grid.attach(name, 0, row, 1, 1)
                dialog.grid.attach(text, 1, row, 1, 1)
                dialog.fields[label] = text
                row += 1
        self._set_file_info_art(dialog, path, info['art'])
        dialog.grid.show_all()
        return False
