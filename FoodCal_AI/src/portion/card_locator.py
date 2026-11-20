"""Find the student card in a photo from the logo printed on its back (EasyCard logo, bottom left).

The logo template and its geometry come from one reference photo of the card back (tools/setup_card_reference.py):
logo size in cm and the logo centre measured from the card's bottom-left corner. A match gives the logo centre,
its size in pixels (so cm per pixel) and a small rotation, and the card outline follows from the card size
(ISO ID-1, 85.60 x 53.98 mm). Search: grey-level template matching over scales and rotations of +-15 degrees on
a 800 px copy, then a finer pass on a crop around the best match. A match must also be green where the logo is
(grey-level matching alone can lock onto rice texture). The card must show its back with the logo at the bottom
left; anything else returns None so the user can draw the card box by hand.
"""
import json
import math
from pathlib import Path

import cv2
import numpy as np

CARD_W_CM, CARD_H_CM = 8.56, 5.398
MIN_SCORE = 0.75          # true matches on campus photos scored 0.94; rice texture reached 0.68
MIN_GREEN_FRACTION = 0.15  # the logo area is about 35% green on real matches, 0% on false ones


def load_card_reference(folder):
    """(logo image, geometry dict) from a reference folder, or None when it has not been set up."""
    folder = Path(folder)
    logo_path, meta_path = folder / 'logo.png', folder / 'card_reference.json'
    if not logo_path.is_file() or not meta_path.is_file():
        return None
    logo = cv2.imread(str(logo_path), cv2.IMREAD_GRAYSCALE)
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    for key in ('logo_width_cm', 'logo_center_right_cm', 'logo_center_up_cm'):
        if not isinstance(meta.get(key), (int, float)) or meta[key] <= 0:
            raise ValueError('card_reference_invalid: ' + key)
    if logo is None:
        raise ValueError('card_reference_logo_unreadable')
    return logo, meta


def _search(gray, logo, angles, widths):
    best = (-1.0, None)
    h, w = gray.shape
    for angle in angles:
        rotation = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        rotated = cv2.warpAffine(gray, rotation, (w, h), borderMode=cv2.BORDER_REPLICATE)
        inverse = cv2.invertAffineTransform(rotation)
        for width in widths:
            width = int(round(width))
            height = int(round(width * logo.shape[0] / logo.shape[1]))
            if width < 8 or height < 6 or width >= w or height >= h:
                continue
            template = cv2.resize(logo, (width, height), interpolation=cv2.INTER_AREA)
            _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(rotated, template, cv2.TM_CCOEFF_NORMED))
            if score > best[0]:
                centre = inverse @ np.array([x + width / 2, y + height / 2, 1.0])
                best = (score, (centre, angle, width))
    return best


def green_fraction(rgb, centre, width, height):
    """Share of clearly green pixels in the axis-aligned logo-sized patch around centre."""
    x0, y0 = max(0, int(centre[0] - width / 2)), max(0, int(centre[1] - height / 2))
    patch = rgb[y0:int(centre[1] + height / 2), x0:int(centre[0] + width / 2)]
    if patch.size == 0:
        return 0.0
    hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)
    return float(((hsv[..., 0] >= 30) & (hsv[..., 0] <= 90) & (hsv[..., 1] >= 60)).mean())


def locate_card(image_rgb, reference):
    """{'quad' TL,TR,BR,BL in image pixels, 'cm_per_px', 'score', 'angle'} or None when no confident match."""
    logo, meta = reference
    rgb = np.ascontiguousarray(np.asarray(image_rgb)[..., :3])
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    full_h, full_w = gray.shape
    f = 800 / max(full_h, full_w)
    small = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    score, found = _search(small, logo, range(-15, 16, 5), [8 * 1.15 ** i for i in range(19)])
    if found is None or score < MIN_SCORE:
        return None
    centre, angle, width = found
    # Finer pass: four times the resolution of the coarse copy (at most the original), on a crop around the match.
    g = min(1.0, 4 * f)
    k = g / f
    cx, cy = centre * k
    half = int(width * k * 3)
    big = cv2.resize(gray, None, fx=g, fy=g, interpolation=cv2.INTER_AREA) if g < 1 else gray
    x0, y0 = max(0, int(cx) - half), max(0, int(cy) - half)
    crop = big[y0:int(cy) + half, x0:int(cx) + half]
    score2, found2 = _search(crop, logo, [angle + d for d in (-3, -1.5, 0, 1.5, 3)],
                             [width * k * (1 + d / 100) for d in range(-12, 13, 3)])
    if found2 is not None and score2 >= score - 0.05:
        (local, angle, width_big), score = found2, max(score, score2)
        centre_px, logo_px = (local + (x0, y0)) / g, width_big / g
    else:
        centre_px, logo_px = centre / f, width / f
    if score < MIN_SCORE or green_fraction(rgb, centre_px, logo_px, logo_px * logo.shape[0] / logo.shape[1]) \
            < MIN_GREEN_FRACTION:
        return None
    px_per_cm = logo_px / meta['logo_width_cm']
    theta = math.radians(angle)  # the photo was rotated by +angle (counter-clockwise) to make the logo upright
    rot = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])

    def point(right_cm, up_cm):  # card frame (x right, y up, cm from the logo centre) -> image pixels
        return centre_px + rot @ np.array([right_cm, -up_cm]) * px_per_cm

    left, bottom = -meta['logo_center_right_cm'], -meta['logo_center_up_cm']
    quad = [point(left, bottom + CARD_H_CM), point(left + CARD_W_CM, bottom + CARD_H_CM),
            point(left + CARD_W_CM, bottom), point(left, bottom)]
    return {'quad': [[round(float(x), 1), round(float(y), 1)] for x, y in quad],
            'cm_per_px': 1 / px_per_cm, 'score': round(float(score), 3), 'angle': float(angle)}
