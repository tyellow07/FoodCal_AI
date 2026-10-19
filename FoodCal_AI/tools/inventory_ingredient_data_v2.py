"""Read-only inventory. Dish labels count sampling opportunities, never ingredient GT."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import math

CANDIDATES = {
    'visible_fried_egg': [0],
    'cooked_rice_region': [4, 14],
    'cooked_noodle_region': [2, 7, 11, 12],
    'loofah_deferred': [13],
}


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()


def cross_split_groups(groups):
    return sum(len({split for split, _ in members}) > 1 for members in groups.values())


def inventory_yolo(root):
    root = Path(root)
    counts, issues = {}, []
    prefix_groups, exact_groups = defaultdict(list), defaultdict(list)
    for split in ('train', 'valid', 'test'):
        images = root/split/'images'
        labels = root/split/'labels'
        if not images.is_dir() or not labels.is_dir():
            raise ValueError('missing_dataset_split')
        files = sorted(p for p in images.iterdir() if p.suffix.lower() in ('.jpg','.jpeg','.png'))
        stats = {'images': len(files), 'class_image_counts': Counter(), 'class_box_counts': Counter(),
                 'candidate_sampling_counts': {}, 'missing_labels': 0, 'orphan_labels': 0}
        parsed = []
        image_stems = set()
        for image in files:
            image_stems.add(image.stem)
            prefix_groups[image.name.split('.rf.')[0]].append((split, image.name))
            exact_groups[file_hash(image)].append((split, image.name))
            label = labels/(image.stem+'.txt')
            rows = []
            if not label.is_file():
                stats['missing_labels'] += 1
            else:
                for index, line in enumerate(label.read_text(encoding='utf-8').splitlines(), 1):
                    if not line.strip():
                        continue
                    try:
                        row = list(map(float, line.split()))
                        if (len(row) != 5 or not all(math.isfinite(x) for x in row) or
                                row[0] != int(row[0]) or not 0 <= row[0] < 15 or
                                not all(0 <= v <= 1 for v in row[1:]) or row[3] <= 0 or row[4] <= 0):
                            raise ValueError()
                        rows.append(int(row[0]))
                    except ValueError:
                        issues.append({'split': split, 'label_file': label.name, 'row': index, 'code':'invalid_yolo_row'})
            stats['class_image_counts'].update(set(rows))
            stats['class_box_counts'].update(rows)
            parsed.append((image.name, rows))
        stats['orphan_labels'] = sum(p.stem not in image_stems for p in labels.glob('*.txt'))
        for candidate, ids in CANDIDATES.items():
            matching = [(name, rows) for name, rows in parsed if set(rows).intersection(ids)]
            stats['candidate_sampling_counts'][candidate] = {
                'images_with_related_dish_or_component_label': len(matching),
                'related_existing_boxes_not_accepted_ingredient_gt': sum(c in ids for _,rows in matching for c in rows),
                'filename_prefix_groups': len({name.split('.rf.')[0] for name,_ in matching}),
                'accepted_ingredient_training_boxes': 0,
            }
        counts[split] = stats
    return {'splits': counts, 'issues': issues,
            'leakage_diagnostics': {'cross_split_filename_prefix_groups': cross_split_groups(prefix_groups),
                                    'cross_split_exact_sha256_groups': cross_split_groups(exact_groups),
                                    'near_duplicate_visual_check': 'not_performed',
                                    'prefix_groups_are_candidates_not_confirmed_source_identity': True},
            'source_metadata_sha256': {p.name:file_hash(p) for p in
                (root/'foodcal_data.yaml',root/'README.dataset.txt',root/'README.roboflow.txt') if p.is_file()}}


def inventory_food101(root):
    root = Path(root)
    try:
        names = (root/'meta/classes.txt').read_text(encoding='utf-8').splitlines()
        counts = {name:sum(1 for p in (root/'images'/name).glob('*.jpg')) for name in names}
        splits = {name:len((root/'meta'/(name+'.txt')).read_text(encoding='utf-8').splitlines()) for name in ('train','test')}
        license_text = root/'license_agreement.txt'
        return {'status':'inventoried','class_count':len(names),'image_counts_by_dish_class':counts,
                'split_metadata_image_counts':splits,'label_granularity':'image_classification_only',
                'ingredient_bbox_count':0,'license_text_sha256':file_hash(license_text),
                'license_review':'manual_review_required; license_text_hash_is_not_permission'}
    except (OSError, ValueError) as error:
        return {'status':'blocked','reason':type(error).__name__,'ingredient_bbox_count':None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--yolo-root',type=Path,required=True)
    parser.add_argument('--food101-root',type=Path)
    args = parser.parse_args()
    result = {'inventory_version':2,'usage':'diagnostic/test_only','yolo':inventory_yolo(args.yolo_root),
              'food101':inventory_food101(args.food101_root) if args.food101_root else {'status':'not_requested'}}
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
