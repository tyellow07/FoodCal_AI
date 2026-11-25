"""The chain of ways to find the student card (card_locator.find_card): synthetic card backs and photos only."""
import math
from pathlib import Path
import random
import tempfile
import unittest

import cv2
import numpy as np

from src.portion.card_locator import (CARD_H_CM, CARD_W_CM, DESIGN_WIDTH_PX, FEATURE_REGIONS, NUMBER_STRIP_TOP, find_card,
                                      find_card_shape, load_card_designs, order_card_corners)
from src.portion.multi_view import side_view_geometry
from src.portion.rough_volume import cm_per_px
from tests.test_card_locator import card_image, synthetic_logo
from tools.setup_card_reference import measure_reference, write_design, write_reference
from src.portion.card_locator import load_card_reference

DESIGN_H = round(DESIGN_WIDTH_PX * CARD_H_CM / CARD_W_CM)
CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ0123456789#%&@'


def scribble(card, rng, box):
    """Random print (letters, digits, small shapes) inside box = (x0, y0, x1, y1) in pixels of a grey card."""
    x0, y0, x1, y1 = box
    y = y0 + 22
    while y < y1 - 4:
        x = x0 + rng.randint(4, 20)
        while x < x1 - 40:
            text = ''.join(rng.choice(CHARS) for _ in range(rng.randint(2, 6)))
            scale = rng.uniform(0.45, 0.8)
            cv2.putText(card, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, rng.randint(20, 90), rng.choice((1, 2)),
                        cv2.LINE_AA)
            x += int(cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)[0][0]) + rng.randint(10, 30)
            if rng.random() < 0.15:
                cv2.circle(card, (min(x, x1 - 10), y - 8), rng.randint(3, 8), rng.randint(20, 90), -1)
        y += rng.randint(26, 40)


def design(seed, keep=None, keep_box=None):
    """A grey card back DESIGN_WIDTH_PX wide full of unique print; keep_box from keep stays as it is (same print)."""
    rng = random.Random(seed)
    card = np.full((DESIGN_H, DESIGN_WIDTH_PX), 226, np.uint8)
    scribble(card, rng, (10, 0, DESIGN_WIDTH_PX - 10, int(NUMBER_STRIP_TOP * DESIGN_H)))
    if keep is not None:
        x0, y0, x1, y1 = (int(v * s) for v, s in zip(keep_box, (DESIGN_WIDTH_PX, DESIGN_H) * 2))
        card[y0:y1, x0:x1] = keep[y0:y1, x0:x1]
    return card


def food_background(size=(2000, 1500), seed=7):
    """A 'table with food' photo: blobs of colour and some texture, nothing light and card-shaped."""
    rng = np.random.default_rng(seed)
    photo = np.full((size[1], size[0], 3), (95, 80, 70), np.uint8)
    for _ in range(140):
        centre = (int(rng.integers(0, size[0])), int(rng.integers(0, size[1])))
        axes = (int(rng.integers(10, 90)), int(rng.integers(10, 90)))
        colour = tuple(int(v) for v in rng.integers((60, 40, 0), (200, 170, 90)))
        cv2.ellipse(photo, centre, axes, float(rng.integers(0, 180)), 0, 360, colour, -1)
    noise = rng.integers(-18, 18, photo.shape)
    return np.clip(photo.astype(int) + noise, 0, 255).astype(np.uint8)


def place(card_gray, photo_rgb, corners):
    """Warp a card (grey or BGR) onto the photo so its corners (TL, TR, BR, BL) land on corners; returns the photo."""
    card = cv2.cvtColor(card_gray, cv2.COLOR_GRAY2RGB) if card_gray.ndim == 2 else cv2.cvtColor(card_gray, cv2.COLOR_BGR2RGB)
    h, w = card.shape[:2]
    matrix = cv2.getPerspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]), np.float32(corners))
    size = photo_rgb.shape[1::-1]
    warped = cv2.warpPerspective(card, matrix, size, flags=cv2.INTER_AREA)
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), matrix, size)
    out = photo_rgb.copy()
    out[mask > 0] = warped[mask > 0]
    return out


