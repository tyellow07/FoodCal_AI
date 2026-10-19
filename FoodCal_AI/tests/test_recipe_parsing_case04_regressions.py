"""Independent synthetic regressions; never load or repair private model audits."""
from copy import deepcopy
import hashlib
import json
import unittest

from src.recipe_parsing import validate_recipe_parsing
from src.recipe_parsing.mock import DEFAULT_FIXTURE_PATH
from src.recipe_parsing.schema import validate_candidate_schema


class MissingQuantityOffsetRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cases = json.loads(DEFAULT_FIXTURE_PATH.read_text(encoding='utf-8'))
        cls.reference = next(c for c in cases if c['input_id'] == 'case_04_missing_weight')

    def fixture(self):
        value = deepcopy(self.reference)
        value['input_id'] = 'case04-offline-regression'
        value['source']['reference'] = 'tests/test_recipe_parsing_case04_regressions.py#synthetic'
        # Independent design baseline with a separate evidence registration per name.
        value['evidence'] = [{'evidence_id': 'ev_0_2', 'start': 0, 'end': 2,
                              'evidence_text': '加入'}] + value['evidence'][1:]
        value['preparation_steps'][0]['evidence_links'] = [
            {'evidence_id': 'ev_0_2', 'supports': ['action']}] + [
            {'evidence_id': i['evidence_links'][0]['evidence_id'], 'supports': ['targets']}
            for i in value['ingredients']]
        return value

    def errors(self, value):
        before = deepcopy(value)
        report = validate_recipe_parsing(value)
        self.assertEqual(value, before)
        return [(e.path, e.code) for e in report.errors]

    def evidence_offset_fault(self, value, index, start, end):
        ev = value['evidence'][index + 1]
        old = ev['evidence_id']
        ev.update(start=start, end=end, evidence_id=f'ev_{start}_{end}')
        for item in value['ingredients'] + value['preparation_steps']:
            for link in item['evidence_links']:
                if link['evidence_id'] == old:
                    link['evidence_id'] = ev['evidence_id']

    def anchor_offset_fault(self, value, index, start, end):
        item = value['ingredients'][index]
        old = item['ingredient_id']
        item.update(anchor={'start': start, 'end': end}, ingredient_id=f'ing_{start}_{end}')
        for step in value['preparation_steps']:
            step['ingredient_ids'] = [item['ingredient_id'] if i == old else i
                                      for i in step['ingredient_ids']]

    def uncertainty_fault(self, index):
        value = self.fixture()
        self.assertEqual(self.errors(value), [])
        value['ingredients'][index]['uncertainty'] = False
        self.assertEqual(self.errors(value), [(f'/ingredients/{index}/uncertainty', 'uncertainty')])

    def test_missing_garlic_requires_uncertainty(self):
        self.uncertainty_fault(0)

    def test_missing_chili_requires_uncertainty(self):
        self.uncertainty_fault(1)

    def test_missing_black_beans_requires_uncertainty(self):
        self.uncertainty_fault(2)

    def test_chili_evidence_wrong_offset(self):
        value = self.fixture()
        valid_name_evidence = deepcopy(value['evidence'][2])
        self.evidence_offset_fault(value, 1, 6, 8)
        # Separate valid name evidence isolates the bad record from coverage errors.
        value['evidence'].append(valid_name_evidence)
        value['ingredients'][1]['evidence_links'].append({
            'evidence_id': valid_name_evidence['evidence_id'], 'supports': ['name']})
        self.assertEqual(self.errors(value), [('/evidence/2/evidence_text', 'evidence')])

    def test_black_beans_evidence_wrong_offset(self):
        value = self.fixture()
        valid_name_evidence = deepcopy(value['evidence'][3])
        self.evidence_offset_fault(value, 2, 9, 11)
        value['evidence'].append(valid_name_evidence)
        value['ingredients'][2]['evidence_links'].append({
            'evidence_id': valid_name_evidence['evidence_id'], 'supports': ['name']})
        self.assertEqual(self.errors(value), [('/evidence/3/evidence_text', 'evidence')])

    def test_chili_anchor_wrong_offset(self):
        value = self.fixture()
        # Keep correct broad literal evidence covering both old and bad anchors.
        value['evidence'][2].update(start=6, end=9, evidence_id='ev_6_9', evidence_text=' 辣椒')
        value['ingredients'][1]['evidence_links'][0]['evidence_id'] = 'ev_6_9'
        value['preparation_steps'][0]['evidence_links'][2]['evidence_id'] = 'ev_6_9'
        self.anchor_offset_fault(value, 1, 6, 8)
        self.assertEqual(self.errors(value), [('/ingredients/1/ingredient_name', 'evidence')])

    def test_black_beans_anchor_wrong_offset(self):
        value = self.fixture()
        value['evidence'][3].update(start=9, end=13, evidence_id='ev_9_13', evidence_text=', 豆豉')
        value['ingredients'][2]['evidence_links'][0]['evidence_id'] = 'ev_9_13'
        value['preparation_steps'][0]['evidence_links'][3]['evidence_id'] = 'ev_9_13'
        self.anchor_offset_fault(value, 2, 9, 11)
        self.assertEqual(self.errors(value), [('/ingredients/2/ingredient_name', 'evidence')])

    def test_exact_seven_fault_combination_passes_schema_not_contract(self):
        value = self.fixture()
        self.assertEqual(self.errors(value), [])
        for item in value['ingredients']:
            item['uncertainty'] = False
        for index, start, end in ((1, 6, 8), (2, 9, 11)):
            self.evidence_offset_fault(value, index, start, end)
            self.anchor_offset_fault(value, index, start, end)
        candidate = {k: value[k] for k in ('ingredients', 'preparation_steps', 'evidence', 'issues')}
        self.assertTrue(validate_candidate_schema(candidate).valid)
        self.assertEqual(self.errors(value), [
            ('/evidence/2/evidence_text', 'evidence'),
            ('/evidence/3/evidence_text', 'evidence'),
            ('/ingredients/0/uncertainty', 'uncertainty'),
            ('/ingredients/1/ingredient_name', 'evidence'),
            ('/ingredients/1/uncertainty', 'uncertainty'),
            ('/ingredients/2/ingredient_name', 'evidence'),
            ('/ingredients/2/uncertainty', 'uncertainty')])

    def single_missing(self, text, name):
        value = self.fixture()
        start = text.index(name)
        end = start + len(name)
        value['source'].update(text=text, text_sha256=hashlib.sha256(text.encode()).hexdigest())
        item = deepcopy(value['ingredients'][0])
        item.update(ingredient_id=f'ing_{start}_{end}', anchor={'start': start, 'end': end},
                    ingredient_name=name, evidence_links=[{'evidence_id': f'ev_{start}_{end}', 'supports': ['name']}])
        value.update(ingredients=[item], preparation_steps=[], evidence=[{
            'evidence_id': f'ev_{start}_{end}', 'start': start, 'end': end, 'evidence_text': name}],
            issues=[value['issues'][0]])
        return value

    def test_unicode_codepoints_fullwidth_spaces_combining_and_crlf(self):
        for prefix in ('🥕：, ', '加入：\r\n ', 'e\u0301，　'):
            with self.subTest(prefix=prefix):
                value = self.single_missing(prefix + '辣椒', '辣椒')
                self.assertEqual(self.errors(value), [])
                self.assertEqual(value['ingredients'][0]['anchor']['start'], len(prefix))

    def test_utf16_and_utf8_offsets_are_rejected(self):
        text = '🥕： 辣椒'
        for start in (len('🥕： '.encode('utf-16-le')) // 2, len('🥕： '.encode('utf-8'))):
            with self.subTest(start=start):
                value = self.single_missing(text, '辣椒')
                ev = value['evidence'][0]
                ev.update(start=start, end=start + 2, evidence_id=f'ev_{start}_{start + 2}')
                value['ingredients'][0]['evidence_links'][0]['evidence_id'] = ev['evidence_id']
                self.anchor_offset_fault(value, 0, start, start + 2)
                self.assertIn(('/evidence/0/evidence_text', 'evidence'), self.errors(value))

    def test_end_is_exclusive_and_missing_is_not_zero(self):
        value = self.single_missing(' 大蒜 ', '大蒜')
        self.assertEqual(self.errors(value), [])
        self.anchor_offset_fault(value, 0, 1, 2)
        self.assertIn(('/ingredients/0/ingredient_name', 'evidence'), self.errors(value))
        value = self.single_missing('大蒜', '大蒜')
        value['ingredients'][0]['quantity'] = 0
        self.assertIn(('/ingredients/0/quantity', 'quantity'), self.errors(value))


if __name__ == '__main__':
    unittest.main()
