"""Volume x density method: synthetic numbers and placeholder density rows, never real food data."""
from copy import deepcopy
import csv
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import json
from pathlib import Path
import tempfile
import unittest

from src.portion import assess_portions, calibration_blockers, validate_calibration, validate_portion_estimate
from src.portion.reference_card import fit_reference_card_calibration
from src.portion.volume_density import (DENSITY_COLUMNS, METHOD, MODEL_CONTRACT, estimate_volume,
                                        estimate_with_volume_density, fit_volume_density_calibration,
                                        load_density_table)
from src.visual_ingredients import ingredient_annotation_envelope
from tests.test_portion_reference_card import CARD, approved, rice_evidence, synthetic_samples

IDENTITY = dict(calibration_id='rice-volume-v0', nominal='0.8', dataset_reference='private://weighing/v0',
                producer_ref='synthetic-calibrator')


def density_rows():
    """Placeholder rows (synthetic numbers and example.org URLs), not published densities."""
    def row(food, shape, grams, volume):
        return {'food': food, 'shape': shape, 'grams': grams, 'volume_ml': volume,
                'source_title': 'synthetic placeholder', 'source_url': 'https://example.org/' + food,
                'retrieved_at': '2026-11-20', 'entered_by': 'synthetic-entry'}
    return [row('cooked_rice_region', 'mound', '200', '250'), row('cooked_noodle_region', 'mound', '150', '250'),
            row('visible_fried_egg', 'layer', '100', '100')]


def fit(samples=None, densities=None, **overrides):
    arguments = dict(IDENTITY, **overrides)
    table = load_density_table(density_rows()) if densities is None else densities
    return fit_volume_density_calibration(synthetic_samples() if samples is None else samples, table, **arguments)


def evidence(food='cooked_rice_region', box=(0, 0, 1000, 1000)):  # 1000 px = 10 cm with CARD, so 100 cm^2
    item = rice_evidence(box)
    item['ingredient_candidate'] = food
    return item


class DensityTableTests(unittest.TestCase):
    def test_density_is_grams_over_volume_and_keeps_its_source(self):
        table = load_density_table(density_rows())
        self.assertEqual(table['cooked_rice_region']['density_g_per_cm3'], '0.8')
        self.assertEqual(table['cooked_noodle_region']['density_g_per_cm3'], '0.6')
        self.assertEqual(table['cooked_rice_region']['source_url'], 'https://example.org/cooked_rice_region')
        self.assertEqual(table['visible_fried_egg']['shape'], 'layer')

    def test_bad_rows_are_refused(self):
        def changed(**values):
            rows = density_rows()
            rows[0].update(values)
            return rows
        missing = density_rows()
        del missing[0]['source_url']
        cases = (missing, changed(source_title=' '), density_rows() + density_rows()[:1], changed(shape='bowl'),
                 changed(grams='0'), changed(grams='-1'), changed(grams='1e3'), changed(grams='10', volume_ml='1'),
                 changed(source_url='fdc.nal.usda.gov'), changed(retrieved_at='20/11/2026'), [])
        for rows in cases:
            with self.assertRaises(ValueError, msg=rows):
                load_density_table(rows)


class VolumeDensityFitTests(unittest.TestCase):
    def test_fit_learns_effective_height_with_area_ratio_coverage(self):
        samples = synthetic_samples()
        before = deepcopy(samples)
        record, model, diagnostics = fit(samples)
        self.assertEqual(samples, before)
        self.assertEqual(validate_calibration(record), [])
        self.assertEqual((record['status'], record['method'], model['contract']), ('draft', METHOD, MODEL_CONTRACT))
        self.assertEqual(calibration_blockers(approved(record)), [])
        # Synthetic ratios are 1.0-1.4 g/cm^2 at density 0.8, so effective heights are 1.25-1.75 cm.
        height = model['height_cm']['cooked_rice_region']
        self.assertTrue(Decimal('1.25') <= Decimal(height['low']) < Decimal(height['high']) <= Decimal('1.75'))
        self.assertTrue(Decimal('1.25') <= Decimal(diagnostics['effective_height_cm_median']['cooked_rice_region'])
                        <= Decimal('1.75'))
        self.assertEqual(model['shape_height_cm']['mound']['foods'], ['cooked_rice_region'])
        self.assertNotIn('layer', model['shape_height_cm'])
        self.assertEqual(record['validity_scope']['ingredient_candidates'], ['cooked_rice_region'])
        # For a weighed food the density cancels: coverage matches the area-ratio method on the same samples.
        area_record, _, _ = fit_reference_card_calibration(samples, **IDENTITY)
        self.assertEqual(record['coverage'], area_record['coverage'])
        self.assertEqual(record['dataset'], area_record['dataset'])

    def test_every_weighed_food_needs_a_sourced_density(self):
        table = load_density_table(density_rows()[1:])
        with self.assertRaises(ValueError) as caught:
            fit(densities=table)
        self.assertEqual(str(caught.exception), 'density_missing: cooked_rice_region')
        with self.assertRaises(ValueError):
            fit(samples=synthetic_samples(groups=('only-day',)))


