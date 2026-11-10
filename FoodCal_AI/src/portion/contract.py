"""Photo Portion Estimation Contract v0: pure, non-mutating checks.

No model, image decoding, training or nutrient lookup happens here. Passing a check means a record
is well-formed, not that it is true; every record stays diagnostic/test_only.
"""
from decimal import Decimal
from hashlib import sha256
import json
import math
import re

CONTRACT = 'foodcal.photo_portion_estimate'
CALIBRATION_CONTRACT = 'foodcal.portion_calibration'
SCHEMA_VERSION = 0
USAGE = 'diagnostic/test_only'
MASS_KIND = 'visible_mass_at_capture'
METHOD_KINDS = ('direct_weighing', 'calibrated_visual_estimate')
PRODUCER_FOR_METHOD = {'direct_weighing': 'human', 'calibrated_visual_estimate': 'calibrated_method'}
# Known non-photo mass sources get explicit codes; any other unlisted kind is rejected as well.
FORBIDDEN_MASS_SOURCES = {
    'recipe_declared_quantity': 'recipe_quantity_is_not_photo_mass',
    'standard_serving': 'standard_serving_is_not_photo_mass',
    'dish_label_default': 'dish_label_is_not_photo_mass',
    'detection_confidence': 'model_confidence_is_not_mass',
    'box_count': 'box_count_is_not_portion_count',
    'ai_draft': 'ai_draft_is_not_measurement',
}
ADMISSIBLE_EVIDENCE_TYPES = ('ingredient_annotation', 'reviewed_prediction')
EVIDENCE_ID_FIELD = {'ingredient_annotation': 'annotation_id', 'reviewed_prediction': 'review_id'}
REVIEW_STATUSES = ('not_requested', 'pending', 'accepted', 'rejected')
CALIBRATION_STATUSES = ('draft', 'approved', 'revoked')
APPROVAL_STATUSES = ('not_requested', 'pending', 'approved', 'rejected')
# Provisional v0 floors. An approval can never lower them; only a contract revision can.
MIN_CALIBRATION_SAMPLES = 30
MIN_SOURCE_GROUPS = 2

ESTIMATE_FIELDS = frozenset({'contract', 'schema_version', 'estimate_id', 'evidence_ref', 'mass_kind', 'mass_g',
                             'method', 'measurement', 'calibration_ref', 'producer', 'review', 'usage'})
CALIBRATION_FIELDS = frozenset({'contract', 'schema_version', 'calibration_id', 'method', 'status', 'dataset',
                                'coverage', 'error', 'validity_scope', 'producer_ref', 'approval', 'usage'})

_DECIMAL = re.compile(r'(0|[1-9][0-9]*)(\.[0-9]+)?')
_SHA256 = re.compile(r'[0-9a-f]{64}')
_PRIVATE_PATH = re.compile(r'^(/|~|[A-Za-z]:)|\\')


def text(value):
    return isinstance(value, str) and bool(value.strip())


def closed(value, fields):
    return isinstance(value, dict) and set(value) == set(fields)


def sha256_text(value):
    return isinstance(value, str) and bool(_SHA256.fullmatch(value))


def decimal_value(value):
    """Exact non-negative decimal string only: no float, exponent, sign, NaN or padding."""
    return Decimal(value) if isinstance(value, str) and _DECIMAL.fullmatch(value) else None


def decimal_text(value):
    return format(value.normalize(), 'f')


def canonical_sha256(value):
    """SHA-256 of canonical JSON v0: sorted keys, compact separators, UTF-8, no NaN."""
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return sha256(payload.encode('utf-8')).hexdigest()


def issue(code, path, message):
    return {'code': code, 'severity': 'error', 'path': path, 'message': message}


def fields_issue(value, fields, path=''):
    missing, extra = sorted(set(fields) - set(value)), sorted(set(value) - set(fields))
    return issue('fields_mismatch', path, f'missing={missing} unexpected={extra}')


def interval(value, path, issues):
    """Check {low, high, coverage}; return Decimals, or None after recording an issue."""
    if not closed(value, ('low', 'high', 'coverage')):
        issues.append(issue('invalid_interval', path, 'object with low, high and coverage decimal strings required'))
        return None
    low, high, coverage = (decimal_value(value[k]) for k in ('low', 'high', 'coverage'))
    if low is None or high is None or low > high:
        issues.append(issue('invalid_interval_bounds', path, 'non-negative decimal strings with low <= high required'))
        return None
    if coverage is None or not 0 < coverage <= 1:
        issues.append(issue('invalid_coverage', path + '/coverage', 'nominal coverage must be a decimal string in (0, 1]'))
        return None
    return low, high, coverage


