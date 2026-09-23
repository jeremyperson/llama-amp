"""System tray icon and track-change notifications."""
import gi
import os
import time

from gi.repository import GLib, Gio, Gtk

from .constants import APP_DIR, APP_NAME, NOTIFY_MIN_INTERVAL_S


class DesktopMixin:
    def _init_tray(self):
        """AppIndicator (Ayatana preferred) with Gtk.StatusIcon fallback.
        Degrades to no tray if neither backend exists."""
        self._tray = None
        self._tray_play_item = None
        if self.config.get("tray_icon") is False:
            return
        app_dir = APP_DIR
        svg = os.path.join(app_dir, "llama-amp.svg")
        AI = None
        for module, version in (('AyatanaAppIndicator3', '0.1'), ('AppIndicator3', '0.1')):
            try:
                gi.require_version(module, version)
                AI = getattr(__import__('gi.repository', fromlist=[module]), module)
                break
            except Exception:
                continue
        try:
            if AI is not None:
                ind = AI.Indicator.new('llamaamp', 'llama-amp',
                                       AI.IndicatorCategory.APPLICATION_STATUS)
                if os.path.exists(svg):
                    ind.set_icon_theme_path(app_dir)
                    ind.set_icon_full('llama-amp', APP_NAME)
                ind.set_status(AI.IndicatorStatus.ACTIVE)
                ind.set_menu(self._build_tray_menu())
                self._tray = ind
                self._tray_ai = AI
                self._tray_backend = 'appindicator'
            else:
                icon = (Gtk.StatusIcon.new_from_file(svg) if os.path.exists(svg)
                        else Gtk.StatusIcon.new_from_icon_name('audio-x-generic'))
                icon.set_tooltip_text(APP_NAME)
                icon.connect('activate', lambda *_: self._toggle_window_visible())
                icon.connect('popup-menu', self._on_statusicon_popup)
                self._tray = icon
                self._tray_backend = 'statusicon'
        except Exception as e:
            self.log_debug(f"tray unavailable: {e}")
            self._tray = None

    def _build_tray_menu(self):
        menu = Gtk.Menu()
        show_item = Gtk.MenuItem(label="Show/Hide")
        show_item.connect("activate", lambda *_: self._toggle_window_visible())
        menu.append(show_item)
        menu.append(Gtk.SeparatorMenuItem())
        self._tray_play_item = Gtk.MenuItem(label="Play")
        self._tray_play_item.connect("activate", lambda *_: self.toggle_play_pause(None))
        menu.append(self._tray_play_item)
        next_item = Gtk.MenuItem(label="Next")
        next_item.connect("activate", lambda *_: self.next_song(None))
        menu.append(next_item)
        prev_item = Gtk.MenuItem(label="Previous")
        prev_item.connect("activate", lambda *_: self.previous_song(None))
        menu.append(prev_item)
        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda *_: self.destroy())
        menu.append(quit_item)
        menu.show_all()
        self._tray_menu = menu
        return menu

    def _on_statusicon_popup(self, icon, button, time):
        self._build_tray_menu().popup(None, None, None, None, button, time)

    def _toggle_window_visible(self):
        if self.get_visible():
            self.hide()
        else:
            self.present()

    def toggle_tray_icon(self, *_args):
        """Live enable/disable of the tray icon (persisted)."""
        if self._tray is not None:
            self._hide_tray()
            self.config['tray_icon'] = False
        else:
            self.config['tray_icon'] = True
            self._init_tray()
        self.schedule_save_config()

    def _hide_tray(self):
        if self._tray is None:
            return
        try:
            if getattr(self, '_tray_backend', '') == 'appindicator':
                # PASSIVE removes it from the panel
                self._tray.set_status(self._tray_ai.IndicatorStatus.PASSIVE)
            else:
                self._tray.set_visible(False)
        except Exception:
            pass
        self._tray = None
        self._tray_play_item = None

    def _notify_track(self, summary, body=""):
        """Track-change popup via org.freedesktop.Notifications (raw GDBus — the
        app has no Gtk.Application, so Gio.Notification isn't available).
        Suppressed while the window is focused; rate-limited; replaces the
        previous popup instead of stacking."""
        if self.config.get("notifications", True) is False:
            return
        conn = getattr(self, '_mpris_conn', None)
        if conn is None or self.is_active():
            return
        now = time.monotonic()
        if now - getattr(self, '_notify_last', 0.0) < NOTIFY_MIN_INTERVAL_S:
            return
        self._notify_last = now

        icon = ""
        art = self._mpris_art_url()
        if art and art.startswith('file://'):
            icon = art[7:]
        else:
            svg = os.path.join(APP_DIR,
                               "llama-amp.svg")
            if os.path.exists(svg):
                icon = svg

        def done(c, res):
            try:
                self._notify_id = c.call_finish(res).unpack()[0]
            except Exception as e:
                self.log_debug(f"notify failed: {e}")

        try:
            conn.call('org.freedesktop.Notifications',
                      '/org/freedesktop/Notifications',
                      'org.freedesktop.Notifications', 'Notify',
                      GLib.Variant('(susssasa{sv}i)',
                                   (APP_NAME, getattr(self, '_notify_id', 0),
                                    icon, summary, body, [], {}, 4000)),
                      GLib.VariantType('(u)'),
                      Gio.DBusCallFlags.NONE, 2000, None, done)
        except Exception as e:
            self.log_debug(f"notify call failed: {e}")

    def toggle_notifications(self, *_args):
        self.config['notifications'] = self.config.get('notifications', True) is False
        self.schedule_save_config()
        self.show_drop_feedback(
            "Notifications on" if self.config['notifications'] else "Notifications off")

