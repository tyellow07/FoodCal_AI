"""Rough volume x density for the web page: synthetic boxes and placeholder reference rows, not food data."""
import csv
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from src.portion.rough_volume import (FOOD_TEMPLATES, REFERENCE_COLUMNS, cm_per_px, kcal_for_grams,
                                      load_food_reference, rough_portion, rough_volume)
from src.ui.adapter import draw_predictions
from src.ui.explain import FOOD_NAMES
from src.ui.portions import note_text, portion_rows, range_text, read_food_reference_csv

CARD = [100, 100, 956, 639.8]  # 856 x 539.8 px: exactly 0.01 cm per pixel


def reference_rows():
    """Placeholder numbers with example.org sources, not published values."""
    def row(food, grams='', volume='', kcal=''):
        return {'food': food, 'grams': grams, 'volume_ml': volume,
                'density_source_url': 'https://example.org/d/' + food if grams else '',
                'kcal_per_100g': kcal, 'kcal_source_url': 'https://example.org/k/' + food if kcal else '',
                'retrieved_at': '2026-11-20', 'entered_by': 'synthetic-entry'}
    return [row('cooked_rice_region', '200', '250', '150'), row('beef_noodles', '250', '250', '80'),
            row('cold_noodle', '150', '250')]


def item(detection_id, food, box):
    return {'detection_id': detection_id, 'class_name': food,
            'bounding_box': dict(zip(('x_min', 'y_min', 'x_max', 'y_max'), box))}


class RoughVolumeTests(unittest.TestCase):
    def test_every_class_and_ingredient_has_a_template_and_a_chinese_name(self):
        self.assertEqual(set(FOOD_TEMPLATES), set(FOOD_NAMES))

    def test_card_sets_the_scale(self):
        self.assertEqual(cm_per_px(CARD), Decimal('0.01'))
        for bad, code in (([0, 0, 500, 500], 'card_box_shape_not_a_card'), ([0, 0, 0, 10], 'invalid_card_box'),
                          ([0, 0, 'x', 10], 'invalid_card_box'), ([0, 0, 1], 'invalid_card_box')):
            with self.assertRaises(ValueError) as caught:
                cm_per_px(bad)
            self.assertEqual(str(caught.exception), code)

    def test_plate_food_is_area_times_fill_times_thickness(self):
        volume = rough_volume('cooked_rice_region', [0, 0, 1000, 1000], CARD)  # 10 x 10 cm
        self.assertEqual(volume['shape'], 'plate')
        self.assertEqual(volume['volume_ml'], {'low': Decimal(90), 'mid': Decimal(151), 'high': Decimal(255)})
        self.assertFalse(volume['coverage_validated'])

    def test_bowl_food_uses_rim_diameter_depth_and_fill(self):
        volume = rough_volume('beef_noodles', [0, 0, 1600, 1600], CARD)  # 16 cm bowl
        self.assertEqual(volume['assumptions']['rim_diameter_cm'], Decimal('16.0'))
        self.assertEqual((volume['volume_ml']['low'], volume['volume_ml']['high']), (Decimal(392), Decimal(966)))
        with self.assertRaises(ValueError):
            rough_volume('pizza', [0, 0, 10, 10], CARD)

    def test_reference_table_needs_sources(self):
        table = load_food_reference(reference_rows())
        self.assertEqual(table['cooked_rice_region']['density'], Decimal('0.800'))
        self.assertEqual(table['cooked_rice_region']['kcal_per_100g'], Decimal('150'))
        self.assertIsNone(table['cold_noodle']['kcal_per_100g'])
        def changed(**values):
            rows = reference_rows()
            rows[0].update(values)
            return rows
        for rows in (changed(density_source_url=''), changed(kcal_source_url='example.org'), changed(grams='0'),
                     changed(grams='10', volume_ml='1'), changed(kcal_per_100g='2000'), changed(retrieved_at='today'),
                     changed(entered_by=''), reference_rows() + reference_rows()[:1], [{'food': 'x'}]):
            with self.assertRaises(ValueError, msg=rows):
                load_food_reference(rows)

    def test_grams_and_kcal_follow_volume(self):
        table = load_food_reference(reference_rows())
        portion = rough_portion('cooked_rice_region', [0, 0, 1000, 1000], CARD, table)
        self.assertEqual(portion['grams'], {'low': Decimal(72), 'mid': Decimal(121), 'high': Decimal(204)})
        self.assertEqual((portion['kcal']['low'], portion['kcal']['high']), (Decimal(108), Decimal(306)))
        self.assertIsNone(rough_portion('cold_noodle', [0, 0, 1000, 1000], CARD, table)['kcal'])
        self.assertIsNone(rough_portion('loofah', [0, 0, 1000, 1000], CARD, table)['grams'])
        self.assertEqual(kcal_for_grams(200, table['cooked_rice_region']), Decimal(300))
        self.assertIsNone(kcal_for_grams(200, table['cold_noodle']))


