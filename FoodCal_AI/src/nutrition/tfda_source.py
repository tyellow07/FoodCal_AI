"""Read-only, pinned eight-food TFDA snapshot. No mapping, approval or calculation."""
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Literal

from experiments.tfda_nutrition_sample.inspect_sample import iter_rows, verify, unique_keys


ARCHIVE_SHA256 = '755d2f0e4a7c1fe80ac0636863117e376773c9342cfd68e7b05e67d81e3c6064'
PROVENANCE_SHA256 = '3880a8191ab704ee1800e2b84af498f13d24fe28445d2c351f6ad39e523f92c5'
INSPECTION_SHA256 = 'fd3f318cb68527b78c3ab03d77a64ee63ad4bf3dd285233de8c67b1ff04143ac'
SOURCE_URL = 'https://data.fda.gov.tw/data/opendata/export/20/json'
FIELDS = ('食品分類', '資料類別', '整合編號', '樣品名稱', '俗名', '樣品英文名稱',
          '內容物描述', '廢棄率', '分析項分類', '分析項', '含量單位', '每100克含量',
          '樣本數', '標準差', '每單位含量', '每單位重', '每單位重含量')
RawScalar = str | int | float | bool | None


class SnapshotError(ValueError):
    def __init__(self, code, path):
        super().__init__(code)
        self.code = code
        self.path = path


@dataclass(frozen=True)
class SourceIssue:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class NutrientValue:
    raw_value: RawScalar
    parsed_value: Decimal | None
    status: Literal['reported', 'missing', 'unparsed', 'invalid']
    reason: str
    parsing_rule: str | None


def parse_value(raw: RawScalar) -> NutrientValue:
    """ASCII unsigned integer/decimal, surrounded by ASCII whitespace only.

    No value imputation, float rounding, ratio/threshold parsing or unit conversion.
    JSON numeric values are unsupported by this string-valued snapshot format.
    """
    if raw is None:
        return NutrientValue(raw, None, 'missing', 'null_value', None)
    if not isinstance(raw, str):
        return NutrientValue(raw, None, 'invalid', 'expected_string_or_null', None)
    if raw == '' or raw.isspace():
        return NutrientValue(raw, None, 'missing', 'empty_value' if raw == '' else 'blank_value', None)
    token = raw.strip(' \t\r\n')
    if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', token):
        return NutrientValue(raw, Decimal(token), 'reported', 'explicit_numeric_literal',
                             'ascii_unsigned_decimal_with_ascii_padding_v1')
    if re.fullmatch(r'-[0-9]+(?:\.[0-9]+)?', token):
        return NutrientValue(raw, None, 'invalid', 'negative_nutrient_value', None)
    return NutrientValue(raw, None, 'unparsed', 'unsupported_or_undefined_token', None)


@dataclass(frozen=True)
class SourceEvidence:
    source_url: str
    retrieved_at_utc: str
    archive_sha256: str
    member_sha256: str
    sample_filename: str
    sample_sha256: str
    source_row_index: int
    source_object_sha256: str
    sample_row_index: int
    value_field: str = '每100克含量'


@dataclass(frozen=True)
class NutrientObservation:
    original_name: str
    original_category: str | None
    value: NutrientValue
    unit: str | None
    evidence: SourceEvidence
    raw_fields: tuple[tuple[str, RawScalar], ...]


@dataclass(frozen=True)
class FoodRecord:
    food_id: str
    original_name: str
    data_type: str
    category: str | None
    description: str | None
    observations: tuple[NutrientObservation, ...]
    issues: tuple[SourceIssue, ...]
    official_dataset_version: None = None
    measurement_amount: int = 100  # Explicit field label, not approved edible-weight basis.
    measurement_unit: str = 'g'
    edible_portion_basis: Literal['unresolved'] = 'unresolved'
    food_state_applicability: Literal['unresolved'] = 'unresolved'
    weight_basis_applicability: Literal['unresolved'] = 'unresolved'
    identity_approval: Literal['unresolved'] = 'unresolved'
    production_eligible: Literal[False] = False


@dataclass(frozen=True)
class SourceLookup:
    status: Literal['found', 'not_found', 'ambiguous', 'invalid_record']
    reason: str
    records: tuple[FoodRecord, ...] = ()
    issues: tuple[SourceIssue, ...] = ()


def _build_record(rows, metadata, snapshot) -> FoodRecord:
    """Internal transformation of verified rows; all fields and evidence are retained."""
    key = tuple(metadata['diagnosis']['food_key'])
    if not rows or len(key) != 3 or any(not isinstance(part, str) or not part for part in key):
        raise ValueError('Missing food identity')
    observations = []
    issues = []
    analyses = set()
    for index, row in enumerate(rows):
        path = f'/samples/{metadata["filename"]}/{index}'
        if set(row) != set(FIELDS):
            raise ValueError(f'{path}: invalid field set')
        if any(value is not None and not isinstance(value, (str, int, float, bool)) for value in row.values()):
            raise ValueError(f'{path}: unsupported non-scalar field')
        if any(row.get(field) != expected for field, expected in zip(('整合編號', '樣品名稱', '資料類別'), key)):
            raise ValueError(f'{path}: food identity mismatch')
        for field in FIELDS:
            if field != '每100克含量' and row[field] is not None and not isinstance(row[field], str):
                raise ValueError(f'{path}/{field}: expected string or null')
        name = row['分析項']
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f'{path}/分析項: missing nutrient name')
        if name in analyses:
            issues.append(SourceIssue('duplicate_analysis_item', path + '/分析項', 'Duplicate nutrient name; no merging'))
        analyses.add(name)
        value = parse_value(row['每100克含量'])
        if value.status == 'invalid':
            issues.append(SourceIssue(value.reason, path + '/每100克含量', 'Invalid value retained without numeric interpretation'))
        evidence = SourceEvidence(snapshot['source_url'], snapshot['retrieved_at_utc'],
                                  snapshot['archive_sha256'], snapshot['member_sha256'],
                                  metadata['filename'], metadata['sha256'],
                                  metadata['source_row_indices_zero_based'][index],
                                  metadata['source_object_sha256'][index], index)
        observations.append(NutrientObservation(name, row['分析項分類'], value, row['含量單位'],
                                                evidence, tuple(row.items())))
    for field in ('食品分類', '內容物描述', '俗名', '廢棄率', '每單位重'):
        if len({row[field] for row in rows}) > 1:
            issues.append(SourceIssue('metadata_conflict', f'/samples/{metadata["filename"]}/{field}',
                                      'Conflicting metadata; no automatic selection'))
    return FoodRecord(*key, rows[0]['食品分類'], rows[0]['內容物描述'], tuple(observations), tuple(issues))


