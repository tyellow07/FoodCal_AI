"""Food recognition with Google's Gemini API (free tier), the web page's recognition engine since 2026-11-20.

One request per photo: a downscaled, metadata-free JPEG with the student card painted over (the page finds the
card first), plus instructions; the answer is constrained to a JSON schema in two levels (since 2026-11-26):

- dishes: each dish on the tray, named in every page language (Traditional Chinese, English, Japanese, Korean,
  Indonesian, Vietnamese), with one sentence on its usual recipe (in the page language);
- foods: the visible ingredients, one box each, so every portion is measured from one ingredient's own box. Each
  names its dish, is named with how it is cooked (照燒雞, 白切雞, 滷豆腐, 白飯), and gives a known food key when it
  matches, the container, a box in Gemini's native [ymin, xmin, ymax, xmax] 0-1000 format, a confidence, its
  ingredient keys with shares, the cooking method from the dish's recipe, and a shape. Bowls and cups stay one item:
  their contents are stacked and measured from the bowl.

Only what is visible becomes a food; the recipe explains the cooking method (the hidden oil and sugar) and is never
visual proof. No weights or calories are asked for. Free-text notes come in the page language.

Free-tier terms let Google use submitted content to improve its products, with human review; the page says so
before the user agrees. Keys come from the caller (the visitor's own key typed on the page, else Streamlit
secrets), never from this module or the repo; only a hash of a key is kept, to remember its model order.
"""
from hashlib import sha256
import json
from pathlib import Path
import re
import time

from PIL import ImageDraw

from src.portion.nutrition_model import COOKING_METHODS, INGREDIENTS
from src.portion.rough_volume import FOOD_TEMPLATES
from .claude_vision import CONFIDENCE, CONTAINERS, GRID, NAME_FIELDS, RecognitionError, items_from_answer, jpeg_bytes

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
# Gemini refuses a schema whose enums branch too much (400 "too much branching for serving"): on 2026-11-26 the
# 226-key ingredient enum broke every request. So ingredient keys are listed in the instructions and checked on
# our side (ingredient_key), and every enum left in the schemas stays at most this long (tested).
MAX_ENUM = 64
_LAST_GOOD = {}  # endpoint cache key -> model that answered last time; tried first next time

NAMES = {field: {'type': 'string'} for _, field in NAME_FIELDS}  # name_zh, name_en, ... one per page language

SCHEMA = {
    'type': 'object',
    'properties': {
        'dishes': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {'number': {'type': 'integer'}, **NAMES, 'recipe': {'type': 'string'}},
            'required': ['number', *NAMES, 'recipe']}},
        'foods': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {
                'dish_number': {'type': 'integer'},
                **NAMES,
                'food_key': {'type': 'string', 'enum': sorted(FOOD_TEMPLATES) + ['other']},
                'container': {'type': 'string', 'enum': list(CONTAINERS)},
                'box_2d': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 4, 'maxItems': 4},
                'confidence': {'type': 'string', 'enum': list(CONFIDENCE)},
                'components': {'type': 'array', 'items': {
                    'type': 'object',
                    'properties': {'ingredient': {'type': 'string'},  # a key from the instructions; see MAX_ENUM
                                   'share_percent': {'type': 'integer'}},
                    'required': ['ingredient', 'share_percent']}},
                'cooking_method': {'type': 'string', 'enum': sorted(COOKING_METHODS)},
                'shape': {'type': 'string', 'enum': ['mound', 'flat', 'whole']},
            },
            'required': ['dish_number', *NAMES, 'food_key', 'container', 'box_2d', 'confidence', 'components',
                         'cooking_method', 'shape']}},
        'notes': {'type': 'string'},
    },
    'required': ['dishes', 'foods', 'notes'],
}

