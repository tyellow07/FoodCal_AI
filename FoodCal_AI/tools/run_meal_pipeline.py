"""Run the photo pipeline over an evaluation workspace (docs/campus_meal_workflow_v0.md).

estimate  accepted annotations + reference card + calibration -> portion estimates (review not_requested)
review    apply an independent reviewer's decisions CSV -> reviewed estimates (new file)
energy    reviewed estimates + photo-calculation nutrition bases + consumption assertion
          -> per-meal photo_energy_estimate and predictions.csv for tools.evaluate_meals

Every gate stays in force: a meal gets a calorie interval only when every visible item has an approved,
in-scope calibrated estimate, an accepted independent review and a fully passed nutrition basis.
Recipe candidates are never read. All outputs go to new paths under the Git-ignored work/ directory.
"""
import argparse
from copy import deepcopy
import csv
import json
from pathlib import Path

from src.evaluation import prediction_from_energy_estimate
from src.portion import canonical_sha256, estimate_photo_energy, validate_portion_estimate
from src.portion.reference_card import estimate_with_reference_card
from tools.ingredient_pilot import ROOT, visual_evidence_from_annotation
from tools.meal_workspace import accepted_annotations, load_manifest, read_csv

PRODUCER = 'reference_card_area_ratio/0'
DECISION_COLUMNS = ('estimate_id', 'status', 'reviewer_ref', 'reviewed_at')
PREDICTION_COLUMNS = ('meal_id', 'status', 'low_kcal', 'high_kcal')


def _load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _new_path(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root):
        raise ValueError('output_must_be_under_ignored_work')
    if path.exists():
        raise ValueError('output_already_exists_no_overwrite')
    return path


