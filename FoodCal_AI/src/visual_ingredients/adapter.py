"""Diagnostic evidence only. Current dish models cannot establish ingredients."""
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Literal, TypedDict

from src.recipes import RecipeRetriever

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / 'examples/visual_ingredients_v1/fixture.json'
CLASSES = ROOT / 'examples/diagnostic_v0/classes.yaml'


class VisualIngredientEvidence(TypedDict):
    ingredient_candidate: str | None
    evidence_source: dict
    evidence_type: Literal['ingredient_annotation', 'dish_detection_only', 'dish_classification_only', 'reviewed_prediction']
    confidence: float | None
    image_region: dict | None
    status: Literal['observed_candidate', 'uncertain', 'unavailable']
    reason: str | None


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _envelope(visual, hidden, *, status, reason):
    return {'schema_version': 1, 'usage': 'diagnostic/test_only', 'status': status,
            'reason': reason, 'visual_ingredient_evidence': visual,
            'recipe_assisted_hidden_ingredients': hidden,
            'photo_portion_grams': None, 'calorie_estimate': {'status': 'unavailable', 'value': None},
            'nutrition_eligible': False}


def recipe_assisted_hidden_ingredients(recipe, names):
    """Explicit caller-selected recipe hypotheses, not visually observed ingredients.

    Source metadata is retained, never authenticated here. No quantity transfer.
    Only named entries actually present in the supplied recipe list are accepted.
    """
    if not isinstance(recipe, dict) or not _text(recipe.get('recipe_id')):
        raise ValueError('invalid_recipe_identity')
    source = recipe.get('source')
    if (not isinstance(source, dict) or not all(_text(source.get(k)) for k in
                                              ('source_id', 'reference', 'kind'))):
        raise ValueError('recipe_source_unavailable')
    ingredients = recipe.get('ingredients')
    if (not isinstance(ingredients, list) or not all(_text(x) for x in ingredients) or
            not isinstance(names, list) or not all(_text(x) for x in names) or
            len(set(names)) != len(names) or any(x not in ingredients for x in names)):
        raise ValueError('hidden_candidate_not_in_recipe')
    return [{'ingredient_candidate': name, 'evidence_source': {
                'recipe_id': recipe['recipe_id'], 'source': deepcopy(source),
                'ingredient_index': ingredients.index(name)},
             'evidence_type': 'inferred_from_recipe', 'status': 'uncertain',
             'confidence': None, 'image_region': None, 'quantity': None, 'unit': None,
             'reason': 'recipe_presence_does_not_establish_photo_presence',
             'source_authentication': 'not_performed', 'usage': 'diagnostic/test_only'}
            for name in names]


