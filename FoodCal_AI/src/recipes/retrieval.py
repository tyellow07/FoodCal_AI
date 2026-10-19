"""Local catalog adapter: exact taxonomy matching, never recipe generation."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from src.detection.model import DEFAULT_CLASSES_PATH


class InvalidDetectionResultError(ValueError):
    """Input does not satisfy the supported Food Detection schema."""


class _InvalidCatalog(ValueError):
    pass


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class RecipeRetriever:
    """Snapshot a local catalog once; no model, network or supplier dependency.

    Production provenance requires caller-supplied, approved source metadata.
    This checks metadata, not the authenticity of the external source itself.
    Test fixtures require explicit opt-in and retain their test-only identity.
    """
    def __init__(self, catalog_path=None, *, include_test_fixtures=False,
                 classes_path=DEFAULT_CLASSES_PATH):
        import yaml

        names = yaml.safe_load(Path(classes_path).read_text(encoding='utf-8'))
        names = names.get('names') if isinstance(names, dict) else None
        if (not isinstance(names, list) or len(names) != 15 or
                not all(_text(name) for name in names) or len(set(names)) != 15):
            raise ValueError('Expected the 15-class dataset mapping')
        if not isinstance(include_test_fixtures, bool):
            raise ValueError('include_test_fixtures must be bool')
        self.class_names = tuple(names)
        self.include_test_fixtures = include_test_fixtures
        self._records = []
        self._catalog = {'path': None, 'sha256': None, 'status': 'unavailable',
                         'reason': 'no_catalog_configured'}
        if catalog_path is None:
            return
        path = Path(catalog_path).expanduser().resolve()
        self._catalog['path'] = str(path)
        try:
            raw = path.read_bytes()
        except OSError:
            self._catalog['reason'] = 'catalog_unreadable'
            return
        self._catalog['sha256'] = hashlib.sha256(raw).hexdigest()
        try:
            catalog = json.loads(raw)
            if (not isinstance(catalog, dict) or type(catalog.get('schema_version')) is not int or
                    catalog['schema_version'] != 1 or not isinstance(catalog.get('recipes'), list)):
                raise _InvalidCatalog('invalid_catalog_schema')
            keys = set()
            records = []
            for record in catalog['recipes']:
                candidate = self._validate_recipe(record)
                key = (candidate['source']['source_id'], candidate['recipe_id'])
                if key in keys:
                    raise _InvalidCatalog('duplicate_recipe_identity')
                keys.add(key)
                records.append(candidate)
            self._records = sorted(records, key=lambda r: (r['source']['source_id'], r['recipe_id']))
        except (ValueError, UnicodeError) as error:
            self._catalog['reason'] = str(error) if isinstance(error, _InvalidCatalog) else 'invalid_catalog_json'
            return
        self._catalog.update(status='loaded', reason=None)

    def _class_pair(self, value):
        return (isinstance(value, dict) and type(value.get('class_id')) is int and
                0 <= value['class_id'] < len(self.class_names) and
                value.get('class_name') == self.class_names[value['class_id']])

    def _validate_recipe(self, record):
        if (not isinstance(record, dict) or not _text(record.get('recipe_id')) or
                not _text(record.get('name')) or not self._class_pair(record.get('food_class'))):
            raise _InvalidCatalog('invalid_recipe_identity_or_class')
        source = record.get('source')
        if (not isinstance(source, dict) or not _text(source.get('source_id')) or
                not _text(source.get('reference')) or
                source.get('kind') not in ('production', 'test_fixture') or
                source.get('review_status') != ('approved' if source['kind'] == 'production' else 'test_only')):
            raise _InvalidCatalog('invalid_or_unapproved_source')
        result = {key: deepcopy(record[key]) for key in ('recipe_id', 'name', 'food_class')}
        result['source'] = {key: source[key] for key in ('source_id', 'reference', 'kind', 'review_status')}
        for key in ('ingredients', 'cooking_methods'):
            if key in record:
                if not isinstance(record[key], list) or not all(_text(item) for item in record[key]):
                    raise _InvalidCatalog('invalid_recipe_optional_fields')
                result[key] = list(record[key])
        return result

    def _validate_detection_result(self, result):
        def invalid():
            raise InvalidDetectionResultError('Expected schema-1 Food Detection result with valid object identities/classes/boxes')

        if (not isinstance(result, dict) or type(result.get('schema_version')) is not int or
                result['schema_version'] != 1 or not isinstance(result.get('detections'), list)):
            invalid()
        image, model = result.get('image'), result.get('model')
        if (not isinstance(image, dict) or not isinstance(model, dict) or not _text(model.get('identifier')) or
                not all(type(image.get(k)) is int and image[k] > 0 for k in ('width', 'height'))):
            invalid()
        detections = result['detections']
        if result.get('status') != ('detections_available' if detections else 'no_detections'):
            invalid()
        identifiers = set()
        for detection in detections:
            if (not self._class_pair(detection) or type(detection.get('detection_id')) is not int or
                    detection['detection_id'] < 1 or detection['detection_id'] in identifiers or
                    not _number(detection.get('confidence')) or not 0 <= detection['confidence'] <= 1 or
                    detection.get('model_identifier') != model['identifier'] or
                    detection.get('interpretation') != 'candidate'):
                invalid()
            identifiers.add(detection['detection_id'])
            box = detection.get('bounding_box')
            keys = ('x_min', 'y_min', 'x_max', 'y_max')
            if (not isinstance(box, dict) or box.get('format') != 'xyxy' or
                    box.get('coordinate_space') != 'original_image_pixels' or
                    not all(_number(box.get(k)) for k in keys)):
                invalid()
            if (not -1e-4 <= box['x_min'] < box['x_max'] <= image['width'] + 1e-4 or
                    not -1e-4 <= box['y_min'] < box['y_max'] <= image['height'] + 1e-4):
                invalid()

    def retrieve(self, detection_result, *, limit=5):
        """Keep one object per detection; repeated/overlapping boxes are not merged."""
        if type(limit) is not int or limit < 1:
            raise ValueError('limit must be a positive integer')
        self._validate_detection_result(detection_result)
        objects = []
        for detection in detection_result['detections']:
            candidates = []
            fixture_excluded = False
            for recipe in self._records:
                if recipe['food_class']['class_id'] != detection['class_id']:
                    continue
                if recipe['source']['kind'] == 'test_fixture' and not self.include_test_fixtures:
                    fixture_excluded = True
                    continue
                candidates.append({**deepcopy(recipe), 'retrieval_score': 1.0,
                                   'score_method': 'exact_class_id_and_name_match',
                                   'interpretation': 'recipe_prior'})
            candidates = candidates[:limit]
            reason = None if candidates else (
                self._catalog['reason'] if self._catalog['status'] != 'loaded' else
                'test_fixtures_disabled' if fixture_excluded else 'no_matching_recipe')
            objects.append({'detection': deepcopy(detection), 'status': 'available' if candidates else 'unavailable',
                            'reason': reason, 'candidates': candidates})
        available = sum(obj['status'] == 'available' for obj in objects)
        status = ('no_detections' if not objects else 'available' if available == len(objects) else
                  'partial' if available else 'unavailable')
        return {'schema_version': 1, 'status': status, 'image': deepcopy(detection_result['image']),
                'detection_model': deepcopy(detection_result['model']),
                'detection_inference_settings': deepcopy(detection_result.get('inference_settings', {})),
                'detection_limitations': deepcopy(detection_result.get('limitations', [])),
                'catalog': deepcopy(self._catalog), 'include_test_fixtures': self.include_test_fixtures,
                'objects': objects,
                'limitations': ['Recipe candidates are priors, not proof of actual ingredients or cooking methods.',
                                'Detection confidence and retrieval score are separate, uncalibrated values; neither is recipe truth probability.',
                                'No detections does not establish absence of food; unavailable does not establish absence of a recipe.',
                                'Source metadata is checked; external source authenticity is not independently verified by this adapter.',
                                'Repeated or overlapping objects must not be treated as independent portions automatically.',
                                'No portion, mass, nutrient or calorie estimate is produced.']}


def retrieve_recipes(detection_result, *, catalog_path=None, include_test_fixtures=False, limit=5):
    """Convenience API; retain RecipeRetriever for repeated catalog reuse."""
    return RecipeRetriever(catalog_path, include_test_fixtures=include_test_fixtures).retrieve(detection_result, limit=limit)
