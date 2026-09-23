"""Collapsible, detachable and snapping EQ/playlist panels."""
from gi.repository import Gdk, Gtk, Pango


class PanelManager:
    """Owns auxiliary windows and reparents their live controls when attached."""
    def __init__(self, app, host):
        self.app, self.host = app, host
        self.items = {}
        self.x11 = 'X11' in Gdk.Display.get_default().__gtype__.name
        self.drag = None
        self.settle_id = None

    def add(self, name, title, content, expand=False):
        saved = self.app.config['panels'].get(name, {})
        if not isinstance(saved, dict):
            saved = {}
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        frame.get_style_context().add_class('music-player-frame')
        frame.set_no_show_all(True)
        header = Gtk.EventBox()
        header.get_style_context().add_class('panel-header')
        row = Gtk.Box(spacing=4)
        caption = Gtk.Label(label=title, xalign=0)
        caption.get_style_context().add_class('music-player-label')
        caption.set_ellipsize(Pango.EllipsizeMode.END)
        row.pack_start(caption, True, True, 0)
        if name == 'playlist':
            row.pack_start(self.app.playlist_info, False, False, 6)
        collapse = self.app._panel_button(name, 'collapse')
        collapse.set_tooltip_text('Collapse / expand ' + title.lower())
        collapse.connect('clicked', lambda *_: self.collapse(name))
        row.pack_start(collapse, False, False, 0)
        detach = self.app._panel_button(name, 'detach')
        detach.set_tooltip_text('Detach / attach ' + title.lower())
        detach.connect('clicked', lambda *_: self.toggle_attach(name))
        row.pack_start(detach, False, False, 0)
        header.add(row)
        header.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        header.connect('button-press-event', self._header_press, name)
        frame.pack_start(header, False, False, 0)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.set_no_show_all(True)
        body.pack_start(content, True, True, 0)
        frame.pack_start(body, True, True, 0)
        slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        slot.set_no_show_all(True)
        self.host.pack_start(slot, expand, expand, 0)
        slot.pack_start(frame, True, True, 0)
        item = dict(frame=frame, body=body, slot=slot, window=None,
                    collapse=collapse, detach=detach, title=title, expand=expand,
                    visible=saved.get('visible') is not False,
                    collapsed=saved.get('collapsed') is True,
                    attached=saved.get('attached') is not False,
                    size=saved.get('size', [560, 240]), position=saved.get('position'),
                    snap_to=saved.get('snap_to') if self.x11 and saved.get('snap_to') in ('main', 'eq', 'playlist') and saved.get('snap_to') != name else None)
        self.items[name] = item
        content.show_all()
        header.show_all()

    def restore(self):
        for name, item in self.items.items():
            if not item['attached']:
                self._detach(name)
            self._show(name)

    def _show(self, name):
        item = self.items[name]
        visible = item['visible'] and not self.app._windowshade
        self._update_controls(name)
        item['body'].set_visible(not item['collapsed'])
        self.host.set_child_packing(item['slot'], item['expand'] and not item['collapsed'],
                                    item['expand'] and not item['collapsed'], 0, Gtk.PackType.START)
        item['frame'].set_visible(visible)
        item['slot'].set_visible(visible and item['attached'])
        if item['window']:
            item['window'].set_visible(visible and not item['attached'])

    def _update_controls(self, name):
        item = self.items[name]
        for key, verb in [('collapse', 'Expand' if item['collapsed'] else 'Collapse'),
                          ('detach', 'Detach' if item['attached'] else 'Reattach')]:
            text = f"{verb} {item['title'].lower()}"
            item[key].set_tooltip_text(text)
            item[key].get_accessible().set_name(text)
            item[key].queue_draw()

    def set_visible(self, name, visible):
        self.items[name]['visible'] = visible
        self._show(name)
        self.app.schedule_save_config()

    def collapse(self, name):
        item = self.items[name]
        item['collapsed'] = not item['collapsed']
        self._show(name)
        self.app.schedule_save_config()

    def toggle_attach(self, name):
        item = self.items[name]
        if item['attached']:
            self._detach(name)
        else:
            frame = item['frame']
            frame.get_parent().remove(frame)
            item['slot'].pack_start(frame, True, True, 0)
            item['window'].hide()
            item['attached'] = True
            item['snap_to'] = None
            self._update_controls(name)
        self._show(name)
        self.app.schedule_save_config()

    def _detach(self, name):
        item = self.items[name]
        if item['window'] is None:
            window = Gtk.Window(title=f"{self.app.get_title()} — {item['title']}")
            window.set_decorated(False)
            window.set_transient_for(self.app)
            window.set_destroy_with_parent(True)
            window.get_style_context().add_class('llama-window')
            shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            shell.get_style_context().add_class('music-player-main')
            self.app._add_resize_grip(window, shell)
            shell.show()
            window.connect('delete-event', lambda *_: (self.set_visible(name, False), True)[1])
            window.connect('key-press-event', self.app.on_window_key_press)
            window.connect('configure-event', self._configured, name)
            window.connect('button-press-event', self.app._resize_press)
            window.set_geometry_hints(None, self.app._minimum_geometry(self.app._px(80)), Gdk.WindowHints.MIN_SIZE)
            width, height = self.app._clamp_size(item['size'], minimum_height=80)
            window.set_default_size(width, height)
            if self.x11 and self.app._valid_pair(item['position']):
                window.move(*self.app._clamp_position(item['position']))
            item['window'] = window
            item['shell'] = shell
        frame = item['frame']
        frame.get_parent().remove(frame)
        item['shell'].pack_start(frame, True, True, 0)
        item['attached'] = False
        self._update_controls(name)

    def _header_press(self, widget, event, name):
        item = self.items[name]
        if event.button == 1 and event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            self.collapse(name)
            return True
        if event.button == 1 and not item['attached']:
            window = item['window']
            self.start_drag(window, event)
            window.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
            return True
        return False

    def windows(self):
        return {'main': self.app, **{name: item['window'] for name, item in self.items.items()
                                    if item['window'] and not item['attached'] and item['visible']}}

    def start_drag(self, window, event):
        if not self.x11:
            return
        windows = self.windows()
        root = next((name for name, win in windows.items() if win == window), 'main')
        if event.state & Gdk.ModifierType.MOD1_MASK:
            if root != 'main':
                self.items[root]['snap_to'] = None
            for item in self.items.values():
                if item['snap_to'] == root:
                    item['snap_to'] = None
        members = {root}
        for _ in self.items:
            for name, item in self.items.items():
                if item['snap_to'] in members and name in windows:
                    members.add(name)
        self.drag = (root, {name: windows[name].get_position() for name in members})
        self._drag_alt = bool(event.state & Gdk.ModifierType.MOD1_MASK)

    def _configured(self, window, event, name):
        item = self.items[name]
        item['size'] = [event.width, event.height]
        if self.x11:
            item['position'] = list(window.get_position())
        self.configured(name)
        return False

    def configured(self, name):
        if not self.x11 or not self.drag or self.drag[0] != name:
            return
        root, origins = self.drag
        windows = self.windows()
        if root not in windows:
            return
        x, y = windows[root].get_position()
        ox, oy = origins[root]
        for member, (mx, my) in origins.items():
            if member != root and member in windows:
                windows[member].move(mx + x - ox, my + y - oy)
        if self.settle_id is not None:
            self.app.tasks.source_remove(self.settle_id)
        self.settle_id = self.app.tasks.timeout_add(180, self._settle)

    def _settle(self):
        if self.drag and not self.app._destroyed:
            pointer = Gdk.Display.get_default().get_default_seat().get_pointer()
            state = self.app.get_window().get_device_position(pointer)[-1]
            if state & Gdk.ModifierType.BUTTON1_MASK:
                return True  # keep moving the group through pauses in a drag
        self.settle_id = None
        drag, self.drag = self.drag, None
        if not drag or self.app._destroyed:
            return False
        root, origins = drag
        windows = self.windows()
        if root != 'main' and root in windows and not self._drag_alt:
            moving = windows[root]
            x, y = moving.get_position()
            w, h = moving.get_size()
            best = None
            for target, window in windows.items():
                if target in origins:
                    continue
                tx, ty = window.get_position()
                tw, th = window.get_size()
                candidates = []
                if x < tx + tw and x + w > tx:
                    candidates.extend([(abs(y - ty - th), x, ty + th),
                                       (abs(y + h - ty), x, ty - h)])
                if y < ty + th and y + h > ty:
                    candidates.extend([(abs(x - tx - tw), tx + tw, y),
                                       (abs(x + w - tx), tx - w, y)])
                for distance, nx, ny in candidates:
                    if distance <= 12 and (best is None or distance < best[0]):
                        best = (distance, nx, ny, target)
            self.items[root]['snap_to'] = best[3] if best else None
            if best:
                moving.move(best[1], best[2])
        self.app.schedule_save_config()
        return False

    def snapshot(self):
        return {name: {key: item[key] for key in ('visible', 'collapsed', 'attached', 'size', 'position', 'snap_to')}
                for name, item in self.items.items()}

    def reset(self):
        for name, item in self.items.items():
            if not item['attached']:
                self.toggle_attach(name)
            item.update(visible=True, collapsed=False, snap_to=None)
            self._show(name)

    def hide_windows(self):
        """Classic mode: detached panel windows step aside with the main window."""
        for item in self.items.values():
            if item['window']:
                item['window'].hide()

    def show_windows(self):
        for name in self.items:
            self._show(name)

    def close(self):
        if self.settle_id is not None:
            self.app.tasks.source_remove(self.settle_id)
        for item in self.items.values():
            if item['window']:
                item['window'].destroy()

