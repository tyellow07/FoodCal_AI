"""Food recognition with Google's Gemini API (free tier), the web page's recognition engine since 2026-11-20.

One request per photo: a downscaled, metadata-free JPEG with the student card painted over (the page finds the
card first), plus instructions; the answer is constrained to a JSON schema listing each visible food with a
Traditional Chinese name, a known food key when it matches, the container, a box in Gemini's native
[ymin, xmin, ymax, xmax] 0-1000 format, visible ingredients and a confidence. Only what is visible is asked for:
no weights, calories or recipe guesses.

Free-tier terms let Google use submitted content to improve its products, with human review; the page says so
before the user agrees. Keys come from the caller (Streamlit secrets), never from this module or the repo.
"""
import json
import time

from PIL import ImageDraw

from src.portion.rough_volume import FOOD_TEMPLATES
from .claude_vision import CONFIDENCE, CONTAINERS, GRID, RecognitionError, items_from_answer, jpeg_bytes

MODELS = ('gemini-flash-latest', 'gemini-2.5-flash')  # tried in order; the alias follows Google's current Flash

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
    """Clients to try in order: Gemini API (AI Studio key), then Vertex AI express mode with the same key."""
    from google import genai
    return [('gemini_api', genai.Client(api_key=api_key)),
            ('vertex_express', genai.Client(vertexai=True, api_key=api_key))]


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


def recognize_foods_gemini(image, *, clients, models=MODELS):
    """{'items', 'notes', 'model', 'endpoint', 'usage', 'latency_s'} for one PIL image (already card-masked)."""
    from google.genai import errors, types
    import httpx
    width, height = image.size
    contents = [types.Part.from_bytes(data=jpeg_bytes(image), mime_type='image/jpeg'),
                'List the foods visible in this photo.']
    config = types.GenerateContentConfig(system_instruction=INSTRUCTIONS, response_mime_type='application/json',
                                         response_json_schema=SCHEMA)
    last = 'gemini_key_invalid'
    for endpoint, client in clients:
        for model in models:
            started = time.perf_counter()
            try:
                response = client.models.generate_content(model=model, contents=contents, config=config)
            except errors.ClientError as error:
                code, text = getattr(error, 'code', None), str(error).lower()
                if code == 404 or 'not found' in text:
                    last = 'gemini_model_unavailable'
                    continue  # try the next model name
                if code == 429 or 'resource_exhausted' in text or 'quota' in text:
                    raise RecognitionError('gemini_quota_exceeded') from None
                if code in (400, 401, 403) and ('api key' in text or 'api_key' in text or 'permission' in text
                                                or 'unauthenticated' in text or code in (401, 403)):
                    last = 'gemini_key_invalid'
                    break  # this endpoint does not accept the key: try the next endpoint
                raise RecognitionError('gemini_bad_request') from None
            except errors.ServerError:
                raise RecognitionError('gemini_service_error') from None
            except httpx.HTTPError:
                raise RecognitionError('gemini_network_error') from None
            latency = time.perf_counter() - started
            answer = _answer_from(response)
            usage = getattr(response, 'usage_metadata', None)
            return {'items': items_from_answer(answer, width, height, prefix='G'), 'notes': answer['notes'],
                    'model': getattr(response, 'model_version', None) or model, 'endpoint': endpoint,
                    'usage': {'input_tokens': getattr(usage, 'prompt_token_count', None),
                              'output_tokens': getattr(usage, 'candidates_token_count', None),
                              'thinking_tokens': getattr(usage, 'thoughts_token_count', None)},
                    'latency_s': round(latency, 2)}
    raise RecognitionError(last)
