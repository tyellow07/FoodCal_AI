"""Stage metrics and red-line audit on synthetic rows and synthetic pipeline outputs."""
from copy import deepcopy
from decimal import Decimal
import unittest

from src.evaluation import (audit_energy_result, audit_results, detection_metrics, detection_rows,
                            nutrition_mapping_metrics, portion_metrics, score_stages)
from src.evaluation.stage_metrics import iou
from src.pipeline.diagnostic_demo import run_demo
from src.portion import estimate_photo_energy
from src.portion.reference_card import estimate_with_reference_card
from src.visual_ingredients import ingredient_annotation_envelope
from tests.test_portion_reference_card import CARD, approved, fit, rice_evidence


def box(image, name, x1, y1, x2, y2, confidence=None):
    row = {'image_id': image, 'class_name': name, 'x_min': x1, 'y_min': y1, 'x_max': x2, 'y_max': y2}
    if confidence is not None:
        row['confidence'] = confidence
    return row


def computed_result():
    record, model, _ = fit()
    final = approved(record)
    evidence = rice_evidence()
    estimate = estimate_with_reference_card(evidence, CARD, model=model, record=final, estimate_id='est-1',
                                            producer_ref='reference-card-v0')
    estimate['review'] = {'status': 'accepted', 'reviewer_ref': 'synthetic-reviewer', 'reviewed_at': '2026-11-19'}
    basis = {'basis_id': 'b1', 'estimate_id': 'est-1', 'ingredient_candidate': 'cooked_rice_region',
             'purpose': 'photo_calculation',
             'energy': {'original_name': '熱量', 'unit': 'kcal', 'per': 'per_100g_edible_portion', 'value': '100',
                        'interval': None},
             'gates': {g: {'status': 'passed', 'evidence_ref': 'test://' + g}
                       for g in ('source_use', 'identity', 'basis', 'integrity')},
             'reviewer_ref': 'synthetic-nutrition-reviewer', 'usage': 'diagnostic/test_only'}
    return estimate_photo_energy(ingredient_annotation_envelope([evidence], reason='test'), estimates=[estimate],
                                 calibrations=[final], nutrition_bases=[basis],
                                 consumption={'status': 'whole_visible_portion_asserted', 'asserted_by': 'u',
                                              'evidence_ref': 'test://c'})


class DetectionMetricTests(unittest.TestCase):
    def test_iou(self):
        a = [Decimal(0), Decimal(0), Decimal(10), Decimal(10)]
        self.assertEqual(iou(a, a), 1)
        self.assertEqual(iou(a, [Decimal(5), Decimal(0), Decimal(15), Decimal(10)]), Decimal(50) / Decimal(150))
        self.assertEqual(iou(a, [Decimal(20), Decimal(20), Decimal(30), Decimal(30)]), 0)

    def test_matching_duplicates_classes_and_taxonomy(self):
        gt = [box('i1', 'beef_noodles', 0, 0, 100, 100), box('i1', 'hamburger', 200, 0, 300, 100),
              box('i2', 'cold_noodle', 0, 0, 50, 50)]
        pred = [box('i1', 'beef_noodles', 2, 2, 100, 100, '0.9'), box('i1', 'beef_noodles', 0, 0, 98, 98, '0.5'),
                box('i2', 'bawan', 0, 0, 50, 50, '0.8')]
        before = deepcopy(gt)
        metrics = detection_metrics(gt, pred, model_classes=['beef_noodles', 'cold_noodle', 'bawan'])
        self.assertEqual(gt, before)
        self.assertEqual((metrics['true_positives'], metrics['predictions'], metrics['ground_truth_items']), (1, 3, 3))
        self.assertEqual(metrics['precision'], Decimal(1) / 3)
        self.assertEqual(metrics['f1'], Decimal(2) * (Decimal(1) / 3) ** 2 / (Decimal(2) / 3))
        self.assertEqual(metrics['taxonomy_coverage'], Decimal(2) / 3)

    def test_no_predictions_is_zero_not_none(self):
        metrics = detection_metrics([box('i', 'a', 0, 0, 1, 1)], [])
        self.assertEqual((metrics['f1'], metrics['recall']), (0, 0))
        with self.assertRaises(ValueError):
            detection_metrics([box('i', 'a', 5, 0, 1, 1)], [])

    def test_detection_rows_from_detector_schema(self):
        rows = detection_rows('img', run_demo()['detection_snapshot'])
        self.assertEqual(rows[0]['class_name'], 'cold_noodle')
        self.assertEqual(rows[0]['x_max'], '210.0')


