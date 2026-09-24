"""The media library index: a SQLite database of the tracks under the watched
folders, with full-text search (FTS5) when SQLite provides it."""
import os
import sqlite3
import threading
import time

from ..paths import displayable, real_path, store_path

SCHEMA_VERSION = 1
TAG_COLUMNS = ('title', 'artist', 'album_artist', 'album', 'disc', 'track', 'year', 'genre', 'duration')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS folders (path TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS tracks (
    path TEXT PRIMARY KEY,
    search_path TEXT,          -- the path as readable text, for search
    mtime REAL, size INTEGER,
    title TEXT, artist TEXT, album_artist TEXT, album TEXT,
    disc INTEGER, track INTEGER, year INTEGER, genre TEXT, duration REAL,
    added REAL, plays INTEGER NOT NULL DEFAULT 0, last_played REAL,
    rg_track REAL, rg_album REAL
);
CREATE INDEX IF NOT EXISTS tracks_album ON tracks(album);
CREATE INDEX IF NOT EXISTS tracks_artist ON tracks(artist);
CREATE INDEX IF NOT EXISTS tracks_added ON tracks(added);
-- measured ReplayGain track gain of untagged files (NULL: tagged or unreadable)
CREATE TABLE IF NOT EXISTS loudness (path TEXT PRIMARY KEY, mtime REAL, size INTEGER, gain REAL);
'''

FTS_SCHEMA = '''
CREATE VIRTUAL TABLE IF NOT EXISTS tracks_fts USING fts5(
    title, artist, album_artist, album, genre, search_path,
    content='tracks', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS tracks_ai AFTER INSERT ON tracks BEGIN
    INSERT INTO tracks_fts(rowid, title, artist, album_artist, album, genre, search_path)
    VALUES (new.rowid, new.title, new.artist, new.album_artist, new.album, new.genre, new.search_path);
END;
CREATE TRIGGER IF NOT EXISTS tracks_ad AFTER DELETE ON tracks BEGIN
    INSERT INTO tracks_fts(tracks_fts, rowid, title, artist, album_artist, album, genre, search_path)
    VALUES ('delete', old.rowid, old.title, old.artist, old.album_artist, old.album, old.genre, old.search_path);
END;
CREATE TRIGGER IF NOT EXISTS tracks_au AFTER UPDATE ON tracks BEGIN
    INSERT INTO tracks_fts(tracks_fts, rowid, title, artist, album_artist, album, genre, search_path)
    VALUES ('delete', old.rowid, old.title, old.artist, old.album_artist, old.album, old.genre, old.search_path);
    INSERT INTO tracks_fts(rowid, title, artist, album_artist, album, genre, search_path)
    VALUES (new.rowid, new.title, new.artist, new.album_artist, new.album, new.genre, new.search_path);
END;
'''


def _fts_available(conn):
    try:
        conn.execute('CREATE VIRTUAL TABLE temp.fts_probe USING fts5(x)')
        conn.execute('DROP TABLE temp.fts_probe')
        return True
    except sqlite3.OperationalError:
        return False


def under(path, folder):
    """Whether path lies inside folder (by path components, not prefix)."""
    folder = folder.rstrip(os.sep) + os.sep
    return path.startswith(folder)


class LibraryDB:
    """Thread-safe: the scanner writes from its thread while the UI reads."""

    def __init__(self, path):
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.fts = _fts_available(self.conn)
        with self.lock, self.conn:
            self.conn.executescript(SCHEMA)
            if self.fts:
                self.conn.executescript(FTS_SCHEMA)
            self.conn.execute(f'PRAGMA user_version={SCHEMA_VERSION}')

    def close(self):
        with self.lock:
            self.conn.close()

    def execute(self, sql, params=()):
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    # -- folders ------------------------------------------------------------
    def folders(self):
        return [real_path(row['path']) for row in self.execute('SELECT path FROM folders ORDER BY path')]

    def add_folder(self, path):
        with self.lock, self.conn:
            self.conn.execute('INSERT OR IGNORE INTO folders(path) VALUES (?)', (store_path(os.path.abspath(path)),))

    def remove_folder(self, path):
        """Forget a folder and the tracks only it provided."""
        path = os.path.abspath(path)
        with self.lock, self.conn:
            self.conn.execute('DELETE FROM folders WHERE path = ?', (store_path(path),))
            others = [real_path(row['path']) for row in self.conn.execute('SELECT path FROM folders')]
            doomed = [row['path'] for row in self.conn.execute('SELECT path FROM tracks')
                      if under(real_path(row['path']), path)
                      and not any(under(real_path(row['path']), other) for other in others)]
            self.conn.executemany('DELETE FROM tracks WHERE path = ?', [(p,) for p in doomed])

    # -- tracks -------------------------------------------------------------
    def known(self):
        """path -> (mtime, size) for every indexed track."""
        return {real_path(row['path']): (row['mtime'], row['size'])
                for row in self.execute('SELECT path, mtime, size FROM tracks')}

    def has(self, path):
        return bool(self.execute('SELECT 1 FROM tracks WHERE path = ?', (store_path(path),)))

    def upsert(self, rows):
        """rows: dicts with path, mtime, size and TAG_COLUMNS. New tracks get an
        'added' time; play counts and ratings of existing ones are kept."""
        now = time.time()
        columns = ('path', 'search_path', 'mtime', 'size') + TAG_COLUMNS
        sql = (f"INSERT INTO tracks({', '.join(columns)}, added) VALUES ({', '.join('?' * len(columns))}, ?) "
               f"ON CONFLICT(path) DO UPDATE SET "
               + ', '.join(f'{column} = excluded.{column}' for column in columns[1:]))
        with self.lock, self.conn:
            self.conn.executemany(sql, [(store_path(row['path']), displayable(row['path']))
                                        + tuple(row.get(column) for column in columns[2:])
                                        + (row.get('added', now),) for row in rows])

    def remove(self, paths):
        with self.lock, self.conn:
            self.conn.executemany('DELETE FROM tracks WHERE path = ?', [(store_path(p),) for p in paths])

    def record_play(self, path, when=None):
        """Count a completed listen; False if the track isn't in the library."""
        with self.lock, self.conn:
            cursor = self.conn.execute('UPDATE tracks SET plays = plays + 1, last_played = ? WHERE path = ?',
                                       (when or time.time(), store_path(path)))
            return cursor.rowcount > 0

    # -- loudness -----------------------------------------------------------
    def loudness(self, path, stamp):
        """(gain,) measured for this version of the file, or None if unknown."""
        rows = self.execute('SELECT gain FROM loudness WHERE path = ? AND mtime = ? AND size = ?',
                            (store_path(path),) + tuple(stamp))
        return (rows[0]['gain'],) if rows else None

    def set_loudness(self, path, stamp, gain):
        with self.lock, self.conn:
            self.conn.execute('INSERT OR REPLACE INTO loudness(path, mtime, size, gain) VALUES (?, ?, ?, ?)',
                              (store_path(path),) + tuple(stamp) + (gain,))

    def count(self):
        return self.execute('SELECT COUNT(*) AS n FROM tracks')[0]['n']
