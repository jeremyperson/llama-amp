"""Lyrics from an .lrc file beside the track or from its tags (never online).

LRC lines carry [mm:ss.xx] timestamps (several per line allowed); [offset:ms]
shifts them, other [key:value] tags are metadata. Text without timestamps is
shown unsynced.
"""
import bisect
import os
import re

try:
    import mutagen
except ImportError:
    mutagen = None

STAMP = re.compile(r'\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]')
META = re.compile(r'^\[([a-zA-Z#]+):(.*)\]\s*$')


class Lyrics:
    def __init__(self, lines, synced, source):
        self.lines = lines          # [(seconds or None, text)]
        self.synced = synced
        self.source = source        # 'file' or 'tags'
        self._times = [time for time, _text in lines] if synced else []

    def current(self, seconds):
        """Index of the line being sung at `seconds` (-1 before the first)."""
        if not self.synced:
            return -1
        return bisect.bisect_right(self._times, seconds) - 1


def parse_lrc(text):
    """(lines, synced) from LRC or plain text."""
    offset = 0.0
    timed, plain = [], []
    for raw in text.splitlines():
        line = raw.strip()
        meta = META.match(line)
        if meta and not STAMP.match(line):
            if meta.group(1).lower() == 'offset':
                try:
                    offset = int(meta.group(2).strip()) / 1000   # positive shows lines earlier
                except ValueError:
                    pass
            continue
        stamps = []
        while True:
            match = STAMP.match(line)
            if not match:
                break
            minutes, seconds, fraction = match.groups()
            value = int(minutes) * 60 + int(seconds)
            if fraction:
                value += int(fraction) / 10 ** len(fraction)
            stamps.append(value)
            line = line[match.end():]
        line = line.strip()
        if stamps:
            timed.extend((stamp, line) for stamp in stamps)
        elif line:
            plain.append((None, line))
    if timed:
        return sorted((max(0.0, stamp - offset), line) for stamp, line in timed), True
    return plain, False


def _read_text(path):
    with open(path, 'rb') as stream:
        data = stream.read(512 * 1024)
    for encoding in ('utf-8-sig', 'cp1252'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('latin-1')


def _embedded(path):
    if mutagen is None:
        return None
    try:
        audio = mutagen.File(path)
    except Exception:
        return None
    tags = getattr(audio, 'tags', None)
    if not tags:
        return None
    if hasattr(tags, 'getall'):                      # ID3 (MP3)
        frames = tags.getall('USLT')
        return frames[0].text if frames else None
    for key in ('lyrics', 'LYRICS', 'unsyncedlyrics', 'UNSYNCEDLYRICS', '\xa9lyr'):
        values = tags.get(key) if hasattr(tags, 'get') else None
        if values:
            return values[0] if isinstance(values, list) else str(values)
    return None


def find_lyrics(path):
    """Lyrics for a track, or None: an .lrc with the same name wins over tags."""
    if not path or path.startswith(('http://', 'https://')):
        return None
    stem = os.path.splitext(path)[0]
    for extension in ('.lrc', '.LRC', '.txt'):
        if os.path.isfile(stem + extension):
            try:
                lines, synced = parse_lrc(_read_text(stem + extension))
            except OSError:
                continue
            if lines:
                return Lyrics(lines, synced, 'file')
    text = _embedded(path)
    if text:
        lines, synced = parse_lrc(text)
        if lines:
            return Lyrics(lines, synced, 'tags')
    return None
