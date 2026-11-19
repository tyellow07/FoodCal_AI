"""Reference-card portion method: synthetic numbers only, never food measurements or nutrient data."""
from copy import deepcopy
from decimal import Decimal, ROUND_FLOOR
import random
import unittest

from src.portion import (assess_portions, calibration_blockers, estimate_photo_energy, validate_calibration,
                         validate_portion_estimate)
from src.portion.reference_card import (CARD_AREA_CM2, METHOD, conformal_ratio_bounds,
                                        estimate_with_reference_card, fit_reference_card_calibration,
                                        visible_area_cm2)
from src.visual_ingredients import ingredient_annotation_envelope

CARD = [100, 100, 956, 639.8]  # 856 x 539.8 px: 0.1 mm per pixel


def synthetic_samples(groups=('day1-水木', 'day2-風雲樓', 'day3-小吃部'), per_group=12, seed=1):
    rng = random.Random(seed)
    rows = []
    for group in groups:
        for index in range(per_group):
            side = rng.randint(600, 1400)  # px; 1 cm = 100 px
            area = Decimal(side * side) / 10000
            ratio = Decimal(str(round(rng.uniform(1.0, 1.4), 4)))  # synthetic g/cm^2 band
            rows.append({'sample_id': f'{group}-{index}', 'source_group': group,
                         'ingredient_candidate': 'cooked_rice_region', 'region_px': [0, 0, side, side],
                         'card_px': list(CARD), 'mass_g': str((ratio * area).quantize(Decimal('0.1'))),
                         'scale_resolution_g': '1', 'measured_by': 'synthetic-weigher'})
    return rows


def fit(samples=None, **overrides):
    arguments = dict(calibration_id='cal-rice-v0', nominal='0.8', dataset_reference='private://weighing/v0',
                     producer_ref='synthetic-calibrator')
    arguments.update(overrides)
    return fit_reference_card_calibration(synthetic_samples() if samples is None else samples, **arguments)


def approved(record):
    record = deepcopy(record)
    record.update(status='approved', approval={'status': 'approved', 'approver_ref': 'synthetic-approver',
                                               'approved_at': '2026-11-19'})
    return record


def rice_evidence(box=(0, 0, 1000, 1000)):
    return {'ingredient_candidate': 'cooked_rice_region',
            'evidence_source': {'kind': 'pilot_annotation', 'annotation_id': 'ann-1', 'image_sha256': 'b' * 64,
                                'annotator': 'synthetic-annotator', 'reviewer': 'synthetic-reviewer'},
            'evidence_type': 'ingredient_annotation', 'confidence': None,
            'image_region': {'format': 'xyxy', 'coordinate_space': 'original_image_pixels',
                             'x_min': box[0], 'y_min': box[1], 'x_max': box[2], 'y_max': box[3]},
            'status': 'observed_candidate', 'reason': 'human_annotation_review_only'}


