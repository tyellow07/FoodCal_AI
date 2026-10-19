"""No model, dataset, private evidence or service needed."""
from copy import deepcopy
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from src.pipeline.diagnostic_demo import run_demo
from src.recipes import InvalidDetectionResultError
from src.visual_ingredients import VisualIngredientEvidenceAdapter, recipe_assisted_hidden_ingredients
from src.visual_ingredients.adapter import FIXTURE


class VisualIngredientTests(unittest.TestCase):
    def setUp(self):
        self.adapter = VisualIngredientEvidenceAdapter()
        self.detection = run_demo()['detection_snapshot']
        self.fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))

    def test_dish_never_expands_into_ingredients(self):
        original = deepcopy(self.detection)
        result = self.adapter.from_detection(self.detection)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(len(result['visual_ingredient_evidence']), len(original['detections']))
        self.assertTrue(all(e['ingredient_candidate'] is None for e in result['visual_ingredient_evidence']))
        self.assertEqual(result['recipe_assisted_hidden_ingredients'], [])
        self.assertEqual(original, self.detection)

    def test_component_named_classes_still_need_semantic_evidence(self):
        for cls, name in ((0, 'Over-easy egg'), (13, 'loofah'), (7, 'cold_noodle')):
            row = self.detection['detections'][0]
            row.update(class_id=cls, class_name=name)
            result = self.adapter.from_detection(self.detection)
            self.assertEqual(result['visual_ingredient_evidence'][0]['status'], 'unavailable')
            self.assertIsNone(result['visual_ingredient_evidence'][0]['ingredient_candidate'])

    def test_confidence_not_reinterpreted_as_ingredient_score(self):
        evidence = self.adapter.from_detection(self.detection)['visual_ingredient_evidence'][0]
        self.assertIsNone(evidence['confidence'])
        self.assertEqual(evidence['evidence_source']['dish_confidence'], self.detection['detections'][0]['confidence'])

    def test_no_detections_is_not_absence(self):
        self.detection.update(status='no_detections', detections=[])
        result = self.adapter.from_detection(self.detection)
        self.assertEqual(result['visual_ingredient_evidence'], [])
        self.assertIn('does_not_establish', result['reason'])

    def test_invalid_detection_and_class_rejected(self):
        with self.assertRaises(InvalidDetectionResultError):
            self.adapter.from_detection({})
        self.detection['detections'][0]['class_id'] = 99
        with self.assertRaises(InvalidDetectionResultError):
            self.adapter.from_detection(self.detection)

    def test_false_approval_cannot_upgrade_dish(self):
        self.detection['approved'] = True
        self.detection['detections'][0]['ingredient_verified'] = True
        self.assertEqual(self.adapter.from_detection(self.detection)['status'], 'unavailable')

    def test_classification_not_detection_or_segmentation(self):
        result = self.adapter.from_classification([{'food': 'edamame', 'confidence': 0.9}], model_identifier='vit-test')
        row = result['visual_ingredient_evidence'][0]
        self.assertEqual(row['status'], 'unavailable')
        self.assertIsNone(row['image_region'])
        self.assertIsNone(row['ingredient_candidate'])

    def test_invalid_classification_scores(self):
        for score in (True, float('nan'), -1, 1.2, None):
            with self.assertRaises(ValueError):
                self.adapter.from_classification([{'food': 'salad', 'confidence': score}], model_identifier='vit-test')

    def test_fixture_separates_annotation_and_recipe(self):
        with patch('src.detection.model._load_yolo', side_effect=AssertionError('no model')):
            result = self.adapter.fixture_demo()
        visual, hidden = result['visual_ingredient_evidence'], result['recipe_assisted_hidden_ingredients']
        self.assertEqual([v['status'] for v in visual], ['observed_candidate', 'uncertain'])
        self.assertTrue(all(v['evidence_source']['kind'] == 'synthetic_annotation' for v in visual))
        self.assertTrue(all(v['evidence_source']['usage'] == 'synthetic/test_only' for v in visual))
        self.assertEqual([x['ingredient_candidate'] for x in hidden], ['烹調油', '糖'])
        self.assertTrue(all(x['evidence_type'] == 'inferred_from_recipe' and x['status'] == 'uncertain' for x in hidden))
        self.assertFalse(result['nutrition_eligible'])
        self.assertIsNone(result['photo_portion_grams'])
        self.assertEqual(result['calorie_estimate']['status'], 'unavailable')
        json.dumps(result, allow_nan=False)

    def test_recipe_metadata_approval_is_not_visual_observation(self):
        recipe = deepcopy(self.fixture['recipe'])
        recipe['source'].update(kind='production', review_status='approved')
        before = deepcopy(recipe)
        rows = recipe_assisted_hidden_ingredients(recipe, ['糖'])
        self.assertEqual(recipe, before)
        self.assertEqual(rows[0]['source_authentication'], 'not_performed')
        self.assertEqual(rows[0]['status'], 'uncertain')
        self.assertIsNone(rows[0]['quantity'])
        self.assertIsNone(rows[0]['confidence'])
        self.assertIsNone(rows[0]['image_region'])

    def test_no_recipe_source_or_invented_ingredient_rejected(self):
        for recipe, names in (({}, ['糖']), (self.fixture['recipe'], ['芝麻醬']),
                              (self.fixture['recipe'], ['糖', '糖'])):
            with self.assertRaises(ValueError):
                recipe_assisted_hidden_ingredients(recipe, names)

    def test_recipe_quantities_not_copied_to_photo(self):
        self.fixture['recipe']['quantity'] = 100
        rows = recipe_assisted_hidden_ingredients(self.fixture['recipe'], ['糖'])
        self.assertIsNone(rows[0]['quantity'])
        self.assertIsNone(rows[0]['unit'])

    def reject_fixture(self, mutate):
        mutate(self.fixture)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'fixture.json'
            path.write_text(json.dumps(self.fixture), encoding='utf-8')
            with patch('src.visual_ingredients.adapter.FIXTURE', path):
                with self.assertRaises(ValueError):
                    self.adapter.fixture_demo()

    def test_fixture_cannot_claim_production(self):
        self.reject_fixture(lambda f: f.update(usage='production'))

    def test_fixture_invalid_or_duplicate_annotation(self):
        self.reject_fixture(lambda f: f['annotations'].append(deepcopy(f['annotations'][0])))

    def test_fixture_invalid_region(self):
        self.reject_fixture(lambda f: f['annotations'][0]['image_region'].update(x_max=999))

    def test_fixture_invalid_confidence(self):
        self.reject_fixture(lambda f: f['annotations'][0].update(confidence=True))


if __name__ == '__main__':
    unittest.main()
