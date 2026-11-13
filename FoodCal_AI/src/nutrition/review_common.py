"""Evidence-protocol helpers shared by the recipe (Review v1) and photo item source reviews.

Moved out of review_validation.py without behavior changes: every helper verifies pinned records and
evidence exactly as before, never infers semantics and never confers trust.
"""
from hashlib import sha256
import io
import json
import math
import re

from experiments.tfda_nutrition_sample.inspect_sample import iter_rows, unique_keys, reject_constant
from .tfda_source import FoodRecord, parse_value
from .review_types import ReviewContext, ReviewTrustAnchor

EVIDENCE_FIELDS = frozenset({'evidence_id', 'origin', 'attachment_ref', 'attachment_sha256', 'locator', 'raw_value',
                             'evidence_text', 'statement_role'})


def text(value):
    return isinstance(value, str) and bool(value.strip())


def digest(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


class Reject(Exception):
    def __init__(self, code, path, state='invalid'):
        self.code, self.path, self.state = code, path, state


def require(condition, code, path, state='invalid'):
    if not condition:
        raise Reject(code, path, state)


def load_json(raw):
    return json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys, parse_constant=reject_constant)


def json_pointer(value, pointer):
    """RFC 6901 including root pointer and escaped ~ /; no URI fragments."""
    require(isinstance(pointer, str) and (pointer == '' or pointer.startswith('/')), 'invalid_json_pointer', '/evidence/locator')
    for token in pointer.split('/')[1:] if pointer else ():
        require(re.search(r'~(?:[^01]|$)', token) is None, 'invalid_json_pointer', '/evidence/locator')
        token = token.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list):
            require(re.fullmatch(r'0|[1-9][0-9]*', token) is not None and int(token) < len(value),
                    'unresolved_json_pointer', '/evidence/locator')
            value = value[int(token)]
        else:
            require(isinstance(value, dict) and token in value, 'unresolved_json_pointer', '/evidence/locator')
            value = value[token]
    return value


def same(a, b):
    # bool vs numeric zero/one is not equality for evidence.
    return type(a) is type(b) and a == b


def load_trusted_ledger(context, anchor):
    """Ledger bytes pinned by an independently configured anchor; a request cannot self-attest."""
    require(isinstance(context, ReviewContext) and isinstance(anchor, ReviewTrustAnchor)
            and text(anchor.authority_ref) and digest(anchor.context_sha256),
            'review_context_untrusted', '/context', 'blocked')
    require(isinstance(context.record_bytes, bytes)
            and sha256(context.record_bytes).hexdigest() == anchor.context_sha256,
            'context_hash_mismatch', '/context')
    return load_json(context.record_bytes)


def resolve_attachments(entries, context, catalog, expected_scope):
    """Bind each referenced attachment to its trusted catalog entry and verify byte hashes."""
    resolved = {}
    for ref in entries:
        require(isinstance(ref, dict) and set(ref) == {'attachment_id', 'representation', 'sha256'},
                'attachment_ref', '/input/attachment_refs')
        key = ref['attachment_id']
        require(text(key) and key in context.attachments and key in catalog, 'missing_attachment', '/attachments')
        meta, raw = catalog[key], context.attachments[key]
        require(set(meta) == {'origin', 'representation', 'sha256', 'scope'}, 'attachment_catalog', '/context/attachment_catalog')
        require(meta['scope'] == expected_scope, 'attachment_scope', '/attachments/' + key)
        require(ref['representation'] == meta['representation'] and ref['sha256'] == meta['sha256'],
                'attachment_manifest_mismatch', '/attachments/' + key)
        if ref['representation'] == 'bytes':
            require(isinstance(raw, bytes) and digest(ref['sha256']) and sha256(raw).hexdigest() == ref['sha256'],
                    'evidence_hash_mismatch', '/attachments/' + key)
        else:
            require(ref['representation'] == 'native' and ref['sha256'] is None, 'attachment_representation', '/attachments/' + key)
        resolved[key] = raw
    return resolved


def verify_food_record(lookup, source, path):
    """The single found TFDA record, still unpromoted and exactly the scoped candidate."""
    require(lookup.status == 'found' and len(lookup.records) == 1 and isinstance(lookup.records[0], FoodRecord),
            'invalid_source_lookup', path)
    food = lookup.records[0]
    require(food.production_eligible is False and food.identity_approval == 'unresolved',
            'untrusted_source_promotion', path)
    require(all(getattr(food, name) == 'unresolved' for name in
                ('edible_portion_basis', 'food_state_applicability', 'weight_basis_applicability')),
            'untrusted_source_promotion', path)
    require((food.food_id, food.original_name, food.data_type) == (source['food_id'], source['original_name'], source['data_type']),
            'food_identity_scope', '/input/candidate_scope')
    return food


