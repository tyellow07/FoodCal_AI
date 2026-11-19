"""Meal-level calorie evaluation (docs/meal_evaluation_protocol_v0.md).

Reference calories come from weighed visible components x TFDA per-100 g values, prepared outside this code.
The system is scored only on intervals it actually produced; abstentions are counted separately so that
refusing everything can never look accurate. Exact Decimal arithmetic; inputs are never modified.
"""
from decimal import Decimal

SUBSETS = ('main', 'should_abstain')
MIN_ANSWERED = 10


def _decimal(value, code):
    try:
        number = Decimal(str(value).strip())
    except (ArithmeticError, ValueError):
        raise ValueError(code) from None
    if not number.is_finite() or number < 0:
        raise ValueError(code)
    return number


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _ratio(numerator, denominator):
    return None if not denominator else Decimal(numerator) / Decimal(denominator)


def prediction_from_energy_estimate(meal_id, energy):
    """Prediction row from a photo_energy_estimate block; only computed kcal totals count as answers."""
    if isinstance(energy, dict) and energy.get('status') == 'computed' and energy.get('unit') == 'kcal':
        return {'meal_id': meal_id, 'status': 'computed', 'low_kcal': energy['energy']['low'],
                'high_kcal': energy['energy']['high']}
    return {'meal_id': meal_id, 'status': 'unavailable', 'low_kcal': None, 'high_kcal': None}


def evaluate_meals(references, predictions):
    """Return metrics for the main subset and the should-abstain subset."""
    refs = {}
    for row in references:
        if row.get('subset') not in SUBSETS or not str(row.get('meal_id', '')).strip():
            raise ValueError('invalid_reference_row')
        if row['meal_id'] in refs:
            raise ValueError('duplicate_reference_meal')
        kcal = _decimal(row['reference_kcal'], 'invalid_reference_kcal') if row['subset'] == 'main' else None
        if row['subset'] == 'main' and kcal == 0:
            raise ValueError('invalid_reference_kcal')
        refs[row['meal_id']] = {'subset': row['subset'], 'kcal': kcal, 'group': row.get('source_group')}
    preds = {}
    for row in predictions:
        if row.get('meal_id') not in refs:
            raise ValueError('prediction_for_unknown_meal')
        if row['meal_id'] in preds:
            raise ValueError('duplicate_prediction')
        if row.get('status') == 'computed':
            low = _decimal(row['low_kcal'], 'invalid_prediction_interval')
            high = _decimal(row['high_kcal'], 'invalid_prediction_interval')
            if low > high or high == 0:
                raise ValueError('invalid_prediction_interval')
            preds[row['meal_id']] = (low, high)
        elif row.get('status') == 'unavailable':
            preds[row['meal_id']] = None
        else:
            raise ValueError('invalid_prediction_status')
    main = {k: v for k, v in refs.items() if v['subset'] == 'main'}
    abstain = [k for k, v in refs.items() if v['subset'] == 'should_abstain']
    answered = [k for k in main if preds.get(k) is not None]  # missing prediction counts as unavailable
    errors, widths, covered = [], [], 0
    for meal in answered:
        low, high = preds[meal]
        truth = main[meal]['kcal']
        errors.append(abs((low + high) / 2 - truth) / truth)
        widths.append((high - low) / truth)
        covered += low <= truth <= high
    baseline = None
    if answered:
        constant = _median([main[k]['kcal'] for k in main])
        baseline = sum(abs(constant - main[k]['kcal']) / main[k]['kcal'] for k in answered) / len(answered)
    return {'main_meals': len(main), 'answered_meals': len(answered), 'answer_rate': _ratio(len(answered), len(main)),
            'mape_midpoint': sum(errors) / len(errors) if errors else None,
            'interval_coverage': _ratio(covered, len(answered)),
            'median_relative_width': _median(widths) if widths else None,
            'baseline_b0_mape': baseline,
            'source_groups': len({v['group'] for v in main.values() if v['group']}),
            'should_abstain_meals': len(abstain),
            'correct_abstention_rate': _ratio(sum(preds.get(k) is None for k in abstain), len(abstain))}


def _tier(value, tiers, *, higher_is_better):
    if value is None:
        return 0
    for threshold, points in tiers:
        if (value >= threshold) if higher_is_better else (value <= threshold):
            return points
    return 0


def score_rubric(metrics):
    """Points for rubric items 1a-1c, 3b and 3c (other items need human or process evidence)."""
    d = Decimal
    enough = metrics['answered_meals'] >= MIN_ANSWERED
    a = _tier(metrics['mape_midpoint'], ((d('0.20'), 10), (d('0.30'), 7), (d('0.40'), 4)), higher_is_better=False)
    if (a > 4 and metrics['baseline_b0_mape'] is not None
            and metrics['mape_midpoint'] >= metrics['baseline_b0_mape']):
        a = 4  # not better than always guessing the median meal
    b = _tier(metrics['interval_coverage'], ((d('0.80'), 8), (d('0.70'), 4)), higher_is_better=True)
    c = _tier(metrics['median_relative_width'], ((d('0.60'), 7), (d('1.00'), 4), (d('1.50'), 1)),
              higher_is_better=False)
    item1 = {'1a_mape': a, '1b_coverage': b, '1c_width': c} if enough else {'1a_mape': 0, '1b_coverage': 0, '1c_width': 0}
    answer = _tier(metrics['answer_rate'], ((d('0.70'), 5), (d('0.50'), 3), (d('0.30'), 1)), higher_is_better=True)
    abstain = (_tier(metrics['correct_abstention_rate'], ((d('0.90'), 5), (d('0.75'), 3), (d('0.50'), 1)),
                     higher_is_better=True) if metrics['should_abstain_meals'] >= 10 else 0)
    notes = []
    if not enough:
        notes.append(f'answered_meals < {MIN_ANSWERED}: item 1 scored 0')
    if metrics['should_abstain_meals'] < 10:
        notes.append('should_abstain subset < 10 photos: item 3c scored 0')
    if metrics['main_meals'] < 30 or metrics['source_groups'] < 3:
        notes.append('test set below protocol (>= 30 meals, >= 3 source groups): items 1-2 not valid')
    return {**item1, '3b_answer_rate': answer, '3c_correct_abstention': abstain,
            'subtotal': sum(item1.values()) + answer + abstain, 'max': 35, 'notes': notes}
