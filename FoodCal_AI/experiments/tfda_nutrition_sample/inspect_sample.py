"""Offline TFDA ZIP inspection. Preserve literal rows; no nutrient normalization."""
import argparse
from collections import Counter
from hashlib import sha256
import io
import json
import math
from pathlib import Path
import zipfile


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError('Non-finite JSON literal')


def iter_rows(stream, chunk_size=65536):
    """Bounded incremental parsing of one JSON array; indexes refer to source rows."""
    decoder = json.JSONDecoder(object_pairs_hook=unique_keys, parse_constant=reject_constant)
    buffer = stream.read(chunk_size).lstrip()
    if not buffer.startswith('['):
        raise ValueError('Expected top-level JSON array')
    buffer = buffer[1:]
    index = 0
    expect_item = True
    after_comma = False
    while True:
        buffer = buffer.lstrip()
        if not buffer:
            buffer = stream.read(chunk_size)
            if not buffer:
                raise ValueError('Truncated array')
            continue
        if expect_item:
            if buffer[0] == ']':
                if after_comma:
                    raise ValueError('Trailing comma')
                tail = buffer[1:] + stream.read()
                if tail.strip():
                    raise ValueError('Unexpected trailing content')
                return
            try:
                row, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                more = stream.read(chunk_size)
                if not more or len(buffer) + len(more) > 1024 * 1024:
                    raise ValueError('Invalid, truncated or oversized row')
                buffer += more
                continue
            if not isinstance(row, dict):
                raise ValueError('Expected object rows')
            yield index, row, buffer[:end]
            index += 1
            buffer = buffer[end:]
            expect_item = False
            after_comma = False
        elif buffer[0] == ',':
            buffer = buffer[1:]
            expect_item = True
            after_comma = True
        elif buffer[0] == ']':
            tail = buffer[1:] + stream.read()
            if tail.strip():
                raise ValueError('Unexpected trailing content')
            return
        else:
            raise ValueError('Missing row separator')


def member_info(archive):
    members = archive.infolist()
    if len(members) != 1 or not members[0].filename.endswith('.json'):
        raise ValueError('Expected one JSON archive member')
    member = members[0]
    if member.file_size > 256 * 1024 * 1024:
        raise ValueError('Archive member exceeds inspection limit')
    return member


def source_rows(archive_path):
    with zipfile.ZipFile(archive_path) as archive:
        member = member_info(archive)
        with archive.open(member) as binary, io.TextIOWrapper(binary, encoding='utf-8-sig') as text:
            yield from iter_rows(text)


def food_key(row):
    fields = ('整合編號', '樣品名稱', '資料類別')
    if any(not isinstance(row.get(field), str) or not row[field] for field in fields):
        raise ValueError('Missing food identity fields')
    return tuple(row[field] for field in fields)


def classify_raw(value):
    """Classify spelling/type without converting any token to a nutrient number."""
    if value is None:
        return 'null'
    if isinstance(value, str):
        if value == '':
            return 'empty_string'
        if value.isspace():
            return 'whitespace_string'
        import re
        if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value.strip()):
            zero = not any(c in '123456789' for c in value)
            state = 'explicit_zero_string' if zero else 'numeric_string'
            return 'padded_' + state if value != value.strip() else state
        return 'uninterpreted_token'
    if type(value) in (int, float):
        if isinstance(value, float) and not math.isfinite(value):
            return 'unexpected_type'
        return 'explicit_zero_number' if value == 0 else 'number'
    return 'unexpected_type'


def diagnose(rows):
    """Inspect all observations for a food; missing nutrient rows stay absent."""
    if not rows or len({food_key(row) for row in rows}) != 1:
        raise ValueError('Expected complete rows for one food identity')
    nutrients = ('熱量', '修正熱量', '粗蛋白', '粗脂肪', '總碳水化合物')
    coverage = {}
    for nutrient in nutrients:
        selected = [row for row in rows if row.get('分析項') == nutrient]
        coverage[nutrient] = {'observation_count': len(selected),
                              'unit_values': distinct_raw(row.get('含量單位') for row in selected),
                              'raw_values': [row.get('每100克含量') for row in selected],
                              'raw_states': [classify_raw(row.get('每100克含量')) for row in selected]}
    profiles = {}
    for field in sorted(set().union(*(row.keys() for row in rows))):
        counts = Counter(classify_raw(row[field]) if field in row else 'absent_field' for row in rows)
        tokens = sorted({str(row[field]) for row in rows if field in row and classify_raw(row[field]) == 'uninterpreted_token'})
        profiles[field] = {'states': dict(counts), 'uninterpreted_tokens': tokens}
    return {'food_key': list(food_key(rows[0])), 'row_count': len(rows), 'fields': list(rows[0]),
            'nutrients': coverage, 'field_profiles': profiles,
            'food_category': rows[0].get('食品分類'), 'description': rows[0].get('內容物描述'),
            'refuse_raw': distinct_raw(row.get('廢棄率') for row in rows),
            'metadata_conflicts': [field for field in ('食品分類', '內容物描述', '俗名', '廢棄率', '每單位重')
                                   if len(distinct_raw(row.get(field) for row in rows)) != 1],
            'duplicate_analysis_items': [name for name, count in Counter(row.get('分析項') for row in rows).items()
                                         if count > 1],
            'semantic_approval': False, 'photo_mass_known': False}


def distinct_raw(values):
    """Keep null distinct from the strings 'None'/'null'; no coercion."""
    unique = {}
    for value in values:
        unique.setdefault(json.dumps(value, ensure_ascii=False, sort_keys=True), value)
    return list(unique.values())


