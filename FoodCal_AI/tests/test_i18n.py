"""Page languages: complete translations, the fallback order, and a language switch that keeps the visitor's work."""
from io import BytesIO
from pathlib import Path
from string import Formatter
import threading
import unittest

from PIL import Image

from src.ui import theme
from src.ui.explain import FOOD_NAMES, NEXT_STEPS, REASONS, dish_name, food_name, reason_text, summarize
from src.ui.i18n import DEFAULT, LANGUAGES, from_locale, join, language, set_language, t
from src.ui.messages import MESSAGES
from src.ui.portions import NOTES, note_text
from src.portion.nutrition_model import COOKING_METHODS, INGREDIENTS, NUTRIENTS, food_nutrition

try:
    from streamlit.testing.v1 import AppTest
except ImportError:  # optional UI environment
    AppTest = None

# Entries whose Chinese stays in an existing table (messages.py holds the other languages): code -> Chinese text.
TABLES = {'food': FOOD_NAMES, 'ingredient': {key: row[0] for key, row in INGREDIENTS.items()},
          'method': {key: row[0] for key, row in COOKING_METHODS.items()},
          'nutrient': {key: name for key, name, _, _ in NUTRIENTS}, 'note': NOTES, 'reason': REASONS,
          'next': NEXT_STEPS}
PARTIAL = ('reason.', 'dev.')  # Chinese and English only is allowed; the other languages fall back to English
OWN_ENTRIES = ('food.pattern', 'food.unknown', 'reason.gate', 'reason.unknown', 'reason.none')


def fields(text):
    return {name for _, name, _, _ in Formatter().parse(text) if name}


class LanguageScope(unittest.TestCase):
    def tearDown(self):
        set_language(DEFAULT)


class TranslationTableTests(LanguageScope):
    def test_every_text_is_in_every_language(self):
        for key, entry in MESSAGES.items():
            prefix = key.split('.')[0]
            from_table = prefix in TABLES and key not in OWN_ENTRIES
            expected = set(LANGUAGES) - ({DEFAULT} if from_table else set())
            if key.startswith(PARTIAL) and key not in OWN_ENTRIES:
                expected = {'en'} | ({DEFAULT} if not from_table else set())
            self.assertLessEqual(expected, set(entry), key)
            for text in entry.values():  # only a separator may be empty (no space between Chinese sentences)
                self.assertTrue(isinstance(text, str) and (text.strip() or key.startswith('sep.')), key)

    def test_the_page_reasons_and_every_table_row_are_translated(self):
        for prefix, table in TABLES.items():
            for code in table:
                self.assertIn(f'{prefix}.{code}', MESSAGES, prefix)
        page_reasons = ('ingredient_level_semantics_unverified', 'visual_items_unavailable', 'consumption_basis_missing',
                        'no_detections_does_not_establish_ingredient_absence', 'explicit_start_required',
                        'local_model_or_taxonomy_missing')
        for code in page_reasons:  # what the synthetic demo and the upload errors can show
            self.assertEqual(set(MESSAGES['reason.' + code]), set(LANGUAGES) - {DEFAULT}, code)

    def test_placeholders_match_in_every_language(self):
        for key, entry in MESSAGES.items():
            names = {code: fields(text) for code, text in entry.items()}
            self.assertEqual(len({frozenset(v) for v in names.values()}), 1, (key, names))
            prefix, _, code = key.partition('.')
            if prefix in TABLES and key not in OWN_ENTRIES:  # the Chinese in the table has the same placeholders
                self.assertEqual(fields(TABLES[prefix][code]), next(iter(names.values())), key)

    def test_fallback_order_and_unknown_keys(self):
        self.assertEqual(t('btn.start'), '開始辨識 →')  # default language
        set_language('ja')
        self.assertEqual(t('btn.start'), '認識を開始 →')
        self.assertEqual(t('dev.status'), 'Pipeline status:')  # Chinese and English only: English
        self.assertEqual(t('no.such.key'), 'no.such.key')  # visible, never blank
        self.assertEqual(t('nut.weight', grams=120), '約 120 g')
        set_language('xx')  # unknown codes mean the default
        self.assertEqual(language(), DEFAULT)
        self.assertEqual(t('note.card_missing', NOTES['card_missing']), NOTES['card_missing'])  # the table's Chinese
        set_language('en')
        self.assertEqual(join(['a', 'b']), 'a, b')
        set_language('zh-TW')
        self.assertEqual(join(['a', 'b']), 'a、b')

    def test_language_is_per_thread(self):
        set_language('ko')
        seen = []
        worker = threading.Thread(target=lambda: seen.append(language()))
        worker.start()
        worker.join()
        self.assertEqual(seen, [DEFAULT])  # another visitor's run starts in the default language
        self.assertEqual(language(), 'ko')

    def test_browser_languages(self):
        cases = {'ja-JP': 'ja', 'ko': 'ko', 'id-ID': 'id', 'in-ID': 'id', 'vi_VN': 'vi', 'en-US': 'en',
                 'zh-TW': 'zh-TW', 'zh-Hant-HK': 'zh-TW', 'zh-CN': 'zh-TW', 'fr-FR': None, '': None, None: None}
        for locale, expected in cases.items():
            self.assertEqual(from_locale(locale), expected, locale)


