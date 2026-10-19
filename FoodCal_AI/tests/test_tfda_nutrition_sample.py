"""Synthetic parser fixtures only; no official data/network required by tests."""
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from experiments.tfda_nutrition_sample.inspect_sample import (
    classify_raw, diagnose, extract, food_key, iter_rows, member_info, verify,
)
from experiments.tfda_nutrition_sample.acquire import fetch, save


class TFDASampleTests(unittest.TestCase):
    def test_unicode_chunk_boundaries_preserve_literal(self):
        text = '[\n{ "食材": "絲瓜", "量": "   0.0", "缺值": null },{}]\n'
        rows = list(iter_rows(io.StringIO(text), chunk_size=2))
        self.assertEqual(rows[0][1], {'食材': '絲瓜', '量': '   0.0', '缺值': None})
        self.assertEqual(rows[0][2], '{ "食材": "絲瓜", "量": "   0.0", "缺值": null }')
        self.assertEqual([r[0] for r in rows], [0, 1])

    def test_invalid_arrays_rejected(self):
        for value in ('{}', '[{}', '[{},]', '[{}{}]', '[{}]x', '[1]', '[NaN]', '[{"x":1,"x":2}]'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                list(iter_rows(io.StringIO(value), chunk_size=3))

    def test_empty_array(self):
        self.assertEqual(list(iter_rows(io.StringIO(' [ ] '))), [])

    def test_missing_and_zero_are_distinct(self):
        self.assertEqual([classify_raw(v) for v in (None, '', ' ', '0', ' 0.0', 0)],
                         ['null', 'empty_string', 'whitespace_string', 'explicit_zero_string',
                          'padded_explicit_zero_string', 'explicit_zero_number'])

    def test_tokens_not_coerced(self):
        for token in ('-', 'ND', '<0.1', '1/2', '1.29/1.00/1.50', '適量'):
            self.assertEqual(classify_raw(token), 'uninterpreted_token')
        self.assertEqual(classify_raw(' 12.3'), 'padded_numeric_string')
        self.assertEqual(classify_raw(True), 'unexpected_type')
        self.assertEqual(classify_raw(float('nan')), 'unexpected_type')

    @staticmethod
    def row(number, nutrient='熱量'):
        return {'整合編號': f'SYNTHETIC_{number}', '樣品名稱': f'測試{number}',
                '資料類別': 'synthetic/test_only', '分析項': nutrient,
                '含量單位': 'kcal', '每100克含量': None, '廢棄率': None}

    def test_identity_does_not_assume_fixed_id_length(self):
        row = self.row(1)
        row['整合編號'] = 'S'
        self.assertEqual(food_key(row)[0], 'S')
        row['整合編號'] = None
        with self.assertRaises(ValueError):
            food_key(row)

    def test_missing_nutrient_is_absent_not_zero(self):
        result = diagnose([self.row(1)])
        self.assertEqual(result['nutrients']['粗蛋白']['observation_count'], 0)
        self.assertEqual(result['nutrients']['熱量']['raw_values'], [None])
        self.assertEqual(result['refuse_raw'], [None])
        self.assertFalse(result['semantic_approval'])

    def test_conflicting_metadata_and_duplicates(self):
        first = self.row(1)
        second = deepcopy(first)
        second['廢棄率'] = '0.0'
        result = diagnose([first, second])
        self.assertEqual(result['metadata_conflicts'], ['廢棄率'])
        self.assertEqual(result['duplicate_analysis_items'], ['熱量'])

    def test_mixed_food_rows_rejected(self):
        with self.assertRaises(ValueError):
            diagnose([self.row(1), self.row(2)])

    def make_archive(self, root):
        rows = [self.row(n, nutrient) for nutrient in ('熱量', '粗蛋白') for n in range(5)]
        raw = json.dumps(rows, ensure_ascii=False).encode('utf-8')
        path = root / 'synthetic.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('synthetic.json', raw)
        return path, rows

    def test_scattered_rows_complete_and_literal_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, rows = self.make_archive(root)
            sample = root / 'sample'
            report = extract(archive, [food_key(row) for row in rows[:5]], sample)
            self.assertEqual(report['records'][0]['source_row_indices_zero_based'], [0, 5])
            self.assertEqual(verify(archive, sample)['status'], 'verified_against_complete_snapshot')
            self.assertEqual(report['records'][0]['diagnosis']['row_count'], 2)
            with self.assertRaises(FileExistsError):
                extract(archive, [food_key(row) for row in rows[:5]], sample)

    def test_tampered_sample_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, rows = self.make_archive(root)
            sample = root / 'sample'
            extract(archive, [food_key(row) for row in rows[:5]], sample)
            (sample / 'food_01.json').write_bytes(b'[]')
            with self.assertRaisesRegex(ValueError, 'Sample hash'):
                verify(archive, sample)

    def test_incomplete_sample_rejected_even_if_hash_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, rows = self.make_archive(root)
            sample = root / 'sample'
            report = extract(archive, [food_key(row) for row in rows[:5]], sample)
            raw = json.dumps([rows[0]], ensure_ascii=False).encode('utf-8')
            (sample / 'food_01.json').write_bytes(raw)
            report['records'][0]['sha256'] = sha256(raw).hexdigest()
            (sample / 'inspection.json').write_text(json.dumps(report), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'completeness'):
                verify(archive, sample)

    def test_invalid_selection_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, rows = self.make_archive(root)
            for keys in ([food_key(rows[0])] * 5, [food_key(row) for row in rows[:4]],
                         [food_key(row) for row in rows[:4]] + [('absent', 'absent', 'absent')]):
                with self.assertRaises(ValueError):
                    extract(archive, keys, root / 'absent')
                self.assertFalse((root / 'absent').exists())

    def test_wrong_archive_shape_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('a.json', '[]')
                archive.writestr('b.json', '[]')
            with zipfile.ZipFile(path) as archive, self.assertRaises(ValueError):
                member_info(archive)

    def test_private_save_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            save(path, 'evidence', b'original')
            with self.assertRaises(FileExistsError):
                save(path, 'evidence', b'replacement')
            self.assertEqual((path / 'evidence').read_bytes(), b'original')

    def test_nonofficial_url_blocked_before_network(self):
        for url in ('http://data.fda.gov.tw/x', 'https://third-party.example/x'):
            with self.assertRaises(ValueError):
                fetch(url)


if __name__ == '__main__':
    unittest.main()
