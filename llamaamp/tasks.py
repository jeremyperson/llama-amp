"""GLib main-loop source bookkeeping."""
import threading

from gi.repository import GLib


class MainLoopTasks:
    """One lifetime for timers and cross-thread callbacks owned by the player."""
    def __init__(self):
        self.ids = set()
        self.closed = False
        self.lock = threading.RLock()

    def _add(self, factory, prefix, callback, args):
        with self.lock:
            if self.closed:
                return None
            handle = [None]
            def invoke():
                with self.lock:
                    if self.closed:
                        return False
                keep = False
                try:
                    keep = bool(callback(*args))
                    return keep
                finally:
                    if not keep:
                        with self.lock:
                            self.ids.discard(handle[0])
            handle[0] = factory(*prefix, invoke)
            self.ids.add(handle[0])
            return handle[0]

    def idle_add(self, callback, *args):
        return self._add(GLib.idle_add, (), callback, args)

    def timeout_add(self, interval, callback, *args):
        return self._add(GLib.timeout_add, (interval,), callback, args)

    def timeout_add_seconds(self, interval, callback, *args):
        return self._add(GLib.timeout_add_seconds, (interval,), callback, args)

    def unix_signal_add(self, priority, sig, callback):
        return self._add(GLib.unix_signal_add, (priority, sig), callback, ())

    def source_remove(self, handle):
        with self.lock:
            if handle in self.ids:
                GLib.source_remove(handle)
                self.ids.discard(handle)

    def close(self):
        with self.lock:
            self.closed = True
            for handle in list(self.ids):
                self.source_remove(handle)

