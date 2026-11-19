"""Football data API v4 adapter for the game's supported free competitions."""

import datetime as dt
import json
import re
import time
import unicodedata
import urllib.request
from zoneinfo import ZoneInfo


COMPETITIONS = {
    'EPL': 'PL',
    'ENG2': 'ELC',
    'BUND': 'BL1',
    'LALIGA': 'PD',
    'L1': 'FL1',
    'SERIEA': 'SA',
    'ERED': 'DED',
    'POR': 'PPL',
}

TIME_ZONES = {
    'EPL': 'Europe/London', 'ENG2': 'Europe/London', 'BUND': 'Europe/Berlin',
    'LALIGA': 'Europe/Madrid', 'L1': 'Europe/Paris', 'SERIEA': 'Europe/Rome',
    'ERED': 'Europe/Amsterdam', 'POR': 'Europe/Lisbon',
}

API_BASE = 'https://api.football-data.org/v4'
SOURCE_URL = 'https://www.football-data.org/coverage'
STATUS = {
    'FINISHED': 'FT', 'AWARDED': 'FT', 'TIMED': 'scheduled', 'SCHEDULED': 'scheduled',
    'IN_PLAY': 'in_progress', 'PAUSED': 'in_progress', 'POSTPONED': 'postponed',
    'SUSPENDED': 'suspended', 'CANCELLED': 'cancelled',
}
REGULAR_SEASON = {None, 'REGULAR_SEASON'}

# Exact aliases for display names that differ from both the game's club names
# and the common English forms in config.json. Short names are also checked.
PROVIDER_NAMES = {
    'Brighton Hove Albion': 'Brighton & Hove Albion',
    'Wolverhampton': 'Wolverhampton Wanderers',
    '1. FC Köln': 'FC Koln',
    '1. FSV Mainz 05': 'Mainz 05',
    'RasenBallsport Leipzig': 'RB Leipzig',
    'Athletic Bilbao': 'Athletic Club',
    'RCD Espanyol de Barcelona': 'Espanyol',
    'RC Deportivo de La Coruña': 'Deportivo La Coruna',
    'Real Club Deportivo de La Coruña': 'Deportivo La Coruna',
    'Real Racing Club de Santander': 'Racing Santander',
    'Paris Saint-Germain': 'PSG',
    'Paris Saint-Germain FC': 'PSG',
    'Paris SG': 'PSG',
    'Paris Football Club': 'Paris FC',
    'Stade Brestois 29': 'Brest',
    'Stade Rennais': 'Rennes',
    'Le Mans FC': 'Le Mans',
    'FC Lorient Bretagne Sud': 'Lorient',
    'Angers Sporting Club de l’Ouest': 'Angers',
    'Olympique Lyonnais': 'Lyon',
    'Internazionale': 'Inter',
    'FC Internazionale Milano': 'Inter',
    'Parma Calcio 1913': 'Parma',
    'Calcio Como': 'Como',
    'Como 1907': 'Como',
    'AC Monza': 'Monza',
    'Frosinone Calcio': 'Frosinone',
    'NEC': 'NEC Nijmegen',
    "FC Twente '65": 'FC Twente',
    'SC Cambuur-Leeuwarden': 'SC Cambuur',
    'Sportclub Telstar': 'Telstar',
    'SC Telstar': 'Telstar',
    'Vitória SC': 'Vitoria Guimaraes',
    'Vitória Guimarães': 'Vitoria Guimaraes',
    'Porto FC': 'Porto',
    'Sporting Clube de Portugal': 'Sporting CP',
    'Académico de Viseu FC': 'Académico de Viseu',
    'Académico Viseu FC': 'Académico de Viseu',
    'Académico Viseu': 'Académico de Viseu',
    'Estoril Praia': 'Estoril',
    'Club Sport Marítimo': 'Marítimo',
    'CS Marítimo': 'Marítimo',
    'SC Marítimo': 'Marítimo',
}


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # In particular, never forward our X-Auth-Token to another host.
        return None


