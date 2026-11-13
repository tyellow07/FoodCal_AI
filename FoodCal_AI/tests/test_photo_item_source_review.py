"""Synthetic/test-only photo item source review: never official approval, photo truth or a real nutrient.

Every image hash, region, mass, nutrient value, reviewer and gate record below is invented for contract
tests; none of it is a measurement or nutrition fact for the named food. Recipe candidates may explain
hidden calories, but are not visual proof, so no recipe artifact appears in a valid fixture.
"""
from copy import deepcopy
from hashlib import sha256
import json
import unittest

from src.nutrition.photo_review import (BASIS_ATTRIBUTES, IDENTITY_ATTRIBUTES, MANUAL_RULE, NA_RULE,
                                        aggregate_photo_review, build_photo_nutrition_basis, validate_photo_review)
from src.nutrition.review_types import ReviewContext, ReviewTrustAnchor
from src.nutrition.review_validation import BASIS_ATTRIBUTES as V1_BASIS, IDENTITY_ATTRIBUTES as V1_IDENTITY
from src.nutrition.tfda_source import FIELDS, FoodRecord, NutrientObservation, SourceEvidence, SourceLookup, parse_value
from src.portion import canonical_sha256, estimate_photo_energy, evidence_identity, validate_energy_basis
from src.visual_ingredients import VisualIngredientEvidenceAdapter

REVIEWED_AT = '2026-11-12T00:00:00+08:00'
CONSUMED = {'status': 'whole_visible_portion_asserted', 'asserted_by': 'synthetic-user',
            'evidence_ref': 'test://synthetic-consumption'}
JUDGED = {'food': '白飯', 'part': '整粒', 'state': '熟', 'processing': '蒸煮', 'quality_composition': '無配料',
          'edible_fraction_visible': '全部可食'}
GATE_NAMES = ('source_use', 'identity', 'basis', 'integrity')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')


def region(x_min, y_min, x_max, y_max):
    return {'format': 'xyxy', 'coordinate_space': 'original_image_pixels',
            'x_min': x_min, 'y_min': y_min, 'x_max': x_max, 'y_max': y_max}


