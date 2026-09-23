"""Crossfade: overlap the end of one track with the start of the next.

playbin plays one stream at a time, so a crossfade runs a second playbin.
At the fade point the new player becomes self.player and loads the next
entry through the normal path (UI, queue, MPRIS follow it); the outgoing
player keeps playing unobserved while the two volumes cross, then stops.
The sound server mixes the two streams, so crossfade needs mixer output:
with exclusive ALSA output (and for internet radio) gapless is used instead.
"""
import math
import time

from gi.repository import Gst

from .constants import REPEAT_ONE

CROSSFADE_STEP_MS = 30
CROSSFADE_MIN_NS = 300 * Gst.MSECOND     # shorter than this: just let the track end


def crossfade_gains(progress):
    """Equal-power (outgoing, incoming) gains for fade progress 0..1."""
    progress = max(0.0, min(1.0, progress))
    if progress >= 1:
        return 0.0, 1.0
    return math.cos(progress * math.pi / 2), math.sin(progress * math.pi / 2)


class CrossfadeMixin:
    def _set_crossfade(self, seconds):
        self.config['crossfade_s'] = seconds
        self._invalidate_next()      # the next transition changes kind
        self._prepare_next()
        self.schedule_save_config()

    def _crossfade_applies(self, next_path):
        """Whether the transition to next_path is crossfaded rather than gapless.
        Also read from the streaming thread (about-to-finish): attribute reads only."""
        current = self.current_song
        return (self.config.get('crossfade_s', 0) > 0 and not self.alsa_output
                and current is not None and next_path is not None
                and not self._is_stream_url(current) and not self._is_stream_url(next_path)
                and self.repeat_mode != REPEAT_ONE and not self._sleep_after_track)

    def _crossfade_tick(self, position):
        """From the position timer: start the fade once the remaining time fits."""
        if self._xfade is not None or self._loading or self.playback_state != 'Playing':
            return
        remaining = self.duration - position
        fade = self.config.get('crossfade_s', 0) * Gst.SECOND
        if not (CROSSFADE_MIN_NS <= remaining <= fade):
            return
        key = self.order.next_id(self._playable, self.shuffle, self.repeat_mode)
        if key is None or key not in self.entry_ids:
            return
        index = self.entry_ids.index(key)
        if self._crossfade_applies(self.playlist[index]):
            self._start_crossfade(index, remaining)

    def _new_player_like(self, template):
        player = Gst.ElementFactory.make('playbin', None)
        sink = template.get_property('audio-sink')
        if sink is not None:
            # Same kind of sink (tests use fakesink); the default is autoaudiosink
            clone = Gst.ElementFactory.make(sink.get_factory().get_name(), None)
            for name in ('sync', 'device'):
                if clone is not None and sink.find_property(name) and clone.find_property(name):
                    clone.set_property(name, sink.get_property(name))
            player.set_property('audio-sink', clone)
        return player

    def _start_crossfade(self, index, fade_ns):
        outgoing = self.player
        # Stop observing the outgoing player; it finishes its last seconds alone
        outgoing.get_bus().remove_signal_watch()
        self.player = self._new_player_like(outgoing)
        self._attach_filter()
        self.setup_bus()
        self.player.set_property('volume', 0.0)
        self.load_song(index)
        self._play_current()
        self._xfade = {'outgoing': outgoing, 'start': time.monotonic(),
                       'length': fade_ns / Gst.SECOND,
                       'timer': self.tasks.timeout_add(CROSSFADE_STEP_MS, self._crossfade_step)}

    def _crossfade_step(self):
        fade = self._xfade
        if fade is None:
            return False
        progress = (time.monotonic() - fade['start']) / fade['length']
        out_gain, in_gain = crossfade_gains(progress)
        fade['outgoing'].set_property('volume', self.volume * out_gain)
        self.player.set_property('volume', self.volume * in_gain)
        if progress >= 1:
            fade['timer'] = None
            self._finish_crossfade()
            return False
        return True

    def _finish_crossfade(self):
        """End any running fade now: stop the outgoing player and restore the
        incoming one to full volume. Safe to call when no fade is running."""
        fade, self._xfade = self._xfade, None
        if fade is None:
            return
        if fade['timer'] is not None:
            self.tasks.source_remove(fade['timer'])
        fade['outgoing'].set_state(Gst.State.NULL)
        self.player.set_property('volume', self.volume)