class ReferenceCardTests(unittest.TestCase):
    def test_card_scale_is_exact(self):
        self.assertEqual(CARD_AREA_CM2, Decimal('46.20688'))
        self.assertEqual(visible_area_cm2([0, 0, 100, 100], CARD), Decimal(1))
        for bad in ([0, 0, 0, 10], [0, 0, float('nan'), 10], [True, 0, 1, 1], [0, 0, 1], [-1, 0, 1, 1]):
            with self.assertRaises(ValueError):
                visible_area_cm2(bad, CARD)

    def test_conformal_ranks_need_enough_samples(self):
        self.assertIsNone(conformal_ratio_bounds([Decimal(1)] * 3, Decimal('0.8')))
        ratios = [Decimal(i) for i in range(1, 20)]  # n=19: ranks floor(2)=2 and ceil(18)=18
        self.assertEqual(conformal_ratio_bounds(ratios, Decimal('0.8')), (Decimal(2), Decimal(18)))

    def test_fit_produces_valid_draft_record_with_group_held_out_metrics(self):
        samples = synthetic_samples()
        before = deepcopy(samples)
        record, model, diagnostics = fit(samples)
        self.assertEqual(samples, before)
        self.assertEqual(validate_calibration(record), [])
        self.assertEqual((record['status'], record['method']), ('draft', METHOD))
        self.assertEqual(record['dataset']['sample_count'], 36)
        self.assertEqual(record['dataset']['source_group_count'], 3)
        self.assertEqual(diagnostics['samples_not_evaluable_out_of_group'], [])
        self.assertGreaterEqual(Decimal(diagnostics['measured_coverage']), Decimal('0.8'))
        self.assertEqual(calibration_blockers(record), ['calibration_not_approved'])
        self.assertEqual(calibration_blockers(approved(record)), [])
        ratio = model['ratio_g_per_cm2']['cooked_rice_region']
        self.assertTrue(Decimal('1.0') <= Decimal(ratio['low']) < Decimal(ratio['high']) <= Decimal('1.4'))
        self.assertGreater(Decimal(record['error']['mae_g']), 0)

    def test_small_or_single_group_data_cannot_reach_ready(self):
        with self.assertRaises(ValueError) as caught:
            fit(synthetic_samples(groups=('only-day',)))
        self.assertEqual(str(caught.exception), 'at_least_two_source_groups_required')
        record, _, diagnostics = fit(synthetic_samples(groups=('a', 'b'), per_group=10))
        self.assertLess(record['dataset']['sample_count'], 30)
        self.assertIn('calibration_sample_too_small', calibration_blockers(approved(record)))
        with self.assertRaises(ValueError) as caught:
            fit(synthetic_samples(groups=('a', 'b'), per_group=8))  # 8 out-of-group ratios cannot give 80% ranks
        self.assertEqual(str(caught.exception), 'no_sample_evaluable_out_of_group')

    def test_measured_coverage_below_nominal_is_reported_not_hidden(self):
        record, _, diagnostics = fit(synthetic_samples(seed=7))
        self.assertLess(Decimal(diagnostics['measured_coverage']), Decimal('0.8'))
        self.assertEqual(calibration_blockers(approved(record)), ['calibration_coverage_below_nominal'])

    def test_sample_validation(self):
        cases = {'duplicate_sample_id': lambda s: s[1].update(sample_id=s[0]['sample_id']),
                 'positive_weighed_mass_required': lambda s: s[0].update(mass_g='0'),
                 'scale_resolution_must_be_at_most_1g': lambda s: s[0].update(scale_resolution_g='5'),
                 'sample_fields_mismatch': lambda s: s[0].update(recipe_grams='120'),
                 'invalid_card_box': lambda s: s[0].update(card_px=[0, 0, 0, 0])}
        for code, mutate in cases.items():
            samples = synthetic_samples()
            mutate(samples)
            with self.assertRaises(ValueError) as caught:
                fit(samples)
            self.assertEqual(str(caught.exception), code)
        with self.assertRaises(ValueError):
            fit(nominal='1')
        with self.assertRaises(ValueError):
            fit(samples=[])

    def test_estimate_is_contract_valid_and_bound_to_record(self):
        record, model, _ = fit()
        final = approved(record)
        evidence = rice_evidence()
        estimate = estimate_with_reference_card(evidence, CARD, model=model, record=final,
                                                estimate_id='est-1', producer_ref='reference-card-v0')
        self.assertEqual(validate_portion_estimate(estimate), [])
        ratio = model['ratio_g_per_cm2']['cooked_rice_region']
        self.assertEqual(Decimal(estimate['mass_g']['low']), (Decimal(ratio['low']) * 100).quantize(Decimal('0.1'), rounding=ROUND_FLOOR))
        self.assertEqual(estimate['mass_g']['coverage'], '0.8')
        self.assertEqual(estimate['review']['status'], 'not_requested')
        assessment = assess_portions(ingredient_annotation_envelope([evidence], reason='test'),
                                     estimates=[estimate], calibrations=[final])
        [item] = assessment['items']
        self.assertEqual(item['status'], 'estimated')
        self.assertEqual(item['blocking_reasons'], ['independent_review_not_requested'])
        # The draft hash differs from the approved record, so a stale reference is caught.
        stale = estimate_with_reference_card(evidence, CARD, model=model, record=record,
                                             estimate_id='est-1', producer_ref='reference-card-v0')
        assessment = assess_portions(ingredient_annotation_envelope([evidence], reason='test'),
                                     estimates=[stale], calibrations=[final])
        self.assertEqual(assessment['items'][0]['reason'], 'calibration_hash_mismatch')

    def test_inadmissible_or_uncalibrated_inputs_refused(self):
        record, model, _ = fit()
        dish = rice_evidence()
        dish.update(evidence_type='dish_detection_only', ingredient_candidate=None, status='unavailable')
        recipe = rice_evidence()
        recipe['evidence_type'] = 'inferred_from_recipe'
        noodle = rice_evidence()
        noodle['ingredient_candidate'] = 'cooked_noodle_region'
        other_model = dict(model, dataset_sha256='0' * 64)
        for evidence, use_model, code in ((dish, model, 'ingredient_level_semantics_unverified'),
                                          (recipe, model, 'recipe_prior_not_visual_evidence'),
                                          (noodle, model, 'ingredient_not_calibrated'),
                                          (rice_evidence(), other_model, 'model_record_mismatch')):
            with self.assertRaises(ValueError) as caught:
                estimate_with_reference_card(evidence, CARD, model=use_model, record=record,
                                             estimate_id='e', producer_ref='p')
            self.assertEqual(str(caught.exception), code)

    def test_synthetic_end_to_end_reaches_computed_energy(self):
        """Full chain on synthetic records: evidence -> calibrated estimate -> review -> basis -> energy."""
        record, model, _ = fit()
        final = approved(record)
        evidence = rice_evidence()
        estimate = estimate_with_reference_card(evidence, CARD, model=model, record=final,
                                                estimate_id='est-1', producer_ref='reference-card-v0')
        estimate['review'] = {'status': 'accepted', 'reviewer_ref': 'synthetic-portion-reviewer',
                              'reviewed_at': '2026-11-19'}
        basis = {'basis_id': 'basis-1', 'estimate_id': 'est-1', 'ingredient_candidate': 'cooked_rice_region',
                 'purpose': 'photo_calculation',
                 'energy': {'original_name': '熱量', 'unit': 'kcal', 'per': 'per_100g_edible_portion',
                            'value': '100', 'interval': None},  # synthetic test number, not TFDA data
                 'gates': {g: {'status': 'passed', 'evidence_ref': f'test://{g}'}
                           for g in ('source_use', 'identity', 'basis', 'integrity')},
                 'reviewer_ref': 'synthetic-nutrition-reviewer', 'usage': 'diagnostic/test_only'}
        consumption = {'status': 'whole_visible_portion_asserted', 'asserted_by': 'synthetic-user',
                       'evidence_ref': 'test://consumption'}
        result = estimate_photo_energy(ingredient_annotation_envelope([evidence], reason='test'),
                                       estimates=[estimate], calibrations=[final], nutrition_bases=[basis],
                                       consumption=consumption)
        self.assertEqual(result['status'], 'computed')
        self.assertEqual(result['energy'], {'low': estimate['mass_g']['low'], 'high': estimate['mass_g']['high']})
        self.assertFalse(result['nutrition_eligible'])
        self.assertEqual(result['completeness'], 'visible_items_only')


