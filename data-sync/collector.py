#!/usr/bin/env python3
"""Free, bounded football-data collector. Python stdlib; GitHub sources only.

No scraping, credentials, paid endpoints, or replacement of good data on failure.
Dates describe the source's football results, never the time of download.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def date(value):
    if not isinstance(value, str) or dt.date.fromisoformat(value).isoformat() != value:
        raise ValueError('Invalid date')
    return value


def norm(value):
    value = ''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(c))
    return ''.join(re.findall(r'\w+', re.sub(r'\b(fc|afc|ac|cf|sv|ud)\b', '', value)))


class GithubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def check_url(url):
    u = urllib.parse.urlparse(url)
    if u.scheme != 'https' or u.hostname != 'raw.githubusercontent.com' or u.username or u.password or u.port:
        raise ValueError('Only public raw.githubusercontent.com HTTPS sources are supported')


def fetch(url, limit):
    check_url(url)
    req = urllib.request.Request(url, headers={'User-Agent': 'FNL-Free-Data-Sync/1.0', 'Accept': 'application/json'})
    with urllib.request.build_opener(GithubRedirect()).open(req, timeout=25) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Source too large')
    return json.loads(data)


def standings(fixtures, team_ids, through):
    table = {i: {'club': i, 'p': 0, 'w': 0, 'd': 0, 'l': 0, 'gf': 0, 'ga': 0, 'pts': 0} for i in team_ids}
    for f in fixtures:
        if f['date'] > through or f['score'] is None:
            continue
        a, b = f['score']
        for i, gf, ga in [(f['home'], a, b), (f['away'], b, a)]:
            t = table[i]
            t['p'] += 1
            t['gf'] += gf
            t['ga'] += ga
            t['w'] += int(gf > ga)
            t['d'] += int(gf == ga)
            t['l'] += int(gf < ga)
            t['pts'] += 3 if gf > ga else int(gf == ga)
    # Display ordering only: competition tie-break rules and deductions not supplied.
    return sorted(table.values(), key=lambda t: (-t['pts'], -(t['gf']-t['ga']), -t['gf'], t['club']))


def parse_league(raw, league, config, catalog, today):
    teams = [c for c in catalog['clubs'] if c['league'] == league['id']]
    ids = {norm(c['name']): c['id'] for c in teams}
    if len(ids) != len(teams):
        raise ValueError('Ambiguous club catalog')
    aliases = config['aliases']
    fixtures, seen, pairs, used = [], set(), set(), set()
    rows = raw.get('matches')
    if not isinstance(rows, list) or len(rows) != len(teams)*(len(teams)-1):
        raise ValueError('Incomplete double round-robin calendar')
    start_year = int(config['season'][:4])
    for row in rows:
        h = ids.get(norm(aliases.get(row['team1'], row['team1'])))
        a = ids.get(norm(aliases.get(row['team2'], row['team2'])))
        if h is None or a is None or h == a:
            raise ValueError('Unknown or ambiguous club')
        d = date(row['date'])
        if not f'{start_year}-07-01' <= d <= f'{start_year+1}-07-01':
            raise ValueError('Fixture outside configured season')
        key = (h, a, d)
        if key in seen or (h, a) in pairs:
            raise ValueError('Duplicate fixture')
        seen.add(key)
        pairs.add((h, a))
        used.update([h, a])
        score = row.get('score')
        score = score.get('ft') if isinstance(score, dict) else None
        if score is not None:
            if not isinstance(score, list) or len(score) != 2 or any(type(n) is not int or not 0 <= n <= 30 for n in score) or d > today:
                raise ValueError('Invalid final score')
        round_match = re.search(r'\d+', str(row.get('round', '')))
        fixtures.append({'home': h, 'away': a, 'date': d, 'round': int(round_match[0]) if round_match else 0, 'score': score})
    if used != {c['id'] for c in teams}:
        raise ValueError('Club set does not match game catalog')
    fixtures.sort(key=lambda f: (f['date'], f['home'], f['away']))
    latest = max((f['date'] for f in fixtures if f['score'] is not None), default=None)
    missing = sum(f['date'] < (dt.date.fromisoformat(today)-dt.timedelta(days=3)).isoformat() and f['score'] is None for f in fixtures)
    stale = not latest or (dt.date.fromisoformat(today)-dt.date.fromisoformat(latest)).days > 7 or missing > 0
    return {'id': league['id'], 'season': config['season'], 'source': league['url'], 'license': 'CC0-1.0',
            'checkedAt': today, 'lastSuccess': today, 'dataAsOf': latest, 'status': 'stale' if stale else 'ok',
            'missingPastResults': missing, 'tableKind': 'calculated', 'clubIds': sorted(used), 'fixtures': fixtures,
            'table': standings(fixtures, used, today)}


def parse_market(raw, today):
    checked = date(raw['checkedAt'])
    if checked > today:
        raise ValueError('Future market check date')
    rows = raw['players']
    if not isinstance(rows, list) or not 0 < len(rows) <= 80000:
        raise ValueError('Invalid market rows')
    result, seen = [], set()
    for r in rows:
        i, amount = r['transfermarktId'], r['valueEUR']
        valuation = date(r['valuationDate'])
        born = date(r['dateOfBirth'])
        name = r['name']
        u = urllib.parse.urlparse(r['sourceUrl'])
        if (type(i) is not int or i < 1 or i in seen or not isinstance(name, str) or not 1 <= len(name) <= 120
            or type(amount) not in (int, float) or not 0 <= amount <= 1e9 or valuation > checked
            or u.scheme != 'https' or u.hostname not in ('www.transfermarkt.com', 'transfermarkt.com')
            or not re.search(r'/profil/spieler/'+str(i)+r'/?$', u.path)):
            raise ValueError('Invalid market identity, value or source')
        seen.add(i)
        result.append({'transfermarktId': i, 'name': name, 'dateOfBirth': born, 'valueEUR': amount,
                       'valuationDate': valuation, 'sourceUrl': r['sourceUrl'], 'checkedAt': checked})
    latest = max(r['valuationDate'] for r in result)
    fresh = sum((dt.date.fromisoformat(today)-dt.date.fromisoformat(r['valuationDate'])).days <= 120 for r in result)
    return {'status': 'ok' if fresh else 'stale', 'checkedAt': today, 'dataAsOf': latest,
            'lastSuccess': today, 'fresh': fresh, 'players': result, 'source': 'repository-inbox'}


def collect(config, catalog, previous, today, loader=fetch, inbox=None):
    output = {'schemaVersion': 1, 'catalogVersion': catalog['version'], 'season': config['season'],
              'checkedAt': today, 'leagues': {}, 'market': {}, 'unavailable': [
                  {'id': 'RPL', 'reason': 'Нет подключённого бесплатного источника текущего сезона'},
                  {'id': 'FNL', 'reason': 'Нет подключённого бесплатного источника текущего сезона'},
                  {'id': 'SECOND', 'reason': 'Нет подключённого бесплатного источника текущего сезона'}]}
    for league in config['leagues']:
        try:
            output['leagues'][league['id']] = parse_league(loader(league['url'], config['maxBytes']), league, config, catalog, today)
        except Exception as error:
            old = previous.get('leagues', {}).get(league['id'], {})
            output['leagues'][league['id']] = {**old, 'id': league['id'], 'checkedAt': today,
                                               'status': 'error', 'error': type(error).__name__}
    try:
        if inbox is None:
            raise FileNotFoundError('No authorized current market export')
        output['market'] = parse_market(inbox, today)
    except Exception as error:
        output['market'] = {**previous.get('market', {}), 'status': 'unavailable' if inbox is None else 'error',
                            'checkedAt': today, 'error': type(error).__name__,
                            'reason': 'Нет актуальной массовой выгрузки Transfermarkt; игровые цены сохранены'}
    digest_data = json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    output['revision'] = hashlib.sha256(digest_data.encode()).hexdigest()
    return output


def atomic_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.sync-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            f.write('\n')
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.root
    config = json.loads((root/'data-sync/config.json').read_text())
    catalog = json.loads((root/'data-sync/catalog.json').read_text())
    target = root/'data/live.json'
    previous = json.loads(target.read_text()) if target.exists() else {}
    inbox_path = root/config['marketInbox']
    inbox = json.loads(inbox_path.read_text()) if inbox_path.exists() else None
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    output = collect(config, catalog, previous, today, inbox=inbox)
    atomic_write(target, output)
    print(json.dumps({'checkedAt': today, 'leagues': {k: {'status': v['status'], 'dataAsOf': v.get('dataAsOf'),
                     'fixtures': len(v.get('fixtures', []))} for k, v in output['leagues'].items()},
                     'market': output['market']['status']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
