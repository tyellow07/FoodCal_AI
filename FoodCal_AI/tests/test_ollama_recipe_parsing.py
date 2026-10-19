from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from src.recipe_parsing import ContractValidationError, OllamaRecipeParsingAdapter, validate_recipe_parsing
from src.recipe_parsing.legacy import CandidateConversionError, FIELDS, convert_legacy_items
from src.recipe_parsing.mock import DEFAULT_FIXTURE_PATH


class FakeTransport:
    def __init__(self, events):
        self.events, self.started, self.closed = events, False, False

    def start(self, channel):
        self.started = True
        for event in self.events:
            channel.put(event)

    def cancel(self):
        self.closed = True


class OllamaRecipeParsingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cases = json.loads(DEFAULT_FIXTURE_PATH.read_text(encoding='utf-8'))
        self.case = deepcopy(self.cases[0])
        self.calls, self.transports = [], []
        self.events = []

    def response(self, candidate, **final):
        raw = candidate if isinstance(candidate, str) else json.dumps(candidate, ensure_ascii=False)
        self.events = [('chunk', {'message': {'thinking': 'synthetic test reasoning'}}),
                       ('chunk', {'message': {'content': raw}, 'done': True, 'done_reason': 'stop',
                                  'model': 'gemma4:31b', 'eval_count': 42, **final})]

    def factory(self, url, payload, timeout):
        self.calls.append((url, deepcopy(payload), timeout))
        transport = FakeTransport(self.events)
        self.transports.append(transport)
        return transport

    def adapter(self, **kwargs):
        return OllamaRecipeParsingAdapter(audit_dir=self.root, transport_factory=self.factory, **kwargs)

    def candidate(self):
        return {key: deepcopy(self.case[key]) for key in ('ingredients', 'preparation_steps', 'evidence', 'issues')}

    def parse(self, adapter=None, **kwargs):
        adapter = adapter or self.adapter()
        result = adapter.parse(self.case['input_id'], source=self.case['source'], **kwargs)
        self.assertTrue(validate_recipe_parsing(result).valid)
        return result, json.loads(adapter.last_audit_path.read_text(encoding='utf-8'))

    def legacy(self, index):
        cases = json.loads(Path('experiments/recipe_parsing_gemma/test_cases.json').read_text(encoding='utf-8-sig'))
        return [{key: item[key] for key in FIELDS} for item in cases[index]['expected']]

    def test_v1_success_is_validated_partial_not_model_certified_complete(self):
        self.response(self.candidate())
        before = deepcopy(self.case)
        result, audit = self.parse()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['review_status'], 'needs_review')
        self.assertEqual(result['ingredients'], self.case['ingredients'])
        self.assertEqual(result['source'], self.case['source'])
        self.assertTrue(any(i['code'] == 'coverage_unknown' and i['severity'] == 'warning' for i in result['issues']))
        self.assertEqual(self.case, before)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.transports[0].closed)
        self.assertEqual(audit['stream']['final_response']['eval_count'], 42)
        self.assertIsNotNone(audit['stream']['first_content_seconds'])
        self.assertIn('synthetic', audit['stream']['thinking_reply'])
        self.assertEqual(result['provenance']['raw_output_sha256'], hashlib.sha256(audit['stream']['raw_reply'].encode()).hexdigest())

    def test_all_five_v1_cases_integrate_without_semantic_promotion(self):
        for case in self.cases:
            with self.subTest(case=case['input_id']):
                self.case = deepcopy(case)
                self.response(self.candidate())
                result, _ = self.parse()
                self.assertEqual(result['preparation_steps'], case['preparation_steps'])
                self.assertEqual(result['status'], 'partial')

    def test_active_prompt_is_sent_verbatim_and_hash_bound_to_provenance(self):
        prompt = Path('src/recipe_parsing/ollama_prompt.md').read_text(encoding='utf-8')
        self.response(self.candidate())
        result, audit = self.parse(self.adapter(num_predict=6144, num_ctx=8192))
        self.assertEqual(self.calls[0][1]['messages'][0], {'role': 'system', 'content': prompt})
        self.assertEqual(audit['request'], self.calls[0][1])
        self.assertEqual(result['provenance']['prompt_sha256'], hashlib.sha256(prompt.encode('utf-8')).hexdigest())
        self.assertEqual(self.calls[0][1]['options'],
                         {'temperature': 0, 'seed': 42, 'num_predict': 6144, 'num_ctx': 8192})
        self.assertEqual(len(self.calls), 1)

    def test_model_metadata_approval_and_completeness_claims_rejected(self):
        for key, value in (('source', {'kind': 'production', 'review_status': 'approved'}),
                           ('provenance', {'parser_kind': 'manual'}), ('review_status', 'approved'),
                           ('status', 'parsed'), ('complete', True)):
            with self.subTest(key=key):
                candidate = self.candidate()
                candidate[key] = value
                self.response(candidate)
                result, audit = self.parse()
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['ingredients'], [])
                self.assertEqual(result['source'], self.case['source'])
                self.assertEqual(audit['validation_errors'][0]['code'], 'invalid_schema')

    def test_schema_types_extra_fields_bad_quantity_and_forged_evidence_fail(self):
        for defect in ('type', 'extra', 'quantity', 'evidence'):
            with self.subTest(defect=defect):
                candidate = self.candidate()
                if defect == 'type':
                    candidate['issues'] = {}
                elif defect == 'extra':
                    candidate['ingredients'][0]['calories'] = 123
                elif defect == 'quantity':
                    candidate['ingredients'][0]['quantity'] = True
                else:
                    candidate['evidence'][0]['evidence_text'] = 'invented'
                self.response(candidate)
                result, audit = self.parse()
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['evidence'], [])
                self.assertTrue(audit['validation_errors'])

    def test_partial_unparsed_nulls_and_original_evidence_preserved(self):
        candidate = self.candidate()
        candidate['ingredients'][0].update(quantity=None, quantity_state='unparsed', quantity_rule=None, uncertainty=True)
        candidate['issues'].append({'code': 'quantity_unparsed', 'severity': 'warning',
                                    'path': '/ingredients/0/quantity', 'message': 'Candidate uncertainty'})
        self.response(candidate)
        result, _ = self.parse()
        self.assertIsNone(result['ingredients'][0]['quantity'])
        self.assertEqual(result['evidence'], candidate['evidence'])

    def test_omitted_group_step_is_not_restored_from_source(self):
        self.case = deepcopy(self.cases[4])
        candidate = self.candidate()
        candidate['preparation_steps'] = candidate['preparation_steps'][1:]
        self.response(candidate)
        result, _ = self.parse()
        self.assertEqual(len(result['preparation_steps']), 1)
        self.assertEqual(result['preparation_steps'][0]['normalized_action'], 'slice')

    def test_invalid_json_duplicate_keys_nonfinite_rejected(self):
        for raw in ('{', '{"ingredients":[],"ingredients":[]}', '[NaN]'):
            with self.subTest(raw=raw):
                self.response(raw)
                result, audit = self.parse()
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['issues'][-1]['code'], 'invalid_json')
                self.assertEqual(audit['stream']['raw_reply'], raw)

    def test_length_and_model_identity_mismatch_do_not_accept_valid_json(self):
        for final, code in (({'done_reason': 'length'}, 'incomplete_generation'),
                            ({'model': 'other-model'}, 'model_identity_mismatch')):
            with self.subTest(final=final):
                self.response(self.candidate(), **final)
                result, _ = self.parse()
                self.assertEqual(result['issues'][-1]['code'], code)
                self.assertEqual(result['ingredients'], [])

    def test_cancellation_before_submission_has_no_start_and_saves_result(self):
        result, audit = self.parse(cancelled=lambda: True)
        self.assertEqual(result['issues'][-1]['code'], 'cancelled')
        self.assertFalse(self.transports[0].started)
        self.assertEqual(audit['stream']['status'], 'cancelled')

    def test_cancel_file_can_stop_single_request(self):
        adapter = self.adapter()
        def progress(state):
            adapter.last_cancel_file.touch()
        result, _ = self.parse(adapter, on_progress=progress)
        self.assertEqual(result['issues'][-1]['code'], 'cancelled')
        self.assertFalse(self.transports[0].started)

    def test_timeout_and_stall_are_bounded_without_retry(self):
        for settings, status in (({'timeout': 0.02}, 'timeout'),
                                  ({'timeout': 1, 'stall_timeout': 0.02}, 'stalled')):
            with self.subTest(settings=settings):
                adapter = self.adapter(**settings)
                result, audit = self.parse(adapter)
                self.assertEqual(audit['stream']['status'], status)
                self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(self.calls), 2)

    def test_stream_eof_and_error_preserve_received_text(self):
        for event in (('eof', None), ('error', OSError('Synthetic transport failure'))):
            with self.subTest(event=event[0]):
                self.events = [('chunk', {'message': {'content': '{partial'}}), event]
                result, audit = self.parse()
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(audit['stream']['raw_reply'], '{partial')

    def test_unique_audit_and_parameters_not_overwritten(self):
        self.response(self.candidate())
        adapter = self.adapter(model_digest='caller-verified-synthetic-digest')
        self.parse(adapter)
        old = adapter.last_audit_path
        before = old.read_bytes()
        self.parse(adapter)
        self.assertNotEqual(old, adapter.last_audit_path)
        self.assertEqual(old.read_bytes(), before)
        self.assertEqual(self.calls[-1][1]['options'], {'temperature': 0, 'seed': 42, 'num_predict': 4096, 'num_ctx': 4096})
        self.assertTrue(self.calls[-1][1]['stream'])

    def test_context_override_changes_only_num_ctx_and_is_recorded(self):
        self.response(self.candidate())
        default_result, default_audit = self.parse()
        result, audit = self.parse(self.adapter(num_ctx=8192))
        expected = deepcopy(default_audit['request'])
        expected['options']['num_ctx'] = 8192
        self.assertEqual(audit['request'], expected)
        self.assertEqual(self.calls[-1][1], expected)
        self.assertEqual(result['provenance']['settings']['options'], expected['options'])
        self.assertEqual(default_result['provenance']['settings']['options']['num_ctx'], 4096)

    def test_generation_override_changes_only_num_predict_and_is_recorded(self):
        self.response(self.candidate())
        default_result, default_audit = self.parse(self.adapter(num_ctx=8192))
        result, audit = self.parse(self.adapter(num_ctx=8192, num_predict=6144))
        expected = deepcopy(default_audit['request'])
        expected['options']['num_predict'] = 6144
        self.assertEqual(audit['request'], expected)
        self.assertEqual(self.calls[-1][1], expected)
        self.assertEqual(result['provenance']['settings']['options'], expected['options'])
        self.assertEqual(default_result['provenance']['settings']['options']['num_predict'], 4096)

    def test_generation_limit_rejects_invalid_values_before_submission(self):
        for value in (True, False, 0, -1, 6144.0, '6144', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.adapter(num_predict=value)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_context_limit_rejects_invalid_values_before_submission(self):
        for value in (True, False, 0, -1, 8192.0, '8192', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.adapter(num_ctx=value)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_empty_candidates_unavailable_not_no_food(self):
        self.response({key: [] for key in self.candidate()})
        result, _ = self.parse()
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['issues'][-1]['code'], 'coverage_unknown')

    def test_caller_source_validation_runs_before_submission(self):
        adapter = self.adapter()
        bad = deepcopy(self.case['source'])
        bad['text_sha256'] = '0' * 64
        with self.assertRaises(ContractValidationError):
            adapter.parse('bad', source=bad)
        with self.assertRaises(ValueError):
            adapter.parse('bad', source=None)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_configuration_rejects_remote_url_invalid_limits(self):
        for kwargs in ({'base_url': 'https://example.org'}, {'timeout': 0}, {'timeout': True},
                       {'stall_timeout': -1}, {'num_predict': False}, {'output_format': 'auto'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.adapter(**kwargs)

    def test_legacy_exact_vague_optional_missing_cases(self):
        for index in range(4):
            with self.subTest(index=index):
                self.case = deepcopy(self.cases[index])
                self.response(self.legacy(index))
                result, audit = self.parse(self.adapter(output_format='legacy_v0'))
                self.assertEqual(result['status'], 'partial', audit['validation_errors'])
                for old, item in zip(self.legacy(index), result['ingredients']):
                    self.assertEqual((item['quantity'], item['unit'], item['optional']),
                                     (old['quantity'], old['unit'], old['optional']))
                self.assertEqual(result['preparation_steps'], [])  # Null methods are not recovered.

    def test_legacy_single_method_not_group_completion(self):
        self.case = deepcopy(self.cases[4])
        legacy = self.legacy(4)
        for item in legacy[:3]:
            item['preparation_method'] = None  # Explicit mock of missing legacy group methods.
        self.response(legacy)
        result, audit = self.parse(self.adapter(output_format='legacy_v0'))
        self.assertEqual(result['status'], 'partial', audit['validation_errors'])
        self.assertEqual(len(result['preparation_steps']), 1)
        self.assertEqual(result['preparation_steps'][0]['normalized_action'], 'slice')
        self.assertEqual(len(result['preparation_steps'][0]['ingredient_ids']), 1)

    def test_legacy_conversion_requires_opt_in(self):
        self.response(self.legacy(0))
        result, _ = self.parse()
        self.assertEqual(result['status'], 'failed')

    def test_legacy_range_preserved_unparsed_without_midpoint(self):
        text = '鹽 1～2g'
        source = deepcopy(self.case['source'])
        source.update(text=text, text_sha256=hashlib.sha256(text.encode()).hexdigest())
        item = {'ingredient_name': '鹽', 'quantity': None, 'unit': 'g',
                'preparation_method': None, 'optional': False, 'uncertainty': True, 'evidence_text': text}
        self.case['source'] = source
        self.response([item])
        result, _ = self.parse(self.adapter(output_format='legacy_v0'))
        self.assertIsNone(result['ingredients'][0]['quantity'])
        self.assertEqual(result['ingredients'][0]['quantity_raw'], '1～2')
        self.assertEqual(result['ingredients'][0]['quantity_state'], 'unparsed')

    def test_legacy_ambiguous_anchor_quarantined_without_guessing(self):
        item = self.legacy(0)[1]
        text = item['evidence_text'] + '；' + item['evidence_text']
        converted = convert_legacy_items([item], text)
        self.assertEqual(converted['ingredients'], [])
        self.assertEqual(converted['issues'][0]['code'], 'coverage_unknown')

    def test_legacy_non_verbatim_method_is_visible_without_synthesized_group(self):
        self.case = deepcopy(self.cases[4])
        self.response(self.legacy(4))  # Original synthetic reference uses 混合醃料.
        result, _ = self.parse(self.adapter(output_format='legacy_v0'))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual([s['normalized_action'] for s in result['preparation_steps']], ['slice'])
        self.assertTrue(any(i['code'] == 'unsupported_legacy_field' for i in result['issues']))

    def test_legacy_unsupported_optionality_retains_uncertainty_and_original_audit(self):
        legacy = self.legacy(0)
        legacy[0]['optional'] = True  # Unverified model declaration without a marker.
        self.response(legacy)
        result, audit = self.parse(self.adapter(output_format='legacy_v0'))
        item = result['ingredients'][0]
        self.assertEqual(item['optional_state'], 'unresolved')
        self.assertFalse(item['optional'])  # v1 unresolved does not assert required.
        self.assertTrue(item['uncertainty'])
        self.assertTrue(audit['wire_candidate'][0]['optional'])

    def test_legacy_no_unique_anchor_returns_unavailable_and_keeps_candidate_audit(self):
        legacy = [self.legacy(0)[1]]
        text = legacy[0]['evidence_text'] + '；' + legacy[0]['evidence_text']
        self.case['source'].update(text=text, text_sha256=hashlib.sha256(text.encode()).hexdigest())
        self.response(legacy)
        result, audit = self.parse(self.adapter(output_format='legacy_v0'))
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(audit['wire_candidate'], legacy)

    def test_legacy_bad_fields_and_fake_evidence_rejected(self):
        for key, value in (('calories', 12), ('quantity', True), ('evidence_text', 'fake'), ('optional', 'true')):
            with self.subTest(key=key):
                legacy = self.legacy(0)
                legacy[0][key] = value
                self.response(legacy)
                result, _ = self.parse(self.adapter(output_format='legacy_v0'))
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['ingredients'], [])


if __name__ == '__main__':
    unittest.main()
