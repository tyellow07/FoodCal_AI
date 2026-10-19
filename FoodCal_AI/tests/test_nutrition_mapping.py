"""Synthetic-only integration and failure-boundary tests; no model calls."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from src.nutrition import MappingFixtureError, MockNutritionMappingAdapter
from src.nutrition.mapping import DEFAULT_FIXTURE_PATH
from src.recipe_parsing import MockRecipeParsingAdapter


class NutritionMappingTests(unittest.TestCase):
    def setUp(self):
        parser = MockRecipeParsingAdapter()
        self.cases = [parser.parse(identity) for identity in parser.input_ids]
        self.adapter = MockNutritionMappingAdapter()
        self.catalog = json.loads(DEFAULT_FIXTURE_PATH.read_text(encoding='utf-8'))

    def run_map(self, case=0, **kwargs):
        value = self.cases[case]
        return self.adapter.map(value, expected_source=deepcopy(value['source']), **kwargs)

    def assertion(self, entry=0, case=0):
        return {'attributes': deepcopy(self.catalog['entries'][entry]['identity']),
                'evidence_reference': 'tests/fixtures/nutrition_mapping.json#caller-test-identity',
                'source_text_sha256': self.cases[case]['source']['text_sha256']}

    def test_all_five_contract_examples_integrate(self):
        for case in range(5):
            with self.subTest(case=case):
                result = self.run_map(case)
                self.assertEqual(len(result['ingredients']), len(self.cases[case]['ingredients']))
                self.assertEqual(result['source_check'], 'matches_caller_snapshot')
                self.assertEqual(result['parsing_snapshot'], self.cases[case])

    def test_unique_explicit_identity_matches(self):
        ingredient_id = self.cases[0]['ingredients'][0]['ingredient_id']
        result = self.run_map(identity_assertions={ingredient_id: self.assertion()})
        item = result['ingredients'][0]
        self.assertEqual(item['mapping']['status'], 'matched')
        self.assertEqual(item['mapping']['selected_entry_id'], 'synthetic-chicken-breast-raw')
        self.assertEqual(item['mass']['mass_g'], 200)
        self.assertFalse(item['qualification']['eligible_for_production'])
        self.assertIsNone(item['mapping']['candidates'][0]['nutrients'])

    def test_single_name_candidate_does_not_prove_identity(self):
        item = self.run_map()['ingredients'][0]
        self.assertEqual(len(item['mapping']['candidates']), 1)
        self.assertEqual(item['mapping']['status'], 'ambiguous')
        self.assertIsNone(item['mapping']['selected_entry_id'])

    def test_raw_cooked_ambiguity_and_explicit_selection(self):
        item = self.run_map(4)['ingredients'][3]
        self.assertEqual(len(item['mapping']['candidates']), 2)
        assertion = self.assertion(2, case=4)
        assertion['attributes']['state'] = None
        identity = item['ingredient_id']
        ambiguous = self.run_map(4, identity_assertions={identity: assertion})['ingredients'][3]
        self.assertEqual(ambiguous['mapping']['reason'], 'identity_unresolved')
        matched = self.run_map(4, identity_assertions={identity: self.assertion(2, case=4)})['ingredients'][3]
        self.assertEqual(matched['mapping']['selected_entry_id'], 'synthetic-beef-raw')

    def test_multiple_identical_entries_remain_ambiguous(self):
        catalog = deepcopy(self.catalog)
        entry = deepcopy(catalog['entries'][0])
        entry['entry_id'] = 'synthetic-second-record'
        catalog['entries'].append(entry)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.json'
            path.write_text(json.dumps(catalog), encoding='utf-8')
            adapter = MockNutritionMappingAdapter(path)
            case = self.cases[0]
            result = adapter.map(case, expected_source=case['source'], identity_assertions={
                case['ingredients'][0]['ingredient_id']: self.assertion()})
        self.assertEqual(result['ingredients'][0]['mapping']['reason'], 'multiple_compatible_entries')

    def test_no_match_does_not_create_entry_or_nutrients(self):
        item = self.run_map()['ingredients'][2]
        self.assertEqual(item['mapping']['reason'], 'no_exact_name_candidate')
        self.assertEqual(item['mapping']['candidates'], [])
        self.assertIsNone(item['mapping']['selected_entry_id'])
        self.assertEqual(item['mass']['mass_g'], 1)

    def test_explicit_identity_conflicts_including_weight_basis(self):
        for key in ('food', 'part', 'state', 'quality', 'weight_basis'):
            with self.subTest(key=key):
                assertion = self.assertion()
                assertion['attributes'][key] = 'conflicting-value'
                identity = self.cases[0]['ingredients'][0]['ingredient_id']
                item = self.run_map(identity_assertions={identity: assertion})['ingredients'][0]
                self.assertEqual(item['mapping']['reason'], 'identity_conflict')
                self.assertIsNone(item['mapping']['selected_entry_id'])
                # Declared grams survive; compatibility with the nutrient basis does not.
                self.assertEqual(item['mass']['mass_g'], 200)

    def test_missing_quantity_stays_null(self):
        for item in self.run_map(3)['ingredients']:
            self.assertIsNone(item['mass']['mass_g'])
            self.assertEqual(item['mass']['reason'], 'quantity_missing')
            self.assertTrue(item['ingredient']['uncertainty'])

    def test_vague_quantity_keeps_words_and_no_grams(self):
        for item in self.run_map(1)['ingredients'][:2]:
            self.assertIn(item['ingredient']['quantity_raw'], ('適量', '少許'))
            self.assertEqual(item['mass']['reason'], 'quantity_vague')
            self.assertIsNone(item['mass']['mass_g'])

    def test_half_and_optional_are_preserved_without_conversion(self):
        items = self.run_map(2)['ingredients']
        self.assertEqual(items[1]['ingredient']['quantity'], 0.5)
        self.assertEqual(items[1]['ingredient']['quantity_raw'], '半')
        self.assertEqual(items[1]['ingredient']['unit'], '顆')
        self.assertIsNone(items[1]['mass']['mass_g'])
        self.assertTrue(items[2]['ingredient']['optional'])
        self.assertFalse(items[0]['ingredient']['optional'])
        self.assertEqual(items[0]['ingredient']['optional_state'], 'not_marked')

    def test_ml_spoons_and_roots_have_no_unfounded_conversion(self):
        for case, index in ((0, 3), (1, 2), (2, 2), (4, 0)):
            with self.subTest(case=case, index=index):
                mass = self.run_map(case)['ingredients'][index]['mass']
                self.assertEqual(mass['reason'], 'conversion_basis_unavailable')
                self.assertIsNone(mass['mass_g'])

    def test_g_mass_is_recipe_declared_not_photo_or_cooked_mass(self):
        mass = self.run_map(4)['ingredients'][3]['mass']
        self.assertEqual(mass['mass_g'], 500)
        self.assertEqual(mass['basis'], 'recipe_declared_ingredient')
        self.assertEqual(mass['rule'], 'literal_g')

    def test_steps_evidence_scope_and_no_nutrient_adjustment(self):
        result = self.run_map(4)
        self.assertEqual(result['parsing_snapshot']['preparation_steps'], self.cases[4]['preparation_steps'])
        self.assertEqual(result['parsing_snapshot']['evidence'], self.cases[4]['evidence'])
        steps = result['parsing_snapshot']['preparation_steps']
        self.assertEqual(len(steps), 2)
        self.assertEqual(len(steps[0]['ingredient_ids']), 3)
        for item in result['ingredients']:
            self.assertEqual(len(item['preparation_step_ids']), 1)
        self.assertNotIn('calories', result)

    def test_test_only_and_unapproved_sources_never_gain_eligibility(self):
        for kind, review in (('test_fixture', 'test_only'), ('unverified', 'unreviewed'),
                            ('production', 'unreviewed'), ('production', 'approved')):
            with self.subTest(kind=kind):
                case = deepcopy(self.cases[0])
                case['source'].update(kind=kind, review_status=review)
                result = self.adapter.map(case, expected_source=deepcopy(case['source']))
                self.assertFalse(result['qualification']['eligible_for_production'])
                self.assertEqual(result['qualification']['status'], 'test_only')
                if review != 'approved':
                    self.assertIn('recipe_source_not_production_approved', result['qualification']['reasons'])

    def test_forged_source_approval_is_rejected(self):
        case = deepcopy(self.cases[0])
        case['source'].update(kind='production', review_status='approved')
        result = self.adapter.map(case, expected_source=self.cases[0]['source'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['issues'][0]['code'], 'source_snapshot_mismatch')
        self.assertEqual(result['ingredients'], [])
        self.assertIsNone(result['parsing_snapshot'])

    def test_invalid_parsing_contract_is_rejected_with_paths(self):
        for value in (None, [], {}, {'contract': 'wrong'}):
            with self.subTest(value=value):
                result = self.adapter.map(value, expected_source=None)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['ingredients'], [])
                self.assertEqual(result['issues'][0]['code'], 'invalid_parsing_contract')
                self.assertTrue(result['issues'][0]['path'].startswith('/parsing_snapshot'))

    def test_bad_numeric_and_evidence_values_are_not_repaired(self):
        for bad in (True, float('nan'), float('inf'), -1, '200'):
            with self.subTest(bad=bad):
                case = deepcopy(self.cases[0])
                case['ingredients'][0]['quantity'] = bad
                result = self.adapter.map(case, expected_source=case['source'])
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['ingredients'], [])
        case = deepcopy(self.cases[0])
        case['evidence'][0]['evidence_text'] = 'invented'
        self.assertEqual(self.adapter.map(case, expected_source=case['source'])['status'], 'failed')

    def test_invalid_assertion_is_a_local_error_with_partial_success(self):
        ids = [item['ingredient_id'] for item in self.cases[0]['ingredients']]
        result = self.run_map(identity_assertions={ids[0]: self.assertion(), ids[1]: {'approved': True}})
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['ingredients'][0]['mapping']['status'], 'matched')
        item = result['ingredients'][1]
        self.assertEqual(item['mapping']['status'], 'error')
        self.assertEqual(item['issues'][0]['path'], '/ingredients/1/identity_assertion')
        self.assertIsNone(item['identity_assertion'])

    def test_invalid_identity_field_type_and_missing_reference(self):
        identity = self.cases[0]['ingredients'][0]['ingredient_id']
        for value in (True, 1, {}, []):
            assertion = self.assertion()
            assertion['attributes']['state'] = value
            item = self.run_map(identity_assertions={identity: assertion})['ingredients'][0]
            self.assertEqual(item['mapping']['status'], 'error')
        assertion = self.assertion()
        assertion['evidence_reference'] = ''
        self.assertEqual(self.run_map(identity_assertions={identity: assertion})['ingredients'][0]['mapping']['status'], 'error')

    def test_assertions_cannot_cross_snapshot_scope(self):
        for assertions in ([], {'ing_999_1000': self.assertion()}):
            result = self.run_map(identity_assertions=assertions)
            self.assertEqual(result['issues'][0]['code'], 'invalid_identity_scope')
            self.assertEqual(result['ingredients'], [])

    def test_assertion_hash_mismatch_is_local_error(self):
        identity = self.cases[0]['ingredients'][0]['ingredient_id']
        assertion = self.assertion(case=4)
        result = self.run_map(identity_assertions={identity: assertion})
        self.assertEqual(result['ingredients'][0]['mapping']['status'], 'error')
        self.assertIsNone(result['ingredients'][0]['mapping']['selected_entry_id'])

    def replaced_snapshot(self, before, after):
        self.assertEqual(len(before), len(after))
        case = deepcopy(self.cases[0])
        case['source']['text'] = case['source']['text'].replace(before, after)
        case['source']['text_sha256'] = sha256(case['source']['text'].encode('utf-8')).hexdigest()
        for evidence in case['evidence']:
            evidence['evidence_text'] = case['source']['text'][evidence['start']:evidence['end']]
        return case

    def test_explicit_zero_remains_zero_not_missing(self):
        case = self.replaced_snapshot('200', '000')
        case['ingredients'][0].update(quantity=0, quantity_raw='000')
        result = self.adapter.map(case, expected_source=case['source'])
        self.assertEqual(result['ingredients'][0]['mass']['status'], 'available')
        self.assertEqual(result['ingredients'][0]['mass']['mass_g'], 0)

    def test_explicit_chinese_grams_use_the_same_literal_rule(self):
        case = self.replaced_snapshot('g', '克')
        for ingredient in case['ingredients']:
            if ingredient['unit'] == 'g':
                ingredient['unit'] = '克'
        result = self.adapter.map(case, expected_source=case['source'])
        self.assertEqual(result['ingredients'][0]['mass']['mass_g'], 200)

    def test_exact_quantity_without_unit_remains_unavailable(self):
        case = deepcopy(self.cases[0])
        case['ingredients'][0].update(unit=None, uncertainty=True)
        case['issues'].append({'code': 'unit_missing', 'severity': 'info',
                               'path': '/ingredients/0/unit', 'message': 'Synthetic missing-unit test'})
        result = self.adapter.map(case, expected_source=case['source'])
        self.assertEqual(result['ingredients'][0]['mass']['reason'], 'unit_missing')
        self.assertIsNone(result['ingredients'][0]['mass']['mass_g'])

    def test_unparsed_range_is_preserved_not_averaged(self):
        case = self.replaced_snapshot('200', '1~2')
        case['ingredients'][0].update(quantity=None, quantity_raw='1~2', quantity_state='unparsed',
                                      quantity_rule=None, uncertainty=True)
        case['status'] = 'partial'
        case['review_status'] = 'needs_review'
        case['issues'].append({'code': 'quantity_unparsed', 'severity': 'warning',
                               'path': '/ingredients/0/quantity', 'message': 'Synthetic range test'})
        result = self.adapter.map(case, expected_source=case['source'])
        self.assertEqual(result['ingredients'][0]['mass']['reason'], 'quantity_unparsed')
        self.assertIsNone(result['ingredients'][0]['mass']['mass_g'])

    def test_complete_only_means_mapping_and_mass_not_production(self):
        case = deepcopy(self.cases[0])
        case['ingredients'] = case['ingredients'][:2]
        assertions = {case['ingredients'][i]['ingredient_id']: self.assertion(i) for i in range(2)}
        result = self.adapter.map(case, expected_source=case['source'], identity_assertions=assertions)
        self.assertEqual(result['status'], 'complete')
        self.assertFalse(result['qualification']['eligible_for_production'])

    def test_matched_half_ingredient_still_has_no_mass(self):
        catalog = deepcopy(self.catalog)
        entry = catalog['entries'][0]
        entry['ingredient_name'] = '洋蔥'
        entry['identity'].update(food='onion', part='whole')
        case = self.cases[2]
        assertion = {'attributes': deepcopy(entry['identity']),
                     'evidence_reference': 'tests/fixtures/nutrition_mapping.json#synthetic-override',
                     'source_text_sha256': case['source']['text_sha256']}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.json'
            path.write_text(json.dumps(catalog), encoding='utf-8')
            result = MockNutritionMappingAdapter(path).map(
                case, expected_source=case['source'],
                identity_assertions={case['ingredients'][1]['ingredient_id']: assertion})
        item = result['ingredients'][1]
        self.assertEqual(item['mapping']['status'], 'matched')
        self.assertEqual(item['mass']['status'], 'unavailable')
        self.assertIsNone(item['mass']['mass_g'])
        self.assertFalse(item['qualification']['eligible_for_production'])

    def test_all_local_assertion_errors_return_failed_diagnostics(self):
        assertions = {item['ingredient_id']: {} for item in self.cases[0]['ingredients']}
        result = self.run_map(identity_assertions=assertions)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(result['ingredients']), 4)
        self.assertTrue(all(item['issues'] for item in result['ingredients']))
        self.assertIsNotNone(result['parsing_snapshot'])

    def test_public_result_is_json_serializable_and_deterministic(self):
        first = self.run_map(4)
        second = self.run_map(4)
        self.assertEqual(first, second)
        self.assertEqual(json.loads(json.dumps(first, allow_nan=False)), first)

    def test_unavailable_and_failed_parsing_remain_empty(self):
        from src.recipe_parsing import diagnostic_result
        for status in ('unavailable', 'failed'):
            case = diagnostic_result('empty-fixture', status=status, code='input_empty', message='test')
            result = self.adapter.map(case, expected_source=None)
            self.assertEqual(result['status'], status)
            self.assertEqual(result['ingredients'], [])

    def test_returned_snapshots_do_not_mutate_inputs_or_catalog(self):
        before = deepcopy(self.cases[0])
        identity = before['ingredients'][0]['ingredient_id']
        assertion = self.assertion()
        copy_assertion = deepcopy(assertion)
        result = self.run_map(identity_assertions={identity: assertion})
        result['parsing_snapshot']['source']['text'] = 'changed'
        result['ingredients'][0]['mapping']['candidates'][0]['identity']['state'] = 'changed'
        result['ingredients'][0]['identity_assertion']['attributes']['state'] = 'changed'
        self.assertEqual(self.cases[0], before)
        self.assertEqual(assertion, copy_assertion)
        self.assertEqual(self.run_map()['ingredients'][0]['mapping']['candidates'][0]['identity']['state'], 'raw')

    def test_fixture_hash_is_actual_bytes(self):
        self.assertEqual(self.adapter.fixture_sha256, sha256(DEFAULT_FIXTURE_PATH.read_bytes()).hexdigest())

    def test_unsafe_or_malformed_fixtures_are_rejected(self):
        variants = []
        for change in ('approval', 'nutrients', 'duplicate', 'unknown_field', 'basis', 'missing_identity'):
            catalog = deepcopy(self.catalog)
            if change == 'approval':
                catalog['entries'][0]['source']['review_status'] = 'approved'
            elif change == 'nutrients':
                catalog['entries'][0]['nutrients'] = {'kcal': 100}
            elif change == 'duplicate':
                catalog['entries'].append(deepcopy(catalog['entries'][0]))
            elif change == 'unknown_field':
                catalog['entries'][0]['approved'] = True
            elif change == 'basis':
                catalog['entries'][0]['identity']['weight_basis'] = 'whole_with_bone'
            else:
                del catalog['entries'][0]['identity']['part']
            variants.append(json.dumps(catalog))
        variants.extend(('{}', '[]', '{invalid', '{"kind":"synthetic","kind":"production"}', 'NaN'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.json'
            for text in variants:
                path.write_text(text, encoding='utf-8')
                with self.subTest(text=text[:60]), self.assertRaises(MappingFixtureError):
                    MockNutritionMappingAdapter(path)
            with self.assertRaises(MappingFixtureError):
                MockNutritionMappingAdapter(Path(directory) / 'missing.json')


if __name__ == '__main__':
    unittest.main()
