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

find_card runs every way of finding the card, one after another, and stops at the first that works, because each
class year's card back differs a little:
  1. features of the whole card back (SIFT, matched to card_back*.png reference images, several designs allowed)
     give the outline through a perspective map, at any rotation and in side photos too;
  2. features of parts that tend to stay when the rest changes: the printed rules at the top, the 悠遊卡 text and
     logo block, the emergency contact lines (the card outline then follows from that part's place on the card);
  3. the logo template above, turned 0, 90, 270 and 180 degrees;
  4. top photos only: a light card-shaped quadrilateral (ID-1, 1.586:1), the least sure, flagged for checking;
  and if all fail, the user marks the card by hand.
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

FEATURE_SIZE_PX = 1600      # photos are matched at this size (longest side); a card is then about 400-700 px wide
DESIGN_WIDTH_PX = 900       # reference card backs are stored at this width
NUMBER_STRIP_TOP = 0.885    # card number and check code start here (share of the card height); blanked, never matched
RATIO_TEST = 0.75           # Lowe's ratio test for SIFT matches
SIDE_STRETCHES = (1.0, 2.0, 3.2)  # side photos: also matched stretched in height, undoing a squash of about 1/stretch
# Parts of the card back tried one after another: (method, (x0, y0, x1, y1) as shares of the card from its top left,
# model, fewest inliers). Wide parts fit a perspective map; small ones an affine map, which extrapolates more calmly.
FEATURE_REGIONS = (
    ('card_back', (0.0, 0.0, 1.0, NUMBER_STRIP_TOP), 'homography', 15),
    ('top_text', (0.0, 0.0, 1.0, 0.25), 'homography', 15),            # the two printed rules, Chinese and English
    ('easycard', (0.0, 0.66, 0.24, NUMBER_STRIP_TOP), 'affine', 10),  # 悠遊卡 text, logo and SUPERCARD
    ('contact_text', (0.0, 0.52, 0.55, 0.70), 'affine', 10),          # 清華大學緊急聯絡電話 lines
)
MIN_INLIER_SHARE = 0.3      # of a part's matches; random matches on food rarely agree on one map
SHAPE_SIZE_PX = 1000
SHAPE_AREA_SHARE = (0.004, 0.25)  # card area as a share of the photo
SHAPE_ASPECT = (1.45, 1.75)       # long / short side; ID-1 is 1.586


def order_card_corners(points, first='long'):
    """Four corners (any order, e.g. as clicked) in card order: clockwise on screen, so TL->TR is the first edge.

    first='long': the first edge is a long one, starting at the corner nearest the photo's top left (top photos,
    where the card keeps its shape). first='level': the first edge is the top one of the most level pair, as for a
    hand-drawn box in a side photo, where the squash can make the long side look short; multi_view.side_view_geometry
    then takes it as the long side facing the camera.
    """
    pts = np.array(points, float).reshape(4, 2)
    centre = pts.mean(0)
    pts = pts[np.argsort(np.arctan2(pts[:, 1] - centre[1], pts[:, 0] - centre[0]))]  # clockwise with y down
    sides = [pts[(i + 1) % 4] - pts[i] for i in range(4)]
    edges = [float(np.linalg.norm(v)) for v in sides]
    if first == 'level':
        tilt = [abs(v[1]) / (abs(v[0]) + 1e-9) for v in sides]
        pair = 0 if tilt[0] + tilt[2] <= tilt[1] + tilt[3] else 1
        start = min((pair, pair + 2), key=lambda i: pts[i][1] + pts[(i + 1) % 4][1])
    else:
        starts = [i for i in range(4) if edges[i] + edges[(i + 2) % 4] >= edges[(i + 1) % 4] + edges[(i + 3) % 4]]
        start = min(starts, key=lambda i: pts[i].sum())
    return [[round(float(x), 1), round(float(y), 1)] for x, y in np.roll(pts, -start, axis=0)]


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


def _turn(rgb, k):
    """The photo turned k quarter turns counter-clockwise (np.rot90)."""
    return np.ascontiguousarray(np.rot90(rgb, k))


def _unturn(points, k, width, height):
    """Points found in a photo turned by _turn(.., k), back in the original photo of width x height."""
    back = {0: lambda x, y: (x, y), 1: lambda x, y: (width - y, x), 2: lambda x, y: (width - x, height - y),
            3: lambda x, y: (y, height - x)}[k % 4]
    return [[round(float(v), 1) for v in back(x, y)] for x, y in points]


def load_card_designs(folder):
    """Reference card backs for feature matching: every card_back*.png in folder (one per class-year design).

    Each is a photo of the card back cropped to its edges, card number blanked (tools/setup_card_reference.py). The
    SIFT features are computed here, once. An empty list when none is set up.
    """
    sift, designs = cv2.SIFT_create(), []
    for path in sorted(Path(folder).glob('card_back*.png')):
        gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if gray is None or abs(gray.shape[1] / gray.shape[0] / (CARD_W_CM / CARD_H_CM) - 1) > 0.04:
            raise ValueError('card_design_invalid: ' + path.name)
        mask = np.full(gray.shape, 255, np.uint8)
        mask[int(NUMBER_STRIP_TOP * gray.shape[0]):] = 0
        keypoints, descriptors = sift.detectAndCompute(gray, mask)
        if descriptors is None or len(keypoints) < 50:
            raise ValueError('card_design_featureless: ' + path.name)
        designs.append({'name': path.stem, 'size': (gray.shape[1], gray.shape[0]),
                        'points': np.float32([k.pt for k in keypoints]), 'descriptors': descriptors})
    return designs


def _quad_ok(quad, image_size, side):
    """A found outline is plausible: convex, not mirrored, of a sensible size, and card-shaped in a top photo."""
    q = np.float32(quad)
    area = 0.5 * sum(q[i][0] * q[(i + 1) % 4][1] - q[(i + 1) % 4][0] * q[i][1] for i in range(4))
    edges = [float(np.linalg.norm(q[(i + 1) % 4] - q[i])) for i in range(4)]
    width, height = image_size
    if area <= 0 or not cv2.isContourConvex(q.reshape(-1, 1, 2)) or min(edges) < 0.01 * max(width, height) \
            or area > 0.6 * width * height:
        return False
    if side:
        return True
    long, short = (edges[0] + edges[2]) / 2, (edges[1] + edges[3]) / 2
    return 1.3 <= long / short <= 1.9 and max(edges[0], edges[2]) / min(edges[0], edges[2]) < 1.5


def _match_features(gray, designs, side, regions=FEATURE_REGIONS):
    """First card-back part that maps cleanly onto the photo: {'quad', 'method', 'inliers', 'design'} or None.

    SIFT copes with turns and scale but not with the strong squash of a side photo (sin of 15-35 degrees, 0.26-0.57).
    That squash is always along the photo's height, so side photos are also matched stretched back by SIDE_STRETCHES.
    """
    f = min(1.0, FEATURE_SIZE_PX / max(gray.shape))
    for stretch in SIDE_STRETCHES if side else (1.0,):
        small = cv2.resize(gray, None, fx=f, fy=f * stretch, interpolation=cv2.INTER_AREA)
        found = _match_scaled(small, designs, side, (f, f * stretch), gray.shape[::-1], regions)
        if found:
            return found
    return None


def _match_scaled(small, designs, side, scale, image_size, regions):
    keypoints, descriptors = cv2.SIFT_create().detectAndCompute(small, None)
    if descriptors is None or len(keypoints) < 10:
        return None
    photo_points = np.float32([k.pt for k in keypoints])
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    matched = []
    for design in designs:
        pairs = matcher.knnMatch(design['descriptors'], descriptors, k=2)
        good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < RATIO_TEST * p[1].distance]
        matched.append((design, np.array([m.queryIdx for m in good], int), np.array([m.trainIdx for m in good], int)))
    for method, (x0, y0, x1, y1), model, fewest in regions:
        for design, query, train in matched:
            w, h = design['size']
            src = design['points'][query] if len(query) else np.zeros((0, 2), np.float32)
            inside = (src[:, 0] >= x0 * w) & (src[:, 0] <= x1 * w) & (src[:, 1] >= y0 * h) & (src[:, 1] <= y1 * h)
            if inside.sum() < fewest:
                continue
            src, dst = src[inside], photo_points[train[inside]]
            if model == 'homography':
                matrix, keep = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
            else:
                matrix, keep = cv2.estimateAffine2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0)
                matrix = None if matrix is None else np.vstack([matrix, [0, 0, 1]])
            inliers = 0 if keep is None else int(keep.sum())
            if matrix is None or inliers < fewest or inliers < MIN_INLIER_SHARE * len(src):
                continue
            corners = np.float32([[[0, 0], [w, 0], [w, h], [0, h]]])
            quad = (cv2.perspectiveTransform(corners, matrix)[0] / scale).tolist()
            if _quad_ok(quad, image_size, side):
                return {'quad': [[round(x, 1), round(y, 1)] for x, y in quad], 'method': method,
                        'inliers': inliers, 'design': design['name']}
    return None


