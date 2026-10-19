"""Local controlled/test-only review supply. Not person authentication or approval."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Callable

from .review_types import ReviewContext, ReviewIssue, ReviewTrustAnchor, ReviewValidation
from .review_validation import validate_review
from .review_aggregation import aggregate_review


@dataclass(frozen=True)
class RegisteredReview:
    """Owner-supplied bytes and native attachments, copied by the provider at bootstrap."""
    input_bytes: bytes
    context: ReviewContext


@dataclass(frozen=True)
class ProviderResult:
    provider_gate: str
    issues: tuple[ReviewIssue, ...]
    validation: ReviewValidation | None = None
    decision: dict | None = None
    context_id: str | None = None
    policy_revision: int | None = None
    usage: str = 'local_controlled/test_only'
    nutrition_eligible: bool = False
    submitter_authenticated: bool = False


class _Blocked(Exception):
    def __init__(self, code, path, message):
        self.issue = ReviewIssue(code, path, message)


def _check(condition, code, path, message):
    if not condition:
        raise _Blocked(code, path, message)


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Non-finite JSON constant')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def _time(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Timezone required')
    return result


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _registry_shape(registry):
    fields = {
        'sources': {'status', 'candidate_scope', 'official_dataset_version', 'source_url',
                    'retrieved_at_utc', 'evidence_ref', 'purposes'},
        'reviewers': {'reviewer_id', 'status', 'identity_evidence_ref'},
        'grants': {'reviewer_id', 'status', 'valid_from', 'expires_at', 'recipe_scope',
                   'candidate_scope', 'source_registration_id', 'purpose', 'process_ref', 'attributes', 'rules'},
        'contexts': {'status', 'valid_from', 'expires_at', 'registered_at', 'input_sha256',
                     'context_sha256', 'recipe_scope', 'candidate_scope', 'basis_target',
                     'purpose', 'source_registration_id', 'grant_ids'},
    }
    for table, keys in fields.items():
        for key, record in registry[table].items():
            path = f'/registry/{table}/{key}'
            _check(_text(key) and isinstance(record, dict) and set(record) == keys
                   and record['status'] in ('active', 'revoked', 'superseded'),
                   'registry_invalid', path, 'Unexpected registration fields or state')
            if table in ('contexts', 'grants'):
                _check(isinstance(record['recipe_scope'], dict) and isinstance(record['candidate_scope'], dict)
                       and record['purpose'] == 'diagnostic/test_only',
                       'registry_invalid', path, 'Only exact diagnostic scopes are supported')
            if table == 'contexts':
                _check(isinstance(record['grant_ids'], list) and bool(record['grant_ids'])
                       and all(_text(g) for g in record['grant_ids'])
                       and len(set(record['grant_ids'])) == len(record['grant_ids']),
                       'registry_invalid', path, 'Grant IDs must be a nonempty unique list')
                for name in ('input_sha256', 'context_sha256'):
                    _check(isinstance(record[name], str) and re.fullmatch('[0-9a-f]{64}', record[name]) is not None,
                           'registry_invalid', path, 'Registered SHA-256 required')
            if table == 'sources':
                _check(isinstance(record['purposes'], list) and record['purposes'] == ['diagnostic/test_only']
                       and (record['official_dataset_version'] is None or _text(record['official_dataset_version'])),
                       'registry_invalid', path, 'Explicit source purpose and nullable version required')
            if table == 'grants':
                for name, width in (('attributes', 3), ('rules', 2)):
                    _check(isinstance(record[name], list)
                           and all(isinstance(a, list) and len(a) == width
                                   and all(_text(v) for v in a[:2])
                                   and (width == 2 or a[2] is None or _text(a[2])) for a in record[name]),
                           'registry_invalid', path, 'Permissions must be explicit tuples encoded as arrays')


def _window(record, now, path):
    start, end = _time(record['valid_from']), _time(record['expires_at'])
    _check(start < end, 'registry_invalid', path, 'Empty or reversed validity interval')
    _check(start <= now < end, 'review_expired', path, 'Outside [valid_from, expires_at)')
    return start, end


class LocalContextProvider:
    """Only trusted bootstrap can supply registry reader, pin, clock and review bundles.

    Public requests supply IDs and expected scope, never anchors, roles or attachments.
    Native objects are trusted-owner snapshots, not cryptographically authenticated.
    A changed registry requires a new independently configured provider instance.
    """
    def __init__(self, *, registry_reader: Callable[[], bytes], registry_sha256: str,
                 authority_ref: str, policy_revision: int,
                 reviews: dict[str, RegisteredReview], clock: Callable[[], datetime]):
        if (not isinstance(registry_sha256, str)
                or re.fullmatch(r'[0-9a-f]{64}', registry_sha256) is None
                or not _text(authority_ref) or type(policy_revision) is not int or policy_revision < 1):
            raise ValueError('Trusted bootstrap needs an independent pin, authority and revision')
        self._reader, self._pin = registry_reader, registry_sha256
        self._authority, self._revision = authority_ref, policy_revision
        self._reviews, self._clock = deepcopy(reviews), clock
        self._last_time = None

    def _registry(self):
        raw = self._reader()
        _check(isinstance(raw, bytes) and sha256(raw).hexdigest() == self._pin,
               'registry_hash_mismatch', '/registry', 'Registry differs from independent bootstrap pin')
        registry = _json(raw)
        _check(set(registry) == {'profile', 'authority_ref', 'revision', 'sources', 'reviewers', 'grants', 'contexts'}
               and registry['profile'] == 'foodcal.local_review_registry.v0'
               and registry['authority_ref'] == self._authority,
               'registry_invalid', '/registry', 'Unexpected registry profile, fields or authority')
        _check(type(registry['revision']) is int and registry['revision'] == self._revision,
               'policy_revision_untrusted', '/registry/revision', 'Revision differs from trusted bootstrap')
        _check(all(isinstance(registry[k], dict) for k in ('sources', 'reviewers', 'grants', 'contexts')),
               'registry_invalid', '/registry', 'Registrations must be ID-indexed objects')
        _registry_shape(registry)
        return registry

    def review_registered(self, context_id, *, expected_recipe_scope, expected_candidate_scope, basis_target):
        """Return no aggregate on failed authorization, evidence or policy recheck."""
        try:
            registry = self._registry()
            now = self._clock()
            _check(isinstance(now, datetime) and now.tzinfo is not None and now.utcoffset() is not None,
                   'review_time_untrusted', '/clock', 'Trusted clock must be timezone aware')
            _check(self._last_time is None or now >= self._last_time,
                   'review_time_untrusted', '/clock', 'Clock moved backwards')
            self._last_time = now
            _check(isinstance(context_id, str) and context_id in registry['contexts'] and context_id in self._reviews,
                   'context_not_registered', '/context_id', 'No registered bundle for this ID')
            registration = registry['contexts'][context_id]
            _check(registration['status'] == 'active', 'context_inactive', '/context', 'Context revoked or superseded')
            _window(registration, now, '/context')
            _check(_time(registration['registered_at']) <= now, 'review_time_untrusted', '/context/registered_at',
                   'Registration is in the future')
            bundle = deepcopy(self._reviews[context_id])
            _check(isinstance(bundle, RegisteredReview)
                   and sha256(bundle.input_bytes).hexdigest() == registration['input_sha256']
                   and sha256(bundle.context.record_bytes).hexdigest() == registration['context_sha256'],
                   'registered_hash_mismatch', '/context', 'Input or ledger differs from registered bytes')
            value, ledger = _json(bundle.input_bytes), _json(bundle.context.record_bytes)
            _check(value['recipe_scope'] == expected_recipe_scope == registration['recipe_scope']
                   and value['candidate_scope'] == expected_candidate_scope == registration['candidate_scope'],
                   'registered_scope_mismatch', '/scope', 'Exact recipe and candidate scopes required')
            _check(value['basis_target'] == basis_target == registration['basis_target']
                   and value['purpose'] == registration['purpose'] == 'diagnostic/test_only',
                   'review_target_mismatch', '/basis_target', 'Purpose and nutrient target differ')
            _check(ledger['authority_ref'] == self._authority,
                   'context_authority_mismatch', '/context/authority_ref', 'Unregistered context authority')
            source_id = registration['source_registration_id']
            _check(source_id in registry['sources'], 'source_not_registered', '/source', 'Unknown source registration')
            source = registry['sources'][source_id]
            _check(source['status'] == 'active', 'source_inactive', '/source', 'Source revoked')
            _check(source['candidate_scope'] == value['candidate_scope'],
                   'source_version_mismatch', '/source', 'Food ID and snapshot pins differ')
            _check(value['purpose'] in source['purposes'] and _text(source['evidence_ref']),
                   'source_not_authorized', '/source', 'Missing source registration evidence or permitted purpose')
            prov_id = value['attachment_refs']['source_provenance']['attachment_id']
            provenance = _json(bundle.context.attachments[prov_id])
            _check(provenance.get('official_dataset_version') == source['official_dataset_version']
                   and provenance['source_url'] == source['source_url']
                   and provenance['retrieved_at_utc'] == source['retrieved_at_utc'],
                   'source_version_mismatch', '/source', 'Source metadata or version differs; null stays unknown')
            results = []
            for index, record in enumerate(ledger['reviews']):
                row = record['result']
                path = f'/context/reviews/{index}'
                reviewer_id = row['reviewer_ref']
                _check(reviewer_id in registry['reviewers'], 'reviewer_not_registered', path, 'Unknown reviewer ID')
                reviewer = registry['reviewers'][reviewer_id]
                _check(reviewer['reviewer_id'] == reviewer_id and _text(reviewer['identity_evidence_ref']),
                       'reviewer_not_registered', path, 'Owner must register identity evidence')
                _check(reviewer['status'] == 'active', 'reviewer_revoked', path, 'Reviewer is not active')
                reviewed_at = _time(row['reviewed_at'])
                _check(reviewed_at <= now and reviewed_at <= _time(registration['registered_at']),
                       'review_time_untrusted', path, 'Review must precede registration and trusted now')
                authorized = False
                for grant_id in registration['grant_ids']:
                    grant = registry['grants'][grant_id]
                    if grant['reviewer_id'] != reviewer_id:
                        continue
                    _check(grant['status'] == 'active', 'grant_revoked', path, 'Registered grant is not active')
                    start, end = _window(grant, now, path)
                    if (start <= reviewed_at < end and grant['recipe_scope'] == value['recipe_scope']
                            and grant['candidate_scope'] == value['candidate_scope']
                            and grant['source_registration_id'] == source_id
                            and grant['purpose'] == value['purpose']
                            and grant['process_ref'] == record['process_ref']
                            and [row['domain'], row['attribute'], row['nutrient_key']] in grant['attributes']
                            and [row['rule_id'], row['rule_version']] in grant['rules']):
                        authorized = True
                _check(authorized, 'review_not_authorized', path, 'No active exact-scope attribute/rule grant')
                results.append(row)
            anchor = ReviewTrustAnchor(self._authority, registration['context_sha256'])
            report = validate_review(value, results, context=bundle.context, trust_anchor=anchor)
            if not report.valid:
                return ProviderResult('passed', report.issues, report, context_id=context_id,
                                      policy_revision=self._revision)
            decision = aggregate_review(value, results, context=bundle.context, trust_anchor=anchor)
            _check(self._registry() == registry, 'policy_changed', '/registry', 'Policy changed during review')
            _check(decision['input_integrity'] == 'verified' and decision['nutrition_eligible'] is False,
                   'aggregation_invalid', '/decision', 'Aggregate did not preserve validated diagnostic state')
            return ProviderResult('passed', (), report, decision, context_id, self._revision)
        except _Blocked as error:
            return ProviderResult('blocked', (error.issue,), context_id=context_id if isinstance(context_id, str) else None,
                                  policy_revision=self._revision)
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, UnicodeError, OSError):
            return ProviderResult('blocked', (ReviewIssue('registry_or_bundle_invalid', '/provider',
                                                         'Malformed or unavailable controlled data; no repair'),),
                                  context_id=context_id if isinstance(context_id, str) else None,
                                  policy_revision=self._revision)
