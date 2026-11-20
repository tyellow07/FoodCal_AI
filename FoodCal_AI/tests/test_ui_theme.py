"""Page look helpers: workflow steps, nutrition panel and numbered food tags (no Streamlit needed)."""
from decimal import Decimal
import unittest

from PIL import Image

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


if __name__ == '__main__':
    unittest.main()