class FootballDataClient:
    def __init__(self, token, limit=4_000_000, *, opener=None, clock=time.monotonic, sleep=time.sleep):
        if not isinstance(token, str) or not re.fullmatch(r'[\x21-\x7e]{1,256}', token):
            raise ValueError('Invalid Football-Data.org token configuration')
        self._token = token
        self._limit = limit
        self._opener = opener or urllib.request.build_opener(_RejectRedirect())
        self._clock = clock
        self._sleep = sleep
        self._last_request = None

    def __call__(self, path):
        # Do not accept host, scheme, or arbitrary paths from config or payloads.
        paths = '|'.join(COMPETITIONS.values())
        if not re.fullmatch(rf'/competitions/(?:{paths})/(?:matches|standings)\?season=\d{{4}}', path):
            raise ValueError('Invalid Football-Data.org route')
        if self._last_request is not None:
            delay = self._last_request + 6.25 - self._clock()
            if delay > 0:
                self._sleep(delay)
        req = urllib.request.Request(API_BASE + path, headers={
            'User-Agent': 'FNL-Manager-Data-Sync/1.0',
            'Accept': 'application/json',
            'X-Auth-Token': self._token,
        })
        self._last_request = self._clock()
        with self._opener.open(req, timeout=20) as response:
            body = response.read(self._limit + 1)
        if len(body) > self._limit:
            raise ValueError('Football-Data.org response too large')
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError('Invalid Football-Data.org response')
        return value


def _club_key(name):
    if not isinstance(name, str):
        return ''
    name = ''.join(c for c in unicodedata.normalize('NFKD', name.casefold()) if not unicodedata.combining(c))
    words = re.findall(r'[a-z0-9]+', name)
    if words == ['paris', 'fc']:
        return 'parisfc'
    affixes = {'afc', 'ac', 'as', 'cf', 'fc', 'rc', 'sc', 'sv', 'ud', 'us'}
    while words and words[0] in affixes:
        words = words[1:]
    while words and words[-1] in affixes:
        words.pop()
    return ''.join(words)


def _club_names(league_id, catalog, config):
    clubs = [c for c in catalog['clubs'] if c['league'] == league_id]
    canonical = {_club_key(c['name']): c['id'] for c in clubs}
    if len(canonical) != len(clubs) or not clubs:
        raise ValueError('Ambiguous game club catalog')
    aliases = dict(canonical)
    for name, target in {**config.get('aliases', {}), **PROVIDER_NAMES}.items():
        key = _club_key(name)
        club = canonical.get(_club_key(target))
        if club is None:
            continue
        if key in aliases and aliases[key] != club:
            raise ValueError(f'Ambiguous club alias: {name}')
        aliases[key] = club
    return aliases, set(canonical.values())


def _team_id(obj):
    key = obj.get('id')
    if type(key) is not int or key < 1:
        raise ValueError('Football-Data.org team identifier missing')
    return key


def _team_name(obj, names, *, required):
    choices = {names[k] for field in ('name', 'shortName') if (k := _club_key(obj.get(field))) in names}
    if len(choices) > 1 or required and not choices:
        raise ValueError(f'Unknown or ambiguous football-data.org club: {str(obj.get("name"))[:120]}')
    return next(iter(choices), None)


def _verify_competition(raw, code, year):
    if raw.get('competition', {}).get('code') != code:
        raise ValueError('Football-Data.org competition mismatch')
    reported = raw.get('filters', {}).get('season')
    if reported is not None and str(reported) != year:
        raise ValueError('Football-Data.org season mismatch')


def _kickoff(value, zone, start, end):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|\+00:00)', value):
        raise ValueError('Invalid UTC kickoff')
    timestamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    day = timestamp.astimezone(zone).date().isoformat()
    if not start <= day <= end:
        raise ValueError('Fixture outside game season')
    return day, timestamp.isoformat().replace('+00:00', 'Z')


def _total_standings(payload):
    rows = [row['table'] for row in payload['standings']
            if row.get('type') == 'TOTAL' and not row.get('group') and row.get('stage') in REGULAR_SEASON]
    if len(rows) != 1 or not isinstance(rows[0], list):
        raise ValueError('Expected one regular season standings table')
    return rows[0]


