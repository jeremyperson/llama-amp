"""Application constants and tunables."""
import os
import re


# Directory containing musicPlayer.py: portable checkout or installed copy
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Application version
APP_VERSION = "1.7"
# Application name (Winamp-inspired, but an original name — "Winamp" is a trademark)
APP_NAME = "Llama Amp"


def N_(message):
    """Marks a string for translation where it's defined; _() translates it
    where it's shown (see llamaamp/i18n.py)."""
    return message

# Default display text
DEFAULT_SONG_TEXT = N_("{app} *** Please select a file ***")   # use i18n.default_song_text()
# Update check (GitHub releases)
UPDATE_API_URL = "https://api.github.com/repos/jeremyperson/llama-amp/releases/latest"
RELEASES_URL = "https://github.com/jeremyperson/llama-amp/releases"
UPDATE_CHECK_DELAY_S = 15        # let startup finish before touching the network
UPDATE_RECHECK_S = 86400         # long-running sessions re-check daily
# Inside Flatpak the store owns updates: no version checks, no update UI.
IS_FLATPAK = os.path.exists('/.flatpak-info')
WINDOW_W, WINDOW_H = 560, 740
POSITION_TIMER_MS = 100          # position/time UI refresh
SAVE_DEBOUNCE_MS = 1000          # debounce for writing config.json
DEFAULT_VOLUME = 0.7
EQ_GAIN_MIN, EQ_GAIN_MAX = -24.0, 12.0   # equalizer-10bands band range (dB)
EQ_BANDS = 10
SPECTRUM_BANDS = 512
DISPLAY_BANDS = 20
SCOPE_POINTS = 76                        # oscilloscope trace resolution (Winamp's scope width)
SCOPE_INTERVAL_S = .03                   # oscilloscope capture rate limit
SPECTRUM_INTERVAL_NS = 33_000_000
SPECTRUM_THRESHOLD = -80                 # dB floor for the analyzer
EQ_FREQUENCIES = ['60', '170', '310', '600', '1K', '3K', '6K', '12K', '14K', '16K']
REPEAT_OFF, REPEAT_ALL, REPEAT_ONE = 0, 1, 2
SHUFFLE_OFF, SHUFFLE_TRACKS, SHUFFLE_ALBUMS = 0, 1, 2
RG_MODES = ('off', 'track', 'album')
LISTENBRAINZ_API = "https://api.listenbrainz.org"   # module-level: test-patchable
NOTIFY_MIN_INTERVAL_S = 5
ALBUM_ART_SIZE = 72
FOLDER_ART_NAMES = ('cover', 'folder', 'front', 'album', 'albumart')
FOLDER_ART_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
META_CACHE_LIMIT = 64                    # LRU caps: probed metadata / embedded art
FOLDER_ART_CACHE_LIMIT = 32              # per-directory folder art
SEEK_STEP_SECONDS = 5                    # arrow-key seek step
VOLUME_STEP = 0.05                       # arrow-key / scroll volume step
EMPTY_PLAYLIST_HINT = N_("Drop audio files or folders here\nor use Add ▾")
CLASSIC_PLAYLIST_HINT = N_("Drop audio files or folders here\nor use the Add button")
URI_TARGET_INFO = 80                     # DnD info id for uri-list drops on the playlist
# Preamp: master gain ahead of the EQ bands (headroom for boosts), Winamp-style
PREAMP_DB_RANGE = 12.0                   # slider spans ±12 dB, 0.5 = unity
# Marquee: long titles scroll through a fixed window of the monospace LCD
# (char-count is a faithful width proxy; 44 matches song_label's width cap)
MARQUEE_TICK_MS = 150
MARQUEE_HOLD_TICKS = 13          # ~2 s pause at the start of each loop
MARQUEE_SEP = "  ***  "
MARQUEE_WINDOW = 44
# Debug logging is opt-in: LLAMAAMP_DEBUG=1 (stdout is discarded by the .desktop launcher anyway)
DEBUG = os.environ.get('LLAMAAMP_DEBUG') not in (None, '', '0')
# Spectrum magnitude parsing (PyGObject can't convert GstValueList; see _parse_magnitudes)
_MAG_LIST_RE = re.compile(r"magnitude=\(float\)\s*\{([^}]*)\}")
_MAG_ONE_RE = re.compile(r"magnitude=\(float\)\s*([-\d.eE+]+)")
# EQ presets in dB per band (60..16K); applied via db_to_eq_value
EQ_PRESETS = {
    N_("Flat"):         [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    N_("Rock"):         [5, 4, 3, 1, -1, -1, 1, 3, 4, 4],
    N_("Pop"):          [-1, 2, 4, 4, 2, 0, -1, -1, 1, 2],
    N_("Jazz"):         [3, 2, 1, 2, -1, -1, 0, 1, 2, 3],
    N_("Classical"):    [4, 3, 2, 0, -1, -1, 0, 2, 3, 4],
    N_("Bass Boost"):   [7, 6, 5, 3, 1, 0, 0, 0, 0, 0],
    N_("Treble Boost"): [0, 0, 0, 0, 0, 2, 4, 6, 7, 7],
}
