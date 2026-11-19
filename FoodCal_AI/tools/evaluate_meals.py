"""Score system predictions against weighed reference calories (docs/meal_evaluation_protocol_v0.md).

references CSV: meal_id,source_group,subset,reference_kcal   (subset: main | should_abstain)
predictions CSV: meal_id,status,low_kcal,high_kcal          (status: computed | unavailable)
Prints metrics and rubric points as JSON. Read-only: writes nothing.
"""
import argparse
import csv
from decimal import Decimal
import json
from pathlib import Path

from src.evaluation import evaluate_meals, score_rubric


def read_csv(path, columns):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != columns:
            raise ValueError('csv_columns_mismatch: expected ' + ','.join(columns))
        return [{k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()} for row in reader]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--references', type=Path, required=True)
    parser.add_argument('--predictions', type=Path, required=True)
    args = parser.parse_args(argv)
    references = read_csv(args.references, ('meal_id', 'source_group', 'subset', 'reference_kcal'))
    predictions = read_csv(args.predictions, ('meal_id', 'status', 'low_kcal', 'high_kcal'))
    metrics = evaluate_meals(references, predictions)
    report = {'metrics': {k: (str(round(v, 4)) if isinstance(v, Decimal) else v) for k, v in metrics.items()},
              'rubric_points': score_rubric(metrics)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
