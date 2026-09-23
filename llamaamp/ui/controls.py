"""LCD display, transport buttons, sliders and the equalizer panel."""
import os

from gi.repository import GLib, Gdk, Gst, Gtk, Pango

from ..constants import (
    DEFAULT_SONG_TEXT,
    EQ_FREQUENCIES,
    EQ_GAIN_MAX,
    EQ_GAIN_MIN,
    EQ_PRESETS,
    MARQUEE_HOLD_TICKS,
    MARQUEE_SEP,
    MARQUEE_TICK_MS,
    MARQUEE_WINDOW,
    PREAMP_DB_RANGE,
    REPEAT_ALL,
    REPEAT_ONE,
    SHUFFLE_ALBUMS,
    SHUFFLE_TRACKS,
    VOLUME_STEP,
)
from .themes import THEMES


class ControlsMixin:
    def _sync_play_ui(self, playing, *, stopped=False):
        self.playback_state = 'Playing' if playing else 'Stopped' if stopped else 'Paused'
        self.is_playing = bool(playing)
        self.play_btn.queue_draw()
        self.shade_controls.queue_draw()
        context = self.play_btn.get_style_context()
        (context.add_class if playing else context.remove_class)('active')
        self.play_btn.set_tooltip_text('Pause (Space)' if playing else 'Play (Space)')
        self._update_queue_markers()
        self._start_decay()
        if getattr(self, '_tray_play_item', None) is not None:
            self._tray_play_item.set_label('Pause' if playing else 'Play')
        self._mpris_notify_playback()

    def on_stop_button_press(self, widget, event):
        """Right-click on ■ toggles stop-after-current-track (classic Winamp)."""
        if event.button != 3:
            return False  # left-click: normal pressed-visual + clicked handlers
        self._set_sleep(None if self._sleep_after_track else 'track')
        return True

    def _update_stop_btn_cue(self):
        """Latch the stop button while stop-after-current is armed."""
        if getattr(self, 'stop_btn', None) is None:
            return
        ctx = self.stop_btn.get_style_context()
        (ctx.add_class if self._sleep_after_track else ctx.remove_class)('active')

    def update_shuffle_button(self):
        ctx = self.shuffle_btn.get_style_context()
        if self.shuffle == SHUFFLE_ALBUMS:
            self.shuffle_btn.set_label("⇄ ALBUMS")
            self.shuffle_btn.set_tooltip_text("Shuffle: ALBUMS")
            ctx.add_class('active')
        elif self.shuffle == SHUFFLE_TRACKS:
            self.shuffle_btn.set_label("⇄ SHUFFLE")
            self.shuffle_btn.set_tooltip_text("Shuffle: TRACKS")
            ctx.add_class('active')
        else:
            self.shuffle_btn.set_label("⇄ SHUFFLE")
            self.shuffle_btn.set_tooltip_text("Shuffle: OFF")
            ctx.remove_class('active')

    def update_repeat_button(self):
        ctx = self.repeat_btn.get_style_context()
        if self.repeat_mode == REPEAT_ALL:
            self.repeat_btn.set_label("↻ REPEAT")
            self.repeat_btn.set_tooltip_text("Repeat: ALL")
            ctx.add_class('active')
        elif self.repeat_mode == REPEAT_ONE:
            self.repeat_btn.set_label("↻ REPEAT 1")
            self.repeat_btn.set_tooltip_text("Repeat: ONE")
            ctx.add_class('active')
        else:
            self.repeat_btn.set_label("↻ REPEAT")
            self.repeat_btn.set_tooltip_text("Repeat: OFF")
            ctx.remove_class('active')

    def on_display_scroll(self, widget, event):
        """Scroll wheel over the display panel adjusts volume."""
        if event.direction == Gdk.ScrollDirection.UP:
            self._step_volume(VOLUME_STEP)
        elif event.direction == Gdk.ScrollDirection.DOWN:
            self._step_volume(-VOLUME_STEP)
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            ok, _dx, dy = event.get_scroll_deltas()
            if ok and dy:
                self._step_volume(-dy * VOLUME_STEP)
        return True

    def create_display_area(self):
        frame = Gtk.Frame()
        frame.get_style_context().add_class('music-player-display')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.song_label = Gtk.Label(label=DEFAULT_SONG_TEXT, xalign=0)
        self.song_label.get_style_context().add_class('song-title')
        self.song_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.song_label.set_width_chars(20)
        self.song_label.set_max_width_chars(44)
        box.pack_start(self.song_label, False, False, 0)
        middle = Gtk.Box(spacing=8)
        self.album_art = Gtk.Image()
        self.album_art.set_no_show_all(True)
        self.album_art.get_style_context().add_class('music-player-art')
        middle.pack_start(self.album_art, False, False, 0)
        self.time_display = Gtk.Label(label='00:00')
        self.time_display.get_style_context().add_class('music-player-time')
        time_events = Gtk.EventBox()
        time_events.set_visible_window(False)
        time_events.add(self.time_display)
        time_events.set_tooltip_text('Click: elapsed / remaining')
        time_events.connect('button-press-event', self._clock_press)
        middle.pack_start(time_events, False, False, 0)
        self.analyzer = Gtk.DrawingArea()
        self._scaled_size(self.analyzer, 100, 62)
        self.analyzer.set_hexpand(True)
        self.analyzer.get_accessible().set_name('Visualization: spectrum or oscilloscope')
        self.analyzer.set_tooltip_text('Click to switch: spectrum, oscilloscope, off')
        self.analyzer.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.analyzer.connect('button-press-event', self._cycle_visualization)
        self.analyzer.connect('draw', self._draw_analyzer)
        self.analyzer.connect('map', lambda *_: self._update_analyzer_visibility())
        self.analyzer.connect('unmap', lambda *_: self._update_analyzer_visibility())
        middle.pack_start(self.analyzer, True, True, 0)
        box.pack_start(middle, False, False, 0)
        info_row = Gtk.Box(spacing=4)
        self.info_label = Gtk.Label(xalign=0)
        self.info_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.info_label.get_style_context().add_class('muted')
        info_row.pack_start(self.info_label, True, True, 0)
        # Winamp's readouts: inset kbps/kHz digits and mono/stereo lights
        self.kbps_value, self.khz_value = Gtk.Label(label='—'), Gtk.Label(label='—')
        for value, unit in ((self.kbps_value, 'kbps'), (self.khz_value, 'kHz')):
            value.set_width_chars(4)
            value.set_xalign(1)
            value.get_style_context().add_class('readout-value')
            info_row.pack_start(value, False, False, 0)
            unit_label = Gtk.Label(label=unit)
            unit_label.get_style_context().add_class('readout-unit')
            info_row.pack_start(unit_label, False, False, 0)
        self.mono_light, self.stereo_light = Gtk.Label(label='mono'), Gtk.Label(label='stereo')
        for light in (self.mono_light, self.stereo_light):
            light.get_style_context().add_class('channel-light')
            info_row.pack_start(light, False, False, 0)
        box.pack_start(info_row, False, False, 0)
        box.pack_start(self.create_position_slider(), False, False, 0)
        frame.add(box)
        return frame

    def create_controls(self):
        box = Gtk.Box(spacing=2)
        for attr, icon, tip, callback in [
                ('prev_btn', 'previous', 'Previous', self.previous_song),
                ('play_btn', 'play', 'Play / Pause (Space)', self.toggle_play_pause),
                ('stop_btn', 'stop', 'Stop (right-click: stop after track)', self.stop_song),
                ('next_btn', 'next', 'Next', self.next_song),
                ('eject_btn', 'eject', 'Add files (Ctrl+O)', self.add_files)]:
            button = self._transport_button(icon, tip, callback)
            setattr(self, attr, button)
            box.pack_start(button, False, False, 0)
        self.stop_btn.connect('button-press-event', self.on_stop_button_press)
        box.pack_start(Gtk.Label(), True, True, 0)
        self.shuffle_btn = Gtk.Button(label='SHUFFLE')
        self.shuffle_btn.connect('clicked', self.toggle_shuffle)
        self.shuffle_btn.set_tooltip_text('Shuffle: off / tracks / albums (S)')
        box.pack_start(self.shuffle_btn, False, False, 1)
        self.repeat_btn = Gtk.Button(label='REPEAT')
        self.repeat_btn.connect('clicked', self.toggle_repeat)
        self.repeat_btn.set_tooltip_text('Repeat: off / all / one (R)')
        box.pack_start(self.repeat_btn, False, False, 1)
        return box

    def _panel_button(self, name, action):
        button = Gtk.Button()
        button.get_style_context().add_class('panel-control')
        self._scaled_size(button, 28, 28)
        drawing = Gtk.DrawingArea()
        self._scaled_size(drawing, 16, 16)
        drawing.connect('draw', self._draw_panel_control, name, action)
        button.add(drawing)
        return button

    def _draw_panel_control(self, widget, cr, name, action):
        item = self.panels.items.get(name)
        if not item:
            return False
        color = '#152019' if self.theme is THEMES['silver'] else self.theme['text']
        self._cairo_color(cr, color)
        cr.set_line_width(1.5)
        cr.translate((widget.get_allocated_width() - 16 * self.ui_scale) / 2,
                     (widget.get_allocated_height() - 16 * self.ui_scale) / 2)
        cr.scale(self.ui_scale, self.ui_scale)
        if action == 'collapse':
            cr.move_to(3, 8); cr.line_to(13, 8)
            if item['collapsed']:
                cr.move_to(8, 3); cr.line_to(8, 13)
        elif item['attached']:
            # An arrow leaving a window: detach.
            cr.move_to(8, 4); cr.line_to(3, 4); cr.line_to(3, 13)
            cr.line_to(12, 13); cr.line_to(12, 8)
            cr.move_to(7, 9); cr.line_to(14, 2)
            cr.move_to(9, 2); cr.line_to(14, 2); cr.line_to(14, 7)
        else:
            # A downward arrow into a dock: reattach.
            cr.move_to(3, 10); cr.line_to(3, 14); cr.line_to(13, 14); cr.line_to(13, 10)
            cr.move_to(8, 2); cr.line_to(8, 11)
            cr.move_to(4, 7); cr.line_to(8, 11); cr.line_to(12, 7)
        cr.stroke()
        return False

    def _transport_button(self, icon, tooltip, callback):
        button = Gtk.Button()
        drawing = Gtk.DrawingArea()
        self._scaled_size(drawing, 18, 16)
        drawing.connect('draw', self._draw_transport, icon)
        button.add(drawing)
        button.set_tooltip_text(tooltip)
        button.get_accessible().set_name(tooltip)
        button.connect('clicked', callback)
        return button

    def _draw_transport(self, widget, cr, icon):
        if icon == 'play' and self.is_playing:
            icon = 'pause'
        cr.translate(widget.get_allocated_width() / 2 - 8 * self.ui_scale,
                     widget.get_allocated_height() / 2 - 7 * self.ui_scale)
        cr.scale(self.ui_scale, self.ui_scale)
        color = self.theme['accent'] if icon in ('play', 'pause') else ('#152019' if self.theme is THEMES['silver'] else self.theme['text'])
        self._cairo_color(cr, color)
        if icon == 'pause':
            cr.rectangle(3, 2, 4, 10); cr.rectangle(10, 2, 4, 10)
        elif icon == 'stop':
            cr.rectangle(3, 2, 11, 11)
        elif icon == 'eject':
            cr.move_to(2, 9); cr.line_to(8, 2); cr.line_to(14, 9); cr.close_path()
            cr.rectangle(2, 11, 12, 2)
        else:
            if icon == 'previous':
                cr.translate(16, 0); cr.scale(-1, 1)
            cr.move_to(3, 1); cr.line_to(13, 7); cr.line_to(3, 13); cr.close_path()
            if icon in ('next', 'previous'):
                cr.rectangle(13, 1, 2, 12)
        cr.fill()
        return False

    @staticmethod
    def _cairo_color(cr, color, alpha=1):
        cr.set_source_rgba(*(int(color[i:i+2], 16) / 255 for i in (1, 3, 5)), alpha)

    def create_position_slider(self):
        """Bare position scale — lives at the bottom of the LCD display panel."""
        self.position_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        self.position_scale.set_can_focus(False)  # arrows are global shortcuts; no focus ring
        self.position_scale.get_style_context().add_class('music-player-slider')
        self.position_scale.set_range(0, 100)
        self.position_scale.set_value(0)
        self.position_scale.set_draw_value(False)
        self.position_scale.set_tooltip_text("Song Position")
        self.position_scale.connect("button-press-event", self.on_position_pressed)
        self.position_scale.connect("button-release-event", self.on_position_released)
        # Swallow scroll: GTK's default scroll-to-change-value moves the slider
        # without seeking, then the position timer snaps it back — confusing.
        self.position_scale.connect("scroll-event", lambda *a: True)
        return self.position_scale

    def create_volume_controls(self):
        container = Gtk.Grid()
        container.set_column_homogeneous(True)
        container.set_column_spacing(0)
        self.level_controls = container
        for column, width, label, attr, limits, callback, formatter in (
                (0, 3, 'VOLUME', 'volume_scale', (0, 1), self.on_volume_changed, self.format_volume_value),
                (3, 2, 'BALANCE', 'balance_scale', (-1, 1), self.on_balance_changed, self.format_balance_value)):
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            group.set_hexpand(True)
            group.set_margin_start(6 if column else 0)
            group.set_margin_end(0 if column else 6)
            header = Gtk.Box()
            caption = Gtk.Label(label=label, xalign=0)
            caption.get_style_context().add_class('music-player-label')
            header.pack_start(caption, True, True, 0)
            readout = Gtk.Label(xalign=1)
            readout.get_style_context().add_class('muted')
            header.pack_end(readout, False, False, 0)
            group.pack_start(header, False, False, 0)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, *limits, .01)
            scale.set_can_focus(True)
            scale.set_draw_value(False)
            scale.get_accessible().set_name(label.title())
            scale.set_tooltip_text('Volume' if column == 0 else 'Balance · double-click to center')
            setattr(self, attr, scale)
            scale.connect('value-changed', callback)
            scale.connect('value-changed', lambda widget, r=readout, f=formatter:
                          r.set_text(f(widget, widget.get_value())))
            readout.set_text(formatter(scale, scale.get_value()))
            if column:
                scale.connect('button-press-event', self._eq_scale_press)
            group.pack_start(scale, False, False, 0)
            container.attach(group, column, 0, width, 1)
        return container

    def create_equalizer(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        controls = Gtk.Box(spacing=4)
        self.eq_on_btn = Gtk.Button(label='ON')
        self.eq_on_btn.connect('clicked', self.toggle_eq_enabled)
        controls.pack_start(self.eq_on_btn, False, False, 0)
        self.preset_button = Gtk.Button(label='Presets ▾')
        self.preset_button.connect('clicked', self._preset_popup)
        controls.pack_start(self.preset_button, False, False, 0)
        reset = Gtk.Button(label='Reset')
        reset.set_tooltip_text('Reset all EQ bands and preamp to 0 dB')
        reset.connect('clicked', self._reset_eq)
        controls.pack_start(reset, False, False, 0)
        self.eq_status = Gtk.Label(xalign=1)
        self.eq_status.set_ellipsize(Pango.EllipsizeMode.END)
        self.eq_status.get_style_context().add_class('muted')
        controls.pack_end(self.eq_status, True, True, 0)
        box.pack_start(controls, False, False, 0)
        bands = Gtk.Box(spacing=1, homogeneous=True)
        self.eq_bars = []
        self._syncing_eq = True
        for index, label in enumerate(['PRE'] + EQ_FREQUENCIES):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            preamp = index == 0
            lo, hi = (-PREAMP_DB_RANGE, PREAMP_DB_RANGE) if preamp else (EQ_GAIN_MIN, EQ_GAIN_MAX)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, lo, hi, .5)
            scale.set_inverted(True)
            self._scaled_size(scale, 24, 68)
            scale.set_draw_value(False)
            scale.add_mark(0, Gtk.PositionType.LEFT, None)
            scale.get_accessible().set_name('Preamp' if preamp else label + ' Hz equalizer gain')
            scale.connect('value-changed', self._eq_scale_changed, index - 1)
            scale.connect('button-press-event', self._eq_scale_press)
            column.pack_start(scale, True, True, 0)
            value = Gtk.Label()
            value.get_style_context().add_class('muted')
            scale._gain_label = value
            column.pack_start(value, False, False, 0)
            caption = Gtk.Label(label=label)
            caption.get_style_context().add_class('muted')
            column.pack_start(caption, False, False, 0)
            bands.pack_start(column, True, True, 0)
            if preamp:
                self.preamp_bar = scale
            else:
                self.eq_bars.append(scale)
        box.pack_start(bands, False, False, 0)
        self._syncing_eq = False
        self._sync_eq_controls()
        return box

    def _eq_scale_changed(self, scale, index):
        if self._syncing_eq:
            return
        db = scale.get_value()
        if index < 0:
            self.preamp_value = (db / PREAMP_DB_RANGE + 1) / 2
            self._apply_preamp()
        else:
            self.eq_values[index] = self.db_to_eq_value(db)
            self._apply_eq_band(index)
        self._sync_eq_controls()
        self.schedule_save_config()

    def _eq_scale_press(self, scale, event):
        if event.button == 1 and event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            scale.set_value(0)
            return True
        if event.button == 3:
            self.show_eq_preset_menu(event)
            return True
        return False

    def _sync_eq_controls(self):
        if not hasattr(self, 'eq_status'):
            return
        self._syncing_eq = True
        try:
            scales = [self.preamp_bar] + self.eq_bars
            gains = [(self.preamp_value * 2 - 1) * PREAMP_DB_RANGE] + [self.eq_value_to_db(v) for v in self.eq_values]
            for scale, gain in zip(scales, gains):
                scale.set_value(gain)
                scale._gain_label.set_text(f'{gain:+g}')
                tip = f'{gain:+g} dB · double-click to reset · arrows adjust'
                if self.direct_mode:
                    tip += ' · stored for later; Direct Mode bypasses EQ'
                elif not self.eq_enabled:
                    tip += ' · stored for later; equalizer is off'
                scale.set_tooltip_text(tip)
                context = scale.get_style_context()
                (context.add_class if self.direct_mode or not self.eq_enabled else context.remove_class)('eq-bypassed')
            self.eq_on_btn.set_label('Bypassed' if self.direct_mode else 'ON' if self.eq_enabled else 'OFF')
            self.eq_on_btn.set_sensitive(not self.direct_mode)
            self.eq_on_btn.set_tooltip_text('Direct Mode bypasses EQ; disable it in Audio Output to use the equalizer'
                                            if self.direct_mode else 'Enable / disable the equalizer')
            context = self.eq_on_btn.get_style_context()
            (context.add_class if self.eq_enabled and not self.direct_mode else context.remove_class)('active')
            self.eq_status.set_text('Bypassed · Direct Mode' if self.direct_mode
                                    else 'Equalizer off' if not self.eq_enabled else self._current_preset_name() or 'Custom')
        finally:
            self._syncing_eq = False

    def _reset_eq(self, *_args):
        self.preamp_value = .5
        self.apply_eq_preset('Flat')
        self._sync_eq_controls()

    def _preset_popup(self, button):
        menu = Gtk.Menu()
        self._append_eq_preset_items(menu)
        menu.show_all()
        self._preset_menu = menu
        menu.popup_at_widget(button, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, None)

    def apply_eq_preset(self, name):
        """Apply a named preset from EQ_PRESETS to all bands."""
        dbs = EQ_PRESETS.get(name)
        if not dbs or len(dbs) != len(self.eq_values):
            return
        self.eq_values = [self.db_to_eq_value(db) for db in dbs]
        self.apply_all_eq()
        for bar in self.eq_bars:
            bar.queue_draw()
        self.schedule_save_config()

    def _current_preset_name(self):
        """Which preset the current gains match (None = custom curve)."""
        for name, dbs in EQ_PRESETS.items():
            values = [self.db_to_eq_value(db) for db in dbs]
            if all(abs(a - b) < 0.02 for a, b in zip(self.eq_values, values)):
                return name
        return None

    def _append_eq_preset_items(self, menu):
        """Radio-style preset entries; the active curve is checked."""
        current = self._current_preset_name()
        for name in EQ_PRESETS:
            label = "Flat (reset)" if name == "Flat" else name
            item = Gtk.CheckMenuItem(label=label)
            item.set_draw_as_radio(True)
            item.set_active(name == current)
            item.connect("activate", lambda _w, n=name: self.apply_eq_preset(n))
            menu.append(item)
        if current is None:
            custom = Gtk.CheckMenuItem(label="Custom")
            custom.set_draw_as_radio(True)
            custom.set_active(True)
            custom.set_sensitive(False)
            menu.append(custom)

    def show_eq_preset_menu(self, event):
        """Right-click menu on the EQ bars: apply a preset."""
        menu = Gtk.Menu()
        header = Gtk.MenuItem(label="EQ Presets")
        header.set_sensitive(False)
        menu.append(header)
        menu.append(Gtk.SeparatorMenuItem())
        self._append_eq_preset_items(menu)
        menu.show_all()
        self._eq_menu = menu  # keep referenced while open
        menu.popup_at_pointer(event)

    def _set_title_text(self, text):
        """Central song-title setter: short titles display plainly; long ones
        scroll Winamp-style through a fixed 44-char window."""
        if hasattr(self, 'shade_title'):
            self.shade_title.set_text(text)
            self.shade_title.set_tooltip_text(text)
        self._title_full = text
        if len(text) <= MARQUEE_WINDOW:
            self._marquee_stop()
            self.song_label.set_ellipsize(Pango.EllipsizeMode.END)
            self.song_label.set_text(text)
            return
        # Ellipsize must be off or GTK re-truncates every rotated frame
        self.song_label.set_ellipsize(Pango.EllipsizeMode.NONE)
        self._marquee_circ = text + MARQUEE_SEP
        self._marquee_offset = 0
        self._marquee_hold = MARQUEE_HOLD_TICKS
        self.song_label.set_text(text[:MARQUEE_WINDOW])
        if getattr(self, '_marquee_id', None) is None:
            self._marquee_id = self.tasks.timeout_add(MARQUEE_TICK_MS, self._marquee_tick)

    def _marquee_tick(self):
        if self._marquee_hold > 0:
            self._marquee_hold -= 1
            return True
        circ = self._marquee_circ
        self._marquee_offset = (self._marquee_offset + 1) % len(circ)
        if self._marquee_offset == 0:
            self._marquee_hold = MARQUEE_HOLD_TICKS
        window = (circ + circ)[self._marquee_offset:self._marquee_offset + MARQUEE_WINDOW]
        self.song_label.set_text(window)
        return True

    def _marquee_stop(self):
        if getattr(self, '_marquee_id', None) is not None:
            self.tasks.source_remove(self._marquee_id)
            self._marquee_id = None

    def _refresh_song_label(self):
        """Set the song label from cached tag metadata, else the filename stem."""
        if not self.current_song:
            self._set_title_text(DEFAULT_SONG_TEXT)
            return
        cached = self._cache_get(self._meta_cache, self.current_song)
        if isinstance(cached, dict) and cached.get('title'):
            artist = cached.get('artist')
            title = cached['title']
            self._set_title_text(f"{artist} - {title}" if artist else title)
        else:
            self._set_title_text(self._display_name(self.current_song))

    def _post_load_ui(self, index, file_path):
        """Refresh everything that presents the current track (shared between
        load_song and the gapless handoff commit)."""
        self.order.select(self.entry_ids[index])
        if self.is_playing:
            self.order.commit(self.order.current)
        self._refresh_song_label()
        self._update_album_art(file_path)
        self.get_audio_properties(file_path)
        self.update_audio_display(file_path)
        self._select_row(index)
        self.schedule_save_config()
        self._scrobble_reset()
        self._mpris_notify_track()
        self._notify_track(getattr(self, '_title_full', '') or self._display_name(file_path))
        self._update_queue_markers()
        self._prepare_next()

    # Slider callbacks
    def format_volume_value(self, scale, value):
        """Format volume value as percentage"""
        return f"{int(value * 100)}%"

    def format_balance_value(self, scale, value):
        """Format balance value as percentage with L/R indication"""
        if value < 0:
            return f"L{int(abs(value) * 100)}%"
        elif value > 0:
            return f"R{int(value * 100)}%"
        else:
            return "CENTER"

    def on_volume_changed(self, scale):
        self.volume = scale.get_value()
        self.player.set_property("volume", self.volume)
        self.schedule_save_config()
        self._mpris_emit({'Volume': GLib.Variant('d', float(self.volume))})

    def on_position_pressed(self, scale, event):
        self.seeking = True

    def on_position_released(self, scale, event):
        position = scale.get_value()
        # Query fresh: self.duration may be stale/zero when paused on a new track
        ok, duration = self.player.query_duration(Gst.Format.TIME)
        if not ok or duration <= 0:
            duration = self.duration
        if duration > 0:
            self._seek_ns(int((position / 100.0) * duration))
        self.seeking = False

    # Update methods
    def update_position(self):
        if self.is_playing and not self.seeking:
            ok_pos, position = self.player.query_position(Gst.Format.TIME)
            duration = self.duration  # cached on the PLAYING transition
            if (ok_pos and position >= 0 and duration <= 0
                    and self.current_song and self._is_stream_url(self.current_song)):
                # Live stream: show elapsed listen time; slider stays put
                pos_sec = position // Gst.SECOND
                if pos_sec != getattr(self, '_last_pos_sec', None):
                    self._last_pos_sec = pos_sec
                    self.time_display.set_text(f"{pos_sec // 60:01d}:{pos_sec % 60:02d}")
            elif ok_pos and position >= 0 and duration > 0:
                self.position = position

                pos_sec = position // Gst.SECOND
                if pos_sec != getattr(self, '_last_pos_sec', None):
                    self._last_pos_sec = pos_sec
                    self.time_display.set_text(self._fmt_clock(position, duration))

                progress = (position / duration) * 100
                # Skip sub-pixel slider updates (~340px trough → 0.25% steps)
                if abs(progress - getattr(self, '_last_progress', -1)) >= 0.25:
                    self._last_progress = progress
                    self.position_scale.set_value(progress)
                self._crossfade_tick(position)
        self._scrobble_tick()
        # Song-end is handled by the real EOS bus message (on_bus_eos), not polling.
        return True

    def _toggle_time_mode(self, *_args):
        """Flip the clock between elapsed and -remaining (click, Winamp-style)."""
        self._time_remaining = not self._time_remaining
        self._last_pos_sec = None   # force an immediate re-render next tick
        # Re-render right away, even while paused
        if self.duration > 0 and self.position >= 0:
            self.time_display.set_text(self._fmt_clock(self.position, self.duration))
        self.schedule_save_config()
        return True

    def _fmt_clock(self, position_ns, duration_ns):
        if self._time_remaining and duration_ns > 0:
            sec = max(0, (duration_ns - position_ns) // Gst.SECOND)
            return f"-{sec // 60:01d}:{sec % 60:02d}"
        sec = position_ns // Gst.SECOND
        return f"{sec // 60:01d}:{sec % 60:02d}"

    def _clock_press(self, widget, event):
        if event.button == 1 and event.type == Gdk.EventType.BUTTON_PRESS:
            # GDK sends a second ordinary press before DOUBLE_BUTTON_PRESS.
            last = getattr(self, '_clock_last_click', None)
            interval = Gtk.Settings.get_default().get_property('gtk-double-click-time')
            if last is None or event.time - last > interval:
                self._toggle_time_mode()
            self._clock_last_click = event.time
            return True
        return False

    def update_audio_display(self, file_path=None):
        """Update the audio information display with current properties"""
        if not self.audio_properties:
            return
            
        sample_rate = self.audio_properties['sample_rate']
        bitrate = self.audio_properties['bitrate']
        channels = self.audio_properties['channels']
        
        self.kbps_value.set_text(str(bitrate) if bitrate else '—')
        self.khz_value.set_text(f'{sample_rate / 1000:g}' if sample_rate else '—')
        for light, lit in ((self.mono_light, channels == 1), (self.stereo_light, channels == 2)):
            (light.get_style_context().add_class if lit else light.get_style_context().remove_class)('lit')

        # Get dynamic file type from current song or provided file path
        file_type = "AUDIO"  # Default fallback
        target_file = file_path or self.current_song
        if target_file and self._is_stream_url(target_file):
            file_type = "NET"
        elif target_file:
            file_ext = os.path.splitext(target_file)[1].lower()
            if file_ext == '.mp3':
                file_type = "MP3"
            elif file_ext == '.flac':
                file_type = "FLAC"
            elif file_ext == '.wav':
                file_type = "WAV"
            elif file_ext == '.ogg':
                file_type = "OGG"
            elif file_ext == '.m4a':
                file_type = "M4A"
            elif file_ext == '.aac':
                file_type = "AAC"
            elif file_ext == '.wma':
                file_type = "WMA"
            else:
                file_type = file_ext[1:].upper() if file_ext else "AUDIO"
        
        # Format and shuffle/repeat status; the lights cover mono and stereo only
        status_parts = [file_type if channels in (0, 1, 2) else f"{file_type} · {channels} ch"]
        if self.shuffle == SHUFFLE_ALBUMS:
            status_parts.append("ALBUMS")
        elif self.shuffle == SHUFFLE_TRACKS:
            status_parts.append("SHUFFLE")
        if self.repeat_mode == REPEAT_ALL:
            status_parts.append("REPEAT")
        elif self.repeat_mode == REPEAT_ONE:
            status_parts.append("REPEAT 1")

        self.info_label.set_text(" • ".join(status_parts))

