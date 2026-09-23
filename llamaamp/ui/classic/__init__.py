"""Classic mode: Winamp 2 skinned windows (main, equalizer, playlist) driven by
the same player as the modern interface, which is hidden meanwhile."""
import os
import shutil

from gi.repository import Gdk, GLib, Gtk

from ...skin.default import build_default_skin
from ...skin.loader import SKIN_EXTENSIONS, Skin
from .eq_window import ClassicEqWindow
from .main_window import ClassicMainWindow
from .playlist_window import ClassicPlaylistWindow
from ...i18n import _

REFRESH_MS = 33          # the main window repaints at ~30 fps while playing
IDLE_EVERY = 8           # ...and every 8th tick otherwise, like the other windows
SNAP = 10                # docking distance, as in Winamp


class ClassicMode:
    """The set of classic windows. EQ and playlist windows stack under the
    main window (Winamp's docking) until the user drags them away."""

    def __init__(self, app):
        self.app = app
        saved = app.config.get('classic_windows') or {}
        size = saved.get('playlist_size')
        valid = isinstance(size, list) and len(size) == 2 and all(isinstance(n, int) and 100 <= n <= 4000 for n in size)
        self.main = ClassicMainWindow(app)
        self.eq = ClassicEqWindow(app)
        self.playlist = ClassicPlaylistWindow(app, tuple(size) if valid else None)
        self.docked = {'eq': True, 'playlist': True}
        self._ticks = 0
        self.main.connect('configure-event', lambda *_: self.relayout())
        self.main.connect('delete-event', lambda *_: (app.destroy(), True)[1])
        for name in ('eq', 'playlist'):
            window = getattr(self, name)
            window.connect('configure-event', lambda *_args, n=name: self._moved(n))
            window.connect('delete-event', lambda *_args, n=name: (self.toggle_window(n), True)[1])
        self._timer = app.tasks.timeout_add(REFRESH_MS, self._tick)

    def windows(self):
        return [window for window in (self.main, self.eq, self.playlist) if window is not None]

    def show(self, restore):
        self.main.apply_size()
        self.main.show()
        for name in ('eq', 'playlist'):
            window = getattr(self, name)
            if window is not None:
                window.apply_size()
                if restore.get(name, True):
                    window.show()
        self.relayout()

    def destroy(self):
        if self._timer is not None:
            self.app.tasks.source_remove(self._timer)
            self._timer = None
        for window in self.windows():
            window.destroy()

    def toggle_window(self, name):
        window = getattr(self, name)
        if window is None:
            return
        window.set_visible(not window.get_visible())
        self.relayout()
        self.main.refresh()
        self.app.schedule_save_config()

    def _docked_position(self, name):
        """Where a docked window sits: under the main window, or under a docked
        equalizer for the playlist."""
        # Heights from the skin geometry: get_size() lags a resize (shade,
        # double size) until the window manager configures the window.
        scale = self.app.ui_scale
        x, y = self.main.get_position()
        y += self.main.skin_size()[1] * scale
        if name == 'playlist' and self.eq.get_visible() and self.docked['eq']:
            y += self.eq.skin_size()[1] * scale
        return x, y

    def relayout(self):
        """Keep docked windows stacked under the main window."""
        if not self.main.get_visible():
            return False
        for name in ('eq', 'playlist'):
            window = getattr(self, name)
            if window.get_visible() and self.docked[name]:
                position = self._docked_position(name)
                if tuple(window.get_position()) != position:
                    window.move(*position)
        return False

    def _moved(self, name):
        """Magnetic docking: close to its slot a window snaps in, else it floats."""
        window = getattr(self, name)
        x, y = window.get_position()
        expected = self._docked_position(name)
        self.docked[name] = abs(x - expected[0]) <= SNAP and abs(y - expected[1]) <= SNAP
        if self.docked[name] and (x, y) != expected:
            window.move(*expected)
        return False

    def snapshot(self):
        return {'eq': self.eq.get_visible(), 'playlist': self.playlist.get_visible(),
                'playlist_size': [self.playlist.width, self.playlist.height]}

    def refresh_all(self):
        for window in self.windows():
            if window.get_visible():
                window.refresh()

    def _tick(self):
        self._ticks += 1
        idle_tick = self._ticks % IDLE_EVERY == 0
        if self.app.is_playing or idle_tick:
            self.main.refresh()
        if idle_tick:
            for window in (self.eq, self.playlist):
                if window.get_visible():
                    window.refresh()
        return True

    def vis_visible(self):
        return self.main.vis_visible()

    def rescale(self):
        for window in self.windows():
            window.apply_size()
        self.relayout()


