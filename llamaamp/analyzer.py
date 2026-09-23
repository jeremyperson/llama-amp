"""Spectrum analyzer: band mapping, meter ballistics and LED drawing."""
import math
import time
import cairo

from .constants import DISPLAY_BANDS, SPECTRUM_THRESHOLD


class AnalyzerState:
    """Time-based ballistics. All levels are normalized to the display height."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.targets = [0.0] * DISPLAY_BANDS
        self.levels = [0.0] * DISPLAY_BANDS
        self.peaks = [0.0] * DISPLAY_BANDS
        self.hold_until = [0.0] * DISPLAY_BANDS
        self.last_input = None
        self.last_tick = None

    def feed(self, magnitudes, sample_rate, now):
        if not magnitudes or not sample_rate or sample_rate <= 0:
            return
        nyquist = sample_rate / 2
        top = min(20000.0, nyquist)
        if top <= 40:
            return
        mapping_key = (len(magnitudes), sample_rate)
        if getattr(self, '_mapping_key', None) != mapping_key:
            step = nyquist / len(magnitudes)
            edges = [40 * (top / 40) ** (i / DISPLAY_BANDS)
                     for i in range(DISPLAY_BANDS + 1)]
            self._ranges = []
            for low, high in zip(edges, edges[1:]):
                start = min(len(magnitudes) - 1, max(0, int(low / step)))
                end = min(len(magnitudes), max(start + 1, int(math.ceil(high / step))))
                self._ranges.append((start, end))
            self._mapping_key = mapping_key
        floor = 10 ** (SPECTRUM_THRESHOLD / 10)
        powers = [floor if not math.isfinite(v) or v <= SPECTRUM_THRESHOLD
                  else 1.0 if v >= 0 else 10 ** (v * .1) for v in magnitudes]
        self.targets = [max(0.0, min(1.0,
                            (10 * math.log10(sum(powers[start:end]) / (end - start))
                             - SPECTRUM_THRESHOLD) / -SPECTRUM_THRESHOLD))
                        for start, end in self._ranges]
        self.last_input = now

    def tick(self, now, playing=True, speed=1.0):
        dt = 0 if self.last_tick is None else max(0, now - self.last_tick)
        self.last_tick = now
        active = playing and self.last_input is not None and now - self.last_input < .25
        changed = False
        for i, target in enumerate(self.targets):
            target = target if active else 0.0
            old, peak = self.levels[i], self.peaks[i]
            level = max(target, old - 1.8 * speed * dt)
            if level > peak or (level > 0 and target >= peak):
                peak = level
                self.hold_until[i] = now + .25
            else:
                fall_time = min(dt, max(0, now - self.hold_until[i]))
                peak = max(level, peak - .65 * speed * fall_time)
            changed |= abs(level - old) > 1e-6 or abs(peak - self.peaks[i]) > 1e-6
            self.levels[i], self.peaks[i] = level, peak
        return changed



class AnalyzerViewMixin:
    def _start_decay(self):
        if self._analyzer_id is None and self._analyzer_visible():
            self._analyzer_id = self.tasks.timeout_add(33, self.animate_equalizer)

    def animate_equalizer(self):
        if self._destroyed or not self._analyzer_visible():
            self._analyzer_id = None
            return False
        speed = {'slow': .5, 'normal': 1, 'fast': 2}[self.config['falloff']]
        if self.analyzer_state.tick(time.monotonic(), self.is_playing, speed):
            self.analyzer.queue_draw()
        alive = any(self.analyzer_state.levels) or any(self.analyzer_state.peaks)
        if not alive:
            self._analyzer_id = None
        return alive

    def _analyzer_visible(self):
        return (not self._destroyed and hasattr(self, 'analyzer') and self.analyzer.get_mapped()
                and self.config.get('visualization', True) and not self._windowshade
                and not getattr(self, '_iconified', False))

    def _update_analyzer_visibility(self):
        visible = self._analyzer_visible()
        if self.spectrum:
            self.spectrum.set_property('post-messages', visible)
        if not visible:
            if self._analyzer_id is not None:
                self.tasks.source_remove(self._analyzer_id)
                self._analyzer_id = None
            self.analyzer_state.reset()
            if hasattr(self, 'analyzer'):
                self.analyzer.queue_draw()
        return False

    def _draw_analyzer(self, widget, cr):
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        palette = self.config.get('palette') or self.theme['palette']
        scale = widget.get_scale_factor()
        key = (width, height, palette, self.theme['lcd'], scale)
        if getattr(self, '_meter_cache_key', None) != key:
            # Two tiny cached surfaces replace hundreds of Cairo fills per
            # frame. Only level clipping and peak positions change with audio.
            surfaces = []
            for lit in (False, True):
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width * scale, height * scale)
                surface.set_device_scale(scale, scale)
                ctx = cairo.Context(surface)
                self._cairo_color(ctx, self.theme['lcd'])
                ctx.paint()
                for y in range(height - 3, 0, -4):
                    fraction = (height - y) / height
                    if palette == 'classic':
                        color = '#ed644d' if fraction > .82 else '#eee85b' if fraction > .6 else '#65ed48'
                    else:
                        color = '#ffba45' if palette == 'amber' else '#52ef34'
                    self._cairo_color(ctx, color, 1 if lit else .08)
                    for index in range(DISPLAY_BANDS):
                        ctx.rectangle(int(index * width / DISPLAY_BANDS) + 1, y,
                                      max(1, int(width / DISPLAY_BANDS) - 2), 2)
                    ctx.fill()
                surfaces.append(surface)
            self._meter_cache_key, self._meter_surfaces = key, surfaces
        if not self.config.get('visualization', True):
            self._cairo_color(cr, self.theme['lcd'])
            cr.paint()
            return False
        background, lit = self._meter_surfaces
        cr.set_source_surface(background)
        cr.paint()
        step = width / DISPLAY_BANDS
        cr.save()
        for index, level in enumerate(self.analyzer_state.levels):
            if level > 0:
                top = height - int(level * height)
                cr.rectangle(int(index * step), top, int(step) + 1, height - top)
        cr.clip()
        cr.set_source_surface(lit)
        cr.paint()
        cr.restore()
        if self.config.get('peaks', True):
            self._cairo_color(cr, '#fff2d0' if palette == 'amber' else '#e8ffdc')
            for index, peak in enumerate(self.analyzer_state.peaks):
                if peak > 0:
                    cr.rectangle(int(index * step) + 1, max(0, int((1 - peak) * (height - 2))),
                                 max(1, int(step) - 2), 2)
            cr.fill()
        return False

