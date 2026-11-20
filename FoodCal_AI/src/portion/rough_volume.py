"""Rough food volume from one top-down photo with a student card, then grams = volume x density.

Method (docs/rough_volume_estimation_v0.md): the ISO ID-1 card (85.60 x 53.98 mm) gives the photo scale, and
each food uses a geometric shape template, as in reference-object + shape-model food volume studies:

- plate foods: volume = box area x footprint fill x average thickness;
- bowl foods:  volume = bowl volume (spherical cap from the rim diameter and a depth ratio) x fill level.

Every parameter is a range, so every result is a wide interval with a geometric-mean point value. Nothing
here was fitted to weighed food: results are labeled rough (粗估), carry coverage_validated = False and are
never PortionEstimates, so the strict photo_energy_estimate path is unchanged. Users can always replace the
grams with their own number. Densities and kcal per 100 g come from a sourced reference table people fill
from published data; foods missing from it get a volume only.
"""
from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math

from .reference_card import CARD_AREA_CM2

D = Decimal
PLATE_FILL = (D('0.6'), D('0.85'))        # food footprint / its box
BOWL_DEPTH_RATIO = (D('0.35'), D('0.5'))  # bowl depth / rim diameter
BOWL_FILL = (D('0.6'), D('0.9'))          # food volume / bowl volume

# Average thickness (cm) over the food footprint for plate foods; None marks bowl foods. Rough priors for
# Taiwanese campus dishes, to be replaced by measured values when the team has them.
FOOD_TEMPLATES = {
    'Over-easy egg': ('plate', (D('0.8'), D('1.5'))),
    'bawan': ('plate', (D('2.5'), D('4'))),
    'beef_noodles': ('bowl', None),
    'braised_napa_cabbage': ('bowl', None),
    'braised_pork_over_rice': ('bowl', None),
    'chicken_mushroom_soup': ('bowl', None),
    'chinese_pickled_cucumber': ('plate', (D('1'), D('2.5'))),
    'cold_noodle': ('plate', (D('2'), D('4'))),
    'deep-fried_chicken_cutlets': ('plate', (D('1'), D('2'))),
    'egg_pancake_roll': ('plate', (D('1.5'), D('3'))),
    'fried-spanish_mackerel_thick_soup': ('bowl', None),
    'fried_instant_noodles': ('plate', (D('2'), D('4'))),
    'fried_rice_noodles': ('plate', (D('2'), D('4'))),
    'loofah': ('plate', (D('1.5'), D('3'))),
    'turkey_rice': ('bowl', None),
    'cooked_rice_region': ('plate', (D('1.5'), D('3'))),
    'cooked_noodle_region': ('plate', (D('2'), D('4'))),
    'visible_fried_egg': ('plate', (D('0.8'), D('1.5'))),
}
REFERENCE_COLUMNS = ('food', 'grams', 'volume_ml', 'density_source_url', 'kcal_per_100g', 'kcal_source_url',
                     'retrieved_at', 'entered_by')
DENSITY_RANGE = (D('0.05'), D('3'))


def _number(value, code):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError(code)
    try:
        number = D(str(value).strip())
    except ArithmeticError:
        raise ValueError(code) from None
    if not number.is_finite() or number < 0:
        raise ValueError(code)
    return number


def _box(box, code):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError(code)
    x0, y0, x1, y1 = (_number(v, code) for v in box)
    if not (x0 < x1 and y0 < y1):
        raise ValueError(code)
    return x1 - x0, y1 - y0


def cm_per_px(card_box):
    """Photo scale from an axis-aligned box around the card (edges roughly parallel to the photo)."""
    width, height = _box(card_box, 'invalid_card_box')
    ratio = max(width, height) / min(width, height)
    if not D('1.3') <= ratio <= D('1.9'):  # a card is 1.586:1; a very different box is not a card
        raise ValueError('card_box_shape_not_a_card')
    return (CARD_AREA_CM2 / (width * height)).sqrt()


def _cap(diameter, depth):
    radius = diameter / 2
    return D(str(math.pi)) * depth * (3 * radius * radius + depth * depth) / 6


def _interval(low, high, step=D('1')):
    low, high = low.quantize(step, rounding=ROUND_FLOOR), high.quantize(step, rounding=ROUND_CEILING)
    mid = (low * high).sqrt().quantize(step) if low > 0 else (high / 2).quantize(step)
    return {'low': low, 'mid': mid, 'high': high}


