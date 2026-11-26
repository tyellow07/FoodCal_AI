"""Run the web page's Gemini recognition on a folder of meal photos and save the answers for scoring.

    .venv/bin/python -m tools.run_gemini_batch --photos <folder> --out work/recognition/predictions.json --agree

Same steps as the web page: the photo is turned upright and shrunk like an upload, the student card is found and
painted grey, then the copy is sent to Google's Gemini API. Photos leave the computer only with --agree (free-tier
terms: Google may use what is sent to improve its products). A photo whose card is not found is skipped unless
--send-unmasked, so a card number is never sent by accident. The key comes from GEMINI_API_KEY or
.streamlit/secrets.toml.

The output (under the Git-ignored work/) keeps no photo: only each file's SHA-256, the size the boxes refer to and
Gemini's answer. It is rewritten after every photo, so an interrupted run continues where it stopped; photos already
answered are skipped unless their file changed or --redo. Score it with tools.evaluate_recognition
(docs/recognition_evaluation_v0.md).
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import time

from src.evaluation.recognition_metrics import PREDICTIONS_SCHEMA
from tools.ingredient_pilot import ROOT

PHOTO_SUFFIXES = ('.jpg', '.jpeg', '.png', '.webp')
DEFAULT_PAUSE_S = 5.0  # Flash Lite free tier: 15 requests a minute


def photo_files(folder):
    """{image_id (file name without extension): path}, sorted; two files with the same stem are an error."""
    photos = {}
    for path in sorted(Path(folder).iterdir()):
        if path.is_file() and path.suffix.lower() in PHOTO_SUFFIXES and not path.name.startswith('.'):
            if path.stem in photos:
                raise ValueError(f'duplicate_image_id: {path.stem}')
            photos[path.stem] = path
    if not photos:
        raise ValueError('no_photos_found')
    return photos


def _inside_work(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root):
        raise ValueError('output_must_be_under_ignored_work')
    return path


def load_predictions(path):
    if not path.exists():
        return {'schema': PREDICTIONS_SCHEMA, 'images': {}}
    saved = json.loads(path.read_text(encoding='utf-8'))
    if saved.get('schema') != PREDICTIONS_SCHEMA or not isinstance(saved.get('images'), dict):
        raise ValueError('predictions_schema_mismatch')
    return saved


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(temporary, path)


def run_batch(photos, out_path, *, recognize, find_card, decode, mask, send_unmasked=False, redo=False,
              pause_s=0.0, root=ROOT / 'work', log=print, sleep=time.sleep, now=None):
    """Recognise every photo not answered yet; returns the saved predictions dict.

    recognize(image) -> result dict (items, notes, model, ...); find_card(image) -> card dict or None;
    decode(raw bytes) -> upright PIL image; mask(image, quad) -> copy with the card painted over.
    """
    out_path = _inside_work(out_path, root)
    data = load_predictions(out_path)
    now = now or (lambda: datetime.now(timezone.utc).isoformat(timespec='seconds'))
    sent = 0
    for image_id, path in photos.items():
        raw = Path(path).read_bytes()
        digest = sha256(raw).hexdigest()
        old = data['images'].get(image_id) or {}
        if not redo and 'items' in old and old.get('file_sha256') == digest:
            continue
        entry = {'file': Path(path).name, 'file_sha256': digest}
        try:
            image = decode(raw)
        except ValueError as error:
            data['images'][image_id] = {**entry, 'error': str(error)}
            _save(out_path, data)
            log(f'{image_id}: cannot read the photo ({error})')
            continue
        card = find_card(image)
        if card is None and not send_unmasked:
            data['images'][image_id] = {**entry, 'skipped': 'card_not_found'}
            _save(out_path, data)
            log(f'{image_id}: student card not found, not sent (use --send-unmasked if it has no card)')
            continue
        if sent and pause_s:
            sleep(pause_s)
        sent += 1
        try:
            result = recognize(mask(image, card['quad']) if card else image)
        except Exception as error:  # quota, network or a blocked answer: keep going, retry on the next run
            data['images'][image_id] = {**entry, 'error': f'{type(error).__name__}: {error}'}
            _save(out_path, data)
            log(f'{image_id}: recognition failed ({type(error).__name__})')
            continue
        data['images'][image_id] = {**entry, 'width': image.width, 'height': image.height,
                                    'card_masked': bool(card), 'card_method': card.get('method') if card else None,
                                    'recognized_at': now(), **result}
        _save(out_path, data)
        log(f'{image_id}: {len(result.get("items") or [])} dishes ({result.get("model")})')
    return data


def api_key(root=ROOT):
    key = os.environ.get('GEMINI_API_KEY')
    if key:
        return key
    secrets = Path(root) / '.streamlit/secrets.toml'
    if secrets.is_file():
        import tomllib
        return tomllib.loads(secrets.read_text(encoding='utf-8')).get('GEMINI_API_KEY')
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--photos', type=Path, required=True, help='folder of JPEG, PNG or WebP meal photos')
    parser.add_argument('--out', type=Path, default=ROOT / 'work/recognition/predictions.json')
    parser.add_argument('--agree', action='store_true',
                        help='send the card-masked photos to Google Gemini (free-tier terms apply)')
    parser.add_argument('--send-unmasked', action='store_true', help='also send photos where no card was found')
    parser.add_argument('--redo', action='store_true', help='ask again for photos that already have an answer')
    parser.add_argument('--pause', type=float, default=DEFAULT_PAUSE_S, help='seconds between requests')
    args = parser.parse_args(argv)
    if not args.agree:
        parser.error('photos are sent to Google only with --agree (see docs/gemini_vision_v0.md)')
    key = api_key()
    if not key:
        parser.error('no GEMINI_API_KEY in the environment or .streamlit/secrets.toml')

    from src.detection.gemini_vision import gemini_clients, mask_card, recognize_foods_gemini
    from src.portion.card_locator import find_card, load_card_designs, load_card_reference
    from src.ui.adapter import decode_upload

    reference_dir = ROOT / 'data/card_reference'
    reference, designs = load_card_reference(reference_dir), load_card_designs(reference_dir)
    clients = gemini_clients(key)
    data = run_batch(photo_files(args.photos), args.out, send_unmasked=args.send_unmasked, redo=args.redo,
                     pause_s=args.pause, decode=decode_upload, mask=mask_card,
                     find_card=lambda image: find_card(image, reference, designs),
                     recognize=lambda image: recognize_foods_gemini(image, clients=clients))
    answered = sum('items' in entry for entry in data['images'].values())
    print(f'{answered}/{len(data["images"])} photos answered -> {args.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
