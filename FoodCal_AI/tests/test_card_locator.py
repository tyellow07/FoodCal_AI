"""Automatic card finding from the logo: synthetic logo, card and photos only (no real card images)."""
import json
import math
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np
from PIL import Image

from src.portion.card_locator import CARD_H_CM, CARD_W_CM, load_card_reference, locate_card
from src.portion.rough_volume import cm_per_px
from src.ui.adapter import draw_predictions
from tools.setup_card_reference import measure_reference, write_reference

RIGHT_CM, UP_CM, LOGO_W_CM = 0.97, 1.14, 0.72


def synthetic_logo():
    """A green swoosh inside a dark rounded frame, about the shape of the real logo (BGR)."""
    logo = np.full((114, 180, 3), 205, np.uint8)
    cv2.rectangle(logo, (6, 6), (173, 107), (60, 60, 60), 4)
    cv2.ellipse(logo, (70, 40), (60, 26), 20, 0, 360, (40, 150, 60), -1)
    cv2.ellipse(logo, (125, 75), (40, 30), -30, 200, 520, (40, 150, 60), 14)
    cv2.circle(logo, (40, 85), 9, (40, 150, 60), -1)
    return logo


def card_image(logo, px_per_cm):
    """Upright card back (BGR) with the logo centre RIGHT_CM / UP_CM from the bottom-left corner."""
    width, height = round(CARD_W_CM * px_per_cm), round(CARD_H_CM * px_per_cm)
    card = np.full((height, width, 3), 228, np.uint8)
    for row in range(1, 6):  # some printed lines so the card is not blank
        cv2.line(card, (int(0.5 * px_per_cm), int(row * 0.6 * px_per_cm)), (int(7.5 * px_per_cm), int(row * 0.6 * px_per_cm)),
                 (70, 70, 70), max(1, int(px_per_cm * 0.05)))
    logo_w = round(LOGO_W_CM * px_per_cm)
    logo_h = round(logo_w * logo.shape[0] / logo.shape[1])
    small = cv2.resize(logo, (logo_w, logo_h), interpolation=cv2.INTER_AREA)
    cx, cy = RIGHT_CM * px_per_cm, height - UP_CM * px_per_cm
    x0, y0 = int(round(cx - logo_w / 2)), int(round(cy - logo_h / 2))
    card[y0:y0 + logo_h, x0:x0 + logo_w] = small
    return card


def photo_with_card(card, angle_deg, top_left=(300, 250), size=(1800, 1200)):
    """Paste the card into a dark 'table' photo, rotated clockwise on screen by angle_deg; returns (RGB, quad)."""
    h, w = card.shape[:2]
    theta = math.radians(angle_deg)
    rotation = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
    offset = np.array(top_left, float)
    matrix = np.hstack([rotation, offset[:, None]])
    photo = np.full((size[1], size[0], 3), 85, np.uint8)
    warped = cv2.warpAffine(card, matrix, size, flags=cv2.INTER_LINEAR)
    mask = cv2.warpAffine(np.full((h, w), 255, np.uint8), matrix, size)
    photo[mask > 0] = warped[mask > 0]
    corners = np.array([[0, 0], [w, 0], [w, h], [0, h]], float) @ rotation.T + offset
    return cv2.cvtColor(photo, cv2.COLOR_BGR2RGB), corners


class CardLocatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.data = Path(cls.folder.name) / 'data'
        cls.logo = synthetic_logo()
        reference_card = card_image(cls.logo, 405)
        logo_w = round(LOGO_W_CM * 405)
        logo = cv2.resize(cls.logo, (logo_w, round(logo_w * cls.logo.shape[0] / cls.logo.shape[1])), interpolation=cv2.INTER_AREA)
        cls.measured = measure_reference(reference_card, logo)
        write_reference(cls.data / 'card_reference', logo, cls.measured, root=cls.data)
        cls.reference = load_card_reference(cls.data / 'card_reference')

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_setup_measures_logo_geometry_on_the_card_photo(self):
        self.assertAlmostEqual(self.measured['logo_center_right_cm'], RIGHT_CM, delta=0.02)
        self.assertAlmostEqual(self.measured['logo_center_up_cm'], UP_CM, delta=0.02)
        self.assertAlmostEqual(self.measured['logo_width_cm'], LOGO_W_CM, delta=0.01)
        with self.assertRaises(ValueError):
            measure_reference(np.full((500, 500, 3), 200, np.uint8), self.logo)  # not cropped to a card
        with self.assertRaises(ValueError):
            write_reference(Path(self.folder.name) / 'elsewhere', self.logo, self.measured, root=self.data)

    def assert_found(self, angle):
        rgb, corners = photo_with_card(card_image(self.logo, 130), angle)
        found = locate_card(Image.fromarray(rgb), self.reference)
        self.assertIsNotNone(found, angle)
        card_width = 8.56 * 130
        for got, expected in zip(found['quad'], corners):
            self.assertLess(np.linalg.norm(np.array(got) - expected), 0.03 * card_width, (angle, found['quad'], corners))
        self.assertAlmostEqual(found['cm_per_px'], 1 / 130, delta=0.03 / 130)
        self.assertAlmostEqual(float(cm_per_px(found['quad'])), 1 / 130, delta=0.03 / 130)

    def test_finds_upright_and_rotated_cards(self):
        for angle in (0, 6, -9):
            self.assert_found(angle)

    def test_no_card_or_grey_lookalike_is_not_a_card(self):
        empty = np.full((900, 1400, 3), 120, np.uint8)
        cv2.circle(empty, (700, 450), 300, (235, 235, 235), -1)
        self.assertIsNone(locate_card(empty, self.reference))
        grey_card = cv2.cvtColor(cv2.cvtColor(card_image(self.logo, 130), cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        rgb, _ = photo_with_card(grey_card, 0)
        self.assertIsNone(locate_card(rgb, self.reference))  # right shape, but no green where the logo is

    def test_missing_or_broken_reference(self):
        self.assertIsNone(load_card_reference(Path(self.folder.name) / 'nothing'))
        broken = self.data / 'broken'
        write_reference(broken, self.logo, {'logo_width_cm': 0}, root=self.data)
        with self.assertRaises(ValueError):
            load_card_reference(broken)

    def test_outline_sets_scale_and_is_drawn(self):
        rgb, corners = photo_with_card(card_image(self.logo, 100), 10)
        quad = corners.tolist()
        self.assertAlmostEqual(float(cm_per_px(quad)), 0.01, delta=0.0002)
        with self.assertRaises(ValueError):
            cm_per_px([[0, 0], [100, 0], [100, 100], [0, 100]])  # a square is not a card
        image = Image.fromarray(rgb)
        result = {'detection_snapshot': {'image': {'width': image.width, 'height': image.height}, 'detections': []}}
        drawn = np.asarray(draw_predictions(image, result, quad))
        self.assertTrue((drawn != np.asarray(image)).any())


if __name__ == '__main__':
    unittest.main()
