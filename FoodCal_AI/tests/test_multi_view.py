"""Two-photo (top + side) volume: card geometry, height and bowl models, synthetic-scene accuracy, page wiring.

Everything here is synthetic: exact geometry, a pinhole-camera simulator with known true volumes, synthetic card
images and fake Gemini clients. It checks the method's geometry, not its accuracy on real food (that needs weighed
meals, docs/multi_view_volume_v0.md).
"""
import json
import math
from pathlib import Path
import random
import tempfile
import unittest

import cv2
import numpy as np
from PIL import Image

from src.evaluation import multi_view_sim as sim
from src.portion.card_locator import SIDE_SQUASHES, load_card_reference, locate_card
from src.portion.multi_view import (CARD_LONG_CM, CARD_SHORT_CM, footprint_depth, multi_view_volume, peak_height,
                                    plate_extent, side_view_geometry)
from src.portion.rough_volume import rough_volume
from src.ui import theme
from src.ui.portions import portion_rows
from tests.test_card_locator import LOGO_W_CM, card_image, photo_with_card, synthetic_logo
from tests.test_gemini_vision import FakeClient, reply
from tools.setup_card_reference import measure_reference, write_reference

NO_NOISE = {'box_noise': 0.0, 'card_scale_noise': 0.0, 'card_angle_noise_deg': 0.0}


def affine_card(scale, elevation_deg, turn_deg=0.0, lean=0.0, origin=(1000.0, 1500.0)):
    """Card outline TL, TR, BR, BL under the local table->photo map ((X + k Y) / s, -Y sin t / s)."""
    t, p = math.radians(elevation_deg), math.radians(turn_deg)
    out = []
    for x, y in ((-1, 1), (1, 1), (1, -1), (-1, -1)):
        lx, ly = x * CARD_LONG_CM / 2, y * CARD_SHORT_CM / 2
        X, Y = lx * math.cos(p) - ly * math.sin(p), lx * math.sin(p) + ly * math.cos(p)
        out.append([origin[0] + (X + lean * Y) / scale, origin[1] - Y * math.sin(t) / scale])
    return out


class CardGeometryTests(unittest.TestCase):
    def test_scale_and_angle_whatever_the_turn_and_lean(self):
        for elevation, turn, lean in ((25, 0, 0), (25, 20, 0), (40, -30, 0.2), (18, 10, -0.3)):
            g = side_view_geometry(affine_card(0.02, elevation, turn, lean))
            self.assertAlmostEqual(g['elevation_deg'], elevation, places=6)
            self.assertAlmostEqual(g['cm_per_px'], 0.02, places=9)
            self.assertAlmostEqual(g['azimuth_deg'], turn, places=6)

    def test_hand_drawn_box_assumes_the_long_side_faces_the_camera(self):
        g = side_view_geometry([100, 200, 100 + 856, 200 + 260])
        self.assertAlmostEqual(g['cm_per_px'], 0.01)
        self.assertAlmostEqual(g['elevation_deg'], math.degrees(math.asin(2.6 / CARD_SHORT_CM)), places=6)
        self.assertEqual(g['card_source'], 'box')

    def test_top_views_and_slivers_are_refused(self):
        for box in ([0, 0, 856, 540], [0, 0, 856, 460], [0, 0, 856, 50]):  # about 90, 58 and 5 degrees
            with self.assertRaisesRegex(ValueError, 'side_photo_angle_out_of_range'):
                side_view_geometry(box)
        with self.assertRaisesRegex(ValueError, 'invalid_side_card'):
            side_view_geometry('card')


class HeightModelTests(unittest.TestCase):
    def test_peak_height_inverts_the_side_view_extent(self):
        t = math.radians(25)
        for h, power in ((4.0, 2.0), (1.5, 6.0), (6.5, 1.8)):
            extent = plate_extent(h, 5.0, power, t, 42.0)
            low, high = peak_height(extent, 10.0, t, power, 42.0)
            self.assertAlmostEqual(low, h, places=3)
            self.assertEqual(low, high)

    def test_a_dish_too_flat_to_show_its_top_gets_a_range_from_zero(self):
        t = math.radians(30)
        low, high = peak_height(plate_extent(0.0, 5.0, 2.0, t, 42.0), 10.0, t, 2.0, 42.0)
        self.assertEqual(low, 0.0)
        self.assertGreater(high, 0.5)

    def test_footprint_depth_picks_the_extent_along_the_view(self):
        self.assertEqual(footprint_depth(12.0, 6.0, 11.8), (5.4, 6.6))   # side width matches 12 -> depth is 6
        self.assertEqual(footprint_depth(6.0, 12.0, 11.8), (5.4, 6.6))
        low, high = footprint_depth(10.0, 9.5, 9.8)                      # round-ish: the mean, wider
        self.assertTrue(low < 9.75 < high)


