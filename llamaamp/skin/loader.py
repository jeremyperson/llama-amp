"""Load Winamp 2 skins (.wsz zip archives or unpacked directories)."""
import configparser
import os
import re
import unicodedata
import zipfile

import cairo
from gi.repository import Gdk, GdkPixbuf

from .sprites import FONT_LOOKUP, SPRITES

# Sheet name -> file stem in the skin (matched case-insensitively, any folder)
SHEET_FILES = {'MAIN': 'main', 'CBUTTONS': 'cbuttons', 'TITLEBAR': 'titlebar', 'NUMBERS': 'numbers',
               'NUMS_EX': 'nums_ex', 'TEXT': 'text', 'POSBAR': 'posbar', 'VOLUME': 'volume',
               'BALANCE': 'balance', 'SHUFREP': 'shufrep', 'MONOSTER': 'monoster',
               'PLAYPAUS': 'playpaus', 'EQMAIN': 'eqmain', 'EQ_EX': 'eq_ex', 'PLEDIT': 'pledit'}
SKIN_EXTENSIONS = ('.wsz', '.zip')
TEXT_CELL = (5, 6)

# sprite name -> (sheet, (x, y, width, height))
_SPRITE_INDEX = {name: (sheet, rect) for sheet, sprites in SPRITES.items() for name, rect in sprites.items()}

DEFAULT_PLEDIT = {'normal': (0, 255, 0), 'current': (255, 255, 255),
                  'normalbg': (0, 0, 0), 'selectedbg': (0, 0, 198), 'font': 'Arial'}


def _color(text):
    match = re.fullmatch(r'\s*#?([0-9a-fA-F]{6})\s*', text or '')
    if not match:
        return None
    value = int(match.group(1), 16)
    return value >> 16, value >> 8 & 255, value & 255


def parse_viscolor(text, fallback):
    """24 RGB colors: 0 background, 1 dots, 2-17 spectrum top to bottom,
    18-22 oscilloscope, 23 peaks. Lines are 'r,g,b, // comment'; missing or
    malformed lines keep the fallback color."""
    colors = list(fallback)
    for index, line in enumerate(text.splitlines()[:24]):
        numbers = re.findall(r'\d+', line.split('//')[0])
        if len(numbers) >= 3:
            colors[index] = tuple(min(255, int(n)) for n in numbers[:3])
    return colors


def parse_pledit(text):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return dict(DEFAULT_PLEDIT)
    section = next((name for name in parser.sections() if name.lower() == 'text'), None)
    result = dict(DEFAULT_PLEDIT)
    if section:
        for key, value in parser.items(section):
            key = key.lower()
            if key == 'font':
                result['font'] = value.strip() or DEFAULT_PLEDIT['font']
            elif key in result:
                result[key] = _color(value) or result[key]
    return result


def parse_region(text):
    """region.txt: per window state ([Normal], [WindowShade], [Equalizer],
    [EqualizerWS]) a list of polygons, each a list of (x, y). NumPoints gives
    the vertex count of each polygon; PointList the coordinates in order."""
    regions, section, values = {}, None, {}

    def finish():
        counts = [int(n) for n in re.findall(r'\d+', values.get('numpoints', ''))]
        numbers = [int(n) for n in re.findall(r'-?\d+', values.get('pointlist', ''))]
        points = list(zip(numbers[0::2], numbers[1::2]))
        if section and counts and sum(counts) <= len(points):
            polygons, start = [], 0
            for count in counts:
                polygons.append(points[start:start + count])
                start += count
            regions[section] = [polygon for polygon in polygons if len(polygon) >= 3]

    for line in text.splitlines():
        line = line.split(';')[0].strip()
        header = re.fullmatch(r'\[(.+)\]', line)
        if header:
            finish()
            section, values = header.group(1).strip().lower(), {}
        elif '=' in line:
            key, value = line.split('=', 1)
            values[key.strip().lower()] = values.get(key.strip().lower(), '') + ' ' + value
    finish()
    return regions


