"""Offline Prototype Profile v0: verify records and evidence, never infer semantics."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import re

from src.recipe_parsing import validate_recipe_parsing
from .review_common import (Reject as _Reject, require as _require, text as _text, digest as _digest,
                            load_json as _json, load_trusted_ledger, resolve_attachments, verify_food_record,
                            verify_source_observations, check_evidence_record, official_record_value,
                            official_document_value, pointer_value, check_extracted, index_review_records)
from .review_types import AttributeResult, ReviewInput, ReviewIssue, ReviewValidation


IDENTITY_ATTRIBUTES = ('food', 'part', 'state', 'processing', 'quality_composition')
BASIS_ATTRIBUTES = ('reference_measure', 'edible_portion', 'weight_basis', 'recipe_declared_mass',
                    'photo_consumed_mass', 'data_version', 'nutrient_units', 'nutrient_definition')
NA_RULE = 'diagnostic_recipe_reference_excludes_photo_v1'


def _validate_shape(review_input, attribute_results) -> ReviewValidation:
    issues = []
    def issue(code, path, message):
        issues.append(ReviewIssue(code, path, message))

    if not isinstance(review_input, dict) or set(review_input) != set(ReviewInput.__annotations__):
        issue('input_fields', '/input', 'Expected exact Review Input fields')
    else:
        if (review_input['contract'] != 'foodcal.ingredient_source_review'
                or type(review_input['schema_version']) is not int or review_input['schema_version'] != 1
                or review_input['purpose'] != 'diagnostic/test_only'):
            issue('input_profile', '/input', 'Expected review v1 diagnostic/test_only profile')
        scope = review_input['recipe_scope']
        if (not isinstance(scope, dict) or set(scope) != {'input_id', 'ingredient_id', 'recipe_text_sha256'}
                or not _text(scope.get('input_id')) or not _text(scope.get('ingredient_id'))
                or not _digest(scope.get('recipe_text_sha256'))):
            issue('recipe_scope', '/input/recipe_scope', 'Invalid Parsing scope shape')
        source = review_input['candidate_scope']
        fields = {'provider', 'dataset', 'food_id', 'original_name', 'data_type',
                  'archive_sha256', 'member_sha256', 'sample_sha256'}
        if (not isinstance(source, dict) or set(source) != fields or source.get('provider') != 'tfda'
                or source.get('dataset') != '8543'
                or not all(_text(source.get(name)) for name in ('food_id', 'original_name', 'data_type'))
                or not all(_digest(source.get(name)) for name in ('archive_sha256', 'member_sha256', 'sample_sha256'))):
            issue('candidate_scope', '/input/candidate_scope', 'Invalid TFDA candidate scope shape')
        for name in ('claims', 'evidence'):
            if not isinstance(review_input[name], list):
                issue('input_type', '/input/' + name, 'Expected array')
    if not isinstance(attribute_results, list) or not attribute_results:
        issue('attribute_results', '/attribute_results', 'Expected nonempty array')
    else:
        seen = set()
        for index, row in enumerate(attribute_results):
            path = f'/attribute_results/{index}'
            if not isinstance(row, dict) or set(row) != set(AttributeResult.__annotations__):
                issue('attribute_fields', path, 'Expected exact Attribute Result fields')
                continue
            domain = row['domain']
            attrs = IDENTITY_ATTRIBUTES if domain == 'identity' else BASIS_ATTRIBUTES if domain == 'basis' else ()
            if row['attribute'] not in attrs:
                issue('attribute_scope', path, 'Unknown domain or attribute')
            nutrient = row['nutrient_key']
            per_nutrient = domain == 'basis' and row['attribute'] in ('nutrient_units', 'nutrient_definition')
            if (_text(nutrient) if per_nutrient else nutrient is None) is not True:
                issue('nutrient_key', path + '/nutrient_key', 'Nutrient key applicability differs from attribute')
            if isinstance(domain, str) and isinstance(row['attribute'], str) and (nutrient is None or isinstance(nutrient, str)):
                key = (domain, row['attribute'], nutrient)
                if key in seen:
                    issue('duplicate_attribute', path, 'Duplicate attribute result')
                seen.add(key)
            if row['status'] not in ('verified', 'mismatch', 'unresolved', 'not_applicable'):
                issue('attribute_status', path + '/status', 'Unknown review state')
            for name in ('reason_code', 'rule_id', 'rule_version', 'reviewer_ref', 'reviewed_at'):
                if not _text(row[name]):
                    issue('attribute_type', path + '/' + name, 'Expected nonempty text')
            for name in ('recipe_claim_ref', 'source_claim_ref'):
                if row[name] is not None and not _text(row[name]):
                    issue('claim_ref', path + '/' + name, 'Expected claim ID or null')
            refs = row['evidence_ids']
            if (not isinstance(refs, list) or any(not _text(ref) for ref in refs)
                    or (all(isinstance(ref, str) for ref in refs) and len(set(refs)) != len(refs))):
                issue('evidence_refs', path + '/evidence_ids', 'Expected unique evidence IDs')
            if row['status'] in ('verified', 'mismatch', 'not_applicable') and not refs:
                issue('evidence_required', path, 'State requires evidence, not just a declaration')
            if row['status'] == 'not_applicable' and (domain != 'basis' or row['attribute'] != 'photo_consumed_mass'
                                                     or row['rule_id'] != NA_RULE):
                issue('invalid_not_applicable', path, 'Only the scoped photo exclusion rule permits NA')
            try:
                timestamp = datetime.fromisoformat(row['reviewed_at'])
                if timestamp.utcoffset() is None:
                    raise ValueError('Missing timezone')
            except (ValueError, TypeError):
                issue('review_time', path + '/reviewed_at', 'Expected timezone-aware ISO date/time')
    return ReviewValidation(tuple(issues))


def validate_review(review_input, attribute_results, *, context=None, trust_anchor=None) -> ReviewValidation:
    shape = _validate_shape(review_input, attribute_results)
    if shape.issues:
        return shape
    try:
        _verify(deepcopy(review_input), deepcopy(attribute_results), context, trust_anchor)
    except _Reject as error:
        return ReviewValidation((ReviewIssue(error.code, error.path, 'Review profile validation blocked or rejected'),), error.state)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError, OverflowError):
        return ReviewValidation((ReviewIssue('malformed_evidence_or_context', '/input', 'Malformed evidence/context; no repair'),))
    return ReviewValidation((), 'valid')


def _verify(value, results, context, anchor):
    ledger = load_trusted_ledger(context, anchor)
    _require(set(ledger) == {'profile', 'authority_ref', 'recipe_scope', 'candidate_scope', 'claims',
                             'evidence', 'attachment_catalog', 'reviews'}, 'context_fields', '/context')
    _require(ledger['profile'] == 'foodcal.review_context.v0' and ledger['authority_ref'] == anchor.authority_ref,
             'context_authority_mismatch', '/context')
    for field in ('recipe_scope', 'candidate_scope', 'claims', 'evidence'):
        _require(ledger[field] == value[field], 'context_scope_or_statement_mismatch', '/input/' + field)
    scope, source = value['recipe_scope'], value['candidate_scope']
    target = value['basis_target']
    _require(isinstance(target, dict) and set(target) == {'purpose', 'required_nutrients'}
             and target['purpose'] == 'recipe_declared_ingredient_reference', 'basis_target', '/input/basis_target')
    nutrients = target['required_nutrients']
    _require(isinstance(nutrients, list) and nutrients and all(isinstance(n, dict) and set(n) == {'original_name', 'expected_unit'}
             and _text(n['original_name']) and _text(n['expected_unit']) for n in nutrients), 'nutrient_profile', '/input/basis_target')
    names = [n['original_name'] for n in nutrients]
    _require(len(set(names)) == len(names), 'duplicate_nutrient', '/input/basis_target')
    required = {('identity', a, None) for a in IDENTITY_ATTRIBUTES}
    required |= {('basis', a, None) for a in BASIS_ATTRIBUTES if a not in ('nutrient_units', 'nutrient_definition')}
    required |= {('basis', a, n) for a in ('nutrient_units', 'nutrient_definition') for n in names}
    _require({(r['domain'], r['attribute'], r['nutrient_key']) for r in results} == required,
             'required_attributes', '/attribute_results')
    refs = value['attachment_refs']
    slots = {'parsing_snapshot', 'expected_recipe_source', 'candidate_lookup', 'source_provenance', 'evidence_files'}
    _require(isinstance(refs, dict) and set(refs) == slots and isinstance(refs['evidence_files'], list),
             'attachment_fields', '/input/attachment_refs')
    catalog = ledger['attachment_catalog']
    entries = [refs[name] for name in ('parsing_snapshot', 'expected_recipe_source', 'candidate_lookup', 'source_provenance')] + refs['evidence_files']
    resolved = resolve_attachments(entries, context, catalog, {'recipe_scope': scope, 'candidate_scope': source})
    def slot(name):
        return resolved[refs[name]['attachment_id']]
    parsing = slot('parsing_snapshot')
    if isinstance(parsing, bytes):
        parsing = _json(parsing)
    expected = slot('expected_recipe_source')
    if isinstance(expected, bytes):
        expected = _json(expected)
    _require(validate_recipe_parsing(parsing).valid and parsing['source'] == expected and expected is not None,
             'invalid_parsing_or_source', '/attachments/parsing_snapshot')
    _require(parsing['input_id'] == scope['input_id'] and parsing['source']['text_sha256'] == scope['recipe_text_sha256'],
             'recipe_scope_mismatch', '/input/recipe_scope')
    ingredient = next((i for i in parsing['ingredients'] if i['ingredient_id'] == scope['ingredient_id']), None)
    _require(ingredient is not None, 'ingredient_scope_mismatch', '/input/recipe_scope/ingredient_id')
    candidate = slot('candidate_lookup')
    _require(isinstance(candidate, dict) and candidate.get('usage') == 'diagnostic/test_only'
             and candidate.get('nutrition_eligible') is False
             and candidate['mock_mapping_snapshot']['parsing_snapshot'] == parsing, 'candidate_snapshot', '/attachments/candidate_lookup')
    items = [i for i in candidate['ingredients'] if i['ingredient_id'] == scope['ingredient_id']]
    _require(len(items) == 1 and all(items[0][key] == scope[key] for key in scope),
             'candidate_scope_mismatch', '/attachments/candidate_lookup')
    found = [c for c in items[0]['candidates'] if c['request']['food_id'] == source['food_id']]
    _require(len(found) == 1 and found[0]['source_status'] == 'found' and found[0]['link_check'] == 'verified',
             'candidate_unresolved', '/attachments/candidate_lookup', 'blocked')
    _require(found[0]['request']['expected_archive_sha256'] == source['archive_sha256']
             and found[0]['request']['expected_sample_sha256'] == source['sample_sha256'],
             'candidate_request_hash_mismatch', '/attachments/candidate_lookup')
    lookup = found[0]['source_lookup']
    food = verify_food_record(lookup, source, '/attachments/candidate_lookup')
    prov, by_index = verify_source_observations(food, slot('source_provenance'), source, resolved)
    evidences = {}
    for index, ev in enumerate(value['evidence']):
        path = f'/input/evidence/{index}'
        raw = check_evidence_record(ev, evidences, resolved, catalog, path)
        loc = ev['locator']
        if ev['origin'] == 'official_record':
            extracted = official_record_value(ev, by_index, path)
        elif ev['origin'] == 'recipe_text':
            _require(set(loc) == {'start', 'end'} and type(loc['start']) is int and type(loc['end']) is int
                     and 0 <= loc['start'] < loc['end'] <= len(expected['text']), 'recipe_span', path)
            relevant = [e for e in parsing['evidence'] if e['evidence_id'] in {link['evidence_id'] for link in ingredient['evidence_links']}]
            _require(any(e['start'] <= loc['start'] and loc['end'] <= e['end'] for e in relevant), 'cross_ingredient_evidence', path)
            _require(raw == expected['text'].encode('utf-8'), 'recipe_evidence_hash', path)
            extracted = expected['text'][loc['start']:loc['end']]
        elif ev['origin'] == 'official_document':
            extracted = official_document_value(ev, raw, source, path)
        elif ev['origin'] in ('caller_assertion', 'unverified_claim'):
            extracted = pointer_value(ev, raw, 'assertion_locator', path)
        else:
            raise _Reject('unsupported_evidence_origin', path)
        check_extracted(ev, extracted, path)
        _require(ev['statement_role'] in ('food_identity', 'sample_preparation', 'recipe_processing',
                                         'measurement_basis', 'version', 'nutrient_definition', 'caller_claim'), 'statement_role', path)
        evidences[ev['evidence_id']] = ev
    claims, seen = {}, set()
    claim_fields = {'claim_id', 'domain', 'attribute', 'nutrient_key', 'side', 'value_state', 'value_kind', 'value', 'unit', 'evidence_ids'}
    for claim in value['claims']:
        _require(isinstance(claim, dict) and set(claim) == claim_fields and _text(claim['claim_id'])
                 and claim['claim_id'] not in claims, 'claim_fields_or_id', '/input/claims')
        key = (claim['domain'], claim['attribute'], claim['nutrient_key'])
        _require(key in required and claim['side'] in ('recipe', 'source') and (key, claim['side']) not in seen,
                 'claim_scope_or_duplicate', '/input/claims')
        seen.add((key, claim['side']))
        refs = claim['evidence_ids']
        _require(isinstance(refs, list) and all(ref in evidences for ref in refs) and len(set(refs)) == len(refs), 'claim_evidence', '/input/claims')
        _require(claim['unit'] is None or _text(claim['unit']), 'claim_unit', '/input/claims')
        _require(claim['value_kind'] in ('text', 'text_list', 'decimal'), 'claim_kind', '/input/claims')
        if claim['value_state'] == 'known':
            v = claim['value']
            valid = (_text(v) and v.lower() != 'unknown' if claim['value_kind'] == 'text' else
                     isinstance(v, list) and bool(v) and all(_text(t) for t in v) if claim['value_kind'] == 'text_list' else
                     isinstance(v, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', v) is not None)
            _require(valid and refs, 'known_claim_requires_value_evidence', '/input/claims')
        else:
            _require(claim['value_state'] in ('missing', 'unknown') and claim['value'] is None
                     and (claim['value_state'] != 'unknown' or refs), 'missing_unknown_claim', '/input/claims')
        claims[claim['claim_id']] = claim
    manual = index_review_records(ledger['reviews'], results)
    for index, r in enumerate(results):
        path = f'/attribute_results/{index}'
        key = (r['domain'], r['attribute'], r['nutrient_key'])
        _require(key in manual and manual[key]['review_status'] == 'reviewed', 'review_not_checked', path, 'blocked')
        record = manual[key]
        _require(record['result'] == r, 'forged_review_result', path)
        _require(record['method'] == 'manual' and r['rule_id'] == 'manual_evidence_comparison_v1'
                 or record['method'] == 'controlled_rule' and r['status'] == 'not_applicable' and r['rule_id'] == NA_RULE,
                 'unsupported_review_rule', path, 'blocked')
        _require(r['rule_version'] == '1', 'unsupported_rule_version', path, 'blocked')
        _require(all(e in evidences for e in r['evidence_ids']), 'missing_evidence_ref', path)
        linked = []
        for side in ('recipe', 'source'):
            ref = r[side + '_claim_ref']
            if ref is not None:
                _require(ref in claims and (claims[ref]['domain'], claims[ref]['attribute'], claims[ref]['nutrient_key']) == key
                         and claims[ref]['side'] == side, 'cross_attribute_claim', path)
                _require(set(claims[ref]['evidence_ids']) <= set(r['evidence_ids']), 'result_evidence_coverage', path)
                linked.append(claims[ref])
        if r['status'] in ('verified', 'mismatch'):
            _require(linked and all(c['value_state'] == 'known' for c in linked), 'unresolved_claim_cannot_verify', path)
            origins = {evidences[e]['origin'] for e in r['evidence_ids']}
            _require(origins <= {'official_record', 'official_document', 'recipe_text'} and origins,
                     'caller_claim_only', path)
            if r['attribute'] == 'recipe_declared_mass':
                _require('recipe_text' in origins, 'recipe_evidence_required', path)
            else:
                _require(any(o in origins for o in ('official_record', 'official_document')), 'source_evidence_required', path)
            if r['domain'] == 'identity' or r['status'] == 'mismatch':
                _require(len(linked) == 2 and 'recipe_text' in origins, 'two_sided_evidence_required', path)
            if r['attribute'] == 'processing':
                _require(all(evidences[e]['statement_role'] != 'sample_preparation' for e in r['evidence_ids']),
                         'processing_role_unresolved', path)
            if r['attribute'] == 'data_version':
                _require(prov.get('official_dataset_version') is not None, 'official_version_unknown', path)
                if r['status'] == 'verified':
                    source_claim = claims.get(r['source_claim_ref'])
                    _require(source_claim is not None and source_claim['value'] == prov['official_dataset_version'],
                             'version_claim_mismatch', path)
            if r['attribute'] == 'recipe_declared_mass':
                _require(ingredient['quantity_state'] == 'exact' and ingredient['quantity'] is not None
                         and ingredient['unit'] in ('g', '克'), 'recipe_mass_missing_or_unconvertible', path)
                recipe_claim = claims.get(r['recipe_claim_ref'])
                _require(recipe_claim is not None and recipe_claim['value_kind'] == 'decimal'
                         and recipe_claim['unit'] == ingredient['unit']
                         and Decimal(recipe_claim['value']) == Decimal(str(ingredient['quantity'])),
                         'recipe_mass_claim_mismatch', path)
            if r['attribute'] == 'nutrient_units':
                unit = next(n['expected_unit'] for n in nutrients if n['original_name'] == r['nutrient_key'])
                observations = [o for o in food.observations if o.original_name == r['nutrient_key']]
                _require(len(observations) == 1 and observations[0].unit is not None,
                         'nutrient_unit_missing', path)
                if r['status'] == 'verified':
                    _require(observations[0].unit == unit, 'nutrient_unit_conflict', path)
                    source_claim = claims.get(r['source_claim_ref'])
                    _require(source_claim is not None and source_claim['value'] == unit,
                             'unit_claim_mismatch', path)
            if r['attribute'] == 'nutrient_definition' and r['status'] == 'verified':
                observations = [o for o in food.observations if o.original_name == r['nutrient_key']]
                _require(len(observations) == 1 and observations[0].value.status == 'reported',
                         'nutrient_value_unresolved', path)
        if r['status'] == 'not_applicable':
            _require(any(evidences[e]['origin'] == 'official_document' for e in r['evidence_ids']), 'na_evidence_required', path)
