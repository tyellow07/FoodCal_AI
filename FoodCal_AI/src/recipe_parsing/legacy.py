"""Explicit, conservative seven-field import; never restores omitted steps."""
from decimal import Decimal
import math
import re

from .validator import _ACTION


FIELDS = {'ingredient_name', 'quantity', 'unit', 'preparation_method',
          'optional', 'evidence_text', 'uncertainty'}


class CandidateConversionError(ValueError):
    def __init__(self, code, path, message):
        self.code, self.path = code, path
        super().__init__(message)


def _positions(text, literal):
    return [m.start() for m in re.finditer('(?=' + re.escape(literal) + ')', text)]


def convert_legacy_items(items, text):
    """Return v1 candidate fields, with coverage_unknown handled by the adapter.

    Unique literal occurrence is required for anchors. Ambiguous anchors/method
    targets are quarantined with warnings; malformed values/evidence fail closed.
    Only the adjacent quantity token is considered, without unit conversion.
    """
    def fail(code, path, message):
        raise CandidateConversionError(code, path, message)

    if not isinstance(items, list):
        fail('invalid_schema', '', 'Legacy response must be an array')
    for i, item in enumerate(items):
        path = f'/{i}'
        if not isinstance(item, dict) or set(item) != FIELDS:
            fail('invalid_schema', path, 'Legacy item must have exactly the original seven fields')
        for key in ('ingredient_name', 'evidence_text'):
            if not isinstance(item[key], str) or not item[key].strip():
                fail('invalid_schema', path + '/' + key, 'Expected nonempty literal')
        for key in ('unit', 'preparation_method'):
            if item[key] is not None and (not isinstance(item[key], str) or not item[key].strip()):
                fail('invalid_schema', path + '/' + key, 'Expected string or null')
        q = item['quantity']
        if q is not None and not (type(q) is int and q >= 0 or type(q) is float and math.isfinite(q) and q >= 0):
            fail('invalid_schema', path + '/quantity', 'Expected nonnegative finite quantity or null')
        if any(type(item[key]) is not bool for key in ('optional', 'uncertainty')):
            fail('invalid_schema', path, 'Flags must be bool')
        quote = item['evidence_text']
        if quote not in text or item['ingredient_name'] not in quote:
            fail('invalid_evidence', path + '/evidence_text', 'Evidence must be a supporting literal source excerpt')
        if item['unit'] is not None and item['unit'] not in text:
            fail('invalid_evidence', path + '/unit', 'Legacy unit does not occur in source')

    result = {'ingredients': [], 'preparation_steps': [], 'evidence': [], 'issues': []}
    evidence = {}
    ids = set()
    names = [i['ingredient_name'] for i in items]

    def issue(code, path, message, severity='warning'):
        result['issues'].append({'code': code, 'severity': severity, 'path': path, 'message': message})

    for i, old in enumerate(items):
        quote, name = old['evidence_text'], old['ingredient_name']
        positions, within = _positions(text, quote), _positions(quote, name)
        if len(positions) != 1 or len(within) != 1:
            issue('coverage_unknown', '', f'Legacy item {i}: ambiguous declaration/evidence anchor; quarantined in audit')
            continue
        start = positions[0] + within[0]
        end = start + len(name)
        identity = f'ing_{start}_{end}'
        if identity in ids:
            fail('invalid_schema', f'/{i}', 'Duplicate legacy declaration; not merged')
        ids.add(identity)
        ev_start, ev_end = positions[0], positions[0] + len(quote)
        ev_id = f'ev_{ev_start}_{ev_end}'
        evidence[ev_id] = {'evidence_id': ev_id, 'start': ev_start, 'end': ev_end, 'evidence_text': quote}
        path = f"/ingredients/{len(result['ingredients'])}"
        tail = quote[within[0] + len(name):].lstrip()
        token = re.split(r'[\s,，;；、()（）]', tail, maxsplit=1)[0]
        if old['unit'] and token.endswith(old['unit']):
            token = token[:-len(old['unit'])]
        raw, rule, quantity = token or None, None, old['quantity']
        if tail.startswith(('適量', '少許')):
            raw = tail[:2]
            if quantity is not None:
                fail('invalid_evidence', f'/{i}/quantity', 'Vague source cannot support an exact legacy value')
            state = 'vague'
        elif not raw:
            if quantity is not None:
                fail('invalid_evidence', f'/{i}/quantity', 'Claimed legacy value has no quantity literal')
            state = 'missing'
        elif quantity is not None and raw == '半' and quantity == 0.5:
            state, rule = 'exact', 'half_literal'
        elif quantity is not None and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', raw) and Decimal(raw) == Decimal(str(quantity)):
            state, rule = 'exact', 'numeric_literal'
        else:
            state, quantity = 'unparsed', None
            issue('quantity_unparsed', path + '/quantity', 'Legacy value/literal is unsupported or inconsistent; retained literal without estimation')
        roles = ['name'] + (['quantity'] if raw is not None else [])
        if old['unit'] is not None:
            if old['unit'] not in quote:
                fail('invalid_evidence', f'/{i}/unit', 'Unit is not scoped to this evidence')
            roles.append('unit')
        optional_state = 'not_marked'
        optional = old['optional']
        if old['optional']:
            # Narrow import rule only: direct marker in the ingredient clause.
            if '可不加' not in quote or any(other != name and other in quote for other in names):
                optional, optional_state = False, 'unresolved'
                issue('optional_unresolved', path + '/optional_state',
                      'Legacy optional=true cannot be scoped by this importer; original flag retained in audit, not asserted as fact')
            else:
                optional_state = 'explicit_optional'
                roles.append('optionality')
        uncertain = old['uncertainty'] or state != 'exact' or old['unit'] is None or optional_state == 'unresolved'
        if state in ('missing', 'vague'):
            issue('quantity_' + state, path + '/quantity', 'Legacy quantity retains original missing/vague meaning', 'info')
        if old['unit'] is None:
            issue('unit_missing', path + '/unit', 'No unit was extracted; no unit inferred', 'info')
        if old['uncertainty'] and state == 'exact' and old['unit'] is not None:
            issue('coverage_unknown', path + '/uncertainty', 'Original legacy uncertainty retained', 'info')
        result['ingredients'].append({
            'ingredient_id': identity, 'anchor': {'start': start, 'end': end}, 'ingredient_name': name,
            'quantity': quantity, 'quantity_raw': raw, 'quantity_state': state, 'quantity_rule': rule,
            'unit': old['unit'], 'optional': optional, 'optional_state': optional_state,
            'uncertainty': uncertain, 'evidence_links': [{'evidence_id': ev_id, 'supports': roles}],
        })
        method = old['preparation_method']
        if method is not None:
            if method not in text:
                issue('unsupported_legacy_field', '', f'Legacy item {i}: non-verbatim preparation method retained only in audit')
                continue
            occurrences = _positions(quote, method)
            if len(occurrences) != 1 or any(other != name and other in quote for other in names):
                issue('coverage_unknown', '', f'Legacy item {i}: preparation scope ambiguous; method quarantined, no group inferred')
                continue
            action_start = ev_start + occurrences[0]
            action_end = action_start + len(method)
            normalized, normalization = _ACTION.get(method, (None, None))
            step_path = f"/preparation_steps/{len(result['preparation_steps'])}"
            if normalized is None:
                issue('action_unmapped', step_path, 'Legacy action outside v1 vocabulary retained verbatim')
            result['preparation_steps'].append({
                'step_id': f'step_{action_start}_{action_end}',
                'anchor': {'start': action_start, 'end': action_end}, 'action_raw': method,
                'normalized_action': normalized, 'normalization_rule': normalization,
                'ingredient_ids': [identity], 'target_status': 'resolved', 'uncertainty': normalized is None,
                'evidence_links': [{'evidence_id': ev_id, 'supports': ['action', 'targets', 'context']}],
            })
    # Canonical display order is independent of original model item order.
    old_steps = list(result['preparation_steps'])
    result['preparation_steps'].sort(key=lambda step: (step['anchor']['start'], step['anchor']['end']))
    order = {step['step_id']: i for i, step in enumerate(result['preparation_steps'])}
    for item in result['issues']:
        match = re.fullmatch(r'/preparation_steps/([0-9]+)(.*)', item['path'])
        if match:
            original = old_steps[int(match[1])]
            item['path'] = f"/preparation_steps/{order[original['step_id']]}{match[2]}"
    result['evidence'] = list(evidence.values())
    return result
