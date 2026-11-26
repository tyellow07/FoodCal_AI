"""Score saved dish recognition against human labels (docs/recognition_evaluation_v0.md).

template  photos folder -> blank labels CSV (one row per photo with its upright size; nothing from the model)
score     labels CSV + predictions JSON (tools.run_gemini_batch) [+ filled-in judgements CSV] -> JSON report

When a predicted name differs from the label and its aliases, a person decides: `score --write-pending FILE` lists
those pairs; fill verdict (same | different) and judge, then pass the file with --judgements. Nothing is scored
while a pair is undecided. Written files go under the Git-ignored work/ and are never overwritten.
"""
import argparse
import csv
from decimal import Decimal
import json
from pathlib import Path

from PIL import Image, ImageOps

from src.evaluation.recognition_metrics import (JUDGEMENT_COLUMNS, LABEL_COLUMNS, recognition_metrics,
                                                score_recognition)
from src.evaluation.stage_metrics import IOU_THRESHOLD
from tools.meal_workspace import write_csv
from tools.run_gemini_batch import photo_files


def read_rows(path, columns):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != columns:
            raise ValueError('csv_columns_mismatch: expected ' + ','.join(columns))
        return [{k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()} for row in reader]


def _text(value):
    if isinstance(value, Decimal):
        return str(round(value, 4))
    if isinstance(value, dict):
        return {k: _text(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_text(v) for v in value]
    return value


def label_template(photos):
    """One blank label row per photo, with the upright (EXIF-turned) size that box coordinates refer to."""
    rows = []
    for image_id, path in photos.items():
        with Image.open(path) as image:
            width, height = ImageOps.exif_transpose(image).size
        rows.append({**{k: '' for k in LABEL_COLUMNS}, 'image_id': image_id, 'dish_id': '1',
                     'image_width': str(width), 'image_height': str(height)})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    template = commands.add_parser('template', help='blank labels CSV for a photo folder')
    template.add_argument('--photos', type=Path, required=True)
    template.add_argument('--out', type=Path, required=True)
    score = commands.add_parser('score', help='score predictions against labels')
    score.add_argument('--labels', type=Path, required=True)
    score.add_argument('--predictions', type=Path, required=True)
    score.add_argument('--judgements', type=Path, help='filled-in pending CSV')
    score.add_argument('--write-pending', type=Path, help='write undecided name pairs here for a person to judge')
    score.add_argument('--iou', type=Decimal, default=IOU_THRESHOLD, help='fix before measuring; 0.5 as in 2a')
    args = parser.parse_args(argv)
    if args.command == 'template':
        path = write_csv(args.out, LABEL_COLUMNS, label_template(photo_files(args.photos)))
        print(f'labels template -> {path}')
        return 0
    judgements = read_rows(args.judgements, JUDGEMENT_COLUMNS) if args.judgements else ()
    metrics = recognition_metrics(read_rows(args.labels, LABEL_COLUMNS),
                                  json.loads(args.predictions.read_text(encoding='utf-8')), judgements,
                                  threshold=args.iou)
    if metrics['status'] == 'needs_judgement' and args.write_pending:
        metrics['pending_file'] = str(write_csv(args.write_pending, JUDGEMENT_COLUMNS, metrics['pending']))
    report = {'metrics': _text(metrics), 'rubric_points': score_recognition(metrics)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if metrics['status'] == 'scored' else 3


if __name__ == '__main__':
    raise SystemExit(main())
