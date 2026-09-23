"""Run with xvfb-run -a /usr/bin/python3 -m unittest discover -s tests."""
import json
import os
import struct
import sys
import tempfile
import time
import unittest
import wave
import zipfile

import cairo
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # golden_scenes, run either way
from golden_scenes import GOLDEN_DIR, SCENES, difference, render
from llamaamp.app import MusicPlayer
from llamaamp.constants import REPEAT_ALL, REPEAT_ONE, SHUFFLE_TRACKS
from llamaamp.fileinfo import read_file_info
from llamaamp.skin.default import build_default_skin
from llamaamp.skin.loader import Skin, glyph_cell, parse_pledit, parse_region, parse_viscolor
from llamaamp.skin.sprites import FONT_LOOKUP
from llamaamp.ui.themes import THEMES
from gi.repository import Gdk, GLib, Gst, Gtk


@unittest.skipUnless(os.environ.get('DISPLAY'), 'requires a display (use xvfb-run)')
class PlayerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='llama-test-')
        self.addCleanup(self.directory.cleanup)
        self.old_data = os.environ.get('LLAMAAMP_DATA_DIR')
        os.environ['LLAMAAMP_DATA_DIR'] = self.directory.name
        self.addCleanup(self.restore_environment)
        Path(self.directory.name, 'config.json').write_text(json.dumps({
            'alsa_output': False, 'direct_mode': False, 'tray_icon': False,
            'notifications': False, 'update_check': False,
        }))
        self.exceptions = []
        old_hook = sys.excepthook
        sys.excepthook = lambda *args: self.exceptions.append(args)
        self.addCleanup(setattr, sys, 'excepthook', old_hook)
        mpris = patch.object(MusicPlayer, '_mpris_setup', lambda self: None)
        mpris.start()
        self.addCleanup(mpris.stop)
        self.app = MusicPlayer()
        self.addCleanup(self.close_app)
        sink = Gst.ElementFactory.make('fakesink')
        sink.set_property('sync', True)
        self.app.player.set_property('audio-sink', sink)
        self.app.show_all()
        self.pump(.08)
        self.files = []
        for i in range(3):
            path = str(Path(self.directory.name, f'{i}.wav'))
            with wave.open(path, 'wb') as audio:
                audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                audio.writeframes(struct.pack('<h', 1000) * 16000)
            self.files.append(path)

    def restore_environment(self):
        if self.old_data is None:
            os.environ.pop('LLAMAAMP_DATA_DIR', None)
        else:
            os.environ['LLAMAAMP_DATA_DIR'] = self.old_data

    def close_app(self):
        self.app.destroy()
        self.pump(.03)
        self.assertFalse(self.app.tasks.ids)
        if self.exceptions:
            kind, error, trace = self.exceptions[0]
            raise error.with_traceback(trace)

    def pump(self, duration=.05):
        deadline = time.monotonic() + duration
        context = GLib.MainContext.default()
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(.002)

    def wait_for(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.pump(.01)
        self.assertTrue(predicate(), 'asynchronous condition did not complete')

    def test_selection_and_metadata_are_anchored(self):
        a = self.app
        a._add_paths(self.files)
        self.pump(.15)
        readout = lambda: (a.song_label.get_text(), a.info_label.get_text(),
                           a.kbps_value.get_text(), a.khz_value.get_text())
        before = readout()
        a._select_row(2)
        self.pump(.03)
        self.assertEqual(readout(), before)
        a.audio_properties = dict(sample_rate=44100, bitrate=900, channels=6)
        a.update_audio_display()
        self.assertEqual(a.kbps_value.get_text(), '900')
        self.assertEqual(a.khz_value.get_text(), '44.1')
        self.assertIn('6 ch', a.info_label.get_text())
        lit = lambda label: label.get_style_context().has_class('lit')
        self.assertFalse(lit(a.mono_light) or lit(a.stereo_light))
        a.audio_properties = dict(sample_rate=16000, bitrate=0, channels=1)
        a.update_audio_display()
        self.assertEqual((a.kbps_value.get_text(), a.khz_value.get_text()), ('—', '16'))
        self.assertTrue(lit(a.mono_light))
        self.assertFalse(lit(a.stereo_light))
        self.assertNotIn('kHz', a.info_label.get_text())

    def test_play_pause_stop_have_distinct_markers_and_mpris_states(self):
        a = self.app
        a._add_paths(self.files)
        self.assertEqual(a._mpris_status(), 'Stopped')
        self.assertEqual(a.playlist_store[0][5], '')
        a.toggle_play_pause(None)
        self.assertEqual(a._mpris_status(), 'Playing')
        self.assertEqual(a.playlist_store[0][5], '▶')
        a.toggle_play_pause(None)
        self.assertEqual(a._mpris_status(), 'Paused')
        self.assertEqual(a.playlist_store[0][5], 'Ⅱ')
        a._pending_seek_ns = 400 * Gst.MSECOND
        a.stop_song(None)
        self.assertEqual(a._mpris_status(), 'Stopped')
        self.assertEqual(a.playlist_store[0][5], '')
        self.assertEqual(a.current_song, self.files[0])
        self.assertEqual(a.time_display.get_text(), '00:00')
        self.assertIsNone(a._pending_seek_ns)
        a.next_song(None)
        self.assertEqual(a.current_song, self.files[1])
        self.assertEqual(a._mpris_status(), 'Stopped')
        self.assertTrue(all(row[5] == '' for row in a.playlist_store))

    def test_saved_current_track_restores_ready_and_stopped(self):
        a = self.app
        a._add_paths(self.files)
        a._play_index(1)
        a.destroy()
        self.app = a = MusicPlayer()
        a.show_all()
        self.pump(.05)
        self.assertEqual(a.current_song, self.files[1])
        self.assertEqual(a._mpris_status(), 'Stopped')
        self.assertFalse(a.is_playing)
        self.assertTrue(all(row[5] == '' for row in a.playlist_store))

    def test_late_titles_follow_duplicate_rows_without_changing_selection_or_queue(self):
        a = self.app
        # Hold worker results so they arrive after a reorder and active search.
        with patch.object(a._probe_queue, 'put'):
            a._add_paths(self.files + [self.files[0]])
        keys = list(a.entry_ids)
        a._queue_paths([keys[3], keys[2]])
        a.playlist_store.reorder([3, 2, 1, 0])
        self.pump(.03)
        a._select_row(2)
        a.search_entry.set_text('After Hours')
        a._search_step(True)
        a.tasks.idle_add(a._probe_done, self.files[0], {'title': 'After Hours'}, None)
        a.tasks.idle_add(a._probe_done, self.files[2], {'title': 'Another Song'}, None)
        self.pump(.04)
        self.assertEqual([row[1] for row in a.playlist_store],
                         ['After Hours', 'Another Song', '1', 'After Hours'])
        self.assertEqual(a.entry_ids, [keys[3], keys[2], keys[1], keys[0]])
        self.assertEqual(list(a.order.queue), [keys[3], keys[2]])
        model, selected = a.playlist_view.get_selection().get_selected_rows()
        self.assertEqual([model[path][4] for path in selected], [keys[1]])
        self.assertIn('2', a.search_count.get_text())
        self.assertNotIn('No matches', a.search_count.get_text())
        # A result for a removed entry cannot rename its replacement row.
        a.remove_selected(None)
        a._probe_done(self.files[1], {'title': 'Removed Song'}, None)
        self.assertNotIn('Removed Song', [row[1] for row in a.playlist_store])

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg generates tagged test audio')
    def test_tagged_titles_load_even_when_duration_is_already_cached(self):
        a = self.app
        tagged = str(Path(self.directory.name, '01. encoded.flac'))
        fallback = str(Path(self.directory.name, '02. Untagged.wav'))
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', self.files[0],
                        '-metadata', 'title=Night Drive', '-metadata', 'artist=Example Artist',
                        tagged], check=True)
        shutil.copyfile(self.files[1], fallback)
        a._note_duration(tagged, 1)
        a._note_duration(fallback, 1)
        a._add_paths([tagged, fallback, tagged])
        self.wait_for(lambda: all(path in a._playlist_titles for path in (tagged, fallback)))
        self.assertEqual([row[1] for row in a.playlist_store],
                         ['Night Drive', '02. Untagged', 'Night Drive'])
        a.search_entry.set_text('Night Drive')
        a._search_step(True)
        self.assertEqual(a.search_count.get_text(), '1/2')

    def test_queue_next_paused_and_playing(self):
        a = self.app
        a._add_paths(self.files)
        a._queue_paths([a.entry_ids[2]])
        a.next_song(None)
        self.assertEqual(a.current_index, 2)
        self.assertFalse(a.is_playing)
        a._play_current()
        self.pump(.1)
        self.assertFalse(a.order.queue)
        a._queue_paths([a.entry_ids[1]])
        a.next_song(None)
        self.assertEqual(a.current_index, 1)
        self.assertTrue(a.is_playing)

    def test_repeated_paused_next_consumes_queue(self):
        a = self.app
        a._add_paths(self.files)
        a._queue_paths([a.entry_ids[2], a.entry_ids[1]])
        a.next_song(None)
        a.next_song(None)
        self.assertEqual(a.current_index, 1)
        self.assertFalse(a.order.queue)
        self.assertFalse(a.order.history)

    def test_mpris_next_and_repeat(self):
        a = self.app
        a._add_paths(self.files)
        a._queue_paths([a.entry_ids[2]])
        class Invocation:
            def return_value(self, value):
                pass
            def return_dbus_error(self, name, message):
                raise AssertionError(message)
        a._mpris_method_call(None, None, None, 'org.mpris.MediaPlayer2.Player',
                            'Next', GLib.Variant('()', ()), Invocation())
        self.assertEqual(a.current_index, 2)
        a._mpris_set_prop(None, None, None, None, 'LoopStatus', GLib.Variant('s', 'Track'))
        self.assertIsNone(a._next_snapshot)

    def test_duplicate_reorder_and_undo(self):
        a = self.app
        a._add_paths(self.files + [self.files[0]])
        keys = list(a.entry_ids)
        a._play_index(3)
        a.playlist_store.reorder([3, 0, 1, 2])
        self.pump(.05)
        self.assertEqual(a.entry_ids[0], keys[3])
        self.assertEqual(a.order.current, keys[3])
        a.undo_playlist()
        self.assertEqual(a.entry_ids, keys)
        self.assertEqual(a.current_index, 3)
        self.assertTrue(a.is_playing)

    def test_own_stream_start_is_not_a_duplicate_handoff(self):
        # A short file can reach about-to-finish before its own stream-start is
        # dispatched; when the next entry is the same file, the URIs match.
        a = self.app
        a._add_paths([self.files[0], self.files[0]])
        keys = list(a.entry_ids)
        a._play_index(0)
        a._on_about_to_finish(a.player)
        self.pump(.05)
        self.assertEqual(a.order.current, keys[0])
        self.assertEqual(a.current_index, 0)
        self.assertEqual(a._gapless_next[0], keys[1])

    def test_clear_and_undo_do_not_autoplay(self):
        a = self.app
        a._add_paths(self.files)
        keys = list(a.entry_ids)
        a._queue_paths([keys[1]])
        a.clear_playlist(None)
        self.assertFalse(a.playlist)
        a.undo_playlist()
        self.assertEqual(a.entry_ids, keys)
        self.assertEqual(list(a.order.queue), [keys[1]])
        self.assertFalse(a.is_playing)
        self.assertIsNone(a.current_song)

    def test_remove_missing_and_undo(self):
        a = self.app
        a._add_paths(self.files + ['/missing/audio.flac'])
        keys = list(a.entry_ids)
        a.remove_missing(None)
        self.assertEqual(a.playlist, self.files)
        a.undo_playlist()
        self.assertEqual(a.entry_ids, keys)
        a.load_song(2)
        a.next_song(None)
        self.assertFalse(a.is_playing)

    def test_windowshade_panels_themes_and_configuration(self):
        a = self.app
        size = list(a.get_size())
        a.panels.toggle_attach('eq')
        self.pump(.05)
        self.assertTrue(a.panels.items['eq']['window'].get_visible())
        a.toggle_windowshade()
        self.pump(.05)
        self.assertLessEqual(a.get_size()[1], 50)
        self.assertFalse(a.panels.items['eq']['window'].get_visible())
        self.assertIsNone(a._analyzer_id)
        a.toggle_windowshade()
        self.pump(.1)
        self.assertEqual(list(a.get_size()), size)
        self.assertTrue(a.panels.items['eq']['window'].get_visible())
        a.panels.toggle_attach('eq')
        a.panels.collapse('playlist')
        self.assertFalse(a.panels.items['playlist']['body'].get_visible())
        a._reset_layout()
        for theme in THEMES:
            a._set_appearance('theme', theme)
            self.pump(.01)
        a._set_appearance('show_art', False)
        a._set_appearance('show_art', True)
        a._write_config()
        config = json.loads(Path(a.config_path()).read_text())
        self.assertEqual(len(config['eq_values']), 10)
        self.assertEqual(config['theme'], 'amber')
        self.assertTrue(config['panels']['eq']['attached'])

    def test_eq_presets_and_direct_mode(self):
        a = self.app
        a.apply_eq_preset('Rock')
        self.assertAlmostEqual(a.eq_bars[0].get_value(), 5)
        a._reset_eq()
        self.assertTrue(all(scale.get_value() == 0 for scale in a.eq_bars))
        a.toggle_direct_mode()
        a.eq_bars[0].set_value(3)
        self.assertTrue(a.direct_mode)
        self.assertEqual(a.eq_status.get_text(), 'Bypassed · Direct Mode')
        self.assertEqual(a.eq_on_btn.get_label(), 'Bypassed')
        self.assertFalse(a.eq_on_btn.get_sensitive())
        self.assertIsNone(a.equalizer)
        a.toggle_direct_mode()
        self.assertEqual(a.eq_status.get_text(), 'Custom')
        self.assertTrue(a.eq_on_btn.get_sensitive())
        self.assertAlmostEqual(a.equalizer.get_property('band0'), 3)
        a.toggle_eq_enabled()
        self.assertEqual(a.eq_status.get_text(), 'Equalizer off')
        self.assertAlmostEqual(a.eq_bars[0].get_value(), 3)
        self.assertAlmostEqual(a.equalizer.get_property('band0'), 0)

    def test_actual_gapless_transition_and_analyzer(self):
        a = self.app
        a._add_paths(self.files)
        a._queue_paths([a.entry_ids[2]])
        a._play_index(0)
        self.pump(1.25)
        self.assertEqual(a.current_index, 2)
        self.assertEqual(a.order.history[-1], a.entry_ids[2])
        self.assertFalse(a.order.queue)
        self.assertIsNotNone(a.analyzer_state.last_input)
        a._set_appearance('visualization', False)
        self.assertIsNone(a._analyzer_id)
        self.assertFalse(a.spectrum.get_property('post-messages'))

    def test_prepared_handoff_invalidated_on_edit(self):
        a = self.app
        a._add_paths(self.files)
        a._on_about_to_finish(a.player)
        old = a._gapless_next
        self.assertIsNotNone(old)
        a._select_row(1)
        a.remove_selected(None)
        self.assertIsNone(a._gapless_next)
        self.assertNotEqual(old[3], a._next_generation)
        self.assertEqual(a.order.current, a.entry_ids[0])

    def test_visualization_click_cycles_spectrum_scope_off(self):
        a = self.app
        seen = []
        for _ in range(3):
            a._cycle_visualization()
            seen.append((a.config['visualization'], a.config['vis_mode']))
        self.assertEqual(seen, [(True, 'scope'), (False, 'scope'), (True, 'spectrum')])
        a._cycle_visualization()
        a._write_config()
        self.assertEqual(json.loads(Path(a.config_path()).read_text())['vis_mode'], 'scope')

    def test_oscilloscope_traces_audio_with_and_without_dsp(self):
        a = self.app
        a._set_appearance('vis_mode', 'scope')
        a._add_paths(self.files)
        a._play_index(0)
        self.wait_for(lambda: a.scope_state.points)
        # The fixture is a constant positive sample: a flat trace above center
        self.assertTrue(all(0 < p < .2 for p in a.scope_state.points), a.scope_state.points)
        a.toggle_direct_mode()
        a.scope_state.reset()
        self.wait_for(lambda: a.scope_state.points)
        self.assertTrue(all(0 < p < .2 for p in a.scope_state.points))
        a.stop_song(None)
        self.wait_for(lambda: not a.scope_state.points)

    def press(self, keyval, state=0):
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval = keyval
        event.state = Gdk.ModifierType(state)
        return self.app.on_window_key_press(self.app, event)

    def test_seeking_after_handoff_is_armed_stays_on_the_track(self):
        # Near the end of a track playbin already holds the next URI; a plain
        # flushing seek would switch to it instead of rewinding.
        a = self.app
        a._add_paths(self.files)
        a._play_index(0)
        self.wait_for(lambda: a._current_position_ns() > 100 * Gst.MSECOND)
        a._on_about_to_finish(a.player)
        self.assertIsNotNone(a._gapless_next)
        started = time.monotonic()
        self.assertTrue(a.seek_to(.5))
        self.wait_for(lambda: a._current_position_ns() >= 450 * Gst.MSECOND)
        # The reload restarts at 0; only the seek can put playback ahead of the clock
        ahead = a._current_position_ns() / Gst.SECOND - (time.monotonic() - started)
        self.assertGreater(ahead, .3)
        self.assertEqual(a.current_index, 0)
        self.assertEqual(a.order.current, a.entry_ids[0])
        self.assertTrue(a.is_playing)
        self.wait_for(lambda: a.current_index == 1)      # the real handoff still happens

    def test_winamp_transport_keys(self):
        a = self.app
        a._add_paths(self.files)
        self.assertTrue(self.press(Gdk.KEY_c))            # C while stopped does nothing
        self.assertEqual(a.playback_state, 'Stopped')
        self.assertTrue(self.press(Gdk.KEY_x))            # X plays
        self.assertEqual(a.playback_state, 'Playing')
        self.wait_for(lambda: a._current_position_ns() > 200 * Gst.MSECOND)
        self.assertTrue(self.press(Gdk.KEY_X))            # X while playing restarts
        self.pump(.05)
        self.assertLess(a._current_position_ns(), 200 * Gst.MSECOND)
        self.assertEqual(a.playback_state, 'Playing')
        self.assertTrue(self.press(Gdk.KEY_c))            # C pauses and resumes
        self.assertEqual(a.playback_state, 'Paused')
        self.assertTrue(self.press(Gdk.KEY_c))
        self.assertEqual(a.playback_state, 'Playing')
        self.assertTrue(self.press(Gdk.KEY_b))            # B next, Z previous
        self.assertEqual(a.current_index, 1)
        self.assertTrue(self.press(Gdk.KEY_z))
        self.assertEqual(a.current_index, 0)
        self.assertTrue(self.press(Gdk.KEY_v))            # V stops
        self.assertEqual(a.playback_state, 'Stopped')
        self.assertTrue(self.press(Gdk.KEY_x))            # X from stopped plays again
        self.assertEqual(a.playback_state, 'Playing')
        self.assertTrue(self.press(Gdk.KEY_z, Gdk.ModifierType.CONTROL_MASK))  # Ctrl+Z is Undo
        self.assertFalse(a.playlist)

    def test_ctrl_t_toggles_remaining_time(self):
        a = self.app
        self.assertFalse(a._time_remaining)
        self.assertTrue(self.press(Gdk.KEY_t, Gdk.ModifierType.CONTROL_MASK))
        self.assertTrue(a._time_remaining)
        self.assertFalse(self.press(Gdk.KEY_t))           # plain T is not bound

    def test_jump_to_time_seeks_within_the_track(self):
        a = self.app
        a._add_paths(self.files)
        a._play_index(0)
        self.wait_for(lambda: a.duration > 0)
        self.assertTrue(a._jump_to_time('0:00.6'))
        # A short fixture has already armed the handoff, so the seek reloads first
        self.wait_for(lambda: a._current_position_ns() >= 550 * Gst.MSECOND)
        self.assertEqual(a.current_index, 0)
        self.assertTrue(a._jump_to_time('5:00'))        # clamped to the track length
        self.assertFalse(a._jump_to_time('soon'))

    def test_double_size_scales_controls_and_persists(self):
        a = self.app
        self.pump(.05)
        width = a.get_size()[0]
        self.assertEqual(a.analyzer.get_size_request(), (100, 62))
        self.assertTrue(self.press(Gdk.KEY_d, Gdk.ModifierType.CONTROL_MASK))
        self.pump(.1)
        self.assertEqual(a.ui_scale, 2)
        self.assertEqual(a.analyzer.get_size_request(), (200, 124))
        self.assertEqual(a.eq_bars[0].get_size_request(), (48, 136))
        self.assertEqual(a.playlist_view.get_column(3).get_min_width(), 128)
        self.assertGreater(a.get_size()[0], width)
        self.assertEqual(a.album_art.get_pixbuf().get_width(), 144)
        a.destroy()
        self.app = a = MusicPlayer()
        a.show_all()
        self.pump(.05)
        self.assertEqual(a.ui_scale, 2)
        self.assertEqual(a.analyzer.get_size_request(), (200, 124))
        a.toggle_double_size()
        self.pump(.05)
        self.assertEqual(a.analyzer.get_size_request(), (100, 62))
        self.assertEqual(a.album_art.get_pixbuf().get_width(), 72)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg generates tagged test audio')
    def test_probe_reads_album_disc_and_track_tags(self):
        a = self.app
        tagged = str(Path(self.directory.name, 'tagged.flac'))
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', self.files[0], '-metadata', 'album=Side A',
                        '-metadata', 'track=3/9', '-metadata', 'disc=2', '-metadata', 'artist=Llama',
                        tagged], check=True)
        a._add_paths([tagged])
        self.wait_for(lambda: a._playlist_tags.get(tagged))
        self.assertEqual(a._playlist_tags[tagged],
                         {'artist': 'Llama', 'album': 'Side A', 'disc': 2, 'track': 3})

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg generates tagged test audio')
    def test_file_info_reads_tags_audio_and_file_details(self):
        tagged = str(Path(self.directory.name, 'info.flac'))
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', self.files[0], '-metadata', 'title=Night Drive',
                        '-metadata', 'artist=Llama', '-metadata', 'album=Side A', '-metadata', 'track=3/9',
                        '-metadata', 'date=1997', '-metadata', 'REPLAYGAIN_TRACK_GAIN=-6.20 dB',
                        tagged], check=True)
        info = read_file_info(tagged)
        fields = {label: value for _section, rows in info['sections'] for label, value in rows}
        self.assertEqual([section for section, _rows in info['sections']], ['Track', 'Audio', 'File'])
        self.assertEqual(fields['Title'], 'Night Drive')
        self.assertEqual(fields['Album'], 'Side A')
        self.assertEqual(fields['Track'], '3/9')
        self.assertEqual(fields['Year'], '1997')
        self.assertEqual(fields['Track gain'], '−6.20 dB')
        self.assertEqual(fields['Sample rate'], '16 kHz')
        self.assertEqual(fields['Channels'], 'Mono')
        self.assertEqual(fields['Length'], '0:01')
        self.assertIn('FLAC', fields['Format'])
        self.assertEqual(fields['Location'], tagged)
        self.assertRegex(fields['Size'], r'^\d+(\.\d)? KB$')
        missing = read_file_info(str(Path(self.directory.name, 'gone.mp3')))
        self.assertEqual(missing['sections'][-1][1][-1], ('Status', 'File not found'))

    def test_alt_3_opens_file_info_for_the_selection(self):
        a = self.app
        a._add_paths(self.files)
        a._select_row(1)
        self.assertTrue(self.press(Gdk.KEY_3, Gdk.ModifierType.MOD1_MASK))
        dialog = a._file_info_dialog
        self.assertIn('1.wav', dialog.get_title())
        self.wait_for(lambda: dialog.fields.get('Sample rate'))
        self.assertEqual(dialog.fields['Sample rate'].get_text(), '16 kHz')
        dialog.response(Gtk.ResponseType.CLOSE)
        self.pump(.02)
        self.assertIsNone(a._file_info_dialog)
        self.assertFalse(self.press(Gdk.KEY_3))

    def test_sort_is_one_undoable_edit_keeping_playback_and_queue(self):
        a = self.app
        with patch.object(a._probe_queue, 'put'):
            a._add_paths(self.files + [self.files[0]])
        keys = list(a.entry_ids)
        for path, album, track in ((self.files[0], 'B', 2), (self.files[1], 'B', 1), (self.files[2], 'A', 5)):
            a._probe_done(path, {'title': f'Song {track}', 'album': album, 'track': track}, None)
        a._play_index(3)
        a._queue_paths([keys[1]])
        a.sort_playlist('album')
        self.assertEqual(a.entry_ids, [keys[2], keys[1], keys[0], keys[3]])
        self.assertEqual([row[4] for row in a.playlist_store], a.entry_ids)
        self.assertEqual(a.order.current, keys[3])
        self.assertEqual(a.current_index, 3)
        self.assertEqual(list(a.order.queue), [keys[1]])
        self.assertTrue(a.is_playing)
        a.undo_playlist()
        self.assertEqual(a.entry_ids, keys)
        self.assertEqual(a.current_index, 3)
        a.sort_playlist('reverse')
        self.assertEqual(a.entry_ids, keys[::-1])
        a._playlist_popup(a.playlist_view)
        items = {item.get_label(): item for item in a._actions_menu.get_children()}
        sort_menu = items['Sort'].get_submenu()
        self.assertEqual([item.get_label() for item in sort_menu.get_children()][:2], ['By title', 'By artist'])
        a._actions_menu.popdown()
        sort_menu.get_children()[-2].activate()        # Reverse list, through the menu
        self.assertEqual(a.entry_ids, keys)

    def long_files(self, count=2, seconds=3):
        paths = []
        for i in range(count):
            path = str(Path(self.directory.name, f'long{i}.wav'))
            with wave.open(path, 'wb') as audio:
                audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                audio.writeframes(struct.pack('<h', 1000) * 16000 * seconds)
            paths.append(path)
        return paths

    def test_crossfade_overlaps_the_next_track(self):
        a = self.app
        a._set_crossfade(1)
        a._add_paths(self.long_files())
        a._play_index(0)
        self.wait_for(lambda: a.duration > 0)
        a.seek_to(1.6)
        self.wait_for(lambda: a._xfade is not None)
        outgoing = a._xfade['outgoing']
        self.assertIsNot(outgoing, a.player)
        self.assertEqual(a.current_index, 1)                   # display follows the incoming track
        self.assertEqual(outgoing.get_state(0)[1], Gst.State.PLAYING)
        self.wait_for(lambda: a.player.get_property('volume') > .1)
        self.assertLess(outgoing.get_property('volume'), a.volume)
        self.wait_for(lambda: a._xfade is None)
        self.assertEqual(outgoing.get_state(0)[1], Gst.State.NULL)
        self.assertAlmostEqual(a.player.get_property('volume'), a.volume, places=3)
        self.assertEqual(a.current_index, 1)
        self.assertEqual(a.order.current, a.entry_ids[1])
        self.assertTrue(a.is_playing)

    def test_crossfade_replaces_gapless_only_where_it_applies(self):
        a = self.app
        a._set_crossfade(2)
        a._add_paths(self.long_files())
        a._play_index(0)
        a._on_about_to_finish(a.player)
        self.assertIsNone(a._gapless_next)                     # the crossfade will take over
        a._set_crossfade(0)
        a._on_about_to_finish(a.player)
        self.assertIsNotNone(a._gapless_next)
        a._invalidate_next()
        a._set_crossfade(2)
        a.alsa_output = True                                   # exclusive output: gapless instead
        self.assertFalse(a._crossfade_applies(a.playlist[1]))

    def test_user_actions_end_a_crossfade_cleanly(self):
        a = self.app
        a._set_crossfade(2)
        a._add_paths(self.long_files(3))
        a._play_index(0)
        self.wait_for(lambda: a.duration > 0)
        a.seek_to(1.5)
        self.wait_for(lambda: a._xfade is not None)
        outgoing = a._xfade['outgoing']
        a.next_song(None)
        self.assertIsNone(a._xfade)
        self.assertEqual(outgoing.get_state(0)[1], Gst.State.NULL)
        self.assertEqual(a.current_index, 2)
        self.assertAlmostEqual(a.player.get_property('volume'), a.volume, places=3)
        a.stop_song(None)
        self.assertEqual(a.playback_state, 'Stopped')

    def click_event(self, x, y, button=1, double=False, state=0):
        """A Gdk.EventButton as GTK delivers it (the union's button member)."""
        event = Gdk.EventButton()
        event.type = Gdk.EventType._2BUTTON_PRESS if double else Gdk.EventType.BUTTON_PRESS
        event.button, event.x, event.y = button, x, y
        event.state = Gdk.ModifierType(state)
        return event

    def test_classic_mode_replaces_and_restores_the_modern_window(self):
        a = self.app
        a._add_paths(self.files)
        a.set_skin('builtin')
        self.pump(.05)
        classic = a._classic
        self.assertFalse(a.get_visible())
        self.assertTrue(all(window.get_visible() for window in classic.windows()))
        self.assertTrue(a._analyzer_visible())
        classic.toggle_window('eq')
        self.assertFalse(classic.eq.get_visible())
        a._write_config()
        saved = json.loads(Path(a.config_path()).read_text())
        self.assertEqual(saved['skin'], 'builtin')
        self.assertEqual(saved['classic_windows']['eq'], False)
        a.set_skin(None)
        self.pump(.05)
        self.assertIsNone(a._classic)
        self.assertTrue(a.get_visible())
        self.assertIsNone(a.config['skin'])

    def test_classic_controls_drive_the_player(self):
        a = self.app
        a._add_paths(self.files)
        a.set_skin('builtin')
        main, eq, playlist = a._classic.main, a._classic.eq, a._classic.playlist
        main.click('play')
        self.assertEqual(a.playback_state, 'Playing')
        main.click('pause')
        self.assertEqual(a.playback_state, 'Paused')
        main.click('next')
        self.assertEqual(a.current_index, 1)
        main.slide('volume', 107 + 7, 60, final=True)          # far left: silent
        self.assertEqual(a.volume, 0.0)
        main.slide('balance', 177 + 7 + 24, 60, final=True)    # far right
        self.assertEqual(a.balance, 1.0)
        main.click('shuffle')
        self.assertEqual(a.shuffle, SHUFFLE_TRACKS)
        eq.slide('band0', 80, 38, final=True)                   # top of the 60 Hz slider
        self.assertEqual(a.eq_values[0], 1.0)
        self.assertEqual(a.eq_bars[0].get_value(), 12.0)
        eq.click('on')
        self.assertFalse(a.eq_enabled)
        playlist._press(playlist.area, self.click_event(30, 20 + 2 + 13 * 2 + 3))
        model, paths = a.playlist_view.get_selection().get_selected_rows()
        self.assertEqual([p.get_indices()[0] for p in paths], [2])
        playlist._press(playlist.area, self.click_event(30, 20 + 2 + 3, double=True))
        self.assertEqual(a.current_index, 0)
        self.assertTrue(a.is_playing)

    def test_classic_playlist_drag_moves_the_selection_as_one_undo_step(self):
        a = self.app
        a._add_paths(self.files)
        keys = list(a.entry_ids)
        a.set_skin('builtin')
        playlist = a._classic.playlist
        row_y = lambda index: 20 + 2 + 13 * index + 5
        playlist._press(playlist.area, self.click_event(30, row_y(0)))
        playlist._press(playlist.area, self.click_event(30, row_y(1), state=Gdk.ModifierType.SHIFT_MASK))
        motion = Gdk.EventMotion()
        motion.x, motion.y = 30, row_y(2)
        playlist._motion(playlist.area, motion)
        self.assertEqual(a.entry_ids, keys)                   # nothing moves until release
        release = self.click_event(30, row_y(2))
        release.type = Gdk.EventType.BUTTON_RELEASE
        playlist._release(playlist.area, release)
        self.assertEqual(a.entry_ids, [keys[2], keys[0], keys[1]])
        model, paths = a.playlist_view.get_selection().get_selected_rows()
        self.assertEqual([p.get_indices()[0] for p in paths], [1, 2])
        a.undo_playlist()
        self.assertEqual(a.entry_ids, keys)

    def test_classic_windows_paint_every_state(self):
        a = self.app
        a._add_paths(self.files)
        a.set_skin('builtin')
        classic = a._classic
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 600, 600)
        def paint_all():
            for window in classic.windows():
                window.paint(cairo.Context(surface), a.skin)
        paint_all()                                             # stopped
        a._play_index(0)
        a.audio_properties = dict(sample_rate=44100, bitrate=1411, channels=2)
        self.pump(.2)
        for mode in ('spectrum', 'scope'):
            a._set_appearance('vis_mode', mode)
            self.pump(.1)
            paint_all()
        a._toggle_time_mode()
        paint_all()
        for window in classic.windows():
            window.toggle_shade()
        paint_all()
        a.toggle_double_size()
        self.pump(.05)
        self.assertEqual(classic.main.area.get_size_request(), (550, 28))
        paint_all()

    def test_dropping_a_wsz_installs_and_switches_to_it(self):
        a = self.app
        default = build_default_skin()
        source = str(Path(self.directory.name, 'Green Dimension.wsz'))
        bmp = str(Path(self.directory.name, 'main.bmp'))
        default.sheets['MAIN'].savev(bmp, 'bmp', [], [])
        with zipfile.ZipFile(source, 'w') as archive:
            archive.write(bmp, 'main.bmp')
        class Dropped:
            def get_uris(self):
                return [GLib.filename_to_uri(source)]
        a.on_drag_data_received(a, None, 0, 0, Dropped(), 0, 0)   # dropping the .wsz installs it
        self.assertEqual(a.config['skin'], 'Green Dimension.wsz')
        self.assertIn('Green Dimension.wsz', a.installed_skins())
        self.assertEqual(a.skin.name, 'Green Dimension')
        self.assertIsNotNone(a._classic)

    def test_classic_scenes_match_golden_images(self):
        # Regenerate with tools/update_golden.py after an intended change
        a = self.app
        a.set_skin('builtin')
        for scene in SCENES:
            with self.subTest(scene=scene):
                changed = difference(render(a, scene), GOLDEN_DIR / f'{scene}.png')
                self.assertLessEqual(changed, .001, f'{scene}: {changed:.2%} of pixels changed')

    def test_corrupt_config_values_fall_back_to_defaults(self):
        a = self.app
        a.destroy()
        Path(a.config_path()).write_text(json.dumps({
            'alsa_output': False, 'tray_icon': False, 'update_check': False,
            'notifications': False, 'eq_values': [.5, .5], 'volume': 'loud',
            'shuffle': True, 'repeat': 'x', 'replaygain': ['track'], 'preamp': 9,
        }))
        self.app = a = MusicPlayer()
        a.show_all()
        self.pump(.05)
        self.assertEqual(a.eq_values, [.5] * 10)
        self.assertEqual(a.volume, .7)
        self.assertEqual(a.shuffle, SHUFFLE_TRACKS)
        self.assertEqual(a.repeat_mode, 0)
        self.assertEqual(a.replaygain, 'off')
        self.assertEqual(a.preamp_value, 1.0)

    def test_config_validation_and_layout_restore(self):
        a = self.app
        a.destroy()
        Path(a.config_path()).write_text(json.dumps({
            'alsa_output': False, 'tray_icon': False, 'update_check': False,
            'notifications': False, 'theme': [], 'palette': {}, 'falloff': 'bad',
            'expanded_size': ['oops', 1], 'window_pos': [-99999, -99999],
            'panels': {'eq': {'size': None, 'attached': False, 'position': ['bad', 1]},
                       'playlist': 'bad'}, 'eq_values': [.4] * 10,
        }))
        self.app = a = MusicPlayer()
        a.show_all()
        self.pump(.08)
        self.assertEqual(a.config['theme'], 'green')
        self.assertEqual(a.config['falloff'], 'normal')
        self.assertEqual(a.eq_values, [.4] * 10)
        self.assertFalse(a.panels.items['eq']['attached'])
        a._reset_layout()
        self.pump(.04)
        a.toggle_windowshade()
        self.pump(.04)
        a.destroy()
        self.app = a = MusicPlayer()
        a.show_all()
        self.pump(.1)
        self.assertTrue(a._windowshade)
        self.assertLessEqual(a.get_size()[1], 50)

    def test_shortcuts_search_and_ten_thousand_entries(self):
        a = self.app
        paths = [self.files[0]] * 10000
        a._add_paths(paths)
        self.assertEqual(len(set(a.entry_ids)), 10000)
        self.assertLessEqual(a._probe_queue.qsize(), 2)
        a.search_entry.set_text('no-such-song')
        a._search_step(True)
        self.assertEqual(a.search_count.get_text(), 'No matches')
        a.search_entry.set_text('0')
        a._search_step(True)
        self.assertIn('/10000', a.search_count.get_text())
        a._set_appearance('visualization', False)
        a.toggle_direct_mode()
        self.assertFalse(a.spectrum.get_property('post-messages'))
        a.playlist_view.grab_focus()
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval = Gdk.KEY_Down
        event.state = Gdk.ModifierType(0)
        self.assertFalse(a.on_window_key_press(a, event))
        event.keyval = Gdk.KEY_z
        event.state = Gdk.ModifierType.CONTROL_MASK
        self.assertTrue(a.on_window_key_press(a, event))
        self.assertFalse(a.playlist)

    def test_wayland_attachment_fallback(self):
        a = self.app
        a.panels.x11 = False  # Exercise fallback without depending on a compositor.
        a.panels.toggle_attach('playlist')
        self.pump(.02)
        self.assertFalse(a.panels.items['playlist']['attached'])
        a.panels.toggle_attach('playlist')
        self.assertTrue(a.panels.items['playlist']['attached'])

    def test_x11_panel_snap_and_group_move(self):
        a = self.app
        if not a.panels.x11:
            self.skipTest('requires X11')
        a.move(20, 20)
        a.panels.toggle_attach('eq')
        self.pump(.03)
        window = a.panels.items['eq']['window']
        window.move(20, 20 + a.get_size()[1] + 6)
        self.pump(.03)
        event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        event.state = Gdk.ModifierType(0)
        a.panels.start_drag(window, event)
        a.panels._settle()
        self.pump(.02)
        self.assertEqual(a.panels.items['eq']['snap_to'], 'main')
        self.assertEqual(window.get_position()[1], a.get_position()[1] + a.get_size()[1])
        origin = window.get_position()
        a.panels.start_drag(a, event)
        a.move(40, 40)
        self.pump(.04)
        self.assertEqual(tuple(window.get_position()), (origin[0] + 20, origin[1] + 20))
        event.state = Gdk.ModifierType.MOD1_MASK
        a.panels.start_drag(window, event)
        self.assertIsNone(a.panels.items['eq']['snap_to'])

    def test_all_invalid_audio_stops_and_sleep_beats_repeat(self):
        a = self.app
        invalid = [str(Path(self.directory.name, f'broken-{i}.mp3')) for i in range(2)]
        for path in invalid:
            Path(path).write_bytes(b'not an audio file')
        a._add_paths(invalid)
        a.shuffle = SHUFFLE_TRACKS
        a.repeat_mode = REPEAT_ALL
        a._play_index(0)
        self.pump(.2)
        self.assertFalse(a.is_playing)
        self.assertEqual(len(a.order.failed), 2)
        a._add_paths(self.files)
        a._play_index(2)
        a.repeat_mode = REPEAT_ONE
        a._set_sleep('track')
        self.assertIsNone(a._next_snapshot)
        a.advance_track(auto=True)
        self.assertFalse(a.is_playing)

    def test_output_unavailable_and_replaygain_rebuild(self):
        a = self.app
        with patch.object(a, '_make_alsa_sink', return_value=None):
            a._alsa_reacquire()
        self.assertFalse(a.alsa_output)
        a._add_paths(self.files)
        a._play_current()
        self.pump(.1)
        a.set_replaygain('track')
        self.pump(.05)
        self.assertEqual(a.replaygain, 'track')
        self.assertIsNotNone(a.rgvolume)
        a.toggle_direct_mode()
        self.pump(.05)
        self.assertIsNone(a.equalizer)
        self.assertIsNone(a.rgvolume)
        self.assertTrue(a.is_playing)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg generates test audio')
    def test_flac_mp3_and_http_stream(self):
        a = self.app
        for extension in ('flac', 'mp3'):
            target = str(Path(self.directory.name, 'encoded.' + extension))
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', self.files[0], target], check=True)
            a._add_paths([target])
            a._play_index(len(a.playlist) - 1)
            self.wait_for(lambda: a.player.get_state(0)[1] == Gst.State.PLAYING)
        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, *_args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=self.directory.name))
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{server.server_port}/encoded.mp3'
        a._add_paths([url])
        a._play_index(len(a.playlist) - 1)
        self.wait_for(lambda: a.player.get_state(0)[1] == Gst.State.PLAYING)
        self.assertFalse(a.position_scale.get_sensitive())
        a._pipeline_live = False
        a.on_bus_buffering(None, Gst.Message.new_buffering(a.player, 25))
        self.assertTrue(a._buffering)
        a.on_bus_buffering(None, Gst.Message.new_buffering(a.player, 100))
        self.assertFalse(a._buffering)
        self.assertTrue(a.is_playing)



