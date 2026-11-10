"""Opt-in single validation image detection, with private audit and no recipe inference."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from hashlib import sha256
import importlib.metadata
import json
from pathlib import Path
import time
from uuid import uuid4

from src.detection import FoodDetector, ModelConfigurationError, DetectionInferenceError, InvalidImageError
from src.detection.model import DEFAULT_MODEL_PATH, DEFAULT_CLASSES_PATH
from src.recipes import RecipeRetriever, InvalidDetectionResultError


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUDIT_ROOT = ROOT / 'runs/detect/diagnostic_mvp_yolo'


def _library_version(name):
    """Installed distribution version, or None when package metadata is unavailable; never guessed.

    PackageNotFoundError is an ImportError, which the diagnostic's handlers do not catch: letting it escape
    skipped result.json and the protected-resource hash check after a real inference.
    """
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _hash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _iou(a, b):
    overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - overlap
    return overlap/union if union else 0.0


def run_yolo_diagnostic(image, approval_record, *, fixture_recipes=False, model_path=DEFAULT_MODEL_PATH,
                        classes_path=DEFAULT_CLASSES_PATH, audit_root=DEFAULT_AUDIT_ROOT):
    """Approval file is explicit caller authorization, not authentication or GT approval.

    One detect_food call. No fallback/retry. No model download or parser invocation.
    """
    result = {'diagnostic_version': 1, 'mode': 'yolo_validation', 'status': 'failed',
              'usage': 'diagnostic/test_only', 'detection_kind': 'true_model_detection',
              'recipe_kind': 'synthetic_fixture' if fixture_recipes else 'unavailable',
              'detection_snapshot': None, 'retrieval_snapshot': None, 'objects': [], 'issues': [],
              'stages': {'detection': {'status': 'not_executed'}, 'retrieval': {'status': 'not_executed'},
                         'parsing': {'status': 'blocked', 'reason': 'trusted_recipe_text_unavailable'},
                         'mapping': {'status': 'blocked', 'reason': 'no_trusted_parsing'},
                         'nutrition': {'status': 'blocked', 'reason': 'nutrition_basis_unresolved'}},
              'trust': {'nutrition_eligible': False, 'source_authentication': 'not_performed'},
              'review': {'status': 'not_requested'}, 'photo_portion_grams': None,
              'calorie_estimate': {'status': 'unavailable', 'value': None,
                                   'reasons': ['recipe_is_prior', 'photo_mass_unknown', 'nutrition_basis_unresolved']},
              'evaluation': None, 'private_audit_id': None}
    audit = Path(audit_root) / uuid4().hex
    audit.mkdir(parents=True, exist_ok=False)
    result['private_audit_id'] = audit.name
    code = 'preflight_invalid'
    protected = {}
    start = time.monotonic()
    try:
        import yaml
        image, model, classes = Path(image).resolve(), Path(model_path).resolve(), Path(classes_path).resolve()
        if not model.is_file():
            code = 'model_missing';raise ValueError(code)
        if not classes.is_file():
            code = 'taxonomy_missing';raise ValueError(code)
        if not image.is_file():
            code = 'image_missing';raise ValueError(code)
        names = yaml.safe_load(classes.read_text(encoding='utf-8'))
        taxonomy = yaml.safe_load((ROOT/'examples/diagnostic_v0/classes.yaml').read_text(encoding='utf-8'))['names']
        if names.get('names') != taxonomy:
            code = 'taxonomy_mismatch';raise ValueError(code)
        dataset_root = Path(names['path'])
        if not dataset_root.is_absolute():dataset_root = ROOT / dataset_root
        valid_root = (dataset_root / names['val']).resolve()
        if not image.is_relative_to(valid_root):
            code = 'not_validation_image';raise ValueError(code)
        approval = _json(approval_record)
        if (approval.get('status') != 'approved_for_single_validation_inference'
                or approval.get('split') != 'validation' or approval.get('image_file') != image.name
                or approval.get('image_sha256') != _hash(image) or not approval.get('authorization_reference')):
            code = 'image_not_approved';raise ValueError(code)
        label = valid_root.parent/'labels'/image.with_suffix('.txt').name
        if not label.is_file():
            code = 'gt_missing';raise ValueError(code)
        protected = {p: _hash(p) for p in (image, model, classes, label, Path(approval_record))}
        _write(audit/'request.json', {'image': str(image), 'model': str(model), 'classes': str(classes),
                                    'approval': approval, 'hashes': {str(k): v for k, v in protected.items()},
                                    'fixture_recipes': fixture_recipes})
        code = 'detection_failed'
        with (audit/'backend.log').open('x', encoding='utf-8') as log, redirect_stdout(log), redirect_stderr(log):
            detector = FoodDetector(model_path=model, classes_path=classes)
            detection = detector.detect_food(image)  # Exactly one model inference request.
        elapsed = time.monotonic() - start
        _write(audit/'detection_raw.json', detection)
        # Retrieval independently checks class IDs, labels, confidence and image-space boxes.
        code = 'invalid_detection_result'
        catalog = ROOT/'examples/diagnostic_v0/catalog.json' if fixture_recipes else None
        retriever = RecipeRetriever(catalog, include_test_fixtures=fixture_recipes, classes_path=classes)
        retrieval = retriever.retrieve(detection)
        _write(audit/'retrieval_raw.json', retrieval)
        # GT is used only after inference, never supplied to the detector or used to alter predictions.
        from src.detection.export_validation_review import read_gt
        gt = read_gt(label, detection['image']['width'], detection['image']['height'])
        pairs = []
        for index, item in enumerate(gt):
            nearby = []
            for pred in detection['detections']:
                box = pred['bounding_box']
                iou = _iou(item[:4], [box[k] for k in ('x_min', 'y_min', 'x_max', 'y_max')])
                nearby.append({'detection_id': pred['detection_id'], 'class_id': pred['class_id'],
                               'confidence': pred['confidence'], 'iou': iou,
                               'correct_class_and_iou_0_5': pred['class_id'] == item[4] and iou >= 0.5})
            pairs.append({'gt_index': index, 'class_id': item[4], 'class_name': taxonomy[item[4]],
                          'box': item[:4], 'predictions': nearby})
        evaluation = {'scope': 'single_validation_image_diagnostic', 'iou_threshold': 0.5,
                      'matching_rule': 'all GT/Pred pairs; same class and IoU >= 0.5, not one-to-one metrics',
                      'gt': pairs}
        _write(audit/'gt_comparison.json', evaluation)
        projected_detection, projected_retrieval = deepcopy(detection), deepcopy(retrieval)
        for target in (projected_detection, projected_retrieval):
            target['image']['source'] = 'private://validation-image/' + protected[image]
        projected_detection['model']['checkpoint'] = 'private://model/' + protected[model]
        projected_retrieval['detection_model']['checkpoint'] = 'private://model/' + protected[model]
        if projected_retrieval['catalog']['path']:
            projected_retrieval['catalog']['path'] = 'fixture://diagnostic-v0/catalog.json'
        result.update(status='partial' if detection['detections'] else 'no_detections',
                      detection_snapshot=projected_detection, retrieval_snapshot=projected_retrieval,
                      objects=[{'detection_id': o['detection']['detection_id'], 'detection': o['detection'],
                                'retrieval_status': o['status'], 'reason': o['reason'],
                                'recipe_candidates': [{'recipe': c, 'kind': 'synthetic_fixture',
                                                       'parsing_snapshot': None, 'mapping_snapshot': None,
                                                       'status': 'blocked', 'reason': 'trusted_recipe_text_unavailable'}
                                                      for c in o['candidates']]} for o in projected_retrieval['objects']],
                      evaluation=evaluation, image_sha256=protected[image], model_sha256=protected[model],
                      classes_sha256=protected[classes], elapsed_seconds=elapsed,
                      library_version=_library_version('ultralytics'))
        result['stages']['detection'] = {'status': 'executed', 'kind': 'true_model_detection'}
        result['stages']['retrieval'] = {'status': 'executed', 'kind': result['recipe_kind'], 'result_status': retrieval['status']}
    except ModelConfigurationError:
        code = 'model_taxonomy_mismatch'
    except InvalidDetectionResultError:
        code = 'invalid_detection_result'
    except yaml.YAMLError:
        code = 'taxonomy_invalid'
    except (OSError, ValueError, KeyError, TypeError, InvalidImageError, DetectionInferenceError):
        pass
    if any(_hash(p) != digest for p, digest in protected.items()):
        result.update(status='failed', detection_snapshot=None, retrieval_snapshot=None, objects=[], evaluation=None)
        code = 'protected_resource_changed'
    if result['status'] == 'failed':
        result['issues'] = [{'code': code, 'message': 'Single-image diagnostic stopped; no retry or fallback'}]
    _write(audit/'result.json', result)
    return result