class Skin:
    """Sprite sheets as Cairo surfaces plus the text configuration."""
    def __init__(self, name, sheets, viscolors, pledit, regions=None):
        self.name = name
        self.viscolors = viscolors
        self.pledit = pledit
        self.regions = regions or {}
        self._surfaces = {sheet: Gdk.cairo_surface_create_from_pixbuf(pixbuf, 1, None)
                          for sheet, pixbuf in sheets.items()}
        self.sheets = sheets

    @classmethod
    def load(cls, path, fallback):
        """Read a .wsz/.zip or directory. Sheets it lacks come from fallback."""
        files = _read_files(path)
        by_stem = {}
        for member, data in files.items():
            stem, ext = os.path.splitext(os.path.basename(member).lower())
            by_stem.setdefault(stem + ext, data)
        sheets = dict(fallback.sheets)
        for sheet, stem in SHEET_FILES.items():
            data = by_stem.get(stem + '.bmp') or by_stem.get(stem + '.png')
            pixbuf = _decode(data) if data else None
            if pixbuf is not None:
                sheets[sheet] = pixbuf
        if 'nums_ex.bmp' not in by_stem and 'numbers.bmp' in by_stem:
            sheets.pop('NUMS_EX', None)     # the skin's own digits beat the fallback's
        viscolor = by_stem.get('viscolor.txt')
        viscolors = parse_viscolor(viscolor.decode('latin-1'), fallback.viscolors) if viscolor else fallback.viscolors
        pledit = by_stem.get('pledit.txt')
        region = by_stem.get('region.txt')
        return cls(os.path.splitext(os.path.basename(path.rstrip('/')))[0], sheets, viscolors,
                   parse_pledit(pledit.decode('latin-1')) if pledit else dict(fallback.pledit),
                   parse_region(region.decode('latin-1')) if region else {})

    def has(self, sheet):
        return sheet in self._surfaces

    def draw(self, cr, sprite, x, y, width=None, height=None):
        """Paint a named sprite at (x, y); width/height crop it (sliders)."""
        sheet, (sx, sy, sw, sh) = _SPRITE_INDEX[sprite]
        self.draw_region(cr, sheet, sx, sy, width or sw, height or sh, x, y)

    def draw_region(self, cr, sheet, sx, sy, width, height, x, y):
        surface = self._surfaces.get(sheet)
        if surface is None:
            return
        cr.save()
        cr.rectangle(x, y, width, height)
        cr.clip()
        cr.set_source_surface(surface, x - sx, y - sy)
        cr.get_source().set_filter(cairo.FILTER_NEAREST)   # crisp at double size
        cr.paint()
        cr.restore()

    def draw_text(self, cr, text, x, y, max_chars=None):
        """Winamp's 5x6 bitmap font from text.bmp; unknown characters are blank."""
        for index, char in enumerate(text[:max_chars]):
            row, column = glyph_cell(char)
            self.draw_region(cr, 'TEXT', column * TEXT_CELL[0], row * TEXT_CELL[1],
                             TEXT_CELL[0], TEXT_CELL[1], x + index * TEXT_CELL[0], y)

    def digit_sprite(self, value):
        """'DIGIT_n' or the nums_ex variant; value may also be '-' or ' '."""
        extended = self.has('NUMS_EX')
        if value == '-':
            return 'MINUS_SIGN_EX' if extended else 'MINUS_SIGN'
        if value == ' ':
            return 'NO_MINUS_SIGN_EX' if extended else 'NO_MINUS_SIGN'
        return f'DIGIT_{value}_EX' if extended else f'DIGIT_{value}'


def glyph_cell(char):
    """text.bmp cell for a character; accented letters fall back to their base
    letter (é -> e) and anything else unknown to a space."""
    for candidate in (char.lower(), char.upper(), unicodedata.normalize('NFKD', char)[:1].lower()):
        if candidate in FONT_LOOKUP:
            return FONT_LOOKUP[candidate]
    return FONT_LOOKUP[' ']


def _read_files(path):
    if os.path.isdir(path):
        files = {}
        for root, _dirs, names in os.walk(path):
            for name in names:
                with open(os.path.join(root, name), 'rb') as stream:
                    files[os.path.relpath(os.path.join(root, name), path)] = stream.read()
        return files
    with zipfile.ZipFile(path) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()
                if not info.is_dir() and info.file_size < 8 * 1024 * 1024}


def _decode(data):
    loader = GdkPixbuf.PixbufLoader()
    try:
        loader.write(data)
        loader.close()
    except Exception:
        return None
    pixbuf = loader.get_pixbuf()
    return pixbuf.add_alpha(False, 0, 0, 0) if pixbuf is not None and not pixbuf.get_has_alpha() else pixbuf
