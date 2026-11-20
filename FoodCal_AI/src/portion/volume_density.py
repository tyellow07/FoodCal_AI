"""Reference-card volume x density (method_id reference_card_volume_density v0).

volume = visible area (card-scaled box, cm^2) x effective height (cm); mass = volume x density.

Density comes from a sourced table that people fill from published data such as "1 cup = 186 g"
(density = grams / volume_ml); every row keeps its source, retrieval date and who entered it.
Effective height is learned per food from directly weighed photos: h = mass / (density x area). It is an
average over the box, so it is usually lower than a ruler reading at the highest point.

For a weighed food the learned height absorbs any density error, so mass intervals keep the coverage
measured by leave-one-source-group-out evaluation, exactly like reference_card_area_ratio. The density
table adds two things: a volume for every estimate, and a volume-based mass for foods of the same shape
that were never weighed. That second number has no measured coverage: estimate_volume marks it
coverage_validated = False and it is never a PortionEstimate, so it cannot reach an energy total.
"""
from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from statistics import median

from .contract import (SCHEMA_VERSION, USAGE, canonical_sha256, decimal_text, decimal_value, evidence_blocker,
                       evidence_identity, region_box, text, validate_calibration, validate_portion_estimate)
from .reference_card import (CARD_AREA_CM2, GRAM_STEP, _nominal, _validate_samples, conformal_ratio_bounds,
                             visible_area_cm2)

METHOD = {'kind': 'calibrated_visual_estimate', 'method_id': 'reference_card_volume_density', 'method_version': '0'}
MODEL_CONTRACT = 'foodcal.volume_density_model'
DENSITY_COLUMNS = ('food', 'shape', 'grams', 'volume_ml', 'source_title', 'source_url', 'retrieved_at', 'entered_by')
SHAPES = ('mound', 'layer')  # mound: rice, noodles; layer: fried egg, pancake
DENSITY_STEP = Decimal('0.000001')
VOLUME_STEP = Decimal('0.1')
DENSITY_RANGE = (Decimal('0.05'), Decimal('3'))


def load_density_table(rows):
    """Validated food -> density entry; density is grams / volume_ml rounded to 1e-6 g/cm^3."""
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValueError('density_rows_required')
    table = {}
    for row in rows:
        if not isinstance(row, dict) or tuple(sorted(row)) != tuple(sorted(DENSITY_COLUMNS)):
            raise ValueError('density_columns_mismatch')
        if not all(text(row[k]) for k in DENSITY_COLUMNS):
            raise ValueError('density_blank_cell')
        food = row['food']
        if food in table:
            raise ValueError('duplicate_density_food: ' + food)
        if row['shape'] not in SHAPES:
            raise ValueError('density_shape_must_be_one_of: ' + ', '.join(SHAPES))
        grams, volume = decimal_value(row['grams']), decimal_value(row['volume_ml'])
        if not grams or not volume:
            raise ValueError('density_grams_and_volume_must_be_positive: ' + food)
        density = (grams / volume).quantize(DENSITY_STEP)
        if not DENSITY_RANGE[0] <= density <= DENSITY_RANGE[1]:
            raise ValueError('implausible_density: ' + food)
        if not row['source_url'].startswith(('https://', 'http://')):
            raise ValueError('density_source_url_required: ' + food)
        try:
            date.fromisoformat(row['retrieved_at'])
        except ValueError:
            raise ValueError('density_retrieved_at_must_be_iso_date: ' + food) from None
        table[food] = {**{k: row[k] for k in DENSITY_COLUMNS if k != 'food'},
                       'density_g_per_cm3': decimal_text(density)}
    return table


def _mass(density, height, area, rounding):
    return (density * height * area).quantize(GRAM_STEP, rounding=rounding)


