"""Diagnostic/test-only composition of Mock Mapping and exact TFDA lookup."""
from copy import deepcopy
import re
from typing import Literal, TypedDict

from src.recipe_parsing.types import Issue

from .mapping import MockNutritionMappingAdapter
from .mapping_types import IdentityAssertion, IngredientMapping, NutritionMappingResult
from .tfda_source import SourceLookup, TFDANutritionSourceAdapter


class CandidateRequest(TypedDict):
    food_id: str
    expected_archive_sha256: str
    expected_sample_sha256: str
    evidence_reference: str


class IngredientSourceLink(TypedDict):
    input_id: str
    ingredient_id: str
    recipe_text_sha256: str
    candidates: list[CandidateRequest]


class CandidateDiagnostic(TypedDict):
    request: CandidateRequest
    source_status: Literal['found', 'not_found', 'ambiguous', 'invalid_record', 'not_queried']
    source_reason: str
    link_check: Literal['verified', 'pending', 'invalid']
    source_lookup: SourceLookup | None


class IngredientDiagnostic(TypedDict):
    input_id: str
    ingredient_id: str
    recipe_text_sha256: str
    caller_assertion: IdentityAssertion | None
    mock_mapping: IngredientMapping
    candidates: list[CandidateDiagnostic]
    identity_status: Literal['pending', 'ambiguous']
    basis_status: Literal['unresolved']
    reasons: list[str]
    nutrition_eligible: Literal[False]


class CandidateLookupResult(TypedDict):
    prototype_version: Literal[1]
    status: Literal['diagnostic', 'partial', 'failed', 'unavailable']
    usage: Literal['diagnostic/test_only']
    nutrition_eligible: Literal[False]
    mock_mapping_snapshot: NutritionMappingResult
    ingredients: list[IngredientDiagnostic]
    issues: list[Issue]


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _valid_request(value):
    return (isinstance(value, dict) and set(value) == set(CandidateRequest.__annotations__)
            and _text(value['food_id']) and value['food_id'] == value['food_id'].strip()
            and _hash(value['expected_archive_sha256']) and _hash(value['expected_sample_sha256'])
            and _text(value['evidence_reference']))


def _query(request, adapter):
    result = {'request': deepcopy(request), 'source_status': 'not_queried',
              'source_reason': 'source_lookup_failed', 'link_check': 'invalid', 'source_lookup': None}
    try:
        lookup = adapter.lookup(request['food_id'])
    except (OSError, ValueError):
        return result
    if not isinstance(lookup, SourceLookup):
        result['source_reason'] = 'invalid_source_response'
        return result
    result.update(source_status=lookup.status, source_reason=lookup.reason)
    if lookup.status in ('not_found', 'invalid_record'):
        result.update(source_lookup=lookup, link_check='pending' if lookup.status == 'not_found' else 'invalid')
        return result
    if (not lookup.records or (lookup.status == 'found' and len(lookup.records) != 1)
            or any(record.food_id != request['food_id'] or not record.observations for record in lookup.records)):
        result['source_reason'] = 'invalid_source_response'
        return result
    observations = [item for record in lookup.records for item in record.observations]
    if any(item.evidence.archive_sha256 != request['expected_archive_sha256'] for item in observations):
        result['source_reason'] = 'source_snapshot_hash_mismatch'
        return result
    if any(item.evidence.sample_sha256 != request['expected_sample_sha256'] for item in observations):
        result['source_reason'] = 'source_record_hash_mismatch'
        return result
    result.update(source_lookup=lookup, link_check='verified')
    return result


