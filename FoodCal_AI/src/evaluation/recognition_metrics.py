"""Dish recognition vs human labels (docs/recognition_evaluation_v0.md): right name, missed, extra, wrong name.

The web page names dishes in free text (Gemini), so a predicted dish matches a labelled one only when the names agree:
exactly after normalising, through the label's aliases, or by a person's written verdict. A name the program cannot
decide is never guessed: the pair is listed as pending and nothing is scored until every pending pair has a verdict.

Labels may carry a box (then the predicted box must overlap it, IoU >= 0.5, as in rubric item 2a) or only a name
(then any dish in the same photo may match). All inputs are plain rows; nothing is modified, written or sent.
"""
from collections import Counter
from decimal import Decimal
import unicodedata

from src.portion.nutrition_model import INGREDIENTS
from .meal_metrics import _decimal, _ratio
from .stage_metrics import IOU_THRESHOLD, _box, iou, score_stages

LABEL_COLUMNS = ('image_id', 'dish_id', 'name', 'aliases', 'ingredients', 'x_min', 'y_min', 'x_max', 'y_max',
                 'image_width', 'image_height')
JUDGEMENT_COLUMNS = ('image_id', 'dish_id', 'detection_id', 'gold_name', 'predicted_name', 'iou', 'verdict', 'judge')
VERDICTS = ('same', 'different')
PREDICTIONS_SCHEMA = 'foodcal_recognition_predictions_v0'
BOX_KEYS = ('x_min', 'y_min', 'x_max', 'y_max')
ASPECT_TOLERANCE = Decimal('0.01')


def normalise_name(text):
    """Comparable form of a dish name: full-width folded, case-folded, spaces and punctuation removed."""
    folded = unicodedata.normalize('NFKC', str(text)).casefold()
    return ''.join(ch for ch in folded if not unicodedata.category(ch).startswith(('P', 'Z', 'C')))


def _split(cell):
    return [part.strip() for part in unicodedata.normalize('NFKC', cell or '').split(';') if part.strip()]


def read_labels(rows):
    """Label rows -> {image_id: [dish]}; raises ValueError with a code for anything a person must fix."""
    images, seen = {}, set()
    for row in rows:
        image_id, dish_id, name = (str(row.get(k) or '').strip() for k in ('image_id', 'dish_id', 'name'))
        if not image_id or not dish_id or not name:
            raise ValueError('label_missing_image_dish_or_name')
        if (image_id, dish_id) in seen:
            raise ValueError(f'duplicate_dish_id: {image_id}/{dish_id}')
        seen.add((image_id, dish_id))
        cells = [str(row.get(k) or '').strip() for k in BOX_KEYS]
        box = size = None
        if any(cells):
            if not all(cells):
                raise ValueError(f'invalid_ground_truth_box: {image_id}/{dish_id}')
            box = _box(dict(zip(BOX_KEYS, cells)), f'invalid_ground_truth_box: {image_id}/{dish_id}')
            size = tuple(_decimal(row.get(k), f'invalid_image_size: {image_id}') for k in ('image_width', 'image_height'))
            if not all(size):
                raise ValueError(f'invalid_image_size: {image_id}')
        ingredients = _split(row.get('ingredients'))
        unknown = [key for key in ingredients if key not in INGREDIENTS and key != 'other']
        if unknown:
            raise ValueError(f'unknown_ingredient_key: {unknown[0]}')
        names = {normalise_name(name)} | {normalise_name(alias) for alias in _split(row.get('aliases'))}
        images.setdefault(image_id, []).append({'dish_id': dish_id, 'name': name, 'names': names - {''}, 'box': box,
                                                'size': size, 'ingredients': set(ingredients)})
    return images


def read_judgements(rows):
    """Filled-in pending rows -> {(image_id, dish_id, detection_id): (verdict, judge)}."""
    verdicts = {}
    for row in rows:
        key = tuple(str(row.get(k) or '').strip() for k in ('image_id', 'dish_id', 'detection_id'))
        verdict, judge = (str(row.get(k) or '').strip().lower() for k in ('verdict', 'judge'))
        if not verdict and not judge:
            continue  # still blank: the pair stays pending
        if verdict not in VERDICTS or not judge:
            raise ValueError(f'invalid_judgement: {"/".join(key)}')
        if key in verdicts and verdicts[key][0] != verdict:
            raise ValueError(f'conflicting_judgement: {"/".join(key)}')
        verdicts[key] = (verdict, str(row.get('judge')).strip())
    return verdicts