def fit_volume_density_calibration(samples, densities, *, calibration_id, nominal, dataset_reference, producer_ref):
    """Return (draft CalibrationRecord, model, diagnostics) from weighed, source-grouped samples and a density table."""
    if not all(text(v) for v in (calibration_id, dataset_reference, producer_ref)):
        raise ValueError('calibration_identity_required')
    level = _nominal(nominal)
    if not isinstance(densities, dict) or not densities:
        raise ValueError('density_table_required')
    rows = _validate_samples(samples)
    for row in rows:
        entry = densities.get(row['ingredient'])
        if entry is None:
            raise ValueError('density_missing: ' + row['ingredient'])
        row['density'] = Decimal(entry['density_g_per_cm3'])
        row['area'] = row['mass'] / row['ratio']
        row['height'] = row['ratio'] / row['density']
    groups = sorted({r['group'] for r in rows})
    if len(groups) < 2:
        raise ValueError('at_least_two_source_groups_required')
    evaluated, covered, absolute_error, skipped = [], 0, Decimal(0), []
    for row in rows:
        fit = [r['height'] for r in rows if r['ingredient'] == row['ingredient'] and r['group'] != row['group']]
        bounds = conformal_ratio_bounds(fit, level) if fit else None
        if bounds is None:
            skipped.append(row['id'])
            continue
        low = _mass(row['density'], bounds[0], row['area'], ROUND_FLOOR)
        high = _mass(row['density'], bounds[1], row['area'], ROUND_CEILING)
        evaluated.append(row)
        covered += low <= row['mass'] <= high
        absolute_error += abs((low + high) / 2 - row['mass'])
    if not evaluated:
        raise ValueError('no_sample_evaluable_out_of_group')
    heights = {}
    for food in sorted({r['ingredient'] for r in rows}):
        bounds = conformal_ratio_bounds([r['height'] for r in rows if r['ingredient'] == food], level)
        if bounds is not None:
            heights[food] = {'low': decimal_text(bounds[0]), 'high': decimal_text(bounds[1]),
                             'sample_count': sum(r['ingredient'] == food for r in rows)}
    in_scope = [r for r in rows if r['ingredient'] in heights]
    masses = [r['mass'] for r in in_scope]
    if not heights or min(masses) == max(masses):
        raise ValueError('validity_scope_unavailable')
    shape_heights = {}
    for shape in SHAPES:
        pool = [r for r in in_scope if densities[r['ingredient']]['shape'] == shape]
        bounds = conformal_ratio_bounds([r['height'] for r in pool], level) if pool else None
        if bounds is not None:
            shape_heights[shape] = {'low': decimal_text(bounds[0]), 'high': decimal_text(bounds[1]),
                                    'sample_count': len(pool), 'foods': sorted({r['ingredient'] for r in pool})}
    dataset_sha = canonical_sha256(list(samples))
    record = {'contract': 'foodcal.portion_calibration', 'schema_version': SCHEMA_VERSION,
              'calibration_id': calibration_id, 'method': dict(METHOD), 'status': 'draft',
              'dataset': {'reference': dataset_reference, 'sha256': dataset_sha, 'split': 'held_out_by_source_group',
                          'ground_truth': 'direct_weighing', 'sample_count': len(evaluated),
                          'source_group_count': len({r['group'] for r in evaluated})},
              'coverage': {'nominal': decimal_text(level), 'covered_count': covered},
              'error': {'mae_g': decimal_text((absolute_error / len(evaluated)).quantize(GRAM_STEP))},
              'validity_scope': {'ingredient_candidates': sorted(heights),
                                 'mass_range_g': {'low': decimal_text(min(masses)), 'high': decimal_text(max(masses))}},
              'producer_ref': producer_ref,
              'approval': {'status': 'not_requested', 'approver_ref': None, 'approved_at': None}, 'usage': USAGE}
    issues = validate_calibration(record)
    if issues:
        raise ValueError('calibration_record_invalid:' + ','.join(i['code'] for i in issues))
    model = {'contract': MODEL_CONTRACT, 'schema_version': SCHEMA_VERSION, 'method': dict(METHOD),
             'calibration_id': calibration_id, 'dataset_sha256': dataset_sha, 'nominal': decimal_text(level),
             'card_area_cm2': decimal_text(CARD_AREA_CM2), 'densities': densities,
             'density_table_sha256': canonical_sha256(densities), 'height_cm': heights,
             'shape_height_cm': shape_heights, 'usage': USAGE}
    diagnostics = {'samples_total': len(rows), 'samples_evaluated': len(evaluated),
                   'samples_not_evaluable_out_of_group': skipped, 'source_groups': groups,
                   'measured_coverage': decimal_text(Decimal(covered) / len(evaluated)),
                   'effective_height_cm_median': {
                       food: decimal_text(median([r['height'] for r in rows if r['ingredient'] == food])
                                          .quantize(Decimal('0.01')))
                       for food in sorted({r['ingredient'] for r in rows})}}
    return record, model, diagnostics


