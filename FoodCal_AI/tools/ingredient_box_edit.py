"""Pure single-box editing and append-only saving; no human decisions invented."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import errno
import json
import math
import os
from pathlib import Path
import time
import uuid

from tools.ingredient_pilot import file_hash

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

LOCK_NAME = '.revision.lock'
LOCK_TIMEOUT_SECONDS = 5.0
LOCK_POLL_SECONDS = 0.02
_LOCK_BUSY = {errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES, getattr(errno, 'EDEADLOCK', errno.EDEADLK)}


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


def _try_lock(fd):
    if fcntl:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    else:
        os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_NBLCK,1)


def _unlock(fd):
    if fcntl:
        fcntl.flock(fd,fcntl.LOCK_UN)
    else:
        os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_UNLCK,1)


@contextmanager
def revision_lock(directory, *, timeout=LOCK_TIMEOUT_SECONDS):
    """Exclusive, non-reentrant cross-process lock for one annotations directory.

    OS advisory lock (flock / msvcrt), released by the OS if the holder crashes. It only covers
    processes on one machine using a local filesystem; cloud-sync folders and unverified network
    shares give no such guarantee. The empty lock file is never deleted: deleting it would let two
    processes lock different files.
    """
    fd=os.open(Path(directory)/LOCK_NAME,os.O_RDWR|os.O_CREAT,0o600)
    try:
        deadline=time.monotonic()+timeout
        while True:
            try:
                _try_lock(fd);break
            except OSError as error:
                if error.errno not in _LOCK_BUSY:raise
                if time.monotonic()>=deadline:raise TimeoutError('revision_lock_timeout') from None
                time.sleep(LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def latest_revision(directory, image_id):
    """Newest revision of one image by filename order, or None. Temp and lock files never match."""
    paths=sorted(Path(directory).glob(image_id+'-*.json'))
    return paths[-1] if paths else None


def _fsync_directory(directory):
    if os.name=='nt':return  # os.open cannot open a Windows directory; rely on the filesystem there.
    try:
        fd=os.open(directory,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    except OSError:
        pass  # Best effort only: the revision is already published under its final name.


def _publish(directory, name, output):
    """Write a complete temp file and fsync it, then rename it to a name nobody holds."""
    final=directory/name;temp=directory/f'.{name}.{uuid.uuid4().hex}.tmp'
    try:
        with temp.open('x',encoding='utf-8') as stream:
            json.dump(output,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        if final.exists():raise FileExistsError(final.name)
        os.replace(temp,final)
    finally:
        temp.unlink(missing_ok=True)  # No-op after a successful rename; drops a partial write.
    _fsync_directory(directory)
    return final


def write_revision(directory, annotation, parent_hash, *, require_latest=False, lock_timeout=LOCK_TIMEOUT_SECONDS):
    """Append a distinct revision atomically; never opens an existing record for writing.

    Runs under revision_lock. With require_latest=True the newest revision of this image must still
    hash to parent_hash inside the lock, so two saves from one parent can never both land.
    """
    directory=Path(directory)
    output=deepcopy(annotation)
    output['parent_revision_sha256']=parent_hash
    prefix=output['pilot_image_id']+'-'
    with revision_lock(directory,timeout=lock_timeout):
        if require_latest:
            latest=latest_revision(directory,output['pilot_image_id'])
            if (file_hash(latest) if latest else None)!=parent_hash:raise ValueError('stale_human_revision')
        output['saved_at_utc']=datetime.now(timezone.utc).isoformat()
        # Filename ordering selects the latest revision. Windows clocks may return
        # the same timestamp twice; a random UUID must not decide which is latest.
        revision_time=datetime.now(timezone.utc)
        for existing in directory.glob(prefix+'*.json'):
            try:
                previous=datetime.strptime(existing.name[len(prefix):].split('-')[0],'%Y%m%dT%H%M%S%f').replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            revision_time=max(revision_time,previous+timedelta(microseconds=1))
        stamp=revision_time.strftime('%Y%m%dT%H%M%S%f')
        return _publish(directory,output['pilot_image_id']+'-'+stamp+'-'+uuid.uuid4().hex+'.json',output)
