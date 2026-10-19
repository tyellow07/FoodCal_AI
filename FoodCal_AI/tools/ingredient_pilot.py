"""Private review-only sampling and annotation checks. Never exports training GT."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import random
import uuid

from tools.inventory_ingredient_data_v2 import CANDIDATES, file_hash

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT/'examples/visual_ingredient_detection_v2/annotation_spec.json'


def select_records(candidates, limit=20):
    """Conservative candidate components BEFORE selection: prefix OR exact hash.

    Prefix links are unresolved, not asserted source identity. One component
    representative is selected globally; unseen visual near-duplicates remain.
    """
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('limit_must_be_1_to_20')
    parent = list(range(len(candidates)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    seen = {}
    for i,c in enumerate(candidates):
        for key in (('prefix',c['prefix']),('hash',c['image_sha256'])):
            if key in seen: parent[find(i)] = find(seen[key])
            else: seen[key] = i
    components = {}
    for i,c in enumerate(candidates):
        components.setdefault(find(i),[]).append(c)
    selected, used = [], set()
    rng=random.Random(42)
    groups=list(components.values()); rng.shuffle(groups)
    for target in list(CANDIDATES)[:3]:
        eligible=[]
        for group in groups:
            matching=[c for c in group if c['original_split']!='test' and set(c['original_classes']).intersection(CANDIDATES[target])]
            if not matching: continue
            matching.sort(key=lambda c:(not c.get('known_review_reference'),c['image_file']))
            representative=matching[0]
            eligible.append((not representative.get('known_review_reference'),group,representative))
        eligible.sort(key=lambda x:x[0])
        count=0
        for _,group,c in eligible:
            group_id=min(x['image_sha256'] for x in group)
            if group_id in used: continue
            row=dict(c,sampling_target=target,candidate_bucket_id=group_id,
                     candidate_group_members=len(group),source_group_id=None,
                     source_group_status='unresolved',split=None,
                     selection_reason='one_candidate_component_representative; related_dish_label_not_ingredient_truth',
                     all_target_classes_require_annotation=[0,1,2],
                     rights_status='review_only_unverified',training_eligible=False,
                     annotation_status='pending',independent_review_status='pending',
                     visual_triage_status='pending')
            selected.append(row);used.add(group_id);count+=1
            if count==limit:break
    return selected


def build_workspace(dataset, workspace, limit=20):
    dataset=Path(dataset).resolve(); workspace=Path(workspace).resolve()
    # This workflow intentionally restricts all persistent private output to /work.
    if not workspace.is_relative_to(ROOT/'work'):
        raise ValueError('workspace_must_be_under_ignored_work')
    if workspace.exists(): raise ValueError('workspace_already_exists_no_overwrite')
    spec=json.loads(SPEC.read_text(encoding='utf-8'))
    prior=json.loads((ROOT/'docs/visual_ingredient_annotation_v0_evidence.json').read_text(encoding='utf-8'))
    known={r['image_file'] for r in prior['reviewed_images']}
    candidates=[]
    from PIL.JpegImagePlugin import JpegImageFile
    for split in ('train','valid','test'):
        for image in sorted((dataset/split/'images').glob('*.jpg')):
            label=dataset/split/'labels'/(image.stem+'.txt')
            if not label.is_file():raise ValueError('missing_original_label')
            classes=[int(line.split()[0]) for line in label.read_text().splitlines() if line.strip()]
            candidates.append({'image_file':image.name,'image_path':str(image),
                'image_sha256':file_hash(image),'original_split':split,'original_classes':classes,
                'prefix':image.name.split('.rf.')[0],'original_label_path':str(label),
                'original_label_sha256':file_hash(label),
                'known_review_reference':'docs/visual_ingredient_annotation_v0_evidence.json' if image.name in known else None})
    rows=select_records(candidates,limit)
    for i,row in enumerate(rows,1):
        with JpegImageFile(row['image_path']) as picture:
            picture.load();row['dimensions']=[picture.width,picture.height]
        row.update(pilot_image_id=f'pilot-{i:03}',source_id='roboflow-taiwanese-food-v1',
                   source_url='https://universe.roboflow.com/chads-workshop/taiwanese-food-detection',
                   license_evidence_ref='local_dataset_README_CC_BY_4.0_statement_not_per_image_clearance')
    manifest={'pilot_version':0,'usage':'private/review_only','spec_version':spec['spec_version'],
              'spec_sha256':file_hash(SPEC),'items':rows,'accepted_annotation_boxes':0,
              'completed_human_annotations':0,'independent_accepted_reviews':0,
              'split_status':'blocked_pending_rights_source_groups_and_visual_triage',
              'source_evidence_hashes':{name:file_hash(dataset/name) for name in
                                      ('README.dataset.txt','README.roboflow.txt','foodcal_data.yaml')}}
    workspace.mkdir(parents=True)
    (workspace/'annotations').mkdir()
    (workspace/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest


def validate_annotation(item, annotation, *, for_training=False):
    """Quality and eligibility are separate. GT/reference labels never read here."""
    spec=json.loads(SPEC.read_text(encoding='utf-8'))
    issues=[]
    def issue(code):issues.append(code)
    for key in ('pilot_image_id','image_sha256','source_id'):
        if annotation.get(key)!=item.get(key):issue('scope_mismatch_'+key)
    if annotation.get('spec_version')!=spec['spec_version']:issue('spec_version_mismatch')
    if not isinstance(annotation.get('annotator'),str) or not annotation['annotator'].strip():issue('missing_annotator')
    checked=annotation.get('classes_checked')
    if not isinstance(checked,list) or checked!=[0,1,2] or any(type(c) is not int for c in checked):issue('incomplete_target_class_review')
    review=annotation.get('independent_review',{})
    if not isinstance(review,dict):review={};issue('invalid_review')
    if review.get('status')!='accepted':issue('independent_review_not_accepted')
    if not review.get('reviewer') or review.get('reviewer')==annotation.get('annotator'):issue('independent_reviewer_required')
    boxes=annotation.get('boxes')
    if not isinstance(boxes,list):issue('invalid_boxes');boxes=[]
    ids=set()
    for box in boxes:
        if not isinstance(box,dict):issue('invalid_box');continue
        cls=box.get('class_id')
        if type(cls) is not int or cls not in (0,1,2):issue('invalid_class');continue
        if not isinstance(box.get('annotation_id'),str) or not box['annotation_id'] or box['annotation_id'] in ids:issue('invalid_annotation_id')
        if isinstance(box.get('annotation_id'),str):ids.add(box['annotation_id'])
        if box.get('granularity')!=spec['classes'][cls]['granularity']:issue('invalid_granularity')
        if box.get('semantic_review_status')!='accepted_for_pilot_annotation':issue('box_semantics_unaccepted')
        if box.get('visibility')=='indeterminate':issue('visible_region_unresolved')
        rect=box.get('bounding_box')
        if (not isinstance(rect,list) or len(rect)!=4 or
            any(type(v) not in (int,float) or not math.isfinite(v) for v in rect)):
            issue('invalid_coordinates');continue
        x1,y1,x2,y2=rect;w,h=item['dimensions']
        if not 0<=x1<x2<=w or not 0<=y1<y2<=h:issue('out_of_bounds')
        for field,values in (('visibility',spec['visibility_values']),('occlusion',spec['occlusion_values']),('truncation',spec['truncation_values'])):
            if box.get(field) not in values:issue('invalid_'+field)
    if for_training:
        if item.get('rights_status')!='verified_for_training':issue('rights_unverified')
        if not item.get('training_rights_evidence_ref'):issue('rights_evidence_missing')
        if item.get('source_group_status')!='verified' or not item.get('source_group_id'):issue('source_group_unresolved')
        if not item.get('source_group_review_ref'):issue('source_group_review_missing')
        if item.get('split') not in ('train','validation','test'):issue('split_unassigned')
    return {'status':'passed' if not issues else 'blocked','issues':issues,
            'training_eligible':for_training and not issues}


def validate_splits(items):
    issues=[];seen={}
    for item in items:
        if item.get('source_group_status')!='verified' or not item.get('source_group_id') or item.get('split') not in ('train','validation','test'):
            issues.append('unresolved_group_or_split');continue
        for key in (('group',item['source_group_id']),('hash',item['image_sha256']),('candidate_bucket',item['candidate_bucket_id'])):
            if key in seen and seen[key]!=item['split']:issues.append('cross_split_leakage')
            seen[key]=item['split']
    return {'status':'passed' if not issues else 'blocked','issues':issues}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=ROOT/'data/roboflow_taiwanese_food_detection')
    parser.add_argument('--workspace',type=Path,required=True)
    args=parser.parse_args()
    m=build_workspace(args.dataset,args.workspace)
    print(json.dumps({'selected':len(m['items']),'sampling_targets':dict(Counter(r['sampling_target'] for r in m['items'])),
                      'human_annotations':0,'accepted_boxes':0,'usage':m['usage'],'split_status':m['split_status']}))


if __name__=='__main__':main()
