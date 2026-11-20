"""Food recognition with Claude vision (claude-opus-5-5); the local YOLO detector stays as the offline option.

One request per photo: a downscaled, metadata-free JPEG plus instructions; the answer is constrained to a JSON
schema (output_config.format) listing each visible food with a Traditional Chinese name, a known food key when it
matches, the container (plate or bowl), an approximate box on a 0-1000 grid, visible ingredients and a confidence.
Claude is asked only what is visible: no weights, calories or recipe guesses, so recipe priors never enter as
visual evidence. Boxes are approximate; the page lets people exclude or add dishes by hand.

Server-side refusal fallbacks are on (fallbacks="default"); a refusal or malformed answer raises
RecognitionError with a readable code. Nothing is written to disk.
"""
from base64 import standard_b64encode
from io import BytesIO
import json
import re
import time

from src.portion.rough_volume import FOOD_TEMPLATES

MODEL = 'claude-opus-5-5'
MAX_SIDE = 1568
GRID = 1000
FALLBACK_BETA = 'server-side-fallback-2026-07-01'
CONFIDENCE = ('high', 'medium', 'low')
CONTAINERS = ('plate', 'bowl')

SCHEMA = {
    'type': 'object',
    'properties': {
        'foods': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {
                'name_zh': {'type': 'string'},
                'name_en': {'type': 'string'},
                'food_key': {'type': 'string', 'enum': sorted(FOOD_TEMPLATES) + ['other']},
                'container': {'type': 'string', 'enum': list(CONTAINERS)},
                'box': {'type': 'object', 'properties': {k: {'type': 'integer'} for k in ('x_min', 'y_min', 'x_max', 'y_max')},
                        'required': ['x_min', 'y_min', 'x_max', 'y_max'], 'additionalProperties': False},
                'visible_ingredients': {'type': 'array', 'items': {'type': 'string'}},
                'confidence': {'type': 'string', 'enum': list(CONFIDENCE)},
            },
            'required': ['name_zh', 'name_en', 'food_key', 'container', 'box', 'visible_ingredients', 'confidence'],
            'additionalProperties': False}},
        'notes': {'type': 'string'},
    },
    'required': ['foods', 'notes'],
    'additionalProperties': False,
}

SYSTEM = f"""You identify the foods visible in one photo of a meal from a Taiwanese university cafeteria.

For every distinct food item you can see, report:
- name_zh: its usual name in Traditional Chinese (for example 白飯, 滷肉飯, 燙青菜, 荷包蛋).
- name_en: a short English name.
- food_key: one of these keys when the item is that food, otherwise "other":
{chr(10).join(f'  {key}' for key in sorted(FOOD_TEMPLATES))}
  (cooked_rice_region is plain cooked rice, cooked_noodle_region plain noodles, visible_fried_egg a fried egg.)
- container: "bowl" when the food is served in a bowl (soups, noodle soups, rice bowls), otherwise "plate".
- box: the tightest box around the food on a {GRID} x {GRID} grid over the whole image (x to the right, y down,
  0 to {GRID}); for bowl dishes, box the bowl opening.
- visible_ingredients: only ingredients you can actually see.
- confidence: high, medium or low.

Report only what is visible in the photo. Do not estimate weights, portions or calories, and do not add ingredients
a recipe would usually contain but the photo does not show. Ignore plates, trays, cutlery, hands, cards and other
non-food objects. Put a whole mixed dish (for example a rice bowl with toppings) in one item unless its parts sit
apart. If there is no food, return an empty list. Use notes for anything uncertain, in Traditional Chinese."""


class RecognitionError(Exception):
    """Recognition could not produce a usable answer; str(error) is a short code."""


def jpeg_bytes(image):
    """Downscaled JPEG of the pixels only: no EXIF (GPS included) or other metadata is carried over."""
    picture = image.convert('RGB')
    picture.thumbnail((MAX_SIDE, MAX_SIDE))
    buffer = BytesIO()
    picture.save(buffer, format='JPEG', quality=88)
    return buffer.getvalue()


def _jpeg(image):
    return standard_b64encode(jpeg_bytes(image)).decode('ascii')


def _snake(text):
    slug = re.sub(r'[^a-z0-9]+', '_', str(text).lower()).strip('_')
    return slug[:48] or 'food'


def _clamp(value):
    return max(0, min(GRID, int(value)))


