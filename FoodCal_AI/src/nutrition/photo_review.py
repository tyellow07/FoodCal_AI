"""Photo Item Source Review v0: review one photo ingredient's TFDA basis without any recipe.

Recipe candidates may explain hidden calories, but are not visual proof: recipe text, Parsing,
candidate lookups and recipe claims are rejected outright. This module verifies pinned records and
evidence, never infers semantics and never confers trust. Every output stays diagnostic/test_only;
nutrition_eligible and production_eligible are always False. Design: docs/photo_item_source_review_design_v0.md.
"""
from copy import deepcopy
from datetime import datetime
import re

from src.portion.contract import (canonical_sha256, evidence_blocker, evidence_identity, interval, region_box,
                                  validate_portion_estimate)
from src.portion.estimation import GATES, validate_energy_basis
from .photo_review_types import (PhotoAttributeResult, PhotoReviewClaim, PhotoReviewInput, PhotoScope,
                                 PhotoVisualReview, SourceGateRecord, SourceLink)
from .review_aggregation import _aggregate_validated
from .review_common import (Reject, check_evidence_record, check_extracted, digest,
                            index_review_records, load_json, load_trusted_ledger, official_document_value,
                            official_record_value, pointer_value, require, resolve_attachments, text,
                            verify_food_record, verify_source_observations)
from .review_types import ReviewIssue, ReviewValidation
from .tfda_source import SourceLookup

CONTRACT = 'foodcal.photo_item_source_review'
SCHEMA_VERSION = 0
USAGE = 'diagnostic/test_only'
CONTEXT_PROFILE = 'foodcal.photo_review_context.v0'
VISUAL_REVIEW_PROFILE = 'foodcal.photo_visual_review.v0'
GATE_RECORD_PROFILE = 'foodcal.source_gate_record.v0'
BASIS_PURPOSE = 'photo_consumed_mass_calculation'
GATE_PURPOSE = 'photo_calculation'
MANUAL_RULE = 'manual_evidence_comparison_v1'
NA_RULE = 'photo_item_excludes_recipe_mass_v1'

# Same attribute sets as Review v1 so both reviews stay comparable; only scope and evidence differ.
IDENTITY_ATTRIBUTES = ('food', 'part', 'state', 'processing', 'quality_composition')
BASIS_ATTRIBUTES = ('reference_measure', 'edible_portion', 'weight_basis', 'recipe_declared_mass',
                    'photo_consumed_mass', 'data_version', 'nutrient_units', 'nutrient_definition')
PER_NUTRIENT = ('nutrient_units', 'nutrient_definition')
RECIPE_MASS = ('basis', 'recipe_declared_mass', None)
PHOTO_MASS = ('basis', 'photo_consumed_mass', None)
# Properties of the official record alone: a mismatch compares the source with the basis target, not the photo.
SOURCE_ONLY = ('reference_measure', 'data_version', 'nutrient_units', 'nutrient_definition')
JUDGMENTS = IDENTITY_ATTRIBUTES + ('edible_fraction_visible',)
# A decided photo-side claim for these attributes must cite the matching independent visual judgment.
JUDGMENT_FOR = {**{name: name for name in IDENTITY_ATTRIBUTES},
                'edible_portion': 'edible_fraction_visible', 'weight_basis': 'state'}

RECIPE_SLOTS = ('parsing_snapshot', 'expected_recipe_source', 'candidate_lookup')
SLOT_ORIGINS = {'visual_evidence': ('photo_evidence_record', 'bytes'),
                'portion_estimate': ('photo_portion_record', 'bytes'),
                'source_lookup': ('source_lookup', 'native'),
                'source_provenance': ('source_provenance', 'bytes')}
SLOTS = tuple(SLOT_ORIGINS) + ('evidence_files',)
OFFICIAL_ORIGINS = ('official_record', 'official_document')
PHOTO_ORIGINS = ('photo_evidence_record', 'photo_portion_record', 'photo_visual_review')
LOW_TRUST_ORIGINS = ('caller_assertion', 'unverified_claim')
FILE_ORIGINS = OFFICIAL_ORIGINS + ('photo_visual_review',) + LOW_TRUST_ORIGINS
CATALOG_ORIGINS = tuple(origin for origin, _ in SLOT_ORIGINS.values()) + FILE_ORIGINS
SIDE_ORIGINS = {'photo': PHOTO_ORIGINS + LOW_TRUST_ORIGINS, 'source': OFFICIAL_ORIGINS + LOW_TRUST_ORIGINS}
PHOTO_ROLE = {'photo_evidence_record': 'photo_observation', 'photo_visual_review': 'photo_observation',
              'photo_portion_record': 'photo_mass'}
