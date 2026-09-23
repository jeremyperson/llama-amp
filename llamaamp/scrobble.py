"""ListenBrainz scrobbling."""
import json
import threading
import time
import urllib.request

from gi.repository import Gst, Gtk

from .constants import LISTENBRAINZ_API


class ScrobbleMixin:
    def _scrobble_reset(self):
        """New listen for the current track (called from _post_load_ui)."""
        self._listen = {
            'path': self.current_song,
            'start_ts': int(time.time()),
            'accum': 0.0,
            'last_wall': time.monotonic(),
            'now_sent': False,
            'submitted': False,
        }

    def _scrobble_enabled(self):
        return (self.config.get('scrobble_enabled') is True
                and bool(self.config.get('listenbrainz_token')))

    def _scrobble_tick(self):
        """Accumulate listened time (seek-proof: wall-clock while playing) and
        submit per the standard 50%-or-4-minutes rule. Runs on the 100ms
        position heartbeat; cheap early-outs keep it free."""
        listen = getattr(self, '_listen', None)
        if listen is None or listen['submitted'] or not self._scrobble_enabled():
            return
        if listen['path'] != self.current_song or not self.current_song:
            return
        if self._is_stream_url(listen['path']):
            return  # radio is not a listen
        now = time.monotonic()
        if self.is_playing and not self.seeking:
            listen['accum'] += now - listen['last_wall']
        listen['last_wall'] = now

        cached = self._cache_get(self._meta_cache, listen['path'])
        artist = cached.get('artist') if isinstance(cached, dict) else None
        title = cached.get('title') if isinstance(cached, dict) else None
        if not artist or not title:
            return  # conservatively skip artist-less files (usually mistagged)

        if not listen['now_sent']:
            listen['now_sent'] = True
            self._scrobble_send('playing_now', artist, title, None)
        duration_s = self.duration // Gst.SECOND if self.duration > 0 else 0
        if duration_s > 0 and listen['accum'] >= min(240, duration_s / 2):
            listen['submitted'] = True
            self._scrobble_send('single', artist, title, listen['start_ts'])

    def _scrobble_send(self, listen_type, artist, title, listened_at):
        """POST to ListenBrainz on a daemon thread; failures are silent
        (log_debug only, no retry queue — a dropped listen is acceptable)."""
        token = self.config.get('listenbrainz_token')
        payload = {'listen_type': listen_type, 'payload': [{
            'track_metadata': {'artist_name': artist, 'track_name': title}}]}
        if listened_at is not None:
            payload['payload'][0]['listened_at'] = listened_at

        def worker():
            try:
                req = urllib.request.Request(
                    f"{LISTENBRAINZ_API}/1/submit-listens",
                    data=json.dumps(payload).encode(),
                    headers={'Authorization': f'Token {token}',
                             'Content-Type': 'application/json'})
                urllib.request.urlopen(req, timeout=10).read()
            except Exception as e:
                self.log_debug(f"scrobble {listen_type} failed: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def show_scrobble_dialog(self, *_args):
        dialog = Gtk.Dialog(title="Scrobbling", transient_for=self, modal=True)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OK, Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_margin_top(10); box.set_margin_bottom(10)
        box.set_margin_start(10); box.set_margin_end(10)
        box.set_spacing(6)
        box.add(Gtk.Label(label="ListenBrainz user token\n(listenbrainz.org → Settings)"))
        entry = Gtk.Entry()
        entry.set_visibility(False)
        entry.set_width_chars(40)
        entry.set_text(self.config.get('listenbrainz_token') or "")
        box.add(entry)
        check = Gtk.CheckButton(label="Enable scrobbling")
        check.set_active(self.config.get('scrobble_enabled') is True)
        box.add(check)
        dialog.show_all()
        response = dialog.run()
        token = entry.get_text().strip()
        enabled = check.get_active()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        self.config['listenbrainz_token'] = token or None
        self.config['scrobble_enabled'] = bool(enabled and token)
        self.schedule_save_config()
        self.show_drop_feedback(
            "Scrobbling enabled" if self.config['scrobble_enabled']
            else "Scrobbling off")