def review_ingredient_sources(
    parsing_result, *, expected_source, mock_mapper: MockNutritionMappingAdapter,
    source_adapter: TFDANutritionSourceAdapter, identity_assertions=None, source_links=None,
) -> CandidateLookupResult:
    """Caller context must be independently obtained; this does not authenticate it.

    Input must already have crossed the model Trust Boundary where applicable.
    No semantic approval, numeric conversion, name lookup, or mass computation added.
    """
    mapping = mock_mapper.map(parsing_result, expected_source=expected_source,
                              identity_assertions=identity_assertions)
    result = {'prototype_version': 1, 'status': 'partial', 'usage': 'diagnostic/test_only',
              'nutrition_eligible': False, 'mock_mapping_snapshot': mapping,
              'ingredients': [], 'issues': []}
    if mapping['status'] in ('failed', 'unavailable'):
        result['status'] = mapping['status']
        result['issues'] = deepcopy(mapping['issues'])
        return result
    snapshot = mapping['parsing_snapshot']
    links = {} if source_links is None else source_links
    ids = {item['ingredient_id'] for item in mapping['ingredients']}
    if not isinstance(links, dict) or any(key not in ids for key in links):
        result['status'] = 'failed'
        result['issues'] = [{'code': 'invalid_source_link_scope', 'path': '/source_links',
                             'severity': 'error', 'message': 'Links must use ingredient IDs in this Parsing snapshot'}]
        return result
    for index, item in enumerate(mapping['ingredients']):
        identifier = item['ingredient_id']
        diagnostic = {'input_id': snapshot['input_id'], 'ingredient_id': identifier,
                      'recipe_text_sha256': snapshot['source']['text_sha256'],
                      'caller_assertion': deepcopy(item['identity_assertion']), 'mock_mapping': deepcopy(item),
                      'candidates': [], 'identity_status': 'pending', 'basis_status': 'unresolved',
                      'nutrition_eligible': False, 'reasons': ['diagnostic_only', 'identity_not_approved',
                                                              'unresolved_basis']}
        result['ingredients'].append(diagnostic)
        if item['identity_assertion'] is None:
            diagnostic['reasons'].append('missing_assertion' if not item['issues'] else 'invalid_identity_assertion')
        elif any(value is None for value in item['identity_assertion']['attributes'].values()):
            diagnostic['reasons'].append('identity_unresolved')
        if item['mapping']['status'] == 'error':
            continue
        link = links.get(identifier)
        reason = None
        if link is None:
            diagnostic['reasons'].append('missing_source_link')
            continue
        if (not isinstance(link, dict) or set(link) != set(IngredientSourceLink.__annotations__)
                or not isinstance(link['candidates'], list) or not link['candidates']
                or not all(_valid_request(request) for request in link['candidates'])):
            reason = 'invalid_source_link'
        elif (link['ingredient_id'] != identifier or link['input_id'] != snapshot['input_id']
              or link['recipe_text_sha256'] != snapshot['source']['text_sha256']):
            reason = 'parsing_scope_hash_mismatch'
        if reason:
            diagnostic['reasons'].append(reason)
            result['issues'].append({'code': reason, 'path': f'/ingredients/{index}/source_link',
                                     'severity': 'error', 'message': 'Caller link shape or Parsing scope is invalid'})
            continue
        seen = set()
        for request in link['candidates']:
            key = tuple(request[name] for name in ('food_id', 'expected_archive_sha256', 'expected_sample_sha256'))
            if key in seen:
                continue
            seen.add(key)
            candidate = _query(request, source_adapter)
            diagnostic['candidates'].append(candidate)
            if candidate['source_status'] != 'found' or candidate['link_check'] != 'verified':
                diagnostic['reasons'].append(candidate['source_reason'])
        if len(diagnostic['candidates']) > 1 or any(c['source_status'] == 'ambiguous' for c in diagnostic['candidates']):
            diagnostic['identity_status'] = 'ambiguous'
            diagnostic['reasons'].append('multiple_source_candidates')
    if result['ingredients'] and all(item['candidates'] and all(
            candidate['source_status'] == 'found' and candidate['link_check'] == 'verified'
            for candidate in item['candidates']) for item in result['ingredients']):
        result['status'] = 'diagnostic'  # Successful query flow, never semantic approval.
    return result
