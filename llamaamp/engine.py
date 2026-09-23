"""Audio engine: GStreamer pipeline, DSP chain, ALSA output, gapless handoff and transport."""
import os
import re
import time

from gi.repository import GLib, Gst

from .analyzer import decode_pcm
from .constants import (
    EQ_GAIN_MAX,
    EQ_GAIN_MIN,
    PREAMP_DB_RANGE,
    REPEAT_ALL,
    REPEAT_OFF,
    REPEAT_ONE,
    RG_MODES,
    SCOPE_INTERVAL_S,
    SHUFFLE_OFF,
    SPECTRUM_BANDS,
    SPECTRUM_INTERVAL_NS,
    SPECTRUM_THRESHOLD,
    _MAG_LIST_RE,
    _MAG_ONE_RE,
)
from .i18n import _


class EngineMixin:
    def build_audio_filter(self, analyzer_only=False):
        """Build (and return) a playbin audio-filter bin.

        Full chain: audioconvert -> equalizer -> panorama -> spectrum -> audioconvert.
        analyzer_only (Direct Mode): audioconvert -> spectrum. The spectrum
        element is passthrough analysis — it never modifies samples — and its
        audioconvert stays passthrough for every common decoder format (at most
        it does lossless integer widening for exotic packed layouts), so the
        bars stay alive while playback remains bit-exact.

        Returns None if elements are missing (degrade to no filter)."""
        bin_ = Gst.Bin.new("audio-filter-bin")
        conv_in = Gst.ElementFactory.make("audioconvert", "conv_in")
        self.spectrum = Gst.ElementFactory.make("spectrum", "spectrum")

        if analyzer_only:
            self.equalizer = None
            self.panorama = None
            self.preamp = None
            self.rgvolume = None
            chain = (conv_in, self.spectrum)
        else:
            self.preamp = Gst.ElementFactory.make("volume", "preamp")
            self.equalizer = Gst.ElementFactory.make("equalizer-10bands", "equalizer")
            self.panorama = Gst.ElementFactory.make("audiopanorama", "panorama")
            conv_out = Gst.ElementFactory.make("audioconvert", "conv_out")
            chain = (conv_in, self.preamp, self.equalizer,
                     self.panorama, self.spectrum, conv_out)
            # ReplayGain normalization sits ahead of the user preamp so the two
            # compose; the limiter soft-clips only RG-boosted signal. Elements
            # exist only when enabled — 'off' keeps the chain byte-identical.
            if self.replaygain != 'off':
                self.rgvolume = Gst.ElementFactory.make("rgvolume", "rgvolume")
                rglimiter = Gst.ElementFactory.make("rglimiter", "rglimiter")
                if self.rgvolume is not None and rglimiter is not None:
                    self.rgvolume.set_property("album-mode",
                                               self.replaygain == 'album')
                    self.rgvolume.set_property("pre-amp", 0.0)
                    self.rgvolume.set_property("fallback-gain", 0.0)
                    chain = (conv_in, self.rgvolume, rglimiter, self.preamp,
                             self.equalizer, self.panorama, self.spectrum, conv_out)
                else:
                    self.log_debug("rgvolume/rglimiter unavailable; ReplayGain off")
                    self.rgvolume = None
            else:
                self.rgvolume = None

        if bin_ is None or not all(chain):
            self.log_debug("DSP elements unavailable; playing without EQ/balance/spectrum")
            self.equalizer = self.panorama = self.spectrum = self.preamp = None
            return None

        self.spectrum.set_property("bands", SPECTRUM_BANDS)
        self.spectrum.set_property("interval", SPECTRUM_INTERVAL_NS)
        self.spectrum.set_property("threshold", SPECTRUM_THRESHOLD)
        self.spectrum.set_property("post-messages", True)
        self.spectrum.set_property("message-magnitude", True)
        if self.panorama is not None:
            try:
                self.panorama.set_property("method", 0)  # psychoacoustic (preserve loudness)
            except Exception:
                pass

        for e in chain:
            bin_.add(e)
        for a, b in zip(chain, chain[1:]):
            if not a.link(b):
                self.log_debug(f"DSP link failed: {a.get_name()} -> {b.get_name()}; "
                               "playing without EQ/balance/spectrum")
                self.equalizer = self.panorama = self.spectrum = self.preamp = None
                return None

        # Read-only tap for the oscilloscope: never modifies samples, so Direct
        # Mode stays bit-exact.
        self.spectrum.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, self._scope_probe)
        bin_.add_pad(Gst.GhostPad.new("sink", conv_in.get_static_pad("sink")))
        bin_.add_pad(Gst.GhostPad.new("src", chain[-1].get_static_pad("src")))
        return bin_

    def _scope_probe(self, pad, info):
        # Streaming thread: decode at most every SCOPE_INTERVAL_S, only while shown
        now = time.monotonic()
        if not self._scope_active or now - self._scope_last < SCOPE_INTERVAL_S:
            return Gst.PadProbeReturn.OK
        caps, buffer = pad.get_current_caps(), info.get_buffer()
        if caps is None or buffer is None or not caps.get_size():
            return Gst.PadProbeReturn.OK
        structure = caps.get_structure(0)
        ok, channels = structure.get_int('channels')
        if structure.get_string('layout') != 'interleaved' or not ok or channels < 1:
            return Gst.PadProbeReturn.OK
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.PadProbeReturn.OK
        try:
            data = mapped.data
        finally:
            buffer.unmap(mapped)
        decoded = decode_pcm(data, structure.get_string('format'))
        if decoded is not None:
            self.scope_state.feed_samples(decoded[0], channels, decoded[1], now)
        self._scope_last = now
        return Gst.PadProbeReturn.OK

    def _attach_filter(self):
        """Attach the filter bin matching the current direct_mode (NULL state only)."""
        bin_ = self.build_audio_filter(analyzer_only=self.direct_mode)
        self.player.set_property("audio-filter", bin_)
        self._update_analyzer_visibility()
        if not self.direct_mode and self.equalizer is not None:
            self.apply_all_eq()
            if self.panorama is not None:
                self.panorama.set_property("panorama",
                                           max(-1.0, min(1.0, self.balance)))

    def _rebuild_pipeline(self):
        """Rebuild the audio-filter chain and resume the current track at the
        same position. playbin's audio-filter only changes in the NULL state,
        so this is the shared machinery for Direct Mode and ReplayGain toggles."""
        self._finish_crossfade()
        self._invalidate_next()
        was_playing = self.is_playing
        resume_ns = self._current_position_ns()

        self._loading = True  # freeze about-to-finish during the rebuild
        try:
            self.player.set_state(Gst.State.NULL)
            self._attach_filter()

            if self.current_song:
                self.load_song(self.current_index)
                if resume_ns > 0 and not self._is_stream_url(self.current_song):
                    self._pending_seek_ns = resume_ns
                if was_playing:
                    self.player.set_state(Gst.State.PLAYING)
                    self._sync_play_ui(True)
                else:
                    self.player.set_state(Gst.State.PAUSED)
        finally:
            self._loading = False

    def toggle_direct_mode(self, *_args):
        """Toggle bit-transparent playback: swap between the full DSP chain and
        the analyzer-only tap (spectrum keeps running either way)."""
        self.direct_mode = not self.direct_mode
        self._rebuild_pipeline()
        self.schedule_save_config()
        if not self.direct_mode:
            message = _("Direct mode OFF — DSP active")
        elif self.replaygain != 'off':
            message = _("Direct mode ON — EQ/balance bypassed, analyzer on (ReplayGain inactive in this mode)")
        else:
            message = _("Direct mode ON — EQ/balance bypassed, analyzer on")
        self.show_drop_feedback(message)

    def set_replaygain(self, mode):
        """Switch ReplayGain mode. track<->album is a live property flip;
        off<->on rebuilds the filter chain (audio-filter swaps only at NULL)."""
        if mode not in RG_MODES or mode == self.replaygain:
            return
        prev = self.replaygain
        self.replaygain = mode
        if 'off' not in (prev, mode) and self.rgvolume is not None:
            self.rgvolume.set_property("album-mode", mode == 'album')
        elif not self.direct_mode:
            self._rebuild_pipeline()
        self.schedule_save_config()
        if self.direct_mode and mode != 'off':
            self.show_drop_feedback(
                _('ReplayGain {mode} armed — no effect while Direct Mode is ON').format(mode=mode))
        else:
            self.show_drop_feedback(_('ReplayGain: {mode}').format(mode=mode))

    def _list_alsa_devices(self):
        """[(hw:X,Y, 'CardName — PcmName'), ...] for all playback PCMs."""
        cards = {}
        try:
            with open('/proc/asound/cards') as f:
                for line in f:
                    m = re.match(r'\s*(\d+)\s+\[', line)
                    if m and ':' in line:
                        cards[int(m.group(1))] = line.split(':', 1)[1].strip().split(' at ')[0]
        except Exception:
            pass
        devices = []
        try:
            with open('/proc/asound/pcm') as f:
                for line in f:
                    if 'playback' not in line.lower():
                        continue
                    parts = line.split(':')
                    c, d = parts[0].strip().split('-')
                    name = parts[2].strip() if len(parts) > 2 else parts[1].strip()
                    card_name = cards.get(int(c), f"card {int(c)}")
                    devices.append((f"hw:{int(c)},{int(d)}", f"{card_name} — {name}"))
        except Exception as e:
            self.log_debug(f"alsa device list failed: {e}")
        return devices

    def _select_alsa_device(self, device):
        """Persist an ALSA device choice (None = auto) and reacquire if active."""
        if self.config.get('alsa_device') == device:
            return
        self.config['alsa_device'] = device
        self.schedule_save_config()
        if self.alsa_output:
            self._alsa_reacquire()
        else:
            self.show_drop_feedback(_("Device saved — enable ALSA Output to use it"))

    def _detect_alsa_device(self):
        """Pick the hw: device for direct DAC output. Config 'alsa_device'
        overrides; otherwise prefer the first *Analog* playback PCM (GPU HDMI
        ports often enumerate as card 0 ahead of the analog codec), falling
        back to the first playback PCM of any kind."""
        dev = self.config.get('alsa_device')
        if isinstance(dev, str) and dev:
            return dev
        first = None
        try:
            with open('/proc/asound/pcm') as f:
                for line in f:
                    if 'playback' not in line.lower():
                        continue
                    card_dev = line.split(':', 1)[0].strip()
                    c, d = card_dev.split('-')
                    hw = f"hw:{int(c)},{int(d)}"
                    if first is None:
                        first = hw
                    if 'analog' in line.lower():
                        return hw
        except Exception as e:
            self.log_debug(f"alsa detect failed: {e}")
        return first or "hw:0,0"

    def _make_alsa_sink(self):
        sink = Gst.ElementFactory.make("alsasink", "alsa_direct")
        if sink is None:
            return None
        self._alsa_device = self._detect_alsa_device()
        sink.set_property("device", self._alsa_device)
        return sink

    def toggle_alsa_output(self, *_args):
        """Toggle direct ALSA output: hand samples straight to the DAC's hw:
        device, skipping the PulseAudio/PipeWire mixer (and its resampling).
        The sound server only releases the device a few seconds after all
        streams stop (WirePlumber suspend-on-idle), so enabling retries for
        ~10s before falling back to the mixer. While active, the device is
        exclusive to Llama Amp."""
        if self.alsa_output:
            # Turning OFF: hand the device back to the mixer (synchronous)
            self._finish_crossfade()
            self.alsa_output = False
            was_playing = self.is_playing
            resume_ns = self._current_position_ns()
            self._switching_output = True
            try:
                self.player.set_state(Gst.State.NULL)
                self.player.set_property("audio-sink", None)
                if self.current_song:
                    self.load_song(self.current_index)
                    if resume_ns > 0:
                        self._pending_seek_ns = resume_ns
                    self.player.set_state(
                        Gst.State.PLAYING if was_playing else Gst.State.PAUSED)
                    if was_playing:
                        self._sync_play_ui(True)
            finally:
                self._switching_output = False
            self.schedule_save_config()
            self.show_drop_feedback(_("Output: system mixer"))
            return

        # Turning ON: async retry while the server releases the device
        self._alsa_reacquire()

    def _alsa_reacquire(self):
        """(Re)build the alsasink from config and start the async acquire flow.
        Used by the ALSA toggle and by device-picker changes."""
        self._invalidate_next()
        sink = self._make_alsa_sink()
        if sink is None:
            self.alsa_output = False
            self.show_drop_feedback(_("alsasink not available"))
            return
        self.alsa_output = True
        self._alsa_ctx = {
            'was_playing': self.is_playing,
            'resume_ns': self._current_position_ns(),
            'tries': 0,
        }
        self._switching_output = True
        self.player.set_state(Gst.State.NULL)
        self.player.set_property("audio-sink", sink)
        self.show_drop_feedback(_('Acquiring {alsa_device}…').format(alsa_device=self._alsa_device))
        self.tasks.timeout_add(400, self._alsa_try_start)

    def _alsa_try_start(self):
        ctx = self._alsa_ctx
        ctx['tries'] += 1
        target = Gst.State.PLAYING if ctx['was_playing'] else Gst.State.PAUSED

        if self.current_song:
            self.load_song(self.current_index)
            if ctx['resume_ns'] > 0:
                self._pending_seek_ns = ctx['resume_ns']
            self.player.set_state(target)
            result = self.player.get_state(2 * Gst.SECOND)[0]
            if result == Gst.StateChangeReturn.FAILURE:
                self.player.set_state(Gst.State.NULL)
                if ctx['tries'] < 5:
                    self.show_drop_feedback(
                        _('DAC busy — waiting for release ({attempt}/5)…').format(attempt=ctx['tries']))
                    self.tasks.timeout_add(1800, self._alsa_try_start)
                    return False
                # Give up: revert to the mixer
                dev = self._alsa_device
                self.alsa_output = False
                self.player.set_property("audio-sink", None)
                self.load_song(self.current_index)
                if ctx['resume_ns'] > 0:
                    self._pending_seek_ns = ctx['resume_ns']
                self.player.set_state(target)
                if ctx['was_playing']:
                    self._sync_play_ui(True)
                self._switching_output = False
                self.schedule_save_config()
                self.show_drop_feedback(_('ALSA {dev} unavailable — using mixer').format(dev=dev))
                return False
            if ctx['was_playing']:
                self._sync_play_ui(True)

        self._switching_output = False
        self.schedule_save_config()
        self.show_drop_feedback(_('ALSA direct → {alsa_device}').format(alsa_device=self._alsa_device))
        return False

    def eq_value_to_db(self, v):
        """Map a 0..1 slider value to dB, with 0.5 = flat (0 dB)."""
        v = max(0.0, min(1.0, v))
        if v >= 0.5:
            return (v - 0.5) / 0.5 * EQ_GAIN_MAX
        return (0.5 - v) / 0.5 * EQ_GAIN_MIN  # EQ_GAIN_MIN is negative

    def db_to_eq_value(self, db):
        """Inverse of eq_value_to_db: map dB to the 0..1 bar value."""
        if db >= 0:
            return 0.5 + 0.5 * min(db, EQ_GAIN_MAX) / EQ_GAIN_MAX
        return 0.5 - 0.5 * max(db, EQ_GAIN_MIN) / EQ_GAIN_MIN

    def _apply_eq_band(self, index):
        if self.equalizer is None:
            return
        try:
            db = self.eq_value_to_db(self.eq_values[index]) if self.eq_enabled else 0.0
            self.equalizer.set_property(f"band{index}", db)
        except Exception as e:
            self.log_debug(f"EQ band {index} failed: {e}")

    def _apply_preamp(self):
        """Master gain ahead of the bands: 0.5 = unity, edges = ±PREAMP_DB_RANGE."""
        if self.preamp is None:
            return
        if self.eq_enabled:
            db = (self.preamp_value - 0.5) * 2 * PREAMP_DB_RANGE
            self.preamp.set_property("volume", 10 ** (db / 20))
        else:
            self.preamp.set_property("volume", 1.0)

    def apply_all_eq(self):
        for i in range(len(self.eq_values)):
            self._apply_eq_band(i)
        self._apply_preamp()

    def toggle_eq_enabled(self, *_args):
        """Winamp-style EQ ON/OFF: bypass bands + preamp by zeroing the live
        element properties — no pipeline rebuild, stored curve untouched."""
        self.eq_enabled = not self.eq_enabled
        self.apply_all_eq()
        for bar in self.eq_bars:
            bar.queue_draw()
        if getattr(self, 'preamp_bar', None) is not None:
            self.preamp_bar.queue_draw()
        if getattr(self, 'eq_on_btn', None) is not None:
            ctx = self.eq_on_btn.get_style_context()
            (ctx.add_class if self.eq_enabled else ctx.remove_class)('active')
        self.schedule_save_config()
        self.show_drop_feedback(_("EQ on") if self.eq_enabled else _("EQ off (bypassed)"))

    def on_balance_changed(self, scale):
        self.balance = scale.get_value()
        if self.panorama is not None:
            try:
                self.panorama.set_property("panorama", max(-1.0, min(1.0, self.balance)))
            except Exception as e:
                self.log_debug(f"balance failed: {e}")
        self.schedule_save_config()

    def _parse_magnitudes(self, structure):
        """Read the spectrum 'magnitude' field as a list of dB floats.

        PyGObject can't convert the GstValueList via get_value() (raises
        'unknown type GstValueList'), so fall back to parsing the structure text."""
        try:
            val = structure.get_value("magnitude")
            if isinstance(val, (list, tuple)):
                return [float(x) for x in val]
        except Exception:
            pass
        try:
            text = structure.to_string()
            m = _MAG_LIST_RE.search(text)
            if m:
                return [float(x) for x in m.group(1).split(",")]
            m = _MAG_ONE_RE.search(text)
            if m:
                return [float(m.group(1))]
        except Exception:
            pass
        return None

    def on_spectrum_message(self, structure):
        if not self._analyzer_visible():
            return
        if self.config['vis_mode'] == 'scope':
            self._start_decay()
            return
        mags = self._parse_magnitudes(structure)
        if not mags:
            return
        rate = self.audio_properties.get('sample_rate') or 0
        if self.spectrum:
            caps = self.spectrum.get_static_pad('sink').get_current_caps()
            if caps and caps.get_size():
                ok, negotiated = caps.get_structure(0).get_int('rate')
                if ok:
                    rate = negotiated
        self.analyzer_state.feed(mags, rate, time.monotonic())
        self._start_decay()

    def setup_bus(self):
        self._error_streak = 0
        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self.on_bus_error)
        bus.connect("message::eos", self.on_bus_eos)
        bus.connect("message::state-changed", self.on_bus_state_changed)
        bus.connect("message::tag", self.on_bus_tag)
        bus.connect("message::element", self.on_bus_element)
        # Gapless: commit the prerolled next track when its stream starts.
        # (Bus signal-watch handlers run on the GLib main loop.)
        bus.connect("message::stream-start", self.on_bus_stream_start)
        bus.connect("message::buffering", self.on_bus_buffering)
        self.player.connect("about-to-finish", self._on_about_to_finish)

    def on_bus_error(self, bus, message):
        # Errors raised while deliberately swapping output sinks are handled by
        # the toggle's own revert logic — don't skip tracks over them.
        if getattr(self, '_switching_output', False):
            err, _dbg = message.parse_error()
            self.log_debug(f"suppressed during output switch: {err}")
            return
        # ALSA-direct is enabled but the DAC was busy (e.g. another app had it
        # at startup): re-run the acquire/retry/fallback flow instead of
        # treating it as a broken track.
        if self.alsa_output and self._alsa_recovers < 2:
            self._alsa_recovers += 1
            err, _dbg = message.parse_error()
            self.log_debug(f"ALSA error, attempting recovery: {err}")
            self._alsa_ctx = {
                'was_playing': self.is_playing,
                'resume_ns': self._current_position_ns(),
                'tries': 0,
            }
            self._switching_output = True
            self.player.set_state(Gst.State.NULL)
            self.show_drop_feedback(_("DAC busy — waiting for release…"))
            self.tasks.timeout_add(1500, self._alsa_try_start)
            return
        err, dbg = message.parse_error()
        self.log_debug(f"GStreamer error: {err} ({dbg})")
        was_playing = self.is_playing
        self.order.failed.add(self.order.current)
        self._invalidate_next()
        self.player.set_state(Gst.State.NULL)
        self._sync_play_ui(False, stopped=True)
        self.info_label.set_text(f"⚠ {err.message}")
        # Skip past a bad track if we were playing, guarding against an all-bad loop.
        self._error_streak += 1
        if was_playing and self.playlist and self._error_streak < len(self.playlist):
            # after_error: never let REPEAT_ONE replay a broken track in a loop
            self.tasks.idle_add(lambda: (self.advance_track(auto=True, after_error=True), False)[1])

    def on_bus_eos(self, bus, message):
        # Only fires when the gapless handoff declined (repeat-one, sleep,
        # gapless off, end of playlist) — advance_track handles those cases.
        self.advance_track(auto=True)

    def _on_about_to_finish(self, playbin):
        # Streaming thread: only a protected, precomputed decision is accessed.
        if playbin is not self.player:
            return      # a crossfade's outgoing player: it just ends
        with self._gapless_lock:
            snapshot = self._next_snapshot
            if snapshot is not None and self._crossfade_applies(snapshot[1]):
                return  # the crossfade starts the next track instead
            # A pending seek would switch playbin to the queued URI (or stall).
            # After the seek the decoder drains again and re-emits this signal;
            # if that happens before the seek is cleared, EOS advances instead.
            if snapshot is None or self._gapless_next is not None or self._pending_seek_ns is not None:
                return
            self._gapless_next = snapshot
            self._next_snapshot = None
            playbin.set_property('uri', snapshot[2])

    def on_bus_buffering(self, bus, message):
        """Network-stream buffering: pause below 100% and resume at 100%,
        without flipping the user-facing play state. Live pipelines
        (NO_PREROLL) must not be paused for buffering."""
        if not (self.current_song and self._is_stream_url(self.current_song)):
            return
        percent = message.parse_buffering()
        if percent < 100:
            if not getattr(self, '_buffering', False) and not getattr(self, '_pipeline_live', False):
                self._buffering = True
                self.player.set_state(Gst.State.PAUSED)
            self.info_label.set_text(_('Buffering {percent}%').format(percent=percent))
        else:
            if getattr(self, '_buffering', False):
                self._buffering = False
                if self.is_playing:
                    self.player.set_state(Gst.State.PLAYING)
            self.update_audio_display()

    def on_bus_stream_start(self, bus, message):
        """Gapless handoff commit: the prerolled track is now the live stream."""
        if self._awaiting_own_start:
            # The bus was flushed on load, so the first start is the loaded
            # track's own. A short file can arm the handoff before this start is
            # dispatched, and a duplicate next entry has the same URI.
            self._awaiting_own_start = False
            return
        if self._gapless_next is None:
            return  # ordinary load_song start
        key, path, uri, generation = self._gapless_next
        if self.player.get_property('current-uri') != uri:
            return  # an earlier stream-start was still queued on the main loop
        self._gapless_next = None
        if generation != self._next_generation or key not in self.entry_ids:
            return
        idx = self.entry_ids.index(key)
        self._load_gen += 1
        self.order.commit(key)
        self.current_index = idx
        self.current_song = path
        self._loaded_uri = uri          # re-arms the stale-tag guard
        self._pending_seek_ns = None
        self._stream_meta = {}
        self.position_scale.set_sensitive(not self._is_stream_url(path))
        # Per-track UI reset (mirrors load_song; no state change happens in a
        # gapless handoff, so on_bus_state_changed never re-caches these)
        self.duration = 0
        self.position = 0
        self.position_scale.set_value(0)
        self.time_display.set_text("00:00")
        self._last_pos_sec = None
        self._last_progress = -1
        ok, dur = self.player.query_duration(Gst.Format.TIME)
        if ok and dur > 0:
            self.duration = dur
            self._note_duration(path, dur // Gst.SECOND)
        else:
            # Sidecar fallback now; some demuxers need a beat — retry once
            secs = self._duration_seconds(path)
            if secs:
                self.duration = secs * Gst.SECOND
            generation = self._load_gen
            def requery():
                if self._destroyed or generation != self._load_gen:
                    return False
                ok2, d2 = self.player.query_duration(Gst.Format.TIME)
                if ok2 and d2 > 0:
                    self.duration = d2
                    self._note_duration(path, d2 // Gst.SECOND)
                return False
            self.tasks.timeout_add(200, requery)
        self._read_caps_props()         # caps may renegotiate (rate change)
        self._post_load_ui(idx, path)

    def on_bus_state_changed(self, bus, message):
        if message.src is not self.player:
            return
        _old, new, _pending = message.parse_state_changed()
        if new == Gst.State.PLAYING:
            self._error_streak = 0
            self._read_caps_props()
            # Cache duration once per track instead of re-querying every UI tick
            ok, dur = self.player.query_duration(Gst.Format.TIME)
            if ok and dur > 0:
                self.duration = dur
                # Also feeds the playlist duration column (covers files the
                # background Discoverer chokes on)
                self._note_duration(self.current_song, dur // Gst.SECOND)
            if self._pending_seek_ns is not None:
                # Clear only after the seek: until then about-to-finish must not
                # queue the next URI, or the flushing seek would stall on it.
                self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, self._pending_seek_ns)
                self._pending_seek_ns = None

    def on_bus_tag(self, bus, message):
        # Ignore tag messages queued before a track switch: playbin's current-uri
        # is authoritative for which stream posted them.
        if self.player.get_property("current-uri") != self._loaded_uri:
            return
        taglist = message.parse_tag()
        title = self._tag_str(taglist, Gst.TAG_TITLE)
        artist = self._tag_str(taglist, Gst.TAG_ARTIST)
        # Tags arriving while a gapless next track prerolls belong to THAT
        # track (current-uri still reports the old one until stream-start).
        # Applying them here flips the title/bitrate early and caches the next
        # track's cover under the current track's path — the title/art
        # mismatch. Cache them for the pending track and leave the display
        # alone; the stream-start handoff applies them via _post_load_ui.
        pending = self._gapless_next
        if pending is not None:
            path = pending[1]
            if title:
                cached = self._cache_get(self._meta_cache, path)
                if isinstance(cached, dict):
                    merged = dict(cached)
                    merged['title'] = title
                    if artist:
                        merged['artist'] = artist
                    self._cache_put(self._meta_cache, path, merged)
            if self._cache_get(self._art_cache, path) is None:
                ok, sample = taglist.get_sample(Gst.TAG_IMAGE)
                if not ok:
                    ok, sample = taglist.get_sample(Gst.TAG_PREVIEW_IMAGE)
                if ok and sample:
                    data = self._sample_to_bytes(sample)
                    if data:
                        pixbuf = self._decode_art_pixbuf(data)
                        if pixbuf:
                            self._cache_put(self._art_cache, path, pixbuf)
            return
        ok, br = taglist.get_uint(Gst.TAG_BITRATE)
        if not ok:
            ok, br = taglist.get_uint(Gst.TAG_NOMINAL_BITRATE)
        if ok and br > 0:
            self.audio_properties['bitrate'] = br // 1000
        if title:
            self._set_title_text(f"{artist} - {title}" if artist else title)
        # ICY now-playing: souphttpsrc posts stream metadata as ordinary tags
        if self.current_song and self._is_stream_url(self.current_song):
            station = self._tag_str(taglist, Gst.TAG_ORGANIZATION)
            meta = getattr(self, '_stream_meta', {})
            changed = title and title != meta.get('title')
            if title:
                meta['title'] = title
            if artist or station:
                meta['artist'] = artist or station
            self._stream_meta = meta
            if changed:
                self._mpris_notify_track()
                self._notify_track(title, meta.get('artist') or '')
        if self.current_song and self._cache_get(self._art_cache, self.current_song) is None:
            ok, sample = taglist.get_sample(Gst.TAG_IMAGE)
            if not ok:
                ok, sample = taglist.get_sample(Gst.TAG_PREVIEW_IMAGE)
            if ok and sample:
                data = self._sample_to_bytes(sample)
                if data:
                    self._apply_art_bytes(self.current_song, data)
        self.update_audio_display()

    def _tag_str(self, taglist, tag):
        ok, val = taglist.get_string(tag)
        return val if ok and val else None

    def on_bus_element(self, bus, message):
        s = message.get_structure()
        if s and s.get_name() == "spectrum":
            self.on_spectrum_message(s)

    def _read_caps_props(self):
        """Pull real sample rate / channels from the negotiated audio pad."""
        try:
            pad = self.player.emit("get-audio-pad", 0)
            if not pad:
                return
            caps = pad.get_current_caps()
            if not caps or caps.get_size() == 0:
                return
            s = caps.get_structure(0)
            ok, rate = s.get_int("rate")
            if ok:
                self.audio_properties['sample_rate'] = rate
            ok, ch = s.get_int("channels")
            if ok:
                self.audio_properties['channels'] = ch
            self.update_audio_display()
        except Exception as e:
            self.log_debug(f"caps read failed: {e}")

    def _set_sleep(self, mode):
        self._invalidate_next()
        """Arm/disarm the sleep timer. mode: None (off), 'track', or minutes."""
        if self._sleep_timer_id is not None:
            self.tasks.source_remove(self._sleep_timer_id)
            self._sleep_timer_id = None
        self._sleep_after_track = False
        self._sleep_deadline = None
        self._sleep_mode = mode
        if mode == 'track':
            self._sleep_after_track = True
            self.show_drop_feedback(_("Sleeping after this track"))
        elif isinstance(mode, int) and mode > 0:
            self._sleep_timer_id = self.tasks.timeout_add_seconds(mode * 60, self._sleep_fire)
            self._sleep_deadline = GLib.get_monotonic_time() + mode * 60 * 1_000_000
            self.show_drop_feedback(_('Sleeping in {minutes} min').format(minutes=mode))
        else:
            self.show_drop_feedback(_("Sleep timer off"))
        self._update_stop_btn_cue()
        self._prepare_next()

    def _sleep_fire(self):
        self._sleep_timer_id = None
        self._sleep_deadline = None
        self._sleep_mode = None
        if self.is_playing:
            self.player.set_state(Gst.State.PAUSED)
            self._sync_play_ui(False)
        self.show_drop_feedback(_("Sleep timer — paused"))
        return False

    def advance_track(self, auto=False, after_error=False):
        """Move to the next track honoring shuffle + tri-state repeat, skipping
        missing files. auto=True means triggered by end-of-song (so REPEAT_ONE
        replays); after_error=True suppresses the replay so a broken track
        can't loop forever."""
        if not self.playlist:
            return
        if auto and self._sleep_after_track:
            # Sleep timer: stop at the end of this track (beats repeat-one)
            self._sleep_after_track = False
            self._sleep_mode = None
            self._update_stop_btn_cue()
            self.stop_song(None)
            self.show_drop_feedback(_("Sleep timer — stopped after track"))
            return
        if auto and self.repeat_mode == REPEAT_ONE and not after_error:
            self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
            self.player.set_state(Gst.State.PLAYING)
            return

        self._navigate(auto=auto, after_error=after_error)

    def _current_position_ns(self):
        try:
            ok, pos = self.player.query_position(Gst.Format.TIME)
            if ok and pos > 0:
                return int(pos)
        except Exception:
            pass
        return int(getattr(self, "position", 0) or 0)

    def _seek_relative(self, seconds):
        """Seek by +/- seconds from the current position, clamped to the track."""
        ok, pos = self.player.query_position(Gst.Format.TIME)
        if not ok:
            return
        ok, dur = self.player.query_duration(Gst.Format.TIME)
        if not ok or dur <= 0:
            dur = self.duration
        target = pos + int(seconds * Gst.SECOND)
        target = max(0, min(target, dur if dur > 0 else target))
        self._seek_ns(target)

    def seek_to(self, seconds):
        """Seek to an absolute time, clamped to the track. False when nothing
        seekable is loaded (stopped, or a live stream)."""
        if (self.playback_state == 'Stopped' or not self.current_song
                or self._is_stream_url(self.current_song)):
            return False
        ok, dur = self.player.query_duration(Gst.Format.TIME)
        if not ok or dur <= 0:
            dur = self.duration
        target = max(0, int(seconds * Gst.SECOND))
        if dur > 0:
            target = min(target, dur)
        self._seek_ns(target)
        return True

    def _seek_ns(self, target):
        """Seek within the current track. Once about-to-finish has handed playbin
        the next URI, a flushing seek switches to that track instead, so the
        current entry is reloaded and the seek applied as it starts."""
        self._finish_crossfade()
        with self._gapless_lock:
            armed = self._gapless_next is not None
        if armed:
            was_playing = self.is_playing
            self.load_song(self.current_index)
            self._pending_seek_ns = target
            self.position = target
            if was_playing:
                self._play_current()
            else:
                self.player.set_state(Gst.State.PAUSED)
        else:
            self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, target)
        self._mpris_notify_seeked(target)

    def _step_volume(self, delta):
        self.volume_scale.set_value(max(0.0, min(1.0, self.volume + delta)))

    def toggle_gapless(self, *_args):
        """Toggle gapless handoff. Note: a next-uri already prerolled before
        turning it off still plays gaplessly once — acceptable."""
        self._invalidate_next()
        self.gapless = not self.gapless
        self.schedule_save_config()
        self.show_drop_feedback(
            _("Gapless playback on") if self.gapless else _("Gapless playback off"))

    # Audio control methods
    def toggle_play_pause(self, button):
        if not self.playlist:
            self.add_files(None)
            return
            
        if self.is_playing:
            self._finish_crossfade()
            self.player.set_state(Gst.State.PAUSED)
            self._sync_play_ui(False)
        else:
            if self.current_song is None and self.playlist:
                self.load_song(self.current_index)

            # Check if current song file exists before trying to play
            if (self.current_song and not self._is_stream_url(self.current_song)
                    and not os.path.exists(self.current_song)):
                # Try to find the next available song
                self.find_and_load_next_available_song()
                if not self.current_song:
                    return  # No available songs found

            self._play_current()

    def play_from_start(self, *_args):
        """Winamp's Play (X): starts when stopped, resumes when paused and
        restarts the track when already playing."""
        if self.playback_state == 'Playing':
            self.seek_to(0)
        else:
            self.toggle_play_pause(None)

    def pause_toggle(self, *_args):
        """Winamp's Pause (C): toggles pause; does nothing while stopped."""
        if self.playback_state != 'Stopped':
            self.toggle_play_pause(None)

    def find_and_load_next_available_song(self):
        """Find and load the next available (existing) song in the playlist"""
        if not self.playlist:
            return
        
        # Try all songs starting from current index
        for i in range(len(self.playlist)):
            index = (self.current_index + i) % len(self.playlist)
            if self._playable(self.playlist[index]):
                self.load_song(index)
                return
        
        # No available songs found
        self.stop_song(None)
        self.current_song = None
        self._set_title_text(_("❌ No available files in playlist"))
        self.info_label.set_text(_("All files are missing - please re-add music files"))

    def stop_song(self, button):
        self._finish_crossfade()
        self._invalidate_next()
        self._pending_seek_ns = None
        self.player.set_state(Gst.State.NULL)
        self._sync_play_ui(False, stopped=True)
        self.position = 0
        self.position_scale.set_value(0)
        self.time_display.set_text("00:00")

    def _invalidate_next(self):
        with self._gapless_lock:
            self._next_generation += 1
            self._next_snapshot = None
            pending = self._gapless_next
            self._gapless_next = None
        # A handed-off URI cannot simply be forgotten: reset the preroll to
        # the current track when an edit supersedes it.
        if pending and self.current_song and not self._destroyed:
            position = self._current_position_ns()
            self.player.set_state(Gst.State.NULL)
            uri = self.current_song if self._is_stream_url(self.current_song) else Gst.filename_to_uri(self.current_song)
            self.player.set_property('uri', uri)
            self._pending_seek_ns = position
            if self.is_playing:
                self.player.set_state(Gst.State.PLAYING)

    def _prepare_next(self):
        if (self._destroyed or not self.gapless or self.repeat_mode == REPEAT_ONE
                or self._sleep_after_track or self._loading or getattr(self, '_switching_output', False)):
            return
        with self._gapless_lock:
            if self._next_snapshot or self._gapless_next:
                return
        key = self.order.next_id(self._playable, self.shuffle, self.repeat_mode)
        if key is None:
            return
        path = dict(self.order.entries)[key]
        uri = path if self._is_stream_url(path) else Gst.filename_to_uri(os.path.abspath(path))
        with self._gapless_lock:
            self._next_snapshot = (key, path, uri, self._next_generation)

    def next_song(self, button):
        self._navigate(auto=False)

    def _navigate(self, auto=False, after_error=False):
        was_playing = self.is_playing
        mode = REPEAT_OFF if after_error and self.repeat_mode == REPEAT_ONE else self.repeat_mode
        key = self.order.next_id(self._playable, self.shuffle, mode, auto)
        if key is None:
            self.stop_song(None)
            return
        self.load_song(self.entry_ids.index(key))
        if auto or was_playing:
            self._play_current()

    def previous_song(self, button):
        if not self.playlist:
            return
        was_playing = self.is_playing
        if self.shuffle != SHUFFLE_OFF:
            key = self.order.previous_id(self._playable)
            index = self.entry_ids.index(key) if key in self.entry_ids else None
        else:
            earlier = [i for i in range(self.current_index) if self._playable(self.playlist[i])]
            index = earlier[-1] if earlier else self.current_index
            if not earlier and self.repeat_mode == REPEAT_ALL:
                available = [i for i, path in enumerate(self.playlist) if self._playable(path)]
                index = available[-1] if available else None
        if index is not None:
            self.load_song(index)
            if was_playing:
                self._play_current()

    def load_song(self, index):
        if not (0 <= index < len(self.playlist)):
            return
        self._finish_crossfade()
        # New load generation: stale async results (probes, art, bus tags) are ignored
        self._load_gen += 1
        self._invalidate_next()
        self.analyzer_state.reset()
        self._pending_seek_ns = None
        self._gapless_next = None   # manual load supersedes any prerolled next track
        was_loading = self._loading
        self._loading = True        # freeze about-to-finish while we rebuild
        try:
            file_path = self.playlist[index]
            self.current_index = index
            # Reset per-track state so nothing stale leaks across tracks
            self.duration = 0
            self.position = 0
            self.position_scale.set_value(0)
            self.time_display.set_text("00:00")

            if self._is_stream_url(file_path):
                # Internet radio: the URL is the URI; no existence check, no
                # seeking, duration unknown until (never, for live streams)
                self.player.set_state(Gst.State.NULL)
                bus = self.player.get_bus()
                bus.set_flushing(True)
                bus.set_flushing(False)
                self._awaiting_own_start = True
                self.player.set_property("uri", file_path)
                self.current_song = file_path
                self._loaded_uri = file_path
                self._stream_meta = {}
                self.position_scale.set_sensitive(False)
                self._post_load_ui(index, file_path)
                return

            self.position_scale.set_sensitive(True)
            if not os.path.exists(file_path):
                self.current_song = None
                self._loaded_uri = None
                self.player.set_state(Gst.State.NULL)
                self._sync_play_ui(False, stopped=True)
                song_name = self._display_name(file_path)
                self._set_title_text(_('❌ File not found: {song_name}').format(song_name=song_name))
                self.info_label.set_text(_("File missing - please re-add to playlist"))
                self._set_album_art(None)
                self.update_missing_file_in_playlist(index)
                self._select_row(index)
                self.schedule_save_config()
                return

            uri = Gst.filename_to_uri(os.path.abspath(file_path))  # encodes spaces/#/unicode
            self.player.set_state(Gst.State.NULL)  # Reset player state
            # Drop bus messages queued by the previous track so its late tags/art
            # can't be attributed to this one.
            bus = self.player.get_bus()
            bus.set_flushing(True)
            bus.set_flushing(False)
            self._awaiting_own_start = True
            self.player.set_property("uri", uri)
            self.current_song = file_path
            self._loaded_uri = uri
            self._post_load_ui(index, file_path)
        finally:
            self._loading = was_loading
            self._prepare_next()

    def toggle_shuffle(self, button):
        self._invalidate_next()
        # Cycle OFF -> TRACKS -> ALBUMS -> OFF
        self.shuffle = (self.shuffle + 1) % 3
        self.update_shuffle_button()
        self.schedule_save_config()
        if self.current_song:
            self.update_audio_display()
        self._mpris_emit({'Shuffle': GLib.Variant('b', self.shuffle != SHUFFLE_OFF)})

    def toggle_repeat(self, button):
        self._invalidate_next()
        # Cycle OFF -> ALL -> ONE -> OFF
        self.repeat_mode = (self.repeat_mode + 1) % 3
        self.update_repeat_button()
        self.schedule_save_config()
        if self.current_song:
            self.update_audio_display()
        loop = {REPEAT_OFF: 'None', REPEAT_ALL: 'Playlist', REPEAT_ONE: 'Track'}
        self._mpris_emit({'LoopStatus': GLib.Variant('s', loop[self.repeat_mode])})

    def _play_current(self):
        """Set PLAYING and record whether the pipeline is live (NO_PREROLL) —
        live streams must not be paused by the buffering handler."""
        if not self.current_song:
            return
        with self._gapless_lock:
            self._next_snapshot = None
        self.order.commit(self.entry_ids[self.current_index])
        ret = self.player.set_state(Gst.State.PLAYING)
        self._pipeline_live = (ret == Gst.StateChangeReturn.NO_PREROLL)
        self._sync_play_ui(True)
        self._prepare_next()

    def _play_index(self, index):
        if 0 <= index < len(self.entry_ids):
            self.order.failed.discard(self.entry_ids[index])
        self.load_song(index)
        self._play_current()

    def load_first_available_song(self):
        """Load the first available (existing) song from the playlist"""
        for i, file_path in enumerate(self.playlist):
            if self._playable(file_path):
                self.load_song(i)
                return
        # If no files exist, just set to first index but don't load
        if self.playlist:
            self.current_index = 0

