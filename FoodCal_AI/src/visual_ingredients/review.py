"""Injected, pinned sample decisions for local tests; not reviewer authentication."""
from copy import deepcopy
from hashlib import sha256
import json
import re


def fingerprint(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def prediction_scope(snapshot):
    return fingerprint({'image_dimensions': [snapshot['image']['width'], snapshot['image']['height']],
                        'model_identifier': snapshot['model']['identifier'],
                        'model_sha256': snapshot['model'].get('sha256'),
                        'inference_settings': snapshot.get('inference_settings'),
                        'detections': snapshot['detections']})


class SampleReviewContext:
    """Owner injects independently held records + expected pin. Test-only always.

    Pin verifies integrity, not authenticity. A caller controlling both can forge
    a context. Keep this object out of untrusted request data/UI input.
    JSON serialization detaches mutable input and every lookup returns a copy.
    """
    def __init__(self, records, *, expected_sha256, class_names):
        if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
            raise ValueError('invalid_sample_review')
        if fingerprint(records) != expected_sha256:
            raise ValueError('review_context_hash_mismatch')
        from src.recipes import RecipeRetriever
        from .adapter import CLASSES
        validator = RecipeRetriever(classes_path=CLASSES)
        if tuple(class_names) != validator.class_names:
            raise ValueError('review_taxonomy_mismatch')
        taxonomy = fingerprint(list(class_names))
        seen = set()
        review_ids = set()
        for record in records:
            if (record.get('usage') != 'test_only' or record.get('policy_version') != 'visual-annotation-v0' or
                    record.get('taxonomy_sha256') != taxonomy or
                    not re.fullmatch('[0-9a-f]{64}', record.get('image_sha256', '')) or
                    any(not isinstance(record.get(k), str) or not record[k].strip()
                        for k in ('review_id','review_process','reason')) or
                    not isinstance(record.get('evidence_refs'), list) or not record['evidence_refs'] or
                    any(not isinstance(ref,str) or not ref.strip() for ref in record['evidence_refs']) or
                    record.get('ingredient_candidate') not in (None, '熟蛋') or
                    type(record.get('detection_id')) is not int or record['detection_id'] < 1 or
                    record.get('review_status') not in ('reviewed_visible_ingredient','ambiguous','rejected','insufficient_evidence') or
                    record.get('prediction_kind') not in ('synthetic_mock_prediction','saved_model_prediction')):
                raise ValueError('invalid_sample_review')
            if record['review_id'] in review_ids:
                raise ValueError('duplicate_review_id')
            review_ids.add(record['review_id'])
            snapshot = record.get('prediction_snapshot')
            validator.retrieve(snapshot)
            if record['prediction_kind'] == 'synthetic_mock_prediction' and not snapshot['model']['identifier'].startswith('mock:'):
                raise ValueError('mock_prediction_identity_mismatch')
            predictions = [p for p in snapshot['detections'] if p['detection_id']==record['detection_id']]
            if len(predictions) != 1:
                raise ValueError('review_prediction_missing')
            if record['review_status'] == 'reviewed_visible_ingredient':
                if (predictions[0]['class_id'] != 0 or record.get('semantic_level') != 'ingredient-level' or
                        record.get('ingredient_candidate') != '熟蛋' or record.get('visibility') != 'clear'):
                    raise ValueError('visible_review_conditions_not_met')
            key = (record['image_sha256'], prediction_scope(snapshot), record['detection_id'])
            if key in seen:
                raise ValueError('duplicate_sample_review')
            seen.add(key)
        self._records_json = json.dumps(records, ensure_ascii=False, allow_nan=False)
        self._pin = expected_sha256
        self._taxonomy = taxonomy

    def lookup(self, snapshot, *, image_sha256, class_names, detection_id):
        if self._taxonomy != fingerprint(list(class_names)):
            return None
        records = json.loads(self._records_json)
        if fingerprint(records) != self._pin:
            return None
        for record in records:
            if (record['image_sha256'] == image_sha256 and record['detection_id'] == detection_id and
                    prediction_scope(record['prediction_snapshot']) == prediction_scope(snapshot)):
                return deepcopy(record)
        return None
