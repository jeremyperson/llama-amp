"""Reading and writing the common tags with mutagen's "easy" interface, which
maps one set of names onto ID3, Vorbis comments and MP4 atoms.
Editing is offered only when mutagen is installed and understands the file."""
try:
    import mutagen
    from mutagen._vorbis import VComment
    from mutagen.easyid3 import EasyID3
    from mutagen.easymp4 import EasyMP4Tags
    EASY_TAGS = (VComment, EasyID3, EasyMP4Tags)
except ImportError:
    mutagen = None

from .constants import N_

# (easy key, label) in the order the editor shows them
FIELDS = (
    ('title', N_('Title')),
    ('artist', N_('Artist')),
    ('album', N_('Album')),
    ('albumartist', N_('Album artist')),
    ('tracknumber', N_('Track')),
    ('discnumber', N_('Disc')),
    ('date', N_('Year')),
    ('genre', N_('Genre')),
    ('comment', N_('Comment')),
)


def _register_id3_comment():
    """EasyID3 has no comment key; map it to the language-neutral COMM frame."""
    from mutagen.id3 import COMM

    def getter(id3, _key):
        return [text for frame in id3.getall('COMM') if not frame.desc for text in frame.text]

    def setter(id3, _key, value):
        id3.delall('COMM')
        id3.add(COMM(encoding=3, lang='eng', desc='', text=value))

    def deleter(id3, _key):
        id3.delall('COMM')

    if 'comment' not in EasyID3.valid_keys:
        EasyID3.RegisterKey('comment', getter, setter, deleter)


if mutagen is not None:
    _register_id3_comment()


def _open(path):
    """The file with easy tags added if it had none, or None if it can't be edited."""
    if mutagen is None or not path or path.startswith(('http://', 'https://')):
        return None
    try:
        audio = mutagen.File(path, easy=True)
        if audio is not None and audio.tags is None:
            audio.add_tags()
    except Exception:
        return None
    # WAV and AIFF carry plain ID3, which wants frame names rather than easy keys
    return audio if audio is not None and isinstance(audio.tags, EASY_TAGS) else None


def editable(path):
    return _open(path) is not None


def read_tags(path):
    """{easy key: text} for FIELDS; multiple values are joined with '; '."""
    audio = _open(path)
    if audio is None:
        return None
    values = {}
    for key, _label in FIELDS:
        try:
            found = audio.tags.get(key)
        except Exception:
            found = None
        values[key] = '; '.join(str(value) for value in found) if found else ''
    return values


def write_tags(path, values):
    """Save {easy key: text}; empty text removes the tag. Raises OSError or
    mutagen errors, and ValueError for a file that can't be tagged."""
    audio = _open(path)
    if audio is None:
        raise ValueError('not taggable')
    for key, _label in FIELDS:
        if key not in values:
            continue
        text = values[key].strip()
        parts = [part.strip() for part in text.split(';') if part.strip()] if key in ('artist', 'genre') else [text]
        if text:
            audio.tags[key] = parts
        elif key in audio.tags:
            del audio.tags[key]
    audio.save()