class SkinTests(unittest.TestCase):
    def test_text_config_parsing_is_lenient(self):
        colors = parse_viscolor('0,0,0, // background\n255, 128,7 // dots\nnot a color\n', [(9, 9, 9)] * 24)
        self.assertEqual(colors[:3], [(0, 0, 0), (255, 128, 7), (9, 9, 9)])
        self.assertEqual(len(colors), 24)
        pledit = parse_pledit('[text]\nNormal=#00FF00\nCurrent=#FFFFFF\nNormalBG=junk\nFont=Tahoma\n')
        self.assertEqual(pledit['normal'], (0, 255, 0))
        self.assertEqual(pledit['normalbg'], (0, 0, 0))
        self.assertEqual(pledit['font'], 'Tahoma')
        self.assertEqual(parse_pledit('[broken')['font'], 'Arial')

    def test_region_polygons_parse_from_either_point_style(self):
        regions = parse_region('[Normal]\n; comment\nNumPoints=4, 3\n'
                               'PointList=0,0, 275,0, 275,116, 0,116, 1,1 5,1 1,5\n'
                               '[WindowShade]\nNumPoints=4\nPointList=0,0 275,0 275,14 0,14\n'
                               '[Equalizer]\nNumPoints=5\nPointList=0,0 1,1\n')
        self.assertEqual(regions['normal'], [[(0, 0), (275, 0), (275, 116), (0, 116)], [(1, 1), (5, 1), (1, 5)]])
        self.assertEqual(len(regions['windowshade']), 1)
        self.assertNotIn('equalizer', regions)          # fewer points than promised: ignored

    def test_glyph_fallbacks(self):
        self.assertEqual(glyph_cell('A'), FONT_LOOKUP['a'])
        self.assertEqual(glyph_cell('é'), FONT_LOOKUP['e'])
        self.assertEqual(glyph_cell('Å'), (2, 0))
        self.assertEqual(glyph_cell('☃'), FONT_LOOKUP[' '])

    def test_wsz_loads_case_insensitively_with_fallback_sheets(self):
        default = build_default_skin()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory, 'Tiny Skin.wsz'))
            with zipfile.ZipFile(path, 'w') as archive:
                for sheet, name in (('MAIN', 'Tiny/MAIN.BMP'), ('NUMBERS', 'Tiny/Numbers.bmp')):
                    bmp = str(Path(directory, sheet + '.bmp'))
                    default.sheets[sheet].savev(bmp, 'bmp', [], [])
                    archive.write(bmp, name)
                archive.writestr('Tiny/VISCOLOR.TXT', '1,2,3\n')
                archive.writestr('Tiny/pledit.txt', '[Text]\nFont=Tahoma\n')
            skin = Skin.load(path, default)
        self.assertEqual(skin.name, 'Tiny Skin')
        self.assertIsNot(skin.sheets['MAIN'], default.sheets['MAIN'])
        self.assertIs(skin.sheets['EQMAIN'], default.sheets['EQMAIN'])     # missing: built-in
        self.assertFalse(skin.has('NUMS_EX'))          # its own numbers.bmp wins
        self.assertEqual(skin.digit_sprite(4), 'DIGIT_4')
        self.assertEqual(skin.viscolors[0], (1, 2, 3))
        self.assertEqual(skin.viscolors[1:], default.viscolors[1:])
        self.assertEqual(skin.pledit['font'], 'Tahoma')
        self.assertIn('EQMAIN', skin.missing)
        self.assertEqual(skin.undecodable, [])

    def test_corrupt_sheets_fall_back_and_are_reported(self):
        default = build_default_skin()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory, 'broken.wsz'))
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('main.bmp', b'BM not really a bitmap')
            skin = Skin.load(path, default)
        self.assertEqual(skin.undecodable, ['MAIN'])
        self.assertIs(skin.sheets['MAIN'], default.sheets['MAIN'])


if __name__ == '__main__':
    unittest.main()
