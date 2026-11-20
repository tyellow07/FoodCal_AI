"""Set up automatic student-card finding for the web page (src/portion/card_locator.py).

    python -m tools.setup_card_reference --logo <logo crop> --card-photo <card back, cropped to its edges>

The logo crop must be cut from the same card photo, so both share one pixel scale. The tool finds the logo in the
card photo and measures its size and the position of its centre from the card's bottom-left corner, in cm, using
the card width (85.60 mm). Ruler measurements can replace the measured position with --center-right/--center-up.
Images are re-encoded as PNG, which drops EXIF (GPS included). Output goes to the Git-ignored data/card_reference/.
The full card photo is only read, never copied, because it shows the card number.
"""
import argparse
import json
from pathlib import Path

import cv2

from src.portion.card_locator import CARD_H_CM, CARD_W_CM

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / 'data/card_reference'


def measure_reference(card_bgr, logo_bgr):
    """Logo size and centre offset (cm) on a photo of the card back cropped to the card edges."""
    height, width = card_bgr.shape[:2]
    if abs((width / height) / (CARD_W_CM / CARD_H_CM) - 1) > 0.04:
        raise ValueError('card_photo_must_be_cropped_to_the_card_edges')
    result = cv2.matchTemplate(card_bgr, logo_bgr, cv2.TM_CCOEFF_NORMED)
    _, score, _, (x, y) = cv2.minMaxLoc(result)
    if score < 0.9:
        raise ValueError('logo_not_found_in_card_photo: crop the logo from this same photo')
    px_per_cm = width / CARD_W_CM
    logo_h, logo_w = logo_bgr.shape[:2]
    return {'logo_width_cm': round(logo_w / px_per_cm, 3), 'logo_height_cm': round(logo_h / px_per_cm, 3),
            'logo_center_right_cm': round((x + logo_w / 2) / px_per_cm, 2),
            'logo_center_up_cm': round((height - y - logo_h / 2) / px_per_cm, 2),
            'match_score': round(float(score), 3)}


def write_reference(out_dir, logo_bgr, meta, *, root=ROOT / 'data'):
    out_dir, root = Path(out_dir).resolve(), Path(root).resolve()
    if not out_dir.is_relative_to(root):
        raise ValueError('output_must_be_under_ignored_data')
    out_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_dir / 'logo.png'), logo_bgr)
    (out_dir / 'card_reference.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return out_dir


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--logo', type=Path, required=True)
    parser.add_argument('--card-photo', type=Path, required=True)
    parser.add_argument('--center-right', type=float, help='ruler value (cm) instead of the measured one')
    parser.add_argument('--center-up', type=float, help='ruler value (cm) instead of the measured one')
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    logo, card = cv2.imread(str(args.logo)), cv2.imread(str(args.card_photo))
    if logo is None or card is None:
        raise ValueError('image_unreadable')
    meta = measure_reference(card, logo)
    measured = dict(meta)
    if args.center_right is not None:
        meta['logo_center_right_cm'] = args.center_right
    if args.center_up is not None:
        meta['logo_center_up_cm'] = args.center_up
    meta['source'] = 'tools.setup_card_reference; measured on the card back photo (width = 8.56 cm)'
    out = write_reference(args.out, logo, meta)
    print(json.dumps({'written': str(out), 'measured': measured, 'used': meta}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
