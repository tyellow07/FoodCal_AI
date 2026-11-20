"""Food recognition with Google's Gemini API (free tier), the web page's recognition engine since 2026-11-20.

One request per photo: a downscaled, metadata-free JPEG with the student card painted over (the page finds the
card first), plus instructions; the answer is constrained to a JSON schema listing each visible food with a
Traditional Chinese name, a known food key when it matches, the container, a box in Gemini's native
[ymin, xmin, ymax, xmax] 0-1000 format, visible ingredients and a confidence. Only what is visible is asked for:
no weights, calories or recipe guesses.

Free-tier terms let Google use submitted content to improve its products, with human review; the page says so
before the user agrees. Keys come from the caller (Streamlit secrets), never from this module or the repo.
"""
from hashlib import sha256
import json
from pathlib import Path
import re
import time

from PIL import ImageDraw

from src.portion.rough_volume import FOOD_TEMPLATES
from .claude_vision import CONFIDENCE, CONTAINERS, GRID, RecognitionError, items_from_answer, jpeg_bytes

# Free-tier quotas are per model (2026-11-20, AI Studio): Flash models 5 requests/minute and 20/day each, Flash Lite
# 15/minute and 500/day. On 2026-11-20 the Flash models often answered 503/504 on the free tier while Flash Lite
# answered in seconds, so the models the key can use are listed once and tried Flash Lite first (newest first), then
# Flash; the model that last answered goes first, remembered across restarts in the ignored data/ folder.
MODELS = ('gemini-3.5-flash-lite', 'gemini-3.1-flash-lite')  # used only when the model list cannot be read
STATE_PATH = Path(__file__).resolve().parents[2] / 'data/gemini_state.json'
PER_MODEL_TIMEOUT_MS = 30_000
TOTAL_BUDGET_S = 75
_FLASH = re.compile(r'gemini-(\d+(?:\.\d+)?)-flash')
_LITE = re.compile(r'gemini-(\d+(?:\.\d+)?)-flash-lite')
_ORDER_CACHE = {}
_LAST_GOOD = {}  # endpoint cache key -> model that answered last time; tried first next time

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
                'box_2d': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 4, 'maxItems': 4},
                'visible_ingredients': {'type': 'array', 'items': {'type': 'string'}},
                'confidence': {'type': 'string', 'enum': list(CONFIDENCE)},
            },
            'required': ['name_zh', 'name_en', 'food_key', 'container', 'box_2d', 'visible_ingredients', 'confidence']}},
        'notes': {'type': 'string'},
    },
    'required': ['foods', 'notes'],
}

INSTRUCTIONS = f"""You identify the foods visible in one photo of a meal from a Taiwanese university cafeteria.

For every distinct food item you can see, report:
- name_zh: its usual name in Traditional Chinese (for example 白飯, 滷肉飯, 燙青菜, 荷包蛋).
- name_en: a short English name.
- food_key: one of these keys when the item is that food, otherwise "other":
{chr(10).join(f'  {key}' for key in sorted(FOOD_TEMPLATES))}
  (cooked_rice_region is plain cooked rice, cooked_noodle_region plain noodles, visible_fried_egg a fried egg.)
- container: "bowl" when the food is served in a bowl (soups, noodle soups, rice bowls), otherwise "plate".
- box_2d: [ymin, xmin, ymax, xmax] of the tightest box around the food, normalised to 0-{GRID}; for bowl dishes,
  box the bowl opening.
- visible_ingredients: only ingredients you can actually see.
- confidence: high, medium or low.

Report only what is visible in the photo. Do not estimate weights, portions or calories, and do not add ingredients
a recipe would usually contain but the photo does not show. Ignore plates, trays, cutlery, hands, the grey
painted-over area and other non-food objects. Put a whole mixed dish (for example a rice bowl with toppings) in one
item unless its parts sit apart. If there is no food, return an empty list. Use notes, in Traditional Chinese, for
anything uncertain."""

BLOCKED = {'SAFETY', 'BLOCKLIST', 'PROHIBITED_CONTENT', 'SPII', 'IMAGE_SAFETY', 'RECITATION', 'LANGUAGE', 'OTHER'}


def mask_card(image, quad):
    """Copy of the photo with the card outline filled grey, so its number never leaves the computer."""
    masked = image.convert('RGB').copy()
    ImageDraw.Draw(masked).polygon([tuple(point) for point in quad], fill=(128, 128, 128))
    return masked


def gemini_clients(api_key):
    """[(endpoint label, client)] for a Google AI Studio key (Gemini API)."""
    from google import genai
    from google.genai import types
    # One attempt per model: the page moves on to the next model itself instead of waiting on SDK retries.
    options = types.HttpOptions(timeout=PER_MODEL_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1))
    return [('gemini_api', genai.Client(api_key=api_key, http_options=options))]


def _thinking(model, types):
    """Short thinking: recognition is perception, and the free tier is slow. Gemini 2.x takes a budget instead."""
    if model.startswith('gemini-2.'):
        return types.ThinkingConfig(thinking_budget=0)
    return types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)


def model_order(names):
    """Generate-capable model names -> Flash Lite models newest first, then Flash models newest first."""
    names = [n.split('/')[-1] for n in names]
    version = lambda name, pattern: float(pattern.fullmatch(name).group(1))
    flash = sorted((n for n in names if _FLASH.fullmatch(n)), key=lambda n: version(n, _FLASH), reverse=True)
    lite = sorted((n for n in names if _LITE.fullmatch(n)), key=lambda n: version(n, _LITE), reverse=True)
    return tuple(lite + flash) or MODELS


