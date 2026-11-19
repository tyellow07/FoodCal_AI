"""The stdlib coverage tool measures a small test module without disturbing the outer test run."""
import sys
import unittest

from tools.measure_coverage import measure


class CoverageToolTests(unittest.TestCase):
    def test_measures_one_module(self):
        if sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) is not None:
            self.skipTest('already running under a coverage monitor (e.g. tools.measure_coverage itself)')
        report = measure(['tests.test_meal_evaluation'])
        self.assertEqual((report['failures'], report['errors']), (0, 0))
        self.assertGreater(report['tests_run'], 0)
        files = {f['file']: f for f in report['files']}
        metrics = files['src/evaluation/meal_metrics.py']
        self.assertGreater(metrics['covered'] / metrics['lines'], 0.8)
        self.assertEqual(files['src/detection/export_validation_review.py']['covered'], 0)
        self.assertTrue(0 < report['line_coverage'] < 1)


if __name__ == '__main__':
    unittest.main()