def instructions(notes_language='Traditional Chinese'):
    """The recognition instructions; Gemini writes the recipes and notes in notes_language (the page language)."""
    return f"""You identify the food in one photo of a meal from a Taiwanese university cafeteria, ingredient by
ingredient. Portions are measured from your boxes, so each food box must hold one ingredient.

First list the dishes (dishes), every dish you can see:
- number: 1, 2, 3 ... in the order you list them.
- name_zh: the dish name a Taiwanese cafeteria would use (for example 照燒雞腿飯, 滷肉飯, 炒高麗菜, 番茄炒蛋, 麻婆豆腐,
  白飯). name_en, name_ja, name_ko, name_id, name_vi: the same dish in English, Japanese, Korean, Indonesian and
  Vietnamese, short, as a menu in that language would write it.
- recipe: in {notes_language}, one short sentence on how this dish is usually made: the cooking method and the oil,
  sugar or sauce its recipe usually uses (for example: chicken thigh pan-fried, then glazed with soy sauce, mirin and
  sugar). This is recipe knowledge, not something you see.

Then list the foods (foods), every visible ingredient of those dishes, each with its own box:
- dish_number: the number of the dish it belongs to.
- name_zh: the ingredient named with how it is cooked, the way a menu would say it (for example 白飯, 照燒雞, 白切雞,
  滷豆腐, 荷包蛋, 炒高麗菜, 滷肉燥). name_en, name_ja, name_ko, name_id, name_vi: the same in those languages.
- Split a dish into its ingredients wherever they lie apart or one lies on top of another: the rice and each topping
  of a rice plate, a fried egg on rice, chicken pieces beside the rice, tofu cubes and the sauce around them when you
  can tell them apart. Keep one food when its ingredients are mixed too finely to box apart (fried rice, a stir-fry
  of shredded vegetables, minced pork sauce) and give their shares in components.
- Food in a bowl or a cup (soups, noodle soups, rice bowls, drinks) is one food for the whole bowl or cup: box the
  rim and list what is in it in components with shares. Its contents are stacked, so it is measured from the bowl.
- food_key: one of these keys only when the food is exactly that, otherwise "other":
{chr(10).join(f'  {key}' for key in sorted(FOOD_TEMPLATES))}
  (cooked_rice_region is plain cooked rice, cooked_noodle_region plain noodles, visible_fried_egg a fried egg.)
- container: "bowl" when the food is served in a bowl, "cup" for a drink in a cup, glass or bottle, otherwise "plate".
- box_2d: [ymin, xmin, ymax, xmax] of the tightest box around this food, normalised to 0-{GRID}; for a bowl or a drink,
  box the bowl or cup opening (the rim) as seen from above.
- confidence: high, medium or low.
- components: what the food is made of, as ingredient keys with your estimate of each one's share of its weight in
  percent (about 100 in total). Usually one key at 100; several only for mixed foods and bowls (count the broth of
  soups). Write each key exactly as listed below; use "other" for anything not listed. For a drink, count the ice
  cubes you can see as ice and the pearls as tapioca_pearls; a shop milk tea is milk_tea unless it is labelled 鮮奶
  (fresh milk). Pick a meat's cut or organ when you can tell it (pork_jowl, beef_brisket, chicken_gizzard), otherwise
  the plain chicken, pork, beef or duck. Likewise pick the fish or seafood when you can tell it (tilapia, saury,
  salmon_sashimi, oyster, crab_stick), otherwise the plain fish, shrimp or squid. Keys:
{chr(10).join(f'  {key} = {INGREDIENTS[key][0]}' for key in sorted(INGREDIENTS))}
- cooking_method: how this ingredient is cooked in its dish's usual recipe (the recipe above), checked against what
  you see (照燒 is sweet_sauce, 白切 is boiled_or_steamed, 滷 is braised); one of the keys below. Plain rice is none.
  For a drink, the sweetness: read it from the cup sticker or label (全糖, 少糖, 半糖, 微糖, 無糖) when you can, use
  drink_sugar_unknown for a shop drink whose sweetness you cannot read, and none for plain milk, black coffee or
  water. Raw fruit is none. A dessert in syrup (豆花, 仙草, 紅豆湯, 刨冰) is sweet_soup; count shaved ice as ice, plus
  its toppings and condensed milk.
{chr(10).join(f'  {key} = {COOKING_METHODS[key][0]}' for key in sorted(COOKING_METHODS))}
- shape: "mound" when the food is piled up into a heap (rice, noodles, heaped vegetables or meat pieces), "flat" when
  it lies in a thin layer (a fried egg, a cutlet, slices laid side by side, a topping spread over rice), "whole" for
  one whole item or a piece still in its peel or rind (a whole banana, apple or orange, a steamed bun, corn on the
  cob, a watermelon wedge), or meat on the bone (a drumstick, chicken wings, spare ribs, pork knuckle), a whole fish,
  or shrimp, crab, clams or mussels still in their shells.

Every food must be visible in the photo. The recipe only explains how a food is cooked; never add a food the photo
does not show (oil, sugar, a sauce soaked in). Do not estimate weights, portions or calories. Ignore plates, trays,
cutlery, hands, the grey painted-over area and other non-food objects. A self-service (自助餐) plate holds separate
dishes that touch each other: list every dish on its own. Boxes may overlap where foods touch or lie on top of each
other, but keep each one tight around its own food. If there is no food, return empty lists. Write all Chinese in
Traditional Chinese characters as used in Taiwan (繁體中文), never Simplified. Use notes, in {notes_language}, for
anything uncertain."""


