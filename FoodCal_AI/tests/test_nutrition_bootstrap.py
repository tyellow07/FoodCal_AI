"""Synthetic lifecycle fixtures; no OS permission or identity-authentication claims."""
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.nutrition.bootstrap import (BootstrapLoader, DeploymentConfig, HighWatermark, controlled_file_reader)
from tests.test_nutrition_context_provider import OwnerFixture, NOW, END
from tests.test_nutrition_review import encoded


class ControlledStore:
    def __init__(self, value):
        self.value = value

    def read(self):
        return self.value


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.o = OwnerFixture()
        self.code = sha256(b'SYNTHETIC approved code profile').hexdigest()
        self.config = DeploymentConfig('test-deployment-1', 1, sha256(self.o.raw).hexdigest(),
                                       self.o.f.anchor.authority_ref, self.code, 'synthetic-v0')
        self.store = ControlledStore(HighWatermark(self.config, NOW - timedelta(minutes=1)))
        self.loader = self.make()

    def make(self, **overrides):
        args = dict(config_reader=lambda: self.config, high_water=self.store,
                    registry_reader=lambda: self.o.raw, clock=lambda: self.o.now,
                    code_sha256=self.code, resolver_id='synthetic-v0',
                    review_resolver=lambda config: {'c1': self.o.bundle})
        args.update(overrides)
        return BootstrapLoader(**args)

    def call(self, loader=None, **overrides):
        args = dict(expected_recipe_scope=self.o.f.input['recipe_scope'],
                    expected_candidate_scope=self.o.f.input['candidate_scope'], basis_target=self.o.f.input['basis_target'])
        args.update(overrides)
        return (loader or self.loader).review_registered('c1', **args)

    def blocked(self, code):
        with patch('src.nutrition.bootstrap.LocalContextProvider') as provider:
            r = self.call()
            self.assertEqual((r.bootstrap_gate, r.issues[0].code), ('blocked', code))
            self.assertIsNone(r.provider_result)
            provider.assert_not_called()
            self.assertFalse(r.nutrition_eligible)

    def activate(self):
        candidate = encoded(self.o.registry)
        if (sha256(candidate).hexdigest() != self.config.registry_sha256
                and self.o.registry['revision'] <= self.config.revision):
            self.o.registry['revision'] = self.config.revision + 1
        self.o.raw = encoded(self.o.registry)
        self.config = replace(self.config, revision=self.o.registry['revision'],
                              deployment_id='test-deployment-' + str(self.o.registry['revision']),
                              registry_sha256=sha256(self.o.raw).hexdigest())
        self.store.value = HighWatermark(self.config, NOW - timedelta(minutes=1))

    def test_01_valid_replay_and_audit(self):
        for _ in range(2):
            r = self.call()
            self.assertEqual(r.bootstrap_gate, 'passed')
            self.assertTrue(r.provider_result.validation.valid)
            self.assertFalse(r.provider_result.decision['nutrition_eligible'])
            self.assertFalse(r.submitter_authenticated)
            self.assertEqual(r.audit['registry_sha256'], self.config.registry_sha256)
            self.assertEqual(r.audit['recipe_scope'], self.o.f.input['recipe_scope'])

    def test_02_registry_only_modified(self):
        self.o.raw += b' '
        self.blocked('registry_hash_mismatch')

    def test_03_request_cannot_choose_pin_or_minimum(self):
        for key in ('registry_sha256', 'minimum_revision', 'root', 'clock', 'approved'):
            with self.assertRaises(TypeError):
                self.call(**{key: 'untrusted'})
        self.config = replace(self.config, registry_sha256='a' * 64)
        self.blocked('bootstrap_revision_collision')

    def test_04_old_registry_with_current_deployment(self):
        old = self.o.raw
        self.o.registry['revision'] = 2
        self.activate()
        self.o.raw = old
        self.blocked('registry_hash_mismatch')

    def test_05_old_registry_and_old_pin_below_protected_water(self):
        old = self.config
        self.o.registry['revision'] = 2
        self.activate()
        self.config = old
        self.o.registry['revision'] = 1
        self.o.raw = encoded(self.o.registry)
        self.blocked('bootstrap_rollback')

    def test_06_same_revision_pin_collision(self):
        self.store.value = replace(self.store.value, deployment=replace(self.config, registry_sha256='f' * 64))
        self.blocked('bootstrap_revision_collision')

    def test_07_interrupted_update_missing_or_corrupt_state(self):
        self.config = replace(self.config, revision=2)
        self.blocked('deployment_state_inconsistent')
        self.store.value = None
        self.blocked('deployment_state_missing')
        self.store.value = 'CORRUPT'
        self.blocked('deployment_state_missing')

    def test_08_update_and_revocations(self):
        self.assertTrue(self.call().provider_result.validation.valid)
        for table, key, expected in (('contexts', 'c1', 'context_inactive'),
                                     ('reviewers', 'test://reviewer', 'reviewer_revoked'),
                                     ('grants', 'g1', 'grant_revoked'), ('sources', 's1', 'source_inactive')):
            self.o = OwnerFixture()
            self.o.registry[table][key]['status'] = 'revoked'
            self.o.registry['revision'] = self.config.revision + 1
            self.activate()
            r = self.call()
            self.assertEqual(r.bootstrap_gate, 'passed')
            self.assertEqual(r.provider_result.issues[0].code, expected)
            self.assertIsNone(r.provider_result.decision)

    def test_09_old_loader_checks_active_deployment_each_call(self):
        old_loader = self.loader
        self.assertTrue(self.call(old_loader).provider_result.validation.valid)
        self.config = replace(self.config, status='revoked')
        self.store.value = replace(self.store.value, deployment=self.config)
        r = self.call(old_loader)
        self.assertEqual(r.issues[0].code, 'deployment_inactive')
        self.assertIsNone(r.provider_result)

    def test_10_expiry_and_invalid_review_time(self):
        self.o.now = self.o.now.fromisoformat(END)
        self.assertEqual(self.call().provider_result.issues[0].code, 'review_expired')
        self.setUp()
        self.o.registry['grants']['g1']['valid_from'] = '2026-10-12T00:30:00+08:00'
        self.activate()
        self.assertEqual(self.call().provider_result.issues[0].code, 'review_not_authorized')

    def test_11_restart_clock_below_protected_floor(self):
        self.o.now = NOW - timedelta(minutes=2)
        self.loader = self.make()
        self.blocked('bootstrap_time_untrusted')

    def test_12_expiry_during_delivery(self):
        times = iter([NOW, NOW.fromisoformat(END)])
        self.loader = self.make(clock=lambda: next(times))
        r = self.call()
        self.assertEqual(r.issues[0].code, 'review_expired')
        self.assertIsNone(r.provider_result)  # Do not expose the earlier successful decision.

    def test_13_source_and_parsing_scope_updates(self):
        self.o.registry['sources']['s1']['candidate_scope']['sample_sha256'] = 'c' * 64
        self.activate()
        self.assertEqual(self.call().provider_result.issues[0].code, 'source_version_mismatch')
        scope = dict(self.o.f.input['recipe_scope'], ingredient_id='other')
        self.assertEqual(self.call(expected_recipe_scope=scope).provider_result.issues[0].code,
                         'registered_scope_mismatch')

    def test_14_code_and_resolver_must_be_approved(self):
        for field, value in (('code_sha256', 'c' * 64), ('resolver_id', 'unapproved')):
            self.setUp()
            self.config = replace(self.config, **{field: value})
            self.store.value = replace(self.store.value, deployment=self.config)
            self.blocked('deployment_code_untrusted')

    def test_15_pdf_missing_evidence_and_forged_reviewer(self):
        from tests.test_nutrition_review import SyntheticReview
        f = SyntheticReview()
        f.attachments['document'] = b'%PDF-1.7 SYNTHETIC'
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
        self.activate()
        r = self.call()
        self.assertEqual(r.provider_result.issues[0].code, 'unsupported_evidence_format')
        self.assertIsNone(r.provider_result.decision)
        self.setUp()
        f = SyntheticReview()
        del f.attachments['sample']
        self.o.bundle = replace(self.o.bundle, context=f.context)
        self.assertIsNone(self.call().provider_result.decision)
        for row in f.results:
            row['reviewer_ref'] = 'forged'
        f.seal()
        self.o.bundle = replace(self.o.bundle, context=f.context)
        self.o.registry['contexts']['c1']['context_sha256'] = f.anchor.context_sha256
        self.activate()
        self.assertEqual(self.call().provider_result.issues[0].code, 'reviewer_not_registered')

    def test_16_controlled_path_and_no_environment_override(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'registry.json').write_bytes(b'SYNTHETIC')
            reader = controlled_file_reader(root, 'registry.json')
            self.assertEqual(reader(), b'SYNTHETIC')
            with self.assertRaises(ValueError):
                controlled_file_reader(root, '../outside.json')
            with self.assertRaises(ValueError):
                controlled_file_reader(root, root / 'registry.json')
            with patch.dict('os.environ', {'REGISTRY_ROOT': 'untrusted'}):
                self.assertEqual(reader(), b'SYNTHETIC')

    def test_both_config_and_water_replacement_limit(self):
        # A fresh loader with an entirely replaced trusted root cannot discover its history.
        old_config, old_water, old_raw = self.config, self.store.value, self.o.raw
        self.o.registry['revision'] = 2
        self.activate()
        self.assertTrue(self.call().provider_result.validation.valid)
        self.config, self.store.value, self.o.raw = old_config, old_water, old_raw
        self.blocked('bootstrap_rollback')  # Same instance retains observation.
        fresh = self.make()
        self.assertTrue(self.call(fresh).provider_result.validation.valid)  # Explicit limit, not security success.

    def test_changes_during_call_and_no_fallback(self):
        original = self.config
        calls = []
        def read_config():
            calls.append(1)
            return original if len(calls) == 1 else replace(original, status='revoked')
        self.loader = self.make(config_reader=read_config)
        r = self.call()
        self.assertEqual(r.bootstrap_gate, 'blocked')
        self.assertIsNone(r.provider_result)

    def test_resolved_link_escape_blocked_without_os_privilege_dependency(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / 'registry.json').write_bytes(self.o.raw)
            reader = controlled_file_reader(root, 'registry.json')
            self.loader = self.make(registry_reader=reader)
            # Model a symlink/junction resolution outside root; no Windows privilege needed.
            with patch.object(Path, 'resolve', return_value=root.parent / 'outside.json'):
                self.blocked('controlled_path_violation')

    def test_missing_config_wrong_registry_revision_and_unavailable_store(self):
        self.config = None
        self.blocked('deployment_state_missing')
        self.setUp()
        self.o.registry['revision'] = 9
        self.o.raw = encoded(self.o.registry)
        self.config = replace(self.config, registry_sha256=sha256(self.o.raw).hexdigest())
        self.store.value = replace(self.store.value, deployment=self.config)
        self.blocked('deployment_state_inconsistent')
        self.setUp()
        with patch.object(self.store, 'read', side_effect=OSError('PRIVATE_PATH')):
            self.blocked('bootstrap_data_invalid')


if __name__ == '__main__':
    unittest.main()
