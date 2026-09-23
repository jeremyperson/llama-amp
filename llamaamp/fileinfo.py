"""File Info: tags, audio properties and file details for one playlist entry."""
import os

from gi.repository import GLib, Gst, GstPbutils

DISCOVER_TIMEOUT = 5 * Gst.SECOND


def _size(size):
    for unit in ('bytes', 'KB', 'MB'):
        if size < 1024 or unit == 'MB':
            return f'{size} bytes' if unit == 'bytes' else f'{size:.1f} {unit}'.replace('.0 ', ' ')
        size /= 1024
    return f'{size:.1f} GB'


def _clock(seconds):
    seconds = int(seconds)
    if seconds >= 3600:
        return f'{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}'
    return f'{seconds // 60}:{seconds % 60:02d}'


def _gain(value):
    return f'{value:+.2f} dB'.replace('-', '−')


def _numbered(tags, number_tag, count_tag):
    ok, number = tags.get_uint(number_tag)
    if not ok or not number:
        return None
    ok, count = tags.get_uint(count_tag)
    return f'{number}/{count}' if ok and count else str(number)


def _year(tags):
    ok, when = tags.get_date_time(Gst.TAG_DATE_TIME)
    if ok and when and when.has_year():
        return str(when.get_year())
    ok, date = tags.get_date(Gst.TAG_DATE)
    return str(date.get_year()) if ok and date and date.valid() else None


def _image_bytes(tags):
    for tag in (Gst.TAG_IMAGE, Gst.TAG_PREVIEW_IMAGE):
        ok, sample = tags.get_sample(tag)
        buffer = sample.get_buffer() if ok and sample else None
        if buffer is None:
            continue
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        if ok:
            try:
                return bytes(mapped.data)
            finally:
                buffer.unmap(mapped)
    return None


def read_file_info(path):
    """{'sections': [(title, [(label, value), ...]), ...], 'art': bytes or None}.
    Blocking (runs GstDiscoverer); call it off the main loop."""
    file_rows = [('Location', path)]
    if path.startswith(('http://', 'https://')):
        return {'sections': [('File', file_rows + [('Status', 'Internet stream')])], 'art': None}
    try:
        size = os.stat(path).st_size
    except OSError:
        return {'sections': [('File', file_rows + [('Status', 'File not found')])], 'art': None}
    file_rows.append(('Size', _size(size)))
    try:
        info = GstPbutils.Discoverer.new(DISCOVER_TIMEOUT).discover_uri(
            Gst.filename_to_uri(os.path.abspath(path)))
    except GLib.Error as error:
        return {'sections': [('File', file_rows + [('Status', f'Unreadable: {error.message}')])], 'art': None}

    track, art = [], None
    tags = info.get_tags()
    if tags:
        def text(tag):
            ok, value = tags.get_string(tag)
            return value if ok and value else None
        def gain(tag):
            ok, value = tags.get_double(tag)
            return _gain(value) if ok else None
        track = [(label, value) for label, value in (
            ('Title', text(Gst.TAG_TITLE)), ('Artist', text(Gst.TAG_ARTIST)),
            ('Album', text(Gst.TAG_ALBUM)), ('Album artist', text(Gst.TAG_ALBUM_ARTIST)),
            ('Track', _numbered(tags, Gst.TAG_TRACK_NUMBER, Gst.TAG_TRACK_COUNT)),
            ('Disc', _numbered(tags, Gst.TAG_ALBUM_VOLUME_NUMBER, Gst.TAG_ALBUM_VOLUME_COUNT)),
            ('Year', _year(tags)), ('Genre', text(Gst.TAG_GENRE)),
            ('Composer', text(Gst.TAG_COMPOSER)), ('Comment', text(Gst.TAG_COMMENT)),
            ('Track gain', gain(Gst.TAG_TRACK_GAIN)), ('Album gain', gain(Gst.TAG_ALBUM_GAIN)),
        ) if value]
        art = _image_bytes(tags)

    audio = []
    duration = info.get_duration() / Gst.SECOND if info.get_duration() > 0 else 0
    streams = info.get_audio_streams()
    if streams:
        stream = streams[0]
        caps = stream.get_caps()
        rate, channels, depth = stream.get_sample_rate(), stream.get_channels(), stream.get_depth()
        bitrate = stream.get_bitrate() or stream.get_max_bitrate()
        audio = [(label, value) for label, value in (
            ('Format', GstPbutils.pb_utils_get_codec_description(caps) if caps else None),
            ('Sample rate', f'{rate / 1000:g} kHz' if rate else None),
            ('Channels', {1: 'Mono', 2: 'Stereo'}.get(channels, f'{channels} channels') if channels else None),
            ('Bit depth', f'{depth}-bit' if depth else None),
            ('Bitrate', f'{bitrate // 1000} kbps' if bitrate
             else f'≈ {round(size * 8 / duration / 1000)} kbps average' if duration else None),
        ) if value]
    if duration:
        audio.append(('Length', _clock(duration)))

    sections = [(title, rows) for title, rows in (('Track', track), ('Audio', audio)) if rows]
    return {'sections': sections + [('File', file_rows)], 'art': art}