class SyntheticPhotoReview:
    """Independent fixture owner pins the ledger; attack tests change data after sealing or reseal on purpose."""

    def __init__(self, *, visual=None, estimate_changes=None, judgments=None, judgment_region=None,
                 visual_reviewer='test://visual-reviewer', sample_state='熟', unit='kcal', expected_unit='kcal',
                 version='synthetic-v1', energy_raw=' 100.0'):
        self.image_sha256 = sha256(b'SYNTHETIC photo bytes; not a real image').hexdigest()
        self.item = {'ingredient_candidate': '白飯',
                     'evidence_source': {'kind': 'synthetic_annotation', 'image_id': 'synthetic-photo-review-v0',
                                         'annotation_id': 'annotation-photo-1', 'image_sha256': self.image_sha256,
                                         'annotator': 'test://annotator', 'usage': 'synthetic/test_only'},
                     'evidence_type': 'ingredient_annotation', 'confidence': None,
                     'image_region': region(100, 80, 300, 240), 'status': 'observed_candidate',
                     'reason': 'synthetic_annotation_not_real_visual_observation'}
        if visual:
            visual(self.item)
        identity = evidence_identity(self.item)
        identity['evidence_id'] = identity['evidence_id'] or 'unbound-0'  # inadmissible items have no evidence ID
        self.estimate = {'contract': 'foodcal.photo_portion_estimate', 'schema_version': 0, 'estimate_id': 'est-photo-1',
                         'evidence_ref': dict(identity), 'mass_kind': 'visible_mass_at_capture',
                         'mass_g': {'low': '148', 'high': '152', 'coverage': '0.95'},
                         'method': {'kind': 'direct_weighing', 'method_id': 'synthetic-scale', 'method_version': '1'},
                         'measurement': {'instrument_resolution_g': '1', 'evidence_ref': 'test://synthetic-weighing/photo-1',
                                         'measured_by': 'test://measurer'},
                         'calibration_ref': None, 'producer': {'kind': 'human', 'ref': 'test://measurer'},
                         'review': {'status': 'accepted', 'reviewer_ref': 'test://portion-reviewer', 'reviewed_at': REVIEWED_AT},
                         'usage': 'diagnostic/test_only'}
        self.estimate.update(estimate_changes or {})
        self.photo_scope = {'image_sha256': self.image_sha256, 'evidence_type': identity['evidence_type'],
                            'evidence_id': identity['evidence_id'], 'ingredient_candidate': identity['ingredient_candidate'],
                            'evidence_sha256': identity['evidence_sha256'], 'estimate_id': self.estimate['estimate_id'],
                            'estimate_sha256': canonical_sha256(self.estimate)}

        row = dict.fromkeys(FIELDS)
        row.update({'整合編號': 'SYNTHETIC1', '樣品名稱': '合成白飯', '資料類別': 'synthetic/test_only',
                    '內容物描述': f'SYNTHETIC:{sample_state}、整粒、蒸煮、測試規格', '分析項': '熱量',
                    '含量單位': unit, '每100克含量': energy_raw})
        object_text = json.dumps(row, ensure_ascii=False)
        sample = ('[' + object_text + ']').encode('utf-8')
        sample_hash = sha256(sample).hexdigest()
        proof = SourceEvidence('test://synthetic', 'TEST_TIME', 'a' * 64, 'b' * 64, 'sample.json', sample_hash, 7,
                               sha256(object_text.encode()).hexdigest(), 0)
        observation = NutrientObservation('熱量', None, parse_value(energy_raw), unit, proof, tuple(row.items()))
        food = FoodRecord('SYNTHETIC1', row['樣品名稱'], row['資料類別'], None, row['內容物描述'], (observation,), ())
        source_scope = {'provider': 'tfda', 'dataset': '8543', 'food_id': food.food_id, 'original_name': food.original_name,
                        'data_type': food.data_type, 'archive_sha256': 'a' * 64, 'member_sha256': 'b' * 64,
                        'sample_sha256': sample_hash}

        judged = {**JUDGED, **(judgments or {})}  # None means the reviewer could not see it
        inner = judgment_region or region(120, 100, 280, 220)
        self.visual_review = {
            'profile': 'foodcal.photo_visual_review.v0', 'photo_scope': deepcopy(self.photo_scope),
            'reviewer_ref': visual_reviewer, 'reviewed_at': REVIEWED_AT, 'image_access': 'reviewed_original_image',
            'judgments': {name: {'value_state': 'known', 'value': value, 'region': deepcopy(inner)} if value is not None
                          else {'value_state': 'unknown', 'value': None, 'region': None} for name, value in judged.items()},
            'usage': 'diagnostic/test_only'}
        self.attachments = {
            'visual': encoded(self.item), 'estimate': encoded(self.estimate),
            'lookup': SourceLookup('found', 'synthetic_fixture', (food,)),
            'provenance': encoded({'archive_sha256': 'a' * 64, 'member_sha256': 'b' * 64, 'source_url': 'test://synthetic',
                                   'retrieved_at_utc': 'TEST_TIME', 'official_dataset_version': version}),
            'sample': sample,
            'document': encoded({'page': 1, 'archive_sha256': 'a' * 64, 'version': version, 'unit': unit,
                                 'sample_state': sample_state, 'statement': 'SYNTHETIC reviewed basis/profile'}),
            'visual_review': encoded(self.visual_review),
            'assertion': encoded({'claim': 'SYNTHETIC caller statement'})}
        origins = {'visual': 'photo_evidence_record', 'estimate': 'photo_portion_record', 'lookup': 'source_lookup',
                   'provenance': 'source_provenance', 'sample': 'official_record', 'document': 'official_document',
                   'visual_review': 'photo_visual_review', 'assertion': 'caller_assertion'}
        self.catalog = {}
        for key, raw in self.attachments.items():
            self.catalog[key] = {'origin': origins[key], 'representation': 'bytes' if isinstance(raw, bytes) else 'native',
                                 'sha256': sha256(raw).hexdigest() if isinstance(raw, bytes) else None,
                                 'scope': {'photo_scope': self.photo_scope, 'candidate_scope': source_scope}}
        self.input = {'contract': 'foodcal.photo_item_source_review', 'schema_version': 0, 'purpose': 'diagnostic/test_only',
                      'photo_scope': self.photo_scope, 'candidate_scope': source_scope,
                      'source_link': {'food_id': food.food_id, 'expected_archive_sha256': 'a' * 64,
                                      'expected_sample_sha256': sample_hash, 'link_ref': 'test://synthetic-source-link'},
                      'attachment_refs': {'visual_evidence': self.ref('visual'), 'portion_estimate': self.ref('estimate'),
                                          'source_lookup': self.ref('lookup'), 'source_provenance': self.ref('provenance'),
                                          'evidence_files': [self.ref(k) for k in ('sample', 'document', 'visual_review', 'assertion')]},
                      'basis_target': {'purpose': 'photo_consumed_mass_calculation',
                                       'required_nutrients': [{'original_name': '熱量', 'expected_unit': expected_unit}]},
                      'claims': [], 'evidence': []}

        def evidence(evidence_id, key, locator, raw, role):
            self.input['evidence'].append({'evidence_id': evidence_id, 'origin': self.catalog[key]['origin'],
                                           'attachment_ref': key, 'attachment_sha256': self.catalog[key]['sha256'],
                                           'locator': locator, 'raw_value': raw,
                                           'evidence_text': raw if isinstance(raw, str) else None, 'statement_role': role})

        evidence('ev_candidate', 'visual', {'pointer': '/ingredient_candidate'}, self.item['ingredient_candidate'], 'photo_observation')
        evidence('ev_mass_kind', 'estimate', {'pointer': '/mass_kind'}, 'visible_mass_at_capture', 'photo_mass')
        for name, judgment in self.visual_review['judgments'].items():
            known = judgment['value_state'] == 'known'
            evidence('ev_judgment_' + name, 'visual_review', {'pointer': f'/judgments/{name}/' + ('value' if known else 'value_state')},
                     judgment['value'] if known else judgment['value_state'], 'photo_observation')
        evidence('ev_sample', 'sample', {'sample_filename': 'sample.json', 'source_row_index': 7,
                                         'source_object_sha256': proof.source_object_sha256, 'field_name': '內容物描述'},
                 row['內容物描述'], 'food_identity')
        evidence('ev_document', 'document', {'page': 1, 'section': '/statement'}, 'SYNTHETIC reviewed basis/profile', 'measurement_basis')
        evidence('ev_sample_state', 'document', {'page': 1, 'section': '/sample_state'}, sample_state, 'measurement_basis')
        evidence('ev_version', 'document', {'page': 1, 'section': '/version'}, version, 'version')
        evidence('ev_unit', 'document', {'page': 1, 'section': '/unit'}, unit, 'nutrient_definition')
        evidence('ev_assertion', 'assertion', {'pointer': '/claim'}, 'SYNTHETIC caller statement', 'caller_claim')

        self.results = []

        def claim(key, side, value, evidence_ids, kind='text', unit_=None):
            claim_id = f'claim_{key[1]}_{side}'
            self.input['claims'].append({'claim_id': claim_id, 'domain': key[0], 'attribute': key[1], 'nutrient_key': key[2],
                                         'side': side, 'value_state': 'known' if value is not None else 'unknown',
                                         'value_kind': kind, 'value': value, 'unit': unit_, 'evidence_ids': list(evidence_ids)})
            return claim_id

        def result(key, photo, source, evidence_ids, known=True, reason=None):
            self.results.append({'domain': key[0], 'attribute': key[1], 'nutrient_key': key[2],
                                 'status': 'verified' if known else 'unresolved',
                                 'reason_code': 'synthetic_manual_comparison' if known else reason,
                                 'photo_claim_ref': photo, 'source_claim_ref': source, 'evidence_ids': list(evidence_ids),
                                 'rule_id': MANUAL_RULE, 'rule_version': '1', 'reviewer_ref': 'test://nutrition-reviewer',
                                 'reviewed_at': REVIEWED_AT})

        def judged_claim(key, name):
            return claim(key, 'photo', self.visual_review['judgments'][name]['value'], ['ev_judgment_' + name])

        for attribute in IDENTITY_ATTRIBUTES:
            key = ('identity', attribute, None)
            photo = judged_claim(key, attribute)
            source = claim(key, 'source', 'SYNTHETIC manually compared', ['ev_sample'])
            result(key, photo, source, ['ev_judgment_' + attribute, 'ev_sample'],
                   judged[attribute] is not None, f'{attribute}_unknown')
        key = ('basis', 'reference_measure', None)
        result(key, None, claim(key, 'source', '每100克含量', ['ev_document']), ['ev_document'])
        key = ('basis', 'edible_portion', None)
        result(key, judged_claim(key, 'edible_fraction_visible'), claim(key, 'source', 'SYNTHETIC edible portion', ['ev_document']),
               ['ev_judgment_edible_fraction_visible', 'ev_document'], judged['edible_fraction_visible'] is not None,
               'edible_portion_unknown')
        key = ('basis', 'weight_basis', None)
        result(key, judged_claim(key, 'state'), claim(key, 'source', sample_state, ['ev_sample_state']),
               ['ev_judgment_state', 'ev_sample_state'], judged['state'] is not None, 'state_unknown')
        self.results.append({'domain': 'basis', 'attribute': 'recipe_declared_mass', 'nutrient_key': None,
                             'status': 'not_applicable', 'reason_code': 'photo_item_excludes_recipe_mass',
                             'photo_claim_ref': None, 'source_claim_ref': None, 'evidence_ids': ['ev_mass_kind'],
                             'rule_id': NA_RULE, 'rule_version': '1', 'reviewer_ref': 'test://nutrition-reviewer',
                             'reviewed_at': REVIEWED_AT})
        key = ('basis', 'photo_consumed_mass', None)
        result(key, claim(key, 'photo', deepcopy(self.estimate['mass_g']), ['ev_mass_kind'], 'decimal_interval', 'g'),
               None, ['ev_mass_kind'])
        key = ('basis', 'data_version', None)
        result(key, None, claim(key, 'source', version, ['ev_version']), ['ev_version'], version is not None,
               'official_version_unknown')
        key = ('basis', 'nutrient_units', '熱量')
        result(key, None, claim(key, 'source', unit, ['ev_unit']), ['ev_unit'], unit == expected_unit,
               'nutrient_unit_conflict' if unit else 'nutrient_unit_missing')
        key = ('basis', 'nutrient_definition', '熱量')
        result(key, None, claim(key, 'source', 'SYNTHETIC energy definition', ['ev_document']), ['ev_document'])
        self.gates = [self.gate('source_use'),
                      self.gate('integrity') if version is not None else self.gate('integrity', 'pending', 'version_unknown')]
        self.seal()

    def ref(self, key):
        return {'attachment_id': key, 'representation': self.catalog[key]['representation'], 'sha256': self.catalog[key]['sha256']}

    def gate(self, name, status='passed', reason='synthetic_gate_review'):
        decided = status != 'pending'
        return {'profile': 'foodcal.source_gate_record.v0', 'gate': name, 'purpose': 'photo_calculation',
                'candidate_scope': deepcopy(self.input['candidate_scope']), 'status': status, 'reason_code': reason,
                'evidence_locator': f'test://synthetic-gate/{name}' if decided else None,
                'reviewer_ref': 'test://source-reviewer' if decided else None,
                'reviewed_at': REVIEWED_AT if decided else None, 'policy_revision': 1 if decided else None}

    def attach(self, key, raw, origin):
        self.attachments[key] = raw
        self.catalog[key] = {'origin': origin, 'representation': 'bytes', 'sha256': sha256(raw).hexdigest(),
                             'scope': {'photo_scope': self.photo_scope, 'candidate_scope': self.input['candidate_scope']}}
        self.input['attachment_refs']['evidence_files'].append(self.ref(key))

    def rewrite(self, key, value):
        """Owner replaces one bytes attachment and re-pins every reference to it (caller reseals)."""
        raw = encoded(value)
        digest = sha256(raw).hexdigest()
        self.attachments[key] = raw
        self.catalog[key]['sha256'] = digest
        refs = self.input['attachment_refs']
        for ref in [refs[name] for name in refs if name != 'evidence_files'] + refs['evidence_files']:
            if ref['attachment_id'] == key:
                ref['sha256'] = digest
        for ev in self.input['evidence']:
            if ev['attachment_ref'] == key:
                ev['attachment_sha256'] = digest

    def row(self, attribute):
        return next(r for r in self.results if r['attribute'] == attribute)

    def claim(self, claim_id):
        return next(c for c in self.input['claims'] if c['claim_id'] == claim_id)

    def evidence(self, evidence_id):
        return next(e for e in self.input['evidence'] if e['evidence_id'] == evidence_id)

    def seal(self, pending=False, ledger_changes=None):
        # Only the fixture owner reseals independently reviewed records.
        ledger = {'profile': 'foodcal.photo_review_context.v0', 'authority_ref': 'test://fixture-owner', 'policy_revision': 1,
                  'photo_scope': self.input['photo_scope'], 'candidate_scope': self.input['candidate_scope'],
                  'source_link': self.input['source_link'], 'claims': self.input['claims'], 'evidence': self.input['evidence'],
                  'attachment_catalog': self.catalog,
                  'reviews': [{'result': row, 'review_status': 'pending' if pending else 'reviewed',
                               'method': 'controlled_rule' if row['status'] == 'not_applicable' else 'manual',
                               'process_ref': 'test://synthetic-photo-review-process'} for row in self.results],
                  'gate_records': self.gates}
        ledger.update(ledger_changes or {})
        blob = encoded(ledger)
        self.context = ReviewContext(blob, self.attachments)
        self.anchor = ReviewTrustAnchor('test://fixture-owner', sha256(blob).hexdigest())

    def validate(self):
        return validate_photo_review(self.input, self.results, self.gates, context=self.context, trust_anchor=self.anchor)

    def aggregate(self):
        return aggregate_photo_review(self.input, self.results, self.gates, context=self.context, trust_anchor=self.anchor)

    def build(self, energy_nutrient='熱量'):
        return build_photo_nutrition_basis(self.input, self.results, self.gates, context=self.context,
                                           trust_anchor=self.anchor, energy_nutrient=energy_nutrient)

    def envelope(self, hidden=True):
        priors = VisualIngredientEvidenceAdapter().fixture_demo()['recipe_assisted_hidden_ingredients'] if hidden else []
        return {'visual_ingredient_evidence': [deepcopy(self.item)], 'recipe_assisted_hidden_ingredients': deepcopy(priors)}

    def energy(self, basis, hidden=False):
        return estimate_photo_energy(self.envelope(hidden), estimates=[deepcopy(self.estimate)], nutrition_bases=[basis],
                                     consumption=CONSUMED)


