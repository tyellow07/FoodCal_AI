"""Stage metrics (rubric item 2) and red-line audit (item 3a); see docs/meal_evaluation_protocol_v0.md section 5.

--detection-gt / --detection-pred  CSV: image_id,class_name,x_min,y_min,x_max,y_max (pred adds confidence)
--model-classes                    YAML with `names:` (taxonomy coverage), e.g. examples/diagnostic_v0/classes.yaml
--portion                          CSV: item_id,status,low_g,high_g,weighed_g (status estimated | unavailable)
--mapping                          CSV: item_id,system_kcal_per_100g,gold_kcal_per_100g (system blank = unmapped)
--audit-results                    results.json from tools.run_meal_pipeline energy
Read-only: prints JSON, writes nothing.
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path

import yaml

from src.evaluation import (audit_results, detection_metrics, nutrition_mapping_metrics, portion_metrics,
                            score_stages)
from tools.evaluate_meals import read_csv

BOX = ('x_min', 'y_min', 'x_max', 'y_max')


def _text(metrics):
    return None if metrics is None else {k: (str(round(v, 4)) if isinstance(v, Decimal) else v)
                                         for k, v in metrics.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--detection-gt', type=Path)
    parser.add_argument('--detection-pred', type=Path)
    parser.add_argument('--model-classes', type=Path)
    parser.add_argument('--portion', type=Path)
    parser.add_argument('--mapping', type=Path)
    parser.add_argument('--oracle-report-attached', action='store_true')
    parser.add_argument('--audit-results', type=Path)
    args = parser.parse_args(argv)
    if (args.detection_gt is None) != (args.detection_pred is None):
        parser.error('--detection-gt and --detection-pred go together')
    detection = portion = mapping = None
    if args.detection_gt:
        classes = None
        if args.model_classes:
            classes = yaml.safe_load(args.model_classes.read_text(encoding='utf-8'))['names']
        detection = detection_metrics(read_csv(args.detection_gt, ('image_id', 'class_name') + BOX),
                                      read_csv(args.detection_pred, ('image_id', 'class_name', 'confidence') + BOX),
                                      model_classes=classes)
    if args.portion:
        portion = portion_metrics(read_csv(args.portion, ('item_id', 'status', 'low_g', 'high_g', 'weighed_g')))
    if args.mapping:
        mapping = nutrition_mapping_metrics(read_csv(args.mapping, ('item_id', 'system_kcal_per_100g',
                                                                    'gold_kcal_per_100g')))
    report = {'detection': _text(detection), 'portion': _text(portion), 'mapping': _text(mapping),
              'rubric_points': score_stages(detection, portion, mapping,
                                            oracle_report_attached=args.oracle_report_attached)}
    if args.audit_results:
        violations = audit_results(json.loads(args.audit_results.read_text(encoding='utf-8')))
        report['red_line_audit'] = {'meals_with_violations': violations,
                                    'rubric_3a_points_if_tests_exist': 0 if violations else 5}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
