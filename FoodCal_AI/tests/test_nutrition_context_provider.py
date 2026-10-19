"""Owner bootstrap is separate from public request; all evidence is synthetic."""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import unittest
from unittest.mock import patch

from tests.test_nutrition_review import SyntheticReview, encoded
from src.nutrition.context_provider import LocalContextProvider, RegisteredReview


NOW = datetime.fromisoformat('2026-10-12T01:00:00+08:00')
START = '2026-10-11T00:00:00+08:00'
END = '2026-10-13T00:00:00+08:00'


class OwnerFixture:
    def __init__(self, review=None):
        self.f = review or SyntheticReview()
        f = self.f
        self.now = NOW
        self.bundle = RegisteredReview(encoded(f.input), f.context)
        self.registry = {
            'profile': 'foodcal.local_review_registry.v0', 'authority_ref': f.anchor.authority_ref,
            'revision': 1,
            'sources': {'s1': {'status': 'active', 'candidate_scope': deepcopy(f.input['candidate_scope']),
                               'official_dataset_version': 'synthetic-v1', 'source_url': 'test://synthetic',
                               'retrieved_at_utc': 'TEST_TIME', 'evidence_ref': 'test://owner/source-registration',
                               'purposes': ['diagnostic/test_only']}},
            'reviewers': {'test://reviewer': {'reviewer_id': 'test://reviewer', 'status': 'active',
                                             'identity_evidence_ref': 'test://owner/synthetic-identity'}},
            'grants': {'g1': {'reviewer_id': 'test://reviewer', 'status': 'active',
                              'valid_from': START, 'expires_at': END,
                              'recipe_scope': deepcopy(f.input['recipe_scope']),
                              'candidate_scope': deepcopy(f.input['candidate_scope']),
                              'source_registration_id': 's1', 'purpose': 'diagnostic/test_only',
                              'process_ref': 'test://synthetic-review-process',
                              'attributes': [[r['domain'], r['attribute'], r['nutrient_key']] for r in f.results],
                              'rules': list({(r['rule_id'], r['rule_version']) for r in f.results})}},
            'contexts': {'c1': {'status': 'active', 'valid_from': START, 'expires_at': END,
                                'registered_at': '2026-10-12T00:30:00+08:00',
                                'input_sha256': sha256(self.bundle.input_bytes).hexdigest(),
                                'context_sha256': f.anchor.context_sha256,
                                'recipe_scope': deepcopy(f.input['recipe_scope']),
                                'candidate_scope': deepcopy(f.input['candidate_scope']),
                                'basis_target': deepcopy(f.input['basis_target']),
                                'purpose': 'diagnostic/test_only', 'source_registration_id': 's1',
                                'grant_ids': ['g1']}}}
        self.boot()

    def boot(self, expected_revision=1, reader=None):
        # Trusted owner action only. Attack tests modify requests/data AFTER this call.
        self.raw = encoded(self.registry)
        self.provider = LocalContextProvider(registry_reader=reader or (lambda: self.raw),
                                              registry_sha256=sha256(self.raw).hexdigest(),
                                              authority_ref=self.f.anchor.authority_ref,
                                              policy_revision=expected_revision,
                                              reviews={'c1': self.bundle}, clock=lambda: self.now)

    def call(self, **changes):
        args = {'expected_recipe_scope': deepcopy(self.f.input['recipe_scope']),
                'expected_candidate_scope': deepcopy(self.f.input['candidate_scope']),
                'basis_target': deepcopy(self.f.input['basis_target'])}
        args.update(changes)
        return self.provider.review_registered('c1', **args)


