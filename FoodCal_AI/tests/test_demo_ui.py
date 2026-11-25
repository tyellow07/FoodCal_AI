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

    def test_large_phone_photos_are_shrunk_not_rejected(self):
        from src.ui import adapter
        source = Image.new('RGB', (1600, 1200), 'white')
        source.paste((255, 0, 0), (0, 0, 80, 80))
        exif = Image.Exif()
        exif[0x0112] = 6  # stored sideways, like many phone photos
        buffer = BytesIO()
        source.save(buffer, format='JPEG', exif=exif.tobytes(), quality=90)
        # Scaled-down stand-in for a 48 MP photo (8064 x 6048) against the 20 MP limit and 4032 px long side.
        with patch.object(adapter, 'MAX_PIXELS', 1_000_000), patch.object(adapter, 'MAX_LONG_SIDE', 800):
            decoded = decode_upload(buffer.getvalue())
        self.assertEqual(decoded.size, (600, 800))  # upright and shrunk to the long-side limit
        self.assertGreater(decoded.getpixel((decoded.width - 10, 10))[0], 200)  # orientation still applied
        self.assertEqual(len(decoded.getexif()), 0)
        with patch.object(adapter, 'HARD_MAX_PIXELS', 1_000_000):
            with self.assertRaisesRegex(ValueError, 'image_too_large'):
                decode_upload(buffer.getvalue())
        self.assertEqual(decode_upload(image_bytes()).size, (640, 480))  # small photos keep their size

    def test_every_decode_error_has_a_message(self):
        from src.ui.app import PHOTO_ERRORS, error_text, photo_error
        from src.ui.i18n import LANGUAGES, set_language
        self.assertEqual(set(PHOTO_ERRORS), {'invalid_upload_size', 'unsupported_image_signature', 'image_too_large',
                                             'invalid_image_content'})
        self.assertEqual(photo_error(ValueError('something_else')), 'photo_default')
        try:
            for code in LANGUAGES:
                set_language(code)
                for error in PHOTO_ERRORS + ('photo_default',):
                    self.assertNotEqual(error_text(error), error, (code, error))  # worded, not the bare code
                self.assertIn('HEIC', error_text('unsupported_image_signature'))
        finally:
            set_language('zh-TW')

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
        self.assertEqual(app.radio[0].value, 'upload')  # the real-photo flow is the default
        load.assert_not_called()
        self.assertNotIn('result', app.session_state)

    def test_fixture_click_renders_diagnostics(self):
        app = self.app()
        app.radio[0].set_value('demo').run()
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
        app.radio[0].set_value('demo').run()
        next(b for b in app.button if b.label == '顯示展示結果').click().run()
        with patch('src.detection.model._load_yolo') as load:
            app.radio[0].set_value('upload').run()
        self.assertFalse(app.exception)
        load.assert_not_called()
        self.assertNotIn('result', app.session_state)
        self.assertFalse(any(b.label == '開始辨識 →' for b in app.button))  # nothing to start before a photo
        self.assertTrue(any('KEY RULE' in m.value for m in app.markdown))
        self.assertTrue(any('class="fc-tips"' in m.value for m in app.markdown))  # the upload step with its photo tips

    def test_clear_discards_session_results(self):
        app = self.app()
        app.radio[0].set_value('demo').run()
        next(b for b in app.button if b.label == '顯示展示結果').click().run()
        next(b for b in app.button if b.label == '清除本次圖片與結果').click().run()
        self.assertFalse(app.exception)
        self.assertNotIn('result', app.session_state)

    def photo_app(self):
        """The page with a chosen photo, before recognition (the upload box is gone by then)."""
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'src/ui/app.py'), default_timeout=10)
        app.session_state['mode'] = 'upload'
        app.session_state['photo'] = {'raw': image_bytes(), 'name': 'lunch.png', 'source': 'upload'}
        return app.run()

    @staticmethod
    def recognised(app):
        """Session state as a recognition of the chosen photo leaves it: one rice dish and a card box."""
        app.session_state['image'] = decode_upload(app.session_state['photo']['raw'])
        app.session_state['card_box'], app.session_state['card_source'] = [20, 20, 180, 120], 'manual'
        app.session_state['result'] = {
            'engine': 'gemini', 'card_masked': False, 'model': 'test-model', 'latency_s': 1, 'usage': {}, 'notes': '',
            'items': [{'detection_id': 'G1', 'class_name': 'cooked_rice_region', 'display_name': '白飯',
                       'container': 'plate', 'components': [('cooked_rice', 100)], 'cooking_method': 'none',
                       'bounding_box': {'x_min': 240, 'y_min': 120, 'x_max': 480, 'y_max': 360}}]}
        return app

    def test_chosen_photo_waits_for_consent_and_can_be_changed(self):
        app = self.photo_app()
        self.assertFalse(app.exception)
        self.assertTrue(next(b for b in app.button if b.label == '開始辨識 →').disabled)  # no consent yet
        self.assertTrue(any('lunch.png' in c.value and '還沒開始辨識' in c.value for c in app.caption))
        self.assertFalse(any('class="fc-tips"' in m.value for m in app.markdown))  # the upload step is gone
        next(b for b in app.button if b.label == '換一張照片').click().run()
        self.assertFalse(app.exception)
        self.assertNotIn('photo', app.session_state)
        self.assertTrue(any('class="fc-tips"' in m.value for m in app.markdown))
        self.assertFalse(any(b.label == '開始辨識 →' for b in app.button))

    def test_result_shows_dishes_under_photo_and_total_beside_it(self):
        app = self.photo_app()
        app.checkbox(key='gemini_consent').check().run()
        self.recognised(app).run()
        self.assertFalse(app.exception)
        self.assertFalse(any(b.label == '開始辨識 →' for b in app.button))
        self.assertTrue(any(b.label == '換一張照片' for b in app.button))
        self.assertTrue(any('class="fc-kcal"' in m.value for m in app.markdown))  # the nutrition panel has a total
        self.assertTrue(any('class="fc-name"' in m.value and '白飯' in m.value for m in app.markdown))
        app.run()  # the consent checkbox is not drawn any more, but the answer stays for the side photo
        self.assertTrue(app.session_state['gemini_consent'])
        self.assertFalse(any(c.key == 'gemini_consent' for c in app.checkbox))

    @staticmethod
    def side_box(app):
        return [u for u in app.file_uploader if u.label == '側面照（選填）']

    def test_side_photo_can_join_before_the_start_without_opening_anything(self):
        app = self.photo_app()
        self.assertFalse(app.expander)  # nothing a user needs is folded away
        self.assertEqual(len(self.side_box(app)), 1)
        self.assertTrue(any('15–35 度' in c.value for c in app.caption))  # how to take it, in view
        self.side_box(app)[0].set_value(('side.png', image_bytes(), 'image/png')).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['side_photo']['name'], 'side.png')
        self.assertFalse(self.side_box(app))  # the thumbnail replaces the upload box
        self.assertTrue(any('再看側面照' in c.value for c in app.caption))
        app.run()  # the upload box is gone; the photo stays
        self.assertEqual(app.session_state['side_photo']['name'], 'side.png')
        next(b for b in app.button if b.label == '移除側面照').click().run()
        self.assertNotIn('side_photo', app.session_state)
        self.assertEqual(len(self.side_box(app)), 1)

    def test_side_photo_chosen_first_is_analysed_right_after_the_top_photo(self):
        app = self.photo_app()
        app.secrets['GEMINI_API_KEY'] = 'test-key'
        self.side_box(app)[0].set_value(('side.png', image_bytes(), 'image/png')).run()
        app.checkbox(key='gemini_consent').check().run()
        found = {'model': 'test-model', 'latency_s': 1, 'usage': {}, 'notes': '',
                 'items': [{'detection_id': 'G1', 'class_name': 'cooked_rice_region', 'display_name': '白飯',
                            'container': 'plate', 'components': [('cooked_rice', 100)], 'cooking_method': 'none',
                            'bounding_box': {'x_min': 240, 'y_min': 120, 'x_max': 480, 'y_max': 360}}]}
        with patch('src.detection.gemini_vision.recognize_foods_gemini', return_value=found), \
                patch('src.detection.gemini_vision.gemini_clients', return_value=[]), \
                patch('src.portion.card_locator.load_card_reference', return_value=None), \
                patch('src.portion.card_locator.load_card_designs', return_value=[]):
            next(b for b in app.button if b.label == '開始辨識 →').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['result']['items'][0]['display_name'], '白飯')
        self.assertTrue(app.session_state['side']['need_card'])  # no card found in it: waits for a drawn box
        self.assertTrue(any('側面照裡的學生證' in i.value for i in app.info))
        self.assertTrue(next(b for b in app.button if b.label == '用這個框繼續 →').disabled)

    def test_tools_are_buttons_under_the_photo(self):
        app = self.photo_app()
        app.checkbox(key='gemini_consent').check().run()
        self.recognised(app).run()
        self.assertFalse(app.expander)
        self.assertEqual(len(self.side_box(app)), 1)  # the side photo can still be added after recognition
        app.toggle(key='show_boxes').set_value(False).run()
        next(b for b in app.button if b.label == '＋ 加一道菜').click().run()
        self.assertEqual(app.session_state['plate_mode'], 'add')
        self.assertTrue(any(s.label == '哪一道菜' for s in app.selectbox))
        self.assertTrue(next(b for b in app.button if b.label == '加入這道菜').disabled)  # nothing boxed yet
        next(b for b in app.button if b.label == '取消').click().run()
        self.assertIsNone(app.session_state['plate_mode'])
        self.assertFalse(app.toggle(key='show_boxes').value)  # kept while the switch was hidden
        next(b for b in app.button if b.label == '重框學生證').click().run()
        self.assertTrue(any('框出學生證' in m.value for m in app.markdown))
        next(b for b in app.button if b.label == '取消').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any(b.label == '重框學生證' for b in app.button))

    def test_missing_card_points_to_the_button_under_the_photo(self):
        app = self.photo_app()
        app.checkbox(key='gemini_consent').check().run()
        self.recognised(app)
        app.session_state['card_box'], app.session_state['card_source'] = None, None
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(next(b for b in app.button if b.key == 'card_mode').label, '框出學生證')
        self.assertTrue(any('框出學生證' in w.value for w in app.warning))
        self.assertFalse(any('修正工具' in m.value for m in app.markdown))

    def test_hand_added_dish_has_its_own_remove_button(self):
        app = self.photo_app()
        app.checkbox(key='gemini_consent').check().run()
        self.recognised(app)
        app.session_state['manual_items'] = [{'detection_id': 'M1', 'class_name': 'beef_noodles',
                                              'bounding_box': {'x_min': 40, 'y_min': 300, 'x_max': 200, 'y_max': 460}}]
        app.run()
        self.assertTrue(any('（手動加入）' in m.value for m in app.markdown))
        next(b for b in app.button if b.label == '移除').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['manual_items'], [])

    def test_raw_answer_only_with_dev_in_the_address(self):
        app = self.photo_app()
        app.checkbox(key='gemini_consent').check().run()
        self.recognised(app).run()
        self.assertFalse(app.json)
        app.query_params['dev'] = '1'
        app.run()
        self.assertTrue(app.json)


if __name__ == '__main__':
    unittest.main()
