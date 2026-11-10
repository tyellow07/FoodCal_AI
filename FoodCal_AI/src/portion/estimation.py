"""Bind portion estimates to visual evidence, then combine them with photo-calculation bases.

Recipe candidates may explain hidden calories, but are not visual proof: recipe hypotheses never
receive a photo mass and are never added to an energy total. No estimator, model or nutrient
lookup runs here; without caller-supplied, reviewed records every output stays unavailable.
"""
from copy import deepcopy
from decimal import Decimal

from .contract import (MASS_KIND, SCHEMA_VERSION, USAGE, calibration_blockers, canonical_sha256, closed,
                       decimal_text, decimal_value, evidence_blocker, evidence_identity, fields_issue,
                       interval, issue, region_box, text, validate_calibration, validate_portion_estimate)

ASSESSMENT_CONTRACT = 'foodcal.photo_portion_assessment'
ENERGY_CONTRACT = 'foodcal.photo_energy_estimate'
GATES = ('source_use', 'identity', 'basis', 'integrity')
GATE_STATUSES = ('pending', 'passed', 'failed', 'not_applicable')
ENERGY_UNITS = ('kcal', 'kJ')
CONSUMPTION_STATUS = 'whole_visible_portion_asserted'
BASIS_FIELDS = frozenset({'basis_id', 'estimate_id', 'ingredient_candidate', 'purpose', 'energy', 'gates',
                          'reviewer_ref', 'usage'})


def _item(evidence_item, *, status='unavailable', reason=None, estimate=None, blockers=(), issues=(),
          calibration_id=None):
    estimated = status == 'estimated'
    estimate_id = estimate.get('estimate_id') if isinstance(estimate, dict) else None
    return {'evidence': evidence_identity(evidence_item), 'image_region': deepcopy(evidence_item.get('image_region')),
            'status': status, 'reason': reason, 'mass_kind': MASS_KIND,
            'mass_g': deepcopy(estimate['mass_g']) if estimated else None,
            'estimate_id': estimate_id if text(estimate_id) else None,
            'method': deepcopy(estimate['method']) if estimated else None, 'calibration_id': calibration_id,
            'ready_for_energy': estimated and not blockers,
            'blocking_reasons': list(blockers) if estimated else [reason], 'issues': list(issues)}


def _calibration_check(estimate, records):
    """(failure_reason, blockers, issues); a failure hides the interval, blockers only gate energy."""
    found = records.get(estimate['calibration_ref']['calibration_id'], [])
    if not found:
        return 'calibration_unavailable', [], []
    if len(found) > 1:
        return 'duplicate_calibration_id', [], []
    record = found[0]
    issues = validate_calibration(record)
    if issues:
        return 'calibration_invalid', [], issues
    if canonical_sha256(record) != estimate['calibration_ref']['record_sha256']:
        return 'calibration_hash_mismatch', [], []
    if record['method'] != estimate['method']:
        return 'calibration_method_mismatch', [], []
    low, high, coverage = (decimal_value(estimate['mass_g'][k]) for k in ('low', 'high', 'coverage'))
    if coverage != decimal_value(record['coverage']['nominal']):
        return 'coverage_differs_from_calibration', [], []
    blockers = calibration_blockers(record)
    if 'calibration_revoked' in blockers:
        return 'calibration_revoked', [], []
    if estimate['evidence_ref']['ingredient_candidate'] not in record['validity_scope']['ingredient_candidates']:
        blockers.append('outside_calibration_ingredient_scope')
    span = record['validity_scope']['mass_range_g']
    if low < decimal_value(span['low']) or high > decimal_value(span['high']):
        blockers.append('outside_calibration_mass_range')
    return None, blockers, []


