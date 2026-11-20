"""Pure offline UI adapter tests; optional Streamlit AppTest in UI environment."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from src.ui.adapter import analyze_upload, decode_upload, draw_predictions, fixture_demo, MAX_UPLOAD_BYTES
from src.pipeline.diagnostic_demo import DEFAULT_FIXTURES, run_demo


def image_bytes():
    buffer = BytesIO()
    Image.new('RGB', (640, 480), 'white').save(buffer, format='PNG')
    return buffer.getvalue()


class UIAdapterTests(unittest.TestCase):
    def test_fixture_matches_cli_and_no_model_load(self):
        with patch('src.detection.model._load_yolo') as load:
            result, image = fixture_demo()
            load.assert_not_called()
        self.assertEqual(result, run_demo())
        self.assertEqual(image.size, (640, 480))
        self.assertIsNone(result['photo_portion_grams'])

    def test_explicit_start_required_before_factory(self):
        factory = Mock()
        result, image = analyze_upload(image_bytes(), detector_factory=factory)
        self.assertEqual(result['issues'][0]['code'], 'explicit_start_required')
        factory.assert_not_called()
        self.assertIsNone(image)
        self.assertIsNone(result['photo_energy_estimate'])

    def test_upload_prediction_preserved_and_no_persistent_writes(self):
        detected = run_demo()['detection_snapshot']
        detected['model'].update(checkpoint='C:/PRIVATE/path/best.pt', sha256='a' * 64)
        original = deepcopy(detected)
        detector = Mock(classes_path=DEFAULT_FIXTURES/'classes.yaml')
        detector.detect_food.return_value = detected
        factory = Mock(return_value=detector)
        original_open = Path.open
        writes = []
        def open_path(path, mode='r', *args, **kwargs):
            if any(letter in mode for letter in 'wax+'):
                writes.append(path)
            return original_open(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', open_path):
            result, image = analyze_upload(image_bytes(), enabled=True, detector_factory=factory)
        self.assertEqual(writes, [])
        self.assertEqual(result['status'], 'partial')
        detector.detect_food.assert_called_once()
        self.assertIsInstance(detector.detect_food.call_args.args[0], Image.Image)
        self.assertEqual(detected, original)
        self.assertEqual(result['detection_snapshot']['detections'], detected['detections'])
        self.assertIsNone(result['photo_portion_grams'])
        self.assertEqual(result['calorie_estimate']['status'], 'unavailable')
        self.assertFalse(result['trust']['nutrition_eligible'])
        self.assertNotIn('PRIVATE', str(result))
        self.assertEqual(result['stages']['parsing']['status'], 'blocked')
        energy = result['photo_energy_estimate']
        self.assertEqual((energy['status'], energy['energy']), ('unavailable', None))
        self.assertTrue(all(i['reason'] == 'ingredient_level_semantics_unverified'
                            for i in energy['portion_assessment']['items']))

    def test_fixture_priors_explicit_and_unparsed(self):
        detector = Mock(classes_path=DEFAULT_FIXTURES/'classes.yaml')
        detector.detect_food.return_value = run_demo()['detection_snapshot']
        result, _ = analyze_upload(image_bytes(), enabled=True, fixture_recipes=True,
                                   detector_factory=lambda: detector)
        self.assertEqual(len(result['objects'][0]['recipe_candidates']), 5)
        self.assertTrue(all(c['parsing_snapshot'] is None for c in result['objects'][0]['recipe_candidates']))

    def test_missing_weights_safe_no_paths(self):
        result, image = analyze_upload(image_bytes(), enabled=True,
            detector_factory=Mock(side_effect=FileNotFoundError('C:/PRIVATE/model.pt')))
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['issues'][0]['code'], 'local_model_or_taxonomy_missing')
        self.assertNotIn('PRIVATE', str(result))
        self.assertIsNone(image)

    def test_bad_or_large_upload_not_sent_to_detector(self):
        factory = Mock()
        for data in (b'not an image', b'', b'x'*(MAX_UPLOAD_BYTES+1)):
            result, _ = analyze_upload(data, enabled=True, detector_factory=factory)
            self.assertEqual(result['status'], 'blocked')
        factory.assert_not_called()

    def test_decode_never_calls_global_image_open_fallback(self):
        with patch('PIL.Image.open', side_effect=AssertionError('Must not call HEIF fallback')) as fallback:
            self.assertEqual(decode_upload(image_bytes()).size, (640, 480))
            with self.assertRaises(ValueError):
                decode_upload(b'invalid bytes')
            with self.assertRaises(ValueError):
                decode_upload(b'\x89PNG\r\n\x1a\ncorrupt')
            fallback.assert_not_called()

    def test_phone_exif_orientation_applied_then_metadata_dropped(self):
        source = Image.new('RGB', (400, 300), 'white')
        source.paste((255, 0, 0), (0, 0, 40, 40))  # red marker at stored top-left
        exif = Image.Exif()
        exif[0x0112] = 6  # stored sideways; viewers rotate 90 degrees clockwise
        buffer = BytesIO()
        source.save(buffer, format='JPEG', exif=exif.tobytes(), quality=95)
        decoded = decode_upload(buffer.getvalue())
        self.assertEqual(decoded.size, (300, 400))
        red = decoded.getpixel((decoded.width - 10, 10))  # top-left moves to top-right
        self.assertGreater(red[0], 200)
        self.assertLess(red[1], 80)
        self.assertNotIn('exif', decoded.info)
        self.assertEqual(len(decoded.getexif()), 0)

    def test_overlay_only_predictions_not_gt(self):
        result, image = fixture_demo()
        result['evaluation'] = {'gt': [{'box': [0, 0, 640, 480]}]}
        before = image.copy()
        rendered = draw_predictions(image, result)
        self.assertEqual(image.tobytes(), before.tobytes())
        self.assertEqual(rendered.getpixel((0, 0)), image.getpixel((0, 0)))
        self.assertNotEqual(rendered.tobytes(), image.tobytes())

    def test_overlay_rejects_wrong_coordinates(self):
        result, image = fixture_demo()
        with self.assertRaises(ValueError):
            draw_predictions(Image.new('RGB', (10, 10)), result)

    def test_overlapping_boxes_get_separate_labels(self):
        from PIL import ImageDraw, ImageFont
        from src.ui.adapter import label_layout
        result, image = fixture_demo()  # the two synthetic boxes overlap almost completely
        detections = result['detection_snapshot']['detections']
        layout = label_layout(ImageDraw.Draw(image.copy()), ImageFont.load_default(size=16), image.size, detections)
        (first_text, a), (second_text, b) = layout
        self.assertEqual((first_text, second_text), ('#1 cold_noodle 0.80', '#2 turkey_rice 0.80'))
        self.assertFalse(a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])
        self.assertTrue(all(0 <= r[1] and r[3] <= image.height for r in (a, b)))
        draw_predictions(image, result)  # still renders

    def test_label_has_dark_background_white_text_and_stays_at_top_edge(self):
        result, image = fixture_demo()
        result['detection_snapshot']['detections'] = result['detection_snapshot']['detections'][:1]
        result['detection_snapshot']['detections'][0]['bounding_box'].update(
            x_min=620, y_min=0, x_max=639, y_max=100)
        before = deepcopy(result)
        rendered = draw_predictions(Image.new('RGB', image.size, 'white'), result)
        self.assertEqual(result, before)
        self.assertEqual(rendered.getpixel((639, 0)), (23, 23, 23))
        self.assertTrue(any(rendered.getpixel((x, y)) != (23, 23, 23)
                            for x in range(500, 635) for y in range(4, 20)))

    def test_unknown_class_blocked_before_display(self):
        detector = Mock(classes_path=DEFAULT_FIXTURES/'classes.yaml')
        detection = run_demo()['detection_snapshot'];detection['detections'][0]['class_id'] = 99
        detector.detect_food.return_value = detection
        result, _ = analyze_upload(image_bytes(), enabled=True, detector_factory=lambda: detector)
        self.assertEqual(result['status'], 'blocked')
        self.assertIsNone(result['detection_snapshot'])


try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest = None


@unittest.skipIf(AppTest is None, 'Optional UI environment: install requirements-ui.txt')
class StreamlitOfflineTests(unittest.TestCase):
    def app(self):
        return AppTest.from_file(str(Path(__file__).resolve().parents[1]/'src/ui/app.py'), default_timeout=10).run()

    def test_default_idle_no_model(self):
        with patch('src.detection.model._load_yolo') as load:
            app = self.app()
        self.assertFalse(app.exception)
        self.assertEqual(app.radio[0].value, '上傳照片辨識')  # the real-photo flow is the default
        load.assert_not_called()
        self.assertNotIn('result', app.session_state)

    def test_fixture_click_renders_diagnostics(self):
        app = self.app()
        app.radio[0].set_value('合成展示（不需模型）').run()
        next(b for b in app.button if b.label == '顯示展示結果').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['result']['mode'], 'fixture_offline')
        self.assertTrue(app.warning)
        self.assertTrue(app.json)
        self.assertIn('各項目的份量狀態', [e.label for e in app.expander])
        self.assertIn('結果摘要', [s.value for s in app.subheader])
        self.assertTrue(any('熱量：目前無法估算熱量' in m.value for m in app.markdown))
        self.assertTrue(any('整道菜' in m.value for m in app.markdown))

    def test_switch_to_yolo_does_not_infer_and_clears_fixture(self):
        app = self.app()
        app.radio[0].set_value('合成展示（不需模型）').run()
        next(b for b in app.button if b.label == '顯示展示結果').click().run()
        with patch('src.detection.model._load_yolo') as load:
            app.radio[0].set_value('上傳照片辨識').run()
        self.assertFalse(app.exception)
        load.assert_not_called()
        self.assertNotIn('result', app.session_state)
        self.assertTrue(next(b for b in app.button if b.label == '開始辨識 →').disabled)
        self.assertTrue(any('KEY RULE' in m.value for m in app.markdown))
        self.assertTrue(any('Nutrition' in m.value for m in app.markdown))

    def test_clear_discards_session_results(self):
        app = self.app()
        app.radio[0].set_value('合成展示（不需模型）').run()
        next(b for b in app.button if b.label == '顯示展示結果').click().run()
        next(b for b in app.button if b.label == '清除本次圖片與結果').click().run()
        self.assertFalse(app.exception)
        self.assertNotIn('result', app.session_state)


if __name__ == '__main__':
    unittest.main()