def find_card_shape(image_rgb):
    """A light, card-shaped quadrilateral (top photos), or None: {'quad', 'method': 'shape', 'fit'}.

    The least sure way: a napkin or a receipt of the right shape would pass too, so the page asks to check it.
    """
    rgb = np.ascontiguousarray(np.asarray(image_rgb)[..., :3])
    f = min(1.0, SHAPE_SIZE_PX / max(rgb.shape[:2]))
    small = cv2.resize(rgb, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else rgb
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
    total = gray.shape[0] * gray.shape[1]
    edges = cv2.dilate(cv2.Canny(gray, 40, 120), np.ones((3, 3), np.uint8))
    _, light = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    best = None
    for binary, mode in ((edges, cv2.RETR_LIST), (light, cv2.RETR_EXTERNAL)):
        for contour in cv2.findContours(binary, mode, cv2.CHAIN_APPROX_SIMPLE)[0]:
            area = cv2.contourArea(contour)
            if not SHAPE_AREA_SHARE[0] * total <= area <= SHAPE_AREA_SHARE[1] * total:
                continue
            hull = cv2.convexHull(contour)
            corners = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True).reshape(-1, 2)
            if len(corners) != 4:
                continue
            (_, _), (rw, rh), _ = cv2.minAreaRect(corners.astype(np.float32))
            if min(rw, rh) <= 0:
                continue
            aspect, fill = max(rw, rh) / min(rw, rh), cv2.contourArea(corners.astype(np.float32)) / (rw * rh)
            if not SHAPE_ASPECT[0] <= aspect <= SHAPE_ASPECT[1] or fill < 0.9:
                continue
            mask = np.zeros(gray.shape, np.uint8)
            cv2.fillConvexPoly(mask, corners.astype(np.int32), 255)
            saturation, value = (float(v) for v in cv2.mean(hsv, mask)[1:3])
            if saturation > 70 or value < 110:  # the card back is white or light grey
                continue
            fit = fill - abs(aspect - CARD_W_CM / CARD_H_CM) / (CARD_W_CM / CARD_H_CM)
            if best is None or fit > best[0]:
                best = (fit, corners / f)
    if best is None:
        return None
    return {'quad': order_card_corners(best[1]), 'method': 'shape', 'fit': round(float(best[0]), 3)}


def find_card(image_rgb, reference=None, designs=(), side=False, regions=FEATURE_REGIONS):
    """Every way of finding the card, in order of trust; the first that works, or None (then: mark it by hand).

    Returns {'quad' (TL, TR, BR, BL in card order), 'method', ...}: method is one of the FEATURE_REGIONS names,
    'logo' or 'shape'. reference: load_card_reference(); designs: load_card_designs(); side: a photo from the side
    (feature maps and squashed logos only; the shape test needs a top view); regions: the card-back parts to try.
    """
    rgb = np.ascontiguousarray(np.asarray(image_rgb)[..., :3])
    height, width = rgb.shape[:2]
    if designs:
        found = _match_features(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), designs, side, regions)
        if found:
            return found
    if reference is not None:
        for k in ((0,) if side else (0, 1, 3, 2)):  # quarter turns: upright first, then on its side, upside down
            turned = _turn(rgb, k)
            found = locate_card(turned, reference, SIDE_SQUASHES if side else (1.0,))
            if found:
                quad = _unturn(found['quad'], k, width, height)
                return {**found, 'quad': quad, 'method': 'logo', 'angle': found['angle'] + 90 * k}
    if not side:
        return find_card_shape(rgb)
    return None
