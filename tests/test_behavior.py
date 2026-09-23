import os
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import struct

from llamaamp.analyzer import AnalyzerState, ScopeState, decode_pcm
from llamaamp.order import PlaybackOrder
from llamaamp.ui.menus import parse_clock
from llamaamp.settings import SCHEMA, SettingsStore, dump_settings, load_settings
from llamaamp.constants import (SHUFFLE_OFF, SHUFFLE_TRACKS, SHUFFLE_ALBUMS,
                                REPEAT_OFF, REPEAT_ONE, REPEAT_ALL)


class OrderTests(unittest.TestCase):
    def setUp(self):
        self.order = PlaybackOrder()
        self.order.reconcile([('a', '/album/a'), ('b', '/album/b'),
                              ('c', '/other/c'), ('d', '/other/c')])
        self.order.commit('a')

    def next(self, shuffle=SHUFFLE_OFF, repeat=REPEAT_OFF, auto=True):
        return self.order.next_id(lambda _: True, shuffle, repeat, auto)

    def test_queue_wins_and_duplicate_occurrences_are_distinct(self):
        self.order.queue.extend(['d', 'c'])
        self.assertEqual(self.next(auto=False), 'd')
        self.order.commit('d')
        self.assertEqual(self.next(SHUFFLE_TRACKS), 'c')
        self.order.commit('c')
        self.assertEqual(list(self.order.queue), [])

    def test_repeat_one_only_automatic(self):
        self.assertEqual(self.next(repeat=REPEAT_ONE), 'a')
        self.assertEqual(self.next(repeat=REPEAT_ONE, auto=False), 'b')

    def test_end_and_wrap(self):
        self.order.commit('d')
        self.assertIsNone(self.next())
        self.assertEqual(self.next(repeat=REPEAT_ALL), 'a')

    def test_previous_and_forward_history(self):
        self.order.commit('c')
        self.order.commit('b')
        self.assertEqual(self.order.previous_id(lambda _: True), 'c')
        self.assertEqual(self.next(SHUFFLE_TRACKS), 'b')
        self.order.queue.append('d')
        self.assertEqual(self.next(SHUFFLE_TRACKS), 'd')

    def test_history_skips_removed_entries(self):
        self.order.commit('b')
        self.order.commit('c')
        self.order.reconcile([('a', '/a'), ('c', '/c')])
        self.assertEqual(self.order.previous_id(lambda _: True), 'a')

    def test_missing_tracks_and_all_missing(self):
        self.order.queue.append('b')
        self.assertEqual(self.order.next_id(lambda p: p != '/album/b', 0, 0), 'c')
        self.assertIsNone(self.order.next_id(lambda _: False, 0, 0))

    def test_album_order(self):
        self.assertEqual(self.next(SHUFFLE_ALBUMS), 'b')
        self.order.commit('b')
        self.assertEqual(self.next(SHUFFLE_ALBUMS), 'c')

    def test_reorder_preserves_identity_and_queue(self):
        self.order.commit('d')
        self.order.queue.append('b')
        self.order.reconcile(iter(reversed(self.order.entries)))
        self.assertEqual(self.order.current, 'd')
        self.assertEqual(list(self.order.queue), ['b'])


class AnalyzerTests(unittest.TestCase):
    def test_hold_and_decay(self):
        state = AnalyzerState()
        state.feed([0.0] * 512, 44100, 0)
        state.tick(0)
        self.assertEqual(state.peaks, [1.0] * 20)
        state.targets = [0.0] * 20
        state.tick(.2, playing=False)
        self.assertLess(state.levels[0], 1)
        self.assertEqual(state.peaks[0], 1)
        state.tick(.5, playing=False)
        self.assertAlmostEqual(state.peaks[0], .8375)
        state.tick(3, playing=False)
        self.assertFalse(any(state.levels + state.peaks))

    def test_decay_independent_of_frame_rate(self):
        values = []
        for fps in (15, 30, 60):
            state = AnalyzerState()
            state.feed([0.0] * 512, 48000, 0)
            state.tick(0)
            for i in range(1, fps + 1):
                state.tick(i / fps, playing=False)
            values.append(state.peaks[0])
        for value in values:
            self.assertAlmostEqual(value, values[0])

    def test_frequency_mapping_and_invalid_input(self):
        state = AnalyzerState()
        for rate in (22050, 44100, 48000, 96000):
            state.feed([-40] * 512, rate, 0)
            self.assertTrue(all(abs(v - .5) < 1e-6 for v in state.targets))
        state.feed([float('nan')] * 512, 48000, 0)
        self.assertFalse(any(state.targets))


class PersistenceTests(unittest.TestCase):
    def test_atomic_unicode_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'playlist.txt')
            SettingsStore.write(path, 'Björk / Jóga\n')
            self.assertEqual(Path(path).read_text(), 'Björk / Jóga\n')
            self.assertFalse(Path(path + '.tmp').exists())



