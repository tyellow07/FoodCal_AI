"""Bounded, one-shot official TFDA evidence acquisition; never a Source Adapter."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4
import zipfile


BASE = 'https://data.fda.gov.tw'
CONFIG = BASE + '/data/v3/api-docs/swagger-config'
ROOT = Path(__file__).resolve().parents[2]
SAFE_HEADERS = {'content-length', 'content-range', 'content-type', 'content-disposition',
                'etag', 'last-modified', 'date'}


def now():
    return datetime.now(timezone.utc).isoformat()


def fetch(url, *, method='GET', max_bytes=2 * 1024 * 1024, deadline=60):
    """TLS verification stays enabled. No retry, cookies or credentials retained."""
    start = time.monotonic()
    if urlparse(url).scheme != 'https' or urlparse(url).hostname != 'data.fda.gov.tw':
        raise ValueError('Only the verified HTTPS TFDA supplier is permitted')
    with urlopen(Request(url, method=method), timeout=20) as response:
        if urlparse(response.url).hostname != 'data.fda.gov.tw':
            raise ValueError('Unexpected supplier redirect')
        info = {'requested_url': url, 'final_url': response.url, 'method': method,
                'retrieved_at_utc': now(), 'status': response.status,
                'headers': {k: v for k, v in response.headers.items() if k.lower() in SAFE_HEADERS}}
        data = bytearray()
        if method != 'HEAD':
            while True:
                if time.monotonic() - start > deadline:
                    raise TimeoutError('Bounded download deadline exceeded')
                chunk = response.read(min(65536, max_bytes + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ValueError('Official response exceeded byte limit; acquisition stopped')
        info.update(bytes=len(data), sha256=sha256(data).hexdigest(), elapsed_seconds=time.monotonic() - start)
        return bytes(data), info


def save(directory, filename, data):
    with (directory / filename).open('xb') as stream:
        stream.write(data)


def operation_tags(value):
    return ' '.join(value.get('get', {}).get('tags', []))


def acquire():
    directory = ROOT / 'data/nutrition_source_review' / uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {'status': 'failed', 'dataset_page': 'https://data.gov.tw/dataset/8543',
                'started_at_utc': now(), 'official_dataset_version': None,
                'version_reason': 'No dataset release identifier verified; OAS version is API documentation only',
                'artifacts': [], 'errors': [], 'size_preflight': None}
    try:
        raw, info = fetch(CONFIG)
        save(directory, 'swagger-config.json', raw)
        manifest['artifacts'].append({'filename': 'swagger-config.json', **info})
        config = json.loads(raw)
        food = next(item for item in config['urls'] if item['name'] == '食品')
        raw, info = fetch(BASE + food['url'])
        save(directory, 'food-openapi.json', raw)
        manifest['artifacts'].append({'filename': 'food-openapi.json', **info})
        spec = json.loads(raw)
        matches = [(path, value['get']) for path, value in spec['paths'].items()
                   if '【8543】' in operation_tags(value) and path.endswith('/20/json')]
        if len(matches) != 1:
            raise ValueError('Expected one dataset 8543 JSON operation')
        path, operation = matches[0]
        manifest['license'] = spec['info']['license']
        if manifest['license'] != {'name': '政府資料開放授權條款-第1版', 'url': 'https://data.gov.tw/license'}:
            raise ValueError('Unexpected official license declaration')
        endpoint = operation['servers'][0]['url'] + path
        _, head = fetch(endpoint, method='HEAD')
        save(directory, 'head.json', json.dumps(head, ensure_ascii=False, indent=2).encode('utf-8'))
        size = next((v for k, v in head['headers'].items() if k.lower() == 'content-length'), None)
        limit = 8 * 1024 * 1024
        manifest['size_preflight'] = {'reported_content_length': size, 'max_download_bytes': limit,
                                     'exact_size_known_before_download': size is not None,
                                     'note': 'Missing size metadata is recorded, not estimated. Bounded read only.'}
        if size is not None and int(size) > limit:
            raise ValueError('HEAD size exceeds acquisition limit')
        raw, info = fetch(endpoint, max_bytes=limit, deadline=90)
        save(directory, 'official-export.zip', raw)
        manifest['artifacts'].append({'filename': 'official-export.zip', **info})
        with zipfile.ZipFile(directory / 'official-export.zip') as archive:
            members = archive.infolist()
            if len(members) != 1 or not members[0].filename.endswith('.json'):
                raise ValueError('Expected one JSON ZIP member')
            member = members[0]
            if member.file_size > 256 * 1024 * 1024:
                raise ValueError('Uncompressed member exceeds limit')
            # Read bytes only; do not extract untrusted archive paths.
            raw_json = archive.read(member)
            manifest['archive_member'] = {'name': member.filename, 'bytes': len(raw_json),
                                          'sha256': sha256(raw_json).hexdigest()}
            save(directory, 'official-export.json', raw_json)
        manifest['status'] = 'acquired'
    except Exception as error:
        manifest['errors'].append({'type': type(error).__name__, 'message': str(error), 'at_utc': now()})
    finally:
        manifest['finished_at_utc'] = now()
        save(directory, 'acquisition.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'))
    print(json.dumps({'status': manifest['status'], 'private_directory': directory.relative_to(ROOT).as_posix(),
                      'artifacts': [{k: v for k, v in a.items() if k in ('filename', 'bytes', 'sha256')}
                                    for a in manifest['artifacts']], 'errors': manifest['errors']}, ensure_ascii=True))
    return 0 if manifest['status'] == 'acquired' else 1


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    raise SystemExit(acquire())