INSTRUCTIONS = instructions()

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


def _is_network_error(error):
    root = type(error).__module__.split('.')[0]
    return root in ('httpx', 'httpx2', 'httpcore', 'httpcore2') or isinstance(error, (ConnectionError, TimeoutError))


def _parse_json(response):
    """The answer's JSON, after the checks every answer needs (blocked, truncated, not JSON)."""
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
        return json.loads(response.text)
    except (TypeError, ValueError):
        raise RecognitionError('gemini_answer_unreadable') from None


def ingredient_key(text):
    """An ingredient key as Gemini wrote it -> the table's key ('Pork Belly' -> pork_belly), else 'other'.

    The schema cannot hold the ingredient keys as an enum (see MAX_ENUM), so this check replaces it.
    """
    key = re.sub(r'[\s\-]+', '_', str(text or '').strip().lower())
    return key if key in INGREDIENTS else 'other'


def _dishes_from(answer):
    """{dish number: {'id': 'D1', 'names': {page language: name}, 'display_name', 'recipe'}} from an answer."""
    dishes = {}
    for dish in answer.get('dishes') or []:
        if not isinstance(dish, dict) or not isinstance(dish.get('number'), int) or dish['number'] in dishes:
            continue
        names = {code: dish[field].strip() for code, field in NAME_FIELDS
                 if isinstance(dish.get(field), str) and dish[field].strip()}
        recipe = dish.get('recipe')
        dishes[dish['number']] = {'id': f"D{dish['number']}", 'names': names,
                                  'display_name': names.get('zh-TW') or names.get('en') or '',
                                  'recipe': recipe.strip() if isinstance(recipe, str) else ''}
    return dishes


def _answer_from(response):
    answer = _parse_json(response)
    if not isinstance(answer, dict) or not isinstance(answer.get('foods'), list):
        raise RecognitionError('gemini_answer_unreadable')
    dishes = _dishes_from(answer)
    foods = []
    for food in answer['foods']:
        box = food.get('box_2d') if isinstance(food, dict) else None
        if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
            continue
        y0, x0, y1, x1 = box
        parts = food.get('components')
        if isinstance(parts, list):
            parts = [{**part, 'ingredient': ingredient_key(part.get('ingredient'))} for part in parts
                     if isinstance(part, dict)]
            food = {**food, 'components': parts}
        dish = dishes.get(food.get('dish_number')) if isinstance(food.get('dish_number'), int) else None
        foods.append({**food, 'box': {'x_min': x0, 'y_min': y0, 'x_max': x1, 'y_max': y1}, 'dish': dish})
    return {'foods': foods, 'dishes': list(dishes.values()), 'notes': answer.get('notes', '')}


def _generate(contents, instructions, schema, clients, models=None):
    """(response, model, endpoint, latency) from the first model that answers; RecognitionError otherwise.

    Models are tried in order: a missing model, a used-up quota or an overloaded model moves on to the next one; a
    key the endpoint refuses moves on to the next endpoint.
    """
    from google.genai import errors, types
    last = 'gemini_key_invalid'
    deadline = time.perf_counter() + TOTAL_BUDGET_S
    for endpoint, client in clients:
        for model in models or models_for(endpoint, client):
            if time.perf_counter() > deadline:
                raise RecognitionError('gemini_too_slow')
            started = time.perf_counter()
            config = types.GenerateContentConfig(system_instruction=instructions, response_mime_type='application/json',
                                                 response_json_schema=schema, thinking_config=_thinking(model, types))
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
            except Exception as error:  # the SDK raises httpx or httpx2 connection errors, depending on its transport
                if _is_network_error(error):
                    raise RecognitionError('gemini_network_error') from None
                raise
            latency = time.perf_counter() - started
            if models is None:
                _LAST_GOOD[_cache_key(endpoint, client)] = model
                _remember(model)
            return response, model, endpoint, latency
    raise RecognitionError(last)


def _meta(response, model, endpoint, latency):
    usage = getattr(response, 'usage_metadata', None)
    return {'model': getattr(response, 'model_version', None) or model, 'endpoint': endpoint,
            'usage': {'input_tokens': getattr(usage, 'prompt_token_count', None),
                      'output_tokens': getattr(usage, 'candidates_token_count', None),
                      'thinking_tokens': getattr(usage, 'thoughts_token_count', None)},
            'latency_s': round(latency, 2)}