def predicted_dishes(entry, size=None):
    """Dishes from one saved recognition result; boxes rescaled to the labeller's image size when given."""
    scale = None
    if size is not None:
        width, height = (_decimal(entry.get(k), 'invalid_prediction_size') for k in ('width', 'height'))
        if not width or not height:
            raise ValueError('invalid_prediction_size')
        if abs((width / height) / (size[0] / size[1]) - 1) > ASPECT_TOLERANCE:
            raise ValueError('image_size_mismatch')  # not the same photo, or turned another way
        scale = (size[0] / width, size[1] / height)
    dishes = []
    for item in entry.get('items') or []:
        names = dict(item.get('names') or {})
        shown = names.get('zh-TW') or item.get('display_name') or names.get('en') or ''
        box = None
        if scale is not None:
            cells = item.get('bounding_box') or {}
            if not all(k in cells for k in BOX_KEYS):
                raise ValueError('invalid_prediction_box')
            box = _box(cells, 'invalid_prediction_box')
            box = [box[0] * scale[0], box[1] * scale[1], box[2] * scale[0], box[3] * scale[1]]
        dishes.append({'detection_id': str(item['detection_id']), 'name': shown,
                       'names': {normalise_name(n) for n in [item.get('display_name'), *names.values()] if n} - {''},
                       'box': box, 'confidence': item.get('confidence'),
                       'ingredients': {part[0] for part in item.get('components') or []}})
    return dishes


def _greedy(pairs, used_gold, used_pred):
    """One-to-one: pairs sorted best first; a pair is taken when neither side is taken yet."""
    taken = []
    for pair in pairs:
        if pair['gold'] not in used_gold and pair['pred'] not in used_pred:
            used_gold.add(pair['gold'])
            used_pred.add(pair['pred'])
            taken.append(pair)
    return taken


def _order(pair):
    return (pair['overlap'] is None, -(pair['overlap'] or 0), pair['gold'], pair['pred'])


def _match_image(image_id, golds, preds, verdicts, threshold):
    pairs = []
    for g, gold in enumerate(golds):
        for p, pred in enumerate(preds):
            overlap = iou(gold['box'], pred['box']) if gold['box'] is not None else None
            if overlap is not None and overlap < threshold:
                continue
            key = (image_id, gold['dish_id'], pred['detection_id'])
            pairs.append({'gold': g, 'pred': p, 'overlap': overlap, 'auto': bool(gold['names'] & pred['names']),
                          'verdict': verdicts.get(key)})
    pairs.sort(key=_order)
    used_gold, used_pred = set(), set()
    auto = _greedy([q for q in pairs if q['auto']], used_gold, used_pred)
    left = [q for q in pairs if q['gold'] not in used_gold and q['pred'] not in used_pred]
    pending = [q for q in left if q['verdict'] is None]
    judged = _greedy([q for q in left if q['verdict'] and q['verdict'][0] == 'same'], used_gold, used_pred)
    misnamed = _greedy([q for q in left if q['overlap'] is not None], used_gold, used_pred)
    return {'auto': auto, 'judged': judged, 'misnamed': misnamed, 'pending': pending,
            'missed': [g for g in range(len(golds)) if g not in used_gold],
            'extra': [p for p in range(len(preds)) if p not in used_pred],
            'used_keys': {(image_id, golds[q['gold']]['dish_id'], preds[q['pred']]['detection_id']) for q in pairs}}


def _closest(gold, preds):
    if gold['box'] is None or not preds:
        return None
    best = max(preds, key=lambda pred: iou(gold['box'], pred['box']))
    return {'detection_id': best['detection_id'], 'predicted_name': best['name'],
            'iou': iou(gold['box'], best['box'])}


