"""Cross-process revision lock and atomic publish. Synthetic temp directories and child processes only."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tests.test_ingredient_ai_draft import DraftFixture
from tools.ingredient_box_edit import LOCK_NAME, latest_revision, revision_lock, write_revision
from tools.ingredient_pilot import file_hash

ROOT = Path(__file__).resolve().parents[1]
PRELUDE = 'import json, sys, time\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[1])\n'
HOLDER = PRELUDE + '''
from tools.ingredient_box_edit import revision_lock
with revision_lock(sys.argv[2]):
    print('locked', flush=True)
    sys.stdin.readline()
'''
RACER = PRELUDE + '''
from tools.ingredient_box_edit import write_revision
directory, go = Path(sys.argv[2]), Path(sys.argv[3])
annotation = json.loads((directory.parent / 'annotation.json').read_text(encoding='utf-8'))
print('ready', flush=True)
while not go.exists():
    time.sleep(0.002)
try:
    write_revision(directory, annotation, sys.argv[4], require_latest=True, lock_timeout=30)
    print('saved')
except ValueError as error:
    print(error)
'''
CHAIN = PRELUDE + '''
from tools.ingredient_box_edit import latest_revision, write_revision
from tools.ingredient_pilot import file_hash
directory, remaining = Path(sys.argv[2]), int(sys.argv[3])
annotation = json.loads((directory.parent / 'annotation.json').read_text(encoding='utf-8'))
while remaining:
    latest = latest_revision(directory, annotation['pilot_image_id'])
    try:
        write_revision(directory, annotation, file_hash(latest) if latest else None, require_latest=True, lock_timeout=30)
        remaining -= 1
    except ValueError as error:
        if str(error) != 'stale_human_revision':
            raise
print('done')
'''


def read_line(stream, timeout=30):
    """readline with a timeout, so a broken child fails the test instead of hanging it."""
    lines = []
    reader = threading.Thread(target=lambda: lines.append(stream.readline()), daemon=True)
    reader.start()
    reader.join(timeout)
    return lines[0].strip() if lines else None


class RevisionLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.directory = self.root / 'annotations'
        self.directory.mkdir()
        self.annotation = {'pilot_image_id': 'synthetic-001', 'boxes': [], 'usage': 'synthetic/test_only'}
        (self.root / 'annotation.json').write_text(json.dumps(self.annotation), encoding='utf-8')

    def spawn(self, script, *args):
        child = subprocess.Popen([sys.executable, '-B', '-c', script, str(ROOT), *map(str, args)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, child)
        return child

    def stop(self, child):
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=30)

    def hold_lock(self):
        holder = self.spawn(HOLDER, self.directory)
        self.assertEqual(read_line(holder.stdout), 'locked')
        return holder

    def revisions(self):
        return sorted(self.directory.glob('*.json'))

    def test_lock_excludes_other_processes_until_released(self):
        holder = self.hold_lock()
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, 'revision_lock_timeout'):
            write_revision(self.directory, self.annotation, None, lock_timeout=0.3)
        self.assertGreaterEqual(time.monotonic() - started, 0.3)
        self.assertEqual(self.revisions(), [])
        holder.stdin.write('\n')
        holder.stdin.flush()
        holder.wait(timeout=30)
        path = write_revision(self.directory, self.annotation, None)
        self.assertEqual(self.revisions(), [path])
        self.assertTrue((self.directory / LOCK_NAME).exists())
        self.assertEqual(latest_revision(self.directory, 'synthetic-001'), path)

    def test_lock_is_released_when_the_holder_crashes(self):
        holder = self.hold_lock()
        holder.kill()
        holder.wait(timeout=30)
        with revision_lock(self.directory, timeout=2):
            pass

    def test_concurrent_saves_from_one_parent_land_exactly_once(self):
        parent = file_hash(write_revision(self.directory, self.annotation, None))
        go = self.root / 'go'
        racers = [self.spawn(RACER, self.directory, go, parent) for _ in range(6)]
        self.assertEqual([read_line(r.stdout) for r in racers], ['ready'] * 6)
        go.touch()
        results = []
        for racer in racers:
            out, err = racer.communicate(timeout=60)
            results.append(out.strip() or err.strip())
        self.assertEqual(sorted(results), ['saved'] + ['stale_human_revision'] * 5)
        revisions = self.revisions()
        self.assertEqual(len(revisions), 2)
        self.assertEqual(json.loads(revisions[-1].read_text(encoding='utf-8'))['parent_revision_sha256'], parent)

    def test_concurrent_writers_build_one_unforked_chain(self):
        writers = [self.spawn(CHAIN, self.directory, 5) for _ in range(4)]
        for writer in writers:
            out, err = writer.communicate(timeout=120)
            self.assertEqual(out.strip(), 'done', err)
        parent = None
        revisions = self.revisions()
        self.assertEqual(len(revisions), 20)
        for path in revisions:  # Filename order is save order, and every save extends the previous one.
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['parent_revision_sha256'], parent)
            parent = file_hash(path)

    def test_parent_is_rechecked_inside_the_lock(self):
        first = write_revision(self.directory, self.annotation, None, require_latest=True)
        for stale in (None, 'a' * 64):
            with self.assertRaisesRegex(ValueError, 'stale_human_revision'):
                write_revision(self.directory, self.annotation, stale, require_latest=True)
        second = write_revision(self.directory, self.annotation, file_hash(first), require_latest=True)
        self.assertEqual(self.revisions(), [first, second])

    def test_interrupted_write_leaves_no_partial_revision(self):
        first = write_revision(self.directory, self.annotation, None)

        def partial_dump(value, stream, **kwargs):
            stream.write('{"pilot_image_id": "synthetic-001", ')
            raise OSError('simulated disk failure mid-write')

        for target, effect in (('tools.ingredient_box_edit.json.dump', partial_dump),
                               ('tools.ingredient_box_edit.os.replace', OSError('simulated failure before rename'))):
            with patch(target, side_effect=effect), self.assertRaises(OSError):
                write_revision(self.directory, self.annotation, file_hash(first), require_latest=True)
            self.assertEqual(self.revisions(), [first])
            self.assertEqual([p.name for p in self.directory.iterdir() if p.suffix == '.tmp'], [])
            self.assertEqual(latest_revision(self.directory, 'synthetic-001'), first)


class SavePendingRaceTests(DraftFixture):
    def test_save_pending_rechecks_the_parent_inside_the_lock(self):
        import tools.ingredient_ai_draft as draft_module
        annotation, draft = self.finish()
        original = draft_module.check_current

        def check_then_concurrent_save(*args, **kwargs):
            original(*args, **kwargs)
            write_revision(self.ws / 'annotations', self.annotation, self.parent_hash)  # another process saves now

        with patch('tools.ingredient_ai_draft.check_current', side_effect=check_then_concurrent_save):
            with self.assertRaisesRegex(ValueError, 'stale_human_revision'):
                self.save(annotation, draft)
        self.assertEqual(len(list((self.ws / 'annotations').glob('*.json'))), 2)


if __name__ == '__main__':
    unittest.main()
