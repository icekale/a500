"""Run with: python test_transition_bond.py (no network requests)."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import fetch_transition_data as fetcher


class BondReadingTest(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(fetcher.CONFIG)
        self.config['indicators']['bond_yield'].update(
            manual_fallback=1.62, last_known_date='2020-01-02')
        self.config_patch = patch.object(fetcher, 'CONFIG', self.config)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    def test_latest_valid_chinese_quote_keeps_its_date(self):
        rows = [
            {'SOLAR_DATE': '2020-01-06', 'EMM00166466': None, 'EMG00001310': 4.9},
            {'SOLAR_DATE': '2020-01-02', 'EMM00166466': 1.62},
            {'SOLAR_DATE': '2020-01-03 00:00:00', 'EMM00166466': '1.6038'},
        ]
        for value in ['NaN', 'inf', 0, -1, 100, '', None]:
            rows.append({'SOLAR_DATE': '2020-01-07', 'EMM00166466': value})
        for date in ['bad-date', '2020-02-30', '9999-01-01', None]:
            rows.append({'SOLAR_DATE': date, 'EMM00166466': 2})
        rows.extend([None, 'invalid row'])
        self.assertEqual(fetcher.latest_bond_reading(rows, 'EMM00166466', 'SOLAR_DATE'),
                         (1.6038, '2020-01-03'))
        for malformed in [[], None, 1, 'bad', {'error': 'offline'}]:
            self.assertEqual(fetcher.latest_bond_reading(malformed, 'v', 'd'), (None, None))

    def test_direct_request_and_healed_fallback(self):
        response = Mock(status_code=200)
        response.json.return_value = {'result': {'data': [
            {'SOLAR_DATE': '2020-01-03', 'EMM00166466': 1.6038}]}}
        with patch.object(fetcher.requests, 'get', return_value=response) as get:
            result = fetcher.fetch_bond_reading()
        url = get.call_args.args[0]
        self.assertIn('sortColumns=SOLAR_DATE', url)
        self.assertIn('reportName=RPTA_WEB_TREASURYYIELD', url)
        self.assertIn('pageSize=10', url)
        self.assertTrue(result['auto_fetched'])
        self.assertEqual(result['date'], '2020-01-03')
        score = fetcher.compute_scores({'bond_yield': result})['bond_yield']
        self.assertEqual(score['score'], 30)
        self.assertTrue(score['auto_fetched'])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'config.json'
            with patch.object(fetcher, 'CONFIG_PATH', str(target)):
                fetcher.heal_config({'bond_yield': result})
            saved = json.loads(target.read_text())['indicators']['bond_yield']
        self.assertEqual(saved['manual_fallback'], 1.6038)
        self.assertEqual(saved['last_known_date'], '2020-01-03')

    def test_backup_skips_empty_last_row_and_failure_preserves_cached_date(self):
        frame = Mock()
        frame.to_dict.return_value = [
            {'日期': '2020-01-03', '中国国债收益率10年': 1.60},
            {'日期': '2020-01-06', '中国国债收益率10年': None}]
        ak = SimpleNamespace(bond_zh_us_rate=Mock(return_value=frame))
        with patch.object(fetcher, 'em_get', return_value=None), \
             patch.object(fetcher, 'HAS_AK', True), patch.object(fetcher, 'ak', ak):
            result = fetcher.fetch_bond_reading()
            self.assertEqual((result['value'], result['date']), (1.60, '2020-01-03'))
            self.assertTrue(result['auto_fetched'])
            ak.bond_zh_us_rate.side_effect = RuntimeError('source offline')
            result = fetcher.fetch_bond_reading()
        self.assertEqual((result['value'], result['date']), (1.62, '2020-01-02'))
        self.assertFalse(result['auto_fetched'])

    def test_old_quote_does_not_replace_newer_cache(self):
        with patch.object(fetcher, 'HAS_AK', False), patch.object(fetcher, 'em_get',
                return_value=[{'SOLAR_DATE': '2020-01-01', 'EMM00166466': 2}]):
            result = fetcher.fetch_bond_reading()
        self.assertEqual(result['date'], '2020-01-02')
        self.assertFalse(result['auto_fetched'])

    def test_indicator_collection_uses_direct_bond_reader(self):
        result = {'value': 1.6038, 'date': '2020-01-03', 'auto_fetched': True, 'source': 'test'}
        with patch.object(fetcher, 'HAS_AK', False), \
             patch.object(fetcher, 'em_get', return_value=None), \
             patch.object(fetcher, 'fetch_url', return_value=None), \
             patch.object(fetcher, 'social_financing_reading', return_value=(None, None)), \
             patch.object(fetcher, 'fetch_bond_reading', return_value=result):
            self.assertEqual(fetcher.fetch_indicators()['bond_yield'], result)


if __name__ == '__main__':
    unittest.main()
