"""The built-in classic skin: an original "Llama" design drawn with Cairo at
Winamp 2's sheet geometry, so bundled and downloaded skins render alike.
(Winamp's own base skin is not redistributable.)"""
import cairo
from gi.repository import Gdk

from .loader import DEFAULT_PLEDIT, Skin
from .sprites import FONT_LOOKUP, SPRITES

CHASSIS, PANEL, CONTROL = (0x1b, 0x23, 0x1c), (0x26, 0x30, 0x27), (0x3a, 0x47, 0x3b)
LIGHT, SHADOW = (0x6a, 0x78, 0x6b), (0x08, 0x0b, 0x08)
TEXT, ACCENT, LCD, DIM = (0xd8, 0xe5, 0xd7), (0x52, 0xef, 0x34), (0x06, 0x0c, 0x06), (0x16, 0x3a, 0x12)
AMBER, RED = (0xff, 0xba, 0x45), (0xed, 0x64, 0x4d)

# 4x5 glyphs in 5x6 cells ('#' lit). Characters sharing a cell in FONT_LOOKUP
# ('<' and '[', ...) share a glyph.
GLYPHS = {
    'a': '.##. #..# #### #..# #..#', 'b': '###. #..# ###. #..# ###.', 'c': '.### #... #... #... .###',
    'd': '###. #..# #..# #..# ###.', 'e': '#### #... ###. #... ####', 'f': '#### #... ###. #... #...',
    'g': '.### #... #.## #..# .###', 'h': '#..# #..# #### #..# #..#', 'i': '###. .#.. .#.. .#.. ###.',
    'j': '..## ...# ...# #..# .##.', 'k': '#..# #.#. ##.. #.#. #..#', 'l': '#... #... #... #... ####',
    'm': '#..# #### #### #..# #..#', 'n': '#..# ##.# #.## #..# #..#', 'o': '.##. #..# #..# #..# .##.',
    'p': '###. #..# ###. #... #...', 'q': '.##. #..# #..# #.#. .#.#', 'r': '###. #..# ###. #.#. #..#',
    's': '.### #... .##. ...# ###.', 't': '###. .#.. .#.. .#.. .#..', 'u': '#..# #..# #..# #..# .##.',
    'v': '#..# #..# #..# .##. .##.', 'w': '#..# #..# #### #### #..#', 'x': '#..# #..# .##. #..# #..#',
    'y': '#.#. #.#. .#.. .#.. .#..', 'z': '#### ...# .##. #... ####',
    '0': '.##. #.## ##.# #..# .##.', '1': '.#.. ##.. .#.. .#.. ###.', '2': '###. ...# .##. #... ####',
    '3': '###. ...# .##. ...# ###.', '4': '#..# #..# #### ...# ...#', '5': '#### #... ###. ...# ###.',
    '6': '.##. #... ###. #..# .##.', '7': '#### ...# ..#. .#.. .#..', '8': '.##. #..# .##. #..# .##.',
    '9': '.##. #..# .### ...# .##.',
    '"': '#.#. #.#. .... .... ....', '@': '.##. #.## #.## #... .###', ' ': '.... .... .... .... ....',
    '…': '.... .... .... .... #.#.', '.': '.... .... .... .... .#..', ':': '.... .#.. .... .#.. ....',
    '(': '..#. .#.. .#.. .#.. ..#.', ')': '.#.. ..#. ..#. ..#. .#..', '-': '.... .... ###. .... ....',
    "'": '.#.. .#.. .... .... ....', '!': '.#.. .#.. .#.. .... .#..', '_': '.... .... .... .... ####',
    '+': '.... .#.. ###. .#.. ....', '\\': '#... .#.. .#.. ..#. ...#', '/': '...# ..#. .#.. .#.. #...',
    '[': '##.. #... #... #... ##..', ']': '..## ...# ...# ...# ..##', '^': '.#.. #.#. .... .... ....',
    '&': '.#.. #.#. .#.. #.#. .#.#', '%': '#..# ..#. .#.. #... #..#', ',': '.... .... .... .#.. #...',
    '=': '.... ###. .... ###. ....', '$': '.### ##.. .##. ..## ###.', '#': '.#.# #### .#.# #### .#.#',
    'å': '..#. .##. #..# #### #..#', 'ö': '#..# .##. #..# #..# .##.', 'ä': '#..# .##. #..# #### #..#',
    '?': '###. ...# .##. .... .#..', '*': '.... #.#. .#.. #.#. ....',
}
# 7-segment layout of a 9x13 digit: segment -> rectangle
SEGMENTS = {'a': (2, 0, 5, 2), 'b': (7, 1, 2, 5), 'c': (7, 7, 2, 5), 'd': (2, 11, 5, 2),
            'e': (0, 7, 2, 5), 'f': (0, 1, 2, 5), 'g': (2, 5.5, 5, 2)}