class TranslatedTextTests(LanguageScope):
    def test_food_names_keep_the_chinese_name_for_menu_boards(self):
        self.assertEqual(food_name('braised_pork_over_rice'), '滷肉飯（braised_pork_over_rice）')
        set_language('ja')
        self.assertEqual(food_name('braised_pork_over_rice'), 'ルーローファン（滷肉飯）')
        set_language('en')
        self.assertEqual(food_name('braised_pork_over_rice'), 'Braised pork rice (滷肉飯)')
        self.assertEqual(food_name('mystery_dish'), 'mystery_dish')
        self.assertEqual(food_name(None), '(unknown)')

    def test_dish_names_follow_the_page_language(self):
        names = {'zh-TW': '紫米飯', 'en': 'Purple rice', 'ja': '紫米ごはん', 'ko': '흑미밥', 'id': 'Nasi ungu',
                 'vi': 'Cơm gạo lứt tím'}
        item = {'detection_id': 'G1', 'class_name': 'purple_rice', 'display_name': '紫米飯', 'names': names}
        for code, name in names.items():
            set_language(code)
            self.assertEqual(dish_name(item), name)
        old = {'detection_id': 'G2', 'class_name': 'cooked_rice_region', 'display_name': '白飯'}  # no names yet
        set_language('ko')
        self.assertEqual(dish_name(old), '밥 (熟米飯)')
        set_language(DEFAULT)
        self.assertEqual(dish_name(old), '白飯')
        set_language('vi')
        self.assertEqual(dish_name({'detection_id': 'G3', 'class_name': 'greens', 'display_name': '燙青菜',
                                    'names': {'zh-TW': '燙青菜', 'en': 'Greens'}}), 'Greens')
        self.assertEqual(dish_name({'detection_id': 'M1', 'class_name': 'beef_noodles'}), 'Mì bò (牛肉麵)')

    def test_reasons_notes_and_summary(self):
        set_language('id')
        self.assertEqual(reason_text('no_visual_items'), 'Tidak ada bahan di foto yang bisa dihitung.')
        self.assertIn('gate_identity_pending', reason_text('gate_identity_pending'))
        self.assertIn('brand_new_code', reason_text('brand_new_code'))
        self.assertTrue(reason_text('calibration_revoked').startswith('This calibration'))  # English fallback
        self.assertIn('kartu mahasiswa', note_text('card_missing'))
        self.assertEqual(note_text('unknown_note'), 'unknown_note')
        from src.pipeline.diagnostic_demo import run_demo
        summary = summarize(run_demo())
        self.assertEqual(summary['energy_text'], 'Kalori belum bisa diperkirakan')
        self.assertEqual(set(summary['foods'][0]), {'No.', 'Hasil pengenalan', 'Skor model'})
        self.assertIn('Mi dingin (涼麵)', summary['foods'][0]['Hasil pengenalan'])

    def test_theme_texts_and_css_words(self):
        self.assertNotIn("content:'", theme.CSS.replace("content:''", ''))  # every drawn word comes from lang_css
        for name in theme.CSS_TEXTS:
            self.assertIn(f'var({name})', theme.CSS)
        self.assertIn("--fc-t-choose:'選擇照片';", theme.lang_css())
        set_language('ko')
        css = theme.lang_css()
        self.assertIn("--fc-t-drop-title:'식사 사진을 여기로 끌어오세요';", css)
        for name in theme.CSS_TEXTS:
            self.assertEqual(css.count(name + ':'), 1, name)
        self.assertIn('Recipe candidates may explain hidden calories', theme.key_rule())
        self.assertIn('레시피는', theme.key_rule())
        self.assertIn('KEY RULE', theme.key_rule())
        self.assertIn('측면 사진', theme.side_offer())
        set_language('en')
        self.assertNotIn('·', theme.card_head(t('head.plate')))  # English labels are not doubled
        self.assertEqual(theme._css_string("it's"), "'it\\'s'")

    def test_nutrition_panel_in_another_language(self):
        from decimal import Decimal
        from src.portion.nutrition_model import empty_nutrients
        total = {'low': 300, 'mid': 640, 'high': 1380, 'visible': {'mid': 500}, 'hidden': {'mid': 140}}
        macros = {'protein': Decimal('20.0'), 'carb': Decimal('80.0'), 'fat': Decimal('10.0')}
        set_language('vi')
        html = theme.nutrition_panel(total, macros, 'khoảng 400 g', [('Cơm', 500, 140)], nutrients=empty_nutrients())
        for text in ('Ước tính dinh dưỡng', 'Chất đạm', 'Chất bột đường', 'kcal · khoảng 300–1380', 'Natri', 'Tổng'):
            self.assertIn(text, html)
        self.assertNotRegex(html, '[一-鿿]')  # no Chinese left on a Vietnamese page
        mobile = theme.mobile_summary(total, macros, empty_nutrients())
        self.assertIn('Natri', mobile)
        self.assertNotRegex(mobile, '[一-鿿]')