def rotated_corners(centre, width_px, angle_deg, squash=1.0):
    """Card corners TL, TR, BR, BL for a card width_px wide, turned clockwise on screen, squashed vertically."""
    theta = math.radians(angle_deg)
    rot = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
    half = np.array([width_px, width_px * CARD_H_CM / CARD_W_CM]) / 2
    local = np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]]) * half
    return [list(np.array(centre) + (rot @ p) * (1, squash)) for p in local]


class CardFinderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.data = Path(cls.folder.name) / 'data'
        cls.card = design(1)
        bgr = cv2.cvtColor(cls.card, cv2.COLOR_GRAY2BGR)
        bgr[int(NUMBER_STRIP_TOP * DESIGN_H) + 8:, 120:600] = 30  # a 'card number', to be blanked by the setup
        cls.design_path = write_design(cls.data / 'card_reference', bgr, root=cls.data)
        cls.designs = load_card_designs(cls.data / 'card_reference')
        cls.background = food_background()

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def assert_quad(self, found, corners, method, tolerance=0.02):
        self.assertIsNotNone(found)
        self.assertEqual(found['method'], method)
        width = np.linalg.norm(np.subtract(corners[1], corners[0]))
        for got, expected in zip(found['quad'], corners):
            self.assertLess(np.linalg.norm(np.subtract(got, expected)), tolerance * width, (found['quad'], corners))

    def test_setup_blanks_the_card_number(self):
        stored = cv2.imread(str(self.design_path), cv2.IMREAD_GRAYSCALE)
        self.assertEqual(stored.shape, (DESIGN_H, DESIGN_WIDTH_PX))
        strip = stored[int(NUMBER_STRIP_TOP * DESIGN_H):]
        self.assertEqual(int(strip.max()), int(strip.min()))
        with self.assertRaises(ValueError):
            write_design(Path(self.folder.name) / 'elsewhere', cv2.cvtColor(self.card, cv2.COLOR_GRAY2BGR),
                         root=self.data)
        with self.assertRaises(ValueError):
            write_design(self.data / 'x', np.full((500, 500, 3), 200, np.uint8), root=self.data)  # not a card crop
        self.assertEqual(load_card_designs(Path(self.folder.name) / 'nothing'), [])

    def test_whole_back_at_any_turn_and_tilt(self):
        for angle in (0, 37, 90, 180, -120):
            corners = rotated_corners((1000, 760), 620, angle)
            photo = place(self.card, self.background, corners)
            self.assert_quad(find_card(photo, designs=self.designs), corners, 'card_back')
        tilted = [[640, 520], [1330, 560], [1390, 1010], [590, 980]]  # held at an angle: a trapezoid
        found = find_card(place(self.card, self.background, tilted), designs=self.designs)
        self.assert_quad(found, tilted, 'card_back')
        self.assertAlmostEqual(float(cm_per_px(found['quad'])), float(cm_per_px(tilted)), delta=0.02 * float(cm_per_px(tilted)))

    def test_another_year_matched_by_a_part_that_stayed(self):
        """Only the top rules stay: the top text finds it; only the EasyCard block stays: that block finds it."""
        for keep_box, method in (((0.0, 0.0, 1.0, 0.25), 'top_text'), ((0.0, 0.66, 0.24, NUMBER_STRIP_TOP), 'easycard')):
            other = design(2, keep=self.card, keep_box=keep_box)
            corners = rotated_corners((1000, 760), 700, 12)
            found = find_card(place(other, self.background, corners), designs=self.designs)
            self.assertIn(found and found['method'], ('card_back', method))  # the whole back may do with that part alone
            self.assert_quad(found, corners, found['method'], tolerance=0.05)
            only_part = find_card(place(other, self.background, corners), designs=[
                {**self.designs[0], 'name': 'part'}], regions=[r for r in FEATURE_REGIONS if r[0] == method])
            self.assert_quad(only_part, corners, method, tolerance=0.05)

    def test_no_card_is_none(self):
        self.assertIsNone(find_card(self.background, designs=self.designs))
        unrelated = design(3)
        photo = place(unrelated, self.background, rotated_corners((1000, 760), 650, 5))
        found = find_card(photo, designs=self.designs)
        self.assertTrue(found is None or found['method'] == 'shape', found)  # another card's print is not ours

    def test_logo_found_on_its_side_and_upside_down(self):
        logo = synthetic_logo()
        reference_card = card_image(logo, 405)
        logo_w = round(0.72 * 405)
        crop = cv2.resize(logo, (logo_w, round(logo_w * logo.shape[0] / logo.shape[1])), interpolation=cv2.INTER_AREA)
        write_reference(self.data / 'logo_ref', crop, measure_reference(reference_card, crop), root=self.data)
        reference = load_card_reference(self.data / 'logo_ref')
        card = card_image(logo, 130)
        table = np.full((1500, 2000, 3), 85, np.uint8)
        for angle in (90, 180, -84):
            corners = rotated_corners((1000, 750), card.shape[1], angle)
            found = find_card(place(card, table, corners), reference=reference)
            self.assert_quad(found, corners, 'logo', tolerance=0.03)

    def test_plain_card_shape_is_the_last_resort(self):
        blank = np.full((DESIGN_H, DESIGN_WIDTH_PX), 232, np.uint8)
        corners = rotated_corners((1000, 760), 600, 20)
        table = np.full((1500, 2000, 3), (90, 70, 55), np.uint8)
        found = find_card(place(blank, table, corners), designs=self.designs)
        self.assertEqual(found['method'], 'shape')
        self.assertAlmostEqual(float(cm_per_px(found['quad'])), CARD_W_CM / 600, delta=0.03 * CARD_W_CM / 600)
        self.assertIsNone(find_card_shape(self.background))
        square = place(np.full((400, 400), 232, np.uint8), table, rotated_corners((1000, 760), 400, 0, 1.586))
        self.assertIsNone(find_card_shape(square))  # light, but not card-shaped
        self.assertIsNone(find_card(place(blank, table, corners), designs=self.designs, side=True))  # no shape in side photos

    def test_side_photo_outline_gives_the_camera_angle(self):
        """Seen from 30 degrees above the table the card is squashed by sin(30) = 0.5; the map keeps that."""
        corners = rotated_corners((1000, 900), 760, 0, squash=0.5)
        found = find_card(place(self.card, self.background, corners), designs=self.designs, side=True)
        self.assert_quad(found, corners, 'card_back')
        geometry = side_view_geometry(found['quad'])
        self.assertAlmostEqual(geometry['elevation_deg'], 30, delta=2)
        self.assertAlmostEqual(geometry['cm_per_px'], CARD_W_CM / 760, delta=0.02 * CARD_W_CM / 760)

    def test_corners_in_card_order_from_any_click_order(self):
        for angle in (0, 25, 90, -60, 160):
            corners = rotated_corners((500, 400), 300, angle)
            shuffled = [corners[i] for i in (2, 0, 3, 1)]
            ordered = np.array(order_card_corners(shuffled))
            edges = [np.linalg.norm(ordered[(i + 1) % 4] - ordered[i]) for i in range(4)]
            self.assertGreater(edges[0], edges[1] * 1.4, (angle, ordered))  # TL -> TR is a long edge
            area = 0.5 * sum(ordered[i][0] * ordered[(i + 1) % 4][1] - ordered[(i + 1) % 4][0] * ordered[i][1]
                             for i in range(4))
            self.assertGreater(area, 0)  # clockwise on screen, as the card finder returns it
            self.assertAlmostEqual(float(cm_per_px(ordered.tolist())), CARD_W_CM / 300, delta=0.001)


if __name__ == '__main__':
    unittest.main()
