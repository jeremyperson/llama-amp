"""The Media Library window (Alt+L) and its wiring into the player."""
import os
import time

from gi.repository import Gdk, GLib, Gtk, Pango

from ..constants import APP_NAME, N_
from ..i18n import _, ngettext
from ..library import queries
from ..library.db import LibraryDB
from ..library.scanner import Scanner
from ..playlist import added_feedback

SCAN_DELAY_S = 4          # let startup finish before the incremental rescan
SEARCH_DELAY_MS = 180
REFRESH_DURING_SCAN_S = 2     # while scanning, redraw the window at most this often
FACET_NAMES = {'genre': N_('Genre'), 'artist': N_('Artist'), 'album': N_('Album')}


def _clock(seconds):
    seconds = int(seconds or 0)
    if seconds >= 3600:
        return f'{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}'
    return f'{seconds // 60}:{seconds % 60:02d}'


class LibraryMixin:
    """Owns the library database, background scans and play counts."""

    def _open_library(self):
        self.library = LibraryDB(os.path.join(self._data_dir(), 'library.db'))
        self._library_window = None
        self._library_scan = None
        self._library_status = None
        if self.library.folders() and self.config.get('library_scan_on_start', True):
            self._timeout_ids.append(self.tasks.timeout_add_seconds(SCAN_DELAY_S, self._startup_library_scan))

    def _startup_library_scan(self):
        self.rescan_library()
        return False

    def rescan_library(self, *_args):
        if self._library_scan is not None and self._library_scan.is_alive():
            return
        folders = self.library.folders()
        if not folders:
            return
        self._library_status = {'scanned': 0, 'updated': 0, 'removed': 0, 'running': True}
        self._library_scan = Scanner(
            self.library, folders,
            on_progress=lambda stats: self.tasks.idle_add(self._library_progress, stats, True),
            on_finished=lambda stats: self.tasks.idle_add(self._library_progress, stats, False))
        self._library_scan.start()
        self._library_changed()

    def _library_progress(self, stats, running):
        if self._destroyed:
            return False
        self._library_status = dict(stats, running=running)
        now = time.monotonic()
        refresh = not running or now - getattr(self, '_library_refreshed', 0) >= REFRESH_DURING_SCAN_S
        if refresh:
            self._library_refreshed = now
        self._library_changed(refresh=refresh)
        return False

    def _library_changed(self, refresh=True):
        window = getattr(self, '_library_window', None)
        if window is not None:
            window.update_status()
            if refresh:
                window.refresh()

    def _listen_completed(self, path):
        """A track was listened to (half its length or four minutes)."""
        if getattr(self, 'library', None) is not None and self.library.record_play(path):
            self._library_changed()

    def show_library(self, *_args):
        if self._library_window is None:
            self._library_window = LibraryWindow(self)
            self._library_window.connect('destroy', self._library_closed)
        self._library_window.present()

    def _library_closed(self, _window):
        self._library_window = None

    def _close_library(self):
        if self._library_scan is not None:
            self._library_scan.cancel()
        if self._library_window is not None:
            self._library_window.destroy()
        self.library.close()

    # -- actions shared by the window -----------------------------------------
    def library_play(self, paths):
        if paths:
            self.replace_playlist(paths)
            self._play_index(0)
            self.show_drop_feedback(_('Playing {count} from the library — Ctrl+Z restores the playlist')
                                    .format(count=len(paths)))

    def library_enqueue(self, paths, play_next=False, play=False):
        if not paths:
            return
        first = len(self.playlist)
        added = self._add_paths(paths, feedback=added_feedback(len(paths)))
        if play_next:
            self._queue_paths(self.entry_ids[first:first + added])
        if play and added:
            self._play_index(first)