def recognition_metrics(label_rows, predictions, judgement_rows=(), *, threshold=IOU_THRESHOLD):
    """Dish-level scores for every labelled photo, or status needs_judgement with the pairs a person must decide.

    predictions: {'schema': PREDICTIONS_SCHEMA, 'images': {image_id: saved result}} as tools.run_gemini_batch writes
    it. A labelled photo without a result (not run, error, skipped) counts all its dishes as missed.
    """
    if not isinstance(predictions, dict) or predictions.get('schema') != PREDICTIONS_SCHEMA:
        raise ValueError('predictions_schema_mismatch')
    labels, verdicts = read_labels(label_rows), read_judgements(judgement_rows)
    results = predictions.get('images') or {}
    without, pending, failures, used_keys = [], [], [], set()
    counts = Counter()
    ingredient = Counter()
    missed_keys, extra_keys = Counter(), Counter()
    for image_id, golds in labels.items():
        entry = results.get(image_id)
        if not isinstance(entry, dict) or 'items' not in entry:
            without.append(image_id)
            preds = []
        else:
            sizes = {gold['size'] for gold in golds if gold['size'] is not None}
            if len(sizes) > 1:
                raise ValueError(f'invalid_image_size: {image_id}')
            preds = predicted_dishes(entry, next(iter(sizes), None))
        found = _match_image(image_id, golds, preds, verdicts, threshold)
        used_keys |= found['used_keys']
        for q in found['pending']:
            gold, pred = golds[q['gold']], preds[q['pred']]
            pending.append({'image_id': image_id, 'dish_id': gold['dish_id'], 'detection_id': pred['detection_id'],
                            'gold_name': gold['name'], 'predicted_name': pred['name'],
                            'iou': '' if q['overlap'] is None else str(round(q['overlap'], 3)),
                            'verdict': '', 'judge': ''})
        counts['gold'] += len(golds)
        counts['pred'] += len(preds)
        counts['boxed'] += sum(gold['box'] is not None for gold in golds)
        for kind in ('auto', 'judged'):
            counts[kind] += len(found[kind])
            counts['correct_boxed'] += sum(golds[q['gold']]['box'] is not None for q in found[kind])
            for q in found[kind]:
                gold, pred = golds[q['gold']], preds[q['pred']]
                if gold['ingredients']:
                    ingredient['dishes'] += 1
                    ingredient['tp'] += len(gold['ingredients'] & pred['ingredients'])
                    ingredient['fp'] += len(pred['ingredients'] - gold['ingredients'])
                    ingredient['fn'] += len(gold['ingredients'] - pred['ingredients'])
                    missed_keys.update(gold['ingredients'] - pred['ingredients'])
                    extra_keys.update(pred['ingredients'] - gold['ingredients'])
        counts['misnamed'] += len(found['misnamed'])
        for q in found['misnamed']:
            gold, pred = golds[q['gold']], preds[q['pred']]
            failures.append({'type': 'misnamed', 'image_id': image_id, 'dish_id': gold['dish_id'],
                             'gold_name': gold['name'], 'detection_id': pred['detection_id'],
                             'predicted_name': pred['name'], 'iou': q['overlap']})
        for g in found['missed']:
            gold = golds[g]
            failures.append({'type': 'missed', 'image_id': image_id, 'dish_id': gold['dish_id'],
                             'gold_name': gold['name'], 'no_result': image_id in without,
                             'closest_prediction': _closest(gold, preds)})
        for p in found['extra']:
            pred = preds[p]
            failures.append({'type': 'extra', 'image_id': image_id, 'detection_id': pred['detection_id'],
                             'predicted_name': pred['name'], 'confidence': pred['confidence']})
    base = {'iou_threshold': threshold, 'images': len(labels), 'images_without_result': without,
            'unlabelled_result_images': len(set(results) - set(labels))}
    if pending:
        return {**base, 'status': 'needs_judgement', 'pending': pending}
    correct = counts['auto'] + counts['judged']
    precision, recall = _ratio(correct, counts['pred']), _ratio(correct, counts['gold'])
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else (
        Decimal(0) if counts['gold'] else None)
    located = counts['correct_boxed'] + counts['misnamed']
    ingredients = None
    if ingredient['dishes']:
        ingredients = {'dishes': ingredient['dishes'], 'precision': _ratio(ingredient['tp'], ingredient['tp'] + ingredient['fp']),
                       'recall': _ratio(ingredient['tp'], ingredient['tp'] + ingredient['fn']),
                       'most_missed': missed_keys.most_common(10), 'most_extra': extra_keys.most_common(10)}
    return {**base, 'status': 'scored', 'gold_dishes': counts['gold'], 'predictions': counts['pred'],
            'correct': correct, 'correct_by_name_rule': counts['auto'], 'correct_by_judgement': counts['judged'],
            'misnamed': counts['misnamed'], 'missed': counts['gold'] - correct - counts['misnamed'],
            'extra': counts['pred'] - correct - counts['misnamed'], 'precision': precision, 'recall': recall,
            'f1': f1, 'boxed_gold_dishes': counts['boxed'], 'localisation_recall': _ratio(located, counts['boxed']),
            'name_accuracy_when_located': _ratio(counts['correct_boxed'], located), 'ingredients': ingredients,
            'judges': sorted({judge for key, (_, judge) in verdicts.items() if key in used_keys}),
            'unused_judgements': len(set(verdicts) - used_keys), 'failure_cases': failures}


def score_recognition(metrics):
    """Rubric 2a points from the dish F1, only when every label has a box (2a needs IoU); otherwise None."""
    if metrics.get('status') != 'scored':
        return {'2a_detection': None, 'note': 'pending name judgements'}
    if metrics['boxed_gold_dishes'] != metrics['gold_dishes'] or not metrics['gold_dishes']:
        return {'2a_detection': None, 'note': 'some labels have no box: dish F1 is reported, but it is not item 2a'}
    return {'2a_detection': score_stages(detection={'f1': metrics['f1']})['2a_detection'],
            'note': 'same name (rule or written verdict) replaces same class; IoU >= threshold as in 2a'}
