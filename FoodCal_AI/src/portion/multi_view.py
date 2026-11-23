"""Food height and volume from a second photo taken from the side, on top of the top-down rough volume.

One top-down photo gives each dish's footprint (student card as the ruler) but not its height, so the single-photo
method (rough_volume.py) assumes a thickness per food. A second photo from about 15-35 degrees above the table, with
the card still lying flat beside the plate, measures the height instead (docs/multi_view_volume_v0.md):

1. Card -> camera angle and scale. Locally the table projects affinely: a table direction (X across, Y away) lands
   at ((X + k Y) / s, -Y sin t / s) in the photo, with s the cm per pixel at the card, t the camera's elevation and
   k the lean perspective gives depth lines away from the photo's middle. The card's two edges (8.56 and 5.398 cm,
   turned by p) give four equations for s, t, p and k, solved exactly (side_view_geometry).
2. Plate dishes are modelled as z = h (1 - r^p) over their footprint (p = 2 a paraboloid heap, larger p a
   flatter-topped slab). For a phone 30-55 cm away (perspective included; the focal length cancels with the card's
   scale), the peak height h is searched for that makes the dish span as many pixels as its box in the side photo.
   Volume = footprint area (top photo: box x fill) x h x p / (p + 2), the mean height for that p.
3. Bowl dishes. The bowl's extent in the side photo gives its height, and with it the depth. Looking down into the
   bowl, the far edge of the food surface shows how far below the rim the food sits (or a heap rises above it), so
   the fill level is measured too. The rim, read larger in the top photo because it stands above the table, is
   corrected with the measured height.

Every input carries a tolerance (box edges, card scale, camera angle and distance, footprint depth, shape); the
result is the interval over all of them, and the middle value is the estimate from the central values. On synthetic
scenes (src/evaluation/multi_view_sim.py) this is checked against known true volumes; nothing here was fitted to
weighed food, so results stay rough (粗估) with coverage_validated = False, and the strict photo_energy_estimate path
is not touched.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from itertools import product
import math

from .rough_volume import FOOD_TEMPLATES, GENERIC_TEMPLATES, PLATE_FILL, _box, cm_per_px

D = Decimal
CARD_LONG_CM, CARD_SHORT_CM = 8.56, 5.398
ELEVATION_LIMITS_DEG = (12.0, 50.0)  # flatter: the card is a sliver; steeper: height barely shows (or a top view)
ANGLE_TOLERANCE_DEG = 2.0
SCALE_TOLERANCE = 0.04               # the card and the dish are not exactly the same distance from the camera
BOX_TOLERANCE = (0.03, 4.0)          # side-photo box edges: +-3% of the box height, at least +-4 px
# Plate dishes are modelled as z = h (1 - r^p) over their footprint: p = 2 is a paraboloid heap (mean height h / 2),
# larger p a flatter-topped slab with rounded edges (mean height h p / (p + 2)). (low, nominal, high) per profile.
PROFILE_POWER = {'mound': (1.6, 2.0, 2.6), 'slab': (3.0, 5.0, 10.0), 'generic': (1.6, 3.0, 10.0)}
SLAB_FOODS = frozenset({'Over-easy egg', 'visible_fried_egg', 'deep-fried_chicken_cutlets', 'egg_pancake_roll'})
BOWL_BASE_RATIO = (0.45, 0.7)        # foot (base) diameter / rim diameter
BOWL_BOTTOM_CM = (0.3, 0.8)          # outer height minus inner depth
ROUNDED_BOWL = (0.85, 1.0)           # a rounded bowl holds less than the straight-walled frustum of the same size
HEAP_FACTOR = (0.5, 0.65)            # heap above a bowl rim: paraboloid-like cap over the rim circle
CAMERA_DISTANCE_CM = (30.0, 42.0, 55.0)  # phone held at arm's length: low, nominal, high (perspective)
SAMPLES = 41                         # points along a silhouette when searching for its top
TOP_CAMERA_CM = (30.0, 40.0, 50.0)   # phone height above the table in the top photo


def _mid(pair):
    return (pair[0] + pair[1]) / 2


def _interval3(low, mid, high, step):
    low, high = max(0.0, low), max(0.0, high)
    mid = min(max(mid, low), high)
    return {'low': D(str(low)).quantize(step, rounding=ROUND_FLOOR), 'mid': D(str(mid)).quantize(step),
            'high': D(str(high)).quantize(step, rounding=ROUND_CEILING)}


def _points(card):
    if isinstance(card, (list, tuple)) and len(card) == 4 and all(isinstance(p, (list, tuple)) and len(p) == 2
                                                                  for p in card):
        try:
            return [(float(x), float(y)) for x, y in card]
        except (TypeError, ValueError):
            raise ValueError('invalid_side_card') from None
    return None


def side_view_geometry(card):
    """{'cm_per_px', 'elevation_deg', 'azimuth_deg', 'card_source'} from the card in the side photo.

    card: the outline [[x, y]] * 4 in card order (TL, TR, BR, BL: TL->TR is a long edge, as the card finder returns
    it), or an axis-aligned box [x0, y0, x1, y1] drawn by hand, in which case the long side is assumed to face the
    camera. Raises ValueError('side_photo_angle_out_of_range') outside ELEVATION_LIMITS_DEG.
    """
    points = _points(card)
    if points is not None:
        tl, tr, br, bl = points
        a, b = ((tr[0] - tl[0] + br[0] - bl[0]) / 2, (tr[1] - tl[1] + br[1] - bl[1]) / 2)  # long edges
        c, d = ((bl[0] - tl[0] + br[0] - tr[0]) / 2, (bl[1] - tl[1] + br[1] - tr[1]) / 2)  # short edges
        source = 'outline'
    else:
        try:
            width, height = (float(v) for v in _box(card, 'invalid_side_card'))
        except ValueError:
            raise ValueError('invalid_side_card') from None
        a, b, c, d = width, 0.0, 0.0, height
        source = 'box'
    # Locally the table projects affinely: a table direction (X across, Y away) lands at ((X + k Y) / s, -Y sin t / s)
    # in the photo (image y down), where k is the lean that perspective gives depth lines away from the photo's middle.
    # With the card turned by p, its long edge (8.56 cm) and short edge (5.398 cm, far side to near side) land at
    #     (a, b) = L / s (cos p + k sin p, -sin p sin t)      (c, d) = W / s (sin p - k cos p, cos p sin t)
    # four equations for s, t, p and k:  tan p = -(b / L) / (d / W),  s = 1 / ((a / L) cos p + (c / W) sin p),
    # sin t = d s / (W cos p). A turn and a lean are told apart, so an off-centre card does not bias the scale.
    if d <= 0:
        raise ValueError('invalid_side_card')
    turn = math.atan2(-b / CARD_LONG_CM, d / CARD_SHORT_CM)
    inverse_scale = (a / CARD_LONG_CM) * math.cos(turn) + (c / CARD_SHORT_CM) * math.sin(turn)
    if inverse_scale <= 0:
        raise ValueError('invalid_side_card')
    scale = 1 / inverse_scale
    sin_t = min(1.0, d * scale / (CARD_SHORT_CM * math.cos(turn)))
    elevation = math.degrees(math.asin(sin_t))
    if not ELEVATION_LIMITS_DEG[0] <= elevation <= ELEVATION_LIMITS_DEG[1]:
        raise ValueError('side_photo_angle_out_of_range')
    return {'cm_per_px': scale, 'elevation_deg': elevation, 'azimuth_deg': math.degrees(turn),
            'card_source': source}


def _side_box(box):
    try:
        width, height = (float(v) for v in _box(box, 'invalid_side_box'))
    except ValueError:
        raise ValueError('invalid_side_box') from None
    return width, height


def _spread_px(value):
    return max(BOX_TOLERANCE[0] * value, BOX_TOLERANCE[1])


def _scales(geometry):
    return geometry['cm_per_px'] * (1 - SCALE_TOLERANCE), geometry['cm_per_px'] * (1 + SCALE_TOLERANCE)


def _angles(geometry):
    return (math.radians(max(1.0, geometry['elevation_deg'] - ANGLE_TOLERANCE_DEG)),
            math.radians(min(89.0, geometry['elevation_deg'] + ANGLE_TOLERANCE_DEG)))


def footprint_depth(top_width_cm, top_height_cm, side_across_cm):
    """(low, high) footprint extent along the side camera's viewing direction.

    The top photo gives the two footprint extents but not which one points at the side camera; the dish's width in
    the side photo (across the view) tells: the depth is the other one. Round-ish dishes use the mean.
    """
    big, small = max(top_width_cm, top_height_cm), min(top_width_cm, top_height_cm)
    if big - small <= 0.15 * big:
        mid = (big + small) / 2
        spread = (big - small) / 2 + 0.05 * mid
    else:
        width_err = abs(side_across_cm - top_width_cm) / top_width_cm
        height_err = abs(side_across_cm - top_height_cm) / top_height_cm
        mid = top_height_cm if width_err <= height_err else top_width_cm
        spread = 0.1 * mid
    return max(0.1, mid - spread), mid + spread


def _proj(z, y, t, dist):
    """Height of a point in the side photo, in cm at the scale of the table under the dish (the card's scale).

    The point is y cm further from the camera and z cm above the table point under the dish; the camera is `dist` cm
    away from that point and looks down at it at elevation t. The focal length cancels out because heights are
    read with the card's scale.
    """
    sin_t, cos_t = math.sin(t), math.cos(t)
    return dist * (z * cos_t + y * sin_t) / (dist + y * cos_t - z * sin_t)


def _ys(b):
    return [b * (2 * i / (SAMPLES - 1) - 1) for i in range(SAMPLES)]


def _solve(f, target, low, high, iterations=40):
    """Smallest x in [low, high] with f(x) >= target, for a nondecreasing f (high when even f(high) is lower)."""
    if f(low) >= target:
        return low
    if f(high) < target:
        return high
    for _ in range(iterations):
        mid = (low + high) / 2
        low, high = (mid, high) if f(mid) < target else (low, mid)
    return high


def plate_extent(h, b, power, t, dist):
    """Side-photo height (cm, card scale) of a dish z = h (1 - r^power) whose footprint is 2b deep."""
    top = max(_proj(h * (1 - abs(y / b) ** power), y, t, dist) for y in _ys(b))
    return top - _proj(0.0, -b, t, dist)


def peak_height(extent, depth, t, power, dist):
    """(low, high) peak height of a dish of shape power `power` that spans `extent` cm in the side photo.

    A dish too flat to rise above its own far edge in this view could be anything from 0 up to the height at which
    its top starts to show above that edge.
    """
    b = depth / 2
    f = lambda h: plate_extent(h, b, power, t, dist)
    flat = f(0.0)
    if extent <= flat + 1e-9:
        return 0.0, _solve(f, flat + 1e-6, 0.0, 2 * b)
    h = _solve(f, extent, 0.0, 2 * extent + 2 * b)
    return h, h


def plate_estimate(side_box_px, geometry, depth_cm, profile, area_cm2):
    """((low, nominal, high) peak height cm, (low, nominal, high) volume mL) for a plate dish.

    The shape power p sets both how the silhouette reads (heap or slab) and the mean height over the footprint,
    h p / (p + 2), so the two are never guessed independently.
    """
    _, extent_px = _side_box(side_box_px)
    spread = _spread_px(extent_px)
    powers = PROFILE_POWER[profile]
    heights, volumes = [], []
    for e_px, s, t, depth, power, dist in product((extent_px - spread, extent_px + spread), _scales(geometry),
                                                  _angles(geometry), depth_cm, powers,
                                                  (CAMERA_DISTANCE_CM[0], CAMERA_DISTANCE_CM[2])):
        for h in peak_height(e_px * s, depth, t, power, dist):
            heights.append(h)
            volumes.extend(h * area_cm2 * float(fill) * power / (power + 2) for fill in PLATE_FILL)
    power = powers[1]
    h = _mid(peak_height(extent_px * geometry['cm_per_px'], _mid(depth_cm), math.radians(geometry['elevation_deg']),
                         power, CAMERA_DISTANCE_CM[1]))
    if max(heights) <= 0.2:
        raise ValueError('side_height_not_measurable')
    nominal = h * area_cm2 * float(_mid(PLATE_FILL)) * power / (power + 2)
    return (min(heights), h, max(heights)), (min(volumes), nominal, max(volumes))


def frustum_volume(depth_cm, rim_radius, base_radius):
    """mL of a straight-walled bowl from its bottom up to depth_cm (radius grows linearly to rim_radius)."""
    return math.pi * depth_cm / 3 * (base_radius ** 2 + base_radius * rim_radius + rim_radius ** 2)


def _food_rise(q, height, rim_r, base, bottom, t, dist):
    """How far (cm, card scale) the top of the food shows above the bowl's top edge in the side photo.

    q <= 0: the food surface is -q cm below the rim and its far edge shows through the opening; q > 0: a
    paraboloid heap q cm high rises above the rim. Nondecreasing in q.
    """
    rim_top = _proj(height, rim_r, t, dist)
    depth = height - bottom
    if q <= 0:
        z = height + q
        r = rim_r * base + (rim_r - rim_r * base) * (z - bottom) / depth
        return _proj(z, r, t, dist) - rim_top
    top = max(_proj(height + q * (1 - (y / rim_r) ** 2), y, t, dist) for y in _ys(rim_r))
    return max(top, rim_top) - rim_top


def _bowl_food(q, depth, rim_r, base, heap, rounded):
    base_r = rim_r * base
    if q <= 0:
        level = max(0.0, depth + q)
        return frustum_volume(level, base_r + (rim_r - base_r) * level / depth, base_r) * rounded
    return frustum_volume(depth, rim_r, base_r) * rounded + math.pi * rim_r ** 2 * q * heap


def bowl_volume(rim_cm, side_container_box_px, side_food_box_px, geometry):
    """((low, nominal, high) food volume mL, (low, nominal, high) inner depth cm) for a bowl dish.

    rim_cm is the rim diameter as read in the top photo with the card's scale. The rim stands H cm above the table,
    closer to the phone than the card, so it looks larger by C / (C - H) for a phone C cm up; once the side photo
    gives H, the rim is corrected (C is not known and spans TOP_CAMERA_CM). The bowl's extent in the side photo gives
    its height; where the top of the food shows relative to the bowl's top edge gives the fill level, or the heap
    above the rim.
    """
    _, container_px = _side_box(side_container_box_px)
    _side_box(side_food_box_px)
    rise_px = float(side_container_box_px[1]) - float(side_food_box_px[1])  # food top above the bowl's top edge
    spread = _spread_px(container_px)

    def solve(e_cm, rise_cm, t, base, bottom, dist, top_camera):
        rim_r = rim_cm / 2
        for _ in range(3):  # the height needs the rim, the rim correction needs the height
            height = _solve(lambda H: _proj(H, rim_r, t, dist) - _proj(0.0, -rim_r * base, t, dist), e_cm,
                            bottom + 0.1, 60.0)
            rim_r = rim_cm / 2 * max(0.3, (top_camera - height) / top_camera)
        depth = height - bottom
        f = lambda q: _food_rise(q, height, rim_r, base, bottom, t, dist)
        low_q = _solve(f, rise_cm, -depth, 3 * rim_r)
        high_q = _solve(f, rise_cm + 1e-6, -depth, 3 * rim_r)  # a heap can hide behind the far rim
        return depth, rim_r, (low_q, high_q)

    volumes, depths = [], []
    for e_px, s, t, base, bottom, dist, top_camera, rise in product(
            (container_px - spread, container_px + spread), _scales(geometry), _angles(geometry), BOWL_BASE_RATIO,
            BOWL_BOTTOM_CM, (CAMERA_DISTANCE_CM[0], CAMERA_DISTANCE_CM[2]), (TOP_CAMERA_CM[0], TOP_CAMERA_CM[2]),
            (rise_px - spread, rise_px + spread)):
        depth, rim_r, qs = solve(e_px * s, rise * s, t, base, bottom, dist, top_camera)
        if depth <= 0.5:
            continue
        depths.append(depth)
        volumes.extend(_bowl_food(q, depth, rim_r, base, heap, rounded)
                       for q, heap, rounded in product(qs, HEAP_FACTOR, ROUNDED_BOWL))
    if not depths:
        raise ValueError('side_bowl_not_measurable')
    s, base = geometry['cm_per_px'], _mid(BOWL_BASE_RATIO)
    depth, rim_r, (q, _) = solve(container_px * s, rise_px * s, math.radians(geometry['elevation_deg']), base,
                                 _mid(BOWL_BOTTOM_CM), CAMERA_DISTANCE_CM[1], TOP_CAMERA_CM[1])
    nominal = _bowl_food(q, max(depth, 0.6), rim_r, base, _mid(HEAP_FACTOR), _mid(ROUNDED_BOWL))
    return (min(volumes), nominal, max(volumes)), (min(depths), depth, max(depths))


def profile_for(food, container, gemini_profile=None):
    """'slab', 'mound' or 'generic'; Gemini's look ('flat' or 'mound') wins when it gave one."""
    if gemini_profile in ('flat', 'mound'):
        return 'slab' if gemini_profile == 'flat' else 'mound'
    if food in SLAB_FOODS:
        return 'slab'
    if food in FOOD_TEMPLATES and FOOD_TEMPLATES[food][0] == 'plate':
        return 'mound'
    return 'generic'


def multi_view_volume(food, top_box_px, top_card_px, side_box_px, side_geometry, container=None,
                      side_container_box_px=None, gemini_profile=None):
    """Volume interval in mL from the top photo (footprint) and the side photo (height), like rough_volume's output.

    Plate dishes use side_box_px (the food) for the height; bowl dishes also need side_container_box_px (the whole
    bowl, rim to base) for the depth, and side_box_px then marks the top of the visible food.
    """
    template = FOOD_TEMPLATES.get(food)
    if container in GENERIC_TEMPLATES and (template is None or template[0] != container):
        template = GENERIC_TEMPLATES[container]
    if template is None:
        raise ValueError('no_shape_template: ' + str(food))
    scale = float(cm_per_px(top_card_px))
    top_w, top_h = (float(v) * scale for v in _box(top_box_px, 'invalid_food_box'))
    side_w_px, _ = _side_box(side_box_px)
    shape = template[0]
    elevation = round(side_geometry['elevation_deg'], 1)
    if shape == 'plate':
        profile = profile_for(food, container, gemini_profile)
        depth = footprint_depth(top_w, top_h, side_w_px * side_geometry['cm_per_px'])
        h, ml = plate_estimate(side_box_px, side_geometry, depth, profile, top_w * top_h)
        volume = _interval3(*ml, D('1'))
        height = _interval3(*h, D('0.1'))
        assumptions = {'footprint_fill': PLATE_FILL, 'profile': profile, 'shape_power': PROFILE_POWER[profile],
                       'footprint_depth_cm': tuple(round(v, 1) for v in depth), 'elevation_deg': elevation}
    else:
        if side_container_box_px is None:
            raise ValueError('side_container_box_missing')
        rim = (top_w + top_h) / 2
        food_ml, depth = bowl_volume(rim, side_container_box_px, side_box_px, side_geometry)
        volume = _interval3(*food_ml, D('1'))
        height = _interval3(*depth, D('0.1'))
        assumptions = {'rim_diameter_cm': round(rim, 1), 'base_ratio': BOWL_BASE_RATIO, 'bottom_cm': BOWL_BOTTOM_CM,
                       'rounded_bowl': ROUNDED_BOWL, 'heap_factor': HEAP_FACTOR, 'elevation_deg': elevation}
    return {'food': food, 'shape': shape, 'method': 'top+side',
            'box_cm': (D(str(round(top_w, 1))), D(str(round(top_h, 1)))),
            'height_cm': height, 'volume_ml': volume, 'assumptions': assumptions, 'coverage_validated': False}
