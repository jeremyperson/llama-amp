"""Background indexing of the watched folders. Incremental: a file whose
modification time and size are unchanged is not read again."""
import os
import re
import threading

from gi.repository import Gst, GstPbutils

from ..constants import AUDIO_EXTENSIONS
from .db import under

BATCH = 100

try:
    import mutagen
except ImportError:          # optional; GStreamer's discoverer is the fallback
    mutagen = None


def _number(value):
    match = re.match(r'\s*(\d+)', str(value)) if value is not None else None
    return int(match.group(1)) if match else None


def _year(value):
    match = re.search(r'(\d{4})', str(value)) if value is not None else None
    return int(match.group(1)) if match else None


def _clean(value):
    text = str(value).strip() if value is not None else ''
    return text or None


def probe_tags(path):
    """Tags for the index, via mutagen when installed (fast), else GStreamer."""
    if mutagen is not None:
        try:
            audio = mutagen.File(path, easy=True)
        except Exception:
            audio = None
        if audio is not None:
            tags = audio.tags or {}

            def first(key):
                values = tags.get(key) if hasattr(tags, 'get') else None
                return values[0] if values else None
            length = getattr(audio.info, 'length', None)
            return {'title': _clean(first('title')), 'artist': _clean(first('artist')),
                    'album_artist': _clean(first('albumartist')), 'album': _clean(first('album')),
                    'disc': _number(first('discnumber')), 'track': _number(first('tracknumber')),
                    'year': _year(first('date')), 'genre': _clean(first('genre')),
                    'duration': round(length, 3) if length else None}
    return _probe_gstreamer(path)


def _probe_gstreamer(path):
    try:
        info = GstPbutils.Discoverer.new(5 * Gst.SECOND).discover_uri(Gst.filename_to_uri(path))
    except Exception:
        return {}
    result = {'duration': info.get_duration() / Gst.SECOND if info.get_duration() > 0 else None}
    tags = info.get_tags()
    if tags:
        for key, tag in (('title', Gst.TAG_TITLE), ('artist', Gst.TAG_ARTIST), ('album', Gst.TAG_ALBUM),
                         ('album_artist', Gst.TAG_ALBUM_ARTIST), ('genre', Gst.TAG_GENRE)):
            ok, value = tags.get_string(tag)
            result[key] = _clean(value) if ok else None
        for key, tag in (('track', Gst.TAG_TRACK_NUMBER), ('disc', Gst.TAG_ALBUM_VOLUME_NUMBER)):
            ok, value = tags.get_uint(tag)
            result[key] = value if ok and value else None
        ok, when = tags.get_date_time(Gst.TAG_DATE_TIME)
        result['year'] = when.get_year() if ok and when and when.has_year() else None
    return result


class Scanner(threading.Thread):
    """Walks the folders on a daemon thread. Callbacks run on that thread;
    the caller hands them to the main loop."""

    def __init__(self, db, folders, on_progress=None, on_finished=None, probe=probe_tags):
        super().__init__(name='llama-library-scan', daemon=True)
        self.db, self.folders = db, list(folders)
        self.on_progress, self.on_finished, self.probe = on_progress, on_finished, probe
        self.cancelled = False
        self.stats = {'scanned': 0, 'updated': 0, 'removed': 0}

    def cancel(self):
        self.cancelled = True

    def _files(self):
        for folder in self.folders:
            for root, dirs, names in os.walk(folder):
                dirs.sort()
                for name in sorted(names):
                    if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
                        yield os.path.join(root, name)

    def run(self):
        known = self.db.known()
        seen, batch = set(), []
        for path in self._files():
            if self.cancelled:
                return
            try:
                stat = os.stat(path)
            except OSError:
                continue
            seen.add(path)
            self.stats['scanned'] += 1
            if known.get(path) != (stat.st_mtime, stat.st_size):
                row = {'path': path, 'mtime': stat.st_mtime, 'size': stat.st_size}
                row.update(self.probe(path))
                batch.append(row)
            if len(batch) >= BATCH:
                self._flush(batch)
        self._flush(batch)
        # Files gone from the scanned folders leave the index
        gone = [path for path in known if path not in seen
                and any(under(path, folder) for folder in self.folders) and not os.path.exists(path)]
        if gone and not self.cancelled:
            self.db.remove(gone)
            self.stats['removed'] = len(gone)
        if self.on_finished and not self.cancelled:
            self.on_finished(dict(self.stats))

    def _flush(self, batch):
        if batch:
            self.db.upsert(batch)
            self.stats['updated'] += len(batch)
            batch.clear()
        if self.on_progress:
            self.on_progress(dict(self.stats))
