"""Find the student card in a photo from the logo printed on its back (EasyCard logo, bottom left).

The logo template and its geometry come from one reference photo of the card back (tools/setup_card_reference.py):
logo size in cm and the logo centre measured from the card's bottom-left corner. A match gives the logo centre,
its size in pixels (so cm per pixel) and a small rotation, and the card outline follows from the card size
(ISO ID-1, 85.60 x 53.98 mm). Search: grey-level template matching over scales and rotations of +-15 degrees on
a 800 px copy, then a finer pass on a crop around the best match. A match must also be green where the logo is
(grey-level matching alone can lock onto rice texture). The card must show its back with the logo at the bottom
left; anything else returns None so the user can draw the card box by hand.

In a photo taken from the side the flat card, logo included, looks squashed vertically by sin(elevation); passing
SIDE_SQUASHES also tries squashed templates. The logo is small, so its squash is coarse: the card's own outline (a
light card on a darker table) is then traced around the match and replaces the outline built from the logo when it
is clean (refine_outline); multi_view.py reads the camera angle and scale from that outline.
"""
import json
import math
from pathlib import Path

import cv2
import numpy as np

CARD_W_CM, CARD_H_CM = 8.56, 5.398
MIN_SCORE = 0.75          # true matches on campus photos scored 0.94; rice texture reached 0.68
MIN_GREEN_FRACTION = 0.15  # the logo area is about 35% green on real matches, 0% on false ones
SIDE_SQUASHES = (0.3, 0.37, 0.45, 0.54, 0.64, 0.75, 0.87)  # sin(elevation) for side photos, about 17-60 degrees


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


def _search(gray, logo, angles, widths, squashes=(1.0,)):
    best = (-1.0, None)
    h, w = gray.shape
    for angle in angles:
        rotation = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        rotated = cv2.warpAffine(gray, rotation, (w, h), borderMode=cv2.BORDER_REPLICATE)
        inverse = cv2.invertAffineTransform(rotation)
        for width, squash in ((wd, sq) for wd in widths for sq in squashes):
            width = int(round(width))
            height = int(round(width * logo.shape[0] / logo.shape[1] * squash))
            if width < 8 or height < 6 or width >= w or height >= h:
                continue
            template = cv2.resize(logo, (width, height), interpolation=cv2.INTER_AREA)
            _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(rotated, template, cv2.TM_CCOEFF_NORMED))
            if score > best[0]:
                centre = inverse @ np.array([x + width / 2, y + height / 2, 1.0])
                best = (score, (centre, angle, width, squash))
    return best


def green_fraction(rgb, centre, width, height):
    """Share of clearly green pixels in the axis-aligned logo-sized patch around centre."""
    x0, y0 = max(0, int(centre[0] - width / 2)), max(0, int(centre[1] - height / 2))
    patch = rgb[y0:int(centre[1] + height / 2), x0:int(centre[0] + width / 2)]
    if patch.size == 0:
        return 0.0
    hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)
    return float(((hsv[..., 0] >= 30) & (hsv[..., 0] <= 90) & (hsv[..., 1] >= 60)).mean())


