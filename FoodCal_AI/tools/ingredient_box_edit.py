"""Pure single-box editing and append-only saving; no human decisions invented."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
import math
import uuid


def edit_box(annotation, annotation_id, updates, *, dimensions, spec):
    allowed={'class_id','bounding_box','visibility','occlusion','truncation','uncertainty_reason'}
    if not updates or set(updates)-allowed:raise ValueError('invalid_edit_fields')
    output=deepcopy(annotation)
    matches=[b for b in output['boxes'] if b['annotation_id']==annotation_id]
    if len(matches)!=1:raise ValueError('selected_box_not_unique')
    box=matches[0];box.update(deepcopy(updates))
    cls=box['class_id']
    if type(cls) is not int or not 0<=cls<len(spec['classes']):raise ValueError('invalid_class')
    rect=box['bounding_box']
    if not isinstance(rect,list) or len(rect)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in rect):raise ValueError('invalid_coordinates')
    x1,y1,x2,y2=rect;w,h=dimensions
    if not 0<=x1<x2<=w or not 0<=y1<y2<=h:raise ValueError('out_of_bounds')
    for field in ('visibility','occlusion','truncation'):
        if box[field] not in spec[field+'_values']:raise ValueError('invalid_'+field)
    if not isinstance(box.get('uncertainty_reason'),str):raise ValueError('invalid_uncertainty_reason')
    box['granularity']=spec['classes'][cls]['granularity']
    box['semantic_review_status']='pending'
    output['independent_review']={'status':'pending','reviewer':None}
    return output


def write_revision(directory, annotation, parent_hash):
    """Append a distinct revision; never opens an existing record for writing."""
    output=deepcopy(annotation)
    output['parent_revision_sha256']=parent_hash
    output['saved_at_utc']=datetime.now(timezone.utc).isoformat()
    # Filename ordering selects the latest revision. Windows clocks may return
    # the same timestamp twice; a random UUID must not decide which is latest.
    revision_time=datetime.now(timezone.utc)
    prefix=output['pilot_image_id']+'-'
    for existing in directory.glob(prefix+'*.json'):
        try:
            previous=datetime.strptime(existing.name[len(prefix):].split('-')[0],'%Y%m%dT%H%M%S%f').replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        revision_time=max(revision_time,previous+timedelta(microseconds=1))
    stamp=revision_time.strftime('%Y%m%dT%H%M%S%f')
    path=directory/(output['pilot_image_id']+'-'+stamp+'-'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as stream:json.dump(output,stream,ensure_ascii=False,indent=2)
    return path