def evidence_identity(item):
    """Binding keys for one evidence item; the hash covers every field, so any edit unbinds it."""
    source = item.get('evidence_source') if isinstance(item.get('evidence_source'), dict) else {}
    field = EVIDENCE_ID_FIELD.get(item.get('evidence_type')) if isinstance(item.get('evidence_type'), str) else None
    evidence_id = source.get(field) if field else None
    return {'evidence_type': item.get('evidence_type'),
            'evidence_id': evidence_id if isinstance(evidence_id, str) else None,
            'ingredient_candidate': item.get('ingredient_candidate'),
            'evidence_sha256': canonical_sha256(item)}


def region_box(region):
    """[x_min, y_min, x_max, y_max] from an evidence image_region, or None if unusable."""
    if not isinstance(region, dict):
        return None
    box = [region.get(k) for k in ('x_min', 'y_min', 'x_max', 'y_max')]
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in box):
        return None
    return box if box[0] < box[2] and box[1] < box[3] else None


def evidence_blocker(item):
    """Why an evidence item cannot carry a photo mass, or None when it is admissible."""
    kind = item.get('evidence_type')
    if kind == 'inferred_from_recipe':
        return 'recipe_prior_not_visual_evidence'
    if kind == 'dish_detection_only':
        return 'ingredient_level_semantics_unverified'
    if kind == 'dish_classification_only':
        return 'classification_is_not_ingredient_detection'
    if kind not in ADMISSIBLE_EVIDENCE_TYPES:
        return 'evidence_type_not_admissible'
    if item.get('status') == 'uncertain':
        return 'visual_evidence_uncertain'
    if item.get('status') != 'observed_candidate':
        return 'visual_evidence_unavailable'
    if not text(item.get('ingredient_candidate')):
        return 'ingredient_candidate_missing'
    if not text(evidence_identity(item)['evidence_id']):
        return 'evidence_id_missing'
    if region_box(item.get('image_region')) is None:
        return 'image_region_missing'
    return None