SOURCE_ROLES = ('food_identity', 'sample_preparation', 'measurement_basis', 'version', 'nutrient_definition')

LEDGER_FIELDS = frozenset({'profile', 'authority_ref', 'policy_revision', 'photo_scope', 'candidate_scope',
                           'source_link', 'claims', 'evidence', 'attachment_catalog', 'reviews', 'gate_records'})
CANDIDATE_FIELDS = frozenset({'provider', 'dataset', 'food_id', 'original_name', 'data_type',
                              'archive_sha256', 'member_sha256', 'sample_sha256'})
REGION_FIELDS = frozenset({'format', 'coordinate_space', 'x_min', 'y_min', 'x_max', 'y_max'})
GATE_DECISION = {'compatible': 'passed', 'incompatible': 'failed', 'needs_review': 'pending'}


def _aware(value):
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return moment.utcoffset() is not None


def _validate_shape(review_input, attribute_results, gate_records) -> ReviewValidation:
    issues = []

    def issue(code, path, message):
        issues.append(ReviewIssue(code, path, message))

    if not isinstance(review_input, dict) or set(review_input) != set(PhotoReviewInput.__annotations__):
        if isinstance(review_input, dict) and 'recipe_scope' in review_input:
            issue('recipe_evidence_not_allowed', '/input/recipe_scope', 'A photo review has no recipe scope')
        issue('input_fields', '/input', 'Expected exact Photo Review Input fields')
    else:
        if (review_input['contract'] != CONTRACT or type(review_input['schema_version']) is not int
                or review_input['schema_version'] != SCHEMA_VERSION or review_input['purpose'] != USAGE):
            issue('input_profile', '/input', 'Expected photo item source review v0 diagnostic/test_only profile')
        scope = review_input['photo_scope']
        if (not isinstance(scope, dict) or set(scope) != set(PhotoScope.__annotations__)
                or not all(text(scope[k]) for k in ('evidence_type', 'evidence_id', 'ingredient_candidate', 'estimate_id'))
                or not all(digest(scope[k]) for k in ('image_sha256', 'evidence_sha256', 'estimate_sha256'))):
            issue('photo_scope', '/input/photo_scope', 'Invalid photo scope shape')
        source = review_input['candidate_scope']
        if (not isinstance(source, dict) or set(source) != CANDIDATE_FIELDS or source.get('provider') != 'tfda'
                or source.get('dataset') != '8543'
                or not all(text(source.get(name)) for name in ('food_id', 'original_name', 'data_type'))
                or not all(digest(source.get(name)) for name in ('archive_sha256', 'member_sha256', 'sample_sha256'))):
            issue('candidate_scope', '/input/candidate_scope', 'Invalid TFDA candidate scope shape')
        link = review_input['source_link']
        if (not isinstance(link, dict) or set(link) != set(SourceLink.__annotations__) or not text(link['food_id'])
                or link['food_id'] != link['food_id'].strip() or not text(link['link_ref'])
                or not digest(link['expected_archive_sha256']) or not digest(link['expected_sample_sha256'])):
            issue('source_link', '/input/source_link', 'Invalid explicit source link shape')
        refs = review_input['attachment_refs']
        if isinstance(refs, dict) and any(name in refs for name in RECIPE_SLOTS):
            issue('recipe_evidence_not_allowed', '/input/attachment_refs',
                  'Recipe, Parsing and candidate lookup attachments cannot enter a photo review')
        for name in ('claims', 'evidence'):
            if not isinstance(review_input[name], list):
                issue('input_type', '/input/' + name, 'Expected array')
    if not isinstance(attribute_results, list) or not attribute_results:
        issue('attribute_results', '/attribute_results', 'Expected nonempty array')
    else:
        seen = set()
        for index, row in enumerate(attribute_results):
            path = f'/attribute_results/{index}'
            if isinstance(row, dict) and 'recipe_claim_ref' in row:
                issue('recipe_evidence_not_allowed', path + '/recipe_claim_ref', 'A photo review has no recipe claims')
                continue
            if not isinstance(row, dict) or set(row) != set(PhotoAttributeResult.__annotations__):
                issue('attribute_fields', path, 'Expected exact Photo Attribute Result fields')
                continue
            domain = row['domain']
            attrs = IDENTITY_ATTRIBUTES if domain == 'identity' else BASIS_ATTRIBUTES if domain == 'basis' else ()
            if row['attribute'] not in attrs:
                issue('attribute_scope', path, 'Unknown domain or attribute')
            nutrient = row['nutrient_key']
            per_nutrient = domain == 'basis' and row['attribute'] in PER_NUTRIENT
            if (text(nutrient) if per_nutrient else nutrient is None) is not True:
                issue('nutrient_key', path + '/nutrient_key', 'Nutrient key applicability differs from attribute')
            if isinstance(domain, str) and isinstance(row['attribute'], str) and (nutrient is None or isinstance(nutrient, str)):
                key = (domain, row['attribute'], nutrient)
                if key in seen:
                    issue('duplicate_attribute', path, 'Duplicate attribute result')
                seen.add(key)
            if row['status'] not in ('verified', 'mismatch', 'unresolved', 'not_applicable'):
                issue('attribute_status', path + '/status', 'Unknown review state')
            for name in ('reason_code', 'rule_id', 'rule_version', 'reviewer_ref', 'reviewed_at'):
                if not text(row[name]):
                    issue('attribute_type', path + '/' + name, 'Expected nonempty text')
            for name in ('photo_claim_ref', 'source_claim_ref'):
                if row[name] is not None and not text(row[name]):
                    issue('claim_ref', path + '/' + name, 'Expected claim ID or null')
            refs = row['evidence_ids']
            if (not isinstance(refs, list) or any(not text(ref) for ref in refs)
                    or (all(isinstance(ref, str) for ref in refs) and len(set(refs)) != len(refs))):
                issue('evidence_refs', path + '/evidence_ids', 'Expected unique evidence IDs')
            if row['status'] in ('verified', 'mismatch', 'not_applicable') and not refs:
                issue('evidence_required', path, 'State requires evidence, not just a declaration')
            recipe_mass = domain == 'basis' and row['attribute'] == 'recipe_declared_mass'
            if row['status'] == 'not_applicable' and (not recipe_mass or row['rule_id'] != NA_RULE):
                issue('invalid_not_applicable', path, 'Only the photo-item recipe-mass exclusion rule permits NA')
            if recipe_mass and row['status'] != 'not_applicable':
                issue('invalid_recipe_mass_review', path, 'Recipe mass is excluded by rule, never reviewed for a photo item')
            if not _aware(row['reviewed_at']):
                issue('review_time', path + '/reviewed_at', 'Expected timezone-aware ISO date/time')
    if not isinstance(gate_records, list):
        issue('gate_records', '/gate_records', 'Expected array of source gate records')
    return ReviewValidation(tuple(issues))