class RecognitionNamesTests(unittest.TestCase):
    def test_gemini_names_dishes_in_every_page_language(self):
        from src.detection.claude_vision import NAME_FIELDS, items_from_answer
        from src.detection.gemini_vision import SCHEMA, instructions, side_instructions
        required = SCHEMA['properties']['foods']['items']['required']
        self.assertEqual({code for code, _ in NAME_FIELDS}, set(LANGUAGES))
        for _, field in NAME_FIELDS:
            self.assertIn(field, required)
        self.assertIn('Use notes, in Korean,', instructions('Korean'))
        self.assertIn('Use notes, in Vietnamese,', side_instructions('Vietnamese'))
        self.assertIn('Use notes, in Traditional Chinese,', instructions())
        food = {'name_zh': '白飯', 'name_en': 'Rice', 'name_ja': 'ごはん', 'name_ko': ' 밥 ', 'name_id': '', 'food_key': 'other',
                'container': 'plate', 'confidence': 'high', 'box': {'x_min': 100, 'y_min': 100, 'x_max': 500, 'y_max': 500}}
        [item] = items_from_answer({'foods': [food]}, 1000, 1000, prefix='G')
        self.assertEqual(item['display_name'], '白飯')
        self.assertEqual(item['names'], {'zh-TW': '白飯', 'en': 'Rice', 'ja': 'ごはん', 'ko': '밥'})  # blanks left out

    def test_nutrition_keeps_the_keys_for_translation(self):
        nutrition = food_nutrition([('cooked_rice', 70), ('egg', 30)], 'pan_fried')
        self.assertEqual(nutrition['part_keys'], ['cooked_rice', 'egg'])
        self.assertEqual(nutrition['method_key'], 'pan_fried')
        self.assertEqual(food_nutrition([('egg', 100)], 'made_up')['method_key'], 'none')

    def test_photo_tags_use_a_font_with_the_letters(self):
        from src.ui.adapter import _draws, label_font
        for text in ('흰쌀밥', 'Cơm trắng, mướp xào', '紫米飯', 'Nasi putih'):
            font, ok = label_font(24, text)
            if not ok:
                self.skipTest('no font on this computer has these letters')
            self.assertTrue(_draws(font.path, text), text)


def image_bytes():
    buffer = BytesIO()
    Image.new('RGB', (640, 480), 'white').save(buffer, format='PNG')
    return buffer.getvalue()


