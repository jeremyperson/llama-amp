import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llamaamp.library import queries, scanner  # noqa: E402
from llamaamp.library.db import LibraryDB  # noqa: E402

DAY = 86400


def row(path, **tags):
    base = {'path': path, 'mtime': 1.0, 'size': 10, 'title': None, 'artist': None, 'album_artist': None,
            'album': None, 'disc': None, 'track': None, 'year': None, 'genre': None, 'duration': 100.0}
    base.update(tags)
    return base


class LibraryTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='llama-library-')
        self.addCleanup(self.directory.cleanup)
        self.db = LibraryDB(os.path.join(self.directory.name, 'library.db'))
        self.addCleanup(self.db.close)


class DatabaseTests(LibraryTestCase):
    def test_rescans_keep_added_time_and_play_counts(self):
        self.db.upsert([row('/m/a.flac', title='A', added=100.0)])
        self.assertTrue(self.db.record_play('/m/a.flac', when=500.0))
        self.db.upsert([row('/m/a.flac', title='A (Remaster)', mtime=2.0, added=900.0)])
        track = self.db.execute('SELECT * FROM tracks')[0]
        self.assertEqual((track['title'], track['added'], track['plays'], track['last_played']),
                         ('A (Remaster)', 100.0, 1, 500.0))
        self.assertFalse(self.db.record_play('/not/indexed.mp3'))

    def test_removing_a_folder_keeps_tracks_another_folder_covers(self):
        for folder in ('/music', '/music/rock', '/music2'):
            self.db.add_folder(folder)
        self.db.upsert([row('/music/x.mp3'), row('/music/rock/y.mp3'), row('/music2/z.mp3')])
        self.db.remove_folder('/music')
        self.assertEqual(sorted(self.db.known()), ['/music/rock/y.mp3', '/music2/z.mp3'])
        self.assertEqual(self.db.folders(), ['/music/rock', '/music2'])


class QueryTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        now = time.time()
        self.db.upsert([
            row('/m/1.flac', title='Whip It Good', artist='Llamas', album='Side A', genre='Rock',
                disc=1, track=2, added=now - 2 * DAY),
            row('/m/2.flac', title='Opening', artist='Llamas', album='Side A', genre='Rock',
                disc=1, track=1, added=now - 90 * DAY),
            row('/m/3.flac', title='Andes Sunrise', artist='Vicuña Club', album='Peaks', genre='Jazz',
                track=1, added=now - 90 * DAY),
            row('/m/4.flac', title='Guest Spot', artist='Alpacas', album_artist='Various Artists',
                album='Herd Hits', genre='Rock', track=1, added=now - 90 * DAY),
            row('/m/5.mp3', title='Untagged', added=now - 90 * DAY),
        ])
        self.db.record_play('/m/3.flac')
        self.db.record_play('/m/3.flac')
        self.db.record_play('/m/1.flac')

    def paths(self, **kwargs):
        return [track['path'] for track in queries.tracks(self.db, **kwargs)]

    def test_facets_cascade_with_untagged_last(self):
        self.assertEqual(queries.facet(self.db, 'genre'), [('Jazz', 1), ('Rock', 3), (None, 1)])
        self.assertEqual(queries.facet(self.db, 'artist', filters={'genre': 'Rock'}),
                         [('Llamas', 2), ('Various Artists', 1)])
        self.assertEqual(queries.facet(self.db, 'album', filters={'genre': 'Rock', 'artist': 'Llamas'}),
                         [('Side A', 2)])
        self.assertEqual(self.paths(filters={'genre': None}), ['/m/5.mp3'])

    def test_tracks_follow_album_order_and_views(self):
        self.assertEqual(self.paths(filters={'album': 'Side A'}), ['/m/2.flac', '/m/1.flac'])
        self.assertEqual(self.paths(view='recent'), ['/m/1.flac'])
        self.assertEqual(self.paths(view='most'), ['/m/3.flac', '/m/1.flac'])
        self.assertNotIn('/m/3.flac', self.paths(view='unplayed'))

    def test_search_matches_word_prefixes_and_ignores_accents(self):
        self.assertTrue(self.db.fts)
        self.assertEqual(self.paths(search='whip ll'), ['/m/1.flac'])
        self.assertEqual(self.paths(search='vicuna'), ['/m/3.flac'])
        self.assertEqual(self.paths(search='various'), ['/m/4.flac'])
        self.assertEqual(self.paths(search='  '), self.paths())

    def test_search_without_fts_falls_back_to_substrings(self):
        self.db.fts = False
        self.assertEqual(self.paths(search='Whip'), ['/m/1.flac'])
        self.assertEqual(self.paths(search='herd'), ['/m/4.flac'])


@unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg generates tagged fixtures')
class ScannerTests(LibraryTestCase):
    def make(self, relative, **tags):
        source = os.path.join(self.directory.name, 'tone.wav')
        if not os.path.exists(source):
            with wave.open(source, 'wb') as audio:
                audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                audio.writeframes(struct.pack('<h', 500) * 8000)
        target = os.path.join(self.music, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        metadata = sum((['-metadata', f'{key}={value}'] for key, value in tags.items()), [])
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', source, *metadata, target], check=True)
        return target

    def setUp(self):
        super().setUp()
        self.music = os.path.join(self.directory.name, 'Music')
        self.first = self.make('Llamas/Side A/01.flac', title='Opening', artist='Llamas', album='Side A',
                               track='1/9', disc='1/2', date='1997-05-01', genre='Rock', album_artist='Llamas')
        self.second = self.make('Llamas/Side A/02.flac', title='Whip It Good', artist='Llamas', album='Side A',
                                track='2/9')
        self.make('Loose/untagged.flac')
        Path(self.music, 'Llamas', 'cover.jpg').write_bytes(b'not audio')
        self.db.add_folder(self.music)

    def scan(self, **kwargs):
        worker = scanner.Scanner(self.db, self.db.folders(), **kwargs)
        worker.run()                    # synchronously, on this thread
        return worker.stats

    def test_scan_indexes_tags_then_rescans_incrementally(self):
        stats = self.scan()
        self.assertEqual((stats['scanned'], stats['updated']), (3, 3))
        track = self.db.execute('SELECT * FROM tracks WHERE path = ?', (self.first,))[0]
        self.assertEqual((track['title'], track['artist'], track['album_artist'], track['album'], track['track'],
                          track['disc'], track['year'], track['genre']),
                         ('Opening', 'Llamas', 'Llamas', 'Side A', 1, 1, 1997, 'Rock'))
        self.assertAlmostEqual(track['duration'], 1.0, places=1)
        self.assertEqual(self.scan()['updated'], 0)                 # nothing changed
        os.utime(self.second, (time.time() + 60, time.time() + 60))
        self.assertEqual(self.scan()['updated'], 1)
        os.remove(self.second)
        self.assertEqual(self.scan()['removed'], 1)
        self.assertEqual(self.db.count(), 2)

    def test_gstreamer_fallback_reads_the_same_tags(self):
        with patch.object(scanner, 'mutagen', None):
            tags = scanner.probe_tags(self.first)
        self.assertEqual((tags['title'], tags['artist'], tags['album'], tags['track'], tags['genre'], tags['year']),
                         ('Opening', 'Llamas', 'Side A', 1, 'Rock', 1997))

    def test_cancelled_scan_stops_early(self):
        worker = scanner.Scanner(self.db, self.db.folders())
        worker.cancel()
        worker.run()
        self.assertEqual(self.db.count(), 0)


if __name__ == '__main__':
    unittest.main()
