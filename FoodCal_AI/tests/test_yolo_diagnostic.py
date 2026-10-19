"""Mock live-mode integration: never loads model weights or performs YOLO inference."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import yaml

from src.detection import ModelConfigurationError
from src.pipeline.yolo_diagnostic import run_yolo_diagnostic
from src.pipeline.diagnostic_demo import DEFAULT_FIXTURES


class YoloDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'valid/images').mkdir(parents=True)
        (self.root/'valid/labels').mkdir(parents=True)
        self.image = self.root/'valid/images/synthetic.jpg'
        self.image.write_bytes(b'SYNTHETIC mock image; not decoded by a real detector')
        (self.root/'valid/labels/synthetic.txt').write_text('7 0.5 0.5 0.5 0.5\n')
        self.weights = self.root/'mock.pt';self.weights.write_bytes(b'MOCK not actual weights')
        self.classes = self.root/'classes.yaml'
        names = yaml.safe_load((DEFAULT_FIXTURES/'classes.yaml').read_text())['names']
        self.classes.write_text(yaml.safe_dump({'names': names, 'path': str(self.root), 'val': 'valid/images'}))
        self.approval = self.root/'approval.json'
        self.approval.write_text(json.dumps({'status': 'approved_for_single_validation_inference',
            'split': 'validation', 'image_file': self.image.name,
            'image_sha256': sha256(self.image.read_bytes()).hexdigest(), 'authorization_reference': 'test://explicit-approval'}))
        self.detection = json.loads((DEFAULT_FIXTURES/'detection.json').read_text())
        self.detection['image']['source'] = str(self.image)
        self.detection['model'].update(identifier='mock-real-detector-output', checkpoint=str(self.weights))
        for d in self.detection['detections']:d['model_identifier'] = self.detection['model']['identifier']
        self.detector = Mock();self.detector.detect_food.return_value = self.detection

    def call(self, fixture=False):
        with patch('src.pipeline.yolo_diagnostic.FoodDetector', return_value=self.detector) as factory:
            r = run_yolo_diagnostic(self.image, self.approval, fixture_recipes=fixture,
                                    model_path=self.weights, classes_path=self.classes, audit_root=self.root/'audit')
        return r, factory

    def test_one_call_private_audit_no_recipe_and_prediction_unchanged(self):
        before = deepcopy(self.detection)
        r, factory = self.call()
        self.assertEqual(r['status'], 'partial', r['issues'])
        self.detector.detect_food.assert_called_once_with(self.image.resolve())
        self.assertEqual(before, self.detection)
        self.assertEqual(r['detection_snapshot']['detections'], before['detections'])
        self.assertEqual(r['detection_kind'], 'true_model_detection')
        self.assertEqual(r['recipe_kind'], 'unavailable')
        self.assertEqual(r['retrieval_snapshot']['status'], 'unavailable')
        self.assertEqual(r['stages']['parsing']['status'], 'blocked')
        self.assertIsNone(r['photo_portion_grams'])
        self.assertEqual(r['calorie_estimate']['status'], 'unavailable')
        self.assertNotIn(str(self.root), json.dumps(r))
        self.assertTrue((self.root/'audit'/r['private_audit_id']/'detection_raw.json').exists())

    def test_fixture_recipes_explicit_but_not_parsed(self):
        r, _ = self.call(True)
        self.assertEqual(r['recipe_kind'], 'synthetic_fixture')
        self.assertEqual(len(r['objects'][0]['recipe_candidates']), 5)
        self.assertTrue(all(c['parsing_snapshot'] is None for c in r['objects'][0]['recipe_candidates']))

    def test_missing_weights_blocks_before_call(self):
        self.weights.unlink()
        r, factory = self.call()
        self.assertEqual(r['issues'][0]['code'], 'model_missing')
        factory.assert_not_called();self.detector.detect_food.assert_not_called()

    def test_yaml_mismatch_blocks_before_model(self):
        v = yaml.safe_load(self.classes.read_text());v['names'][0] = 'wrong'
        self.classes.write_text(yaml.safe_dump(v))
        r, factory = self.call()
        self.assertEqual(r['issues'][0]['code'], 'taxonomy_mismatch')
        factory.assert_not_called()

    def test_checkpoint_class_mismatch_no_prediction(self):
        with patch('src.pipeline.yolo_diagnostic.FoodDetector', side_effect=ModelConfigurationError('mismatch')):
            r = run_yolo_diagnostic(self.image, self.approval, model_path=self.weights,
                                    classes_path=self.classes, audit_root=self.root/'audit')
        self.assertEqual(r['issues'][0]['code'], 'model_taxonomy_mismatch')
        self.detector.detect_food.assert_not_called()

    def test_unknown_class_does_not_reach_downstream(self):
        self.detection['detections'][0]['class_id'] = 99
        r, _ = self.call()
        self.assertEqual(r['status'], 'failed')
        self.assertEqual(r['issues'][0]['code'], 'invalid_detection_result')
        self.assertIsNone(r['retrieval_snapshot'])

    def test_unapproved_or_wrong_hash_does_not_infer(self):
        v = json.loads(self.approval.read_text());v['image_sha256'] = 'a' * 64
        self.approval.write_text(json.dumps(v))
        r, factory = self.call()
        self.assertEqual(r['issues'][0]['code'], 'image_not_approved')
        factory.assert_not_called()

    def test_non_validation_image_does_not_infer(self):
        other = self.root/'test.jpg';other.write_bytes(self.image.read_bytes());self.image = other
        r, factory = self.call()
        self.assertEqual(r['issues'][0]['code'], 'not_validation_image')
        factory.assert_not_called()

    def test_empty_prediction_is_not_food_absence(self):
        self.detection.update(detections=[], status='no_detections')
        r, _ = self.call()
        self.assertEqual(r['status'], 'no_detections')
        self.assertNotIn('has_food', r)


if __name__ == '__main__':
    unittest.main()
