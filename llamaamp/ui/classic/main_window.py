"""The classic main window (275x116, or the 14px windowshade strip)."""
import time

from gi.repository import Gst

from ...constants import APP_NAME, DISPLAY_BANDS, REPEAT_OFF, SHUFFLE_OFF
from .base import SkinnedWindow

SHADE_HEIGHT = 14
TRANSPORT = {'previous': (16, 88, 23, 18), 'play': (39, 88, 23, 18), 'pause': (62, 88, 23, 18),
             'stop': (85, 88, 23, 18), 'next': (108, 88, 22, 18), 'eject': (136, 89, 22, 16)}
SHADE_TRANSPORT = {'previous': (169, 2, 7, 10), 'play': (176, 2, 10, 10), 'pause': (186, 2, 9, 10),
                   'stop': (195, 2, 9, 10), 'next': (204, 2, 10, 10), 'eject': (215, 2, 10, 10)}
TITLE_BUTTONS = {'options': (6, 3, 9, 9), 'minimize': (244, 3, 9, 9), 'shade': (254, 3, 9, 9),
                 'close': (264, 3, 9, 9)}
VOLUME, BALANCE, POSITION = (107, 57, 68, 13), (177, 57, 38, 13), (16, 72, 248, 10)
SHADE_POSITION = (226, 4, 17, 7)
MARQUEE_CHARS = 31
MARQUEE_STEP_S = .15
BARS = 19


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


