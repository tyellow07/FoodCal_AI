"""Private annotation workspaces for campus meal photos (docs/campus_meal_workflow_v0.md).

build               photos + meals.csv -> work/<workspace> usable by tools.annotate_ingredient_pilot
export-calibration  accepted annotations + scale readings -> weighing CSV for tools.build_portion_calibration

Photos are copied upright (EXIF orientation applied, metadata dropped) into the Git-ignored workspace.
A workspace has one purpose, evaluation or calibration, so test meals can never be exported as calibration
samples. Nothing is uploaded, trained or approved here.
"""
import argparse
import csv
from io import BytesIO
import json
from pathlib import Path
import re

from PIL import ImageOps
from PIL.JpegImagePlugin import JpegImageFile
from PIL.PngImagePlugin import PngImageFile

from tools.build_portion_calibration import COLUMNS as WEIGHING_COLUMNS
from tools.ingredient_box_edit import latest_revision
from tools.ingredient_pilot import ROOT, SPEC, file_hash, validate_annotation

SOURCE_ID = 'foodcal-campus-meals-v0'
PURPOSES = ('evaluation', 'calibration')
MEAL_COLUMNS = ('meal_id', 'source_group', 'photo_file')
WEIGHT_COLUMNS = ('meal_id', 'annotation_id', 'mass_g', 'scale_resolution_g', 'measured_by')
# No hyphen: revision files are '<id>-<timestamp>-<uuid>.json', so 'm1' must never glob-match 'm1-2'.
MEAL_ID = re.compile(r'[A-Za-z0-9_]{1,64}')
MAX_PIXELS = 60_000_000


def read_csv(path, columns):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != columns:
            raise ValueError('csv_columns_mismatch: expected ' + ','.join(columns))
        rows = []
        for row in reader:
            if any(row.get(k) is None or not row[k].strip() for k in columns):
                raise ValueError('csv_blank_cell')
            rows.append({k: row[k].strip() for k in columns})
        return rows


def _upright_jpeg(path):
    raw = Path(path).read_bytes()
    decoder = (PngImageFile if raw.startswith(b'\x89PNG\r\n\x1a\n') else
               JpegImageFile if raw.startswith(b'\xff\xd8\xff') else None)
    if decoder is None:
        raise ValueError('photo_must_be_jpeg_or_png')  # HEIC: export as JPEG from the phone first
    with decoder(BytesIO(raw)) as source:
        if source.width * source.height > MAX_PIXELS:
            raise ValueError('photo_too_large')
        source.load()
        image = ImageOps.exif_transpose(source).convert('RGB')
    image.info.clear()
    output = BytesIO()
    image.save(output, format='JPEG', quality=95)
    return output.getvalue(), image.size, file_hash(Path(path))


