"""File Info: tags, audio properties and file details for one playlist entry."""
import os

from gi.repository import GLib, Gst, GstPbutils

from .i18n import _
from .paths import displayable

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
    file_rows = [(_('Location'), displayable(path))]
    if path.startswith(('http://', 'https://')):
        return {'sections': [(_('File'), file_rows + [(_('Status'), _('Internet stream'))])], 'art': None}
    try:
        size = os.stat(path).st_size
    except OSError:
        return {'sections': [(_('File'), file_rows + [(_('Status'), _('File not found'))])], 'art': None}
    file_rows.append((_('Size'), _size(size)))
    try:
        info = GstPbutils.Discoverer.new(DISCOVER_TIMEOUT).discover_uri(
            Gst.filename_to_uri(os.path.abspath(path)))
    except GLib.Error as error:
        return {'sections': [(_('File'), file_rows + [(_('Status'), _('Unreadable: {error}').format(error=error.message))])], 'art': None}

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
            (_('Title'), text(Gst.TAG_TITLE)), (_('Artist'), text(Gst.TAG_ARTIST)),
            (_('Album'), text(Gst.TAG_ALBUM)), (_('Album artist'), text(Gst.TAG_ALBUM_ARTIST)),
            (_('Track'), _numbered(tags, Gst.TAG_TRACK_NUMBER, Gst.TAG_TRACK_COUNT)),
            (_('Disc'), _numbered(tags, Gst.TAG_ALBUM_VOLUME_NUMBER, Gst.TAG_ALBUM_VOLUME_COUNT)),
            (_('Year'), _year(tags)), (_('Genre'), text(Gst.TAG_GENRE)),
            (_('Composer'), text(Gst.TAG_COMPOSER)), (_('Comment'), text(Gst.TAG_COMMENT)),
            (_('Track gain'), gain(Gst.TAG_TRACK_GAIN)), (_('Album gain'), gain(Gst.TAG_ALBUM_GAIN)),
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
            (_('Format'), GstPbutils.pb_utils_get_codec_description(caps) if caps else None),
            (_('Sample rate'), _('{rate} kHz').format(rate=f'{rate / 1000:g}') if rate else None),
            (_('Channels'), {1: _('Mono'), 2: _('Stereo')}.get(channels, _('{count} channels').format(count=channels)) if channels else None),
            (_('Bit depth'), _('{depth}-bit').format(depth=depth) if depth else None),
            (_('Bitrate'), _('{rate} kbps').format(rate=bitrate // 1000) if bitrate
             else _('≈ {rate} kbps average').format(rate=round(size * 8 / duration / 1000)) if duration else None),
        ) if value]
    if duration:
        audio.append((_('Length'), _clock(duration)))

    sections = [(title, rows) for title, rows in ((_('Track'), track), (_('Audio'), audio)) if rows]
    return {'sections': sections + [(_('File'), file_rows)], 'art': art}
