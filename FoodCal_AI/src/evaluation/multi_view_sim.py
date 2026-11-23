"""Synthetic camera scenes for checking the multi-view volume geometry: no photos, no model, known true volumes.

A pinhole camera (focal length in pixels, perspective included) photographs a food of known shape and a flat student
card from above (the top photo) and from the side (the side photo). The boxes and card outlines it produces are what
the recognizer and the card finder would return, optionally with noise, so single-photo and two-photo estimates can
be compared against the true volume. This checks geometry and measurement tolerance only: real foods, real boxes
from Gemini and real cameras still need weighed meals (docs/multi_view_volume_v0.md).

Shapes (x right, y away from the side camera, z up, cm):
- heap or slab: z = h (1 - r^p) over an ellipse with semi-axes a, b; volume = pi a b h p / (p + 2)
  (p = 2 is a paraboloid heap; larger p is a flatter-topped slab);
- bowl: a frustum (rim radius R, foot radius R k, outer height H, inner depth H - bottom) filled to a share of its
  capacity, optionally heaped above the rim with a paraboloid cap.
"""
import math
import random

from src.portion.multi_view import CARD_LONG_CM, CARD_SHORT_CM, multi_view_volume, side_view_geometry
from src.portion.rough_volume import rough_volume

IMAGE = (4032, 3024)
FOCAL_PX = 3000.0


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(_dot(a, a))
    return tuple(x / n for x in a)


def camera(position, target, up_hint=(0.0, 0.0, 1.0)):
    forward = _unit(_sub(target, position))
    right = _unit(_cross(forward, up_hint))
    return {'position': position, 'forward': forward, 'right': right, 'up': _cross(right, forward)}


def side_camera(elevation_deg, distance_cm, target=(0.0, 0.0, 0.0)):
    t = math.radians(elevation_deg)
    position = (target[0], target[1] - distance_cm * math.cos(t), target[2] + distance_cm * math.sin(t))
    return camera(position, target)


def top_camera(height_cm, centre=(0.0, 0.0)):
    return camera((centre[0], centre[1], height_cm), (centre[0], centre[1], 0.0), up_hint=(0.0, 1.0, 0.0))


def project(points, cam):
    out = []
    for p in points:
        d = _sub(p, cam['position'])
        z = _dot(d, cam['forward'])
        out.append((IMAGE[0] / 2 + FOCAL_PX * _dot(d, cam['right']) / z,
                    IMAGE[1] / 2 - FOCAL_PX * _dot(d, cam['up']) / z))
    return out


