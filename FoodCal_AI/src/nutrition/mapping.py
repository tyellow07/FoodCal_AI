"""Explicit synthetic fixture mapping. No inference, nutrition or calorie estimates."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from src.recipe_parsing import load_recipe_parsing, validate_recipe_parsing
from .mapping_types import NutritionMappingResult


DEFAULT_FIXTURE_PATH = Path(__file__).resolve().parents[2] / 'tests/fixtures/nutrition_mapping.json'
IDENTITY_FIELDS = ('food', 'part', 'state', 'quality', 'weight_basis')


class MappingFixtureError(ValueError):
    """Unreadable or unsafe mock catalog; never fall back to the legacy database."""


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _unique_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key')
        value[key] = item
    return value


def _reject_constant(value):
    raise ValueError('Non-finite JSON number')


def _issue(code, path, message):
    return {'code': code, 'severity': 'error', 'path': path, 'message': message}


def _qualification(*reasons):
    return {'status': 'test_only', 'eligible_for_production': False,
            'reasons': ['mock_adapter', 'synthetic_nutrition_catalog', 'nutrients_unavailable', *reasons]}


def _assertion_error(value):
    if not isinstance(value, dict) or set(value) != {'attributes', 'evidence_reference', 'source_text_sha256'}:
        return 'Expected attributes, evidence_reference and source_text_sha256'
    attributes = value['attributes']
    if not isinstance(attributes, dict) or set(attributes) != set(IDENTITY_FIELDS):
        return 'Identity must explicitly contain food, part, state, quality and weight_basis'
    if any(v is not None and not _text(v) for v in attributes.values()):
        return 'Identity values must be nonempty strings or null'
    if not _text(value['evidence_reference']):
        return 'Identity assertions require an evidence reference; it is not authenticated'
    digest = value['source_text_sha256']
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        return 'Identity assertions require the exact source text SHA-256'
    return None


class MockNutritionMappingAdapter:
    """Accept a whole validated Parsing v1 snapshot, preserving entity scope.

    expected_source is independently obtained by the caller, not copied from an
    untrusted model answer. Identity assertions are caller-supplied test context;
    they do not approve a source, prove photo content or upgrade mock eligibility.
    Names only discover candidates; all five identity attributes must be resolved
    before a unique candidate is selected. Only literal g/克 need no conversion.
    """
    is_mock = True

    def __init__(self, fixture_path=DEFAULT_FIXTURE_PATH):
        try:
            raw = Path(fixture_path).read_bytes()
            catalog = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_keys,
                                 parse_constant=_reject_constant)
            if (not isinstance(catalog, dict) or set(catalog) != {'kind', 'review_status', 'entries'} or
                    catalog['kind'] != 'synthetic' or catalog['review_status'] != 'test_only' or
                    not isinstance(catalog['entries'], list) or not catalog['entries']):
                raise ValueError('Expected nonempty synthetic/test_only catalog')
            ids = set()
            for entry in catalog['entries']:
                if not isinstance(entry, dict) or set(entry) != {
                        'entry_id', 'ingredient_name', 'identity', 'source', 'nutrient_basis', 'nutrients'}:
                    raise ValueError('Invalid entry fields')
                if not _text(entry['entry_id']) or entry['entry_id'] in ids or not _text(entry['ingredient_name']):
                    raise ValueError('Invalid or duplicate entry identity')
                ids.add(entry['entry_id'])
                identity = entry['identity']
                if (not isinstance(identity, dict) or set(identity) != set(IDENTITY_FIELDS) or
                        not all(_text(v) for v in identity.values()) or
                        identity['weight_basis'] != 'edible_portion'):
                    raise ValueError('Synthetic entries require explicit identity and edible_portion basis')
                source = entry['source']
                if (not isinstance(source, dict) or set(source) != {'source_id', 'reference', 'kind', 'review_status'} or
                        not _text(source['source_id']) or not _text(source['reference']) or
                        source['kind'] != 'synthetic' or source['review_status'] != 'test_only'):
                    raise ValueError('Every entry must declare synthetic/test_only provenance')
                if entry['nutrient_basis'] != 'per_100g_edible_portion' or entry['nutrients'] is not None:
                    raise ValueError('This mock carries no nutrient numbers')
            self._entries = catalog['entries']
            self.fixture_sha256 = sha256(raw).hexdigest()
        except (OSError, UnicodeError, ValueError) as error:
            raise MappingFixtureError(f'Cannot load synthetic nutrition mapping fixture: {error}') from error

    def map(self, parsing_result: object, *, expected_source: object,
            identity_assertions: dict | None = None) -> NutritionMappingResult:
        """Return safe diagnostics on invalid inputs; never return rejected entities."""
        result = {'contract': 'foodcal.ingredient_nutrition_mapping', 'schema_version': 1,
                  'status': 'failed', 'adapter_kind': 'mock', 'fixture_sha256': self.fixture_sha256,
                  'parsing_snapshot': None, 'source_check': 'not_checked',
                  'qualification': _qualification(), 'ingredients': [], 'issues': []}
        report = validate_recipe_parsing(parsing_result)
        if not report.valid:
            result['issues'] = [_issue('invalid_parsing_contract', '/parsing_snapshot' + e.path,
                                       f'{e.code}: {e.message}') for e in report.errors]
            return result
        snapshot = load_recipe_parsing(parsing_result)
        if snapshot['source'] != expected_source:
            result['issues'] = [_issue('source_snapshot_mismatch', '/parsing_snapshot/source',
                                       'Source differs from independently supplied caller snapshot')]
            return result
        assertions = {} if identity_assertions is None else identity_assertions
        ids = {item['ingredient_id'] for item in snapshot['ingredients']}
        if not isinstance(assertions, dict) or any(key not in ids for key in assertions):
            result['issues'] = [_issue('invalid_identity_scope', '/identity_assertions',
                                       'Assertions must be keyed by ingredient IDs in this snapshot')]
            return result
        result['parsing_snapshot'] = snapshot
        result['source_check'] = 'matches_caller_snapshot'
        source = snapshot['source']
        source_reasons = [] if source and source['kind'] == 'production' and source['review_status'] == 'approved' else ['recipe_source_not_production_approved']
        if snapshot['review_status'] != 'approved':
            source_reasons.append('parsing_not_human_approved')
        result['qualification'] = _qualification(*source_reasons)
        if snapshot['status'] in ('unavailable', 'failed'):
            result['status'] = snapshot['status']
            result['issues'] = deepcopy(snapshot['issues'])
            return result
        for index, ingredient in enumerate(snapshot['ingredients']):
            assertion = assertions.get(ingredient['ingredient_id'])
            assertion_error = _assertion_error(assertion) if assertion is not None else None
            if assertion is not None and not assertion_error and assertion['source_text_sha256'] != source['text_sha256']:
                assertion_error = 'Identity assertion belongs to a different source text snapshot'
            mapping = self._map_entry(ingredient['ingredient_name'], assertion, assertion_error)
            mass = self._mass(ingredient)
            reasons = list(source_reasons)
            if mapping['status'] != 'matched':
                reasons.append('mapping_unresolved')
            if mass['status'] != 'available':
                reasons.append('mass_unavailable')
            errors = []
            if mapping['status'] == 'error':
                errors.append(_issue('invalid_identity_assertion', f'/ingredients/{index}/identity_assertion',
                                     assertion_error))
            result['ingredients'].append({
                'ingredient_id': ingredient['ingredient_id'], 'ingredient': deepcopy(ingredient),
                'identity_assertion': deepcopy(assertion) if not errors else None,
                'preparation_step_ids': [step['step_id'] for step in snapshot['preparation_steps']
                                         if ingredient['ingredient_id'] in step['ingredient_ids']],
                'mapping': mapping, 'mass': mass, 'qualification': _qualification(*reasons),
                'issues': errors,
            })
        items = result['ingredients']
        if items and all(item['mapping']['status'] == 'error' for item in items):
            result['status'] = 'failed'
        elif items and all(item['mapping']['status'] == 'matched' and item['mass']['status'] == 'available'
                           for item in items):
            result['status'] = 'complete'
        else:
            result['status'] = 'partial'
        return result

    def _map_entry(self, name, assertion, assertion_error):
        candidates = [deepcopy(entry) for entry in self._entries if entry['ingredient_name'] == name]
        mapping = {'status': 'unavailable', 'reason': 'no_exact_name_candidate',
                   'selected_entry_id': None, 'candidates': candidates}
        if assertion_error:
            mapping.update(status='error', reason='invalid_identity_assertion')
            return mapping
        if not candidates:
            return mapping
        if assertion is None:
            mapping.update(status='ambiguous', reason='identity_unresolved')
            return mapping
        attributes = assertion['attributes']
        compatible = [entry for entry in candidates if all(
            value is None or value == entry['identity'][key] for key, value in attributes.items())]
        mapping['candidates'] = compatible
        if not compatible:
            mapping['reason'] = 'identity_conflict'
        elif any(value is None for value in attributes.values()):
            mapping.update(status='ambiguous', reason='identity_unresolved')
        elif len(compatible) != 1:
            mapping.update(status='ambiguous', reason='multiple_compatible_entries')
        else:
            mapping.update(status='matched', reason='unique_explicit_identity',
                           selected_entry_id=compatible[0]['entry_id'])
        return mapping

    @staticmethod
    def _mass(ingredient):
        mass = {'status': 'unavailable', 'reason': 'conversion_basis_unavailable', 'mass_g': None,
                'basis': 'recipe_declared_ingredient', 'rule': None,
                'evidence_links': deepcopy(ingredient['evidence_links'])}
        state = ingredient['quantity_state']
        if state != 'exact':
            mass['reason'] = 'quantity_' + state
        elif ingredient['unit'] is None:
            mass['reason'] = 'unit_missing'
        elif ingredient['unit'] in ('g', '克'):
            mass.update(status='available', reason='explicit_grams', mass_g=ingredient['quantity'], rule='literal_g')
        return mass
