"""Preserve reviewed validation images and append distinct new predictions."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def unique_manifest(records):
    by_image = {}
    for record in records:
        name = record['image']
        if Path(name).name != name or '\\' in name:
            raise ValueError(f'Invalid image identifier: {name}')
        if name in by_image and record != by_image[name]:
            raise ValueError(f'Conflicting duplicate review records: {name}')
        by_image.setdefault(name, record)
    return list(by_image.values())


def get_images_for_classes(label_dir, target_classes, count=20,
                           excluded_images=(), image_dir=None):
    """Select each image once, rotating target classes in deterministic order."""
    if count < 0:
        raise ValueError('count must be nonnegative')
    targets = list(dict.fromkeys(target_classes))
    buckets = {cls: [] for cls in targets}
    seen = set(excluded_images)
    image_dir = Path(image_dir) if image_dir is not None else None
    seen_hashes = {sha256(image_dir / name) for name in seen} if image_dir else set()
    for path in sorted(Path(label_dir).glob('*.txt')):
        name = path.with_suffix('.jpg').name
        if name in seen or (image_dir and not (image_dir / name).is_file()):
            continue
        classes = {int(line.split()[0]) for line in path.read_text().splitlines() if line.strip()}
        for cls in targets:
            if cls in classes:
                buckets[cls].append(name)
    selected = []
    while len(selected) < count:
        added = False
        for cls in targets:
            while buckets[cls]:
                name = buckets[cls].pop(0)
                if name in seen:
                    continue
                seen.add(name)
                digest = sha256(image_dir / name) if image_dir else None
                if image_dir and digest in seen_hashes:
                    continue
                if digest is not None:
                    seen_hashes.add(digest)
                selected.append(name)
                added = True
                break
            if len(selected) == count:
                break
        if not added:
            break
    return selected


def read_gt(label_path, width, height):
    boxes = []
    for line in Path(label_path).read_text().splitlines():
        if not line.strip():
            continue
        cls, cx, cy, bw, bh = map(float, line.split())
        if cls != int(cls) or not 0 <= cls < 15 or not (
                0 <= cx <= 1 and 0 <= cy <= 1 and 0 < bw <= 1 and 0 < bh <= 1):
            raise ValueError(f'Invalid GT annotation: {label_path}')
        boxes.append([(cx-bw/2)*width, (cy-bh/2)*height,
                      (cx+bw/2)*width, (cy+bh/2)*height, int(cls), 1.0])
    return boxes


def draw_boxes(img, boxes, color, label, names, thickness=2,
               label_background=False, occupied_labels=None):
    import cv2
    occupied = occupied_labels if occupied_labels is not None else []
    for index, (x1, y1, x2, y2, cls_id, conf) in enumerate(boxes, 1):
        cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), color, thickness)
        text = f'{label} #{index} {names[cls_id]}'
        if label == 'Pred':
            text += f' {conf:.2f}'
        (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, thickness)
        x = max(0, min(int(x1), img.shape[1]-tw-1))
        y = max(th+2, min(int(y1)-8, img.shape[0]-baseline-1))
        if label_background:
            # Prefer the box's own upper edge, then find an unobstructed label row.
            candidates = [y] + list(range(th+4, img.shape[0]-baseline-3, th+baseline+7))
            for candidate in candidates:
                rect = (x, candidate-th-2, min(x+tw+3, img.shape[1]-1), candidate+baseline+2)
                if not any(rect[0] < r[2] and rect[2] > r[0] and
                           rect[1] < r[3] and rect[3] > r[1] for r in occupied):
                    y = candidate
                    occupied.append(rect)
                    break
            else:
                raise ValueError('Not enough space for readable box labels')
            cv2.rectangle(img, rect[:2], rect[2:], (0, 0, 0), -1)
            if y != candidates[0]:
                cv2.line(img, (x, y+baseline+2), (int(x1), int(y1)), color, 1)
        cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, thickness)


def run_export_review(project_root=None, count=20):
    import cv2
    import yaml
    from ultralytics import YOLO
    root = Path(project_root) if project_root else Path(__file__).resolve().parents[2]
    model_path = root / 'runs/detect/taiwanese_food_baseline_10ep/weights/best.pt'
    data_dir = root / 'data/roboflow_taiwanese_food_detection'
    image_dir, label_dir = data_dir / 'valid/images', data_dir / 'valid/labels'
    output = root / 'runs/detect/taiwanese_food_validation_review'
    manifest_path = output / 'review_manifest.json'
    original_bytes = manifest_path.read_bytes()
    original = json.loads(original_bytes)
    manifest = unique_manifest(original)
    existing = {record['image'] for record in manifest}
    if len(manifest) > count:
        raise ValueError('Existing review exceeds requested count; refusing to discard records')
    for sub in ('predictions', 'comparisons'):
        actual = {p.name for p in (output / sub).iterdir() if p.is_file()}
        if actual != existing:
            raise ValueError(f'Existing {sub} files do not match manifest; refusing to overwrite')
    protected = {p: sha256(p) for sub in ('predictions', 'comparisons')
                 for p in (output / sub).iterdir() if p.is_file()}
    protected[model_path] = sha256(model_path)
    selected = get_images_for_classes(label_dir, [0, 13, 7], count-len(manifest), existing, image_dir)
    if len(selected) != count-len(manifest):
        raise ValueError('Not enough distinct validation candidates')
    hashes = [sha256(image_dir / name) for name in existing | set(selected)]
    if len(set(hashes)) != count:
        raise ValueError('Review contains duplicate image contents')
    if manifest == original and not selected:
        print(f'Already complete: {count} unique images; no files changed')
        return []
    names = yaml.safe_load((data_dir / 'foodcal_data.yaml').read_text())['names']
    model = YOLO(str(model_path)) if selected else None
    if model and model.names != dict(enumerate(names)):
        raise ValueError('Checkpoint class names differ from dataset YAML')
    # Decode and infer all additions in staging before publishing any files.
    with tempfile.TemporaryDirectory(prefix='.review-stage-', dir=output) as temp:
        stage = Path(temp)
        for sub in ('predictions', 'comparisons'):
            (stage / sub).mkdir()
        for name in selected:
            image = cv2.imread(str(image_dir / name))
            if image is None:
                raise ValueError(f'Cannot decode validation image: {name}')
            height, width = image.shape[:2]
            gt = read_gt(label_dir / Path(name).with_suffix('.txt'), width, height)
            result = model.predict(str(image_dir / name), conf=0.25, verbose=False)[0]
            if Path(result.path).name != name:
                raise ValueError('Prediction image identity mismatch')
            pred = [box.xyxy[0].cpu().tolist() + [int(box.cls[0]), float(box.conf[0])]
                    for box in result.boxes]
            comparison = image.copy()
            draw_boxes(comparison, gt, (0, 255, 0), 'GT', names)
            draw_boxes(comparison, pred, (0, 0, 255), 'Pred', names)
            for sub, rendered in (('predictions', result.plot()), ('comparisons', comparison)):
                if not cv2.imwrite(str(stage / sub / name), rendered):
                    raise OSError(f'Failed to write {sub}/{name}')
            manifest.append({'image': name,
                             'gt': [{'cls': b[4], 'box': b[:4]} for b in gt],
                             'pred': [{'cls': b[4], 'conf': b[5], 'box': b[:4]} for b in pred],
                             'status': 'Pending Review'})
        staged_manifest = stage / 'review_manifest.json'
        staged_manifest.write_text(json.dumps(manifest, indent=4)+'\n', encoding='utf-8')
        if manifest_path.read_bytes() != original_bytes or any(
                sha256(path) != digest for path, digest in protected.items()):
            raise RuntimeError('Protected files changed during export; refusing to commit')
        backup = output / f'review_manifest.backup.{uuid.uuid4().hex}.json'
        with backup.open('xb') as stream:
            stream.write(original_bytes)
        created = []
        committed = False
        try:
            for name in selected:
                for sub in ('predictions', 'comparisons'):
                    destination = output / sub / name
                    with destination.open('xb') as stream:
                        created.append(destination)
                        stream.write((stage / sub / name).read_bytes())
            os.replace(staged_manifest, manifest_path)
            committed = True
        finally:
            if not committed:
                # Remove only files exclusively created by this failed attempt.
                for path in created:
                    path.unlink()
    print(f'Review now has {len(manifest)} unique images; preserved {len(existing)}, added {len(selected)}')
    print(f'Manifest backup: {backup}')
    for name in selected:
        print(f'Added: {name}')
    return selected


if __name__ == '__main__':
    run_export_review()
