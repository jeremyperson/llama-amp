"""Settings, appearance and playlist menus, and small dialogs."""
from gi.repository import GLib, Gdk, Gtk

from ..constants import APP_NAME, APP_VERSION, IS_FLATPAK
from ..ui.themes import THEMES


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
        self._choice_menu(menu, 'Theme', 'theme', [(key, value['name']) for key, value in THEMES.items()])
        visualization, sub = Gtk.MenuItem(label='Visualization'), Gtk.Menu()
        for key, caption in [('visualization', 'Show visualization'), ('peaks', 'Falling peak caps')]:
            item = Gtk.CheckMenuItem(label=caption)
            item.set_active(self.config[key])
            item.connect('toggled', lambda w, k=key: self._set_appearance(k, w.get_active()))
            sub.append(item)
        self._choice_menu(sub, 'Mode', 'vis_mode', [('spectrum', 'Spectrum analyzer'),
                                                    ('scope', 'Oscilloscope')])
        self._choice_menu(sub, 'Colors', 'palette', [(None, 'Follow theme'), ('green', 'Green'),
                                                     ('classic', 'Green / yellow / red'), ('amber', 'Amber')])
        self._choice_menu(sub, 'Falloff', 'falloff', [('slow', 'Slow'), ('normal', 'Normal'), ('fast', 'Fast')])
        visualization.set_submenu(sub)
        menu.append(visualization)
        view, sub = Gtk.MenuItem(label='View'), Gtk.Menu()
        shade = Gtk.CheckMenuItem(label='Windowshade')
        shade.set_active(self._windowshade)
        shade.connect('activate', self.toggle_windowshade)
        sub.append(shade)
        art = Gtk.CheckMenuItem(label='Album artwork')
        art.set_active(self.config['show_art'])
        art.connect('toggled', lambda w: self._set_appearance('show_art', w.get_active()))
        sub.append(art)
        for name, panel in self.panels.items.items():
            item = Gtk.CheckMenuItem(label=panel['title'].title())
            item.set_active(panel['visible'])
            item.connect('toggled', lambda w, n=name: self.panels.set_visible(n, w.get_active()))
            sub.append(item)
        reset = Gtk.MenuItem(label='Reset layout')
        reset.connect('activate', self._reset_layout)
        sub.append(reset)
        view.set_submenu(sub)
        menu.append(view)

    def _popup_actions(self, button, actions):
        menu = Gtk.Menu()
        for caption, callback in actions:
            item = Gtk.MenuItem(label=caption)
            item.connect('activate', callback)
            menu.append(item)
        menu.show_all()
        self._actions_menu = menu
        menu.popup_at_widget(button, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, None)
        return menu

    def _add_popup(self, button):
        self._popup_actions(button, [('Files… (Ctrl+O)', self.add_files),
                                     ('Folder…', self.add_folder), ('Stream URL… (Ctrl+L)', self.open_url_dialog)])

    def _playlist_popup(self, button):
        actions = [('Undo edit (Ctrl+Z)', self.undo_playlist),
                   ('Save playlist as…', self._save_playlist_as), ('Save playlist', self._save_playlist_named)]
        for name in self._saved_playlist_names():
            actions.append((f'Load: {name}', lambda _w, n=name: self._load_named_playlist(n)))
        actions.extend([('Export M3U…', self.export_m3u), ('Remove missing files', self.remove_missing),
                        ('Clear playlist', self.clear_playlist)])
        menu = self._popup_actions(button, actions)
        menu.get_children()[0].set_sensitive(bool(self._undo))

    def build_settings_menu(self):
        """The settings menu: audio fidelity toggles, EQ presets, window actions.
        Served by both the titlebar ⚙ button and the titlebar right-click."""
        menu = Gtk.Menu()
        self._appearance_menus(menu)
        menu.append(Gtk.SeparatorMenuItem())
        header = Gtk.MenuItem(label="Audio Output")
        header.set_sensitive(False)
        menu.append(header)

        # Direct mode: bit-transparent playback (bypass EQ/balance/spectrum)
        direct_item = Gtk.CheckMenuItem(label="Direct Mode (bypass EQ/DSP)")
        direct_item.set_active(self.direct_mode)
        direct_item.connect("toggled", self.toggle_direct_mode)
        menu.append(direct_item)

        # ALSA direct: skip the system mixer, exclusive DAC access
        alsa_item = Gtk.CheckMenuItem(label="ALSA Output (bit-perfect to DAC)")
        alsa_item.set_active(self.alsa_output)
        alsa_item.connect("toggled", self.toggle_alsa_output)
        menu.append(alsa_item)

        # ALSA device picker (rebuilt each open = hotplug-aware)
        dev_item = Gtk.MenuItem(label="ALSA Device")
        dev_sub = Gtk.Menu()
        current_dev = self.config.get('alsa_device') or None
        auto_item = Gtk.CheckMenuItem(label="Auto (Analog)")
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

        gapless_item = Gtk.CheckMenuItem(label="Gapless Playback")
        gapless_item.set_active(self.gapless)
        gapless_item.connect("toggled", self.toggle_gapless)
        menu.append(gapless_item)

        # ReplayGain volume normalization (inactive in Direct Mode)
        rg_item = Gtk.MenuItem(label="ReplayGain")
        rg_sub = Gtk.Menu()
        for mode, label in (('off', 'Off'), ('track', 'Track gain'),
                            ('album', 'Album gain')):
            item = Gtk.CheckMenuItem(label=label)
            item.set_draw_as_radio(True)
            item.set_active(self.replaygain == mode)
            item.connect("activate", lambda _w, m=mode: self.set_replaygain(m))
            rg_sub.append(item)
        rg_item.set_submenu(rg_sub)
        menu.append(rg_item)

        tray_item = Gtk.CheckMenuItem(label="Tray Icon")
        tray_item.set_active(self._tray is not None)
        tray_item.connect("toggled", self.toggle_tray_icon)
        menu.append(tray_item)

        scrobble_item = Gtk.MenuItem(label="Scrobbling…")
        scrobble_item.connect("activate", self.show_scrobble_dialog)
        menu.append(scrobble_item)

        notif_item = Gtk.CheckMenuItem(label="Notifications")
        notif_item.set_active(self.config.get('notifications', True) is not False)
        notif_item.connect("toggled", self.toggle_notifications)
        menu.append(notif_item)

        menu.append(Gtk.SeparatorMenuItem())

        # EQ presets as a submenu (they otherwise hide behind the EQ right-click)
        eq_item = Gtk.MenuItem(label="EQ Preset")
        eq_sub = Gtk.Menu()
        self._append_eq_preset_items(eq_sub)
        eq_item.set_submenu(eq_sub)
        menu.append(eq_item)

        menu.append(Gtk.SeparatorMenuItem())

        url_item = Gtk.MenuItem(label="Open URL…")
        url_item.connect("activate", self.open_url_dialog)
        menu.append(url_item)

        # Named playlists
        pl_item = Gtk.MenuItem(label="Playlists")
        pl_sub = Gtk.Menu()
        save_label = (f"Save ({self._playlist_name})" if self._playlist_name else "Save")
        save_item = Gtk.MenuItem(label=save_label)
        save_item.set_sensitive(bool(self._playlist_name and self.playlist))
        save_item.connect("activate", self._save_playlist_named)
        pl_sub.append(save_item)
        save_as_item = Gtk.MenuItem(label="Save As…")
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
        sleep_item = Gtk.MenuItem(label="Sleep Timer")
        sleep_sub = Gtk.Menu()
        armed_min = None
        if self._sleep_deadline is not None:
            armed_min = max(0, (self._sleep_deadline - GLib.get_monotonic_time())
                            // 60_000_000) + 1
        choices = [("Off", None), ("After current track", 'track'),
                   ("15 min", 15), ("30 min", 30), ("60 min", 60)]
        for label, mode in choices:
            active = self._sleep_mode == mode
            if isinstance(mode, int) and active and armed_min is not None:
                label = f"{label} ({armed_min} min left)"
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
                    label=f"⬆ Update to v{self._update_info['version']}…")
                update_item.connect("activate", self._start_update)
            else:
                update_item = Gtk.MenuItem(label="Check for Updates…")
                update_item.connect("activate",
                                    lambda _w: self._check_updates(manual=True))
            menu.append(update_item)

            autoupd_item = Gtk.CheckMenuItem(label="Check for Updates on Startup")
            autoupd_item.set_active(
                self.config.get('update_check', True) is not False)
            autoupd_item.connect("toggled", self._toggle_update_check)
            menu.append(autoupd_item)

        about_item = Gtk.MenuItem(label=f"About {APP_NAME}")
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
        dialog.set_title(f"About {APP_NAME}")
        dialog.set_program_name(APP_NAME)
        dialog.set_version(APP_VERSION)
        dialog.set_comments("A Python GTK music player with a modern, Winamp-inspired interface")
        dialog.set_copyright("© 2026 Jeremy Person")
        dialog.set_website("https://github.com/jeremyperson/llama-amp")
        dialog.set_website_label("GitHub")
        dialog.set_authors(["Jeremy Person"])
        dialog.run()
        dialog.destroy()

    def open_url_dialog(self, *_args):
        """Open an internet-radio / stream URL (Ctrl+L)."""
        dialog = Gtk.Dialog(title="Open URL", transient_for=self, modal=True)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_placeholder_text("https://…")
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
        box.add(Gtk.Label(label="Stream URL (SHOUTcast / Icecast / direct):"))
        box.add(entry)
        dialog.show_all()
        response = dialog.run()
        url = entry.get_text().strip()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not url:
            return
        if not self._is_stream_url(url):
            self.show_drop_feedback("Not an http(s) URL")
            return
        self._add_paths([url])
        self._play_index(len(self.playlist) - 1)