class SyntheticSceneTests(unittest.TestCase):
    def test_exact_scenes_contain_the_true_volume(self):
        rng = random.Random(3)
        for kind in ('heap', 'slab', 'bowl'):
            for _ in range(6):
                s = sim.scene(kind, rng, **NO_NOISE)
                _, multi = sim.estimate(s)
                self.assertIsNotNone(multi, kind)
                self.assertTrue(float(multi['low']) <= s['true_ml'] <= float(multi['high']), (kind, s['true_ml'], multi))

    def test_side_photo_beats_the_single_photo_on_noisy_scenes(self):
        for kind, max_mape in (('heap', 0.25), ('slab', 0.40), ('bowl', 0.20)):
            report = sim.benchmark(kind, n=25, seed=21)
            self.assertEqual(report['two_photo_failed'], 0, kind)
            self.assertLess(report['multi']['mape'], report['single']['mape'], kind)
            self.assertLess(report['multi']['mape'], max_mape, kind)
            self.assertGreaterEqual(report['multi']['coverage'], 0.9, kind)

    def test_bowl_heap_and_level_are_read_from_the_side(self):
        rng = random.Random(1)
        cam = sim.side_camera(25, 42)
        card = sim.measured_card((-12.5, 0), 0, cam, rng)
        top_cam = sim.top_camera(40)
        top_card = sim.measured_card((-12.5, 0), 0, top_cam, rng)
        for fill, heap in ((0.7, 0.0), (1.0, 3.0)):
            bowl, food, true_ml = sim.bowl_scene_points(6.5, 0.6, 7.0, 0.5, fill, heap)
            top_box = sim.bbox(sim.project(sim._circle(6.5, 7.0, (0.0, 0.0)), top_cam))
            out = multi_view_volume('beef_noodles', top_box, top_card, sim.bbox(sim.project(food, cam)),
                                    side_view_geometry(card), 'bowl', sim.bbox(sim.project(bowl, cam)))
            volume = out['volume_ml']
            self.assertTrue(volume['low'] <= true_ml <= volume['high'])
            self.assertLess(abs(float(volume['mid']) - true_ml) / true_ml, 0.15)
            self.assertTrue(out['height_cm']['low'] <= 6.5 <= out['height_cm']['high'])

    def test_drink_cup_is_read_like_a_tall_bowl(self):
        rng = random.Random(2)
        cam = sim.side_camera(25, 42)
        card = sim.measured_card((-12.5, 0), 0, cam, rng)
        top_cam = sim.top_camera(40)
        top_card = sim.measured_card((-12.5, 0), 0, top_cam, rng)
        cup, drink, true_ml = sim.bowl_scene_points(4.5, 0.56, 17.7, 0.3, 0.9, 0.0)  # a 700 mL cup, 90% full
        top_box = sim.bbox(sim.project(sim._circle(4.5, 17.7, (0.0, 0.0)), top_cam))
        out = multi_view_volume('bubble_milk_tea', top_box, top_card, sim.bbox(sim.project(drink, cam)),
                                side_view_geometry(card), 'cup', sim.bbox(sim.project(cup, cam)))
        self.assertEqual(out['shape'], 'cup')
        self.assertTrue(out['volume_ml']['low'] <= true_ml <= out['volume_ml']['high'], (true_ml, out['volume_ml']))
        single = rough_volume('bubble_milk_tea', top_box, top_card)['volume_ml']
        self.assertTrue(single['low'] <= true_ml <= single['high'], (true_ml, single))
        side_error = abs(float(out['volume_ml']['mid']) - true_ml) / true_ml
        self.assertLess(side_error, 0.1)  # the measured height and rim width beat the cup-size prior
        self.assertLess(side_error, abs(float(single['mid']) - true_ml) / true_ml)

    def test_whole_fruit_height_comes_from_its_outline(self):
        rng = random.Random(4)
        cam = sim.side_camera(25, 42)
        card = sim.measured_card((-12.5, 0), 0, cam, rng)
        top_cam = sim.top_camera(40)
        top_card = sim.measured_card((-12.5, 0), 0, top_cam, rng)
        r = 3.81  # a 7.62 cm apple resting on the table
        sphere = [(r * math.sin(a) * math.cos(b), r * math.sin(a) * math.sin(b), r + r * math.cos(a))
                  for a in (math.pi * i / 24 for i in range(25)) for b in (math.pi * j / 24 for j in range(48))]
        true_ml = 4 / 3 * math.pi * r ** 3
        out = multi_view_volume('mystery_fruit', sim.bbox(sim.project(sphere, top_cam)), top_card,
                                sim.bbox(sim.project(sphere, cam)), side_view_geometry(card), 'plate', None, 'whole')
        self.assertEqual(out['shape'], 'whole')
        self.assertTrue(out['height_cm']['low'] <= 2 * r <= out['height_cm']['high'], out['height_cm'])
        self.assertTrue(out['volume_ml']['low'] <= true_ml <= out['volume_ml']['high'], (true_ml, out['volume_ml']))

    def test_bowl_needs_its_container_box(self):
        with self.assertRaisesRegex(ValueError, 'side_container_box_missing'):
            multi_view_volume('beef_noodles', [0, 0, 400, 400], [[0, 0], [856, 0], [856, 540], [0, 540]],
                              [0, 0, 300, 100], side_view_geometry([0, 0, 856, 260]), 'bowl')


