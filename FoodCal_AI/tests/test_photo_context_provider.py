"""Photo review context provider: registry authorization on top of the synthetic photo review fixture."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import unittest

from src.nutrition.context_provider import RegisteredReview
from src.nutrition.photo_context_provider import PhotoContextProvider
from tests.test_photo_item_source_review import SyntheticPhotoReview, encoded

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 13, 12, tzinfo=TZ)
PROCESS = 'test://synthetic-photo-review-process'


class Setup:
    def __init__(self, fixture=None):
        self.f = fixture or SyntheticPhotoReview()
        self.input_bytes = encoded(self.f.input)
        refs = self.f.input['attachment_refs']
        provenance = json.loads(self.f.context.attachments[refs['source_provenance']['attachment_id']])
        scope, candidate = self.f.input['photo_scope'], self.f.input['candidate_scope']
        grants = {}
        for row in self.f.results:
            grant = grants.setdefault('g:' + row['reviewer_ref'], self.grant(row['reviewer_ref'], scope, candidate))
            permission = [row['domain'], row['attribute'], row['nutrient_key']]
            if permission not in grant['attributes']:
                grant['attributes'].append(permission)
            if [row['rule_id'], row['rule_version']] not in grant['rules']:
                grant['rules'].append([row['rule_id'], row['rule_version']])
        for gate in self.f.gates:
            if gate['status'] != 'pending':
                grant = grants.setdefault('g:' + gate['reviewer_ref'], self.grant(gate['reviewer_ref'], scope, candidate))
                grant['attributes'].append(['gate', gate['gate'], None])
        people = {g['reviewer_id'] for g in grants.values()}
        self.registry = {
            'profile': 'foodcal.local_photo_review_registry.v0', 'authority_ref': 'test://fixture-owner', 'revision': 1,
            'sources': {'tfda-snapshot': {'status': 'active', 'candidate_scope': candidate,
                                          'official_dataset_version': provenance.get('official_dataset_version'),
                                          'source_url': provenance['source_url'],
                                          'retrieved_at_utc': provenance['retrieved_at_utc'],
                                          'evidence_ref': 'test://source-registration',
                                          'purposes': ['diagnostic/test_only']}},
            'reviewers': {p: {'reviewer_id': p, 'status': 'active', 'identity_evidence_ref': 'test://identity/' + p}
                          for p in people},
            'grants': grants,
            'contexts': {'ctx-1': {'status': 'active', 'valid_from': '2026-11-01T00:00:00+08:00',
                                   'expires_at': '2026-12-01T00:00:00+08:00',
                                   'registered_at': '2026-11-12T12:00:00+08:00',
                                   'input_sha256': sha256(self.input_bytes).hexdigest(),
                                   'context_sha256': self.f.anchor.context_sha256, 'photo_scope': scope,
                                   'candidate_scope': candidate, 'basis_target': self.f.input['basis_target'],
                                   'purpose': 'diagnostic/test_only', 'source_registration_id': 'tfda-snapshot',
                                   'grant_ids': sorted(grants)}}}

    @staticmethod
    def grant(reviewer, scope, candidate):
        return {'reviewer_id': reviewer, 'status': 'active', 'valid_from': '2026-11-01T00:00:00+08:00',
                'expires_at': '2026-12-01T00:00:00+08:00', 'photo_scope': deepcopy(scope),
                'candidate_scope': deepcopy(candidate), 'source_registration_id': 'tfda-snapshot',
                'purpose': 'diagnostic/test_only', 'process_ref': PROCESS, 'attributes': [], 'rules': []}

    def provider(self, *, registry=None, pin=None, input_bytes=None, clock=None):
        raw = json.dumps(registry or self.registry, ensure_ascii=False).encode('utf-8')
        return PhotoContextProvider(registry_reader=lambda: raw, registry_sha256=pin or sha256(raw).hexdigest(),
                                    authority_ref='test://fixture-owner', policy_revision=1,
                                    reviews={'ctx-1': RegisteredReview(input_bytes or self.input_bytes, self.f.context)},
                                    clock=clock or (lambda: NOW))

    def review(self, provider, **changes):
        arguments = {'expected_photo_scope': self.f.input['photo_scope'],
                     'expected_candidate_scope': self.f.input['candidate_scope'],
                     'basis_target': self.f.input['basis_target'], 'energy_nutrient': '熱量'}
        arguments.update(changes)
        return provider.review_registered('ctx-1', **arguments)


class PhotoContextProviderTests(unittest.TestCase):
    def setUp(self):
        self.s = Setup()

    def blocked(self, result, code):
        self.assertEqual(result.provider_gate, 'blocked', result)
        self.assertEqual(result.issues[0].code, code)
        self.assertIsNone(result.build)

    def test_registered_review_builds_a_basis_that_computes_downstream(self):
        result = self.s.review(self.s.provider())
        self.assertEqual(result.provider_gate, 'passed', result.issues)
        self.assertEqual((result.build['status'], result.build['reasons']), ('built', []))
        self.assertFalse(result.nutrition_eligible or result.production_eligible or result.submitter_authenticated)
        energy = self.s.f.energy(result.build['basis'])
        self.assertEqual(energy['items'][0]['status'], 'computed')

    def test_unregistered_or_revoked_reviewer(self):
        registry = deepcopy(self.s.registry)
        reviewer = self.s.f.results[0]['reviewer_ref']
        del registry['reviewers'][reviewer]
        self.blocked(self.s.review(self.s.provider(registry=registry)), 'reviewer_not_registered')
        registry = deepcopy(self.s.registry)
        registry['reviewers'][reviewer]['status'] = 'revoked'
        self.blocked(self.s.review(self.s.provider(registry=registry)), 'reviewer_revoked')

    def test_grants_must_cover_attribute_rule_process_and_gate(self):
        reviewer = self.s.f.results[0]['reviewer_ref']
        gate_reviewer = next(g['reviewer_ref'] for g in self.s.f.gates if g['status'] != 'pending')
        for mutate, code in (
                (lambda g: g['g:' + reviewer]['attributes'].pop(), 'review_not_authorized'),
                (lambda g: g['g:' + reviewer].update(rules=[]), 'review_not_authorized'),
                (lambda g: g['g:' + reviewer].update(process_ref='test://other-process'), 'review_not_authorized'),
                (lambda g: g['g:' + reviewer].update(status='revoked'), 'grant_revoked'),
                (lambda g: g['g:' + gate_reviewer].update(
                    attributes=[a for a in g['g:' + gate_reviewer]['attributes'] if a[0] != 'gate']), 'gate_not_authorized')):
            registry = deepcopy(self.s.registry)
            mutate(registry['grants'])
            self.blocked(self.s.review(self.s.provider(registry=registry)), code)

    def test_pins_scopes_and_hashes(self):
        self.blocked(self.s.review(self.s.provider(pin='0' * 64)), 'registry_hash_mismatch')
        other = deepcopy(self.s.f.input['photo_scope']); other['estimate_id'] = 'someone-else'
        self.blocked(self.s.review(self.s.provider(), expected_photo_scope=other), 'registered_scope_mismatch')
        self.blocked(self.s.review(self.s.provider(input_bytes=self.s.input_bytes + b' ')), 'registered_hash_mismatch')
        registry = deepcopy(self.s.registry)
        registry['sources']['tfda-snapshot']['source_url'] = 'https://example.org/other'
        self.blocked(self.s.review(self.s.provider(registry=registry)), 'source_version_mismatch')

    def test_time_rules(self):
        registry = deepcopy(self.s.registry)
        registry['contexts']['ctx-1']['registered_at'] = '2026-11-11T00:00:00+08:00'  # before the reviews
        self.blocked(self.s.review(self.s.provider(registry=registry)), 'review_time_untrusted')
        late = self.s.provider(clock=lambda: datetime(2026, 12, 2, tzinfo=TZ))
        self.blocked(self.s.review(late), 'review_expired')
        times = iter([NOW, NOW - timedelta(seconds=1)])
        provider = self.s.provider(clock=lambda: next(times))
        self.assertEqual(self.s.review(provider).provider_gate, 'passed')
        self.blocked(self.s.review(provider), 'review_time_untrusted')

    def test_pending_gate_passes_authorization_but_blocks_energy(self):
        fixture = SyntheticPhotoReview()
        fixture.gates[0] = fixture.gate(fixture.gates[0]['gate'], status='pending')
        fixture.seal()
        setup = Setup(fixture)
        result = setup.review(setup.provider())
        self.assertEqual(result.provider_gate, 'passed', result.issues)
        self.assertIn('gate_' + fixture.gates[0]['gate'] + '_pending', result.build['reasons'])
        self.assertEqual(fixture.energy(result.build['basis'])['items'][0]['status'], 'unavailable')

if __name__ == '__main__':
    unittest.main()