class PhotoReviewCase(unittest.TestCase):
    def setUp(self):
        self.f = SyntheticPhotoReview()

    def assertRejected(self, code, fixture=None, state='invalid'):
        report = (fixture or self.f).validate()
        self.assertFalse(report.valid)
        self.assertEqual((report.state, report.issues[0].code), (state, code), report)
        build = (fixture or self.f).build()
        self.assertEqual((build['status'], build['basis']), ('unavailable', None))
        self.assertIn(code, build['reasons'])

    def gate_statuses(self, build):
        return {name: gate['status'] for name, gate in build['basis']['gates'].items()}

    def downstream(self, build, fixture=None):
        return (fixture or self.f).energy(build['basis'])['items'][0]


class AcceptanceTests(PhotoReviewCase):
    """docs/photo_item_source_review_design_v0.md §9, one test per row."""

    def test_01_fully_reviewed_synthetic_basis_computes_but_stays_ineligible(self):
        self.assertTrue(self.f.validate().valid, self.f.validate())
        build = self.f.build()
        self.assertEqual((build['status'], build['reasons']), ('built', []))
        basis = build['basis']
        self.assertEqual(validate_energy_basis(basis), [])
        self.assertEqual(self.gate_statuses(build), dict.fromkeys(GATE_NAMES, 'passed'))
        self.assertEqual(basis['energy'], {'original_name': '熱量', 'unit': 'kcal', 'per': 'per_100g_edible_portion',
                                           'value': '100.0', 'interval': None})
        self.assertEqual((basis['estimate_id'], basis['ingredient_candidate'], basis['purpose'], basis['usage']),
                         ('est-photo-1', '白飯', 'photo_calculation', 'diagnostic/test_only'))
        self.assertEqual((build['nutrition_eligible'], build['production_eligible']), (False, False))
        self.assertEqual((build['decision']['identity_decision'], build['decision']['basis_decision']), ('compatible', 'compatible'))
        energy = self.f.energy(basis)
        self.assertEqual((energy['status'], energy['unit'], energy['energy']), ('computed', 'kcal', {'low': '148', 'high': '152'}))
        self.assertEqual((energy['coverage_lower_bound'], energy['coverage_note']), (None, 'composition_uncertainty_not_quantified'))
        self.assertEqual((energy['nutrition_eligible'], energy['production_eligible']), (False, False))
        self.assertEqual(self.f.build()['basis'], basis)  # deterministic, rebuilt from the review every call

    def test_02_recipe_attachments_evidence_and_claims_are_rejected(self):
        self.f.input['attachment_refs']['parsing_snapshot'] = self.f.ref('assertion')
        self.assertRejected('recipe_evidence_not_allowed')
        f = SyntheticPhotoReview()
        f.attach('recipe', 'SYNTHETIC 白飯 200g'.encode(), 'recipe_text')
        f.seal()
        self.assertRejected('recipe_evidence_not_allowed', f)
        f = SyntheticPhotoReview()
        f.input['evidence'][-1]['origin'] = 'recipe_text'
        f.seal()
        self.assertRejected('recipe_evidence_not_allowed', f)
        f = SyntheticPhotoReview()
        f.claim('claim_food_photo')['side'] = 'recipe'
        f.seal()
        self.assertRejected('recipe_evidence_not_allowed', f)
        f = SyntheticPhotoReview()
        f.row('food')['recipe_claim_ref'] = None
        self.assertRejected('recipe_evidence_not_allowed', f)

    def test_03_uncertain_or_dish_level_visual_evidence_is_not_reviewable(self):
        for mutate in (lambda item: item.update(status='uncertain'),
                       lambda item: item.update(evidence_type='dish_detection_only'),
                       lambda item: item.update(image_region=None)):
            self.assertRejected('photo_scope_not_reviewable', SyntheticPhotoReview(visual=mutate))

    def test_04_portion_estimate_must_bind_this_evidence_and_hash(self):
        other = dict(evidence_identity(self.f.item), evidence_id='annotation-other')
        self.assertRejected('portion_evidence_unbound', SyntheticPhotoReview(estimate_changes={'evidence_ref': other}))
        self.f.input['photo_scope']['estimate_sha256'] = '0' * 64
        self.f.seal()
        self.assertRejected('portion_estimate_hash_mismatch')
        f = SyntheticPhotoReview()
        f.attachments['estimate'] = f.attachments['estimate'].replace(b'"148"', b'"140"')
        self.assertRejected('evidence_hash_mismatch', f)

    def test_05_state_not_visible_keeps_identity_and_basis_pending(self):
        f = SyntheticPhotoReview(judgments={'state': None})
        self.assertTrue(f.validate().valid, f.validate())
        decision = f.aggregate()
        self.assertEqual((decision['identity_decision'], decision['basis_decision']), ('needs_review', 'needs_review'))
        self.assertIn('state_unknown', decision['blocking_reasons'])
        build = f.build()
        self.assertEqual(build['status'], 'built')
        self.assertEqual(self.gate_statuses(build), {'source_use': 'passed', 'identity': 'pending', 'basis': 'pending',
                                                     'integrity': 'passed'})
        item = self.downstream(build, f)
        self.assertEqual((item['status'], item['reason']), ('unavailable', 'nutrition_basis_unresolved'))
        self.assertEqual(item['blocking_reasons'], ['gate_identity_pending', 'gate_basis_pending'])
        f.row('state').update(status='verified', reason_code='synthetic_manual_comparison')
        f.seal()
        self.assertRejected('unresolved_claim_cannot_verify', f)

    def test_06_raw_sample_basis_against_cooked_photo_is_a_mismatch(self):
        f = SyntheticPhotoReview(sample_state='生')
        f.row('weight_basis').update(status='mismatch', reason_code='raw_cooked_conflict')
        f.row('state').update(status='mismatch', reason_code='state_mismatch')
        f.seal()
        self.assertTrue(f.validate().valid, f.validate())
        build = f.build()
        self.assertEqual(build['decision']['basis_decision'], 'incompatible')
        self.assertEqual((self.gate_statuses(build)['identity'], self.gate_statuses(build)['basis']), ('failed', 'failed'))
        self.assertEqual(build['basis']['energy']['value'], '100.0')  # no raw-to-cooked conversion exists in v0
        self.assertEqual(self.downstream(build, f)['blocking_reasons'], ['gate_identity_failed', 'gate_basis_failed'])

    def test_07_inedible_parts_in_visible_mass_are_never_converted(self):
        for status, gate in (('unresolved', 'pending'), ('mismatch', 'failed')):
            f = SyntheticPhotoReview(judgments={'edible_fraction_visible': '含骨，可食比例未知'})
            f.row('edible_portion').update(status=status, reason_code='visible_mass_includes_bone')
            f.seal()
            build = f.build()
            self.assertEqual((build['status'], self.gate_statuses(build)['basis']), ('built', gate), status)
            self.assertEqual((build['basis']['energy']['value'], build['basis']['energy']['interval']), ('100.0', None))
            self.assertEqual(self.downstream(build, f)['status'], 'unavailable')

    def test_08_photo_consumed_mass_cannot_be_not_applicable(self):
        self.f.row('photo_consumed_mass').update(status='not_applicable', rule_id=NA_RULE)
        self.f.seal()
        self.assertRejected('invalid_not_applicable')

    def test_09_recipe_declared_mass_accepts_only_the_rule_exclusion(self):
        for status in ('verified', 'unresolved'):
            f = SyntheticPhotoReview()
            f.row('recipe_declared_mass').update(status=status, rule_id=MANUAL_RULE, reason_code='manual_attempt')
            f.seal()
            self.assertRejected('invalid_recipe_mass_review', f)
        f = SyntheticPhotoReview()
        f.row('recipe_declared_mass')['evidence_ids'] = ['ev_document']
        f.seal()
        self.assertRejected('na_evidence_required', f)

    def test_10_pending_or_missing_source_use_gate_blocks_downstream(self):
        self.f.gates[0] = self.f.gate('source_use', 'pending', 'license_scope_unconfirmed')
        self.f.seal()
        build = self.f.build()
        self.assertEqual((build['status'], build['reasons']), ('built', ['gate_source_use_pending']))
        self.assertEqual(build['basis']['gates']['source_use'], {'status': 'pending', 'evidence_ref': None})
        self.assertEqual(self.downstream(build)['blocking_reasons'], ['gate_source_use_pending'])
        self.f.gates = [self.f.gate('integrity')]
        self.f.seal()
        self.assertEqual(self.f.build()['reasons'], ['gate_source_use_pending'])

    def test_11_unknown_official_version_cannot_pass_integrity(self):
        f = SyntheticPhotoReview(version=None)
        build = f.build()
        self.assertEqual(self.gate_statuses(build), {'source_use': 'passed', 'identity': 'passed', 'basis': 'pending',
                                                     'integrity': 'pending'})
        self.assertIn('official_version_unknown', build['decision']['blocking_reasons'])
        f.gates[1] = f.gate('integrity')
        f.seal()
        self.assertRejected('integrity_version_unknown', f)
        f = SyntheticPhotoReview()
        f.row('data_version').update(status='unresolved', reason_code='version_not_compared')
        f.seal()
        self.assertRejected('integrity_requires_verified_version', f)

    def test_12_kilojoule_and_corrected_energy_are_never_converted_or_merged(self):
        f = SyntheticPhotoReview(unit='kJ')  # target asks for kcal
        build = f.build()
        self.assertEqual((self.gate_statuses(build)['basis'], build['basis']['energy']['unit'],
                          build['basis']['energy']['value']), ('pending', 'kJ', '100.0'))
        f.row('nutrient_units').update(status='verified', reason_code='synthetic_manual_comparison')
        f.seal()
        self.assertRejected('nutrient_unit_conflict', f)
        f.row('nutrient_units').update(status='mismatch', reason_code='nutrient_unit_conflict')
        f.seal()
        self.assertEqual(self.gate_statuses(f.build())['basis'], 'failed')
        f = SyntheticPhotoReview(unit='kJ', expected_unit='kJ')
        energy = f.energy(f.build()['basis'])
        self.assertEqual((energy['status'], energy['unit'], energy['energy']), ('computed', 'kJ', {'low': '148', 'high': '152'}))
        build = self.f.build(energy_nutrient='修正熱量')
        self.assertEqual((build['status'], build['reasons']), ('unavailable', ['energy_nutrient_not_reviewed']))
        self.f.input['basis_target']['required_nutrients'].append({'original_name': '熱量', 'expected_unit': 'kJ'})
        self.assertRejected('duplicate_nutrient')

    def test_13_request_cannot_supply_its_own_trust(self):
        report = validate_photo_review(self.f.input, self.f.results, self.f.gates)
        self.assertEqual((report.state, report.issues[0].code), ('blocked', 'review_context_untrusted'))
        build = build_photo_nutrition_basis(self.f.input, self.f.results, self.f.gates, context={'approved': True},
                                            trust_anchor=None, energy_nutrient='熱量')
        self.assertEqual((build['status'], build['reasons'], build['basis']), ('unavailable', ['review_context_untrusted'], None))
        forged = ReviewTrustAnchor('test://attacker', self.f.anchor.context_sha256)
        report = validate_photo_review(self.f.input, self.f.results, self.f.gates, context=self.f.context, trust_anchor=forged)
        self.assertEqual(report.issues[0].code, 'context_authority_mismatch')
        self.f.input['trust_anchor'] = {'authority_ref': 'test://fixture-owner'}
        self.assertRejected('input_fields')

    def test_14_recipe_hidden_candidates_have_no_photo_scope_and_stay_excluded(self):
        hidden = VisualIngredientEvidenceAdapter().fixture_demo()['recipe_assisted_hidden_ingredients'][0]
        replace = lambda item: (item.clear(), item.update(deepcopy(hidden)))
        self.assertRejected('photo_scope_not_reviewable', SyntheticPhotoReview(visual=replace))
        energy = self.f.energy(self.f.build()['basis'], hidden=True)
        self.assertEqual((energy['status'], energy['energy']), ('computed', {'low': '148', 'high': '152'}))
        self.assertEqual(energy['completeness'], 'incomplete_hidden_ingredients_unquantified')
        self.assertEqual([e['evidence']['ingredient_candidate'] for e in energy['excluded_recipe_priors']], ['烹調油', '糖'])


