import copy
import io
import json
from pathlib import Path
import unittest

from football_data import COMPETITIONS, FootballDataClient, _club_names, load_league


TEAM_A = {'id': 86, 'name': 'Real Madrid CF', 'shortName': 'Real Madrid'}
TEAM_B = {'id': 81, 'name': 'FC Barcelona', 'shortName': 'Barça'}


class FootballDataTest(unittest.TestCase):
    def setUp(self):
        self.config = {'season': '2026-27'}
        self.catalog = {'clubs': [{'id': 0, 'name': 'Real Madrid', 'league': 'LALIGA'},
                                  {'id': 1, 'name': 'Barcelona', 'league': 'LALIGA'}]}
        self.common = {'filters': {'season': '2026'}, 'competition': {'code': 'PD'}}
        row = {'id': 123, 'stage': 'REGULAR_SEASON', 'homeTeam': TEAM_A, 'awayTeam': TEAM_B,
               'utcDate': '2026-11-09T23:30:00Z', 'matchday': 1, 'status': 'FINISHED',
               'score': {'fullTime': {'home': 1, 'away': 0}}}
        future = {**row, 'id': 124, 'homeTeam': TEAM_B, 'awayTeam': TEAM_A,
                  'utcDate': '2027-04-09T13:30:00Z', 'matchday': 2, 'status': 'POSTPONED'}
        self.matches = copy.deepcopy([row, future])
        self.table = [
            {'team': TEAM_A, 'position': 1, 'playedGames': 1, 'won': 1, 'draw': 0, 'lost': 0,
             'goalsFor': 1, 'goalsAgainst': 0, 'points': 2},
            {'team': TEAM_B, 'position': 2, 'playedGames': 1, 'won': 0, 'draw': 0, 'lost': 1,
             'goalsFor': 0, 'goalsAgainst': 1, 'points': 0},
        ]

    def data(self):
        responses = {
            '/competitions/PD/matches?season=2026': {**self.common, 'matches': self.matches},
            '/competitions/PD/standings?season=2026': {**self.common, 'standings': [
                {'type': 'TOTAL', 'group': None, 'table': self.table},
                {'type': 'HOME', 'group': None, 'table': []},
            ]},
        }
        return load_league(responses.__getitem__, 'LALIGA', self.config, self.catalog, '2026-11-19')

    def test_verified_scores_official_points_and_postponement(self):
        league = self.data()
        self.assertEqual(league['dataAsOf'], '2026-11-10')
        self.assertEqual(league['clubIds'], [0, 1])
        self.assertEqual(league['tableKind'], 'verified')
        self.assertEqual(league['table'][0]['adjustment'], -1)
        archived, postponed = league['fixtures']
        self.assertEqual((archived['status'], archived['score'], archived['providerId']), ('FT', [1, 0], 123))
        self.assertEqual((postponed['status'], postponed['score'], postponed['providerId']), ('postponed', None, 124))

    def test_do_not_count_live_score(self):
        self.matches[1].update(status='IN_PLAY', utcDate='2026-11-19T12:00:00Z')
        new = self.data()
        self.assertEqual(new['fixtures'][1]['status'], 'in_progress')
        self.assertIsNone(new['fixtures'][1]['score'])
        self.assertEqual(new['table'][0]['p'], 1)

    def test_standings_race_rejects_candidate(self):
        self.table[0]['playedGames'] = 2
        with self.assertRaisesRegex(ValueError, 'reconcile'):
            self.data()

    def test_incomplete_schedule_and_competition_mismatch_rejected(self):
        self.matches.pop()
        with self.assertRaisesRegex(ValueError, 'schedule'):
            self.data()
        self.common['competition']['code'] = 'PL'
        with self.assertRaisesRegex(ValueError, 'competition mismatch'):
            self.data()

    def test_playoff_fixture_does_not_duplicate_regular_season(self):
        self.matches.append({**self.matches[0], 'id': 321, 'stage': 'PLAYOFF_ROUND_1'})
        self.assertEqual(len(self.data()['fixtures']), 2)

    def test_future_finished_score_rejected(self):
        self.matches[1]['status'] = 'FINISHED'
        with self.assertRaisesRegex(ValueError, 'final score'):
            self.data()

    def test_no_wrong_club_from_conflicting_alias(self):
        self.matches[0]['awayTeam']['name'] = 'Real Madrid CF'
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            self.data()

    def test_aliases_match_game_catalog_without_conflating_paris_clubs(self):
        directory = Path(__file__).parent
        catalog = json.loads((directory / 'catalog.json').read_text())
        config = json.loads((directory / 'config.json').read_text())
        for league in COMPETITIONS:
            with self.subTest(league=league):
                names, clubs = _club_names(league, catalog, config)
                self.assertEqual(set(names.values()), clubs)
        names, _ = _club_names('L1', catalog, config)
        self.assertNotIn('paris', names)
        self.assertNotEqual(names['parisfc'], names['parissg'])

    def test_one_client_throttles_and_scopes_authentication(self):
        timestamps, requests = [0.0], []

        class Loader:
            def open(self, request, timeout):
                requests.append((request, timeout, timestamps[0]))
                return io.BytesIO(json.dumps({'ok': True}).encode())

        def advance(seconds):
            timestamps[0] += seconds

        client = FootballDataClient('unit-test-token', opener=Loader(),
                                    clock=lambda: timestamps[0], sleep=advance)
        for _ in range(5):
            self.assertEqual(client('/competitions/PD/matches?season=2026'), {'ok': True})
            self.assertEqual(client('/competitions/PD/standings?season=2026'), {'ok': True})
        self.assertEqual(len(requests), 10)
        self.assertGreaterEqual(requests[-1][2] - requests[0][2], 55)
        self.assertEqual(requests[0][0].host, 'api.football-data.org')
        self.assertEqual(requests[0][0].get_header('X-auth-token'), 'unit-test-token')
        with self.assertRaises(ValueError):
            client('https://example.com/competitions/PD/matches?season=2026')
        self.assertEqual(len(requests), 10)


if __name__ == '__main__':
    unittest.main()
