"""Synthetic portion contract checks: no model, image decoding, calibration data or real nutrients.

Every mass, calibration and energy value below is an invented synthetic number for contract tests,
not a measurement or nutrition fact for the named food.
"""
from copy import deepcopy
import json
import unittest

from src.pipeline.diagnostic_demo import run_demo
from src.portion import (assess_portions, calibration_blockers, canonical_sha256, estimate_photo_energy,
                         evidence_identity, validate_calibration, validate_energy_basis, validate_portion_estimate)
from src.visual_ingredients import VisualIngredientEvidenceAdapter

CONSUMED = {'status': 'whole_visible_portion_asserted', 'asserted_by': 'synthetic-user',
            'evidence_ref': 'test://synthetic-consumption'}


def codes(issues):
    return {i['code'] for i in issues}


class PortionFixture(unittest.TestCase):
    def setUp(self):
        self.envelope = VisualIngredientEvidenceAdapter().fixture_demo()
        self.cucumber, self.carrot = self.envelope['visual_ingredient_evidence']
        # Cucumber-only variant: the uncertain carrot can never be estimated, so totals need it removed.
        self.visible = deepcopy(self.envelope)
        self.visible['visual_ingredient_evidence'] = [deepcopy(self.cucumber)]

    def weighed(self, evidence=None, **changes):
        estimate = {'contract': 'foodcal.photo_portion_estimate', 'schema_version': 0, 'estimate_id': 'est-1',
                    'evidence_ref': evidence_identity(evidence or self.cucumber),
                    'mass_kind': 'visible_mass_at_capture', 'mass_g': {'low': '118', 'high': '122', 'coverage': '0.95'},
                    'method': {'kind': 'direct_weighing', 'method_id': 'synthetic-scale', 'method_version': '1'},
                    'measurement': {'instrument_resolution_g': '1', 'evidence_ref': 'test://synthetic-weighing/1',
                                    'measured_by': 'synthetic-measurer'},
                    'calibration_ref': None, 'producer': {'kind': 'human', 'ref': 'synthetic-measurer'},
                    'review': {'status': 'accepted', 'reviewer_ref': 'synthetic-reviewer',
                               'reviewed_at': '2026-11-11T00:00:00Z'},
                    'usage': 'diagnostic/test_only'}
        estimate.update(changes)
        return estimate

    def calibration(self, **changes):
        record = {'contract': 'foodcal.portion_calibration', 'schema_version': 0, 'calibration_id': 'cal-1',
                  'method': {'kind': 'calibrated_visual_estimate', 'method_id': 'synthetic-area-method',
                             'method_version': '1'},
                  'status': 'approved',
                  'dataset': {'reference': 'test://synthetic-calibration-set', 'sha256': 'a' * 64,
                              'split': 'held_out_by_source_group', 'ground_truth': 'direct_weighing',
                              'sample_count': 40, 'source_group_count': 5},
                  'coverage': {'nominal': '0.90', 'covered_count': 37}, 'error': {'mae_g': '12.5'},
                  'validity_scope': {'ingredient_candidates': ['小黃瓜'], 'mass_range_g': {'low': '20', 'high': '400'}},
                  'producer_ref': 'synthetic-calibrator',
                  'approval': {'status': 'approved', 'approver_ref': 'synthetic-approver',
                               'approved_at': '2026-11-11T00:00:00Z'},
                  'usage': 'diagnostic/test_only'}
        for key, value in changes.items():
            record[key] = value
        return record

    def calibrated(self, record, **changes):
        fields = {'mass_g': {'low': '95', 'high': '150', 'coverage': '0.90'}, 'method': deepcopy(record['method']),
                  'measurement': None, 'producer': {'kind': 'calibrated_method', 'ref': 'synthetic-area-method@1'},
                  'calibration_ref': {'calibration_id': record['calibration_id'], 'record_sha256': canonical_sha256(record)}}
        fields.update(changes)
        return self.weighed(**fields)

    def basis(self, **changes):
        record = {'basis_id': 'basis-1', 'estimate_id': 'est-1', 'ingredient_candidate': '小黃瓜',
                  'purpose': 'photo_calculation',
                  'energy': {'original_name': 'synthetic energy', 'unit': 'kcal', 'per': 'per_100g_edible_portion',
                             'value': '15', 'interval': None},
                  'gates': {g: {'status': 'passed', 'evidence_ref': f'test://synthetic-gate/{g}'}
                            for g in ('source_use', 'identity', 'basis', 'integrity')},
                  'reviewer_ref': 'synthetic-nutrition-reviewer', 'usage': 'diagnostic/test_only'}
        record.update(changes)
        return record

    def item(self, result, index=0):
        return result['items'][index]