class LibraryWindow(Gtk.Window):
    """Views on the left; genre, artist and album lists narrowing the tracks
    below them; search on top."""

    def __init__(self, app):
        super().__init__(title=_('Media Library — {app}').format(app=APP_NAME))
        self.app = app
        self.view = 'all'
        self.filters = {}
        self._search_id = None
        self._refreshing = False
        self.set_default_size(980, 620)
        self.set_icon_name('llama-amp')
        self.connect('key-press-event', self._key)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        root.get_style_context().add_class('music-player-main')
        root.set_border_width(8)
        self.add(root)

        top = Gtk.Box(spacing=6)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text(_('Search title, artist, album, genre…'))
        self.search.get_style_context().add_class('playlist-search')
        self.search.connect('search-changed', self._search_changed)
        top.pack_start(self.search, True, True, 0)
        self.status = Gtk.Label(xalign=1)
        self.status.get_style_context().add_class('muted')
        top.pack_start(self.status, False, False, 6)
        for label, callback in ((_('Rescan'), self.app.rescan_library), (_('Folders…'), self.edit_folders)):
            button = Gtk.Button(label=label)
            button.connect('clicked', lambda _w, c=callback: c())
            top.pack_start(button, False, False, 0)
        root.pack_start(top, False, False, 0)

        self.stack = Gtk.Stack()
        root.pack_start(self.stack, True, True, 0)
        self.stack.add_named(self._empty_page(), 'empty')
        self.stack.add_named(self._browser_page(), 'browser')
        self.show_all()
        self.refresh()
        self.update_status()
        self.track_view.grab_focus()         # the search keeps its placeholder visible

    # -- layout ---------------------------------------------------------------
    def _empty_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER)
        title = Gtk.Label(label=_('Build your library'))
        title.get_style_context().add_class('song-title')
        box.pack_start(title, False, False, 0)
        hint = Gtk.Label(label=_('Add the folders where your music lives. {app} indexes their tags '
                                 'and keeps them up to date each time it starts.').format(app=APP_NAME),
                         justify=Gtk.Justification.CENTER, wrap=True, max_width_chars=60)
        hint.get_style_context().add_class('muted')
        box.pack_start(hint, False, False, 0)
        button = Gtk.Button(label=_('Add a music folder…'), halign=Gtk.Align.CENTER)
        button.connect('clicked', lambda _w: self.add_folder())
        box.pack_start(button, False, False, 0)
        return box

    def _browser_page(self):
        paned = Gtk.Paned()
        self.views = Gtk.ListBox()
        self.views.get_style_context().add_class('playlist')
        for key, (caption, *_rest) in queries.VIEWS.items():
            row = Gtk.ListBoxRow()
            row.view = key
            label = Gtk.Label(label=_(caption), xalign=0)
            label.set_margin_start(8); label.set_margin_end(8)
            label.set_margin_top(5); label.set_margin_bottom(5)
            row.add(label)
            self.views.add(row)
        self.views.connect('row-selected', self._view_selected)
        scroller = Gtk.ScrolledWindow()
        scroller.add(self.views)
        scroller.set_size_request(170, -1)
        paned.pack1(scroller, False, False)

        right = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        facets = Gtk.Box(spacing=6, homogeneous=True)
        self.facet_views = {}
        for name in queries.CASCADE:
            store = Gtk.ListStore(str, object, bool)      # caption, value, is "All" row
            view = Gtk.TreeView(model=store, headers_visible=True)
            view.get_style_context().add_class('playlist')
            view.append_column(Gtk.TreeViewColumn(_(FACET_NAMES[name]), Gtk.CellRendererText(
                ellipsize=Pango.EllipsizeMode.END), text=0))
            view.get_selection().connect('changed', self._facet_selected, name)
            self.facet_views[name] = view
            scroller = Gtk.ScrolledWindow()
            scroller.add(view)
            facets.pack_start(scroller, True, True, 0)
        facets.set_size_request(-1, 170)
        right.pack1(facets, False, False)

        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.tracks = Gtk.ListStore(str, str, str, str, str, str, int)   # path, #, title, artist, album, length, plays
        self.track_view = Gtk.TreeView(model=self.tracks)
        self.track_view.get_style_context().add_class('playlist')
        self.track_view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        for index, (caption, expand) in enumerate(((_('#'), False), (_('Title'), True), (_('Artist'), True),
                                                   (_('Album'), True), (_('Length'), False), (_('Plays'), False)),
                                                  start=1):
            renderer = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END if expand else Pango.EllipsizeMode.NONE)
            if not expand:
                renderer.set_property('xalign', 1.0)
            column = Gtk.TreeViewColumn(caption, renderer, text=index)
            column.set_expand(expand)
            column.set_resizable(expand)
            column.set_sort_column_id(index)
            self.track_view.append_column(column)
        self.track_view.connect('row-activated', self._track_activated)
        self.track_view.connect('button-press-event', self._track_press)
        self.track_view.enable_model_drag_source(Gdk.ModifierType.BUTTON1_MASK, [], Gdk.DragAction.COPY)
        self.track_view.drag_source_add_uri_targets()
        self.track_view.connect('drag-data-get', self._drag_data_get)
        scroller = Gtk.ScrolledWindow()
        scroller.add(self.track_view)
        bottom.pack_start(scroller, True, True, 0)
        actions = Gtk.Box(spacing=6)
        for label, callback in ((_('Play'), self.play), (_('Enqueue'), self.enqueue), (_('Play next'), self.play_next)):
            button = Gtk.Button(label=label)
            button.connect('clicked', lambda _w, c=callback: c())
            actions.pack_start(button, False, False, 0)
        self.count = Gtk.Label(xalign=1)
        self.count.get_style_context().add_class('muted')
        actions.pack_end(self.count, False, False, 0)
        bottom.pack_start(actions, False, False, 0)
        right.pack2(bottom, True, False)
        paned.pack2(right, True, False)
        self.views.select_row(self.views.get_row_at_index(0))
        return paned

    # -- data -----------------------------------------------------------------
    def refresh(self, changed=None):
        """Reload facets and tracks for the current view, search and filters.
        After a click in one facet list only the lists after it are rebuilt, so
        the list under the pointer keeps its scroll position."""
        if not self.app.library.folders():
            self.stack.set_visible_child_name('empty')
            return
        self.stack.set_visible_child_name('browser')
        search = self.search.get_text()
        names = queries.CASCADE[queries.CASCADE.index(changed) + 1:] if changed else queries.CASCADE
        self._refreshing = True
        try:
            for name in names:
                self._fill_facet(name, queries.facet(self.app.library, name, self.view, search, self.filters))
        finally:
            self._refreshing = False
        self._fill_tracks()

    def _fill_facet(self, name, values):
        view = self.facet_views[name]
        store = view.get_model()
        view.set_model(None)
        store.clear()
        total = sum(count for _value, count in values)
        store.append([_('All ({count})').format(count=total), None, True])
        selected = None
        unknown = _('(Unknown)')
        for value, count in values:
            # append(): insert_with_valuesv can't fill the Python-object value column
            it = store.append([f"{value if value is not None else unknown} ({count})", value, False])
            if name in self.filters and self.filters[name] == value:
                selected = it
        view.set_model(store)
        if name in self.filters and selected is None:
            del self.filters[name]              # the value vanished (new search)
        view.get_selection().select_iter(selected or store.get_iter_first())

    def _fill_tracks(self):
        rows = queries.tracks(self.app.library, self.view, self.search.get_text(), self.filters)
        self.track_view.set_model(None)          # much faster bulk loads
        self.tracks.clear()
        columns = list(range(7))
        insert = self.tracks.insert_with_valuesv   # several times faster than append()
        for track in rows:
            number = f"{track['disc']}.{track['track']:02d}" if track['disc'] and track['disc'] > 1 and track['track'] \
                else str(track['track'] or '')
            title = track['title'] or os.path.splitext(os.path.basename(track['path']))[0]
            insert(-1, columns, (track['path'], number, title, track['artist'] or '', track['album'] or '',
                                 _clock(track['duration']), track['plays']))
        self.track_view.set_model(self.tracks)
        seconds = sum(track['duration'] or 0 for track in rows)
        self.count.set_text(ngettext('{count} track · {length}', '{count} tracks · {length}', len(rows))
                            .format(count=len(rows), length=_clock(seconds)))

    def update_status(self):
        library, status = self.app.library, self.app._library_status
        text = ngettext('{count} track', '{count} tracks', library.count()).format(count=library.count())
        if status and status.get('running'):
            text = _('Scanning… {scanned} files checked').format(scanned=status['scanned'])
        elif status:
            text += ' · ' + _('up to date')
        self.status.set_text(text)

    # -- events -----------------------------------------------------------------
    def _view_selected(self, _box, row):
        if row is not None and row.view != self.view:
            self.view = row.view
            self.filters.clear()
            self.refresh()

    def _facet_selected(self, selection, name):
        if self._refreshing:
            return
        model, it = selection.get_selected()
        if it is None:
            return
        if model[it][2]:
            self.filters.pop(name, None)
        else:
            self.filters[name] = model[it][1]
        # Facets after this one depend on it
        for later in queries.CASCADE[queries.CASCADE.index(name) + 1:]:
            self.filters.pop(later, None)
        self.refresh(changed=name)

    def _search_changed(self, _entry):
        if self._search_id is not None:
            self.app.tasks.source_remove(self._search_id)
        self._search_id = self.app.tasks.timeout_add(SEARCH_DELAY_MS, self._run_search)

    def _run_search(self):
        self._search_id = None
        self.refresh()
        return False

    def _key(self, _window, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if event.state & Gdk.ModifierType.MOD1_MASK and event.keyval in (Gdk.KEY_l, Gdk.KEY_L):
            self.close()
            return True
        return False

    # -- actions ---------------------------------------------------------------
    def chosen_paths(self):
        """The selected tracks, else everything listed."""
        model, paths = self.track_view.get_selection().get_selected_rows()
        if paths:
            return [model[path][0] for path in paths]
        return [row[0] for row in self.tracks]

    def play(self):
        self.app.library_play(self.chosen_paths())

    def enqueue(self):
        self.app.library_enqueue(self.chosen_paths())

    def play_next(self):
        self.app.library_enqueue(self.chosen_paths(), play_next=True)

    def _track_activated(self, view, path, _column):
        self.app.library_enqueue([view.get_model()[path][0]], play=True)

    def _track_press(self, view, event):
        if event.button != 3:
            return False
        hit = view.get_path_at_pos(int(event.x), int(event.y))
        if hit is not None and not view.get_selection().path_is_selected(hit[0]):
            view.get_selection().unselect_all()
            view.get_selection().select_path(hit[0])
        menu = Gtk.Menu()
        for label, callback in ((_('Play'), self.play), (_('Enqueue'), self.enqueue),
                                (_('Play next'), self.play_next),
                                (_('File Info…'), lambda: self.app.show_file_info(path=self.chosen_paths()[0]))):
            item = Gtk.MenuItem(label=label)
            item.connect('activate', lambda _w, c=callback: c())
            menu.append(item)
        menu.show_all()
        self._menu = menu
        menu.popup_at_pointer(event)
        return True

    def _drag_data_get(self, view, _context, data, _info, _time):
        model, paths = view.get_selection().get_selected_rows()
        data.set_uris([GLib.filename_to_uri(model[path][0]) for path in paths])

    # -- folders ----------------------------------------------------------------
    def add_folder(self):
        dialog = Gtk.FileChooserDialog(title=_('Add Music Folder to Library'), transient_for=self,
                                       action=Gtk.FileChooserAction.SELECT_FOLDER)
        dialog.add_buttons(_('_Cancel'), Gtk.ResponseType.CANCEL, _('_Add'), Gtk.ResponseType.OK)
        music = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_MUSIC)
        if music:
            dialog.set_current_folder(music)
        if dialog.run() == Gtk.ResponseType.OK:
            self.app.library.add_folder(dialog.get_filename())
            self.refresh()
            self.app.rescan_library()
        dialog.destroy()

    def edit_folders(self):
        dialog = Gtk.Dialog(title=_('Library Folders'), transient_for=self, modal=True)
        dialog.add_button(_('_Close'), Gtk.ResponseType.CLOSE)
        dialog.set_default_size(460, 300)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(10)
        store = Gtk.ListStore(str)
        view = Gtk.TreeView(model=store, headers_visible=False)
        view.append_column(Gtk.TreeViewColumn('', Gtk.CellRendererText(), text=0))
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.add(view)
        box.pack_start(scroller, True, True, 0)

        def reload():
            store.clear()
            for folder in self.app.library.folders():
                store.append([folder])

        def remove(_button):
            model, it = view.get_selection().get_selected()
            if it is not None:
                self.app.library.remove_folder(model[it][0])
                reload()
                self.refresh()
                self.update_status()

        buttons = Gtk.Box(spacing=6)
        add = Gtk.Button(label=_('Add folder…'))
        add.connect('clicked', lambda _b: (self.add_folder(), reload()))
        drop = Gtk.Button(label=_('Remove'))
        drop.connect('clicked', remove)
        buttons.pack_start(add, False, False, 0)
        buttons.pack_start(drop, False, False, 0)
        box.pack_start(buttons, False, False, 0)
        reload()
        dialog.show_all()
        dialog.run()
        dialog.destroy()

