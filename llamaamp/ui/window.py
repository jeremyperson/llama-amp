"""Main window: styling, geometry, windowshade, resize grip, layout and drag & drop."""
import math

from gi.repository import Gdk, Gtk, Pango

from ..constants import APP_VERSION, DEFAULT_SONG_TEXT, URI_TARGET_INFO, WINDOW_H, WINDOW_W
from ..ui.panels import PanelManager
from ..ui.themes import THEMES


class WindowMixin:
    def on_configure_event(self, widget, event):
        self._win_pos = self.get_position()
        if hasattr(self, 'panels'):
            self.panels.configured('main')
        if getattr(self, '_layout_restored', False) and not self._windowshade and not getattr(self, '_layout_switching', False):
            self._expanded_size = [event.width, event.height]
        return False

    def on_window_state_event(self, widget, event):
        self._iconified = bool(event.new_window_state & Gdk.WindowState.ICONIFIED)
        self._update_analyzer_visibility()
        if self._iconified:
            self._marquee_stop()
        else:
            self._set_title_text(getattr(self, '_title_full', '') or DEFAULT_SONG_TEXT)
        return False

    def setup_drag_and_drop(self):
        """Set up drag and drop functionality for adding music files"""
        self.log_debug("Setting up drag and drop...")
        
        # Define what we can accept
        target_entries = [
            Gtk.TargetEntry.new("text/uri-list", 0, 0),
            Gtk.TargetEntry.new("application/x-kde4-urilist", 0, 1)
        ]
        
        self.log_debug(f"Target entries: {[entry.target for entry in target_entries]}")
        
        # Set this window as a drag destination
        self.drag_dest_set(
            Gtk.DestDefaults.ALL,
            target_entries,
            Gdk.DragAction.COPY
        )
        
        self.log_debug("Window set as drag destination")
        
        # Connect the drag and drop signals
        self.connect("drag-data-received", self.on_drag_data_received)
        self.connect("drag-motion", self.on_drag_motion)
        self.connect("drag-leave", self.on_drag_leave)
        
        self.log_debug("Drag and drop signals connected")

    def on_drag_data_received(self, widget, drag_context, x, y, data, info, time):
        """Handle files dropped on the window. Registered with DestDefaults.ALL,
        so GTK finishes the drag itself — no manual drag_finish here (a second
        call would be a double-finish)."""
        self.get_style_context().remove_class('drag-over')
        try:
            uris = list(data.get_uris() or [])
            if not uris:
                text_data = data.get_text()
                if text_data:
                    uris = text_data.strip().split('\n')
            files = self._uris_to_audio_paths(uris)
            if files:
                self._add_paths(files, feedback=f"Added {len(files)} file(s)")
        except Exception as e:
            self.log_debug(f"Error handling dropped files: {e}")

    def on_view_drag_data_received(self, widget, drag_context, x, y, data, info, time):
        """File drops landing on the playlist TreeView. Row reorders (the
        built-in TREE_MODEL_ROW target) pass through to the default handler."""
        if info != URI_TARGET_INFO:
            return  # let the TreeView's own reorder machinery run
        files = self._uris_to_audio_paths(data.get_uris() or [])
        added = self._add_paths(files, feedback=f"Added {len(files)} file(s)" if files else None)
        drag_context.finish(bool(added), False, time)
        widget.stop_emission_by_name("drag-data-received")

    def on_drag_motion(self, widget, drag_context, x, y, time):
        """Handle drag motion over the window"""
        # Show visual feedback that we can accept the drop
        self.get_style_context().add_class('drag-over')
        Gdk.drag_status(drag_context, Gdk.DragAction.COPY, time)
        return True

    def on_drag_leave(self, widget, drag_context, time):
        """Handle when drag leaves the window"""
        self.get_style_context().remove_class('drag-over')

    def show_drop_feedback(self, message):
        if self._destroyed:
            return
        self.feedback_label.set_text(message)
        self.feedback_label.set_tooltip_text(message)
        if self._drop_feedback_id is not None:
            self.tasks.source_remove(self._drop_feedback_id)
        def clear():
            self._drop_feedback_id = None
            if not self._destroyed:
                self.feedback_label.set_text('')
            return False
        self._drop_feedback_id = self.tasks.timeout_add_seconds(5, clear)

    def setup_styling(self):
        self.theme = THEMES[self.config.get('theme', 'green')]
        t = self.theme
        css = """
        window.llama-window { background-color: transparent; }
        .music-player-main { background: CHASSIS; color: TEXT; border: 1px solid CONTROL;
                             border-radius: 5px; font: 12px "DejaVu Sans"; }
        .music-player-titlebar { background: linear-gradient(to bottom, CONTROL, PANEL);
            color: TEXT; padding: 3px 5px; border-bottom: 1px solid #080a08; }
        .brand { font: bold 11px "Orbitron", sans-serif; letter-spacing: 1px; color: ACCENT; }
        .music-player-frame { background: PANEL; border: 1px solid CONTROL;
            border-radius: 3px; margin: 2px 5px; padding: 5px; }
        frame.music-player-frame > border, frame.music-player-display > border { border: none; }
        .version-badge { font: 10px "Liberation Mono", monospace; color: TEXT;
            background: LCD; border: 1px solid CONTROL; border-top-color: #080b08;
            border-radius: 3px; padding: 2px 5px; opacity: .8; }
        .song-title { font-size: 14px; font-weight: bold; }
        .music-player-display { background: LCD; color: ACCENT; padding: 7px;
            border: 1px solid #050705; border-bottom-color: CONTROL;
            border-radius: 2px; font: bold 12px "Liberation Mono"; }
        .music-player-time { font: 30px "DSEG7 Classic", monospace; color: ACCENT;
            background: LCD; padding: 4px; }
        .music-player-label { font: 10px "DejaVu Sans"; color: TEXT; }
        .music-player-art { border: 1px solid CONTROL; }
        .music-player-main button { background: linear-gradient(to bottom, CONTROL, PANEL);
            color: TEXT; border: 1px solid CONTROL; border-top-color: #667067;
            border-bottom-color: #090c09; border-radius: 2px; padding: 4px 7px;
            min-width: 16px; min-height: 18px; font: bold 10px "DejaVu Sans"; }
        .music-player-main .panel-control { min-width: 24px; min-height: 24px; padding: 1px; }
        .music-player-main button:hover { border-color: ACCENT; }
        .music-player-main button:active, .music-player-main button.active,
        .music-player-main button:checked { background: LCD; color: ACCENT;
            border-top-color: #030503; border-bottom-color: CONTROL; }
        .music-player-main button:disabled { opacity: .45; }
        .music-player-main :focus { outline: 1px solid ACCENT; outline-offset: -2px; }
        .music-player-main scale { padding: 3px; color: TEXT; font-size: 10px; }
        .music-player-main scale trough { background: LCD; min-height: 4px; min-width: 4px;
            border: 1px solid #080b08; border-bottom-color: CONTROL; }
        .music-player-main scale highlight { background: ACCENT; border: none; box-shadow: none; }
        .music-player-main scale.vertical highlight { background: transparent; }
        .music-player-main scale slider { background: linear-gradient(to bottom, #c7ccc6, #778176);
            border: 1px solid #242c24; border-top-color: #edf3e9;
            box-shadow: none; border-radius: 1px; min-width: 9px; min-height: 12px; margin: -4px 0; }
        .music-player-main scale.vertical slider { min-width: 19px; min-height: 7px; margin: 0 -8px; }
        .music-player-main scale.eq-bypassed trough { background: alpha(LCD, .6);
            border-color: alpha(#080b08, .6); border-bottom-color: alpha(CONTROL, .6); }
        .music-player-main scale.eq-bypassed slider {
            background: linear-gradient(to bottom, alpha(#c7ccc6, .45), alpha(#778176, .45));
            border-color: alpha(#242c24, .45); border-top-color: alpha(#edf3e9, .45); }
        .music-player-main scale mark { color: TEXT; font-size: 8px; }
        .playlist { background: LCD; color: ACCENT; font: 12px "Liberation Mono";
                    -GtkTreeView-vertical-separator: 0; }
        .playlist:selected { background: CONTROL; color: TEXT; }
        .playlist-search { background: LCD; color: TEXT; border: 1px solid CONTROL;
                           border-radius: 2px; padding: 3px; min-height: 20px; font-size: 11px; }
        .search-miss { border-color: #e78d55; }
        .muted { color: TEXT; font-size: 10px; opacity: .8; }
        .panel-header { padding: 2px; }
        .music-player-main.drag-over { border-color: ACCENT; }
        """
        for token in ('CHASSIS', 'PANEL', 'CONTROL', 'TEXT', 'ACCENT', 'LCD'):
            css = css.replace(token, t[token.lower()])
        if t is THEMES['silver']:
            css += '.music-player-main button { color: #10151b; background: linear-gradient(to bottom, #d3d7df, #929aaa); }'
            css += '.music-player-main button:active, .music-player-main button.active { color: #8cfa65; background: #131a15; }'
            css += '.playlist:selected { color: #10151b; }'
        if hasattr(self, '_css_provider'):
            Gtk.StyleContext.remove_provider_for_screen(Gdk.Screen.get_default(), self._css_provider)
        self._css_provider = Gtk.CssProvider()
        self._css_provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), self._css_provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        if hasattr(self, 'analyzer'):
            self.analyzer.queue_draw()
            self.playlist_view.queue_draw()

    @staticmethod
    def _minimum_geometry(height):
        geometry = Gdk.Geometry()
        geometry.min_width = 440
        geometry.min_height = height
        return geometry

    @staticmethod
    def _valid_pair(value):
        return (isinstance(value, (list, tuple)) and len(value) == 2 and
                all(isinstance(n, (int, float)) and math.isfinite(n) for n in value))

    def _workarea(self):
        display = Gdk.Display.get_default()
        monitor = display.get_monitor_at_window(self.get_window()) if self.get_window() else display.get_primary_monitor()
        if monitor is None:
            monitor = display.get_monitor(0)
        return monitor.get_workarea()

    def _clamp_size(self, size, minimum_height=220):
        if not self._valid_pair(size):
            size = [WINDOW_W, WINDOW_H]
        area = self._workarea()
        return (max(440, min(int(size[0]), area.width)),
                max(minimum_height, min(int(size[1]), area.height)))

    def _clamp_position(self, position):
        x, y = map(int, position)
        display = Gdk.Display.get_default()
        monitor = display.get_monitor_at_point(x, y) or display.get_monitor(0)
        area = monitor.get_workarea()
        return max(area.x, min(x, area.x + area.width - 100)), max(area.y, min(y, area.y + area.height - 60))

    def _window_mapped(self, *_args):
        if not getattr(self, '_layout_restored', False):
            self._layout_restored = True
            self.resize(*self._clamp_size(self._expanded_size))
            self.panels.restore()
            if self._restore_shade:
                self.tasks.idle_add(self.toggle_windowshade)
        self._update_analyzer_visibility()
        return False

    def toggle_windowshade(self, *_args):
        if self._destroyed:
            return False
        self._layout_switching = True
        if not self._windowshade:
            self._expanded_size = list(self.get_size())
        self._windowshade = not self._windowshade
        self.resize_grip.set_visible(not self._windowshade)
        self.player_frame.set_visible(not self._windowshade)
        self.brand_group.set_visible(not self._windowshade)
        self._title_spacer.set_visible(not self._windowshade)
        for widget in (self.shade_title, self.shade_time, self.shade_controls):
            if self._windowshade:
                widget.show()
                if widget is self.shade_controls:
                    for child in widget.get_children():
                        child.show_all()
            else:
                widget.hide()
        for name in self.panels.items:
            self.panels._show(name)
        self.set_geometry_hints(None, self._minimum_geometry(48 if self._windowshade else 220),
                                Gdk.WindowHints.MIN_SIZE)
        self.resize(self._expanded_size[0], 48 if self._windowshade else self._expanded_size[1])
        self._update_analyzer_visibility()
        self._marquee_stop()
        if not self._windowshade:
            self._set_title_text(getattr(self, '_title_full', '') or DEFAULT_SONG_TEXT)
        def settled():
            self._layout_switching = False
            return False
        self.tasks.idle_add(settled)
        self.schedule_save_config()
        return False

    def _add_resize_grip(self, window, content):
        """Overlay a visible hit target without adding a footer to the layout."""
        overlay = Gtk.Overlay()
        overlay.add(content)
        window.add(overlay)
        grip = Gtk.DrawingArea()
        grip.set_size_request(24, 24)
        grip.set_halign(Gtk.Align.END)
        grip.set_valign(Gtk.Align.END)
        grip.set_margin_end(2)
        grip.set_margin_bottom(2)
        grip.set_tooltip_text('Drag to resize')
        grip.get_accessible().set_name('Resize window')
        grip.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK
                        | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        grip.connect('draw', self._draw_resize_grip)
        grip.connect('button-press-event',
                     lambda _widget, event: self._resize_press(window, event, from_grip=True))
        def realize(widget):
            cursor = Gdk.Cursor.new_from_name(widget.get_display(), 'se-resize')
            if cursor is None:
                cursor = Gdk.Cursor.new_for_display(widget.get_display(), Gdk.CursorType.BOTTOM_RIGHT_CORNER)
            widget.get_window().set_cursor(cursor)
        def hover(widget, event, entering):
            if entering:
                widget.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
            else:
                widget.unset_state_flags(Gtk.StateFlags.PRELIGHT)
            widget.queue_draw()
            return False
        grip.connect('realize', realize)
        grip.connect('enter-notify-event', hover, True)
        grip.connect('leave-notify-event', hover, False)
        overlay.add_overlay(grip)
        overlay.set_overlay_pass_through(grip, False)
        window.resize_grip = grip
        grip.show()
        grip.set_no_show_all(True)
        overlay.show()

    def _draw_resize_grip(self, widget, cr):
        hovered = bool(widget.get_state_flags() & Gtk.StateFlags.PRELIGHT)
        x, y = widget.get_allocated_width() - 4.5, widget.get_allocated_height() - 4.5
        # Paired dark/light diagonals give the grip the same bevel as the buttons.
        for offset, color, alpha in ((1, self.theme['lcd'], 1),
                                     (0, self.theme['accent'] if hovered else self.theme['text'],
                                      1 if hovered else .65)):
            self._cairo_color(cr, color, alpha)
            cr.set_line_width(1)
            for length in (4, 8, 12):
                cr.move_to(x - length, y + offset)
                cr.line_to(x, y - length + offset)
            cr.stroke()
        return False

    def _resize_press(self, window, event, from_grip=False):
        if event.button != 1 or (window is self and self._windowshade):
            return False
        if from_grip or (event.x >= window.get_allocated_width() - 12
                         and event.y >= window.get_allocated_height() - 12):
            window.begin_resize_drag(Gdk.WindowEdge.SOUTH_EAST, event.button,
                                     int(event.x_root), int(event.y_root), event.time)
            return True
        return False

    def _reset_layout(self, *_args):
        if self._windowshade:
            self.toggle_windowshade()
        self.panels.reset()
        self._expanded_size = [WINDOW_W, WINDOW_H]
        self.resize(*self._clamp_size(self._expanded_size))
        if self.panels.x11:
            area = self._workarea()
            self.move(area.x + max(0, (area.width - WINDOW_W) // 2), area.y + 30)
        self.schedule_save_config()

    def create_interface(self):
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.main_box.get_style_context().add_class('music-player-main')
        self._add_resize_grip(self, self.main_box)
        self.main_box.pack_start(self.create_title_bar(), False, False, 0)
        self.player_frame = Gtk.Frame()
        self.player_frame.get_style_context().add_class('music-player-frame')
        player_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        player_box.pack_start(self.create_display_area(), False, False, 0)
        player_box.pack_start(self.create_controls(), False, False, 0)
        player_box.pack_start(self.create_volume_controls(), False, False, 0)
        self.player_frame.add(player_box)
        self.main_box.pack_start(self.player_frame, False, False, 0)
        self.panels = PanelManager(self, self.main_box)
        self.panels.add('eq', 'EQUALIZER', self.create_equalizer())
        self.panels.add('playlist', 'PLAYLIST', self.create_playlist(), expand=True)
        self.set_geometry_hints(None, self._minimum_geometry(220),
                                Gdk.WindowHints.MIN_SIZE)
        self.time_display.connect('notify::label', lambda *_: self.shade_time.set_text(self.time_display.get_text()))
        self.connect('map-event', self._window_mapped)
        self.connect('unmap-event', lambda *_: self._update_analyzer_visibility())
        self.connect('button-press-event', self._resize_press)

    def create_title_bar(self):
        events = Gtk.EventBox()
        events.get_style_context().add_class('music-player-titlebar')
        box = Gtk.Box(spacing=3)
        self.brand_group = Gtk.Box(spacing=8)
        self.brand_group.set_margin_start(19)  # 20 px including the chassis border
        self.brand_label = Gtk.Label(label='LLAMA AMP')
        self.brand_label.get_style_context().add_class('brand')
        self.brand_group.pack_start(self.brand_label, False, False, 0)
        self.version_badge = Gtk.Label(label=f'v{APP_VERSION}')
        self.version_badge.get_style_context().add_class('version-badge')
        self.version_badge.set_valign(Gtk.Align.CENTER)
        self.version_badge.get_accessible().set_name(f'Version {APP_VERSION}')
        self.brand_group.pack_start(self.version_badge, False, False, 0)
        box.pack_start(self.brand_group, False, False, 0)
        self.shade_title = Gtk.Label()
        self.shade_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.shade_title.set_width_chars(8)
        self.shade_title.set_max_width_chars(35)
        self.shade_title.set_no_show_all(True)
        box.pack_start(self.shade_title, True, True, 3)
        self.shade_time = Gtk.Label(label='00:00')
        self.shade_time.set_no_show_all(True)
        box.pack_start(self.shade_time, False, False, 2)
        self.shade_controls = Gtk.Box(spacing=1)
        self.shade_controls.set_no_show_all(True)
        for icon, tip, callback in [('previous', 'Previous', self.previous_song),
                                     ('play', 'Play / Pause (Space)', self.toggle_play_pause),
                                     ('next', 'Next', self.next_song)]:
            button = self._transport_button(icon, tip, callback)
            self.shade_controls.pack_start(button, False, False, 0)
        box.pack_start(self.shade_controls, False, False, 0)
        spacer = Gtk.Label()
        box.pack_start(spacer, True, True, 0)
        self._title_spacer = spacer
        for label, tip, callback in [('⚙', 'Settings', self.on_settings_clicked),
                                     ('▱', 'Windowshade / restore (double-click title bar)', self.toggle_windowshade),
                                     ('−', 'Minimize', lambda *_: self.iconify()),
                                     ('×', 'Close', self.on_close_clicked)]:
            button = Gtk.Button(label=label)
            button.set_tooltip_text(tip)
            button.get_accessible().set_name(tip)
            button.connect('clicked', callback)
            box.pack_start(button, False, False, 0)
        events.add(box)
        events.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        events.connect('button-press-event', self.on_title_press)
        return events

    def on_title_press(self, widget, event):
        if event.button == 3:
            self.show_title_context_menu(widget, event)
            return True
        if event.button != 1:
            return False
        if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            self.toggle_windowshade()
        else:
            self.panels.start_drag(self, event) if hasattr(self, 'panels') else None
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
        return True

    def on_button_press(self, widget, event):
        """Handle button press for visual feedback"""
        if event.button == 1:  # Left mouse button
            widget.get_style_context().add_class('pressed')

    def on_button_release(self, widget, event):
        """Handle button release for visual feedback"""
        if event.button == 1:  # Left mouse button
            widget.get_style_context().remove_class('pressed')

    def on_close_clicked(self, button):
        """Handle close button click — route through destroy so on_destroy
        (config write, playlist save, MPRIS teardown) always runs."""
        self.destroy()