def bbox(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def card_corners(centre, azimuth_deg):
    """TL, TR, BR, BL of a flat card in card order (TL->TR is a long edge; 'top' is the far side)."""
    p = math.radians(azimuth_deg)
    out = []
    for x, y in ((-1, 1), (1, 1), (1, -1), (-1, -1)):
        lx, ly = x * CARD_LONG_CM / 2, y * CARD_SHORT_CM / 2
        out.append((centre[0] + lx * math.cos(p) - ly * math.sin(p), centre[1] + lx * math.sin(p) + ly * math.cos(p),
                    0.0))
    return out


def heap_points(a, b, h, power, centre=(0.0, 0.0), rings=24, spokes=72):
    pts = []
    for i in range(rings + 1):
        r = i / rings
        z = h * (1 - r ** power)
        for j in range(spokes):
            w = 2 * math.pi * j / spokes
            pts.append((centre[0] + a * r * math.cos(w), centre[1] + b * r * math.sin(w), z))
    return pts


def heap_volume(a, b, h, power):
    return math.pi * a * b * h * power / (power + 2)


def _circle(radius, z, centre, spokes=72):
    return [(centre[0] + radius * math.cos(2 * math.pi * j / spokes),
             centre[1] + radius * math.sin(2 * math.pi * j / spokes), z) for j in range(spokes)]


def bowl_scene_points(rim_r, base_ratio, height, bottom, fill, heap_cm, centre=(0.0, 0.0)):
    """(bowl outer points, food points, food volume): frustum bowl filled to `fill` of its capacity."""
    depth = height - bottom
    inner_bottom = rim_r * base_ratio

    def frustum(level):  # capacity from the inner bottom up to `level` cm
        top = inner_bottom + (rim_r - inner_bottom) * level / depth
        return math.pi * level / 3 * (inner_bottom ** 2 + inner_bottom * top + top ** 2)

    capacity = frustum(depth)
    if heap_cm > 0:
        level, volume = depth, capacity + math.pi * rim_r ** 2 * heap_cm / 2
    else:
        lo, hi = 0.0, depth  # level whose frustum volume is fill x capacity
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if frustum(mid) < fill * capacity else (lo, mid)
        level, volume = lo, fill * capacity
    bowl = _circle(rim_r, height, centre) + _circle(rim_r * base_ratio, 0.0, centre)
    surface_r = inner_bottom + (rim_r - inner_bottom) * level / depth
    food = _circle(surface_r, bottom + level, centre)
    if heap_cm > 0:
        food += [(x, y, z + bottom + depth) for x, y, z in heap_points(rim_r, rim_r, heap_cm, 2, centre)]
    return bowl, food, volume


def _noisy(box, rng, share):
    if not share:
        return box
    w, h = box[2] - box[0], box[3] - box[1]
    return [box[0] + rng.uniform(-share, share) * w, box[1] + rng.uniform(-share, share) * h,
            box[2] + rng.uniform(-share, share) * w, box[3] + rng.uniform(-share, share) * h]


def _noisy_quad(quad, rng, px):
    return [[x + rng.uniform(-px, px), y + rng.uniform(-px, px)] for x, y in quad] if px else [list(p) for p in quad]


def measured_card(centre, azimuth_deg, cam, rng, scale_noise=0.0, angle_noise_deg=0.0):
    """Card outline as the card finder reports it: a parallelogram from the card's local scale and squash.

    The finder measures the logo where the card lies and builds the outline from the card's known size, so it has no
    perspective convergence; its errors are a small scale error and a small error in the squash (camera angle).
    """
    eps = 0.01
    origin = (centre[0], centre[1], 0.0)
    o = project([origin], cam)[0]
    jx = [(v - w) / eps for v, w in zip(project([(centre[0] + eps, centre[1], 0.0)], cam)[0], o)]
    jy = [(v - w) / eps for v, w in zip(project([(centre[0], centre[1] + eps, 0.0)], cam)[0], o)]
    k = 1 + (rng.uniform(-scale_noise, scale_noise) if scale_noise else 0.0)
    squash = 1 + (math.radians(rng.uniform(-angle_noise_deg, angle_noise_deg)) if angle_noise_deg else 0.0)
    out = []
    for x, y, _ in card_corners((0.0, 0.0), azimuth_deg):
        out.append([o[0] + k * (jx[0] * x + jy[0] * y * squash), o[1] + k * (jx[1] * x + jy[1] * y * squash)])
    return out


def scene(kind, rng, *, box_noise=0.02, card_scale_noise=0.02, card_angle_noise_deg=1.0):
    """One random scene -> {'kind', 'food', 'container', 'true_ml', boxes and card outlines for both photos}."""
    elevation, distance = rng.uniform(18, 35), rng.uniform(30, 55)
    if kind in ('heap', 'slab'):
        a, b = rng.uniform(4, 7.5), rng.uniform(3, 6)
        if kind == 'heap':
            h, power, food, container = rng.uniform(1.5, 7), rng.uniform(1.6, 2.6), 'cooked_rice_region', 'plate'
        else:
            h, power, food, container = rng.uniform(0.6, 3), rng.uniform(3.5, 10), 'deep-fried_chicken_cutlets', 'plate'
        food_pts, true_ml = heap_points(a, b, h, power), heap_volume(a, b, h, power)
        top_food, side_food, side_bowl, half = food_pts, food_pts, None, a
    else:
        rim, base, height = rng.uniform(5, 8), rng.uniform(0.5, 0.65), rng.uniform(5, 9)
        heap = rng.uniform(1, 4) if rng.random() < 0.3 else 0.0
        bowl, food_pts, true_ml = bowl_scene_points(rim, base, height, rng.uniform(0.4, 0.7), rng.uniform(0.65, 0.95),
                                                    heap)
        top_food, side_food, side_bowl, half = _circle(rim, height, (0.0, 0.0)), food_pts, bowl, rim
        food, container = 'beef_noodles', 'bowl'
    card_centre, card_azimuth = (-(half + rng.uniform(5, 8)), rng.uniform(-3, 3)), rng.uniform(-20, 20)
    top_cam, side_cam = top_camera(rng.uniform(35, 50)), side_camera(elevation, distance)
    out = {'kind': kind, 'food': food, 'container': container, 'true_ml': true_ml, 'elevation_deg': elevation,
           'top_card': measured_card(card_centre, card_azimuth, top_cam, rng, card_scale_noise,
                                     card_angle_noise_deg),
           'top_box': _noisy(bbox(project(top_food, top_cam)), rng, box_noise),
           'side_card': measured_card(card_centre, card_azimuth, side_cam, rng, card_scale_noise,
                                      card_angle_noise_deg),
           'side_box': _noisy(bbox(project(side_food, side_cam)), rng, box_noise),
           'side_container_box': _noisy(bbox(project(side_bowl, side_cam)), rng, box_noise) if side_bowl else None}
    return out


def estimate(s):
    """(single-photo volume, two-photo volume) intervals for one scene; the two-photo one is None when it fails."""
    single = rough_volume(s['food'], s['top_box'], s['top_card'], s['container'])['volume_ml']
    try:
        geometry = side_view_geometry(s['side_card'])
        multi = multi_view_volume(s['food'], s['top_box'], s['top_card'], s['side_box'], geometry, s['container'],
                                  s['side_container_box'])['volume_ml']
    except ValueError:
        multi = None
    return single, multi


def benchmark(kind, n=300, seed=7, **noise):
    """Accuracy of both methods on n random scenes: MAPE of the middle value, median error, coverage, width."""
    rng = random.Random(seed)
    rows = {'single': [], 'multi': []}
    failed = 0
    for _ in range(n):
        s = scene(kind, rng, **noise)
        single, multi = estimate(s)
        if multi is None:
            failed += 1
            continue
        for name, est in (('single', single), ('multi', multi)):
            low, mid, high = (float(est[k]) for k in ('low', 'mid', 'high'))
            rows[name].append((abs(mid - s['true_ml']) / s['true_ml'], low <= s['true_ml'] <= high,
                               high / low if low > 0 else float('inf')))
    report = {'kind': kind, 'scenes': n, 'two_photo_failed': failed}
    for name, values in rows.items():
        errors = sorted(v[0] for v in values)
        widths = sorted(v[2] for v in values)
        report[name] = {'mape': sum(errors) / len(errors), 'median_ape': errors[len(errors) // 2],
                        'coverage': sum(v[1] for v in values) / len(values), 'median_width': widths[len(widths) // 2]}
    return report
