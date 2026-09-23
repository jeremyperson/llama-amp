"""The classic playlist editor: a resizable frame of skin tiles around the
track list. Rows, selection and the queue are the modern playlist's own model,
so every playlist action behaves the same in both modes."""
from gi.repository import Gdk, Gtk, Pango, PangoCairo

from ...constants import CLASSIC_PLAYLIST_HINT
from ...playlist import SORT_KEYS, move_block
from .base import SkinnedWindow
from ...i18n import _

TOP, BOTTOM, LEFT, RIGHT = 20, 38, 12, 20
ROW_HEIGHT = 13
STEP_W, STEP_H = 25, 29           # Winamp resizes the editor in tile steps
MIN_W, MIN_H = 275, 116
DEFAULT_SIZE = (275, 232)
MENUS = ('add', 'remove', 'select', 'misc', 'list')


def clamp(value, low, high):
    return max(low, min(high, value))


class ClassicPlaylistWindow(SkinnedWindow):
    def __init__(self, app, size=None):
        super().__init__(app, _('Playlist Editor'))
        self.width, self.height = size or DEFAULT_SIZE
        self.shaded = False
        self.scroll_row = 0
        self.anchor = None             # shift-click range anchor
        self.resizing = None           # (pointer x, y, width, height) at press
        self.row_drag = None           # {'start', 'offset', 'collapse_to'} while dragging rows
        self.area.add_events(Gdk.EventMask.BUTTON_MOTION_MASK)

    def skin_size(self):
        return self.width, 14 if self.shaded else self.height

    def toggle_shade(self):
        self.shaded = not self.shaded
        self.apply_size()
        self.app._classic.relayout()

    # -- geometry of the list -------------------------------------------------
    def _rows(self):
        return [(row[1], row[3], row[4]) for row in self.app.playlist_store]

    def _visible_rows(self):
        return max(1, (self.height - TOP - BOTTOM - 4) // ROW_HEIGHT)

    def _max_scroll(self):
        return max(0, len(self.app.playlist_store) - self._visible_rows())

    def scroll(self, direction):
        self.scroll_row = clamp(self.scroll_row - direction * 3, 0, self._max_scroll())
        self.refresh()

    def _row_at(self, y):
        index = self.scroll_row + int((y - TOP - 2) // ROW_HEIGHT)
        return index if 0 <= index < len(self.app.playlist_store) else None

    def _scroll_track(self):
        return self.width - 15, TOP, 8, self.height - TOP - BOTTOM

    # -- controls ---------------------------------------------------------------
    def controls(self):
        w, h = self.width, self.height
        if self.shaded:
            return [('close', (w - 11, 3, 9, 9)), ('shade', (w - 21, 3, 9, 9))]
        menus = [(name, (x, h - 30, 22, 18)) for name, x in
                 (('add', 14), ('remove', 43), ('select', 72), ('misc', 101), ('list', w - 44))]
        transport = [(name, (w - 147 + index * 8, h - 16, 8, 8)) for index, name in
                     enumerate(('previous', 'play', 'pause', 'stop', 'next', 'eject'))]
        return [('close', (w - 11, 3, 9, 9)), ('shade', (w - 21, 3, 9, 9)),
                ('resize', (w - 20, h - 20, 20, 20)), ('scrollbar', self._scroll_track())] + \
            menus + transport + [('tracks', (LEFT, TOP, w - LEFT - RIGHT, h - TOP - BOTTOM))]

    def is_slider(self, name):
        return name in ('scrollbar', 'resize')

    def _press(self, widget, event):
        x, y = self._point(event)
        if not self.shaded and self.hit(x, y) == 'tracks':
            return self._press_tracks(event, y)
        if self.hit(x, y) == 'resize' and event.button == 1 and event.type == Gdk.EventType.BUTTON_PRESS:
            self.resizing = (event.x_root, event.y_root, self.width, self.height)
        return super()._press(widget, event)

    def _press_tracks(self, event, y):
        app = self.app
        index = self._row_at(y)
        selection = app.playlist_view.get_selection()
        if event.button == 3:
            if index is not None and not selection.path_is_selected(Gtk.TreePath(index)):
                selection.unselect_all()
                selection.select_path(Gtk.TreePath(index))
            self._menu = app._build_row_menu()
            self._menu.popup_at_pointer(event)
            self.refresh()
            return True
        if event.button != 1 or index is None:
            return True
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            self.row_drag = None
            app._play_index(index)
            self.refresh()
            return True
        collapse_to = None
        if event.state & Gdk.ModifierType.SHIFT_MASK and self.anchor is not None:
            selection.unselect_all()
            selection.select_range(Gtk.TreePath(min(self.anchor, index)), Gtk.TreePath(max(self.anchor, index)))
        elif event.state & Gdk.ModifierType.CONTROL_MASK:
            path = Gtk.TreePath(index)
            (selection.unselect_path if selection.path_is_selected(path) else selection.select_path)(path)
            self.anchor = index
        elif selection.path_is_selected(Gtk.TreePath(index)):
            collapse_to = index          # keep the block for a drag; a plain click selects just this row
            self.anchor = index
        else:
            selection.unselect_all()
            selection.select_path(Gtk.TreePath(index))
            self.anchor = index
        # Dragging a selected row moves the selection as a block (Winamp)
        self.row_drag = {'start': index, 'offset': 0, 'collapse_to': collapse_to}
        self.refresh()
        return True

    def _selected_indices(self):
        _model, paths = self.app.playlist_view.get_selection().get_selected_rows()
        return [path.get_indices()[0] for path in paths]

    def _motion(self, widget, event):
        if self.row_drag is not None:
            _x, y = self._point(event)
            row = self.scroll_row + int((y - TOP - 2) // ROW_HEIGHT)
            offset = row - self.row_drag['start']
            if offset != self.row_drag['offset']:
                self.row_drag['offset'] = offset
                self.refresh()
            return False
        return super()._motion(widget, event)

    def _release(self, widget, event):
        if self.row_drag is None or event.button != 1:
            return super()._release(widget, event)
        drag, self.row_drag = self.row_drag, None
        selection = self.app.playlist_view.get_selection()
        if drag['offset']:
            targets = self.app.move_entries(self._selected_indices(), drag['offset'])
            selection.unselect_all()
            for index in targets:
                selection.select_path(Gtk.TreePath(index))
            self.anchor = targets[0] if targets else None
        elif drag['collapse_to'] is not None:
            selection.unselect_all()
            selection.select_path(Gtk.TreePath(drag['collapse_to']))
        self.refresh()
        return True

    def slide(self, name, x, y, final):
        if name == 'scrollbar':
            _x, top, _w, height = self._scroll_track()
            fraction = clamp((y - top - 9) / max(1, height - 18), 0, 1)
            self.scroll_row = round(fraction * self._max_scroll())
        elif name == 'resize' and self.resizing is not None:
            pointer = self.get_display().get_default_seat().get_pointer().get_position()
            start_x, start_y, width, height = self.resizing
            dx, dy = (pointer[1] - start_x) / self.scale, (pointer[2] - start_y) / self.scale
            self.width = max(MIN_W, width + round(dx / STEP_W) * STEP_W)
            self.height = max(MIN_H, height + round(dy / STEP_H) * STEP_H)
            self.apply_size()
            if final:
                self.resizing = None
                self.app._classic.relayout()
                self.app.schedule_save_config()

    def click(self, name):
        app = self.app
        actions = {'previous': lambda: app.previous_song(None), 'play': app.play_from_start,
                   'pause': app.pause_toggle, 'stop': lambda: app.stop_song(None),
                   'next': lambda: app.next_song(None), 'eject': lambda: app.add_files(None),
                   'close': lambda: app._classic.toggle_window('playlist'), 'shade': self.toggle_shade}
        if name in actions:
            actions[name]()
        elif name in MENUS:
            self._popup(name)

    def _popup(self, name):
        app = self.app
        entries = {
            'add': [(_('Add URL…'), app.open_url_dialog), (_('Add folder…'), app.add_folder),
                    (_('Add files…'), app.add_files)],
            'remove': [(_('Remove selected'), app.remove_selected), (_('Remove missing files'), app.remove_missing),
                       (_('Clear playlist'), app.clear_playlist), (_('Undo edit'), app.undo_playlist)],
            'select': [(_('Select all'), lambda *_args: app.playlist_view.get_selection().select_all()),
                       (_('Select none'), lambda *_args: app.playlist_view.get_selection().unselect_all()),
                       (_('Invert selection'), lambda *_args: self._invert_selection())],
            'misc': [(_('File info…'), lambda *_args: app.show_file_info()),
                     (_('Sort'), [(_(caption), lambda _w, k=key: app.sort_playlist(k))
                                  for key, caption in SORT_KEYS.items()])],
            'list': [(_('Save playlist as…'), app._save_playlist_as), (_('Export M3U…'), app.export_m3u)]
                    + [(_('Load: {name}').format(name=n), lambda _w, n=n: app._load_named_playlist(n))
                       for n in app._saved_playlist_names()],
        }[name]
        self._menu = app._popup_actions(self.area, entries)

    def _invert_selection(self):
        selection = self.app.playlist_view.get_selection()
        for index in range(len(self.app.playlist_store)):
            path = Gtk.TreePath(index)
            (selection.unselect_path if selection.path_is_selected(path) else selection.select_path)(path)
        self.refresh()

    # -- painting ---------------------------------------------------------------
    def paint(self, cr, skin):
        if self.shaded:
            self._paint_shade(cr, skin)
            return
        w, h = self.width, self.height
        active = '_SELECTED' if self.focused else ''
        # Top: corner, tiles, the centred title, tiles, corner
        skin.draw(cr, 'PLAYLIST_TOP_LEFT_SELECTED' if self.focused else 'PLAYLIST_TOP_LEFT_CORNER', 0, 0)
        for x in range(25, w - 25, 25):
            skin.draw(cr, 'PLAYLIST_TOP_TILE' + active, x, 0)
        skin.draw(cr, 'PLAYLIST_TITLE_BAR' + active, (w - 100) // 2, 0)
        skin.draw(cr, 'PLAYLIST_TOP_RIGHT_CORNER' + active, w - 25, 0)
        for y in range(TOP, h - BOTTOM, 29):
            skin.draw(cr, 'PLAYLIST_LEFT_TILE', 0, y, height=min(29, h - BOTTOM - y))
            skin.draw(cr, 'PLAYLIST_RIGHT_TILE', w - RIGHT, y, height=min(29, h - BOTTOM - y))
        # Bottom: left block, tiles, optional visualizer block, right block
        skin.draw(cr, 'PLAYLIST_BOTTOM_LEFT_CORNER', 0, h - BOTTOM)
        right = w - 150 - (75 if w >= 350 else 0)
        for x in range(125, right, 25):
            skin.draw(cr, 'PLAYLIST_BOTTOM_TILE', x, h - BOTTOM, width=min(25, right - x))
        if w >= 350:
            skin.draw(cr, 'PLAYLIST_VISUALIZER_BACKGROUND', w - 225, h - BOTTOM)
        skin.draw(cr, 'PLAYLIST_BOTTOM_RIGHT_CORNER', w - 150, h - BOTTOM)
        self._paint_tracks(cr, skin)
        self._paint_scrollbar(cr, skin)
        if self.pressed == 'close':
            skin.draw(cr, 'PLAYLIST_CLOSE_SELECTED', w - 11, 3)
        if self.pressed == 'shade':
            skin.draw(cr, 'PLAYLIST_COLLAPSE_SELECTED', w - 21, 3)
        skin.draw_text(cr, self._running_time(), w - 143, h - 28)
        if self.app.playback_state != 'Stopped':
            skin.draw_text(cr, self.app._classic.main._clock_text()[-5:].rjust(5), w - 84, h - 15)

    def _paint_scrollbar(self, cr, skin):
        x, top, _w, height = self._scroll_track()
        fraction = self.scroll_row / self._max_scroll() if self._max_scroll() else 0
        sprite = 'PLAYLIST_SCROLL_HANDLE_SELECTED' if self.dragging == 'scrollbar' else 'PLAYLIST_SCROLL_HANDLE'
        skin.draw(cr, sprite, x, top + round(fraction * (height - 18)))

    def _paint_tracks(self, cr, skin):
        app, colors = self.app, skin.pledit
        x, y, width, height = LEFT, TOP, self.width - LEFT - RIGHT, self.height - TOP - BOTTOM
        cr.save()
        cr.rectangle(x, y, width, height)
        cr.clip()
        cr.set_source_rgb(*(c / 255 for c in colors['normalbg']))
        cr.paint()
        self.scroll_row = clamp(self.scroll_row, 0, self._max_scroll())
        layout = PangoCairo.create_layout(cr)
        font = Pango.FontDescription(colors['font'])
        font.set_absolute_size(9 * Pango.SCALE)
        layout.set_font_description(font)
        selection = app.playlist_view.get_selection()
        rows = self._rows()
        if not rows:
            layout.set_text(_(CLASSIC_PLAYLIST_HINT), -1)
            layout.set_alignment(Pango.Alignment.CENTER)
            layout.set_width(width * Pango.SCALE)
            cr.set_source_rgba(*(c / 255 for c in colors['normal']), .6)
            cr.move_to(x, y + height / 2 - layout.get_pixel_size()[1] / 2)
            PangoCairo.show_layout(cr, layout)
        for offset in range(self._visible_rows() + 1):
            index = self.scroll_row + offset
            if index >= len(rows):
                break
            title, duration, entry_id = rows[index]
            top = y + 2 + offset * ROW_HEIGHT
            if selection.path_is_selected(Gtk.TreePath(index)):
                cr.set_source_rgb(*(c / 255 for c in colors['selectedbg']))
                cr.rectangle(x, top, width, ROW_HEIGHT)
                cr.fill()
            color = colors['current'] if entry_id == app.order.current else colors['normal']
            cr.set_source_rgb(*(c / 255 for c in color))
            layout.set_text(duration or '', -1)
            duration_width = layout.get_pixel_size()[0]
            cr.move_to(x + width - duration_width - 3, top)
            PangoCairo.show_layout(cr, layout)
            layout.set_width((width - duration_width - 10) * Pango.SCALE)
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            layout.set_text(f'{index + 1}. {title}', -1)
            cr.move_to(x + 2, top)
            PangoCairo.show_layout(cr, layout)
            layout.set_width(-1)
        if self.row_drag is not None and self.row_drag['offset']:
            # Outline where the dragged rows will land
            _order, targets = move_block(rows, self._selected_indices(), self.row_drag['offset'])
            cr.set_source_rgb(*(c / 255 for c in colors['current']))
            cr.set_line_width(1)
            for index in targets:
                cr.rectangle(x + .5, y + 2 + (index - self.scroll_row) * ROW_HEIGHT + .5, width - 1, ROW_HEIGHT - 1)
            cr.stroke()
        cr.restore()

    def _running_time(self):
        """Winamp's 'selected/total' running time."""
        app = self.app
        seconds = [app._duration_seconds(path) or 0 for path in app.playlist]
        _model, paths = app.playlist_view.get_selection().get_selected_rows()
        selected = sum(seconds[path.get_indices()[0]] for path in paths if path.get_indices()[0] < len(seconds))

        def clock(total):
            return f'{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}' if total >= 3600 else \
                f'{total // 60}:{total % 60:02d}'
        return f'{clock(selected)}/{clock(sum(seconds))}'

    def _paint_shade(self, cr, skin):
        w = self.width
        skin.draw(cr, 'PLAYLIST_SHADE_BACKGROUND_LEFT', 0, 0)
        for x in range(25, w - 50, 25):
            skin.draw(cr, 'PLAYLIST_SHADE_BACKGROUND', x, 0)
        skin.draw(cr, 'PLAYLIST_SHADE_BACKGROUND_RIGHT' + ('_SELECTED' if self.focused else ''), w - 50, 0)
        title = getattr(self.app, '_title_full', '') or ''
        skin.draw_text(cr, title, 5, 4, max_chars=max(0, (w - 60) // 5))
        if self.pressed == 'close':
            skin.draw(cr, 'PLAYLIST_CLOSE_SELECTED', w - 11, 3)
        if self.pressed == 'shade':
            skin.draw(cr, 'PLAYLIST_EXPAND_SELECTED', w - 21, 3)