class PortionAssessmentTests(PortionFixture):
    def test_default_has_no_photo_mass_and_recipe_priors_stay_unquantified(self):
        before = deepcopy(self.envelope)
        result = assess_portions(self.envelope)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual([(i['status'], i['reason']) for i in result['items']],
                         [('unavailable', 'photo_mass_unknown'), ('unavailable', 'visual_evidence_uncertain')])
        self.assertEqual([(h['evidence']['ingredient_candidate'], h['reason'], h['mass_g']) for h in result['hidden_ingredients']],
                         [('烹調油', 'recipe_prior_not_visual_evidence', None), ('糖', 'recipe_prior_not_visual_evidence', None)])
        self.assertIsNone(result['photo_portion_grams'])
        self.assertFalse(result['nutrition_eligible'])
        self.assertEqual(self.envelope, before)
        json.dumps(result, ensure_ascii=False)

    def test_dish_detection_never_receives_photo_mass(self):
        envelope = VisualIngredientEvidenceAdapter().from_detection(run_demo()['detection_snapshot'])
        estimate = self.weighed(evidence=envelope['visual_ingredient_evidence'][0])
        result = assess_portions(envelope, estimates=[estimate])
        self.assertTrue(all(i['reason'] == 'ingredient_level_semantics_unverified' for i in result['items']))
        self.assertEqual(codes(result['issues']), {'estimate_evidence_unmatched'})

    def test_accepted_direct_weighing_is_ready_but_never_formally_eligible(self):
        estimate = self.weighed()
        before = deepcopy(estimate)
        result = assess_portions(self.envelope, estimates=[estimate])
        item = self.item(result)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual((item['status'], item['ready_for_energy'], item['blocking_reasons']), ('estimated', True, []))
        self.assertEqual(item['mass_g'], estimate['mass_g'])
        self.assertIsNone(result['photo_portion_grams'])
        self.assertFalse(result['nutrition_eligible'])
        self.assertEqual(estimate, before)

    def test_review_must_be_independent_and_decided(self):
        pending = self.weighed(review={'status': 'pending', 'reviewer_ref': None, 'reviewed_at': None})
        item = self.item(assess_portions(self.envelope, estimates=[pending]))
        self.assertEqual((item['status'], item['ready_for_energy']), ('estimated', False))
        self.assertEqual(item['blocking_reasons'], ['independent_review_pending'])
        for reviewer in ('synthetic-measurer',):
            own = self.weighed(review={'status': 'accepted', 'reviewer_ref': reviewer, 'reviewed_at': '2026-11-11'})
            item = self.item(assess_portions(self.envelope, estimates=[own]))
            self.assertEqual(item['reason'], 'invalid_estimate')
            self.assertIn('review_not_independent', codes(item['issues']))
        rejected = self.weighed(review={'status': 'rejected', 'reviewer_ref': 'synthetic-reviewer', 'reviewed_at': '2026-11-11'})
        item = self.item(assess_portions(self.envelope, estimates=[rejected]))
        self.assertEqual((item['status'], item['reason'], item['mass_g']), ('unavailable', 'estimate_rejected_by_review', None))

    def test_non_photo_mass_sources_are_rejected(self):
        for kind, code in (('recipe_declared_quantity', 'recipe_quantity_is_not_photo_mass'),
                           ('standard_serving', 'standard_serving_is_not_photo_mass'),
                           ('detection_confidence', 'model_confidence_is_not_mass'),
                           ('ai_draft', 'ai_draft_is_not_measurement'),
                           ('unlisted_method', 'method_kind_not_allowed')):
            estimate = self.weighed(method={'kind': kind, 'method_id': 'synthetic', 'method_version': '1'})
            self.assertIn(code, codes(validate_portion_estimate(estimate)))
            item = self.item(assess_portions(self.envelope, estimates=[estimate]))
            self.assertEqual((item['status'], item['reason'], item['mass_g']), ('unavailable', 'invalid_estimate', None))

    def test_recipe_hidden_candidate_cannot_bind_an_estimate(self):
        hidden = self.envelope['recipe_assisted_hidden_ingredients'][0]
        estimate = self.weighed(evidence=hidden)
        self.assertIn('invalid_evidence_ref', codes(validate_portion_estimate(estimate)))  # no evidence_id at all
        estimate['evidence_ref']['evidence_id'] = 'oil-0'
        self.assertIn('recipe_prior_not_visual_evidence', codes(validate_portion_estimate(estimate)))
        result = assess_portions(self.envelope, estimates=[estimate])
        self.assertEqual(codes(result['issues']), {'recipe_prior_not_visual_evidence'})
        self.assertEqual(self.item(result)['reason'], 'photo_mass_unknown')
        self.assertTrue(all(h['mass_g'] is None for h in result['hidden_ingredients']))

    def test_uncertain_visual_evidence_cannot_carry_mass(self):
        result = assess_portions(self.envelope, estimates=[self.weighed(evidence=self.carrot)])
        item = self.item(result, 1)
        self.assertEqual((item['status'], item['reason']), ('unavailable', 'visual_evidence_uncertain'))
        self.assertEqual(codes(item['issues']), {'estimate_for_inadmissible_evidence'})

    def test_evidence_edit_after_estimation_unbinds_the_estimate(self):
        estimate = self.weighed()
        changed = deepcopy(self.envelope)
        changed['visual_ingredient_evidence'][0]['image_region']['x_max'] = 210
        self.assertEqual(self.item(assess_portions(changed, estimates=[estimate]))['reason'], 'evidence_hash_mismatch')

    def test_interval_and_number_formats_fail_closed(self):
        cases = [({'low': '122', 'high': '118', 'coverage': '0.95'}, 'invalid_interval_bounds'),
                 ({'low': 118.0, 'high': '122', 'coverage': '0.95'}, 'invalid_interval_bounds'),
                 ({'low': '1e2', 'high': '122', 'coverage': '0.95'}, 'invalid_interval_bounds'),
                 ({'low': '-1', 'high': '122', 'coverage': '0.95'}, 'invalid_interval_bounds'),
                 ({'low': '118', 'high': '122', 'coverage': '0'}, 'invalid_coverage'),
                 ({'low': '118', 'high': '122', 'coverage': '1.5'}, 'invalid_coverage'),
                 ({'low': '120', 'high': '120.5', 'coverage': '0.95'}, 'interval_narrower_than_resolution'),
                 ({'low': '0', 'high': '0', 'coverage': '0.95'}, 'zero_mass_interval'),
                 ({'low': '118', 'high': '122'}, 'invalid_interval')]
        for mass, code in cases:
            self.assertIn(code, codes(validate_portion_estimate(self.weighed(mass_g=mass))), mass)
        private = self.weighed(measurement={'instrument_resolution_g': '1', 'measured_by': 'synthetic-measurer',
                                            'evidence_ref': '/Users/someone/scale.jpg'})
        self.assertIn('private_path_not_allowed', codes(validate_portion_estimate(private)))
        extra = dict(self.weighed(), point_estimate_g='120')
        self.assertEqual(codes(validate_portion_estimate(extra)), {'fields_mismatch'})

    def test_duplicate_estimates_are_not_resolved_by_choice(self):
        result = assess_portions(self.envelope, estimates=[self.weighed(), self.weighed(estimate_id='est-2')])
        self.assertEqual(self.item(result)['reason'], 'duplicate_estimates')

    def test_invalid_envelope_is_refused(self):
        for envelope in (None, {}, {'visual_ingredient_evidence': [], 'recipe_assisted_hidden_ingredients': None}):
            with self.assertRaisesRegex(ValueError, 'invalid_visual_envelope'):
                assess_portions(envelope)


