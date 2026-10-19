from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from src.recipe_parsing.budget import diagnose_generation_budget
from src.recipe_parsing.mock import DEFAULT_FIXTURE_PATH
from src.recipe_parsing.ollama import OllamaRecipeParsingAdapter


class BudgetDiagnosticsTests(unittest.TestCase):
    def report(self, **final):
        return diagnose_generation_budget({'num_predict': 6144, 'num_ctx': 8192}, final)

    def test_excess_is_advisory_not_server_option_confirmation(self):
        report = self.report(prompt_eval_count=861, eval_count=7063, done_reason='stop')
        self.assertEqual(report['reported_excess_tokens'], 919)
        self.assertFalse(report['aggregate_within_requested'])
        self.assertIsNone(report['server_received_options'])
        self.assertIsNone(report['backend_effective_budget'])
        self.assertFalse(report['hard_generation_cap_verified'])

    def test_within_budget_does_not_prove_hard_cap(self):
        report = self.report(prompt_eval_count=834, eval_count=4696)
        self.assertTrue(report['aggregate_within_requested'])
        self.assertFalse(report['hard_generation_cap_verified'])

    def test_multiple_observed_phases(self):
        report = diagnose_generation_budget({'num_predict': 6144}, {'eval_count': 7063},
                                            backend_phase_counts=[5802, 1261])
        self.assertIn('multiple_backend_phases_observed', report['codes'])
        self.assertNotIn('backend_api_count_mismatch', report['codes'])
        self.assertIsNone(report['thinking_token_count'])
        self.assertIsNone(report['answer_token_count'])

    def test_missing_terminal_phase_is_not_zero(self):
        report = diagnose_generation_budget({}, {'eval_count': 7063}, backend_phase_counts=[None, 1261])
        self.assertEqual(report['backend_phase_counts'], [None, 1261])
        self.assertIn('incomplete_backend_phase_counts', report['codes'])

    def test_phase_total_mismatch(self):
        report = diagnose_generation_budget({}, {'eval_count': 100}, backend_phase_counts=[50, 40])
        self.assertIn('backend_api_count_mismatch', report['codes'])

    def test_missing_statistics(self):
        report = self.report()
        self.assertIsNone(report['reported_excess_tokens'])
        self.assertIn('missing_eval_count', report['codes'])

    def test_invalid_statistics_do_not_crash_or_coerce(self):
        for value in (True, -1, 1.5, '7063', {}, float('nan')):
            with self.subTest(value=value):
                report = self.report(eval_count=value)
                self.assertIsNone(report['api_final_counters']['eval_count'])
                self.assertIn('invalid_eval_count', report['codes'])
                json.dumps(report, allow_nan=False)

    def test_length_is_not_assumed_to_be_generation_budget(self):
        report = self.report(prompt_eval_count=686, eval_count=3410, done_reason='length')
        self.assertIn('length_cause_unresolved', report['codes'])
        self.assertIsNone(report['context_occupancy'])

    def test_arbitrary_fields_and_text_are_not_retained(self):
        secret = 'private-user-recipe-credential'
        report = diagnose_generation_budget({'num_predict': 6144, 'secret': secret},
                                            {'eval_count': 1, 'message': secret, 'done_reason': secret,
                                             'server_received_options': {'num_predict': 6144}})
        self.assertNotIn(secret, json.dumps(report))
        self.assertIsNone(report['server_received_options'])

    def test_invalid_phase_container_is_diagnostic(self):
        report = diagnose_generation_budget({}, {}, backend_phase_counts='private log')
        self.assertIn('invalid_backend_phase_counts', report['codes'])
        self.assertNotIn('private log', json.dumps(report))


class BudgetAdapterTests(unittest.TestCase):
    def run_adapter(self, enabled=True, error=False):
        case = json.loads(DEFAULT_FIXTURE_PATH.read_text(encoding='utf-8'))[0]
        candidate = {key: deepcopy(case[key]) for key in ('ingredients', 'preparation_steps', 'evidence', 'issues')}
        calls = []

        class Transport:
            def start(self, channel):
                channel.put(('chunk', {'message': {'content': json.dumps(candidate)}, 'done': True,
                                      'model': 'gemma4:31b', 'done_reason': 'stop',
                                      'prompt_eval_count': 861, 'eval_count': 7063}))

            def cancel(self):
                pass

        def factory(url, payload, timeout):
            calls.append(deepcopy(payload))
            if error:
                raise RuntimeError('synthetic transport failure')
            return Transport()

        with tempfile.TemporaryDirectory() as directory:
            adapter = OllamaRecipeParsingAdapter(audit_dir=Path(directory), transport_factory=factory,
                                                 num_predict=6144, num_ctx=8192, budget_diagnostics=enabled)
            result = adapter.parse(case['input_id'], source=case['source'])
            audit = json.loads(adapter.last_audit_path.read_text(encoding='utf-8'))
        return result, audit, calls

    def test_diagnostics_do_not_change_request_or_contract(self):
        enabled, audit, calls = self.run_adapter()
        disabled, other, other_calls = self.run_adapter(False)
        self.assertEqual(enabled, disabled)
        self.assertEqual(calls, other_calls)
        self.assertEqual(len(calls), 1)
        self.assertEqual(enabled['status'], 'partial')
        self.assertIn('reported_generation_exceeds_requested', audit['budget_diagnostics']['codes'])
        self.assertNotIn('budget_diagnostics', other)

    def test_transport_failure_retains_safe_result_and_missing_counters(self):
        result, audit, calls = self.run_adapter(error=True)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['ingredients'], [])
        self.assertIn('missing_eval_count', audit['budget_diagnostics']['codes'])
        self.assertEqual(len(calls), 1)

    def test_switch_requires_boolean(self):
        with self.assertRaises(ValueError):
            OllamaRecipeParsingAdapter(budget_diagnostics=1)
