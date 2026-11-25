"""Plain-language UI summary: presentation only, never invents or narrows numbers."""
from copy import deepcopy
import unittest

from src.pipeline.diagnostic_demo import run_demo
from src.ui.explain import (DETECTION_CLASSES, FOOD_NAMES, REASONS, food_name, reason_text, summarize,
                            supported_foods_markdown)


class ExplainTests(unittest.TestCase):
    def test_fixture_summary_is_chinese_warning_without_numbers(self):
        result = run_demo()
        before = deepcopy(result)
        summary = summarize(result)
        self.assertEqual(result, before)
        self.assertTrue(summary['synthetic'])
        self.assertEqual(summary['level'], 'warning')
        self.assertEqual(summary['energy_text'], '目前無法估算熱量')
        self.assertIn(REASONS['ingredient_level_semantics_unverified'], summary['reasons'])
        self.assertIn(REASONS['consumption_basis_missing'], summary['reasons'])
        self.assertEqual(len(summary['reasons']), len(set(summary['reasons'])))
        self.assertTrue(summary['next_steps'])
        self.assertEqual([f['編號'] for f in summary['foods']], [1, 2])
        self.assertIn('涼麵', summary['foods'][0]['辨識結果'])
        self.assertNotRegex(summary['energy_text'], r'[0-9]')

    def test_issue_summary_is_error_with_next_step(self):
        summary = summarize({'mode': 'yolo_upload', 'issues': [{'code': 'local_model_or_taxonomy_missing'}],
                             'detection_snapshot': None, 'photo_energy_estimate': None})
        self.assertEqual(summary['level'], 'error')
        self.assertIn(REASONS['local_model_or_taxonomy_missing'], summary['reasons'])
        self.assertTrue(any('合成展示' in step for step in summary['next_steps']))
        self.assertFalse(summary['synthetic'])

    def test_no_detection_is_not_absence(self):
        summary = summarize({'mode': 'yolo_upload', 'issues': [], 'photo_energy_estimate': None,
                             'detection_snapshot': {'status': 'no_detections', 'detections': []}})
        self.assertEqual(summary['headline'], '沒有辨識到食物')
        self.assertIn(REASONS['no_detections_does_not_establish_ingredient_absence'], summary['reasons'])
        energy = {'status': 'unavailable', 'items': [], 'reasons': ['no_visual_items', 'consumption_basis_missing']}
        summary = summarize({'mode': 'yolo_upload', 'issues': [], 'photo_energy_estimate': energy,
                             'detection_snapshot': {'status': 'no_detections', 'detections': []}})
        self.assertEqual(summary['reasons'], [REASONS['no_detections_does_not_establish_ingredient_absence']])

    def test_computed_range_rounds_outward_and_flags_hidden_calories(self):
        energy = {'status': 'computed', 'unit': 'kcal', 'energy': {'low': '512.9', 'high': '690.01'},
                  'completeness': 'incomplete_hidden_ingredients_unquantified', 'coverage_lower_bound': None,
                  'items': [], 'reasons': []}
        summary = summarize({'mode': 'yolo_upload', 'issues': [], 'detection_snapshot': None,
                             'photo_energy_estimate': energy})
        self.assertEqual(summary['level'], 'success')
        self.assertEqual(summary['energy_text'], '約 512–691 kcal（只含照片中看得到的食物）')
        self.assertTrue(any('油' in note for note in summary['notes']))

    def test_kj_is_never_converted(self):
        energy = {'status': 'computed', 'unit': 'kJ', 'energy': {'low': '2000', 'high': '2500'}, 'items': [],
                  'reasons': []}
        summary = summarize({'issues': [], 'detection_snapshot': None, 'photo_energy_estimate': energy})
        self.assertIn('kJ', summary['energy_text'])
        self.assertNotIn('kcal', summary['energy_text'])

    def test_unknown_codes_and_names_fall_back_visibly(self):
        self.assertIn('brand_new_code', reason_text('brand_new_code'))
        self.assertIn('gate_identity_pending', reason_text('gate_identity_pending'))
        self.assertEqual(food_name('mystery_dish'), 'mystery_dish')
        self.assertEqual(len(FOOD_NAMES), 25)  # 15 detection classes + 3 region classes + 7 shop drinks


    def test_supported_foods_match_the_taxonomy_fixture(self):
        import yaml
        from pathlib import Path
        names = yaml.safe_load((Path(__file__).resolve().parents[1] / 'examples/diagnostic_v0/classes.yaml')
                               .read_text(encoding='utf-8'))['names']
        self.assertEqual(list(DETECTION_CLASSES), names)
        text = supported_foods_markdown()
        self.assertTrue(all(FOOD_NAMES[c] in text for c in names))
        self.assertIn('漢堡', text)

    def test_cli_summary_mode_is_plain_chinese(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from src.pipeline.diagnostic_demo import main
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--summary']), 0)
        text = out.getvalue()
        self.assertIn('熱量：目前無法估算熱量', text)
        self.assertIn('涼麵', text)
        self.assertNotIn('{', text)


if __name__ == '__main__':
    unittest.main()
