"""Rough portion rows for the web page: card-scaled volume x density, or grams the user typed in.

Pure functions only (no Streamlit), so the page logic is unit-tested. Every number here is rough (粗估):
shape templates and published densities, never validated against weighed food. The strict
photo_energy_estimate result is not touched.
"""
import csv
from decimal import Decimal
from pathlib import Path

from src.portion.nutrition_model import DEFAULT_COMPONENTS, energy_for_grams, food_nutrition
from src.portion.rough_volume import REFERENCE_COLUMNS, kcal_for_grams, load_food_reference, rough_portion
from .explain import food_name

NOTES = {
    'card_missing': '先在照片上框出學生證，才能估體積',
    'card_box_shape_not_a_card': '框的長寬比不像學生證（約 1.6 : 1），請重新框',
    'invalid_card_box': '學生證的框不正確，請重新框',
    'no_shape_template': '這道菜還沒有形狀模板，請自己填重量',
    'density_missing': '密度表還沒有這道菜，只能估體積；可以自己填重量',
    'kcal_missing': '熱量表還沒有這道菜，只能顯示重量',
    'composition_partly_unknown': '有些成分不在食材表裡，熱量只算認得的部分',
}


def read_food_reference_csv(path):
    """Sourced density / kcal table, or {} when the file does not exist yet."""
    path = Path(path)
    if not path.is_file():
        return {}
    with path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != REFERENCE_COLUMNS:
            raise ValueError('food_reference_columns_mismatch')
        return load_food_reference(list(reader))


def _point(value):
    return {'low': value, 'mid': value, 'high': value}


def nutrition_of(item):
    """Composition (from Gemini, else the default for a known dish) -> food_nutrition result, or None."""
    components, method = item.get('components'), item.get('cooking_method')
    if not components and item.get('class_name') in DEFAULT_COMPONENTS:
        components, default_method = DEFAULT_COMPONENTS[item['class_name']]
        method = method or default_method
    return food_nutrition(components, method or 'none') if components else None


def _scale(interval, factor):
    return {k: (interval[k] * factor).quantize(Decimal('1')) for k in ('low', 'mid', 'high')}


def _sum(intervals):
    return {k: sum(i[k] for i in intervals) for k in ('low', 'mid', 'high')}


def portion_rows(items, card_box, reference, user_grams):
    """(rows, total or None, missing names) for recognised and hand-added foods.

    Energy is split into 'kcal' (visible ingredients) and 'hidden_kcal' (recipe-based oil and sugar); the total
    carries both under 'visible' and 'hidden' next to its own low/mid/high. Items with a composition use the
    nutrition model; others fall back to the per-food reference table.
    user_grams maps detection_id -> grams typed by the user (None or 0 means not typed).
    """
    rows = []
    for detection in items:
        class_name, detection_id = detection['class_name'], detection['detection_id']
        box = [detection['bounding_box'][k] for k in ('x_min', 'y_min', 'x_max', 'y_max')]
        name = detection.get('display_name')
        nutrition = nutrition_of(detection)
        row = {'id': detection_id, 'class_name': class_name,
               'name': name or food_name(class_name),
               'volume_ml': None, 'grams': None, 'grams_source': None, 'kcal': None, 'hidden_kcal': None,
               'nutrition': nutrition, 'notes': []}
        if card_box is None:
            row['notes'].append('card_missing')
        else:
            try:
                rough = rough_portion(class_name, box, card_box, reference, detection.get('container'))
            except ValueError as error:
                row['notes'].append(str(error).split(':')[0])
            else:
                row['volume_ml'] = rough['volume_ml']
                if nutrition is not None:
                    row['grams'], row['grams_source'] = _scale(rough['volume_ml'], nutrition['density']), 'rough'
                    row['kcal'], row['hidden_kcal'] = energy_for_grams(row['grams'], nutrition)
                elif rough['grams'] is not None:
                    row['grams'], row['grams_source'] = rough['grams'], 'rough'
                    row['kcal'] = rough['kcal']
                else:
                    row['notes'].append('density_missing')
        typed = user_grams.get(detection_id)
        if typed:
            grams = Decimal(str(typed)).quantize(Decimal('1'))
            row['grams'], row['grams_source'] = _point(grams), 'user'
            if nutrition is not None:
                row['kcal'], row['hidden_kcal'] = energy_for_grams(row['grams'], nutrition)
            else:
                kcal = kcal_for_grams(grams, (reference or {}).get(class_name))
                row['kcal'], row['hidden_kcal'] = (_point(kcal) if kcal is not None else None), None
            row['notes'] = [n for n in row['notes'] if n not in ('card_missing', 'density_missing', 'no_shape_template')]
        if row['grams'] is not None and row['kcal'] is None:
            row['notes'].append('kcal_missing')
        if nutrition is not None and nutrition['known_share'] < Decimal('0.8'):
            row['notes'].append('composition_partly_unknown')
        rows.append(row)
    missing = [r['name'] for r in rows if r['kcal'] is None]
    total = None
    if rows and not missing:
        visible = _sum([r['kcal'] for r in rows])
        hidden = _sum([r['hidden_kcal'] for r in rows if r['hidden_kcal'] is not None]) if any(
            r['hidden_kcal'] is not None for r in rows) else {'low': 0, 'mid': 0, 'high': 0}
        total = {**_sum([visible, hidden]), 'visible': visible, 'hidden': hidden}
    return rows, total, missing


def range_text(interval, unit):
    if interval is None:
        return '—'
    if interval['low'] == interval['high']:
        return f"{interval['mid']} {unit}"
    return f"約 {interval['mid']} {unit}（{interval['low']}–{interval['high']}）"


def note_text(code):
    return NOTES.get(code, code)