def validate_portion_estimate(estimate):
    """Structural and evidence-boundary issues for one estimate; [] means well-formed only."""
    if not isinstance(estimate, dict):
        return [issue('invalid_estimate', '', 'estimate must be an object')]
    if set(estimate) != ESTIMATE_FIELDS:
        return [fields_issue(estimate, ESTIMATE_FIELDS)]
    issues = []
    if (estimate['contract'] != CONTRACT or type(estimate['schema_version']) is not int
            or estimate['schema_version'] != SCHEMA_VERSION):
        issues.append(issue('contract_mismatch', '/contract', f'{CONTRACT} schema_version {SCHEMA_VERSION} required'))
    if not text(estimate['estimate_id']):
        issues.append(issue('invalid_estimate_id', '/estimate_id', 'non-empty estimate_id required'))
    ref = estimate['evidence_ref']
    if (not closed(ref, ('evidence_type', 'evidence_id', 'ingredient_candidate', 'evidence_sha256'))
            or not all(text(ref[k]) for k in ('evidence_type', 'evidence_id', 'ingredient_candidate'))
            or not sha256_text(ref['evidence_sha256'])):
        issues.append(issue('invalid_evidence_ref', '/evidence_ref',
                            'evidence_type, evidence_id, ingredient_candidate and evidence_sha256 required'))
    elif ref['evidence_type'] not in ADMISSIBLE_EVIDENCE_TYPES:
        code = ('recipe_prior_not_visual_evidence' if ref['evidence_type'] == 'inferred_from_recipe'
                else 'evidence_type_not_admissible')
        issues.append(issue(code, '/evidence_ref/evidence_type', 'only visual ingredient evidence can carry photo mass'))
    if estimate['mass_kind'] != MASS_KIND:
        issues.append(issue('mass_kind_mismatch', '/mass_kind',
                            f'only {MASS_KIND} is defined; consumption is asserted separately'))
    mass = interval(estimate['mass_g'], '/mass_g', issues)
    if mass and mass[1] == 0:
        issues.append(issue('zero_mass_interval', '/mass_g/high', 'an observed item needs a positive upper bound'))
    method, kind = estimate['method'], None
    if (not closed(method, ('kind', 'method_id', 'method_version')) or not text(method['kind'])
            or not text(method['method_id']) or not text(method['method_version'])):
        issues.append(issue('invalid_method', '/method', 'kind, method_id and method_version required'))
    elif method['kind'] in FORBIDDEN_MASS_SOURCES:
        issues.append(issue(FORBIDDEN_MASS_SOURCES[method['kind']], '/method/kind', 'not a photo mass measurement'))
    elif method['kind'] not in METHOD_KINDS:
        issues.append(issue('method_kind_not_allowed', '/method/kind', 'allowed: ' + ', '.join(METHOD_KINDS)))
    else:
        kind = method['kind']
    producer = estimate['producer']
    if (not closed(producer, ('kind', 'ref')) or not text(producer['ref'])
            or producer['kind'] not in PRODUCER_FOR_METHOD.values()):
        issues.append(issue('invalid_producer', '/producer', 'kind human|calibrated_method and ref required'))
        producer = None
    elif kind and producer['kind'] != PRODUCER_FOR_METHOD[kind]:
        issues.append(issue('producer_method_mismatch', '/producer/kind',
                            f'{kind} requires producer {PRODUCER_FOR_METHOD[kind]}'))
    measurement, calibration_ref, measured_by = estimate['measurement'], estimate['calibration_ref'], None
    if kind == 'direct_weighing':
        if calibration_ref is not None:
            issues.append(issue('calibration_ref_not_applicable', '/calibration_ref', 'direct weighing is not calibrated estimation'))
        if (not closed(measurement, ('instrument_resolution_g', 'evidence_ref', 'measured_by'))
                or not text(measurement['evidence_ref']) or not text(measurement['measured_by'])):
            issues.append(issue('invalid_measurement', '/measurement',
                                'instrument_resolution_g, evidence_ref and measured_by required'))
        else:
            measured_by = measurement['measured_by']
            resolution = decimal_value(measurement['instrument_resolution_g'])
            if resolution is None or resolution == 0:
                issues.append(issue('invalid_instrument_resolution', '/measurement/instrument_resolution_g',
                                    'positive decimal string required'))
            elif mass and mass[1] - mass[0] < resolution:
                issues.append(issue('interval_narrower_than_resolution', '/mass_g',
                                    'an interval cannot be narrower than the scale resolution'))
            if _PRIVATE_PATH.search(measurement['evidence_ref']):
                issues.append(issue('private_path_not_allowed', '/measurement/evidence_ref',
                                    'use a shareable locator, not a machine path'))
    elif kind == 'calibrated_visual_estimate':
        if measurement is not None:
            issues.append(issue('measurement_not_applicable', '/measurement', 'calibrated estimates carry no direct measurement'))
        if (not closed(calibration_ref, ('calibration_id', 'record_sha256')) or not text(calibration_ref['calibration_id'])
                or not sha256_text(calibration_ref['record_sha256'])):
            issues.append(issue('invalid_calibration_ref', '/calibration_ref', 'calibration_id and record_sha256 required'))
    review = estimate['review']
    if not closed(review, ('status', 'reviewer_ref', 'reviewed_at')) or review['status'] not in REVIEW_STATUSES:
        issues.append(issue('invalid_review', '/review', 'status one of ' + ', '.join(REVIEW_STATUSES)))
    elif review['status'] in ('accepted', 'rejected'):
        if not text(review['reviewer_ref']) or not text(review['reviewed_at']):
            issues.append(issue('review_record_incomplete', '/review', 'decisions need reviewer_ref and reviewed_at'))
        elif review['reviewer_ref'] in {producer['ref'] if producer else None, measured_by}:
            issues.append(issue('review_not_independent', '/review/reviewer_ref',
                                'reviewer must differ from the producer and the measurer'))
    elif review['reviewer_ref'] is not None or review['reviewed_at'] is not None:
        issues.append(issue('review_fields_without_decision', '/review', 'reviewer fields are set only with a decision'))
    if estimate['usage'] != USAGE:
        issues.append(issue('usage_mismatch', '/usage', f'usage must stay {USAGE}'))
    return issues


