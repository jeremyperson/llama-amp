"""File paths that aren't valid UTF-8.

Linux filenames are bytes; Python represents undecodable bytes as lone
surrogates, which GTK, SQLite and UTF-8 files all reject. Paths cross those
boundaries in a reversible text form, and are shown with the bad bytes
replaced.
"""
import os

PREFIX = 'bytes:'        # real paths are absolute, so they never start with this


def store_path(path):
    """A form GTK and SQLite accept, reversible with real_path()."""
    try:
        path.encode('utf-8')
        return path
    except UnicodeEncodeError:
        return PREFIX + os.fsencode(path).hex()


def real_path(text):
    if text.startswith(PREFIX):
        return os.fsdecode(bytes.fromhex(text[len(PREFIX):]))
    return text


def displayable(text):
    """Text safe to show: undecodable bytes become U+FFFD."""
    return text.encode('utf-8', 'surrogateescape').decode('utf-8', 'replace')