@unittest.skipIf(AppTest is None, 'Optional UI environment: install requirements-ui.txt')
class LanguageSwitchTests(unittest.TestCase):
    APP = str(Path(__file__).resolve().parents[1] / 'src/ui/app.py')

    def photo_app(self, lang=None):
        app = AppTest.from_file(self.APP, default_timeout=15)
        app.session_state['mode'] = 'upload'
        app.session_state['photo'] = {'raw': image_bytes(), 'name': 'lunch.png', 'source': 'upload'}
        if lang:
            app.query_params['lang'] = lang
        return app.run()

    @staticmethod
    def recognised(app):
        from src.ui.adapter import decode_upload
        app.session_state['image'] = decode_upload(app.session_state['photo']['raw'])
        app.session_state['card_box'], app.session_state['card_source'] = [20, 20, 180, 120], 'manual'
        names = {'zh-TW': '白飯', 'en': 'White rice', 'ja': '白ごはん', 'ko': '흰쌀밥', 'id': 'Nasi putih', 'vi': 'Cơm trắng'}
        app.session_state['result'] = {
            'engine': 'gemini', 'card_masked': False, 'model': 'test-model', 'latency_s': 1, 'usage': {}, 'notes': '',
            'items': [{'detection_id': 'G1', 'class_name': 'cooked_rice_region', 'display_name': '白飯', 'names': names,
                       'container': 'plate', 'components': [('cooked_rice', 100)], 'cooking_method': 'none',
                       'confidence': 'high', 'bounding_box': {'x_min': 240, 'y_min': 120, 'x_max': 480, 'y_max': 360}}]}
        return app

    def test_picker_defaults_to_chinese_and_lists_every_language(self):
        app = AppTest.from_file(self.APP, default_timeout=15).run()
        self.assertFalse(app.exception)
        picker = app.selectbox(key='lang')
        self.assertEqual(picker.value, DEFAULT)
        self.assertEqual(list(picker.options), list(LANGUAGES.values()))
        self.assertTrue(any('st-key-fc_lang' in m.value and 'position:fixed' in m.value for m in app.markdown))

    def test_address_picks_the_first_language(self):
        app = self.photo_app('ja')
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key='lang').value, 'ja')
        self.assertTrue(any(b.label == '認識を開始 →' for b in app.button))
        self.assertTrue(any('lunch.png · 真上からの写真' in c.value for c in app.caption))
        self.assertFalse(any(b.label == '開始辨識 →' for b in app.button))

    def test_switch_keeps_photo_grams_and_consent_and_rewords_everything(self):
        app = self.photo_app()
        app.checkbox(key='gemini_consent').check().run()
        self.recognised(app).run()
        tag = str(app.session_state['input_key'][0])[:12]
        app.number_input(key=f'grams_{tag}_G1').set_value(150.0).run()
        app.selectbox(key='lang').set_value('en').run()
        self.assertFalse(app.exception)
        self.assertEqual(app.query_params['lang'], 'en')  # a reload or a shared link keeps the language
        self.assertEqual(app.number_input(key=f'grams_{tag}_G1').value, 150.0)
        self.assertTrue(app.session_state['gemini_consent'])
        self.assertEqual(app.radio[0].value, 'upload')  # keyed, so the new label cannot reset it
        self.assertIn('result', app.session_state)
        self.assertTrue(any(b.label == 'Change photo' for b in app.button))
        self.assertTrue(any('class="fc-name"' in m.value and 'White rice' in m.value for m in app.markdown))
        self.assertTrue(any('class="fc-panel"' in m.value and 'Nutrition' in m.value and '150 g' in m.value
                            for m in app.markdown))
        self.assertTrue(any('high confidence' in m.value for m in app.markdown))
        app.selectbox(key='lang').set_value('ko').run()
        self.assertTrue(any('class="fc-name"' in m.value and '흰쌀밥' in m.value for m in app.markdown))
        self.assertTrue(any('흰밥 100%' in m.value for m in app.markdown))  # the ingredient, in Korean

    def test_errors_are_kept_as_codes_and_reworded(self):
        app = self.photo_app()
        app.session_state['gemini_error'] = 'gemini_quota_exceeded'
        app.run()
        self.assertTrue(any('免費額度' in e.value for e in app.error))
        app.selectbox(key='lang').set_value('vi').run()
        self.assertTrue(any('hạn mức miễn phí' in e.value for e in app.error))
        self.assertFalse(any('免費額度' in e.value for e in app.error))

    def test_demo_mode_in_another_language(self):
        app = AppTest.from_file(self.APP, default_timeout=15)
        app.query_params['lang'] = 'id'
        app.run()
        app.radio[0].set_value('demo').run()
        next(b for b in app.button if b.label == 'Tampilkan hasil demo').click().run()
        self.assertFalse(app.exception)
        self.assertIn('Ringkasan hasil', [s.value for s in app.subheader])
        self.assertTrue(any('Kalori: Kalori belum bisa diperkirakan' in m.value for m in app.markdown))
        self.assertIn('Status porsi tiap item', [e.label for e in app.expander])
        app.selectbox(key='lang').set_value('en').run()
        self.assertEqual(app.radio[0].value, 'demo')  # the switch keeps the mode and the result
        self.assertIn('Summary', [s.value for s in app.subheader])


if __name__ == '__main__':
    unittest.main()