class CalibrationTests(PortionFixture):
    def test_approved_in_scope_calibration_makes_estimate_ready(self):
        record = self.calibration()
        self.assertEqual((validate_calibration(record), calibration_blockers(record)), ([], []))
        item = self.item(assess_portions(self.envelope, estimates=[self.calibrated(record)], calibrations=[record]))
        self.assertEqual((item['status'], item['ready_for_energy'], item['calibration_id']), ('estimated', True, 'cal-1'))

    def test_calibration_binding_is_hash_pinned(self):
        record = self.calibration()
        estimate = self.calibrated(record)
        edited = self.calibration(error={'mae_g': '13'})
        cases = [([edited], 'calibration_hash_mismatch'), ([], 'calibration_unavailable'),
                 ([record, deepcopy(record)], 'duplicate_calibration_id')]
        for calibrations, reason in cases:
            item = self.item(assess_portions(self.envelope, estimates=[estimate], calibrations=calibrations))
            self.assertEqual((item['status'], item['reason'], item['mass_g']), ('unavailable', reason, None))

    def test_calibration_floors_cannot_be_approved_away(self):
        cases = [(dict(dataset=dict(self.calibration()['dataset'], sample_count=20), coverage={'nominal': '0.90', 'covered_count': 19}),
                  'calibration_sample_too_small'),
                 (dict(dataset=dict(self.calibration()['dataset'], source_group_count=1)), 'calibration_source_groups_insufficient'),
                 (dict(coverage={'nominal': '0.90', 'covered_count': 30}), 'calibration_coverage_below_nominal'),
                 (dict(status='draft', approval={'status': 'pending', 'approver_ref': None, 'approved_at': None}),
                  'calibration_not_approved')]
        for changes, reason in cases:
            record = self.calibration(**changes)
            self.assertEqual(validate_calibration(record), [], reason)
            item = self.item(assess_portions(self.envelope, estimates=[self.calibrated(record)], calibrations=[record]))
            self.assertEqual((item['status'], item['ready_for_energy']), ('estimated', False), reason)
            self.assertIn(reason, item['blocking_reasons'])
        revoked = self.calibration(status='revoked')
        item = self.item(assess_portions(self.envelope, estimates=[self.calibrated(revoked)], calibrations=[revoked]))
        self.assertEqual((item['status'], item['reason']), ('unavailable', 'calibration_revoked'))

    def test_scope_coverage_and_method_must_match_calibration(self):
        blockers = [(self.calibration(validity_scope={'ingredient_candidates': ['紅蘿蔔'], 'mass_range_g': {'low': '20', 'high': '400'}}),
                     'outside_calibration_ingredient_scope'),
                    (self.calibration(validity_scope={'ingredient_candidates': ['小黃瓜'], 'mass_range_g': {'low': '20', 'high': '100'}}),
                     'outside_calibration_mass_range')]
        for record, reason in blockers:
            item = self.item(assess_portions(self.envelope, estimates=[self.calibrated(record)], calibrations=[record]))
            self.assertEqual((item['status'], item['blocking_reasons']), ('estimated', [reason]))
        record = self.calibration()
        failures = [(self.calibrated(record, mass_g={'low': '95', 'high': '150', 'coverage': '0.95'}),
                     'coverage_differs_from_calibration'),
                    (self.calibrated(record, method=dict(record['method'], method_version='2')), 'calibration_method_mismatch')]
        for estimate, reason in failures:
            item = self.item(assess_portions(self.envelope, estimates=[estimate], calibrations=[record]))
            self.assertEqual((item['status'], item['reason']), ('unavailable', reason))

    def test_calibration_structure_is_closed_and_independent(self):
        cases = [(self.calibration(approval={'status': 'approved', 'approver_ref': 'synthetic-calibrator', 'approved_at': '2026-11-11'}),
                  'approval_not_independent'),
                 (self.calibration(dataset=dict(self.calibration()['dataset'], split='random')), 'invalid_dataset'),
                 (self.calibration(dataset=dict(self.calibration()['dataset'], reference='C:\\data\\set.json')),
                  'private_path_not_allowed'),
                 (self.calibration(approval={'status': 'pending', 'approver_ref': None, 'approved_at': None}),
                  'calibration_status_approval_mismatch'),
                 (self.calibration(coverage={'nominal': '1', 'covered_count': 40}), 'invalid_coverage_evaluation')]
        for record, code in cases:
            self.assertIn(code, codes(validate_calibration(record)), code)
        record = cases[0][0]
        item = self.item(assess_portions(self.envelope, estimates=[self.calibrated(record)], calibrations=[record]))
        self.assertEqual((item['reason'], codes(item['issues'])), ('calibration_invalid', {'approval_not_independent'}))


