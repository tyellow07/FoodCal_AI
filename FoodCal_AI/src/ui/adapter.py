"""Memory-only uploaded-image adapter using the existing detector and retriever."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from hashlib import sha256
from io import BytesIO, StringIO

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from PIL.PngImagePlugin import PngImageFile
from PIL.JpegImagePlugin import JpegImageFile
from PIL.WebPImagePlugin import WebPImageFile
from src.detection import FoodDetector, ModelConfigurationError, DetectionInferenceError, InvalidImageError
from src.recipes import RecipeRetriever, InvalidDetectionResultError
from src.pipeline.diagnostic_demo import DEFAULT_FIXTURES, run_demo


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 20_000_000


def decode_upload(raw):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError('invalid_upload_size')
    # Avoid Ultralytics' global Image.open HEIF fallback/automatic dependency installer.
    decoder = (PngImageFile if raw.startswith(b'\x89PNG\r\n\x1a\n') else
               JpegImageFile if raw.startswith(b'\xff\xd8\xff') else
               WebPImageFile if raw.startswith(b'RIFF') and raw[8:12] == b'WEBP' else None)
    if decoder is None:
        raise ValueError('unsupported_image_signature')
    try:
        with decoder(BytesIO(raw)) as source:
            if source.width * source.height > MAX_PIXELS:
                raise ValueError('unsupported_or_large_image')
            source.load()
            image = source.convert('RGB')
            image.info.clear()  # Do not carry user EXIF or filenames into the display object.
            return image
    except (OSError, SyntaxError) as error:
        raise ValueError('invalid_image_content') from error


def fixture_demo():
    result = run_demo()
    snapshot = result['detection_snapshot']
    image = Image.new('RGB', (snapshot['image']['width'], snapshot['image']['height']), '#f2eee5')
    ImageDraw.Draw(image).text((15, 10), 'SYNTHETIC CANVAS - not a food photograph', fill='#453d32')
    return result, image


def analyze_upload(raw, *, enabled=False, fixture_recipes=False, detector_factory=FoodDetector):
    """No file upload save, CLI audit, GT, parsing, portion or nutrient computation."""
    result = {'diagnostic_version': 1, 'mode': 'yolo_upload', 'usage': 'diagnostic/test_only',
              'status': 'blocked', 'detection_kind': 'not_executed',
              'recipe_kind': 'synthetic_fixture' if fixture_recipes else 'unavailable',
              'detection_snapshot': None, 'retrieval_snapshot': None, 'objects': [], 'issues': [],
              'stages': {'detection': {'status': 'not_executed'}, 'retrieval': {'status': 'not_executed'},
                         'parsing': {'status': 'blocked', 'reason': 'trusted_recipe_text_unavailable'},
                         'mapping': {'status': 'blocked', 'reason': 'no_trusted_parsing'},
                         'nutrition': {'status': 'blocked', 'reason': 'nutrition_basis_unresolved'}},
              'photo_portion_grams': None, 'calorie_estimate': {'status': 'unavailable', 'value': None},
              'review': {'status': 'not_requested'}, 'trust': {'nutrition_eligible': False,
                                                            'source_authentication': 'not_performed'}}
    if not enabled:
        result['issues'] = [{'code': 'explicit_start_required', 'message': 'Choose an image and explicitly start inference'}]
        return result, None
    try:
        image = decode_upload(raw)
        # Backend messages stay in memory and are discarded; no private path/error text is exposed.
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            detector = detector_factory()
            detected = detector.detect_food(image)
        retriever = RecipeRetriever(DEFAULT_FIXTURES/'catalog.json' if fixture_recipes else None,
                                    include_test_fixtures=fixture_recipes, classes_path=detector.classes_path)
        retrieved = retriever.retrieve(detected)
        detection, retrieval = deepcopy(detected), deepcopy(retrieved)
        reference = 'memory://upload/' + sha256(raw).hexdigest()
        detection['image']['source'] = retrieval['image']['source'] = reference
        digest = detection['model'].get('sha256')
        detection['model']['checkpoint'] = retrieval['detection_model']['checkpoint'] = 'local://configured-model/' + (digest or 'unverified')
        if retrieval['catalog']['path']:
            retrieval['catalog']['path'] = 'fixture://diagnostic-v0/catalog.json'
        result.update(status='partial' if detection['detections'] else 'no_detections',
                      detection_kind='true_model_detection', detection_snapshot=detection, retrieval_snapshot=retrieval,
                      objects=[{'detection_id': o['detection']['detection_id'], 'detection': o['detection'],
                                'retrieval_status': o['status'], 'reason': o['reason'],
                                'recipe_candidates': [{'recipe': c, 'kind': 'synthetic_fixture', 'status': 'blocked',
                                                       'reason': 'trusted_recipe_text_unavailable',
                                                       'parsing_snapshot': None, 'mapping_snapshot': None}
                                                      for c in o['candidates']]} for o in retrieval['objects']])
        result['stages']['detection'] = {'status': 'executed', 'kind': 'true_model_detection'}
        result['stages']['retrieval'] = {'status': 'executed', 'result_status': retrieval['status']}
        return result, image
    except FileNotFoundError:
        code = 'local_model_or_taxonomy_missing'
    except ModelConfigurationError:
        code = 'model_taxonomy_mismatch'
    except (UnidentifiedImageError, Image.DecompressionBombError, ValueError, OSError):
        code = 'invalid_upload_or_configuration'
    except (InvalidImageError, DetectionInferenceError, InvalidDetectionResultError):
        code = 'detection_or_retrieval_failed'
    result['issues'] = [{'code': code, 'message': 'Analysis unavailable; no fallback or invented data'}]
    return result, None


def draw_predictions(image, result):
    """Draw only Detection predictions. GT/evaluation fields are deliberately ignored."""
    canvas = image.copy()
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=max(16, min(32, image.width // 40)))
    snapshot = result.get('detection_snapshot') or {}
    if snapshot.get('image', {}).get('width') != image.width or snapshot.get('image', {}).get('height') != image.height:
        raise ValueError('display_coordinate_mismatch')
    for prediction in snapshot.get('detections', []):
        box = prediction['bounding_box']
        rect = tuple(box[k] for k in ('x_min', 'y_min', 'x_max', 'y_max'))
        draw.rectangle(rect, outline='#e36c25', width=3)
        label = f"{prediction['detection_id']}: {prediction['class_name']} {prediction['confidence']:.3f}"
        bounds = draw.textbbox((0, 0), label, font=font, anchor='lt')
        width, height = bounds[2] + 8, bounds[3] + 8
        left = max(0, min(rect[0], image.width - width))
        top = max(0, min(rect[1] - height, image.height - height))
        draw.rectangle((left, top, left + width - 1, top + height - 1), fill='#171717')
        draw.text((left + 4, top + 4), label, font=font, anchor='lt', fill='white')
    return canvas
