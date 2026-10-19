"""Offline structural schema and Mock Ollama tests; no native runtime required."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from src.recipe_parsing.schema import candidate_json_schema, schema_sha256, validate_candidate_schema
from tests import test_ollama_recipe_parsing as mock_tools


class CandidateSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(Path('docs/examples/recipe_parsing_contract_v1_cases.json').read_text(encoding='utf-8'))

    def candidate(self, index=0):
        return {key: deepcopy(self.cases[index][key]) for key in ('ingredients', 'preparation_steps', 'evidence', 'issues')}

    def test_all_five_contract_examples_accept_null_quantities_and_steps(self):
        for i in range(5):
            value = self.candidate(i)
            before = deepcopy(value)
            self.assertTrue(validate_candidate_schema(value).valid)
            self.assertEqual(value, before)

    def test_all_eight_null_boolean_flags_rejected_without_defaulting(self):
        value = self.candidate()
        for item in value['ingredients']:
            item.update(optional=None, uncertainty=None)
        before = deepcopy(value)
        report = validate_candidate_schema(value)
        self.assertEqual([(e.path, e.code) for e in report.errors],
                         [(f'/ingredients/{i}/{field}', 'schema_type') for i in range(4) for field in ('optional', 'uncertainty')])
        self.assertEqual(value, before)

    def test_boolean_not_number_integer_or_string(self):
        for field, value in (('quantity', True), ('optional', 0), ('uncertainty', 'false')):
            with self.subTest(field=field):
                data = self.candidate()
                data['ingredients'][0][field] = value
                self.assertFalse(validate_candidate_schema(data).valid)
        data = self.candidate()
        data['ingredients'][0]['anchor']['start'] = True
        self.assertFalse(validate_candidate_schema(data).valid)

    def test_missing_extra_enum_roles_and_step_flag_rejected(self):
        for defect in ('missing', 'source', 'role', 'state', 'step'):
            with self.subTest(defect=defect):
                value = self.candidate(4)
                if defect == 'missing':
                    del value['ingredients'][0]['quantity']
                elif defect == 'source':
                    value['source'] = {'review_status': 'approved'}
                elif defect == 'role':
                    value['ingredients'][0]['evidence_links'][0]['supports'] = ['approval']
                elif defect == 'state':
                    value['ingredients'][0]['quantity_state'] = 'guessed'
                else:
                    value['preparation_steps'][0]['uncertainty'] = None
                self.assertFalse(validate_candidate_schema(value).valid)

    def test_schema_is_fresh_deterministic_minimal_and_has_no_defaults(self):
        schema = candidate_json_schema()
        expected = schema_sha256(schema)
        def check(node):
            self.assertTrue(set(node) <= {'type', 'properties', 'required', 'additionalProperties', 'items', 'enum'})
            if node.get('type') == 'object':
                self.assertFalse(node['additionalProperties'])
                self.assertEqual(set(node['required']), set(node['properties']))
                for child in node['properties'].values():
                    check(child)
            if 'items' in node:
                check(node['items'])
        check(schema)
        schema['properties']['ingredients']['items']['properties']['optional']['type'] = 'null'
        self.assertEqual(schema_sha256(candidate_json_schema()), expected)


class SchemaAdapterTests(unittest.TestCase):
    # Reuse just the mock harness; original tests remain independently discovered.
    setUp = mock_tools.OllamaRecipeParsingTests.setUp
    response = mock_tools.OllamaRecipeParsingTests.response
    factory = mock_tools.OllamaRecipeParsingTests.factory
    adapter = mock_tools.OllamaRecipeParsingTests.adapter
    candidate = mock_tools.OllamaRecipeParsingTests.candidate
    parse = mock_tools.OllamaRecipeParsingTests.parse
    def test_schema_request_is_only_format_change_and_hash_is_recorded(self):
        self.response(self.candidate())
        plain, plain_audit = self.parse(self.adapter(num_ctx=8192, num_predict=6144))
        self.response(self.candidate())
        result, audit = self.parse(self.adapter(json_schema=True, num_ctx=8192, num_predict=6144))
        expected = deepcopy(plain_audit['request'])
        expected['format'] = candidate_json_schema()
        self.assertEqual(audit['request'], expected)
        self.assertEqual(audit['schema_sha256'], schema_sha256(expected['format']))
        self.assertEqual(result['provenance']['settings']['schema_sha256'], audit['schema_sha256'])
        self.assertEqual(result['provenance']['settings']['format'], expected['format'])
        self.assertEqual(audit['schema_validation_errors'], [])
        self.assertEqual(result['source'], plain['source'])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(len(self.calls), 2)  # Exactly one submission per separate parse.

    def test_schema_null_flags_rejected_raw_candidate_preserved(self):
        candidate = self.candidate()
        for item in candidate['ingredients']:
            item.update(optional=None, uncertainty=None)
        self.response(candidate)
        result, audit = self.parse(self.adapter(json_schema=True))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['ingredients'], [])
        self.assertEqual(audit['wire_candidate'], candidate)
        self.assertEqual(len(audit['schema_validation_errors']), 8)
        self.assertEqual(len(self.calls), 1)

    def test_schema_all_five_mock_cases_preserve_nulls_and_group_steps(self):
        for case in self.cases:
            with self.subTest(input_id=case['input_id']):
                self.case = deepcopy(case)
                self.response(self.candidate())
                result, audit = self.parse(self.adapter(json_schema=True))
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['ingredients'], case['ingredients'])
                self.assertEqual(result['preparation_steps'], case['preparation_steps'])
                self.assertEqual(audit['schema_validation_errors'], [])

    def test_schema_pass_does_not_bypass_semantic_validator_or_trust(self):
        candidate = self.candidate()
        candidate['evidence'][0]['evidence_text'] = 'forged'
        self.assertTrue(validate_candidate_schema(candidate).valid)
        self.response(candidate)
        result, audit = self.parse(self.adapter(json_schema=True))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(audit['schema_validation_errors'], [])
        self.assertTrue(audit['validation_errors'])
        candidate = self.candidate()
        candidate['source'] = {'review_status': 'approved'}
        self.response(candidate)
        result, audit = self.parse(self.adapter(json_schema=True))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['source'], self.case['source'])
        self.assertTrue(audit['schema_validation_errors'])

    def test_switch_can_be_disabled_and_rejects_legacy_or_non_boolean(self):
        self.response(self.candidate())
        _, audit = self.parse(self.adapter(json_schema=False))
        self.assertEqual(audit['request']['format'], 'json')
        self.assertNotIn('schema_sha256', audit)
        for kwargs in ({'json_schema': 1}, {'json_schema': None}, {'json_schema': True, 'output_format': 'legacy_v0'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.adapter(**kwargs)


if __name__ == '__main__':
    unittest.main()
