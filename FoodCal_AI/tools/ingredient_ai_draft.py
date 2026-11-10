"""Private AI draft import. Human actions stay in memory until an explicit save."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

from PIL import Image
from tools.ingredient_box_edit import edit_box, write_revision
from tools.ingredient_pilot import file_hash


FIELDS = ('class_id', 'bounding_box', 'visibility', 'occlusion', 'truncation',
          'uncertainty_reason')


def latest_hash(workspace, image_id):
    paths = sorted((Path(workspace) / 'annotations').glob(image_id + '-*.json'))
    return file_hash(paths[-1]) if paths else None


def check_current(workspace, item, spec_path, parent_hash):
    """Recheck disk inputs, including another GUI saving since this GUI loaded."""
    manifest = json.loads((Path(workspace) / 'manifest.json').read_text(encoding='utf-8'))
    matches = [i for i in manifest['items'] if i['pilot_image_id'] == item['pilot_image_id']]
    if len(matches) != 1 or matches[0] != item:
        raise ValueError('manifest_changed')
    if file_hash(spec_path) != manifest['spec_sha256']:
        raise ValueError('spec_hash_mismatch')
    if file_hash(Path(item['image_path'])) != item['image_sha256']:
        raise ValueError('image_hash_mismatch')
    with Image.open(item['image_path']) as image:
        if list(image.size) != item['dimensions']:
            raise ValueError('image_dimensions_mismatch')
    if latest_hash(workspace, item['pilot_image_id']) != parent_hash:
        raise ValueError('stale_human_revision')


def candidate_box(candidate, dimensions, spec):
    """Use the existing box editor for types, xyxy, enums and granularity."""
    if not isinstance(candidate, dict) or not all(k in candidate for k in FIELDS):
        raise ValueError('missing_candidate_fields')
    box = {'annotation_id': 'validation-only'}
    box = edit_box({'boxes': [box]}, 'validation-only',
                   {k: candidate[k] for k in FIELDS}, dimensions=dimensions, spec=spec)['boxes'][0]
    if (box['visibility'] != 'clear' or box['occlusion'] != 'none' or
            box['truncation'] != 'none') and not box['uncertainty_reason'].strip():
        raise ValueError('uncertainty_reason_required')
    return box


def validate_proposal(value, item, spec, spec_sha256, parent_hash):
    if not isinstance(value, dict) or value.get('schema_version') != 'ai_ingredient_proposal_v0':
        raise ValueError('invalid_proposal_version')
    for key in ('pilot_image_id', 'image_sha256', 'source_id', 'dimensions'):
        if value.get(key) != item.get(key):
            raise ValueError('scope_mismatch_' + key)
    if value.get('spec_version') != spec['spec_version'] or value.get('spec_sha256') != spec_sha256:
        raise ValueError('spec_hash_mismatch')
    if 'human_revision_sha256' not in value or value['human_revision_sha256'] != parent_hash:
        raise ValueError('stale_human_revision')
    if value.get('status') != 'draft' or value.get('training_eligible') is not False:
        raise ValueError('proposal_must_remain_draft')
    if not isinstance(value.get('boxes'), list):
        raise ValueError('invalid_boxes')
    seen = set()
    for candidate in value['boxes']:
        if not isinstance(candidate, dict):
            raise ValueError('invalid_candidate')
        key = candidate.get('proposal_id')
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError('duplicate_or_invalid_proposal_id')
        seen.add(key)
        box = candidate_box(candidate, item['dimensions'], spec)
        if candidate.get('granularity') != box['granularity']:
            raise ValueError('invalid_granularity')
        cls = next(c for c in spec['classes'] if c['class_id'] == box['class_id'])
        if candidate.get('class_name', cls['name']) != cls['name']:
            raise ValueError('class_name_mismatch')


def load_draft(path, *, workspace, item, spec_path, parent_hash, annotation):
    """No annotation writes, no classes_checked changes, no model calls."""
    path = Path(path).resolve()
    if not path.is_relative_to(Path(workspace).resolve()) or path.suffix != '.json':
        raise ValueError('private_workspace_proposal_required')
    check_current(workspace, item, spec_path, parent_hash)
    raw = path.read_bytes()
    value = json.loads(raw.decode('utf-8'))
    spec = json.loads(Path(spec_path).read_text(encoding='utf-8'))
    validate_proposal(value, item, spec, file_hash(spec_path), parent_hash)
    sha = hashlib.sha256(raw).hexdigest()
    if any(a['proposal_sha256'] == sha for a in annotation.get('ai_imports', [])):
        raise ValueError('proposal_already_imported')
    return {'schema_version': 'ai_gui_import_v0', 'proposal_sha256': sha,
            'proposal_path': path.relative_to(Path(workspace).resolve()).as_posix(),
            'original_proposal': deepcopy(value), 'base_revision_sha256': parent_hash,
            'candidates': {b['proposal_id']: deepcopy(b) for b in value['boxes']},
            'decisions': {}, 'events': [], 'training_eligible': False}


def event(action, actor, **details):
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError('actor_required')
    return {'action': action, 'actor': actor, 'at_utc': datetime.now(timezone.utc).isoformat(), **deepcopy(details)}


def edit_candidate(draft, proposal_id, updates, *, actor, dimensions, spec):
    output = deepcopy(draft)
    if proposal_id in output['decisions']:
        raise ValueError('candidate_already_decided')
    if not updates or set(updates) - set(FIELDS):
        raise ValueError('invalid_edit_fields')
    before = deepcopy(output['candidates'][proposal_id])
    candidate = output['candidates'][proposal_id]
    candidate.update(deepcopy(updates))
    box = candidate_box(candidate, dimensions, spec)
    candidate['granularity'] = box['granularity']
    candidate['class_name'] = spec['classes'][box['class_id']]['name']
    output['events'].append(event('edit_candidate', actor, proposal_id=proposal_id,
                                  before=before, after=candidate))
    return output


def decide(annotation, draft, proposal_id, action, *, actor, dimensions, spec, target_id=None, note=''):
    """Explicit add/replace/reject only; repeat handling and duplicate boxes fail."""
    if action not in ('add', 'replace', 'reject'):
        raise ValueError('invalid_action')
    if proposal_id not in draft['candidates']:
        raise ValueError('unknown_proposal_id')
    if proposal_id in draft['decisions']:
        raise ValueError('candidate_already_decided')
    output, updated = deepcopy(annotation), deepcopy(draft)
    candidate = updated['candidates'][proposal_id]
    box = candidate_box(candidate, dimensions, spec)
    before = None
    if action == 'replace':
        matches = [b for b in output['boxes'] if b['annotation_id'] == target_id]
        if len(matches) != 1:
            raise ValueError('replacement_target_required')
        if matches[0].get('ai_origin'):
            raise ValueError('target_already_ai_linked_edit_existing_box')
        before = deepcopy(matches[0])
    elif target_id is not None:
        raise ValueError('unexpected_replacement_target')
    if action != 'reject':
        for existing in output['boxes']:
            if existing['annotation_id'] != target_id and all(existing.get(k) == box[k] for k in ('class_id', 'bounding_box')):
                raise ValueError('duplicate_box_use_replace')
        box['annotation_id'] = target_id if action == 'replace' else uuid.uuid4().hex
        box['ai_origin'] = {'kind': 'ai_generated_human_confirmed_pending',
                            'proposal_sha256': draft['proposal_sha256'], 'proposal_id': proposal_id}
        if action == 'replace':
            output['boxes'] = [box if b['annotation_id'] == target_id else b for b in output['boxes']]
        else:
            output['boxes'].append(box)
    record = event(action, actor, proposal_id=proposal_id, note=note, before=before,
                   after=None if action == 'reject' else box,
                   annotation_id=None if action == 'reject' else box['annotation_id'])
    updated['events'].append(record)
    updated['decisions'][proposal_id] = deepcopy(record)
    return output, updated


def record_box_change(annotation, draft, before, after, actor):
    """Trace later human edits/removal of AI-linked boxes, including after reopen."""
    origin = before.get('ai_origin')
    if not origin:
        return annotation, draft
    output, updated = deepcopy(annotation), deepcopy(draft)
    logs = output.get('ai_imports', []) + ([updated] if updated else [])
    matches = [log for log in logs if log['proposal_sha256'] == origin['proposal_sha256']]
    if len(matches) != 1:
        raise ValueError('ai_provenance_missing')
    matches[0]['events'].append(event('edit_imported_box' if after else 'remove_imported_box', actor,
                                     proposal_id=origin['proposal_id'], before=before, after=after))
    return output, updated


def save_pending(workspace, annotation, draft, *, item, spec_path, parent_hash, actor, classes_checked):
    check_current(workspace, item, spec_path, parent_hash)
    spec = json.loads(Path(spec_path).read_text(encoding='utf-8'))
    output = deepcopy(annotation)
    for key in ('pilot_image_id', 'source_id', 'image_sha256'):
        if output.get(key) != item[key]:
            raise ValueError('annotation_scope_mismatch')
    if output.get('spec_version') != spec['spec_version']:
        raise ValueError('spec_hash_mismatch')
    ids = [b['annotation_id'] for b in output['boxes']]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate_annotation_id')
    for box in output['boxes']:
        # Preserve legacy empty reasons; do not silently repair human annotation.
        validated = edit_box(output, box['annotation_id'], {k: box.get(k, '') for k in FIELDS},
                             dimensions=item['dimensions'], spec=spec)
        normalized = next(b for b in validated['boxes'] if b['annotation_id'] == box['annotation_id'])
        if box.get('granularity') != normalized['granularity']:
            raise ValueError('invalid_granularity')
        if box.get('ai_origin'):
            candidate_box(box, item['dimensions'], spec)
        box['semantic_review_status'] = 'pending'
    if draft:
        path = (Path(workspace) / draft['proposal_path']).resolve()
        if not path.is_relative_to(Path(workspace).resolve()) or file_hash(path) != draft['proposal_sha256']:
            raise ValueError('proposal_hash_mismatch')
        validate_proposal(draft['original_proposal'], item, spec, file_hash(spec_path), parent_hash)
        if set(draft['decisions']) != set(draft['candidates']):
            raise ValueError('unresolved_ai_candidates')
        output.setdefault('ai_imports', []).append(deepcopy(draft))
    event('save', actor)  # Validate actor without inserting fabricated review decisions.
    if not isinstance(classes_checked, list) or any(type(c) is not int or c not in (0, 1, 2) for c in classes_checked):
        raise ValueError('invalid_classes_checked')
    if classes_checked != sorted(set(classes_checked)):
        raise ValueError('invalid_classes_checked')
    output['classes_checked'] = list(classes_checked)
    output['annotator'] = actor
    output['independent_review'] = {'status': 'pending', 'reviewer': None}
    output['training_eligible'] = False
    output['nutrition_eligible'] = False
    directory = Path(workspace) / 'annotations'
    directory.mkdir(exist_ok=True)
    return write_revision(directory, output, parent_hash)