DIGIT_SEGMENTS = ['abcdef', 'bc', 'abged', 'abgcd', 'fgbc', 'afgcd', 'afgedc', 'abc', 'abcdefg', 'abfgcd']
EQ_LABELS = ['60', '170', '310', '600', '1k', '3k', '6k', '12k', '14k', '16k']


def _rgb(cr, color, alpha=1.0):
    cr.set_source_rgba(color[0] / 255, color[1] / 255, color[2] / 255, alpha)


def _fill(cr, color, x, y, w, h):
    _rgb(cr, color)
    cr.rectangle(x, y, w, h)
    cr.fill()


def _bevel(cr, x, y, w, h, face=CONTROL, raised=True):
    """A 1px bevelled box, raised (button) or sunken (well)."""
    _fill(cr, face, x, y, w, h)
    top, bottom = (LIGHT, SHADOW) if raised else (SHADOW, LIGHT)
    _fill(cr, top, x, y, w, 1)
    _fill(cr, top, x, y, 1, h)
    _fill(cr, bottom, x, y + h - 1, w, 1)
    _fill(cr, bottom, x + w - 1, y, 1, h)


def _text(cr, text, x, y, color=ACCENT):
    """Pixel-font text straight onto a sheet (5px per character)."""
    _rgb(cr, color)
    for index, char in enumerate(text.lower()):
        rows = GLYPHS.get(char, GLYPHS[' ']).split()
        for row, bits in enumerate(rows):
            for column, bit in enumerate(bits):
                if bit == '#':
                    cr.rectangle(x + index * 5 + column, y + row, 1, 1)
    cr.fill()


def _digit(cr, value, x, y, color=ACCENT):
    _rgb(cr, color)
    for segment in DIGIT_SEGMENTS[value]:
        sx, sy, sw, sh = SEGMENTS[segment]
        cr.rectangle(x + sx, y + round(sy), sw, sh)
    cr.fill()


def _icon(cr, kind, x, y, color):
    """Transport glyphs sized for 23x18 buttons."""
    _rgb(cr, color)
    if kind == 'previous':
        cr.rectangle(x, y, 2, 8)
        cr.move_to(x + 9, y); cr.line_to(x + 2, y + 4); cr.line_to(x + 9, y + 8)
    elif kind == 'play':
        cr.move_to(x + 1, y); cr.line_to(x + 8, y + 4); cr.line_to(x + 1, y + 8)
    elif kind == 'pause':
        cr.rectangle(x + 1, y, 3, 8); cr.rectangle(x + 6, y, 3, 8)
    elif kind == 'stop':
        cr.rectangle(x + 1, y, 8, 8)
    elif kind == 'next':
        cr.move_to(x, y); cr.line_to(x + 7, y + 4); cr.line_to(x, y + 8)
        cr.rectangle(x + 7, y, 2, 8)
    elif kind == 'eject':
        cr.move_to(x, y + 5); cr.line_to(x + 4.5, y); cr.line_to(x + 9, y + 5)
        cr.rectangle(x, y + 6, 9, 2)
    cr.close_path()
    cr.fill()


def _sheet_size(sheet):
    rects = SPRITES[sheet].values()
    return max(x + w for x, y, w, h in rects), max(y + h for x, y, w, h in rects)


