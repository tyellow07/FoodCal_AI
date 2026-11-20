"""Memory-only uploaded-image adapter using the existing detector and retriever."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from hashlib import sha256
from io import BytesIO, StringIO

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from PIL.PngImagePlugin import PngImageFile
from PIL.JpegImagePlugin import JpegImageFile
from PIL.WebPImagePlugin import WebPImageFile
from src.detection import FoodDetector, ModelConfigurationError, DetectionInferenceError, InvalidImageError
from src.recipes import RecipeRetriever, InvalidDetectionResultError
from src.pipeline.diagnostic_demo import DEFAULT_FIXTURES, run_demo
from src.portion import diagnostic_photo_energy


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
            # Phone photos store rotation in EXIF; apply it before EXIF is discarded so the display,
            # the detector input and the drawn boxes all use the upright image the user took.
            image = ImageOps.exif_transpose(source).convert('RGB')
            image.info.clear()  # Do not carry user EXIF or filenames into the display object.
            return image
    except (OSError, SyntaxError) as error:
        raise ValueError('invalid_image_content') from error


def fixture_demo():
    result = run_demo()
    snapshot = result['detection_snapshot']
    image = Image.new('RGB', (snapshot['image']['width'], snapshot['image']['height']), '#f2eee5')
    # Bottom-left, so detection labels drawn at the top never hide this notice.
    ImageDraw.Draw(image).text((15, image.height - 30), 'SYNTHETIC CANVAS - not a food photograph', fill='#453d32',
                               font=ImageFont.load_default(size=18))
    return result, image


def analyze_upload(raw, *, enabled=False, fixture_recipes=False, detector_factory=FoodDetector):
    """No file upload save, CLI audit, GT, parsing or nutrient values; the portion contract only reports gaps."""
    result = {'diagnostic_version': 1, 'mode': 'yolo_upload', 'usage': 'diagnostic/test_only',
              'status': 'blocked', 'detection_kind': 'not_executed',
              'recipe_kind': 'synthetic_fixture' if fixture_recipes else 'unavailable',
              'detection_snapshot': None, 'retrieval_snapshot': None, 'objects': [], 'issues': [],
              'stages': {'detection': {'status': 'not_executed'}, 'retrieval': {'status': 'not_executed'},
                         'parsing': {'status': 'blocked', 'reason': 'trusted_recipe_text_unavailable'},
                         'mapping': {'status': 'blocked', 'reason': 'no_trusted_parsing'},
                         'nutrition': {'status': 'blocked', 'reason': 'nutrition_basis_unresolved'}},
              'photo_portion_grams': None, 'calorie_estimate': {'status': 'unavailable', 'value': None},
              'photo_energy_estimate': None,
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
        result['photo_energy_estimate'] = diagnostic_photo_energy(detection, classes_path=detector.classes_path)
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


def label_layout(draw, font, size, detections):
    """[(text, (left, top, right, bottom))] per detection; later labels move down instead of covering earlier ones."""
    width_limit, height_limit = size
    placed = []
    for prediction in detections:
        box = prediction['bounding_box']
        label = f"#{prediction['detection_id']} {prediction['class_name']} {prediction['confidence']:.2f}"
        bounds = draw.textbbox((0, 0), label, font=font, anchor='lt')
        width, height = bounds[2] + 8, bounds[3] + 8
        left = max(0, min(box['x_min'], width_limit - width))
        top = max(0, min(box['y_min'] - height, height_limit - height))
        for _ in range(len(placed) + 1):
            hits = [r for _, r in placed if left < r[2] and r[0] < left + width and top < r[3] and r[1] < top + height]
            if not hits:
                break
            top = min(max(r[3] for r in hits), height_limit - height)
        placed.append((label, (left, top, left + width, top + height)))
    return placed


def draw_predictions(image, result, card_box=None, manual_items=None):
    """Draw Detection predictions plus the user's card box and hand-added foods. GT fields are deliberately ignored."""
    canvas = image.copy()
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=max(16, min(32, image.width // 40)))
    snapshot = result.get('detection_snapshot') or {}
    if snapshot.get('image', {}).get('width') != image.width or snapshot.get('image', {}).get('height') != image.height:
        raise ValueError('display_coordinate_mismatch')
    detections = snapshot.get('detections', [])
    for prediction in detections:
        box = prediction['bounding_box']
        draw.rectangle(tuple(box[k] for k in ('x_min', 'y_min', 'x_max', 'y_max')), outline='#e36c25', width=3)
    for label, (left, top, right, bottom) in label_layout(draw, font, image.size, detections):
        draw.rectangle((left, top, right - 1, bottom - 1), fill='#171717')
        draw.text((left + 4, top + 4), label, font=font, anchor='lt', fill='white')
    big = ImageFont.load_default(size=max(16, image.width // 45))  # readable after the page scales the photo down
    for item in manual_items or []:
        box = item['bounding_box']
        draw.rectangle(tuple(box[k] for k in ('x_min', 'y_min', 'x_max', 'y_max')), outline='#2b6cb0',
                       width=max(3, image.width // 300))
        left, top = box['x_min'] + 4, box['y_min'] + 4
        x0, y0, x1, y1 = draw.textbbox((left, top), item['detection_id'], font=big, anchor='lt')
        draw.rectangle((x0 - 4, y0 - 4, x1 + 4, y1 + 4), fill='#2b6cb0')
        draw.text((left, top), item['detection_id'], font=big, anchor='lt', fill='white')
    if card_box is not None:
        if isinstance(card_box[0], (list, tuple)):  # detected outline TL, TR, BR, BL
            points = [tuple(p) for p in card_box]
        else:
            x0, y0, x1, y1 = card_box
            points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        draw.line(points + points[:1], fill='#1f9d55', width=max(3, image.width // 300), joint='curve')
        draw.text((points[0][0] + 4, points[0][1] + 4), 'CARD', font=font, anchor='lt', fill='#1f9d55')
    return canvas


CJK_FONTS = ('/System/Library/Fonts/STHeiti Medium.ttc', '/System/Library/Fonts/Hiragino Sans GB.ttc',
             'C:/Windows/Fonts/msjh.ttc', 'C:/Windows/Fonts/msyh.ttc',
             '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc')
PILLS = (('#dcebd3', '#5f8f52'), ('#fbe7c2', '#c58b2b'), ('#d6e6f5', '#4f7fb0'),
         ('#f6d9d6', '#b5615a'), ('#e6def3', '#7a64a8'), ('#d9eeeb', '#3f8f86'))


def label_font(size):
    """A font with Chinese glyphs when the computer has one (macOS, Windows, Noto on Linux), else Pillow's default."""
    from pathlib import Path
    for path in CJK_FONTS:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size), True
            except OSError:
                continue
    return ImageFont.load_default(size=size), False


def draw_items(image, items, card=None, show_boxes=True):
    """Recognised foods as pastel boxes with numbered name tags ("01 紫米飯"), plus the card outline in green."""
    canvas = image.convert('RGB').copy()
    draw = ImageDraw.Draw(canvas)
    # The page shows the photo about 300-600 px wide, so tags are sized relative to the photo, not in pixels.
    font, has_cjk = label_font(max(18, image.width // 24))
    line = max(3, image.width // 200)
    if card is not None:
        if isinstance(card[0], (list, tuple)):
            points = [tuple(p) for p in card]
        else:
            x0, y0, x1, y1 = card
            points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        draw.line(points + points[:1], fill='#3d6b4f', width=line, joint='curve')
    if not show_boxes:
        return canvas
    for index, item in enumerate(items, 1):
        fill, edge = PILLS[(index - 1) % len(PILLS)]
        box = item['bounding_box']
        x0, y0, x1, y1 = (box[k] for k in ('x_min', 'y_min', 'x_max', 'y_max'))
        draw.rounded_rectangle((x0, y0, x1, y1), radius=max(6, line * 3), outline=edge, width=line)
        name = item.get('display_name') if has_cjk else None
        text = f'{index:02d} {name}' if name else f'{index:02d} {item["detection_id"]}'
        left, top = x0 + line, max(0, y0 - font.size - 10)
        tx0, ty0, tx1, ty1 = draw.textbbox((left + 8, top + 4), text, font=font, anchor='lt')
        draw.rounded_rectangle((tx0 - 8, ty0 - 5, tx1 + 8, ty1 + 6), radius=8, fill=fill, outline=edge, width=max(1, line // 2))
        draw.text((left + 8, top + 4), text, font=font, anchor='lt', fill='#1f2a24')
    return canvas