def validate_calibration(record):
    """Structural issues for one calibration record; usability floors are separate."""
    if not isinstance(record, dict):
        return [issue('invalid_calibration', '', 'calibration record must be an object')]
    if set(record) != CALIBRATION_FIELDS:
        return [fields_issue(record, CALIBRATION_FIELDS)]
    issues = []
    if (record['contract'] != CALIBRATION_CONTRACT or type(record['schema_version']) is not int
            or record['schema_version'] != SCHEMA_VERSION):
        issues.append(issue('contract_mismatch', '/contract',
                            f'{CALIBRATION_CONTRACT} schema_version {SCHEMA_VERSION} required'))
    if not text(record['calibration_id']):
        issues.append(issue('invalid_calibration_id', '/calibration_id', 'non-empty calibration_id required'))
    method = record['method']
    if (not closed(method, ('kind', 'method_id', 'method_version')) or method['kind'] != 'calibrated_visual_estimate'
            or not text(method['method_id']) or not text(method['method_version'])):
        issues.append(issue('invalid_method', '/method', 'calibrated_visual_estimate method identity required'))
    if record['status'] not in CALIBRATION_STATUSES:
        issues.append(issue('invalid_calibration_status', '/status', 'one of ' + ', '.join(CALIBRATION_STATUSES)))
    data, count = record['dataset'], None
    if (not closed(data, ('reference', 'sha256', 'split', 'ground_truth', 'sample_count', 'source_group_count'))
            or not text(data['reference']) or not sha256_text(data['sha256'])
            or data['split'] != 'held_out_by_source_group' or data['ground_truth'] != 'direct_weighing'
            or type(data['sample_count']) is not int or type(data['source_group_count']) is not int
            or not 1 <= data['source_group_count'] <= data['sample_count']):
        issues.append(issue('invalid_dataset', '/dataset',
                            'held-out, source-grouped, directly weighed dataset identity and counts required'))
    else:
        count = data['sample_count']
        if _PRIVATE_PATH.search(data['reference']):
            issues.append(issue('private_path_not_allowed', '/dataset/reference', 'use a shareable locator, not a machine path'))
    coverage = record['coverage']
    nominal = decimal_value(coverage.get('nominal')) if isinstance(coverage, dict) else None
    if (not closed(coverage, ('nominal', 'covered_count')) or nominal is None or not 0 < nominal < 1
            or type(coverage['covered_count']) is not int or count is None
            or not 0 <= coverage['covered_count'] <= count):
        issues.append(issue('invalid_coverage_evaluation', '/coverage',
                            'nominal in (0, 1) and covered_count within sample_count required'))
    if not closed(record['error'], ('mae_g',)) or decimal_value(record['error']['mae_g']) is None:
        issues.append(issue('invalid_error_metrics', '/error', 'mae_g decimal string required'))
    scope = record['validity_scope']
    names = scope.get('ingredient_candidates') if isinstance(scope, dict) else None
    span = scope.get('mass_range_g') if isinstance(scope, dict) else None
    bounds = [decimal_value(span.get(k)) for k in ('low', 'high')] if closed(span, ('low', 'high')) else [None, None]
    if (not closed(scope, ('ingredient_candidates', 'mass_range_g')) or not isinstance(names, list) or not names
            or not all(text(n) for n in names) or len(set(names)) != len(names)
            or None in bounds or bounds[0] >= bounds[1]):
        issues.append(issue('invalid_validity_scope', '/validity_scope',
                            'unique ingredient_candidates and mass_range_g with low < high required'))
    if not text(record['producer_ref']):
        issues.append(issue('invalid_producer_ref', '/producer_ref', 'who ran the calibration is required'))
    approval = record['approval']
    if not closed(approval, ('status', 'approver_ref', 'approved_at')) or approval['status'] not in APPROVAL_STATUSES:
        issues.append(issue('invalid_approval', '/approval', 'status one of ' + ', '.join(APPROVAL_STATUSES)))
        approval = None
    elif approval['status'] in ('approved', 'rejected'):
        if not text(approval['approver_ref']) or not text(approval['approved_at']):
            issues.append(issue('approval_record_incomplete', '/approval', 'decisions need approver_ref and approved_at'))
        elif approval['approver_ref'] == record['producer_ref']:
            issues.append(issue('approval_not_independent', '/approval/approver_ref', 'approver must differ from producer'))
    elif approval['approver_ref'] is not None or approval['approved_at'] is not None:
        issues.append(issue('approval_fields_without_decision', '/approval', 'approver fields are set only with a decision'))
    if record['status'] == 'approved' and (approval is None or approval['status'] != 'approved'):
        issues.append(issue('calibration_status_approval_mismatch', '/status', 'approved status needs an approved decision'))
    if record['usage'] != USAGE:
        issues.append(issue('usage_mismatch', '/usage', f'usage must stay {USAGE}'))
    return issues


def calibration_blockers(record):
    """Usability floors for a structurally valid record; approval cannot waive any of them."""
    reasons = []
    if record['status'] == 'revoked':
        reasons.append('calibration_revoked')
    elif record['status'] != 'approved':
        reasons.append('calibration_not_approved')
    count = record['dataset']['sample_count']
    if count < MIN_CALIBRATION_SAMPLES:
        reasons.append('calibration_sample_too_small')
    if record['dataset']['source_group_count'] < MIN_SOURCE_GROUPS:
        reasons.append('calibration_source_groups_insufficient')
    if Decimal(record['coverage']['covered_count']) / Decimal(count) < decimal_value(record['coverage']['nominal']):
        reasons.append('calibration_coverage_below_nominal')
    return reasons
