"""Time the demo's real upload path (decode -> Detection -> Retrieval -> portion report) N times.

Requires the local YOLO weights and taxonomy (not in Git). The first call, which loads the model, is reported
separately and excluded from the p95, matching what a user sees after the first analysis. Prints JSON only;
the image is read from disk and nothing is written.
"""
import argparse
import json
from pathlib import Path
import time

from src.evaluation.usability import latency_p95
from src.ui.adapter import analyze_upload


def measure(raw, *, runs=20, detector_factory=None, clock=time.perf_counter):
    from src.detection import FoodDetector
    detector = (detector_factory or FoodDetector)()
    timings, statuses = [], []
    for _ in range(runs + 1):
        start = clock()
        result, _ = analyze_upload(raw, enabled=True, detector_factory=lambda: detector)
        timings.append(clock() - start)
        statuses.append(result['status'])
    if any(status == 'blocked' for status in statuses):
        raise ValueError('analysis_blocked: ' + str(result['issues']))
    return {'runs': runs, 'first_call_seconds': round(timings[0], 3),
            'p95_seconds': str(round(latency_p95(timings[1:]), 3)),
            'max_seconds': round(max(timings[1:]), 3)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True, help='a local PNG/JPEG/WEBP food photo')
    parser.add_argument('--runs', type=int, default=20)
    args = parser.parse_args(argv)
    print(json.dumps(measure(args.image.read_bytes(), runs=args.runs), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
