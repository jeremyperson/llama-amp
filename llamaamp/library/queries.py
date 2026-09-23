"""Browsing the library like Winamp's Media Library: a view (all music or a
smart playlist), an optional search, and cascading genre > artist > album
filters. Facet values are None for untagged tracks."""
import re
import time

from ..constants import N_
from ..paths import real_path

# The artist facet groups compilations under their album artist
ARTIST = "COALESCE(NULLIF(album_artist, ''), NULLIF(artist, ''))"
FACETS = {'genre': "NULLIF(genre, '')", 'artist': ARTIST, 'album': "NULLIF(album, '')"}
CASCADE = ('genre', 'artist', 'album')
TRACK_ORDER = (f"{ARTIST} COLLATE NOCASE, album COLLATE NOCASE, COALESCE(disc, 0), "
               "COALESCE(track, 0), title COLLATE NOCASE, path")
RECENT_DAYS = 30

# view -> (caption, extra WHERE, ORDER BY, LIMIT)
VIEWS = {
    'all': (N_('All music'), '', TRACK_ORDER, None),
    'recent': (N_('Recently added'), 'added >= :recent_since', 'added DESC, ' + TRACK_ORDER, None),
    'most': (N_('Most played'), 'plays > 0', 'plays DESC, last_played DESC', 100),
    'unplayed': (N_('Never played'), 'plays = 0', TRACK_ORDER, None),
}


def fts_query(text):
    """Every word must match, as a prefix: 'whip ll' finds 'Whip It Good' by Llamas."""
    words = re.findall(r'\w+', text, re.UNICODE)
    return ' AND '.join(f'"{word}"*' for word in words)


def _where(db, view, search, filters, upto=None):
    """WHERE clause and parameters for a view, search text and the facet
    filters that come before `upto` in the cascade."""
    clauses, params = [], {'recent_since': time.time() - RECENT_DAYS * 86400}
    extra = VIEWS[view][1]
    if extra:
        clauses.append(extra)
    if search and search.strip():
        if db.fts and fts_query(search):
            clauses.append('rowid IN (SELECT rowid FROM tracks_fts WHERE tracks_fts MATCH :match)')
            params['match'] = fts_query(search)
        else:
            clauses.append("(title LIKE :like OR artist LIKE :like OR album_artist LIKE :like "
                           "OR album LIKE :like OR genre LIKE :like OR search_path LIKE :like)")
            params['like'] = f'%{search.strip()}%'
    for facet in CASCADE:
        if facet == upto:
            break
        if facet in filters:
            value = filters[facet]
            if value is None:
                clauses.append(f'{FACETS[facet]} IS NULL')
            else:
                clauses.append(f'{FACETS[facet]} = :{facet}')
                params[facet] = value
    return ('WHERE ' + ' AND '.join(clauses)) if clauses else '', params


def facet(db, name, view='all', search='', filters=None):
    """[(value, count)] for a facet column, narrowed by the facets before it."""
    where, params = _where(db, view, search, filters or {}, upto=name)
    column = FACETS[name]
    sql = (f'SELECT {column} AS value, COUNT(*) AS n FROM tracks {where} '
           f'GROUP BY value ORDER BY value IS NULL, value COLLATE NOCASE')
    return [(row['value'], row['n']) for row in db.execute(sql, params)]


def tracks(db, view='all', search='', filters=None):
    """Track rows for the view, search and all facet filters, in album order
    (or the view's own order)."""
    where, params = _where(db, view, search, filters or {})
    _caption, _extra, order, limit = VIEWS[view]
    sql = f'SELECT * FROM tracks {where} ORDER BY {order}' + (f' LIMIT {limit}' if limit else '')
    return [dict(row, path=real_path(row['path'])) for row in db.execute(sql, params)]
