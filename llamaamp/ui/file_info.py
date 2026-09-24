"""Winamp's File Info window (Alt+3): details for one entry, and a tag editor
for files mutagen can write."""
import os
import threading

from gi.repository import GLib, Gtk, Pango

from ..fileinfo import read_file_info
from ..i18n import _
from ..paths import displayable, real_path
from ..tags import FIELDS, read_tags, write_tags

RESPONSE_EDIT, RESPONSE_SAVE = 1, 2


class FileInfoMixin:
    def _file_info_target(self):
        """The selected playlist entry, else the current track."""
        model, paths = self.playlist_view.get_selection().get_selected_rows()
        return real_path(model[paths[0]][0]) if paths else self.current_song

    def show_file_info(self, *_args, path=None):
        path = path or self._file_info_target()
        if not path:
            self.show_drop_feedback(_("Select a track for File Info"))
            return
        if self._file_info_dialog is not None:
            self._file_info_dialog.destroy()
        name = path if self._is_stream_url(path) else displayable(os.path.basename(path))
        dialog = Gtk.Dialog(title=_('File Info — {name}').format(name=name), transient_for=self)
        dialog.set_destroy_with_parent(True)
        dialog.edit_button = dialog.add_button(_("_Edit Tags"), RESPONSE_EDIT)
        dialog.save_button = dialog.add_button(_("_Save"), RESPONSE_SAVE)
        for button in (dialog.edit_button, dialog.save_button):
            button.set_no_show_all(True)
            button.hide()
        dialog.close_button = dialog.add_button(_("_Close"), Gtk.ResponseType.CLOSE)
        dialog.set_default_size(440, -1)
        dialog.fields = {}
        dialog.tags = None
        dialog.entries = {}
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
        dialog.grid.attach(Gtk.Label(label=_("Reading…"), xalign=0), 0, 0, 2, 1)
        box.pack_start(dialog.grid, False, False, 0)
        self._set_file_info_art(dialog, path, None)
        dialog.connect('response', self._file_info_response, path)
        dialog.connect('destroy', self._file_info_closed)
        dialog.show_all()
        self._file_info_dialog = dialog
        threading.Thread(target=lambda: self.tasks.idle_add(self._file_info_ready, dialog, path,
                                                            read_file_info(path), read_tags(path)),
                         daemon=True).start()

    def _file_info_response(self, dialog, response, path):
        if response == RESPONSE_EDIT:
            self._file_info_edit(dialog)
        elif response == RESPONSE_SAVE:
            self._file_info_save(dialog, path)
        elif dialog.entries and response == Gtk.ResponseType.CLOSE:
            self.show_file_info(path=path)          # Cancel leaves the editor
        else:
            dialog.destroy()

    def _file_info_closed(self, dialog):
        if self._file_info_dialog is dialog:
            self._file_info_dialog = None

    def _set_file_info_art(self, dialog, path, data):
        pixbuf = self._decode_art_pixbuf(data) if data else None
        if pixbuf is None and not self._is_stream_url(path):
            pixbuf = self._cache_get(self._art_cache, path)
        dialog.art.set_from_pixbuf(pixbuf or self._get_default_art())

    def _file_info_ready(self, dialog, path, info, tags=None):
        if dialog is not self._file_info_dialog:
            return False    # closed or replaced while reading
        for child in dialog.grid.get_children():
            dialog.grid.remove(child)
        row = 0
        for section, rows in info['sections']:
            heading = Gtk.Label(xalign=0)
            heading.set_markup(f"<b>{GLib.markup_escape_text(section)}</b>")
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
        dialog.tags = tags
        dialog.edit_button.set_visible(tags is not None)
        dialog.grid.show_all()
        return False

    def _file_info_edit(self, dialog):
        """Swap the details for entries holding the file's tags."""
        for child in dialog.grid.get_children():
            dialog.grid.remove(child)
        for row, (key, label) in enumerate(FIELDS):
            name = Gtk.Label(label=_(label), xalign=1)
            name.get_style_context().add_class('dim-label')
            entry = Gtk.Entry(text=dialog.tags.get(key, ''), hexpand=True, activates_default=True)
            dialog.grid.attach(name, 0, row, 1, 1)
            dialog.grid.attach(entry, 1, row, 1, 1)
            dialog.entries[key] = entry
        hint = Gtk.Label(label=_('Separate several artists or genres with “;”.'), xalign=0)
        hint.get_style_context().add_class('dim-label')
        dialog.grid.attach(hint, 1, len(FIELDS), 1, 1)
        dialog.error = Gtk.Label(xalign=0, wrap=True)
        dialog.error.get_style_context().add_class('error')
        dialog.grid.attach(dialog.error, 0, len(FIELDS) + 1, 2, 1)
        dialog.edit_button.hide()
        dialog.save_button.show()
        dialog.save_button.set_can_default(True)
        dialog.save_button.grab_default()
        dialog.close_button.set_label(_("_Cancel"))
        dialog.grid.show_all()
        dialog.entries['title'].grab_focus()

    def _file_info_save(self, dialog, path):
        values = {key: entry.get_text() for key, entry in dialog.entries.items()}
        try:
            write_tags(path, values)
        except Exception as exc:
            self.log_debug(f"tag write failed for {displayable(os.path.basename(path))}: {exc}")
            dialog.error.set_text(_("Couldn't save the tags: {reason}").format(
                reason=getattr(exc, 'strerror', None) or type(exc).__name__))
            return
        self._tags_edited(path)
        self.show_file_info(path=path)
        self.show_drop_feedback(_("Tags saved"))

    def _tags_edited(self, path):
        """Re-read a file whose tags changed: playlist title, now-playing
        details and the library index all follow."""
        self._meta_cache.pop(path, None)
        if path not in self._probe_inflight:
            self._probe_inflight.add(path)
            self._probe_queue.put(('meta', path, {'sample_rate': 0, 'bitrate': 0, 'channels': 0}))
        self.library_file_changed(path)
