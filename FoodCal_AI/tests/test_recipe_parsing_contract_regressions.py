"""Offline reproductions using synthetic/design fixtures, never private audits."""
from copy import deepcopy
import hashlib
import json
import unittest

from src.recipe_parsing import validate_recipe_parsing
from src.recipe_parsing.mock import DEFAULT_FIXTURE_PATH


class ContractOutputRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(DEFAULT_FIXTURE_PATH.read_text(encoding='utf-8'))

    def minimal_case(self):
        value = deepcopy(self.cases[0])
        text = '鹽 2g'
        value['input_id'] = 'contract-regression-salt'
        value['source'].update(source_id='foodcal-contract-regression-fixtures',
            reference='tests/test_recipe_parsing_contract_regressions.py#salt',
            text=text, text_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest())
        value['ingredients'] = [{
            'ingredient_id': 'ing_0_1', 'anchor': {'start': 0, 'end': 1},
            'ingredient_name': '鹽', 'quantity': 2, 'quantity_raw': '2',
            'quantity_state': 'exact', 'quantity_rule': 'numeric_literal',
            'unit': 'g', 'optional': False, 'optional_state': 'not_marked',
            'uncertainty': False,
            'evidence_links': [{'evidence_id': 'ev_0_4',
                                'supports': ['name', 'quantity', 'unit']}]}]
        value['evidence'] = [{'evidence_id': 'ev_0_4', 'start': 0, 'end': 4,
                              'evidence_text': text}]
        value['preparation_steps'] = []
        value['issues'] = []
        return value

    def checked_errors(self, value):
        before = deepcopy(value)
        report = validate_recipe_parsing(value)
        self.assertEqual(value, before)  # No coercion, repair or ID replacement.
        return [(e.path, e.code) for e in report.errors]

    def evidence_prefix_fault(self, value, index):
        evidence = value['evidence'][index]
        old = evidence['evidence_id']
        new = f"ing_{evidence['start']}_{evidence['end']}"
        evidence['evidence_id'] = new
        for ingredient in value['ingredients']:
            for link in ingredient['evidence_links']:
                if link['evidence_id'] == old:
                    link['evidence_id'] = new
        # References remain present: isolate namespace from dangling references.

    def declaration_anchor_fault(self, value, index):
        ingredient = value['ingredients'][index]
        evidence_id = ingredient['evidence_links'][0]['evidence_id']
        evidence = next(e for e in value['evidence'] if e['evidence_id'] == evidence_id)
        ingredient['anchor'] = {'start': evidence['start'], 'end': evidence['end']}
        ingredient['ingredient_id'] = f"ing_{evidence['start']}_{evidence['end']}"
        # ID matches declared anchor: isolate incorrect name span, not ID math.

    def test_minimal_positive_array_roles_and_distinct_spans(self):
        value = self.minimal_case()
        self.assertEqual(self.checked_errors(value), [])
        self.assertNotEqual(value['ingredients'][0]['anchor']['end'],
                            value['evidence'][0]['end'])

    def test_scalar_supports_minimal_and_all_twelve_observed_locations(self):
        value = self.minimal_case()
        value['ingredients'][0]['evidence_links'][0]['supports'] = 'name'
        self.assertEqual(self.checked_errors(value),
                         [('/ingredients/0/evidence_links/0/supports', 'type')])
        baseline = deepcopy(self.cases[0])
        for ingredient in baseline['ingredients']:
            evidence_id = ingredient['evidence_links'][0]['evidence_id']
            ingredient['evidence_links'] = [
                {'evidence_id': evidence_id, 'supports': [role]}
                for role in ('name', 'quantity', 'unit')]
        self.assertEqual(self.checked_errors(baseline), [])
        for i in range(4):
            for j, role in enumerate(('name', 'quantity', 'unit')):
                with self.subTest(ingredient=i, role=role):
                    value = deepcopy(baseline)
                    value['ingredients'][i]['evidence_links'][j]['supports'] = role
                    self.assertEqual(self.checked_errors(value),
                        [(f'/ingredients/{i}/evidence_links/{j}/supports', 'type')])

    def test_evidence_prefix_minimal_and_all_four_observed_spans(self):
        for baseline, indices in ((self.minimal_case(), range(1)),
                                  (self.cases[0], range(4))):
            for i in indices:
                with self.subTest(input_id=baseline['input_id'], evidence=i):
                    value = deepcopy(baseline)
                    self.assertEqual(self.checked_errors(value), [])
                    self.evidence_prefix_fault(value, i)
                    self.assertEqual(self.checked_errors(value),
                                     [(f'/evidence/{i}/evidence_id', 'id')])

    def test_full_declaration_anchor_minimal_and_all_four_observed_spans(self):
        for baseline, indices in ((self.minimal_case(), range(1)),
                                  (self.cases[0], range(4))):
            for i in indices:
                with self.subTest(input_id=baseline['input_id'], ingredient=i):
                    value = deepcopy(baseline)
                    self.assertEqual(self.checked_errors(value), [])
                    self.declaration_anchor_fault(value, i)
                    self.assertEqual(self.checked_errors(value),
                                     [(f'/ingredients/{i}/ingredient_name', 'evidence')])

    def test_combined_faults_stop_at_structure_not_false_semantic_success(self):
        value = self.minimal_case()
        self.declaration_anchor_fault(value, 0)
        self.evidence_prefix_fault(value, 0)
        value['ingredients'][0]['evidence_links'][0]['supports'] = 'name'
        self.assertEqual(self.checked_errors(value),
                         [('/ingredients/0/evidence_links/0/supports', 'type')])

    def test_step_supports_also_requires_array(self):
        value = deepcopy(self.cases[4])
        self.assertEqual(self.checked_errors(value), [])
        value['preparation_steps'][0]['evidence_links'][0]['supports'] = 'action'
        self.assertEqual(self.checked_errors(value),
                         [('/preparation_steps/0/evidence_links/0/supports', 'type')])

    def test_null_optional_and_uncertainty_are_rejected_without_coercion(self):
        value = deepcopy(self.cases[0])
        self.assertEqual(self.checked_errors(value), [])
        for ingredient in value['ingredients']:
            ingredient['optional'] = None
            ingredient['uncertainty'] = None
        expected = [(f'/ingredients/{i}/{field}', 'type')
                    for i in range(4) for field in ('optional', 'uncertainty')]
        self.assertEqual(self.checked_errors(value), expected)


if __name__ == '__main__':
    unittest.main()