def verify(archive_path, sample_dir):
    """Read-only replay against all source rows, including completeness and literal hashes."""
    sample_dir = Path(sample_dir)
    report = json.loads((sample_dir / 'inspection.json').read_text(encoding='utf-8'))
    if sha256(Path(archive_path).read_bytes()).hexdigest() != report['archive_sha256']:
        raise ValueError('Archive hash mismatch')
    records = report['records']
    keys = [tuple(record['diagnosis']['food_key']) for record in records]
    if not 5 <= len(keys) <= 10 or len(set(keys)) != len(keys):
        raise ValueError('Invalid sample identities')
    observed = {key: [] for key in keys}
    indices = {key: [] for key in keys}
    hashes = {key: [] for key in keys}
    for index, row, raw in source_rows(archive_path):
        key = food_key(row)
        if key in observed:
            observed[key].append(row)
            indices[key].append(index)
            hashes[key].append(sha256(raw.encode('utf-8')).hexdigest())
    diagnoses = []
    for record, key in zip(records, keys):
        filename = record['filename']
        if Path(filename).name != filename or not filename.endswith('.json'):
            raise ValueError('Invalid sample filename')
        data = (sample_dir / filename).read_bytes()
        if sha256(data).hexdigest() != record['sha256']:
            raise ValueError('Sample hash mismatch')
        actual = list(iter_rows(io.StringIO(data.decode('utf-8'))))
        if ([row for _, row, _ in actual] != observed[key]
                or [sha256(raw.encode('utf-8')).hexdigest() for _, _, raw in actual] != hashes[key]
                or hashes[key] != record['source_object_sha256']
                or indices[key] != record['source_row_indices_zero_based']
                or len(actual) != record['diagnosis']['row_count']):
            raise ValueError('Sample completeness or literal source mismatch')
        diagnoses.append({'filename': filename, 'sha256': record['sha256'], 'diagnosis': diagnose(observed[key])})
    with zipfile.ZipFile(archive_path) as archive:
        member = member_info(archive)
        digest = sha256()
        with archive.open(member) as stream:
            for chunk in iter(lambda: stream.read(65536), b''):
                digest.update(chunk)
        if (digest.hexdigest() != report['member_sha256'] or member.filename != report['member_name']
                or member.file_size != report['member_bytes']):
            raise ValueError('Archive member mismatch')
    return {'status': 'verified_against_complete_snapshot', 'archive_sha256': report['archive_sha256'],
            'member_sha256': report['member_sha256'], 'records': diagnoses,
            'production_eligible': False, 'official_dataset_version': None}


def catalog(archive_path):
    foods = {}
    count = 0
    for index, row, raw in source_rows(archive_path):
        key = food_key(row)
        if key not in foods:
            foods[key] = {'key': list(key), 'category': row.get('食品分類'),
                          'description': row.get('內容物描述'), 'first_row_index': index, 'row_count': 0}
        foods[key]['row_count'] += 1
        count += 1
    return {'row_count': count, 'foods': list(foods.values())}


def extract(archive_path, keys, output_dir):
    if not 5 <= len(keys) <= 10 or len(set(map(tuple, keys))) != len(keys):
        raise ValueError('Select 5-10 unique food keys')
    selected = {tuple(key): [] for key in keys}
    indices = {tuple(key): [] for key in keys}
    literal = {tuple(key): [] for key in keys}
    for index, row, raw in source_rows(archive_path):
        key = food_key(row)
        if key in selected:
            selected[key].append(row)
            indices[key].append(index)
            literal[key].append(raw)
    if any(not rows for rows in selected.values()):
        raise ValueError('Selected identity absent from official export')
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    records = []
    for i, key in enumerate(selected):
        filename = f'food_{i + 1:02d}.json'
        # Original object spellings/order preserved; array wrapper/separators are derived.
        raw = ('[\n' + ',\n'.join(literal[key]) + '\n]\n').encode('utf-8')
        (output_dir / filename).write_bytes(raw)
        records.append({'filename': filename, 'sha256': sha256(raw).hexdigest(),
                        'source_row_indices_zero_based': indices[key],
                        'source_object_sha256': [sha256(value.encode('utf-8')).hexdigest() for value in literal[key]],
                        'diagnosis': diagnose(selected[key])})
    with zipfile.ZipFile(archive_path) as archive:
        member = member_info(archive)
        digest = sha256()
        with archive.open(member) as stream:
            for chunk in iter(lambda: stream.read(65536), b''):
                digest.update(chunk)
    report = {'archive_sha256': sha256(Path(archive_path).read_bytes()).hexdigest(),
              'member_name': member.filename, 'member_bytes': member.file_size,
              'member_sha256': digest.hexdigest(), 'records': records,
              'extraction_note': 'Original JSON object text preserved; wrapper is derived. No normalization.',
              'official_dataset_version': None, 'production_eligible': False}
    (output_dir / 'inspection.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--selection', type=Path)
    parser.add_argument('--verify-samples', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.verify_samples:
        if args.selection:
            parser.error('Verification and extraction are separate operations')
        result = verify(args.archive, args.verify_samples)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    elif args.selection:
        result = extract(args.archive, json.loads(args.selection.read_text(encoding='utf-8')), args.output)
    else:
        result = catalog(args.archive)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'record_count': len(result.get('records', result.get('foods', []))),
                      'row_count': result.get('row_count'), 'production_eligible': False}, ensure_ascii=True))


if __name__ == '__main__':
    main()
