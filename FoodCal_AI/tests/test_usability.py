"""Usability scoring on synthetic answers; latency tool with a stub detector (no model)."""
from decimal import Decimal
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from src.evaluation.usability import latency_p95, score_usability, summarize_usability, sus_score


def sus_rows(answer_sets):
    return [{'participant_id': f'P{i}', **{f'q{j + 1}': str(a) for j, a in enumerate(answers)}}
            for i, answers in enumerate(answer_sets, 1)]


def task_rows(participants, outcome='completed', seconds='40'):
    return [{'participant_id': f'P{i}', 'task': task, 'outcome': outcome, 'seconds': seconds}
            for i in range(1, participants + 1) for task in ('T1', 'T2', 'T3', 'T4')]


class UsabilityTests(unittest.TestCase):
    def test_sus_formula(self):
        self.assertEqual(sus_score([5, 1] * 5), 100)
        self.assertEqual(sus_score([1, 5] * 5), 0)
        self.assertEqual(sus_score([3] * 10), 50)
        for bad in ([3] * 9, [0] + [3] * 9, [True] + [3] * 9):
            with self.assertRaises(ValueError):
                sus_score(bad)

    def test_full_marks_and_small_sample_rule(self):
        summary = summarize_usability(sus_rows([[5, 1] * 5] * 5), task_rows(5), latency_p95_seconds='2.5',
                                      checklist={'no_raw_json_on_main_screen': True,
                                                 'chinese_reasons_and_next_steps': True,
                                                 'phone_width_t1_without_side_scroll': True})
        self.assertEqual(summary['sus_mean'], 100)
        self.assertEqual(summary['t1_median_seconds'], 40)
        self.assertEqual(score_usability(summary)['subtotal'], 15)
        small = summarize_usability(sus_rows([[5, 1] * 5] * 4), task_rows(4))
        points = score_usability(small)
        self.assertEqual((points['4a_sus'], points['4b_task_success'], points['4c_speed']), (0, 0, 1))
        self.assertTrue(points['notes'])

    def test_assisted_is_not_success(self):
        rows = task_rows(5)
        for row in rows[:4]:
            row['outcome'] = 'assisted'
        summary = summarize_usability(sus_rows([[3] * 10] * 5), rows)
        self.assertEqual(summary['task_success_rate'], Decimal(16) / 20)
        self.assertEqual(score_usability(summary)['4b_task_success'], 2)
        with self.assertRaises(ValueError):
            summarize_usability([], rows + rows[:1])
        with self.assertRaises(ValueError):
            summarize_usability([], [], checklist={'made_up': True})

    def test_latency_percentile(self):
        self.assertEqual(latency_p95(range(1, 21)), 19)
        self.assertEqual(latency_p95([3]), 3)

    def test_measure_latency_excludes_first_call(self):
        from tools.measure_latency import measure
        from tests.test_demo_ui import image_bytes
        from src.pipeline.diagnostic_demo import DEFAULT_FIXTURES, run_demo
        from unittest.mock import Mock
        detector = Mock(classes_path=DEFAULT_FIXTURES / 'classes.yaml')
        detector.detect_food.return_value = run_demo()['detection_snapshot']
        ticks = iter([0, 10] + [x for i in range(20) for x in (100 + i, 100 + i + 0.5)])
        result = measure(image_bytes(), runs=20, detector_factory=lambda: detector, clock=lambda: next(ticks))
        self.assertEqual(result['first_call_seconds'], 10)
        self.assertEqual(Decimal(result['p95_seconds']), Decimal('0.5'))
        failing = Mock(classes_path=DEFAULT_FIXTURES / 'classes.yaml')
        failing.detect_food.side_effect = FileNotFoundError('x')
        with self.assertRaisesRegex(ValueError, 'analysis_blocked'):
            measure(image_bytes(), runs=1, detector_factory=lambda: failing)

    def test_cli_and_templates(self):
        from tools.score_usability import main
        root = Path(__file__).resolve().parents[1] / 'docs/examples'
        self.assertEqual((root / 'usability_sus_template.csv').read_text(encoding='utf-8').strip(),
                         'participant_id,' + ','.join(f'q{i}' for i in range(1, 11)))
        with tempfile.TemporaryDirectory() as folder:
            sus, tasks = Path(folder) / 'sus.csv', Path(folder) / 'tasks.csv'
            sus.write_text('participant_id,' + ','.join(f'q{i}' for i in range(1, 11)) + '\nP1,4,2,4,2,4,2,4,2,4,2\n',
                           encoding='utf-8')
            tasks.write_text('participant_id,task,outcome,seconds\nP1,T1,completed,55\n', encoding='utf-8')
            out = io.StringIO()
            with redirect_stdout(out):
                main(['--sus', str(sus), '--tasks', str(tasks), '--check', 'no_raw_json_on_main_screen'])
            report = json.loads(out.getvalue())
        self.assertEqual(report['summary']['sus_mean'], '75.00')
        self.assertEqual(report['rubric_points']['4d_checklist'], 1)
        self.assertEqual(report['rubric_points']['4c_speed'], 1)


if __name__ == '__main__':
    unittest.main()
