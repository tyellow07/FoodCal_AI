"""Meal-level evaluation and rubric scoring on synthetic numbers (not food measurements)."""
from copy import deepcopy
from decimal import Decimal
import csv
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from src.evaluation import evaluate_meals, prediction_from_energy_estimate, score_rubric


def references(n=30, abstain=10):
    rows = [{'meal_id': f'm{i}', 'source_group': f'g{i % 3}', 'subset': 'main', 'reference_kcal': str(400 + 10 * i)}
            for i in range(n)]
    rows += [{'meal_id': f'x{i}', 'source_group': 'g0', 'subset': 'should_abstain', 'reference_kcal': ''}
             for i in range(abstain)]
    return rows


def interval(kcal, low_factor, high_factor):
    return {'status': 'computed', 'low_kcal': str(Decimal(kcal) * Decimal(low_factor)),
            'high_kcal': str(Decimal(kcal) * Decimal(high_factor))}


class MealEvaluationTests(unittest.TestCase):
    def test_all_abstain_is_not_accurate(self):
        refs = references()
        metrics = evaluate_meals(refs, [{'meal_id': r['meal_id'], 'status': 'unavailable'} for r in refs])
        self.assertEqual((metrics['answered_meals'], metrics['answer_rate']), (0, 0))
        self.assertIsNone(metrics['mape_midpoint'])
        self.assertEqual(metrics['correct_abstention_rate'], 1)
        points = score_rubric(metrics)
        self.assertEqual((points['1a_mape'], points['3b_answer_rate'], points['3c_correct_abstention']), (0, 0, 5))
        self.assertEqual(points['subtotal'], 5)

    def test_good_intervals_score_full_marks(self):
        refs = references()
        before = deepcopy(refs)
        preds = [{'meal_id': r['meal_id'], **interval(r['reference_kcal'], '0.8', '1.3')}
                 for r in refs if r['subset'] == 'main']
        preds += [{'meal_id': r['meal_id'], 'status': 'unavailable'} for r in refs if r['subset'] != 'main']
        metrics = evaluate_meals(refs, preds)
        self.assertEqual(refs, before)
        self.assertEqual(metrics['mape_midpoint'], Decimal('0.05'))
        self.assertEqual(metrics['interval_coverage'], 1)
        self.assertEqual(metrics['median_relative_width'], Decimal('0.5'))
        self.assertGreater(metrics['baseline_b0_mape'], Decimal('0.05'))
        points = score_rubric(metrics)
        self.assertEqual(points['subtotal'], 35)
        self.assertEqual(points['notes'], [])

    def test_baseline_cap_and_width_tradeoff(self):
        refs = [r for r in references() if r['subset'] == 'main']
        constant = '545'  # median of 400..690
        preds = [{'meal_id': r['meal_id'], 'status': 'computed', 'low_kcal': constant, 'high_kcal': constant}
                 for r in refs]
        metrics = evaluate_meals(refs, preds)
        self.assertEqual(metrics['mape_midpoint'], metrics['baseline_b0_mape'])
        self.assertLessEqual(score_rubric(metrics)['1a_mape'], 4)
        wide = [{'meal_id': r['meal_id'], **interval(r['reference_kcal'], '0', '3')} for r in refs]
        points = score_rubric(evaluate_meals(refs, wide))
        self.assertEqual((points['1b_coverage'], points['1c_width']), (8, 0))

    def test_too_few_answers_zero_item1(self):
        refs = references()
        preds = [{'meal_id': f'm{i}', **interval(str(400 + 10 * i), '0.9', '1.1')} for i in range(9)]
        points = score_rubric(evaluate_meals(refs, preds))
        self.assertEqual(points['1a_mape'] + points['1b_coverage'] + points['1c_width'], 0)
        self.assertTrue(any('item 1 scored 0' in n for n in points['notes']))

    def test_input_validation(self):
        refs = references(3, 0)
        for preds, code in (([{'meal_id': 'nope', 'status': 'unavailable'}], 'prediction_for_unknown_meal'),
                            ([{'meal_id': 'm0', 'status': 'guess'}], 'invalid_prediction_status'),
                            ([{'meal_id': 'm0', 'status': 'computed', 'low_kcal': '5', 'high_kcal': '1'}],
                             'invalid_prediction_interval'),
                            ([{'meal_id': 'm0', 'status': 'computed', 'low_kcal': 'NaN', 'high_kcal': '1'}],
                             'invalid_prediction_interval')):
            with self.assertRaises(ValueError) as caught:
                evaluate_meals(refs, preds)
            self.assertEqual(str(caught.exception), code)
        with self.assertRaises(ValueError):
            evaluate_meals(refs + [refs[0]], [])
        with self.assertRaises(ValueError):
            evaluate_meals([{'meal_id': 'a', 'subset': 'main', 'reference_kcal': '0'}], [])

    def test_prediction_from_energy_estimate(self):
        self.assertEqual(prediction_from_energy_estimate('m1', None)['status'], 'unavailable')
        kj = {'status': 'computed', 'unit': 'kJ', 'energy': {'low': '1', 'high': '2'}}
        self.assertEqual(prediction_from_energy_estimate('m1', kj)['status'], 'unavailable')
        kcal = dict(kj, unit='kcal')
        self.assertEqual(prediction_from_energy_estimate('m1', kcal),
                         {'meal_id': 'm1', 'status': 'computed', 'low_kcal': '1', 'high_kcal': '2'})

    def test_cli_reads_csvs(self):
        from tools.evaluate_meals import main
        with tempfile.TemporaryDirectory() as folder:
            ref_path, pred_path = Path(folder) / 'refs.csv', Path(folder) / 'preds.csv'
            with ref_path.open('w', encoding='utf-8', newline='') as handle:
                writer = csv.DictWriter(handle, ('meal_id', 'source_group', 'subset', 'reference_kcal'))
                writer.writeheader(); writer.writerows(references())
            with pred_path.open('w', encoding='utf-8', newline='') as handle:
                writer = csv.writer(handle)
                writer.writerow(('meal_id', 'status', 'low_kcal', 'high_kcal'))
                writer.writerow(('m0', 'computed', '350', '480'))
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(['--references', str(ref_path), '--predictions', str(pred_path)]), 0)
            self.assertIn('"answered_meals": 1', out.getvalue())


if __name__ == '__main__':
    unittest.main()
