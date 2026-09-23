"""Atomic settings/playlist file writes."""
import os


class SettingsStore:
    """Atomic persistence shared by settings, playlists and duration caches."""
    @staticmethod
    def write(path, data, binary=False):
        tmp = path + '.tmp'
        try:
            with open(tmp, 'wb' if binary else 'w', **({} if binary else {'encoding': 'utf-8'})) as stream:
                stream.write(data)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