def _verify_visual_review(record, scope, item, people, path):
    """Independent judgments of the original image; unknown stays unknown, never filled from a dish name."""
    require(isinstance(record, dict) and set(record) == set(PhotoVisualReview.__annotations__)
            and record['profile'] == VISUAL_REVIEW_PROFILE and record['image_access'] == 'reviewed_original_image'
            and record['usage'] == USAGE and text(record['reviewer_ref']) and _aware(record['reviewed_at'])
            and isinstance(record['judgments'], dict) and set(record['judgments']) == set(JUDGMENTS),
            'photo_visual_review_invalid', path)
    require(record['photo_scope'] == scope, 'photo_visual_review_scope', path)
    require(record['reviewer_ref'] not in people, 'photo_review_not_independent', path)
    region, outer = item['image_region'], region_box(item['image_region'])
    for name in JUDGMENTS:
        judgment, where = record['judgments'][name], f'{path}/judgments/{name}'
        require(isinstance(judgment, dict) and set(judgment) == {'value_state', 'value', 'region'},
                'photo_visual_review_invalid', where)
        if judgment['value_state'] == 'known':
            box = region_box(judgment['region'])
            require(text(judgment['value']) and judgment['value'].lower() != 'unknown' and box is not None
                    and set(judgment['region']) == REGION_FIELDS
                    and judgment['region']['format'] == region.get('format')
                    and judgment['region']['coordinate_space'] == region.get('coordinate_space'),
                    'photo_visual_review_invalid', where)
            require(outer[0] <= box[0] and outer[1] <= box[1] and box[2] <= outer[2] and box[3] <= outer[3],
                    'judgment_region_outside_evidence', where)
        else:
            require(judgment['value_state'] in ('unknown', 'missing') and judgment['value'] is None
                    and judgment['region'] is None, 'photo_visual_review_invalid', where)
    return record


