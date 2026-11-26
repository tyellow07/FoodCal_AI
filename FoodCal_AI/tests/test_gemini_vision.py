"""Gemini recognizer with fake clients: no network, no API key, synthetic images only."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from google.genai import errors
from PIL import Image

from src.detection import gemini_vision as gv
from src.detection.claude_vision import MAX_SIDE, RecognitionError
from src.detection.gemini_vision import SCHEMA, mask_card, model_order, recognize_foods_gemini
from src.portion.nutrition_model import INGREDIENTS

MODELS = ('gemini-3.8-flash', 'gemini-3.6-flash', 'gemini-3.5-flash-lite')


def reply(*foods, notes='', finish='STOP', text=None, block=None, dishes=()):
    body = text if text is not None else json.dumps({'dishes': list(dishes), 'foods': list(foods), 'notes': notes},
                                                    ensure_ascii=False)
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


def server_error():
    return errors.ServerError(503, {'error': {'code': 503, 'message': 'high demand', 'status': 'UNAVAILABLE'}})


class FakeClient:
    def __init__(self, *outcomes, listed=()):
        self.outcomes, self.calls, self.listed = list(outcomes), [], list(listed)
        self.models = SimpleNamespace(generate_content=self.generate_content, list=self.list)

    def list(self):
        return [SimpleNamespace(name='models/' + n, supported_actions=['generateContent']) for n in self.listed]

    def generate_content(self, *, model, contents, config):
        self.calls.append({'model': model, 'contents': contents, 'config': config})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def run(client, image, models=MODELS):
    return recognize_foods_gemini(image, clients=[('gemini_api', client)], models=models)


class GeminiVisionTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new('RGB', (4000, 3000), (200, 190, 180))
        self.image.info['exif'] = Image.Exif().tobytes()
        gv._ORDER_CACHE.clear()
        gv._LAST_GOOD.clear()
        self.state = tempfile.TemporaryDirectory()
        self.addCleanup(self.state.cleanup)
        gv.STATE_PATH = Path(self.state.name) / 'gemini_state.json'

    def test_request_shape_and_box_conversion(self):
        client = FakeClient(reply(food(), food(name_zh='燙青菜', name_en='Greens', key='other', box=(0, 800, 300, 1000))))
        result = run(client, self.image)
        [call] = client.calls
        self.assertEqual(call['model'], MODELS[0])
        self.assertEqual((call['config'].response_mime_type, call['config'].response_json_schema),
                         ('application/json', SCHEMA))
        self.assertIn('calories', call['config'].system_instruction)
        self.assertEqual(call['config'].thinking_config.thinking_level.name, 'LOW')
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

    def test_model_order_newest_flash_then_lite(self):
        names = ['models/gemini-2.5-flash', 'models/gemini-3.8-flash', 'models/gemini-3.5-flash-lite',
                 'models/gemini-3.1-flash-image', 'models/gemini-3.6-flash', 'models/gemini-omni-flash-preview',
                 'models/gemini-3.1-flash-lite', 'models/text-embedding']
        self.assertEqual(model_order(names), ('gemini-3.5-flash-lite', 'gemini-3.1-flash-lite',
                                              'gemini-3.8-flash', 'gemini-3.6-flash', 'gemini-2.5-flash'))
        self.assertEqual(model_order([]), gv.MODELS)

    def test_busy_missing_or_used_up_models_fall_through_and_the_winner_goes_first(self):
        listed = ('gemini-3.8-flash', 'gemini-3.6-flash', 'gemini-3.5-flash-lite')
        client = FakeClient(server_error(), client_error(429, 'quota exceeded'), reply(food()), reply(food()),
                            listed=listed)
        result = recognize_foods_gemini(self.image, clients=[('gemini_api', client)])
        # Flash Lite first, then Flash newest first: lite busy, 3.8 used up, 3.6 answers
        self.assertEqual([c['model'] for c in client.calls], ['gemini-3.5-flash-lite', 'gemini-3.8-flash', 'gemini-3.6-flash'])
        self.assertEqual(len(result['items']), 1)
        recognize_foods_gemini(self.image, clients=[('gemini_api', client)])
        self.assertEqual(client.calls[-1]['model'], 'gemini-3.6-flash')  # remembered, tried first
        gv._LAST_GOOD.clear()
        gv._ORDER_CACHE.clear()  # a restarted page reads the winner back from disk
        self.assertEqual(gv.models_for('gemini_api', FakeClient(listed=listed))[0], 'gemini-3.6-flash')
        older = FakeClient(reply(food()))
        run(older, self.image, models=('gemini-2.5-flash',))
        self.assertEqual(older.calls[0]['config'].thinking_config.thinking_budget, 0)

    def test_errors_become_readable_codes(self):
        cases = (([client_error(429, 'Resource has been exhausted (e.g. check quota).')] * 3, 'gemini_quota_exceeded'),
                 ([server_error()] * 3, 'gemini_service_error'),
                 ([client_error(404, 'not found')] * 3, 'gemini_model_unavailable'),
                 ([client_error(400, 'API key not valid. Please pass a valid API key.')], 'gemini_key_invalid'),
                 ([reply(block='SAFETY')], 'gemini_blocked'),
                 ([reply(finish='SAFETY')], 'gemini_blocked'),
                 ([reply(finish='MAX_TOKENS')], 'gemini_answer_truncated'),
                 ([reply(text='not json')], 'gemini_answer_unreadable'),
                 ([reply(text='{"notes": "no foods key"}')], 'gemini_answer_unreadable'))
        for outcomes, code in cases:
            with self.assertRaises(RecognitionError, msg=code) as caught:
                run(FakeClient(*outcomes), self.image)
            self.assertEqual(str(caught.exception), code)

    def test_network_errors_from_either_http_library(self):
        import httpx
        import httpx2
        for error in (httpx2.ConnectError('nodename nor servname provided'), httpx.ConnectError('dns'), ConnectionError('x')):
            with self.assertRaises(RecognitionError) as caught:
                run(FakeClient(error), self.image)
            self.assertEqual(str(caught.exception), 'gemini_network_error')

    def test_total_time_budget_stops_trying(self):
        budget = gv.TOTAL_BUDGET_S
        gv.TOTAL_BUDGET_S = -1
        self.addCleanup(setattr, gv, 'TOTAL_BUDGET_S', budget)
        with self.assertRaises(RecognitionError) as caught:
            run(FakeClient(reply(food())), self.image)
        self.assertEqual(str(caught.exception), 'gemini_too_slow')

    def test_components_and_cooking_method_pass_through(self):
        bowl = dict(food(name_zh='滷肉飯', name_en='Braised pork rice', key='braised_pork_over_rice', container='bowl'),
                    components=[{'ingredient': 'cooked_rice', 'share_percent': 80},
                                {'ingredient': 'braised_pork_mince', 'share_percent': 20},
                                {'ingredient': 'other', 'share_percent': 0}],
                    cooking_method='braised')
        [item] = run(FakeClient(reply(bowl)), self.image)['items']
        self.assertEqual(item['components'], [('cooked_rice', 80), ('braised_pork_mince', 20)])
        self.assertEqual(item['cooking_method'], 'braised')
        self.assertIn('components', SCHEMA['properties']['foods']['items']['required'])

    def test_schemas_stay_small_enough_for_gemini(self):
        """A long enum makes Gemini refuse every request (400 "too much branching"), whatever the photo."""
        def enums(node):
            if isinstance(node, dict):
                if 'enum' in node:
                    yield node['enum']
                for value in node.values():
                    yield from enums(value)
            elif isinstance(node, list):
                for value in node:
                    yield from enums(value)
        for schema in (SCHEMA, gv.SIDE_SCHEMA):
            for values in enums(schema):
                self.assertLessEqual(len(values), gv.MAX_ENUM, values[:5])
        prompt = gv.instructions()
        for key in INGREDIENTS:  # no enum, so every key must be in the instructions instead
            self.assertIn(f'  {key} = ', prompt)

    def test_ingredient_keys_are_checked_on_our_side(self):
        dish = dict(food(name_zh='滷肉飯', name_en='Braised pork rice', key='braised_pork_over_rice', container='bowl'),
                    components=[{'ingredient': 'Cooked Rice', 'share_percent': 70},
                                {'ingredient': 'braised-pork-mince', 'share_percent': 20},
                                {'ingredient': 'unicorn_steak', 'share_percent': 10}, 'not a component'],
                    cooking_method='braised')
        [item] = run(FakeClient(reply(dish)), self.image)['items']
        self.assertEqual(item['components'], [('cooked_rice', 70), ('braised_pork_mince', 20), ('other', 10)])

    def test_ingredients_carry_their_dish_and_its_recipe(self):
        """Each ingredient has its own box; the dish it belongs to (names, recipe) comes along for the page."""
        def dish(number, zh, en, recipe):
            return {'number': number, 'name_zh': zh, 'name_en': en, 'name_ja': '', 'name_ko': '', 'name_id': '',
                    'name_vi': '', 'recipe': recipe}
        dishes = [dish(1, '照燒雞腿飯', 'Teriyaki chicken rice', '雞腿肉煎過後用醬油、味醂、糖收汁，配白飯。'),
                  dish(2, '燙青菜', 'Blanched greens', '青菜燙熟後淋油和醬油膏。'),
                  dish(1, '重複的編號', 'Duplicate', '不該蓋掉第一道'),
                  dish(3, '沒有食材的菜', 'Dish with no food', '這道菜沒有框，不會列出')]
        rice = dict(food(), dish_number=1, components=[{'ingredient': 'cooked_rice', 'share_percent': 100}],
                    cooking_method='none', shape='mound')
        chicken = dict(food(name_zh='照燒雞', name_en='Teriyaki chicken', key='other', box=(300, 300, 600, 700)),
                       dish_number=1, components=[{'ingredient': 'chicken', 'share_percent': 100}],
                       cooking_method='sweet_sauce', shape='flat')
        stray = dict(food(name_zh='不知道哪一道', name_en='Stray', key='other', box=(0, 0, 100, 100)), dish_number=9)
        bad_box = dict(food(name_zh='燙青菜', name_en='Greens', key='other', box=(1, 2, 3)), dish_number=3)
        result = run(FakeClient(reply(rice, chicken, stray, bad_box, dishes=dishes)), self.image)
        first, second, third = result['items']
        self.assertEqual((first['dish']['id'], second['dish']['id']), ('D1', 'D1'))
        self.assertEqual(second['dish']['display_name'], '照燒雞腿飯')
        self.assertEqual(second['dish']['names'], {'zh-TW': '照燒雞腿飯', 'en': 'Teriyaki chicken rice'})
        self.assertIn('收汁', second['dish']['recipe'])
        self.assertEqual((second['display_name'], second['components'], second['cooking_method'], second['shape']),
                         ('照燒雞', [('chicken', 100)], 'sweet_sauce', 'flat'))
        self.assertNotIn('dish', third)  # no such dish: shown on its own
        self.assertEqual([d['id'] for d in result['dishes']], ['D1'])  # only dishes with a food in the list
        prompt = gv.instructions()
        for text in ('照燒雞', '白切雞', 'recipe', 'dish_number', 'Traditional Chinese characters'):
            self.assertIn(text, prompt)
        self.assertEqual(SCHEMA['required'], ['dishes', 'foods', 'notes'])

    def test_malformed_boxes_are_skipped_not_fatal(self):
        bad = dict(food(), box_2d=[1, 2, 3])
        result = run(FakeClient(reply(bad, food())), self.image)
        self.assertEqual([i['detection_id'] for i in result['items']], ['G1'])

    def test_card_is_painted_over_on_a_copy(self):
        photo = Image.new('RGB', (400, 300), (250, 10, 10))
        masked = mask_card(photo, [[50, 50], [200, 50], [200, 145], [50, 145]])
        self.assertEqual(masked.getpixel((100, 100)), (128, 128, 128))
        self.assertEqual(masked.getpixel((300, 200)), (250, 10, 10))
        self.assertEqual(photo.getpixel((100, 100)), (250, 10, 10))


if __name__ == '__main__':
    unittest.main()
