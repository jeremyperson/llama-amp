import os
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llamaamp.analyzer import AnalyzerState
from llamaamp.order import PlaybackOrder
from llamaamp.settings import SettingsStore
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


if __name__ == '__main__':
    unittest.main()