def _assess(evidence_item, matches, calibrations):
    blocker = evidence_blocker(evidence_item)
    if blocker:
        notes = [issue('estimate_for_inadmissible_evidence', '', blocker)] if matches else []
        return _item(evidence_item, reason=blocker, issues=notes)
    if not matches:
        return _item(evidence_item, reason='photo_mass_unknown')
    if len(matches) > 1:
        return _item(evidence_item, reason='duplicate_estimates')
    estimate = matches[0]
    issues = validate_portion_estimate(estimate)
    if issues:
        return _item(evidence_item, reason='invalid_estimate', estimate=estimate, issues=issues)
    identity = evidence_identity(evidence_item)
    if estimate['evidence_ref']['evidence_sha256'] != identity['evidence_sha256']:
        return _item(evidence_item, reason='evidence_hash_mismatch', estimate=estimate)
    if estimate['evidence_ref']['ingredient_candidate'] != identity['ingredient_candidate']:
        return _item(evidence_item, reason='ingredient_candidate_mismatch', estimate=estimate)
    if estimate['review']['status'] == 'rejected':
        return _item(evidence_item, reason='estimate_rejected_by_review', estimate=estimate)
    blockers, calibration_id = [], None
    if estimate['method']['kind'] == 'calibrated_visual_estimate':
        calibration_id = estimate['calibration_ref']['calibration_id']
        failure, blockers, issues = _calibration_check(estimate, calibrations)
        if failure:
            return _item(evidence_item, reason=failure, estimate=estimate, issues=issues,
                         calibration_id=calibration_id)
    if estimate['review']['status'] != 'accepted':
        blockers.append('independent_review_' + estimate['review']['status'])
    return _item(evidence_item, status='estimated', estimate=estimate, blockers=blockers,
                 calibration_id=calibration_id)


def _key(evidence_type, evidence_id):
    return (evidence_type, evidence_id) if isinstance(evidence_type, str) and isinstance(evidence_id, str) else None


def assess_portions(envelope, *, estimates=(), calibrations=()):
    """Per visual evidence item portion records; inputs are never mutated and nothing is inferred."""
    if (not isinstance(envelope, dict) or not isinstance(envelope.get('visual_ingredient_evidence'), list)
            or not isinstance(envelope.get('recipe_assisted_hidden_ingredients'), list)
            or not all(isinstance(e, dict) for e in envelope['visual_ingredient_evidence']
                       + envelope['recipe_assisted_hidden_ingredients'])):
        raise ValueError('invalid_visual_envelope')
    if not isinstance(estimates, (list, tuple)) or not isinstance(calibrations, (list, tuple)):
        raise ValueError('invalid_assessment_inputs')
    visual = envelope['visual_ingredient_evidence']
    by_calibration = {}
    for record in calibrations:
        identifier = record.get('calibration_id') if isinstance(record, dict) else None
        if text(identifier):
            by_calibration.setdefault(identifier, []).append(record)
    keys = [_key(e.get('evidence_type'), evidence_identity(e)['evidence_id']) for e in visual]
    matches = {k: [] for k in keys if k is not None}
    issues = []
    for index, estimate in enumerate(estimates):
        ref = estimate.get('evidence_ref') if isinstance(estimate, dict) else None
        key = _key(ref.get('evidence_type'), ref.get('evidence_id')) if isinstance(ref, dict) else None
        if key in matches:
            matches[key].append(estimate)
        else:
            recipe = isinstance(ref, dict) and ref.get('evidence_type') == 'inferred_from_recipe'
            issues.append(issue('recipe_prior_not_visual_evidence' if recipe else 'estimate_evidence_unmatched',
                                f'/estimates/{index}', 'estimate binds to no visual evidence item'))
    items = []
    for evidence_item, key in zip(visual, keys):
        if key is not None and keys.count(key) > 1:
            items.append(_item(evidence_item, reason='duplicate_evidence_identity'))
        else:
            items.append(_assess(evidence_item, matches.get(key, []), by_calibration))
    hidden = [{'evidence': evidence_identity(h), 'status': 'unavailable', 'mass_g': None,
               'reason': 'recipe_prior_not_visual_evidence'} for h in envelope['recipe_assisted_hidden_ingredients']]
    estimated = sum(i['status'] == 'estimated' for i in items)
    status = 'unavailable' if not estimated else 'estimated' if estimated == len(items) else 'partial'
    return {'contract': ASSESSMENT_CONTRACT, 'schema_version': SCHEMA_VERSION, 'usage': USAGE, 'status': status,
            'items': items, 'hidden_ingredients': hidden, 'photo_portion_grams': None,
            'nutrition_eligible': False, 'issues': issues}