def _cache_key(endpoint, client):
    key = getattr(getattr(client, '_api_client', None), 'api_key', None) or repr(client)
    return endpoint, sha256(str(key).encode()).hexdigest()


def _remembered():
    try:
        return json.loads(STATE_PATH.read_text(encoding='utf-8')).get('last_good_model')
    except (OSError, ValueError, AttributeError):
        return None


def _remember(model):
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({'last_good_model': model}) + '\n', encoding='utf-8')
    except OSError:
        pass  # remembering is a speed-up only


def models_for(endpoint, client):
    """Model order for this client, listed once per key and cached; the last model that answered goes first."""
    cache_key = _cache_key(endpoint, client)
    if cache_key not in _ORDER_CACHE:
        try:
            listed = [m.name for m in client.models.list()
                      if not getattr(m, 'supported_actions', None) or 'generateContent' in m.supported_actions]
        except Exception:  # listing is a convenience; fall back to known names
            listed = []
        _ORDER_CACHE[cache_key] = model_order(listed)
    order, good = _ORDER_CACHE[cache_key], _LAST_GOOD.get(cache_key) or _remembered()
    return (good,) + tuple(m for m in order if m != good) if good in order else order


def _answer_from(response):
    feedback = getattr(response, 'prompt_feedback', None)
    if feedback is not None and getattr(feedback, 'block_reason', None):
        raise RecognitionError('gemini_blocked')
    candidates = getattr(response, 'candidates', None) or []
    if not candidates:
        raise RecognitionError('gemini_answer_unreadable')
    reason = getattr(candidates[0].finish_reason, 'name', str(candidates[0].finish_reason or ''))
    if reason == 'MAX_TOKENS':
        raise RecognitionError('gemini_answer_truncated')
    if reason in BLOCKED:
        raise RecognitionError('gemini_blocked')
    try:
        answer = json.loads(response.text)
    except (TypeError, ValueError):
        raise RecognitionError('gemini_answer_unreadable') from None
    if not isinstance(answer, dict) or not isinstance(answer.get('foods'), list):
        raise RecognitionError('gemini_answer_unreadable')
    foods = []
    for food in answer['foods']:
        box = food.get('box_2d') if isinstance(food, dict) else None
        if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
            continue
        y0, x0, y1, x1 = box
        foods.append({**food, 'box': {'x_min': x0, 'y_min': y0, 'x_max': x1, 'y_max': y1}})
    return {'foods': foods, 'notes': answer.get('notes', '')}


def recognize_foods_gemini(image, *, clients, models=None):
    """{'items', 'notes', 'model', 'endpoint', 'usage', 'latency_s'} for one PIL image (already card-masked)."""
    from google.genai import errors, types
    import httpx
    width, height = image.size
    contents = [types.Part.from_bytes(data=jpeg_bytes(image), mime_type='image/jpeg'),
                'List the foods visible in this photo.']

    last = 'gemini_key_invalid'
    deadline = time.perf_counter() + TOTAL_BUDGET_S
    for endpoint, client in clients:
        for model in models or models_for(endpoint, client):
            if time.perf_counter() > deadline:
                raise RecognitionError('gemini_too_slow')
            started = time.perf_counter()
            config = types.GenerateContentConfig(system_instruction=INSTRUCTIONS, response_mime_type='application/json',
                                                 response_json_schema=SCHEMA, thinking_config=_thinking(model, types))
            try:
                response = client.models.generate_content(model=model, contents=contents, config=config)
            except errors.ClientError as error:
                code, text = getattr(error, 'code', None), str(error).lower()
                if code == 404 or 'not found' in text:
                    last = 'gemini_model_unavailable'
                    continue  # try the next model name
                if code == 429 or 'resource_exhausted' in text or 'quota' in text:
                    last = 'gemini_quota_exceeded'
                    continue  # quotas are per model: try the next one
                if code in (400, 401, 403) and ('api key' in text or 'api_key' in text or 'permission' in text
                                                or 'unauthenticated' in text or code in (401, 403)):
                    last = 'gemini_key_invalid'
                    break  # this endpoint does not accept the key: try the next endpoint
                raise RecognitionError('gemini_bad_request') from None
            except errors.ServerError:
                last = 'gemini_service_error'
                continue  # overloaded or failing model: try the next one
            except httpx.HTTPError:
                raise RecognitionError('gemini_network_error') from None
            latency = time.perf_counter() - started
            answer = _answer_from(response)
            if models is None:
                _LAST_GOOD[_cache_key(endpoint, client)] = model
                _remember(model)
            usage = getattr(response, 'usage_metadata', None)
            return {'items': items_from_answer(answer, width, height, prefix='G'), 'notes': answer['notes'],
                    'model': getattr(response, 'model_version', None) or model, 'endpoint': endpoint,
                    'usage': {'input_tokens': getattr(usage, 'prompt_token_count', None),
                              'output_tokens': getattr(usage, 'candidates_token_count', None),
                              'thinking_tokens': getattr(usage, 'thoughts_token_count', None)},
                    'latency_s': round(latency, 2)}
    raise RecognitionError(last)
