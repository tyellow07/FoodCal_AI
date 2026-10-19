"""Offline Prototype Profile v0: verify records and evidence, never infer semantics."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import io
import json
import math
import re

from experiments.tfda_nutrition_sample.inspect_sample import iter_rows, unique_keys, reject_constant
from src.recipe_parsing import validate_recipe_parsing
from .tfda_source import FoodRecord, parse_value
from .review_types import (AttributeResult, ReviewInput, ReviewIssue, ReviewValidation,
                           ReviewContext, ReviewTrustAnchor)


IDENTITY_ATTRIBUTES = ('food', 'part', 'state', 'processing', 'quality_composition')
BASIS_ATTRIBUTES = ('reference_measure', 'edible_portion', 'weight_basis', 'recipe_declared_mass',
                    'photo_consumed_mass', 'data_version', 'nutrient_units', 'nutrient_definition')
NA_RULE = 'diagnostic_recipe_reference_excludes_photo_v1'


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _digest(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


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


class _Reject(Exception):
    def __init__(self, code, path, state='invalid'):
        self.code, self.path, self.state = code, path, state


def _require(condition, code, path, state='invalid'):
    if not condition:
        raise _Reject(code, path, state)


def _json(raw):
    return json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys, parse_constant=reject_constant)


def _pointer(value, pointer):
    """RFC 6901 including root pointer and escaped ~ /; no URI fragments."""
    _require(isinstance(pointer, str) and (pointer == '' or pointer.startswith('/')), 'invalid_json_pointer', '/evidence/locator')
    for token in pointer.split('/')[1:] if pointer else ():
        _require(re.search(r'~(?:[^01]|$)', token) is None, 'invalid_json_pointer', '/evidence/locator')
        token = token.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list):
            _require(re.fullmatch(r'0|[1-9][0-9]*', token) is not None and int(token) < len(value),
                     'unresolved_json_pointer', '/evidence/locator')
            value = value[int(token)]
        else:
            _require(isinstance(value, dict) and token in value, 'unresolved_json_pointer', '/evidence/locator')
            value = value[token]
    return value


def _same(a, b):
    # bool vs numeric zero/one is not equality for evidence.
    return type(a) is type(b) and a == b


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
    _require(isinstance(context, ReviewContext) and isinstance(anchor, ReviewTrustAnchor)
             and _text(anchor.authority_ref) and _digest(anchor.context_sha256),
             'review_context_untrusted', '/context', 'blocked')
    _require(isinstance(context.record_bytes, bytes)
             and sha256(context.record_bytes).hexdigest() == anchor.context_sha256,
             'context_hash_mismatch', '/context')
    ledger = _json(context.record_bytes)
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
    resolved, catalog = {}, ledger['attachment_catalog']
    entries = [refs[name] for name in ('parsing_snapshot', 'expected_recipe_source', 'candidate_lookup', 'source_provenance')] + refs['evidence_files']
    for ref in entries:
        _require(isinstance(ref, dict) and set(ref) == {'attachment_id', 'representation', 'sha256'},
                 'attachment_ref', '/input/attachment_refs')
        key = ref['attachment_id']
        _require(_text(key) and key in context.attachments and key in catalog, 'missing_attachment', '/attachments')
        meta, raw = catalog[key], context.attachments[key]
        _require(set(meta) == {'origin', 'representation', 'sha256', 'scope'}, 'attachment_catalog', '/context/attachment_catalog')
        _require(meta['scope'] == {'recipe_scope': scope, 'candidate_scope': source}, 'attachment_scope', '/attachments/' + key)
        _require(ref['representation'] == meta['representation'] and ref['sha256'] == meta['sha256'],
                 'attachment_manifest_mismatch', '/attachments/' + key)
        if ref['representation'] == 'bytes':
            _require(isinstance(raw, bytes) and _digest(ref['sha256']) and sha256(raw).hexdigest() == ref['sha256'],
                     'evidence_hash_mismatch', '/attachments/' + key)
        else:
            _require(ref['representation'] == 'native' and ref['sha256'] is None, 'attachment_representation', '/attachments/' + key)
        resolved[key] = raw
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
    _require(lookup.status == 'found' and len(lookup.records) == 1 and isinstance(lookup.records[0], FoodRecord),
             'invalid_source_lookup', '/attachments/candidate_lookup')
    food = lookup.records[0]
    _require(food.production_eligible is False and food.identity_approval == 'unresolved',
             'untrusted_source_promotion', '/attachments/candidate_lookup')
    _require(all(getattr(food, name) == 'unresolved' for name in
                 ('edible_portion_basis', 'food_state_applicability', 'weight_basis_applicability')),
             'untrusted_source_promotion', '/attachments/candidate_lookup')
    _require((food.food_id, food.original_name, food.data_type) == (source['food_id'], source['original_name'], source['data_type']),
             'food_identity_scope', '/input/candidate_scope')
    provenance = slot('source_provenance')
    _require(isinstance(provenance, bytes), 'provenance_requires_bytes', '/attachments/source_provenance')
    prov = _json(provenance)
    _require(prov['archive_sha256'] == source['archive_sha256'] and prov['member_sha256'] == source['member_sha256'],
             'source_hash_mismatch', '/attachments/source_provenance')
    _require(_text(prov.get('source_url')) and _text(prov.get('retrieved_at_utc')), 'missing_source_metadata', '/attachments/source_provenance')
    by_index, sample_cache, used_rows = {}, {}, {}
    for obs in food.observations:
        proof = obs.evidence
        _require(proof.source_url == prov['source_url'] and proof.retrieved_at_utc == prov['retrieved_at_utc']
                 and proof.value_field == '每100克含量', 'source_provenance_mismatch', '/source/observations')
        _require(type(proof.source_row_index) is int and proof.source_row_index >= 0 and proof.source_row_index not in by_index,
                 'source_row_index', '/source/observations')
        _require((proof.archive_sha256, proof.member_sha256, proof.sample_sha256) ==
                 (source['archive_sha256'], source['member_sha256'], source['sample_sha256']), 'source_hash_mismatch', '/source/observations')
        samples = [raw for key, raw in resolved.items() if isinstance(raw, bytes) and sha256(raw).hexdigest() == proof.sample_sha256]
        _require(samples, 'missing_source_record', '/source/observations')
        if proof.sample_sha256 not in sample_cache:
            sample_cache[proof.sample_sha256] = list(iter_rows(io.StringIO(samples[0].decode('utf-8'))))
        rows = sample_cache[proof.sample_sha256]
        _require(type(proof.sample_row_index) is int and 0 <= proof.sample_row_index < len(rows), 'source_row_index', '/source/observations')
        row, text = rows[proof.sample_row_index][1:]
        _require(sha256(text.encode('utf-8')).hexdigest() == proof.source_object_sha256
                 and tuple(row.items()) == obs.raw_fields
                 and (row['整合編號'], row['樣品名稱'], row['資料類別']) == (food.food_id, food.original_name, food.data_type),
                 'source_object_mismatch', '/source/observations')
        _require(obs.original_name == row['分析項'] and _same(obs.unit, row['含量單位'])
                 and obs.value == parse_value(row['每100克含量']), 'source_value_mismatch', '/source/observations')
        by_index[proof.source_row_index] = (row, proof)
        used_rows.setdefault(proof.sample_sha256, set()).add(proof.sample_row_index)
    _require(all(indices == set(range(len(sample_cache[digest]))) for digest, indices in used_rows.items()),
             'incomplete_source_observations', '/source/observations')
    evidences = {}
    evidence_fields = {'evidence_id', 'origin', 'attachment_ref', 'attachment_sha256', 'locator', 'raw_value', 'evidence_text', 'statement_role'}
    for index, ev in enumerate(value['evidence']):
        path = f'/input/evidence/{index}'
        _require(isinstance(ev, dict) and set(ev) == evidence_fields and _text(ev['evidence_id'])
                 and ev['evidence_id'] not in evidences, 'evidence_fields_or_id', path)
        key = ev['attachment_ref']
        _require(key in resolved and catalog[key]['representation'] == 'bytes' and ev['origin'] == catalog[key]['origin'],
                 'evidence_origin_or_attachment', path)
        raw = resolved[key]
        _require(ev['attachment_sha256'] == sha256(raw).hexdigest(), 'evidence_hash_mismatch', path)
        if raw.startswith(b'%PDF'):
            raise _Reject('unsupported_evidence_format', path, 'blocked')
        loc = ev['locator']
        scalar = ev['raw_value']
        _require(scalar is None or type(scalar) in (str, int, bool) or type(scalar) is float and math.isfinite(scalar),
                 'evidence_scalar', path)
        _require(ev['evidence_text'] is None or isinstance(ev['evidence_text'], str), 'evidence_text_type', path)
        if ev['origin'] == 'official_record':
            _require(set(loc) == {'sample_filename', 'source_row_index', 'source_object_sha256', 'field_name'}, 'record_locator', path)
            _require(type(loc['source_row_index']) is int and loc['source_row_index'] in by_index, 'cross_record_evidence', path)
            row, proof = by_index[loc['source_row_index']]
            _require(loc['sample_filename'] == proof.sample_filename and loc['source_object_sha256'] == proof.source_object_sha256
                     and ev['attachment_sha256'] == proof.sample_sha256 and loc['field_name'] in row, 'record_locator_mismatch', path)
            extracted = row[loc['field_name']]
        elif ev['origin'] == 'recipe_text':
            _require(set(loc) == {'start', 'end'} and type(loc['start']) is int and type(loc['end']) is int
                     and 0 <= loc['start'] < loc['end'] <= len(expected['text']), 'recipe_span', path)
            relevant = [e for e in parsing['evidence'] if e['evidence_id'] in {link['evidence_id'] for link in ingredient['evidence_links']}]
            _require(any(e['start'] <= loc['start'] and loc['end'] <= e['end'] for e in relevant), 'cross_ingredient_evidence', path)
            _require(raw == expected['text'].encode('utf-8'), 'recipe_evidence_hash', path)
            extracted = expected['text'][loc['start']:loc['end']]
        elif ev['origin'] == 'official_document':
            _require(set(loc) == {'page', 'section'} and type(loc['page']) is int and loc['page'] > 0, 'document_locator', path)
            document = _json(raw)
            _require(type(document['page']) is int and document['page'] == loc['page'] and document['archive_sha256'] == source['archive_sha256'],
                     'document_applicability', path)
            extracted = _pointer(document, loc['section'])
        elif ev['origin'] in ('caller_assertion', 'unverified_claim'):
            _require(set(loc) == {'pointer'}, 'assertion_locator', path)
            extracted = _pointer(_json(raw), loc['pointer'])
        else:
            raise _Reject('unsupported_evidence_origin', path)
        _require(_same(ev['raw_value'], extracted), 'evidence_value_mismatch', path)
        if ev['evidence_text'] is not None:
            _require(isinstance(extracted, str) and ev['evidence_text'] == extracted, 'evidence_text_mismatch', path)
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
    reviews = ledger['reviews']
    _require(isinstance(reviews, list) and len(reviews) == len(results), 'review_record_count', '/context/reviews')
    manual = {}
    for record in reviews:
        _require(set(record) == {'result', 'review_status', 'method', 'process_ref'} and _text(record['process_ref']),
                 'review_record_fields', '/context/reviews')
        r = record['result']
        key = (r['domain'], r['attribute'], r['nutrient_key'])
        _require(key not in manual, 'duplicate_review_record', '/context/reviews')
        manual[key] = record
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
