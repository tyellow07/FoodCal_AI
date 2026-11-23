"""Page look helpers: workflow steps, nutrition panel and numbered food tags (no Streamlit needed)."""
from decimal import Decimal
import unittest

from PIL import Image

from src.portion.nutrition_model import empty_nutrients
from src.ui import theme
from src.ui.adapter import draw_items


class ThemeTests(unittest.TestCase):
    def test_steps_mark_finished_cards(self):
        html = theme.steps(2)
        self.assertEqual(html.count('fc-step done'), 2)
        self.assertEqual(html.count('class="fc-step"') + html.count('class="fc-step done"'), 5)
        self.assertIn('KEY RULE', theme.key_rule())

    def test_empty_and_filled_nutrition_panel(self):
        self.assertIn('請先拍照', theme.nutrition_panel(None, None, '', [], '請先拍照'))
        total = {'low': Decimal(300), 'mid': Decimal(480), 'high': Decimal(900),
                 'visible': {'low': 280, 'mid': 440, 'high': 800}, 'hidden': {'low': 20, 'mid': 40, 'high': 100}}
        html = theme.nutrition_panel(total, {'protein': Decimal('30.0'), 'carb': Decimal('55.0'), 'fat': Decimal('12.5')},
                                     '約 400 g', [('<b>白飯</b>', 300, 0), ('炒青菜', 140, 40)])
        for text in ('480', '300–900', '蛋白質', '30.0 g', '約 400 g', '440', '>40<', '&lt;b&gt;白飯&lt;/b&gt;'):
            self.assertIn(text, html)
        self.assertNotIn('<b>白飯</b>', html)  # names from the vision answer are escaped
        self.assertNotIn('其他營養素', html)  # no nutrient list unless nutrients are given

    def test_nutrient_list_shows_recipe_part_and_lower_bounds(self):
        nutrients = empty_nutrients()
        nutrients['sodium_mg'] = {'visible': Decimal('120'), 'hidden': Decimal('300'), 'hidden_low': Decimal('200'),
                                  'hidden_high': Decimal('400'), 'partial': False}
        nutrients['fiber_g'] = {**nutrients['fiber_g'], 'visible': Decimal('2.5'), 'partial': True}
        total = {'low': 1, 'mid': 2, 'high': 3, 'visible': {'mid': 2}, 'hidden': {'mid': 0}}
        html = theme.nutrition_panel(total, {'protein': 1, 'carb': 1, 'fat': 1}, '約 1 g', [], nutrients=nutrients)
        for text in ('其他營養素', '飽和脂肪', '膳食纖維', '膽固醇', '鉀', '鈣', '鐵', '420 mg', '其中調味約 300 mg（200–400，食譜估計',
                     '≥ 2.5 g', '「≥」'):
            self.assertIn(text, html)
        self.assertNotIn('「≥」', theme.nutrient_list(empty_nutrients()))

    def test_mobile_summary_and_phone_css(self):
        self.assertEqual(theme.mobile_summary(None, None), '')
        nutrients = empty_nutrients()
        nutrients['sodium_mg'] = {**nutrients['sodium_mg'], 'visible': Decimal('300'), 'hidden': Decimal('600'),
                                  'partial': True}
        total = {'low': 300, 'mid': 640, 'high': 1380}
        html = theme.mobile_summary(total, {'protein': Decimal('47.0'), 'carb': Decimal('47.8'), 'fat': Decimal('28.4')},
                                    nutrients)
        for text in ('fc-mobile-summary', '640', '300–1380', '47.0 g', '28.4 g', '≥ 900 mg'):
            self.assertIn(text, html)
        self.assertNotIn('鈉', theme.mobile_summary(total, {'protein': 1, 'carb': 1, 'fat': 1}))
        css = theme.CSS
        self.assertIn('@media (max-width: 640px)', css)
        self.assertIn('.st-key-fc_mobile_summary { display:none !important; }', css)  # hidden on wide screens
        self.assertIn('font-size:16px', css)  # iOS Safari zooms into inputs smaller than 16 px
        self.assertEqual(css.count('.fc-sub {'), 1)  # the nutrient heading has its own class

    def test_item_header_escapes_and_numbers(self):
        html = theme.item_header(3, '<i>雞</i>', 'meta', '＋ 油')
        self.assertIn('03', html)
        self.assertIn('&lt;i&gt;', html)


class DrawItemsTests(unittest.TestCase):
    def test_tags_boxes_and_card(self):
        image = Image.new('RGB', (800, 600), 'white')
        items = [{'detection_id': 'G1', 'display_name': '紫米飯',
                  'bounding_box': {'x_min': 100, 'y_min': 150, 'x_max': 400, 'y_max': 450}}]
        card = [[500, 400], [700, 400], [700, 520], [500, 520]]
        drawn = draw_items(image, items, card)
        self.assertNotEqual(drawn.getpixel((100, 300)), (255, 255, 255))  # box edge
        self.assertNotEqual(drawn.getpixel((600, 400)), (255, 255, 255))  # card edge
        hidden = draw_items(image, items, card, show_boxes=False)
        self.assertEqual(hidden.getpixel((100, 300)), (255, 255, 255))
        self.assertNotEqual(hidden.getpixel((600, 400)), (255, 255, 255))
        self.assertEqual(image.getpixel((100, 300)), (255, 255, 255))  # the original is untouched

    def test_food_tags_do_not_cover_each_other(self):
        from PIL import ImageDraw
        from src.ui.adapter import label_font, tag_layout
        image = Image.new('RGB', (800, 600), 'white')
        items = [{'detection_id': f'G{i}', 'display_name': name,
                  'bounding_box': {'x_min': x, 'y_min': 40, 'x_max': x + 300, 'y_max': 400}}
                 for i, (name, x) in enumerate((('炒高麗菜', 240), ('醬燒雞肉', 330), ('白斬雞', 790)), 1)]
        font, has_cjk = label_font(32)
        tags = tag_layout(ImageDraw.Draw(image), font, items, image.size, 4, has_cjk)
        rects = [rect for _, rect, _ in tags]
        for i, a in enumerate(rects):
            self.assertTrue(0 <= a[0] and a[2] <= 800 and 0 <= a[1] and a[3] <= 600)  # inside the photo
            for b in rects[i + 1:]:
                self.assertFalse(a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3], (a, b))
        self.assertEqual([text[:2] for text, _, _ in tags], ['01', '02', '03'])


if __name__ == '__main__':
    unittest.main()
