from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from src.recipe_parsing import (
    ContractValidationError, FixtureLoadError, MockRecipeParsingAdapter,
    UnsupportedQuantityFormat, decimal_literal, diagnostic_result, load_recipe_parsing, validate_recipe_parsing,
    load_model_recipe_parsing, validate_model_recipe_parsing,
)
from src.recipe_parsing.mock import DEFAULT_FIXTURE_PATH


class RecipeParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(DEFAULT_FIXTURE_PATH.read_text(encoding='utf-8'))

    def setUp(self):
        self.value = deepcopy(self.cases[0])

    def assert_valid(self, value=None):
        report = validate_recipe_parsing(self.value if value is None else value)
        self.assertTrue(report.valid, report.errors)

    def assert_invalid(self, path, code=None):
        report = validate_recipe_parsing(self.value)
        self.assertFalse(report.valid)
        self.assertTrue(any(e.path == path and (code is None or e.code == code) for e in report.errors), report.errors)

    def issue(self, path, code='coverage_unknown', severity='warning'):
        self.value['issues'].append({'code': code, 'severity': severity, 'path': path, 'message': 'Explicit test uncertainty'})

    def partial(self):
        self.value.update(status='partial', review_status='needs_review')

    def test_all_five_design_examples_validate_without_mutation(self):
        original = deepcopy(self.cases)
        for case in self.cases:
            with self.subTest(case=case['input_id']):
                self.assert_valid(case)
        self.assertEqual(self.cases, original)

    def test_required_fields_and_closed_nested_objects(self):
        for path, mutate in (
            ('/contract', lambda v: v.pop('contract')),
            ('/ingredients/0/quantity', lambda v: v['ingredients'][0].pop('quantity')),
            ('/ingredients/0/telefone', lambda v: v['ingredients'][0].update(telefone=None)),
            ('/source/private_path', lambda v: v['source'].update(private_path='hidden')),
            ('/provenance/model/extra', lambda v: v['provenance'].update(model={'identifier': 'test', 'digest': None, 'extra': 1})),
        ):
            with self.subTest(path=path):
                self.value = deepcopy(self.cases[0])
                mutate(self.value)
                self.assert_invalid(path)

    def test_bad_types_are_localized_not_crashes(self):
        for field, values in (
            ('ingredients', [None, {}, 'x']), ('schema_version', [True, 1.0, 2]),
            ('status', ['success', [], None]), ('source', [[], 'x']),
        ):
            for bad in values:
                with self.subTest(field=field, value=bad):
                    self.value = deepcopy(self.cases[0])
                    self.value[field] = bad
                    self.assert_invalid('/' + field)
        self.assertFalse(validate_recipe_parsing(None).valid)

    def test_bool_nan_infinity_negative_quantity_rejected(self):
        for bad in (True, float('nan'), float('inf'), -1, '200'):
            with self.subTest(quantity=bad):
                self.value['ingredients'][0]['quantity'] = bad
                self.assert_invalid('/ingredients/0/quantity', 'type')

    def test_strict_boolean_and_integer_span(self):
        self.value['ingredients'][0]['optional'] = 0
        self.assert_invalid('/ingredients/0/optional', 'type')
        self.value = deepcopy(self.cases[0])
        self.value['evidence'][0]['start'] = False
        self.assert_invalid('/evidence/0/start', 'type')

    def test_duplicate_all_entity_ids_rejected(self):
        for key, identity, case in (('ingredients', 'ingredient_id', 0), ('evidence', 'evidence_id', 0),
                                    ('preparation_steps', 'step_id', 4)):
            with self.subTest(key=key):
                self.value = deepcopy(self.cases[case])
                self.value[key].append(deepcopy(self.value[key][0]))
                self.assert_invalid(f'/{key}/{len(self.value[key])-1}/{identity}', 'duplicate_id')

    def test_dangling_evidence_and_target_ids(self):
        self.value['ingredients'][0]['evidence_links'][0]['evidence_id'] = 'ev_999_1000'
        self.assert_invalid('/ingredients/0/evidence_links/0/evidence_id', 'reference')
        self.value = deepcopy(self.cases[4])
        self.value['preparation_steps'][0]['ingredient_ids'][0] = 'ing_999_1000'
        self.assert_invalid('/preparation_steps/0/ingredient_ids/0', 'reference')

    def test_duplicate_targets_and_empty_roles_rejected(self):
        self.value = deepcopy(self.cases[4])
        step = self.value['preparation_steps'][0]
        step['ingredient_ids'].append(step['ingredient_ids'][0])
        self.assert_invalid('/preparation_steps/0/ingredient_ids', 'duplicate_id')
        self.value = deepcopy(self.cases[0])
        self.value['ingredients'][0]['evidence_links'][0]['supports'] = []
        self.assert_invalid('/ingredients/0/evidence_links/0/supports', 'roles')

    def test_duplicate_and_wrong_entity_roles(self):
        for roles in (['name', 'name'], ['name', 'action']):
            with self.subTest(roles=roles):
                self.value['ingredients'][0]['evidence_links'][0]['supports'] = roles
                self.assert_invalid('/ingredients/0/evidence_links/0/supports', 'roles')

    def test_evidence_text_span_and_id_must_match(self):
        for field, bad, error_field in (('evidence_text', 'fake', 'evidence_text'),
                                        ('end', 999, ''), ('evidence_id', 'ev_0_1', 'evidence_id')):
            with self.subTest(field=field):
                self.value = deepcopy(self.cases[0])
                self.value['evidence'][0][field] = bad
                self.assert_invalid('/evidence/0' + ('/' + error_field if error_field else ''))

    def test_name_and_action_anchors_must_match(self):
        self.value['ingredients'][0]['ingredient_name'] = '同義詞不可取代原文'
        self.assert_invalid('/ingredients/0/ingredient_name', 'evidence')
        self.value = deepcopy(self.cases[4])
        self.value['preparation_steps'][0]['action_raw'] = '混合醃料'
        self.assert_invalid('/preparation_steps/0/action_raw', 'evidence')

    def test_role_evidence_must_cover_anchor_and_literals(self):
        self.value['ingredients'][0]['evidence_links'] = deepcopy(self.value['ingredients'][1]['evidence_links'])
        self.assert_invalid('/ingredients/0/evidence_links', 'evidence')
        self.value = deepcopy(self.cases[0])
        self.value['ingredients'][0]['unit'] = 'kg'
        self.assert_invalid('/ingredients/0/unit', 'evidence')
        self.value['ingredients'][0]['quantity_raw'] = '999'
        self.assert_invalid('/ingredients/0/quantity_raw', 'evidence')

    def test_hash_mismatch_and_kind_pair(self):
        self.value['source']['text_sha256'] = '0' * 64
        self.assert_invalid('/source/text_sha256', 'hash')
        self.value['source']['review_status'] = 'approved'
        self.assert_invalid('/source/review_status', 'source')

    def test_unsafe_source_locators(self):
        for locator in ('C:/Users/private/recipe.txt', r'C:\Users\private\recipe.txt',
                        '/home/private/recipe', '../recipe', 'file:///recipe',
                        'https://user:password@example.org/recipe', '//private/recipe',
                        'https://example.org/recipe?api_key=secret'):
            with self.subTest(locator=locator):
                self.value['source']['reference'] = locator
                self.assert_invalid('/source/reference', 'reference')
        self.value['source']['reference'] = 'https://example.org/recipe#ingredients'
        self.assert_valid()

    def test_binding_identity_and_catalog_hash(self):
        self.value['retrieval_binding'] = {'source_id': self.value['source']['source_id'],
                                           'recipe_id': 'fixture-only', 'catalog_sha256': 'a' * 64}
        self.assert_valid()
        self.value['retrieval_binding']['source_id'] = 'other'
        self.assert_invalid('/retrieval_binding/source_id', 'binding')
        self.value['retrieval_binding']['catalog_sha256'] = 'A' * 64
        self.assert_invalid('/retrieval_binding/catalog_sha256', 'type')

    def test_exact_quantity_rule_and_half(self):
        self.value['ingredients'][0]['quantity'] = 201
        self.assert_invalid('/ingredients/0/quantity', 'normalization')
        self.value = deepcopy(self.cases[2])
        self.assertEqual(self.value['ingredients'][1]['quantity'], 0.5)
        self.assertEqual(self.value['ingredients'][1]['unit'], '顆')
        self.assert_valid()
        self.value['ingredients'][1]['quantity'] = 1
        self.assert_invalid('/ingredients/1/quantity', 'normalization')

    def test_numeric_policy_and_injection(self):
        for raw in ('1e2', '+1', '-1', '一', '1/2', '1～2', '.5', ' 1', '1.'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decimal_literal(raw)
        self.assertEqual(decimal_literal('0'), Decimal(0))
        self.assertEqual(decimal_literal('0.5'), Decimal('0.5'))
        calls = []
        def custom(raw):
            calls.append(raw)
            return Decimal(raw)
        self.assertTrue(validate_recipe_parsing(self.value, numeric_parser=custom).valid)
        self.assertEqual(calls, ['200', '2', '1', '10'])

    def quantity_example(self, raw):
        text = '鹽 ' + raw + 'g'
        self.value = deepcopy(self.cases[0])
        self.value['source']['text'] = text
        self.value['source']['text_sha256'] = hashlib.sha256(text.encode()).hexdigest()
        identity = f'ev_0_{len(text)}'
        self.value['evidence'] = [{'evidence_id': identity, 'start': 0, 'end': len(text), 'evidence_text': text}]
        item = deepcopy(self.cases[0]['ingredients'][1])
        item.update(ingredient_id='ing_0_1', anchor={'start': 0, 'end': 1}, quantity_raw=raw,
                    evidence_links=[{'evidence_id': identity, 'supports': ['name', 'quantity', 'unit']}])
        self.value['ingredients'] = [item]

    def test_unsupported_quantity_is_not_reported_as_content_mismatch(self):
        for raw in ('二', '+2', '2e0', '1/2', '1～2'):
            with self.subTest(raw=raw):
                self.quantity_example(raw)
                before = deepcopy(self.value)
                report = validate_recipe_parsing(self.value)
                self.assertEqual([(e.path, e.code) for e in report.errors],
                                 [('/ingredients/0/quantity_raw', 'unsupported_quantity_format')])
                self.assertIn('source content is not judged incorrect', report.errors[0].message)
                self.assertEqual(self.value, before)
                with self.assertRaises(UnsupportedQuantityFormat):
                    decimal_literal(raw)

    def test_unsupported_literal_preserved_as_unparsed_is_valid(self):
        for raw in ('二', '1/2', '1～2', '一碗多一點'):
            with self.subTest(raw=raw):
                self.quantity_example(raw)
                item = self.value['ingredients'][0]
                item.update(quantity=None, quantity_rule=None, quantity_state='unparsed', uncertainty=True)
                self.partial()
                self.issue('/ingredients/0/quantity', 'quantity_unparsed')
                snapshot = load_recipe_parsing(self.value)
                self.assertIsNone(snapshot['ingredients'][0]['quantity'])
                self.assertEqual(snapshot['ingredients'][0]['quantity_raw'], raw)
                self.assertEqual(snapshot['source']['text'], '鹽 ' + raw + 'g')

    def test_supported_literal_mismatch_remains_invalid_data(self):
        self.quantity_example('3')  # Declared quantity remains 2.
        report = validate_recipe_parsing(self.value)
        self.assertEqual([(e.path, e.code) for e in report.errors],
                         [('/ingredients/0/quantity', 'normalization')])
        self.value['ingredients'][0]['quantity'] = '3'
        self.assert_invalid('/ingredients/0/quantity', 'type')

    def test_custom_numeric_parser_can_report_unsupported(self):
        def unsupported(raw):
            raise UnsupportedQuantityFormat('Custom whitelist')
        report = validate_recipe_parsing(self.value, numeric_parser=unsupported)
        self.assertEqual({e.code for e in report.errors}, {'unsupported_quantity_format'})

    def test_missing_values_are_null_not_zero(self):
        self.value = deepcopy(self.cases[3])
        self.assert_valid()
        for item in self.value['ingredients']:
            self.assertIsNone(item['quantity'])
            self.assertIsNone(item['unit'])
        self.value['ingredients'][0]['quantity'] = 0
        self.assert_invalid('/ingredients/0/quantity', 'quantity')

    def test_explicit_zero_is_valid_and_unit_missing_is_uncertain(self):
        text = self.value['source']['text'].replace('200', '000')
        self.value['source']['text'] = text
        self.value['source']['text_sha256'] = hashlib.sha256(text.encode()).hexdigest()
        for item in self.value['evidence']:
            item['evidence_text'] = text[item['start']:item['end']]
        self.value['ingredients'][0].update(quantity=0, quantity_raw='000')
        self.assert_valid()
        self.value['ingredients'][0].update(unit=None, uncertainty=True)
        self.issue('/ingredients/0/unit', 'unit_missing', 'info')
        self.assert_valid()
        self.value['ingredients'][0]['uncertainty'] = False
        self.assert_invalid('/ingredients/0/uncertainty', 'uncertainty')

    def test_vague_null_raw_uncertainty_and_issue(self):
        self.value = deepcopy(self.cases[1])
        self.value['ingredients'][0]['uncertainty'] = False
        self.assert_invalid('/ingredients/0/uncertainty', 'uncertainty')
        self.value = deepcopy(self.cases[1])
        self.value['ingredients'][0]['quantity_raw'] = None
        self.assert_invalid('/ingredients/0/quantity', 'quantity')
        self.value = deepcopy(self.cases[1])
        self.value['issues'] = []
        self.assert_invalid('/ingredients/0', 'issue')

    def test_unparsed_requires_partial_warning_and_review(self):
        item = self.value['ingredients'][0]
        item.update(quantity=None, quantity_state='unparsed', quantity_rule=None, uncertainty=True)
        self.assert_invalid('/ingredients/0', 'status')
        self.partial()
        self.issue('/ingredients/0/quantity', 'quantity_unparsed')
        self.assert_valid()
        self.value['review_status'] = 'unreviewed'
        self.assert_invalid('/review_status', 'status')

    def test_optional_flag_evidence_and_unresolved_policy(self):
        self.value = deepcopy(self.cases[2])
        carrot = self.value['ingredients'][2]
        carrot['evidence_links'] = [link for link in carrot['evidence_links'] if 'optionality' not in link['supports']]
        self.assert_invalid('/ingredients/2/evidence_links', 'evidence')
        self.value = deepcopy(self.cases[0])
        self.value['ingredients'][0]['optional'] = True
        self.assert_invalid('/ingredients/0/optional', 'optionality')
        self.value['ingredients'][0].update(optional=False, optional_state='unresolved', uncertainty=True)
        self.partial()
        self.issue('/ingredients/0', 'optional_unresolved')
        self.assert_valid()

    def test_additional_uncertainty_requires_issue(self):
        self.value['ingredients'][0]['uncertainty'] = True
        self.assert_invalid('/ingredients/0/uncertainty', 'issue')
        self.issue('/ingredients/0', severity='info')
        self.assert_valid()

    def test_action_normalization_and_target_evidence(self):
        self.value = deepcopy(self.cases[4])
        self.value['preparation_steps'][0]['normalization_rule'] = 'v1.zh.slice'
        self.assert_invalid('/preparation_steps/0/normalized_action', 'normalization')
        self.value = deepcopy(self.cases[4])
        self.value['preparation_steps'][0]['ingredient_ids'] = [self.value['ingredients'][3]['ingredient_id']]
        self.assert_invalid('/preparation_steps/0/ingredient_ids/0', 'evidence')

    def test_unresolved_and_partial_targets(self):
        self.value = deepcopy(self.cases[4])
        step = self.value['preparation_steps'][0]
        step.update(ingredient_ids=[], target_status='unresolved', uncertainty=True)
        self.partial()
        self.issue('/preparation_steps/0', 'targets_unresolved')
        self.assert_valid()
        step['target_status'] = 'partial'
        self.assert_invalid('/preparation_steps/0/ingredient_ids', 'targets')
        step['ingredient_ids'] = [self.value['ingredients'][0]['ingredient_id']]
        self.assert_valid()

    def test_unknown_action_requires_null_and_visible_partial(self):
        self.value = deepcopy(self.cases[4])
        step = self.value['preparation_steps'][0]
        # Existing exact source slice that is not a v1 processing verb.
        start = self.value['source']['text'].index('醃料')
        step.update(anchor={'start': start, 'end': start + 2}, step_id=f'step_{start}_{start+2}',
                    action_raw='醃料', normalized_action=None, normalization_rule=None, uncertainty=True)
        self.partial()
        self.issue('/preparation_steps/0', 'action_unmapped')
        self.assert_valid()  # Literal validity does NOT prove this is a cooking verb.
        step['normalized_action'] = 'mix'
        self.assert_invalid('/preparation_steps/0/normalized_action', 'normalization')

    def test_status_consistency_and_diagnostic_states(self):
        self.value['status'] = 'partial'
        self.assert_invalid('/status', 'status')
        self.value = deepcopy(self.cases[0])
        self.issue('', severity='warning')
        self.assert_invalid('/status', 'status')
        for status in ('unavailable', 'failed'):
            value = diagnostic_result('unknown', status=status, code='timeout', message='Test only')
            self.assert_valid(value)
            self.assertEqual(value['ingredients'], [])
        self.value = diagnostic_result('x', status='failed', code='invalid_json', message='Test only')
        self.value['issues'][0]['severity'] = 'info'
        self.assert_invalid('/issues', 'status')

    def test_failed_cannot_retain_bad_entities_or_bypass_schema(self):
        self.value.update(status='failed')
        self.issue('', 'invalid_schema', 'error')
        self.assert_invalid('/ingredients', 'status')
        self.value = diagnostic_result('x', status='failed', code='invalid_schema', message='Test only')
        self.value['ingredients'] = 'invalid'
        self.assert_invalid('/ingredients', 'type')

    def test_source_required_for_parsed_and_partial(self):
        self.value['source'] = None
        self.assert_invalid('/source', 'status')
        self.partial()
        self.issue('')
        self.assert_invalid('/source', 'status')

    def test_issue_pointer_syntax_and_nonempty_reason(self):
        for path in ('not-a-pointer', '/bad~2escape'):
            with self.subTest(path=path):
                self.value['issues'] = [{'code': 'test', 'severity': 'info', 'path': path, 'message': 'test'}]
                self.assert_invalid('/issues/0/path', 'type')

    def test_model_provenance_missing_hashes_require_issues(self):
        provenance = self.value['provenance']
        provenance.update(parser_kind='model', model={'identifier': 'saved-output-only', 'digest': None}, settings={'temperature': 0})
        self.assert_invalid('/provenance/model/digest', 'issue')
        for key in ('model/digest', 'prompt_sha256', 'raw_output_sha256'):
            self.issue('/provenance/' + key, severity='info')
        self.assert_valid()
        provenance['model'] = None
        self.assert_invalid('/provenance/model', 'provenance')

    def test_design_provenance_and_non_json_settings(self):
        self.value['provenance']['model'] = {'identifier': 'not-a-real-inference', 'digest': None}
        self.assert_invalid('/provenance', 'provenance')
        for bad in ({'secret': {1, 2}}, {'nan': float('nan')}, {1: 'not-string-key'}):
            self.value = deepcopy(self.cases[0])
            self.value['provenance']['settings'] = bad
            self.assert_invalid('/provenance/settings', 'type')

    def test_circular_settings_are_rejected(self):
        settings = {}
        settings['cycle'] = settings
        self.value['provenance']['settings'] = settings
        self.assert_invalid('/provenance/settings', 'type')

    def test_group_single_target_and_display_order(self):
        self.value = deepcopy(self.cases[4])
        self.assertEqual([len(s['ingredient_ids']) for s in self.value['preparation_steps']], [3, 1])
        self.assert_valid()
        self.value['preparation_steps'].reverse()
        self.assert_invalid('/preparation_steps', 'order')

    def test_reordering_ingredients_does_not_change_ids(self):
        self.value = deepcopy(self.cases[4])
        original = {item['ingredient_id'] for item in self.value['ingredients']}
        self.value['ingredients'].reverse()
        self.assert_valid()
        self.assertEqual({item['ingredient_id'] for item in self.value['ingredients']}, original)

    def test_emoji_crlf_offsets_use_python_codepoints(self):
        prefix = '🥣\r\n'
        self.value['source']['text'] = prefix + self.value['source']['text']
        text = self.value['source']['text']
        self.value['source']['text_sha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
        changes = {}
        for e in self.value['evidence']:
            old = e['evidence_id']
            e['start'] += len(prefix)
            e['end'] += len(prefix)
            e['evidence_id'] = f"ev_{e['start']}_{e['end']}"
            changes[old] = e['evidence_id']
        for item in self.value['ingredients']:
            item['anchor']['start'] += len(prefix)
            item['anchor']['end'] += len(prefix)
            item['ingredient_id'] = f"ing_{item['anchor']['start']}_{item['anchor']['end']}"
            for link in item['evidence_links']:
                link['evidence_id'] = changes[link['evidence_id']]
        self.assert_valid()
        self.value['evidence'][0]['start'] += 1  # UTF-16 emoji offset would be wrong.
        self.assert_invalid('/evidence/0/evidence_text', 'evidence')

    def test_same_name_distinct_declarations_not_merged(self):
        self.value = diagnostic_result('repeated', status='unavailable', code='coverage_unknown', message='Test only')
        text = '鹽 2g；鹽 3g'
        self.value.update(status='parsed', review_status='unreviewed', issues=[])
        self.value['source'] = {'source_id': 'synthetic', 'reference': 'tests/test_recipe_parsing.py',
                                'kind': 'test_fixture', 'review_status': 'test_only', 'text': text,
                                'text_sha256': hashlib.sha256(text.encode()).hexdigest()}
        for start, quantity in ((0, 2), (5, 3)):
            e_id = f'ev_{start}_{start+4}'
            self.value['evidence'].append({'evidence_id': e_id, 'start': start, 'end': start+4, 'evidence_text': text[start:start+4]})
            item = deepcopy(self.cases[0]['ingredients'][1])
            item.update(ingredient_id=f'ing_{start}_{start+1}', anchor={'start': start, 'end': start+1},
                        quantity=quantity, quantity_raw=str(quantity),
                        evidence_links=[{'evidence_id': e_id, 'supports': ['name', 'quantity', 'unit']}])
            self.value['ingredients'].append(item)
        self.assert_valid()
        self.assertNotEqual(self.value['ingredients'][0]['ingredient_id'], self.value['ingredients'][1]['ingredient_id'])

    def test_load_returns_copy_and_raises_locatable_errors(self):
        result = load_recipe_parsing(self.value)
        result['ingredients'][0]['quantity'] = 999
        self.assertEqual(self.value['ingredients'][0]['quantity'], 200)
        self.value['contract'] = 'retrieval'
        with self.assertRaises(ContractValidationError) as caught:
            load_recipe_parsing(self.value)
        self.assertEqual(caught.exception.report.errors[0].path, '/contract')

    def test_mock_replays_exact_five_and_isolates_mutations(self):
        adapter = MockRecipeParsingAdapter()
        self.assertTrue(adapter.is_mock)
        self.assertEqual(len(adapter.input_ids), 5)
        for case in self.cases:
            result = adapter.parse(case['input_id'])
            self.assertEqual(result, case)
            result['source']['kind'] = 'production'
            self.assertEqual(adapter.parse(case['input_id'])['source']['kind'], 'test_fixture')

    def test_mock_unknown_and_invalid_input(self):
        adapter = MockRecipeParsingAdapter()
        result = adapter.parse('case_03')
        self.assert_valid(result)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['source'])
        for value in ('', ' ', None, 3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                adapter.parse(value)

    def test_mock_rejects_invalid_json_duplicate_keys_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixtures.json'
            for content in ('{', '[{"input_id":"x","input_id":"y"}]', '[]', '[NaN]'):
                with self.subTest(content=content):
                    path.write_text(content, encoding='utf-8')
                    with self.assertRaises(FixtureLoadError):
                        MockRecipeParsingAdapter(path)
            with self.assertRaises(FixtureLoadError):
                MockRecipeParsingAdapter(Path(directory) / 'missing.json')

    def test_mock_rejects_duplicates_invalid_contract_and_promoted_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixtures.json'
            duplicate = deepcopy(self.cases) + [deepcopy(self.cases[0])]
            promoted = deepcopy(self.cases)
            promoted[0]['review_status'] = 'approved'
            production = deepcopy(self.cases)
            production[0]['source'].update(kind='production', review_status='approved')
            invalid = deepcopy(self.cases)
            invalid[0]['ingredients'][0]['quantity'] = True
            for cases in (duplicate, promoted, production, invalid):
                path.write_text(json.dumps(cases), encoding='utf-8')
                with self.assertRaises(FixtureLoadError):
                    MockRecipeParsingAdapter(path)


class RecipeParsingBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads(DEFAULT_FIXTURE_PATH.read_text(encoding='utf-8'))
        self.value = deepcopy(self.cases[0])

    def model_context(self):
        self.value['provenance'] = {
            'parser_id': 'synthetic-model-boundary-test', 'parser_kind': 'model',
            'model': {'identifier': 'mock-no-inference', 'digest': 'synthetic-digest'},
            'prompt_sha256': 'a' * 64, 'raw_output_sha256': 'b' * 64,
            'settings': {'temperature': 0, 'num_predict': 4096},
        }
        return {'expected_input_id': self.value['input_id'],
                'expected_source': deepcopy(self.value['source']),
                'expected_provenance': deepcopy(self.value['provenance']),
                'expected_binding': deepcopy(self.value['retrieval_binding'])}

    def test_optional_marker_not_marked_and_required_are_distinct(self):
        optional = self.cases[2]['ingredients'][2]
        self.assertTrue(optional['optional'])
        self.assertEqual(optional['optional_state'], 'explicit_optional')
        by_id = {e['evidence_id']: e for e in self.cases[2]['evidence']}
        quotes = [by_id[link['evidence_id']]['evidence_text'] for link in optional['evidence_links']
                  if 'optionality' in link['supports']]
        self.assertTrue(any('可不加' in quote for quote in quotes))
        self.assertTrue(validate_recipe_parsing(self.cases[2]).valid)
        unmarked = self.value['ingredients'][0]
        self.assertFalse(unmarked['optional'])
        self.assertEqual(unmarked['optional_state'], 'not_marked')
        unmarked['optional_state'] = 'explicit_required'  # No corresponding original evidence.
        report = validate_recipe_parsing(self.value)
        self.assertTrue(any(e.code == 'evidence' and e.path == '/ingredients/0/evidence_links' for e in report.errors))

    def test_vague_amount_is_not_missing_or_known_zero(self):
        self.value = deepcopy(self.cases[1])
        vague = self.value['ingredients'][0]
        self.assertEqual((vague['quantity_raw'], vague['quantity_state']), ('適量', 'vague'))
        self.assertIsNone(vague['quantity'])
        self.assertTrue(vague['uncertainty'])
        self.assertTrue(validate_recipe_parsing(self.value).valid)
        for bad in (0, 1):
            vague['quantity'] = bad
            report = validate_recipe_parsing(self.value)
            self.assertTrue(any(e.code == 'quantity' and e.path == '/ingredients/0/quantity' for e in report.errors))
        vague['quantity'] = None
        vague['quantity_state'] = 'missing'  # Existing literal cannot become missing.
        self.assertFalse(validate_recipe_parsing(self.value).valid)

    def test_missing_quantity_is_known_absence_of_extracted_amount_not_empty_recipe(self):
        self.value = deepcopy(self.cases[3])
        self.assertEqual(self.value['status'], 'parsed')
        self.assertEqual(len(self.value['ingredients']), 3)
        self.assertTrue(all(item['quantity'] is None and item['unit'] is None
                            and item['quantity_state'] == 'missing' and item['uncertainty']
                            for item in self.value['ingredients']))
        self.assertTrue(validate_recipe_parsing(self.value).valid)
        self.value['ingredients'][0]['uncertainty'] = False
        self.assertFalse(validate_recipe_parsing(self.value).valid)

    def test_unresolved_optionality_requires_visible_warning_and_needs_review(self):
        item = self.value['ingredients'][0]
        item.update(optional=False, optional_state='unresolved', uncertainty=True)
        self.value.update(status='partial', review_status='needs_review')
        self.value['issues'] = [{'code': 'optional_unresolved', 'severity': 'warning',
                                 'path': '/ingredients/0/optional_state', 'message': 'Optional scope is unknown'}]
        self.assertTrue(validate_recipe_parsing(self.value).valid)
        self.value['issues'][0]['severity'] = 'info'
        self.assertFalse(validate_recipe_parsing(self.value).valid)

    def test_model_output_matching_caller_context_passes_without_promotion(self):
        context = self.model_context()
        original = deepcopy(self.value)
        result = load_model_recipe_parsing(self.value, **context)
        self.assertEqual(result, original)
        result['source']['text'] = 'Changed returned copy'
        self.assertEqual(self.value, original)
        self.assertEqual(context['expected_source'], original['source'])

    def test_model_self_approval_rejected_even_when_structurally_valid(self):
        context = self.model_context()
        self.value['review_status'] = 'approved'
        self.assertTrue(validate_recipe_parsing(self.value).valid)  # Archival schema, not an authorization.
        report = validate_model_recipe_parsing(self.value, **context)
        self.assertEqual([(e.path, e.code) for e in report.errors], [('/review_status', 'untrusted_approval')])
        with self.assertRaises(ContractValidationError):
            load_model_recipe_parsing(self.value, **context)
        self.assertEqual(self.value['review_status'], 'approved')  # Fail closed; do not silently rewrite.

    def test_source_promotion_and_forged_source_identity_rejected(self):
        for field, replacement in (('kind', 'production'), ('source_id', 'forged'),
                                    ('reference', 'https://example.org/forged')):
            with self.subTest(field=field):
                self.value = deepcopy(self.cases[0])
                context = self.model_context()
                if field == 'kind':
                    self.value['source'].update(kind='production', review_status='approved')
                else:
                    self.value['source'][field] = replacement
                self.assertTrue(validate_recipe_parsing(self.value).valid)
                report = validate_model_recipe_parsing(self.value, **context)
                self.assertTrue(any(e.code == 'source_mismatch' and e.path == '/source/' + field for e in report.errors))

    def test_caller_approved_source_does_not_approve_model_result(self):
        self.value['source'].update(kind='production', review_status='approved')
        context = self.model_context()
        self.assertTrue(validate_model_recipe_parsing(self.value, **context).valid)
        self.assertEqual(self.value['review_status'], 'unreviewed')
        self.value['review_status'] = 'approved'
        self.assertFalse(validate_model_recipe_parsing(self.value, **context).valid)

    def test_model_cannot_change_source_text_even_with_recomputed_hash(self):
        context = self.model_context()
        text = self.value['source']['text'] + ' 其他說明'
        self.value['source'].update(text=text, text_sha256=hashlib.sha256(text.encode()).hexdigest())
        self.assertTrue(validate_recipe_parsing(self.value).valid)
        report = validate_model_recipe_parsing(self.value, **context)
        self.assertEqual({e.path for e in report.errors}, {'/source/text', '/source/text_sha256'})

    def test_model_cannot_forge_parser_identity_settings_binding_or_input_id(self):
        for field in ('provenance', 'retrieval_binding', 'input_id'):
            with self.subTest(field=field):
                self.value = deepcopy(self.cases[0])
                context = self.model_context()
                if field == 'provenance':
                    self.value['provenance'] = deepcopy(self.cases[0]['provenance'])  # Claims design/manual identity.
                elif field == 'retrieval_binding':
                    self.value[field] = {'source_id': self.value['source']['source_id'],
                                         'recipe_id': 'forged', 'catalog_sha256': 'a' * 64}
                else:
                    self.value[field] = 'forged'
                report = validate_model_recipe_parsing(self.value, **context)
                self.assertFalse(report.valid)
                self.assertTrue(any(e.path.startswith('/' + field) for e in report.errors))
        self.value = deepcopy(self.cases[0])
        context = self.model_context()
        self.value['provenance']['settings']['temperature'] = 1
        self.assertTrue(any(e.path == '/provenance/settings' and e.code == 'provenance_mismatch'
                            for e in validate_model_recipe_parsing(self.value, **context).errors))

    def test_issue_pointers_must_resolve_and_errors_have_stable_identity(self):
        for path in ('/ingredients/99/quantity', '/ingredients/00', '/ingredients/-', '/unknown'):
            with self.subTest(path=path):
                self.value['issues'] = [{'code': 'coverage_unknown', 'severity': 'info',
                                         'path': path, 'message': 'Test reason'}]
                report = validate_recipe_parsing(self.value)
                self.assertEqual([(e.path, e.code) for e in report.errors], [('/issues/0/path', 'issue_path')])
                self.assertTrue(report.errors[0].message)
                self.assertEqual(report, validate_recipe_parsing(self.value))

    def test_root_and_escaped_issue_pointers_remain_valid(self):
        self.model_context()
        self.value['provenance']['settings']['a/b~c'] = None
        for path in ('', '/ingredients/0/quantity', '/provenance/settings/a~1b~0c'):
            with self.subTest(path=path):
                self.value['issues'] = [{'code': 'custom_future_code', 'severity': 'info',
                                         'path': path, 'message': 'Original declaration'}]
                self.assertTrue(validate_recipe_parsing(self.value).valid)

    def test_diagnostic_empty_states_validate_against_known_context(self):
        for status in ('unavailable', 'failed'):
            with self.subTest(status=status):
                self.value = deepcopy(self.cases[0])
                context = self.model_context()
                self.value.update(status=status, ingredients=[], preparation_steps=[], evidence=[],
                                  review_status='needs_review', issues=[{'code': 'timeout',
                                  'severity': 'error' if status == 'failed' else 'info', 'path': '', 'message': 'Synthetic only'}])
                self.assertTrue(validate_model_recipe_parsing(self.value, **context).valid)


if __name__ == '__main__':
    unittest.main()