def _canvas(sheet, size=None):
    width, height = size or _sheet_size(sheet)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surface)
    cr.set_antialias(cairo.ANTIALIAS_NONE)
    _fill(cr, CHASSIS, 0, 0, width, height)
    return surface, cr


def _rect(sheet, name):
    return SPRITES[sheet][name]


def _main():
    surface, cr = _canvas('MAIN')
    _bevel(cr, 0, 0, 275, 116, CHASSIS)
    # Status well (indicator, clock, visualizer) and song info well
    _bevel(cr, 9, 20, 98, 42, LCD, raised=False)
    _bevel(cr, 108, 20, 159, 34, LCD, raised=False)
    _text(cr, 'kbps', 128, 43, TEXT)
    _text(cr, 'khz', 168, 43, TEXT)
    # Separator above the transport row and a small llama in the corner
    _fill(cr, SHADOW, 12, 85, 251, 1)
    _fill(cr, LIGHT, 12, 86, 251, 1)
    _llama(cr, 253, 91)
    return surface


def _llama(cr, x, y):
    _rgb(cr, ACCENT)
    for px, py in [(9, 0), (10, 0), (9, 1), (10, 1), (11, 1), (9, 2), (9, 3), (9, 4), (9, 5), (2, 6), (3, 6),
                   (4, 6), (5, 6), (6, 6), (7, 6), (8, 6), (9, 6), (2, 7), (3, 7), (4, 7), (5, 7), (6, 7), (7, 7),
                   (8, 7), (9, 7), (2, 8), (9, 8), (2, 9), (9, 9), (2, 10), (4, 10), (7, 10), (9, 10)]:
        cr.rectangle(x + px, y + py + 2, 1, 1)
    cr.fill()


