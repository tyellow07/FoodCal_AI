"""Offline composition tests, not food-recognition or nutrient-accuracy evaluation."""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.pipeline.diagnostic_demo import DEFAULT_FIXTURES, run_demo, json_value
from src.recipe_parsing import validate_recipe_parsing
from src.nutrition.tfda_source import parse_value


class DiagnosticDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for p in DEFAULT_FIXTURES.iterdir():
            if p.suffix in ('.json', '.yaml'):
                shutil.copyfile(p, self.root / p.name)

    def read(self, name):
        return json.loads((self.root / name).read_text(encoding='utf-8'))

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def first(self, result):
        return result['objects'][0]['recipe_candidates'][0]

    def enable_link(self):
        manifest = self.read('associations.json')
        link = manifest['associations'][0]
        identifier = 'ing_3_6'
        link['identity_assertions'] = {identifier: {
            'attributes': {'food': 'chicken', 'part': 'breast', 'state': 'raw',
                           'quality': 'synthetic_unspecified_grade', 'weight_basis': 'edible_portion'},
            'evidence_reference': 'test://synthetic-caller-assertion',
            'source_text_sha256': link['expected_source']['text_sha256']}}
        from tests.test_nutrition_review import SyntheticReview
        fixture = SyntheticReview()
        food = fixture.attachments['candidate']['ingredients'][0]['candidates'][0]['source_lookup'].records[0]
        proof = food.observations[0].evidence
        link['source_links'] = {identifier: {'ingredient_id': identifier, 'input_id': link['input_id'],
            'recipe_text_sha256': link['expected_source']['text_sha256'], 'candidates': [{
                'food_id': food.food_id, 'expected_archive_sha256': proof.archive_sha256,
                'expected_sample_sha256': proof.sample_sha256, 'evidence_reference': 'test://explicit-source-link'}]}}
        self.write('associations.json', manifest)
        adapter = Mock()
        adapter.lookup.return_value = fixture.attachments['candidate']['ingredients'][0]['candidates'][0]['source_lookup']
        return manifest, adapter

    def test_complete_fixture_snapshots_and_no_eligibility(self):
        r = run_demo(self.root)
        self.assertEqual(r['status'], 'partial')
        self.assertEqual(len(r['objects']), 2)
        self.assertEqual(len(r['objects'][0]['recipe_candidates']), 5)
        self.assertEqual(r['objects'][1]['retrieval_status'], 'unavailable')
        for c in r['objects'][0]['recipe_candidates']:
            self.assertTrue(validate_recipe_parsing(c['parsing_snapshot']).valid)
            self.assertEqual(c['association']['status'], 'fixture_association_only')
            self.assertIsNone(c['parsing_snapshot']['retrieval_binding'])
            self.assertEqual(c['parsing_snapshot']['review_status'], 'unreviewed')
            self.assertEqual(c['parsing_snapshot']['source']['review_status'], 'test_only')
            self.assertFalse(c['mapping_snapshot']['qualification']['eligible_for_production'])
        self.assertIsNone(r['photo_portion_grams'])
        self.assertEqual(r['calorie_estimate']['status'], 'unavailable')
        self.assertIsNone(r['calorie_estimate']['value'])
        self.assertFalse(r['trust']['nutrition_eligible'])

    def test_photo_energy_section_reports_dish_level_gaps_without_values(self):
        r = run_demo(self.root)
        energy = r['photo_energy_estimate']
        self.assertEqual((energy['status'], energy['energy'], energy['unit']), ('unavailable', None, None))
        self.assertEqual(energy['reasons'], ['visual_items_unavailable', 'consumption_basis_missing'])
        items = energy['portion_assessment']['items']
        self.assertEqual(len(items), len(r['detection_snapshot']['detections']))
        self.assertTrue(all(i['reason'] == 'ingredient_level_semantics_unverified' and i['mass_g'] is None for i in items))
        self.assertEqual((energy['nutrition_eligible'], energy['production_eligible']), (False, False))
        self.assertIsNone(r['photo_portion_grams'])
        self.assertEqual(r['calorie_estimate']['status'], 'unavailable')

    def test_photo_energy_absent_when_fixture_fails(self):
        (self.root / 'catalog.json').unlink()
        self.assertIsNone(run_demo(self.root)['photo_energy_estimate'])

    def test_photo_energy_has_no_items_without_detections(self):
        d = self.read('detection.json');d.update(detections=[], status='no_detections')
        self.write('detection.json', d)
        energy = run_demo(self.root)['photo_energy_estimate']
        self.assertEqual(energy['portion_assessment']['items'], [])
        self.assertIn('no_visual_items', energy['reasons'])

    def test_half_optional_missing_and_group_steps_preserved(self):
        c = run_demo(self.root)['objects'][0]['recipe_candidates']
        self.assertEqual(c[2]['parsing_snapshot']['ingredients'][1]['quantity'], 0.5)
        self.assertTrue(c[2]['parsing_snapshot']['ingredients'][2]['optional'])
        for ingredient in c[3]['parsing_snapshot']['ingredients']:
            self.assertIsNone(ingredient['quantity'])
            self.assertIsNone(ingredient['unit'])
            self.assertTrue(ingredient['uncertainty'])
        self.assertEqual(sorted(len(s['ingredient_ids']) for s in c[4]['parsing_snapshot']['preparation_steps']), [1, 3])

    def test_repeated_objects_remain_distinct(self):
        d = self.read('detection.json')
        other = deepcopy(d['detections'][0]);other['detection_id'] = 3
        d['detections'].append(other)
        self.write('detection.json', d)
        r = run_demo(self.root)
        self.assertEqual([o['detection_id'] for o in r['objects']], [1, 2, 3])
        self.assertEqual(len(r['objects'][2]['recipe_candidates']), 5)

    def test_no_detections_not_food_absence(self):
        d = self.read('detection.json');d.update(detections=[], status='no_detections')
        self.write('detection.json', d)
        r = run_demo(self.root)
        self.assertEqual(r['status'], 'no_detections')
        self.assertIsNone(r['photo_portion_grams'])
        self.assertNotIn('has_food', r)

    def test_missing_catalog_and_empty_catalog_safe(self):
        (self.root / 'catalog.json').unlink()
        self.assertEqual(run_demo(self.root)['status'], 'failed')
        self.write('catalog.json', {'schema_version': 1, 'recipes': []})
        r = run_demo(self.root)
        self.assertTrue(all(not o['recipe_candidates'] for o in r['objects']))

    def test_missing_and_duplicate_association(self):
        m = self.read('associations.json')
        m['associations'].pop(0);self.write('associations.json', m)
        self.assertEqual(self.first(run_demo(self.root))['reason'], 'fixture_association_missing_or_ambiguous')
        m['associations'].append(deepcopy(m['associations'][0]));self.write('associations.json', m)
        self.assertEqual(run_demo(self.root)['objects'][0]['recipe_candidates'][1]['reason'],
                         'fixture_association_missing_or_ambiguous')

    def test_hash_source_and_text_failures_no_parsing_output(self):
        original = self.read('associations.json')
        for change, code in [('catalog', 'fixture_hash_mismatch'), ('parsing', 'fixture_hash_mismatch'),
                             ('text', 'source_text_hash_mismatch'), ('missing', 'recipe_text_unavailable'),
                             ('source', 'recipe_source_mismatch'), ('case', 'parsing_fixture_unavailable')]:
            m = deepcopy(original);a = m['associations'][0]
            if change == 'catalog':a['catalog_sha256'] = 'a' * 64
            elif change == 'parsing':a['parsing_fixture_sha256'] = 'a' * 64
            elif change == 'text':a['expected_source']['text'] += ' altered'
            elif change == 'missing':a['expected_source']['text'] = ''
            elif change == 'source':a['expected_source']['source_id'] = 'other'
            else:a['input_id'] = 'UNKNOWN'
            self.write('associations.json', m)
            c = self.first(run_demo(self.root))
            self.assertEqual(c['reason'], code)
            self.assertIsNone(c['parsing_snapshot'])
            self.assertIsNone(c['mapping_snapshot'])

    def test_ambiguous_ingredient_not_auto_selected(self):
        c = self.first(run_demo(self.root))
        item = c['mapping_snapshot']['ingredients'][0]
        self.assertEqual(item['mapping']['status'], 'ambiguous')
        self.assertIsNone(item['mapping']['selected_entry_id'])
        self.assertEqual(item['mass']['mass_g'], 200)  # Recipe declared, never photo portion.
        self.assertEqual(c['source_diagnostic']['lookup'], None)

    def test_link_without_snapshot_blocked_and_no_hidden_load(self):
        self.enable_link()
        c = self.first(run_demo(self.root))
        self.assertEqual(c['source_diagnostic']['ingredients'][0]['reason'], 'external_snapshot_not_supplied')
        self.assertIsNone(c['source_diagnostic']['lookup'])

    def test_explicit_candidate_lookup_and_decimal_serialization(self):
        _, adapter = self.enable_link()
        r = run_demo(self.root, source_adapter=adapter)
        adapter.lookup.assert_called_once_with('SYNTHETIC0')
        lookup = self.first(r)['source_diagnostic']['lookup']
        self.assertEqual(lookup['ingredients'][0]['candidates'][0]['source_status'], 'found')
        self.assertEqual(lookup['ingredients'][0]['identity_status'], 'pending')
        self.assertFalse(lookup['nutrition_eligible'])
        obs = lookup['ingredients'][0]['candidates'][0]['source_lookup']['records'][0]['observations'][0]
        self.assertEqual(obs['value']['raw_value'], ' 1.0')
        self.assertEqual(obs['value']['parsed_value'], '1.0')

    def test_bad_links_and_assertions_never_query(self):
        original, adapter = self.enable_link()
        for scenario in ('id', 'hash', 'scope', 'assertion'):
            m = deepcopy(original);a = m['associations'][0];identifier = 'ing_3_6'
            if scenario == 'id':a['source_links'][identifier]['candidates'][0]['food_id'] = ''
            elif scenario == 'hash':a['source_links'][identifier]['candidates'][0]['expected_archive_sha256'] = 'bad'
            elif scenario == 'scope':a['source_links'][identifier]['ingredient_id'] = 'another'
            else:a['identity_assertions'][identifier]['source_text_sha256'] = 'a' * 64
            self.write('associations.json', m)
            r = run_demo(self.root, source_adapter=adapter)
            adapter.lookup.assert_not_called()
            self.assertIsNone(self.first(r)['source_diagnostic'].get('lookup'))

    def test_valid_shape_wrong_snapshot_hash_remains_invalid(self):
        m, adapter = self.enable_link()
        m['associations'][0]['source_links']['ing_3_6']['candidates'][0]['expected_archive_sha256'] = 'c' * 64
        self.write('associations.json', m)
        c = self.first(run_demo(self.root, source_adapter=adapter))['source_diagnostic']['lookup']['ingredients'][0]['candidates'][0]
        self.assertEqual(c['link_check'], 'invalid')
        self.assertIsNone(c['source_lookup'])

    def test_serialization_missing_zero_ratio_without_invention(self):
        for raw in (None, '', ' 0.0', 'P/M/S'):
            value = json_value(parse_value(raw))
            self.assertEqual(value['raw_value'], raw)
            self.assertEqual(value['parsed_value'], '0.0' if raw == ' 0.0' else None)
        self.assertEqual(json_value(Decimal('1.234567890000')), '1.234567890000')

    def test_no_real_model_calls_determinism_and_fixture_immutability(self):
        before = {p.name: sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir()}
        with patch('src.detection.model._load_yolo') as yolo, \
                patch('src.recipe_parsing.ollama.HTTPStream') as ollama:
            r = run_demo(self.root)
            self.assertEqual(r, run_demo(self.root))
            yolo.assert_not_called();ollama.assert_not_called()
        self.assertEqual(before, {p.name: sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir()})
        self.assertNotIn(str(self.root), json.dumps(r))

    def test_partial_needs_review_preserved_at_composition_boundary(self):
        from src.recipe_parsing import MockRecipeParsingAdapter
        parsed = MockRecipeParsingAdapter().parse('case_01_clear_quantities')
        parsed.update(status='partial', review_status='needs_review')
        parsed['issues'] = [{'code': 'coverage_unknown', 'severity': 'warning', 'path': '',
                             'message': 'Synthetic integration boundary test'}]
        self.assertTrue(validate_recipe_parsing(parsed).valid)
        with patch.object(MockRecipeParsingAdapter, 'parse', return_value=parsed):
            c = self.first(run_demo(self.root))
        self.assertEqual(c['parsing_snapshot']['status'], 'partial')
        self.assertEqual(c['mapping_snapshot']['parsing_snapshot']['review_status'], 'needs_review')
        self.assertFalse(c['mapping_snapshot']['qualification']['eligible_for_production'])

    def test_parsing_file_pin_portable_without_changing_source_text(self):
        from src.pipeline.diagnostic_demo import PARSING_FIXTURE
        raw = PARSING_FIXTURE.read_bytes()
        # Mock the file read at the boundary; do not overwrite the original evidence.
        original = Path.read_bytes
        def read(path):
            return raw.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n') if path == PARSING_FIXTURE else original(path)
        with patch.object(Path, 'read_bytes', read):
            self.assertIsNotNone(self.first(run_demo(self.root))['parsing_snapshot'])

    def test_cli_stdout_json_stderr_short_summary(self):
        p = subprocess.run([sys.executable, '-B', '-m', 'src.pipeline.diagnostic_demo', '--fixtures', str(self.root)],
                           capture_output=True, encoding='utf-8', timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)['status'], 'partial')
        self.assertIn('SYNTHETIC FIXTURE ONLY', p.stderr)
        self.assertNotIn(str(self.root), p.stdout + p.stderr)


if __name__ == '__main__':
    unittest.main()