def _fixtures(payload, teams, names, league, year, today):
    fixtures, fixture_ids, pairs = [], set(), set()
    rows = payload['matches']
    if not isinstance(rows, list) or len(rows) > 2_000:
        raise ValueError('Invalid football-data.org matches list')
    zone = ZoneInfo(TIME_ZONES[league])
    start, end = f'{year}-07-01', f'{int(year) + 1}-07-01'
    for row in rows:
        if row.get('stage') not in REGULAR_SEASON:
            continue
        home_team, away_team = row['homeTeam'], row['awayTeam']
        home, away = teams.get(_team_id(home_team)), teams.get(_team_id(away_team))
        if home is None or away is None or home == away or (home, away) in pairs:
            raise ValueError('Unknown club or duplicate fixture pairing')
        for team, game_id in [(home_team, home), (away_team, away)]:
            matched = _team_name(team, names, required=False)
            if matched is not None and matched != game_id:
                raise ValueError('Conflicting football-data.org fixture club identifier')
        key = row['id']
        if type(key) is not int or key < 1 or key in fixture_ids:
            raise ValueError('Invalid or duplicate football-data.org match identifier')
        day, kickoff = _kickoff(row['utcDate'], zone, start, end)
        provider_status = row['status']
        if provider_status not in STATUS:
            raise ValueError('Unknown football-data.org match status')
        status = STATUS[provider_status]
        score = None
        if status == 'FT':
            full_time = row['score']['fullTime']
            score = [full_time['home'], full_time['away']]
            if day > today or any(type(v) is not int or not 0 <= v <= 30 for v in score):
                raise ValueError('Invalid football-data.org final score')
        round_no = row.get('matchday')
        if round_no is None:
            round_no = 0
        if type(round_no) is not int or not 0 <= round_no <= 100:
            raise ValueError('Invalid football-data.org matchday')
        fixtures.append({'home': home, 'away': away, 'date': day, 'round': round_no, 'score': score,
                         'source': SOURCE_URL, 'status': status, 'providerId': key,
                         'providerStatus': provider_status, 'kickoff': kickoff})
        fixture_ids.add(key)
        pairs.add((home, away))
    if len(pairs) != len(teams) * (len(teams) - 1):
        raise ValueError('Incomplete football-data.org league schedule')
    return sorted(fixtures, key=lambda row: (row['date'], row['kickoff'], row['providerId']))


def _standings(rows, teams, fixtures, compute_standings, today):
    computed = {row['club']: row for row in compute_standings(fixtures, teams.values(), today)}
    fields = {'p': 'playedGames', 'w': 'won', 'd': 'draw', 'l': 'lost', 'gf': 'goalsFor', 'ga': 'goalsAgainst'}
    result = []
    for row in rows:
        club = teams[_team_id(row['team'])]
        totals = {label: row[field] for label, field in fields.items()}
        pts, rank = row['points'], row['position']
        if any(type(value) is not int or value != computed[club][label] for label, value in totals.items()):
            raise ValueError('Football-Data.org standings do not reconcile with final scores')
        if type(pts) is not int or type(rank) is not int:
            raise ValueError('Invalid football-data.org points or position')
        result.append({'club': club, **totals, 'pts': pts, 'rank': rank,
                       'adjustment': pts - computed[club]['pts']})
    if {row['rank'] for row in result} != set(range(1, len(teams) + 1)):
        raise ValueError('Incomplete football-data.org rank positions')
    return sorted(result, key=lambda row: row['rank'])


def load_league(client, league_id, config, catalog, today, *, calculate_standings=None):
    """Load and normalize one complete, consistent v2 league component."""
    if calculate_standings is None:
        from collector import standings as calculate_standings
    code = COMPETITIONS[league_id]
    season = config['season']
    if not re.fullmatch(r'\d{4}-\d{2}', season) or int(season[-2:]) != (int(season[:4]) + 1) % 100:
        raise ValueError('Invalid game season')
    year = season[:4]
    names, game_clubs = _club_names(league_id, catalog, config)
    matches = client(f'/competitions/{code}/matches?season={year}')
    _verify_competition(matches, code, year)
    table_payload = client(f'/competitions/{code}/standings?season={year}')
    _verify_competition(table_payload, code, year)
    rows = _total_standings(table_payload)
    teams = {_team_id(row['team']): _team_name(row['team'], names, required=True) for row in rows}
    if len(rows) != len(game_clubs) or len(teams) != len(rows) or set(teams.values()) != game_clubs:
        raise ValueError('Football-Data.org standings club set differs from the game')
    fixtures = _fixtures(matches, teams, names, league_id, year, today)
    final_dates = [row['date'] for row in fixtures if row['score'] is not None]
    if not final_dates:
        raise ValueError('League has no finished matches for this snapshot')
    table = _standings(rows, teams, fixtures, calculate_standings, today)
    return {'id': league_id, 'season': season, 'source': SOURCE_URL, 'sources': [SOURCE_URL],
            'provider': 'football-data.org', 'checkedAt': today, 'lastSuccess': today,
            'verifiedAt': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
            'dataAsOf': max(final_dates), 'status': 'ok', 'tableKind': 'verified',
            'clubIds': sorted(game_clubs), 'fixtures': fixtures, 'table': table}
