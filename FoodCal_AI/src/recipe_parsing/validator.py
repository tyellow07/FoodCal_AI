"""Pure, non-mutating structural and literal-evidence checks for Contract v1."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import math
import re
from typing import Callable, cast
from urllib.parse import parse_qsl, urlsplit

from .types import ContractValidationError, RecipeParsingResult, ValidationError, ValidationReport


class UnsupportedQuantityFormat(ValueError):
    """Outside the prototype whitelist; does not mean the source is incorrect."""


def decimal_literal(raw: str) -> Decimal:
    """Conservative prototype policy; the contract does not specify this grammar."""
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', raw):
        raise UnsupportedQuantityFormat('Quantity format is outside the prototype ASCII integer/decimal whitelist')
    return Decimal(raw)


_HASH = re.compile(r'[0-9a-f]{64}')
_ACTION = {'混合': ('mix', 'v1.zh.mix'), '混合而成': ('mix', 'v1.zh.mix'),
           '切片': ('slice', 'v1.zh.slice'), '加入': ('add', 'v1.zh.add')}

# Compact schema descriptors. Every declared object is closed, except settings.
_ANCHOR = {'start': 'int', 'end': 'int'}
_LINK = {'evidence_id': 'text', 'supports': ('list', 'text')}
_SOURCE = {'source_id': 'text', 'reference': 'text',
           'kind': ('enum', 'production', 'test_fixture', 'unverified'),
           'review_status': ('enum', 'approved', 'test_only', 'unreviewed'),
           'text': 'text', 'text_sha256': 'hash'}
_BINDING = {'source_id': 'text', 'recipe_id': 'text', 'catalog_sha256': 'hash'}
_PROVENANCE = {'parser_id': 'text',
               'parser_kind': ('enum', 'model', 'manual', 'legacy_import', 'design_example'),
               'model': ('nullable', {'identifier': 'text', 'digest': ('nullable', 'text')}),
               'prompt_sha256': ('nullable', 'hash'), 'raw_output_sha256': ('nullable', 'hash'),
               'settings': 'json_object'}
_INGREDIENT = {'ingredient_id': 'text', 'anchor': _ANCHOR, 'ingredient_name': 'text',
               'quantity': ('nullable', 'number'), 'quantity_raw': ('nullable', 'text'),
               'quantity_state': ('enum', 'exact', 'vague', 'missing', 'unparsed'),
               'quantity_rule': ('nullable', ('enum', 'numeric_literal', 'half_literal')),
               'unit': ('nullable', 'text'), 'optional': 'bool',
               'optional_state': ('enum', 'explicit_optional', 'explicit_required', 'not_marked', 'unresolved'),
               'uncertainty': 'bool', 'evidence_links': ('list', _LINK)}
_STEP = {'step_id': 'text', 'anchor': _ANCHOR, 'action_raw': 'text',
         'normalized_action': ('nullable', ('enum', 'mix', 'slice', 'add')),
         'normalization_rule': ('nullable', ('enum', 'v1.zh.mix', 'v1.zh.slice', 'v1.zh.add')),
         'ingredient_ids': ('list', 'text'), 'target_status': ('enum', 'resolved', 'partial', 'unresolved'),
         'uncertainty': 'bool', 'evidence_links': ('list', _LINK)}
_EVIDENCE = {'evidence_id': 'text', 'start': 'int', 'end': 'int', 'evidence_text': 'text'}
_ISSUE = {'code': 'text', 'severity': ('enum', 'info', 'warning', 'error'),
          'path': 'pointer', 'message': 'text'}
_RESULT = {'contract': ('enum', 'foodcal.recipe_parsing'), 'schema_version': 'version',
           'input_id': 'text', 'status': ('enum', 'parsed', 'partial', 'unavailable', 'failed'),
           'review_status': ('enum', 'unreviewed', 'needs_review', 'approved'),
           'source': ('nullable', _SOURCE), 'retrieval_binding': ('nullable', _BINDING),
           'provenance': _PROVENANCE, 'ingredients': ('list', _INGREDIENT),
           'preparation_steps': ('list', _STEP), 'evidence': ('list', _EVIDENCE),
           'issues': ('list', _ISSUE)}


def _pointer_part(value):
    return str(value).replace('~', '~0').replace('/', '~1')


def _pointer_exists(value, pointer):
    if pointer == '':
        return True
    for token in pointer[1:].split('/'):
        token = token.replace('~1', '/').replace('~0', '~')
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and re.fullmatch(r'0|[1-9][0-9]*', token):
            # Compare before int conversion, including arbitrarily long tokens.
            if len(token) > len(str(len(value))) or int(token) >= len(value):
                return False
            value = value[int(token)]
        else:
            return False
    return True


def _json_value(value, seen=None):
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, (list, dict)):
        seen = set() if seen is None else seen
        if id(value) in seen:
            return False
        seen.add(id(value))
        try:
            if isinstance(value, list):
                return all(_json_value(item, seen) for item in value)
            return all(isinstance(key, str) and _json_value(item, seen) for key, item in value.items())
        finally:
            seen.remove(id(value))
    return False


class _Validator:
    def __init__(self, numeric_parser):
        self.errors = []
        self.numeric_parser = numeric_parser

    def check(self, condition, path, code, message):
        if not condition:
            self.errors.append(ValidationError(path, code, message))

    def schema(self, value, schema, path=''):
        if isinstance(schema, dict):
            if not isinstance(value, dict):
                self.check(False, path, 'type', 'Expected object')
                return
            for key in schema:
                child = path + '/' + key
                if key not in value:
                    self.check(False, child, 'missing_field', 'Required field is missing')
                else:
                    self.schema(value[key], schema[key], child)
            for key in sorted(value.keys() - schema.keys(), key=str):
                self.check(False, path + '/' + _pointer_part(key), 'extra_field', 'Unknown field')
            return
        if isinstance(schema, tuple):
            if schema[0] == 'nullable':
                if value is not None:
                    self.schema(value, schema[1], path)
            elif schema[0] == 'list':
                if not isinstance(value, list):
                    self.check(False, path, 'type', 'Expected array')
                else:
                    for i, item in enumerate(value):
                        self.schema(item, schema[1], f'{path}/{i}')
            else:
                self.check(isinstance(value, str) and value in schema[1:], path, 'enum',
                           f'Expected one of {schema[1:]}')
            return
        valid = False
        if schema == 'text':
            valid = isinstance(value, str) and bool(value.strip())
        elif schema == 'int':
            valid = type(value) is int
        elif schema == 'version':
            valid = type(value) is int and value == 1
        elif schema == 'bool':
            valid = type(value) is bool
        elif schema == 'number':
            valid = type(value) is int and value >= 0 or (
                type(value) is float and math.isfinite(value) and value >= 0)
        elif schema == 'hash':
            valid = isinstance(value, str) and bool(_HASH.fullmatch(value))
        elif schema == 'pointer':
            valid = isinstance(value, str) and (value == '' or value.startswith('/')) and not re.search(r'~(?![01])', value)
        elif schema == 'json_object':
            valid = isinstance(value, dict) and _json_value(value)
        self.check(valid, path, 'type', f'Expected {schema}')

    def issue(self, value, path, severity=None):
        # A parent/root issue can intentionally describe several child fields.
        return any((issue['path'] == '' or path == issue['path'] or
                    path.startswith(issue['path'] + '/') or issue['path'].startswith(path + '/')) and
                   (severity is None or issue['severity'] == severity)
                   for issue in value['issues'])

    def unique(self, items, key, path):
        seen = set()
        for i, item in enumerate(items):
            identity = item[key]
            self.check(identity not in seen, f'{path}/{i}/{key}', 'duplicate_id', 'ID must be unique')
            seen.add(identity)

    def span(self, span, text, path):
        valid = 0 <= span['start'] < span['end'] <= len(text)
        self.check(valid, path, 'span', 'Expected Unicode code point span within source text')
        return valid

    def links(self, entity, path, evidence, roles):
        links = entity['evidence_links']
        self.check(bool(links), path + '/evidence_links', 'evidence', 'Evidence links must not be empty')
        result = {role: [] for role in roles}
        for i, link in enumerate(links):
            p = f'{path}/evidence_links/{i}'
            self.check(link['evidence_id'] in evidence, p + '/evidence_id', 'reference', 'Unknown evidence ID')
            supports = link['supports']
            self.check(bool(supports) and len(supports) == len(set(supports)), p + '/supports',
                       'roles', 'Roles must be nonempty and unique')
            for role in supports:
                self.check(role in roles, p + '/supports', 'roles', 'Unsupported role for entity')
                if role in roles and link['evidence_id'] in evidence:
                    result[role].append(evidence[link['evidence_id']])
        return result

    def covered(self, anchor, items):
        return any(e['start'] <= anchor['start'] < anchor['end'] <= e['end'] for e in items)

    def literal(self, text, items):
        return any(text in e['evidence_text'] for e in items)

    def semantics(self, value):
        for i, issue in enumerate(value['issues']):
            self.check(_pointer_exists(value, issue['path']), f'/issues/{i}/path',
                       'issue_path', 'Issue JSON Pointer must identify an existing result value')
        source = value['source']
        status = value['status']
        severities = {i['severity'] for i in value['issues']}
        if status in ('parsed', 'partial'):
            self.check(source is not None, '/source', 'status', 'Parsed/partial requires source')
        if status == 'parsed':
            self.check(bool(value['ingredients']) and not severities & {'warning', 'error'},
                       '/status', 'status', 'Parsed requires ingredients and no warning/error')
        elif status == 'partial':
            self.check(bool(value['ingredients'] or value['preparation_steps']) and
                       'warning' in severities and 'error' not in severities,
                       '/status', 'status', 'Partial requires retained entities, warning, and no error')
            self.check(value['review_status'] == 'needs_review', '/review_status', 'status',
                       'Partial requires needs_review')
        else:
            for key in ('ingredients', 'preparation_steps', 'evidence'):
                self.check(not value[key], '/' + key, 'status', 'Unavailable/failed must have empty arrays')
            self.check(bool(value['issues']), '/issues', 'status', 'Failure/unavailability requires reason')
            if status == 'failed':
                self.check('error' in severities, '/issues', 'status', 'Failed requires error issue')

        provenance = value['provenance']
        if provenance['parser_kind'] == 'design_example':
            self.check(provenance['model'] is None and provenance['settings'] == {}, '/provenance',
                       'provenance', 'Design example requires model=null and settings={}')
        if provenance['parser_kind'] == 'model':
            self.check(provenance['model'] is not None, '/provenance/model', 'provenance', 'Model parser requires model identity')
            missing = [k for k in ('prompt_sha256', 'raw_output_sha256') if provenance[k] is None]
            if provenance['model'] is not None and provenance['model']['digest'] is None:
                missing.append('model/digest')
            for key in missing:
                self.check(self.issue(value, '/provenance/' + key), '/provenance/' + key, 'issue',
                           'Unavailable model hash/digest requires issue')

        binding = value['retrieval_binding']
        if binding is not None:
            self.check(source is not None and binding['source_id'] == source['source_id'],
                       '/retrieval_binding/source_id', 'binding', 'Binding must match source identity')
        if source is None:
            return
        text = source['text']
        try:
            digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        except UnicodeEncodeError:
            digest = None
        self.check(source['text_sha256'] == digest and digest is not None, '/source/text_sha256',
                   'hash', 'Expected SHA-256 of exact UTF-8 source text')
        pairs = {'production': {'approved', 'unreviewed'}, 'test_fixture': {'test_only'}, 'unverified': {'unreviewed'}}
        self.check(source['review_status'] in pairs[source['kind']], '/source/review_status',
                   'source', 'Invalid kind/review_status pair')
        reference = source['reference']
        try:
            url = urlsplit(reference)
            if url.scheme:
                safe = url.scheme in ('http', 'https') and bool(url.hostname) and url.username is None and url.password is None
                credential_keys = {'password', 'passwd', 'token', 'access_token', 'api_key', 'apikey', 'secret', 'authorization'}
                safe = safe and not any(key.lower() in credential_keys for key, _ in parse_qsl(url.query))
            else:
                safe = not reference.startswith(('/', '\\')) and '\\' not in reference and '..' not in url.path.split('/')
        except ValueError:
            safe = False
        self.check(safe, '/source/reference', 'reference', 'Expected repository-relative locator or URL without credentials')

        for key, identity in (('ingredients', 'ingredient_id'), ('preparation_steps', 'step_id'), ('evidence', 'evidence_id')):
            self.unique(value[key], identity, '/' + key)
        evidence = {}
        for i, item in enumerate(value['evidence']):
            path = f'/evidence/{i}'
            valid = self.span(item, text, path)
            self.check(item['evidence_id'] == f"ev_{item['start']}_{item['end']}", path + '/evidence_id', 'id', 'ID must be derived from span')
            self.check(valid and text[item['start']:item['end']] == item['evidence_text'], path + '/evidence_text', 'evidence', 'Evidence must equal exact source slice')
            evidence[item['evidence_id']] = item
        ingredients = {item['ingredient_id']: item for item in value['ingredients']}
        for i, item in enumerate(value['ingredients']):
            self.ingredient(value, item, f'/ingredients/{i}', text, evidence)
        starts = []
        for i, item in enumerate(value['preparation_steps']):
            self.step(value, item, f'/preparation_steps/{i}', text, evidence, ingredients)
            starts.append((item['anchor']['start'], item['anchor']['end']))
        self.check(starts == sorted(starts), '/preparation_steps', 'order', 'Steps must be displayed in anchor order')

    def entity_anchor(self, item, path, text, prefix, identity, literal):
        anchor = item['anchor']
        valid = self.span(anchor, text, path + '/anchor')
        self.check(item[identity] == f"{prefix}_{anchor['start']}_{anchor['end']}", path + '/' + identity,
                   'id', 'ID must be derived from anchor')
        self.check(valid and item[literal] == text[anchor['start']:anchor['end']], path + '/' + literal,
                   'evidence', 'Entity literal must equal exact anchor slice')

    def ingredient(self, value, item, path, text, evidence):
        self.entity_anchor(item, path, text, 'ing', 'ingredient_id', 'ingredient_name')
        roles = self.links(item, path, evidence, ('name', 'quantity', 'unit', 'optionality'))
        self.check(self.covered(item['anchor'], roles['name']), path + '/evidence_links', 'evidence', 'Name evidence must cover anchor')
        for key, role in (('quantity_raw', 'quantity'), ('unit', 'unit')):
            if item[key] is not None:
                self.check(self.literal(item[key], roles[role]), path + '/' + key, 'evidence', 'Literal requires matching role evidence')
        state = item['quantity_state']
        raw, quantity, rule = item['quantity_raw'], item['quantity'], item['quantity_rule']
        if state == 'exact':
            self.check(quantity is not None and raw is not None and rule is not None, path + '/quantity', 'quantity', 'Exact requires value, literal and rule')
            if raw is not None and quantity is not None:
                if rule == 'half_literal':
                    valid = raw == '半' and quantity == 0.5
                elif rule == 'numeric_literal':
                    try:
                        parsed = self.numeric_parser(raw)
                        valid = isinstance(parsed, Decimal) and parsed.is_finite() and parsed == Decimal(str(quantity))
                    except UnsupportedQuantityFormat:
                        self.check(False, path + '/quantity_raw', 'unsupported_quantity_format',
                                   'Prototype cannot parse this literal; source content is not judged incorrect. '
                                   'Represent it as unparsed with quantity=null, quantity_rule=null, '
                                   'uncertainty=true, warning, partial and needs_review; retain literal evidence.')
                        valid = None
                    except (ValueError, InvalidOperation, OverflowError):
                        valid = False
                else:
                    valid = False
                if valid is not None:
                    self.check(valid, path + '/quantity', 'normalization', 'Quantity does not match literal normalization rule')
        else:
            self.check(quantity is None and rule is None and ((raw is None) == (state == 'missing')),
                       path + '/quantity', 'quantity', 'Quantity state requires correct null/value combination')
        if state in ('missing', 'vague') or item['unit'] is None:
            self.check(self.issue(value, path), path, 'issue', 'Missing/vague quantity or unit requires explanatory issue')
        needs_warning = state == 'unparsed' or item['optional_state'] == 'unresolved'
        if needs_warning:
            self.check(self.issue(value, path, 'warning') and value['status'] == 'partial', path, 'status', 'Unparsed/unresolved requires warning and partial')
        optional_state = item['optional_state']
        self.check(item['optional'] == (optional_state == 'explicit_optional'), path + '/optional', 'optionality', 'Optional flag must match explicit_optional')
        if optional_state in ('explicit_optional', 'explicit_required'):
            self.check(bool(roles['optionality']), path + '/evidence_links', 'evidence', 'Explicit optionality requires original evidence; semantics need review')
        uncertain = state != 'exact' or item['unit'] is None or optional_state == 'unresolved'
        self.check(not uncertain or item['uncertainty'], path + '/uncertainty', 'uncertainty', 'Missing/unresolved fields require uncertainty=true')
        if item['uncertainty'] and not uncertain:
            self.check(self.issue(value, path), path + '/uncertainty', 'issue', 'Additional uncertainty requires reason')

    def step(self, value, item, path, text, evidence, ingredients):
        self.entity_anchor(item, path, text, 'step', 'step_id', 'action_raw')
        roles = self.links(item, path, evidence, ('action', 'targets', 'context'))
        self.check(self.covered(item['anchor'], roles['action']), path + '/evidence_links', 'evidence', 'Action evidence must cover anchor')
        expected = _ACTION.get(item['action_raw'])
        actual = (item['normalized_action'], item['normalization_rule'])
        self.check(actual == (expected if expected else (None, None)), path + '/normalized_action', 'normalization', 'Action/rule must match v1 whitelist')
        ids = item['ingredient_ids']
        self.check(len(ids) == len(set(ids)), path + '/ingredient_ids', 'duplicate_id', 'Targets must be unique')
        for i, identity in enumerate(ids):
            self.check(identity in ingredients, f'{path}/ingredient_ids/{i}', 'reference', 'Target must exist in this snapshot')
            if identity in ingredients:
                self.check(self.literal(ingredients[identity]['ingredient_name'], roles['targets']),
                           f'{path}/ingredient_ids/{i}', 'evidence', 'Target name must occur in targets evidence; semantics need review')
        unresolved = item['target_status'] != 'resolved'
        self.check(bool(ids) == (item['target_status'] != 'unresolved'), path + '/ingredient_ids', 'targets', 'Target status and ID count disagree')
        if unresolved or expected is None:
            self.check(item['uncertainty'] and self.issue(value, path, 'warning') and value['status'] == 'partial',
                       path, 'status', 'Unknown action/targets requires uncertainty, warning and partial')
        if item['uncertainty'] and not unresolved and expected is not None:
            self.check(self.issue(value, path), path + '/uncertainty', 'issue', 'Additional uncertainty requires reason')


def validate_recipe_parsing(value: object, *, numeric_parser: Callable[[str], Decimal] = decimal_literal) -> ValidationReport:
    """Return JSON Pointer errors; never repair input or authenticate approval.

    Structural errors stop dependent checks, preventing malformed values from
    being treated as usable partial data. Literal coverage is not semantic proof.
    """
    validator = _Validator(numeric_parser)
    validator.schema(value, _RESULT)
    if not validator.errors:
        validator.semantics(value)
    return ValidationReport(tuple(validator.errors))


def load_recipe_parsing(value: object, *, numeric_parser: Callable[[str], Decimal] = decimal_literal) -> RecipeParsingResult:
    """Validate and return an isolated JSON snapshot, or raise with all errors."""
    report = validate_recipe_parsing(value, numeric_parser=numeric_parser)
    if not report.valid:
        raise ContractValidationError(report)
    return cast(RecipeParsingResult, deepcopy(value))