class VisualIngredientEvidenceAdapter:
    """No model loading. Caller supplies the existing Detection API snapshot.

    No class-name allowlist grants ingredient semantics, including egg/loofah.
    Default input remains unavailable; v1.1 permits pinned test-only sample reviews.
    """
    def __init__(self, *, classes_path=CLASSES):
        self._validator = RecipeRetriever(classes_path=classes_path)

    @property
    def class_names(self):
        return self._validator.class_names

    def from_detection(self, detection_result):
        # Reuse the existing schema/class/box checks without copying them.
        self._validator.retrieve(detection_result)
        source = {'model_identifier': detection_result['model']['identifier'],
                  'model_sha256': detection_result['model'].get('sha256'),
                  'kind': 'dish_detection', 'source_authentication': 'not_performed'}
        visual = []
        for detection in detection_result['detections']:
            visual.append({'ingredient_candidate': None,
                           'evidence_source': {**source, 'detection_id': detection['detection_id'],
                                               'dish_candidate': detection['class_name'],
                                               'dish_confidence': detection['confidence']},
                           'evidence_type': 'dish_detection_only', 'confidence': None,
                           'image_region': deepcopy(detection['bounding_box']),
                           'status': 'unavailable', 'reason': 'ingredient_level_semantics_unverified'})
        return _envelope(visual, [], status='unavailable',
                         reason='ingredient_level_semantics_unverified' if visual else
                                'no_detections_does_not_establish_ingredient_absence')

    def from_reviewed_detection(self, detection_result, *, image_sha256, review_context):
        """No GT input. Only independently injected, exact sample reviews apply."""
        from .review import SampleReviewContext
        result = self.from_detection(detection_result)
        if not isinstance(review_context, SampleReviewContext):
            result['reason'] = 'controlled_review_context_required'
            return result
        for evidence, prediction in zip(result['visual_ingredient_evidence'], detection_result['detections']):
            record = review_context.lookup(detection_result, image_sha256=image_sha256,
                                          class_names=self._validator.class_names,
                                          detection_id=prediction['detection_id'])
            if record is None:
                evidence['reason'] = 'sample_review_scope_unmatched'
                continue
            status = record['review_status']
            evidence['evidence_source'].update(review_id=record['review_id'], review_status=status,
                kind='local_sample_review',
                review_process=record['review_process'], policy_version=record['policy_version'],
                image_sha256=image_sha256, evidence_refs=deepcopy(record['evidence_refs']),
                prediction_kind=record['prediction_kind'], usage='test_only',
                source_authentication='not_performed')
            evidence['reason'] = record['reason']
            if status == 'reviewed_visible_ingredient':
                evidence.update(ingredient_candidate=record['ingredient_candidate'],
                                evidence_type='reviewed_prediction', confidence=prediction['confidence'],
                                status='observed_candidate')
            elif status == 'ambiguous':
                evidence.update(ingredient_candidate=record.get('ingredient_candidate'),
                                evidence_type='reviewed_prediction', confidence=None, status='uncertain')
        if any(e['status'] != 'unavailable' for e in result['visual_ingredient_evidence']):
            result.update(status='partial', reason='sample_review_test_only_not_class_approval')
        return result

    def from_classification(self, predictions, *, model_identifier):
        """Accept FoodRecognizer's top-k list; scores remain dish scores only."""
        if not _text(model_identifier) or not isinstance(predictions, list):
            raise ValueError('invalid_classification_input')
        for item in predictions:
            if (not isinstance(item, dict) or not _text(item.get('food')) or
                    type(item.get('confidence')) not in (int, float) or
                    not math.isfinite(item['confidence']) or not 0 <= item['confidence'] <= 1):
                raise ValueError('invalid_classification_input')
        visual = [{'ingredient_candidate': None,
                   'evidence_source': {'kind': 'dish_classification', 'model_identifier': model_identifier,
                                       'dish_candidate': item['food'], 'dish_confidence': item['confidence'],
                                       'source_authentication': 'not_performed'},
                   'evidence_type': 'dish_classification_only', 'confidence': None,
                   'image_region': None, 'status': 'unavailable',
                   'reason': 'classification_is_not_ingredient_detection'} for item in predictions]
        return _envelope(visual, [], status='unavailable', reason='classification_is_not_ingredient_detection')

    def fixture_demo(self):
        """Fixed synthetic annotation fixture, never a model output or real photo."""
        raw = FIXTURE.read_bytes()
        fixture = json.loads(raw)
        if fixture.get('fixture_version') != 1 or fixture.get('usage') != 'synthetic/test_only':
            raise ValueError('invalid_fixture_identity')
        image = fixture['image']
        if (not _text(image.get('image_id')) or
                any(type(image.get(k)) is not int or image[k] <= 0 for k in ('width', 'height'))):
            raise ValueError('invalid_fixture_image')
        visual = []
        identifiers = set()
        for item in fixture['annotations']:
            identifier = item.get('annotation_id')
            if not _text(identifier) or identifier in identifiers or not _text(item.get('ingredient_candidate')):
                raise ValueError('invalid_annotation_identity')
            identifiers.add(identifier)
            if item.get('status') not in ('observed_candidate', 'uncertain'):
                raise ValueError('invalid_annotation_status')
            score = item.get('confidence')
            if score is not None and (type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1):
                raise ValueError('invalid_annotation_confidence')
            box = item['image_region']
            if (box.get('format') != 'xyxy' or box.get('coordinate_space') != 'original_image_pixels' or
                    any(type(box.get(k)) not in (int, float) or not math.isfinite(box[k])
                        for k in ('x_min', 'y_min', 'x_max', 'y_max')) or
                    not 0 <= box['x_min'] < box['x_max'] <= image['width'] or
                    not 0 <= box['y_min'] < box['y_max'] <= image['height']):
                raise ValueError('invalid_annotation_region')
            visual.append({'ingredient_candidate': item['ingredient_candidate'],
                           'evidence_source': {'kind': 'synthetic_annotation', 'image_id': image['image_id'],
                                               'annotation_id': identifier, 'usage': 'synthetic/test_only',
                                               'reference': 'fixture://visual_ingredients_v1/fixture.json',
                                               'fixture_sha256': sha256(raw.replace(b'\r\n', b'\n')).hexdigest(),
                                               'source_authentication': 'not_performed'},
                           'evidence_type': 'ingredient_annotation', 'confidence': score,
                           'image_region': deepcopy(box), 'status': item['status'],
                           'reason': 'synthetic_annotation_not_real_visual_observation'})
        hidden = recipe_assisted_hidden_ingredients(fixture['recipe'], fixture['hidden_candidates'])
        return _envelope(visual, hidden, status='partial', reason='synthetic_fixture_only')
