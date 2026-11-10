"""Real Tk widgets; synthetic image/workspace only. No private GT is written."""
from copy import deepcopy
import json
import tkinter as tk
from unittest.mock import patch

from tests.test_ingredient_ai_draft import DraftFixture
from tools.annotate_ingredient_pilot import AnnotationApp
from tools.ingredient_pilot import file_hash


class AIGuiTests(DraftFixture):
    def setUp(self):
        super().setUp()
        self.root_patch = patch('tools.annotate_ingredient_pilot.ROOT', self.root)
        self.root_patch.start(); self.addCleanup(self.root_patch.stop)
        self.info = patch('tools.annotate_ingredient_pilot.messagebox.showinfo').start()
        self.error = patch('tools.annotate_ingredient_pilot.messagebox.showerror').start()
        self.confirm = patch('tools.annotate_ingredient_pilot.messagebox.askyesno', return_value=True).start()
        self.addCleanup(patch.stopall)
        try:
            self.window = tk.Tk()
        except tk.TclError as error:
            self.skipTest('Tk display unavailable: '+str(error))
        self.window.withdraw(); self.addCleanup(self.window.destroy)
        self.app = AnnotationApp(self.window, self.ws, 'synthetic-human', image_id='synthetic-001')
        # Withdrawn windows get no window-system events, so flush idle geometry/redraw only: a full
        # update() on withdrawn windows can spin forever with Tk 8.6.13 on recent macOS.
        self.window.update_idletasks()

    def select_ai(self, index):
        self.app.ai_list.selection_clear(0, 'end'); self.app.ai_list.selection_set(index)
        self.app.select_candidate()

    def test_tk_import_edit_replace_add_reject_save_and_reopen(self):
        app = self.app; before = deepcopy(app.annotation); checks = [v.get() for v in app.checked]
        app.import_draft(self.path); self.window.update_idletasks()
        self.assertEqual(app.annotation, before)
        self.assertEqual([v.get() for v in app.checked], checks)
        self.assertEqual(app.ai_list.size(), 3)
        self.assertTrue(any(app.canvas.itemcget(i, 'dash') for i in app.canvas.find_all() if app.canvas.type(i)=='rectangle'))
        self.select_ai(0); app.target_id.set('h0'); app.decide_candidate('replace')
        self.select_ai(1); app.coordinates[0].set('31'); app.apply_edit(); app.decide_candidate('add')
        self.select_ai(2); app.decide_candidate('reject')
        self.assertEqual(len(list((self.ws/'annotations').glob('*.json'))), 1)
        app.save('pending'); self.window.update_idletasks()
        self.error.assert_not_called()
        self.assertEqual(app.annotation['independent_review']['status'], 'pending')
        self.assertEqual(len(app.annotation['boxes']), 4)
        self.assertEqual(app.annotation['classes_checked'], [0])
        self.assertEqual(len(app.annotation['ai_imports'][0]['decisions']), 3)
        self.assertEqual(app.ai_list.size(), 3)
        self.assertEqual(self.old_path.read_bytes(), self.old_bytes)
        self.select_ai(0)  # Reopened source/audit viewer, not another import.
        self.assertIn('proposal_sha256', app.ai_details.get('1.0', 'end'))

    def test_review_mode_cannot_import_mutate_or_undo(self):
        window = tk.Toplevel(self.window); self.addCleanup(window.destroy)
        app = AnnotationApp(window, self.ws, 'synthetic-reviewer', review=True)
        before = deepcopy(app.annotation)
        app.import_draft(self.path); app.apply_edit(); app.decide_candidate('add'); app.undo()
        app.press(type('Event', (), {'x': 1, 'y': 1})())
        app.release(type('Event', (), {'x': 20, 'y': 30})())
        self.assertEqual(app.annotation, before)
        self.assertIsNone(app.draft)
        self.assertEqual(len(list((self.ws/'annotations').glob('*.json'))), 1)

    def review_app(self):
        window = tk.Toplevel(self.window); window.withdraw(); self.addCleanup(window.destroy)
        return AnnotationApp(window, self.ws, 'synthetic-reviewer', review=True)

    def test_review_save_rechecks_the_parent_inside_the_lock(self):
        import tools.annotate_ingredient_pilot as gui
        from tools.ingredient_box_edit import write_revision
        app, original = self.review_app(), gui.check_current

        def check_then_concurrent_save(*args, **kwargs):
            original(*args, **kwargs)
            write_revision(self.ws/'annotations', self.annotation, self.parent_hash)  # another process saves now

        with patch('tools.annotate_ingredient_pilot.check_current', side_effect=check_then_concurrent_save):
            app.save('rejected')
        self.assertIn('stale_human_revision', self.error.call_args.args[1])
        self.info.assert_not_called()
        self.assertEqual(len(list((self.ws/'annotations').glob('*.json'))), 2)

    def test_review_save_lock_timeout_is_blocked_not_raised(self):
        app = self.review_app()
        with patch('tools.annotate_ingredient_pilot.write_revision', side_effect=TimeoutError('revision_lock_timeout')):
            app.save('rejected')
        self.assertIn('revision_lock_timeout', self.error.call_args.args[1])
        self.info.assert_not_called()
        self.assertEqual(len(list((self.ws/'annotations').glob('*.json'))), 1)

    def test_save_unresolved_blocked_and_cancel_navigation_preserves_session(self):
        self.app.import_draft(self.path)
        self.app.save('pending')
        self.assertIn('unresolved_ai_candidates', self.error.call_args.args[1])
        self.confirm.return_value = False
        self.app.move(1)
        self.assertIsNotNone(self.app.draft)
        self.assertEqual(len(list((self.ws/'annotations').glob('*.json'))), 1)

    def test_unsaved_field_edit_is_not_silently_accepted(self):
        self.app.import_draft(self.path); self.select_ai(0)
        self.app.coordinates[0].set('4')
        self.app.decide_candidate('add')
        self.assertIn('apply_edits_first', self.error.call_args.args[1])
        self.assertEqual(self.app.draft['decisions'], {})

    def test_save_detects_concurrent_revision_change(self):
        from tools.ingredient_box_edit import write_revision
        self.app.import_draft(self.path)
        write_revision(self.ws/'annotations', self.annotation, self.parent_hash)
        self.app.save('pending')
        self.assertIn('stale_human_revision', self.error.call_args.args[1])
        self.assertEqual(len(list((self.ws/'annotations').glob('*.json'))), 2)

    def test_duplicate_click_is_blocked_and_does_not_duplicate_box(self):
        self.app.import_draft(self.path); self.select_ai(0); self.app.decide_candidate('add')
        self.app.decide_candidate('add')
        self.assertIn('candidate_already_decided', self.error.call_args.args[1])
        self.assertEqual(len(self.app.annotation['boxes']), 4)

    def test_reject_discards_unapplied_fields_and_can_save(self):
        self.app.import_draft(self.path)
        for index in range(3):
            self.select_ai(index)
            self.app.coordinates[0].set('not a coordinate')
            self.app.decide_candidate('reject')
        self.app.save('pending')
        self.error.assert_not_called()
        self.assertEqual(self.app.annotation['boxes'][0]['bounding_box'], self.annotation['boxes'][0]['bounding_box'])
        self.assertEqual(self.app.annotation['independent_review']['status'], 'pending')

    def test_no_prior_manual_revision_can_import_and_save_first_annotation(self):
        # Only a temporary synthetic file is removed to model pilot-029/032.
        self.old_path.unlink()
        self.proposal['human_revision_sha256'] = None; self.write_proposal()
        self.app.load(); self.app.import_draft(self.path)
        self.assertEqual(self.app.annotation['boxes'], [])
        self.assertFalse(any(v.get() for v in self.app.checked))
        self.select_ai(0); self.app.decide_candidate('add')
        for index in (1, 2):
            self.select_ai(index); self.app.decide_candidate('reject')
        self.app.save('pending')
        self.error.assert_not_called()
        self.assertEqual(len(self.app.annotation['boxes']), 1)
        self.assertEqual(self.app.annotation['classes_checked'], [])
        self.assertIsNone(self.app.annotation['parent_revision_sha256'])
