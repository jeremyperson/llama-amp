"""GitHub release checks and the in-app update flow."""
import json
import os
import re
import threading
import urllib.request

from gi.repository import GLib, Gdk, Gio, Gtk

from .constants import APP_DIR, APP_NAME, APP_VERSION, RELEASES_URL, UPDATE_API_URL
from .i18n import _


class UpdatesMixin:
    def _startup_update_check(self):
        self._check_updates(manual=False)
        return False  # one-shot timer

    def _periodic_update_check(self):
        if self.config.get("update_check", True) is not False:
            self._check_updates(manual=False)
        return True  # keep the daily timer alive

    def _check_updates(self, manual=False):
        """Ask GitHub for the latest release (worker thread; UI via idle_add)."""
        def worker():
            info, err = None, None
            try:
                req = urllib.request.Request(
                    UPDATE_API_URL,
                    headers={'User-Agent': f'llama-amp/{APP_VERSION}',
                             'Accept': 'application/vnd.github+json'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    rel = json.load(resp)
                deb = next((a.get('browser_download_url')
                            for a in rel.get('assets', [])
                            if (a.get('name') or '').endswith('.deb')), None)
                info = {'version': (rel.get('tag_name') or '').lstrip('v'),
                        'url': rel.get('html_url') or RELEASES_URL,
                        'deb_url': deb}
            except Exception as e:
                err = str(e)
            self.tasks.idle_add(self._check_updates_done, info, err, manual)
        threading.Thread(target=worker, daemon=True, name='update-check').start()

    @staticmethod
    def _version_tuple(v):
        return tuple(int(x) for x in re.findall(r'\d+', v or ''))

    def _check_updates_done(self, info, err, manual):
        """UI-thread result: remember a newer release and surface it."""
        newer = (info and info['version'] and
                 self._version_tuple(info['version']) > self._version_tuple(APP_VERSION))
        if newer:
            # The daily re-check shouldn't re-announce a version it already
            # surfaced — notify only the first time each version is seen.
            already_known = (self._update_info or {}).get('version') == info['version']
            self._update_info = info
            if manual:
                self._offer_update_dialog(info)
            elif not already_known:
                self.show_drop_feedback(_('v{version} available — see ⚙ menu').format(version=info['version']))
                self._notify_track(_('{app} {version} is available').format(app=APP_NAME, version=info['version']),
                                   _("Update from the ⚙ menu"))
        elif manual:
            dialog = Gtk.MessageDialog(
                transient_for=self, modal=True, message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text=(_("Could not check for updates") if err
                      else _('{app} {version} is up to date').format(app=APP_NAME, version=APP_VERSION)))
            if err:
                dialog.format_secondary_text(err)
            dialog.run()
            dialog.destroy()
        if err:
            self.log_debug(f"update check failed: {err}")
        return False

    def _offer_update_dialog(self, info):
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=_('{app} {version} is available (you have {current})').format(
                app=APP_NAME, version=info['version'], current=APP_VERSION))
        dialog.format_secondary_text(_("Download and install it now?"))
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            self._start_update()

    def _start_update(self, *_args):
        """GUI update path. Installed (.deb) copies download the new package and
        hand it to the system installer (which prompts for the admin password);
        portable checkouts just get the release page."""
        info = self._update_info
        if not info:
            return
        installed = APP_DIR.startswith('/usr/')
        if not installed or not info.get('deb_url'):
            try:
                Gtk.show_uri_on_window(self, info['url'], Gdk.CURRENT_TIME)
            except Exception as e:
                self.log_debug(f"open releases page failed: {e}")
            return
        dest_dir = (GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
                    or GLib.get_home_dir())
        dest = os.path.join(dest_dir, os.path.basename(info['deb_url']))
        self.show_drop_feedback(_('Downloading v{version}…').format(version=info['version']))

        def worker():
            err = None
            try:
                req = urllib.request.Request(
                    info['deb_url'],
                    headers={'User-Agent': f'llama-amp/{APP_VERSION}'})
                with urllib.request.urlopen(req, timeout=60) as r, \
                        open(dest + '.part', 'wb') as f:
                    while True:
                        chunk = r.read(1 << 16)
                        if not chunk:
                            break
                        f.write(chunk)
                os.replace(dest + '.part', dest)
            except Exception as e:
                err = str(e)
            self.tasks.idle_add(self._update_downloaded, dest, err)
        threading.Thread(target=worker, daemon=True, name='update-download').start()

    def _update_downloaded(self, dest, err):
        if err:
            self.log_debug(f"update download failed: {err}")
            self.show_drop_feedback(_("Download failed — opening releases page"))
            try:
                Gtk.show_uri_on_window(self, self._update_info['url'],
                                       Gdk.CURRENT_TIME)
            except Exception:
                pass
            return False
        self.show_drop_feedback(_("Opening installer…"))
        try:
            # Hands the .deb to the software installer; it prompts for the
            # admin password and replaces the running version in place.
            Gio.AppInfo.launch_default_for_uri(f'file://{dest}', None)
        except Exception as e:
            self.log_debug(f"launch installer failed: {e}")
            self.show_drop_feedback(_('Saved to {dest}').format(dest=dest))
        return False

    def _toggle_update_check(self, *_args):
        enabled = self.config.get('update_check', True) is False
        self.config['update_check'] = enabled
        self.schedule_save_config()
        self.show_drop_feedback(
            _("Startup update check on") if enabled else _("Startup update check off"))