class EvidenceBoundaryTests(PhotoReviewCase):
    def test_attribute_sets_parallel_review_v1(self):
        self.assertEqual((IDENTITY_ATTRIBUTES, BASIS_ATTRIBUTES), (V1_IDENTITY, V1_BASIS))

    def test_annotation_class_alone_cannot_verify_food(self):
        self.f.claim('claim_food_photo')['evidence_ids'] = ['ev_candidate']
        self.f.row('food')['evidence_ids'] = ['ev_candidate', 'ev_sample']
        self.f.seal()
        self.assertRejected('photo_visual_review_required')

    def test_photo_claim_must_state_the_judged_value(self):
        self.f.claim('claim_state_photo')['value'] = '生'
        self.f.seal()
        self.assertRejected('photo_claim_judgment_mismatch')

    def test_photo_and_source_evidence_cannot_cross_sides(self):
        self.f.claim('claim_food_source')['evidence_ids'] = ['ev_judgment_food']
        self.f.seal()
        self.assertRejected('claim_side_evidence')
        f = SyntheticPhotoReview()
        f.claim('claim_food_photo')['evidence_ids'] = ['ev_judgment_food', 'ev_sample']
        f.seal()
        self.assertRejected('claim_side_evidence', f)
        f = SyntheticPhotoReview()
        f.input['evidence'][0]['statement_role'] = 'food_identity'
        f.seal()
        self.assertRejected('statement_role', f)

    def test_caller_assertions_cannot_verify(self):
        self.f.claim('claim_reference_measure_source')['evidence_ids'] = ['ev_assertion']
        self.f.row('reference_measure')['evidence_ids'] = ['ev_assertion']
        self.f.seal()
        self.assertRejected('caller_claim_only')

    def test_visual_reviewer_must_be_independent_of_annotator_and_measurer(self):
        for reviewer in ('test://annotator', 'test://measurer'):
            self.assertRejected('photo_review_not_independent', SyntheticPhotoReview(visual_reviewer=reviewer))

    def test_visual_evidence_must_bind_image_and_producer(self):
        f = SyntheticPhotoReview(visual=lambda item: item['evidence_source'].update(image_sha256='0' * 64))
        self.assertRejected('photo_image_unbound', f)
        f = SyntheticPhotoReview(visual=lambda item: item['evidence_source'].pop('annotator'))
        self.assertRejected('visual_evidence_producer_unrecorded', f)

    def test_judgment_region_must_lie_inside_the_evidence_region(self):
        self.assertRejected('judgment_region_outside_evidence', SyntheticPhotoReview(judgment_region=region(0, 0, 50, 50)))

    def test_photo_mass_claim_must_equal_the_estimate_interval(self):
        self.f.claim('claim_photo_consumed_mass_photo')['value'] = {'low': '140', 'high': '152', 'coverage': '0.95'}
        self.f.seal()
        self.assertRejected('photo_mass_claim_mismatch')
        f = SyntheticPhotoReview()
        f.claim('claim_photo_consumed_mass_photo')['unit'] = 'kg'
        f.seal()
        self.assertRejected('known_claim_requires_value_evidence', f)
        f = SyntheticPhotoReview()
        f.claim('claim_photo_consumed_mass_photo').update(value_kind='decimal', value='150')
        f.seal()
        self.assertRejected('claim_kind', f)

    def test_energy_reference_must_be_one_reported_kcal_or_kj_value(self):
        f = SyntheticPhotoReview(energy_raw='')
        f.row('nutrient_definition').update(status='unresolved', reason_code='nutrient_value_missing')
        f.seal()
        self.assertEqual((f.build()['status'], f.build()['reasons']), ('unavailable', ['energy_value_unreported']))
        f = SyntheticPhotoReview(unit='g', expected_unit='g')
        self.assertEqual(f.build()['reasons'], ['energy_unit_not_supported'])
        # Corrected energy is its own reviewed nutrient: no observation, no merge with 熱量.
        f = SyntheticPhotoReview()
        f.input['basis_target']['required_nutrients'].append({'original_name': '修正熱量', 'expected_unit': 'kcal'})
        for attribute in ('nutrient_units', 'nutrient_definition'):
            f.results.append(dict(deepcopy(f.row(attribute)), nutrient_key='修正熱量', status='unresolved',
                                  reason_code='nutrient_missing', source_claim_ref=None, evidence_ids=[]))
        f.seal()
        self.assertEqual(f.build(energy_nutrient='修正熱量')['reasons'], ['energy_observation_missing'])
        build = f.build()
        self.assertEqual((build['status'], build['basis']['energy']['original_name'], build['reasons']),
                         ('built', '熱量', ['gate_basis_pending']))

    def test_explicit_source_link_must_match_the_candidate(self):
        self.f.input['source_link']['food_id'] = 'SYNTHETIC2'
        self.f.seal()
        self.assertRejected('source_link_mismatch')


