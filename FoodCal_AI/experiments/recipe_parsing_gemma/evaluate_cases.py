"""Offline per-field evaluation of explicitly selected, immutable run records."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path

from run_local import FIELDS, ROOT, fingerprint, load_inputs


def schema_valid(items):
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict) or set(item) != FIELDS:
            return False
        if not all(isinstance(item[k], str) for k in ('ingredient_name', 'evidence_text')):
            return False
        if any(item[k] is not None and not isinstance(item[k], str) for k in ('unit', 'preparation_method')):
            return False
        if any(type(item[k]) is not bool for k in ('optional', 'uncertainty')):
            return False
        q = item['quantity']
        if q is not None and (type(q) not in (int, float) or not math.isfinite(q) or q < 0):
            return False
    return True


def equal_field(key, actual, expected):
    if key == 'quantity' and type(actual) in (int, float) and type(expected) in (int, float):
        return actual == expected
    return type(actual) is type(expected) and actual == expected


def analyze_case(case, record):
    errors = []
    try:
        def reject_constant(value):
            raise ValueError('Non-JSON constant: '+value)
        parsed = json.loads(record.get('raw_reply', ''), parse_constant=reject_constant)
        json_valid = True
    except (TypeError, ValueError):
        parsed, json_valid = None, False
    structured = schema_valid(parsed)
    if not json_valid:
        errors.append('invalid_or_missing_json_answer')
    elif not structured:
        errors.append('invalid_field_schema')
    if record.get('response', {}).get('done_reason') == 'length':
        errors.append('generation_token_limit')
    if not record.get('raw_reply'):
        errors.append('no_answer_content')
    actual = parsed if isinstance(parsed, list) else []
    expected = case['expected']
    remaining = list(range(len(actual)))
    matches = Counter()
    rows, differences = [], []
    for reference in expected:
        index = next((i for i in remaining if isinstance(actual[i], dict) and
                      actual[i].get('ingredient_name') == reference['ingredient_name']), None)
        prediction = actual[index] if index is not None else None
        if index is not None:
            remaining.remove(index)
        per_field = {}
        for key in sorted(FIELDS):
            ok = prediction is not None and key in prediction and equal_field(key, prediction[key], reference[key])
            per_field[key] = ok
            matches[key] += int(ok)
            if not ok:
                differences.append({'ingredient_name': reference['ingredient_name'], 'field': key,
                                    'expected': reference[key],
                                    'actual': prediction.get(key) if prediction is not None else None,
                                    'actual_field_present': prediction is not None and key in prediction,
                                    'issue': 'missing_ingredient' if prediction is None else 'field_mismatch'})
        rows.append({'ingredient_name': reference['ingredient_name'], 'expected': reference,
                     'actual': prediction, 'field_matches': per_field})
    extras = [actual[i] for i in remaining]
    if extras:
        errors.append('extra_or_duplicate_ingredients')
    if differences:
        errors.extend(sorted({d['issue']+':'+d['field'] for d in differences}))
    evidence_checks = []
    for item in actual:
        evidence, name = (item.get('evidence_text'), item.get('ingredient_name')) if isinstance(item, dict) else (None, None)
        grounded = (isinstance(evidence, str) and bool(evidence) and evidence in case['text'] and
                    isinstance(name, str) and bool(name) and name in evidence)
        evidence_checks.append(grounded)
    reference_issues = []
    for item in expected:
        if set(item)-FIELDS:
            reference_issues.append({'ingredient_name': item['ingredient_name'],
                                     'issue': 'unsupported_reference_fields', 'fields': sorted(set(item)-FIELDS)})
        if item['preparation_method'] is not None and item['preparation_method'] not in case['text']:
            reference_issues.append({'ingredient_name': item['ingredient_name'],
                                     'issue': 'reference_method_not_verbatim', 'value': item['preparation_method']})
    return {'case_id': case['id'], 'runner_status': record['status'],
            'transport_completed': record.get('stream_observation', {}).get('status') == 'completed',
            'json_valid': json_valid, 'schema_valid': structured,
            'evidence_all_pass': bool(actual) and all(evidence_checks),
            'evidence_items_pass': sum(evidence_checks), 'predicted_items': len(actual),
            'reference_items': len(expected), 'field_matches': dict(matches), 'rows': rows,
            'differences': differences, 'extra_or_duplicate_items': extras, 'issue_types': errors,
            'runner_validation_errors': record.get('validation_errors', []), 'reference_issues': reference_issues,
            'first_token_seconds': record.get('first_token_seconds'),
            'first_content_seconds': record.get('first_content_seconds'),
            'inference_elapsed_seconds': record.get('inference_elapsed_seconds'),
            'total_elapsed_seconds': record.get('elapsed_seconds'),
            'failure_reason': record.get('error') or '; '.join(record.get('validation_errors', [])) or None,
            'reference_exact_all_fields': structured and not differences and not extras,
            'semantic_correctness': ('reference_agreement_only_not_independent_semantic_adjudication'
                                     if structured else 'not_assessable_no_valid_structured_answer')}


def build_report(cases, records, stop_reason=None):
    analyses = [analyze_case(case, records[case['id']]) for case in cases if case['id'] in records]
    reference_total = sum(c['reference_items'] for c in analyses)
    predicted_total = sum(c['predicted_items'] for c in analyses)
    fields = {key: sum(c['field_matches'].get(key, 0) for c in analyses) for key in sorted(FIELDS)}
    rows = [row for case in analyses for row in case['rows']]
    def ratio(passed, denominator):
        return {'passed': passed, 'denominator': denominator,
                'rate': passed/denominator if denominator else None}
    return {'schema_version': 1, 'planned_cases': len(cases), 'evaluated_cases': len(analyses),
            'not_executed': [c['id'] for c in cases if c['id'] not in records], 'stop_reason': stop_reason,
            'metrics': {
                'json_valid_cases': ratio(sum(c['json_valid'] for c in analyses), len(analyses)),
                'schema_valid_cases': ratio(sum(c['schema_valid'] for c in analyses), len(analyses)),
                'evidence_all_pass_cases': ratio(sum(c['evidence_all_pass'] for c in analyses), len(analyses)),
                'evidence_pass_predicted_items': ratio(sum(c['evidence_items_pass'] for c in analyses), predicted_total),
                'ingredient_exact_name_reference_coverage': ratio(fields['ingredient_name'], reference_total),
                'ingredient_exact_name_prediction_precision': ratio(fields['ingredient_name'], predicted_total),
                'quantity_reference_agreement': ratio(fields['quantity'], reference_total),
                'explicit_quantity_reference_agreement': ratio(sum(row['field_matches']['quantity']
                    for row in rows if row['expected']['quantity'] is not None),
                    sum(row['expected']['quantity'] is not None for row in rows)),
                'missing_quantity_null_preservation': ratio(sum(row['field_matches']['quantity']
                    for row in rows if row['expected']['quantity'] is None),
                    sum(row['expected']['quantity'] is None for row in rows)),
                'quantity_and_unit_reference_agreement': ratio(sum(
                    row['field_matches']['quantity'] and row['field_matches']['unit']
                    for c in analyses for row in c['rows']), reference_total),
                'per_field_reference_agreement': {key: ratio(count, reference_total) for key, count in fields.items()}},
            'cases': analyses,
            'limitations': ['Five targeted synthetic cases are not representative real recipes or independent accuracy evidence.',
                            'Attempted-case reference coverage counts absent outputs as zero; this is operational extraction yield, not semantic accuracy.',
                            'Evidence precision over returned items excludes missing outputs; see attempted-case evidence rate and coverage alongside it.',
                            'Reference counts include null quantities; quantities are explicit text, not estimated image mass.',
                            'Exact field agreement is separate from schema validity and human semantic adjudication.',
                            'Unsupported telefone is retained in original reference, excluded only from the seven-field API comparison.',
                            'Non-verbatim normalized reference methods require adjudication, not automatic model correction.',
                            'Missing/extra/duplicate objects are explicitly counted; a valid JSON array alone is not extraction success.']}


def render(report):
    lines = ['# Gemma 五筆合成食譜初步評估', '',
             '第一筆使用既有成功結果；其餘案例每次一筆，零重試。指標為合成參考一致性，不是正式食譜語意準確率。', '',
             '| 案例 | 程式狀態 | JSON / schema / evidence | 首答案秒 | 推論秒 | 全欄參考一致 |',
             '| --- | --- | --- | --- | --- | --- |']
    for case in report['cases']:
        lines.append(f"| {case['case_id']} | {case['runner_status']} | {case['json_valid']} / {case['schema_valid']} / {case['evidence_all_pass']} | {case['first_content_seconds']} | {case['inference_elapsed_seconds']} | {case['reference_exact_all_fields']} |")
    for case_id in report['not_executed']:
        lines.append(f'| {case_id} | not_executed | — | — | — | — |')
    lines.extend(['', f"停止原因：{report['stop_reason'] or '無；全部指定案例已結束。'}", '', '## 指標（明示分母）', ''])
    for key, metric in report['metrics'].items():
        if key != 'per_field_reference_agreement':
            lines.append(f"- {key}: {metric['passed']}/{metric['denominator']} ({metric['rate']:.2%})" if metric['rate'] is not None else f'- {key}: unavailable')
    lines.extend(['', '## 逐欄比較', ''])
    for case in report['cases']:
        lines.extend([f"### {case['case_id']}", '', f"Runner validation：{case['runner_validation_errors']}; failure：{case['failure_reason']}", ''])
        lines.append('語意評估狀態：'+case['semantic_correctness']+'；錯誤類型：'+', '.join(case['issue_types']))
        for row in case['rows']:
            good = ', '.join(k for k, ok in row['field_matches'].items() if ok)
            lines.append(f"- {row['ingredient_name']}：一致欄位 {good or '無'}。")
        for diff in case['differences']:
            lines.append(f"- 差異 {diff['ingredient_name']}.{diff['field']}: expected={json.dumps(diff['expected'], ensure_ascii=False)}, actual={json.dumps(diff['actual'], ensure_ascii=False)}; {diff['issue']}。")
        if case['extra_or_duplicate_items']:
            lines.append('- 額外／重複項目：'+json.dumps(case['extra_or_duplicate_items'], ensure_ascii=False))
        for issue in case['reference_issues']:
            lines.append('- 參考疑慮：'+json.dumps(issue, ensure_ascii=False))
        lines.append('')
    lines.extend(['## 整合建議與限制', '',
                  '目前僅適合保留為實驗性候選解析器；正式整合前需仲裁參考標準、逐欄人工語意審核，擴充真實且可追溯的繁中案例。維持 null、來源/evidence 與不確定性，不讓 LLM 直接產生營養數值或照片份量。', ''])
    lines.extend('- '+limit for limit in report['limitations'])
    return '\n'.join(lines)+'\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', action='append', default=[], help='Explicit CASE_ID=relative/or/absolute/report.json')
    parser.add_argument('--stop-reason')
    parser.add_argument('--output', type=Path, default=ROOT/'results/initial_evaluation')
    args = parser.parse_args()
    cases, _, audit = load_inputs()
    records, sources = {}, {}
    for argument in args.result:
        case_id, path_text = argument.split('=', 1)
        path = Path(path_text).resolve()
        raw = path.read_bytes()
        record = json.loads(raw)
        if case_id in records or case_id not in {c['id'] for c in cases} or record.get('input', {}).get('input_id') != case_id:
            raise ValueError('Invalid or duplicate explicitly selected run')
        if record['input_validation'] != audit:
            raise ValueError('Run inputs differ from unchanged current cases/prompt')
        records[case_id] = record
        sources[case_id] = {'path': str(path), 'sha256': fingerprint(raw)}
    report = build_report(cases, records, args.stop_reason)
    report.update(sources=sources, input_validation=audit)
    for suffix, text in (('.json', json.dumps(report, ensure_ascii=False, indent=2)+'\n'), ('.md', render(report))):
        with args.output.with_suffix(suffix).open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
    print(json.dumps({'evaluated': len(records), 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
