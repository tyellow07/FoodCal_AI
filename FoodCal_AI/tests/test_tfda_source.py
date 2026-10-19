"""Rebuild synthetic snapshots offline. No real TFDA values/private files needed."""
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from experiments.tfda_nutrition_sample.inspect_sample import extract
from src.nutrition import tfda_source as source


def synthetic_snapshot(directory):
    """SYNTHETIC TEST data; source URL is protocol fixture, not a source claim.

    Only tests patch pinned hashes. Production API offers no pin override.
    """
    rows = []
    keys = []
    for food in range(8):
        key = (f'TEST_{food}', f'SYNTHETIC 食材{food}', 'synthetic/test_only')
        keys.append(key)
        for nutrient in range(104):
            row = dict.fromkeys(source.FIELDS)
            row.update({'食品分類': 'synthetic/test_only', '資料類別': key[2],
                        '整合編號': key[0], '樣品名稱': key[1], '內容物描述': 'SYNTHETIC ONLY',
                        '分析項': ('熱量', '修正熱量', '粗蛋白', '粗脂肪', '總碳水化合物')[nutrient]
                        if nutrient < 5 else f'SYNTHETIC 分析{nutrient}',
                        '分析項分類': 'synthetic/test_only', '含量單位': 'kcal' if nutrient < 2 else 'g',
                        '每100克含量': '   0.0' if nutrient == 2 else '   1.25' if nutrient < 5 else None})
            rows.append(row)
    # Interleave all foods so completeness cannot depend on contiguous food blocks.
    rows.sort(key=lambda row: row['分析項'])
    archive = directory / 'official-export.zip'
    with zipfile.ZipFile(archive, 'w') as file:
        file.writestr('synthetic.json', json.dumps(rows, ensure_ascii=False))
    report = extract(archive, keys, directory / 'samples')
    provenance = {'source_url': source.SOURCE_URL, 'retrieved_at_utc': 'SYNTHETIC_TEST_TIME',
                  'archive_sha256': report['archive_sha256'], 'member_sha256': report['member_sha256']}
    (directory / 'source-provenance.json').write_text(json.dumps(provenance), encoding='utf-8')
    return {name: sha256((directory / path).read_bytes()).hexdigest() for name, path in (
        ('ARCHIVE_SHA256', 'official-export.zip'), ('PROVENANCE_SHA256', 'source-provenance.json'),
        ('INSPECTION_SHA256', 'samples/inspection.json'))}


class TFDAValueTests(unittest.TestCase):
    def test_decimal_preserves_precision_and_padding(self):
        value = source.parse_value('   123456789.123456789000')
        self.assertEqual(value.raw_value, '   123456789.123456789000')
        self.assertEqual(value.parsed_value, Decimal('123456789.123456789000'))
        self.assertEqual(value.status, 'reported')

    def test_zero_is_reported_without_claiming_true_zero(self):
        self.assertEqual(source.parse_value(' 0.0').parsed_value, Decimal('0.0'))
        self.assertEqual(source.parse_value('0').status, 'reported')

    def test_missing_never_becomes_zero(self):
        for value in (None, '', ' ', '\t\n'):
            with self.subTest(value=value):
                parsed = source.parse_value(value)
                self.assertEqual(parsed.raw_value, value)
                self.assertIsNone(parsed.parsed_value)
                self.assertEqual(parsed.status, 'missing')

    def test_unknown_and_unsupported_formats_retained(self):
        for value in ('ND', '-', '<0.1', '1.29/1.00/1.50', '1e3', '+1', '.5', '半', '１２', '\u00a01.2'):
            with self.subTest(value=value):
                parsed = source.parse_value(value)
                self.assertIsNone(parsed.parsed_value)
                self.assertEqual(parsed.raw_value, value)
                self.assertEqual(parsed.status, 'unparsed')

    def test_invalid_types_and_negative_values(self):
        for value in (True, 0, 1.2, float('nan'), '-1.2'):
            self.assertEqual(source.parse_value(value).status, 'invalid')
            self.assertIsNone(source.parse_value(value).parsed_value)


class TFDASourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pins = synthetic_snapshot(self.root)
        for name, value in self.pins.items():
            context = patch.object(source, name, value)
            context.start()
            self.addCleanup(context.stop)

    def adapter(self):
        return source.TFDANutritionSourceAdapter(self.root)

    def test_all_eight_records_and_all_observations(self):
        adapter = self.adapter()
        self.assertFalse(adapter.issues)
        self.assertEqual(len(adapter.list_foods()), 8)
        for n in range(8):
            result = adapter.lookup(f'TEST_{n}')
            self.assertEqual(result.status, 'found')
            record = result.records[0]
            self.assertEqual(len(record.observations), 104)
            self.assertFalse(record.production_eligible)
            self.assertEqual(record.edible_portion_basis, 'unresolved')
            self.assertEqual(record.weight_basis_applicability, 'unresolved')
            self.assertEqual(record.identity_approval, 'unresolved')

    def test_units_missing_metadata_and_evidence(self):
        record = self.adapter().lookup('TEST_0').records[0]
        observations = {item.original_name: item for item in record.observations}
        self.assertEqual(observations['熱量'].unit, 'kcal')
        self.assertEqual(observations['粗蛋白'].unit, 'g')
        self.assertEqual(observations['粗蛋白'].value.raw_value, '   0.0')
        self.assertEqual(observations['SYNTHETIC 分析5'].value.status, 'missing')
        item = observations['熱量']
        self.assertEqual(item.evidence.archive_sha256, self.pins['ARCHIVE_SHA256'])
        self.assertEqual(item.evidence.retrieved_at_utc, 'SYNTHETIC_TEST_TIME')
        self.assertEqual(len(item.evidence.source_object_sha256), 64)
        self.assertEqual(dict(item.raw_fields)['每100克含量'], item.value.raw_value)

    def test_no_name_similarity_or_unknown_id_inference(self):
        adapter = self.adapter()
        for value in ('SYNTHETIC 食材0', 'TEST_99', 'test_0'):
            self.assertEqual(adapter.lookup(value).status, 'not_found')
        for value in (None, True, '', ' TEST_0'):
            self.assertEqual(adapter.lookup(value).status, 'invalid_record')

    def test_missing_snapshot_is_explicit(self):
        result = source.TFDANutritionSourceAdapter(self.root / 'absent').lookup('TEST_0')
        self.assertEqual(result.status, 'invalid_record')
        self.assertEqual(result.reason, 'snapshot_missing')
        self.assertNotIn(str(self.root), repr(result))

    def test_archive_hash_mismatch(self):
        (self.root / 'official-export.zip').write_bytes(b'changed')
        result = self.adapter().lookup('TEST_0')
        self.assertEqual(result.reason, 'pinned_hash_mismatch')
        self.assertEqual(result.issues[0].path, '/snapshot/official-export.zip')

    def test_source_metadata_hash_mismatch(self):
        (self.root / 'source-provenance.json').write_text('{}', encoding='utf-8')
        self.assertEqual(self.adapter().lookup('TEST_0').reason, 'pinned_hash_mismatch')

    def test_manifest_tampering_cannot_authorize_replaced_rows(self):
        (self.root / 'samples/inspection.json').write_text('{}', encoding='utf-8')
        self.assertEqual(self.adapter().lookup('TEST_0').reason, 'pinned_hash_mismatch')

    def test_sample_tampering_rejected(self):
        (self.root / 'samples/food_01.json').write_text('[]', encoding='utf-8')
        result = self.adapter().lookup('TEST_0')
        self.assertEqual(result.reason, 'source_replay_failed')

    def test_duplicate_and_ambiguous_ids_never_select_first(self):
        adapter = self.adapter()
        record = adapter.lookup('TEST_0').records[0]
        with patch.object(source, '_load_snapshot', return_value=(record, replace(record, original_name='different'))):
            result = self.adapter().lookup('TEST_0')
            self.assertEqual(result.status, 'ambiguous')
            self.assertEqual(len(result.records), 2)
        with patch.object(source, '_load_snapshot', return_value=(record, record)):
            self.assertEqual(self.adapter().lookup('TEST_0').status, 'invalid_record')

    def test_results_are_immutable_and_loading_does_not_write(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        adapter = self.adapter()
        result = adapter.lookup('TEST_0')
        with self.assertRaises(FrozenInstanceError):
            result.records[0].original_name = 'changed'
        with self.assertRaises(TypeError):
            result.records[0].observations[0].raw_fields[0] = ('x', 'y')
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        with patch.object(source, '_load_snapshot', side_effect=AssertionError('must not reload')):
            self.assertEqual(adapter.lookup('TEST_1').status, 'found')

    def test_invalid_nutrient_and_duplicate_analysis_report_paths(self):
        adapter = self.adapter()
        record = adapter.lookup('TEST_0').records[0]
        item = record.observations[0]
        row = dict(item.raw_fields)
        row['每100克含量'] = True
        metadata = {'filename': 'synthetic.json', 'sha256': '1' * 64,
                    'diagnosis': {'food_key': [record.food_id, record.original_name, record.data_type]},
                    'source_row_indices_zero_based': [1, 2], 'source_object_sha256': ['2' * 64, '3' * 64]}
        snapshot = {'source_url': 'test://synthetic', 'retrieved_at_utc': 'SYNTHETIC',
                    'archive_sha256': '4' * 64, 'member_sha256': '5' * 64}
        broken = source._build_record([row, row], metadata, snapshot)
        self.assertIn('duplicate_analysis_item', [issue.code for issue in broken.issues])
        self.assertIn('/samples/synthetic.json/0/每100克含量', [issue.path for issue in broken.issues])
        with patch.object(source, '_load_snapshot', return_value=(broken,)):
            self.assertEqual(self.adapter().lookup('TEST_0').status, 'invalid_record')
        row['樣品名稱'] = 'different'
        with self.assertRaises(ValueError):
            source._build_record([row], metadata, snapshot)


if __name__ == '__main__':
    unittest.main()
