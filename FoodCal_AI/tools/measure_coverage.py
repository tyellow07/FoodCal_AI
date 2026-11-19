"""Line coverage of src/ while running the unittest suite, using only the standard library (Python 3.12+).

Rubric item 6c without installing coverage.py. Counts the executable lines the compiler reports for each
src/*.py file and the ones executed during the run. Import-time-only modules and scripts that need private
data (for example detection/export_validation_review.py) stay low by design. Read-only; prints JSON.
"""
import argparse
import json
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _lines(code):
    found = {line for _, _, line in code.co_lines() if line is not None}
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            found |= _lines(const)
    return found


def measure(test_names=None, *, source_dir=ROOT / 'src'):
    monitoring = sys.monitoring
    tool = monitoring.COVERAGE_ID
    if monitoring.get_tool(tool) is not None:
        raise RuntimeError('coverage_tool_id_in_use')
    prefix, executed = str(Path(source_dir).resolve()), {}

    def on_line(code, line):
        if code.co_filename.startswith(prefix):
            executed.setdefault(code.co_filename, set()).add(line)
        return monitoring.DISABLE

    monitoring.use_tool_id(tool, 'foodcal-line-coverage')
    try:
        monitoring.register_callback(tool, monitoring.events.LINE, on_line)
        monitoring.set_events(tool, monitoring.events.LINE)
        loader = unittest.defaultTestLoader
        suite = (loader.loadTestsFromNames(test_names) if test_names
                 else loader.discover(str(ROOT / 'tests'), top_level_dir=str(ROOT)))
        with open(__import__('os').devnull, 'w') as sink:
            result = unittest.TextTestRunner(stream=sink, verbosity=0).run(suite)
    finally:
        monitoring.set_events(tool, 0)
        monitoring.register_callback(tool, monitoring.events.LINE, None)
        monitoring.free_tool_id(tool)
    files, total, hit = [], 0, 0
    for path in sorted(Path(source_dir).resolve().rglob('*.py')):
        lines = _lines(compile(path.read_text(encoding='utf-8'), str(path), 'exec'))
        covered = executed.get(str(path), set()) & lines
        total, hit = total + len(lines), hit + len(covered)
        files.append({'file': str(path.relative_to(ROOT)), 'lines': len(lines), 'covered': len(covered)})
    return {'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
            'skipped': len(result.skipped), 'lines': total, 'covered': hit,
            'line_coverage': round(hit / total, 4) if total else None, 'files': files}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tests', nargs='*', help='optional test module names; default: discover tests/')
    args = parser.parse_args(argv)
    report = measure(args.tests or None)
    report['lowest'] = sorted(report.pop('files'), key=lambda f: f['covered'] / f['lines'] if f['lines'] else 1)[:10]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not (report['failures'] or report['errors']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
