"""Playlist panel: tree view, search, jump dialog and row menus."""
from gi.repository import Gdk, Gtk, Pango

from ..constants import URI_TARGET_INFO


class PlaylistViewMixin:
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
        self._scaled_size(scrolled, -1, 96)  # Four rows at the minimum window height
        
        self.playlist_store = Gtk.ListStore(str, str, int, str, str, str)  # path, display, number, duration
        self.playlist_view = Gtk.TreeView(model=self.playlist_store)
        self.playlist_view.get_style_context().add_class('playlist')
        self.playlist_view.set_has_tooltip(True)
        self.playlist_view.connect('query-tooltip', self._playlist_tooltip)
        self.playlist_view.set_can_focus(True)  # Allow keyboard focus
        
        marker = Gtk.CellRendererText()
        marker_col = Gtk.TreeViewColumn('', marker, text=5)
        marker_col.set_cell_data_func(marker, self._zebra_bg)
        self._scaled_min_width(marker_col, 42)
        self.playlist_view.append_column(marker_col)
        # Track number column
        track_renderer = Gtk.CellRendererText()
        track_column = Gtk.TreeViewColumn("", track_renderer, text=2)
        track_column.set_cell_data_func(track_renderer, self._zebra_bg)
        self._scaled_min_width(track_column, 30)
        self.playlist_view.append_column(track_column)
        
        # Song name column
        song_renderer = Gtk.CellRendererText()
        song_renderer.set_property('ellipsize', Pango.EllipsizeMode.END)
        song_renderer.set_property('height', self._px(24))
        self._song_renderer = song_renderer
        song_column = Gtk.TreeViewColumn("", song_renderer, text=1)
        song_column.set_cell_data_func(song_renderer, self._zebra_bg)
        song_column.set_expand(True)
        song_column.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
        self._scaled_min_width(song_column, 60)
        self.playlist_view.append_column(song_column)

        # Track duration, right-aligned
        dur_renderer = Gtk.CellRendererText()
        dur_renderer.set_property('xalign', 1.0)
        dur_column = Gtk.TreeViewColumn("", dur_renderer, text=3)
        dur_column.set_cell_data_func(dur_renderer, self._zebra_bg)
        self._scaled_min_width(dur_column, 64)
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

    def _select_row(self, index):
        selection = self.playlist_view.get_selection()
        selection.unselect_all()
        selection.select_path(Gtk.TreePath(index))
        self.playlist_view.scroll_to_cell(Gtk.TreePath(index), None, True, 0.5, 0.0)

    def _zebra_bg(self, column, cell, model, it, data):
        path = model.get_path(it)
        if self.playlist_view.get_selection().path_is_selected(path):
            cell.set_property('cell-background-set', False)
        else:
            cell.set_property('cell-background', self.theme['stripe'] if path.get_indices()[0] % 2 else self.theme['lcd'])
        cell.set_property('weight', 700 if model.get_value(it, 4) == self.order.current else 400)

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
        self._playlist_menu = self._build_row_menu()  # keep referenced while open
        self._playlist_menu.popup_at_pointer(event)
        return True

    def _build_row_menu(self):
        """Play Now / Play Next / queue / File Info / Remove for the selected rows."""
        model, paths = self.playlist_view.get_selection().get_selected_rows()
        sel_paths = [model.get_value(model.get_iter(p), 4) for p in paths]
        menu = Gtk.Menu()
        if not paths:
            return menu
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

        info_item = Gtk.MenuItem(label="File Info… (Alt+3)")
        info_item.connect("activate",
                          lambda _w, p=model[paths[0]][0]: self.show_file_info(path=p))
        menu.append(info_item)

        menu.append(Gtk.SeparatorMenuItem())
        rm_item = Gtk.MenuItem(label="Remove")
        rm_item.connect("activate", lambda _w: self.remove_selected(None))
        menu.append(rm_item)
        menu.show_all()
        return menu

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