class ContextProviderTests(unittest.TestCase):
    def setUp(self):
        self.o = OwnerFixture()

    def blocked(self, code, result=None):
        with patch('src.nutrition.context_provider.validate_review') as validator, \
                patch('src.nutrition.context_provider.aggregate_review') as aggregator:
            result = result() if callable(result) else result or self.o.call()
            self.assertEqual(result.provider_gate, 'blocked')
            self.assertEqual(result.issues[0].code, code)
            self.assertIsNone(result.decision)
            self.assertFalse(result.nutrition_eligible)
            validator.assert_not_called()
            aggregator.assert_not_called()

    def test_01_registered_review_remains_test_only(self):
        result = self.o.call()
        self.assertEqual(result.provider_gate, 'passed')
        self.assertTrue(result.validation.valid, result.issues)
        self.assertEqual(result.decision['identity_decision'], 'compatible')
        self.assertFalse(result.decision['nutrition_eligible'])
        self.assertEqual(result.usage, 'local_controlled/test_only')
        self.assertFalse(result.submitter_authenticated)

    def test_02_unknown_id_and_request_cannot_supply_anchor(self):
        self.blocked('context_not_registered', lambda: self.o.provider.review_registered(
            '../unregistered', expected_recipe_scope={}, expected_candidate_scope={}, basis_target={}))
        with self.assertRaises(TypeError):
            self.o.call(trust_anchor=self.o.f.anchor)
        with self.assertRaises(TypeError):
            self.o.call(approved=True)

    def test_03_reviewer_revoked_with_unchanged_evidence(self):
        self.o.registry['reviewers']['test://reviewer']['status'] = 'revoked'
        self.o.boot()
        self.blocked('reviewer_revoked')

    def test_04_revoked_and_superseded_context(self):
        for status in ('revoked', 'superseded'):
            self.o.registry['contexts']['c1']['status'] = status
            self.o.boot()
            self.blocked('context_inactive')

    def test_05_expiration_and_future_review(self):
        self.o.now = datetime.fromisoformat(END)
        self.blocked('review_expired')
        self.o.now = datetime.fromisoformat(START)
        self.blocked('review_time_untrusted')  # Registration has not happened.
        self.o = OwnerFixture()
        for row in self.o.f.results:
            row['reviewed_at'] = '2026-10-14T00:00:00+08:00'
        self.o.f.seal()
        self.o.bundle = RegisteredReview(encoded(self.o.f.input), self.o.f.context)
        self.o.registry['contexts']['c1']['context_sha256'] = self.o.f.anchor.context_sha256
        self.o.boot()
        self.blocked('review_time_untrusted')

    def test_06_bundle_and_anchor_replacement_cannot_replace_registry_pin(self):
        self.o.f.row('part')['reason_code'] = 'attacker'
        self.o.f.seal()
        self.o.bundle = RegisteredReview(encoded(self.o.f.input), self.o.f.context)
        self.o.boot()  # Owner keeps ORIGINAL context pin in registry.
        self.blocked('registered_hash_mismatch')

    def test_07_cross_ingredient_and_input_scope(self):
        for key in ('ingredient_id', 'input_id', 'recipe_text_sha256'):
            scope = deepcopy(self.o.f.input['recipe_scope'])
            scope[key] = 'another'
            self.blocked('registered_scope_mismatch', lambda: self.o.call(expected_recipe_scope=scope))

    def test_08_source_updated_same_food_id(self):
        self.o.registry['sources']['s1']['candidate_scope']['sample_sha256'] = 'c' * 64
        self.o.boot()
        self.blocked('source_version_mismatch')
        self.o = OwnerFixture()
        self.o.registry['sources']['s1']['official_dataset_version'] = 'synthetic-v2'
        self.o.boot()
        self.blocked('source_version_mismatch')

    def test_09_forged_reviewer_even_with_registered_context_bytes(self):
        f = SyntheticReview()
        for row in f.results:
            row['reviewer_ref'] = 'test://forged'
        f.seal()
        self.o = OwnerFixture(f)  # Exact bytes pinned, identity NEVER registered by caller.
        self.blocked('reviewer_not_registered')

    def test_10_attribute_rule_and_process_permissions(self):
        for field in ('attributes', 'rules', 'process_ref', 'recipe_scope'):
            self.o = OwnerFixture()
            grant = self.o.registry['grants']['g1']
            if field == 'attributes':
                grant[field] = [a for a in grant[field] if a[0] == 'identity']
            elif field == 'rules':
                grant[field] = []
            elif field == 'process_ref':
                grant[field] = 'test://unauthorized-process'
            else:
                grant[field]['ingredient_id'] = 'another'
            self.o.boot()
            self.blocked('review_not_authorized')

    def test_11_revision_target_and_during_review_change(self):
        self.o.boot(expected_revision=2)
        self.blocked('policy_revision_untrusted')
        self.o = OwnerFixture()
        target = deepcopy(self.o.f.input['basis_target'])
        target['required_nutrients'].append({'original_name': '粗脂肪', 'expected_unit': 'g'})
        self.blocked('review_target_mismatch', lambda: self.o.call(basis_target=target))
        original = self.o.raw
        calls = []
        def reader():
            calls.append(1)
            return original if len(calls) == 1 else original + b' '
        self.o.boot(reader=reader)
        result = self.o.call()
        self.assertEqual(result.issues[0].code, 'registry_hash_mismatch')
        self.assertIsNone(result.decision)

    def test_12_pdf_still_blocked_after_authorization(self):
        f = SyntheticReview()
        f.attachments['document'] = b'%PDF-1.7 synthetic'
        digest = sha256(f.attachments['document']).hexdigest()
        f.catalog['document']['sha256'] = digest
        for ref in f.input['attachment_refs']['evidence_files']:
            if ref['attachment_id'] == 'document':
                ref['sha256'] = digest
        for evidence in f.input['evidence']:
            if evidence['attachment_ref'] == 'document':
                evidence['attachment_sha256'] = digest
        f.seal()
        self.o = OwnerFixture(f)
        result = self.o.call()
        self.assertEqual(result.provider_gate, 'passed')
        self.assertEqual(result.validation.state, 'blocked')
        self.assertEqual(result.issues[0].code, 'unsupported_evidence_format')
        self.assertIsNone(result.decision)

    def test_registry_modified_after_bootstrap(self):
        self.o.registry['reviewers']['test://forged'] = {'status': 'active'}
        self.o.raw = encoded(self.o.registry)
        self.blocked('registry_hash_mismatch')

    def test_replay_valid_then_revoked_under_new_policy(self):
        self.assertTrue(self.o.call().validation.valid)
        self.assertTrue(self.o.call().validation.valid)  # Reproducible review, not a one-use token.
        self.o.registry['contexts']['c1']['status'] = 'revoked'
        self.o.registry['revision'] = 2
        self.o.boot(expected_revision=2)
        self.blocked('context_inactive')
        old = deepcopy(self.o.registry)
        old['revision'] = 1
        self.o.raw = encoded(old)
        self.blocked('registry_hash_mismatch')

    def test_grant_revocation_and_expiry(self):
        self.o.registry['grants']['g1']['status'] = 'revoked'
        self.o.boot()
        self.blocked('grant_revoked')
        self.o.registry['grants']['g1']['status'] = 'active'
        self.o.registry['grants']['g1']['expires_at'] = NOW.isoformat()
        self.o.boot()
        self.blocked('review_expired')

    def test_window_start_inclusive_and_end_exclusive(self):
        self.o.registry['contexts']['c1']['valid_from'] = NOW.isoformat()
        self.o.boot()
        self.assertTrue(self.o.call().validation.valid)
        self.o.now = datetime.fromisoformat(END) - timedelta(microseconds=1)
        self.assertTrue(self.o.call().validation.valid)
        self.o.now += timedelta(microseconds=1)
        self.blocked('review_expired')

    def test_untrusted_and_backwards_clock(self):
        self.o.now = NOW.replace(tzinfo=None)
        self.blocked('review_time_untrusted')
        self.o.now = NOW
        self.assertTrue(self.o.call().validation.valid)
        self.o.now -= timedelta(seconds=1)
        self.blocked('review_time_untrusted')

    def test_owner_native_objects_copied_and_output_isolated(self):
        before = deepcopy(self.o.f.attachments)
        self.o.f.attachments['candidate']['nutrition_eligible'] = True
        result = self.o.call()
        self.assertTrue(result.validation.valid)
        result.decision['attribute_results'][0]['status'] = 'mismatch'
        self.assertEqual(self.o.call().decision['identity_decision'], 'compatible')
        self.assertFalse(before['candidate']['nutrition_eligible'])

    def test_missing_evidence_not_aggregated(self):
        f = SyntheticReview()
        del f.attachments['sample']
        self.o = OwnerFixture(f)
        with patch('src.nutrition.context_provider.aggregate_review') as aggregate:
            result = self.o.call()
            self.assertFalse(result.validation.valid)
            self.assertIsNone(result.decision)
            aggregate.assert_not_called()

    def test_malformed_or_unavailable_registry_fails_closed(self):
        for value in (b'{', b'{"x":1,"x":2}'):
            self.o.raw = value
            self.blocked('registry_hash_mismatch')
        self.o = OwnerFixture()
        self.o.registry['contexts']['c1']['grant_ids'] = ['absent']
        self.o.boot()
        self.blocked('registry_or_bundle_invalid')

    def test_review_before_grant_is_not_authorized(self):
        self.o.registry['grants']['g1']['valid_from'] = '2026-10-12T00:30:00+08:00'
        self.o.boot()
        self.blocked('review_not_authorized')

    def test_source_revocation_and_wrong_food_request(self):
        self.o.registry['sources']['s1']['status'] = 'revoked'
        self.o.boot()
        self.blocked('source_inactive')
        self.o = OwnerFixture()
        scope = deepcopy(self.o.f.input['candidate_scope'])
        scope['food_id'] = 'OTHER'
        self.blocked('registered_scope_mismatch', lambda: self.o.call(expected_candidate_scope=scope))

    def test_missing_quantity_and_unknown_version_remain_unresolved(self):
        f = SyntheticReview(missing_quantity=True, version=None)
        f.row('recipe_declared_mass').update(status='unresolved', reason_code='recipe_mass_missing')
        f.row('data_version').update(status='unresolved', reason_code='official_version_unknown')
        f.seal()
        self.o = OwnerFixture(f)
        self.o.registry['sources']['s1']['official_dataset_version'] = None
        self.o.boot()
        result = self.o.call()
        self.assertTrue(result.validation.valid, result.issues)
        self.assertEqual(result.decision['basis_decision'], 'needs_review')
        self.assertIn('recipe_mass_missing', result.decision['blocking_reasons'])
        self.assertIn('official_version_unknown', result.decision['blocking_reasons'])
        self.assertIsNone(f.parsing['ingredients'][0]['quantity'])

    def test_pinned_but_malformed_registry_and_duplicate_json(self):
        self.o.registry['sources']['s1']['purposes'] = 'diagnostic/test_only'
        self.o.boot()
        self.blocked('registry_invalid')
        raw = b'{"profile":1,"profile":2}'
        self.o.provider = LocalContextProvider(registry_reader=lambda: raw,
            registry_sha256=sha256(raw).hexdigest(), authority_ref='test://owner', policy_revision=1,
            reviews={}, clock=lambda: NOW)
        self.blocked('registry_or_bundle_invalid')

    def test_registry_read_failure_does_not_leak_path(self):
        def fail():
            raise OSError('PRIVATE_PATH_AND_SECRET')
        self.o.boot(reader=fail)
        self.blocked('registry_or_bundle_invalid')
        self.assertNotIn('PRIVATE_PATH', str(self.o.call()))


if __name__ == '__main__':
    unittest.main()
