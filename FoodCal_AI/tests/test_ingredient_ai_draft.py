"""No private images/revisions required: every workspace here is synthetic."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from PIL import Image
from tools.ingredient_pilot import SPEC, file_hash
from tools.ingredient_box_edit import edit_box, write_revision
from tools.ingredient_ai_draft import (load_draft, edit_candidate, decide, save_pending,
                                      latest_hash, record_box_change)


class DraftFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Canonical like production ROOT (resolve()); macOS temp dirs sit behind /var -> /private/var.
        self.root = Path(self.temp.name).resolve()
        self.ws = self.root / 'work' / 'synthetic-pilot'
        (self.ws / 'annotations').mkdir(parents=True)
        self.spec_path = SPEC
        self.spec = json.loads(SPEC.read_text(encoding='utf-8'))
        image = self.ws / 'synthetic.jpg'
        Image.new('RGB', (128, 96), '#556677').save(image)
        self.item = {'pilot_image_id': 'synthetic-001', 'image_sha256': file_hash(image),
                     'image_path': str(image), 'source_id': 'synthetic-test-only',
                     'dimensions': [128, 96], 'sampling_target': 'synthetic',
                     'rights_status': 'review_only_unverified', 'training_eligible': False}
        (self.ws / 'manifest.json').write_text(json.dumps({'spec_sha256': file_hash(SPEC),
                                                        'items': [self.item]}), encoding='utf-8')
        self.annotation = {'pilot_image_id': 'synthetic-001', 'image_sha256': self.item['image_sha256'],
                           'source_id': self.item['source_id'], 'spec_version': self.spec['spec_version'],
                           'classes_checked': [0], 'annotator': 'human-original',
                           'independent_review': {'status': 'accepted', 'reviewer': 'other-human'},
                           'boxes': []}
        candidates = []
        for cls, coords in enumerate(([2, 3, 20, 30], [30, 4, 60, 30], [70, 5, 120, 50])):
            box = {'class_id': cls, 'bounding_box': coords, 'visibility': 'partial',
                   'occlusion': 'unknown', 'truncation': 'none',
                   'uncertainty_reason': 'synthetic uncertainty only',
                   'granularity': self.spec['classes'][cls]['granularity']}
            candidates.append(dict(box, proposal_id=f'p{cls}', class_name=self.spec['classes'][cls]['name']))
            manual = deepcopy(box)
            manual.update(annotation_id=f'h{cls}', bounding_box=[v+1 for v in coords],
                          semantic_review_status='accepted_for_pilot_annotation')
            self.annotation['boxes'].append(manual)
        self.old_path = write_revision(self.ws / 'annotations', self.annotation, None)
        self.old_bytes = self.old_path.read_bytes()
        self.parent_hash = file_hash(self.old_path)
        self.proposal = {'schema_version': 'ai_ingredient_proposal_v0', 'status': 'draft',
                         'training_eligible': False, 'pilot_image_id': self.item['pilot_image_id'],
                         'source_id': self.item['source_id'], 'dimensions': self.item['dimensions'],
                         'image_sha256': self.item['image_sha256'], 'spec_version': self.spec['spec_version'],
                         'spec_sha256': file_hash(SPEC), 'human_revision_sha256': self.parent_hash,
                         'boxes': candidates, 'model_execution': {'kind': 'synthetic/test_only'}}
        self.path = self.ws / 'proposal.json'
        self.write_proposal()

    def write_proposal(self):
        self.path.write_text(json.dumps(self.proposal), encoding='utf-8')

    def load(self):
        return load_draft(self.path, workspace=self.ws, item=self.item, spec_path=SPEC,
                          parent_hash=self.parent_hash, annotation=self.annotation)

    def decide(self, annotation, draft, key, action, **kwargs):
        return decide(annotation, draft, key, action, actor='synthetic-human',
                      dimensions=self.item['dimensions'], spec=self.spec, **kwargs)

    def finish(self):
        draft = self.load()
        annotation, draft = self.decide(self.annotation, draft, 'p0', 'replace', target_id='h0')
        annotation, draft = self.decide(annotation, draft, 'p1', 'add')
        return self.decide(annotation, draft, 'p2', 'reject')

    def save(self, annotation, draft, **kwargs):
        return save_pending(self.ws, annotation, draft, item=self.item, spec_path=SPEC,
                            parent_hash=kwargs.get('parent_hash', self.parent_hash),
                            actor='synthetic-human', classes_checked=kwargs.get('checked', [0]))


class AIDraftTests(DraftFixture):
    def test_equal_clock_times_still_order_revisions_by_save(self):
        class FrozenTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2030, 1, 1, tzinfo=timezone.utc)
        with patch('tools.ingredient_box_edit.datetime', FrozenTime):
            first = write_revision(self.ws/'annotations', self.annotation, self.parent_hash)
            second = write_revision(self.ws/'annotations', self.annotation, file_hash(first))
        self.assertGreater(second.name, first.name)
        self.assertEqual(latest_hash(self.ws, self.item['pilot_image_id']), file_hash(second))
        self.assertEqual(self.old_path.read_bytes(), self.old_bytes)

    def test_import_does_not_change_manual_data_or_class_checks(self):
        before = deepcopy(self.annotation)
        draft = self.load()
        self.assertEqual(self.annotation, before)
        self.assertEqual(draft['original_proposal'], self.proposal)
        self.assertEqual(draft['proposal_sha256'], file_hash(self.path))
        self.assertEqual(draft['decisions'], {})
        self.assertEqual(self.old_path.read_bytes(), self.old_bytes)

    def test_scope_spec_and_revision_hash_mismatch_blocked(self):
        original = deepcopy(self.proposal)
        for key, value in [('pilot_image_id', 'other'), ('source_id', 'wrong'),
                           ('image_sha256', 'b'*64), ('dimensions', [96, 128]),
                           ('spec_sha256', 'b'*64), ('human_revision_sha256', None)]:
            with self.subTest(key=key):
                self.proposal = deepcopy(original); self.proposal[key] = value; self.write_proposal()
                with self.assertRaises(ValueError): self.load()

    def test_actual_image_hash_is_checked(self):
        Path(self.item['image_path']).write_bytes(b'changed synthetic content')
        with self.assertRaisesRegex(ValueError, 'image_hash_mismatch'): self.load()

    def test_latest_revision_checked_on_import(self):
        write_revision(self.ws/'annotations', self.annotation, self.parent_hash)
        with self.assertRaisesRegex(ValueError, 'stale_human_revision'): self.load()

    def test_invalid_candidate_rejected_without_side_effects(self):
        original = deepcopy(self.proposal)
        for field, value in [('class_id', True), ('class_id', 7), ('bounding_box', [1, 1, 200, 30]),
                              ('bounding_box', [1, 1, 1, 2]), ('bounding_box', [1, 1, float('nan'), 5]),
                              ('visibility', 'fake'), ('occlusion', 'covered'),
                              ('truncation', 'false'), ('uncertainty_reason', ''),
                              ('granularity', 'dish'), ('class_name', 'dish')]:
            with self.subTest(field=field, value=value):
                self.proposal = deepcopy(original); self.proposal['boxes'][0][field] = value; self.write_proposal()
                with self.assertRaises(ValueError): self.load()
        self.assertEqual(self.old_path.read_bytes(), self.old_bytes)

    def test_duplicate_ids_and_fake_approval_blocked(self):
        original = deepcopy(self.proposal)
        self.proposal['boxes'][1]['proposal_id'] = 'p0'; self.write_proposal()
        with self.assertRaisesRegex(ValueError, 'proposal_id'): self.load()
        for key, value in [('status', 'accepted'), ('training_eligible', True)]:
            self.proposal = deepcopy(original); self.proposal[key] = value; self.write_proposal()
            with self.assertRaisesRegex(ValueError, 'remain_draft'): self.load()

    def test_replace_preserves_id_other_boxes_and_records_original(self):
        draft = self.load(); original = deepcopy(self.annotation)
        annotation, updated = self.decide(self.annotation, draft, 'p0', 'replace', target_id='h0')
        self.assertEqual(self.annotation, original)
        self.assertEqual(len(annotation['boxes']), 3)
        self.assertEqual(annotation['boxes'][0]['annotation_id'], 'h0')
        self.assertEqual(annotation['boxes'][1:], original['boxes'][1:])
        self.assertEqual(updated['decisions']['p0']['before'], original['boxes'][0])
        self.assertEqual(updated['decisions']['p0']['after'], annotation['boxes'][0])
        self.assertEqual(annotation['classes_checked'], [0])

    def test_repeated_accept_and_duplicate_coordinates_blocked(self):
        annotation, draft = self.decide(self.annotation, self.load(), 'p0', 'add')
        with self.assertRaisesRegex(ValueError, 'already_decided'):
            self.decide(annotation, draft, 'p0', 'add')
        fresh = self.load()
        with self.assertRaisesRegex(ValueError, 'duplicate_box'):
            self.decide(annotation, fresh, 'p0', 'add')

    def test_replace_requires_explicit_valid_target(self):
        for target in (None, 'unknown'):
            with self.assertRaisesRegex(ValueError, 'replacement_target_required'):
                self.decide(self.annotation, self.load(), 'p0', 'replace', target_id=target)

    def test_edit_candidate_does_not_change_original_or_manual_boxes(self):
        draft = self.load(); before = deepcopy(draft)
        edited = edit_candidate(draft, 'p0', {'class_id': 2, 'bounding_box': [1, 2, 15, 20]},
                                actor='synthetic-human', dimensions=[128, 96], spec=self.spec)
        self.assertEqual(draft, before)
        self.assertEqual(edited['original_proposal'], self.proposal)
        self.assertEqual(edited['candidates']['p0']['granularity'], 'visible_contiguous_group')
        self.assertEqual(edited['candidates']['p0']['class_name'], 'cooked_noodle_region')
        self.assertEqual(edited['events'][0]['action'], 'edit_candidate')

    def test_reject_has_no_box_side_effect(self):
        annotation, draft = self.decide(self.annotation, self.load(), 'p0', 'reject')
        self.assertEqual(annotation, self.annotation)
        self.assertIsNone(draft['decisions']['p0']['after'])
        self.assertEqual(len(list((self.ws/'annotations').glob('*.json'))), 1)

    def test_save_requires_all_candidates_processed(self):
        with self.assertRaisesRegex(ValueError, 'unresolved_ai_candidates'):
            self.save(self.annotation, self.load())
        self.assertEqual(len(list((self.ws/'annotations').glob('*.json'))), 1)

    def test_saved_pending_reopens_with_provenance_and_no_training_upgrade(self):
        annotation, draft = self.finish(); path = self.save(annotation, draft)
        reopened = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(len(reopened['boxes']), 4)
        self.assertEqual(reopened['classes_checked'], [0])
        self.assertEqual(reopened['independent_review'], {'status': 'pending', 'reviewer': None})
        self.assertFalse(reopened['training_eligible'])
        self.assertTrue(all(b['semantic_review_status'] == 'pending' for b in reopened['boxes']))
        self.assertEqual(reopened['ai_imports'][0]['original_proposal'], self.proposal)
        self.assertEqual(reopened['parent_revision_sha256'], self.parent_hash)
        self.assertEqual(self.old_path.read_bytes(), self.old_bytes)
        self.assertEqual(latest_hash(self.ws, self.item['pilot_image_id']), file_hash(path))

    def test_stale_or_modified_proposal_blocks_save_without_new_revision(self):
        annotation, draft = self.finish()
        self.path.write_bytes(self.path.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError, 'proposal_hash_mismatch'): self.save(annotation, draft)
        self.write_proposal()
        write_revision(self.ws/'annotations', self.annotation, self.parent_hash)
        with self.assertRaisesRegex(ValueError, 'stale_human_revision'): self.save(annotation, draft)
        self.assertEqual(len(list((self.ws/'annotations').glob('*.json'))), 2)

    def test_edit_after_reopen_keeps_trace_and_original(self):
        annotation, draft = self.finish(); path = self.save(annotation, draft)
        saved = json.loads(path.read_text(encoding='utf-8'))
        before = deepcopy(saved['boxes'][0])
        edited = edit_box(saved, 'h0', {'bounding_box': [1, 2, 19, 29]}, dimensions=[128, 96], spec=self.spec)
        edited, _ = record_box_change(edited, None, before, edited['boxes'][0], 'another-human')
        final = self.save(edited, None, parent_hash=file_hash(path))
        reopened = json.loads(final.read_text(encoding='utf-8'))
        self.assertEqual(reopened['ai_imports'][0]['events'][-1]['action'], 'edit_imported_box')
        self.assertEqual(reopened['ai_imports'][0]['original_proposal'], self.proposal)

    def test_old_proposal_cannot_be_reimported_after_save(self):
        annotation, draft = self.finish(); path = self.save(annotation, draft)
        with self.assertRaisesRegex(ValueError, 'stale_human_revision'):
            load_draft(self.path, workspace=self.ws, item=self.item, spec_path=SPEC,
                       parent_hash=file_hash(path), annotation=json.loads(path.read_text(encoding='utf-8')))

    def test_missing_human_hash_cannot_masquerade_as_no_revision(self):
        self.proposal.pop('human_revision_sha256'); self.write_proposal()
        with self.assertRaisesRegex(ValueError, 'stale_human_revision'): self.load()

    def test_private_path_and_manifest_changes_blocked(self):
        external = self.root / 'outside.json'; external.write_text(json.dumps(self.proposal))
        with self.assertRaisesRegex(ValueError, 'private_workspace'):
            load_draft(external, workspace=self.ws, item=self.item, spec_path=SPEC,
                       parent_hash=self.parent_hash, annotation=self.annotation)
        manifest = json.loads((self.ws/'manifest.json').read_text())
        manifest['items'][0]['source_id'] = 'changed'
        (self.ws/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'manifest_changed'): self.load()


if __name__ == '__main__':
    unittest.main()
