"""Reference-card area ratio: the first calibrated visual portion method (method_id reference_card_area_ratio v0).

Protocol: a top-down photo with an ISO/IEC 7810 ID-1 card (student ID / EasyCard, 85.60 x 53.98 mm) lying flat
next to the plate, edges roughly parallel to the image. The visible ingredient box area in pixels is converted
to cm^2 with the card's pixel area, and grams-per-cm^2 ratios learned from directly weighed samples give an
interval. Intervals use split-conformal order statistics per ingredient; reported coverage comes from
leave-one-source-group-out evaluation, so no sample is ever scored by a fit that saw its own restaurant/day.

Nothing here weighs food, reads images or approves anything. Without real weighed samples no record exists and
every photo mass stays unavailable. A fitted record starts as draft; the contract floors (>= 30 held-out
samples, >= 2 source groups, measured coverage >= nominal, independent approval) still decide readiness.
Recipe quantities, standard servings, dish labels and detection confidence are never inputs.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math

from .contract import (SCHEMA_VERSION, USAGE, canonical_sha256, decimal_text, decimal_value, evidence_blocker,
                       evidence_identity, region_box, text, validate_calibration, validate_portion_estimate)

METHOD = {'kind': 'calibrated_visual_estimate', 'method_id': 'reference_card_area_ratio', 'method_version': '0'}
MODEL_CONTRACT = 'foodcal.reference_card_model'
CARD_WIDTH_MM, CARD_HEIGHT_MM = Decimal('85.60'), Decimal('53.98')
CARD_AREA_CM2 = CARD_WIDTH_MM * CARD_HEIGHT_MM / 100
SAMPLE_FIELDS = frozenset({'sample_id', 'source_group', 'ingredient_candidate', 'region_px', 'card_px', 'mass_g',
                           'scale_resolution_g', 'measured_by'})
GRAM_STEP = Decimal('0.1')
RATIO_STEP = Decimal('0.000001')


def _box_area(box, code):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError(code)
    values = []
    for value in box:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(code)
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(code)
        try:
            number = Decimal(str(value))
        except ArithmeticError:
            raise ValueError(code) from None
        if not number.is_finite() or number < 0:
            raise ValueError(code)
        values.append(number)
    if not (values[0] < values[2] and values[1] < values[3]):
        raise ValueError(code)
    return (values[2] - values[0]) * (values[3] - values[1])


def visible_area_cm2(region_px, card_px):
    """Visible box area in cm^2 scaled by the reference card in the same photo."""
    return _box_area(region_px, 'invalid_region_box') * CARD_AREA_CM2 / _box_area(card_px, 'invalid_card_box')


def _validate_samples(samples):
    if not isinstance(samples, (list, tuple)) or not samples:
        raise ValueError('weighed_samples_required')
    seen = set()
    rows = []
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != SAMPLE_FIELDS:
            raise ValueError('sample_fields_mismatch')
        if not all(text(sample[k]) for k in ('sample_id', 'source_group', 'ingredient_candidate', 'measured_by')):
            raise ValueError('sample_identity_missing')
        if sample['sample_id'] in seen:
            raise ValueError('duplicate_sample_id')
        seen.add(sample['sample_id'])
        mass, resolution = decimal_value(sample['mass_g']), decimal_value(sample['scale_resolution_g'])
        if mass is None or mass == 0:
            raise ValueError('positive_weighed_mass_required')
        if resolution is None or resolution == 0 or resolution > 1:
            raise ValueError('scale_resolution_must_be_at_most_1g')
        rows.append({'id': sample['sample_id'], 'group': sample['source_group'],
                     'ingredient': sample['ingredient_candidate'], 'mass': mass,
                     'ratio': mass / visible_area_cm2(sample['region_px'], sample['card_px'])})
    return rows


def _nominal(value):
    nominal = decimal_value(value)
    if nominal is None or not 0 < nominal < 1:
        raise ValueError('nominal_coverage_must_be_in_0_1')
    return nominal


def conformal_ratio_bounds(ratios, nominal):
    """Order-statistic bounds giving >= nominal coverage for an exchangeable new ratio, or None if too few."""
    ordered = sorted(ratios)
    n = len(ordered)
    alpha = 1 - nominal
    low_rank = math.floor((n + 1) * alpha / 2)
    high_rank = math.ceil((n + 1) * (1 - alpha / 2))
    if low_rank < 1 or high_rank > n:
        return None
    # Round outward so the stored model is never narrower than the order statistics it came from.
    return (ordered[low_rank - 1].quantize(RATIO_STEP, rounding=ROUND_FLOOR),
            ordered[high_rank - 1].quantize(RATIO_STEP, rounding=ROUND_CEILING))


def _interval(area, bounds):
    low = (bounds[0] * area).quantize(GRAM_STEP, rounding=ROUND_FLOOR)
    high = (bounds[1] * area).quantize(GRAM_STEP, rounding=ROUND_CEILING)
    return low, high


def fit_reference_card_calibration(samples, *, calibration_id, nominal, dataset_reference, producer_ref):
    """Return (draft CalibrationRecord, model, diagnostics) from directly weighed, source-grouped samples."""
    if not all(text(v) for v in (calibration_id, dataset_reference, producer_ref)):
        raise ValueError('calibration_identity_required')
    level = _nominal(nominal)
    rows = _validate_samples(samples)
    groups = sorted({r['group'] for r in rows})
    if len(groups) < 2:
        raise ValueError('at_least_two_source_groups_required')
    evaluated, covered, absolute_error, skipped = [], 0, Decimal(0), []
    for row in rows:
        fit = [r['ratio'] for r in rows if r['ingredient'] == row['ingredient'] and r['group'] != row['group']]
        bounds = conformal_ratio_bounds(fit, level) if fit else None
        if bounds is None:
            skipped.append(row['id'])
            continue
        area = row['mass'] / row['ratio']
        low, high = _interval(area, bounds)
        evaluated.append(row)
        covered += low <= row['mass'] <= high
        absolute_error += abs((low + high) / 2 - row['mass'])
    if not evaluated:
        raise ValueError('no_sample_evaluable_out_of_group')
    ratios = {}
    for ingredient in sorted({r['ingredient'] for r in rows}):
        bounds = conformal_ratio_bounds([r['ratio'] for r in rows if r['ingredient'] == ingredient], level)
        if bounds is not None:
            ratios[ingredient] = {'low': decimal_text(bounds[0]), 'high': decimal_text(bounds[1]),
                                  'sample_count': sum(r['ingredient'] == ingredient for r in rows)}
    in_scope = [r for r in rows if r['ingredient'] in ratios]
    masses = [r['mass'] for r in in_scope]
    if not ratios or min(masses) == max(masses):
        raise ValueError('validity_scope_unavailable')
    dataset_sha = canonical_sha256(list(samples))
    record = {'contract': 'foodcal.portion_calibration', 'schema_version': SCHEMA_VERSION,
              'calibration_id': calibration_id, 'method': dict(METHOD), 'status': 'draft',
              'dataset': {'reference': dataset_reference, 'sha256': dataset_sha, 'split': 'held_out_by_source_group',
                          'ground_truth': 'direct_weighing', 'sample_count': len(evaluated),
                          'source_group_count': len({r['group'] for r in evaluated})},
              'coverage': {'nominal': decimal_text(level), 'covered_count': covered},
              'error': {'mae_g': decimal_text((absolute_error / len(evaluated)).quantize(GRAM_STEP))},
              'validity_scope': {'ingredient_candidates': sorted(ratios),
                                 'mass_range_g': {'low': decimal_text(min(masses)), 'high': decimal_text(max(masses))}},
              'producer_ref': producer_ref,
              'approval': {'status': 'not_requested', 'approver_ref': None, 'approved_at': None}, 'usage': USAGE}
    issues = validate_calibration(record)
    if issues:
        raise ValueError('calibration_record_invalid:' + ','.join(i['code'] for i in issues))
    model = {'contract': MODEL_CONTRACT, 'schema_version': SCHEMA_VERSION, 'method': dict(METHOD),
             'calibration_id': calibration_id, 'dataset_sha256': dataset_sha, 'nominal': decimal_text(level),
             'card_area_cm2': decimal_text(CARD_AREA_CM2), 'ratio_g_per_cm2': ratios, 'usage': USAGE}
    diagnostics = {'samples_total': len(rows), 'samples_evaluated': len(evaluated),
                   'samples_not_evaluable_out_of_group': skipped, 'source_groups': groups,
                   'measured_coverage': decimal_text(Decimal(covered) / len(evaluated))}
    return record, model, diagnostics


def estimate_with_reference_card(evidence_item, card_px, *, model, record, estimate_id, producer_ref):
    """PortionEstimate for one admissible visual evidence item; review starts not_requested.

    The record may be draft or approved: readiness is decided later by assess_portions, never here.
    """
    blocker = evidence_blocker(evidence_item)
    if blocker:
        raise ValueError(blocker)
    if not text(estimate_id) or not text(producer_ref):
        raise ValueError('estimate_identity_required')
    if (not isinstance(model, dict) or model.get('contract') != MODEL_CONTRACT or model.get('method') != METHOD
            or not isinstance(record, dict) or validate_calibration(record)
            or model.get('calibration_id') != record['calibration_id']
            or model.get('dataset_sha256') != record['dataset']['sha256'] or record['method'] != METHOD
            or model.get('nominal') != record['coverage']['nominal']):
        raise ValueError('model_record_mismatch')
    ingredient = evidence_item['ingredient_candidate']
    ratio = model['ratio_g_per_cm2'].get(ingredient)
    if ratio is None:
        raise ValueError('ingredient_not_calibrated')
    box = region_box(evidence_item['image_region'])
    area = visible_area_cm2(box, card_px)
    low, high = _interval(area, (Decimal(ratio['low']), Decimal(ratio['high'])))
    if high == 0:
        raise ValueError('region_too_small_for_estimate')
    estimate = {'contract': 'foodcal.photo_portion_estimate', 'schema_version': SCHEMA_VERSION,
                'estimate_id': estimate_id, 'evidence_ref': evidence_identity(evidence_item),
                'mass_kind': 'visible_mass_at_capture',
                'mass_g': {'low': decimal_text(low) if low else '0', 'high': decimal_text(high),
                           'coverage': model['nominal']},
                'method': dict(METHOD), 'measurement': None,
                'calibration_ref': {'calibration_id': record['calibration_id'], 'record_sha256': canonical_sha256(record)},
                'producer': {'kind': 'calibrated_method', 'ref': producer_ref},
                'review': {'status': 'not_requested', 'reviewer_ref': None, 'reviewed_at': None}, 'usage': USAGE}
    issues = validate_portion_estimate(estimate)
    if issues:
        raise ValueError('portion_estimate_invalid:' + ','.join(i['code'] for i in issues))
    return estimate