def _write_json(path, value, root):
    path = _new_path(path, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path


def meal_envelopes(workspace):
    """meal_id -> (envelope or None, annotation or None, reason) for an evaluation workspace."""
    manifest = load_manifest(workspace, purpose='evaluation')
    out = {}
    for meal_id, (item, annotation, reason) in accepted_annotations(workspace, manifest).items():
        out[meal_id] = ((visual_evidence_from_annotation(item, annotation), annotation, None) if annotation
                        else (None, None, reason))
    return out


def estimate_meals(workspace, *, record, model):
    meals = {}
    for meal_id, (envelope, annotation, reason) in meal_envelopes(workspace).items():
        entry = {'estimates': [], 'skipped': [], 'meal_reason': reason}
        meals[meal_id] = entry
        if envelope is None:
            continue
        card = annotation.get('reference_card')
        for item in envelope['visual_ingredient_evidence']:
            annotation_id = item['evidence_source']['annotation_id']
            if card is None:
                entry['skipped'].append({'annotation_id': annotation_id, 'reason': 'reference_card_missing'})
                continue
            try:
                entry['estimates'].append(estimate_with_reference_card(
                    item, card['bounding_box'], model=model, record=record,
                    estimate_id=f'{meal_id}:{annotation_id}:{record["calibration_id"]}', producer_ref=PRODUCER))
            except ValueError as error:
                entry['skipped'].append({'annotation_id': annotation_id, 'reason': str(error)})
    return {'contract': 'foodcal.meal_portion_estimates', 'schema_version': 0, 'usage': 'diagnostic/test_only',
            'calibration_id': record['calibration_id'], 'calibration_record_sha256': canonical_sha256(record),
            'meals': meals}


def apply_reviews(estimates, decisions):
    """Copy with reviewer decisions applied; independence and record completeness are re-validated."""
    output = deepcopy(estimates)
    index = {e['estimate_id']: e for m in output['meals'].values() for e in m['estimates']}
    seen = set()
    for row in decisions:
        if row['estimate_id'] not in index:
            raise ValueError('decision_for_unknown_estimate: ' + row['estimate_id'])
        if row['estimate_id'] in seen:
            raise ValueError('duplicate_decision: ' + row['estimate_id'])
        if row['status'] not in ('accepted', 'rejected'):
            raise ValueError('decision_status_must_be_accepted_or_rejected')
        seen.add(row['estimate_id'])
        estimate = index[row['estimate_id']]
        estimate['review'] = {'status': row['status'], 'reviewer_ref': row['reviewer_ref'],
                              'reviewed_at': row['reviewed_at']}
        issues = validate_portion_estimate(estimate)
        if issues:
            raise ValueError(row['estimate_id'] + ': ' + ','.join(i['code'] for i in issues))
    return output


def energy_meals(workspace, *, estimates, record, bases, consumption_by, consumption_evidence):
    if estimates.get('calibration_record_sha256') != canonical_sha256(record):
        raise ValueError('estimates_were_made_from_a_different_calibration_record')
    consumption = {'status': 'whole_visible_portion_asserted', 'asserted_by': consumption_by,
                   'evidence_ref': consumption_evidence}
    results, predictions = {}, []
    for meal_id, (envelope, _, reason) in meal_envelopes(workspace).items():
        if envelope is None:
            results[meal_id] = {'status': 'unavailable', 'reasons': [reason]}
            predictions.append(prediction_from_energy_estimate(meal_id, None))
            continue
        own = estimates['meals'].get(meal_id, {}).get('estimates', [])
        energy = estimate_photo_energy(envelope, estimates=own, calibrations=[record], nutrition_bases=bases,
                                       consumption=consumption)
        results[meal_id] = energy
        predictions.append(prediction_from_energy_estimate(meal_id, energy))
    return results, predictions


def main(argv=None, *, root=ROOT / 'work'):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    est = commands.add_parser('estimate')
    est.add_argument('--workspace', type=Path, required=True)
    est.add_argument('--record', type=Path, required=True, help='approved_record.json')
    est.add_argument('--model', type=Path, required=True, help='reference_card_model.json')
    est.add_argument('--out', type=Path, required=True, help='new directory under work/')
    rev = commands.add_parser('review')
    rev.add_argument('--estimates', type=Path, required=True)
    rev.add_argument('--decisions', type=Path, required=True, help='CSV: ' + ','.join(DECISION_COLUMNS))
    rev.add_argument('--out', type=Path, required=True, help='new JSON file under work/')
    eng = commands.add_parser('energy')
    eng.add_argument('--workspace', type=Path, required=True)
    eng.add_argument('--record', type=Path, required=True)
    eng.add_argument('--estimates', type=Path, required=True, help='reviewed estimates JSON')
    eng.add_argument('--bases', type=Path, required=True, help='JSON list of PhotoNutritionBasis records')
    eng.add_argument('--consumption-asserted-by', required=True, help='who asserts each meal was eaten whole')
    eng.add_argument('--consumption-evidence', required=True, help='shareable locator of that assertion')
    eng.add_argument('--out', type=Path, required=True, help='new directory under work/')
    args = parser.parse_args(argv)
    if args.command == 'estimate':
        output = estimate_meals(args.workspace, record=_load_json(args.record), model=_load_json(args.model))
        out = _new_path(args.out, root)
        _write_json(out / 'estimates.json', output, root)
        rows = [{'estimate_id': e['estimate_id'], 'status': '', 'reviewer_ref': '', 'reviewed_at': ''}
                for m in output['meals'].values() for e in m['estimates']]
        with (out / 'review_decisions_template.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, DECISION_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        summary = {'estimates': len(rows), 'skipped': sum(len(m['skipped']) for m in output['meals'].values())}
    elif args.command == 'review':
        output = apply_reviews(_load_json(args.estimates), read_csv(args.decisions, DECISION_COLUMNS))
        _write_json(args.out, output, root)
        summary = {'reviewed_estimates': str(args.out)}
    else:
        results, predictions = energy_meals(args.workspace, estimates=_load_json(args.estimates),
                                            record=_load_json(args.record), bases=_load_json(args.bases),
                                            consumption_by=args.consumption_asserted_by,
                                            consumption_evidence=args.consumption_evidence)
        out = _new_path(args.out, root)
        _write_json(out / 'results.json', results, root)
        with (out / 'predictions.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, PREDICTION_COLUMNS)
            writer.writeheader()
            writer.writerows(predictions)
        summary = {'meals': len(predictions), 'computed': sum(p['status'] == 'computed' for p in predictions),
                   'next': 'python -m tools.evaluate_meals --references <refs.csv> --predictions <out>/predictions.csv'}
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
