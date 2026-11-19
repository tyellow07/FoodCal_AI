"""Score a usability study (docs/usability_test_v0.md); read-only, prints JSON.

--sus    CSV: participant_id,q1,...,q10 (answers 1-5)
--tasks  CSV: participant_id,task,outcome,seconds (task T1-T4; outcome completed | assisted | failed)
--latency-p95-seconds  from tools.measure_latency
--check  checklist items that passed (repeatable): no_raw_json_on_main_screen,
         chinese_reasons_and_next_steps, phone_width_t1_without_side_scroll
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path

from src.evaluation.usability import CHECKLIST, score_usability, summarize_usability
from tools.evaluate_meals import read_csv


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sus', type=Path, required=True)
    parser.add_argument('--tasks', type=Path, required=True)
    parser.add_argument('--latency-p95-seconds')
    parser.add_argument('--check', action='append', choices=CHECKLIST, default=[])
    args = parser.parse_args(argv)
    summary = summarize_usability(read_csv(args.sus, ('participant_id',) + tuple(f'q{i}' for i in range(1, 11))),
                                  read_csv(args.tasks, ('participant_id', 'task', 'outcome', 'seconds')),
                                  latency_p95_seconds=args.latency_p95_seconds,
                                  checklist={k: True for k in args.check})
    text = {k: (str(round(v, 2)) if isinstance(v, Decimal) else v) for k, v in summary.items()}
    print(json.dumps({'summary': text, 'rubric_points': score_usability(summary)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
