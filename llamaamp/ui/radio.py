"""The Internet Radio window (Alt+R): browse radio-browser.info's directory,
keep favorites, and play or enqueue stations."""
import threading

from gi.repository import Gdk, GLib, Gtk, Pango

from ..constants import APP_NAME, N_
from ..i18n import _, ngettext
from ..radio import RadioBrowser, Station

SEARCH_DELAY_MS = 500
STATION_NAMES_LIMIT = 500
VIEWS = (('top', N_('Top voted')), ('favorites', N_('Favorites')))


class RadioMixin:
    _radio_server = None        # None: pick a directory mirror (tests point this at a local server)

    def show_radio(self, *_args):
        if self._radio_window is None:
            self._radio_window = RadioWindow(self)
            self._radio_window.connect('destroy', self._radio_closed)
        self._radio_window.present()

    def _radio_closed(self, _window):
        self._radio_window = None

    def _radio_client(self):
        if getattr(self, '_radio_browser', None) is None:
            self._radio_browser = RadioBrowser(self._radio_server)
        return self._radio_browser

    def radio_request(self, call, done):
        """Run call(client) on a thread; done(result, error) on the main loop."""
        client = self._radio_client()

        def work():
            try:
                result, error = call(client), None
            except Exception as exc:
                result, error = None, exc
            self.tasks.idle_add(lambda: (done(result, error), False)[1])
        threading.Thread(target=work, name='llama-radio', daemon=True).start()

    # -- favorites and names ------------------------------------------------------
    def radio_favorites(self):
        stations = (Station.from_dict(item) for item in self.config.get('radio_favorites', []))
        return [station for station in stations if station is not None]

    def radio_is_favorite(self, station):
        return any(favorite.url == station.url for favorite in self.radio_favorites())

    def radio_toggle_favorite(self, station):
        favorites = [favorite for favorite in self.radio_favorites() if favorite.url != station.url]
        added = len(favorites) == len(self.radio_favorites())
        if added:
            favorites.append(station)
        self.config['radio_favorites'] = [favorite.to_dict() for favorite in favorites]
        self.schedule_save_config()
        return added

    def _remember_station_name(self, station):
        names = dict(self.config.get('radio_names', {}))
        names.pop(station.url, None)
        names[station.url] = station.name           # newest last; the oldest go first
        while len(names) > STATION_NAMES_LIMIT:
            names.pop(next(iter(names)))
        self.config['radio_names'] = names

    def station_name(self, url):
        return self.config.get('radio_names', {}).get(url)

    # -- playing -----------------------------------------------------------------
    def radio_add(self, stations, play=False):
        if not stations:
            return
        for station in stations:
            self._remember_station_name(station)
        self.schedule_save_config()
        first = len(self.playlist)
        added = self._add_paths([station.url for station in stations], feedback=ngettext(
            'Added {count} station', 'Added {count} stations', len(stations)).format(count=len(stations)))
        if play and added:
            self._play_index(first)
            self.radio_request(lambda client: client.count_click(stations[0].uuid), lambda *_args: None)


class RadioWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title=_('Internet Radio — {app}').format(app=APP_NAME))
        self.app = app
        self.view = 'top'
        self.generation = 0
        self._search_id = None
        self.set_default_size(820, 540)
        self.set_icon_name('llama-amp')
        self.get_style_context().add_class('library-window')
        self.connect('key-press-event', self._key)
        self.connect('destroy', self._destroyed)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        root.get_style_context().add_class('music-player-main')
        root.set_border_width(8)
        self.add(root)

        top = Gtk.Box(spacing=6)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text(_('Search stations by name or genre…'))
        self.search.get_style_context().add_class('playlist-search')
        self.search.connect('search-changed', self._search_changed)
        self.search.connect('activate', lambda _w: self._search_now())
        top.pack_start(self.search, True, True, 0)
        self.status = Gtk.Label(xalign=1)
        self.status.get_style_context().add_class('muted')
        top.pack_start(self.status, False, False, 6)
        root.pack_start(top, False, False, 0)

        paned = Gtk.Paned()
        self.views = Gtk.ListBox()
        self.views.get_style_context().add_class('playlist')
        for key, caption in VIEWS:
            row = Gtk.ListBoxRow()
            row.view = key
            label = Gtk.Label(label=_(caption), xalign=0)
            label.set_margin_start(8); label.set_margin_end(8)
            label.set_margin_top(5); label.set_margin_bottom(5)
            row.add(label)
            self.views.add(row)
        scroller = Gtk.ScrolledWindow()
        scroller.add(self.views)
        scroller.set_size_request(150, -1)
        paned.pack1(scroller, False, False)

        # favorite mark, name, tags, country, quality, votes, station
        self.stations = Gtk.ListStore(str, str, str, str, str, int, object)
        self.station_view = Gtk.TreeView(model=self.stations)
        self.station_view.get_style_context().add_class('playlist')
        self.station_view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        for index, (caption, expand) in enumerate((('★', False), (_('Station'), True), (_('Genre'), True),
                                                   (_('Country'), False), (_('Quality'), False),
                                                   (_('Votes'), False))):
            renderer = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END if expand else Pango.EllipsizeMode.NONE)
            if index == 5:
                renderer.set_property('xalign', 1.0)
            column = Gtk.TreeViewColumn(caption, renderer, text=index)
            column.set_expand(expand)
            column.set_resizable(expand)
            if index:
                column.set_sort_column_id(index)
            self.station_view.append_column(column)
        self.station_view.connect('row-activated', lambda *_args: self.play())
        self.station_view.get_selection().connect('changed', lambda _s: self._update_actions())
        scroller = Gtk.ScrolledWindow()
        scroller.add(self.station_view)
        paned.pack2(scroller, True, False)
        root.pack_start(paned, True, True, 0)

        actions = Gtk.Box(spacing=6)
        for label, callback in ((_('Play'), self.play), (_('Enqueue'), self.enqueue)):
            button = Gtk.Button(label=label)
            button.connect('clicked', lambda _w, c=callback: c())
            actions.pack_start(button, False, False, 0)
        self.favorite_button = Gtk.Button(label=_('☆ Add to Favorites'))
        self.favorite_button.connect('clicked', lambda _w: self.toggle_favorite())
        actions.pack_start(self.favorite_button, False, False, 0)
        credit = Gtk.Label(xalign=1)
        credit.set_markup(_('Directory by <a href="https://www.radio-browser.info/">radio-browser.info</a>'))
        credit.get_style_context().add_class('muted')
        actions.pack_end(credit, False, False, 0)
        root.pack_start(actions, False, False, 0)

        self.show_all()
        self.views.connect('row-selected', self._view_selected)
        self.views.select_row(self.views.get_row_at_index(1 if app.radio_favorites() else 0))
        self.station_view.grab_focus()

    # -- loading ---------------------------------------------------------------
    def _view_selected(self, _box, row):
        if row is None:
            return
        self.view = row.view
        if self.search.get_text().strip():
            self.search.set_text('')        # its search-changed reloads nothing: we load below
            self._cancel_search()
        self.load()

    def load(self):
        """Fill the list for the current view or search."""
        self.generation += 1
        text = self.search.get_text().strip()
        if not text and self.view == 'favorites':
            favorites = self.app.radio_favorites()
            self._show(favorites, _('No favorites yet — select a station and press ☆.') if not favorites else '')
            return
        generation = self.generation
        self.status.set_text(_('Searching…') if text else _('Loading…'))
        call = (lambda client: client.search(text)) if text else (lambda client: client.top())
        self.app.radio_request(call, lambda stations, error: self._loaded(generation, stations, error))

    def _loaded(self, generation, stations, error):
        if generation != self.generation or self.app._radio_window is not self:
            return          # superseded or closed
        if error is not None:
            self.app.log_debug(f'radio directory: {type(error).__name__}: {error}')
            self._show([], _("Couldn't reach the radio directory. Check your connection and try again."))
            return
        self._show(stations, '' if stations else _('No stations found.'))

    def _show(self, stations, message):
        self.station_view.set_model(None)
        self.stations.clear()
        favorites = {station.url for station in self.app.radio_favorites()}
        for station in stations:
            self.stations.append(['★' if station.url in favorites else '', station.name, station.tags,
                                  station.country, station.quality(), station.votes, station])
        self.station_view.set_model(self.stations)
        self.status.set_text(message or ngettext('{count} station', '{count} stations',
                                                 len(stations)).format(count=len(stations)))
        self._update_actions()

    def _search_changed(self, _entry):
        self._cancel_search()
        self._search_id = GLib.timeout_add(SEARCH_DELAY_MS, self._search_now)

    def _cancel_search(self):
        if self._search_id is not None:
            GLib.source_remove(self._search_id)
            self._search_id = None

    def _search_now(self):
        self._cancel_search()
        self.load()
        return False

    def _destroyed(self, _window):
        self._cancel_search()
        self.generation += 1

    # -- actions ---------------------------------------------------------------
    def chosen(self):
        model, paths = self.station_view.get_selection().get_selected_rows()
        return [model[path][6] for path in paths]

    def play(self):
        self.app.radio_add(self.chosen()[:1], play=True)

    def enqueue(self):
        self.app.radio_add(self.chosen())

    def toggle_favorite(self):
        chosen = self.chosen()
        if not chosen:
            return
        added = self.app.radio_toggle_favorite(chosen[0])
        if self.view == 'favorites' and not self.search.get_text().strip():
            self.load()
        else:
            model, paths = self.station_view.get_selection().get_selected_rows()
            model[paths[0]][0] = '★' if added else ''
        self._update_actions()

    def _update_actions(self):
        chosen = self.chosen()
        self.favorite_button.set_sensitive(bool(chosen))
        favorite = bool(chosen) and self.app.radio_is_favorite(chosen[0])
        self.favorite_button.set_label(_('★ Remove from Favorites') if favorite else _('☆ Add to Favorites'))

    def _key(self, _window, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False