def _visual_review(change):
    def mutate(f):
        record = deepcopy(f.visual_review)
        change(record)
        f.rewrite('visual_review', record)
    return mutate


def _add_reference_measure_photo_claim(f):
    f.input['claims'].append({'claim_id': 'claim_reference_measure_photo', 'domain': 'basis', 'attribute': 'reference_measure',
                              'nutrient_key': None, 'side': 'photo', 'value_state': 'known', 'value_kind': 'text',
                              'value': 'SYNTHETIC photo-only statement', 'unit': None, 'evidence_ids': ['ev_judgment_food']})
    f.row('reference_measure').update(photo_claim_ref='claim_reference_measure_photo', source_claim_ref=None,
                                      evidence_ids=['ev_judgment_food'])


def _known_source_claim(attribute, status, value):
    def mutate(f):
        f.claim(f'claim_{attribute}_source').update(value=value, value_state='known', evidence_ids=['ev_document'])
        f.row(attribute).update(status=status, reason_code='synthetic_manual_comparison', evidence_ids=['ev_document'])
    return mutate


# (name, fixture kwargs, mutation, expected first issue code, state); the owner reseals after each mutation
# unless the mutation is applied to the request only, which must then disagree with the pinned ledger.
ATTACKS = [
    ('visual review extra field', {}, _visual_review(lambda r: r.update(approved=True)), 'photo_visual_review_invalid', 'invalid'),
    ('visual review without original image', {}, _visual_review(lambda r: r.update(image_access='thumbnail_only')),
     'photo_visual_review_invalid', 'invalid'),
    ('visual review of another estimate', {}, _visual_review(lambda r: r['photo_scope'].update(estimate_id='est-other')),
     'photo_visual_review_scope', 'invalid'),
    ('judgment extra field', {}, _visual_review(lambda r: r['judgments']['food'].update(confidence=0.9)),
     'photo_visual_review_invalid', 'invalid'),
    ('known judgment saying unknown', {}, _visual_review(lambda r: r['judgments']['part'].update(value='Unknown')),
     'photo_visual_review_invalid', 'invalid'),
    ('judgment region in another coordinate space', {},
     _visual_review(lambda r: r['judgments']['part']['region'].update(coordinate_space='normalized')),
     'photo_visual_review_invalid', 'invalid'),
    ('unknown judgment carrying a value', {},
     _visual_review(lambda r: r['judgments']['part'].update(value_state='unknown', region=None)),
     'photo_visual_review_invalid', 'invalid'),
    ('photo mass without portion evidence', {},
     lambda f: (f.claim('claim_photo_consumed_mass_photo').update(evidence_ids=['ev_candidate']),
                f.row('photo_consumed_mass').update(evidence_ids=['ev_candidate'])),
     'photo_mass_evidence_required', 'invalid'),
    ('basis attribute without official evidence', {}, _add_reference_measure_photo_claim, 'source_evidence_required', 'invalid'),
    ('identity without the source-side claim', {}, lambda f: f.row('food').update(source_claim_ref=None),
     'two_sided_evidence_required', 'invalid'),
    ('sample preparation used for processing', {}, lambda f: f.evidence('ev_sample').update(statement_role='sample_preparation'),
     'processing_role_unresolved', 'invalid'),
    ('verified version when official version is null', {'version': None},
     _known_source_claim('data_version', 'verified', 'synthetic-v1'), 'official_version_unknown', 'invalid'),
    ('version claim differs from provenance', {}, lambda f: f.claim('claim_data_version_source').update(value='synthetic-v2'),
     'version_claim_mismatch', 'invalid'),
    ('decided unit when the source unit is missing', {'unit': None},
     _known_source_claim('nutrient_units', 'mismatch', 'kcal'), 'nutrient_unit_missing', 'invalid'),
    ('unit claim differs from the observation', {}, lambda f: f.claim('claim_nutrient_units_source').update(value='kJ'),
     'unit_claim_mismatch', 'invalid'),
    ('verified definition of an unreported value', {'energy_raw': ''}, lambda f: None, 'nutrient_value_unresolved', 'invalid'),
    ('gate without reason code', {}, lambda f: f.gates[0].update(reason_code=' '), 'gate_reason_code', 'invalid'),
    ('ledger extra field', {}, lambda f: f.seal(ledger_changes={'approved': True}), 'context_fields', 'invalid'),
    ('ledger boolean policy revision', {}, lambda f: f.seal(ledger_changes={'policy_revision': True}),
     'context_policy_revision', 'invalid'),
    ('request claim differs from the ledger', {}, lambda f: (f.seal(), f.claim('claim_food_source').update(value='edited')),
     'context_scope_or_statement_mismatch', 'invalid'),
    ('recipe basis target purpose', {}, lambda f: f.input['basis_target'].update(purpose='recipe_declared_ingredient_reference'),
     'basis_target', 'invalid'),
    ('empty nutrient profile', {}, lambda f: f.input['basis_target'].update(required_nutrients=[]), 'nutrient_profile', 'invalid'),
    ('missing attribute result', {}, lambda f: f.results.remove(f.row('recipe_declared_mass')), 'required_attributes', 'invalid'),
    ('unknown attachment slot', {}, lambda f: f.input['attachment_refs'].update(extra=f.ref('assertion')),
     'attachment_fields', 'invalid'),
    ('catalog is not an object', {}, lambda f: setattr(f, 'catalog', []), 'attachment_catalog', 'invalid'),
    # Unreferenced entries: the whole trusted catalog is checked, not only the attachments this request cites.
    ('unreferenced catalog entry with an extra field', {},
     lambda f: f.catalog.update(orphan=dict(deepcopy(f.catalog['assertion']), note='x')), 'attachment_catalog', 'invalid'),
    ('unreferenced catalog entry with an unknown origin', {},
     lambda f: f.catalog.update(orphan=dict(deepcopy(f.catalog['assertion']), origin='model_reply')), 'attachment_origin', 'invalid'),
    ('unreferenced catalog entry for another photo', {},
     lambda f: f.catalog.update(orphan={'origin': 'caller_assertion', 'representation': 'bytes', 'sha256': 'c' * 64,
                                        'scope': {'photo_scope': {}, 'candidate_scope': f.input['candidate_scope']}}),
     'attachment_scope', 'invalid'),
    ('referenced catalog entry with an unknown origin', {}, lambda f: f.catalog['assertion'].update(origin='model_reply'),
     'attachment_origin', 'invalid'),
    ('visual evidence slot bound to another origin', {},
     lambda f: f.input['attachment_refs'].update(visual_evidence=f.ref('assertion')), 'attachment_origin', 'invalid'),
    ('portion record listed as an evidence file', {},
     lambda f: f.input['attachment_refs']['evidence_files'].append(f.ref('estimate')), 'attachment_origin', 'invalid'),
    ('visual evidence is not an object', {}, lambda f: f.rewrite('visual', [deepcopy(f.item)]), 'invalid_visual_evidence', 'invalid'),
    ('photo scope evidence hash', {}, lambda f: f.photo_scope.update(evidence_sha256='0' * 64),
     'visual_evidence_hash_mismatch', 'invalid'),
    ('photo scope ingredient', {}, lambda f: f.photo_scope.update(ingredient_candidate='糙米'), 'photo_scope_mismatch', 'invalid'),
    ('invalid portion estimate', {'estimate_changes': {'usage': 'production'}}, lambda f: None,
     'invalid_portion_estimate', 'invalid'),
    ('photo scope estimate ID', {}, lambda f: f.photo_scope.update(estimate_id='est-other'), 'photo_scope_mismatch', 'invalid'),
    ('native lookup replaced by a dict', {}, lambda f: f.attachments.update(lookup={'status': 'found'}),
     'invalid_source_lookup', 'invalid'),
    ('evidence read from the provenance slot', {},
     lambda f: f.evidence('ev_assertion').update(origin='source_provenance', attachment_ref='provenance',
                                                 attachment_sha256=f.catalog['provenance']['sha256']),
     'unsupported_evidence_origin', 'invalid'),
    ('claim extra field', {}, lambda f: f.claim('claim_food_photo').update(approved=True), 'claim_fields_or_id', 'invalid'),
    ('second photo claim for one attribute', {},
     lambda f: f.input['claims'].append(dict(deepcopy(f.claim('claim_food_photo')), claim_id='claim_food_photo_2')),
     'claim_scope_or_duplicate', 'invalid'),
    ('claim cites unknown evidence', {}, lambda f: f.claim('claim_food_photo').update(evidence_ids=['ev_missing']),
     'claim_evidence', 'invalid'),
    ('blank claim unit', {}, lambda f: f.claim('claim_food_source').update(unit=' '), 'claim_unit', 'invalid'),
    ('unknown claim carrying a value', {}, lambda f: f.claim('claim_food_source').update(value_state='unknown'),
     'missing_unknown_claim', 'invalid'),
    ('unsupported review rule', {}, lambda f: f.row('food').update(rule_id='automatic_match_v1'), 'unsupported_review_rule', 'blocked'),
    ('unsupported rule version', {}, lambda f: f.row('food').update(rule_version='2'), 'unsupported_rule_version', 'blocked'),
    ('result cites unknown evidence', {}, lambda f: f.row('food')['evidence_ids'].append('ev_missing'),
     'missing_evidence_ref', 'invalid'),
    ('result links another attribute claim', {}, lambda f: f.row('part').update(photo_claim_ref='claim_food_photo'),
     'cross_attribute_claim', 'invalid'),
    ('result omits its claim evidence', {}, lambda f: f.row('food').update(evidence_ids=['ev_sample', 'ev_judgment_part']),
     'result_evidence_coverage', 'invalid'),
]