def _require_judgment(claim, name, evidences, visual_reviews, path):
    """The photo claim cites the same-named known judgment and states exactly its value."""
    pointer = {'pointer': f'/judgments/{name}/value'}
    cited = [evidences[ref] for ref in claim['evidence_ids']
             if evidences[ref]['origin'] == 'photo_visual_review' and evidences[ref]['locator'] == pointer]
    require(cited and all(visual_reviews[ev['attachment_ref']]['judgments'][name]['value_state'] == 'known' for ev in cited),
            'photo_visual_review_required', path)
    require(all(claim['value_kind'] == 'text' and claim['value'] == ev['raw_value'] for ev in cited),
            'photo_claim_judgment_mismatch', path)


def _check_decided(r, linked, origins, evidences, visual_reviews, verified, path):
    """Evidence requirements for a verified or mismatch result; the semantic decision stays human."""
    attribute = r['attribute']
    require(linked and all(claim['value_state'] == 'known' for claim in linked.values()),
            'unresolved_claim_cannot_verify', path)
    require(origins and origins <= set(OFFICIAL_ORIGINS + PHOTO_ORIGINS), 'caller_claim_only', path)
    if attribute == 'photo_consumed_mass':
        estimate = verified['estimate']
        require('photo_portion_record' in origins, 'photo_mass_evidence_required', path)
        claim = linked.get('photo')
        require(claim is not None and claim['value'] == estimate['mass_g'] and claim['unit'] == 'g'
                and estimate['mass_kind'] == 'visible_mass_at_capture', 'photo_mass_claim_mismatch', path)
    else:
        require(origins & set(OFFICIAL_ORIGINS), 'source_evidence_required', path)
    if (r['domain'] == 'identity' or attribute in ('edible_portion', 'weight_basis')
            or r['status'] == 'mismatch' and attribute not in SOURCE_ONLY):
        require(set(linked) == {'photo', 'source'} and origins & set(PHOTO_ORIGINS) and origins & set(OFFICIAL_ORIGINS),
                'two_sided_evidence_required', path)
    if attribute in JUDGMENT_FOR:
        _require_judgment(linked['photo'], JUDGMENT_FOR[attribute], evidences, visual_reviews, path)
    if attribute == 'processing':
        require(all(evidences[e]['statement_role'] != 'sample_preparation' for e in r['evidence_ids']),
                'processing_role_unresolved', path)
    food, prov = verified['food'], verified['provenance']
    if attribute == 'data_version':
        require(prov.get('official_dataset_version') is not None, 'official_version_unknown', path)
        if r['status'] == 'verified':
            claim = linked.get('source')
            require(claim is not None and claim['value'] == prov['official_dataset_version'], 'version_claim_mismatch', path)
    if attribute == 'nutrient_units':
        unit = next(n['expected_unit'] for n in verified['nutrients'] if n['original_name'] == r['nutrient_key'])
        observations = [o for o in food.observations if o.original_name == r['nutrient_key']]
        require(len(observations) == 1 and observations[0].unit is not None, 'nutrient_unit_missing', path)
        if r['status'] == 'verified':
            require(observations[0].unit == unit, 'nutrient_unit_conflict', path)
            claim = linked.get('source')
            require(claim is not None and claim['value'] == unit, 'unit_claim_mismatch', path)
    if attribute == 'nutrient_definition' and r['status'] == 'verified':
        observations = [o for o in food.observations if o.original_name == r['nutrient_key']]
        require(len(observations) == 1 and observations[0].value.status == 'reported', 'nutrient_value_unresolved', path)


def _verify_gates(gates, source, revision, statuses, provenance):
    """Source-use and integrity gates from the pinned ledger; photo calculation never accepts not_applicable."""
    found = {}
    for index, gate in enumerate(gates):
        path = f'/gate_records/{index}'
        require(isinstance(gate, dict) and set(gate) == set(SourceGateRecord.__annotations__)
                and gate['profile'] == GATE_RECORD_PROFILE, 'gate_record_fields', path)
        require(gate['gate'] in ('source_use', 'integrity') and gate['gate'] not in found, 'gate_scope_or_duplicate', path)
        require(gate['purpose'] == GATE_PURPOSE, 'gate_purpose', path)
        require(gate['candidate_scope'] == source, 'gate_scope_mismatch', path)
        require(gate['status'] in ('pending', 'passed', 'failed'), 'gate_status', path)
        require(text(gate['reason_code']), 'gate_reason_code', path)
        if gate['status'] == 'pending':
            require(all(gate[name] is None for name in ('evidence_locator', 'reviewer_ref', 'reviewed_at', 'policy_revision')),
                    'gate_fields_without_decision', path)
        else:
            require(text(gate['reviewer_ref']) and _aware(gate['reviewed_at'])
                    and type(gate['policy_revision']) is int and gate['policy_revision'] == revision,
                    'gate_record_incomplete', path)
            require(text(gate['evidence_locator']) or gate['status'] == 'failed' and gate['evidence_locator'] is None,
                    'gate_evidence_missing', path)
        if gate['gate'] == 'integrity' and gate['status'] == 'passed':
            require(provenance.get('official_dataset_version') is not None, 'integrity_version_unknown', path)
            require(statuses[('basis', 'data_version', None)] == 'verified', 'integrity_requires_verified_version', path)
        found[gate['gate']] = gate
    return found