def build_meal_workspace(photos_dir, meals_csv, workspace, *, purpose, root=ROOT / 'work'):
    """Create a new workspace; refuses to overwrite, to leave work/, or to mix purposes."""
    if purpose not in PURPOSES:
        raise ValueError('purpose_must_be_evaluation_or_calibration')
    photos_dir, workspace, root = Path(photos_dir).resolve(), Path(workspace).resolve(), Path(root).resolve()
    if not workspace.is_relative_to(root):
        raise ValueError('workspace_must_be_under_ignored_work')
    if workspace.exists():
        raise ValueError('workspace_already_exists_no_overwrite')
    meals = read_csv(meals_csv, MEAL_COLUMNS)
    if not meals:
        raise ValueError('no_meals')
    ids = [m['meal_id'] for m in meals]
    if len(ids) != len(set(ids)) or not all(MEAL_ID.fullmatch(i) for i in ids):
        raise ValueError('meal_ids_must_be_unique_letters_digits_underscore')
    prepared = []
    for meal in meals:
        source = (photos_dir / meal['photo_file']).resolve()
        if not source.is_relative_to(photos_dir) or not source.is_file():
            raise ValueError('photo_missing_or_outside_photos_dir: ' + meal['meal_id'])
        prepared.append((meal, *_upright_jpeg(source)))
    (workspace / 'images').mkdir(parents=True)
    (workspace / 'annotations').mkdir()
    items = []
    for meal, data, (width, height), original_sha in prepared:
        image_path = workspace / 'images' / (meal['meal_id'] + '.jpg')
        image_path.write_bytes(data)
        digest = file_hash(image_path)
        items.append({'pilot_image_id': meal['meal_id'], 'image_sha256': digest, 'source_id': SOURCE_ID,
                      'image_path': str(image_path), 'dimensions': [width, height],
                      'original_photo_sha256': original_sha, 'sampling_target': 'campus_meal_' + purpose,
                      'source_group_id': meal['source_group'], 'source_group_status': 'team_recorded',
                      'candidate_bucket_id': digest, 'split': purpose, 'rights_status': 'team_photo_review_only',
                      'training_eligible': False, 'annotation_status': 'pending',
                      'independent_review_status': 'pending'})
    manifest = {'pilot_version': 0, 'kind': 'campus_meal_workspace', 'purpose': purpose,
                'usage': 'private/review_only', 'spec_version': json.loads(SPEC.read_text(encoding='utf-8'))['spec_version'],
                'spec_sha256': file_hash(SPEC), 'items': items, 'training_eligible': False}
    (workspace / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


def load_manifest(workspace, *, purpose=None):
    manifest = json.loads((Path(workspace) / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('kind') != 'campus_meal_workspace' or manifest.get('purpose') not in PURPOSES:
        raise ValueError('not_a_campus_meal_workspace')
    if purpose is not None and manifest['purpose'] != purpose:
        raise ValueError(f'workspace_purpose_must_be_{purpose}')
    if manifest['spec_sha256'] != file_hash(SPEC):
        raise ValueError('spec_hash_mismatch')
    return manifest


def accepted_annotations(workspace, manifest):
    """meal_id -> (item, annotation or None, reason). Only the latest revision counts, and only if accepted."""
    out = {}
    for item in manifest['items']:
        path = latest_revision(Path(workspace) / 'annotations', item['pilot_image_id'])
        if path is None:
            out[item['pilot_image_id']] = (item, None, 'not_annotated')
            continue
        annotation = json.loads(path.read_text(encoding='utf-8'))
        if file_hash(Path(item['image_path'])) != item['image_sha256']:
            out[item['pilot_image_id']] = (item, None, 'image_hash_mismatch')
            continue
        result = validate_annotation(item, annotation)
        out[item['pilot_image_id']] = ((item, annotation, None) if result['status'] == 'passed'
                                       else (item, None, 'annotation_not_accepted'))
    return out


def export_calibration_samples(workspace, weights_csv):
    """Weighing rows bound to accepted calibration-workspace boxes and the same photo's card box."""
    manifest = load_manifest(workspace, purpose='calibration')
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    annotations = accepted_annotations(workspace, manifest)
    rows, seen = [], set()
    for weight in read_csv(weights_csv, WEIGHT_COLUMNS):
        key = (weight['meal_id'], weight['annotation_id'])
        if key in seen:
            raise ValueError('duplicate_weight_row')
        seen.add(key)
        item, annotation, reason = annotations.get(weight['meal_id'], (None, None, 'unknown_meal'))
        if annotation is None:
            raise ValueError(f'{reason}: {weight["meal_id"]}')
        card = annotation.get('reference_card')
        if card is None:
            raise ValueError('reference_card_missing: ' + weight['meal_id'])
        boxes = [b for b in annotation['boxes'] if b['annotation_id'] == weight['annotation_id']]
        if len(boxes) != 1:
            raise ValueError('annotation_box_not_found: ' + '/'.join(key))
        box = boxes[0]
        rows.append({'sample_id': weight['meal_id'] + ':' + weight['annotation_id'],
                     'source_group': item['source_group_id'],
                     'ingredient_candidate': spec['classes'][box['class_id']]['name'],
                     **{f'region_{k}': str(v) for k, v in zip(('x_min', 'y_min', 'x_max', 'y_max'), box['bounding_box'])},
                     **{f'card_{k}': str(v) for k, v in zip(('x_min', 'y_min', 'x_max', 'y_max'), card['bounding_box'])},
                     'mass_g': weight['mass_g'], 'scale_resolution_g': weight['scale_resolution_g'],
                     'measured_by': weight['measured_by']})
    return rows


def write_csv(path, columns, rows, *, root=ROOT / 'work'):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root):
        raise ValueError('output_must_be_under_ignored_work')
    if path.exists():
        raise ValueError('output_already_exists_no_overwrite')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build', help='create a workspace from meal photos')
    build.add_argument('--photos', type=Path, required=True)
    build.add_argument('--meals', type=Path, required=True, help='CSV: meal_id,source_group,photo_file')
    build.add_argument('--workspace', type=Path, required=True, help='new directory under work/')
    build.add_argument('--purpose', choices=PURPOSES, required=True)
    export = commands.add_parser('export-calibration', help='weighing CSV from a calibration workspace')
    export.add_argument('--workspace', type=Path, required=True)
    export.add_argument('--weights', type=Path, required=True,
                        help='CSV: meal_id,annotation_id,mass_g,scale_resolution_g,measured_by')
    export.add_argument('--out', type=Path, required=True, help='new CSV under work/')
    args = parser.parse_args(argv)
    if args.command == 'build':
        manifest = build_meal_workspace(args.photos, args.meals, args.workspace, purpose=args.purpose)
        print(json.dumps({'workspace': str(args.workspace), 'purpose': manifest['purpose'], 'meals': len(manifest['items']),
                          'next': 'python -m tools.annotate_ingredient_pilot --workspace <workspace> --actor <name>'},
                         ensure_ascii=False))
    else:
        rows = export_calibration_samples(args.workspace, args.weights)
        write_csv(args.out, WEIGHING_COLUMNS, rows)
        print(json.dumps({'samples': len(rows), 'out': str(args.out),
                          'next': 'python -m tools.build_portion_calibration --samples <out> ...'}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
