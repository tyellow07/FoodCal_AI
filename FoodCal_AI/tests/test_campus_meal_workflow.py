"""Campus meal workflow end to end on synthetic photos, boxes and weights (not food measurements)."""
from decimal import Decimal
import csv
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import random
import tempfile
import unittest

from PIL import Image

from src.evaluation import evaluate_meals
from src.portion import calibration_blockers
from src.portion.reference_card import fit_reference_card_calibration
from tools.build_portion_calibration import approve_calibration, load_weighing_csv
from tools.ingredient_box_edit import write_revision
from tools.ingredient_pilot import SPEC, REFERENCE_CARD_OBJECT
from tools.meal_workspace import (WEIGHING_COLUMNS, build_meal_workspace, export_calibration_samples,
                                  load_manifest, write_csv)
from tools.run_meal_pipeline import apply_reviews, energy_meals, estimate_meals, main as pipeline_main

CARD = [10, 10, 181.2, 117.96]  # 171.2 x 107.96 px: 0.5 mm per pixel, so 1 cm^2 = 400 px^2
SPEC_VERSION = json.loads(SPEC.read_text(encoding='utf-8'))['spec_version']


def write_photo(path, *, rotated=False, fmt='JPEG'):
    image = Image.new('RGB', (600, 800) if rotated else (800, 600), (240, 236, 228))
    if rotated:
        exif = Image.Exif()
        exif[0x0112] = 6  # stored sideways: decoded upright as 800 x 600
        image.save(path, format=fmt, exif=exif.tobytes())
    else:
        image.save(path, format=fmt)


def accepted_annotation(item, boxes, *, card=True):
    annotation = {'pilot_image_id': item['pilot_image_id'], 'image_sha256': item['image_sha256'],
                  'source_id': item['source_id'], 'spec_version': SPEC_VERSION, 'annotator': 'synthetic-annotator',
                  'classes_checked': [0, 1, 2],
                  'independent_review': {'status': 'accepted', 'reviewer': 'synthetic-box-reviewer'},
                  'boxes': [{'annotation_id': annotation_id, 'class_id': class_id, 'bounding_box': box,
                             'granularity': 'visible_instance' if class_id == 0 else 'visible_contiguous_group',
                             'semantic_review_status': 'accepted_for_pilot_annotation', 'visibility': 'clear',
                             'occlusion': 'none', 'truncation': 'none', 'uncertainty_reason': ''}
                            for annotation_id, class_id, box in boxes]}
    if card:
        annotation['reference_card'] = {'object': REFERENCE_CARD_OBJECT, 'bounding_box': list(CARD)}
    return annotation


class CampusMealWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.tmp = Path(self.folder.name)
        self.work = self.tmp / 'work'
        self.photos = self.tmp / 'photos'
        self.photos.mkdir()

    def meals_csv(self, name, rows):
        path = self.tmp / name
        with path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(('meal_id', 'source_group', 'photo_file'))
            writer.writerows(rows)
        return path

    def calibration(self):
        rng = random.Random(3)
        rows, weights = [], []
        for group in ('d1_shuimu', 'd2_fengyun', 'd3_snack'):
            for index in range(12):
                meal = f'c_{group}_{index}'
                write_photo(self.photos / f'{meal}.jpg')
                rows.append((meal, group, f'{meal}.jpg'))
        workspace = self.work / 'calibration_v0'
        manifest = build_meal_workspace(self.photos, self.meals_csv('cal.csv', rows), workspace,
                                        purpose='calibration', root=self.work)
        for item in manifest['items']:
            side = rng.randint(120, 280)  # px; area = side^2 / 400 cm^2
            box = [200, 150, 200 + side, 150 + side]
            write_revision(workspace / 'annotations', accepted_annotation(item, [('rice1', 1, box)]), None)
            ratio = Decimal(str(round(rng.uniform(0.9, 1.3), 4)))
            weights.append((item['pilot_image_id'], 'rice1', str((ratio * side * side / 400).quantize(Decimal('0.1'))),
                            '1', 'synthetic-weigher'))
        weights_path = self.tmp / 'weights.csv'
        with weights_path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(('meal_id', 'annotation_id', 'mass_g', 'scale_resolution_g', 'measured_by'))
            writer.writerows(weights)
        samples_csv = write_csv(self.work / 'weighing.csv', WEIGHING_COLUMNS,
                                export_calibration_samples(workspace, weights_path), root=self.work)
        record, model, diagnostics = fit_reference_card_calibration(
            load_weighing_csv(samples_csv), calibration_id='rice-v0', nominal='0.8',
            dataset_reference='private://campus/calibration_v0', producer_ref='synthetic-calibrator')
        approved, blockers = approve_calibration(record, approver_ref='synthetic-approver', approved_at='2026-11-20')
        return workspace, approved, model, blockers, diagnostics

    def evaluation(self):
        write_photo(self.photos / 'e1.jpg', rotated=True)
        write_photo(self.photos / 'e2.png', fmt='PNG')
        write_photo(self.photos / 'e3.jpg')
        workspace = self.work / 'evaluation_v0'
        manifest = build_meal_workspace(
            self.photos, self.meals_csv('eval.csv', [('e1', 'd4_shuimu', 'e1.jpg'), ('e2', 'd4_shuimu', 'e2.png'),
                                                     ('e3', 'd5_snack', 'e3.jpg')]),
            workspace, purpose='evaluation', root=self.work)
        items = {i['pilot_image_id']: i for i in manifest['items']}
        write_revision(workspace / 'annotations', accepted_annotation(items['e1'], [('r', 1, [300, 200, 500, 400])]), None)
        write_revision(workspace / 'annotations',
                       accepted_annotation(items['e2'], [('r', 1, [300, 200, 500, 400])], card=False), None)
        return workspace, manifest

    def test_build_rules(self):
        workspace, manifest = self.evaluation()
        e1 = next(i for i in manifest['items'] if i['pilot_image_id'] == 'e1')
        self.assertEqual(e1['dimensions'], [800, 600])  # EXIF orientation applied before annotation
        with Image.open(e1['image_path']) as image:
            self.assertEqual(len(image.getexif()), 0)
        self.assertEqual(load_manifest(workspace, purpose='evaluation')['purpose'], 'evaluation')
        bad = [(self.meals_csv('dash.csv', [('e-1', 'g', 'e3.jpg')]), self.work / 'x1', 'meal_ids_must'),
               (self.meals_csv('dup.csv', [('a', 'g', 'e3.jpg'), ('a', 'g', 'e3.jpg')]), self.work / 'x2', 'meal_ids_must'),
               (self.meals_csv('esc.csv', [('a', 'g', '../outside.jpg')]), self.work / 'x3', 'photo_missing'),
               (self.meals_csv('ok.csv', [('a', 'g', 'e3.jpg')]), workspace, 'already_exists'),
               (self.meals_csv('ok2.csv', [('a', 'g', 'e3.jpg')]), self.tmp / 'elsewhere', 'under_ignored_work')]
        for meals, target, code in bad:
            with self.assertRaisesRegex(ValueError, code):
                build_meal_workspace(self.photos, meals, target, purpose='evaluation', root=self.work)
        (self.photos / 'fake.jpg').write_bytes(b'not an image')
        with self.assertRaisesRegex(ValueError, 'jpeg_or_png'):
            build_meal_workspace(self.photos, self.meals_csv('f.csv', [('f', 'g', 'fake.jpg')]), self.work / 'x4',
                                 purpose='evaluation', root=self.work)

    def test_evaluation_meals_can_never_become_calibration_samples(self):
        workspace, _ = self.evaluation()
        weights = self.tmp / 'w.csv'
        weights.write_text('meal_id,annotation_id,mass_g,scale_resolution_g,measured_by\ne1,r,100,1,x\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'workspace_purpose_must_be_calibration'):
            export_calibration_samples(workspace, weights)
        with self.assertRaisesRegex(ValueError, 'workspace_purpose_must_be_evaluation'):
            estimate_meals(self.calibration()[0], record={}, model={})

    def test_end_to_end_from_photos_to_rubric_metrics(self):
        _, record, model, blockers, diagnostics = self.calibration()
        self.assertEqual(diagnostics['samples_evaluated'], 36)
        self.assertEqual(blockers, [], diagnostics)
        self.assertEqual(calibration_blockers(record), [])
        workspace, _ = self.evaluation()
        estimates = estimate_meals(workspace, record=record, model=model)
        meals = estimates['meals']
        self.assertEqual(len(meals['e1']['estimates']), 1)
        self.assertEqual(meals['e2']['skipped'], [{'annotation_id': 'r', 'reason': 'reference_card_missing'}])
        self.assertEqual(meals['e3']['meal_reason'], 'not_annotated')
        estimate_id = meals['e1']['estimates'][0]['estimate_id']
        with self.assertRaisesRegex(ValueError, 'review_not_independent'):
            apply_reviews(estimates, [{'estimate_id': estimate_id, 'status': 'accepted',
                                       'reviewer_ref': 'reference_card_area_ratio/0', 'reviewed_at': '2026-11-21'}])
        reviewed = apply_reviews(estimates, [{'estimate_id': estimate_id, 'status': 'accepted',
                                              'reviewer_ref': 'synthetic-portion-reviewer', 'reviewed_at': '2026-11-21'}])
        self.assertEqual(estimates['meals']['e1']['estimates'][0]['review']['status'], 'not_requested')
        basis = {'basis_id': 'b1', 'estimate_id': estimate_id, 'ingredient_candidate': 'cooked_rice_region',
                 'purpose': 'photo_calculation',
                 'energy': {'original_name': '熱量', 'unit': 'kcal', 'per': 'per_100g_edible_portion',
                            'value': '180', 'interval': None},  # synthetic number, not TFDA data
                 'gates': {g: {'status': 'passed', 'evidence_ref': f'test://{g}'}
                           for g in ('source_use', 'identity', 'basis', 'integrity')},
                 'reviewer_ref': 'synthetic-nutrition-reviewer', 'usage': 'diagnostic/test_only'}
        results, predictions = energy_meals(workspace, estimates=reviewed, record=record, bases=[basis],
                                            consumption_by='synthetic-photographer',
                                            consumption_evidence='test://protocol')
        self.assertEqual(results['e1']['status'], 'computed')
        mass = reviewed['meals']['e1']['estimates'][0]['mass_g']
        self.assertEqual(Decimal(results['e1']['energy']['low']), Decimal(mass['low']) * Decimal('1.8'))
        self.assertEqual(results['e2']['status'], 'unavailable')
        self.assertIn('visual_items_unavailable', results['e2']['reasons'])
        self.assertEqual(results['e3'], {'status': 'unavailable', 'reasons': ['not_annotated']})
        from src.evaluation import audit_results
        self.assertEqual(audit_results(results), {})
        statuses = {p['meal_id']: p['status'] for p in predictions}
        self.assertEqual(statuses, {'e1': 'computed', 'e2': 'unavailable', 'e3': 'unavailable'})
        refs = [{'meal_id': m, 'source_group': 'g', 'subset': 'main', 'reference_kcal': '360'} for m in statuses]
        metrics = evaluate_meals(refs, predictions)
        self.assertEqual((metrics['answered_meals'], metrics['main_meals']), (1, 3))
        # Unreviewed estimates never reach energy, and a different record is refused outright.
        unreviewed, _ = energy_meals(workspace, estimates=estimates, record=record, bases=[basis],
                                     consumption_by='p', consumption_evidence='test://p')
        self.assertEqual(unreviewed['e1']['status'], 'unavailable')
        with self.assertRaisesRegex(ValueError, 'different_calibration_record'):
            energy_meals(workspace, estimates=reviewed, record=dict(record, calibration_id='other'), bases=[basis],
                         consumption_by='p', consumption_evidence='test://p')

    def test_cli_writes_only_new_paths_under_work(self):
        _, record, model, _, _ = self.calibration()
        workspace, _ = self.evaluation()
        record_path, model_path = self.work / 'record.json', self.work / 'model.json'
        record_path.write_text(json.dumps(record), encoding='utf-8')
        model_path.write_text(json.dumps(model), encoding='utf-8')
        args = ['estimate', '--workspace', str(workspace), '--record', str(record_path), '--model', str(model_path)]
        with redirect_stdout(io.StringIO()) as out:
            pipeline_main(args + ['--out', str(self.work / 'run1')], root=self.work)
        self.assertIn('"estimates": 1', out.getvalue())
        template = (self.work / 'run1' / 'review_decisions_template.csv').read_text(encoding='utf-8')
        self.assertTrue(template.startswith('estimate_id,status,reviewer_ref,reviewed_at'))
        for target in (self.work / 'run1', self.tmp / 'outside'):
            with self.assertRaises(ValueError):
                pipeline_main(args + ['--out', str(target)], root=self.work)


if __name__ == '__main__':
    unittest.main()
