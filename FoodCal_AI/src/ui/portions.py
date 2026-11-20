"""Rough portion rows for the web page: card-scaled volume x density, or grams the user typed in.

Pure functions only (no Streamlit), so the page logic is unit-tested. Every number here is rough (粗估):
shape templates and published densities, never validated against weighed food. The strict
photo_energy_estimate result is not touched.
"""
import csv
from decimal import Decimal
from pathlib import Path

from src.portion.rough_volume import REFERENCE_COLUMNS, kcal_for_grams, load_food_reference, rough_portion
from .explain import food_name

NOTES = {
    'card_missing': '先在照片上框出學生證，才能估體積',
    'card_box_shape_not_a_card': '框的長寬比不像學生證（約 1.6 : 1），請重新框',
    'invalid_card_box': '學生證的框不正確，請重新框',
    'no_shape_template': '這道菜還沒有形狀模板，請自己填重量',
    'density_missing': '密度表還沒有這道菜，只能估體積；可以自己填重量',
    'kcal_missing': '熱量表還沒有這道菜，只能顯示重量',
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


def portion_rows(items, card_box, reference, user_grams):
    """(rows, total_kcal or None, missing names) for detections and hand-added foods.

    items: dicts with detection_id, class_name and an xyxy bounding_box in original image pixels;
    user_grams maps detection_id -> grams typed by the user (None or 0 means not typed).
    """
    rows = []
    for detection in items:
        class_name, detection_id = detection['class_name'], detection['detection_id']
        box = [detection['bounding_box'][k] for k in ('x_min', 'y_min', 'x_max', 'y_max')]
        name = detection.get('display_name')
        row = {'id': detection_id, 'class_name': class_name,
               'name': f'{name}（{class_name}）' if name else food_name(class_name),
               'volume_ml': None, 'grams': None, 'grams_source': None, 'kcal': None, 'notes': []}
        if card_box is None:
            row['notes'].append('card_missing')
        else:
            try:
                rough = rough_portion(class_name, box, card_box, reference, detection.get('container'))
            except ValueError as error:
                row['notes'].append(str(error).split(':')[0])
            else:
                row['volume_ml'] = rough['volume_ml']
                if rough['grams'] is not None:
                    row['grams'], row['grams_source'] = rough['grams'], 'rough'
                    row['kcal'] = rough['kcal']
                else:
                    row['notes'].append('density_missing')
        typed = user_grams.get(detection_id)
        if typed:
            grams = Decimal(str(typed)).quantize(Decimal('1'))
            row['grams'], row['grams_source'] = _point(grams), 'user'
            kcal = kcal_for_grams(grams, (reference or {}).get(class_name))
            row['kcal'] = _point(kcal) if kcal is not None else None
            row['notes'] = [n for n in row['notes'] if n not in ('card_missing', 'density_missing', 'no_shape_template')]
        if row['grams'] is not None and row['kcal'] is None:
            row['notes'].append('kcal_missing')
        rows.append(row)
    missing = [r['name'] for r in rows if r['kcal'] is None]
    total = None
    if rows and not missing:
        total = {k: sum(r['kcal'][k] for r in rows) for k in ('low', 'mid', 'high')}
    return rows, total, missing


def range_text(interval, unit):
    if interval is None:
        return '—'
    if interval['low'] == interval['high']:
        return f"{interval['mid']} {unit}"
    return f"約 {interval['mid']} {unit}（{interval['low']}–{interval['high']}）"


def note_text(code):
    return NOTES.get(code, code)