def refine_outline(image_rgb, quad):
    """The card's traced outline (TL, TR, BR, BL, same order as quad) near an approximate quad, or None.

    The card is lighter than the table: threshold a crop around the quad (Otsu), take the region under the quad's
    centre, and simplify its hull to four corners. Refused when it is not a clean quadrilateral of about the
    expected size (a hand over the card, glare, a white plate touching it).
    """
    rgb = np.ascontiguousarray(np.asarray(image_rgb)[..., :3])
    q = np.array(quad, float)
    span = q.max(0) - q.min(0)
    h, w = rgb.shape[:2]
    x0, y0 = np.maximum(0, np.floor(q.min(0) - 0.35 * span)).astype(int)
    x1, y1 = np.minimum((w, h), np.ceil(q.max(0) + 0.35 * span)).astype(int)
    gray = cv2.GaussianBlur(cv2.cvtColor(rgb[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY), (5, 5), 0)
    if gray.size == 0:
        return None
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    centre = tuple(float(v) for v in q.mean(0) - (x0, y0))
    inside = [c for c in contours if cv2.pointPolygonTest(c, centre, False) >= 0]
    if not inside:
        return None
    hull = cv2.convexHull(max(inside, key=cv2.contourArea))
    corners = cv2.approxPolyDP(hull, 0.03 * cv2.arcLength(hull, True), True).reshape(-1, 2).astype(float)
    expected = cv2.contourArea(q.astype(np.float32))
    if len(corners) != 4 or not 0.7 <= cv2.contourArea(corners.astype(np.float32)) / max(expected, 1.0) <= 1.4:
        return None
    corners += (x0, y0)
    order = [int(np.argmin(((corners - corner) ** 2).sum(1))) for corner in q]
    if len(set(order)) != 4:
        return None
    return [[round(float(corners[i][0]), 1), round(float(corners[i][1]), 1)] for i in order]


def locate_card(image_rgb, reference, squashes=(1.0,)):
    """{'quad' TL,TR,BR,BL in image pixels, 'cm_per_px', 'score', 'angle', 'squash'} or None when no confident match.

    squashes: vertical squash factors to try; (1.0,) for top-down photos, SIDE_SQUASHES for photos from the side.
    """
    logo, meta = reference
    rgb = np.ascontiguousarray(np.asarray(image_rgb)[..., :3])
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    full_h, full_w = gray.shape
    f = 800 / max(full_h, full_w)
    small = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    score, found = _search(small, logo, range(-15, 16, 5), [8 * 1.15 ** i for i in range(19)], squashes)
    if found is None or score < MIN_SCORE:
        return None
    centre, angle, width, squash = found
    # Finer pass: four times the resolution of the coarse copy (at most the original), on a crop around the match.
    g = min(1.0, 4 * f)
    k = g / f
    cx, cy = centre * k
    half = int(width * k * 3)
    big = cv2.resize(gray, None, fx=g, fy=g, interpolation=cv2.INTER_AREA) if g < 1 else gray
    x0, y0 = max(0, int(cx) - half), max(0, int(cy) - half)
    crop = big[y0:int(cy) + half, x0:int(cx) + half]
    fine_squashes = sorted({min(1.0, squash * f) for f in (0.92, 1.0, 1.08)}) if len(squashes) > 1 else (squash,)
    score2, found2 = _search(crop, logo, [angle + d for d in (-3, -1.5, 0, 1.5, 3)],
                             [width * k * (1 + d / 100) for d in range(-12, 13, 3)], fine_squashes)
    if found2 is not None and score2 >= score - 0.05:
        (local, angle, width_big, squash), score = found2, max(score, score2)
        centre_px, logo_px = (local + (x0, y0)) / g, width_big / g
    else:
        centre_px, logo_px = centre / f, width / f
    if score < MIN_SCORE or green_fraction(rgb, centre_px, logo_px, logo_px * logo.shape[0] / logo.shape[1] * squash) \
            < MIN_GREEN_FRACTION:
        return None
    px_per_cm = logo_px / meta['logo_width_cm']
    theta = math.radians(angle)  # the photo was rotated by +angle (counter-clockwise) to make the logo upright
    rot = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])

    def point(right_cm, up_cm):  # card frame (x right, y up, cm from the logo centre) -> image pixels
        return centre_px + rot @ np.array([right_cm, -up_cm * squash]) * px_per_cm

    left, bottom = -meta['logo_center_right_cm'], -meta['logo_center_up_cm']
    quad = [point(left, bottom + CARD_H_CM), point(left + CARD_W_CM, bottom + CARD_H_CM),
            point(left + CARD_W_CM, bottom), point(left, bottom)]
    quad = [[round(float(x), 1), round(float(y), 1)] for x, y in quad]
    traced = refine_outline(rgb, quad) if len(squashes) > 1 else None  # side photos: the logo's squash is coarse
    return {'quad': traced or quad, 'cm_per_px': 1 / px_per_cm, 'score': round(float(score), 3),
            'angle': float(angle), 'squash': round(float(squash), 3), 'outline': 'traced' if traced else 'logo'}