def _verify(value, results, gates, context, anchor):
    ledger = load_trusted_ledger(context, anchor)
    require(set(ledger) == LEDGER_FIELDS, 'context_fields', '/context')
    require(ledger['profile'] == CONTEXT_PROFILE and ledger['authority_ref'] == anchor.authority_ref,
            'context_authority_mismatch', '/context')
    revision = ledger['policy_revision']
    require(type(revision) is int and revision >= 1, 'context_policy_revision', '/context/policy_revision')
    for field in ('photo_scope', 'candidate_scope', 'source_link', 'claims', 'evidence'):
        require(ledger[field] == value[field], 'context_scope_or_statement_mismatch', '/input/' + field)
    require(ledger['gate_records'] == gates, 'forged_gate_record', '/gate_records')
    scope, source, link = value['photo_scope'], value['candidate_scope'], value['source_link']
    target = value['basis_target']
    require(isinstance(target, dict) and set(target) == {'purpose', 'required_nutrients'}
            and target['purpose'] == BASIS_PURPOSE, 'basis_target', '/input/basis_target')
    nutrients = target['required_nutrients']
    require(isinstance(nutrients, list) and nutrients and all(isinstance(n, dict) and set(n) == {'original_name', 'expected_unit'}
            and text(n['original_name']) and text(n['expected_unit']) for n in nutrients), 'nutrient_profile', '/input/basis_target')
    names = [n['original_name'] for n in nutrients]
    require(len(set(names)) == len(names), 'duplicate_nutrient', '/input/basis_target')
    required = {('identity', a, None) for a in IDENTITY_ATTRIBUTES}
    required |= {('basis', a, None) for a in BASIS_ATTRIBUTES if a not in PER_NUTRIENT}
    required |= {('basis', a, n) for a in PER_NUTRIENT for n in names}
    require({(r['domain'], r['attribute'], r['nutrient_key']) for r in results} == required,
            'required_attributes', '/attribute_results')

    refs = value['attachment_refs']
    require(isinstance(refs, dict) and set(refs) == set(SLOTS) and isinstance(refs['evidence_files'], list),
            'attachment_fields', '/input/attachment_refs')
    catalog = ledger['attachment_catalog']
    expected_scope = {'photo_scope': scope, 'candidate_scope': source}
    require(isinstance(catalog, dict), 'attachment_catalog', '/context/attachment_catalog')
    for key, meta in catalog.items():
        path = f'/context/attachment_catalog/{key}'
        require(isinstance(meta, dict) and set(meta) == {'origin', 'representation', 'sha256', 'scope'}, 'attachment_catalog', path)
        recipe_origin = isinstance(meta['origin'], str) and meta['origin'].startswith('recipe')
        require(not recipe_origin and not (isinstance(meta['scope'], dict) and 'recipe_scope' in meta['scope']),
                'recipe_evidence_not_allowed', path)
        require(meta['origin'] in CATALOG_ORIGINS, 'attachment_origin', path)
        require(meta['scope'] == expected_scope, 'attachment_scope', path)
    resolved = resolve_attachments([refs[name] for name in SLOT_ORIGINS] + refs['evidence_files'],
                                   context, catalog, expected_scope)
    for name, (origin, representation) in SLOT_ORIGINS.items():
        meta = catalog[refs[name]['attachment_id']]
        require(meta['origin'] == origin and meta['representation'] == representation,
                'attachment_origin', '/input/attachment_refs/' + name)
    for ref in refs['evidence_files']:
        meta = catalog[ref['attachment_id']]
        require(meta['origin'] in FILE_ORIGINS and meta['representation'] == 'bytes',
                'attachment_origin', '/input/attachment_refs/evidence_files')

    def slot(name):
        return resolved[refs[name]['attachment_id']]

    item = load_json(slot('visual_evidence'))
    require(isinstance(item, dict), 'invalid_visual_evidence', '/attachments/visual_evidence')
    require(evidence_blocker(item) is None, 'photo_scope_not_reviewable', '/attachments/visual_evidence')
    identity = evidence_identity(item)
    require(identity['evidence_sha256'] == scope['evidence_sha256'], 'visual_evidence_hash_mismatch', '/input/photo_scope')
    require(all(identity[k] == scope[k] for k in ('evidence_type', 'evidence_id', 'ingredient_candidate')),
            'photo_scope_mismatch', '/input/photo_scope')
    origin_meta = item['evidence_source']
    require(origin_meta.get('image_sha256') == scope['image_sha256'], 'photo_image_unbound', '/attachments/visual_evidence')
    require(text(origin_meta.get('annotator')), 'visual_evidence_producer_unrecorded', '/attachments/visual_evidence')

    estimate = load_json(slot('portion_estimate'))
    require(validate_portion_estimate(estimate) == [], 'invalid_portion_estimate', '/attachments/portion_estimate')
    require(canonical_sha256(estimate) == scope['estimate_sha256'], 'portion_estimate_hash_mismatch', '/input/photo_scope')
    require(estimate['estimate_id'] == scope['estimate_id'], 'photo_scope_mismatch', '/input/photo_scope')
    require(estimate['evidence_ref'] == identity, 'portion_evidence_unbound', '/attachments/portion_estimate')

    require((link['food_id'], link['expected_archive_sha256'], link['expected_sample_sha256'])
            == (source['food_id'], source['archive_sha256'], source['sample_sha256']), 'source_link_mismatch', '/input/source_link')
    lookup = slot('source_lookup')
    require(isinstance(lookup, SourceLookup), 'invalid_source_lookup', '/attachments/source_lookup')
    food = verify_food_record(lookup, source, '/attachments/source_lookup')
    prov, by_index = verify_source_observations(food, slot('source_provenance'), source, resolved)

    people = {origin_meta['annotator'], estimate['producer']['ref']}
    if estimate['measurement'] is not None:
        people.add(estimate['measurement']['measured_by'])
    visual_reviews = {ref['attachment_id']: _verify_visual_review(load_json(resolved[ref['attachment_id']]), scope, item,
                                                                  people, '/attachments/' + ref['attachment_id'])
                      for ref in refs['evidence_files'] if catalog[ref['attachment_id']]['origin'] == 'photo_visual_review'}

    evidences = {}
    for index, ev in enumerate(value['evidence']):
        path = f'/input/evidence/{index}'
        require(not (isinstance(ev, dict) and ev.get('origin') == 'recipe_text'), 'recipe_evidence_not_allowed', path)
        raw = check_evidence_record(ev, evidences, resolved, catalog, path)
        origin = ev['origin']
        if origin == 'official_record':
            extracted = official_record_value(ev, by_index, path)
        elif origin == 'official_document':
            extracted = official_document_value(ev, raw, source, path)
        elif origin in PHOTO_ORIGINS:
            # Defense in depth: slot and evidence-file origins already leave no other attachment with these origins.
            allowed = ({refs['visual_evidence']['attachment_id']} if origin == 'photo_evidence_record' else
                       {refs['portion_estimate']['attachment_id']} if origin == 'photo_portion_record' else set(visual_reviews))
            require(ev['attachment_ref'] in allowed, 'photo_evidence_attachment', path)
            extracted = pointer_value(ev, raw, 'photo_locator', path)
        elif origin in LOW_TRUST_ORIGINS:
            extracted = pointer_value(ev, raw, 'assertion_locator', path)
        else:
            raise Reject('unsupported_evidence_origin', path)
        check_extracted(ev, extracted, path)
        roles = ((PHOTO_ROLE[origin],) if origin in PHOTO_ORIGINS else
                 SOURCE_ROLES if origin in OFFICIAL_ORIGINS else ('caller_claim',))
        require(ev['statement_role'] in roles, 'statement_role', path)
        evidences[ev['evidence_id']] = ev

    claims, seen = {}, set()
    for claim in value['claims']:
        require(isinstance(claim, dict) and set(claim) == set(PhotoReviewClaim.__annotations__) and text(claim['claim_id'])
                and claim['claim_id'] not in claims, 'claim_fields_or_id', '/input/claims')
        key = (claim['domain'], claim['attribute'], claim['nutrient_key'])
        require(claim['side'] != 'recipe' and key != RECIPE_MASS, 'recipe_evidence_not_allowed', '/input/claims')
        require(key in required and claim['side'] in SIDE_ORIGINS and (key, claim['side']) not in seen,
                'claim_scope_or_duplicate', '/input/claims')
        seen.add((key, claim['side']))
        cited = claim['evidence_ids']
        require(isinstance(cited, list) and all(ref in evidences for ref in cited) and len(set(cited)) == len(cited),
                'claim_evidence', '/input/claims')
        require(all(evidences[ref]['origin'] in SIDE_ORIGINS[claim['side']] for ref in cited),
                'claim_side_evidence', '/input/claims')
        require(claim['unit'] is None or text(claim['unit']), 'claim_unit', '/input/claims')
        mass_claim = key == PHOTO_MASS and claim['side'] == 'photo'
        require(claim['value_kind'] in (('decimal_interval',) if mass_claim else ('text', 'text_list', 'decimal')),
                'claim_kind', '/input/claims')
        if claim['value_state'] == 'known':
            v, kind = claim['value'], claim['value_kind']
            valid = (interval(v, '/input/claims', []) is not None and claim['unit'] == 'g' if kind == 'decimal_interval' else
                     text(v) and v.lower() != 'unknown' if kind == 'text' else
                     isinstance(v, list) and bool(v) and all(text(t) for t in v) if kind == 'text_list' else
                     isinstance(v, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', v) is not None)
            require(valid and cited, 'known_claim_requires_value_evidence', '/input/claims')
        else:
            require(claim['value_state'] in ('missing', 'unknown') and claim['value'] is None
                    and (claim['value_state'] != 'unknown' or cited), 'missing_unknown_claim', '/input/claims')
        claims[claim['claim_id']] = claim

    verified = {'food': food, 'provenance': prov, 'estimate': estimate, 'nutrients': nutrients}
    manual = index_review_records(ledger['reviews'], results)
    statuses = {}
    for index, r in enumerate(results):
        path = f'/attribute_results/{index}'
        key = (r['domain'], r['attribute'], r['nutrient_key'])
        require(key in manual and manual[key]['review_status'] == 'reviewed', 'review_not_checked', path, 'blocked')
        record = manual[key]
        require(record['result'] == r, 'forged_review_result', path)
        require(record['method'] == 'manual' and r['rule_id'] == MANUAL_RULE
                or record['method'] == 'controlled_rule' and r['status'] == 'not_applicable' and r['rule_id'] == NA_RULE,
                'unsupported_review_rule', path, 'blocked')
        require(r['rule_version'] == '1', 'unsupported_rule_version', path, 'blocked')
        require(all(e in evidences for e in r['evidence_ids']), 'missing_evidence_ref', path)
        linked = {}
        for side in ('photo', 'source'):
            ref = r[side + '_claim_ref']
            if ref is not None:
                require(ref in claims and (claims[ref]['domain'], claims[ref]['attribute'], claims[ref]['nutrient_key']) == key
                        and claims[ref]['side'] == side, 'cross_attribute_claim', path)
                require(set(claims[ref]['evidence_ids']) <= set(r['evidence_ids']), 'result_evidence_coverage', path)
                linked[side] = claims[ref]
        origins = {evidences[e]['origin'] for e in r['evidence_ids']}
        if r['status'] in ('verified', 'mismatch'):
            _check_decided(r, linked, origins, evidences, visual_reviews, verified, path)
        if r['status'] == 'not_applicable':
            require('photo_portion_record' in origins, 'na_evidence_required', path)
        statuses[key] = r['status']
    verified['gates'] = _verify_gates(gates, source, revision, statuses, prov)
    return verified


def _validated(review_input, attribute_results, gate_records, context, trust_anchor):
    """(ReviewValidation, verified records or None); inputs are copied, never mutated or repaired."""
    shape = _validate_shape(review_input, attribute_results, gate_records)
    if shape.issues:
        return shape, None
    try:
        verified = _verify(deepcopy(review_input), deepcopy(attribute_results), deepcopy(gate_records),
                           context, trust_anchor)
    except Reject as error:
        return ReviewValidation((ReviewIssue(error.code, error.path, 'Photo review validation blocked or rejected'),),
                                error.state), None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError, OverflowError):
        return ReviewValidation((ReviewIssue('malformed_evidence_or_context', '/input',
                                             'Malformed evidence/context; no repair'),)), None
    return ReviewValidation((), 'valid'), verified


def validate_photo_review(review_input, attribute_results, gate_records, *, context=None,
                          trust_anchor=None) -> ReviewValidation:
    """Full record and evidence verification; valid means checked, never approved or nutrition-eligible."""
    return _validated(review_input, attribute_results, gate_records, context, trust_anchor)[0]


def _invalid_decision(report):
    return {'identity_decision': 'invalid', 'basis_decision': 'invalid', 'input_integrity': 'invalid',
            'blocking_reasons': list(dict.fromkeys(i.code for i in report.issues)), 'attribute_results': [],
            'usage': USAGE, 'nutrition_eligible': False}


def aggregate_photo_review(review_input, attribute_results, gate_records, *, context=None, trust_anchor=None):
    """Always validate first; the Review v1 fold (mismatch, then unresolved, then compatible) is reused."""
    results = deepcopy(attribute_results)
    report = validate_photo_review(review_input, results, gate_records, context=context, trust_anchor=trust_anchor)
    return _aggregate_validated(results) if report.valid else _invalid_decision(report)


def _unavailable(reasons, decision):
    return {'status': 'unavailable', 'reasons': list(reasons), 'basis': None, 'decision': decision,
            'usage': USAGE, 'nutrition_eligible': False, 'production_eligible': False}


def build_photo_nutrition_basis(review_input, attribute_results, gate_records, *, context=None, trust_anchor=None,
                                energy_nutrient):
    """PhotoNutritionBasis for estimate_photo_energy, rebuilt from a fully revalidated review every call.

    No caller conclusion is accepted. Invalid or blocked reviews produce no basis (downstream
    nutrition_basis_unavailable); a valid review with pending or failed gates produces a basis that
    keeps those gates, so photo calculation stays blocked. Values are copied, never converted.
    """
    results = deepcopy(attribute_results)
    report, verified = _validated(review_input, results, gate_records, context, trust_anchor)
    if not report.valid:
        decision = _invalid_decision(report)
        return _unavailable(decision['blocking_reasons'], decision)
    decision = _aggregate_validated(results)
    if energy_nutrient not in [n['original_name'] for n in verified['nutrients']]:
        return _unavailable(['energy_nutrient_not_reviewed'], decision)
    observations = [o for o in verified['food'].observations if o.original_name == energy_nutrient]
    if len(observations) != 1:
        return _unavailable(['energy_observation_missing'], decision)
    observation = observations[0]
    if observation.value.status != 'reported' or observation.value.parsed_value is None:
        return _unavailable(['energy_value_unreported'], decision)
    if observation.unit not in ('kcal', 'kJ'):
        return _unavailable(['energy_unit_not_supported'], decision)
    review_ref = f'photo-review-context:sha256:{trust_anchor.context_sha256}'
    gates = {}
    for name in GATES:
        if name in ('identity', 'basis'):
            gates[name] = {'status': GATE_DECISION[decision[name + '_decision']], 'evidence_ref': f'{review_ref}#{name}'}
        else:
            record = verified['gates'].get(name)
            gates[name] = ({'status': record['status'], 'evidence_ref': record['evidence_locator']} if record
                           else {'status': 'pending', 'evidence_ref': None})
    scope = review_input['photo_scope']
    basis = {'basis_id': 'photo-basis:' + canonical_sha256({'context_sha256': trust_anchor.context_sha256,
                                                            'estimate_id': scope['estimate_id'],
                                                            'food_id': review_input['candidate_scope']['food_id'],
                                                            'energy_nutrient': energy_nutrient}),
             'estimate_id': scope['estimate_id'], 'ingredient_candidate': scope['ingredient_candidate'],
             'purpose': 'photo_calculation',
             'energy': {'original_name': observation.original_name, 'unit': observation.unit,
                        'per': 'per_100g_edible_portion', 'value': format(observation.value.parsed_value, 'f'),
                        'interval': None},
             'gates': gates, 'reviewer_ref': ', '.join(sorted({r['reviewer_ref'] for r in results})), 'usage': USAGE}
    if validate_energy_basis(basis):
        return _unavailable(['basis_self_check_failed'], decision)
    return {'status': 'built', 'basis': basis, 'decision': decision, 'usage': USAGE,
            'reasons': [f'gate_{name}_{gate["status"]}' for name, gate in gates.items() if gate['status'] != 'passed'],
            'nutrition_eligible': False, 'production_eligible': False}