class PortionRowsTests(unittest.TestCase):
    def setUp(self):
        self.table = load_food_reference(reference_rows())
        self.items = [item(1, 'cooked_rice_region', [0, 0, 1000, 1000]), item('M1', 'beef_noodles', [0, 0, 1600, 1600])]

    def test_without_a_card_only_typed_grams_count(self):
        rows, total, missing = portion_rows(self.items, None, self.table, {})
        self.assertEqual([r['notes'] for r in rows], [['card_missing'], ['card_missing']])
        self.assertIsNone(total)
        self.assertEqual(missing, ['熟米飯（cooked_rice_region）', '牛肉麵（beef_noodles）'])
        rows, total, _ = portion_rows(self.items, None, self.table, {1: 200, 'M1': 500.0})
        self.assertEqual(rows[0]['grams_source'], 'user')
        self.assertEqual(rows[0]['kcal'], {'low': Decimal(300), 'mid': Decimal(300), 'high': Decimal(300)})
        self.assertEqual(rows[0]['notes'], [])
        self.assertEqual(total['mid'], Decimal(700))

    def test_with_a_card_rough_values_and_total(self):
        rows, total, missing = portion_rows(self.items, CARD, self.table, {1: None})
        self.assertEqual(rows[0]['grams_source'], 'rough')
        self.assertEqual(total['low'], rows[0]['kcal']['low'] + rows[1]['kcal']['low'])
        self.assertEqual(missing, [])
        rows, total, missing = portion_rows(self.items + [item(2, 'loofah', [0, 0, 500, 500])], CARD, self.table, {})
        self.assertEqual(rows[2]['notes'], ['density_missing'])
        self.assertIsNone(rows[2]['grams'])
        self.assertIsNone(total)
        self.assertEqual(missing, ['絲瓜（loofah）'])

    def test_text_helpers(self):
        self.assertEqual(range_text(None, 'g'), '—')
        self.assertEqual(range_text({'low': 1, 'mid': 1, 'high': 1}, 'g'), '1 g')
        self.assertEqual(range_text({'low': 1, 'mid': 2, 'high': 4}, 'g'), '約 2 g（1–4）')
        self.assertIn('學生證', note_text('card_missing'))

    def test_reference_csv(self):
        template = Path(__file__).resolve().parents[1] / 'docs/examples/food_reference_template.csv'
        self.assertEqual(template.read_text(encoding='utf-8').strip().split(','), list(REFERENCE_COLUMNS))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'food_reference.csv'
            self.assertEqual(read_food_reference_csv(path), {})
            with path.open('w', encoding='utf-8', newline='') as handle:
                writer = csv.DictWriter(handle, REFERENCE_COLUMNS)
                writer.writeheader()
                writer.writerows(reference_rows())
            self.assertEqual(read_food_reference_csv(path), self.table)
            path.write_text('food,grams\nx,1\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                read_food_reference_csv(path)

    def test_result_image_shows_card_and_hand_added_boxes(self):
        image = Image.new('RGB', (400, 300), 'white')
        result = {'detection_snapshot': {'image': {'width': 400, 'height': 300}, 'detections': []}}
        plain = draw_predictions(image, result)
        marked = draw_predictions(image, result, [10, 10, 96, 64],
                                  [item('M1', 'cooked_rice_region', [150, 100, 300, 250])])
        self.assertEqual(plain.tobytes(), image.tobytes())
        self.assertNotEqual(marked.getpixel((10, 40)), (255, 255, 255))
        self.assertNotEqual(marked.getpixel((150, 200)), (255, 255, 255))


if __name__ == '__main__':
    unittest.main()
