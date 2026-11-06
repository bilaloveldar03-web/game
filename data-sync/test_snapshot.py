import copy
import unittest
import collector as c


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {'version': 'test', 'clubs': [{'id': 1, 'name': 'One', 'league': 'TEST'}, {'id': 2, 'name': 'Two', 'league': 'TEST'}]}
        fixtures = [{'home': 1, 'away': 2, 'date': '2026-11-06', 'score': [0, 0], 'status': 'FT'},
                    {'home': 2, 'away': 1, 'date': '2027-02-01', 'score': None, 'status': 'scheduled'}]
        rows = c.standings(fixtures, [1, 2], '2026-11-07')
        for i, row in enumerate(rows):
            row['rank'] = i+1
            row['group'] = ''
        league = {'id': 'TEST', 'checkedAt': '2026-11-07', 'dataAsOf': '2026-11-06', 'clubIds': [1, 2], 'fixtures': fixtures, 'table': rows}
        self.snapshot = {'schemaVersion': 2, 'catalogVersion': 'test', 'season': '2026-27', 'asOf': '2026-11-07', 'verifiedAt': '2026-11-07T03:30:21+05:00', 'leagues': {'TEST': league}, 'news': []}
        self.old = {'schemaVersion': 1, 'checkedAt': '2026-11-07', 'leagues': {'TEST': {'dataAsOf': '2026-09-20', 'fixtures': []}}, 'unavailable': []}

    def test_newer_snapshot_survives_stale_scheduled_collection(self):
        first = c.merge_snapshot(copy.deepcopy(self.old), {}, self.snapshot, self.catalog, '2026-11-07')
        second = c.merge_snapshot(copy.deepcopy(self.old), first, None, self.catalog, '2026-11-08')
        self.assertEqual(second['leagues']['TEST']['dataAsOf'], '2026-11-06')
        self.assertEqual(second['leagues']['TEST']['checkedAt'], '2026-11-07')
        self.assertEqual(second['summary']['completedMatches'], 1)

    def test_live_score_cannot_be_recorded_as_final(self):
        bad = copy.deepcopy(self.snapshot)
        bad['leagues']['TEST']['fixtures'][0]['status'] = 'live'
        with self.assertRaises(ValueError):
            c.validate_snapshot(bad, self.catalog, '2026-11-07')

    def test_bad_table_does_not_replace_last_good_snapshot(self):
        previous = c.merge_snapshot(copy.deepcopy(self.old), {}, self.snapshot, self.catalog, '2026-11-07')
        bad = copy.deepcopy(self.snapshot)
        bad['leagues']['TEST']['table'][0]['pts'] = 99
        result = c.merge_snapshot(copy.deepcopy(self.old), previous, bad, self.catalog, '2026-11-07')
        self.assertEqual(result['snapshotStatus'], 'error')
        self.assertEqual(result['leagues']['TEST']['table'][0]['pts'], 1)

    def test_future_check_dates_are_rejected(self):
        bad = copy.deepcopy(self.snapshot)
        bad['asOf'] = '2026-11-08'
        with self.assertRaises(ValueError):
            c.validate_snapshot(bad, self.catalog, '2026-11-07')


if __name__ == '__main__':
    unittest.main()
