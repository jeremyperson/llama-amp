"""Background tag/duration probing, playlist titles and album art."""
import os
import queue
import threading

from gi.repository import GdkPixbuf, Gst, GstPbutils

from .constants import (
    ALBUM_ART_SIZE,
    APP_DIR,
    FOLDER_ART_CACHE_LIMIT,
    FOLDER_ART_EXTS,
    FOLDER_ART_NAMES,
    META_CACHE_LIMIT,
)
from .i18n import _
from .paths import real_path


class MetadataWorker:
    """One worker and one queue; no GTK state is accessed by dispatch itself."""
    def __init__(self, handlers, on_error):
        self.queue = queue.Queue()
        self.handlers = handlers
        self.on_error = on_error
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._run, name='llama-metadata', daemon=True)
        self.thread.start()

    def _run(self):
        while not self.closed.is_set():
            job = self.queue.get()
            if job is None or self.closed.is_set():
                break
            try:
                self.handlers[job[0]](*job[1:])
            except Exception as exc:
                self.on_error(f'Metadata worker: {type(exc).__name__}')

    def close(self):
        self.closed.set()
        self.queue.put(None)



class MetadataMixin:
    def _queue_playlist_metadata(self):
        """Probe titles even when an earlier session already cached durations."""
        for path in self.playlist:
            if (path not in self._playlist_titles and path not in self._probe_inflight
                    and not self._is_stream_url(path) and self._playable(path)):
                self._probe_inflight.add(path)
                self._probe_queue.put(('meta', path, {'sample_rate': 0, 'bitrate': 0, 'channels': 0}))

    @staticmethod
    def _cache_get(cache, key):
        """LRU read: refresh recency on hit, None on miss."""
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        return None

    @staticmethod
    def _cache_put(cache, key, val, limit=META_CACHE_LIMIT):
        """LRU write: insert and evict oldest entries beyond limit."""
        cache[key] = val
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)

    def get_audio_properties(self, file_path):
        self.audio_properties = {'sample_rate': 0, 'bitrate': 0, 'channels': 0}
        if self._is_stream_url(file_path):
            return
        cached = self._cache_get(self._meta_cache, file_path)
        if isinstance(cached, dict):
            self.audio_properties.update(cached)
            return
        if cached is False or file_path in self._probe_inflight:
            return
        self._probe_inflight.add(file_path)
        self._probe_queue.put(('meta', file_path, dict(self.audio_properties)))

    def _probe_one(self, file_path, base_props):
        """Probe one file with GstDiscoverer (worker thread; no GTK calls)."""
        props = {}
        art_bytes = None
        try:
            disc = GstPbutils.Discoverer.new(3 * Gst.SECOND)
            info = disc.discover_uri(Gst.filename_to_uri(os.path.abspath(file_path)))
            streams = info.get_audio_streams()
            if streams:
                a = streams[0]
                props['sample_rate'] = a.get_sample_rate() or 0
                props['channels'] = a.get_channels() or 0
                br = a.get_bitrate() or 0
                if br > 0:
                    props['bitrate'] = br // 1000
            dur = info.get_duration()
            if dur and dur > 0:
                props['duration'] = dur // Gst.SECOND
            tags = info.get_tags()
            if tags:
                ok, title = tags.get_string(Gst.TAG_TITLE)
                ok2, artist = tags.get_string(Gst.TAG_ARTIST)
                if ok and title:
                    props['title'] = title
                if ok2 and artist:
                    props['artist'] = artist
                ok, album = tags.get_string(Gst.TAG_ALBUM)
                if ok and album:
                    props['album'] = album
                for tag, name in ((Gst.TAG_TRACK_NUMBER, 'track'), (Gst.TAG_ALBUM_VOLUME_NUMBER, 'disc')):
                    ok, number = tags.get_uint(tag)
                    if ok and number:
                        props[name] = number
                ok, sample = tags.get_sample(Gst.TAG_IMAGE)
                if not ok:
                    ok, sample = tags.get_sample(Gst.TAG_PREVIEW_IMAGE)
                if ok and sample:
                    art_bytes = self._sample_to_bytes(sample)
        except Exception as e:
            self.log_debug(f"discover failed for {os.path.basename(file_path)}: {e}")
        result = {**base_props, **props} if props else False
        self.tasks.idle_add(self._probe_done, file_path, result, art_bytes)

    def _update_playlist_title(self, file_path, title):
        references = self._title_rows.get(file_path)
        if not references:
            return
        self._playlist_titles[file_path] = title.strip() if isinstance(title, str) and title.strip() else None
        display = self._display_name(file_path)
        if not self._playable(file_path):
            display = _("{name} [MISSING]").format(name=display)
        changed = False
        for reference in references:
            path = reference.get_path()
            if path is not None and real_path(self.playlist_store[path][0]) == file_path:
                if self.playlist_store[path][1] != display:
                    self.playlist_store[path][1] = display
                    changed = True
        if changed and self.search_entry.get_text():
            self._search_step(False, select=False)

    def _probe_done(self, file_path, result, art_bytes):
        """Store a probe result on the UI thread; apply if still relevant.
        result is a props dict, or False to negative-cache a failed probe."""
        if self._destroyed:
            return False
        self._probe_inflight.discard(file_path)
        self._cache_put(self._meta_cache, file_path, result)
        title = result.get('title') if isinstance(result, dict) else None
        if isinstance(result, dict) and file_path in self._title_rows:
            self._playlist_tags[file_path] = {key: result[key] for key in ('artist', 'album', 'disc', 'track')
                                              if key in result}
        self._update_playlist_title(file_path, title)
        if isinstance(result, dict) and result.get('duration'):
            self._note_duration(file_path, result['duration'])
        if art_bytes and self._cache_get(self._art_cache, file_path) is None:
            self._apply_art_bytes(file_path, art_bytes)
        if not isinstance(result, dict):
            return False
        if file_path != self.current_song:
            return False
        for k in ('sample_rate', 'bitrate', 'channels'):
            if k in result:
                self.audio_properties[k] = result[k]
        if result.get('title') and file_path == self.current_song:
            self._refresh_song_label()
            self._mpris_notify_track()
        self.update_audio_display()
        return False

    def _sample_to_bytes(self, sample):
        """Extract raw image bytes from a Gst.Sample (embedded cover art tag)."""
        try:
            buf = sample.get_buffer()
            ok, mapinfo = buf.map(Gst.MapFlags.READ)
            if not ok:
                return None
            try:
                return bytes(mapinfo.data)
            finally:
                buf.unmap(mapinfo)
        except Exception as e:
            self.log_debug(f"art sample read failed: {e}")
            return None

    def _decode_art_pixbuf(self, data):
        """Decode image bytes into a pixbuf that fits double-size art."""
        try:
            loader = GdkPixbuf.PixbufLoader()
            try:
                loader.write(data)
            finally:
                loader.close()
            pixbuf = loader.get_pixbuf()
            if not pixbuf:
                return None
            w, h = pixbuf.get_width(), pixbuf.get_height()
            scale = ALBUM_ART_SIZE * 2 / max(w, h)
            if scale < 1:
                pixbuf = pixbuf.scale_simple(max(1, round(w * scale)),
                                             max(1, round(h * scale)),
                                             GdkPixbuf.InterpType.BILINEAR)
            return pixbuf
        except Exception as e:
            self.log_debug(f"art decode failed: {e}")
            return None

    def _apply_art_bytes(self, file_path, data):
        """Decode embedded art on the UI thread, cache it, show if still current."""
        pixbuf = self._decode_art_pixbuf(data)
        if pixbuf:
            self._cache_put(self._art_cache, file_path, pixbuf)
            if file_path == self.current_song:
                self._set_album_art(pixbuf)
        return False

    def _folder_art_scan(self, directory):
        """Scan a directory for cover art (worker thread; pure I/O + decode)."""
        pixbuf = None
        try:
            for entry in sorted(os.listdir(directory)):
                stem, ext = os.path.splitext(entry)
                if stem.lower() in FOLDER_ART_NAMES and ext.lower() in FOLDER_ART_EXTS:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        os.path.join(directory, entry),
                        ALBUM_ART_SIZE * 2, ALBUM_ART_SIZE * 2, True)
                    break
        except Exception as e:
            # Transient failure (unmounted volume, permissions): don't cache,
            # so a later attempt can succeed.
            self.log_debug(f"folder art scan failed: {e}")
            return
        self.tasks.idle_add(self._folder_art_done, directory, pixbuf)

    def _folder_art_done(self, directory, pixbuf):
        if self._destroyed:
            return False
        """Cache a completed folder scan; show it if still relevant (UI thread)."""
        self._cache_put(self._folder_art_cache, directory, pixbuf,
                        FOLDER_ART_CACHE_LIMIT)
        if (pixbuf is not None and self.current_song
                and os.path.dirname(os.path.abspath(self.current_song)) == directory
                and self._cache_get(self._art_cache, self.current_song) is None):
            self._set_album_art(pixbuf)
        return False

    def _update_album_art(self, file_path):
        """Show the best art known right now: embedded (cached) else cached folder
        art. On a folder-cache miss, show nothing and queue an off-thread scan —
        never block the UI on directory I/O (network mounts)."""
        if not file_path:
            self._set_album_art(None)
            return
        if self._is_stream_url(file_path):
            self._set_album_art(None)   # placeholder
            return
        pixbuf = self._cache_get(self._art_cache, file_path)
        if pixbuf is None:
            directory = os.path.dirname(os.path.abspath(file_path))
            if directory in self._folder_art_cache:
                pixbuf = self._cache_get(self._folder_art_cache, directory)
            else:
                self._probe_queue.put(('folderart', directory))
        self._set_album_art(pixbuf)

    def _get_default_art(self):
        """Placeholder art (the llama app icon, desaturated) for tracks with
        no embedded or folder art. Loaded once; None if the icon is missing."""
        if self._default_art_loaded:
            return self._default_art
        self._default_art_loaded = True
        candidates = (
            os.path.join(APP_DIR, "llama-amp.svg"),
            "/usr/share/icons/hicolor/scalable/apps/llama-amp.svg",  # installed
        )
        for p in candidates:
            if not os.path.exists(p):
                continue
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    p, ALBUM_ART_SIZE * 2, ALBUM_ART_SIZE * 2, True)
                dim = pb.copy()
                pb.saturate_and_pixelate(dim, 0.25, False)  # muted = "placeholder"
                self._default_art = dim
                break
            except Exception as e:
                self.log_debug(f"default art load failed: {e}")
        return self._default_art

    def _set_album_art(self, pixbuf):
        if not self.config.get('show_art', True):
            self.album_art.hide()
            return
        if pixbuf is None:
            pixbuf = self._get_default_art()
        if pixbuf:
            # Art is cached at double-size resolution; fit it to the current size
            fit = ALBUM_ART_SIZE * self.ui_scale / max(pixbuf.get_width(), pixbuf.get_height())
            if fit < 1:
                pixbuf = pixbuf.scale_simple(max(1, round(pixbuf.get_width() * fit)),
                                             max(1, round(pixbuf.get_height() * fit)),
                                             GdkPixbuf.InterpType.BILINEAR)
            self.album_art.set_from_pixbuf(pixbuf)
            self.album_art.show()
        else:
            self.album_art.clear()
            self.album_art.hide()