def recognize_foods_gemini(image, *, clients, models=None, notes_language='Traditional Chinese'):
    """{'items', 'dishes', 'notes', 'model', 'endpoint', 'usage', 'latency_s'} for one PIL image (already card-masked).

    items are the visible ingredients, each with its dish under 'dish' (or None); dishes lists the dishes those items
    belong to, in Gemini's order.
    """
    from google.genai import types
    width, height = image.size
    contents = [types.Part.from_bytes(data=jpeg_bytes(image), mime_type='image/jpeg'),
                'List the foods visible in this photo.']
    response, model, endpoint, latency = _generate(contents, instructions(notes_language), SCHEMA, clients, models)
    answer = _answer_from(response)
    items = items_from_answer(answer, width, height, prefix='G')
    used = {item['dish']['id'] for item in items if item.get('dish')}
    return {'items': items, 'dishes': [dish for dish in answer['dishes'] if dish['id'] in used],
            'notes': answer['notes'], **_meta(response, model, endpoint, latency)}


SIDE_SCHEMA = {
    'type': 'object',
    'properties': {
        'dishes': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {
                'number': {'type': 'integer'},
                'food_box_2d': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 4, 'maxItems': 4},
                'container_box_2d': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 0, 'maxItems': 4},
            },
            'required': ['number', 'food_box_2d', 'container_box_2d']}},
        'notes': {'type': 'string'},
    },
    'required': ['dishes', 'notes'],
}

def side_instructions(notes_language='Traditional Chinese'):
    """The side photo instructions; Gemini writes its notes in notes_language (the page language)."""
    return f"""You see a second photo of a meal, taken from the side at a low angle. The foods were already
identified in a photo taken from above (each one an ingredient of a dish, or a whole bowl or cup); the user lists
them with their numbers. Below, "dish" means one listed food.

For every listed dish you can see in this side photo, report:
- number: the dish's number from the list.
- food_box_2d: [ymin, xmin, ymax, xmax], normalised to 0-{GRID}, the tightest box around the food itself as seen from
  this angle: from the highest point of the food down to where it meets the plate. For a dish in a bowl or cup, box
  the food you can see inside it (its surface, and any heap rising above the rim).
- container_box_2d: for a dish in a bowl or cup, the box around the whole bowl or cup, from the top of its rim down to
  where it stands on the table; an empty list for dishes on a plate.

Skip listed dishes you cannot see. Report positions only: no sizes, weights or calories. Ignore the grey painted-over
area, cutlery and hands. Use notes, in {notes_language}, for anything uncertain."""


SIDE_INSTRUCTIONS = side_instructions()


def _side_box(box, width, height):
    if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
        return None
    y0, x0, y1, x1 = (min(GRID, max(0, v)) for v in box)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    if x1 - x0 < 5 or y1 - y0 < 5:
        return None
    return [round(x0 * width / GRID, 1), round(y0 * height / GRID, 1), round(x1 * width / GRID, 1),
            round(y1 * height / GRID, 1)]


def recognize_side_view(image, dishes, *, clients, models=None, notes_language='Traditional Chinese'):
    """{'boxes': {detection_id: {'food': [x0, y0, x1, y1], 'container': [...] or None}}, 'notes', model meta}.

    image: the side photo, already card-masked. dishes: [(detection_id, name, container)] from the top photo, in
    the page's order; Gemini answers with their numbers (1-based).
    """
    from google.genai import types
    width, height = image.size
    listing = '\n'.join(f'{index:02d} {name} ({container})' for index, (_, name, container) in enumerate(dishes, 1))
    contents = [types.Part.from_bytes(data=jpeg_bytes(image), mime_type='image/jpeg'),
                'Dishes from the photo taken from above:\n' + listing]
    response, model, endpoint, latency = _generate(contents, side_instructions(notes_language), SIDE_SCHEMA, clients,
                                                   models)
    answer = _parse_json(response)
    if not isinstance(answer, dict) or not isinstance(answer.get('dishes'), list):
        raise RecognitionError('gemini_answer_unreadable')
    boxes = {}
    for entry in answer['dishes']:
        if not isinstance(entry, dict) or not isinstance(entry.get('number'), int):
            continue
        if not 1 <= entry['number'] <= len(dishes):
            continue
        food = _side_box(entry.get('food_box_2d'), width, height)
        if food is None:
            continue
        boxes[dishes[entry['number'] - 1][0]] = {'food': food,
                                                 'container': _side_box(entry.get('container_box_2d'), width, height)}
    return {'boxes': boxes, 'notes': answer.get('notes', ''), **_meta(response, model, endpoint, latency)}