def _load_snapshot(directory):
    """Pinned metadata plus full source replay prevent edited manifests authorizing rows."""
    directory = Path(directory)
    verified_bytes = {}
    for name, expected in (('official-export.zip', ARCHIVE_SHA256),
                           ('source-provenance.json', PROVENANCE_SHA256),
                           ('samples/inspection.json', INSPECTION_SHA256)):
        raw = (directory / name).read_bytes()
        if sha256(raw).hexdigest() != expected:
            raise SnapshotError('pinned_hash_mismatch', '/snapshot/' + name)
        if name != 'official-export.zip':
            verified_bytes[name] = raw
    snapshot = json.loads(verified_bytes['source-provenance.json'].decode('utf-8'),
                          object_pairs_hook=unique_keys)
    if snapshot['source_url'] != SOURCE_URL or snapshot['archive_sha256'] != ARCHIVE_SHA256:
        raise SnapshotError('source_identity_mismatch', '/snapshot/source-provenance.json')
    try:
        report = verify(directory / 'official-export.zip', directory / 'samples')
    except ValueError:
        raise SnapshotError('source_replay_failed', '/snapshot/samples') from None
    manifest = json.loads(verified_bytes['samples/inspection.json'].decode('utf-8'))
    if len(manifest['records']) != 8 or sum(r['diagnosis']['row_count'] for r in manifest['records']) != 832:
        raise SnapshotError('fixed_sample_size_mismatch', '/snapshot/samples/inspection.json')
    if snapshot['member_sha256'] != report['member_sha256']:
        raise SnapshotError('source_member_mismatch', '/snapshot/official-export.zip')
    foods = []
    for metadata in manifest['records']:
        filename = metadata['filename']  # Validated as basename by verify().
        with (directory / 'samples' / filename).open(encoding='utf-8') as stream:
            captured = list(iter_rows(stream))
        raw = (directory / 'samples' / filename).read_bytes()
        if sha256(raw).hexdigest() != metadata['sha256']:
            raise SnapshotError('sample_changed_during_loading', '/snapshot/samples/' + filename)
        if [sha256(text.encode('utf-8')).hexdigest() for _, _, text in captured] != metadata['source_object_sha256']:
            raise SnapshotError('source_objects_changed_during_loading', '/snapshot/samples/' + filename)
        foods.append(_build_record([row for _, row, _ in captured], metadata, snapshot))
    return tuple(foods)


class TFDANutritionSourceAdapter:
    """Load once, immutable results, exact official-ID lookup. No implicit local path."""

    def __init__(self, snapshot_directory):
        self._foods = ()
        self._issues = ()
        try:
            self._foods = _load_snapshot(snapshot_directory)
        except SnapshotError as error:
            self._issues = (SourceIssue(error.code, error.path, 'Snapshot evidence verification failed'),)
        except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
            # Do not leak caller's absolute paths or OS error payloads through the API.
            code = ('snapshot_missing' if isinstance(error, FileNotFoundError) else
                    'snapshot_unreadable' if isinstance(error, OSError) else 'snapshot_invalid_structure')
            self._issues = (SourceIssue(code, '/snapshot',
                                        'Snapshot missing, unreadable, malformed or integrity check failed'),)
            self._foods = ()

    @property
    def issues(self) -> tuple[SourceIssue, ...]:
        return self._issues

    def list_foods(self) -> tuple[FoodRecord, ...]:
        return self._foods

    def lookup(self, food_id: str) -> SourceLookup:
        if self._issues:
            return SourceLookup('invalid_record', self._issues[0].code, issues=self._issues)
        if not isinstance(food_id, str) or not food_id or food_id != food_id.strip():
            issue = SourceIssue('invalid_food_id', '/query/food_id', 'Expected exact nonempty official ID')
            return SourceLookup('invalid_record', issue.code, issues=(issue,))
        matches = tuple(record for record in self._foods if record.food_id == food_id)
        if not matches:
            return SourceLookup('not_found', 'id_not_in_fixed_sample')
        if len(matches) > 1:
            keys = {(r.food_id, r.original_name, r.data_type) for r in matches}
            status = 'ambiguous' if len(keys) > 1 else 'invalid_record'
            return SourceLookup(status, 'multiple_records_for_id' if status == 'ambiguous' else 'duplicate_food_identity', matches)
        if matches[0].issues:
            return SourceLookup('invalid_record', 'record_integrity_or_value_error', matches, matches[0].issues)
        return SourceLookup('found', 'exact_id_in_verified_snapshot', matches)
