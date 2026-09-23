"""The classic equalizer window (275x116, or its 14px shade strip)."""
from gi.repository import Gtk

from ...constants import EQ_BANDS, PREAMP_DB_RANGE
from .base import SkinnedWindow
from ...i18n import _

SLIDER_W, SLIDER_H, THUMB = 14, 63, 11
PREAMP_X, BANDS_X, BAND_STEP, SLIDERS_Y = 21, 78, 18, 38
GRAPH = (86, 17, 113, 19)
SHADE_VOLUME, SHADE_BALANCE = (61, 4, 97, 6), (164, 4, 43, 6)


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


class ClassicEqWindow(SkinnedWindow):
    def __init__(self, app):
        super().__init__(app, _('Equalizer'))
        self.shaded = False

    def skin_size(self):
        return 275, 14 if self.shaded else 116

    def toggle_shade(self):
        self.shaded = not self.shaded
        self.apply_size()
        self.app._classic.relayout()

    def region_key(self):
        return 'equalizerws' if self.shaded else 'equalizer'

    # -- model: slider fractions (1 = top) ---------------------------------------
    def _value(self, index):
        """index -1 is the preamp; bands are 0..9. 0.5 is 0 dB for both."""
        return self.app.preamp_value if index < 0 else self.app.eq_values[index]

    def _set_value(self, index, fraction):
        app = self.app
        if index < 0:
            app.preamp_bar.set_value((fraction * 2 - 1) * PREAMP_DB_RANGE)
        else:
            app.eq_bars[index].set_value(app.eq_value_to_db(fraction))

    def _slider_x(self, index):
        return PREAMP_X if index < 0 else BANDS_X + index * BAND_STEP

    # -- controls ---------------------------------------------------------------
    def controls(self):
        if self.shaded:
            return [('close', (264, 3, 9, 9)), ('shade', (254, 3, 9, 9)),
                    ('volume', SHADE_VOLUME), ('balance', SHADE_BALANCE)]
        sliders = [(f'band{index}', (self._slider_x(index), SLIDERS_Y, SLIDER_W, SLIDER_H))
                   for index in range(-1, EQ_BANDS)]
        return [('close', (264, 3, 9, 9)), ('shade', (254, 3, 9, 9)), ('on', (14, 18, 26, 12)),
                ('auto', (40, 18, 32, 12)), ('presets', (217, 18, 44, 12))] + sliders

    def is_slider(self, name):
        return name.startswith('band') or name in ('volume', 'balance')

    def slide(self, name, x, y, final):
        if name.startswith('band'):
            fraction = 1 - clamp((y - SLIDERS_Y - THUMB / 2) / (SLIDER_H - THUMB))
            if abs(fraction - .5) < .03:
                fraction = .5                      # a small detent at 0 dB
            self._set_value(int(name[4:]), fraction)
        elif name == 'volume':
            left, _top, width, _height = SHADE_VOLUME
            self.app.volume_scale.set_value(clamp((x - left - 1.5) / (width - 3)))
        elif name == 'balance':
            left, _top, width, _height = SHADE_BALANCE
            value = clamp((x - left - 1.5) / (width - 3)) * 2 - 1
            self.app.balance_scale.set_value(0.0 if abs(value) < .1 else value)

    def click(self, name):
        app = self.app
        if name == 'close':
            app._classic.toggle_window('eq')
        elif name == 'shade':
            self.toggle_shade()
        elif name == 'on':
            app.toggle_eq_enabled()
        elif name == 'auto':
            app.show_drop_feedback(_("Per-track EQ presets aren't available"))
        elif name == 'presets':
            menu = Gtk.Menu()
            app._append_eq_preset_items(menu)
            menu.show_all()
            self._menu = menu      # keep referenced while open
            menu.popup_at_pointer(None)

    # -- painting ---------------------------------------------------------------
    def paint(self, cr, skin):
        if self.shaded:
            self._paint_shade(cr, skin)
            return
        skin.draw(cr, 'EQ_WINDOW_BACKGROUND', 0, 0)
        skin.draw(cr, 'EQ_TITLE_BAR_SELECTED' if self.focused else 'EQ_TITLE_BAR', 0, 0)
        if self.pressed == 'close':
            skin.draw(cr, 'EQ_CLOSE_BUTTON_ACTIVE', 264, 3)
        if self.pressed == 'shade':
            skin.draw(cr, 'EQ_MAXIMIZE_BUTTON_ACTIVE_FALLBACK', 254, 3)
        on = 'EQ_ON_BUTTON' + ('_SELECTED' if self.app.eq_enabled else '') + ('_DEPRESSED' if self.pressed == 'on' else '')
        skin.draw(cr, on, 14, 18)
        skin.draw(cr, 'EQ_AUTO_BUTTON' + ('_DEPRESSED' if self.pressed == 'auto' else ''), 40, 18)
        skin.draw(cr, 'EQ_PRESETS_BUTTON_SELECTED' if self.pressed == 'presets' else 'EQ_PRESETS_BUTTON', 217, 18)
        self._paint_graph(cr, skin)
        for index in range(-1, EQ_BANDS):
            self._paint_slider(cr, skin, index)

    def _paint_slider(self, cr, skin, index):
        x, fraction = self._slider_x(index), self._value(index)
        frame = round(fraction * 27)
        skin.draw_region(cr, 'EQMAIN', 13 + (frame % 14) * 15, 164 + (frame // 14) * 65,
                         SLIDER_W, SLIDER_H, x, SLIDERS_Y)
        thumb = 'EQ_SLIDER_THUMB_SELECTED' if self.dragging == f'band{index}' else 'EQ_SLIDER_THUMB'
        skin.draw(cr, thumb, x + 1, SLIDERS_Y + round((1 - fraction) * (SLIDER_H - THUMB)))

    def _paint_graph(self, cr, skin):
        x, y, width, height = GRAPH
        skin.draw(cr, 'EQ_GRAPH_BACKGROUND', x, y)
        preamp_y = y + round((1 - self.app.preamp_value) * (height - 1))
        skin.draw(cr, 'EQ_PREAMP_LINE', x, preamp_y)
        values = self.app.eq_values
        previous = None
        for column in range(width):
            # Linear interpolation between band centers across the graph
            position = column / (width - 1) * (EQ_BANDS - 1)
            low = int(position)
            high = min(EQ_BANDS - 1, low + 1)
            value = values[low] + (values[high] - values[low]) * (position - low)
            row = round((1 - value) * (height - 1))
            top, bottom = (row, row) if previous is None else (min(row, previous), max(row, previous))
            for line in range(top, bottom + 1):
                # The skin's color column says how to paint each height
                skin.draw_region(cr, 'EQMAIN', 115, 294 + line, 1, 1, x + column, y + line)
            previous = row

    def _paint_shade(self, cr, skin):
        skin.draw(cr, 'EQ_SHADE_BACKGROUND_SELECTED' if self.focused else 'EQ_SHADE_BACKGROUND', 0, 0)
        skin.draw(cr, 'EQ_SHADE_CLOSE_BUTTON_ACTIVE' if self.pressed == 'close' else 'EQ_SHADE_CLOSE_BUTTON', 264, 3)
        skin.draw(cr, 'EQ_MAXIMIZE_BUTTON_ACTIVE', 254, 3)
        app = self.app
        for (left, top, width, _h), fraction, sprite in (
                (SHADE_VOLUME, app.volume, 'EQ_SHADE_VOLUME_SLIDER'),
                (SHADE_BALANCE, (app.balance + 1) / 2, 'EQ_SHADE_BALANCE_SLIDER')):
            part = '_LEFT' if fraction < .33 else '_RIGHT' if fraction > .66 else '_CENTER'
            skin.draw(cr, sprite + part, left + round(clamp(fraction) * (width - 3)), top)
