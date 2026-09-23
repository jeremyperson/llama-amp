"""Shared machinery for the classic skinned windows: undecorated windows that
paint sprites in 1x skin coordinates (scaled for double size) and map mouse
events onto named controls."""
import cairo
from gi.repository import Gdk, Gtk

TITLE_HEIGHT = 14


class SkinnedWindow(Gtk.Window):
    """Subclasses define controls() -> [(name, (x, y, w, h))], paint(cr) and the
    click/drag hooks. Coordinates are 1x skin pixels throughout."""

    def __init__(self, app, title):
        super().__init__(title=title)
        self.app = app
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_icon_name('llama-amp')
        self.pressed = None          # control held down under the pointer
        self.dragging = None         # slider being dragged
        self.focused = False
        self.iconified = False
        self.user_moving = False     # a window-manager move drag the user started
        self.on_move_finished = None
        self.area = Gtk.DrawingArea()
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                             | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.SCROLL_MASK)
        self.area.connect('draw', self._draw)
        self.area.connect('button-press-event', self._press)
        self.area.connect('button-release-event', self._release)
        self.area.connect('motion-notify-event', self._motion)
        self.area.connect('scroll-event', self._scroll)
        self.add(self.area)
        self.connect('key-press-event', app.on_window_key_press)
        self.connect('focus-in-event', self._focus_changed, True)
        self.connect('focus-out-event', self._focus_changed, False)
        self.connect('window-state-event', self._state_changed)
        self.connect('realize', lambda *_: self.apply_shape())
        # Files and skins dropped on any classic window
        self.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        self.drag_dest_add_uri_targets()
        self.connect('drag-data-received', app.on_drag_data_received)
        self.area.show()

    # -- geometry ---------------------------------------------------------
    @property
    def scale(self):
        return self.app.ui_scale

    def skin_size(self):
        raise NotImplementedError

    def apply_size(self):
        width, height = self.skin_size()
        self.area.set_size_request(width * self.scale, height * self.scale)
        self.resize(width * self.scale, height * self.scale)
        self.apply_shape()
        self.area.queue_draw()

    def region_key(self):
        """The region.txt section shaping this window in its current state."""
        return None

    def apply_shape(self):
        """Cut the window to the skin's region.txt polygons (shaped windows)."""
        if not self.get_realized():
            return
        key = self.region_key()
        polygons = self.app.skin.regions.get(key) if key and self.app.skin else None
        if not polygons:
            self.shape_combine_region(None)
            return
        width, height = self.skin_size()
        mask = cairo.ImageSurface(cairo.FORMAT_A8, width * self.scale, height * self.scale)
        cr = cairo.Context(mask)
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        cr.scale(self.scale, self.scale)
        for polygon in polygons:
            cr.move_to(*polygon[0])
            for point in polygon[1:]:
                cr.line_to(*point)
            cr.close_path()
            cr.fill()          # each polygon adds to the shape
        self.shape_combine_region(Gdk.cairo_region_create_from_surface(mask))

    # -- drawing ------------------------------------------------------------
    def _draw(self, _widget, cr):
        cr.scale(self.scale, self.scale)
        self.paint(cr, self.app.skin)
        return False

    def paint(self, cr, skin):
        raise NotImplementedError

    def refresh(self):
        self.area.queue_draw()

    def _focus_changed(self, _window, _event, focused):
        self.focused = focused
        self.refresh()
        return False

    def _state_changed(self, _window, event):
        self.iconified = bool(event.new_window_state & Gdk.WindowState.ICONIFIED)
        self.app._update_analyzer_visibility()
        return False

    # -- input --------------------------------------------------------------
    def controls(self):
        return []

    def hit(self, x, y):
        for name, (cx, cy, cw, ch) in self.controls():
            if cx <= x < cx + cw and cy <= y < cy + ch:
                return name
        return None

    def _point(self, event):
        return event.x / self.scale, event.y / self.scale

    def _press(self, _widget, event):
        x, y = self._point(event)
        if event.button == 3:
            self.app.show_classic_menu(event)
            return True
        if event.button != 1:
            return False
        name = self.hit(x, y)
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            if name is None and y < TITLE_HEIGHT:
                self.toggle_shade()
            return True
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return True
        if name is None:
            if y < TITLE_HEIGHT or self.drag_anywhere():
                self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
                self._watch_move()
            return True
        self.pressed = name
        if self.is_slider(name):
            self.dragging = name
            self.slide(name, x, y, final=False)
        self.refresh()
        return True

    def _motion(self, _widget, event):
        if self.dragging is not None:
            self.slide(self.dragging, *self._point(event), final=False)
            self.refresh()
        return False

    def _release(self, _widget, event):
        if event.button != 1:
            return False
        x, y = self._point(event)
        name, self.pressed = self.pressed, None
        if self.dragging is not None:
            dragged, self.dragging = self.dragging, None
            self.slide(dragged, x, y, final=True)
        elif name is not None and self.hit(x, y) == name:
            self.click(name)
        self.refresh()
        return True

    def _scroll(self, _widget, event):
        direction = {Gdk.ScrollDirection.UP: 1, Gdk.ScrollDirection.DOWN: -1}.get(event.direction)
        if direction is None:
            ok, _dx, dy = event.get_scroll_deltas()
            direction = -1 if ok and dy > 0 else 1 if ok and dy < 0 else 0
        if direction:
            self.scroll(direction)
        return True

    def _watch_move(self):
        """The window manager owns a move drag and GTK never sees its button
        release, so poll the pointer to learn when the user lets go."""
        if self.user_moving:
            return
        self.user_moving = True
        pointer = self.get_display().get_default_seat().get_pointer()

        def poll():
            window = self.get_window()
            if window is not None:
                _window, _x, _y, mask = window.get_device_position(pointer)
                if mask & Gdk.ModifierType.BUTTON1_MASK:
                    return True
            self.user_moving = False
            if self.on_move_finished is not None:
                self.on_move_finished()
            return False
        self.app.tasks.timeout_add(50, poll)

    # -- hooks ----------------------------------------------------------------
    def is_slider(self, name):
        return False

    def slide(self, name, x, y, final):
        pass

    def click(self, name):
        pass

    def scroll(self, direction):
        self.app._step_volume(direction * .05)

    def drag_anywhere(self):
        """Whether empty areas move the window (Winamp: every classic window)."""
        return True

    def toggle_shade(self):
        pass
