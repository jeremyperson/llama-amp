"""Settings, appearance and playlist menus, and small dialogs."""
import math

from gi.repository import GLib, Gdk, Gst, Gtk

from ..constants import APP_NAME, APP_VERSION, IS_FLATPAK
from ..playlist import SORT_KEYS
from .themes import THEMES
from ..i18n import _, ngettext


class MenusMixin:
    def _set_appearance(self, key, value):
        self.config[key] = value
        if key == 'theme':
            self.config['palette'] = None
            self.setup_styling()
        if key == 'show_art':
            self._update_album_art(self.current_song)
        if key in ('vis_mode', 'visualization'):
            self.analyzer_state.reset()
            self.scope_state.reset()
        self.analyzer.queue_draw()
        self._update_analyzer_visibility()
        self.schedule_save_config()

    def _choice_menu(self, parent, label, key, choices):
        item, submenu = Gtk.MenuItem(label=label), Gtk.Menu()
        for value, caption in choices:
            choice = Gtk.CheckMenuItem(label=caption)
            choice.set_draw_as_radio(True)
            choice.set_active(self.config.get(key) == value)
            choice.connect('activate', lambda _w, k=key, v=value: self._set_appearance(k, v))
            submenu.append(choice)
        item.set_submenu(submenu)
        parent.append(item)

    def _appearance_menus(self, menu):
        self._choice_menu(menu, _('Theme'), 'theme', [(key, _(value['name'])) for key, value in THEMES.items()])
        visualization, sub = Gtk.MenuItem(label=_('Visualization')), Gtk.Menu()
        for key, caption in [('visualization', _('Show visualization')), ('peaks', _('Falling peak caps'))]:
            item = Gtk.CheckMenuItem(label=caption)
            item.set_active(self.config[key])
            item.connect('toggled', lambda w, k=key: self._set_appearance(k, w.get_active()))
            sub.append(item)
        self._choice_menu(sub, _('Mode'), 'vis_mode', [('spectrum', _('Spectrum analyzer')),
                                                       ('scope', _('Oscilloscope'))])
        self._choice_menu(sub, _('Colors'), 'palette', [(None, _('Follow theme')), ('green', _('Green')),
                                                        ('classic', _('Green / yellow / red')), ('amber', _('Amber'))])
        self._choice_menu(sub, _('Falloff'), 'falloff', [('slow', _('Slow')), ('normal', _('Normal')),
                                                         ('fast', _('Fast'))])
        visualization.set_submenu(sub)
        menu.append(visualization)
        view, sub = Gtk.MenuItem(label=_('View')), Gtk.Menu()
        shade = Gtk.CheckMenuItem(label=_('Windowshade'))
        shade.set_active(self._windowshade)
        shade.connect('activate', self.toggle_windowshade)
        sub.append(shade)
        double = Gtk.CheckMenuItem(label=_('Double size (Ctrl+D)'))
        double.set_active(self.ui_scale == 2)
        double.connect('activate', self.toggle_double_size)
        sub.append(double)
        art = Gtk.CheckMenuItem(label=_('Album artwork'))
        art.set_active(self.config['show_art'])
        art.connect('toggled', lambda w: self._set_appearance('show_art', w.get_active()))
        sub.append(art)
        for name, panel in self.panels.items.items():
            item = Gtk.CheckMenuItem(label=_(panel['title']).title())
            item.set_active(panel['visible'])
            item.connect('toggled', lambda w, n=name: self.panels.set_visible(n, w.get_active()))
            sub.append(item)
        reset = Gtk.MenuItem(label=_('Reset layout'))
        reset.connect('activate', self._reset_layout)
        sub.append(reset)
        view.set_submenu(sub)
        menu.append(view)

    def _popup_actions(self, button, actions):
        """actions: (caption, callback) pairs; a list in place of the callback
        becomes a submenu of further pairs."""
        def build(pairs):
            menu = Gtk.Menu()
            for caption, callback in pairs:
                item = Gtk.MenuItem(label=caption)
                if isinstance(callback, list):
                    item.set_submenu(build(callback))
                else:
                    item.connect('activate', callback)
                menu.append(item)
            return menu
        menu = build(actions)
        menu.show_all()
        self._actions_menu = menu
        menu.popup_at_widget(button, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, None)
        return menu

    def _add_popup(self, button):
        self._popup_actions(button, [(_('Files… (Ctrl+O)'), self.add_files), (_('Folder…'), self.add_folder),
                                     (_('Stream URL… (Ctrl+L)'), self.open_url_dialog)])

    def _playlist_popup(self, button):
        actions = [(_('Undo edit (Ctrl+Z)'), self.undo_playlist),
                   (_('Save playlist as…'), self._save_playlist_as), (_('Save playlist'), self._save_playlist_named)]
        for name in self._saved_playlist_names():
            actions.append((_('Load: {name}').format(name=name), lambda _w, n=name: self._load_named_playlist(n)))
        actions.append((_('Sort'), [(_(caption), lambda _w, k=key: self.sort_playlist(k))
                                    for key, caption in SORT_KEYS.items()]))
        actions.extend([(_('Export M3U…'), self.export_m3u), (_('Remove missing files'), self.remove_missing),
                        (_('Clear playlist'), self.clear_playlist)])
        menu = self._popup_actions(button, actions)
        menu.get_children()[0].set_sensitive(bool(self._undo))

    def build_settings_menu(self):
        """The settings menu: audio fidelity toggles, EQ presets, window actions.
        Served by both the titlebar ⚙ button and the titlebar right-click."""
        menu = Gtk.Menu()
        self._skin_menu(menu)
        self._appearance_menus(menu)
        menu.append(Gtk.SeparatorMenuItem())
        header = Gtk.MenuItem(label=_("Audio Output"))
        header.set_sensitive(False)
        menu.append(header)

        # Direct mode: bit-transparent playback (bypass EQ/balance/spectrum)
        direct_item = Gtk.CheckMenuItem(label=_("Direct Mode (bypass EQ/DSP)"))
        direct_item.set_active(self.direct_mode)
        direct_item.connect("toggled", self.toggle_direct_mode)
        menu.append(direct_item)

        # ALSA direct: skip the system mixer, exclusive DAC access
        alsa_item = Gtk.CheckMenuItem(label=_("ALSA Output (bit-perfect to DAC)"))
        alsa_item.set_active(self.alsa_output)
        alsa_item.connect("toggled", self.toggle_alsa_output)
        menu.append(alsa_item)

        # ALSA device picker (rebuilt each open = hotplug-aware)
        dev_item = Gtk.MenuItem(label=_("ALSA Device"))
        dev_sub = Gtk.Menu()
        current_dev = self.config.get('alsa_device') or None
        auto_item = Gtk.CheckMenuItem(label=_("Auto (Analog)"))
        auto_item.set_draw_as_radio(True)
        auto_item.set_active(current_dev is None)
        auto_item.connect("activate", lambda _w: self._select_alsa_device(None))
        dev_sub.append(auto_item)
        for dev, label in self._list_alsa_devices():
            item = Gtk.CheckMenuItem(label=f"{label}  ({dev})")
            item.set_draw_as_radio(True)
            item.set_active(dev == current_dev)
            item.connect("activate", lambda _w, d=dev: self._select_alsa_device(d))
            dev_sub.append(item)
        dev_item.set_submenu(dev_sub)
        menu.append(dev_item)

        gapless_item = Gtk.CheckMenuItem(label=_("Gapless Playback"))
        gapless_item.set_active(self.gapless)
        gapless_item.connect("toggled", self.toggle_gapless)
        menu.append(gapless_item)

        # Crossfade overlaps two players in the sound server's mixer
        fade_item = Gtk.MenuItem(label=_("Crossfade (needs ALSA Output off)") if self.alsa_output else _("Crossfade"))
        fade_sub = Gtk.Menu()
        for seconds in (0, 2, 4, 6, 8, 10):
            item = Gtk.CheckMenuItem(label=ngettext("{count} second", "{count} seconds", seconds).format(count=seconds)
                                     if seconds else _("Off"))
            item.set_draw_as_radio(True)
            item.set_active(self.config.get('crossfade_s', 0) == seconds)
            item.connect("activate", lambda _w, s=seconds: self._set_crossfade(s))
            fade_sub.append(item)
        fade_item.set_submenu(fade_sub)
        fade_item.set_sensitive(not self.alsa_output)
        menu.append(fade_item)

        # ReplayGain volume normalization (inactive in Direct Mode)
        rg_item = Gtk.MenuItem(label=_("ReplayGain"))
        rg_sub = Gtk.Menu()
        for mode, label in (('off', _('Off')), ('track', _('Track gain')),
                            ('album', _('Album gain'))):
            item = Gtk.CheckMenuItem(label=label)
            item.set_draw_as_radio(True)
            item.set_active(self.replaygain == mode)
            item.connect("activate", lambda _w, m=mode: self.set_replaygain(m))
            rg_sub.append(item)
        rg_item.set_submenu(rg_sub)
        menu.append(rg_item)

        tray_item = Gtk.CheckMenuItem(label=_("Tray Icon"))
        tray_item.set_active(self._tray is not None)
        tray_item.connect("toggled", self.toggle_tray_icon)
        menu.append(tray_item)

        scrobble_item = Gtk.MenuItem(label=_("Scrobbling…"))
        scrobble_item.connect("activate", self.show_scrobble_dialog)
        menu.append(scrobble_item)

        notif_item = Gtk.CheckMenuItem(label=_("Notifications"))
        notif_item.set_active(self.config.get('notifications', True) is not False)
        notif_item.connect("toggled", self.toggle_notifications)
        menu.append(notif_item)

        menu.append(Gtk.SeparatorMenuItem())

        # EQ presets as a submenu (they otherwise hide behind the EQ right-click)
        eq_item = Gtk.MenuItem(label=_("EQ Preset"))
        eq_sub = Gtk.Menu()
        self._append_eq_preset_items(eq_sub)
        eq_item.set_submenu(eq_sub)
        menu.append(eq_item)

        menu.append(Gtk.SeparatorMenuItem())

        url_item = Gtk.MenuItem(label=_("Open URL…"))
        url_item.connect("activate", self.open_url_dialog)
        menu.append(url_item)

        # Named playlists
        pl_item = Gtk.MenuItem(label=_("Playlists"))
        pl_sub = Gtk.Menu()
        save_label = (_("Save ({name})").format(name=self._playlist_name) if self._playlist_name else _("Save"))
        save_item = Gtk.MenuItem(label=save_label)
        save_item.set_sensitive(bool(self._playlist_name and self.playlist))
        save_item.connect("activate", self._save_playlist_named)
        pl_sub.append(save_item)
        save_as_item = Gtk.MenuItem(label=_("Save As…"))
        save_as_item.set_sensitive(bool(self.playlist))
        save_as_item.connect("activate", self._save_playlist_as)
        pl_sub.append(save_as_item)
        names = self._saved_playlist_names()
        if names:
            pl_sub.append(Gtk.SeparatorMenuItem())
            for name in names:
                item = Gtk.CheckMenuItem(label=name)
                item.set_draw_as_radio(True)
                item.set_active(name == self._playlist_name)
                item.connect("activate",
                             lambda _w, n=name: self._load_named_playlist(n))
                pl_sub.append(item)
        pl_item.set_submenu(pl_sub)
        menu.append(pl_item)

        # Sleep timer
        sleep_item = Gtk.MenuItem(label=_("Sleep Timer"))
        sleep_sub = Gtk.Menu()
        armed_min = None
        if self._sleep_deadline is not None:
            armed_min = max(0, (self._sleep_deadline - GLib.get_monotonic_time())
                            // 60_000_000) + 1
        choices = [(_("Off"), None), (_("After current track"), 'track')] + \
                  [(_("{minutes} min").format(minutes=minutes), minutes) for minutes in (15, 30, 60)]
        for label, mode in choices:
            active = self._sleep_mode == mode
            if isinstance(mode, int) and active and armed_min is not None:
                label = _("{choice} ({minutes} min left)").format(choice=label, minutes=armed_min)
            item = Gtk.CheckMenuItem(label=label)
            item.set_draw_as_radio(True)
            item.set_active(active)
            item.connect("activate", lambda _w, m=mode: self._set_sleep(m))
            sleep_sub.append(item)
        sleep_item.set_submenu(sleep_sub)
        menu.append(sleep_item)

        menu.append(Gtk.SeparatorMenuItem())

        # Updates: one action item (check, or install if one is known) plus
        # the startup-check toggle. Hidden in Flatpak — the store updates it.
        if not IS_FLATPAK:
            if getattr(self, '_update_info', None):
                update_item = Gtk.MenuItem(
                    label=_('⬆ Update to v{version}…').format(version=self._update_info['version']))
                update_item.connect("activate", self._start_update)
            else:
                update_item = Gtk.MenuItem(label=_("Check for Updates…"))
                update_item.connect("activate",
                                    lambda _w: self._check_updates(manual=True))
            menu.append(update_item)

            autoupd_item = Gtk.CheckMenuItem(label=_("Check for Updates on Startup"))
            autoupd_item.set_active(
                self.config.get('update_check', True) is not False)
            autoupd_item.connect("toggled", self._toggle_update_check)
            menu.append(autoupd_item)

        about_item = Gtk.MenuItem(label=_('About {app}').format(app=APP_NAME))
        about_item.connect("activate", self.show_about_dialog)
        menu.append(about_item)

        menu.show_all()
        return menu

    def on_settings_clicked(self, button):
        """Open the settings menu from the titlebar gear button. The menu is
        kept as an attribute — a local would be garbage-collected while open."""
        self._settings_menu = self.build_settings_menu()
        self._settings_menu.popup_at_widget(button, Gdk.Gravity.SOUTH_EAST,
                                            Gdk.Gravity.NORTH_EAST, None)

    def show_title_context_menu(self, widget, event):
        """Show the settings menu on title bar right-click"""
        menu = self.build_settings_menu()
        menu.popup(None, None, None, None, event.button, event.time)

    def show_about_dialog(self, widget):
        """Show about dialog"""
        dialog = Gtk.AboutDialog()
        dialog.set_transient_for(self)
        dialog.set_modal(True)
        dialog.set_title(_('About {app}').format(app=APP_NAME))
        dialog.set_program_name(APP_NAME)
        dialog.set_version(APP_VERSION)
        dialog.set_comments(_("A Python GTK music player with a modern, Winamp-inspired interface"))
        dialog.set_copyright("© 2026 Jeremy Person")
        dialog.set_website("https://github.com/jeremyperson/llama-amp")
        dialog.set_website_label("GitHub")
        dialog.set_authors(["Jeremy Person"])
        dialog.run()
        dialog.destroy()

    def open_url_dialog(self, *_args):
        """Open an internet-radio / stream URL (Ctrl+L)."""
        dialog = Gtk.Dialog(title=_("Open URL"), transient_for=self, modal=True)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_placeholder_text(_("https://…"))
        entry.set_width_chars(46)
        entry.set_activates_default(True)
        dialog.set_default_response(Gtk.ResponseType.OK)
        # Convenience: prefill from clipboard when it looks like a URL
        try:
            clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text()
            if clip and self._is_stream_url(clip.strip()):
                entry.set_text(clip.strip())
        except Exception:
            pass
        box = dialog.get_content_area()
        box.set_margin_top(10); box.set_margin_bottom(10)
        box.set_margin_start(10); box.set_margin_end(10)
        box.add(Gtk.Label(label=_("Stream URL (SHOUTcast / Icecast / direct):")))
        box.add(entry)
        dialog.show_all()
        response = dialog.run()
        url = entry.get_text().strip()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not url:
            return
        if not self._is_stream_url(url):
            self.show_drop_feedback(_("Not an http(s) URL"))
            return
        self._add_paths([url])
        self._play_index(len(self.playlist) - 1)

    def _jump_to_time(self, text):
        seconds = parse_clock(text)
        return seconds is not None and self.seek_to(seconds)

    def show_jump_to_time_dialog(self, *_args):
        """Winamp's Jump to Time (Ctrl+J): accepts ss, m:ss or h:mm:ss."""
        if (self.playback_state == 'Stopped' or not self.current_song
                or self._is_stream_url(self.current_song)):
            self.show_drop_feedback(_("Play a track to jump within it"))
            return
        dialog = Gtk.Dialog(title=_("Jump to Time"), transient_for=self, modal=True)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           _("_Jump"), Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_text(self._fmt_duration(self._current_position_ns() // Gst.SECOND))
        entry.set_activates_default(True)
        box = dialog.get_content_area()
        box.set_margin_top(10); box.set_margin_bottom(10)
        box.set_margin_start(10); box.set_margin_end(10)
        box.set_spacing(6)
        length = self._fmt_duration(self.duration // Gst.SECOND) if self.duration > 0 else "unknown"
        box.add(Gtk.Label(label=_('Jump to (m:ss) — track length {length}:').format(length=length), xalign=0))
        box.add(entry)
        dialog.show_all()
        while dialog.run() == Gtk.ResponseType.OK:
            if self._jump_to_time(entry.get_text()):
                break
            entry.get_style_context().add_class('error')
            entry.grab_focus()
        dialog.destroy()


def parse_clock(text):
    """Seconds for 'ss', 'm:ss' or 'h:mm:ss' (the last field may have decimals),
    or None when malformed."""
    *whole, last = text.strip().split(':')
    if len(whole) > 2 or not all(part.isdigit() for part in whole):
        return None
    try:
        seconds = float(last)
    except ValueError:
        return None
    if not math.isfinite(seconds) or seconds < 0 or (whole and seconds >= 60):
        return None
    if len(whole) == 2 and int(whole[1]) >= 60:
        return None
    for multiplier, part in zip((60, 3600), reversed(whole)):
        seconds += int(part) * multiplier
    return seconds
