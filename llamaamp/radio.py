"""The internet radio directory at radio-browser.info: a free, community-run
list of stations. Blocking calls; the UI runs them on a worker thread."""
import json
import random
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass

from .constants import APP_VERSION

SERVERS_URL = 'https://all.api.radio-browser.info/json/servers'
FALLBACK_SERVER = 'https://de1.api.radio-browser.info'
TIMEOUT_S = 10
LIMIT = 100


@dataclass(frozen=True)
class Station:
    name: str
    url: str
    uuid: str = ''
    tags: str = ''
    country: str = ''
    codec: str = ''
    bitrate: int = 0
    votes: int = 0
    homepage: str = ''

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        """A saved favorite; None if it's damaged."""
        try:
            station = cls(**{key: data[key] for key in cls.__dataclass_fields__ if key in data})
            usable = isinstance(station.name, str) and station.name and station.url.startswith(('http://', 'https://'))
        except (TypeError, KeyError, AttributeError):
            return None
        return station if usable else None

    def quality(self):
        codec = self.codec if self.codec and self.codec.upper() != 'UNKNOWN' else ''
        if codec and self.bitrate:
            return f'{codec} {self.bitrate}k'
        return codec or (f'{self.bitrate}k' if self.bitrate else '')


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_stations(data):
    """Stations from the API's JSON list, skipping entries without a usable
    stream and repeats of the same stream."""
    stations, seen = [], set()
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        url = str(item.get('url_resolved') or item.get('url') or '').strip()
        name = ' '.join(str(item.get('name') or '').split())
        if not name or not url.startswith(('http://', 'https://')) or url in seen:
            continue
        seen.add(url)
        tags = ', '.join(tag.strip() for tag in str(item.get('tags') or '').split(',') if tag.strip())
        stations.append(Station(uuid=str(item.get('stationuuid') or ''), name=name, url=url, tags=tags,
                                country=str(item.get('countrycode') or '').upper(),
                                codec=str(item.get('codec') or ''), bitrate=_int(item.get('bitrate')),
                                votes=_int(item.get('votes')), homepage=str(item.get('homepage') or '')))
    return stations


class RadioBrowser:
    """Picks one of the directory's mirrors per session, as its API asks."""

    def __init__(self, server=None):
        self.server = server.rstrip('/') if server else None

    def _get(self, url):
        request = urllib.request.Request(url, headers={'User-Agent': f'LlamaAmp/{APP_VERSION}',
                                                       'Accept': 'application/json'})
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return json.loads(response.read().decode('utf-8'))

    def _base(self):
        if self.server is None:
            try:
                names = [entry['name'] for entry in self._get(SERVERS_URL) if entry.get('name')]
                self.server = f'https://{random.choice(names)}' if names else FALLBACK_SERVER
            except Exception:
                self.server = FALLBACK_SERVER
        return self.server

    def _search(self, **params):
        query = dict(params, limit=LIMIT, hidebroken='true', order='votes', reverse='true')
        return parse_stations(self._get(f'{self._base()}/json/stations/search?' + urllib.parse.urlencode(query)))

    def top(self):
        return self._search()

    def search(self, text):
        """Stations whose name or tags match, name matches first."""
        text = text.strip()
        by_name = self._search(name=text)
        by_tag = self._search(tag=text.lower())
        seen = {station.url for station in by_name}
        return by_name + [station for station in by_tag if station.url not in seen]

    def count_click(self, uuid):
        """Tell the directory a station was played (its popularity ranking)."""
        if uuid:
            self._get(f'{self._base()}/json/url/{urllib.parse.quote(uuid)}')