class ClassicMixin:
    """Mode switching and skin management on the MusicPlayer."""

    def skins_dir(self):
        return os.path.join(self._data_dir(), 'skins')

    def installed_skins(self):
        try:
            return sorted(name for name in os.listdir(self.skins_dir())
                          if name.lower().endswith(SKIN_EXTENSIONS)
                          or os.path.isdir(os.path.join(self.skins_dir(), name)))
        except OSError:
            return []

    def _builtin_skin(self):
        if self._default_skin is None:
            self._default_skin = build_default_skin()
        return self._default_skin

    def _load_skin(self, skin_id):
        """None = modern interface, 'builtin' = the Llama classic skin, else a
        file or folder in the skins directory."""
        if skin_id in (None, 'builtin'):
            return self._builtin_skin()
        try:
            return Skin.load(os.path.join(self.skins_dir(), skin_id), self._builtin_skin())
        except Exception as error:
            self.log_debug(f"skin {skin_id} failed to load: {error}")
            self.show_drop_feedback(_("Couldn't load skin {skin_id}").format(skin_id=skin_id))
            return None

    def set_skin(self, skin_id):
        """Switch between the modern interface and classic skins."""
        if skin_id is not None:
            skin = self._load_skin(skin_id)
            if skin is None:
                return
            self.skin = skin
        self.config['skin'] = skin_id
        if skin_id is None:
            if self._classic is not None:
                self._classic.destroy()
                self._classic = None
            self.show()
            self.panels.show_windows()
        else:
            if self._classic is None:
                self._classic = ClassicMode(self)
                self.panels.hide_windows()
                self.hide()
                self._classic.show(self.config.get('classic_windows') or {})
                self._classic.main.present()
            else:
                for window in self._classic.windows():
                    window.apply_shape()
                self._classic.refresh_all()
        self._update_analyzer_visibility()
        self.schedule_save_config()

    def _uris_to_skin_paths(self, uris):
        paths = []
        for uri in uris:
            try:
                path = GLib.filename_from_uri(uri.strip())[0]
            except Exception:
                continue
            if path.lower().endswith(SKIN_EXTENSIONS) and os.path.isfile(path):
                paths.append(path)
        return paths

    def install_skin(self, path):
        """Copy a .wsz into the skins folder and switch to it."""
        name = os.path.basename(path)
        os.makedirs(self.skins_dir(), exist_ok=True)
        shutil.copyfile(path, os.path.join(self.skins_dir(), name))
        self.set_skin(name)

    def choose_skin_file(self, *_args):
        dialog = Gtk.FileChooserDialog(title=_("Install Winamp Skin"), transient_for=self._dialog_parent(),
                                       action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, _("_Install"), Gtk.ResponseType.OK)
        skins = Gtk.FileFilter()
        skins.set_name(_("Winamp 2 skins (*.wsz, *.zip)"))
        for extension in SKIN_EXTENSIONS:
            skins.add_pattern('*' + extension)
            skins.add_pattern('*' + extension.upper())
        dialog.add_filter(skins)
        if dialog.run() == Gtk.ResponseType.OK:
            self.install_skin(dialog.get_filename())
        dialog.destroy()

    def _dialog_parent(self):
        return self._classic.main if self._classic is not None else self

    def show_classic_menu(self, event):
        menu = self.build_settings_menu()
        self._settings_menu = menu
        if event is not None:
            menu.popup_at_pointer(event)
        else:
            menu.popup_at_widget(self._classic.main.area, Gdk.Gravity.NORTH_WEST, Gdk.Gravity.NORTH_WEST, None)

    def _skin_menu(self, menu):
        item, sub = Gtk.MenuItem(label=_('Skin')), Gtk.Menu()
        current = self.config.get('skin')
        choices = [(None, _('Modern (Llama Amp)')), ('builtin', _('Classic: Llama'))]
        choices += [(name, _('Classic: {skin}').format(skin=os.path.splitext(name)[0]))
                    for name in self.installed_skins()]
        for skin_id, caption in choices:
            choice = Gtk.CheckMenuItem(label=caption)
            choice.set_draw_as_radio(True)
            choice.set_active(current == skin_id)
            choice.connect('activate', lambda _w, s=skin_id: self.set_skin(s) if self.config.get('skin') != s else None)
            sub.append(choice)
        sub.append(Gtk.SeparatorMenuItem())
        install = Gtk.MenuItem(label=_('Install skin…'))
        install.connect('activate', self.choose_skin_file)
        sub.append(install)
        gallery = Gtk.MenuItem(label=_('Find skins (Winamp Skin Museum)…'))
        gallery.connect('activate', lambda _w: Gtk.show_uri_on_window(
            self._dialog_parent(), 'https://skins.webamp.org/', Gdk.CURRENT_TIME))
        sub.append(gallery)
        item.set_submenu(sub)
        menu.append(item)
