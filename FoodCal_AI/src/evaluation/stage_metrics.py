"""Stage-level metrics (rubric item 2) and the red-line audit (rubric item 3a).

All inputs are plain rows prepared by people (ground-truth boxes, weighed grams, gold TFDA values) or
copied from system outputs. Exact Decimal arithmetic for grams and kcal; inputs are never modified.
"""
from decimal import Decimal

from src.portion.contract import METHOD_KINDS
from .meal_metrics import _decimal, _ratio, _tier

IOU_THRESHOLD = Decimal('0.5')


def _box(row, code):
    box = [_decimal(row[k], code) for k in ('x_min', 'y_min', 'x_max', 'y_max')]
    if not (box[0] < box[2] and box[1] < box[3]):
        raise ValueError(code)
    return box


def iou(a, b):
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return Decimal(0)
    inter = width * height
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def detection_rows(image_id, detection_result):
    """Prediction rows from a Detection schema 1 result (boxes in original image pixels)."""
    return [{'image_id': image_id, 'class_name': d['class_name'], 'confidence': str(d['confidence']),
             **{k: str(d['bounding_box'][k]) for k in ('x_min', 'y_min', 'x_max', 'y_max')}}
            for d in detection_result.get('detections', [])]


def detection_metrics(ground_truth, predictions, *, model_classes=None):
    """Greedy one-to-one matching per image and class, highest confidence first, IoU >= 0.5.

    Ground-truth items whose class the model cannot output stay false negatives, and taxonomy coverage
    reports how many of them there are.
    """
    gt = {}
    for row in ground_truth:
        gt.setdefault((row['image_id'], row['class_name']), []).append(_box(row, 'invalid_ground_truth_box'))
    preds = sorted(((row['image_id'], row['class_name'], _decimal(row['confidence'], 'invalid_confidence'),
                     _box(row, 'invalid_prediction_box')) for row in predictions), key=lambda p: -p[2])
    used = {key: [False] * len(boxes) for key, boxes in gt.items()}
    true_positive = 0
    for image_id, class_name, _, box in preds:
        candidates = gt.get((image_id, class_name), [])
        best, best_iou = None, IOU_THRESHOLD
        for index, other in enumerate(candidates):
            overlap = iou(box, other)
            if not used[(image_id, class_name)][index] and overlap >= best_iou:
                best, best_iou = index, overlap
        if best is not None:
            used[(image_id, class_name)][best] = True
            true_positive += 1
    total_gt = sum(len(v) for v in gt.values())
    precision, recall = _ratio(true_positive, len(preds)), _ratio(true_positive, total_gt)
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else (Decimal(0) if total_gt else None)
    coverage = None
    if model_classes is not None and total_gt:
        coverage = _ratio(sum(len(v) for (_, name), v in gt.items() if name in set(model_classes)), total_gt)
    return {'ground_truth_items': total_gt, 'predictions': len(preds), 'true_positives': true_positive,
            'precision': precision, 'recall': recall, 'f1': f1, 'taxonomy_coverage': coverage}


def portion_metrics(rows):
    """Visible-item mass intervals vs weighed grams; unavailable items lower the answer rate only."""
    answered, errors, covered, total = 0, [], 0, 0
    for row in rows:
        total += 1
        weighed = _decimal(row['weighed_g'], 'invalid_weighed_mass')
        if weighed == 0:
            raise ValueError('invalid_weighed_mass')
        if row['status'] == 'unavailable':
            continue
        if row['status'] != 'estimated':
            raise ValueError('invalid_portion_status')
        low, high = _decimal(row['low_g'], 'invalid_mass_interval'), _decimal(row['high_g'], 'invalid_mass_interval')
        if low > high:
            raise ValueError('invalid_mass_interval')
        answered += 1
        errors.append(abs((low + high) / 2 - weighed) / weighed)
        covered += low <= weighed <= high
    return {'items': total, 'answered_items': answered, 'answer_rate': _ratio(answered, total),
            'mape_midpoint': sum(errors) / len(errors) if errors else None,
            'interval_coverage': _ratio(covered, answered)}


def nutrition_mapping_metrics(rows, *, tolerance=Decimal('0.10')):
    """Share of items whose chosen TFDA kcal/100 g is within 10% of the gold entry; unmapped counts as a miss."""
    within, total = 0, 0
    for row in rows:
        total += 1
        gold = _decimal(row['gold_kcal_per_100g'], 'invalid_gold_value')
        if gold == 0:
            raise ValueError('invalid_gold_value')
        system = row.get('system_kcal_per_100g')
        if system in (None, ''):
            continue
        within += abs(_decimal(system, 'invalid_system_value') - gold) / gold <= tolerance
    return {'items': total, 'within_tolerance': within, 'within_share': _ratio(within, total)}