class ClassicMainWindow(SkinnedWindow):
    def __init__(self, app):
        super().__init__(app, APP_NAME)
        self.shaded = False
        self.drag_value = None       # slider fraction shown while dragging

    def skin_size(self):
        return 275, SHADE_HEIGHT if self.shaded else 116

    def toggle_shade(self):
        self.shaded = not self.shaded
        self.apply_size()
        self.app._classic.relayout()

    def region_key(self):
        return 'windowshade' if self.shaded else 'normal'

    # -- controls -------------------------------------------------------------
    def controls(self):
        if self.shaded:
            return [(name, rect) for name, rect in TITLE_BUTTONS.items()] + \
                   [(name, rect) for name, rect in SHADE_TRANSPORT.items()] + [('position', SHADE_POSITION)]
        return [(name, rect) for name, rect in TITLE_BUTTONS.items()] + [
            ('time', (39, 26, 59, 13)), ('vis', (24, 43, 76, 16)), ('volume', VOLUME),
            ('balance', BALANCE), ('eq', (219, 58, 23, 12)), ('pl', (242, 58, 23, 12)),
            ('position', POSITION), ('shuffle', (164, 89, 47, 15)), ('repeat', (210, 89, 28, 15)),
        ] + list(TRANSPORT.items())

    def is_slider(self, name):
        return name in ('volume', 'balance') or (name == 'position' and self._seekable())

    def _seekable(self):
        app = self.app
        return (app.duration > 0 and app.current_song is not None
                and not app._is_stream_url(app.current_song) and app.playback_state != 'Stopped')

    def slide(self, name, x, y, final):
        app = self.app
        if name == 'volume':
            app.volume_scale.set_value(clamp((x - VOLUME[0] - 7) / (VOLUME[2] - 14)))
        elif name == 'balance':
            value = clamp((x - BALANCE[0] - 7) / (BALANCE[2] - 14)) * 2 - 1
            app.balance_scale.set_value(0.0 if abs(value) < .08 else value)   # snaps to center
        elif name == 'position':
            left, _top, width, _height = SHADE_POSITION if self.shaded else POSITION
            thumb = 3 if self.shaded else 29
            fraction = clamp((x - left - thumb / 2) / (width - thumb))
            self.drag_value = None if final else fraction
            if final and self._seekable():
                app._seek_ns(int(fraction * app.duration))

    def click(self, name):
        app, classic = self.app, self.app._classic
        actions = {
            'previous': lambda: app.previous_song(None), 'play': app.play_from_start,
            'pause': app.pause_toggle, 'stop': lambda: app.stop_song(None),
            'next': lambda: app.next_song(None), 'eject': lambda: app.add_files(None),
            'shuffle': lambda: app.toggle_shuffle(None), 'repeat': lambda: app.toggle_repeat(None),
            'time': app._toggle_time_mode, 'vis': app._cycle_visualization,
            'eq': lambda: classic.toggle_window('eq'), 'pl': lambda: classic.toggle_window('playlist'),
            'options': lambda: app.show_classic_menu(None), 'minimize': self.iconify,
            'shade': self.toggle_shade, 'close': app.destroy,
        }
        action = actions.get(name)
        if action is not None:
            action()

    # -- painting -------------------------------------------------------------
    def paint(self, cr, skin):
        if self.shaded:
            self._paint_shade(cr, skin)
            return
        skin.draw(cr, 'MAIN_WINDOW_BACKGROUND', 0, 0)
        skin.draw(cr, 'MAIN_TITLE_BAR_SELECTED' if self.focused else 'MAIN_TITLE_BAR', 0, 0)
        self._paint_title_buttons(cr, skin, 'MAIN_SHADE_BUTTON')
        skin.draw(cr, 'MAIN_CLUTTER_BAR_BACKGROUND', 10, 22)
        app = self.app
        state = app.playback_state
        skin.draw(cr, {'Playing': 'MAIN_PLAYING_INDICATOR', 'Paused': 'MAIN_PAUSED_INDICATOR'}.get(
            state, 'MAIN_STOPPED_INDICATOR'), 26, 28)
        if state != 'Stopped':
            self._paint_time(cr, skin)
            self._paint_media_info(cr, skin)
        self._paint_vis(cr, skin, 24, 43, 76, 16, BARS)
        skin.draw_text(cr, self._marquee_text(), 111, 27, MARQUEE_CHARS)
        self._paint_slider(cr, skin, 'VOLUME', 0, VOLUME, app.volume, 'MAIN_VOLUME_THUMB')
        self._paint_slider(cr, skin, 'BALANCE', 9, BALANCE, (app.balance + 1) / 2, 'MAIN_BALANCE_THUMB',
                           frame=abs(app.balance))
        classic = app._classic
        for name, window, (x, y) in (('MAIN_EQ_BUTTON', classic.eq, (219, 58)),
                                     ('MAIN_PLAYLIST_BUTTON', classic.playlist, (242, 58))):
            pressed = self.pressed == ('eq' if 'EQ' in name else 'pl')
            shown = window is not None and window.get_visible()
            skin.draw(cr, name + ('_DEPRESSED' if pressed else '') + ('_SELECTED' if shown else ''), x, y)
        skin.draw(cr, 'MAIN_POSITION_SLIDER_BACKGROUND', *POSITION[:2])
        fraction = self._position_fraction()
        if fraction is not None:
            thumb = 'MAIN_POSITION_SLIDER_THUMB_SELECTED' if self.dragging == 'position' else 'MAIN_POSITION_SLIDER_THUMB'
            skin.draw(cr, thumb, POSITION[0] + round(fraction * (POSITION[2] - 29)), POSITION[1])
        for name, (x, y, _w, _h) in TRANSPORT.items():
            sprite = f'MAIN_{name.upper()}_BUTTON'
            skin.draw(cr, sprite + ('_ACTIVE' if self.pressed == name else ''), x, y)
        for name, x, on in (('SHUFFLE', 164, app.shuffle != SHUFFLE_OFF), ('REPEAT', 210, app.repeat_mode != REPEAT_OFF)):
            sprite = f'MAIN_{name}_BUTTON' + ('_SELECTED' if on else '') + \
                ('_DEPRESSED' if self.pressed == name.lower() else '')
            skin.draw(cr, sprite, x, 89)

    def _paint_title_buttons(self, cr, skin, shade_sprite):
        for name, (x, y, _w, _h) in TITLE_BUTTONS.items():
            sprite = shade_sprite if name == 'shade' else f'MAIN_{name.upper()}_BUTTON'
            skin.draw(cr, sprite + ('_DEPRESSED' if self.pressed == name else ''), x, y)

    def _paint_shade(self, cr, skin):
        skin.draw(cr, 'MAIN_SHADE_BACKGROUND_SELECTED' if self.focused else 'MAIN_SHADE_BACKGROUND', 0, 0)
        self._paint_title_buttons(cr, skin, 'MAIN_SHADE_BUTTON_SELECTED')
        if self.app.playback_state != 'Stopped':
            skin.draw_text(cr, self._clock_text(), 127, 4)
        self._paint_vis(cr, skin, 79, 5, 38, 5, 9)
        skin.draw(cr, 'MAIN_SHADE_POSITION_BACKGROUND', *SHADE_POSITION[:2])
        fraction = self._position_fraction()
        if fraction is not None:
            sprite = ('MAIN_SHADE_POSITION_THUMB_LEFT' if fraction < .1 else
                      'MAIN_SHADE_POSITION_THUMB_RIGHT' if fraction > .9 else 'MAIN_SHADE_POSITION_THUMB')
            skin.draw(cr, sprite, SHADE_POSITION[0] + round(fraction * (SHADE_POSITION[2] - 3)), SHADE_POSITION[1])

    def _position_fraction(self):
        if self.drag_value is not None:
            return self.drag_value
        if not self._seekable():
            return None
        return clamp(self.app._current_position_ns() / self.app.duration)

    def _clock_values(self):
        app = self.app
        position = app._current_position_ns()
        remaining = app._time_remaining and app.duration > 0
        seconds = max(0, (app.duration - position if remaining else position) // Gst.SECOND)
        return remaining, min(99, seconds // 60), seconds % 60

    def _clock_text(self):
        remaining, minutes, seconds = self._clock_values()
        return f"{'-' if remaining else ''}{minutes:02d}:{seconds:02d}"

    def _paint_time(self, cr, skin):
        remaining, minutes, seconds = self._clock_values()
        if skin.has('NUMS_EX'):
            skin.draw(cr, skin.digit_sprite('-' if remaining else ' '), 39, 26)
        elif remaining:
            skin.draw(cr, 'MINUS_SIGN', 38, 32)
        for value, x in ((minutes // 10, 48), (minutes % 10, 60), (seconds // 10, 78), (seconds % 10, 90)):
            skin.draw(cr, skin.digit_sprite(value), x, 26)

    def _paint_media_info(self, cr, skin):
        props = self.app.audio_properties or {}
        bitrate, rate, channels = props.get('bitrate') or 0, props.get('sample_rate') or 0, props.get('channels') or 0
        if bitrate:
            # Three characters: Winamp shows 1411 kbps as "14H" (hundreds)
            skin.draw_text(cr, f'{bitrate:>3}' if bitrate < 1000 else f'{min(99, bitrate // 100)}H', 111, 43)
        if rate:
            skin.draw_text(cr, f'{round(rate / 1000):>2}', 156, 43)
        skin.draw(cr, 'MAIN_MONO_SELECTED' if channels == 1 else 'MAIN_MONO', 212, 41)
        skin.draw(cr, 'MAIN_STEREO_SELECTED' if channels >= 2 else 'MAIN_STEREO', 239, 41)

    def _paint_slider(self, cr, skin, sheet, sheet_x, rect, fraction, thumb, frame=None):
        x, y, width, height = rect
        value = clamp(fraction if frame is None else frame)
        skin.draw_region(cr, sheet, sheet_x, round(value * 27) * 15, width, height, x, y)
        pressed = self.dragging == ('volume' if sheet == 'VOLUME' else 'balance')
        active = thumb + ('_ACTIVE' if sheet == 'BALANCE' else '_SELECTED')
        skin.draw(cr, active if pressed else thumb, x + round(clamp(fraction) * (width - 14)), y + 1)

    def _marquee_text(self):
        app = self.app
        title = getattr(app, '_title_full', '') or ''
        if not app.current_song or not title:
            return APP_NAME
        text = f'{app.current_index + 1}. {title}'
        if app.duration > 0:
            seconds = app.duration // Gst.SECOND
            text += f' ({seconds // 60}:{seconds % 60:02d})'
        if len(text) <= MARQUEE_CHARS:
            return text
        loop = text + '  ***  '
        offset = int(time.monotonic() / MARQUEE_STEP_S) % len(loop)
        return (loop[offset:] + loop)[:MARQUEE_CHARS]

    def _paint_vis(self, cr, skin, x, y, width, height, bars):
        colors = skin.viscolors
        def rgb(index):
            r, g, b = colors[index]
            cr.set_source_rgb(r / 255, g / 255, b / 255)
        rgb(0)
        cr.rectangle(x, y, width, height)
        cr.fill()
        rgb(1)
        for dy in range(1, height, 2):
            for dx in range(1, width, 2):
                cr.rectangle(x + dx, y + dy, 1, 1)
        cr.fill()
        app = self.app
        if not app.config.get('visualization', True):
            return
        if app.config['vis_mode'] == 'scope':
            points = app.scope_state.points
            if points:
                middle = (height - 1) / 2
                for column in range(width):
                    value = points[min(len(points) - 1, column * len(points) // width)]
                    row = round(middle - value * middle)
                    rgb(18 + min(4, int(abs(value) * 5)))
                    cr.rectangle(x + column, y + clamp(row, 0, height - 1), 1, 1)
                    cr.fill()
            return
        levels, peaks = app.analyzer_state.levels, app.analyzer_state.peaks
        bar_width = max(1, width // bars - 1)
        for bar in range(bars):
            band = min(DISPLAY_BANDS - 1, bar * DISPLAY_BANDS // bars)
            top = height - round(levels[band] * height)
            left = x + bar * (bar_width + 1)
            for row in range(top, height):
                # Viscolor 2 is the top of the meter, 17 the bottom
                rgb(2 + min(15, row * 16 // height))
                cr.rectangle(left, y + row, bar_width, 1)
                cr.fill()
            if app.config.get('peaks', True) and peaks[band] > 0:
                rgb(23)
                cr.rectangle(left, y + clamp(height - round(peaks[band] * height), 0, height - 1), bar_width, 1)
                cr.fill()

    def vis_visible(self):
        return self.get_visible() and not self.iconified
