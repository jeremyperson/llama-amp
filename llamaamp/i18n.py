"""Translations (gettext, domain "llamaamp").

Compiled catalogs are looked up beside the app (a portable checkout after
running tools/compile_translations.py, or /usr/share/llama-amp/locale in the
.deb) and then in the system locale directory. Without a catalog every string
stays English.

_() translates at display time. N_() only marks strings kept in tables whose
English text is also an identifier (theme names, EQ presets); wrap them in _()
where they are shown.
"""
import gettext
import os

from .constants import APP_DIR, APP_NAME, DEFAULT_SONG_TEXT, N_

__all__ = ['_', 'ngettext', 'N_', 'default_song_text']

DOMAIN = 'llamaamp'


def _catalog():
    for directory in (os.path.join(APP_DIR, 'locale'), None):
        translation = gettext.translation(DOMAIN, localedir=directory, fallback=True)
        if type(translation) is not gettext.NullTranslations:     # found a real catalog
            return translation
    return gettext.NullTranslations()


_translation = _catalog()


def _(message):
    return _translation.gettext(message)


def ngettext(singular, plural, count):
    return _translation.ngettext(singular, plural, count)


def default_song_text():
    return _(DEFAULT_SONG_TEXT).format(app=APP_NAME)
