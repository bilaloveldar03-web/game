import copy
import importlib.util
import json
from pathlib import Path
import unittest

P = Path(__file__).parent
spec = importlib.util.spec_from_file_location('collector', P/'collector.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.config = {'season': '2026-27', 'maxBytes': 4000000, 'aliases': {},
                       'leagues': [{'id': 'TEST', 'url': 'https://raw.githubusercontent.com/example/data/main/x.json'}]}
        self.catalog = {'version': 'test', 'clubs': [{'id': 1, 'name': 'One', 'league': 'TEST'}, {'id': 2, 'name': 'Two', 'league': 'TEST'}]}
        self.raw = {'matches': [{'team1': 'One', 'team2': 'Two', 'date': '2026-10-01', 'score': {'ft': [0, 0]}},
                                {'team1': 'Two', 'team2': 'One', 'date': '2027-02-01', 'score': []}]}

    def run_feed(self, raw=None, previous=None, loader=None):
        return c.collect(self.config, self.catalog, previous or {}, '2026-11-03',
                         loader=loader or (lambda *_: raw or self.raw))

    def test_zero_draw_is_result_missing_is_not(self):
        league = self.run_feed()['leagues']['TEST']
        self.assertEqual(league['dataAsOf'], '2026-10-01')
        self.assertEqual(league['status'], 'stale')
        self.assertEqual([r['pts'] for r in league['table']], [1, 1])
        self.assertIsNone(league['fixtures'][1]['score'])

    def test_failure_retains_previous_calendar_and_success_date(self):
        previous = self.run_feed()
        def fail(*_):
            raise TimeoutError()
        current = self.run_feed(previous=previous, loader=fail)['leagues']['TEST']
        self.assertEqual(current['fixtures'], previous['leagues']['TEST']['fixtures'])
        self.assertEqual(current['dataAsOf'], '2026-10-01')
        self.assertEqual(current['lastSuccess'], previous['leagues']['TEST']['lastSuccess'])
        self.assertEqual(current['status'], 'error')

    def test_bad_dates_scores_clubs_and_duplicates_rejected(self):
        variants = []
        for key, value in [('date', '2026-02-30'), ('score', {'ft': [1, -1]}), ('team1', 'Unknown')]:
            raw = copy.deepcopy(self.raw)
            raw['matches'][0][key] = value
            variants.append(raw)
        variants.append({'matches': [self.raw['matches'][0]]*2})
        variants.append({'matches': []})
        for raw in variants:
            self.assertEqual(self.run_feed(raw=raw)['leagues']['TEST']['status'], 'error')

    def test_market_does_not_use_download_date_as_valuation_date(self):
        raw = {'checkedAt': '2026-11-03', 'players': [{'transfermarktId': 123, 'name': 'Test', 'dateOfBirth': '2000-01-01',
               'valueEUR': 500000, 'valuationDate': '2025-09-01', 'sourceUrl': 'https://www.transfermarkt.com/test/profil/spieler/123'}]}
        market = c.parse_market(raw, '2026-11-03')
        self.assertEqual(market['status'], 'stale')
        self.assertEqual(market['dataAsOf'], '2025-09-01')
        raw['players'] *= 2
        with self.assertRaises(ValueError):
            c.parse_market(raw, '2026-11-03')

    def test_no_paid_or_arbitrary_hosts(self):
        for url in ['http://raw.githubusercontent.com/x', 'https://www.transfermarkt.com/x', 'https://raw.githubusercontent.com.evil.test/x', 'https://token@raw.githubusercontent.com/x']:
            with self.assertRaises(ValueError):
                c.check_url(url)


if __name__ == '__main__':
    unittest.main()