class PortionAndMappingTests(unittest.TestCase):
    def test_portion_metrics_and_points(self):
        rows = [{'status': 'estimated', 'low_g': '90', 'high_g': '120', 'weighed_g': '100'},
                {'status': 'estimated', 'low_g': '150', 'high_g': '170', 'weighed_g': '200'},
                {'status': 'unavailable', 'low_g': '', 'high_g': '', 'weighed_g': '80'}]
        metrics = portion_metrics(rows)
        self.assertEqual((metrics['answered_items'], metrics['items']), (2, 3))
        self.assertEqual(metrics['mape_midpoint'], (Decimal('0.05') + Decimal('0.2')) / 2)
        self.assertEqual(metrics['interval_coverage'], Decimal('0.5'))
        self.assertEqual(score_stages(portion=metrics)['2b_portion'], 1)

    def test_mapping_within_ten_percent(self):
        rows = [{'system_kcal_per_100g': '183', 'gold_kcal_per_100g': '180'},
                {'system_kcal_per_100g': '350', 'gold_kcal_per_100g': '180'},
                {'system_kcal_per_100g': '', 'gold_kcal_per_100g': '150'}]
        metrics = nutrition_mapping_metrics(rows)
        self.assertEqual(metrics['within_share'], Decimal(1) / 3)
        self.assertEqual(score_stages(mapping=metrics)['2c_mapping'], 0)

    def test_oracle_report_cap(self):
        detection = {'f1': Decimal('0.9')}
        portion = {'mape_midpoint': Decimal('0.1'), 'interval_coverage': Decimal('0.9')}
        mapping = {'within_share': Decimal('0.95')}
        self.assertEqual(score_stages(detection, portion, mapping)['subtotal'], 10)
        self.assertEqual(score_stages(detection, portion, mapping, oracle_report_attached=True)['subtotal'], 15)


class RedLineAuditTests(unittest.TestCase):
    def test_clean_outputs_have_no_violations(self):
        result = computed_result()
        self.assertEqual(result['status'], 'computed')
        self.assertEqual(audit_energy_result(result), [])
        self.assertEqual(audit_energy_result(run_demo()['photo_energy_estimate']), [])
        self.assertEqual(audit_results({'a': result, 'b': {'status': 'unavailable', 'reasons': ['not_annotated']}}), {})

    def test_each_tampering_is_caught(self):
        base = computed_result()
        cases = {
            'R1_total_differs_from_visible_items': lambda r: r['energy'].update(high='99999'),
            'R4_untraceable_item': lambda r: r['items'][0].update(basis_id=None),
            'R2_non_photo_mass_source': lambda r: r['portion_assessment']['items'][0]['method'].update(kind='standard_serving'),
            'R1_recipe_prior_in_visual_items': lambda r: r['portion_assessment']['items'][0]['evidence'].update(
                evidence_type='inferred_from_recipe'),
            'R3_scalar_photo_grams_filled': lambda r: r['portion_assessment'].update(photo_portion_grams=120),
            'eligibility_flag_raised': lambda r: r.update(nutrition_eligible=True),
        }
        for code, tamper in cases.items():
            result = deepcopy(base)
            tamper(result)
            self.assertIn(code, audit_energy_result(result), code)
        unavailable = run_demo()['photo_energy_estimate']
        shown = deepcopy(unavailable); shown.update(energy={'low': '0', 'high': '0'}, unit='kcal')
        self.assertIn('R3_number_shown_when_unavailable', audit_energy_result(shown))
        item = deepcopy(unavailable); item['items'][0]['energy'] = {'low': '1', 'high': '2'}
        self.assertIn('R3_energy_shown_for_unavailable_item', audit_energy_result(item))
        self.assertEqual(audit_results({'x': {'status': 'computed'}}), {'x': ['R3_unknown_result_shape']})


class StageToolTests(unittest.TestCase):
    def test_cli_combines_stage_inputs(self):
        import csv, io, json, tempfile
        from contextlib import redirect_stdout
        from pathlib import Path
        from tools.evaluate_stages import main
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            def write(name, header, rows):
                with (folder / name).open('w', encoding='utf-8', newline='') as handle:
                    writer = csv.writer(handle); writer.writerow(header); writer.writerows(rows)
                return str(folder / name)
            gt = write('gt.csv', ('image_id', 'class_name', 'x_min', 'y_min', 'x_max', 'y_max'),
                       [('i1', 'cold_noodle', 0, 0, 10, 10), ('i1', 'hamburger', 20, 20, 30, 30)])
            pred = write('pred.csv', ('image_id', 'class_name', 'confidence', 'x_min', 'y_min', 'x_max', 'y_max'),
                         [('i1', 'cold_noodle', '0.7', 0, 0, 10, 10)])
            portion = write('p.csv', ('item_id', 'status', 'low_g', 'high_g', 'weighed_g'), [('a', 'estimated', 90, 110, 100)])
            results = folder / 'results.json'
            results.write_text(json.dumps({'m': computed_result()}), encoding='utf-8')
            out = io.StringIO()
            with redirect_stdout(out):
                main(['--detection-gt', gt, '--detection-pred', pred, '--model-classes',
                      str(Path(__file__).resolve().parents[1] / 'examples/diagnostic_v0/classes.yaml'),
                      '--portion', portion, '--audit-results', str(results)])
            report = json.loads(out.getvalue())
            self.assertEqual(Decimal(report['detection']['taxonomy_coverage']), Decimal('0.5'))
            self.assertEqual(report['rubric_points']['2b_portion'], 5)
            self.assertEqual(report['red_line_audit']['meals_with_violations'], {})


if __name__ == '__main__':
    unittest.main()
