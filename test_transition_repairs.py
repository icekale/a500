"""Run: .venv/bin/python -m unittest test_transition_bond test_transition_repairs -v

Standard-library unittest/mocks; no network, AKShare or pandas required.
"""
import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import build_vpush_data as aggregator
import fetch_transition_data as fetcher


class TransitionRepairsTest(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(fetcher.CONFIG)
        config_patch = patch.object(fetcher, 'CONFIG', self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)

    def readings(self):
        return {k: {'value': c['manual_fallback'], 'date': c['last_known_date'],
                    'source': c['data_source'], 'auto_fetched': False}
                for k, c in self.config['indicators'].items()}

    def test_ai_old_value_never_scores_or_heals_and_collection_skips_openrouter(self):
        results = self.readings()
        results['ai_market_share'] = {'value': 46.6, 'date': '2020-01-01', 'auto_fetched': True}
        ai = fetcher.build_output(results)['indicators']['ai_market_share']
        self.assertIsNone(ai['value'])
        self.assertIsNone(ai['score'])
        self.assertFalse(ai['included'])
        self.assertFalse(ai['auto'])
        self.assertEqual(ai['weight'], 0)
        self.assertEqual(ai['configured_weight'], 0.06)
        self.assertEqual(ai['exclusion_reason'], '缺少可核验的调用量数据，暂不计分')
        with tempfile.TemporaryDirectory() as directory, patch.object(fetcher, 'CONFIG_PATH', str(Path(directory) / 'config.json')):
            fetcher.heal_config({'ai_market_share': results['ai_market_share']})
            self.assertFalse((Path(directory) / 'config.json').exists())
        self.assertEqual(fetcher.cfg_fallback('ai_market_share'), (None, None))
        with patch.object(fetcher, 'HAS_AK', False), \
             patch.object(fetcher, 'em_get', return_value=None), \
             patch.object(fetcher, 'fetch_url', return_value=None) as get, \
             patch.object(fetcher, 'social_financing_reading', return_value=(None, None)), \
             patch.object(fetcher, 'rmb_loan_reading', return_value=(None, None)):
            collected = fetcher.fetch_indicators()
        self.assertIsNone(collected['ai_market_share']['value'])
        self.assertFalse(collected['ai_market_share']['included'])
        self.assertFalse(any('openrouter' in call.args[0] for call in get.call_args_list))

    def test_scoring_keeps_four_lines_and_reports_configured_coverage(self):
        self.assertAlmostEqual(sum(c['weight'] for c in self.config['indicators'].values()), 1)
        results = self.readings()
        # 固定本次评审样本；每日采集回写 config 后本回归仍检查同一组数。
        values = {'cpi': 0.9, 'ppi': 5.0, 'pmi': 49.3, 'pmi_nonmfg': 49.7,
                  'electricity': 3.9, 'retail_sales': 1.1, 'fixed_asset_investment': -7.2,
                  'unemployment': 5.2, 'consumer_confidence': 89, 'disposable_income': 4.3,
                  'm2': 7.2, 'm1': 3.2, 'rmb_loan': 4.82, 'social_financing': -10.1,
                  'bond_yield': 1.6038, 'lpr': 3.0, 'industrial_output': 5.5, 'export': 36.3,
                  'import_yoy': 31, 'currency_index': 103.77, 'new_energy_penetration': 67.0,
                  'ai_market_share': None}
        for key, value in values.items():
            results[key]['value'] = value
        output = fetcher.build_output(results)
        self.assertEqual(output['available_coverage'], 0.94)
        self.assertEqual(output['excluded_indicators'], ['ai_market_share'])
        self.assertEqual(output['lineScores']['line4']['available_coverage'], 0.8)
        expected = 0
        for lid, wkey in fetcher.LINE_WEIGHT_KEYS.items():
            items = [s for s in output['indicators'].values() if s['line'] == lid and s['included']]
            weight = self.config['weights'][wkey]
            self.assertAlmostEqual(sum(s['weight'] for s in items), weight)
            line_score = sum(s['score'] * s['configured_weight'] for s in items) / sum(s['configured_weight'] for s in items)
            expected += weight * line_score
            self.assertEqual(output['lineScores'][f'line{lid}']['score'], round(line_score, 1))
            self.assertEqual(output['lineScores'][f'line{lid}']['weight'], weight * 100)
        self.assertEqual(output['temperature'], round(expected, 1))
        self.assertEqual(output['temperature'], 55.6)
        for key, item in output['indicators'].items():
            self.assertEqual(item['unit'], self.config['indicators'][key]['unit'])
            self.assertEqual(item['reference_range'], self.config['indicators'][key]['reference_range'])
        # 原54.5的22项完整框架仍是相同算法；只在缺失时采用线内归一。
        self.config['indicators']['ai_market_share']['included'] = True
        results['ai_market_share']['value'] = 46.6
        results['new_energy_penetration']['value'] = 41.8
        results['fixed_asset_investment']['value'] = -10.1
        complete = fetcher.build_output(results)
        self.assertEqual(complete['temperature'], 54.5)
        self.assertEqual(complete['available_coverage'], 1)

    def test_missing_and_all_excluded_never_get_neutral_scores(self):
        results = self.readings()
        results = dict(reversed(list(results.items())))
        results.pop('cpi')
        results['pmi']['value'] = float('nan')
        output = fetcher.build_output(results)
        self.assertIsNone(output['indicators']['cpi']['score'])
        self.assertEqual(list(output['indicators']), [*results, 'cpi'])
        self.assertIsNone(output['indicators']['pmi']['value'])
        self.assertEqual(output['available_coverage'], 0.84)
        results = self.readings()
        for key, c in self.config['indicators'].items():
            if c['line'] == 4:
                results[key]['included'] = False
        output = fetcher.build_output(results)
        self.assertIsNone(output['lineScores']['line4']['score'])
        self.assertEqual(output['lineScores']['line4']['weight'], 30)
        self.assertEqual(output['available_coverage'], 0.7)
        self.assertAlmostEqual(sum(s['weight'] for s in output['indicators'].values()), 1)
        for empty in ({}, {k: {**v, 'included': False} for k, v in results.items()}):
            output = fetcher.build_output(empty)
            self.assertIsNone(output['temperature'])
            self.assertEqual(output['available_coverage'], 0)
            self.assertEqual(len(output['excluded_indicators']), 22)
            self.assertTrue(all(s['score'] is None for s in output['lineScores'].values()))
            self.assertEqual(output['band'], '暂无数据')
            json.dumps(output, allow_nan=False)

    def test_nev_uses_same_month_retail_and_skips_nan_without_shifting_rows(self):
        fuel = [{'月份': '1月', '2020年': 20}, {'月份': '2月', '2020年': float('nan')},
                {'月份': '9月', '2020年': 108.3843}, {'月份': '10月', '2020年': None}]
        total = [{'月份': '9月', '2020年': 161.7989}, {'月份': '1月', '2020年': 50},
                 {'月份': '2月', '2020年': 55}, {'月份': '10月', '2020年': 170}]
        self.assertEqual(fetcher.latest_nev_retail_reading(fuel, total, 2020), (67.0, '2020-09'))
        for bad in [None, float('nan'), float('inf'), 0, -1, 100]:
            with self.subTest(total=bad):
                self.assertEqual(fetcher.latest_nev_retail_reading(fuel, [{'月份': '9月', '2020年': bad}], 2020), (None, None))
        self.assertEqual(fetcher.latest_nev_retail_reading(fuel, [{'月份': '8月', '2020年': 200}], 2020), (None, None))
        self.assertEqual(fetcher.latest_nev_retail_reading(fuel, total, 2021), (None, None))
        ak = SimpleNamespace(car_market_fuel_cpca=Mock(return_value=Mock(to_dict=Mock(return_value=fuel))),
                             car_market_total_cpca=Mock(return_value=Mock(to_dict=Mock(return_value=total))))
        self.config['indicators']['new_energy_penetration'].update(last_known_date='2020-08', manual_fallback=67.0)
        with patch.object(fetcher, 'HAS_AK', True), patch.object(fetcher, 'ak', ak), patch.object(fetcher, 'CUR_YEAR', 2020):
            result = fetcher.fetch_nev_reading()
        self.assertEqual((result['value'], result['date'], result['auto_fetched']), (67.0, '2020-09', True))
        ak.car_market_total_cpca.assert_called_once_with(symbol='狭义乘用车', indicator='零售')
        ak.car_market_fuel_cpca.assert_called_once_with(symbol='整体市场')
        with patch.object(fetcher, 'HAS_AK', False):
            self.assertEqual(fetcher.fetch_nev_reading()['value'], 67.0)
            self.assertFalse(fetcher.fetch_nev_reading()['auto_fetched'])

    def test_fai_official_comparable_yoy_not_raw_amount_or_monthly_yoy(self):
        for title, phrase, expected, date in [
            ('2020年1—9月份全国固定资产投资基本情况', '下降7.2%', -7.2, '2020-09'),
            ('2020年1-8月份全国固定资产投资基本情况', '增长3.5%', 3.5, '2020-08'),
            ('2020年全国固定资产投资基本情况', '持平', 0, '2020-12'),
            ('2020年9月份全国固定资产投资基本情况', '增长1.2%', 1.2, '2020-09'),
        ]:
            html = f'<title>{title}</title><p>全国固定资产投资（不含农户）<b>334115</b>亿元，同比{phrase}（按可比口径计算）。</p><p>当月同比下降9.69%，去年累计371535亿元。</p>'
            self.assertEqual(fetcher.parse_fai_release(html), (expected, date))
        for text in [None, '', '334115/371535',
                     '<title>2020年1—9月份全国固定资产投资</title>民间固定资产投资同比下降10.4%（可比口径）',
                     '<title>2020年1—9月份全国固定资产投资</title>全国固定资产投资（不含农户）334115亿元，同比下降7.2%']:
            self.assertEqual(fetcher.parse_fai_release(text), (None, None))

    def test_fai_discovers_latest_report_and_retains_cache_on_failure(self):
        c = self.config['indicators']['fixed_asset_investment']
        c.update(last_known_date='2020-08', manual_fallback=-1)
        index = '''<a href="./202009/old.html" title='2020年1—8月份全国固定资产投资基本情况'>旧</a>
                   <a href="./202010/new.html" title='2020年1—9月份全国固定资产投资基本情况'>新</a>
                   <a href="https://example.com/wrong" title='2020年1—10月份全国固定资产投资基本情况'>异域</a>'''
        html = '<title>2020年1—9月份全国固定资产投资基本情况</title>全国固定资产投资（不含农户）334115亿元，同比下降7.2%（按可比口径计算）'
        with patch.object(fetcher, 'fetch_url', side_effect=[index, html]) as get:
            result = fetcher.fetch_fai_reading()
        self.assertEqual((result['value'], result['date'], result['auto_fetched']), (-7.2, '2020-09', True))
        self.assertEqual(result['source_url'], 'https://www.stats.gov.cn/sj/zxfb/202010/new.html')
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args.kwargs, {'encoding': 'utf-8'})
        with patch.object(fetcher, 'fetch_url', side_effect=['', index, html]) as get:
            self.assertTrue(fetcher.fetch_fai_reading()['auto_fetched'])
            self.assertEqual(get.call_count, 3)
            self.assertIn('index_1.html', get.call_args_list[1].args[0])
        for responses in ([None, None, None], [index, 'wrong page']):
            with patch.object(fetcher, 'fetch_url', side_effect=responses):
                result = fetcher.fetch_fai_reading()
            self.assertEqual((result['value'], result['date'], result['auto_fetched']), (-1, '2020-08', False))
        c['last_known_date'] = '2020-10'
        with patch.object(fetcher, 'fetch_url', return_value=index) as get:
            result = fetcher.fetch_fai_reading()
            self.assertEqual(get.call_count, 1)
            self.assertFalse(result['auto_fetched'])

    def test_rmb_loan_balance_yoy_from_pbc_credit_tables(self):
        def xlsx(header, loans):
            def row(r, values):
                cells = ''.join(
                    f'<c r="{chr(65 + i)}{r}" t="inlineStr"><is><t>{v}</t></is></c>' if isinstance(v, str) and i == 0
                    else f'<c r="{chr(65 + i)}{r}"><v>{v}</v></c>' for i, v in enumerate(values) if v is not None)
                return f'<row r="{r}">{cells}</row>'
            ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
            sheet = (f'<worksheet xmlns="{ns}"><sheetData>{row(1, ["金融机构人民币信贷收支表"])}'
                     f'{row(6, ["项目 Item", *header])}{row(9, ["一、各项存款 Total Deposits", *[9e9] * len(header)])}'
                     f'{row(27, ["一、各项贷款 Total Loans", *loans])}</sheetData></worksheet>')
            blob = io.BytesIO()
            with zipfile.ZipFile(blob, 'w') as book:
                book.writestr('xl/worksheets/sheet1.xml', sheet)
            return blob.getvalue()

        this_year = xlsx(['2020.08', '2020.09', '2020.1', '2020.11'], [104, 105, 106, None])
        self.assertEqual(fetcher.parse_pbc_balance_xlsx(this_year),
                         {'2020-08': 104.0, '2020-09': 105.0, '2020-10': 106.0})
        last_year = xlsx(['2019.09', '2019.1', '2019.11', '2019.12'], [100, 100, 101, 102])
        index = """<a href="/s/2020n/index.html">2020年统计数据</a><a href="/s/2020n/jr/index.html">金融机构信贷收支统计</a>
                   <a href="/s/555/index.html">2019年统计数据</a><a href="/s/555/556/index.html">金融机构信贷收支统计</a>
                   <a href="/s/554/index.html">2018年统计数据</a><a href="/s/554/557/index.html">金融机构信贷收支统计</a>"""
        self.assertEqual([y for y, _ in fetcher.pbc_loan_tables(index)], [2020, 2019, 2018])
        page = lambda name: f"""<div>存款类金融机构人民币信贷收支表</div><a href="/a/wrong.xlsx">xls</a>
                                <div>金融机构人民币信贷收支表</div><a href="/a/{name}.xlsx">xls</a>"""
        pages = {'https://www.pbc.gov.cn' + p: page(n) for p, n in
                 [('/s/2020n/jr/index.html', 'this'), ('/s/555/556/index.html', 'last'), ('/s/554/557/index.html', 'older')]}
        pages[fetcher.PBC_STATS] = index
        books = {'this': this_year, 'last': last_year}

        def get(url, **_):
            name = url.rsplit('/', 1)[1].removesuffix('.xlsx')
            return SimpleNamespace(status_code=200 if name in books else 404, content=books.get(name, b''))

        with patch.object(fetcher, 'fetch_pbc', side_effect=pages.get), \
             patch.object(fetcher.requests, 'get', side_effect=get):
            self.assertEqual(fetcher.rmb_loan_reading(), (6.0, '2020-10'))
            books['this'] = xlsx(['2020.01'], [None])  # 1 月数据未出：用上一年 12 月
            books['older'] = xlsx(['2018.12'], [100])
            self.assertEqual(fetcher.rmb_loan_reading(), (2.0, '2019-12'))
            del books['this']  # 最新年份打不开就不拿旧年份冒充
            self.assertEqual(fetcher.rmb_loan_reading(), (None, None))

        results = self.readings()
        with patch.object(fetcher, 'HAS_AK', False), \
             patch.object(fetcher, 'em_get', return_value=None), \
             patch.object(fetcher, 'fetch_url', return_value=None), \
             patch.object(fetcher, 'social_financing_reading', return_value=(None, None)), \
             patch.object(fetcher, 'fetch_bond_reading', return_value=results['bond_yield']), \
             patch.object(fetcher, 'rmb_loan_reading', return_value=(4.82, '2026-09')):
            loan = fetcher.fetch_indicators()['rmb_loan']
        self.assertEqual((loan['value'], loan['date'], loan['auto_fetched']), (4.82, '2026-09', True))

    def test_heal_updates_same_date_revisions_new_dates_and_provenance(self):
        c = self.config['indicators']['cpi']
        c.update(last_known_date='2020-01', last_known_value=1, manual_fallback=1)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'config.json'
            with patch.object(fetcher, 'CONFIG_PATH', str(target)):
                for value, date, source in [(2, '2020-01', 'https://example.com/a'),
                                             (2, '2020-02', 'https://example.com/a'),
                                             (2, '2020-02', 'https://example.com/b')]:
                    fetcher.heal_config({'cpi': {'value': value, 'date': date, 'auto_fetched': True, 'source_url': source}})
                    saved = json.loads(target.read_text())['indicators']['cpi']
                    self.assertEqual((saved['last_known_value'], saved['manual_fallback'], saved['last_known_date']), (value, value, date))
                    self.assertEqual(saved['last_known_source_url'], source)
                c['manual_fallback'] = 9
                fetcher.heal_config({'cpi': {'value': 2, 'date': '2020-02', 'auto_fetched': True}})
                self.assertEqual(c['manual_fallback'], 2)

    def test_heal_and_fallback_reject_invalid_values_dates_and_older_observations(self):
        c = self.config['indicators']['cpi']
        c.update(last_known_date='2020-03-01', last_known_value=1, manual_fallback=1)
        bad = [{'value': v, 'date': '2020-03-02'} for v in [None, 'bad', 'NaN', float('inf'), float('-inf')]]
        bad += [{'value': 2, 'date': d} for d in [None, '', '2020-02-30', '2020-13', '2020-Q1', '9999-01-01', '2020-02', '2020-02-29']]
        bad += [{'value': 2, 'date': '2020-03-02', 'included': False}, {'value': 2, 'date': '2020-03-02', 'auto_fetched': False}]
        before = copy.deepcopy(self.config)
        with tempfile.TemporaryDirectory() as directory, patch.object(fetcher, 'CONFIG_PATH', str(Path(directory) / 'config.json')):
            for info in bad:
                fetcher.heal_config({'cpi': {'auto_fetched': True, **info}})
                self.assertEqual(self.config, before)
            self.assertFalse((Path(directory) / 'config.json').exists())
        for value, date in [(float('nan'), '2020-01'), (1, 'bad-date'), (1, '2020-02-30')]:
            c.update(manual_fallback=value, last_known_date=date)
            self.assertEqual(fetcher.cfg_fallback('cpi'), (None, None))
        self.assertTrue(all(fetcher.observation_date(c['last_known_date']) for key, c in self.config['indicators'].items() if key not in ('ai_market_share', 'cpi')))

    def test_public_aggregator_passes_contract_through_without_loss(self):
        output = fetcher.build_output(self.readings())
        with tempfile.TemporaryDirectory() as directory, patch.object(aggregator, 'ROOT', Path(directory)), \
             patch.object(aggregator, 'read_page_data', return_value={}), \
             patch.object(aggregator, 'read_js', return_value={}), \
             patch.object(aggregator, 'read_json', side_effect=lambda name: output if name == 'transition_data.json' else {}):
            aggregator.main()
            payload = json.loads((Path(directory) / 'vpush_data.json').read_text())
        self.assertEqual(payload['transition'], output)
        self.assertIsNone(payload['transition']['indicators']['ai_market_share']['score'])
        self.assertEqual(payload['transition']['indicators']['pmi']['unit'], '')


if __name__ == '__main__':
    unittest.main()