def _check_model(model):
    if (not isinstance(model, dict) or model.get('contract') != MODEL_CONTRACT or model.get('method') != METHOD
            or not isinstance(model.get('densities'), dict)
            or canonical_sha256(model['densities']) != model.get('density_table_sha256')):
        raise ValueError('volume_density_model_invalid')


def estimate_volume(evidence_item, card_px, *, model):
    """Volume (and a volume-based mass) for one visual item; may use a same-shape height for unweighed foods.

    The result is informational: coverage_validated is True only for foods that were weighed for this model.
    """
    blocker = evidence_blocker(evidence_item)
    if blocker:
        raise ValueError(blocker)
    _check_model(model)
    food = evidence_item['ingredient_candidate']
    entry = model['densities'].get(food)
    if entry is None:
        raise ValueError('density_missing: ' + food)
    if food in model['height_cm']:
        height, basis = model['height_cm'][food], 'weighed_food'
    elif entry['shape'] in model['shape_height_cm']:
        height, basis = model['shape_height_cm'][entry['shape']], 'same_shape_unweighed'
    else:
        raise ValueError('no_height_for_shape: ' + entry['shape'])
    area = visible_area_cm2(region_box(evidence_item['image_region']), card_px)
    low_h, high_h, density = Decimal(height['low']), Decimal(height['high']), Decimal(entry['density_g_per_cm3'])
    return {'food': food, 'shape': entry['shape'], 'basis': basis, 'coverage_validated': basis == 'weighed_food',
            'area_cm2': decimal_text(area.quantize(VOLUME_STEP)),
            'height_cm': {'low': height['low'], 'high': height['high']},
            'volume_cm3': {'low': decimal_text((low_h * area).quantize(VOLUME_STEP, rounding=ROUND_FLOOR)),
                           'high': decimal_text((high_h * area).quantize(VOLUME_STEP, rounding=ROUND_CEILING))},
            'density_g_per_cm3': entry['density_g_per_cm3'], 'density_source': entry['source_url'],
            'mass_g': {'low': decimal_text(_mass(density, low_h, area, ROUND_FLOOR)),
                       'high': decimal_text(_mass(density, high_h, area, ROUND_CEILING)),
                       'coverage': model['nominal'] if basis == 'weighed_food' else None}}


def estimate_with_volume_density(evidence_item, card_px, *, model, record, estimate_id, producer_ref):
    """PortionEstimate for a weighed (in-scope) food; review starts not_requested. Unweighed foods are refused."""
    blocker = evidence_blocker(evidence_item)
    if blocker:
        raise ValueError(blocker)
    if not text(estimate_id) or not text(producer_ref):
        raise ValueError('estimate_identity_required')
    _check_model(model)
    if (not isinstance(record, dict) or validate_calibration(record) or record['method'] != METHOD
            or model.get('calibration_id') != record['calibration_id']
            or model.get('dataset_sha256') != record['dataset']['sha256']
            or model.get('nominal') != record['coverage']['nominal']):
        raise ValueError('model_record_mismatch')
    food = evidence_item['ingredient_candidate']
    if food not in model['height_cm'] or food not in record['validity_scope']['ingredient_candidates']:
        raise ValueError('ingredient_not_calibrated')
    volume = estimate_volume(evidence_item, card_px, model=model)
    low, high = Decimal(volume['mass_g']['low']), Decimal(volume['mass_g']['high'])
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
