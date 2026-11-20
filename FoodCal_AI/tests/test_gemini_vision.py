"""Gemini recognizer with fake clients: no network, no API key, synthetic images only."""
from io import BytesIO
import json
from types import SimpleNamespace
import unittest

from google.genai import errors
from PIL import Image

from src.detection.claude_vision import MAX_SIDE, RecognitionError
from src.detection.gemini_vision import MODELS, SCHEMA, mask_card, recognize_foods_gemini


def reply(*foods, notes='', finish='STOP', text=None, block=None):
    body = text if text is not None else json.dumps({'foods': list(foods), 'notes': notes}, ensure_ascii=False)
    return SimpleNamespace(text=body, model_version='gemini-test-flash',
                           prompt_feedback=SimpleNamespace(block_reason=block) if block else None,
                           candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))],
                           usage_metadata=SimpleNamespace(prompt_token_count=1300, candidates_token_count=250,
                                                          thoughts_token_count=400))


def food(name_zh='白飯', name_en='Cooked rice', key='cooked_rice_region', container='plate', box=(200, 100, 700, 600)):
    return {'name_zh': name_zh, 'name_en': name_en, 'food_key': key, 'container': container, 'box_2d': list(box),
            'visible_ingredients': ['白飯'], 'confidence': 'high'}


def client_error(code, message):
    status = {400: 'INVALID_ARGUMENT', 404: 'NOT_FOUND', 429: 'RESOURCE_EXHAUSTED'}.get(code, 'ERROR')
    return errors.ClientError(code, {'error': {'code': code, 'message': message, 'status': status}})


class FakeClient:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []
        self.models = SimpleNamespace(generate_content=self.generate_content)

    def generate_content(self, *, model, contents, config):
        self.calls.append({'model': model, 'contents': contents, 'config': config})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class GeminiVisionTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new('RGB', (4000, 3000), (200, 190, 180))
        self.image.info['exif'] = Image.Exif().tobytes()

    def test_request_shape_and_box_conversion(self):
        client = FakeClient(reply(food(), food(name_zh='燙青菜', name_en='Greens', key='other', box=(0, 800, 300, 1000))))
        result = recognize_foods_gemini(self.image, clients=[('gemini_api', client)])
        [call] = client.calls
        self.assertEqual(call['model'], MODELS[0])
        self.assertEqual((call['config'].response_mime_type, call['config'].response_json_schema),
                         ('application/json', SCHEMA))
        self.assertIn('calories', call['config'].system_instruction)
        part = call['contents'][0]
        self.assertEqual(part.inline_data.mime_type, 'image/jpeg')
        sent = Image.open(BytesIO(part.inline_data.data))
        self.assertEqual(max(sent.size), MAX_SIDE)
        self.assertEqual(len(sent.getexif()), 0)
        first, second = result['items']
        # box_2d is [ymin, xmin, ymax, xmax] on 0-1000
        self.assertEqual(first['bounding_box'], {'x_min': 400.0, 'y_min': 600.0, 'x_max': 2400.0, 'y_max': 2100.0})
        self.assertEqual((first['detection_id'], second['detection_id'], second['class_name']), ('G1', 'G2', 'greens'))
        self.assertEqual((result['model'], result['endpoint'], result['usage']['thinking_tokens']),
                         ('gemini-test-flash', 'gemini_api', 400))

    def test_falls_back_across_model_names_and_endpoints(self):
        missing = FakeClient(client_error(404, 'models/gemini-flash-latest is not found'), reply(food()))
        result = recognize_foods_gemini(self.image, clients=[('gemini_api', missing)])
        self.assertEqual([c['model'] for c in missing.calls], list(MODELS))
        wrong_endpoint = FakeClient(client_error(400, 'API key not valid. Please pass a valid API key.'))
        vertex = FakeClient(reply(food()))
        result = recognize_foods_gemini(self.image, clients=[('gemini_api', wrong_endpoint), ('vertex_express', vertex)])
        self.assertEqual(result['endpoint'], 'vertex_express')
        with self.assertRaises(RecognitionError) as caught:
            recognize_foods_gemini(self.image, clients=[('gemini_api', FakeClient(client_error(400, 'API key not valid')))])
        self.assertEqual(str(caught.exception), 'gemini_key_invalid')

    def test_quota_blocks_and_bad_answers_raise_readable_codes(self):
        for outcome, code in ((client_error(429, 'Resource has been exhausted (e.g. check quota).'), 'gemini_quota_exceeded'),
                              (reply(block='SAFETY'), 'gemini_blocked'),
                              (reply(finish='SAFETY'), 'gemini_blocked'),
                              (reply(finish='MAX_TOKENS'), 'gemini_answer_truncated'),
                              (reply(text='not json'), 'gemini_answer_unreadable'),
                              (reply(text='{"notes": "no foods key"}'), 'gemini_answer_unreadable')):
            with self.assertRaises(RecognitionError) as caught:
                recognize_foods_gemini(self.image, clients=[('gemini_api', FakeClient(outcome))])
            self.assertEqual(str(caught.exception), code)

    def test_malformed_boxes_are_skipped_not_fatal(self):
        bad = dict(food(), box_2d=[1, 2, 3])
        result = recognize_foods_gemini(self.image, clients=[('gemini_api', FakeClient(reply(bad, food())))])
        self.assertEqual([i['detection_id'] for i in result['items']], ['G1'])

    def test_card_is_painted_over_on_a_copy(self):
        photo = Image.new('RGB', (400, 300), (250, 10, 10))
        masked = mask_card(photo, [[50, 50], [200, 50], [200, 145], [50, 145]])
        self.assertEqual(masked.getpixel((100, 100)), (128, 128, 128))
        self.assertEqual(masked.getpixel((300, 200)), (250, 10, 10))
        self.assertEqual(photo.getpixel((100, 100)), (250, 10, 10))


if __name__ == '__main__':
    unittest.main()