class VolumeDensityEstimateTests(unittest.TestCase):
    def setUp(self):
        self.record, self.model, _ = fit()
        self.final = approved(self.record)

    def test_volume_times_density_gives_the_contract_estimate(self):
        height = self.model['height_cm']['cooked_rice_region']
        volume = estimate_volume(evidence(), CARD, model=self.model)
        self.assertEqual((volume['basis'], volume['coverage_validated'], volume['area_cm2']),
                         ('weighed_food', True, '100'))
        self.assertEqual(Decimal(volume['volume_cm3']['low']),
                         (Decimal(height['low']) * 100).quantize(Decimal('0.1'), rounding=ROUND_FLOOR))
        self.assertEqual(Decimal(volume['mass_g']['high']),
                         (Decimal('0.8') * Decimal(height['high']) * 100).quantize(Decimal('0.1'), rounding=ROUND_CEILING))
        estimate = estimate_with_volume_density(evidence(), CARD, model=self.model, record=self.final,
                                                estimate_id='est-1', producer_ref='reference_card_volume_density/0')
        self.assertEqual(validate_portion_estimate(estimate), [])
        self.assertEqual((estimate['method'], estimate['review']['status']), (METHOD, 'not_requested'))
        self.assertEqual({k: estimate['mass_g'][k] for k in ('low', 'high')},
                         {k: volume['mass_g'][k] for k in ('low', 'high')})
        assessment = assess_portions(ingredient_annotation_envelope([evidence()], reason='test'),
                                     estimates=[estimate], calibrations=[self.final])
        self.assertEqual(assessment['items'][0]['status'], 'estimated')

    def test_unweighed_food_gets_a_volume_but_never_an_estimate(self):
        noodle = estimate_volume(evidence('cooked_noodle_region'), CARD, model=self.model)
        self.assertEqual((noodle['basis'], noodle['coverage_validated'], noodle['mass_g']['coverage']),
                         ('same_shape_unweighed', False, None))
        self.assertEqual(noodle['height_cm'], {k: self.model['shape_height_cm']['mound'][k] for k in ('low', 'high')})
        self.assertEqual(noodle['density_g_per_cm3'], '0.6')
        for item, code in ((evidence('cooked_noodle_region'), 'ingredient_not_calibrated'),):
            with self.assertRaises(ValueError) as caught:
                estimate_with_volume_density(item, CARD, model=self.model, record=self.final,
                                             estimate_id='e', producer_ref='p')
            self.assertEqual(str(caught.exception), code)
        for food, code in (('visible_fried_egg', 'no_height_for_shape: layer'),
                           ('braised_pork', 'density_missing: braised_pork')):
            with self.assertRaises(ValueError) as caught:
                estimate_volume(evidence(food), CARD, model=self.model)
            self.assertEqual(str(caught.exception), code)

    def test_inadmissible_or_tampered_inputs_refused(self):
        recipe = evidence()
        recipe['evidence_type'] = 'inferred_from_recipe'
        edited = deepcopy(self.model)
        edited['densities']['cooked_rice_region']['density_g_per_cm3'] = '1.6'
        other_record, _, _ = fit(calibration_id='another-run')
        _, area_model, _ = fit_reference_card_calibration(synthetic_samples(), **IDENTITY)
        for item, model, record, code in ((recipe, self.model, self.final, 'recipe_prior_not_visual_evidence'),
                                          (evidence(), edited, self.final, 'volume_density_model_invalid'),
                                          (evidence(), area_model, self.final, 'volume_density_model_invalid'),
                                          (evidence(), self.model, approved(other_record), 'model_record_mismatch')):
            with self.assertRaises(ValueError) as caught:
                estimate_with_volume_density(item, CARD, model=model, record=record, estimate_id='e', producer_ref='p')
            self.assertEqual(str(caught.exception), code)


class VolumeDensityToolTests(unittest.TestCase):
    def test_density_csv_template_and_model_file_name(self):
        from tools.build_portion_calibration import load_density_csv, write_calibration
        template = Path(__file__).resolve().parents[1] / 'docs/examples/food_density_template.csv'
        self.assertEqual(template.read_text(encoding='utf-8').strip().split(','), list(DENSITY_COLUMNS))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'densities.csv'
            with path.open('w', encoding='utf-8', newline='') as handle:
                writer = csv.DictWriter(handle, DENSITY_COLUMNS)
                writer.writeheader()
                writer.writerows(density_rows())
            table = load_density_csv(path)
            self.assertEqual(table, load_density_table(density_rows()))
            record, model, diagnostics = fit(densities=table)
            work = Path(folder) / 'work'
            out = write_calibration(work / 'vol-v0', record, model, diagnostics, root=work)
            self.assertEqual(json.loads((out / 'volume_density_model.json').read_text(encoding='utf-8')), model)
            self.assertFalse((out / 'reference_card_model.json').exists())
            path.write_text('food,grams\nx,1\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_density_csv(path)


if __name__ == '__main__':
    unittest.main()