def _cbuttons():
    surface, cr = _canvas('CBUTTONS')
    for kind in ('previous', 'play', 'pause', 'stop', 'next', 'eject'):
        base = 'MAIN_' + kind.upper() + '_BUTTON'
        for name, raised in ((base, True), (base + '_ACTIVE', False)):
            x, y, w, h = _rect('CBUTTONS', name)
            _bevel(cr, x, y, w, h, CONTROL if raised else PANEL, raised)
            offset = 0 if raised else 1
            color = ACCENT if kind in ('play', 'pause') else TEXT
            _icon(cr, kind, x + (w - 9) // 2 + offset, y + (h - 8) // 2 + offset, color)
    return surface


def _titlebar():
    surface, cr = _canvas('TITLEBAR')
    for name, active in (('MAIN_TITLE_BAR', False), ('MAIN_TITLE_BAR_SELECTED', True),
                         ('MAIN_EASTER_EGG_TITLE_BAR', False), ('MAIN_EASTER_EGG_TITLE_BAR_SELECTED', True),
                         ('MAIN_SHADE_BACKGROUND', False), ('MAIN_SHADE_BACKGROUND_SELECTED', True)):
        x, y, w, h = _rect('TITLEBAR', name)
        _title_bar(cr, x, y, w, h, 'llama amp' if 'SHADE' not in name else '', active)
        if 'SHADE' in name:
            _bevel(cr, x + 78, y + 2, 40, 10, LCD, raised=False)     # shade-mode visualizer well
            _bevel(cr, x + 125, y + 2, 32, 10, LCD, raised=False)    # mini clock well
    for name in ('MAIN_OPTIONS_BUTTON', 'MAIN_MINIMIZE_BUTTON', 'MAIN_SHADE_BUTTON', 'MAIN_CLOSE_BUTTON',
                 'MAIN_SHADE_BUTTON_SELECTED'):
        for suffix, raised in (('', True), ('_DEPRESSED', False)):
            x, y, w, h = _rect('TITLEBAR', name + suffix)
            _small_button(cr, x, y, name.split('_')[1].lower() if 'SELECTED' not in name else 'unshade', raised)
    for name in ('MAIN_CLUTTER_BAR_BACKGROUND', 'MAIN_CLUTTER_BAR_BACKGROUND_DISABLED'):
        x, y, w, h = _rect('TITLEBAR', name)
        _bevel(cr, x, y, w, h, PANEL)
    x, y, w, h = _rect('TITLEBAR', 'MAIN_SHADE_POSITION_BACKGROUND')
    _bevel(cr, x, y, w, h, LCD, raised=False)
    for name in ('MAIN_SHADE_POSITION_THUMB', 'MAIN_SHADE_POSITION_THUMB_LEFT', 'MAIN_SHADE_POSITION_THUMB_RIGHT'):
        x, y, w, h = _rect('TITLEBAR', name)
        _fill(cr, ACCENT, x, y + 1, w, h - 2)
    return surface


def _title_bar(cr, x, y, w, h, title, active):
    _fill(cr, CONTROL if active else PANEL, x, y, w, h)
    _fill(cr, LIGHT if active else CONTROL, x, y, w, 1)
    _fill(cr, SHADOW, x, y + h - 1, w, 1)
    stripe = (0x4c, 0x5c, 0x4d) if active else (0x30, 0x3a, 0x31)
    for row in range(y + 4, y + h - 3, 2):
        _fill(cr, stripe, x + 18, row, w - 60, 1)
    if title:
        width = len(title) * 5 + 6
        left = x + (w - width) // 2
        _fill(cr, CONTROL if active else PANEL, left, y + 2, width, h - 4)
        _text(cr, title, left + 3, y + 4, ACCENT if active else TEXT)


def _small_button(cr, x, y, kind, raised):
    _bevel(cr, x, y, 9, 9, CONTROL, raised)
    _rgb(cr, TEXT)
    o = 0 if raised else 1
    if kind == 'close':
        for i in range(5):
            cr.rectangle(x + 2 + i + o, y + 2 + i + o, 1, 1)
            cr.rectangle(x + 6 - i + o, y + 2 + i + o, 1, 1)
    elif kind == 'minimize':
        cr.rectangle(x + 2 + o, y + 6 + o, 5, 1)
    elif kind in ('shade', 'unshade'):
        cr.rectangle(x + 2 + o, y + 2 + o, 5, 1)
        cr.rectangle(x + 2 + o, y + (6 if kind == 'unshade' else 4) + o, 5, 1)
    elif kind == 'options':
        for i in range(3):
            cr.rectangle(x + 2 + o, y + 2 + 2 * i + o, 5, 1)
    cr.fill()


def _numbers(sheet):
    surface, cr = _canvas(sheet, (108, 13) if sheet == 'NUMS_EX' else (99, 13))
    _fill(cr, LCD, 0, 0, surface.get_width(), 13)
    for value in range(10):
        _digit(cr, value, value * 9, 0)
    if sheet == 'NUMS_EX':
        _fill(cr, ACCENT, 99 + 2, 6, 5, 2)
    else:
        _fill(cr, ACCENT, 20, 6, 5, 1)       # Winamp stores the minus inside digit 2's cell
    return surface


def _text_sheet():
    surface, cr = _canvas('TEXT', (155, 18))
    _fill(cr, LCD, 0, 0, 155, 18)
    for char, (row, column) in FONT_LOOKUP.items():
        _text(cr, char.lower() if char.lower() in GLYPHS else ' ', column * 5, row * 6)
    return surface


def _posbar():
    surface, cr = _canvas('POSBAR')
    _bevel(cr, 0, 0, 248, 10, LCD, raised=False)
    _fill(cr, DIM, 3, 4, 242, 2)
    for name, face in (('MAIN_POSITION_SLIDER_THUMB', CONTROL), ('MAIN_POSITION_SLIDER_THUMB_SELECTED', LIGHT)):
        x, y, w, h = _rect('POSBAR', name)
        _bevel(cr, x, y, w, h, face)
        _fill(cr, ACCENT, x + 13, y + 3, 3, 4)
    return surface


def _gradient(fraction):
    """Green through amber to red, like a level meter."""
    stops = (ACCENT, AMBER, RED)
    position = fraction * 2
    low = stops[min(1, int(position))]
    high = stops[min(2, int(position) + 1)]
    t = position - int(position) if position < 2 else 1
    return tuple(round(a + (b - a) * t) for a, b in zip(low, high))


def _slider_sheet(sheet, width, offset_x, centered):
    surface, cr = _canvas(sheet)
    for frame in range(28):
        fraction = frame / 27
        y = frame * 15
        _bevel(cr, offset_x, y, width, 13, LCD, raised=False)
        color = _gradient(fraction) if not centered else _gradient(fraction * .6)
        if centered:
            half = (width - 4) / 2 * fraction
            _fill(cr, color, offset_x + width / 2 - half, y + 5, max(1, half * 2), 3)
        else:
            _fill(cr, color, offset_x + 2, y + 5, max(1, (width - 4) * fraction), 3)
    for name, face in ((sheet_thumb(sheet), CONTROL), (sheet_thumb(sheet) + ('_ACTIVE' if sheet == 'BALANCE' else '_SELECTED'), LIGHT)):
        x, y, w, h = _rect(sheet, name)
        _bevel(cr, x, y, w, h, face)
        _fill(cr, ACCENT, x + 6, y + 3, 2, 5)
    return surface


def sheet_thumb(sheet):
    return 'MAIN_BALANCE_THUMB' if sheet == 'BALANCE' else 'MAIN_VOLUME_THUMB'


def _shufrep():
    surface, cr = _canvas('SHUFREP')
    for base, label in (('MAIN_SHUFFLE_BUTTON', 'shuffle'), ('MAIN_REPEAT_BUTTON', 'rep')):
        for suffix, raised, lit in (('', True, False), ('_DEPRESSED', False, False),
                                    ('_SELECTED', True, True), ('_SELECTED_DEPRESSED', False, True)):
            x, y, w, h = _rect('SHUFREP', base + suffix)
            _bevel(cr, x, y, w, h, CONTROL if raised else PANEL, raised)
            _fill(cr, ACCENT if lit else DIM, x + 4, y + 6, 3, 3)
            _text(cr, label, x + 9 + (0 if raised else 1), y + 5 + (0 if raised else 1), TEXT)
    for base, label in (('MAIN_EQ_BUTTON', 'eq'), ('MAIN_PLAYLIST_BUTTON', 'pl')):
        for suffix, raised, lit in (('', True, False), ('_SELECTED', True, True),
                                    ('_DEPRESSED', False, False), ('_DEPRESSED_SELECTED', False, True)):
            x, y, w, h = _rect('SHUFREP', base + suffix)
            _bevel(cr, x, y, w, h, CONTROL if raised else PANEL, raised)
            _fill(cr, ACCENT if lit else DIM, x + 3, y + 5, 2, 2)
            _text(cr, label, x + 9, y + 3, TEXT)
    return surface


def _monoster():
    surface, cr = _canvas('MONOSTER')
    _fill(cr, LCD, 0, 0, 58, 24)
    for base, label in (('MAIN_STEREO', 'stereo'), ('MAIN_MONO', 'mono')):
        for suffix, color in (('', DIM), ('_SELECTED', ACCENT)):
            x, y, w, h = _rect('MONOSTER', base + suffix)
            _text(cr, label, x + (w - len(label) * 5) // 2 + 1, y + 3, color)
    return surface


def _playpaus():
    surface, cr = _canvas('PLAYPAUS', (48, 9))
    _fill(cr, LCD, 0, 0, 48, 9)
    _rgb(cr, ACCENT)
    cr.move_to(1, 1); cr.line_to(7, 4.5); cr.line_to(1, 8); cr.close_path(); cr.fill()
    _fill(cr, AMBER, 10, 1, 2, 7); _fill(cr, AMBER, 14, 1, 2, 7)
    _fill(cr, RED, 20, 2, 5, 5)
    _fill(cr, ACCENT, 40, 2, 1, 5)            # "working" tick
    return surface


def _eqmain():
    surface, cr = _canvas('EQMAIN')
    _bevel(cr, 0, 0, 275, 116, CHASSIS)
    _bevel(cr, 83, 15, 119, 23, LCD, raised=False)              # graph well
    _text(cr, 'preamp', 12, 105, TEXT)
    for label, y in (('+12', 36), ('+0', 64), ('-12', 95)):
        _text(cr, label, 46, y, TEXT)
    for index, label in enumerate(EQ_LABELS):
        _text(cr, label, 78 + index * 18 + (14 - len(label) * 5) // 2 + 1, 105, TEXT)
    for name, active in (('EQ_TITLE_BAR', False), ('EQ_TITLE_BAR_SELECTED', True)):
        x, y, w, h = _rect('EQMAIN', name)
        _title_bar(cr, x, y, w, h, 'equalizer', active)
    for frame in range(28):
        fx, fy = 13 + (frame % 14) * 15, 164 + (frame // 14) * 65
        _bevel(cr, fx, fy, 14, 63, LCD, raised=False)
        level = frame / 27                       # 0 = +12 dB (top) ... 1 = -12 dB
        top = fy + 2 + round(level * 58)
        _fill(cr, _gradient(1 - level) if level < .5 else ACCENT, fx + 6, top, 2, fy + 61 - top)
    for name, face in (('EQ_SLIDER_THUMB', CONTROL), ('EQ_SLIDER_THUMB_SELECTED', LIGHT)):
        x, y, w, h = _rect('EQMAIN', name)
        _bevel(cr, x, y, w, h, face)
        _fill(cr, ACCENT, x + 3, y + 5, 5, 1)
    for name, active in (('EQ_CLOSE_BUTTON', True), ('EQ_CLOSE_BUTTON_ACTIVE', False)):
        x, y, w, h = _rect('EQMAIN', name)
        _small_button(cr, x, y, 'close', active)
    x, y, w, h = _rect('EQMAIN', 'EQ_MAXIMIZE_BUTTON_ACTIVE_FALLBACK')
    _small_button(cr, x, y, 'shade', False)
    for base, label in (('EQ_ON_BUTTON', 'on'), ('EQ_AUTO_BUTTON', 'auto')):
        for suffix, raised, lit in (('', True, False), ('_DEPRESSED', False, False),
                                    ('_SELECTED', True, True), ('_SELECTED_DEPRESSED', False, True)):
            x, y, w, h = _rect('EQMAIN', base + suffix)
            _bevel(cr, x, y, w, h, CONTROL if raised else PANEL, raised)
            _fill(cr, ACCENT if lit else DIM, x + 3, y + 5, 2, 2)
            _text(cr, label, x + 8, y + 3, TEXT)
    x, y, w, h = _rect('EQMAIN', 'EQ_GRAPH_BACKGROUND')
    _fill(cr, LCD, x, y, w, h)
    _fill(cr, DIM, x, y + 9, w, 1)
    x, y, w, h = _rect('EQMAIN', 'EQ_GRAPH_LINE_COLORS')
    for row in range(h):
        _fill(cr, _gradient(abs(row - 9) / 9), x, y + row, 1, 1)
    for name, raised in (('EQ_PRESETS_BUTTON', True), ('EQ_PRESETS_BUTTON_SELECTED', False)):
        x, y, w, h = _rect('EQMAIN', name)
        _bevel(cr, x, y, w, h, CONTROL if raised else PANEL, raised)
        _text(cr, 'presets', x + 5, y + 3, TEXT)
    x, y, w, h = _rect('EQMAIN', 'EQ_PREAMP_LINE')
    _fill(cr, AMBER, x, y, w, h)
    return surface


def _eq_ex():
    surface, cr = _canvas('EQ_EX')
    for name, active in (('EQ_SHADE_BACKGROUND', False), ('EQ_SHADE_BACKGROUND_SELECTED', True)):
        x, y, w, h = _rect('EQ_EX', name)
        _title_bar(cr, x, y, w, h, '', active)
        _bevel(cr, x + 60, y + 3, 99, 8, LCD, raised=False)
        _bevel(cr, x + 163, y + 3, 45, 8, LCD, raised=False)
    for name in SPRITES['EQ_EX']:
        if 'SLIDER' in name:
            x, y, w, h = _rect('EQ_EX', name)
            _fill(cr, ACCENT, x, y + 1, w, h - 2)
    for name, kind, raised in (('EQ_MAXIMIZE_BUTTON_ACTIVE', 'unshade', False), ('EQ_MINIMIZE_BUTTON_ACTIVE', 'shade', False),
                               ('EQ_SHADE_CLOSE_BUTTON', 'close', True), ('EQ_SHADE_CLOSE_BUTTON_ACTIVE', 'close', False)):
        x, y, w, h = _rect('EQ_EX', name)
        _small_button(cr, x, y, kind, raised)
    return surface


def _pledit():
    surface, cr = _canvas('PLEDIT')
    for active, names in ((False, ('PLAYLIST_TOP_TILE', 'PLAYLIST_TOP_LEFT_CORNER')),
                          (True, ('PLAYLIST_TOP_TILE_SELECTED', 'PLAYLIST_TOP_LEFT_SELECTED'))):
        for name in names:
            _title_bar(cr, *_rect('PLEDIT', name), '', active)
        suffix = '_SELECTED' if active else ''
        x, y, w, h = _rect('PLEDIT', 'PLAYLIST_TITLE_BAR' + suffix)
        _title_bar(cr, x, y, w, h, '', active)
        _fill(cr, CONTROL if active else PANEL, x + 14, y + 3, 72, 13)
        _text(cr, 'playlist', x + 30, y + 7, ACCENT if active else TEXT)
        # Shade and close sit 12px and 2px from the right edge of this corner
        x, y, w, h = _rect('PLEDIT', 'PLAYLIST_TOP_RIGHT_CORNER' + suffix)
        _title_bar(cr, x, y, w, h, '', active)
        _small_button(cr, x + w - 21, y + 3, 'shade', True)
        _small_button(cr, x + w - 11, y + 3, 'close', True)
    x, y, w, h = _rect('PLEDIT', 'PLAYLIST_LEFT_TILE')
    _bevel(cr, x, y, w, h, CHASSIS)
    x, y, w, h = _rect('PLEDIT', 'PLAYLIST_RIGHT_TILE')
    _bevel(cr, x, y, w, h, CHASSIS)
    _bevel(cr, x + 5, y, 10, h, LCD, raised=False)             # scroll groove
    for name, face in (('PLAYLIST_SCROLL_HANDLE', CONTROL), ('PLAYLIST_SCROLL_HANDLE_SELECTED', LIGHT)):
        x, y, w, h = _rect('PLEDIT', name)
        _bevel(cr, x, y, w, h, face)
    x, y, w, h = _rect('PLEDIT', 'PLAYLIST_BOTTOM_TILE')
    _bevel(cr, x, y, w, h, CHASSIS)
    x, y, w, h = _rect('PLEDIT', 'PLAYLIST_BOTTOM_LEFT_CORNER')
    _bevel(cr, x, y, w, h, CHASSIS)
    for index, label in enumerate(('add', 'rem', 'sel', 'misc')):
        _text(cr, label, x + 14 + index * 29 + (22 - len(label) * 5) // 2, y + 29, TEXT)
    x, y, w, h = _rect('PLEDIT', 'PLAYLIST_BOTTOM_RIGHT_CORNER')
    _bevel(cr, x, y, w, h, CHASSIS)
    _bevel(cr, x + 5, y + 8, 88, 12, LCD, raised=False)        # running-time well
    _bevel(cr, x + 62, y + 21, 34, 10, LCD, raised=False)      # mini clock well
    for index, kind in enumerate(('previous', 'play', 'pause', 'stop', 'next', 'eject')):
        _mini_icon(cr, kind, x + 3 + index * 8, y + 22)
    _text(cr, 'list', x + w - 44 + (22 - 20) // 2, y + 29, TEXT)
    x, y, w, h = _rect('PLEDIT', 'PLAYLIST_VISUALIZER_BACKGROUND')
    _bevel(cr, x, y, w, h, CHASSIS)
    _bevel(cr, x + 2, y + 10, 72, 18, LCD, raised=False)
    for name, active in (('PLAYLIST_SHADE_BACKGROUND', False), ('PLAYLIST_SHADE_BACKGROUND_LEFT', False),
                         ('PLAYLIST_SHADE_BACKGROUND_RIGHT', False), ('PLAYLIST_SHADE_BACKGROUND_RIGHT_SELECTED', True)):
        x, y, w, h = _rect('PLEDIT', name)
        _title_bar(cr, x, y, w, h, '', active)
    for name in SPRITES['PLEDIT']:
        if name.endswith('_MENU_BAR') or name == 'PLAYLIST_LIST_BAR':
            x, y, w, h = _rect('PLEDIT', name)
            _fill(cr, ACCENT, x, y, w, h)
    menus = {'ADD_URL': 'url', 'ADD_DIR': 'dir', 'ADD_FILE': 'file', 'REMOVE_ALL': 'all', 'CROP': 'crop',
             'REMOVE_SELECTED': 'sel', 'REMOVE_MISC': 'misc', 'INVERT_SELECTION': 'inv', 'SELECT_ZERO': 'none',
             'SELECT_ALL': 'all', 'SORT_LIST': 'sort', 'FILE_INFO': 'info', 'MISC_OPTIONS': 'opts',
             'NEW_LIST': 'new', 'SAVE_LIST': 'save', 'LOAD_LIST': 'load'}
    for key, label in menus.items():
        for suffix, raised in (('', True), ('_SELECTED', False)):
            x, y, w, h = _rect('PLEDIT', 'PLAYLIST_' + key + suffix)
            _bevel(cr, x, y, w, h, CONTROL if raised else PANEL, raised)
            _text(cr, label[:4], x + (w - len(label[:4]) * 5) // 2 + 1, y + 6, ACCENT if not raised else TEXT)
    for name, kind in (('PLAYLIST_CLOSE_SELECTED', 'close'), ('PLAYLIST_COLLAPSE_SELECTED', 'shade'),
                       ('PLAYLIST_EXPAND_SELECTED', 'unshade')):
        x, y, w, h = _rect('PLEDIT', name)
        _small_button(cr, x, y, kind, False)
    return surface


def _mini_icon(cr, kind, x, y):
    cr.save()
    cr.translate(x, y)
    cr.scale(.7, .7)
    _icon(cr, kind, 1, 1, TEXT)
    cr.restore()


def _to_pixbuf(surface):
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, surface.get_width(), surface.get_height())


VISCOLORS = [LCD, (0x1f, 0x2b, 0x20)] + [_gradient(1 - i / 15) for i in range(16)] + \
    [(0xd8, 0xff, 0xcc), (0xa8, 0xf5, 0x90), (0x52, 0xef, 0x34), (0x38, 0xb8, 0x22), (0x24, 0x80, 0x16)] + \
    [(0xe8, 0xff, 0xdc)]


def build_default_skin():
    sheets = {'MAIN': _main(), 'CBUTTONS': _cbuttons(), 'TITLEBAR': _titlebar(), 'NUMBERS': _numbers('NUMBERS'),
              'NUMS_EX': _numbers('NUMS_EX'), 'TEXT': _text_sheet(), 'POSBAR': _posbar(),
              'VOLUME': _slider_sheet('VOLUME', 68, 0, False), 'BALANCE': _slider_sheet('BALANCE', 38, 9, True),
              'SHUFREP': _shufrep(), 'MONOSTER': _monoster(), 'PLAYPAUS': _playpaus(), 'EQMAIN': _eqmain(),
              'EQ_EX': _eq_ex(), 'PLEDIT': _pledit()}
    pledit = dict(DEFAULT_PLEDIT, normal=ACCENT, current=(0xff, 0xff, 0xff), normalbg=LCD,
                  selectedbg=(0x30, 0x4a, 0x30), font='DejaVu Sans')
    return Skin('Llama (built-in)', {name: _to_pixbuf(s) for name, s in sheets.items()}, VISCOLORS, pledit)