def verify_source_observations(food, provenance, source, resolved):
    """Re-derive every observation from pinned provenance and sample bytes; returns (provenance, rows by index)."""
    require(isinstance(provenance, bytes), 'provenance_requires_bytes', '/attachments/source_provenance')
    prov = load_json(provenance)
    require(prov['archive_sha256'] == source['archive_sha256'] and prov['member_sha256'] == source['member_sha256'],
            'source_hash_mismatch', '/attachments/source_provenance')
    require(text(prov.get('source_url')) and text(prov.get('retrieved_at_utc')), 'missing_source_metadata', '/attachments/source_provenance')
    by_index, sample_cache, used_rows = {}, {}, {}
    for obs in food.observations:
        proof = obs.evidence
        require(proof.source_url == prov['source_url'] and proof.retrieved_at_utc == prov['retrieved_at_utc']
                and proof.value_field == '每100克含量', 'source_provenance_mismatch', '/source/observations')
        require(type(proof.source_row_index) is int and proof.source_row_index >= 0 and proof.source_row_index not in by_index,
                'source_row_index', '/source/observations')
        require((proof.archive_sha256, proof.member_sha256, proof.sample_sha256) ==
                (source['archive_sha256'], source['member_sha256'], source['sample_sha256']), 'source_hash_mismatch', '/source/observations')
        samples = [raw for key, raw in resolved.items() if isinstance(raw, bytes) and sha256(raw).hexdigest() == proof.sample_sha256]
        require(samples, 'missing_source_record', '/source/observations')
        if proof.sample_sha256 not in sample_cache:
            sample_cache[proof.sample_sha256] = list(iter_rows(io.StringIO(samples[0].decode('utf-8'))))
        rows = sample_cache[proof.sample_sha256]
        require(type(proof.sample_row_index) is int and 0 <= proof.sample_row_index < len(rows), 'source_row_index', '/source/observations')
        row, row_text = rows[proof.sample_row_index][1:]
        require(sha256(row_text.encode('utf-8')).hexdigest() == proof.source_object_sha256
                and tuple(row.items()) == obs.raw_fields
                and (row['整合編號'], row['樣品名稱'], row['資料類別']) == (food.food_id, food.original_name, food.data_type),
                'source_object_mismatch', '/source/observations')
        require(obs.original_name == row['分析項'] and same(obs.unit, row['含量單位'])
                and obs.value == parse_value(row['每100克含量']), 'source_value_mismatch', '/source/observations')
        by_index[proof.source_row_index] = (row, proof)
        used_rows.setdefault(proof.sample_sha256, set()).add(proof.sample_row_index)
    require(all(indices == set(range(len(sample_cache[key]))) for key, indices in used_rows.items()),
            'incomplete_source_observations', '/source/observations')
    return prov, by_index


def check_evidence_record(ev, seen, resolved, catalog, path):
    """Common evidence record checks; returns the pinned attachment bytes the locator reads from."""
    require(isinstance(ev, dict) and set(ev) == EVIDENCE_FIELDS and text(ev['evidence_id'])
            and ev['evidence_id'] not in seen, 'evidence_fields_or_id', path)
    key = ev['attachment_ref']
    require(key in resolved and catalog[key]['representation'] == 'bytes' and ev['origin'] == catalog[key]['origin'],
            'evidence_origin_or_attachment', path)
    raw = resolved[key]
    require(ev['attachment_sha256'] == sha256(raw).hexdigest(), 'evidence_hash_mismatch', path)
    if raw.startswith(b'%PDF'):
        raise Reject('unsupported_evidence_format', path, 'blocked')
    scalar = ev['raw_value']
    require(scalar is None or type(scalar) in (str, int, bool) or type(scalar) is float and math.isfinite(scalar),
            'evidence_scalar', path)
    require(ev['evidence_text'] is None or isinstance(ev['evidence_text'], str), 'evidence_text_type', path)
    return raw


def official_record_value(ev, by_index, path):
    """Field of one verified TFDA row, addressed by row index, object hash and field name."""
    loc = ev['locator']
    require(set(loc) == {'sample_filename', 'source_row_index', 'source_object_sha256', 'field_name'}, 'record_locator', path)
    require(type(loc['source_row_index']) is int and loc['source_row_index'] in by_index, 'cross_record_evidence', path)
    row, proof = by_index[loc['source_row_index']]
    require(loc['sample_filename'] == proof.sample_filename and loc['source_object_sha256'] == proof.source_object_sha256
            and ev['attachment_sha256'] == proof.sample_sha256 and loc['field_name'] in row, 'record_locator_mismatch', path)
    return row[loc['field_name']]


def official_document_value(ev, raw, source, path):
    """Pointer into a pinned official document page that applies to the scoped archive."""
    loc = ev['locator']
    require(set(loc) == {'page', 'section'} and type(loc['page']) is int and loc['page'] > 0, 'document_locator', path)
    document = load_json(raw)
    require(type(document['page']) is int and document['page'] == loc['page'] and document['archive_sha256'] == source['archive_sha256'],
            'document_applicability', path)
    return json_pointer(document, loc['section'])


def pointer_value(ev, raw, code, path):
    """Pointer into pinned JSON bytes; `code` names the locator rule that failed."""
    loc = ev['locator']
    require(set(loc) == {'pointer'}, code, path)
    return json_pointer(load_json(raw), loc['pointer'])


def check_extracted(ev, extracted, path):
    require(same(ev['raw_value'], extracted), 'evidence_value_mismatch', path)
    if ev['evidence_text'] is not None:
        require(isinstance(extracted, str) and ev['evidence_text'] == extracted, 'evidence_text_mismatch', path)


def index_review_records(reviews, results):
    """One independently witnessed ledger record per attribute result, keyed by (domain, attribute, nutrient)."""
    require(isinstance(reviews, list) and len(reviews) == len(results), 'review_record_count', '/context/reviews')
    manual = {}
    for record in reviews:
        require(set(record) == {'result', 'review_status', 'method', 'process_ref'} and text(record['process_ref']),
                'review_record_fields', '/context/reviews')
        r = record['result']
        key = (r['domain'], r['attribute'], r['nutrient_key'])
        require(key not in manual, 'duplicate_review_record', '/context/reviews')
        manual[key] = record
    return manual