class AttackTableTests(PhotoReviewCase):
    def test_every_rejection_path_fails_closed(self):
        for name, kwargs, mutate, code, state in ATTACKS:
            with self.subTest(name):
                f = SyntheticPhotoReview(**kwargs)
                mutate(f)
                if name != 'request claim differs from the ledger' and not name.startswith('ledger '):
                    f.seal()
                self.assertRejected(code, f, state=state)


class LedgerAndGateTests(PhotoReviewCase):
    def test_gate_records_are_closed_scoped_and_decided_by_a_reviewer(self):
        cases = [(dict(status='not_applicable'), 'gate_status'), (dict(purpose='recipe_calculation'), 'gate_purpose'),
                 (dict(evidence_locator=None), 'gate_evidence_missing'), (dict(policy_revision=2), 'gate_record_incomplete'),
                 (dict(policy_revision=True), 'gate_record_incomplete'), (dict(reviewer_ref=None), 'gate_record_incomplete'),
                 (dict(candidate_scope={}), 'gate_scope_mismatch'), (dict(approved=True), 'gate_record_fields')]
        for changes, code in cases:
            f = SyntheticPhotoReview()
            f.gates[0].update(changes)
            f.seal()
            self.assertRejected(code, f)
        f = SyntheticPhotoReview()
        f.gates[0] = dict(f.gate('source_use', 'pending', 'license_scope_unconfirmed'), reviewer_ref='test://source-reviewer')
        f.seal()
        self.assertRejected('gate_fields_without_decision', f)
        f = SyntheticPhotoReview()
        f.gates.append(f.gate('source_use'))
        f.seal()
        self.assertRejected('gate_scope_or_duplicate', f)
        f = SyntheticPhotoReview()
        f.gates[0] = dict(f.gate('source_use', 'failed', 'attribution_missing'), evidence_locator=None)
        f.seal()
        self.assertEqual(f.build()['basis']['gates']['source_use'], {'status': 'failed', 'evidence_ref': None})

    def test_request_gates_and_results_must_equal_the_witnessed_ledger(self):
        self.f.gates[0]['reason_code'] = 'attacker_edit'
        self.assertRejected('forged_gate_record')
        f = SyntheticPhotoReview()
        f.row('part')['status'] = 'unresolved'
        self.assertRejected('forged_review_result', f)
        f = SyntheticPhotoReview()
        f.seal(pending=True)
        self.assertRejected('review_not_checked', f, state='blocked')

    def test_hash_and_context_integrity(self):
        self.f.attachments['visual'] += b' '
        self.assertRejected('evidence_hash_mismatch')
        f = SyntheticPhotoReview()
        f.context = ReviewContext(f.context.record_bytes + b' ', f.attachments)
        self.assertRejected('context_hash_mismatch', f)
        f = SyntheticPhotoReview()
        f.catalog['assertion']['scope'] = {'photo_scope': {}, 'candidate_scope': f.input['candidate_scope']}
        f.seal()
        self.assertRejected('attachment_scope', f)

    def test_inputs_are_not_mutated_and_outputs_are_isolated(self):
        native = self.f.attachments.pop('lookup')
        before = deepcopy((self.f.input, self.f.results, self.f.gates, self.f.attachments))
        self.f.attachments['lookup'] = native
        build = self.f.build()
        self.f.attachments.pop('lookup')
        self.assertEqual(before, (self.f.input, self.f.results, self.f.gates, self.f.attachments))
        self.f.attachments['lookup'] = native
        build['decision']['attribute_results'][0]['status'] = 'mismatch'
        build['basis']['gates']['identity']['status'] = 'failed'
        self.assertEqual(self.f.build()['decision']['identity_decision'], 'compatible')
        self.assertEqual(self.f.results, before[1])


if __name__ == '__main__':
    unittest.main()
