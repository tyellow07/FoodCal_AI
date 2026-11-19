"""Local controlled supply of photo item source reviews (photo_item_source_review_design_v0 §10 item 7).

Same trust model as LocalContextProvider (Review v1), with photo_scope in place of recipe_scope:
- only the trusted bootstrap supplies the registry reader, its SHA-256 pin, authority, policy revision,
  the registered review bundles and the clock; a request supplies only a context ID and the exact scopes
  it expects;
- every attribute result needs an active reviewer and an active exact-scope grant for that attribute and
  rule; every decided source_use / integrity gate record needs a grant listing ['gate', <name>, null];
- reviews and gate decisions must precede the registration and the trusted now.

The provider then rebuilds the basis with build_photo_nutrition_basis, which revalidates everything.
This is not person authentication: registry entries are owner assertions. Output stays diagnostic/test_only
and nutrition_eligible / production_eligible stay false.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import re
from typing import Callable

from .context_provider import RegisteredReview, _Blocked, _check, _json, _text, _time, _window
from .photo_review import CONTEXT_PROFILE, build_photo_nutrition_basis
from .review_types import ReviewIssue, ReviewTrustAnchor

REGISTRY_PROFILE = 'foodcal.local_photo_review_registry.v0'
PURPOSE = 'diagnostic/test_only'
GATE_DOMAIN = 'gate'

_FIELDS = {
    'sources': {'status', 'candidate_scope', 'official_dataset_version', 'source_url', 'retrieved_at_utc',
                'evidence_ref', 'purposes'},
    'reviewers': {'reviewer_id', 'status', 'identity_evidence_ref'},
    'grants': {'reviewer_id', 'status', 'valid_from', 'expires_at', 'photo_scope', 'candidate_scope',
               'source_registration_id', 'purpose', 'process_ref', 'attributes', 'rules'},
    'contexts': {'status', 'valid_from', 'expires_at', 'registered_at', 'input_sha256', 'context_sha256',
                 'photo_scope', 'candidate_scope', 'basis_target', 'purpose', 'source_registration_id', 'grant_ids'},
}


@dataclass(frozen=True)
class PhotoProviderResult:
    provider_gate: str
    issues: tuple[ReviewIssue, ...]
    build: dict | None = None
    context_id: str | None = None
    policy_revision: int | None = None
    usage: str = 'local_controlled/test_only'
    nutrition_eligible: bool = False
    production_eligible: bool = False
    submitter_authenticated: bool = False


def _registry_shape(registry):
    for table, keys in _FIELDS.items():
        for key, record in registry[table].items():
            path = f'/registry/{table}/{key}'
            _check(_text(key) and isinstance(record, dict) and set(record) == keys
                   and record['status'] in ('active', 'revoked', 'superseded'),
                   'registry_invalid', path, 'Unexpected registration fields or state')
            if table in ('contexts', 'grants'):
                _check(isinstance(record['photo_scope'], dict) and isinstance(record['candidate_scope'], dict)
                       and record['purpose'] == PURPOSE, 'registry_invalid', path, 'Only exact diagnostic scopes')
            if table == 'contexts':
                _check(isinstance(record['grant_ids'], list) and bool(record['grant_ids'])
                       and all(_text(g) for g in record['grant_ids'])
                       and len(set(record['grant_ids'])) == len(record['grant_ids']),
                       'registry_invalid', path, 'Grant IDs must be a nonempty unique list')
                for name in ('input_sha256', 'context_sha256'):
                    _check(isinstance(record[name], str) and re.fullmatch('[0-9a-f]{64}', record[name]) is not None,
                           'registry_invalid', path, 'Registered SHA-256 required')
            if table == 'sources':
                _check(isinstance(record['purposes'], list) and record['purposes'] == [PURPOSE]
                       and (record['official_dataset_version'] is None or _text(record['official_dataset_version'])),
                       'registry_invalid', path, 'Explicit source purpose and nullable version required')
            if table == 'grants':
                for name, width in (('attributes', 3), ('rules', 2)):
                    _check(isinstance(record[name], list)
                           and all(isinstance(a, list) and len(a) == width and all(_text(v) for v in a[:2])
                                   and (width == 2 or a[2] is None or _text(a[2])) for a in record[name]),
                           'registry_invalid', path, 'Permissions must be explicit tuples encoded as arrays')


class PhotoContextProvider:
    """Trusted bootstrap only; see the module docstring. A changed registry needs a new instance."""

    def __init__(self, *, registry_reader: Callable[[], bytes], registry_sha256: str, authority_ref: str,
                 policy_revision: int, reviews: dict[str, RegisteredReview], clock: Callable[[], datetime]):
        if (not isinstance(registry_sha256, str) or re.fullmatch(r'[0-9a-f]{64}', registry_sha256) is None
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
               and registry['profile'] == REGISTRY_PROFILE and registry['authority_ref'] == self._authority,
               'registry_invalid', '/registry', 'Unexpected registry profile, fields or authority')
        _check(type(registry['revision']) is int and registry['revision'] == self._revision,
               'policy_revision_untrusted', '/registry/revision', 'Revision differs from trusted bootstrap')
        _check(all(isinstance(registry[k], dict) for k in _FIELDS),
               'registry_invalid', '/registry', 'Registrations must be ID-indexed objects')
        _registry_shape(registry)
        return registry

    def _reviewer(self, registry, reviewer_id, path):
        _check(isinstance(reviewer_id, str) and reviewer_id in registry['reviewers'],
               'reviewer_not_registered', path, 'Unknown reviewer ID')
        reviewer = registry['reviewers'][reviewer_id]
        _check(reviewer['reviewer_id'] == reviewer_id and _text(reviewer['identity_evidence_ref']),
               'reviewer_not_registered', path, 'Owner must register identity evidence')
        _check(reviewer['status'] == 'active', 'reviewer_revoked', path, 'Reviewer is not active')

    def _authorized(self, registry, registration, value, source_id, reviewer_id, reviewed_at, now, path,
                    permission, *, rule=None, process_ref=None):
        for grant_id in registration['grant_ids']:
            _check(grant_id in registry['grants'], 'grant_not_registered', path, 'Unknown grant ID')
            grant = registry['grants'][grant_id]
            if grant['reviewer_id'] != reviewer_id:
                continue
            _check(grant['status'] == 'active', 'grant_revoked', path, 'Registered grant is not active')
            start, end = _window(grant, now, path)
            if (start <= reviewed_at < end and grant['photo_scope'] == value['photo_scope']
                    and grant['candidate_scope'] == value['candidate_scope']
                    and grant['source_registration_id'] == source_id and grant['purpose'] == value['purpose']
                    and permission in grant['attributes']
                    and (rule is None or rule in grant['rules'])
                    and (process_ref is None or grant['process_ref'] == process_ref)):
                return True
        return False

    def review_registered(self, context_id, *, expected_photo_scope, expected_candidate_scope, basis_target,
                          energy_nutrient):
        """Return no basis on failed authorization, evidence or policy recheck."""
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
            registered_at = _time(registration['registered_at'])
            _check(registered_at <= now, 'review_time_untrusted', '/context/registered_at', 'Registration is in the future')
            bundle = deepcopy(self._reviews[context_id])
            _check(isinstance(bundle, RegisteredReview)
                   and sha256(bundle.input_bytes).hexdigest() == registration['input_sha256']
                   and sha256(bundle.context.record_bytes).hexdigest() == registration['context_sha256'],
                   'registered_hash_mismatch', '/context', 'Input or ledger differs from registered bytes')
            value, ledger = _json(bundle.input_bytes), _json(bundle.context.record_bytes)
            _check(value['photo_scope'] == expected_photo_scope == registration['photo_scope']
                   and value['candidate_scope'] == expected_candidate_scope == registration['candidate_scope'],
                   'registered_scope_mismatch', '/scope', 'Exact photo and candidate scopes required')
            _check(value['basis_target'] == basis_target == registration['basis_target']
                   and value['purpose'] == registration['purpose'] == PURPOSE,
                   'review_target_mismatch', '/basis_target', 'Purpose and nutrient target differ')
            _check(ledger.get('profile') == CONTEXT_PROFILE and ledger.get('authority_ref') == self._authority,
                   'context_authority_mismatch', '/context/authority_ref', 'Unregistered context authority or profile')
            _check(ledger.get('policy_revision') == self._revision, 'policy_revision_untrusted',
                   '/context/policy_revision', 'Ledger revision differs from trusted bootstrap')
            source_id = registration['source_registration_id']
            _check(source_id in registry['sources'], 'source_not_registered', '/source', 'Unknown source registration')
            source = registry['sources'][source_id]
            _check(source['status'] == 'active', 'source_inactive', '/source', 'Source revoked')
            _check(source['candidate_scope'] == value['candidate_scope'],
                   'source_version_mismatch', '/source', 'Food ID and snapshot pins differ')
            _check(value['purpose'] in source['purposes'] and _text(source['evidence_ref']),
                   'source_not_authorized', '/source', 'Missing source registration evidence or permitted purpose')
            provenance = _json(bundle.context.attachments[value['attachment_refs']['source_provenance']['attachment_id']])
            _check(provenance.get('official_dataset_version') == source['official_dataset_version']
                   and provenance['source_url'] == source['source_url']
                   and provenance['retrieved_at_utc'] == source['retrieved_at_utc'],
                   'source_version_mismatch', '/source', 'Source metadata or version differs; null stays unknown')
            results = []
            for index, record in enumerate(ledger['reviews']):
                row, path = record['result'], f'/context/reviews/{index}'
                self._reviewer(registry, row['reviewer_ref'], path)
                reviewed_at = _time(row['reviewed_at'])
                _check(reviewed_at <= now and reviewed_at <= registered_at, 'review_time_untrusted', path,
                       'Review must precede registration and trusted now')
                _check(self._authorized(registry, registration, value, source_id, row['reviewer_ref'], reviewed_at,
                                        now, path, [row['domain'], row['attribute'], row['nutrient_key']],
                                        rule=[row['rule_id'], row['rule_version']], process_ref=record['process_ref']),
                       'review_not_authorized', path, 'No active exact-scope attribute/rule grant')
                results.append(row)
            gates = ledger['gate_records']
            for index, gate in enumerate(gates):
                if gate.get('status') == 'pending':
                    continue
                path = f'/context/gate_records/{index}'
                self._reviewer(registry, gate.get('reviewer_ref'), path)
                reviewed_at = _time(gate['reviewed_at'])
                _check(reviewed_at <= now and reviewed_at <= registered_at, 'review_time_untrusted', path,
                       'Gate decision must precede registration and trusted now')
                _check(self._authorized(registry, registration, value, source_id, gate['reviewer_ref'], reviewed_at,
                                        now, path, [GATE_DOMAIN, gate['gate'], None]),
                       'gate_not_authorized', path, 'No active exact-scope grant for this gate')
            anchor = ReviewTrustAnchor(self._authority, registration['context_sha256'])
            build = build_photo_nutrition_basis(value, results, gates, context=bundle.context, trust_anchor=anchor,
                                                energy_nutrient=energy_nutrient)
            _check(self._registry() == registry, 'policy_changed', '/registry', 'Policy changed during review')
            _check(build['nutrition_eligible'] is False and build['production_eligible'] is False,
                   'aggregation_invalid', '/build', 'Basis must stay diagnostic')
            return PhotoProviderResult('passed', (), build, context_id, self._revision)
        except _Blocked as error:
            return PhotoProviderResult('blocked', (error.issue,),
                                       context_id=context_id if isinstance(context_id, str) else None,
                                       policy_revision=self._revision)
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, UnicodeError, OSError):
            return PhotoProviderResult('blocked', (ReviewIssue('registry_or_bundle_invalid', '/provider',
                                                               'Malformed or unavailable controlled data; no repair'),),
                                       context_id=context_id if isinstance(context_id, str) else None,
                                       policy_revision=self._revision)
