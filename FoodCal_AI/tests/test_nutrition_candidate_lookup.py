"""Candidate composition tests: synthetic Source responses, no private snapshot."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock
import unittest

from src.recipe_parsing import MockRecipeParsingAdapter
from src.nutrition.mapping import MockNutritionMappingAdapter
from src.nutrition.candidate_lookup import review_ingredient_sources
from src.nutrition.tfda_source import (FoodRecord, NutrientObservation, SourceEvidence,
                                       SourceLookup, TFDANutritionSourceAdapter, parse_value)


class CandidateLookupTests(unittest.TestCase):
    def setUp(self):
        parser = MockRecipeParsingAdapter()
        self.cases = [parser.parse(key) for key in parser.input_ids]
        self.case = self.cases[0]
        self.mapper = MockNutritionMappingAdapter()
        self.adapter = Mock(spec=TFDANutritionSourceAdapter)
        self.evidence = SourceEvidence('test://synthetic', 'SYNTHETIC', 'a' * 64, 'b' * 64,
                                       'synthetic.json', 'c' * 64, 7, 'd' * 64, 0)
        self.food = FoodRecord('I0402201', 'SYNTHETIC chicken', 'synthetic/test_only', None, None,
                              tuple(NutrientObservation(name, 'synthetic', parse_value(value), unit,
                                                       self.evidence, (('每100克含量', value),))
                                    for name, value, unit in (('熱量', ' 1.234567890123456789', 'kcal'),
                                                               ('missing', None, 'g'), ('ratio', '1/2', None))), ())
        self.adapter.lookup.side_effect = lambda identifier: (
            SourceLookup('found', 'SYNTHETIC', (self.food,)) if identifier == self.food.food_id
            else SourceLookup('not_found', 'id_not_in_fixed_sample'))

    def link(self, case=None, index=0):
        case = self.case if case is None else case
        identifier = case['ingredients'][index]['ingredient_id']
        return {identifier: {'ingredient_id': identifier, 'input_id': case['input_id'],
                             'recipe_text_sha256': case['source']['text_sha256'], 'candidates': [{
                                 'food_id': 'I0402201', 'expected_archive_sha256': 'a' * 64,
                                 'expected_sample_sha256': 'c' * 64,
                                 'evidence_reference': 'test://caller-synthetic-binding'}]}}

    def run_review(self, case=None, **kwargs):
        case = self.case if case is None else case
        return review_ingredient_sources(case, expected_source=deepcopy(case['source']),
                                         mock_mapper=self.mapper, source_adapter=self.adapter, **kwargs)

    def test_explicit_id_preserves_full_source_and_decimal(self):
        item = self.run_review(source_links=self.link())['ingredients'][0]
        candidate = item['candidates'][0]
        self.assertEqual(candidate['link_check'], 'verified')
        self.assertIs(candidate['source_lookup'].records[0], self.food)
        self.assertEqual(self.food.observations[0].value.parsed_value, Decimal('1.234567890123456789'))
        self.assertEqual(self.food.observations[0].value.raw_value, ' 1.234567890123456789')
        self.assertIsNone(self.food.observations[1].value.parsed_value)
        self.assertEqual(self.food.observations[2].value.status, 'unparsed')
        self.assertEqual(item['identity_status'], 'pending')
        self.assertIn('missing_assertion', item['reasons'])
        self.assertFalse(item['nutrition_eligible'])

    def test_mock_matched_cannot_approve_tfda(self):
        identifier = self.case['ingredients'][0]['ingredient_id']
        assertion = {'attributes': deepcopy(self.mapper._entries[0]['identity']),
                     'evidence_reference': 'test://synthetic-identity',
                     'source_text_sha256': self.case['source']['text_sha256']}
        result = self.run_review(source_links=self.link(), identity_assertions={identifier: assertion})
        item = result['ingredients'][0]
        self.assertEqual(item['mock_mapping']['mapping']['status'], 'matched')
        self.assertEqual(item['identity_status'], 'pending')
        self.assertEqual(item['caller_assertion'], assertion)
        self.assertFalse(result['nutrition_eligible'])
        self.assertEqual(result['usage'], 'diagnostic/test_only')

    def test_wrong_id_never_uses_name_fallback(self):
        links = self.link()
        next(iter(links.values()))['candidates'][0]['food_id'] = '雞胸肉'
        item = self.run_review(source_links=links)['ingredients'][0]
        self.assertEqual(item['candidates'][0]['source_status'], 'not_found')
        self.adapter.lookup.assert_called_once_with('雞胸肉')

    def test_hash_mismatch_never_returns_unverified_record(self):
        for field, reason in (('expected_archive_sha256', 'source_snapshot_hash_mismatch'),
                              ('expected_sample_sha256', 'source_record_hash_mismatch')):
            links = self.link()
            next(iter(links.values()))['candidates'][0][field] = '0' * 64
            candidate = self.run_review(source_links=links)['ingredients'][0]['candidates'][0]
            self.assertEqual(candidate['source_reason'], reason)
            self.assertEqual(candidate['link_check'], 'invalid')
            self.assertIsNone(candidate['source_lookup'])

    def test_recipe_scope_mismatch_stops_query(self):
        for field, value in (('input_id', 'wrong'), ('ingredient_id', 'ing_999_1000'),
                              ('recipe_text_sha256', '0' * 64)):
            self.adapter.lookup.reset_mock()
            links = self.link()
            next(iter(links.values()))[field] = value
            result = self.run_review(source_links=links)
            self.assertEqual(result['issues'][0]['code'], 'parsing_scope_hash_mismatch')
            self.adapter.lookup.assert_not_called()

    def test_unknown_ingredient_key_and_wrong_source_rejected(self):
        result = self.run_review(source_links={'ing_999_1000': next(iter(self.link().values()))})
        self.assertEqual(result['status'], 'failed')
        self.adapter.lookup.assert_not_called()
        expected = deepcopy(self.case['source'])
        expected['text_sha256'] = '0' * 64
        result = review_ingredient_sources(self.case, expected_source=expected, mock_mapper=self.mapper,
                                           source_adapter=self.adapter, source_links=self.link())
        self.assertEqual(result['status'], 'failed')
        self.adapter.lookup.assert_not_called()

    def test_invalid_assertion_hash_and_forged_approval_block_query(self):
        identifier = self.case['ingredients'][0]['ingredient_id']
        for assertion in ({'approved': True}, {'attributes': self.mapper._entries[0]['identity'],
                                               'evidence_reference': 'test://bad', 'source_text_sha256': '0' * 64}):
            result = self.run_review(source_links=self.link(), identity_assertions={identifier: assertion})
            self.assertIn('invalid_identity_assertion', result['ingredients'][0]['reasons'])
            self.adapter.lookup.assert_not_called()

    def test_missing_quantity_and_basis_never_filled(self):
        case = self.cases[3]
        item = self.run_review(case, source_links=self.link(case))['ingredients'][0]
        self.assertIsNone(item['mock_mapping']['ingredient']['quantity'])
        self.assertIsNone(item['mock_mapping']['mass']['mass_g'])
        self.assertTrue(item['mock_mapping']['ingredient']['uncertainty'])
        self.assertEqual(item['basis_status'], 'unresolved')
        self.assertIn('unresolved_basis', item['reasons'])
        self.assertEqual(item['candidates'][0]['source_lookup'].records[0].food_state_applicability, 'unresolved')

    def test_ambiguous_source_and_multiple_candidates_stay_ambiguous(self):
        self.adapter.lookup.side_effect = None
        self.adapter.lookup.return_value = SourceLookup('ambiguous', 'multiple_records_for_id',
                                                        (self.food, replace(self.food, original_name='SYNTHETIC other')))
        item = self.run_review(source_links=self.link())['ingredients'][0]
        self.assertEqual(item['identity_status'], 'ambiguous')
        self.assertEqual(len(item['candidates'][0]['source_lookup'].records), 2)
        self.assertFalse(item['nutrition_eligible'])

    def test_partial_and_test_only_snapshot_preserved(self):
        case = deepcopy(self.cases[1])
        case.update(status='partial', review_status='needs_review')
        case['issues'].append({'code': 'coverage_unknown', 'severity': 'warning',
                               'path': '/ingredients', 'message': 'Synthetic partial coverage test'})
        result = self.run_review(case)
        snapshot = result['mock_mapping_snapshot']['parsing_snapshot']
        self.assertEqual(snapshot, case)
        self.assertEqual(snapshot['status'], 'partial')
        self.assertEqual(snapshot['review_status'], 'needs_review')
        self.assertEqual(snapshot['source']['review_status'], 'test_only')
        self.assertTrue(all('missing_source_link' in item['reasons'] for item in result['ingredients']))

    def test_bad_link_shapes_and_missing_evidence_rejected(self):
        for field, value in (('food_id', None), ('expected_sample_sha256', 'bad'), ('evidence_reference', '')):
            links = self.link()
            next(iter(links.values()))['candidates'][0][field] = value
            result = self.run_review(source_links=links)
            self.assertEqual(result['issues'][0]['code'], 'invalid_source_link')
            self.adapter.lookup.assert_not_called()

    def test_invalid_source_and_exception_are_diagnostics(self):
        self.adapter.lookup.side_effect = None
        self.adapter.lookup.return_value = SourceLookup('invalid_record', 'pinned_hash_mismatch')
        self.assertEqual(self.run_review(source_links=self.link())['ingredients'][0]['candidates'][0]['source_reason'],
                         'pinned_hash_mismatch')
        self.adapter.lookup.side_effect = ValueError('private detail')
        result = self.run_review(source_links=self.link())
        self.assertNotIn('private detail', repr(result))
        self.assertEqual(result['ingredients'][0]['candidates'][0]['source_status'], 'not_queried')

    def test_inputs_and_original_contract_are_unchanged(self):
        links = self.link()
        before = deepcopy((self.case, links))
        result = self.run_review(source_links=links)
        self.assertEqual((self.case, links), before)
        self.assertEqual(result['mock_mapping_snapshot'], self.mapper.map(self.case, expected_source=self.case['source']))
        result['ingredients'][0]['candidates'][0]['request']['food_id'] = 'changed'
        self.assertEqual((self.case, links), before)

    def test_distinct_ingredients_never_merge_and_duplicate_requests_are_deduplicated(self):
        links = self.link(index=0)
        links.update(self.link(index=1))
        first = next(iter(links.values()))
        first['candidates'].append(deepcopy(first['candidates'][0]))
        result = self.run_review(source_links=links)
        self.assertNotEqual(result['ingredients'][0]['ingredient_id'], result['ingredients'][1]['ingredient_id'])
        self.assertEqual(len(result['ingredients'][0]['candidates']), 1)
        self.assertEqual(len(result['ingredients'][1]['candidates']), 1)
        self.assertEqual(self.adapter.lookup.call_count, 2)

    def test_complete_query_flow_still_diagnostic_and_ineligible(self):
        links = {}
        for index in range(len(self.case['ingredients'])):
            links.update(self.link(index=index))
        result = self.run_review(source_links=links)
        self.assertEqual(result['status'], 'diagnostic')
        self.assertTrue(all(item['identity_status'] == 'pending' for item in result['ingredients']))
        self.assertFalse(result['nutrition_eligible'])

    def test_multiple_explicit_ids_stay_separate(self):
        second = replace(self.food, food_id='I04021', original_name='SYNTHETIC average')
        self.adapter.lookup.side_effect = lambda identifier: SourceLookup('found', 'SYNTHETIC',
                                                                        (self.food if identifier == 'I0402201' else second,))
        links = self.link()
        request = deepcopy(next(iter(links.values()))['candidates'][0])
        request['food_id'] = 'I04021'
        next(iter(links.values()))['candidates'].append(request)
        item = self.run_review(source_links=links)['ingredients'][0]
        self.assertEqual(item['identity_status'], 'ambiguous')
        self.assertEqual([candidate['request']['food_id'] for candidate in item['candidates']], ['I0402201', 'I04021'])
        self.assertFalse(item['nutrition_eligible'])


if __name__ == '__main__':
    unittest.main()
