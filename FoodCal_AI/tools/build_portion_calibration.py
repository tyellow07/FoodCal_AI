"""Fit the reference-card portion calibration from a private weighing CSV (see docs/portion_calibration_protocol_v0.md).

Default: read directly weighed samples and write a draft calibration record, the fitted model and diagnostics
under the Git-ignored work/ directory. `approve` records an explicit human approval (approver must differ from
the producer) as a separate approved_record.json; the draft is kept. Nothing is downloaded or trained.
"""
import argparse
from copy import deepcopy
import csv
import json
from pathlib import Path

from src.portion import calibration_blockers, validate_calibration
from src.portion.reference_card import fit_reference_card_calibration

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = ('sample_id', 'source_group', 'ingredient_candidate', 'region_x_min', 'region_y_min', 'region_x_max',
           'region_y_max', 'card_x_min', 'card_y_min', 'card_x_max', 'card_y_max', 'mass_g', 'scale_resolution_g',
           'measured_by')


def load_weighing_csv(path):
    """CSV rows -> sample dicts. Pixel coordinates stay exact decimal strings; mass is never converted to float."""
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError('weighing_csv_columns_mismatch')
        samples = []
        for row in reader:
            if any(row[k] is None or not row[k].strip() for k in COLUMNS):
                raise ValueError('weighing_csv_blank_cell')
            row = {k: row[k].strip() for k in COLUMNS}
            samples.append({'sample_id': row['sample_id'], 'source_group': row['source_group'],
                            'ingredient_candidate': row['ingredient_candidate'],
                            'region_px': [row[f'region_{k}'] for k in ('x_min', 'y_min', 'x_max', 'y_max')],
                            'card_px': [row[f'card_{k}'] for k in ('x_min', 'y_min', 'x_max', 'y_max')],
                            'mass_g': row['mass_g'], 'scale_resolution_g': row['scale_resolution_g'],
                            'measured_by': row['measured_by']})
    return samples


def write_calibration(out_dir, record, model, diagnostics, *, root=ROOT / 'work'):
    out_dir, root = Path(out_dir).resolve(), Path(root).resolve()
    if not out_dir.is_relative_to(root):
        raise ValueError('output_must_be_under_ignored_work')
    if out_dir.exists():
        raise ValueError('output_already_exists_no_overwrite')
    out_dir.mkdir(parents=True)
    for name, value in (('calibration_record.json', record), ('reference_card_model.json', model),
                        ('diagnostics.json', diagnostics)):
        (out_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return out_dir


def approve_calibration(record, *, approver_ref, approved_at):
    """Approved copy of a valid draft; refuses self-approval and never lowers the contract floors."""
    if validate_calibration(record) or record['status'] != 'draft':
        raise ValueError('valid_draft_record_required')
    approved = deepcopy(record)
    approved.update(status='approved', approval={'status': 'approved', 'approver_ref': approver_ref,
                                                 'approved_at': approved_at})
    issues = validate_calibration(approved)
    if issues:
        raise ValueError('approval_invalid:' + ','.join(i['code'] for i in issues))
    return approved, calibration_blockers(approved)


def approve_main(argv):
    parser = argparse.ArgumentParser(prog='build_portion_calibration approve')
    parser.add_argument('--calibration', type=Path, required=True, help='directory written by the fit step')
    parser.add_argument('--approver', required=True, help='a different person from the producer')
    parser.add_argument('--date', required=True, help='approval date, e.g. 2026-11-20')
    args = parser.parse_args(argv)
    folder = args.calibration.resolve()
    if not folder.is_relative_to((ROOT / 'work').resolve()):
        raise ValueError('calibration_must_be_under_ignored_work')
    target = folder / 'approved_record.json'
    if target.exists():
        raise ValueError('approved_record_already_exists_no_overwrite')
    record = json.loads((folder / 'calibration_record.json').read_text(encoding='utf-8'))
    approved, blockers = approve_calibration(record, approver_ref=args.approver, approved_at=args.date)
    target.write_text(json.dumps(approved, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'approved_record': str(target), 'remaining_blockers': blockers}, ensure_ascii=False))
    return 0


def main(argv=None):
    import sys
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['approve']:
        return approve_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=Path, required=True, help='private weighing CSV (columns: see template)')
    parser.add_argument('--out', type=Path, required=True, help='new directory under work/')
    parser.add_argument('--calibration-id', required=True)
    parser.add_argument('--dataset-reference', required=True, help='shareable locator, not a machine path')
    parser.add_argument('--producer', required=True, help='who ran this calibration')
    parser.add_argument('--nominal', default='0.8')
    args = parser.parse_args(argv)
    record, model, diagnostics = fit_reference_card_calibration(
        load_weighing_csv(args.samples), calibration_id=args.calibration_id, nominal=args.nominal,
        dataset_reference=args.dataset_reference, producer_ref=args.producer)
    write_calibration(args.out, record, model, diagnostics)
    print(json.dumps({'status': record['status'], 'held_out_samples': record['dataset']['sample_count'],
                      'source_groups': record['dataset']['source_group_count'],
                      'measured_coverage': diagnostics['measured_coverage'], 'nominal': record['coverage']['nominal'],
                      'mae_g': record['error']['mae_g'], 'next': 'independent approval required before use'},
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