class SideCardFinderTests(unittest.TestCase):
    def test_squashed_card_in_a_side_photo_is_found(self):
        logo = synthetic_logo()
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / 'data'
            logo_w = round(LOGO_W_CM * 405)
            small = cv2.resize(logo, (logo_w, round(logo_w * logo.shape[0] / logo.shape[1])), interpolation=cv2.INTER_AREA)
            write_reference(data / 'card_reference', small, measure_reference(card_image(logo, 405), small), root=data)
            reference = load_card_reference(data / 'card_reference')
            card = card_image(logo, 60)
            squash = 0.5  # seen from 30 degrees above the table
            squashed = cv2.resize(card, (card.shape[1], round(card.shape[0] * squash)), interpolation=cv2.INTER_AREA)
            photo, corners = photo_with_card(squashed, 0)
            self.assertIsNone(locate_card(photo, reference))  # the top-view search does not expect a squashed logo
            found = locate_card(photo, reference, SIDE_SQUASHES)
            self.assertIsNotNone(found)
            self.assertEqual(found['outline'], 'traced')  # the logo's squash alone is too coarse for the angle
            g = side_view_geometry(found['quad'])
            self.assertAlmostEqual(g['elevation_deg'], 30, delta=4)
            self.assertAlmostEqual(g['cm_per_px'], 1 / 60, delta=0.1 / 60)
            np.testing.assert_allclose(found['quad'], corners, atol=8)


class SideViewWiringTests(unittest.TestCase):
    def test_gemini_side_answer_maps_numbers_to_dishes(self):
        from src.detection.gemini_vision import recognize_side_view
        answer = {'dishes': [{'number': 1, 'food_box_2d': [400, 100, 600, 500], 'container_box_2d': []},
                             {'number': 2, 'food_box_2d': [300, 550, 500, 900], 'container_box_2d': [280, 520, 800, 950]},
                             {'number': 7, 'food_box_2d': [1, 2, 3, 4], 'container_box_2d': []}], 'notes': ''}
        client = FakeClient(reply(text=json.dumps(answer)))
        out = recognize_side_view(Image.new('RGB', (1000, 800), 'white'),
                                  [('G1', '白飯', 'plate'), ('G2', '牛肉麵', 'bowl')],
                                  clients=[('gemini_api', client)], models=('gemini-3.5-flash-lite',))
        self.assertEqual(out['boxes']['G1'], {'food': [100.0, 320.0, 500.0, 480.0], 'container': None})
        self.assertEqual(out['boxes']['G2']['container'], [520.0, 224.0, 950.0, 640.0])
        self.assertNotIn('G7', out['boxes'])
        self.assertIn('02 牛肉麵 (bowl)', client.calls[0]['contents'][1])

    def test_portion_rows_use_the_side_photo_and_keep_the_single_estimate(self):
        rng = random.Random(4)
        s = sim.scene('heap', rng, **NO_NOISE)
        x0, y0, x1, y1 = s['top_box']
        item = {'detection_id': 'G1', 'class_name': 'cooked_rice_region', 'display_name': '白飯', 'container': 'plate',
                'components': [('cooked_rice', 100)], 'cooking_method': 'none',
                'bounding_box': {'x_min': x0, 'y_min': y0, 'x_max': x1, 'y_max': y1}}
        side = {'geometry': side_view_geometry(s['side_card']), 'boxes': {'G1': {'food': s['side_box'], 'container': None}}}
        rows, total, _ = portion_rows([item], s['top_card'], {}, {}, side)
        row = rows[0]
        self.assertEqual(row['volume_method'], 'side')
        self.assertIsNotNone(row['single_volume_ml'])
        self.assertTrue(row['volume_ml']['low'] <= round(s['true_ml']) <= row['volume_ml']['high'])
        self.assertIsNotNone(total)
        html = theme.side_table(rows)
        self.assertIn('只用俯視照', html)
        self.assertIn('白飯', html)
        bad = {'geometry': side['geometry'], 'boxes': {'G1': {'food': [5, 5, 4, 4], 'container': None}}}
        fallback = portion_rows([item], s['top_card'], {}, {}, bad)[0][0]
        self.assertEqual(fallback['volume_method'], 'top')
        self.assertIn('invalid_side_box', fallback['notes'])


if __name__ == '__main__':
    unittest.main()
