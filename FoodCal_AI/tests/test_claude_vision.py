"""Claude vision recognizer with a fake client: no network, no credentials, synthetic images only."""
from base64 import b64decode
from decimal import Decimal
from io import BytesIO
import json
from types import SimpleNamespace
import unittest

from PIL import Image

from src.detection.claude_vision import (FALLBACK_BETA, MAX_SIDE, MODEL, SCHEMA, RecognitionError, items_from_answer,
                                         recognize_foods)
from src.ui.portions import portion_rows

CARD = [100, 100, 956, 639.8]  # 0.01 cm per pixel


def answer(*foods, notes=''):
    return {'foods': list(foods), 'notes': notes}


def food(name_zh='白飯', name_en='Cooked rice', key='cooked_rice_region', container='plate',
         box=(100, 200, 600, 700), confidence='high', ingredients=('白飯',)):
    return {'name_zh': name_zh, 'name_en': name_en, 'food_key': key, 'container': container,
            'box': dict(zip(('x_min', 'y_min', 'x_max', 'y_max'), box)),
            'visible_ingredients': list(ingredients), 'confidence': confidence}


class FakeClient:
    def __init__(self, reply=None, stop_reason='end_turn', error=None):
        self.calls, self.reply, self.stop_reason, self.error = [], reply, stop_reason, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply, ensure_ascii=False)
        return SimpleNamespace(stop_reason=self.stop_reason, model=MODEL, _request_id='req_test',
                               content=[SimpleNamespace(type='thinking', thinking=''), SimpleNamespace(type='text', text=text)],
                               usage=SimpleNamespace(input_tokens=1500, output_tokens=300))


class ClaudeVisionTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new('RGB', (4000, 3000), (200, 190, 180))
        self.image.info['exif'] = Image.Exif().tobytes()

    def test_request_shape_and_metadata_free_downscaled_image(self):
        client = FakeClient(answer(food()))
        result = recognize_foods(self.image, client=client)
        [call] = client.calls
        self.assertEqual(call['model'], 'claude-opus-5-5')
        self.assertEqual((call['betas'], call['fallbacks']), ([FALLBACK_BETA], 'default'))
        self.assertEqual(call['output_config']['format'], {'type': 'json_schema', 'schema': SCHEMA})
        self.assertNotIn('temperature', call)
        image_block = call['messages'][0]['content'][0]
        self.assertEqual(image_block['source']['media_type'], 'image/jpeg')
        sent = Image.open(BytesIO(b64decode(image_block['source']['data'])))
        self.assertEqual(max(sent.size), MAX_SIDE)
        self.assertEqual(len(sent.getexif()), 0)
        self.assertEqual((result['model'], result['request_id'], result['usage']['input_tokens']),
                         (MODEL, 'req_test', 1500))
        self.assertIn('calories', call['system'])  # instructions forbid estimating them

    def test_answer_becomes_page_items_in_original_pixels(self):
        items = items_from_answer(answer(
            food(),
            food(name_zh='燙青菜', name_en='Blanched greens!', key='other', box=(1200, -5, 700, 400)),
            food(name_zh='湯', name_en='Soup', key='other', container='bowl', box=(10, 10, 12, 500))), 4000, 3000)
        self.assertEqual([i['detection_id'] for i in items], ['C1', 'C2'])  # the 2-unit-wide box is dropped
        self.assertEqual(items[0]['class_name'], 'cooked_rice_region')
        self.assertEqual(items[0]['bounding_box'], {'x_min': 400.0, 'y_min': 600.0, 'x_max': 2400.0, 'y_max': 2100.0})
        self.assertEqual((items[1]['class_name'], items[1]['display_name']), ('blanched_greens', '燙青菜'))
        self.assertEqual(items[1]['bounding_box'], {'x_min': 2800.0, 'y_min': 0.0, 'x_max': 4000.0, 'y_max': 1200.0})

    def test_refusal_truncation_and_bad_answers_raise_readable_codes(self):
        for client, code in ((FakeClient(answer(), stop_reason='refusal'), 'claude_refused'),
                             (FakeClient(answer(), stop_reason='max_tokens'), 'claude_answer_truncated'),
                             (FakeClient('not json'), 'claude_answer_unreadable'),
                             (FakeClient(error=TypeError('Could not resolve authentication method.')),
                              'claude_credentials_missing')):
            with self.assertRaises(RecognitionError) as caught:
                recognize_foods(self.image, client=client)
            self.assertEqual(str(caught.exception), code)
        import anthropic
        import httpx2
        offline = FakeClient(error=anthropic.APIConnectionError(request=httpx2.Request('POST', 'https://api.anthropic.com')))
        with self.assertRaises(RecognitionError) as caught:
            recognize_foods(self.image, client=offline)
        self.assertEqual(str(caught.exception), 'claude_network_error')

    def test_claude_items_get_rough_portions_with_generic_templates(self):
        items = items_from_answer(answer(
            food(box=(0, 0, 250, 250)),
            food(name_zh='燙青菜', name_en='Greens', key='other', box=(250, 0, 500, 250)),
            food(name_zh='排骨湯', name_en='Pork rib soup', key='other', container='bowl', box=(500, 0, 900, 400))),
            4000, 4000)
        rows, total, missing = portion_rows(items, CARD, {}, {})
        self.assertEqual([r['name'] for r in rows],
                         ['白飯（cooked_rice_region）', '燙青菜（greens）', '排骨湯（pork_rib_soup）'])
        self.assertTrue(all(r['volume_ml'] is not None for r in rows))
        self.assertGreater(rows[2]['volume_ml']['mid'], rows[1]['volume_ml']['mid'])  # a 16 cm bowl beats a plate pile
        self.assertEqual(rows[1]['notes'], ['density_missing'])
        self.assertIsNone(total)
        rows, total, _ = portion_rows(items, CARD, {}, {'C1': 200})
        self.assertEqual(rows[0]['grams']['mid'], Decimal(200))


if __name__ == '__main__':
    unittest.main()