def validate_energy_basis(basis):
    """Structural issues for one photo-calculation nutrition basis record."""
    if not isinstance(basis, dict):
        return [issue('invalid_basis', '', 'basis must be an object')]
    if set(basis) != BASIS_FIELDS:
        return [fields_issue(basis, BASIS_FIELDS)]
    issues = [issue('invalid_' + k, '/' + k, 'non-empty string required')
              for k in ('basis_id', 'estimate_id', 'ingredient_candidate', 'reviewer_ref') if not text(basis[k])]
    if basis['purpose'] != 'photo_calculation':
        issues.append(issue('purpose_not_photo_calculation', '/purpose', 'only photo_calculation bases may multiply photo mass'))
    energy = basis['energy']
    if (not closed(energy, ('original_name', 'unit', 'per', 'value', 'interval')) or not text(energy['original_name'])
            or energy['unit'] not in ENERGY_UNITS or energy['per'] != 'per_100g_edible_portion'
            or decimal_value(energy['value']) is None):
        issues.append(issue('invalid_energy_reference', '/energy',
                            'original_name, kcal|kJ unit, per_100g_edible_portion and decimal value required'))
    elif energy['interval'] is not None:
        bounds = interval(energy['interval'], '/energy/interval', issues)
        if bounds and not bounds[0] <= decimal_value(energy['value']) <= bounds[1]:
            issues.append(issue('value_outside_interval', '/energy/interval', 'reported value must lie inside its interval'))
    gates = basis['gates']
    if not closed(gates, GATES) or not all(closed(g, ('status', 'evidence_ref')) and g['status'] in GATE_STATUSES
                                         for g in gates.values()):
        issues.append(issue('invalid_gates', '/gates', 'source_use, identity, basis and integrity gate records required'))
    else:
        issues += [issue('gate_evidence_missing', f'/gates/{name}', 'a passed gate needs an evidence_ref')
                   for name, gate in gates.items() if gate['status'] == 'passed' and not text(gate['evidence_ref'])]
    if basis['usage'] != USAGE:
        issues.append(issue('usage_mismatch', '/usage', f'usage must stay {USAGE}'))
    return issues


def _energy_item(item, bases):
    row = {'estimate_id': item['estimate_id'], 'ingredient_candidate': item['evidence']['ingredient_candidate'],
           'basis_id': None, 'status': 'unavailable', 'reason': None, 'blocking_reasons': [], 'unit': None,
           'energy': None, 'coverage_lower_bound': None, 'coverage_note': None, 'issues': []}
    if item['status'] != 'estimated':
        return {**row, 'reason': item['reason']}
    if not item['ready_for_energy']:
        return {**row, 'reason': 'portion_not_ready', 'blocking_reasons': list(item['blocking_reasons'])}
    found = [b for b in bases if isinstance(b, dict) and b.get('estimate_id') == item['estimate_id']]
    if not found:
        return {**row, 'reason': 'nutrition_basis_unavailable'}
    if len(found) > 1:
        return {**row, 'reason': 'duplicate_nutrition_basis'}
    basis = found[0]
    issues = validate_energy_basis(basis)
    if issues:
        return {**row, 'reason': 'nutrition_basis_invalid', 'issues': issues}
    row['basis_id'] = basis['basis_id']
    if basis['ingredient_candidate'] != item['evidence']['ingredient_candidate']:
        return {**row, 'reason': 'basis_ingredient_mismatch'}
    # Photo calculation may not skip any gate: not_applicable blocks exactly like pending or failed.
    gates = [f'gate_{name}_{gate["status"]}' for name, gate in basis['gates'].items() if gate['status'] != 'passed']
    if gates:
        return {**row, 'reason': 'nutrition_basis_unresolved', 'blocking_reasons': gates}
    mass_low, mass_high, mass_coverage = (decimal_value(item['mass_g'][k]) for k in ('low', 'high', 'coverage'))
    reference = basis['energy']
    if reference['interval'] is None:
        per_low = per_high = decimal_value(reference['value'])
        bound, note = None, 'composition_uncertainty_not_quantified'
    else:
        per_low, per_high, per_coverage = (decimal_value(reference['interval'][k]) for k in ('low', 'high', 'coverage'))
        bound, note = max(Decimal(0), mass_coverage + per_coverage - 1), 'bonferroni_union_bound'
    return {**row, 'status': 'computed', 'unit': reference['unit'],
            'energy': {'low': decimal_text(mass_low * per_low / 100), 'high': decimal_text(mass_high * per_high / 100)},
            'coverage_lower_bound': None if bound is None else decimal_text(bound), 'coverage_note': note}


