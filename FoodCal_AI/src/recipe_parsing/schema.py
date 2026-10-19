"""Small Ollama-compatible structural subset; never replaces Contract or trust."""
from copy import deepcopy
import hashlib
import json
import math

from .types import ValidationError, ValidationReport


def candidate_json_schema():
    """Fresh, closed four-field candidate schema. No metadata or default values.

    Only type/properties/required/additionalProperties/items/enum are used.
    Span equality, IDs, role uniqueness and semantic rules remain in Validator.
    """
    def obj(properties):
        return {'type': 'object', 'properties': deepcopy(properties),
                'required': list(properties), 'additionalProperties': False}

    def array(item):
        return {'type': 'array', 'items': deepcopy(item)}

    def enum(*values):
        return {'enum': list(values)}

    text, boolean = {'type': 'string'}, {'type': 'boolean'}
    nullable_text = {'type': ['string', 'null']}
    anchor = obj({'start': {'type': 'integer'}, 'end': {'type': 'integer'}})
    def links(*roles):
        return array(obj({'evidence_id': text, 'supports': array(enum(*roles))}))

    ingredient = obj({
        'ingredient_id': text, 'anchor': anchor, 'ingredient_name': text,
        'quantity': {'type': ['number', 'null']}, 'quantity_raw': nullable_text,
        'quantity_state': enum('exact', 'vague', 'missing', 'unparsed'),
        'quantity_rule': enum('numeric_literal', 'half_literal', None),
        'unit': nullable_text, 'optional': boolean,
        'optional_state': enum('explicit_optional', 'explicit_required', 'not_marked', 'unresolved'),
        'uncertainty': boolean, 'evidence_links': links('name', 'quantity', 'unit', 'optionality')})
    step = obj({
        'step_id': text, 'anchor': anchor, 'action_raw': text,
        'normalized_action': enum('mix', 'slice', 'add', None),
        'normalization_rule': enum('v1.zh.mix', 'v1.zh.slice', 'v1.zh.add', None),
        'ingredient_ids': array(text), 'target_status': enum('resolved', 'partial', 'unresolved'),
        'uncertainty': boolean, 'evidence_links': links('action', 'targets', 'context')})
    evidence = obj({'evidence_id': text, 'start': {'type': 'integer'},
                    'end': {'type': 'integer'}, 'evidence_text': text})
    issue = obj({'code': text, 'severity': enum('info', 'warning', 'error'),
                 'path': text, 'message': text})
    return obj({'ingredients': array(ingredient), 'preparation_steps': array(step),
                'evidence': array(evidence), 'issues': array(issue)})


def schema_sha256(schema):
    """SHA-256 of UTF-8 canonical JSON (sorted keys, compact, no ASCII escaping)."""
    canonical = json.dumps(schema, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def validate_candidate_schema(value):
    """Non-mutating validation of precisely this schema's supported subset.

    This is not a general JSON Schema engine. No defaults or coercion. Independent
    Contract validation is required even when this report is valid.
    """
    errors = []
    def error(path, code, message):
        errors.append(ValidationError(path, code, message))

    def walk(item, schema, path):
        kind = schema.get('type')
        def matches(name):
            if name == 'number':
                return type(item) in (int, float) and (type(item) is int or math.isfinite(item))
            return {'object': type(item) is dict, 'array': type(item) is list,
                    'string': type(item) is str, 'integer': type(item) is int,
                    'boolean': type(item) is bool, 'null': item is None}.get(name, False)
        if kind is not None and not any(matches(name) for name in (kind if isinstance(kind, list) else [kind])):
            error(path, 'schema_type', f'Expected {kind}; received {type(item).__name__}')
            return
        if 'enum' in schema and not any(type(item) is type(option) and item == option for option in schema['enum']):
            error(path, 'schema_enum', 'Value is outside the declared enum')
        if kind == 'object':
            for field in schema['required']:
                if field not in item:
                    error(path + '/' + field, 'schema_required', 'Required field is absent')
            for field in item:
                token = str(field).replace('~', '~0').replace('/', '~1')
                if field not in schema['properties']:
                    error(path + '/' + token, 'schema_extra', 'Additional field is forbidden')
                else:
                    walk(item[field], schema['properties'][field], path + '/' + token)
        elif kind == 'array':
            for index, member in enumerate(item):
                walk(member, schema['items'], path + '/' + str(index))

    walk(value, candidate_json_schema(), '')
    return ValidationReport(tuple(errors))
