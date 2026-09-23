"""Playback order: session identities, the Play Next queue and history."""
import os
import random
from collections import deque

from .constants import REPEAT_ALL, REPEAT_ONE, SHUFFLE_ALBUMS, SHUFFLE_TRACKS


class PlaybackOrder:
    """Session identities, queue and actual playback history, independent of GTK."""
    def __init__(self):
        self.entries = []  # (session ID, path); duplicate paths remain distinct
        self.queue = deque()
        self.history = []
        self.cursor = -1
        self.current = None
        self.failed = set()

    def reconcile(self, entries):
        self.entries = list(entries)
        valid = {key for key, _ in self.entries}
        self.queue = deque(key for key in self.queue if key in valid)

    def next_id(self, playable, shuffle, repeat, auto=True, rng=random):
        available = [(key, path) for key, path in self.entries
                     if key not in self.failed and playable(path)]
        valid = {key for key, _ in available}
        if not valid:
            return None
        if auto and repeat == REPEAT_ONE and self.current in valid:
            return self.current
        for key in self.queue:
            if key in valid:
                return key
        for key in self.history[self.cursor + 1:]:
            if key in valid:
                return key
        if shuffle == SHUFFLE_TRACKS:
            return rng.choice([key for key, _ in available if key != self.current]
                              or list(valid))
        indices = {key: i for i, (key, _) in enumerate(self.entries)}
        index = indices.get(self.current, -1)
        if shuffle == SHUFFLE_ALBUMS:
            def album(path):
                return path if path.startswith(('http://', 'https://')) else os.path.dirname(path)
            current_album = album(self.entries[index][1]) if index >= 0 else None
            after = [(key, path) for key, path in available if indices[key] > index]
            if after and album(after[0][1]) == current_album:
                return after[0][0]
            runs = []
            previous = None
            for key, path in available:
                group = album(path)
                if group != previous:
                    runs.append((key, group))
                previous = group
            return rng.choice([r for r in runs if r[1] != current_album] or runs)[0]
        after = [key for key, _ in available if indices[key] > index]
        return after[0] if after else (available[0][0] if repeat == REPEAT_ALL else None)

    def previous_id(self, playable):
        paths = dict(self.entries)
        start = self.cursor
        if start >= 0 and self.history[start] == self.current:
            start -= 1
        for index in range(start, -1, -1):
            key = self.history[index]
            if key in paths and key not in self.failed and playable(paths[key]):
                self.cursor = index
                self.current = key
                return key
        return self.current

    def select(self, key):
        # Navigation consumes queue entries even while paused; history records
        # only tracks for which playback has actually been requested.
        if key in self.queue:
            self.queue.remove(key)
            self.history = self.history[:self.cursor + 1]
        self.current = key

    def commit(self, key):
        valid = dict(self.entries)
        if key not in valid:
            return
        queued = key in self.queue
        if queued:
            self.queue.remove(key)
        if self.cursor < 0 or self.history[self.cursor] != key:
            forward = self.history[self.cursor + 1:]
            if not queued and key in forward:
                self.cursor += forward.index(key) + 1
            else:
                self.history = self.history[:self.cursor + 1] + [key]
                self.history = self.history[-1000:]
                self.cursor = len(self.history) - 1
        self.current = key

