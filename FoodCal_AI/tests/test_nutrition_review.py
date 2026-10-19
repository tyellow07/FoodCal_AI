"""Synthetic/test-only evidence protocol, never official approval or semantic inference."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import json
import unittest
from unittest.mock import Mock

from src.recipe_parsing import MockRecipeParsingAdapter, validate_recipe_parsing
from src.nutrition.mapping import MockNutritionMappingAdapter
from src.nutrition.candidate_lookup import review_ingredient_sources
from src.nutrition.tfda_source import FIELDS, FoodRecord, NutrientObservation, SourceEvidence, SourceLookup, parse_value
from src.nutrition.review_types import ReviewContext, ReviewTrustAnchor
from src.nutrition.review_validation import validate_review, IDENTITY_ATTRIBUTES, BASIS_ATTRIBUTES, NA_RULE
from src.nutrition.review_aggregation import aggregate_review


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')


class SyntheticReview:
    """Independent fixture owner pins ledger; test requests cannot self-attest."""
    def __init__(self, missing_quantity=False, unit='kcal', version='synthetic-v1', recipe_state='生'):
        parser = MockRecipeParsingAdapter()
        self.parsing = parser.parse('case_01_clear_quantities')
        text = '雞胸肉' + ('' if missing_quantity else '200g') + f'（{recipe_state}、胸部、未加工、測試規格）'
        self.parsing.update(input_id='synthetic_review', ingredients=[deepcopy(self.parsing['ingredients'][0])],
                            preparation_steps=[], evidence=[], issues=[])
        self.parsing['source'].update(text=text, text_sha256=sha256(text.encode()).hexdigest())
        ingredient = self.parsing['ingredients'][0]
        ingredient.update(ingredient_id='ing_0_3', anchor={'start': 0, 'end': 3},
                          evidence_links=[{'evidence_id': f'ev_0_{len(text)}', 'supports': ['name', 'quantity', 'unit']}])
        if missing_quantity:
            ingredient.update(quantity=None, quantity_raw=None, quantity_rule=None, quantity_state='missing',
                              unit=None, uncertainty=True)
            ingredient['evidence_links'][0]['supports'] = ['name']
            self.parsing['issues'].append({'code': 'quantity_missing', 'severity': 'info',
                                            'path': '/ingredients/0', 'message': 'Synthetic source omits quantity and unit'})
        self.parsing['evidence'] = [{'evidence_id': f'ev_0_{len(text)}', 'start': 0, 'end': len(text), 'evidence_text': text}]
        assert validate_recipe_parsing(self.parsing).valid, validate_recipe_parsing(self.parsing).errors
        row = dict.fromkeys(FIELDS)
        row.update({'整合編號': 'SYNTHETIC0', '樣品名稱': '合成雞胸肉', '資料類別': 'synthetic/test_only',
                    '內容物描述': 'SYNTHETIC:生、胸部、無加工、測試規格', '分析項': '熱量',
                    '含量單位': unit, '每100克含量': ' 1.0'})
        object_text = json.dumps(row, ensure_ascii=False)
        sample = ('[' + object_text + ']').encode('utf-8')
        self.sample_hash = sha256(sample).hexdigest()
        proof = SourceEvidence('test://synthetic', 'TEST_TIME', 'a' * 64, 'b' * 64,
                               'sample.json', self.sample_hash, 7, sha256(object_text.encode()).hexdigest(), 0)
        observation = NutrientObservation('熱量', None, parse_value(' 1.0'), unit, proof, tuple(row.items()))
        food = FoodRecord('SYNTHETIC0', row['樣品名稱'], row['資料類別'], None, row['內容物描述'], (observation,), ())
        adapter = Mock()
        adapter.lookup.return_value = SourceLookup('found', 'synthetic_fixture', (food,))
        recipe_scope = {'input_id': self.parsing['input_id'], 'ingredient_id': ingredient['ingredient_id'],
                        'recipe_text_sha256': self.parsing['source']['text_sha256']}
        request = {'food_id': food.food_id, 'expected_archive_sha256': 'a' * 64,
                   'expected_sample_sha256': self.sample_hash, 'evidence_reference': 'test://binding'}
        candidate = review_ingredient_sources(self.parsing, expected_source=deepcopy(self.parsing['source']),
                                              mock_mapper=MockNutritionMappingAdapter(), source_adapter=adapter,
                                              source_links={ingredient['ingredient_id']: {**recipe_scope, 'candidates': [request]}})
        source_scope = {'provider': 'tfda', 'dataset': '8543', 'food_id': food.food_id, 'original_name': food.original_name,
                        'data_type': food.data_type, 'archive_sha256': 'a' * 64, 'member_sha256': 'b' * 64,
                        'sample_sha256': self.sample_hash}
        self.attachments = {'parsing': self.parsing, 'expected': deepcopy(self.parsing['source']), 'candidate': candidate,
                            'provenance': encoded({'archive_sha256': 'a' * 64, 'member_sha256': 'b' * 64,
                                                   'source_url': 'test://synthetic', 'retrieved_at_utc': 'TEST_TIME',
                                                   'official_dataset_version': version}),
                            'recipe': text.encode(), 'sample': sample,
                            'document': encoded({'page': 1, 'archive_sha256': 'a' * 64,
                                                 'version': version, 'unit': unit,
                                                 'statement': 'SYNTHETIC reviewed basis/version/profile'}),
                            'assertion': encoded({'x/y': {'~z': 0}})}
        origins = {'recipe': 'recipe_text', 'sample': 'official_record', 'document': 'official_document'}
        self.catalog = {key: {'origin': origins.get(key, 'caller_assertion'),
                              'representation': 'bytes' if isinstance(raw, bytes) else 'native',
                              'sha256': sha256(raw).hexdigest() if isinstance(raw, bytes) else None,
                              'scope': {'recipe_scope': recipe_scope, 'candidate_scope': source_scope}}
                        for key, raw in self.attachments.items()}
        def ref(key):
            return {'attachment_id': key, 'representation': self.catalog[key]['representation'], 'sha256': self.catalog[key]['sha256']}
        self.input = {'contract': 'foodcal.ingredient_source_review', 'schema_version': 1, 'purpose': 'diagnostic/test_only',
                      'recipe_scope': recipe_scope, 'candidate_scope': source_scope,
                      'attachment_refs': {'parsing_snapshot': ref('parsing'), 'expected_recipe_source': ref('expected'),
                                          'candidate_lookup': ref('candidate'), 'source_provenance': ref('provenance'),
                                          'evidence_files': [ref(key) for key in ('recipe', 'sample', 'document', 'assertion')]},
                      'basis_target': {'purpose': 'recipe_declared_ingredient_reference',
                                       'required_nutrients': [{'original_name': '熱量', 'expected_unit': 'kcal'}]},
                      'claims': [], 'evidence': []}
        for key, locator, raw, role in (
            ('recipe', {'start': 0, 'end': len(text)}, text, 'food_identity'),
            ('sample', {'sample_filename': 'sample.json', 'source_row_index': 7,
                        'source_object_sha256': proof.source_object_sha256, 'field_name': '內容物描述'}, row['內容物描述'], 'food_identity'),
            ('document', {'page': 1, 'section': '/statement'}, 'SYNTHETIC reviewed basis/version/profile', 'measurement_basis'),
            ('assertion', {'pointer': '/x~1y/~0z'}, 0, 'caller_claim')):
            self.input['evidence'].append({'evidence_id': 'ev_' + key, 'origin': self.catalog[key]['origin'],
                                          'attachment_ref': key, 'attachment_sha256': self.catalog[key]['sha256'],
                                          'locator': locator, 'raw_value': raw, 'evidence_text': raw if isinstance(raw, str) else None,
                                          'statement_role': role})
        self.results = []
        attributes = [('identity', a, None) for a in IDENTITY_ATTRIBUTES]
        attributes += [('basis', a, '熱量' if a.startswith('nutrient_') else None) for a in BASIS_ATTRIBUTES]
        for index, (domain, attribute, nutrient) in enumerate(attributes):
            claims = []
            for side in ('recipe', 'source'):
                claim_id = f'claim_{index}_{side}'
                ev = 'ev_recipe' if side == 'recipe' else 'ev_sample' if domain == 'identity' else 'ev_document'
                self.input['claims'].append({'claim_id': claim_id, 'domain': domain, 'attribute': attribute,
                                             'nutrient_key': nutrient, 'side': side, 'value_state': 'known',
                                             'value_kind': 'text', 'value': 'SYNTHETIC manually compared', 'unit': None,
                                             'evidence_ids': [ev]})
                if attribute == 'recipe_declared_mass' and side == 'recipe':
                    self.input['claims'][-1].update(value_kind='decimal', value=None if missing_quantity else '200',
                                                    value_state='missing' if missing_quantity else 'known',
                                                    unit=None if missing_quantity else 'g')
                if attribute in ('data_version', 'nutrient_units') and side == 'source':
                    actual = version if attribute == 'data_version' else unit
                    ev_id = 'ev_' + attribute
                    self.input['evidence'].append({'evidence_id': ev_id, 'origin': 'official_document',
                                                  'attachment_ref': 'document', 'attachment_sha256': self.catalog['document']['sha256'],
                                                  'locator': {'page': 1, 'section': '/version' if attribute == 'data_version' else '/unit'},
                                                  'raw_value': actual, 'evidence_text': actual,
                                                  'statement_role': 'version' if attribute == 'data_version' else 'nutrient_definition'})
                    self.input['claims'][-1].update(value=actual, value_state='known' if actual is not None else 'unknown',
                                                    evidence_ids=[ev_id])
                claims.append(claim_id)
            self.results.append({'domain': domain, 'attribute': attribute, 'nutrient_key': nutrient,
                                 'status': 'not_applicable' if attribute == 'photo_consumed_mass' else 'verified',
                                 'reason_code': 'scoped_photo_exclusion' if attribute == 'photo_consumed_mass' else 'synthetic_manual_comparison',
                                 'recipe_claim_ref': claims[0], 'source_claim_ref': claims[1],
                                 'evidence_ids': ['ev_recipe', 'ev_sample' if domain == 'identity' else 'ev_document'],
                                 'rule_id': NA_RULE if attribute == 'photo_consumed_mass' else 'manual_evidence_comparison_v1',
                                 'rule_version': '1', 'reviewer_ref': 'test://reviewer', 'reviewed_at': '2026-10-12T00:00:00+08:00'})
            if attribute == 'recipe_declared_mass':
                self.results[-1].update(source_claim_ref=None, evidence_ids=['ev_recipe'])
            if attribute in ('data_version', 'nutrient_units'):
                self.results[-1]['evidence_ids'].append('ev_' + attribute)
        self.seal()

    def row(self, attribute):
        return next(r for r in self.results if r['attribute'] == attribute)

    def seal(self, pending=False):
        # Only fixture owner reseals independently reviewed records; attack tests do not.
        ledger = {'profile': 'foodcal.review_context.v0', 'authority_ref': 'test://fixture-owner',
                  'recipe_scope': self.input['recipe_scope'], 'candidate_scope': self.input['candidate_scope'],
                  'claims': self.input['claims'], 'evidence': self.input['evidence'], 'attachment_catalog': self.catalog,
                  'reviews': [{'result': row, 'review_status': 'pending' if pending else 'reviewed',
                               'method': 'controlled_rule' if row['status'] == 'not_applicable' else 'manual',
                               'process_ref': 'test://synthetic-review-process'} for row in self.results]}
        blob = encoded(ledger)
        self.context = ReviewContext(blob, self.attachments)
        self.anchor = ReviewTrustAnchor('test://fixture-owner', sha256(blob).hexdigest())

    def validate(self):
        return validate_review(self.input, self.results, context=self.context, trust_anchor=self.anchor)

    def aggregate(self):
        return aggregate_review(self.input, self.results, context=self.context, trust_anchor=self.anchor)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.f = SyntheticReview()

    def test_01_fully_reviewed_synthetic_still_not_eligible(self):
        self.assertTrue(self.f.validate().valid, self.f.validate())
        result = self.f.aggregate()
        self.assertEqual((result['identity_decision'], result['basis_decision']), ('compatible', 'compatible'))
        self.assertFalse(result['nutrition_eligible'])

    def unresolved(self, attribute, reason):
        self.f.row(attribute).update(status='unresolved', reason_code=reason)
        self.f.seal()
        self.assertTrue(self.f.validate().valid, self.f.validate())
        return self.f.aggregate()

    def test_02_part_ambiguity(self):
        self.assertEqual(self.unresolved('part', 'part_ambiguous')['identity_decision'], 'needs_review')

    def test_03_raw_cooked_mismatch_and_priority(self):
        self.f = SyntheticReview(recipe_state='熟')
        self.f.row('state').update(status='mismatch', reason_code='state_mismatch')
        self.f.row('part').update(status='unresolved', reason_code='part_ambiguous')
        self.f.seal()
        result = self.f.aggregate()
        self.assertEqual(result['identity_decision'], 'incompatible')
        self.assertIn('part_ambiguous', result['blocking_reasons'])

    def test_04_edible_basis_unknown(self):
        self.assertEqual(self.unresolved('edible_portion', 'edible_basis_unknown')['basis_decision'], 'needs_review')

    def test_05_version_unknown(self):
        self.f = SyntheticReview(version=None)
        self.assertFalse(self.f.validate().valid)  # Cannot declare the missing version verified.
        self.assertEqual(self.unresolved('data_version', 'official_version_unknown')['basis_decision'], 'needs_review')

    def test_06_hash_mismatch(self):
        self.f.attachments['sample'] += b' '
        self.assertEqual(self.f.validate().state, 'invalid')
        self.assertEqual(self.f.aggregate()['attribute_results'], [])

    def test_07_missing_and_zero_values_do_not_merge(self):
        self.f = SyntheticReview(missing_quantity=True)
        self.assertFalse(self.f.validate().valid)
        result = self.unresolved('recipe_declared_mass', 'recipe_mass_missing')
        self.assertEqual(result['basis_decision'], 'needs_review')
        claim = self.f.input['claims'][0]
        claim.update(value_kind='decimal', value='0')
        self.f.seal()
        self.assertTrue(self.f.validate().valid)
        claim['value'] = None
        self.f.seal()
        self.assertFalse(self.f.validate().valid)

    def test_08_preparation_roles(self):
        self.f.input['evidence'][1]['statement_role'] = 'sample_preparation'
        self.f.seal()
        self.assertEqual(self.f.validate().issues[0].code, 'processing_role_unresolved')
        self.f.row('processing').update(status='unresolved', reason_code='processing_role_unresolved')
        self.f.seal()
        self.assertTrue(self.f.validate().valid)

    def test_09_illegal_na_and_fake_approval(self):
        self.f.row('part')['status'] = 'not_applicable'
        self.assertFalse(self.f.validate().valid)
        self.f = SyntheticReview()
        self.f.input['approved'] = True
        self.assertFalse(self.f.validate().valid)

    def test_10_unit_and_definition_unknown(self):
        self.f = SyntheticReview(unit=None)
        self.assertFalse(self.f.validate().valid)
        result = self.unresolved('nutrient_units', 'nutrient_unit_missing')
        self.assertEqual(result['basis_decision'], 'needs_review')
        self.unresolved('nutrient_definition', 'nutrient_definition_unresolved')

    def test_context_required_not_just_verified_strings(self):
        report = validate_review(self.f.input, self.f.results)
        self.assertEqual(report.state, 'blocked')
        self.assertEqual(report.issues[0].code, 'review_context_untrusted')
        self.assertFalse(aggregate_review(self.f.input, self.f.results)['nutrition_eligible'])

    def test_context_integrity_and_pending_review(self):
        self.f.context = ReviewContext(self.f.context.record_bytes + b' ', self.f.attachments)
        self.assertEqual(self.f.validate().issues[0].code, 'context_hash_mismatch')
        self.f.seal(pending=True)
        self.assertEqual(self.f.validate().state, 'blocked')

    def test_unwitnessed_attribute_result_is_rejected(self):
        self.f.row('part')['status'] = 'unresolved'
        self.assertEqual(self.f.validate().issues[0].code, 'forged_review_result')

    def test_scope_and_missing_reference(self):
        self.f.input['recipe_scope']['ingredient_id'] = 'ing_999_1000'
        self.assertFalse(self.f.validate().valid)
        self.f = SyntheticReview()
        del self.f.attachments['sample']
        self.assertEqual(self.f.validate().issues[0].code, 'missing_attachment')

    def test_pointer_escape_and_missing_path(self):
        self.assertTrue(self.f.validate().valid)  # /x~1y/~0z resolves literal keys x/y and ~z.
        self.f.input['evidence'][3]['locator']['pointer'] = '/absent'
        self.f.seal()
        self.assertEqual(self.f.validate().issues[0].code, 'unresolved_json_pointer')

    def test_pdf_is_blocked_even_with_hash_pin(self):
        self.f.attachments['document'] = b'%PDF-1.7 synthetic unsupported'
        digest = sha256(self.f.attachments['document']).hexdigest()
        self.f.catalog['document']['sha256'] = digest
        for ref in self.f.input['attachment_refs']['evidence_files']:
            if ref['attachment_id'] == 'document':
                ref['sha256'] = digest
        self.f.input['evidence'][2]['attachment_sha256'] = digest
        self.f.seal()
        self.assertEqual(self.f.validate().state, 'blocked')
        self.assertEqual(self.f.validate().issues[0].code, 'unsupported_evidence_format')

    def test_duplicates_missing_attributes_unknown_state(self):
        for change in ('duplicate', 'missing', 'unknown'):
            self.f = SyntheticReview()
            if change == 'duplicate':
                self.f.results.append(deepcopy(self.f.results[0]))
            elif change == 'missing':
                self.f.results.pop()
            else:
                self.f.results[0]['status'] = 'approved'
            self.f.seal()
            self.assertFalse(self.f.validate().valid)

    def test_inputs_not_mutated(self):
        before = deepcopy((self.f.input, self.f.results, self.f.attachments))
        decision = self.f.aggregate()
        self.assertEqual(before, (self.f.input, self.f.results, self.f.attachments))
        decision['attribute_results'][0]['status'] = 'mismatch'
        self.assertEqual(before[1], self.f.results)

    def test_cross_ingredient_span_is_rejected(self):
        # Independent context cannot make an out-of-ingredient span a legitimate link.
        self.f.parsing['ingredients'][0]['evidence_links'] = []
        self.assertFalse(self.f.validate().valid)

    def test_bad_original_object_hash_and_global_row(self):
        for field, value in (('source_object_sha256', '0' * 64), ('source_row_index', 8)):
            self.f = SyntheticReview()
            self.f.input['evidence'][1]['locator'][field] = value
            self.f.seal()
            self.assertFalse(self.f.validate().valid)

    def test_bool_and_zero_are_not_equal_evidence(self):
        self.f.input['evidence'][3]['raw_value'] = False
        self.f.seal()
        self.assertEqual(self.f.validate().issues[0].code, 'evidence_value_mismatch')

    def test_pointer_invalid_escape_and_claim_reference(self):
        self.f.input['evidence'][3]['locator']['pointer'] = '/x~2y'
        self.f.seal()
        self.assertEqual(self.f.validate().issues[0].code, 'invalid_json_pointer')
        self.f = SyntheticReview()
        self.f.results[0]['recipe_claim_ref'] = 'missing'
        self.f.seal()
        self.assertEqual(self.f.validate().issues[0].code, 'cross_attribute_claim')

    def test_low_trust_evidence_cannot_verify(self):
        self.f.results[0]['evidence_ids'] = ['ev_assertion']
        for claim in self.f.input['claims'][:2]:
            claim['evidence_ids'] = ['ev_assertion']
        self.f.seal()
        self.assertEqual(self.f.validate().issues[0].code, 'caller_claim_only')

    def test_changed_parsed_nutrient_is_rejected(self):
        candidate = self.f.attachments['candidate']['ingredients'][0]['candidates'][0]
        lookup = candidate['source_lookup']
        food = lookup.records[0]
        obs = food.observations[0]
        replacement = replace(obs, value=replace(obs.value, parsed_value=Decimal('999')))
        candidate['source_lookup'] = replace(lookup, records=(replace(food, observations=(replacement,)),))
        self.assertEqual(self.f.validate().issues[0].code, 'source_value_mismatch')

    def test_missing_context_does_not_allow_aggregator_flag_bypass(self):
        result = aggregate_review(self.f.input, self.f.results, context={'approved': True}, trust_anchor=None)
        self.assertEqual(result['input_integrity'], 'invalid')
        self.assertEqual(result['attribute_results'], [])
        self.assertIn('review_context_untrusted', result['blocking_reasons'])

    def test_candidate_label_cannot_override_request_hash(self):
        candidate = self.f.attachments['candidate']['ingredients'][0]['candidates'][0]
        candidate['request']['expected_archive_sha256'] = '0' * 64
        self.assertEqual(self.f.validate().issues[0].code, 'candidate_request_hash_mismatch')

    def test_mass_claim_cannot_invent_zero(self):
        claim_id = self.f.row('recipe_declared_mass')['recipe_claim_ref']
        next(c for c in self.f.input['claims'] if c['claim_id'] == claim_id)['value'] = '0'
        self.f.seal()
        self.assertEqual(self.f.validate().issues[0].code, 'recipe_mass_claim_mismatch')


if __name__ == '__main__':
    unittest.main()
