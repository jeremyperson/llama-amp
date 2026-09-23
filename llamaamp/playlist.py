"""Playlist entries, the edit/Undo protocol, M3U and named playlists."""
import os
import random
import re
import threading
import urllib.parse
import uuid
from collections import deque
from contextlib import contextmanager

from gi.repository import GLib, Gtk

from .constants import AUDIO_EXTENSIONS
from .paths import displayable, real_path, store_path
from .i18n import N_, _, default_song_text, ngettext

# Winamp's Sort menu plus artist/album/length; captions (translate with _()
# where shown) for the Playlist menu
SORT_KEYS = {'title': N_('By title'), 'artist': N_('By artist'), 'album': N_('By album'),
             'filename': N_('By filename'), 'path': N_('By path and filename'),
             'length': N_('By length'), 'reverse': N_('Reverse list'), 'randomize': N_('Randomize list')}


def missing_label(name):
    return _("❌ {name} [MISSING]").format(name=name)


def added_feedback(count):
    return ngettext("Added {count} file", "Added {count} files", count).format(count=count)


def _natural(text):
    """Case-insensitive key that orders "Track 2" before "Track 10"."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r'(\d+)', text)]


def sort_entries(entries, key, info, rng=random):
    """Reorder (entry_id, path) pairs. info(path) returns known tags (title,
    artist, album, disc, track, duration) or None. Sorts are stable, so
    duplicates keep their relative order; missing values sort last."""
    entries = list(entries)
    if key == 'reverse':
        return entries[::-1]
    if key == 'randomize':
        rng.shuffle(entries)
        return entries

    def title(path, tags):
        return _natural(tags.get('title') or os.path.splitext(os.path.basename(path))[0])

    def text(value):
        return (0, _natural(value)) if value else (1, [])

    def number(value):
        return (0, value) if isinstance(value, (int, float)) and value > 0 else (1, 0)

    def sort_key(entry):
        path = entry[1]
        tags = info(path) or {}
        if key == 'title':
            return title(path, tags)
        if key in ('artist', 'album'):
            order = [text(tags.get('album')), number(tags.get('disc')), number(tags.get('track'))]
            if key == 'artist':
                order.insert(0, text(tags.get('artist')))
            # Untagged entries go last as a group, in title order
            return (all(part[0] for part in order), order, title(path, tags))
        if key == 'filename':
            return _natural(os.path.basename(path))
        if key == 'path':
            return _natural(path)
        if key == 'length':
            return number(tags.get('duration'))
        raise ValueError(key)
    return sorted(entries, key=sort_key)


def move_block(entries, indices, offset):
    """Shift the entries at indices by offset, keeping their order; the others
    fill the remaining slots in order. The offset is clamped so the block stays
    in range. Returns (new order, new indices of the moved entries)."""
    indices = sorted(set(indices))
    if not indices:
        return list(entries), []
    offset = max(-indices[0], min(len(entries) - 1 - indices[-1], offset))
    targets = [index + offset for index in indices]
    moving = set(indices)
    rest = iter(entry for index, entry in enumerate(entries) if index not in moving)
    placed = dict(zip(targets, (entries[index] for index in indices)))
    return [placed[slot] if slot in placed else next(rest) for slot in range(len(entries))], targets


class PlaylistMixin:
    @contextmanager
    def _store_guard(self):
        """Suppress store-changed resyncs during programmatic edits, exception-safe."""
        self._suppress_store = True
        try:
            yield
        finally:
            self._suppress_store = False

    def _uris_to_audio_paths(self, uris):
        """Convert dropped URIs to playable entries: local audio files, or
        http(s) stream URLs passed through verbatim."""
        files = []
        for uri in uris:
            uri = uri.strip()
            if not uri:
                continue
            if self._is_stream_url(uri):
                files.append(uri)
                continue
            try:
                path = GLib.filename_from_uri(uri)[0]
            except Exception:
                continue
            if self.is_audio_file(path) and os.path.exists(path):
                files.append(path)
        return files

    def _add_paths(self, paths, feedback=None):
        """Append paths to playlist + store (missing entries marked), persist,
        and auto-load the first added track if nothing is loaded. Returns count."""
        if paths:
            self._remember_playlist()
        added = 0
        first_new = len(self.playlist)
        with self._store_guard():
            for p in paths:
                self.playlist.append(p)
                self.entry_ids.append(uuid.uuid4().hex)
                name = self._display_name(p)
                display = (name if self._is_stream_url(p) or os.path.exists(p)
                           else missing_label(name))
                self.playlist_store.append([store_path(p), display, len(self.playlist), self._duration_str(p),
                                            self.entry_ids[-1], ""])
                added += 1
        if not added:
            return 0
        self.update_playlist_info()
        self.save_playlist()
        self._queue_playlist_metadata()
        self._refresh_order()
        if self.current_song is None:
            self.load_song(first_new)
        if feedback:
            self.show_drop_feedback(feedback)
        return added

    @staticmethod
    def _is_stream_url(path):
        """True for internet-radio / remote stream playlist entries."""
        return path.startswith(('http://', 'https://'))

    def _display_name(self, path):
        """Human name for a playlist entry: hostname for streams, stem for files."""
        if self._is_stream_url(path):
            return urllib.parse.urlparse(path).hostname or path
        title = self._playlist_titles.get(path)
        if not title:
            cached = self._meta_cache.get(path)
            title = cached.get('title') if isinstance(cached, dict) else None
        return title if title else displayable(os.path.splitext(os.path.basename(path))[0])

    def _playable(self, path):
        """Can this playlist entry be played right now?"""
        return self._is_stream_url(path) or (
            self.is_audio_file(path) and os.path.exists(path))

    def is_audio_file(self, file_path):
        """Check if a file is an audio file based on its extension"""
        return os.path.splitext(file_path.lower())[1] in AUDIO_EXTENSIONS

    def _refresh_order(self):
        self._invalidate_next()
        self.order.reconcile(zip(self.entry_ids, self.playlist))
        self._title_rows = {}
        for row in self.playlist_store:
            reference = Gtk.TreeRowReference.new(self.playlist_store, row.path)
            self._title_rows.setdefault(real_path(row[0]), []).append(reference)
        self._playlist_tags = {path: tags for path, tags in self._playlist_tags.items()
                               if path in self._title_rows}
        self._playlist_titles = {path: title for path, title in self._playlist_titles.items()
                                 if path in self._title_rows}
        self._play_next = self.order.queue
        self._update_queue_markers()
        self._prepare_next()

    def _remember_playlist(self):
        if self._undoing:
            return
        self._invalidate_next()
        self._undo.append((list(self.playlist), list(self.entry_ids), list(self.order.queue),
                           self._playlist_name))

    def _playlist_edited(self, rebuild=True):
        current = self.order.current
        if rebuild:
            with self._store_guard():
                self.playlist_store.clear()
                for i, (key, path) in enumerate(zip(self.entry_ids, self.playlist)):
                    name = self._display_name(path)
                    if not self._playable(path):
                        name = _("{name} [MISSING]").format(name=name)
                    self.playlist_store.append([store_path(path), name, i + 1, self._duration_str(path), key, ''])
        if current in self.entry_ids:
            self.current_index = self.entry_ids.index(current)
        else:
            self.stop_song(None)
            self.current_song = None
            self.order.current = None
            self.current_index = 0
            self._set_title_text(default_song_text())
            self.info_label.set_text('')
            self._set_album_art(None)
        self._renumber_rows()
        self._refresh_order()
        self.update_playlist_info()
        self.save_playlist()
        self.schedule_save_config()
        self._queue_playlist_metadata()
        self._search_step(True)

    def undo_playlist(self, *_args):
        if not self._undo:
            self.show_drop_feedback(_('Nothing to undo'))
            return
        self._undoing = True
        try:
            self._invalidate_next()
            paths, keys, queued, name = self._undo.pop()
            self.playlist, self.entry_ids = paths, keys
            self.order.queue = deque(queued)
            self._playlist_name = name
            self._playlist_edited()
        finally:
            self._undoing = False
        self.show_drop_feedback(_('Playlist edit undone'))

    def update_missing_file_in_playlist(self, index):
        """Update the playlist display to show a file as missing"""
        if 0 <= index < len(self.playlist):
            file_path = self.playlist[index]
            song_name = self._display_name(file_path)
            missing_display = missing_label(song_name)
            
            # Update the playlist store display
            tree_iter = self.playlist_store.get_iter(Gtk.TreePath(index))
            self.playlist_store.set_value(tree_iter, 1, missing_display)

    # File management
    def _parse_m3u(self, m3u_path):
        """Read an M3U/M3U8 playlist: skip comments, resolve relative entries
        against the playlist's own directory."""
        base = os.path.dirname(os.path.abspath(m3u_path))
        entries = []
        try:
            with open(m3u_path, 'r', encoding='utf-8', errors='surrogateescape') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if self._is_stream_url(line):
                        entries.append(line)
                        continue
                    if line.startswith('file://'):
                        try:
                            line = GLib.filename_from_uri(line)[0]
                        except Exception:
                            continue
                    if not os.path.isabs(line):
                        line = os.path.normpath(os.path.join(base, line))
                    entries.append(line)
        except Exception as e:
            self.log_debug(f"m3u parse failed: {e}")
        return entries

    def add_files(self, button):
        dialog = Gtk.FileChooserDialog(
            title=_("Add Music Files"),
            parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        dialog.set_select_multiple(True)

        # Audio file filter
        filter_audio = Gtk.FileFilter()
        filter_audio.set_name(_("Audio files"))
        filter_audio.add_mime_type("audio/*")
        dialog.add_filter(filter_audio)

        # Playlist import filter
        filter_m3u = Gtk.FileFilter()
        filter_m3u.set_name(_("Playlists (*.m3u, *.m3u8)"))
        filter_m3u.add_pattern("*.m3u")
        filter_m3u.add_pattern("*.m3u8")
        dialog.add_filter(filter_m3u)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            paths = []
            for filename in dialog.get_filenames():
                if filename.lower().endswith(('.m3u', '.m3u8')):
                    paths.extend(self._parse_m3u(filename))
                else:
                    paths.append(filename)
            self._add_paths(paths)
        dialog.destroy()

    def add_folder(self, button):
        """Add every audio file under a chosen folder (recursive). The walk runs
        off-thread so a huge or slow tree can't freeze the UI."""
        dialog = Gtk.FileChooserDialog(
            title=_("Add Music Folder"),
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        response = dialog.run()
        folder = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        if not folder:
            return

        def walk():
            found = []
            for root, dirs, files in os.walk(folder):
                dirs.sort()
                for f in sorted(files):
                    p = os.path.join(root, f)
                    if self.is_audio_file(p):
                        found.append(p)
            self.tasks.idle_add(self._folder_walk_done, folder, found)

        threading.Thread(target=walk, daemon=True).start()

    def _folder_walk_done(self, folder, found):
        if found:
            self._add_paths(found, feedback=added_feedback(len(found)))
        else:
            self.show_drop_feedback(_('No audio files in {folder}').format(folder=os.path.basename(folder)))
        return False

    def remove_missing(self, button):
        keep = [(key, path) for key, path in zip(self.entry_ids, self.playlist) if self._playable(path)]
        removed = len(self.playlist) - len(keep)
        if not removed:
            self.show_drop_feedback(_('No missing files'))
            return
        self._remember_playlist()
        self.entry_ids = [key for key, _ in keep]
        self.playlist = [path for _, path in keep]
        self._playlist_edited()
        self.show_drop_feedback(ngettext('Removed {count} missing file — Ctrl+Z to undo',
                                          'Removed {count} missing files — Ctrl+Z to undo', removed).format(count=removed))

    def export_m3u(self, button):
        """Export the playlist as an extended M3U file."""
        if not self.playlist:
            return
        dialog = Gtk.FileChooserDialog(
            title=_("Export Playlist"),
            parent=self,
            action=Gtk.FileChooserAction.SAVE
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK
        )
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name("playlist.m3u")
        response = dialog.run()
        target = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        if not target:
            return
        try:
            self._write_m3u(target)
            self.show_drop_feedback(ngettext('Exported {count} track', 'Exported {count} tracks', len(self.playlist)).format(count=len(self.playlist)))
        except Exception as e:
            self.log_debug(f"m3u export failed: {e}")
            self.show_drop_feedback(_("Export failed"))

    def _write_m3u(self, target):
        lines = ["#EXTM3U"]
        for p in self.playlist:
            secs = self._duration_seconds(p) or -1
            lines.append(f"#EXTINF:{secs},{self._display_name(p)}")
            lines.append(p)
        self._atomic_write(target, ("\n".join(lines) + "\n").encode('utf-8', 'surrogateescape'), binary=True)

    def playlists_dir(self):
        d = os.path.join(self._data_dir(), "playlists")
        os.makedirs(d, exist_ok=True)
        return d

    def _saved_playlist_names(self):
        try:
            return sorted(os.path.splitext(f)[0] for f in os.listdir(self.playlists_dir())
                          if f.lower().endswith('.m3u'))
        except OSError:
            return []

    def _do_save_playlist_as(self, name):
        name = re.sub(r'[/\\\0]', '', name).strip()
        if not name or not self.playlist:
            return False
        self._write_m3u(os.path.join(self.playlists_dir(), f"{name}.m3u"))
        self._playlist_name = name
        self.schedule_save_config()
        self.show_drop_feedback(_("Saved playlist '{name}'").format(name=name))
        return True

    def _save_playlist_as(self, *_args):
        dialog = Gtk.Dialog(title=_("Save Playlist As"), transient_for=self, modal=True)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_text(self._playlist_name or "")
        entry.set_activates_default(True)
        dialog.set_default_response(Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_margin_top(10); box.set_margin_bottom(10)
        box.set_margin_start(10); box.set_margin_end(10)
        box.add(Gtk.Label(label=_("Playlist name:")))
        box.add(entry)
        dialog.show_all()
        response = dialog.run()
        name = entry.get_text()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            self._do_save_playlist_as(name)

    def _save_playlist_named(self, *_args):
        if self._playlist_name:
            self._do_save_playlist_as(self._playlist_name)

    def _load_named_playlist(self, name):
        entries = self._parse_m3u(os.path.join(self.playlists_dir(), f'{name}.m3u'))
        if not entries:
            self.show_drop_feedback(_("Playlist '{name}' is empty/unreadable").format(name=name))
            return
        self._remember_playlist()
        self.playlist = entries
        self.entry_ids = [uuid.uuid4().hex for _ in entries]
        self.order.queue.clear()
        self._playlist_name = name
        self._playlist_edited()

    def replace_playlist(self, paths, name=None):
        """Swap in a new playlist as one undoable edit (Ctrl+Z restores the old one)."""
        self._remember_playlist()
        self.playlist = list(paths)
        self.entry_ids = [uuid.uuid4().hex for _path in self.playlist]
        self.order.queue.clear()
        self._playlist_name = name
        self._playlist_edited()

    def remove_selected(self, button):
        model, paths = self.playlist_view.get_selection().get_selected_rows()
        if not paths:
            return
        self._remember_playlist()
        for index in sorted((p.get_indices()[0] for p in paths), reverse=True):
            del self.playlist[index]
            del self.entry_ids[index]
        self._playlist_edited()
        self.show_drop_feedback(_('Removed from playlist — Ctrl+Z to undo'))

    def clear_playlist(self, button):
        if not self.playlist:
            return
        self._remember_playlist()
        self.playlist.clear()
        self.entry_ids.clear()
        self.order.queue.clear()
        self._playlist_edited()
        self.show_drop_feedback(_('Playlist cleared — Ctrl+Z to undo'))

    def update_playlist_info(self):
        count = len(self.playlist)
        if hasattr(self, 'playlist_hint'):
            self.playlist_hint.set_visible(count == 0)
        base = ngettext("{count} track", "{count} tracks", count).format(count=count)
        known = [self._duration_seconds(p) for p in self.playlist]
        known = [s for s in known if s]
        if count > 0 and len(known) >= max(1, int(count * 0.9)):
            self.playlist_info.set_text(f"{base} · {self._fmt_total(sum(known))}")
        else:
            self.playlist_info.set_text(base)

    def on_store_rows_changed(self, *args):
        """Fires on user drag-reorder (and programmatic edits we suppress)."""
        if self._suppress_store or self._reordering:
            return
        self._reordering = True
        self.tasks.idle_add(self._resync_playlist_from_store)

    def _renumber_rows(self):
        """Rewrite the track-number column (col 2) to match current row order."""
        it = self.playlist_store.get_iter_first()
        n = 0
        while it is not None:
            n += 1
            self.playlist_store.set_value(it, 2, n)
            it = self.playlist_store.iter_next(it)

    def _resync_playlist_from_store(self):
        self._reordering = False
        if self._destroyed:
            return False
        self._remember_playlist()
        self.playlist = [real_path(row[0]) for row in self.playlist_store]
        self.entry_ids = [row[4] for row in self.playlist_store]
        self._playlist_edited(rebuild=False)
        return False

    def _queue_paths(self, entry_ids):
        for key in entry_ids:
            if key in self.entry_ids and key not in self.order.queue:
                self.order.queue.append(key)
        self._refresh_order()
        self.show_drop_feedback(ngettext('Queued {count} track', 'Queued {count} tracks', len(entry_ids)).format(count=len(entry_ids)))

    def _unqueue_paths(self, entry_ids):
        self.order.queue = deque(key for key in self.order.queue if key not in entry_ids)
        self._refresh_order()

    def save_playlist(self):
        """Save the current playlist to a file (atomically)"""
        try:
            # surrogateescape keeps paths that aren't valid UTF-8 byte-exact
            data = "".join(f"{p}\n" for p in self.playlist).encode('utf-8', 'surrogateescape')
            self._atomic_write(self.playlist_path(), data, binary=True)
        except Exception as e:
            print(f"Error saving playlist: {e}")

    def load_playlist(self):
        """Load the saved playlist. Every line is kept — unknown or missing
        entries are only *marked*, never silently dropped (a later save would
        otherwise erase them from disk). Rows are bulk-inserted with the model
        detached, and existence checks run afterwards in idle chunks so startup
        never blocks on a slow or unmounted media drive."""
        try:
            playlist_file = self.playlist_path()
            if not os.path.exists(playlist_file):
                return
            with open(playlist_file, 'r', encoding='utf-8', errors='surrogateescape') as f:
                lines = [line.strip() for line in f if line.strip()]
            if not lines:
                return
            with self._store_guard():
                self.playlist_view.set_model(None)  # bulk-insert speedup
                for i, file_path in enumerate(lines):
                    self.playlist.append(file_path)
                    self.entry_ids.append(uuid.uuid4().hex)
                    display_name = self._display_name(file_path)
                    self.playlist_store.append([store_path(file_path), display_name, i + 1,
                                                self._duration_str(file_path), self.entry_ids[-1], ""])
                self.playlist_view.set_model(self.playlist_store)

            self._refresh_order()
            self.update_playlist_info()
            self.tasks.idle_add(self._check_missing_chunk, 0)
            self._queue_playlist_metadata()

            # If we have a playlist, try to load the first available song
            if self.playlist and self.current_song is None:
                self.load_first_available_song()
        except Exception as e:
            print(f"Error loading playlist: {e}")

    def _check_missing_chunk(self, start, chunk=50):
        """Mark missing/unsupported rows in idle-time chunks after startup."""
        end = min(start + chunk, len(self.playlist))
        for i in range(start, end):
            p = self.playlist[i]
            if self._is_stream_url(p):
                continue
            try:
                it = self.playlist_store.get_iter(Gtk.TreePath(i))
            except Exception:
                return False  # store changed under us; a later edit re-marks
            name = self._display_name(p)
            if not os.path.exists(p):
                self.playlist_store.set_value(it, 1, missing_label(name))
            elif not self.is_audio_file(p):
                self.playlist_store.set_value(it, 1, _("⚠ {name} [UNSUPPORTED]").format(name=name))
        if end < len(self.playlist):
            self.tasks.idle_add(self._check_missing_chunk, end)
        return False

    def _sort_info(self, path):
        tags = dict(self._playlist_tags.get(path) or {})
        title = self._playlist_titles.get(path)
        if title:
            tags['title'] = title
        duration = self._duration_seconds(path)
        if duration:
            tags['duration'] = duration
        return tags

    def sort_playlist(self, key):
        """Sort, reverse or randomize the playlist as one undoable edit. The
        playing entry, the queue and duplicate identities are kept."""
        if len(self.playlist) < 2:
            return
        self._remember_playlist()
        ordered = sort_entries(zip(self.entry_ids, self.playlist), key, self._sort_info)
        self.entry_ids = [entry_id for entry_id, _ in ordered]
        self.playlist = [path for _, path in ordered]
        self._playlist_edited(rebuild=True)
        self.show_drop_feedback(_('{action} — Ctrl+Z to undo').format(action=_(SORT_KEYS[key])))

    def move_entries(self, indices, offset):
        """Move the given rows by offset as one undoable edit; returns their new
        indices."""
        order, targets = move_block(list(zip(self.entry_ids, self.playlist)), indices, offset)
        if targets == sorted(set(indices)):
            return targets
        self._remember_playlist()
        self.entry_ids = [entry_id for entry_id, _ in order]
        self.playlist = [path for _, path in order]
        self._playlist_edited(rebuild=True)
        return targets