def _composition(food):
    """Optional components [(ingredient key, share percent)] and cooking method from a vision answer."""
    out = {}
    parts = [(c.get('ingredient'), c.get('share_percent')) for c in food.get('components') or [] if isinstance(c, dict)]
    parts = [(k, int(v)) for k, v in parts if isinstance(k, str) and isinstance(v, (int, float)) and 0 < v <= 100]
    if parts:
        out['components'] = parts
    if isinstance(food.get('cooking_method'), str):
        out['cooking_method'] = food['cooking_method']
    return out


def items_from_answer(answer, width, height, prefix='C'):
    """Schema answer -> page items (detection_id C1.., class_name, display_name, pixel box, container, ...)."""
    items = []
    for index, food in enumerate(answer.get('foods', []), 1):
        box = food['box']
        x0, x1 = sorted((_clamp(box['x_min']), _clamp(box['x_max'])))
        y0, y1 = sorted((_clamp(box['y_min']), _clamp(box['y_max'])))
        if x1 - x0 < 5 or y1 - y0 < 5:  # degenerate box: keep the name, let the user draw the box
            continue
        known = food['food_key'] in FOOD_TEMPLATES
        items.append({
            'detection_id': f'{prefix}{index}',
            'class_name': food['food_key'] if known else _snake(food['name_en']),
            'display_name': food['name_zh'] or food['name_en'],
            'container': food['container'] if food['container'] in CONTAINERS else 'plate',
            'confidence': food['confidence'] if food['confidence'] in CONFIDENCE else 'low',
            'visible_ingredients': list(food.get('visible_ingredients') or []),
            **_composition(food),
            'bounding_box': {'x_min': round(x0 * width / GRID, 1), 'y_min': round(y0 * height / GRID, 1),
                             'x_max': round(x1 * width / GRID, 1), 'y_max': round(y1 * height / GRID, 1)},
        })
    return items


def make_client(api_key=None):
    """Anthropic client from an explicit key, ANTHROPIC_API_KEY, or an `ant auth login` profile."""
    import anthropic
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def recognize_foods(image, *, client, effort='medium'):
    """{'items', 'notes', 'model', 'usage', 'latency_s', 'request_id'} for one PIL image."""
    import anthropic
    width, height = image.size
    started = time.perf_counter()
    try:
        response = client.beta.messages.create(
            model=MODEL, max_tokens=16000, system=SYSTEM,
            betas=[FALLBACK_BETA], fallbacks='default',
            output_config={'effort': effort, 'format': {'type': 'json_schema', 'schema': SCHEMA}},
            messages=[{'role': 'user', 'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': _jpeg(image)}},
                {'type': 'text', 'text': 'List the foods visible in this photo.'}]}])
    except anthropic.CredentialsError:
        raise RecognitionError('claude_credentials_missing') from None
    except anthropic.AuthenticationError:
        raise RecognitionError('claude_authentication_failed') from None
    except anthropic.PermissionDeniedError:
        raise RecognitionError('claude_permission_denied') from None
    except anthropic.RateLimitError:
        raise RecognitionError('claude_rate_limited') from None
    except anthropic.BadRequestError as error:
        message = str(getattr(error, 'message', error)).lower()
        raise RecognitionError('claude_credit_or_billing' if 'credit' in message or 'billing' in message
                               else 'claude_bad_request') from None
    except anthropic.APIStatusError:
        raise RecognitionError('claude_service_error') from None
    except anthropic.APIConnectionError:
        raise RecognitionError('claude_network_error') from None
    except TypeError as error:  # the SDK raises this when no credential source resolves
        if 'api_key' in str(error) or 'auth' in str(error).lower():
            raise RecognitionError('claude_credentials_missing') from None
        raise
    latency = time.perf_counter() - started
    if response.stop_reason == 'refusal':
        raise RecognitionError('claude_refused')
    if response.stop_reason == 'max_tokens':
        raise RecognitionError('claude_answer_truncated')
    text = next((block.text for block in response.content if block.type == 'text'), None)
    try:
        answer = json.loads(text)
    except (TypeError, ValueError):
        raise RecognitionError('claude_answer_unreadable') from None
    usage = getattr(response, 'usage', None)
    return {'items': items_from_answer(answer, width, height), 'notes': answer.get('notes', ''),
            'model': getattr(response, 'model', MODEL),
            'usage': {'input_tokens': getattr(usage, 'input_tokens', None),
                      'output_tokens': getattr(usage, 'output_tokens', None)},
            'latency_s': round(latency, 2), 'request_id': getattr(response, '_request_id', None)}
