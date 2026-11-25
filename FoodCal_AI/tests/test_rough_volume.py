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

    def test_container_seen_in_the_photo_wins_over_a_mismatched_key(self):
        as_bowl = rough_volume('braised_napa_cabbage', [0, 0, 1000, 1000], CARD)
        on_plate = rough_volume('braised_napa_cabbage', [0, 0, 1000, 1000], CARD, container='plate')
        self.assertEqual((as_bowl['shape'], on_plate['shape']), ('bowl', 'plate'))

    def test_bowl_food_uses_rim_diameter_depth_and_fill(self):
        volume = rough_volume('beef_noodles', [0, 0, 1600, 1600], CARD)  # 16 cm bowl
        self.assertEqual(volume['assumptions']['rim_diameter_cm'], Decimal('16.0'))
        self.assertEqual((volume['volume_ml']['low'], volume['volume_ml']['high']), (Decimal(392), Decimal(966)))
        with self.assertRaises(ValueError):
            rough_volume('pizza', [0, 0, 10, 10], CARD)

    def test_drink_cup_corrects_the_rim_seen_from_above(self):
        # A 700 mL shop cup (精裕 90U-700: 9 cm rim, 17.7 cm tall) seen from a phone 40 cm up looks 16.1 cm wide at
        # the card's scale; the range must hold what it serves (about 650 mL) without counting that 16 cm rim.
        seen = 9 * 40 / (40 - 17.7)
        volume = rough_volume('bubble_milk_tea', [0, 0, round(seen * 100), round(seen * 100)], CARD)
        self.assertEqual(volume['shape'], 'cup')
        self.assertTrue(volume['volume_ml']['low'] <= 650 <= volume['volume_ml']['high'], volume['volume_ml'])
        self.assertLess(volume['volume_ml']['high'], 1000)  # the uncorrected 16 cm rim would give over 2 L
        on_plate = rough_volume('bubble_milk_tea', [0, 0, 900, 900], CARD, container='plate')
        self.assertEqual(on_plate['shape'], 'plate')  # what the photo shows wins, as for bowls

    def test_whole_item_is_an_ellipsoid_in_its_box(self):
        # A 7.62 cm apple (USDA medium, 3 in) seen from 40 cm: its widest part is 3.8 cm up, so it looks 8.4 cm wide.
        seen = 7.62 * 40 / (40 - 3.81)
        box = [0, 0, round(seen * 100), round(seen * 100)]
        whole = rough_volume('mystery_fruit', box, CARD, container='plate', shape='whole')
        self.assertEqual(whole['shape'], 'whole')
        true_ml = 4 / 3 * 3.14159 * 3.81 ** 3
        self.assertTrue(whole['volume_ml']['low'] <= true_ml <= whole['volume_ml']['high'], whole['volume_ml'])
        flat = rough_volume('mystery_fruit', box, CARD, container='plate')
        self.assertLess(flat['volume_ml']['high'], true_ml)  # the plate layer would miss most of a whole fruit
        cup = rough_volume('bubble_milk_tea', box, CARD, shape='whole')
        self.assertEqual(cup['shape'], 'cup')  # whole applies to plate foods only

    def test_whole_fruit_rows_count_only_the_edible_part(self):
        seen = 7.62 * 40 / (40 - 3.81)
        apple = dict(item(1, 'mystery_fruit', [0, 0, round(seen * 100), round(seen * 100)]), container='plate',
                     shape='whole', components=[('apple', 100)], cooking_method='none')
        rows, total, missing = portion_rows([apple], CARD, {}, {})
        grams = rows[0]['grams']
        self.assertTrue(grams['low'] <= 182 <= grams['high'] + 5, grams)  # USDA 9003: medium apple 182 g edible
        self.assertEqual(rows[0]['nutrition']['density'], Decimal('0.78'))  # 0.95 x (1 - 17.8% core and peel)

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
        # Known dishes use the nutrition model (TFDA white rice 183 kcal / 100 g), not the placeholder table.
        self.assertEqual(rows[0]['kcal'], {'low': Decimal(366), 'mid': Decimal(366), 'high': Decimal(366)})
        self.assertEqual(rows[0]['hidden_kcal'], {'low': 0, 'mid': 0, 'high': 0})
        self.assertEqual(rows[0]['notes'], [])
        self.assertGreater(rows[1]['hidden_kcal']['high'], rows[1]['hidden_kcal']['low'])  # soup oil range
        self.assertEqual(total['mid'], total['visible']['mid'] + total['hidden']['mid'])

    def test_with_a_card_rough_values_and_total(self):
        rows, total, missing = portion_rows(self.items, CARD, self.table, {1: None})
        self.assertEqual(rows[0]['grams_source'], 'rough')
        self.assertEqual(rows[0]['grams']['mid'], (rows[0]['volume_ml']['mid'] * Decimal('0.79')).quantize(Decimal('1')))
        self.assertEqual(total['low'], sum(r['kcal']['low'] + r['hidden_kcal']['low'] for r in rows))
        self.assertEqual(missing, [])
        mystery = dict(item(2, 'mystery_food', [0, 0, 500, 500]), container='plate')
        rows, total, missing = portion_rows(self.items + [mystery], CARD, self.table, {})
        self.assertEqual(rows[2]['notes'], ['density_missing'])
        self.assertIsNone(rows[2]['grams'])
        self.assertIsNone(total)
        self.assertEqual(missing, ['mystery_food'])

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