if __name__ == '__main__':
    unittest.main()


class CalibrationToolTests(unittest.TestCase):
    def test_csv_round_trip_and_safe_output(self):
        import csv
        import json
        import tempfile
        from pathlib import Path
        from tools.build_portion_calibration import COLUMNS, load_weighing_csv, write_calibration
        template = Path(__file__).resolve().parents[1] / 'docs/examples/portion_weighing_template.csv'
        self.assertEqual(template.read_text(encoding='utf-8').strip().split(','), list(COLUMNS))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'weighing.csv'
            with path.open('w', encoding='utf-8', newline='') as handle:
                writer = csv.writer(handle)
                writer.writerow(COLUMNS)
                for s in synthetic_samples():
                    writer.writerow([s['sample_id'], s['source_group'], s['ingredient_candidate'], *s['region_px'],
                                     *s['card_px'], s['mass_g'], s['scale_resolution_g'], s['measured_by']])
            samples = load_weighing_csv(path)
            self.assertEqual(len(samples), 36)
            record, model, diagnostics = fit(samples)
            self.assertEqual(record['dataset']['sample_count'], 36)
            work = Path(folder) / 'work'
            out = write_calibration(work / 'cal-v0', record, model, diagnostics, root=work)
            self.assertEqual(json.loads((out / 'calibration_record.json').read_text(encoding='utf-8')), record)
            with self.assertRaises(ValueError):
                write_calibration(work / 'cal-v0', record, model, diagnostics, root=work)
            with self.assertRaises(ValueError):
                write_calibration(Path(folder) / 'elsewhere', record, model, diagnostics, root=work)
            path.write_text('sample_id,mass_g\nx,1\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_weighing_csv(path)