def rough_volume(food, box_px, card_box_px):
    """Volume interval in mL for one detected food box, using the card in the same photo for scale."""
    template = FOOD_TEMPLATES.get(food)
    if template is None:
        raise ValueError('no_shape_template: ' + str(food))
    scale = cm_per_px(card_box_px)
    width, height = (v * scale for v in _box(box_px, 'invalid_food_box'))
    shape, thickness = template
    if shape == 'plate':
        area = width * height
        volume = _interval(area * PLATE_FILL[0] * thickness[0], area * PLATE_FILL[1] * thickness[1])
        assumptions = {'footprint_fill': PLATE_FILL, 'thickness_cm': thickness}
    else:
        diameter = (width + height) / 2
        volume = _interval(_cap(diameter, diameter * BOWL_DEPTH_RATIO[0]) * BOWL_FILL[0],
                           _cap(diameter, diameter * BOWL_DEPTH_RATIO[1]) * BOWL_FILL[1])
        assumptions = {'rim_diameter_cm': diameter.quantize(D('0.1')), 'depth_ratio': BOWL_DEPTH_RATIO,
                       'fill_level': BOWL_FILL}
    return {'food': food, 'shape': shape, 'box_cm': (width.quantize(D('0.1')), height.quantize(D('0.1'))),
            'volume_ml': volume, 'assumptions': assumptions, 'coverage_validated': False}


def load_food_reference(rows):
    """food -> {'density', 'kcal_per_100g', sources}; a value is accepted only with its source URL."""
    table = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(REFERENCE_COLUMNS):
            raise ValueError('food_reference_columns_mismatch')
        row = {k: (row[k] or '').strip() for k in REFERENCE_COLUMNS}
        food = row['food']
        if not food or food in table:
            raise ValueError('food_reference_food_missing_or_duplicate: ' + food)
        if not row['entered_by']:
            raise ValueError('food_reference_entered_by_required: ' + food)
        try:
            date.fromisoformat(row['retrieved_at'])
        except ValueError:
            raise ValueError('food_reference_retrieved_at_must_be_iso_date: ' + food) from None
        entry = {'density': None, 'kcal_per_100g': None, 'density_source_url': None, 'kcal_source_url': None}
        if row['grams'] or row['volume_ml']:
            grams, volume = _number(row['grams'], 'invalid_grams'), _number(row['volume_ml'], 'invalid_volume')
            if not grams or not volume:
                raise ValueError('density_needs_positive_grams_and_volume: ' + food)
            density = (grams / volume).quantize(D('0.001'))
            if not DENSITY_RANGE[0] <= density <= DENSITY_RANGE[1]:
                raise ValueError('implausible_density: ' + food)
            if not row['density_source_url'].startswith(('https://', 'http://')):
                raise ValueError('density_source_url_required: ' + food)
            entry.update(density=density, density_source_url=row['density_source_url'])
        if row['kcal_per_100g']:
            kcal = _number(row['kcal_per_100g'], 'invalid_kcal')
            if not kcal or kcal > 950:  # pure fat is about 900 kcal / 100 g
                raise ValueError('implausible_kcal: ' + food)
            if not row['kcal_source_url'].startswith(('https://', 'http://')):
                raise ValueError('kcal_source_url_required: ' + food)
            entry.update(kcal_per_100g=kcal, kcal_source_url=row['kcal_source_url'])
        table[food] = entry
    return table


def kcal_for_grams(grams, entry):
    if not entry or entry.get('kcal_per_100g') is None:
        return None
    return (_number(grams, 'invalid_grams') * entry['kcal_per_100g'] / 100).quantize(D('1'))


def rough_portion(food, box_px, card_box_px, reference):
    """rough_volume plus grams (volume x density) and kcal when the reference table has them."""
    out = rough_volume(food, box_px, card_box_px)
    entry = (reference or {}).get(food)
    out['grams'] = out['kcal'] = None
    if entry and entry['density'] is not None:
        volume = out['volume_ml']
        out['grams'] = _interval(volume['low'] * entry['density'], volume['high'] * entry['density'])
        if entry['kcal_per_100g'] is not None:
            out['kcal'] = _interval(out['grams']['low'] * entry['kcal_per_100g'] / 100,
                                    out['grams']['high'] * entry['kcal_per_100g'] / 100)
    out['reference'] = entry
    return out
