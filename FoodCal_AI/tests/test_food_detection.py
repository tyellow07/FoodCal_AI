import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
from PIL import Image
import yaml

from src.detection import (FoodDetector, detect_food, get_default_detector,
                           InvalidImageError, ModelConfigurationError, DetectionInferenceError)
from src.detection.model import BASELINE_SETTINGS
DEFAULT_CLASSES_PATH = Path(__file__).resolve().parents[1] / "examples/diagnostic_v0/classes.yaml"


class FakeTensor:
    def __init__(self, rows):
        self.rows = rows

    def cpu(self):
        return self

    def tolist(self):
        return self.rows


class FoodDetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.weights = self.root/'local.pt'
        self.weights.write_bytes(b'test-double checkpoint')
        self.names = yaml.safe_load(DEFAULT_CLASSES_PATH.read_text())['names']
        self.classes = self.root/'classes.yaml'
        self.classes.write_text(yaml.safe_dump({'names': self.names}))
        self.backend = SimpleNamespace(names=dict(enumerate(self.names)), predict=Mock())
        self.factory = Mock(return_value=self.backend)
        self.image = np.zeros((60,80,3), dtype=np.uint8)
        self.set_rows([])

    def set_rows(self, rows):
        self.backend.predict.return_value = [SimpleNamespace(orig_shape=(60,80), boxes=SimpleNamespace(data=FakeTensor(rows)))]

    def detector(self):
        return FoodDetector(self.weights, self.classes, model_factory=self.factory)

    def test_multi_object_mapping_scores_coordinates_and_serialization(self):
        self.set_rows([[1,2,20,30,.35,0],[10,20,70,55,.95,14]])
        result = self.detector().detect_food(self.image)
        self.assertEqual([d['class_name'] for d in result['detections']], ['Over-easy egg','turkey_rice'])
        self.assertEqual(result['detections'][0]['confidence'], .35)
        self.assertEqual(result['detections'][0]['interpretation'], 'candidate')
        self.assertEqual(result['detections'][0]['bounding_box']['x_min'], 1)
        self.assertEqual(result['detections'][1]['model_identifier'],result['model']['identifier'])
        self.assertEqual(result['status'],'detections_available')
        self.assertEqual(json.loads(json.dumps(result)),result)
        for key in ['ingredients','mass_g','calories','nutrition']:
            self.assertNotIn(key,result)

    def test_empty_results_do_not_assert_food_absence(self):
        result = self.detector().detect_food(self.image)
        self.assertEqual(result['status'],'no_detections')
        self.assertEqual(result['detections'],[])
        self.assertNotIn('has_food',result)
        self.assertTrue(any('does not mean' in text for text in result['limitations']))

    def test_lazy_loading_reuse_and_baseline_arguments(self):
        detector = self.detector()
        self.factory.assert_not_called()
        detector.detect_food(self.image)
        detector.detect_food(self.image)
        self.factory.assert_called_once_with(str(self.weights.resolve()))
        self.assertEqual(self.backend.predict.call_count,2)
        self.assertEqual(self.backend.predict.call_args.kwargs,{**BASELINE_SETTINGS,'save':False,'verbose':False})

    def test_convenience_function_reuses_cached_instance(self):
        get_default_detector.cache_clear()
        self.addCleanup(get_default_detector.cache_clear)
        detector = Mock()
        with patch('src.detection.model.FoodDetector',return_value=detector) as factory:
            detect_food(self.image)
            detect_food(self.image)
            self.assertEqual(factory.call_count,1)
            self.assertEqual(detector.detect_food.call_count,2)

    def test_all_15_class_ids_follow_yaml(self):
        self.set_rows([[1,2,20,30,.5,i] for i in range(15)])
        result = self.detector().detect_food(self.image)
        self.assertEqual([d['class_name'] for d in result['detections']],self.names)

    def test_missing_checkpoint_and_configuration(self):
        with self.assertRaises(FileNotFoundError):
            FoodDetector(self.root/'missing.pt',self.classes,model_factory=self.factory)
        with self.assertRaises(FileNotFoundError):
            FoodDetector(self.weights,self.root/'missing.yaml',model_factory=self.factory)
        self.factory.assert_not_called()

    def test_invalid_images_fail_before_model_load(self):
        detector = self.detector()
        broken = self.root/'broken.jpg'
        broken.write_bytes(b'not an image')
        for image in [None,broken,self.root/'missing.jpg','https://example.com/image.jpg',
                      np.zeros((0,80,3),np.uint8),np.zeros((60,80,3),np.float32),np.zeros((60,80),np.uint8)]:
            with self.subTest(image_type=type(image)),self.assertRaises(InvalidImageError):
                detector.detect_food(image)
        self.factory.assert_not_called()

    def test_local_unicode_path_and_pil_to_bgr(self):
        path = self.root/'食物.png'
        ok, encoded = cv2.imencode('.png',self.image)
        self.assertTrue(ok)
        path.write_bytes(encoded.tobytes())
        detector = self.detector()
        self.assertEqual(detector.detect_food(path)['image']['source'],str(path.resolve()))
        detector.detect_food(Image.new('RGB',(80,60),(255,0,0)))
        np.testing.assert_array_equal(self.backend.predict.call_args.args[0][0,0],[0,0,255])

    def test_wrong_yaml_or_checkpoint_mapping_rejected(self):
        self.classes.write_text(yaml.safe_dump({'names':['wrong']}))
        with self.assertRaises(ModelConfigurationError):
            self.detector()
        self.classes.write_text(yaml.safe_dump({'names':self.names}))
        self.backend.names = dict(enumerate(list(reversed(self.names))))
        with self.assertRaises(ModelConfigurationError):
            self.detector().detect_food(self.image)

    def test_backend_failure_and_malformed_results_are_errors_not_empty_detections(self):
        detector = self.detector()
        self.backend.predict.side_effect = RuntimeError('backend failed')
        with self.assertRaises(DetectionInferenceError):
            detector.detect_food(self.image)
        self.backend.predict.side_effect = None
        for rows in [[[1,2,20,30,.5,15]],[[1,2,20,30,float('nan'),0]],
                     [[20,2,1,30,.5,0]],[[1,2,20,30,.1,0]],[[1,2,20,30,.5,1.5]]]:
            self.set_rows(rows)
            with self.subTest(rows=rows),self.assertRaises(DetectionInferenceError):
                detector.detect_food(self.image)
        self.backend.predict.return_value=[]
        with self.assertRaises(DetectionInferenceError):
            detector.detect_food(self.image)


if __name__ == '__main__':
    unittest.main()