class EnergyTests(PortionFixture):
    def test_default_energy_is_unavailable_and_recipe_priors_are_excluded(self):
        result = estimate_photo_energy(self.envelope)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['reasons'], ['visual_items_unavailable', 'consumption_basis_missing'])
        self.assertEqual([e['evidence']['ingredient_candidate'] for e in result['excluded_recipe_priors']], ['烹調油', '糖'])
        self.assertEqual(result['completeness'], 'incomplete_hidden_ingredients_unquantified')
        self.assertEqual((result['nutrition_eligible'], result['production_eligible']), (False, False))
        self.assertIsNone(result['energy'])
        json.dumps(result, ensure_ascii=False)

    def test_ready_portion_without_photo_basis_stays_unavailable(self):
        result = estimate_photo_energy(self.visible, estimates=[self.weighed()], consumption=CONSUMED)
        self.assertEqual(result['items'][0]['reason'], 'nutrition_basis_unavailable')
        self.assertEqual(result['status'], 'unavailable')

    def test_every_gate_must_pass_for_photo_calculation(self):
        gates = self.basis()['gates']
        cases = [(self.basis(gates=dict(gates, basis={'status': 'pending', 'evidence_ref': None})),
                  'nutrition_basis_unresolved', ['gate_basis_pending']),
                 (self.basis(gates=dict(gates, source_use={'status': 'not_applicable', 'evidence_ref': 'test://na'})),
                  'nutrition_basis_unresolved', ['gate_source_use_not_applicable']),
                 (self.basis(ingredient_candidate='紅蘿蔔'), 'basis_ingredient_mismatch', [])]
        for basis, reason, blocking in cases:
            item = estimate_photo_energy(self.visible, estimates=[self.weighed()], nutrition_bases=[basis],
                                         consumption=CONSUMED)['items'][0]
            self.assertEqual((item['status'], item['reason'], item['blocking_reasons']), ('unavailable', reason, blocking))
        recipe_purpose = self.basis(purpose='recipe_declared_ingredient_reference')
        self.assertIn('purpose_not_photo_calculation', codes(validate_energy_basis(recipe_purpose)))
        item = estimate_photo_energy(self.visible, estimates=[self.weighed()], nutrition_bases=[recipe_purpose],
                                     consumption=CONSUMED)['items'][0]
        self.assertEqual(item['reason'], 'nutrition_basis_invalid')

    def test_interval_arithmetic_is_exact_and_conservative(self):
        result = estimate_photo_energy(self.visible, estimates=[self.weighed()], nutrition_bases=[self.basis()],
                                       consumption=CONSUMED)
        self.assertEqual(result['status'], 'computed')
        self.assertEqual((result['unit'], result['energy']), ('kcal', {'low': '17.7', 'high': '18.3'}))
        self.assertEqual((result['coverage_lower_bound'], result['coverage_note']),
                         (None, 'composition_uncertainty_not_quantified'))
        self.assertEqual(result['completeness'], 'incomplete_hidden_ingredients_unquantified')
        ranged = self.basis(energy=dict(self.basis()['energy'], interval={'low': '12', 'high': '18', 'coverage': '0.90'}))
        result = estimate_photo_energy(self.visible, estimates=[self.weighed()], nutrition_bases=[ranged],
                                       consumption=CONSUMED)
        self.assertEqual(result['energy'], {'low': '14.16', 'high': '21.96'})
        self.assertEqual((result['coverage_lower_bound'], result['coverage_note']), ('0.85', 'bonferroni_union_bound'))

    def test_total_needs_a_consumption_assertion(self):
        result = estimate_photo_energy(self.visible, estimates=[self.weighed()], nutrition_bases=[self.basis()])
        self.assertEqual((result['status'], result['reasons']), ('unavailable', ['consumption_basis_missing']))
        self.assertEqual(result['items'][0]['status'], 'computed')  # visible-mass energy, not consumed energy
        with self.assertRaisesRegex(ValueError, 'invalid_consumption_assertion'):
            estimate_photo_energy(self.visible, consumption={'status': 'assumed_finished', 'asserted_by': 'x',
                                                             'evidence_ref': 'y'})

    def test_kilojoule_reference_is_not_converted(self):
        basis = self.basis(energy=dict(self.basis()['energy'], unit='kJ', value='63'))
        result = estimate_photo_energy(self.visible, estimates=[self.weighed()], nutrition_bases=[basis],
                                       consumption=CONSUMED)
        self.assertEqual((result['unit'], result['energy']), ('kJ', {'low': '74.34', 'high': '76.86'}))

    def test_overlap_and_mixed_units_block_the_total(self):
        second = deepcopy(self.cucumber)
        second['evidence_source']['annotation_id'] = 'annotation-3'
        envelope = deepcopy(self.visible)
        envelope['visual_ingredient_evidence'].append(second)
        ranged = dict(self.basis()['energy'], interval={'low': '12', 'high': '18', 'coverage': '0.90'})
        bases = [self.basis(energy=ranged), self.basis(basis_id='basis-2', estimate_id='est-2', energy=ranged)]

        def run(region, second_basis=None):
            second['image_region'].update(region)
            estimates = [self.weighed(), self.weighed(evidence=second, estimate_id='est-2')]
            return estimate_photo_energy(envelope, estimates=estimates, consumption=CONSUMED,
                                         nutrition_bases=[bases[0], second_basis or bases[1]])

        overlapping = run({'x_min': 200, 'x_max': 300})
        self.assertIn('overlapping_same_ingredient_regions', overlapping['reasons'])
        separate = run({'x_min': 300, 'x_max': 400})
        self.assertEqual(separate['status'], 'computed')
        self.assertEqual(separate['energy'], {'low': '28.32', 'high': '43.92'})
        self.assertEqual(separate['coverage_lower_bound'], '0.7')
        kilojoule = self.basis(basis_id='basis-2', estimate_id='est-2', energy=dict(self.basis()['energy'], unit='kJ'))
        self.assertIn('mixed_energy_units', run({'x_min': 300, 'x_max': 400}, kilojoule)['reasons'])

    def test_inputs_are_not_mutated(self):
        estimates, bases = [self.weighed()], [self.basis()]
        snapshot = deepcopy((self.visible, estimates, bases, CONSUMED))
        estimate_photo_energy(self.visible, estimates=estimates, nutrition_bases=bases, consumption=CONSUMED)
        self.assertEqual((self.visible, estimates, bases, CONSUMED), snapshot)


if __name__ == '__main__':
    unittest.main()