class SchemaTests(unittest.TestCase):
    def test_missing_keys_take_defaults_and_unknown_keys_are_not_saved(self):
        cfg = load_settings({'future_option': 1})
        self.assertEqual(cfg['volume'], .7)
        self.assertEqual(cfg['expanded_size'], [560, 740])
        self.assertEqual(cfg['future_option'], 1)
        self.assertEqual(list(dump_settings(cfg)), list(SCHEMA))

    def test_each_bad_value_falls_back_on_its_own(self):
        cfg = load_settings({'volume': 2, 'balance': 'left', 'eq_values': [.1] * 3,
                             'shuffle': True, 'repeat': 4, 'window_pos': [1, float('nan')],
                             'theme': ['green'], 'palette': 'amber', 'direct_mode': 'yes',
                             'gapless': 0, 'playlist_name': '', 'panels': [],
                             'expanded_size': [100, 99999]})
        self.assertEqual(cfg['volume'], 1.0)
        self.assertEqual(cfg['balance'], 0.0)
        self.assertEqual(cfg['eq_values'], [.5] * 10)
        self.assertEqual(cfg['shuffle'], SHUFFLE_TRACKS)
        self.assertEqual(cfg['repeat'], REPEAT_ALL)
        self.assertIsNone(cfg['window_pos'])
        self.assertEqual(cfg['theme'], 'green')
        self.assertEqual(cfg['palette'], 'amber')
        self.assertIs(cfg['direct_mode'], False)
        self.assertIs(cfg['gapless'], True)
        self.assertIsNone(cfg['playlist_name'])
        self.assertEqual(cfg['panels'], {})
        self.assertEqual(cfg['expanded_size'], [440, 4000])

    def test_defaults_are_not_shared_between_loads(self):
        load_settings({})['eq_values'][0] = 1.0
        self.assertEqual(load_settings({})['eq_values'][0], .5)

    def test_dump_round_trips(self):
        cfg = load_settings({'window_pos': (3, 4), 'shuffle': 2, 'theme': 'amber'})
        data = dump_settings(cfg)
        self.assertEqual(data['window_pos'], [3, 4])
        self.assertEqual(dump_settings(load_settings(data)), data)



class ScopeTests(unittest.TestCase):
    def test_decodes_common_little_endian_formats(self):
        samples, full_scale = decode_pcm(struct.pack('<3h', 1, -2, 3), 'S16LE')
        self.assertEqual(list(samples), [1, -2, 3])
        self.assertEqual(full_scale, 32768.0)
        samples, full_scale = decode_pcm(struct.pack('<2f', .5, -.25) + b'\0', 'F32LE')
        self.assertEqual(list(samples), [.5, -.25])
        self.assertEqual(full_scale, 1.0)
        self.assertIsNone(decode_pcm(b'\0' * 6, 'S24LE'))

    def test_downmixes_and_decimates_to_fixed_points(self):
        scope = ScopeState()
        stereo = [16384, -16384] * 50 + [32767, 32767] * 50   # silence, then full scale
        scope.feed_samples(stereo, 2, 32768.0, now=1.0)
        self.assertEqual(len(scope.points), 76)
        self.assertEqual(scope.points[0], 0.0)
        self.assertAlmostEqual(scope.points[-1], 1.0, places=3)
        mono = [8192] * 10
        scope.feed_samples(mono, 1, 32768.0, now=1.0)
        self.assertEqual(scope.points, (.25,) * 10)

    def test_trace_clears_once_audio_stops(self):
        scope = ScopeState()
        scope.feed_samples([100] * 200, 1, 32768.0, now=1.0)
        self.assertTrue(scope.tick(1.1, playing=True))
        self.assertTrue(scope.points)
        self.assertTrue(scope.tick(1.5, playing=True))    # no input for .25 s
        self.assertEqual(scope.points, ())
        self.assertFalse(scope.tick(1.6, playing=True))
        scope.feed_samples([100] * 200, 1, 32768.0, now=2.0)
        scope.tick(2.0, playing=False)
        self.assertEqual(scope.points, ())



class ClockParseTests(unittest.TestCase):
    def test_accepts_seconds_minutes_and_hours(self):
        for text, seconds in [('90', 90), (' 1:30 ', 90), ('1:05', 65), ('01:02:03', 3723),
                              ('2.5', 2.5), ('0:07.5', 7.5), ('125:00', 7500)]:
            self.assertEqual(parse_clock(text), seconds, text)

    def test_rejects_malformed_times(self):
        for text in ['', 'abc', '-5', '1:-3', '1:75', '1:2:3:4', ':30', '1:', 'nan', 'inf']:
            self.assertIsNone(parse_clock(text), text)


if __name__ == '__main__':
    unittest.main()