def score_stages(detection=None, portion=None, mapping=None, *, oracle_report_attached=False):
    d = Decimal
    points = {
        '2a_detection': _tier(detection and detection['f1'], ((d('0.80'), 5), (d('0.65'), 3), (d('0.50'), 1)),
                              higher_is_better=True),
        '2b_portion': 0,
        '2c_mapping': _tier(mapping and mapping['within_share'], ((d('0.90'), 5), (d('0.75'), 3), (d('0.50'), 1)),
                            higher_is_better=True)}
    if portion and portion['mape_midpoint'] is not None:
        mape, cover = portion['mape_midpoint'], portion['interval_coverage']
        points['2b_portion'] = (5 if mape <= d('0.20') and cover >= d('0.80') else
                                3 if mape <= d('0.30') and cover >= d('0.70') else 1 if mape <= d('0.45') else 0)
    subtotal = sum(points.values())
    notes = []
    if not oracle_report_attached and subtotal > 10:
        subtotal, notes = 10, ['oracle error-decomposition report missing: item 2 capped at 10']
    return {**points, 'subtotal': subtotal, 'max': 15, 'notes': notes}


def audit_energy_result(result):
    """Red-line violations (R1-R4) in one photo_energy_estimate block; [] means none found."""
    violations = []
    if result.get('nutrition_eligible') is not False or result.get('production_eligible') is not False:
        violations.append('eligibility_flag_raised')
    assessment = result.get('portion_assessment') or {}
    portion_items = assessment.get('items', [])
    recipe_keys = {(p['evidence'].get('evidence_type'), p['evidence'].get('evidence_sha256'))
                   for p in result.get('excluded_recipe_priors', [])}
    for item in portion_items:
        evidence = item.get('evidence', {})
        if evidence.get('evidence_type') == 'inferred_from_recipe' or \
                (evidence.get('evidence_type'), evidence.get('evidence_sha256')) in recipe_keys:
            violations.append('R1_recipe_prior_in_visual_items')
        if item.get('status') == 'estimated' and (item.get('method') or {}).get('kind') not in METHOD_KINDS:
            violations.append('R2_non_photo_mass_source')
        if item.get('status') != 'estimated' and item.get('mass_g') is not None:
            violations.append('R3_mass_shown_without_estimate')
    if assessment.get('photo_portion_grams') is not None:
        violations.append('R3_scalar_photo_grams_filled')
    items = result.get('items', [])
    for item in items:
        if item.get('status') != 'computed' and item.get('energy') is not None:
            violations.append('R3_energy_shown_for_unavailable_item')
    if result.get('status') == 'computed':
        computed = [i for i in items if i.get('status') == 'computed']
        if not items or len(computed) != len(items):
            violations.append('R3_total_with_unavailable_items')
        try:
            low = sum((Decimal(i['energy']['low']) for i in computed), Decimal(0))
            high = sum((Decimal(i['energy']['high']) for i in computed), Decimal(0))
            if Decimal(result['energy']['low']) != low or Decimal(result['energy']['high']) != high:
                violations.append('R1_total_differs_from_visible_items')
        except (KeyError, TypeError, ArithmeticError):
            violations.append('R4_energy_values_unreadable')
        for item in computed:
            if not item.get('estimate_id') or not item.get('basis_id'):
                violations.append('R4_untraceable_item')
        if any(not (p.get('evidence') or {}).get('evidence_id') or not p.get('estimate_id')
               for p in portion_items):
            violations.append('R4_untraceable_evidence')
    elif result.get('energy') is not None or result.get('unit') is not None:
        violations.append('R3_number_shown_when_unavailable')
    return sorted(set(violations))


def audit_results(results):
    """meal_id -> violations for a run_meal_pipeline results.json; meals with none are omitted."""
    out = {}
    for meal_id, result in results.items():
        if result.get('contract') != 'foodcal.photo_energy_estimate':
            if result.get('status') != 'unavailable':
                out[meal_id] = ['R3_unknown_result_shape']
            continue
        violations = audit_energy_result(result)
        if violations:
            out[meal_id] = violations
    return out