def _overlapping_same_ingredient(items):
    """Same-ingredient regions that intersect may be one portion annotated twice."""
    boxes = [(i['evidence']['ingredient_candidate'], region_box(i['image_region']))
             for i in items if i['status'] == 'estimated']
    for index, (name, box) in enumerate(boxes):
        for other_name, other in boxes[index + 1:]:
            if name == other_name and (box is None or other is None or (
                    min(box[2], other[2]) > max(box[0], other[0]) and min(box[3], other[3]) > max(box[1], other[1]))):
                return True
    return False


def estimate_photo_energy(envelope, *, estimates=(), calibrations=(), nutrition_bases=(), consumption=None):
    """Visible-item energy intervals; a total needs every gate, every item and a consumption assertion.

    The portion assessment is recomputed from the raw records: a caller-supplied conclusion is never accepted.
    """
    if not isinstance(nutrition_bases, (list, tuple)):
        raise ValueError('invalid_nutrition_bases')
    if consumption is not None and (not closed(consumption, ('status', 'asserted_by', 'evidence_ref'))
                                    or consumption['status'] != CONSUMPTION_STATUS
                                    or not text(consumption['asserted_by']) or not text(consumption['evidence_ref'])):
        raise ValueError('invalid_consumption_assertion')
    assessment = assess_portions(envelope, estimates=estimates, calibrations=calibrations)
    items = [_energy_item(i, nutrition_bases) for i in assessment['items']]
    computed = [i for i in items if i['status'] == 'computed']
    reasons = []
    if not items:
        reasons.append('no_visual_items')
    if len(computed) != len(items):
        reasons.append('visual_items_unavailable')
    if consumption is None:
        reasons.append('consumption_basis_missing')
    if len({i['unit'] for i in computed}) > 1:
        reasons.append('mixed_energy_units')
    if _overlapping_same_ingredient(assessment['items']):
        reasons.append('overlapping_same_ingredient_regions')
    excluded = [{'evidence': deepcopy(h['evidence']), 'reason': 'recipe_prior_not_visual_evidence'}
                for h in assessment['hidden_ingredients']]
    result = {'contract': ENERGY_CONTRACT, 'schema_version': SCHEMA_VERSION, 'usage': USAGE, 'status': 'unavailable',
              'scope': 'visible_items_only', 'unit': None, 'energy': None, 'coverage_lower_bound': None,
              'coverage_note': None, 'consumption': deepcopy(consumption), 'items': items,
              'portion_assessment': assessment, 'excluded_recipe_priors': excluded,
              'completeness': 'incomplete_hidden_ingredients_unquantified' if excluded else 'visible_items_only',
              'reasons': reasons, 'nutrition_eligible': False, 'production_eligible': False}
    if reasons:
        return result
    low = sum((decimal_value(i['energy']['low']) for i in computed), Decimal(0))
    high = sum((decimal_value(i['energy']['high']) for i in computed), Decimal(0))
    bounds = [i['coverage_lower_bound'] for i in computed]
    if None in bounds:
        bound, note = None, 'composition_uncertainty_not_quantified'
    else:
        bound = max(Decimal(0), 1 - sum((1 - decimal_value(b) for b in bounds), Decimal(0)))
        note = 'bonferroni_union_bound'
    result.update(status='computed', unit=computed[0]['unit'],
                  energy={'low': decimal_text(low), 'high': decimal_text(high)},
                  coverage_lower_bound=None if bound is None else decimal_text(bound), coverage_note=note)
    return result
