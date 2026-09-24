"""ReplayGain for files that have no ReplayGain tags.

While ReplayGain is on, a background thread measures the loudness of the
playlist's untagged tracks with GStreamer's rganalysis and remembers the
track gain in the library database (keyed by path, size and mtime; files
are never modified). When such a track loads, the gain becomes rgvolume's
fallback-gain, which rgvolume uses exactly when a file carries no tags.
"""
import collections
import math
import os
import sqlite3
import threading

from gi.repository import Gst

try:
    import mutagen
except ImportError:
    mutagen = None

ANALYZE_TIMEOUT_S = 120
MAX_GAIN_DB = 60.0                   # rgvolume's fallback-gain range
APPLY_WITHIN_NS = 2 * Gst.SECOND    # a gain found this early still applies to the playing track
RG_TAG_KEYS = ('replaygain_track_gain', 'REPLAYGAIN_TRACK_GAIN',
               '----:com.apple.iTunes:replaygain_track_gain', 'TXXX:REPLAYGAIN_TRACK_GAIN',
               'TXXX:replaygain_track_gain')


def analyze(path, timeout_s=ANALYZE_TIMEOUT_S):
    """Track gain in dB for a file (capped so its peak never clips), or None."""
    pipeline = Gst.parse_launch('uridecodebin name=source ! audioconvert ! audioresample ! '
                                'rganalysis forced=true ! fakesink sync=false')
    pipeline.get_by_name('source').set_property('uri', Gst.filename_to_uri(path))
    gain = peak = None
    ended = False
    try:
        pipeline.set_state(Gst.State.PLAYING)
        bus = pipeline.get_bus()
        wanted = Gst.MessageType.EOS | Gst.MessageType.ERROR | Gst.MessageType.TAG
        while True:
            message = bus.timed_pop_filtered(timeout_s * Gst.SECOND, wanted)
            if message is None or message.type != Gst.MessageType.TAG:
                ended = message is not None and message.type == Gst.MessageType.EOS
                break
            tags = message.parse_tag()
            ok, value = tags.get_double(Gst.TAG_TRACK_GAIN)
            if ok:
                gain = value
            ok, value = tags.get_double(Gst.TAG_TRACK_PEAK)
            if ok:
                peak = value
    finally:
        pipeline.set_state(Gst.State.NULL)
    if not ended or gain is None or peak is None or peak <= 0:
        return None         # unreadable, or silence: nothing to normalize
    gain = min(gain, -20 * math.log10(peak), MAX_GAIN_DB)
    return round(max(gain, -MAX_GAIN_DB), 2)


def file_stamp(path):
    """(mtime, size): a stored gain is valid while these match."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return stat.st_mtime, stat.st_size


def has_rg_tags(path):
    """Whether the file carries its own track gain (rgvolume then ignores ours)."""
    if mutagen is None:
        return False
    try:
        audio = mutagen.File(path)
    except Exception:
        return False
    tags = getattr(audio, 'tags', None)
    if not tags:
        return False
    try:
        keys = {str(key) for key in tags.keys()}
    except Exception:
        return False
    lowered = {key.lower() for key in keys}
    return any(key.lower() in lowered for key in RG_TAG_KEYS)


class LoudnessWorker(threading.Thread):
    """Measures queued files one at a time; `on_result(path, gain)` runs on
    this thread after the gain is stored (gain None for tagged or unreadable
    files)."""

    def __init__(self, db, on_result=None, measure=analyze):
        super().__init__(name='llama-loudness', daemon=True)
        self.db, self.on_result, self.measure = db, on_result, measure
        self.pending = collections.deque()
        self.queued = set()
        self.resolved = set()       # checked this session; first=True checks again
        self.condition = threading.Condition()
        self.closed = False

    def add(self, paths, first=False):
        """Queue files not measured yet; first=True moves them to the front."""
        with self.condition:
            for path in (reversed(paths) if first else paths):
                if path in self.resolved and not first:
                    continue
                if path in self.queued:
                    if first and path in self.pending:
                        self.pending.remove(path)
                        self.pending.appendleft(path)
                    continue
                self.queued.add(path)
                if first:
                    self.pending.appendleft(path)
                else:
                    self.pending.append(path)
            self.condition.notify()

    def close(self):
        with self.condition:
            self.closed = True
            self.pending.clear()
            self.condition.notify()

    def idle(self):
        with self.condition:
            return not self.queued

    def run(self):
        while True:
            with self.condition:
                while not self.pending and not self.closed:
                    self.condition.wait()
                if self.closed:
                    return
                path = self.pending.popleft()
            try:
                self._measure(path)
            finally:
                with self.condition:
                    self.queued.discard(path)
                    self.resolved.add(path)

    def _measure(self, path):
        stamp = file_stamp(path)
        if stamp is None or self.db.loudness(path, stamp) is not None:
            return
        gain = None
        if not has_rg_tags(path):
            try:
                gain = self.measure(path)
            except Exception:
                gain = None
        try:
            if self.closed:
                return
            self.db.set_loudness(path, stamp, gain)
        except sqlite3.ProgrammingError:
            return      # the app closed the database while we measured
        if self.on_result:
            self.on_result(path, gain)


class LoudnessMixin:
    """Feeds the worker from the playlist and applies stored gains on load."""

    def _open_loudness(self):
        self._loudness = LoudnessWorker(
            self.library, on_result=lambda path, gain: self.tasks.idle_add(self._loudness_measured, path, gain))
        self._loudness.start()

    def _loudness_wanted(self):
        return getattr(self, '_loudness', None) is not None and self.replaygain != 'off' and self.config.get('replaygain_analyze', True) is not False

    def _loudness_queue_playlist(self):
        if not self._loudness_wanted():
            return
        paths = [path for path in dict.fromkeys(self.playlist)
                 if not self._is_stream_url(path) and self._playable(path)]
        if self.current_song in paths:          # the playing track first
            paths.remove(self.current_song)
            paths.insert(0, self.current_song)
        self._loudness.add(paths)

    def _apply_fallback_gain(self, path):
        """rgvolume's gain for untagged files: the measured one, else 0 dB."""
        if self.rgvolume is None or getattr(self, 'library', None) is None:
            return
        gain = None
        if path and not self._is_stream_url(path):
            stamp = file_stamp(path)
            known = self.library.loudness(path, stamp) if stamp else None
            gain = known[0] if known else None
            if known is None and self._loudness_wanted() and self._playable(path):
                self._loudness.add([path], first=True)
        self.rgvolume.set_property('fallback-gain', gain if gain is not None else 0.0)

    def _loudness_measured(self, path, gain):
        if self._destroyed or path != self.current_song or gain is None:
            return False
        if self._current_position_ns() <= APPLY_WITHIN_NS:
            self._apply_fallback_gain(path)
        return False

    def toggle_replaygain_analyze(self, *_args):
        self.config['replaygain_analyze'] = self.config.get('replaygain_analyze', True) is False
        self.schedule_save_config()
        self._loudness_queue_playlist()
